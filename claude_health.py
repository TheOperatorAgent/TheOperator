#!/usr/bin/env python3
"""Operator — Gesundheit des Claude-CLI-Logins (#59, stdlib-only).

Ziel: Der Nutzer soll NICHT mitten in einer Frage auflaufen („Login abgelaufen"),
sondern vorher eine einmalige, freundliche Vorwarnung bekommen.

Leitgedanke — sparsam statt geschwätzig:
  Jeder echte Claude-Lauf IST der Gesundheitsbeweis. Geprobt wird nur, wenn seit
  PROBE_AFTER_H kein Lauf mehr stattgefunden hat. Wer täglich chattet, löst nie
  einen Probe-Call aus. Der Probe selbst ist ein Minimal-Prompt mit knappem Timeout.

Zustände: "ok" · "expired" (Login abgelaufen) · "limit" (Abo am Limit) · "unknown".

Es werden BEWUSST keine Zugangsdaten gelesen (kein Keychain-/Token-Zugriff) —
der Zustand ergibt sich ausschließlich aus Rückgabecodes und Fehlertexten.
"""
import json
import os
import subprocess
import time

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
STATE_FILE = os.path.join(BOT_DIR, "run", "claude-health.json")
PROBE_AFTER_H = 6        # erst nach so vielen Stunden ohne Lauf aktiv nachsehen
PROBE_TIMEOUT = 45

AUTH_MARKERS = ("401", "authenticate", "oauth", "unauthorized", "please run /login",
                "invalid api key", "not logged in")
LIMIT_MARKERS = ("limit", "429", "rate", "overloaded", "quota")


def classify(rc, output):
    """Rückgabecode + Ausgabe → Zustand. Reihenfolge zählt: Auth schlägt Limit."""
    if rc == 0:
        return "ok"
    low = (output or "").lower()
    if any(m in low for m in AUTH_MARKERS):
        return "expired"
    if any(m in low for m in LIMIT_MARKERS):
        return "limit"
    return "unknown"


def state():
    """Aktueller Zustand als dict (fail-open: unbekannt statt Absturz)."""
    try:
        d = json.load(open(STATE_FILE))
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {"state": "unknown", "checked_at": 0, "warned_at": 0}


def _write(d):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def record(rc, output=""):
    """Ergebnis eines echten Laufs verbuchen. Gibt (neuer_zustand, ist_neu) zurück —
    `ist_neu` nur beim Wechsel, damit der Aufrufer genau einmal warnt."""
    new = classify(rc, output)
    old = state()
    d = dict(old)
    d["state"] = new
    d["checked_at"] = int(time.time())
    if new == "ok":
        d["last_ok"] = d["checked_at"]
        d["warned_at"] = 0          # Erholung → nächster Ausfall darf wieder warnen
    changed = (old.get("state") != new)
    _write(d)
    return new, changed


def needs_probe(now=None):
    """Nur nachsehen, wenn länger kein echter Lauf Beweis geliefert hat."""
    now = now or time.time()
    return (now - state().get("checked_at", 0)) > PROBE_AFTER_H * 3600


def probe(claude_bin="claude", env=None):
    """Billiger Lebendtest. Gibt (zustand, ist_neu) zurück wie record()."""
    try:
        r = subprocess.run([claude_bin, "-p", "ok", "--output-format", "json"],
                           capture_output=True, text=True, timeout=PROBE_TIMEOUT,
                           env=env or dict(os.environ))
        return record(r.returncode, r.stdout + r.stderr)
    except (OSError, subprocess.TimeoutExpired):
        return state().get("state", "unknown"), False


def mark_warned():
    d = state()
    d["warned_at"] = int(time.time())
    _write(d)


def should_warn():
    """Vorwarnung nur bei abgelaufenem Login und nur einmal je Ausfall."""
    d = state()
    return d.get("state") == "expired" and not d.get("warned_at")


WARN_TEXT = (
    "🔑 Kleine Vorwarnung: Mein Claude-Zugang ist abgelaufen — ich kann gerade keine "
    "Aufgaben bearbeiten.\n"
    "👉 So bin ich in einer Minute wieder da: Öffne am Rechner das Terminal und gib "
    "`claude /login` ein. Danach schreib mir einfach nochmal.\n"
    "(Tipp: Wenn du im Dashboard unter »Modelle & Provider« einen Claude-API-Key als "
    "Reserve hinterlegst, arbeite ich in so einem Fall automatisch weiter.)")


if __name__ == "__main__":   # kleiner CLI-Blick für Diagnose
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        print(probe())
    print(json.dumps(state(), indent=2))
