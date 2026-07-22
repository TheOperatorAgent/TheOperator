#!/usr/bin/env python3
"""Öffnet das Operator-Dashboard im Browser (Zugangs-Token aus dem OS-Secret-Store)."""
import json
import os
import sys

d = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
import secretstore       # noqa: E402  (stdlib-Modul aus BOT_DIR)
import platform_compat   # noqa: E402

cfg = json.load(open(os.path.join(d, "..", "dashboard.json")))
token = secretstore.get("dashboard-token")
if not token:
    # Fallback für Alt-Installationen mit .token-Datei
    tp = os.path.join(d, ".token")
    if not os.path.exists(tp):
        sys.exit("Dashboard-Token nicht gefunden — Installer erneut ausführen")
    token = open(tp).read().strip()
url = f"http://127.0.0.1:{cfg.get('port', 8737)}/#t={token}"
platform_compat.open_url(url)
print("Dashboard geöffnet:", url.split("#")[0])
