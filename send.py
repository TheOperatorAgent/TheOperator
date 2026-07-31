#!/usr/bin/env python3
"""Sendet eine Nachricht als Bot in den konfigurierten Matrix-Raum.

Aufruf: python3 send.py [--bot NAME] [--quelle WOHER] [--meta JSON] "Text"
        (oder Text über stdin)

#132: Was hier rausgeht, landet ANSCHLIESSEND im Verlauf (sessions.db). Vorher
existierten proaktive Meldungen nur im Matrix-Raum — für den Operator waren sie nie
passiert. Michi bekam eine Mail-Zusammenfassung gepusht, fragte sieben Minuten später
»hast du den termin schon zugesagt?«, und der Operator wusste nicht, wovon die Rede war.

Das Protokollieren sitzt bewusst HIER und nicht in mail_watch.py/cron_runner.py/
triggers.py: send.py ist der eine Weg, den alle proaktiven Kanäle benutzen. Damit kann
kein künftiger Kanal es vergessen."""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
import secretstore  # noqa: E402  (stdlib-Modul aus BOT_DIR)


def keychain_token(account, fallback):
    if fallback != "keychain":
        return fallback
    return secretstore.get(account) or ""

args = sys.argv[1:]
bot = None
quelle = ""          # #132: wer hat das ausgelöst? (mail_watch, cron, trigger, …)
meta_roh = ""        # #132: Kennungen (Mail-ID, Termin-ID, Absender) als JSON
while args and args[0] in ("--bot", "--quelle", "--meta"):
    schalter, wert, args = args[0], (args[1] if len(args) > 1 else ""), args[2:]
    if schalter == "--bot":
        bot = wert
    elif schalter == "--quelle":
        quelle = wert
    else:
        meta_roh = wert

creds = json.load(open(os.path.expanduser("~/.claude/matrix-bot/credentials.json")))
if bot:
    bots = json.load(open(os.path.expanduser("~/.claude/matrix-bot/bots.json")))
    entry = next((b for b in bots["bots"] if b["agent"] == bot), None)
    if not entry:
        sys.exit(f"Bot '{bot}' nicht in bots.json")
    creds = {"homeserver": creds["homeserver"],
             "access_token": keychain_token("matrix-bot-" + bot, entry["access_token"]),
             "room_id": entry["room_id"]}
else:
    creds["access_token"] = keychain_token("matrix-owner", creds["access_token"])
if not creds["access_token"]:
    sys.exit("Kein Matrix-Token verfügbar (Keychain leer?)")

text = args[0] if args else sys.stdin.read().strip()
if not text:
    sys.exit("Kein Text übergeben")

# Tool-Re-ID-Brücke: Pseudonymisierungs-Surrogate → echte Werte (Michi sieht echte Namen)
sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
try:
    import reid
    text = reid.reidentify(text)
except Exception:
    pass

url = (
    f"{creds['homeserver']}/_matrix/client/v3/rooms/"
    f"{urllib.parse.quote(creds['room_id'])}/send/m.room.message/{time.time_ns()}"
)
req = urllib.request.Request(
    url,
    method="PUT",
    data=json.dumps({"msgtype": "m.text", "body": text}).encode(),
    headers={
        "Authorization": "Bearer " + creds["access_token"],
        "Content-Type": "application/json",
    },
)
event_id = json.load(urllib.request.urlopen(req, timeout=15))["event_id"]
print(event_id)

# #132: Erst jetzt protokollieren — was nicht rausging, gehört auch nicht in den Verlauf.
# Fail-open: Ein Fehler hier darf eine bereits zugestellte Nachricht nicht zum Fehlschlag
# machen. Der Verlauf ist Komfort, die Zustellung ist die Aufgabe.
try:
    import sessions
    meta = json.loads(meta_roh) if meta_roh else None
    sessions.record_proaktiv(bot or "owner", quelle or "operator", text, meta)
except Exception:
    pass
