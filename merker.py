#!/usr/bin/env python3
"""Automatisches Merken (#110, stdlib-only) — die Lücke zu Honcho, ohne Fremddienst.

**Warum.** Speichern hing bisher an einer Bitte in VERHALTEN.md: »wenn Michi dir etwas
Merkenswertes sagt → memory.py add«. In der Praxis passierte es fast nie — vier Fakten
in acht Tagen. Ein leeres Gedächtnis ist schlechter als ein schlechteres, das benutzt
wird. Hermes/Honcho baut sein Nutzermodell automatisch auf; genau dort lag der Rückstand.

**Wie.** Nach einer beantworteten Runde läuft ein kurzer, günstiger Extraktionslauf über
Frage und Antwort. Er läuft NACH dem Senden (wie die Prüfung in #100) und verzögert
deshalb keine Antwort.

**Grenze, die wir nicht überschreiten.** Kein verdecktes Nutzerprofil. Was gemerkt wird,
erscheint im Chat mit einem Zeichen (🧠) und ist im Gedächtnis-Tab mit einem Klick
widerrufbar. Alles bleibt sichtbar, editierbar und löschbar.

Dieses Modul enthält NUR Prompt-Bau und Antwort-Auswertung — die Modell-Aufrufe macht
der Listener. So bleibt es ohne Seiteneffekte testbar.
"""
import re

MARK = "🧠"          # Zeichen im Chat: »hiervon habe ich mir etwas gemerkt«
NICHTS = "NICHTS"    # Antwort des Extraktors, wenn es nichts zu merken gibt
MAX_LEN = 300        # ein Fakt ist ein Satz, kein Aufsatz

# Ein Merk-Vorschlag muss ein eigenständiger Fakt sein. Diese Muster deuten auf
# Gesprächs-Sätze statt auf Wissen hin — die will niemand im Langzeitgedächtnis.
_ABLEHNEN = re.compile(
    r"^(ja|nein|ok|okay|danke|bitte|gern|hallo|servus|tschüss)\b"
    r"|^(ich|er|sie) (habe|hab|hat) .{0,40}?\b(gerade|soeben|jetzt|eben)\b"
    r"|\b(gerade|soeben|eben) (erledigt|gemacht|angelegt|erstellt|geschrieben|gesendet)\b"
    r"|\b(ist|wurde) (gerade|soeben|eben) \w+"
    r"|^(der|die|das) (nutzer|user)\b"
    r"|^(michi )?(fragt|fragte|möchte wissen|wollte wissen)\b",
    re.IGNORECASE)


# Floskeln, die manche Modelle an einen sonst brauchbaren Fakt anhängen.
_META_ENDE = re.compile(
    r"\s*[—–-]?\s*(ich (merke|notiere|speichere|habe).{0,40}|"
    r"(das )?(merke|notiere) ich mir.{0,30}|für später( gemerkt)?|notiert)\s*[.!]?$",
    re.IGNORECASE)


def extraktor_prompts(frage, antwort):
    """(system, user) für den Extraktionslauf. Bewusst streng: im Zweifel NICHTS."""
    system = (
        "Du extrahierst dauerhaft nützliches Wissen aus einem Chat-Austausch.\n\n"
        "Merkenswert ist NUR, was auch in Wochen noch gilt:\n"
        "- Fakten über den Nutzer (Rolle, Vorlieben, Arbeitsweise, wichtige Termine)\n"
        "- seine Technik/Infrastruktur (Geräte, Adressen, Dienste, Zugänge — keine Passwörter)\n"
        "- getroffene Entscheidungen und deren Begründung\n\n"
        "NICHT merkenswert: Smalltalk, Höflichkeiten, Rückfragen, Zwischenstände, "
        "Aufgaben die gerade erledigt wurden, alles was nur für diesen Moment gilt.\n\n"
        "AUSGABEFORMAT — nur diese zwei Möglichkeiten:\n"
        f"- Nichts Dauerhaftes dabei → GENAU ein Wort: {NICHTS}\n"
        "- Sonst → GENAU EIN vollständiger Aussagesatz, der auch ohne den Chat "
        "verständlich ist (nenne Dinge beim Namen, keine Wörter wie »er«/»das«).\n"
        "  Schreibe SACHLICH ÜBER den Nutzer, sprich ihn NICHT an: »Der Drucker im Büro "
        "des Nutzers heißt HP-Nord« — nicht »Dein Drucker heißt …«.\n"
        "  KEINE Zusätze wie »ich merke mir das«, »für später«, »notiert«. "
        "Nur der Fakt. Keine Vorrede, keine Anführungszeichen, keine Aufzählung.\n\n"
        "Im Zweifel " + NICHTS + ". Lieber nichts merken als Unfug merken."
    )
    user = f"NACHRICHT DES NUTZERS:\n{frage}\n\nANTWORT DES ASSISTENTEN:\n{antwort}"
    return system, user


def auswerten(ausgabe):
    """Extraktor-Ausgabe → Fakt oder None. Streng und fail-closed: Was nicht wie ein
    sauberer Einzelfakt aussieht, wird verworfen. Ein falsch gemerkter Fakt begleitet
    den Nutzer wochenlang — die Kosten sind asymmetrisch."""
    if not ausgabe:
        return None
    t = " ".join(str(ausgabe).split()).strip().strip('"“”')
    if not t or t.upper().startswith(NICHTS):
        return None
    t = t.lstrip("-•* ").strip()
    if len(t) < 12 or len(t) > MAX_LEN:
        return None
    if "\n" in ausgabe.strip():           # mehrzeilig = keine Einzelaussage
        return None
    if _ABLEHNEN.search(t):
        return None
    # Angehängte Gesprächs-Floskeln abschneiden ("… — ich merke mir das für später").
    # Sie entstehen, wenn der Extraktor die Assistenten-Antwort nachplappert; im
    # Langzeitgedächtnis stören sie bei jedem späteren Treffer.
    t = _META_ENDE.sub("", t).strip(" —-,;")
    if len(t) < 12:
        return None
    return t


def ist_dublette(fakt, bestehende, schwelle=0.86, vektor_fn=None):
    """Kennt das Gedächtnis das schon? Erst Wort-Überlappung (immer verfügbar),
    dann — falls vorhanden — semantisch über Vektoren. Ohne diese Prüfung stünde
    derselbe Fakt nach zehn Gesprächen zehnmal drin."""
    def worte(s):
        s = (s.lower().replace("ß", "ss").replace("ä", "ae")
             .replace("ö", "oe").replace("ü", "ue"))
        # Zahlen/Adressen mitnehmen (IPs, Ports) — die tragen viel Bedeutung
        return {w for w in re.findall(r"[\w.]{3,}", s) if not w.strip(".").isdigit() or len(w) > 6}
    neu = worte(fakt)
    for alt in bestehende:
        a = worte(alt)
        if not neu or not a:
            continue
        gemeinsam = len(neu & a) / max(1, min(len(neu), len(a)))
        if gemeinsam >= 0.8:
            return True
    if vektor_fn:
        try:
            v = vektor_fn(fakt)
            for alt in bestehende:
                va = vektor_fn(alt)
                if v and va and _kosinus(v, va) >= schwelle:
                    return True
        except Exception:
            pass
    return False


def _kosinus(a, b):
    if len(a) != len(b):
        return 0.0
    p = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return p / (na * nb) if na and nb else 0.0
