#!/usr/bin/env python3
"""Öffnet das Operator-Dashboard im Browser (Zugangs-Token aus dem macOS-Schlüsselbund)."""
import json
import os
import subprocess
import sys

d = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(d, "..", "dashboard.json")))
r = subprocess.run(["security", "find-generic-password", "-s", "the-operator",
                    "-a", "dashboard-token", "-w"], capture_output=True, text=True)
if r.returncode != 0:
    # Fallback für Alt-Installationen mit .token-Datei
    tp = os.path.join(d, ".token")
    if not os.path.exists(tp):
        sys.exit("Dashboard-Token nicht gefunden — install.sh erneut ausführen")
    token = open(tp).read().strip()
else:
    token = r.stdout.strip()
url = f"http://127.0.0.1:{cfg.get('port', 8737)}/#t={token}"
subprocess.run(["open", url])
print("Dashboard geöffnet:", url.split("#")[0])
