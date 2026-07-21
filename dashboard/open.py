#!/usr/bin/env python3
"""Öffnet das Operator-Dashboard im Browser (mit Zugangs-Token in der URL)."""
import json
import os
import subprocess

d = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(d, "..", "dashboard.json")))
token = open(os.path.join(d, ".token")).read().strip()
url = f"http://127.0.0.1:{cfg.get('port', 8737)}/#t={token}"
subprocess.run(["open", url])
print("Dashboard geöffnet:", url.split("#")[0])
