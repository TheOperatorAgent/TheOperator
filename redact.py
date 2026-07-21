#!/usr/bin/env python3
"""Operator Redaction — entfernt Secrets aus Texten, bevor sie in Verlauf/Logs/Prompts landen.

Stdlib-only (wird vom Listener importiert). Zwei Schichten:
- extra_values: konkret bekannte Secret-Werte (z. B. aus dem Tresor) — exakte Ersetzung
- generische Muster: Token-Formate, die auch ohne Tresor-Wissen erkennbar sind
"""
import re

# (Label, kompiliertes Muster) — Reihenfolge: spezifisch vor generisch
PATTERNS = [
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("matrix-token", re.compile(r"syt_[A-Za-z0-9_]{20,}")),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("api-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("gitea-token", re.compile(r"(?i)(?:token|authorization:)\s+[0-9a-f]{40}")),
    ("secret-zuweisung", re.compile(
        r"(?i)((?:passwor[dt]|passphrase|token|secret|api[_-]?key|client[_-]?secret)\s*[=:]\s*)(\S{6,})")),
]


def redact(text: str, extra_values=()) -> str:
    """Ersetzt bekannte Werte und generische Secret-Muster durch [REDACTED:…]-Marker."""
    if not text:
        return text
    for value in sorted(set(v for v in extra_values if v and len(v) >= 4), key=len, reverse=True):
        text = text.replace(value, "[REDACTED:tresor]")
    for label, pat in PATTERNS:
        if label == "secret-zuweisung":
            text = pat.sub(lambda m: m.group(1) + "[REDACTED:" + label + "]", text)
        else:
            text = pat.sub("[REDACTED:" + label + "]", text)
    return text


if __name__ == "__main__":
    import sys
    sys.stdout.write(redact(sys.stdin.read()))
