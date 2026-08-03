#!/usr/bin/env python3
"""Das Compliance-Protokoll — was der Operator tat und was er nicht durfte (#146).

Warum es das gibt
-----------------
Ein Mittelständler, der einen KI-Assistenten an sein Postfach lässt, muss zwei Fragen
beantworten können — dem Datenschutzbeauftragten, dem Betriebsrat, im Zweifel der
Aufsichtsbehörde:

1. Was hat das Ding getan?
2. **Was hat es versucht und nicht gedurft?**

Die zweite Frage beantwortet weder OpenClaw noch Hermes: Sie protokollieren Ausführung,
nicht Ablehnung. Bei uns kostet sie fast nichts, weil die Schleuse (#139) und der Broker
ohnehin jede Handlung sehen und ein Urteil samt Begründung erzeugen.

Vier Eigenschaften, jede aus einem konkreten Grund
--------------------------------------------------
**Verkettet.** Jeder Eintrag trägt die Prüfsumme des vorigen. Wer eine Zeile
herausschneidet, macht die Kette sichtbar kaputt. Kein Kryptozauber — SHA-256 aus der
Standardbibliothek, läuft auf dem Pi.

**Ohne Klartext.** Hier stehen **Handlungen und Urteile**, keine Inhalte. Kein Dateiinhalt,
kein Mailtext, kein Passwort. Ein Protokoll, das Geheimnisse enthält, ist ein Schaden und
kein Nachweis — genau die dokumentierte Schwäche von OpenClaw. Und es doppelt nicht
`sessions.db`: Dort steht der Gesprächsverlauf, hier steht, was daraufhin geschah.

**Begrenzt.** Aufbewahrungsdauer einstellbar, Voreinstellung 90 Tage. Ein Protokoll ist
selbst personenbezogene Datenverarbeitung — unbegrenztes Aufheben löst ein Problem und
schafft ein neues.

**Lesbar.** Der Monatsbericht ist ein Absatz in Alltagssprache, keine Rohdatenwüste.
Er ist das, was in einem Verkaufsgespräch die Datenschutzfrage beendet.
"""
import hashlib
import json
import os
import time

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATEI = os.path.join(BOT_DIR, "protokoll.jsonl")
AUFBEWAHRUNG_TAGE = 90
MAX_GRUND = 200

# Dieselbe Vorsichtsregel wie im Broker (#148): Was so aussieht, gehört nicht ins
# Protokoll — auch nicht als Teil eines Pfades oder Befehls.
import re                                                     # noqa: E402
_GEHEIM = re.compile(r"(sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}|eyJ[A-Za-z0-9_\-]{10,}"
                     r"|xox[baprs]-[A-Za-z0-9\-]{8,}|Bearer\s+\S{12,}"
                     r"|[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")

URTEILE = ("ausgefuehrt", "bestaetigung", "abgelehnt", "gesperrt")


def frist():
    """Aufbewahrungsdauer in Tagen — einstellbar in dashboard.json unter »retention«.

    Bewusst dieselbe Stelle wie die übrigen Fristen (#18): Wer im Dashboard
    festlegt, wie lange sein Verlauf bleibt, erwartet die Protokollfrist nicht
    in einer zweiten Datei. Unplausible Werte werden auf 1…3650 begrenzt —
    »0 Tage« hieße, dass jedes Aufräumen den ganzen Nachweis löscht.
    """
    try:
        wert = json.load(open(os.path.join(BOT_DIR, "dashboard.json"))) \
            .get("retention", {}).get("protokoll_days", AUFBEWAHRUNG_TAGE)
        return max(1, min(3650, int(wert)))
    except (OSError, ValueError, TypeError, AttributeError):
        return AUFBEWAHRUNG_TAGE


def _saeubern(text):
    """Alles, was nach Geheimnis oder Adresse aussieht, wird ersetzt.

    Lieber ein unscharfer Eintrag als ein Protokoll, das man wegsperren muss.
    """
    return _GEHEIM.sub("[entfernt]", str(text or ""))[:MAX_GRUND]


def _letzte_pruefsumme(datei=None):
    """Die Prüfsumme des letzten Eintrags — der Datei, in die WIRKLICH geschrieben wird.

    Der erste Entwurf las hier immer die Standarddatei. Wer das Protokoll woanders
    führt (Test, zweite Instanz, Export), bekam eine Kette, die gegen eine fremde
    Datei gebildet wurde — und damit ein Protokoll, das sich beim ersten Prüfen als
    »manipuliert« meldet. Ein Nachweis, der grundlos Alarm schlägt, ist so wertlos
    wie einer, der schweigt.
    """
    try:
        with open(datei or DATEI, "rb") as f:
            f.seek(0, 2)
            groesse = f.tell()
            f.seek(max(0, groesse - 4096))
            zeilen = [z for z in f.read().decode("utf-8", "replace").splitlines() if z.strip()]
        return json.loads(zeilen[-1]).get("pruefsumme", "") if zeilen else ""
    except (OSError, ValueError, IndexError):
        return ""


def _pruefsumme(eintrag, vorher):
    roh = json.dumps(eintrag, sort_keys=True, ensure_ascii=False) + vorher
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:32]


def eintragen(urteil, art, werkzeug, grund="", agent="", modell="", ziel="",
              herkunft="", datei=None):
    """Eine Handlung festhalten. Gibt den Eintrag zurück (oder None bei Fehler).

    `urteil` ist eines aus URTEILE. Wichtig: **abgelehnt und gesperrt werden genauso
    protokolliert wie ausgefuehrt** — das ist der ganze Punkt dieses Moduls.
    """
    eintrag = {
        "zeit": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "urteil": urteil if urteil in URTEILE else "unbekannt",
        "art": str(art or "")[:40],
        "werkzeug": _saeubern(werkzeug)[:80],
        "grund": _saeubern(grund),
        "agent": str(agent or "")[:40],
        "modell": str(modell or "")[:60],
        "ziel": str(ziel or "")[:60],          # wohin gingen Daten (Anthropic, M365, …)
        "herkunft": str(herkunft or "")[:20],  # nutzer | modell | fremdmodell | kern
    }
    eintrag["pruefsumme"] = _pruefsumme(eintrag, _letzte_pruefsumme(datei))
    try:
        with open(datei or DATEI, "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    except OSError:
        # Ein kaputtes Protokoll darf den Operator nicht anhalten. Es ist ein Nachweis,
        # keine Sicherheitsschranke — die steht in der Schleuse.
        return None
    return eintrag


def lesen(datei=None, seit=None, bis=None):
    eintraege = []
    try:
        with open(datei or DATEI, encoding="utf-8") as f:
            for zeile in f:
                try:
                    e = json.loads(zeile)
                except ValueError:
                    continue
                if seit and e.get("zeit", "") < seit:
                    continue
                if bis and e.get("zeit", "") > bis:
                    continue
                eintraege.append(e)
    except OSError:
        pass
    return eintraege


def kette_pruefen(datei=None):
    """→ (heil?, meldung). Findet entfernte oder veränderte Zeilen."""
    vorher = ""
    for nr, e in enumerate(lesen(datei), 1):
        erwartet = e.get("pruefsumme", "")
        ohne = {k: v for k, v in e.items() if k != "pruefsumme"}
        if _pruefsumme(ohne, vorher) != erwartet:
            return False, (f"Das Protokoll wurde nachträglich verändert — die Kette "
                           f"bricht bei Eintrag {nr} vom {e.get('zeit', '?')}.")
        vorher = erwartet
    return True, "Das Protokoll ist lückenlos."


def aufraeumen(tage=None, datei=None):
    """Alte Einträge entfernen und die Kette neu bilden.

    Die Kette wird dabei bewusst NEU gebildet: Sonst wäre jedes Aufräumen von einem
    Angriff nicht zu unterscheiden. Der Zweck der Kette ist, **heimliche** Änderungen
    sichtbar zu machen — nicht, das Löschen zu verhindern, das die Datenschutz-
    Grundverordnung sogar verlangt.
    """
    tage = frist() if tage is None else tage
    grenze = time.strftime("%Y-%m-%dT%H:%M:%S",
                           time.localtime(time.time() - tage * 86400))
    behalten = [e for e in lesen(datei) if e.get("zeit", "") >= grenze]
    ziel = datei or DATEI
    vorher = ""
    with open(ziel, "w", encoding="utf-8") as f:
        for e in behalten:
            ohne = {k: v for k, v in e.items() if k != "pruefsumme"}
            ohne["pruefsumme"] = _pruefsumme(ohne, vorher)
            vorher = ohne["pruefsumme"]
            f.write(json.dumps(ohne, ensure_ascii=False) + "\n")
    return len(behalten)


def bericht(seit=None, bis=None, datei=None):
    """Der Absatz, den Petras Chef versteht — und der im Verkaufsgespräch zählt."""
    e = lesen(datei, seit, bis)
    if not e:
        return "In diesem Zeitraum hat der Operator nichts getan, was festzuhalten wäre."

    zaehler = {u: sum(1 for x in e if x["urteil"] == u) for u in URTEILE}
    ziele = sorted({x["ziel"] for x in e if x.get("ziel")})
    heil, kette = kette_pruefen(datei)

    handlung = "Handlung" if zaehler["ausgefuehrt"] == 1 else "Handlungen"
    text = [
        f"In diesem Zeitraum hat der Operator **{zaehler['ausgefuehrt']} {handlung} "
        f"ausgeführt**, **{zaehler['bestaetigung']} zur Bestätigung vorgelegt** und "
        f"**{zaehler['abgelehnt'] + zaehler['gesperrt']} selbst verweigert** "
        f"(davon {zaehler['gesperrt']} ohne Rückfrage, weil grundsätzlich untersagt)."]
    if ziele:
        text.append("Nach außen gingen Daten an: " + ", ".join(ziele) + ".")
    else:
        text.append("Es gingen keine Daten nach außen.")

    verweigert = [x for x in e if x["urteil"] in ("abgelehnt", "gesperrt")]
    if verweigert:
        gruende = {}
        for x in verweigert:
            gruende[x["grund"]] = gruende.get(x["grund"], 0) + 1
        text.append("Verweigert wurde vor allem: " + "; ".join(
            f"{g} ({n}×)" for g, n in sorted(gruende.items(), key=lambda p: -p[1])[:3]))
    text.append(kette if heil else "⚠️ " + kette)
    return "\n\n".join(text)


def markdown(seit=None, bis=None, datei=None):
    """Der Nachweis zum Weiterreichen — Fließtext plus die Rohtabelle darunter.

    Warum beides: Der Absatz ist für den Menschen, der fragt »was hat das Ding
    getan?«. Die Tabelle ist für den, der es nachrechnen will — mit Prüfsumme je
    Zeile, sodass die Kette außerhalb dieses Programms nachvollziehbar bleibt.
    Ein Nachweis, den man nur im eigenen Werkzeug prüfen kann, ist keiner.
    """
    e = lesen(datei, seit, bis)
    zeitraum = f"{seit or 'Beginn'} bis {bis or 'jetzt'}"
    zeilen = ["# Nachweis — Operator", "",
              f"Erstellt am {time.strftime('%d.%m.%Y um %H:%M')} Uhr  ",
              f"Zeitraum: {zeitraum}  ",
              f"Aufbewahrung: {frist()} Tage", "",
              bericht(seit, bis, datei), ""]
    if e:
        zeilen += ["## Einzelne Handlungen", "",
                   "| Zeit | Urteil | Werkzeug | Ziel | Auslöser | Grund | Prüfsumme |",
                   "|---|---|---|---|---|---|---|"]
        for x in e:
            zeilen.append("| " + " | ".join(
                str(x.get(k, "")).replace("|", "/") for k in
                ("zeit", "urteil", "werkzeug", "ziel", "herkunft", "grund", "pruefsumme")
            ) + " |")
        zeilen += ["", "Hinweis: Hier stehen Handlungen und Urteile — keine Inhalte, "
                       "keine Nachrichtentexte, keine Zugangsdaten."]
    return "\n".join(zeilen) + "\n"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "pruefen":
        heil, meldung = kette_pruefen()
        print(meldung)
        sys.exit(0 if heil else 1)
    print(bericht())
