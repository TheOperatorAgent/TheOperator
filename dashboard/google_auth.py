"""Google-Drive-Anbindung: OAuth Auth-Code + PKCE mit dem EIGENEN Client des Nutzers.

Kein zentraler Hersteller-Client (Datenschutz by Design). Refresh-Token wird
AES-verschlüsselt gespeichert (tokens.py), Client-Secret ebenso.
"""
import base64
import hashlib
import json
import os
import secrets as pysecrets
import time
import urllib.parse

import requests

import tokens

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
CONN_FILE = os.path.join(BOT_DIR, "connections", "google.json")
SCOPE_RO = "https://www.googleapis.com/auth/drive.readonly"
SCOPE_RW = "https://www.googleapis.com/auth/drive"


def _load() -> dict:
    return json.load(open(CONN_FILE)) if os.path.exists(CONN_FILE) else {}


def _save(conn: dict) -> None:
    os.makedirs(os.path.dirname(CONN_FILE), exist_ok=True)
    fd = os.open(CONN_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(conn, f, indent=1)
    os.replace(CONN_FILE + ".tmp", CONN_FILE)


def set_client(client_id: str, client_secret: str) -> None:
    conn = _load()
    conn["client_id"] = client_id.strip()
    conn["client_secret_ref"] = "secrets"
    _save(conn)
    tokens.save("google_client_secret", client_secret.strip())


def start_auth(write: bool, redirect_uri: str) -> dict:
    conn = _load()
    if not conn.get("client_id"):
        raise RuntimeError("Zuerst Client-ID/Secret im Wizard eintragen")
    verifier = base64.urlsafe_b64encode(os.urandom(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = pysecrets.token_urlsafe(24)
    scope = SCOPE_RW if write else SCOPE_RO
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": conn["client_id"], "redirect_uri": redirect_uri,
        "response_type": "code", "scope": scope + " email",
        "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    })
    return {"auth_url": url, "state": state, "verifier": verifier,
            "write": write, "scope": scope, "redirect_uri": redirect_uri}


def complete_auth(flow: dict, code: str) -> dict:
    conn = _load()
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": conn["client_id"],
        "client_secret": tokens.load("google_client_secret") or "",
        "code": code, "code_verifier": flow["verifier"],
        "grant_type": "authorization_code", "redirect_uri": flow["redirect_uri"],
    }, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Google-Token-Tausch: HTTP {r.status_code} {r.text[:200]}")
    tok = r.json()
    email = ""
    try:
        ui = requests.get("https://www.googleapis.com/oauth2/v2/userinfo",
                          headers={"Authorization": "Bearer " + tok["access_token"]},
                          timeout=15)
        email = ui.json().get("email", "")
    except Exception:
        pass
    tokens.save("google_token", {
        "refresh_token": tok.get("refresh_token", ""),
        "access_token": tok["access_token"],
        "expires_at": time.time() + tok.get("expires_in", 3600) - 60,
    })
    conn.update({"write_enabled": flow["write"], "scopes": [flow["scope"]],
                 "connected_email": email,
                 "connected_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    _save(conn)
    return {"email": email, "write": flow["write"]}


def get_access_token() -> str:
    tok = tokens.load("google_token")
    if not tok:
        raise RuntimeError("Google nicht verbunden")
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    conn = _load()
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": conn["client_id"],
        "client_secret": tokens.load("google_client_secret") or "",
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
    }, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Google-Refresh fehlgeschlagen: HTTP {r.status_code}")
    new = r.json()
    tok["access_token"] = new["access_token"]
    tok["expires_at"] = time.time() + new.get("expires_in", 3600) - 60
    tokens.save("google_token", tok)
    return tok["access_token"]


def disconnect() -> None:
    tok = tokens.load("google_token")
    if tok:
        for t in (tok.get("refresh_token"), tok.get("access_token")):
            if t:
                try:
                    requests.post("https://oauth2.googleapis.com/revoke",
                                  data={"token": t}, timeout=15)
                except Exception:
                    pass
    tokens.delete("google_token")
    tokens.delete("google_client_secret")
    if os.path.exists(CONN_FILE):
        os.remove(CONN_FILE)


def status() -> dict:
    conn = _load()
    return {"configured": bool(conn.get("client_id")),
            "connected": bool(tokens.load("google_token")),
            "write_enabled": conn.get("write_enabled", False),
            "scopes": conn.get("scopes", []),
            "connected_email": conn.get("connected_email", "")}
