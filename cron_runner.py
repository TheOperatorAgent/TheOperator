#!/usr/bin/env python3
"""Operator-Automationen (Issue #4, Plan A3): vom NUTZER im Dashboard angelegte,
zeitgesteuerte Aufträge an den eigenen Assistenten.

Sicherheitsmodell:
- Jobs stammen ausschließlich aus ~/.claude/matrix-bot/cron.json (0600) — die Datei wird
  nur vom Dashboard (Bearer-Token-geschützt) oder vom Nutzer selbst geschrieben.
- Ausführung = identischer Weg wie eine normale Chat-Nachricht des Owners: gleiche
  Werkzeug-Freigaben, gleiche VERHALTEN.md-Regeln, gleiches Sessions-/Audit-Logging.
- Kein Job kann sich selbst anlegen oder verändern; der Assistent hat keinen Schreibzugriff
  auf cron.json über seine Verhaltensregeln.

Stdlib-only. Wird vom Listener-Hauptloop minütlich aufgerufen.
"""
import json
import os
import threading
import time

CRON_FILE = os.path.expanduser("~/.claude/matrix-bot/cron.json")


def _field_match(field: str, val: int) -> bool:
    for part in field.split(","):
        if part == "*":
            return True
        if part.startswith("*/") and part[2:].isdigit() and int(part[2:]) > 0:
            if val % int(part[2:]) == 0:
                return True
        elif "-" in part:
            a, _, b = part.partition("-")
            if a.isdigit() and b.isdigit() and int(a) <= val <= int(b):
                return True
        elif part.isdigit() and int(part) == val:
            return True
    return False


def cron_match(expr: str, t: time.struct_time) -> bool:
    """5-Feld-Cron (min hour dom mon dow; dow 0=Sonntag) gegen lokale Zeit."""
    fields = expr.split()
    if len(fields) != 5:
        return False
    vals = [t.tm_min, t.tm_hour, t.tm_mday, t.tm_mon, (t.tm_wday + 1) % 7]
    return all(_field_match(f, v) for f, v in zip(fields, vals))


def load_jobs() -> list:
    try:
        return json.load(open(CRON_FILE)).get("jobs", [])
    except (OSError, ValueError):
        return []


def save_jobs(jobs: list) -> None:
    fd = os.open(CRON_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"jobs": jobs}, f, indent=1)
    os.replace(CRON_FILE + ".tmp", CRON_FILE)


def tick(owner_session, agent_sessions: dict, log=print) -> None:
    """Einmal pro Aufruf: fällige Jobs starten (zeitgesteuert oder per Dashboard
    „Jetzt ausführen"-Flag). Doppelstart derselben Minute wird über last_run_min verhindert."""
    now = time.localtime()
    this_minute = time.strftime("%Y-%m-%d %H:%M")
    jobs = load_jobs()
    changed = False
    for job in jobs:
        manual = bool(job.get("run_now"))
        scheduled = (job.get("enabled") and job.get("schedule")
                     and cron_match(job["schedule"], now)
                     and job.get("last_run_min") != this_minute)
        if not (manual or scheduled):
            continue
        target = job.get("target", "owner")
        session = owner_session if target == "owner" else agent_sessions.get(target)
        if not session:
            log(f"Automation '{job.get('name')}': Ziel '{target}' nicht aktiv — übersprungen")
            job.pop("run_now", None)
            changed = True
            continue
        job["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        job["last_run_min"] = this_minute
        job.pop("run_now", None)
        changed = True
        threading.Thread(target=session.run_automation, args=(dict(job),),
                         daemon=True).start()
    if changed:
        save_jobs(jobs)
