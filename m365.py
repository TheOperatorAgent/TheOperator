#!/usr/bin/env python3
"""Operator-Helfer für Microsoft 365 (nutzt die auto-registrierte Connector-App).

Aufrufe (Beispiele):
  m365.py mail list [n]              Letzte n Mails (Betreff, Von, Datum)
  m365.py mail read <id>             Eine Mail im Volltext
  m365.py mail send <an> <betreff> <text>
  m365.py cal list [tage]            Termine der nächsten Tage
  m365.py cal add <betreff> <startISO> <endeISO>
  m365.py files ls [pfad]            OneDrive-Inhalt
  m365.py files get <pfad> [ziel]    Datei herunterladen
  m365.py files put <lokal> [pfad]   Datei hochladen
  m365.py sites list                 SharePoint-Sites
  m365.py planner plans              Planner-Pläne (über Gruppen)
  m365.py teams list                 Teams + Kanäle

Jeder Aufruf prüft VOR dem Graph-Call die im Dashboard vergebenen Rechte und
nennt bei Ablehnung den fehlenden Regler. Läuft im Dashboard-venv:
  ~/.claude/matrix-bot/dashboard/venv/bin/python3 ~/.claude/matrix-bot/m365.py …
"""
import json
import os
import sys
import time

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
sys.path.insert(0, os.path.join(BOT_DIR, "dashboard"))

import requests  # noqa: E402

import tokens  # noqa: E402

GRAPH = "https://graph.microsoft.com/v1.0"
AUDIT = os.path.join(BOT_DIR, "audit.log")

NEEDS = {  # (dienst, regler) je Kommando-Präfix
    "mail list": ("mail", "read"), "mail read": ("mail", "read"),
    "mail send": ("mail", "write"),
    "cal list": ("calendar", "read"), "cal add": ("calendar", "write"),
    "files ls": ("onedrive", "read"), "files get": ("onedrive", "read"),
    "files put": ("onedrive", "write"),
    "sites list": ("sharepoint", "read"),
    "planner plans": ("planner", "read"),
    "teams list": ("teams", "read"),
}
REGLER_DE = {"read": "Lesen", "write": "Schreiben"}
DIENST_DE = {"mail": "Mail", "calendar": "Kalender", "onedrive": "OneDrive",
             "sharepoint": "SharePoint", "planner": "Planner", "teams": "Teams"}


def audit(action, target, ok=True):
    try:
        with open(AUDIT, "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "actor": "m365.py", "action": action,
                                "target": target, "ok": ok}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def conn():
    p = os.path.join(BOT_DIR, "connections", "m365.json")
    if not os.path.exists(p):
        sys.exit("M365 ist nicht verbunden — im Dashboard unter 'Microsoft 365' einrichten.")
    return json.load(open(p))


def require(cmd, c):
    need = NEEDS.get(cmd)
    if not need:
        return
    svc, mode = need
    if not c.get("permissions", {}).get(svc, {}).get(mode):
        sys.exit(f"Fehlendes Recht: {DIENST_DE[svc]} › {REGLER_DE[mode]} — "
                 f"im Dashboard unter 'Microsoft 365' aktivieren.")


def token(c, frisch=False):
    cached = tokens.load("m365_cc_token")
    if not frisch and cached and cached.get("expires_at", 0) > time.time():
        return cached["access_token"]
    secret = tokens.load("m365_secret")
    r = requests.post(
        f"https://login.microsoftonline.com/{c['tenant_id']}/oauth2/v2.0/token",
        data={"client_id": c["app_client_id"], "client_secret": secret,
              "scope": "https://graph.microsoft.com/.default",
              "grant_type": "client_credentials"}, timeout=30)
    if r.status_code != 200:
        sys.exit(f"M365-Token fehlgeschlagen: HTTP {r.status_code} {r.text[:200]}")
    t = r.json()
    tokens.save("m365_cc_token", {"access_token": t["access_token"],
                                  "expires_at": time.time() + t["expires_in"] - 120})
    return t["access_token"]


def g(c, method, path, payload=None, raw=False):
    """Bei 403 EINMAL mit frischem Token wiederholen — ein zwischengespeicherter Token
    trägt die alten Rechte in sich (real erlebt 30.07., siehe mcp_m365.g)."""
    def ruf(frisch=False):
        return requests.request(method, GRAPH + path,
                                headers={"Authorization": "Bearer " + token(c, frisch)},
                                json=payload, timeout=60)
    r = ruf()
    if r.status_code == 403:
        r = ruf(frisch=True)
    if r.status_code >= 400:
        extra = ("\n→ Prüfe im Dashboard unter 'Microsoft 365', ob der Regler an ist "
                 "und ob du auf 'Rechte aktualisieren' geklickt hast."
                 if r.status_code == 403 else "")
        sys.exit(f"Graph-Fehler {r.status_code}: {r.text[:300]}{extra}")
    return r.content if raw else (r.json() if r.text else {})


def default_user(c):
    """App-only braucht einen Ziel-Nutzer: erster Member-Account mit Mailbox."""
    if c.get("primary_user"):
        return c["primary_user"]
    users = g(c, "GET", "/users?$top=10&$select=userPrincipalName,mail,userType")
    for u in users.get("value", []):
        if u.get("mail") and u.get("userType", "Member") == "Member":
            return u["userPrincipalName"]
    sys.exit("Kein Nutzer mit Mailbox gefunden — primary_user in connections/m365.json setzen")


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    cmd = f"{args[0]} {args[1]}"
    c = conn()
    require(cmd, c)
    u = None

    if cmd == "mail list":
        u = default_user(c)
        n = int(args[2]) if len(args) > 2 else 10
        res = g(c, "GET", f"/users/{u}/messages?$top={n}&$select=id,subject,from,receivedDateTime&$orderby=receivedDateTime desc")
        for m in res.get("value", []):
            frm = m.get("from", {}).get("emailAddress", {}).get("address", "?")
            print(f"[{m['id'][-12:]}] {m['receivedDateTime'][:16]} | {frm} | {m.get('subject','')}")
    elif cmd == "mail read":
        u = default_user(c)
        res = g(c, "GET", f"/users/{u}/messages?$top=50&$select=id,subject,from,body")
        msg = next((m for m in res.get("value", []) if m["id"].endswith(args[2])), None)
        if not msg:
            sys.exit("Mail-ID nicht gefunden (Suffix aus 'mail list' verwenden)")
        import re as _re
        body = _re.sub(r"<[^>]+>", " ", msg["body"]["content"])
        print(f"Betreff: {msg.get('subject','')}\n\n{body[:4000]}")
    elif cmd == "mail send":
        u = default_user(c)
        to, subject, text = args[2], args[3], " ".join(args[4:])
        g(c, "POST", f"/users/{u}/sendMail", {"message": {
            "subject": subject, "body": {"contentType": "Text", "content": text},
            "toRecipients": [{"emailAddress": {"address": to}}]}})
        print(f"Mail an {to} gesendet")
    elif cmd == "cal list":
        u = default_user(c)
        days = int(args[2]) if len(args) > 2 else 7
        start = time.strftime("%Y-%m-%dT00:00:00")
        end = time.strftime("%Y-%m-%dT23:59:59", time.localtime(time.time() + days * 86400))
        res = g(c, "GET", f"/users/{u}/calendarView?startDateTime={start}&endDateTime={end}&$top=25&$select=subject,start,end&$orderby=start/dateTime")
        for e in res.get("value", []):
            print(f"{e['start']['dateTime'][:16]} – {e['end']['dateTime'][11:16]} | {e.get('subject','')}")
    elif cmd == "cal add":
        u = default_user(c)
        g(c, "POST", f"/users/{u}/events", {"subject": args[2],
            "start": {"dateTime": args[3], "timeZone": "Europe/Berlin"},
            "end": {"dateTime": args[4], "timeZone": "Europe/Berlin"}})
        print("Termin angelegt")
    elif cmd == "files ls":
        u = default_user(c)
        path = f":/{args[2]}:" if len(args) > 2 else ""
        res = g(c, "GET", f"/users/{u}/drive/root{path}/children?$select=name,size,folder,lastModifiedDateTime")
        for f in res.get("value", []):
            kind = "📁" if "folder" in f else "📄"
            print(f"{kind} {f['name']} ({f.get('size',0)} B, {f['lastModifiedDateTime'][:10]})")
    elif cmd == "files get":
        u = default_user(c)
        data = g(c, "GET", f"/users/{u}/drive/root:/{args[2]}:/content", raw=True)
        dest = args[3] if len(args) > 3 else os.path.basename(args[2])
        open(dest, "wb").write(data)
        print(f"Gespeichert: {dest} ({len(data)} B)")
    elif cmd == "files put":
        u = default_user(c)
        local = args[2]
        remote = args[3] if len(args) > 3 else os.path.basename(local)
        data = open(local, "rb").read()
        r = requests.put(f"{GRAPH}/users/{u}/drive/root:/{remote}:/content",
                         headers={"Authorization": "Bearer " + token(c)},
                         data=data, timeout=120)
        if r.status_code >= 400:
            sys.exit(f"Upload-Fehler {r.status_code}: {r.text[:200]}")
        print(f"Hochgeladen: {remote} ({len(data)} B)")
    elif cmd == "sites list":
        res = g(c, "GET", "/sites?search=*&$select=displayName,webUrl")
        for s in res.get("value", []):
            print(f"{s.get('displayName','?')} — {s.get('webUrl','')}")
    elif cmd == "planner plans":
        groups = g(c, "GET", "/groups?$top=20&$select=id,displayName")
        for grp in groups.get("value", []):
            try:
                plans = g(c, "GET", f"/groups/{grp['id']}/planner/plans?$select=id,title")
                for p in plans.get("value", []):
                    print(f"{grp['displayName']} › {p['title']} [{p['id']}]")
            except SystemExit:
                continue
    elif cmd == "teams list":
        res = g(c, "GET", "/teams?$top=20&$select=id,displayName")
        for t in res.get("value", []):
            print(f"Team: {t['displayName']}")
            try:
                ch = g(c, "GET", f"/teams/{t['id']}/channels?$select=displayName")
                for x in ch.get("value", []):
                    print(f"  # {x['displayName']}")
            except SystemExit:
                continue
    else:
        sys.exit(__doc__)
    audit(cmd, u or "")


if __name__ == "__main__":
    main()
