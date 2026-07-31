#!/usr/bin/env python3
"""Vorfilter für personenbezogene Daten in Werkzeug-Ergebnissen (#88, stdlib-only).

Das Problem
-----------
Wenn ein Agent auf einem Fremdmodell (Ollama, OpenAI) läuft, wird der Prompt
pseudonymisiert — Namen und Adressen sieht das Modell nie im Klartext. Was der Agent
dann aber mit seinen Werkzeugen liest (Dateien, Mails, Webseiten), kommt aus der echten
Welt und geht **am Prompt vorbei** ans Modell. `llm_runner._sanitize_result()` ersetzt
bisher nur, was ohnehin schon in der Surrogat-Map steht. Ein neuer Name in einer
gelesenen Datei geht im Klartext raus.

Warum nicht einfach alles durch Presidio schicken
--------------------------------------------------
Weil es dann zu langsam wird. Bis zu 15 Werkzeug-Aufrufe pro Nachricht, jeder mit einem
IPC-Roundtrip zum Presidio-Dienst, jeder mit einer NER-Analyse über bis zu 16 KB Text.
Auf einem Raspberry Pi kippt das Feature. Und ein Operator, der zwar datensparsam, aber
träge ist, wird abgeschaltet — dann ist niemandem gedient (EINFACHHEIT.md).

Was dieses Modul tut
--------------------
Es beantwortet zwei Fragen, ohne Netz und ohne Modell:

1. **Was kann ich hier schon selbst entfernen?** Mail-Adressen, Telefonnummern, IBAN,
   Kreditkarten, IP-Adressen sind Muster — dafür braucht niemand ein Sprachmodell.
2. **Lohnt sich für den Rest die teure Prüfung?** Ein Verzeichnislisting, ein JSON-Blob
   oder eine Hex-Ausgabe enthält keine Personennamen. Prosa mit »Sehr geehrte Frau
   Zimmermann« schon.

Im Zweifel wird gesendet: Ein Fehlurteil hier darf nie dazu führen, dass echte Daten
ungeprüft rausgehen.
"""
import re

# ---------------------------------------------------------------- Strukturiertes --
# Diese Muster sind eindeutig genug, um sie ohne Sprachmodell zu ersetzen. Die Namen der
# Gruppen tauchen im Ersatztext auf — der Nutzer soll sehen, WAS entfernt wurde.
MUSTER = [
    ("Mail", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Za-z0-9]{4}[ ]?){2,7}[A-Za-z0-9]{1,4}\b")),
    ("Kreditkarte", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # Telefon ist das kniffligste Muster. Zwei Fehler aus dem ersten Wurf, beide gemessen:
    #   * »… unter 0821 4455-12.« wurde NICHT erkannt — der Punkt am Satzende ließ den
    #     Nachschau-Ausdruck scheitern. Eine Nummer am Satzende ist aber der Normalfall.
    #   * »(0821) 44 55 12« wurde nicht erkannt, weil nach der Vorwahl mindestens drei
    #     Ziffern am Stück verlangt wurden. Zweiergruppen sind in Deutschland üblich.
    # Der Nachschau-Ausdruck sperrt jetzt nur Wortzeichen (also Ziffern und Buchstaben),
    # nicht mehr den Punkt. Versionsnummern wie 1.26.1 schützt der Vorschau-Ausdruck.
    #   * »[2026-07-31 18:02:41] Listener gestartet« — ein Zeitstempel in JEDER Logzeile
    #     wurde zur Rufnummer. Der Vorschau-Ausdruck lehnt Datums-Anfänge jetzt ab, und
    #     der Nachschau-Ausdruck verhindert, dass mitten in einem Datum angesetzt wird.
    ("Telefon", re.compile(r"(?<![\w./-])"
                           r"(?!\d{4}[-/.]\d{1,2}[-/.]\d{1,2})(?!\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})"
                           r"(?:\+\d{1,3}[ /-]?)?\(?\d{2,5}\)?[ /-]?"
                           r"\d{2,}(?:[ /-]?\d{2,}){1,5}(?!\w)")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]

# Wörter, die häufig groß geschrieben am Satzanfang stehen und keine Namen sind. Bewusst
# klein gehalten — das ist ein Vorfilter, keine Wörterbuchprüfung (die macht pseudonym.py).
_KEINE_NAMEN = {
    "Der", "Die", "Das", "Ein", "Eine", "Und", "Oder", "Aber", "Auch", "Wenn", "Dann",
    "Ich", "Du", "Er", "Sie", "Es", "Wir", "Ihr", "Hallo", "Guten", "Liebe", "Lieber",
    "Mit", "Für", "Von", "Bei", "Nach", "Vor", "Über", "Unter", "Am", "Im", "Zum", "Zur",
    "Datei", "Ordner", "Fehler", "Warnung", "Hinweis", "Gesamt", "Summe", "Datum", "Zeit",
    "True", "False", "None", "Null", "Error", "Warning", "Info", "Debug",
}
_ANREDE = re.compile(r"\b(?:Herr|Frau|Herrn|Sehr geehrte[rn]?|Liebe[rn]?|an|von|für)\s+$",
                     re.IGNORECASE)
_GROSSWORT = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]{2,}\b")
_WORTARTIG = re.compile(r"\b[A-Za-zÄÖÜäöüß]{3,}\b")


# Ein Datum sieht einer Telefonnummer zum Verwechseln ähnlich: »2026-07-31« passt auf
# »Vorwahl, Trenner, Ziffern, Trenner, Ziffern«. Im ersten Wurf wurde jedes Datum in einem
# Log oder Dateilisting zur Telefonnummer — massenhafter Fehlalarm in genau den Texten,
# die Werkzeuge am häufigsten liefern. Deshalb eine echte Prüfung statt noch eines Regex.
_DATUM = re.compile(r"^(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})$")


def _ist_datum(treffer):
    return bool(_DATUM.match(treffer.strip()))


def strukturiert_entfernen(text):
    """Mail, Telefon, IBAN, Karte, IP durch sprechende Platzhalter ersetzen.

    Rückgabe: (bereinigter_text, anzahl). Braucht kein Sprachmodell und läuft immer —
    auch wenn der Presidio-Dienst gerade nicht erreichbar ist."""
    n = 0
    for name, muster in MUSTER:
        def ersetzen(m, _name=name):
            nonlocal n
            if _name == "Telefon" and _ist_datum(m.group()):
                return m.group()          # Datum, keine Rufnummer
            n += 1
            return f"[{_name} entfernt]"
        text = muster.sub(ersetzen, text or "")
    return text, n


def _prosaartig(zeile):
    """Sieht diese Zeile nach Fließtext aus — oder nach Maschinenausgabe?

    Ein Verzeichnislisting, eine JSON-Zeile oder ein Hex-Dump enthält keine
    Personennamen. Die teure Namenserkennung dort laufen zu lassen, kostet Zeit und
    produziert obendrein Fehlalarme (real erlebt: #107 »FERTIG« wurde zum Firmennamen,
    #102 »Satelitenmodus« zu »Dinkelsbühl«)."""
    if len(zeile) < 5:
        return False
    woerter = _WORTARTIG.findall(zeile)
    if not woerter:
        return False
    # Anteil wortartiger Zeichen — bei JSON, Hex und Pfaden ist der niedrig.
    anteil = sum(len(w) for w in woerter) / max(len(zeile), 1)
    if anteil < 0.45:
        return False
    if len(woerter) >= 3:
        return True
    # Kurze Zeilen mit genau zwei Wörtern: Beim ersten Live-Lauf blieb die Signaturzeile
    # »Katrin Zimmermann« stehen, weil sie die Drei-Wort-Schwelle nicht erreichte — und
    # das ist die Stelle, an der in einer Mail garantiert ein Name steht. Zwei
    # großgeschriebene Wörter allein in einer Zeile sind fast immer ein Name.
    return len(woerter) == 2 and len(_GROSSWORT.findall(zeile)) == 2


def namensverdacht(zeile):
    """Könnte in dieser Zeile ein Personenname stehen?

    Zwei Signale, bewusst großzügig: ein großgeschriebenes Wort nach einer Anrede
    (»Herr Weber«), oder zwei großgeschriebene Wörter hintereinander (»Anna Weber«)."""
    if not _prosaartig(zeile):
        return False
    for treffer in _GROSSWORT.finditer(zeile):
        wort = treffer.group()
        if wort in _KEINE_NAMEN:
            continue
        davor = zeile[:treffer.start()]
        if _ANREDE.search(davor):
            return True
        danach = zeile[treffer.end():treffer.end() + 40]
        folge = _GROSSWORT.match(danach.lstrip())
        if folge and folge.group() not in _KEINE_NAMEN:
            return True
    return False


def zeilen_pruefen(text, max_zeichen=8000):
    """Welche Zeilen lohnen die teure Namenserkennung?

    Rückgabe: (indizes, verworfen_ab). `indizes` sind die Zeilen, die geprüft werden
    sollen. `verworfen_ab` ist der Zeilenindex, ab dem nichts mehr geprüft wird, weil das
    Zeichenbudget erschöpft ist (None = alles passt).

    Das Budget ist kein Schönheitsfehler: Ohne Grenze könnte eine 16-KB-Datei die
    Antwortzeit verdoppeln. Was nicht mehr hineinpasst, wird vom Aufrufer **verworfen**
    und nicht etwa ungeprüft durchgereicht (Entscheidung Michi, 31.07.2026)."""
    zeilen = (text or "").splitlines()
    indizes, budget = [], max_zeichen
    for i, zeile in enumerate(zeilen):
        if not namensverdacht(zeile):
            continue
        if len(zeile) > budget:
            return indizes, i
        budget -= len(zeile)
        indizes.append(i)
    return indizes, None
