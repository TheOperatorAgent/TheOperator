#!/usr/bin/env python3
"""SkillGuard (#48) — statischer Sicherheits-Scan für Skills (stdlib-only).

Prüft Skill-Texte (SKILL.md-Inhalte) auf die Muster, mit denen bösartige Skills in
freier Wildbahn aufgefallen sind (vgl. ClawHub-Vorfälle: Secret-Exfiltration,
verschleierte Payloads, persistente Prompt-Injection ins Gedächtnis):

  GEFAHR  (rot)  — Secret-/Tresor-Zugriff, Daten-Exfiltration, curl|bash,
                   Verhaltens-/Gedächtnis-Manipulation, "verheimliche dies"
  WARNUNG (gelb) — Verschleierung (base64/eval), Injection-Formulierungen,
                   externe Downloads
  OK      (grün) — nichts gefunden

Ehrliche Grenze: ein statischer Scan BEWEIST keine Harmlosigkeit — er senkt das
Risiko und macht Funde sichtbar. Import bleibt immer eine menschliche Entscheidung.
"""
import re

# (code, level, regex, beschreibung) — level: "gefahr" | "warnung"
_CHECKS = [
    ("secrets", "gefahr",
     r"(secrets/|secretstore|credentials\.json|vault\.enc|find-generic-password|"
     r"\.ssh/|id_rsa|id_ed25519|api[_-]?key\s*=|ANTHROPIC_API_KEY|NOTION_TOKEN)",
     "Greift auf Zugangsdaten/Tresor/Schlüssel zu"),
    ("exfil", "gefahr",
     r"(curl|wget|nc|Invoke-WebRequest|urllib|requests)[^\n]{0,120}"
     r"(-d\s|--data|--upload|POST|@|<)[^\n]{0,120}https?://",
     "Sendet Daten an einen externen Server"),
    ("pipe-shell", "gefahr",
     r"(curl|wget)[^\n|]{0,160}\|\s*(ba)?sh",
     "Lädt Code aus dem Netz und führt ihn direkt aus (curl | bash)"),
    ("verhalten", "gefahr",
     r"(VERHALTEN\.md|verhalten\.md)[^\n]{0,60}(schreib|änder|ersetz|>>|>\s|write|append)|"
     r"(schreib|änder|ersetz|write|append)[^\n]{0,60}(VERHALTEN\.md|verhalten\.md)",
     "Will die Verhaltensregeln des Operators verändern"),
    ("memory-inject", "gefahr",
     r"memory\.py\s+add[^\n]{0,160}(ignorier|ab jetzt|immer wenn|anweisung|instruktion|regel)",
     "Will Anweisungen dauerhaft ins Gedächtnis schreiben (persistente Injection)"),
    ("conceal", "gefahr",
     r"(verheimliche|erwähne\s+nicht|sag\s+(dem\s+nutzer|michi)\s+nichts|"
     r"do\s+not\s+tell|hide\s+this\s+from)",
     "Verlangt, etwas vor dem Nutzer zu verbergen"),
    ("injection", "warnung",
     r"(ignorier\w*\s+(alle\s+)?(vorherig|bisherig|obig)\w*\s+(anweisung|regel|instruktion)|"
     r"ignore\s+(all\s+)?previous\s+instructions|du\s+bist\s+jetzt\s+(?!der\s+skill)|"
     r"system\s*prompt)",
     "Enthält typische Prompt-Injection-Formulierungen"),
    ("obfuscation", "warnung",
     r"(base64\s+(-d|--decode)|[A-Za-z0-9+/]{60,}={0,2}|eval\s*\(|exec\s*\(|"
     r"\\x[0-9a-fA-F]{2}\\x[0-9a-fA-F]{2})",
     "Verschleierter Code (base64/eval/hex) — Inhalt nicht offen lesbar"),
    ("download", "warnung",
     r"(curl|wget|Invoke-WebRequest)[^\n]{0,120}https?://[^\n]{0,80}\.(sh|py|exe|ps1|zip)",
     "Lädt ausführbare Dateien aus dem Netz"),
]


def scan(text):
    """Skill-Text prüfen. Rückgabe:
    {"level": "ok"|"warnung"|"gefahr", "findings": [{code, level, msg, snippet}]}"""
    findings = []
    t = text or ""
    for code, level, pattern, msg in _CHECKS:
        m = re.search(pattern, t, re.IGNORECASE)
        if m:
            snippet = t[max(0, m.start() - 20):m.end() + 20].replace("\n", " ").strip()
            findings.append({"code": code, "level": level, "msg": msg,
                             "snippet": snippet[:120]})
    level = ("gefahr" if any(f["level"] == "gefahr" for f in findings)
             else "warnung" if findings else "ok")
    return {"level": level, "findings": findings}
