#!/usr/bin/env python3
"""Die Agentenschleife — der eigene Kern (#143, Epic #137).

Warum es das gibt
-----------------
K1 bis K4 haben die Teile gebaut: die Schleuse (#139), den MCP-Client (#140), den
Werkzeugkasten (#141) und die Anbieter (#142). Hier laufen sie zum ersten Mal zusammen —
das ist die Stelle, an der aus Bausteinen ein Assistent wird.

Die Schleife selbst ist einfach: fragen, Werkzeuge ausführen, wieder fragen, bis eine
Antwort ohne Werkzeugwunsch kommt. Schwierig sind die drei Dinge drumherum.

**1. Kürzen.** Der Verlauf wächst mit jedem Werkzeugergebnis. Irgendwann passt er nicht
mehr, und dann entscheidet die Kürzungsregel darüber, woran sich der Assistent erinnert.
Hier fliegen **alte Werkzeugergebnisse zuerst** — sie sind lang, und ihr Inhalt ist meist
schon in der Antwort des Modells verarbeitet. Der Auftrag des Nutzers und die
Systemanweisung bleiben immer.

**2. Im Kreis drehen.** Ein Modell, das dasselbe Werkzeug mit denselben Argumenten immer
wieder aufruft, hat sich verrannt. Ohne Erkennung läuft es bis zum Schrittlimit und
kostet dabei Geld für nichts. Wir brechen ab und sagen ehrlich, woran es lag.

**3. Bestätigungen.** Sagt die Schleuse »fragen«, wird **nicht** ausgeführt. Das Modell
bekommt einen Satz zurück, den es dem Nutzer weitergeben kann. Ob es später eine echte
Rückfrage im Chat gibt, entscheidet K6 (#144) — hier gilt: im Zweifel nicht handeln.

Bordmittel, weil der Pi mitkommen muss.
"""
import json
import os
import sys

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)

import anbieter                                              # noqa: E402
import werkzeuge as wz                                       # noqa: E402

MAX_SCHRITTE = 15
MAX_ZEICHEN_VERLAUF = 120000     # grobe Schranke, absichtlich in Zeichen statt Token:
                                 # Token zählt jeder Anbieter anders (#142), Zeichen sind
                                 # überall gleich und für eine Schranke genau genug.
WIEDERHOLUNGEN_BIS_ABBRUCH = 3


def _laenge(nachrichten):
    return sum(len(n.get("text") or "") for n in nachrichten)


def kuerzen(nachrichten, grenze=MAX_ZEICHEN_VERLAUF):
    """Verlauf auf die Grenze bringen. Gibt (nachrichten, wieviel_entfernt) zurück.

    Reihenfolge des Wegwerfens:
      1. alte Werkzeugergebnisse (lang, Inhalt meist schon verarbeitet)
      2. alte Modellantworten
    Niemals: die Systemanweisung und die ERSTE Nutzernachricht. Wer den Auftrag
    wegkürzt, hat einen Assistenten, der eifrig das Falsche tut.
    """
    if _laenge(nachrichten) <= grenze:
        return nachrichten, 0

    behalten = [True] * len(nachrichten)
    erste_nutzer = next((i for i, n in enumerate(nachrichten)
                         if n["rolle"] == "nutzer"), None)
    unantastbar = {i for i, n in enumerate(nachrichten) if n["rolle"] == "system"}
    if erste_nutzer is not None:
        unantastbar.add(erste_nutzer)
    unantastbar |= set(range(max(0, len(nachrichten) - 4), len(nachrichten)))

    entfernt = 0
    for rolle in ("werkzeug", "modell"):
        for i, n in enumerate(nachrichten):
            if _laenge([m for j, m in enumerate(nachrichten) if behalten[j]]) <= grenze:
                break
            if i in unantastbar or not behalten[i] or n["rolle"] != rolle:
                continue
            behalten[i] = False
            entfernt += 1

    gekuerzt = [n for i, n in enumerate(nachrichten) if behalten[i]]
    if entfernt:
        # Sichtbar machen. Ein Verlauf, dem stillschweigend die Hälfte fehlt, führt zu
        # Antworten, die niemand nachvollziehen kann.
        einfuegen = min(len(gekuerzt), 1 + len(unantastbar & {0}))
        gekuerzt.insert(einfuegen, {
            "rolle": "nutzer",
            "text": f"[Hinweis: {entfernt} ältere Zwischenschritte wurden aus "
                    "Platzgründen entfernt. Frag nach, wenn dir etwas fehlt.]"})
    return gekuerzt, entfernt


def _fingerabdruck(aufruf):
    return (aufruf.get("name", ""),
            json.dumps(aufruf.get("argumente") or {}, sort_keys=True, ensure_ascii=False))


class Kern:
    """Ein Gespräch. Hält den Verlauf, führt die Schleife, gibt Text zurück."""

    def __init__(self, umgebung, anbieter_reihenfolge=None, modelle=None,
                 mcp=None, protokoll=None):
        self.umgebung = umgebung
        self.reihenfolge = anbieter_reihenfolge or ["anthropic"]
        self.modelle = modelle or {}
        self.mcp = mcp
        self.protokoll = protokoll or (lambda *_: None)
        self.nachrichten = []
        self.schritte = []

    # ------------------------------------------------------------- Werkzeuge --
    def werkzeugliste(self):
        liste = list(wz.beschreibungen("openai"))
        if self.mcp:
            liste += self.mcp.werkzeuge()
        return liste

    def _ausfuehren(self, aufruf):
        name = aufruf.get("name", "")
        args = aufruf.get("argumente") or {}
        if name.startswith("mcp__"):
            if not self.mcp:
                return "Diese Anbindung ist gerade nicht verfügbar."
            antwort = self.mcp.aufrufen(name, args, self.umgebung, herkunft="kern")
        elif name in wz.NACH_NAME:
            antwort = wz.ausfuehren(name, args, self.umgebung, herkunft="kern")
        else:
            # Erfundene Werkzeugnamen sind bei kleineren Modellen ein bekanntes
            # Vorkommnis. Ehrlich benennen und die Liste mitgeben, statt zu raten.
            bekannt = ", ".join(sorted(wz.NACH_NAME))
            return (f"Das Werkzeug »{name}« gibt es nicht. Verfügbar sind: {bekannt}"
                    + (" sowie die Anbindungen." if self.mcp else "."))

        if antwort.get("bestaetigung_noetig"):
            return ("Dieser Schritt braucht die Zustimmung des Besitzers und wurde "
                    f"deshalb nicht ausgeführt ({antwort.get('grund', '')}). Sag dem "
                    "Nutzer freundlich Bescheid und mach ohne diesen Schritt weiter.")
        return str(antwort.get("ergebnis") or antwort.get("fehler") or "")

    # --------------------------------------------------------------- Schleife --
    def frage(self, text, system=None, max_schritte=MAX_SCHRITTE):
        if system and not any(n["rolle"] == "system" for n in self.nachrichten):
            self.nachrichten.append({"rolle": "system", "text": system})
        self.nachrichten.append({"rolle": "nutzer", "text": text})

        gesehen = {}
        for schritt in range(max_schritte):
            self.nachrichten, entfernt = kuerzen(self.nachrichten)
            if entfernt:
                self.protokoll(f"Verlauf gekürzt: {entfernt} Zwischenschritte entfernt.")

            antwort = anbieter.mit_wechsel(self.reihenfolge, self.nachrichten,
                                           self.werkzeugliste(), self.modelle,
                                           protokoll=self.protokoll)
            if antwort.fehler:
                if antwort.anmeldung_fehlt:
                    return ("Ich komme gerade an kein Sprachmodell heran, weil keine "
                            "gültige Anmeldung vorliegt. 👉 Bitte im Terminal »claude« "
                            "starten und anmelden — danach geht es sofort weiter.")
                return f"Ich konnte gerade nicht antworten: {antwort.fehler}"

            if not antwort.werkzeug_aufrufe:
                self.nachrichten.append({"rolle": "modell", "text": antwort.text})
                return antwort.text

            self.nachrichten.append({"rolle": "modell", "text": antwort.text,
                                     "werkzeug_aufrufe": antwort.werkzeug_aufrufe})
            for aufruf in antwort.werkzeug_aufrufe:
                fp = _fingerabdruck(aufruf)
                gesehen[fp] = gesehen.get(fp, 0) + 1
                if gesehen[fp] >= WIEDERHOLUNGEN_BIS_ABBRUCH:
                    # Verrannt. Weiterlaufen kostet nur Geld und endet doch am Limit.
                    self.protokoll(f"Im Kreis gedreht bei »{aufruf.get('name')}«.")
                    return ("Ich drehe mich hier im Kreis — ich rufe immer wieder "
                            f"»{aufruf.get('name')}« mit denselben Angaben auf und komme "
                            "nicht weiter. 👉 Sag mir bitte genauer, was du brauchst.")
                ergebnis = self._ausfuehren(aufruf)
                self.schritte.append({"werkzeug": aufruf.get("name"),
                                      "zeichen": len(ergebnis)})
                self.nachrichten.append({"rolle": "werkzeug",
                                         "aufruf_id": aufruf.get("id", ""),
                                         "text": ergebnis})

        return ("Ich habe die erlaubte Zahl an Zwischenschritten erreicht, ohne fertig "
                "zu werden. 👉 Bitte stell die Aufgabe etwas kleiner, dann schaffe ich es.")
