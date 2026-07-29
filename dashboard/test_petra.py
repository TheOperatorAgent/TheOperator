#!/usr/bin/env python3
"""Operator — Petra-Wege als automatische Prüfung (#84).

Hintergrund: Jede Sicherheitsmaßnahme macht ein Produkt leicht umständlicher.
Genau das soll hier NICHT passieren. Diese Tests halten die Zusagen aus
EINFACHHEIT.md fest, damit sie beim nächsten Feature nicht still verloren gehen.

Geprüft wird das, was eine Büromitarbeiterin merkt:
  * Muss ich ins Terminal? (nein — außer beim einen Installationsbefehl)
  * Werde ich mit Fachbegriffen zugeworfen? (nein)
  * Fragt der Operator bei harmloser Arbeit dauernd nach? (nein)
  * Sagt mir jeder Fehler, was ich als Nächstes tun soll? (ja)
  * Sind Geheimnisse sichtbar? (nein)
"""
import ast
import json
import os
import re
import sys

BOT = os.path.expanduser("~/.claude/matrix-bot")
sys.path.insert(0, BOT)


def _lies(pfad):
    return open(os.path.join(BOT, pfad), encoding="utf-8").read()


# ------------------------------------------------------- Weg 1: sichere Arbeit --
def test_harmlose_arbeit_fragt_nie_nach():
    """Petra recherchiert, liest, lässt schreiben — ohne eine einzige Rückfrage."""
    import permission_broker as pb
    harmlos = [
        ("Read", {"file_path": "/tmp/bericht.txt"}),
        ("Grep", {"pattern": "Umsatz"}),
        ("Glob", {"pattern": "*.xlsx"}),
        ("WebSearch", {"query": "Bahnverbindung München"}),
        ("Bash", {"command": "ls -la"}),
        ("Bash", {"command": "python3 auswertung.py"}),
        ("Bash", {"command": "git status"}),
        ("Write", {"file_path": os.path.join(BOT, "workspace", "notiz.txt")}),
        ("mcp__m365__mail_list", {}),
    ]
    for tool, args in harmlos:
        riskant, _ = pb.classify(tool, args)
        assert riskant is False, f"{tool} fragt unnötig nach — bremst Petra aus"


def test_riskante_arbeit_fragt_genau_einmal():
    """Nur bei echtem Risiko kommt eine Rückfrage — und die ist verständlich."""
    import permission_broker as pb
    riskant = [
        ("Bash", {"command": "rm -rf /Users/petra/Dokumente"}),
        ("Bash", {"command": "sudo systemctl restart nginx"}),
        ("mcp__m365__mail_send", {"to": "kunde@firma.de"}),
    ]
    for tool, args in riskant:
        ist_riskant, text = pb.classify(tool, args)
        assert ist_riskant is True, f"{tool} müsste nachfragen"
        assert text and len(text) > 5, "Rückfrage ohne verständliche Erklärung"
        # keine Fachbegriffe in der Rückfrage
        for begriff in ("subprocess", "exec", "payload", "SSRF", "fingerprint", "nonce"):
            assert begriff.lower() not in text.lower(), f"Fachbegriff »{begriff}« in der Rückfrage"


# ------------------------------------------------------- Weg 2: kein Terminal --
def test_kein_terminal_zwang_in_der_oberflaeche():
    """Außer dem einen Installationsbefehl darf die Oberfläche kein Terminal verlangen."""
    html = _lies("dashboard/static/index.html")
    js = _lies("dashboard/static/app.js")
    # Terminal-Befehle dürfen vorkommen, aber nur als FALLBACK neben einem Klick-Weg
    for text, name in ((html, "index.html"), (js, "app.js")):
        for treffer in re.findall(r"(?:python3|bash|sudo)\s+[~/][^\"'`<\n]{5,60}", text):
            umfeld = text[max(0, text.find(treffer) - 400): text.find(treffer) + 200].lower()
            assert any(w in umfeld for w in ("fallback", "alternativ", "falls", "notfall",
                                             "chat", "einfachsten", "oder am rechner")), \
                f"{name}: »{treffer}« ohne Klick-Alternative angeboten"


def test_fehlermeldungen_sagen_was_zu_tun_ist():
    """Jede Klartext-Fehlermeldung nennt den nächsten Schritt."""
    js = _lies("dashboard/static/app.js")
    start = js.find("function friendlyError")
    assert start > 0, "friendlyError fehlt — Fehler wären technisch"
    block = js[start:start + 4000]
    assert "👉" in block, "Fehlermeldungen ohne 👉-nächsten-Schritt"


# ------------------------------------------------------- Weg 3: Verständlichkeit --
def test_chat_zeichen_werden_erklaert():
    """Alles, was im Chat als Zeichen auftaucht, muss das Dashboard erklären."""
    html = _lies("dashboard/static/index.html")
    import verify_loop as vl
    for zeichen in (vl.MARK_OK, vl.MARK_REVISED, "🔐", "⚡"):
        assert zeichen in html, f"Zeichen {zeichen} wird dem Nutzer nirgends erklärt"


def test_texte_an_den_nutzer_sind_deutsch_und_ohne_jargon():
    """Nutzertexte in den neuen Sicherheits-Modulen bleiben verständlich."""
    jargon = ("SSRF", "loopback", "link-local", "replay", "nonce", "fingerprint",
              "PreToolUse", "fail-closed", "stdout", "subprocess")
    import permission_broker as pb
    import net_guard as ng
    nutzertexte = [pb.WARN_TEXT if hasattr(pb, "WARN_TEXT") else "",
                   ng.hinweis("https://x.tld", "zeigt in dein privates Netz")]
    import claude_health as ch
    nutzertexte.append(ch.WARN_TEXT)
    for t in nutzertexte:
        for j in jargon:
            assert j.lower() not in t.lower(), f"Fachbegriff »{j}« in einem Nutzertext"


def test_geheimnisse_erscheinen_nie_im_klartext():
    """Weder im Chat-Verlauf noch im Protokoll stehen Zugangsdaten."""
    listener = _lies("listener.py")
    assert "{result[-200:]}" not in listener and "{text[:120]}" not in listener, \
        "Antworttexte im Protokoll"
    broker = _lies("permission_broker.py")
    assert "Ein-Klick-Login-Link" not in broker or "#ott=" not in broker
    # Der Dashboard-Einmal-Link darf nie in die durchsuchbare DB
    assert 'record_direct(bodies, "(Ich habe dem Nutzer einen Ein-Klick-Login-Link' in listener, \
        "Kurzbefehl-Verlauf schreibt womöglich den Token mit"


# ------------------------------------------------------- Weg 4: Selbstheilung --
def test_alles_laeuft_ohne_zusatzsoftware():
    """Petra installiert kein Docker und kein Python-Paket. Die Sicherheitsmodule
    müssen mit der Standardbibliothek auskommen."""
    for modul in ("listener.py", "permission_broker.py", "claude_tool_hook.py",
                  "net_guard.py", "retention.py", "throttle.py", "claude_health.py",
                  "persona.py", "providers.py"):
        src = _lies(modul)
        imports = {a.name.split(".")[0] for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.Import) for a in n.names}
        imports |= {n.module.split(".")[0] for n in ast.walk(ast.parse(src))
                    if isinstance(n, ast.ImportFrom) and n.module}
        verboten = {"fastapi", "uvicorn", "msal", "cryptography", "requests",
                    "playwright", "openai", "docker"}
        assert not (imports & verboten), f"{modul} braucht Zusatzsoftware: {imports & verboten}"


def test_sicherheit_richtet_sich_selbst_ein():
    """Petra klickt nichts an, damit der Schutz wirkt — der Listener macht das."""
    listener = _lies("listener.py")
    assert "def ensure_tool_hook" in listener, "Rückfrage-Hook wird nicht selbst eingerichtet"
    assert "ensure_tool_hook()" in listener, "Hook-Einrichtung wird nie aufgerufen"
    assert "retention.faellig()" in listener, "Aufräumen läuft nicht von selbst"


def test_ausfaelle_blockieren_petra_nicht():
    """Ist etwas kaputt, darf der Operator nicht einfach verstummen."""
    listener = _lies("listener.py")
    # Jedes optionale Sicherheitsmodul ist in try/except eingebunden (fail-open im Betrieb)
    for modul in ("claude_health", "throttle", "permission_broker", "retention"):
        assert f"import {modul}" in listener and f"{modul} = None" in listener, \
            f"{modul} ist nicht ausfallsicher eingebunden"


def test_arbeitsordner_ist_privat():
    """#18: Agenten legen dort Ergebnisse ab — die gehören nur dem Nutzer."""
    listener = _lies("listener.py")
    assert "def ensure_private_workspace" in listener
    assert "ensure_private_workspace()" in listener, "wird nie aufgerufen"
    ws = os.path.join(BOT, "workspace")
    if os.path.isdir(ws):
        assert not (os.stat(ws).st_mode & 0o077), "Arbeitsordner ist für andere lesbar"
