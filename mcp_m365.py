#!/usr/bin/env python3
"""Operator M365 MCP-Server (stdio) — Standard-MCP des Operators für Microsoft 365.

Eigenbau statt Fremd-Server (Entscheidung 2026-07-21, siehe RECHERCHE im Dev-Repo):
- nutzt die auto-registrierte Connector-App (app-only Client-Credentials aus tokens.py)
- JEDES Tool prüft vorher die im Dashboard vergebene Regler-Matrix (Least Privilege)
- Tools sind bewusst auf /users/{id}-Pfade gebaut (app-only-tauglich, kein /me)

Start (macht der Listener automatisch via --mcp-config):
  ~/.claude/matrix-bot/dashboard/venv/bin/python3 ~/.claude/matrix-bot/mcp_m365.py
"""
import json
import os
import re
import sys
import time

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
sys.path.insert(0, os.path.join(BOT_DIR, "dashboard"))

import requests  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

import tokens  # noqa: E402
try:
    import reid  # noqa: E402  (Re-ID der Pseudonymisierungs-Surrogate)
except ImportError:
    reid = None


def _rid(s):
    return reid.reidentify(s) if reid and s else s

GRAPH = "https://graph.microsoft.com/v1.0"
mcp = FastMCP("m365")

DIENST_DE = {"mail": "Mail", "calendar": "Kalender", "onedrive": "OneDrive",
             "sharepoint": "SharePoint", "planner": "Planner", "teams": "Teams"}


def conn():
    p = os.path.join(BOT_DIR, "connections", "m365.json")
    if not os.path.exists(p):
        raise RuntimeError("M365 ist nicht verbunden — im Dashboard unter 'Microsoft 365' einrichten.")
    return json.load(open(p))


def require(c, svc, mode):
    if not c.get("permissions", {}).get(svc, {}).get(mode):
        regler = "Lesen" if mode == "read" else "Schreiben"
        raise RuntimeError(f"Fehlendes Recht: {DIENST_DE[svc]} › {regler} — "
                           f"im Dashboard unter 'Microsoft 365' aktivieren.")


def token(c):
    cached = tokens.load("m365_cc_token")
    if cached and cached.get("expires_at", 0) > time.time():
        return cached["access_token"]
    r = requests.post(
        f"https://login.microsoftonline.com/{c['tenant_id']}/oauth2/v2.0/token",
        data={"client_id": c["app_client_id"], "client_secret": tokens.load("m365_secret"),
              "scope": "https://graph.microsoft.com/.default",
              "grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status()
    t = r.json()
    tokens.save("m365_cc_token", {"access_token": t["access_token"],
                                  "expires_at": time.time() + t["expires_in"] - 120})
    return t["access_token"]


def g(c, method, path, payload=None):
    r = requests.request(method, GRAPH + path,
                         headers={"Authorization": "Bearer " + token(c)},
                         json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Graph {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def user(c):
    u = c.get("primary_user")
    if not u:
        raise RuntimeError("Kein Benutzer gewählt — im Dashboard unter 'Microsoft 365' › 'Wessen Daten?' eintragen.")
    return u


def audit(action, target):
    try:
        with open(os.path.join(BOT_DIR, "audit.log"), "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "actor": "mcp_m365", "action": action,
                                "target": target, "ok": True}, ensure_ascii=False) + "\n")
    except OSError:
        pass


@mcp.tool()
def mail_list(count: int = 10) -> str:
    """Letzte Mails des Nutzers auflisten (ID, Datum, Absender, Betreff)."""
    c = conn(); require(c, "mail", "read"); u = user(c)
    res = g(c, "GET", f"/users/{u}/messages?$top={min(count, 50)}&$select=id,subject,from,receivedDateTime,isRead&$orderby=receivedDateTime desc")
    audit("mail_list", u)
    return "\n".join(
        f"[{m['id'][-12:]}] {m['receivedDateTime'][:16]} {'  ' if m.get('isRead') else '● '}"
        f"{m.get('from', {}).get('emailAddress', {}).get('address', '?')} | {m.get('subject', '')}"
        for m in res.get("value", [])) or "(keine Mails)"


def _resolve_mail(c, u, mail_id, select):
    """Mail per ID finden: Surrogate zuerst auflösen (Tool-Re-ID-Brücke — die
    Pseudonymisierung kann IDs durch Platzhalter ersetzt haben), lange IDs direkt
    per Graph laden (findet auch Unterordner-Mails), sonst Suffix aus mail_list."""
    mail_id = _rid(mail_id or "").strip()
    if len(mail_id) > 40:                     # volle Graph-ID → direkter Zugriff
        try:
            return g(c, "GET", f"/users/{u}/messages/{mail_id}?$select={select}")
        except Exception:
            pass
    res = g(c, "GET", f"/users/{u}/messages?$top=50&$select={select}"
                      "&$orderby=receivedDateTime desc")
    return next((m for m in res.get("value", []) if m["id"].endswith(mail_id)), None)


@mcp.tool()
def mail_read(mail_id: str) -> str:
    """Eine Mail im Volltext lesen. mail_id: Suffix aus mail_list ODER die ID aus einem
    Mail-Watch-Ereignis (exakt übernehmen — auch wenn sie wie ein Name aussieht,
    das ist ein Pseudonymisierungs-Platzhalter und wird automatisch aufgelöst)."""
    c = conn(); require(c, "mail", "read"); u = user(c)
    msg = _resolve_mail(c, u, mail_id, "id,subject,from,body,receivedDateTime")
    if not msg:
        return "Mail-ID nicht gefunden — Suffix aus mail_list verwenden."
    body = re.sub(r"<[^>]+>", " ", msg["body"]["content"])
    audit("mail_read", mail_id)
    return f"Von: {msg.get('from', {}).get('emailAddress', {}).get('address', '?')}\nBetreff: {msg.get('subject', '')}\nDatum: {msg['receivedDateTime'][:16]}\n\n{body[:6000]}"


@mcp.tool()
def mail_attachments(mail_id: str) -> str:
    """Anhänge einer Mail lesen: Name, Typ, Größe und — wo möglich — extrahierter Text
    (txt/csv/json/html direkt, PDF via pypdf). mail_id wie bei mail_read (Ereignis-IDs
    exakt übernehmen, Platzhalter werden automatisch aufgelöst)."""
    c = conn(); require(c, "mail", "read"); u = user(c)
    msg = _resolve_mail(c, u, mail_id, "id,subject")
    if not msg:
        return "Mail-ID nicht gefunden — Suffix aus mail_list verwenden."
    atts = g(c, "GET", f"/users/{u}/messages/{msg['id']}/attachments").get("value", [])
    audit("mail_attachments", mail_id)
    if not atts:
        return "(keine Anhänge)"
    import base64
    import io
    out = []
    for a in atts:
        name = a.get("name", "?")
        ctype = a.get("contentType", "?")
        size = a.get("size", 0)
        head = f"— {name} ({ctype}, {size // 1024} KB)"
        blob = a.get("contentBytes")
        if not blob:
            out.append(head + " [Inhalt nicht eingebettet — z. B. Element-Anhang]")
            continue
        raw = base64.b64decode(blob)
        text = None
        try:
            if name.lower().endswith(".pdf") or "pdf" in ctype:
                from pypdf import PdfReader
                pages = PdfReader(io.BytesIO(raw)).pages[:15]
                text = "\n".join(p.extract_text() or "" for p in pages)
            elif any(name.lower().endswith(e) for e in
                     (".txt", ".csv", ".json", ".md", ".log", ".xml", ".html")) \
                    or ctype.startswith("text/"):
                text = raw.decode("utf-8", "replace")
                if name.lower().endswith((".html", ".htm")):
                    text = re.sub(r"<[^>]+>", " ", text)
        except Exception as e:
            out.append(head + f" [Extraktion fehlgeschlagen: {e}]")
            continue
        if text and text.strip():
            out.append(head + "\n" + text.strip()[:4000])
        else:
            out.append(head + " [kein extrahierbarer Text — Binärformat]")
    return "\n\n".join(out)


@mcp.tool()
def mail_send(to: str, subject: str, text: str) -> str:
    """Eine Mail im Namen des Nutzers senden (braucht Mail › Schreiben)."""
    c = conn(); require(c, "mail", "write"); u = user(c)
    # Pseudonymisierungs-Surrogate → echte Werte, bevor die Mail real rausgeht
    to, subject, text = _rid(to), _rid(subject), _rid(text)
    g(c, "POST", f"/users/{u}/sendMail", {"message": {
        "subject": subject, "body": {"contentType": "Text", "content": text},
        "toRecipients": [{"emailAddress": {"address": to}}]}})
    audit("mail_send", to)
    return f"Mail an {to} gesendet."


@mcp.tool()
def calendar_list(days: int = 7) -> str:
    """Termine der nächsten Tage auflisten."""
    c = conn(); require(c, "calendar", "read"); u = user(c)
    start = time.strftime("%Y-%m-%dT00:00:00")
    end = time.strftime("%Y-%m-%dT23:59:59", time.localtime(time.time() + days * 86400))
    res = g(c, "GET", f"/users/{u}/calendarView?startDateTime={start}&endDateTime={end}&$top=30&$select=subject,start,end,location&$orderby=start/dateTime")
    audit("calendar_list", u)
    return "\n".join(
        f"{e['start']['dateTime'][:16]} – {e['end']['dateTime'][11:16]} | {e.get('subject', '')}"
        + (f" ({e['location']['displayName']})" if e.get("location", {}).get("displayName") else "")
        for e in res.get("value", [])) or "(keine Termine)"


@mcp.tool()
def calendar_add(subject: str, start_iso: str, end_iso: str) -> str:
    """Termin anlegen, Zeiten als ISO z. B. 2026-07-22T14:00:00 (braucht Kalender › Schreiben)."""
    c = conn(); require(c, "calendar", "write"); u = user(c)
    subject = _rid(subject)
    g(c, "POST", f"/users/{u}/events", {"subject": subject,
        "start": {"dateTime": start_iso, "timeZone": "Europe/Berlin"},
        "end": {"dateTime": end_iso, "timeZone": "Europe/Berlin"}})
    audit("calendar_add", subject)
    return f"Termin '{subject}' angelegt."


@mcp.tool()
def files_list(path: str = "") -> str:
    """OneDrive-Ordner auflisten (leer = Wurzel)."""
    c = conn(); require(c, "onedrive", "read"); u = user(c)
    p = f":/{path}:" if path else ""
    res = g(c, "GET", f"/users/{u}/drive/root{p}/children?$top=50&$select=name,size,folder,lastModifiedDateTime")
    audit("files_list", path or "/")
    return "\n".join(
        f"{'[Ordner]' if 'folder' in f else '[Datei] '} {f['name']} ({f.get('size', 0)} B, {f['lastModifiedDateTime'][:10]})"
        for f in res.get("value", [])) or "(leer)"


@mcp.tool()
def sharepoint_search(query: str = "*") -> str:
    """SharePoint-Sites suchen/auflisten."""
    c = conn(); require(c, "sharepoint", "read")
    res = g(c, "GET", f"/sites?search={query}&$select=displayName,webUrl")
    audit("sharepoint_search", query)
    return "\n".join(f"{s.get('displayName', '?')} — {s.get('webUrl', '')}"
                     for s in res.get("value", [])) or "(keine Sites)"


@mcp.tool()
def planner_plans() -> str:
    """Planner-Pläne aller Gruppen auflisten."""
    c = conn(); require(c, "planner", "read")
    out = []
    for grp in g(c, "GET", "/groups?$top=20&$select=id,displayName").get("value", []):
        try:
            for p in g(c, "GET", f"/groups/{grp['id']}/planner/plans?$select=id,title").get("value", []):
                out.append(f"{grp['displayName']} › {p['title']} [{p['id']}]")
        except RuntimeError:
            continue
    audit("planner_plans", "")
    return "\n".join(out) or "(keine Pläne)"


@mcp.tool()
def teams_list() -> str:
    """Teams und ihre Kanäle auflisten (nur Basisdaten)."""
    c = conn(); require(c, "teams", "read")
    out = []
    for t in g(c, "GET", "/teams?$top=20&$select=id,displayName").get("value", []):
        out.append(f"Team: {t['displayName']}")
        try:
            out += [f"  # {x['displayName']}" for x in
                    g(c, "GET", f"/teams/{t['id']}/channels?$select=displayName").get("value", [])]
        except RuntimeError:
            continue
    audit("teams_list", "")
    return "\n".join(out) or "(keine Teams)"


if __name__ == "__main__":
    mcp.run()  # stdio
