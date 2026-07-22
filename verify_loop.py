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


def verifier_prompts(question, answer):
    """Baut (system, user) für den Verifier-Lauf. Sprache: Deutsch, knapp, parsebar."""
    system = (
        "Du bist ein kritischer, sorgfältiger Prüfer. Du bekommst eine FRAGE und eine "
        "ANTWORT eines anderen Assistenten. Prüfe die ANTWORT auf sachliche Fehler, "
        "Widersprüche, Halluzinationen und fehlende, für die FRAGE wichtige Punkte.\n\n"
        "Wenn die Antwort korrekt und ausreichend ist, antworte mit GENAU einem Wort: "
        f"{_OK_MARKER}\n"
        "Andernfalls antworte mit einer VERBESSERTEN, vollständigen Fassung der Antwort, "
        "die unverändert an den Nutzer gesendet werden kann — ohne Vorrede, ohne "
        "Meta-Kommentar, ohne die Wörter 'FRAGE'/'ANTWORT', nur der finale Text."
    )
    user = f"FRAGE:\n{question}\n\nANTWORT:\n{answer}"
    return system, user


def interpret(verifier_output, original_answer):
    """Wertet die Verifier-Ausgabe aus.

    Rückgabe: (final_text, revised: bool).
    - Leere/None-Ausgabe (Verifier ausgefallen): Original durchlassen, revised=False
      (fail-open: die Prüfung darf die Antwort nie verschlucken).
    - Beginnt die Ausgabe mit dem OK-Marker: Original ist gut → (original, False).
    - Sonst: die Ausgabe IST die verbesserte Antwort → (ausgabe, True).
    """
    if not verifier_output or not verifier_output.strip():
        return original_answer, False
    text = verifier_output.strip()
    # Marker robust erkennen (auch "VERIFIZIERT." / "**VERIFIZIERT**" / kleingeschrieben).
    head = text.lstrip("*_# ").rstrip("*_.! ").upper()
    if head == _OK_MARKER or head.startswith(_OK_MARKER):
        return original_answer, False
    return text, True


def footer(verify_model, revised):
    """Kurze, transparente Fußzeile für den Chat. verify_model None ⇒ 'Prüfer'."""
    who = verify_model or "Prüfer"
    return f"\n\n— {'✓ überarbeitet' if revised else '✓ geprüft'} von {who}"
