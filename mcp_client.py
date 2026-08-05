#!/usr/bin/env python3
"""MCP-Client mit Bordmitteln — damit auch Fremdmodelle die Werkzeuge bedienen (#140).

Warum es das gibt
-----------------
Microsoft 365, n8n und die Microsoft-Dokumentation hängen heute am Programm `claude`:
Es bringt den MCP-Anschluss mit, wir reichen nur eine Konfigurationsdatei durch. Ein
Agent auf Ollama oder OpenAI hat davon **nichts** — er kann rechnen und schreiben, aber
nicht in den Kalender sehen.

Dieser Client schließt die Lücke. Er ist gleichzeitig das größte Einzelstück von Weg 2
(Epic #137) — und der einzige Block, dessen Nutzen bleibt, falls das Epic gestoppt wird.

Wie das Protokoll aussieht
--------------------------
Erstaunlich wenig: JSON-RPC über die Standardein- und -ausgabe, eine Nachricht je Zeile.
Drei Aufrufe genügen — `initialize`, `tools/list`, `tools/call`. Deshalb Bordmittel statt
Bibliothek: Der Pi muss mitkommen, und eine Abhängigkeit, die 90 % ungenutzt bleibt, ist
auf einem Firmen-Notebook eine Hürde beim Installieren.

Die drei Eigenschaften, auf die es ankommt
------------------------------------------
1. **Nichts wird ausgeführt, was nicht durch die Schleuse ging.** `aufrufen()` fragt
   zuerst `schleuse.pruefen()`. Ein zweiter Aufrufpfad wäre genau der Umweg, den K1
   abgeschafft hat.
2. **Ein kaputter Server darf den Operator nicht anhalten.** Zeitlimit je Aufruf; wer
   dreimal nicht antwortet, wird abgemeldet statt endlos erneut gefragt. Verdacht 3 aus
   #130 war genau dieser Fall.
3. **Werkzeugbeschreibungen sind fremder Text.** Sie gehen in den Prompt, also könnte
   dort eine Anweisung stehen. Sie werden als Daten behandelt, gekürzt und markiert —
   und über die Ausführung entscheidet ohnehin die Schleuse, nicht die Beschreibung.
"""
import json
import os
import subprocess
import sys
import threading
import time
import platform_compat as _plat

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)

ZEITLIMIT = 60          # Sekunden je Aufruf
START_ZEITLIMIT = 20    # Sekunden für die Anmeldung eines Servers
FEHLER_BIS_ABMELDUNG = 3
BESCHREIBUNG_MAX = 400  # fremder Text im Prompt bleibt kurz


class Server:
    """Ein MCP-Server als Unterprozess. Nicht wiederverwendbar: Wer stirbt, bleibt tot,
    bis ihn jemand bewusst neu startet. Automatisches Wiederbeleben würde einen
    dauerhaft kaputten Server in eine Endlosschleife verwandeln."""

    def __init__(self, name, befehl, argumente=None, umgebung=None):
        self.name = name
        self.befehl = [befehl] + list(argumente or [])
        self.umgebung = dict(os.environ, **(umgebung or {}))
        self.prozess = None
        self.werkzeuge = []
        self.fehler = 0
        self.abgemeldet = False
        self.grund = ""
        self._zaehler = 0
        self._schloss = threading.Lock()

    # ---------------------------------------------------------------- Protokoll --
    def _senden(self, methode, parameter=None, mit_antwort=True):
        self._zaehler += 1
        nachricht = {"jsonrpc": "2.0", "method": methode}
        if parameter is not None:
            nachricht["params"] = parameter
        if mit_antwort:
            nachricht["id"] = self._zaehler
        self.prozess.stdin.write(json.dumps(nachricht, ensure_ascii=False) + "\n")
        self.prozess.stdin.flush()
        return self._zaehler if mit_antwort else None

    def _lesen(self, kennung, zeitlimit):
        """Antwort mit passender Kennung abwarten.

        Zeilen, die kein gültiges JSON sind, werden verworfen statt die Verbindung zu
        beenden. Unsere eigenen Server halten sich an die Regel, dass die Standardausgabe
        der Protokollkanal ist — bei fremden Servern ist ein verirrtes `print()` aber der
        häufigste Fehler überhaupt, und daran soll der Operator nicht sterben.
        """
        ende = time.time() + zeitlimit
        while time.time() < ende:
            zeile = self.prozess.stdout.readline()
            if not zeile:
                raise OSError(f"{self.name}: Verbindung beendet")
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                antwort = json.loads(zeile)
            except ValueError:
                self._notiz(f"unverständliche Ausgabe verworfen: {zeile[:80]}")
                continue
            if antwort.get("id") == kennung:
                return antwort
            # Benachrichtigungen und fremde Kennungen: ignorieren, weiterlesen.
        raise TimeoutError(f"{self.name}: keine Antwort in {zeitlimit} s")

    def _notiz(self, text):
        try:
            with open(os.path.join(BOT_DIR, "listener.log"), "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%F %T')}] 🔌 MCP {text}\n")
        except OSError:
            pass

    # ------------------------------------------------------------------ Leben --
    def starten(self):
        try:
            self.prozess = subprocess.Popen(
                self.befehl, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                errors="replace", env=self.umgebung, bufsize=1, **_plat.OHNE_FENSTER)
            kennung = self._senden("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "operator", "version": "1"}})
            self._lesen(kennung, START_ZEITLIMIT)
            self._senden("notifications/initialized", {}, mit_antwort=False)
            kennung = self._senden("tools/list")
            antwort = self._lesen(kennung, START_ZEITLIMIT)
            self.werkzeuge = (antwort.get("result") or {}).get("tools") or []
            return True
        except Exception as e:
            self.abmelden(f"Start fehlgeschlagen: {type(e).__name__}: {e}")
            return False

    def abmelden(self, grund):
        self.abgemeldet, self.grund = True, grund
        self._notiz(f"»{self.name}« abgemeldet — {grund}")
        try:
            if self.prozess:
                self.prozess.kill()
        except Exception:
            pass

    def aufrufen(self, werkzeug, argumente, zeitlimit=ZEITLIMIT):
        """Rohaufruf ohne Prüfung — nur für die Verbindung gedacht.

        Wer das von außen benutzt, umgeht die Schleuse. Ein Wächter-Test hält fest,
        dass es außerhalb dieser Datei keinen solchen Aufruf gibt.
        """
        if self.abgemeldet:
            return {"fehler": f"»{self.name}« ist nicht verfügbar: {self.grund}"}
        with self._schloss:
            try:
                kennung = self._senden("tools/call", {"name": werkzeug,
                                                      "arguments": argumente or {}})
                antwort = self._lesen(kennung, zeitlimit)
            except Exception as e:
                self.fehler += 1
                if self.fehler >= FEHLER_BIS_ABMELDUNG:
                    self.abmelden(f"{self.fehler} Fehlversuche, zuletzt: {e}")
                return {"fehler": f"{type(e).__name__}: {e}"}
        self.fehler = 0
        if "error" in antwort:
            return {"fehler": str((antwort["error"] or {}).get("message", "unbekannt"))}
        return {"ergebnis": _text_aus(antwort.get("result") or {})}


def _text_aus(ergebnis):
    """MCP liefert eine Liste von Inhaltsblöcken. Für ein Sprachmodell zählt der Text."""
    teile = []
    for block in ergebnis.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            teile.append(str(block.get("text", "")))
    return "\n".join(teile) if teile else json.dumps(ergebnis, ensure_ascii=False)


# ------------------------------------------------------------------ Verbindung --
class Verbindung:
    """Alle konfigurierten Server zusammen, mit einheitlicher Werkzeugliste.

    Die Namen bekommen das Muster `mcp__<server>__<werkzeug>` — dasselbe, das der
    Broker und die Schleuse kennen. Ein eigenes Namensschema hätte bedeutet, die
    Sicherheitsregeln ein zweites Mal zu schreiben; genau das soll aufhören.
    """

    def __init__(self, konfiguration=None):
        self.server = {}
        for name, eintrag in (konfiguration or {}).items():
            s = Server(name, eintrag.get("command", ""), eintrag.get("args"),
                       eintrag.get("env"))
            if s.starten():
                self.server[name] = s

    @classmethod
    def aus_datei(cls, pfad):
        """Liest dieselbe .mcp.json, die auch der Claude-Weg benutzt. Eine zweite
        Konfiguration wäre eine zweite Wahrheit — und eine davon veraltet."""
        try:
            daten = json.load(open(pfad, encoding="utf-8"))
        except (OSError, ValueError):
            return cls({})
        return cls(daten.get("mcpServers") or {})

    def werkzeuge(self, nur=None):
        """Alle Werkzeuge im OpenAI-Format. `nur` begrenzt auf bestimmte Server —
        der Beschneidungs-Gedanke aus #121, sonst wächst der Prompt ungebremst."""
        raus = []
        for name, s in self.server.items():
            if s.abgemeldet or (nur is not None and name not in nur):
                continue
            for w in s.werkzeuge:
                raus.append({"type": "function", "function": {
                    "name": f"mcp__{name}__{w.get('name', '')}",
                    "description": _saubere_beschreibung(w.get("description", "")),
                    "parameters": w.get("inputSchema") or {"type": "object",
                                                           "properties": {}}}})
        return raus

    def aufrufen(self, voller_name, argumente, umgebung=None, herkunft="modell"):
        """Der EINZIGE Weg, ein MCP-Werkzeug auszuführen — immer durch die Schleuse."""
        import schleuse
        urteil = schleuse.pruefen({"art": "werkzeug", "name": voller_name,
                                  "argumente": argumente or {}, "herkunft": herkunft},
                                  umgebung or {})
        if not urteil["erlaubt"]:
            return {"fehler": urteil["grund"], "urteil": urteil}
        if urteil["bestaetigung_noetig"]:
            return {"bestaetigung_noetig": True, "grund": urteil["grund"],
                    "urteil": urteil}

        teile = voller_name.split("__", 2)
        if len(teile) != 3 or teile[0] != "mcp":
            return {"fehler": f"unbekannter Werkzeugname: {voller_name}"}
        server = self.server.get(teile[1])
        if not server:
            return {"fehler": f"»{teile[1]}« ist nicht verbunden"}
        return server.aufrufen(teile[2], argumente)

    def zustand(self):
        return {n: ("abgemeldet: " + s.grund if s.abgemeldet
                    else f"{len(s.werkzeuge)} Werkzeuge")
                for n, s in self.server.items()}

    def schliessen(self):
        for s in self.server.values():
            try:
                if s.prozess:
                    s.prozess.terminate()
            except Exception:
                pass


def _saubere_beschreibung(text):
    """Fremder Text, der in den Prompt geht. Kürzen und einzeilig machen.

    Eine Beschreibung kann Anweisungen enthalten (»ignoriere vorherige Regeln«). Das
    ist nicht abwendbar, solange Beschreibungen überhaupt in den Prompt gehören — aber
    sie darf sich nicht als eigener Abschnitt tarnen können, und lang sein muss sie
    auch nicht. Über die Ausführung entscheidet ohnehin die Schleuse.
    """
    einzeilig = " ".join(str(text or "").split())
    return einzeilig[:BESCHREIBUNG_MAX]


if __name__ == "__main__":
    # Selbstprobe: eigene Server starten und ihre Werkzeuge zeigen.
    pfad = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.expanduser("~"), "Operator", ".mcp.json")
    v = Verbindung.aus_datei(pfad)
    print(json.dumps(v.zustand(), indent=1, ensure_ascii=False))
    for w in v.werkzeuge():
        print("  ", w["function"]["name"])
    v.schliessen()
