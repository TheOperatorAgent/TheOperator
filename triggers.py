#!/usr/bin/env python3
"""Event-getriggerte Proaktivität (#47, A2): Der Operator meldet sich von selbst.

Ablauf:
  n8n-Workflow / Skript / Webhook  →  POST /api/trigger {source, summary, payload}
        (Dashboard, 127.0.0.1 + Bearer-Token)
  → enqueue(): prüft Regel (Quelle erlaubt? Stichwort passt?) + Rate-Limit
  → events.json (0600) — Warteschlange
  → Listener-Hauptloop ruft drain(): je Ereignis EIN proaktiver Lauf über den
    normalen run_event-Pfad (gleiche Werkzeuge, VERHALTEN.md, Pseudonymisierung,
    Sessions/Audit wie jede Chat-Nachricht).

Sicherheitsmodell:
- Ohne passende, AKTIVE Regel wird kein Ereignis angenommen (Allowlist, kein
  offener Ingress). Regeln schreibt nur das Dashboard (Bearer) bzw. der Nutzer.
- Rate-Limit je Quelle (Standard 6/Stunde) gegen Alarm-Stürme.
- Jede proaktive Nachricht ist im Prompt klar als Ereignis gekennzeichnet.

Stdlib-only; reine Logik hier, I/O-Aufrufe macht Dashboard/Listener.
"""
import json
import os
import time

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
RULES_FILE = os.path.join(BOT_DIR, "triggers.json")
EVENTS_FILE = os.path.join(BOT_DIR, "events.json")
RATE_PER_HOUR = 6          # je Quelle
MAX_QUEUE = 50             # Notbremse gegen volllaufende Warteschlange


def _load(path, key):
    try:
        return json.load(open(path)).get(key, [])
    except (OSError, ValueError):
        return []


def _save(path, key, items):
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({key: items}, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def load_rules():
    return _load(RULES_FILE, "rules")


def save_rules(rules):
    _save(RULES_FILE, "rules", rules)


def load_events():
    return _load(EVENTS_FILE, "events")


def save_events(events):
    _save(EVENTS_FILE, "events", events)


def match_rule(rules, source, summary):
    """Erste aktive Regel, deren Quelle passt und deren Stichwort (falls gesetzt)
    in der Zusammenfassung vorkommt (case-insensitive). None = nicht erlaubt."""
    s = (summary or "").lower()
    for r in rules:
        if not r.get("enabled"):
            continue
        if r.get("source", "").strip().lower() != (source or "").strip().lower():
            continue
        kw = (r.get("keyword") or "").strip().lower()
        if kw and kw not in s:
            continue
        return r
    return None


def _rate_ok(events, source, now=None):
    now = now or time.time()
    recent = [e for e in events
              if e.get("source") == source and now - e.get("ts", 0) < 3600]
    return len(recent) < RATE_PER_HOUR


def enqueue(source, summary, payload=None, now=None):
    """Ereignis annehmen (oder ablehnen). Rückgabe: (ok, meldung).
    Persistiert bei ok in events.json; der Listener arbeitet die Queue ab."""
    source = (source or "").strip()
    summary = (summary or "").strip()
    if not source or not summary:
        return False, "source und summary sind Pflicht"
    rule = match_rule(load_rules(), source, summary)
    if not rule:
        return False, f"Keine aktive Regel erlaubt Quelle »{source}« (mit diesem Inhalt)"
    events = load_events()
    if len(events) >= MAX_QUEUE:
        return False, "Warteschlange voll"
    if not _rate_ok(events, source, now):
        return False, f"Rate-Limit: max. {RATE_PER_HOUR} Ereignisse/Stunde je Quelle"
    events.append({"ts": (now or time.time()), "source": source, "summary": summary,
                   "payload": payload if isinstance(payload, (dict, list, str)) else None,
                   "rule": rule.get("id"), "target": rule.get("target", "owner"),
                   "prompt": rule.get("prompt", "")})
    save_events(events)
    return True, "angenommen"


def event_prompt(event):
    """Prompt-Rahmung für einen proaktiven Lauf — klar als Ereignis gekennzeichnet."""
    base = (f"[Proaktives Ereignis von »{event.get('source')}«] {event.get('summary')}"
            + (f"\nDetails: {json.dumps(event['payload'], ensure_ascii=False)[:800]}"
               if event.get("payload") else ""))
    extra = (event.get("prompt") or "").strip()
    if extra:
        base += f"\nDeine Anweisung für dieses Ereignis: {extra}"
    base += ("\nMelde dich proaktiv mit einer kurzen, hilfreichen Nachricht — "
             "kennzeichne sie mit ⚡ am Anfang, damit klar ist, dass du dich selbst meldest.")
    return base


def drain(owner_session, agent_sessions, log=print, run=None):
    """Vom Listener-Loop aufgerufen: alle wartenden Ereignisse ausführen (je eines =
    ein Lauf). run(session, name, prompt) ist injizierbar (Tests); Standard:
    session.run_event(name, prompt) in eigenem Thread durch den Aufrufer."""
    events = load_events()
    if not events:
        return 0
    save_events([])          # sofort leeren — kein Doppellauf bei langsamen Läufen
    started = 0
    for ev in events:
        target = ev.get("target", "owner")
        session = owner_session if target == "owner" else (agent_sessions or {}).get(target)
        if not session:
            log(f"Ereignis von '{ev.get('source')}': Ziel '{target}' nicht aktiv — verworfen")
            continue
        name = f"{ev.get('source')}: {ev.get('summary', '')[:60]}"
        if run:
            run(session, name, event_prompt(ev))
        else:
            session.run_event(name, event_prompt(ev))
        started += 1
    return started
