#!/usr/bin/env python3
"""Operator — Fair-Use-Drossel für automatische Läufe (#58, stdlib-only).

Warum: Automationen (Cron, Ereignis-Trigger, Skill-Scout) laufen ohne dein Zutun.
Ein fehlkonfigurierter Zeitplan oder eine Ereignis-Schleife könnte still dein
Abo-Kontingent leerlaufen — und Dauer-Automation fällt bei Anthropic unangenehm auf.

Leitplanke: **Nur Automationen werden gedrosselt. Deine eigenen Chat-Nachrichten
niemals** — wer den Operator anschreibt, bekommt immer eine Antwort.

Konfiguration in dashboard.json:
  "fair_use": {"enabled": true, "max_per_hour": 6, "max_per_day": 40}
Standardwerte sind bewusst konservativ, aber großzügig genug für normalen Betrieb.
"""
import json
import os
import time

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
STATE_FILE = os.path.join(BOT_DIR, "run", "throttle.json")
CONFIG_FILE = os.path.join(BOT_DIR, "dashboard.json")
AUTOMATED = ("cron", "event")          # was gedrosselt wird — "chat" NIE
DEFAULTS = {"enabled": True, "max_per_hour": 6, "max_per_day": 40}


def config():
    """Frisch aus dashboard.json (Änderungen wirken ohne Neustart), fail-open."""
    cfg = dict(DEFAULTS)
    try:
        c = json.load(open(CONFIG_FILE)).get("fair_use", {})
        if isinstance(c, dict):
            for k in DEFAULTS:
                if k in c:
                    cfg[k] = c[k]
    except (OSError, ValueError):
        pass
    return cfg


def _load():
    try:
        d = json.load(open(STATE_FILE))
        return [float(t) for t in d.get("runs", [])]
    except (OSError, ValueError, TypeError):
        return []


def _save(runs):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"runs": runs}, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def _recent(now=None):
    """Zeitstempel der letzten 24 h (ältere fallen automatisch raus)."""
    now = now or time.time()
    return [t for t in _load() if now - t < 86400]


def allow(kind, now=None):
    """Darf dieser Lauf starten? Gibt (ja_nein, klartext_grund) zurück.
    Interaktive Läufe (kind='chat') sind immer erlaubt."""
    if kind not in AUTOMATED:
        return True, ""
    cfg = config()
    if not cfg.get("enabled", True):
        return True, ""
    now = now or time.time()
    runs = _recent(now)
    letzte_stunde = sum(1 for t in runs if now - t < 3600)
    if letzte_stunde >= int(cfg["max_per_hour"]):
        return False, (f"Fair-Use-Drossel: schon {letzte_stunde} automatische Läufe in der "
                       f"letzten Stunde (Grenze {cfg['max_per_hour']})")
    if len(runs) >= int(cfg["max_per_day"]):
        return False, (f"Fair-Use-Drossel: schon {len(runs)} automatische Läufe in 24 h "
                       f"(Grenze {cfg['max_per_day']})")
    return True, ""


def record(kind, now=None):
    """Einen tatsächlich gestarteten automatischen Lauf zählen."""
    if kind not in AUTOMATED:
        return
    now = now or time.time()
    runs = _recent(now)
    runs.append(now)
    _save(runs)


def stats(now=None):
    """Für die Dashboard-Anzeige: wie viel vom Kontingent ist verbraucht?"""
    now = now or time.time()
    runs = _recent(now)
    cfg = config()
    return {"letzte_stunde": sum(1 for t in runs if now - t < 3600),
            "letzte_24h": len(runs),
            "max_per_hour": cfg["max_per_hour"], "max_per_day": cfg["max_per_day"],
            "enabled": cfg["enabled"]}


if __name__ == "__main__":
    print(json.dumps(stats(), indent=2, ensure_ascii=False))
