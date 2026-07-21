#!/usr/bin/env python3
"""Sendet eine Nachricht als Bot in den konfigurierten Matrix-Raum.
Aufruf: python3 send.py "Text"   (oder Text über stdin)"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

creds = json.load(open(os.path.expanduser("~/.claude/matrix-bot/credentials.json")))
text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read().strip()
if not text:
    sys.exit("Kein Text übergeben")

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
