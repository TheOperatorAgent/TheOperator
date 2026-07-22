#!/usr/bin/env python3
"""Sendet eine Nachricht als Bot in den konfigurierten Matrix-Raum.
Aufruf: python3 send.py "Text"   (oder Text über stdin)"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request


def keychain_token(account, fallback):
    if fallback != "keychain":
        return fallback
    r = subprocess.run(["security", "find-generic-password", "-s", "the-operator",
                        "-a", account, "-w"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""

args = sys.argv[1:]
bot = None
if args and args[0] == "--bot":
    bot = args[1]
    args = args[2:]

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
print(json.load(urllib.request.urlopen(req, timeout=15))["event_id"])
