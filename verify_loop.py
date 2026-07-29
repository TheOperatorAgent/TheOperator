"""Verifikations-Schleife (A1, Issue #46) — reine, testbare Logik (stdlib-only).

Idee: Ein Agent kann per Frontmatter `verify: true` (optional `verify_with: <modell>`)
eine zweite Instanz als Qualitäts-Schleuse bekommen. Ablauf im Listener:

    Worker (Claude ODER Fremd-LLM) erzeugt eine Antwort als TEXT (nicht selbst gesendet)
      → Verifier-Modell prüft kritisch
        → gibt entweder  VERIFIZIERT  zurück  (Original geht raus)
        →  oder  eine verbesserte, vollständige Fassung  (die geht raus)
      → Listener sendet das Endergebnis + kurze Fußzeile.

Dieses Modul enthält NUR die Prompt-Konstruktion und die Antwort-Interpretation — die
Modell-Aufrufe (subprocess/claude/llm_runner) macht der Listener. So bleibt es ohne
Seiteneffekte unit-testbar und der Listener stdlib-only.

Bewusste Grenze: Der Verifier liefert die Korrektur selbst (ein Zusatz-Aufruf), statt den
Worker erneut mit Werkzeugen laufen zu lassen. Das ist robust und genügt dem Kernfall
„günstiges/lokales Modell arbeitet, ein stärkeres Modell prüft und veredelt".
"""

# Wörter, die eine Frontmatter-Angabe als "an" interpretieren.
_TRUE = {"true", "ja", "yes", "1", "on", "an"}
# Marker, mit dem der Verifier eine fehlerfreie Antwort durchwinkt.
_OK_MARKER = "VERIFIZIERT"
# Marker, hinter dem AUSSCHLIESSLICH der finale, sendbare Text steht (#99):
# Prüfer-Ausgaben ohne Marker werden verworfen — nie wieder Prüfer-Prosa im Chat.
_FIX_MARKER = "KORREKTUR:"


def verify_config(meta):
    """Liest die Verifikations-Konfiguration aus dem Agenten-Frontmatter.

    meta: dict aus parse_agent_md() (Schlüssel u. a. 'verify', 'verify_with', 'model').
    Rückgabe: (enabled: bool, verify_model: str|None).
    `verify_with` gewinnt; sonst gilt bei `verify: true` das Standard-Verifizierermodell
    (None ⇒ der Listener nimmt Claude/inherit als Prüfer, unabhängig vom Worker-Modell).
    """
    if not isinstance(meta, dict):
        return False, None
    vw = str(meta.get("verify_with") or "").strip()
    if vw:
        return True, vw
    v = meta.get("verify")
    if isinstance(v, bool):          # JSON-Bool (z. B. owner_verify: true in credentials.json)
        return v, None
    v = str(v or "").strip().lower()
    return (v in _TRUE), None


def verifier_prompts(question, answer, verlauf=""):
    """Baut (system, user) für den Verifier-Lauf. Sprache: Deutsch, knapp, parsebar.
    verlauf (#101): die letzten Gesprächsrunden — ohne sie beurteilte der Prüfer
    Smalltalk-Wechsel ohne Kontext und »korrigierte« völlig richtige Antworten."""
    system = (
        "Du bist ein kritischer, sorgfältiger Prüfer. Du bekommst eine FRAGE und eine "
        "ANTWORT eines anderen Assistenten. Prüfe die ANTWORT auf sachliche Fehler, "
        "Widersprüche, Halluzinationen und fehlende, für die FRAGE wichtige Punkte.\n\n"
        "WICHTIGE GRENZEN DEINER PRÜFUNG:\n"
        "- Die ANTWORT kann auf Werkzeug-Ergebnissen beruhen (gelesene Mails, Dateien, "
        "Web, Systemzustand), die DIR nicht vorliegen. Solche Inhalte kannst du nicht "
        "widerlegen — sie gelten als korrekt. Erfinde NIEMALS einen Fehlschlag "
        "(»Abruf schlug fehl«), wenn die Antwort das Gegenteil zeigt.\n"
        "- Namen oder seltsame Werte an Stellen, wo technische IDs stehen, sind "
        "Pseudonymisierungs-Platzhalter des Systems — Absicht, KEIN Fehler. Nicht "
        "beanstanden, nicht kommentieren.\n"
        "- Korrigiere nur innere Widersprüche und klare Fehler im allgemeinen "
        "Weltwissen. Im Zweifel gilt die Antwort.\n\n"
        "DEIN AUSGABEFORMAT (hart, es gibt nur diese zwei Möglichkeiten):\n"
        f"- Antwort korrekt und ausreichend → GENAU ein Wort: {_OK_MARKER}\n"
        f"- Sonst → die Zeile beginnt mit {_FIX_MARKER} direkt gefolgt vom vollständigen, "
        "verbesserten Text, der unverändert an den Nutzer gesendet werden kann. "
        "KEINE Vorrede, KEIN Meta-Kommentar, KEINE Analyse, nicht die Wörter "
        "'FRAGE'/'ANTWORT' — alles nach dem Marker geht 1:1 in den Chat.\n"
        "Ausgaben ohne einen der beiden Marker werden verworfen und das Original gesendet."
    )
    kontext = f"GESPRÄCH BISHER (nur zur Einordnung):\n{verlauf.strip()}\n\n" if verlauf else ""
    user = f"{kontext}FRAGE:\n{question}\n\nANTWORT:\n{answer}"
    return system, user


def interpret(verifier_output, original_answer):
    """Wertet die Verifier-Ausgabe aus.

    Rückgabe: (final_text, revised: bool).
    - Leere/None-Ausgabe (Verifier ausgefallen): Original durchlassen, revised=False
      (fail-open: die Prüfung darf die Antwort nie verschlucken).
    - Beginnt die Ausgabe mit dem OK-Marker: Original ist gut → (original, False).
    - KORREKTUR-Marker: NUR der Text dahinter ist die verbesserte Antwort.
    - Alles andere (#99): verwerfen und Original senden. Vorher landete hier die
      komplette Prüfer-Prosa (»Die Antwort enthält einen klaren sachlichen Fehler …
      Verbesserte Fassung: …«) wortwörtlich im Chat des Nutzers.
    """
    if not verifier_output or not verifier_output.strip():
        return original_answer, False
    text = verifier_output.strip()
    # Marker robust erkennen (auch "VERIFIZIERT." / "**VERIFIZIERT**" / kleingeschrieben).
    head = text.lstrip("*_# ").rstrip("*_.! ").upper()
    if head == _OK_MARKER or head.startswith(_OK_MARKER):
        return original_answer, False
    idx = text.upper().find(_FIX_MARKER)
    if idx >= 0:
        fix = text[idx + len(_FIX_MARKER):].strip().strip("*_").strip()
        return (fix, True) if fix else (original_answer, False)
    return original_answer, False


def trivial(question, answer):
    """#100: Kurze Smalltalk-Wechsel gar nicht erst prüfen — ein »hä?« braucht keinen
    zweiten Modell-Lauf. Konservativ: sobald Zahlen, Links oder Länge im Spiel sind,
    wird geprüft."""
    q, a = (question or "").strip(), (answer or "").strip()
    return (len(q) <= 40 and len(a) <= 160
            and not any(ch.isdigit() for ch in a)
            and "http" not in a.lower())


# Dezente Prüfzeichen statt sperriger Fußzeile — der Nutzer soll die Antwort lesen,
# nicht die Meta-Information. Die Bedeutung erklärt das Dashboard (Übersicht).
MARK_OK = "✓"        # zweites Modell hat gegengelesen, Antwort blieb unverändert
MARK_REVISED = "✎"   # zweites Modell hat etwas korrigiert


def footer(verify_model, revised):
    """Kleines Prüfzeichen, das ans Ende der Antwort gehängt wird."""
    return f"  {MARK_REVISED}" if revised else f"  {MARK_OK}"
