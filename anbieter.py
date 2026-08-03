#!/usr/bin/env python3
"""Modell-Anbieter unter einem Dach — Claude wird einer von vielen (#142, Epic #137).

Warum es das gibt
-----------------
Heute ist Claude die Hauptsache und alles andere ein Sonderfall: Der Claude-Weg läuft
über das Programm `claude`, Fremdmodelle über `llm_runner.py` mit dem openai-Paket, und
beide Wege haben eigene Vorstellungen davon, was eine Nachricht ist. Solange das so ist,
gibt es keinen »eigenen Kern«, sondern zwei halbe.

Hier ist die Umkehrung: **ein** Nachrichtenformat, **eine** Antwortform, und je Anbieter
nur eine Übersetzung an der Außenkante.

Wie akut das ist, hat sich am 03.08. gezeigt (#151): Ein abgelaufenes Anmeldefenster im
Programm `claude` legte den Operator drei Tage lahm — obwohl Ollama die ganze Zeit
bereitstand. Deshalb ist hier **»kann sich nicht anmelden« kein Sonderfall, sondern ein
ausgefallener Anbieter** wie jeder andere: Der Auto-Wechsel greift.

Bordmittel
----------
`urllib` statt des openai-Pakets. Beide Schnittstellen sind HTTP mit JSON; das Paket
nimmt einem wenig ab und kostet auf dem Pi und auf verwalteten Firmen-Notebooks eine
Abhängigkeit. `llm_runner.py` bleibt vorerst wie es ist — dieser Baustein steht daneben,
bis K5 (#143) die Schleife darauf umstellt. Zwei Wege gleichzeitig umbauen ist der
sicherste Weg, beide kaputtzumachen.

Das Nachrichtenformat
---------------------
    {"rolle": "system" | "nutzer" | "modell" | "werkzeug",
     "text": "...",
     "werkzeug_aufrufe": [{"id", "name", "argumente"}],   # nur bei rolle=modell
     "aufruf_id": "..."}                                  # nur bei rolle=werkzeug
"""
import json
import os
import sys
import urllib.error
import urllib.request

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)

ZEITLIMIT = 120


class Antwort:
    """Was ein Modell zurückgibt — für alle Anbieter gleich geformt."""

    def __init__(self, text="", werkzeug_aufrufe=None, stopgrund="", verbrauch=None,
                 fehler="", anmeldung_fehlt=False):
        self.text = text
        self.werkzeug_aufrufe = werkzeug_aufrufe or []
        self.stopgrund = stopgrund
        self.verbrauch = verbrauch or {"ein": 0, "aus": 0}
        self.fehler = fehler
        # Eigenes Kennzeichen, weil es eine eigene Behandlung braucht: Der Nutzer muss
        # sich anmelden, kein Wiederholen hilft. Siehe #151.
        self.anmeldung_fehlt = anmeldung_fehlt

    def __repr__(self):
        return (f"Antwort(text={self.text[:40]!r}, "
                f"werkzeuge={[a['name'] for a in self.werkzeug_aufrufe]}, "
                f"fehler={self.fehler!r})")


def _post(url, kopf, koerper, zeitlimit=ZEITLIMIT):
    daten = json.dumps(koerper, ensure_ascii=False).encode("utf-8")
    anfrage = urllib.request.Request(url, data=daten, method="POST",
                                     headers={"Content-Type": "application/json", **kopf})
    with urllib.request.urlopen(anfrage, timeout=zeitlimit) as a:
        return json.loads(a.read().decode("utf-8", "replace"))


def _anmeldeproblem(text):
    """Erkennt ein Anmeldeproblem an der Fehlermeldung.

    Bewusst mit Wortliste statt HTTP-Status: Ollama, Azure und Anthropic melden
    dasselbe auf drei Arten, und das Programm `claude` gibt sogar HTTP 200 mit einer
    Fehlermeldung im Text zurück (#151).
    """
    t = (text or "").lower()
    return any(w in t for w in ("oauth", "unauthorized", "authentication", "api key",
                                "api-key", "invalid_api_key", "expired", "401",
                                "403", "forbidden", "credential"))


# ------------------------------------------------------------------- Anbieter --
class Anbieter:
    name = "?"

    def antworten(self, nachrichten, werkzeuge=None, modell="", max_zeichen=4096):
        raise NotImplementedError


class OpenAIArtig(Anbieter):
    """OpenAI, Azure, Ollama, LM Studio — alle sprechen dasselbe Format.

    Ein eigener Anbieter je Dienst wäre dreimal derselbe Code mit anderer Adresse.
    Unterschiede stecken in Adresse und Schlüssel, nicht im Protokoll.
    """

    def __init__(self, name, basis_url, schluessel=""):
        self.name = name
        self.basis = (basis_url or "").rstrip("/")
        self.schluessel = schluessel or ""

    def _nachricht(self, n):
        rolle = {"system": "system", "nutzer": "user", "modell": "assistant",
                 "werkzeug": "tool"}[n["rolle"]]
        if n["rolle"] == "werkzeug":
            return {"role": "tool", "tool_call_id": n.get("aufruf_id", ""),
                    "content": n.get("text", "")}
        raus = {"role": rolle, "content": n.get("text", "")}
        if n.get("werkzeug_aufrufe"):
            raus["tool_calls"] = [
                {"id": a["id"], "type": "function",
                 "function": {"name": a["name"],
                              "arguments": json.dumps(a.get("argumente") or {},
                                                      ensure_ascii=False)}}
                for a in n["werkzeug_aufrufe"]]
        return raus

    def antworten(self, nachrichten, werkzeuge=None, modell="", max_zeichen=4096):
        koerper = {"model": modell,
                   "messages": [self._nachricht(n) for n in nachrichten],
                   "max_tokens": max_zeichen}
        if werkzeuge:
            koerper["tools"] = werkzeuge
        kopf = {"Authorization": f"Bearer {self.schluessel}"} if self.schluessel else {}
        try:
            d = _post(f"{self.basis}/chat/completions", kopf, koerper)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:300]
            return Antwort(fehler=f"{e.code}: {text}",
                           anmeldung_fehlt=_anmeldeproblem(f"{e.code} {text}"))
        except Exception as e:
            return Antwort(fehler=f"{type(e).__name__}: {e}")

        wahl = (d.get("choices") or [{}])[0]
        m = wahl.get("message") or {}
        aufrufe = []
        for a in m.get("tool_calls") or []:
            f = a.get("function") or {}
            try:
                args = json.loads(f.get("arguments") or "{}")
            except ValueError:
                # Ein Modell, das kaputtes JSON liefert, ist ein bekanntes Vorkommnis —
                # kein Grund, den ganzen Lauf abzubrechen. Leere Argumente sind
                # ehrlicher als geraten.
                args = {}
            aufrufe.append({"id": a.get("id", ""), "name": f.get("name", ""),
                            "argumente": args})
        v = d.get("usage") or {}
        return Antwort(text=(m.get("content") or "").strip(), werkzeug_aufrufe=aufrufe,
                       stopgrund=wahl.get("finish_reason", ""),
                       verbrauch={"ein": v.get("prompt_tokens", 0),
                                  "aus": v.get("completion_tokens", 0)})


class AnthropicArtig(Anbieter):
    """Die Anthropic-Schnittstelle. Anderes Format, gleiche Antwortform.

    Drei Unterschiede, die man leicht übersieht:
    * Die Systemanweisung ist ein eigenes Feld, keine Nachricht.
    * Werkzeuge heißen `input_schema` statt `parameters`.
    * Werkzeugergebnisse sind Inhaltsblöcke in einer Nutzernachricht, keine eigene Rolle.
    """

    name = "anthropic"

    def __init__(self, schluessel, basis_url="https://api.anthropic.com/v1"):
        self.schluessel = schluessel or ""
        self.basis = basis_url.rstrip("/")

    def antworten(self, nachrichten, werkzeuge=None, modell="", max_zeichen=4096):
        system = " ".join(n.get("text", "") for n in nachrichten
                          if n["rolle"] == "system").strip()
        verlauf = []
        for n in nachrichten:
            if n["rolle"] == "system":
                continue
            if n["rolle"] == "werkzeug":
                verlauf.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": n.get("aufruf_id", ""),
                     "content": n.get("text", "")}]})
            elif n["rolle"] == "modell":
                bloecke = []
                if n.get("text"):
                    bloecke.append({"type": "text", "text": n["text"]})
                for a in n.get("werkzeug_aufrufe") or []:
                    bloecke.append({"type": "tool_use", "id": a["id"], "name": a["name"],
                                    "input": a.get("argumente") or {}})
                verlauf.append({"role": "assistant", "content": bloecke or [
                    {"type": "text", "text": ""}]})
            else:
                verlauf.append({"role": "user", "content": n.get("text", "")})

        koerper = {"model": modell, "messages": verlauf, "max_tokens": max_zeichen}
        if system:
            koerper["system"] = system
        if werkzeuge:
            koerper["tools"] = [
                {"name": w["function"]["name"],
                 "description": w["function"].get("description", ""),
                 "input_schema": w["function"].get("parameters") or {"type": "object"}}
                if "function" in w else w for w in werkzeuge]
        kopf = {"x-api-key": self.schluessel, "anthropic-version": "2023-06-01"}
        try:
            d = _post(f"{self.basis}/messages", kopf, koerper)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:300]
            return Antwort(fehler=f"{e.code}: {text}",
                           anmeldung_fehlt=_anmeldeproblem(f"{e.code} {text}"))
        except Exception as e:
            return Antwort(fehler=f"{type(e).__name__}: {e}")

        text, aufrufe = "", []
        for block in d.get("content") or []:
            if block.get("type") == "text":
                text += block.get("text", "")
            elif block.get("type") == "tool_use":
                aufrufe.append({"id": block.get("id", ""), "name": block.get("name", ""),
                                "argumente": block.get("input") or {}})
        v = d.get("usage") or {}
        return Antwort(text=text.strip(), werkzeug_aufrufe=aufrufe,
                       stopgrund=d.get("stop_reason", ""),
                       verbrauch={"ein": v.get("input_tokens", 0),
                                  "aus": v.get("output_tokens", 0)})


# ------------------------------------------------------------------ Auswahl --
def aus_einstellungen(name):
    """Baut einen Anbieter aus dem, was providers.py schon weiß.

    Keine zweite Konfiguration: Adressen und Schlüssel liegen bereits dort, und eine
    zweite Quelle würde auseinanderlaufen.
    """
    import providers
    if name == "anthropic":
        return AnthropicArtig(providers.get_key("anthropic") or "")
    basis = providers.base_url(name) if hasattr(providers, "base_url") else ""
    if not basis:
        basis = {"ollama": "http://localhost:11434/v1",
                 "openai": "https://api.openai.com/v1"}.get(name, "")
    return OpenAIArtig(name, basis, providers.get_key(name) or "")


def mit_wechsel(reihenfolge, nachrichten, werkzeuge=None, modelle=None,
                max_zeichen=4096, protokoll=None):
    """Der Reihe nach probieren, bis einer antwortet.

    **Ein Anbieter, der sich nicht anmelden kann, gilt als ausgefallen** — nicht als
    Sonderfall. Genau daran hing der dreitägige Ausfall vom 03.08. (#151): Claude kam
    nicht durch die Anmeldung, und weil das kein »Fehler beim Antworten« war, griff der
    Wechsel nicht. Ollama stand die ganze Zeit bereit.
    """
    modelle = modelle or {}
    versuche = []
    for name in reihenfolge:
        try:
            a = aus_einstellungen(name)
        except Exception as e:
            versuche.append((name, f"nicht einsatzbereit: {e}"))
            continue
        antwort = a.antworten(nachrichten, werkzeuge, modelle.get(name, ""), max_zeichen)
        if not antwort.fehler:
            antwort.verbrauch["anbieter"] = name
            if versuche and protokoll:
                protokoll(f"»{name}« hat übernommen, nachdem "
                          f"{', '.join(n for n, _ in versuche)} ausfielen.")
            return antwort
        versuche.append((name, ("Anmeldung abgelaufen" if antwort.anmeldung_fehlt
                                else antwort.fehler[:100])))
        if protokoll:
            protokoll(f"»{name}« ausgefallen: {versuche[-1][1]}")
    return Antwort(fehler="Kein Anbieter konnte antworten. "
                          + "; ".join(f"{n}: {g}" for n, g in versuche),
                   anmeldung_fehlt=all("Anmeldung" in g for _, g in versuche) and
                   bool(versuche))
