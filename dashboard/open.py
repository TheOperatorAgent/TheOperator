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
port = cfg.get("port", 8737)
url = f"http://127.0.0.1:{port}/#t={token}"
if platform_compat.open_url(url):
    print("Dashboard geöffnet:", url.split("#")[0])
else:
    # Kein Bildschirm da — typisch, wenn man per SSH auf einem Raspberry Pi arbeitet.
    # Das Dashboard hört bewusst nur auf 127.0.0.1, ist also von außen nicht erreichbar.
    # Der SSH-Tunnel ist der sichere Weg: er leitet den Port auf den eigenen Rechner um.
    import getpass
    import socket
    print("Hier ist kein Browser verfügbar (kein Bildschirm — vermutlich per SSH).")
    print()
    print("So kommst du vom eigenen Rechner ans Dashboard:")
    print(f"  1. Dort ein Terminal öffnen und eingeben:")
    print(f"       ssh -N -L {port}:127.0.0.1:{port} "
          f"{getpass.getuser()}@{socket.gethostname()}")
    print(f"  2. Dieses Fenster offen lassen und im Browser aufrufen:")
    print(f"       {url}")
    print()
    print("Direkt am Pi mit Bildschirm: einfach »operator« im Desktop-Terminal starten.")
