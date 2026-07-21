#!/usr/bin/env python3
"""Operator-Helfer für Google Drive (eigener OAuth-Client des Nutzers).

Aufrufe:
  gdrive.py ls [ordnerId]         Inhalt (Wurzel oder Ordner)
  gdrive.py search "query"        Dateien suchen (Name/Volltext)
  gdrive.py get <fileId> [ziel]   Datei herunterladen
  gdrive.py put <lokal> [ordnerId]  Datei hochladen (braucht Schreib-Regler!)
  gdrive.py mkdir <name> [ordnerId] Ordner anlegen (braucht Schreib-Regler!)

Läuft im Dashboard-venv:
  ~/.claude/matrix-bot/dashboard/venv/bin/python3 ~/.claude/matrix-bot/gdrive.py …
"""
import json
import os
import sys
import time

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
sys.path.insert(0, os.path.join(BOT_DIR, "dashboard"))

import requests  # noqa: E402

import google_auth  # noqa: E402

API = "https://www.googleapis.com/drive/v3"
AUDIT = os.path.join(BOT_DIR, "audit.log")


def audit(action, target, ok=True):
    try:
        with open(AUDIT, "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "actor": "gdrive.py", "action": action,
                                "target": target, "ok": ok}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def require_write():
    if not google_auth.status().get("write_enabled"):
        sys.exit("Fehlendes Recht: Google Drive › Schreiben — im Dashboard unter "
                 "'Google Drive' den Schreib-Regler aktivieren und neu verbinden.")


def h():
    return {"Authorization": "Bearer " + google_auth.get_access_token()}


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd = args[0]

    if cmd == "ls":
        parent = args[1] if len(args) > 1 else "root"
        r = requests.get(f"{API}/files", headers=h(), params={
            "q": f"'{parent}' in parents and trashed=false",
            "fields": "files(id,name,mimeType,size,modifiedTime)",
            "pageSize": 50, "orderBy": "folder,name"}, timeout=30)
        r.raise_for_status()
        for f in r.json().get("files", []):
            kind = "📁" if f["mimeType"].endswith("folder") else "📄"
            print(f"{kind} {f['name']} [{f['id']}] ({f.get('size','-')} B, {f['modifiedTime'][:10]})")
    elif cmd == "search" and len(args) > 1:
        q = args[1].replace("'", "\\'")
        r = requests.get(f"{API}/files", headers=h(), params={
            "q": f"(name contains '{q}' or fullText contains '{q}') and trashed=false",
            "fields": "files(id,name,mimeType,modifiedTime)", "pageSize": 25}, timeout=30)
        r.raise_for_status()
        for f in r.json().get("files", []):
            print(f"{f['name']} [{f['id']}] ({f['modifiedTime'][:10]})")
    elif cmd == "get" and len(args) > 1:
        fid = args[1]
        meta = requests.get(f"{API}/files/{fid}", headers=h(),
                            params={"fields": "name,mimeType"}, timeout=30).json()
        if meta.get("mimeType", "").startswith("application/vnd.google-apps"):
            r = requests.get(f"{API}/files/{fid}/export", headers=h(),
                             params={"mimeType": "application/pdf"}, timeout=120)
            name = meta["name"] + ".pdf"
        else:
            r = requests.get(f"{API}/files/{fid}", headers=h(),
                             params={"alt": "media"}, timeout=120)
            name = meta.get("name", fid)
        r.raise_for_status()
        dest = args[2] if len(args) > 2 else name
        open(dest, "wb").write(r.content)
        print(f"Gespeichert: {dest} ({len(r.content)} B)")
    elif cmd == "put" and len(args) > 1:
        require_write()
        local = args[1]
        meta = {"name": os.path.basename(local)}
        if len(args) > 2:
            meta["parents"] = [args[2]]
        r = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            headers=h(), files={
                "metadata": (None, json.dumps(meta), "application/json"),
                "file": open(local, "rb")}, timeout=300)
        r.raise_for_status()
        print(f"Hochgeladen: {meta['name']} [{r.json()['id']}]")
    elif cmd == "mkdir" and len(args) > 1:
        require_write()
        meta = {"name": args[1], "mimeType": "application/vnd.google-apps.folder"}
        if len(args) > 2:
            meta["parents"] = [args[2]]
        r = requests.post(f"{API}/files", headers=h(), json=meta, timeout=30)
        r.raise_for_status()
        print(f"Ordner angelegt: {args[1]} [{r.json()['id']}]")
    else:
        sys.exit(__doc__)
    audit(cmd, args[1] if len(args) > 1 else "")


if __name__ == "__main__":
    main()
