#!/usr/bin/env python3
"""Re-Identifikation (stdlib) — ersetzt Pseudonymisierungs-Surrogate wieder durch echte Werte.

Die Werkzeuge des Operators (send.py, mcp_m365.py, gdrive.py) rufen das auf, BEVOR sie handeln:
Claude „denkt" in Surrogaten; die echte Aktion (Mail an reale Adresse, Antwort mit echtem Namen)
braucht die Klartext-Werte. Das flüchtige Mapping liegt im Pfad aus $OPERATOR_PII_MAP (0600),
den der Listener nur für die Dauer eines Claude-Laufs setzt.
"""
import json
import os


def reidentify(text: str) -> str:
    if not text:
        return text
    p = os.environ.get("OPERATOR_PII_MAP")
    if not p or not os.path.exists(p):
        return text
    try:
        s2r = json.load(open(p)).get("s2r", {})
    except (OSError, ValueError):
        return text
    for sur in sorted(s2r, key=len, reverse=True):
        if sur in text:
            text = text.replace(sur, s2r[sur])
    return text
