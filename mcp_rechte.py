#!/usr/bin/env python3
"""Welche Werkzeuge eines MCP-Servers dürfen ohne Rückfrage lesen? (#158)

Das Problem
-----------
Die Schleuse hält bei einem **unbekannten** MCP-Server jedes Werkzeug für schreibend.
Das ist als Grundhaltung richtig — nur zahlt der Alltag den Preis: Beim Prüfstandslauf
am 03.08. galten **18 von 21 Stopps reinen Lesezugriffen**, und beide geschützten Wege
fielen von 12 auf 2 erledigte Arbeitsaufgaben.

Im Betrieb heißt dasselbe: Wer sich einen weiteren Konnektor einrichtet, wird vor jedem
Blick ins Postfach gefragt. Nach `EINFACHHEIT.md` ist das unbenutzbar — und der
wahrscheinlichste Ausgang ist, dass irgendwann alles durchgewinkt wird. **Eine Schranke,
die zu oft fragt, wird zur Schranke, die niemand mehr liest.**

Die Lösung: einmal entscheiden statt fünfzigmal fragen
------------------------------------------------------
Beim Anbinden eines Servers wird **einmal** festgelegt, welche seiner Werkzeuge nur
lesen. Diese Entscheidung liegt hier, sichtbar und jederzeit änderbar.

Es bleibt fail-closed: Was nicht ausdrücklich eingetragen ist, braucht weiter eine
Bestätigung. Diese Datei kann nur erlauben, was ohnehin nur liest — sie kann nichts
freigeben, was in `RISKY_TOOLS` oder der Sperrliste steht; der Broker prüft die zuerst.

Warum `readOnlyHint` nur ein Vorschlag ist — und nie mehr
----------------------------------------------------------
Das MCP-Protokoll erlaubt Servern, ein Werkzeug selbst als »nur lesend« zu kennzeichnen.
Das ist bequem und wäre hier ein Fehler: **Die Auskunft kommt von genau der Stelle, vor
der die Schleuse schützt.** Ein übernommener oder böswilliger Server bräuchte ein Feld im
JSON, um an der Bestätigungspflicht vorbeizukommen.

Deshalb wandert `readOnlyHint` in den **Vorschlag**, den der Nutzer bestätigt — nie
direkt in die Erlaubnis. Dasselbe gilt für Namensmuster: `*_list` sieht harmlos aus, aber
`mail_list_delete` löscht. Ein Schutz, der an einer Zeichenkette hängt, ist keiner.
"""
import json
import os

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
# Umlenkbar — und das ist keine Bequemlichkeit, sondern die Lehre aus #157: Der
# Prüfstand benutzt dieselben Funktionen wie der Betrieb und schrieb seine Testdaten
# in die Betriebsdatei. Ein Messlauf, der die echte Einrichtung verändert, ist ein
# Messlauf, der Schaden anrichtet. Er setzt jetzt OPERATOR_MCP_RECHTE auf eine Datei
# in seinem Wegwerf-Ordner.
DATEI = os.path.join(BOT_DIR, "connections", "mcp_rechte.json")


def _datei():
    """Der Ablageort — **bei jedem Zugriff neu gelesen**, nicht einmal beim Import.

    Der erste Anlauf las die Umgebungsvariable auf Modulebene. Das sieht gleichwertig
    aus und ist es nicht: Wer das Modul früher importiert, als die Variable gesetzt
    wird, bekommt für den Rest des Laufs die falsche Datei — und der Prüfstand
    schrieb weiter in die Betriebsdatei, obwohl die Umlenkung »eingebaut« war.
    Eine Verdrahtung, die von der Importreihenfolge abhängt, ist keine.
    """
    return os.environ.get("OPERATOR_MCP_RECHTE") or DATEI

# Wortteile, die auf Lesen deuten — ausschliesslich fuer den VORSCHLAG.
LESE_WOERTER = ("list", "read", "get", "search", "such", "lesen", "zeig", "show",
                "info", "status", "find", "abfrage", "query", "view")
# Wortteile, die einen Vorschlag sofort zunichte machen. Sie gewinnen immer — auch
# wenn ein Lesewort daneben steht (»mail_list_delete«).
SCHREIB_WOERTER = ("send", "delete", "remove", "loesch", "losch", "create", "add",
                   "update", "write", "schreib", "upload", "activate", "trigger",
                   "execute", "run", "start", "stop", "move", "copy", "rename",
                   "absag", "weiterleit", "forward", "reply", "antwort", "post",
                   "put", "patch", "install", "set", "aendern", "andern")


def _laden():
    try:
        with open(_datei(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _speichern(daten):
    ziel = _datei()
    os.makedirs(os.path.dirname(ziel) or ".", exist_ok=True)
    with open(ziel, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1, sort_keys=True)


def lesend(voller_name):
    """Darf »mcp__buero__mail_list« ohne Rückfrage lesen? Im Zweifel: nein."""
    teile = (voller_name or "").split("__")
    if len(teile) < 3 or teile[0] != "mcp":
        return False
    return teile[2] in (_laden().get(teile[1], {}).get("lesend") or [])


def bekannt(server):
    """Wurde für diesen Server schon einmal entschieden?

    Der Unterschied zu »hat keine Lesewerkzeuge« ist wichtig: Beim einen ist noch nichts
    gefragt worden, beim anderen hat der Nutzer bewusst nichts freigegeben.
    """
    return server in _laden()


def vorschlag(werkzeuge):
    """→ [{name, lesend, grund}] — ein Vorschlag zum Bestätigen, keine Entscheidung.

    `werkzeuge` sind die Rohangaben aus `tools/list`.
    """
    raus = []
    for w in werkzeuge or []:
        name = w.get("name", "")
        klein = name.lower()
        hinweis = ((w.get("annotations") or {}).get("readOnlyHint") is True)
        schreibt = any(s in klein for s in SCHREIB_WOERTER)
        liest = any(l in klein for l in LESE_WOERTER)

        if schreibt:
            # Gewinnt immer — auch gegen readOnlyHint. Ein Server, der »delete« im
            # Namen fuehrt und sich selbst als lesend bezeichnet, widerspricht sich;
            # in dem Fall glauben wir dem Namen.
            wahl, grund = False, "der Name deutet auf eine Änderung hin"
        elif hinweis:
            wahl, grund = True, "der Dienst bezeichnet es selbst als nur lesend"
        elif liest:
            wahl, grund = True, "der Name deutet auf reines Nachsehen hin"
        else:
            wahl, grund = False, "unklar — im Zweifel lieber nachfragen"
        raus.append({"name": name, "lesend": wahl, "grund": grund,
                     "beschreibung": str(w.get("description") or "")[:160]})
    return raus


def setzen(server, lesende_namen):
    """Die Entscheidung des Nutzers festhalten. Ersetzt die bisherige vollständig."""
    daten = _laden()
    daten[server] = {"lesend": sorted({str(n) for n in (lesende_namen or [])})}
    _speichern(daten)
    return daten[server]


def vergessen(server):
    daten = _laden()
    if daten.pop(server, None) is None:
        return False
    _speichern(daten)
    return True


def alle():
    return _laden()


def alle_lesenden():
    """Alle bestätigten Lesewerkzeuge als volle Namen: {»mcp__buero__mail_list«, …}.

    Für die Schleuse: Die urteilt bewusst ohne Dateizugriff und bekommt ihre Listen
    von aussen gereicht. Diese Funktion ist die Brücke.
    """
    return {f"mcp__{server}__{w}"
            for server, d in _laden().items()
            for w in (d.get("lesend") or [])}
