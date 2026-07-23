#!/usr/bin/env python3
"""Re-Identifikation (stdlib) — ersetzt Pseudonymisierungs-Surrogate wieder durch echte Werte.

Die Werkzeuge des Operators (send.py, mcp_m365.py, gdrive.py) rufen das auf, BEVOR sie handeln:
Claude „denkt" in Surrogaten; die echte Aktion (Mail an reale Adresse, Antwort mit echtem Namen)
braucht die Klartext-Werte. Das flüchtige Mapping liegt im Pfad aus $OPERATOR_PII_MAP (0600),
den der Listener nur für die Dauer eines Claude-Laufs setzt.
"""
import json
import os
import re


def _derived(s2r):
    """Abgeleitete Token-Paare (#60): Das Modell bildet aus Surrogaten neue Formen
    (Nachname allein, kleingeschrieben in Dateinamen, Vorname allein). Wir mappen
    darum die Namens-Tokens paarweise mit — aus E-Mail-Local-Parts und Namens-Strings."""
    extra = {}
    for sur, real in s2r.items():
        if "@" in sur and "@" in real:
            st = [t for t in re.split(r"[._+]", sur.split("@", 1)[0]) if len(t) >= 3]
            rt = [t for t in re.split(r"[._+]", real.split("@", 1)[0]) if len(t) >= 3]
        else:
            st = [t for t in re.split(r"\s+", sur) if len(t) >= 3]
            rt = [t for t in re.split(r"\s+", real) if len(t) >= 3]
        if st and len(st) == len(rt):
            for s, r in zip(st, rt):
                if s.lower() != r.lower():
                    extra.setdefault(s, r)
    return extra


def apply(text: str, s2r: dict) -> str:
    """Surrogate -> echte Werte: exakte Treffer UND abgeleitete Formen,
    case-insensitiv mit Wortgrenzen (Unterstrich/Punkt zählen als Grenze, damit
    Dateinamen wie check_berger_email.py erfasst werden; »Hamberger« nicht).
    Ersetzung passt sich der Schreibweise an (berger->bauer, BERGER->BAUER)."""
    if not text or not s2r:
        return text
    pairs = dict(s2r)
    for k, v in _derived(s2r).items():
        pairs.setdefault(k, v)
    for sur in sorted(pairs, key=len, reverse=True):
        real = pairs[sur]
        rx = re.compile(r"(?<![A-Za-z0-9])" + re.escape(sur) + r"(?![A-Za-z0-9])",
                        re.IGNORECASE)

        def rep(m, real=real):
            t = m.group(0)
            if t.islower():
                return real.lower()
            if t.isupper() and len(t) > 2:
                return real.upper()
            if t[:1].isupper() and "@" not in real:   # Berger -> Bauer (auch wenn der
                return real[:1].upper() + real[1:]    # Ersatzwert klein gemappt ist);
            return real                               # E-Mail-Adressen nie umformen
        text = rx.sub(rep, text)
    return text


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
    return apply(text, s2r)
