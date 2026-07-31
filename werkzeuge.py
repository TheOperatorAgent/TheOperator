#!/usr/bin/env python3
"""Der eigene Werkzeugkasten — jedes Werkzeug nur über die Schleuse (#141, Epic #137).

Warum es das gibt
-----------------
Lesen, Schreiben, Suchen und Befehle ausführen bringt heute das Programm `claude` mit.
Für einen eigenen Kern brauchen wir sie selbst — und zwar so, dass **kein** Werkzeug an
der Schleuse (#139) vorbeikommt.

Der Schnitt, auf den es ankommt
-------------------------------
Ein Werkzeug ist **eine Beschreibung plus eine Ausführungsfunktion**, und die Beschreibung
steht **einmal**. Anthropic und OpenAI erwarten unterschiedliche Formate, aber nicht
unterschiedliche Werkzeuge. Zwei Listen wären zwei Wahrheiten, und eine davon wäre in
einem halben Jahr veraltet — genau das Muster, das bei den zwei Installern zweimal
Fehler erzeugt hat (#126).

Ausführungsort ist Konfiguration, nicht Code
--------------------------------------------
*Wo* ein Befehl läuft (hier, in einer Sandbox, später auf einem anderen Rechner) gehört
in die Umgebung. Der Gedanke stammt von Hermes — dort ist er allerdings so gebaut, dass
die interessanten Fälle Docker voraussetzen. Bei uns bleibt »hier« die Voreinstellung
und alles Weitere ist optional: Der Pi und ein verwaltetes Firmen-Notebook müssen
mitkommen.
"""
import os
import re
import subprocess
import sys

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)
import schleuse                                              # noqa: E402

MAX_AUSGABE = 30000        # Zeichen je Werkzeug-Ergebnis
MAX_DATEI = 200000         # Zeichen beim Lesen
BEFEHL_ZEITLIMIT = 60
MAX_TREFFER = 200


def _kappen(text, grenze=MAX_AUSGABE):
    text = text or ""
    if len(text) <= grenze:
        return text
    # Sichtbar kappen. Eine stillschweigend abgeschnittene Ausgabe lässt das Modell
    # glauben, es habe alles gesehen — und es zieht falsche Schlüsse.
    return text[:grenze] + f"\n[… gekürzt, {len(text) - grenze} Zeichen mehr]"


def _pfad(arbeitsordner, roh):
    """Auflösen, bevor die Schleuse urteilt. Sie prüft rein textlich (damit sie ohne
    Dateisystem testbar bleibt) und verlangt dafür einen bereits aufgelösten Pfad —
    das Auflösen ist unsere Aufgabe, nicht ihre."""
    voll = os.path.expanduser(roh or "")
    if not os.path.isabs(voll):
        voll = os.path.join(arbeitsordner, voll)
    return os.path.realpath(voll)


# ------------------------------------------------------------------ Werkzeuge --
def _lies(args, umgebung):
    p = _pfad(umgebung["arbeitsordner"], args.get("pfad"))
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return _kappen(f.read(MAX_DATEI))
    except OSError as e:
        return f"Konnte nicht gelesen werden: {e.strerror or e}"


def _schreib(args, umgebung):
    p = _pfad(umgebung["arbeitsordner"], args.get("pfad"))
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(str(args.get("inhalt") or ""))
        return f"Geschrieben: {os.path.basename(p)}"
    except OSError as e:
        return f"Konnte nicht geschrieben werden: {e.strerror or e}"


def _aendere(args, umgebung):
    """Ersetzt GENAU den angegebenen Text. Kein »ungefähr passend«.

    Ein Werkzeug, das ähnliche Stellen ersetzt, zerstört stillschweigend Dateien —
    und zwar so, dass es niemand bemerkt, bis etwas nicht mehr funktioniert.
    """
    p = _pfad(umgebung["arbeitsordner"], args.get("pfad"))
    alt, neu = str(args.get("alt") or ""), str(args.get("neu") or "")
    if not alt:
        return "Es fehlt der zu ersetzende Text."
    try:
        with open(p, encoding="utf-8") as f:
            inhalt = f.read()
    except OSError as e:
        return f"Konnte nicht gelesen werden: {e.strerror or e}"
    treffer = inhalt.count(alt)
    if treffer == 0:
        return "Der zu ersetzende Text kommt in der Datei nicht vor — nichts geändert."
    if treffer > 1:
        return (f"Der Text kommt {treffer}-mal vor. Bitte mehr Zusammenhang angeben, "
                "damit die Stelle eindeutig ist — nichts geändert.")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(inhalt.replace(alt, neu, 1))
    except OSError as e:
        return f"Konnte nicht geschrieben werden: {e.strerror or e}"
    return f"Geändert: {os.path.basename(p)}"


def _liste(args, umgebung):
    p = _pfad(umgebung["arbeitsordner"], args.get("pfad") or ".")
    raus = []
    for wurzel, ordner, dateien in os.walk(p):
        ordner[:] = [o for o in ordner if not o.startswith(".")]
        for d in dateien:
            raus.append(os.path.relpath(os.path.join(wurzel, d), p))
            if len(raus) >= MAX_TREFFER:
                return "\n".join(raus) + f"\n[… mehr als {MAX_TREFFER} Dateien]"
    return "\n".join(raus) or "(leer)"


def _suche(args, umgebung):
    p = _pfad(umgebung["arbeitsordner"], args.get("pfad") or ".")
    try:
        muster = re.compile(str(args.get("muster") or ""), re.IGNORECASE)
    except re.error as e:
        return f"Das Suchmuster ist fehlerhaft: {e}"
    raus = []
    for wurzel, ordner, dateien in os.walk(p):
        ordner[:] = [o for o in ordner if not o.startswith(".")]
        for d in dateien:
            voll = os.path.join(wurzel, d)
            try:
                with open(voll, encoding="utf-8", errors="replace") as f:
                    for nr, zeile in enumerate(f, 1):
                        if muster.search(zeile):
                            raus.append(f"{os.path.relpath(voll, p)}:{nr}: {zeile.strip()[:200]}")
                            if len(raus) >= MAX_TREFFER:
                                return "\n".join(raus) + "\n[… weitere Treffer]"
            except OSError:
                continue
    return "\n".join(raus) or "Keine Treffer."


def _befehl(args, umgebung):
    cmd = str(args.get("befehl") or "")
    lauf = umgebung.get("ausfuehren") or _hier_ausfuehren
    try:
        return _kappen(lauf(cmd, umgebung["arbeitsordner"]))
    except subprocess.TimeoutExpired:
        return f"Abgebrochen: länger als {BEFEHL_ZEITLIMIT} Sekunden."
    except OSError as e:
        return f"Konnte nicht ausgeführt werden: {e}"


def _hier_ausfuehren(cmd, arbeitsordner):
    """Voreinstellung: auf diesem Rechner. Sandbox und Fernausführung docken hier an,
    indem die Umgebung ein anderes `ausfuehren` mitgibt — kein Docker-Zwang."""
    r = subprocess.run(cmd, shell=True, cwd=arbeitsordner, capture_output=True,
                       text=True, timeout=BEFEHL_ZEITLIMIT, errors="replace")
    ausgabe = (r.stdout or "") + (("\n[Fehlerausgabe]\n" + r.stderr) if r.stderr else "")
    return ausgabe if ausgabe.strip() else f"(keine Ausgabe, Rückgabewert {r.returncode})"


# --------------------------------------------- Eine Beschreibung, zwei Formate --
KASTEN = [
    {"name": "lies", "zweck": "Liest eine Datei und gibt ihren Inhalt zurück.",
     "art": "datei_lesen", "pfadfeld": "pfad", "fn": _lies,
     "felder": {"pfad": ("string", "Pfad zur Datei")}, "pflicht": ["pfad"]},
    {"name": "schreib", "zweck": "Schreibt eine Datei (vorhandener Inhalt wird ersetzt).",
     "art": "datei_schreiben", "pfadfeld": "pfad", "fn": _schreib,
     "felder": {"pfad": ("string", "Pfad zur Datei"),
                "inhalt": ("string", "der neue Inhalt")}, "pflicht": ["pfad", "inhalt"]},
    {"name": "aendere", "zweck": "Ersetzt in einer Datei genau eine Textstelle.",
     "art": "datei_schreiben", "pfadfeld": "pfad", "fn": _aendere,
     "felder": {"pfad": ("string", "Pfad zur Datei"),
                "alt": ("string", "der Text, der ersetzt wird — muss genau passen"),
                "neu": ("string", "der neue Text")}, "pflicht": ["pfad", "alt", "neu"]},
    {"name": "liste", "zweck": "Listet die Dateien in einem Ordner auf.",
     "art": "datei_lesen", "pfadfeld": "pfad", "fn": _liste,
     "felder": {"pfad": ("string", "Ordner, Vorgabe ist der Arbeitsordner")},
     "pflicht": []},
    {"name": "suche", "zweck": "Sucht ein Textmuster in allen Dateien eines Ordners.",
     "art": "datei_lesen", "pfadfeld": "pfad", "fn": _suche,
     "felder": {"muster": ("string", "wonach gesucht wird"),
                "pfad": ("string", "Ordner, Vorgabe ist der Arbeitsordner")},
     "pflicht": ["muster"]},
    {"name": "befehl", "zweck": "Führt einen Befehl im Arbeitsordner aus.",
     "art": "befehl", "befehlsfeld": "befehl", "fn": _befehl,
     "felder": {"befehl": ("string", "der auszuführende Befehl")}, "pflicht": ["befehl"]},
]
NACH_NAME = {w["name"]: w for w in KASTEN}


def beschreibungen(format="openai"):
    """EINE Quelle, zwei Formate. Ein zweiter Satz Beschreibungen wäre eine zweite
    Wahrheit — und die veraltet."""
    raus = []
    for w in KASTEN:
        schema = {"type": "object",
                  "properties": {k: {"type": t, "description": b}
                                 for k, (t, b) in w["felder"].items()},
                  "required": w["pflicht"]}
        if format == "anthropic":
            raus.append({"name": w["name"], "description": w["zweck"],
                         "input_schema": schema})
        else:
            raus.append({"type": "function",
                         "function": {"name": w["name"], "description": w["zweck"],
                                      "parameters": schema}})
    return raus


def ausfuehren(name, argumente, umgebung, herkunft="modell"):
    """Der EINZIGE Weg, ein Werkzeug auszuführen. Immer erst die Schleuse."""
    w = NACH_NAME.get(name)
    if not w:
        return {"fehler": f"Unbekanntes Werkzeug: {name}"}
    argumente = argumente or {}
    arbeitsordner = os.path.realpath(umgebung.get("arbeitsordner") or ".")
    umgebung = dict(umgebung, arbeitsordner=arbeitsordner)

    handlung = {"art": w["art"], "name": name, "argumente": {},
                "herkunft": herkunft, "sitzung": umgebung.get("sitzung")}
    if w.get("pfadfeld"):
        handlung["argumente"]["pfad"] = _pfad(arbeitsordner,
                                              argumente.get(w["pfadfeld"]) or ".")
    if w.get("befehlsfeld"):
        handlung["argumente"]["befehl"] = str(argumente.get(w["befehlsfeld"]) or "")

    urteil = schleuse.pruefen(handlung, umgebung)
    if not urteil["erlaubt"]:
        return {"fehler": urteil["grund"], "urteil": urteil}
    if urteil["bestaetigung_noetig"]:
        return {"bestaetigung_noetig": True, "grund": urteil["grund"], "urteil": urteil}
    return {"ergebnis": w["fn"](argumente, umgebung), "urteil": urteil}
