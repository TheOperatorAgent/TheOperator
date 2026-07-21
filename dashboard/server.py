"""Operator Dashboard — Backend (FastAPI, bindet ausschließlich 127.0.0.1).

Sicherheitsmodell:
- Bearer-Token-Pflicht für /api/* (Token-Hash in dashboard.json; Übergabe via URL-Fragment)
- Host-Header-Whitelist gegen DNS-Rebinding; kein CORS; kein Cookie => kein CSRF
- OAuth-Callbacks sind tokenfrei, aber über state + Pending-Flow-Registry abgesichert
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

import agents_store
import google_auth
import m365_setup
import tokens as token_store

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
DASH_CFG = json.load(open(os.path.join(BOT_DIR, "dashboard.json")))
PORT = DASH_CFG.get("port", 8737)
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
AUDIT = os.path.join(BOT_DIR, "audit.log")
BOTS_FILE = os.path.join(BOT_DIR, "bots.json")
LISTENER_LABEL = "com.the-operator.listener"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_pending_flows: dict = {}   # {"m365": flow, "google": flow}


# ---------------------------------------------------------------- Hilfen --
def audit(actor: str, action: str, target: str = "", ok: bool = True) -> None:
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "actor": actor,
             "action": action, "target": target, "ok": ok}
    try:
        if os.path.exists(AUDIT) and os.path.getsize(AUDIT) > 5 * 1024 * 1024:
            shutil.move(AUDIT, AUDIT + ".1")
        with open(AUDIT, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def err(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message_de": message}}, status_code=status)


def creds() -> dict:
    return json.load(open(os.path.join(BOT_DIR, "credentials.json")))


def load_bots() -> dict:
    if os.path.exists(BOTS_FILE):
        return json.load(open(BOTS_FILE))
    return {"bots": []}


def save_bots(data: dict) -> None:
    fd = os.open(BOTS_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(BOTS_FILE + ".tmp", BOTS_FILE)


def mx(homeserver: str, method: str, path: str, body=None, token: str = None, ok=(200,)):
    req = urllib.request.Request(
        homeserver + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Matrix {method} {path}: HTTP {e.code} {e.read().decode()[:200]}")


# ---------------------------------------------------------------- Middleware --
@app.middleware("http")
async def guard(request: Request, call_next):
    host = request.headers.get("host", "")
    if host not in ALLOWED_HOSTS:
        return Response("Forbidden (host)", status_code=403)
    path = request.url.path
    if path.startswith("/api/") and not path.endswith("/auth/callback"):
        auth = request.headers.get("authorization", "")
        tok = auth[7:] if auth.startswith("Bearer ") else ""
        if hashlib.sha256(tok.encode()).hexdigest() != DASH_CFG["token_sha256"]:
            return JSONResponse(
                {"error": {"code": "auth", "message_de": "Ungültiges Dashboard-Token"}},
                status_code=401)
    return await call_next(request)


# ---------------------------------------------------------------- System --
@app.get("/api/status")
def api_status():
    listener = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{LISTENER_LABEL}"],
        capture_output=True, text=True).returncode == 0
    mem_count = 0
    try:
        r = subprocess.run(["python3", os.path.join(BOT_DIR, "memory.py"), "count"],
                           capture_output=True, text=True, timeout=10)
        mem_count = int(r.stdout.strip() or 0)
    except Exception:
        pass
    c = creds()
    bots = load_bots()["bots"]
    return {
        "listener_running": listener,
        "owner": c.get("owner_id"),
        "bot": c.get("user_id"),
        "homeserver": c.get("homeserver"),
        "allowed_tools": c.get("allowed_tools", []),
        "agents": agents_store.list_agents(),
        "published": {b["agent"]: b["user_id"] for b in bots if b.get("enabled")},
        "memory_count": mem_count,
        "m365": m365_setup.status(),
        "google": google_auth.status(),
    }


@app.post("/api/listener/restart")
def api_listener_restart():
    r = subprocess.run(["launchctl", "kickstart", "-k",
                        f"gui/{os.getuid()}/{LISTENER_LABEL}"], capture_output=True, text=True)
    audit("dashboard", "listener.restart", ok=r.returncode == 0)
    if r.returncode != 0:
        return err("listener", f"kickstart fehlgeschlagen: {r.stderr.strip()}", 500)
    return {"ok": True}


@app.get("/api/audit")
def api_audit(limit: int = 200):
    if not os.path.exists(AUDIT):
        return {"entries": []}
    lines = open(AUDIT).readlines()[-limit:]
    return {"entries": [json.loads(x) for x in lines if x.strip()]}


@app.get("/api/verhalten")
def api_verhalten_get():
    p = os.path.join(BOT_DIR, "VERHALTEN.md")
    return {"content": open(p).read() if os.path.exists(p) else ""}


@app.put("/api/verhalten")
async def api_verhalten_put(request: Request):
    body = await request.json()
    p = os.path.join(BOT_DIR, "VERHALTEN.md")
    if os.path.exists(p):
        shutil.copy(p, p + ".bak")
    open(p, "w").write(body.get("content", ""))
    audit("dashboard", "verhalten.update")
    return {"ok": True}


# ---------------------------------------------------------------- Agenten --
@app.get("/api/agents")
def api_agents():
    bots = {b["agent"]: b for b in load_bots()["bots"]}
    out = []
    for a in agents_store.list_agents():
        b = bots.get(a["name"])
        a["published"] = bool(b and b.get("enabled"))
        a["bot_user_id"] = b["user_id"] if b else None
        out.append(a)
    return {"agents": out}


@app.post("/api/agents")
async def api_agents_create(request: Request):
    b = await request.json()
    if agents_store.get_agent(b.get("name", "")):
        return err("exists", "Agent existiert bereits", 409)
    ok, msg = agents_store.save_agent(
        b.get("name", ""), b.get("description", ""), b.get("tools", []),
        b.get("model", "inherit"), b.get("body", ""))
    audit("dashboard", "agent.create", b.get("name", ""), ok)
    return {"ok": True} if ok else err("validate", msg)


@app.get("/api/agents/{name}")
def api_agent_get(name: str):
    a = agents_store.get_agent(name)
    return a if a else err("notfound", "Agent nicht gefunden", 404)


@app.put("/api/agents/{name}")
async def api_agent_put(name: str, request: Request):
    if not agents_store.get_agent(name):
        return err("notfound", "Agent nicht gefunden", 404)
    b = await request.json()
    ok, msg = agents_store.save_agent(
        name, b.get("description", ""), b.get("tools", []),
        b.get("model", "inherit"), b.get("body", ""))
    audit("dashboard", "agent.update", name, ok)
    return {"ok": True} if ok else err("validate", msg)


@app.delete("/api/agents/{name}")
def api_agent_delete(name: str):
    bots = load_bots()
    if any(b["agent"] == name and b.get("enabled") for b in bots["bots"]):
        return err("published", "Agent ist als Bot veröffentlicht — zuerst Veröffentlichung aufheben", 409)
    ok = agents_store.delete_agent(name)
    audit("dashboard", "agent.delete", name, ok)
    return {"ok": True} if ok else err("notfound", "Agent nicht gefunden", 404)


# ---------------------------------------------------------------- Publishing --
@app.post("/api/agents/{name}/publish")
async def api_agent_publish(name: str, request: Request):
    agent = agents_store.get_agent(name)
    if not agent:
        return err("notfound", "Agent nicht gefunden", 404)
    body = await request.json()
    c = creds()
    hs = c["homeserver"]
    server_name = c["owner_id"].split(":", 1)[1]
    localpart = body.get("localpart") or name
    if not re.match(r"^[a-z0-9._=-]{1,64}$", localpart):
        return err("validate", "Ungültiger Bot-Benutzername")
    bot_mxid = f"@{localpart}:{server_name}"
    password = body.get("password") or hashlib.sha256(os.urandom(32)).hexdigest()[:20]

    bots = load_bots()
    if any(b["agent"] == name for b in bots["bots"]):
        return err("exists", "Agent ist bereits veröffentlicht", 409)

    try:
        if body.get("admin_user"):
            admin_tok = mx(hs, "POST", "/_matrix/client/v3/login", {
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": body["admin_user"]},
                "password": body.get("admin_password", "")})["access_token"]
            mx(hs, "PUT", f"/_synapse/admin/v2/users/{urllib.parse.quote(bot_mxid)}",
               {"password": password, "admin": False}, token=admin_tok, ok=(200, 201))
        login = mx(hs, "POST", "/_matrix/client/v3/login", {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": localpart},
            "password": password,
            "initial_device_display_name": f"Operator Agent {name}"})
        room = mx(hs, "POST", "/_matrix/client/v3/createRoom", {
            "is_direct": True, "invite": [c["owner_id"]],
            "preset": "trusted_private_chat", "name": f"{name} (Operator-Agent)"},
            token=login["access_token"])
    except RuntimeError as e:
        audit("dashboard", "agent.publish", name, False)
        return err("matrix", str(e), 502)

    bots["bots"].append({"agent": name, "user_id": bot_mxid,
                         "access_token": login["access_token"],
                         "room_id": room["room_id"], "enabled": True,
                         "created": time.strftime("%Y-%m-%dT%H:%M:%S")})
    save_bots(bots)
    audit("dashboard", "agent.publish", f"{name} -> {bot_mxid}")
    return {"ok": True, "user_id": bot_mxid, "room_id": room["room_id"]}


@app.delete("/api/agents/{name}/publish")
def api_agent_unpublish(name: str):
    bots = load_bots()
    entry = next((b for b in bots["bots"] if b["agent"] == name), None)
    if not entry:
        return err("notfound", "Agent ist nicht veröffentlicht", 404)
    c = creds()
    try:
        mx(c["homeserver"], "POST", "/_matrix/client/v3/logout", {},
           token=entry["access_token"])
    except RuntimeError:
        pass  # Token evtl. schon tot
    bots["bots"] = [b for b in bots["bots"] if b["agent"] != name]
    save_bots(bots)
    audit("dashboard", "agent.unpublish", name)
    return {"ok": True}


# ---------------------------------------------------------------- M365 --
@app.get("/api/m365/status")
def api_m365_status():
    return m365_setup.status()


@app.post("/api/m365/auth/start")
def api_m365_auth_start():
    try:
        flow = m365_setup.start_auth(f"http://localhost:{PORT}/api/m365/auth/callback")
    except RuntimeError as e:
        return err("m365", str(e))
    _pending_flows["m365"] = flow
    audit("dashboard", "m365.auth.start")
    return {"auth_url": flow["auth_uri"]}


@app.get("/api/m365/auth/callback")
def api_m365_auth_callback(request: Request):
    flow = _pending_flows.pop("m365", None)
    if not flow:
        return HTMLResponse("<h3>Kein laufender Anmeldevorgang.</h3>", status_code=400)
    try:
        result = m365_setup.complete_auth(flow, dict(request.query_params))
    except RuntimeError as e:
        audit("dashboard", "m365.auth.complete", ok=False)
        return HTMLResponse(f"<h3>Anmeldung fehlgeschlagen</h3><p>{e}</p>", status_code=400)
    audit("dashboard", "m365.auth.complete", result.get("account", ""))
    return HTMLResponse("<h3>✅ Angemeldet — dieses Fenster kann geschlossen werden.</h3>"
                        "<script>setTimeout(()=>window.close(),1500)</script>")


@app.post("/api/m365/setup/run")
async def api_m365_setup_run(request: Request):
    matrix = (await request.json()).get("permissions", {})

    def stream():
        try:
            for evt in m365_setup.ensure_pipeline(matrix):
                yield "data: " + json.dumps(evt, ensure_ascii=False) + "\n\n"
            audit("dashboard", "m365.setup", json.dumps(matrix))
        except Exception as e:
            audit("dashboard", "m365.setup", ok=False)
            yield "data: " + json.dumps(
                {"step": "error", "status": "error", "detail": str(e)}) + "\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.put("/api/m365/permissions")
async def api_m365_permissions(request: Request):
    matrix = (await request.json()).get("permissions", {})
    try:
        result = m365_setup.update_permissions(matrix)
    except Exception as e:
        audit("dashboard", "m365.permissions", ok=False)
        return err("m365", str(e), 502)
    audit("dashboard", "m365.permissions", json.dumps(result))
    return result


@app.delete("/api/m365")
def api_m365_delete():
    try:
        m365_setup.delete_connector()
    except Exception as e:
        return err("m365", str(e), 502)
    audit("dashboard", "m365.delete")
    return {"ok": True}


@app.put("/api/m365/setup-client")
async def api_m365_setup_client(request: Request):
    cid = (await request.json()).get("client_id", "").strip()
    if not re.match(r"^[0-9a-f-]{36}$", cid):
        return err("validate", "Client-ID muss eine GUID sein")
    cfg = json.load(open(os.path.join(BOT_DIR, "dashboard.json")))
    cfg["m365_setup_client_id"] = cid
    open(os.path.join(BOT_DIR, "dashboard.json"), "w").write(json.dumps(cfg, indent=1))
    global DASH_CFG
    DASH_CFG = cfg
    audit("dashboard", "m365.setup-client", cid)
    return {"ok": True}


# ---------------------------------------------------------------- Google --
@app.get("/api/google/status")
def api_google_status():
    return google_auth.status()


@app.put("/api/google/config")
async def api_google_config(request: Request):
    b = await request.json()
    if not b.get("client_id", "").endswith(".apps.googleusercontent.com"):
        return err("validate", "Client-ID muss auf .apps.googleusercontent.com enden")
    google_auth.set_client(b["client_id"], b.get("client_secret", ""))
    audit("dashboard", "google.config")
    return {"ok": True}


@app.post("/api/google/auth/start")
async def api_google_auth_start(request: Request):
    write = (await request.json()).get("write", False)
    try:
        flow = google_auth.start_auth(
            write, f"http://127.0.0.1:{PORT}/api/google/auth/callback")
    except RuntimeError as e:
        return err("google", str(e))
    _pending_flows["google"] = flow
    audit("dashboard", "google.auth.start", f"write={write}")
    return {"auth_url": flow["auth_url"]}


@app.get("/api/google/auth/callback")
def api_google_auth_callback(request: Request):
    q = dict(request.query_params)
    flow = _pending_flows.get("google")
    if not flow or q.get("state") != flow["state"]:
        return HTMLResponse("<h3>Ungültiger Anmeldevorgang (state).</h3>", status_code=400)
    _pending_flows.pop("google", None)
    if "code" not in q:
        return HTMLResponse(f"<h3>Abgebrochen: {q.get('error','?')}</h3>", status_code=400)
    try:
        result = google_auth.complete_auth(flow, q["code"])
    except RuntimeError as e:
        audit("dashboard", "google.auth.complete", ok=False)
        return HTMLResponse(f"<h3>Fehlgeschlagen</h3><p>{e}</p>", status_code=400)
    audit("dashboard", "google.auth.complete", result.get("email", ""))
    return HTMLResponse("<h3>✅ Google Drive verbunden — Fenster kann zu.</h3>"
                        "<script>setTimeout(()=>window.close(),1500)</script>")


@app.delete("/api/google")
def api_google_delete():
    google_auth.disconnect()
    audit("dashboard", "google.disconnect")
    return {"ok": True}


# ---------------------------------------------------------------- Static --
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/{fn}")
def static_file(fn: str):
    p = os.path.join(STATIC, os.path.basename(fn))
    if os.path.exists(p) and os.path.isfile(p):
        return FileResponse(p)
    return Response(status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
