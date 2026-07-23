#!/usr/bin/env python3
"""Mail-Watch (#62): Der Operator meldet sich von selbst bei neuen Mails.

Produkt-Weg für Wünsche wie »informiere mich, wenn eine Mail von X kommt« —
KEIN Ad-hoc-Skript nötig (Headless-Schreibschutz #61). Der Operator (oder Michi)
legt per CLI eine Regel an; der Listener pollt alle ~5 min; neue Treffer landen
als Ereignis in der #47-Trigger-Queue → proaktiver Lauf fasst Mail + Anhang
zusammen (m365-MCP: mail_read + mail_attachments) und meldet sich in Matrix.

Aufrufe (venv-Python, wie m365.py):
  mail_watch.py add --name N --folder ORDNER [--from ABSENDER] [--prompt TEXT]
  mail_watch.py list
  mail_watch.py remove <id>
  mail_watch.py check          # vom Listener-Tick aufgerufen; auch manuell nutzbar
  mail_watch.py test <id>      # jüngste passende Mail als »neu« behandeln (E2E-Test)

Regeln + Gesehen-Liste: mail_watch.json (0600). Mail-Leserecht (Dashboard-Regler
Mail›Lesen) ist Voraussetzung — wird je Lauf geprüft.
"""
import json
import os
import sys
import time
import urllib.parse

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
sys.path.insert(0, BOT_DIR)
sys.path.insert(0, os.path.join(BOT_DIR, "dashboard"))

import triggers                    # noqa: E402  (stdlib; Ereignis-Queue #47)
from m365 import conn, g, default_user   # noqa: E402  (Graph-Zugriff, venv)

WATCH_FILE = os.path.join(BOT_DIR, "mail_watch.json")
SEEN_CAP = 300


def _load():
    try:
        return json.load(open(WATCH_FILE))
    except (OSError, ValueError):
        return {"rules": [], "seen": {}}


def _save(data):
    fd = os.open(WATCH_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(WATCH_FILE + ".tmp", WATCH_FILE)


def has_active_rules():
    """Billiger Vorab-Check für den Listener (stdlib-seitig nutzbar)."""
    return any(r.get("enabled") for r in _load().get("rules", []))


def _require_mail_read(c):
    if not c.get("permissions", {}).get("mail", {}).get("read"):
        sys.exit("Fehlendes Recht: Mail › Lesen — im Dashboard unter 'Microsoft 365' aktivieren.")


def _find_folder(c, user, name):
    """Ordner-ID per Anzeigename (Top-Level + eine Ebene tiefer)."""
    q = urllib.parse.quote(name.replace("'", "''"))
    r = g(c, "GET", f"/users/{user}/mailFolders?$filter=displayName eq '{q}'&$select=id,displayName")
    if r.get("value"):
        return r["value"][0]["id"]
    tops = g(c, "GET", f"/users/{user}/mailFolders?$top=50&$select=id,displayName")
    for top in tops.get("value", []):
        r = g(c, "GET", f"/users/{user}/mailFolders/{top['id']}/childFolders"
                        f"?$filter=displayName eq '{q}'&$select=id,displayName")
        if r.get("value"):
            return r["value"][0]["id"]
    return None


def _default_prompt(rule):
    who = rule.get("from") or "dem überwachten Ordner"
    return (f"In den Details steht eine mail_id. WICHTIG: Übergib sie EXAKT und unverändert "
            f"an die m365-Werkzeuge — auch wenn sie wie ein Name aussieht (das ist ein "
            f"Pseudonymisierungs-Platzhalter, die Werkzeuge lösen ihn selbst auf). "
            f"Rufe mail_read(mail_id) und bei Anhängen mail_attachments(mail_id) auf und fasse "
            f"BEIDES kompakt zusammen: Absender, Betreff, Kernaussagen der Mail und den Inhalt "
            f"jedes Anhangs (nicht lesbare Anhänge: Name/Typ/Größe nennen). Stelle KEINE "
            f"Rückfragen zur Einrichtung — die Überwachung läuft bereits. Es geht um Post von {who}.")


def cmd_add(args):
    name = _flag(args, "--name") or _flag(args, "-n")
    folder = _flag(args, "--folder") or _flag(args, "-f")
    sender = _flag(args, "--from") or ""
    prompt = _flag(args, "--prompt") or ""
    if not name or not folder:
        sys.exit("Pflicht: --name und --folder (Anzeigename des Postfach-Ordners)")
    c = conn()
    _require_mail_read(c)
    user = default_user(c)
    fid = _find_folder(c, user, folder)
    if not fid:
        sys.exit(f"Ordner »{folder}« nicht im Postfach von {user} gefunden — "
                 "Anzeigename exakt wie in Outlook angeben.")
    data = _load()
    rid = hex(int(time.time() * 1000))[2:][-8:]
    rule = {"id": rid, "name": name, "folder": folder, "folder_id": fid,
            "from": sender.lower(), "user": user, "enabled": True}
    data["rules"].append(rule)
    _save(data)
    # Passende Ereignis-Regel (#47) sicherstellen — Quelle mail-watch, gezielt je Watch-Regel
    trules = triggers.load_rules()
    if not any(r.get("source") == "mail-watch" and r.get("id") == "mw-" + rid for r in trules):
        trules.append({"id": "mw-" + rid, "name": f"Mail-Watch: {name}",
                       "source": "mail-watch", "keyword": rid,
                       "prompt": prompt or _default_prompt(rule),
                       "target": "owner", "enabled": True})
        triggers.save_rules(trules)
    print(f"Regel angelegt [{rid}]: Ordner »{folder}«"
          + (f", Absender {sender}" if sender else "") + f" — Postfach {user}. "
          "Der Operator meldet sich ab jetzt automatisch bei neuen Mails.")


def cmd_list():
    data = _load()
    if not data["rules"]:
        print("Keine Mail-Watch-Regeln.")
        return
    for r in data["rules"]:
        state = "aktiv" if r.get("enabled") else "pausiert"
        print(f"[{r['id']}] {r['name']} — Ordner »{r['folder']}«"
              + (f", Absender {r['from']}" if r.get("from") else "")
              + f" ({state}, {len(_load()['seen'].get(r['id'], []))} gesehen)")


def cmd_remove(rid):
    data = _load()
    before = len(data["rules"])
    data["rules"] = [r for r in data["rules"] if r["id"] != rid]
    data["seen"].pop(rid, None)
    _save(data)
    triggers.save_rules([r for r in triggers.load_rules() if r.get("id") != "mw-" + rid])
    print("entfernt" if len(data["rules"]) < before else "id nicht gefunden")


def _fetch(c, rule, top=10):
    return g(c, "GET", f"/users/{rule['user']}/mailFolders/{rule['folder_id']}/messages"
             f"?$top={top}&$orderby=receivedDateTime desc"
             "&$select=id,subject,from,receivedDateTime,hasAttachments").get("value", [])


def _matches(rule, msg):
    if not rule.get("from"):
        return True
    addr = (((msg.get("from") or {}).get("emailAddress") or {}).get("address") or "").lower()
    return addr == rule["from"]


def check(force_rule=None, mark_only=False):
    """Alle aktiven Regeln pollen; neue Treffer -> Trigger-Queue. Rückgabe: Anzahl neu.
    force_rule: diese Regel-ID auch dann melden, wenn die Mail schon gesehen ist (Test)."""
    data = _load()
    c = conn()
    _require_mail_read(c)
    new_total = 0
    for rule in data["rules"]:
        if not rule.get("enabled"):
            continue
        seen = set(data["seen"].get(rule["id"], []))
        first_run = not seen and rule["id"] != force_rule
        for msg in _fetch(c, rule):
            if not _matches(rule, msg):
                continue
            is_new = msg["id"] not in seen
            if rule["id"] == force_rule and not new_total:
                is_new = True                      # Test: jüngsten Treffer erzwingen
            if not is_new:
                continue
            seen.add(msg["id"])
            if first_run or mark_only:
                continue                           # Erstlauf: Bestand nur markieren
            frm = ((msg.get("from") or {}).get("emailAddress") or {})
            summary = (f"Neue Mail von {frm.get('name') or frm.get('address', '?')} "
                       f"({frm.get('address', '?')}): »{msg.get('subject', '(ohne Betreff)')}« "
                       f"im Ordner »{rule['folder']}« "
                       f"[{'mit Anhang' if msg.get('hasAttachments') else 'ohne Anhang'}] "
                       f"{rule['id']}")
            ok, info = triggers.enqueue("mail-watch", summary,
                                        payload={"mail_id": msg["id"],
                                                 "from": frm.get("address", ""),
                                                 "subject": msg.get("subject", ""),
                                                 "has_attachments": bool(msg.get("hasAttachments"))})
            if ok:
                new_total += 1
            else:
                print(f"Ereignis abgelehnt: {info}", file=sys.stderr)
        data["seen"][rule["id"]] = list(seen)[-SEEN_CAP:]
    _save(data)
    return new_total


def _flag(args, *names, default=None):
    for n in names:
        if n in args:
            i = args.index(n)
            if i + 1 < len(args):
                return args[i + 1]
    return default


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd, rest = args[0], args[1:]
    if cmd == "add":
        cmd_add(rest)
    elif cmd == "list":
        cmd_list()
    elif cmd == "remove" and rest:
        cmd_remove(rest[0])
    elif cmd == "check":
        n = check()
        print(f"{n} neue Mail(s) gemeldet" if n else "nichts Neues")
    elif cmd == "test" and rest:
        n = check(force_rule=rest[0])
        print(f"Test: {n} Ereignis(se) ausgelöst" if n else
              "Test: keine passende Mail im Ordner gefunden")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
