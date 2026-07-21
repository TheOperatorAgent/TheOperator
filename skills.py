#!/usr/bin/env python3
"""Operator Skills: wiederverwendbare Fähigkeiten als SKILL.md-Dateien.

Ein Skill ist ein Ordner workspace/.claude/skills/<name>/SKILL.md (Claude-Code-Standard,
wird von claude -p automatisch geladen und per Skill-Werkzeug aufgerufen). Quellen:
  - dashboard : von Michi im Dashboard angelegt/bearbeitet (heilig — Bot fasst sie nie an)
  - bot       : vom Operator selbst angelegt (bei erkannter Wiederholung im Chat)
  - scout     : Vorschlag des wöchentlichen Skill-Scouts, im Dashboard angenommen

Hermes-Vorbild („selbstverbessernde Skills"), aber bewusst OHNE deren Hauptärgernis:
Auto-Verbesserung überschreibt hier NIE manuelle Edits — existiert ein Skill mit
source!=bot, wird aus einem create ein Vorschlag (skills_proposals.json), den Michi
im Dashboard annimmt oder ablehnt.

Stdlib-only. CLI (für den Bot und den Scout):
  skills.py list                       Alle Skills (Name — Beschreibung)
  skills.py show <name>                SKILL.md ausgeben
  skills.py create <name> -d "Beschr." [--source bot]   Inhalt via stdin
  skills.py delete <name>
  skills.py propose <name> -d "Beschr." [-r "Begründung"]   Inhalt via stdin
  skills.py proposals                  Offene Vorschläge
  skills.py history [tage]             Kompakte Aufgaben-Historie aus sessions.db
"""
import hashlib
import json
import os
import re
import sys
import time

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
SKILLS_DIR = os.path.join(BOT_DIR, "workspace", ".claude", "skills")
PROPOSALS_FILE = os.path.join(BOT_DIR, "skills_proposals.json")
NAME_RE = re.compile(r"^[a-z0-9-]{2,40}$")


# ---------------------------------------------------------------- Frontmatter --
def parse(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return {"frontmatter": {}, "body": text}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return {"frontmatter": fm, "body": m.group(2)}


def serialize(name: str, description: str, body: str, source: str) -> str:
    return (f"---\nname: {name}\ndescription: {description}\n"
            f"source: {source}\n---\n\n{body.strip()}\n")


def _path(name: str) -> str:
    return os.path.join(SKILLS_DIR, name, "SKILL.md")


# ---------------------------------------------------------------- CRUD --
def list_skills() -> list:
    out = []
    if not os.path.isdir(SKILLS_DIR):
        return out
    for d in sorted(os.listdir(SKILLS_DIR)):
        p = _path(d)
        if not NAME_RE.match(d) or not os.path.exists(p):
            continue
        fm = parse(open(p).read())["frontmatter"]
        out.append({"name": d, "description": fm.get("description", ""),
                    "source": fm.get("source", "dashboard"),
                    "modified": time.strftime("%Y-%m-%dT%H:%M", time.localtime(os.path.getmtime(p)))})
    return out


def get(name: str) -> dict | None:
    if not NAME_RE.match(name) or not os.path.exists(_path(name)):
        return None
    p = parse(open(_path(name)).read())
    fm = p["frontmatter"]
    return {"name": name, "description": fm.get("description", ""),
            "source": fm.get("source", "dashboard"), "body": p["body"].lstrip("\n")}


def save(name: str, description: str, body: str, source: str = "dashboard") -> tuple:
    """Anlegen/Ändern. Bot/Scout dürfen nur eigene (source=bot) Skills überschreiben —
    Michis Handarbeit (source=dashboard/scout-angenommen) ist tabu → (False, 'geschützt')."""
    if not NAME_RE.match(name):
        return False, "Name muss ^[a-z0-9-]{2,40}$ entsprechen (z. B. pi-status)"
    if not description.strip() or not body.strip():
        return False, "Beschreibung und Anleitung sind Pflicht"
    old = get(name)
    if old and source == "bot" and old["source"] != "bot":
        return False, ("geschützt: Skill wurde manuell gepflegt — lege stattdessen "
                       "einen Vorschlag an (skills.py propose)")
    os.makedirs(os.path.dirname(_path(name)), exist_ok=True)
    tmp = _path(name) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(serialize(name, description.strip(), body, source))
    os.replace(tmp, _path(name))
    return True, "ok"


def delete(name: str) -> bool:
    if not NAME_RE.match(name) or not os.path.exists(_path(name)):
        return False
    os.remove(_path(name))
    try:
        os.rmdir(os.path.dirname(_path(name)))
    except OSError:
        pass
    return True


# ---------------------------------------------------------------- Vorschläge --
def load_proposals() -> list:
    try:
        return json.load(open(PROPOSALS_FILE)).get("proposals", [])
    except (OSError, ValueError):
        return []


def save_proposals(props: list) -> None:
    fd = os.open(PROPOSALS_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"proposals": props}, f, indent=1, ensure_ascii=False)
    os.replace(PROPOSALS_FILE + ".tmp", PROPOSALS_FILE)


def propose(name: str, description: str, body: str, reason: str = "",
            source: str = "bot") -> tuple:
    if not NAME_RE.match(name):
        return False, "Name muss ^[a-z0-9-]{2,40}$ entsprechen"
    if not description.strip() or not body.strip():
        return False, "Beschreibung und Anleitung sind Pflicht"
    props = load_proposals()
    # Dedup: gleicher Name ersetzt den alten Vorschlag (neuester gewinnt)
    props = [p for p in props if p["name"] != name]
    props.append({"id": hashlib.sha256(os.urandom(8)).hexdigest()[:8], "name": name,
                  "description": description.strip(), "content": body.strip(),
                  "reason": reason.strip(), "source": source,
                  "created": time.strftime("%Y-%m-%dT%H:%M")})
    save_proposals(props)
    return True, "ok"


def accept(pid: str) -> tuple:
    props = load_proposals()
    p = next((x for x in props if x["id"] == pid), None)
    if not p:
        return False, "Vorschlag nicht gefunden"
    # Annahme im Dashboard = bewusste Entscheidung ⇒ Skill gehört ab jetzt Michi
    ok, msg = save(p["name"], p["description"], p["content"], source="dashboard")
    if ok:
        save_proposals([x for x in props if x["id"] != pid])
    return ok, msg


def reject(pid: str) -> tuple:
    props = load_proposals()
    if not any(x["id"] == pid for x in props):
        return False, "Vorschlag nicht gefunden"
    save_proposals([x for x in props if x["id"] != pid])
    return True, "ok"


# ---------------------------------------------------------------- Historie (Scout) --
def history(days: int = 7, limit: int = 200) -> list:
    """Kompakte Aufgaben-Liste (nur eingehende Nachrichten) für die Muster-Analyse."""
    import sqlite3
    dbp = os.path.join(BOT_DIR, "sessions.db")
    if not os.path.exists(dbp):
        return []
    con = sqlite3.connect(dbp)
    rows = con.execute(
        "SELECT ts, bot, messages FROM sessions WHERE kind='chat' AND epoch > ? "
        "ORDER BY id DESC LIMIT ?", (time.time() - days * 86400, limit)).fetchall()
    con.close()
    return [{"ts": r[0], "bot": r[1], "task": " ".join(r[2].split())[:160]}
            for r in reversed(rows)]


# ---------------------------------------------------------------- CLI --
def _flag(args, *names, default=""):
    for n in names:
        if n in args:
            return args[args.index(n) + 1]
    return default


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    if cmd == "list":
        for s in list_skills():
            print(f"{s['name']} [{s['source']}] — {s['description']}")
    elif cmd == "show" and len(a) > 1:
        if not get(a[1]):
            print("Skill nicht gefunden")
            sys.exit(1)
        print(open(_path(a[1])).read())
    elif cmd == "create" and len(a) > 1:
        ok, msg = save(a[1], _flag(a, "-d", "--description"), sys.stdin.read(),
                       source=_flag(a, "--source", default="bot"))
        print(msg if not ok else f"Skill '{a[1]}' angelegt — ab der nächsten Nachricht einsatzbereit")
        sys.exit(0 if ok else 1)
    elif cmd == "delete" and len(a) > 1:
        sys.exit(0 if delete(a[1]) else 1)
    elif cmd == "propose" and len(a) > 1:
        ok, msg = propose(a[1], _flag(a, "-d", "--description"), sys.stdin.read(),
                          reason=_flag(a, "-r", "--reason"),
                          source=_flag(a, "--source", default="bot"))
        print(msg if not ok else f"Vorschlag '{a[1]}' angelegt — Michi entscheidet im Dashboard")
        sys.exit(0 if ok else 1)
    elif cmd == "proposals":
        for p in load_proposals():
            print(f"[{p['id']}] {p['name']} ({p['source']}, {p['created']}) — "
                  f"{p['description']} | Grund: {p['reason']}")
    elif cmd == "history":
        for h in history(int(a[1]) if len(a) > 1 else 7):
            print(f"{h['ts']} {h['bot']}: {h['task']}")
    else:
        sys.exit(__doc__)
