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
import platform_compat as _plat

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
    """Nachsehen, wenn der letzte BEWEIS zu alt ist.

    Früher: »kein echter Lauf seit 6 h«. Das ging an der Wirklichkeit vorbei — wer
    regelmäßig chattet, erneuert damit dauernd `checked_at`, und aktiv geprüft wurde
    NIE. Die Anmeldung läuft aber zeitgesteuert ab, unabhängig von der Nutzung.
    Ergebnis (Michi, 30.07.): Die erste Meldung über den abgelaufenen Zugang war eine
    gescheiterte echte Anfrage — genau das, was diese Vorwarnung verhindern soll.

    Jetzt zählt `last_ok` (wann hat Claude zuletzt wirklich funktioniert). Ist das
    länger als PROBE_AFTER_H her, wird geprobt — auch wenn zwischendurch Läufe
    stattfanden. Kosten: ein Minimal-Prompt alle paar Stunden."""
    now = now or time.time()
    d = state()
    if d.get("state") in ("expired", "limit"):
        return False                     # bekannt kaputt — nicht zusätzlich proben
    letzter_beweis = d.get("last_ok") or d.get("checked_at", 0)
    return (now - letzter_beweis) > PROBE_AFTER_H * 3600


def probe(claude_bin="claude", env=None):
    """Billiger Lebendtest. Gibt (zustand, ist_neu) zurück wie record()."""
    try:
        # Prompt per stdin (Windows begrenzt Befehlszeilen) und leerer Eingabekanal,
        # damit ein fragendes claude nie endlos wartet.
        r = subprocess.run([claude_bin, "-p", "--output-format", "json"], input="ok",
                           capture_output=True, text=True, timeout=PROBE_TIMEOUT,
                           env=env or dict(os.environ), **_plat.OHNE_FENSTER)
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


def klartext(now=None):
    """Zustand in einem Satz — für »operator pruefen« und das Dashboard."""
    now = now or time.time()
    d = state()
    z = d.get("state", "unknown")
    alter = int((now - (d.get("last_ok") or 0)) / 3600) if d.get("last_ok") else None
    if z == "ok":
        return ("ok", f"Anmeldung gültig (zuletzt bestätigt vor {alter} h)"
                if alter is not None else "Anmeldung gültig")
    if z == "expired":
        return ("expired", "Anmeldung ABGELAUFEN — »claude /login« ausführen; "
                           "mit hinterlegtem API-Key würde der Operator selbst einspringen")
    if z == "limit":
        return ("limit", "Abo am Limit — mit hinterlegtem API-Key läuft es automatisch weiter")
    return ("unknown", "Zustand unbekannt (noch kein Lauf verbucht)")


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
