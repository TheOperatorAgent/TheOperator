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

import sys
sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
import memory as memory_db          # noqa: E402  (stdlib-Modul aus BOT_DIR)
import sessions as sessions_db      # noqa: E402
import cron_runner                  # noqa: E402
import skills as skills_store       # noqa: E402
import vault as vault_store         # noqa: E402
import vaultwarden as vw_store       # noqa: E402  (optionales Vaultwarden-Backend)

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
    try:
        import redact as _redact
        target = _redact.redact(target or "")
    except ImportError:
        pass
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


def keychain_set(account: str, value: str) -> None:
    subprocess.run(["security", "add-generic-password", "-U", "-s", "the-operator",
                    "-a", account, "-w", value], check=True, capture_output=True)


def keychain_get(account: str, fallback: str = "keychain") -> str:
    if fallback != "keychain":
        return fallback
    r = subprocess.run(["security", "find-generic-password", "-s", "the-operator",
                        "-a", account, "-w"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def keychain_delete(account: str) -> None:
    subprocess.run(["security", "delete-generic-password", "-s", "the-operator",
                    "-a", account], capture_output=True)


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
    # Health (A6): Disk, Synapse, DB-Größen
    st = os.statvfs(BOT_DIR)
    disk_free_gb = round(st.f_bavail * st.f_frsize / 1e9, 1)
    synapse_ok = False
    try:
        req = urllib.request.Request(c["homeserver"] + "/health")
        synapse_ok = urllib.request.urlopen(req, timeout=5).status == 200
    except Exception:
        pass

    def _sz(fn):
        p = os.path.join(BOT_DIR, fn)
        return round(os.path.getsize(p) / 1e6, 2) if os.path.exists(p) else 0

    return {
        "listener_running": listener,
        "owner": c.get("owner_id"),
        "bot": c.get("user_id"),
        "homeserver": c.get("homeserver"),
        "allowed_tools": c.get("allowed_tools", []),
        "agents": agents_store.list_agents(),
        "published": {b["agent"]: b["user_id"] for b in bots if b.get("enabled")},
        "memory_count": mem_count,
        "skills_count": len(skills_store.list_skills()),
        "skill_proposals": len(skills_store.load_proposals()),
        "vault": vault_store.status(),
        "m365": m365_setup.status(),
        "google": google_auth.status(),
        "health": {"disk_free_gb": disk_free_gb, "synapse_ok": synapse_ok,
                   "memory_db_mb": _sz("memory.db"), "sessions_db_mb": _sz("sessions.db"),
                   "cron_jobs": len(cron_runner.load_jobs()),
                   "usage_5h": sessions_db.usage(5)},
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

    keychain_set("matrix-bot-" + name, login["access_token"])
    bots["bots"].append({"agent": name, "user_id": bot_mxid,
                         "access_token": "keychain",
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
        tok = keychain_get("matrix-bot-" + name, entry["access_token"])
        if tok:
            mx(c["homeserver"], "POST", "/_matrix/client/v3/logout", {}, token=tok)
    except RuntimeError:
        pass  # Token evtl. schon tot
    keychain_delete("matrix-bot-" + name)
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


@app.put("/api/m365/primary-user")
async def api_m365_primary_user(request: Request):
    upn = (await request.json()).get("upn", "").strip()
    if upn and ("@" not in upn or " " in upn):
        return err("validate", "Bitte die vollständige M365-E-Mail-Adresse angeben")
    m365_setup.set_primary_user(upn)
    audit("dashboard", "m365.primary-user", upn)
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


# ---------------------------------------------------------------- Verlauf (A1) --
@app.get("/api/sessions")
def api_sessions(q: str = "", limit: int = 30):
    if q.strip():
        return {"sessions": sessions_db.search(q, limit)}
    return {"sessions": sessions_db.list_sessions(limit)}


# ---------------------------------------------------------------- Nutzung (A4) --
@app.get("/api/usage")
def api_usage():
    return {
        "window_5h": sessions_db.usage(5),
        "buckets_24h": sessions_db.usage_buckets(24, 1),
        "buckets_7d": sessions_db.usage_buckets(24 * 7, 24),
    }


# ---------------------------------------------------------------- Gedächtnis (A2) --
@app.get("/api/memory")
def api_memory(q: str = "", limit: int = 50):
    con = memory_db.db()
    if q.strip():
        fq = memory_db.fts_query(q)
        rows = con.execute(
            "SELECT m.id, m.text, m.created, m.uses FROM memories_fts f "
            "JOIN memories m ON m.id=f.rowid WHERE memories_fts MATCH ? "
            "ORDER BY bm25(memories_fts) LIMIT ?", (fq, limit)).fetchall() if fq else []
    else:
        rows = con.execute(
            "SELECT id, text, created, uses FROM memories ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
    return {"memories": [{"id": r[0], "text": r[1], "created": r[2], "uses": r[3]}
                         for r in rows]}


@app.post("/api/memory")
async def api_memory_add(request: Request):
    text = (await request.json()).get("text", "").strip()
    if not text:
        return err("validate", "Text fehlt")
    con = memory_db.db()
    if not con.execute("SELECT 1 FROM memories WHERE text=?", (text,)).fetchone():
        con.execute("INSERT INTO memories(text) VALUES (?)", (text,))
        con.commit()
    audit("dashboard", "memory.add", text[:60])
    return {"ok": True}


@app.delete("/api/memory/{mid}")
def api_memory_delete(mid: int):
    con = memory_db.db()
    con.execute("DELETE FROM memories WHERE id=?", (mid,))
    con.commit()
    audit("dashboard", "memory.forget", str(mid))
    return {"ok": True}


# ---------------------------------------------------------------- Logs (A5) --
LOG_WHITELIST = {"listener": os.path.join(BOT_DIR, "listener.log"),
                 "dashboard": os.path.join(BOT_DIR, "dashboard.log")}


@app.get("/api/logs")
def api_logs(file: str = "listener", lines: int = 200, errors_only: bool = False):
    path = LOG_WHITELIST.get(file)
    if not path:
        return err("validate", "Unbekannte Log-Datei", 404)
    if not os.path.exists(path):
        return {"lines": []}
    out = open(path, errors="replace").readlines()[-max(10, min(lines, 2000)):]
    if errors_only:
        out = [x for x in out if any(k in x for k in ("Fehler", "FEHLER", "⚠", "ERROR", "rc=1", "401"))]
    return {"lines": [x.rstrip("\n") for x in out]}


# ---------------------------------------------------------------- Automationen (A3) --
@app.get("/api/cron")
def api_cron_list():
    return {"jobs": cron_runner.load_jobs()}


@app.post("/api/cron")
async def api_cron_create(request: Request):
    b = await request.json()
    if not b.get("name") or not b.get("prompt"):
        return err("validate", "Name und Auftrag sind Pflicht")
    if b.get("schedule") and not cron_runner.cron_match(
            b["schedule"], time.localtime()) and len(b["schedule"].split()) != 5:
        return err("validate", "Zeitplan muss 5-Feld-Cron sein (z. B. '0 7 * * *')")
    jobs = cron_runner.load_jobs()
    job = {"id": hashlib.sha256(os.urandom(8)).hexdigest()[:8],
           "name": b["name"], "schedule": b.get("schedule", ""),
           "prompt": b["prompt"], "target": b.get("target", "owner"),
           "enabled": bool(b.get("enabled", True))}
    jobs.append(job)
    cron_runner.save_jobs(jobs)
    audit("dashboard", "cron.create", job["name"])
    return {"ok": True, "id": job["id"]}


@app.put("/api/cron/{jid}")
async def api_cron_update(jid: str, request: Request):
    b = await request.json()
    jobs = cron_runner.load_jobs()
    job = next((j for j in jobs if j["id"] == jid), None)
    if not job:
        return err("notfound", "Automation nicht gefunden", 404)
    for k in ("name", "schedule", "prompt", "target", "enabled"):
        if k in b:
            job[k] = b[k]
    cron_runner.save_jobs(jobs)
    audit("dashboard", "cron.update", job["name"])
    return {"ok": True}


@app.delete("/api/cron/{jid}")
def api_cron_delete(jid: str):
    jobs = cron_runner.load_jobs()
    if not any(j["id"] == jid for j in jobs):
        return err("notfound", "Automation nicht gefunden", 404)
    cron_runner.save_jobs([j for j in jobs if j["id"] != jid])
    audit("dashboard", "cron.delete", jid)
    return {"ok": True}


@app.post("/api/cron/{jid}/run")
def api_cron_run(jid: str):
    jobs = cron_runner.load_jobs()
    job = next((j for j in jobs if j["id"] == jid), None)
    if not job:
        return err("notfound", "Automation nicht gefunden", 404)
    job["run_now"] = True
    cron_runner.save_jobs(jobs)
    audit("dashboard", "cron.run_now", job["name"])
    return {"ok": True, "info": "Listener startet den Lauf binnen ~5 Sekunden"}


# ---------------------------------------------------------------- Tresor --
_vault_fails = {"count": 0, "until": 0.0}


def _vault_brake() -> JSONResponse | None:
    if time.time() < _vault_fails["until"]:
        wait = int(_vault_fails["until"] - time.time()) + 1
        return err("ratelimit", f"Zu viele Fehlversuche — bitte {wait} s warten", 429)
    return None


def _vault_fail():
    _vault_fails["count"] += 1
    if _vault_fails["count"] >= 5:
        _vault_fails["until"] = time.time() + 30
        _vault_fails["count"] = 0


@app.get("/api/vault/status")
def api_vault_status():
    return vault_store.status()


@app.post("/api/vault/init")
async def api_vault_init(request: Request):
    b = await request.json()
    try:
        recovery_key = vault_store.init(b.get("master_pw", ""))
    except (ValueError, RuntimeError) as e:
        audit("dashboard", "vault.init", "", False)
        return err("validate", str(e), 409 if "existiert" in str(e) else 400)
    audit("dashboard", "vault.init", "")
    return {"ok": True, "recovery_key": recovery_key}


@app.post("/api/vault/unlock")
async def api_vault_unlock(request: Request):
    if (brake := _vault_brake()):
        return brake
    b = await request.json()
    try:
        vault_store.unlock(b.get("master_pw", ""))
    except (ValueError, RuntimeError) as e:
        _vault_fail()
        audit("dashboard", "vault.unlock", "", False)
        return err("auth", str(e), 403)
    _vault_fails["count"] = 0
    audit("dashboard", "vault.unlock", "")
    return {"ok": True}


@app.post("/api/vault/lock")
def api_vault_lock():
    vault_store.lock()
    audit("dashboard", "vault.lock", "")
    return {"ok": True}


@app.get("/api/vault/entries")
def api_vault_entries():
    try:
        return {"entries": vault_store.list_entries()}
    except PermissionError:
        return err("locked", "Tresor ist gesperrt", 423)
    except RuntimeError as e:
        return err("notfound", str(e), 404)


@app.put("/api/vault/entries/{name}")
async def api_vault_entry_put(name: str, request: Request):
    b = await request.json()
    try:
        if b.get("value"):
            vault_store.add_entry(name, b["value"], b.get("description", ""),
                                  b.get("username", ""), b.get("url", ""))
        else:
            vault_store.update_meta(name, b.get("description"), b.get("username"),
                                    b.get("url"))
    except PermissionError:
        return err("locked", "Tresor ist gesperrt", 423)
    except KeyError:
        return err("notfound", "Eintrag nicht gefunden (neuer Eintrag braucht einen Wert)", 404)
    except (ValueError, RuntimeError) as e:
        return err("validate", str(e))
    audit("dashboard", "vault.entry.save", name)
    return {"ok": True}


@app.delete("/api/vault/entries/{name}")
def api_vault_entry_delete(name: str):
    try:
        vault_store.remove_entry(name)
    except PermissionError:
        return err("locked", "Tresor ist gesperrt", 423)
    except (KeyError, RuntimeError):
        return err("notfound", "Eintrag nicht gefunden", 404)
    audit("dashboard", "vault.entry.delete", name)
    return {"ok": True}


@app.post("/api/vault/rotate-master")
async def api_vault_rotate(request: Request):
    if (brake := _vault_brake()):
        return brake
    b = await request.json()
    try:
        vault_store.rotate_master(b.get("old_pw", ""), b.get("new_pw", ""))
    except (ValueError, RuntimeError) as e:
        _vault_fail()
        audit("dashboard", "vault.rotate", "", False)
        return err("auth", str(e), 403)
    audit("dashboard", "vault.rotate", "")
    return {"ok": True}


@app.get("/api/vault/fido")
def api_vault_fido_list():
    return {"keys": vault_store.fido_list()}


@app.post("/api/vault/fido/enroll")
async def api_vault_fido_enroll(request: Request):
    label = (await request.json()).get("label", "")
    try:
        name = vault_store.fido_enroll(label)
    except PermissionError:
        return err("locked", "Tresor ist gesperrt", 423)
    except (ValueError, RuntimeError) as e:
        audit("dashboard", "vault.fido.add", "", False)
        return err("fido", str(e))
    audit("dashboard", "vault.fido.add", name)
    return {"ok": True, "label": name}


@app.post("/api/vault/fido/unlock")
def api_vault_fido_unlock():
    try:
        vault_store.fido_unlock()
    except (ValueError, RuntimeError) as e:
        audit("dashboard", "vault.fido.unlock", "", False)
        return err("fido", str(e), 403)
    audit("dashboard", "vault.fido.unlock", "")
    return {"ok": True}


@app.delete("/api/vault/fido/{label}")
def api_vault_fido_remove(label: str):
    try:
        vault_store.fido_remove(label)
    except PermissionError:
        return err("locked", "Tresor ist gesperrt", 423)
    except (KeyError, RuntimeError):
        return err("notfound", "Schlüssel nicht gefunden", 404)
    audit("dashboard", "vault.fido.remove", label)
    return {"ok": True}


@app.post("/api/vault/recover")
async def api_vault_recover(request: Request):
    if (brake := _vault_brake()):
        return brake
    b = await request.json()
    try:
        new_key = vault_store.recover(b.get("recovery_key", ""), b.get("new_master_pw", ""))
    except (ValueError, RuntimeError) as e:
        _vault_fail()
        audit("dashboard", "vault.recover", "", False)
        return err("auth", str(e), 403)
    audit("dashboard", "vault.recover", "")
    return {"ok": True, "recovery_key": new_key}


# ------------------------------------------------ Tresor-Backend (lokal/Vaultwarden) --
def _save_dash_cfg() -> None:
    """dashboard.json atomar (0600) schreiben — Listener/vault lesen es pro Nutzung frisch."""
    p = os.path.join(BOT_DIR, "dashboard.json")
    fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(DASH_CFG, f, indent=1)
    os.replace(p + ".tmp", p)


@app.get("/api/vault/backend")
def api_vault_backend_get():
    return {"backend": DASH_CFG.get("vault_backend", "local"),
            "vaultwarden": vw_store.status()}


@app.put("/api/vault/backend")
async def api_vault_backend_put(request: Request):
    b = await request.json()
    backend = b.get("backend", "local")
    if backend not in ("local", "vaultwarden"):
        return err("validate", "Unbekanntes Backend")
    DASH_CFG["vault_backend"] = backend
    _save_dash_cfg()
    audit("dashboard", "vault.backend", backend)
    return {"ok": True, "backend": backend}


@app.put("/api/vault/vaultwarden/config")
async def api_vw_config(request: Request):
    url = ((await request.json()).get("url") or "").strip()
    try:
        vw_store.set_server(url)
    except (ValueError, RuntimeError) as e:
        return err("vaultwarden", str(e))
    audit("dashboard", "vaultwarden.config", url)
    return {"ok": True}


@app.post("/api/vault/vaultwarden/unlock")
async def api_vw_unlock(request: Request):
    if (brake := _vault_brake()):
        return brake
    b = await request.json()
    try:
        vw_store.unlock(b.get("master_pw", ""), b.get("email", ""))
    except (ValueError, RuntimeError) as e:
        _vault_fail()
        audit("dashboard", "vaultwarden.unlock", "", False)
        return err("auth", str(e), 403)
    _vault_fails["count"] = 0
    audit("dashboard", "vaultwarden.unlock", "")
    return {"ok": True}


@app.post("/api/vault/vaultwarden/lock")
def api_vw_lock():
    vw_store.lock()
    audit("dashboard", "vaultwarden.lock", "")
    return {"ok": True}


@app.get("/api/vault/vaultwarden/items")
def api_vw_items():
    try:
        return {"items": vw_store.list_items()}
    except PermissionError:
        return err("locked", "Vaultwarden-Tresor ist gesperrt", 423)
    except (RuntimeError, ValueError) as e:
        return err("vaultwarden", str(e), 400)


@app.delete("/api/vault/vaultwarden")
def api_vw_disconnect():
    vw_store.disconnect()
    audit("dashboard", "vaultwarden.disconnect", "")
    return {"ok": True}


# ---------------------------------------------------------------- n8n --
N8N_CONN = os.path.join(BOT_DIR, "connections", "n8n.json")


@app.get("/api/n8n/status")
def api_n8n_status():
    cfg = {}
    try:
        cfg = json.load(open(N8N_CONN))
    except (OSError, ValueError):
        pass
    return {"configured": bool(cfg.get("url")) and bool(token_store.load("n8n_api_key")),
            "url": cfg.get("url", "")}


@app.put("/api/n8n/config")
async def api_n8n_config(request: Request):
    b = await request.json()
    url = (b.get("url") or "").strip().rstrip("/")
    key = (b.get("api_key") or "").strip()
    if not url.startswith(("http://", "https://")):
        return err("validate", "Die Adresse muss mit http:// oder https:// beginnen")
    if not key:
        return err("validate", "API-Key fehlt (in n8n: Settings › n8n API › Create an API key)")
    # Verbindung SOFORT testen — der Nutzer sieht direkt, ob es passt
    import urllib.request as _ur
    req = _ur.Request(url + "/api/v1/workflows?limit=1",
                      headers={"X-N8N-API-KEY": key})
    try:
        with _ur.urlopen(req, timeout=10) as r:
            if r.status != 200:
                return err("n8n", f"n8n antwortet mit HTTP {r.status}")
    except Exception as e:
        code = getattr(e, "code", None)
        if code == 401:
            return err("n8n", "n8n lehnt den API-Key ab — bitte neu erzeugen und einfügen", 400)
        return err("n8n", f"n8n unter {url} nicht erreichbar: {str(e)[:120]}", 400)
    os.makedirs(os.path.dirname(N8N_CONN), exist_ok=True)
    fd = os.open(N8N_CONN + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"url": url}, f)
    os.replace(N8N_CONN + ".tmp", N8N_CONN)
    token_store.save("n8n_api_key", key)
    audit("dashboard", "n8n.config", url)
    return {"ok": True}


@app.delete("/api/n8n")
def api_n8n_delete():
    try:
        os.remove(N8N_CONN)
    except OSError:
        pass
    token_store.delete("n8n_api_key")
    audit("dashboard", "n8n.disconnect", "")
    return {"ok": True}


# ---------------------------------------------------------------- Pseudonymisierung --
PII_MODES = {"structured", "standard", "strict"}


@app.get("/api/pseudonymize")
def api_pii_get():
    cfg = DASH_CFG.get("pseudonymize", {})
    stats = {}
    try:
        stats = json.load(open(os.path.join(BOT_DIR, "pseudonymize-stats.json")))
    except (OSError, ValueError):
        pass
    return {"enabled": cfg.get("enabled", True), "mode": cfg.get("mode", "standard"),
            "allow": cfg.get("allow", []), "deny": cfg.get("deny", []),
            "last": stats, "presidio_ready": os.path.exists(
                os.path.join(BOT_DIR, "dashboard", "venv", "bin", "python3"))}


@app.put("/api/pseudonymize")
async def api_pii_put(request: Request):
    b = await request.json()
    cfg = DASH_CFG.setdefault("pseudonymize", {})
    if "enabled" in b:
        cfg["enabled"] = bool(b["enabled"])
    if b.get("mode") in PII_MODES:
        cfg["mode"] = b["mode"]
    if isinstance(b.get("allow"), list):
        cfg["allow"] = [str(x).strip() for x in b["allow"] if str(x).strip()]
    if isinstance(b.get("deny"), list):
        cfg["deny"] = [str(x).strip() for x in b["deny"] if str(x).strip()]
    # dashboard.json atomar (0600) schreiben — der Listener liest es pro Nachricht frisch
    p = os.path.join(BOT_DIR, "dashboard.json")
    fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(DASH_CFG, f, indent=1)
    os.replace(p + ".tmp", p)
    audit("dashboard", "pseudonymize.config", cfg.get("mode", ""))
    return {"ok": True}


# ---------------------------------------------------------------- Skills --
@app.get("/api/skills")
def api_skills():
    return {"skills": skills_store.list_skills(),
            "proposals": skills_store.load_proposals()}


@app.get("/api/skills/{name}")
def api_skill_get(name: str):
    s = skills_store.get(name)
    return s if s else err("notfound", "Skill nicht gefunden", 404)


@app.post("/api/skills")
async def api_skill_create(request: Request):
    b = await request.json()
    if skills_store.get(b.get("name", "")):
        return err("exists", "Skill existiert bereits", 409)
    ok, msg = skills_store.save(b.get("name", ""), b.get("description", ""),
                                b.get("body", ""), source="dashboard")
    audit("dashboard", "skill.create", b.get("name", ""), ok)
    return {"ok": True} if ok else err("validate", msg)


@app.put("/api/skills/{name}")
async def api_skill_put(name: str, request: Request):
    if not skills_store.get(name):
        return err("notfound", "Skill nicht gefunden", 404)
    b = await request.json()
    ok, msg = skills_store.save(name, b.get("description", ""), b.get("body", ""),
                                source="dashboard")
    audit("dashboard", "skill.update", name, ok)
    return {"ok": True} if ok else err("validate", msg)


@app.delete("/api/skills/{name}")
def api_skill_delete(name: str):
    ok = skills_store.delete(name)
    audit("dashboard", "skill.delete", name, ok)
    return {"ok": True} if ok else err("notfound", "Skill nicht gefunden", 404)


@app.post("/api/skills/proposals/{pid}/accept")
def api_skill_proposal_accept(pid: str):
    ok, msg = skills_store.accept(pid)
    audit("dashboard", "skill.proposal_accept", pid, ok)
    return {"ok": True} if ok else err("validate", msg)


@app.post("/api/skills/proposals/{pid}/reject")
def api_skill_proposal_reject(pid: str):
    ok, msg = skills_store.reject(pid)
    audit("dashboard", "skill.proposal_reject", pid, ok)
    return {"ok": True} if ok else err("validate", msg)


# ---------------------------------------------------------------- MCP (B1) --
MCP_FILE = os.path.join(BOT_DIR, "workspace", ".mcp.json")


def load_mcp() -> dict:
    try:
        return json.load(open(MCP_FILE))
    except (OSError, ValueError):
        return {"mcpServers": {}}


@app.get("/api/mcp")
def api_mcp_list():
    servers = load_mcp().get("mcpServers", {})
    out = []
    for name, cfg in servers.items():
        out.append({"name": name,
                    "transport": "http" if cfg.get("url") else "stdio",
                    "command": cfg.get("command", ""), "url": cfg.get("url", ""),
                    "args": cfg.get("args", []), "env_keys": sorted(cfg.get("env", {}))})
    return {"servers": out}


@app.post("/api/mcp")
async def api_mcp_add(request: Request):
    b = await request.json()
    name = b.get("name", "").strip()
    if not re.match(r"^[a-zA-Z0-9_-]{2,32}$", name):
        return err("validate", "Ungültiger Server-Name")
    if not b.get("command") and not b.get("url"):
        return err("validate", "command (stdio) oder url (http) angeben")
    data = load_mcp()
    entry = {}
    if b.get("url"):
        entry["url"] = b["url"]
    else:
        entry["command"] = b["command"]
        if b.get("args"):
            entry["args"] = b["args"] if isinstance(b["args"], list) else b["args"].split()
    if b.get("env"):
        entry["env"] = b["env"]
    data.setdefault("mcpServers", {})[name] = entry
    os.makedirs(os.path.dirname(MCP_FILE), exist_ok=True)
    open(MCP_FILE, "w").write(json.dumps(data, indent=1))
    audit("dashboard", "mcp.add", name)
    return {"ok": True}


@app.delete("/api/mcp/{name}")
def api_mcp_delete(name: str):
    data = load_mcp()
    if name not in data.get("mcpServers", {}):
        return err("notfound", "MCP-Server nicht gefunden", 404)
    del data["mcpServers"][name]
    open(MCP_FILE, "w").write(json.dumps(data, indent=1))
    audit("dashboard", "mcp.delete", name)
    return {"ok": True}


# ---------------------------------------------------------------- Backup (B2) --
BACKUP_DIR = os.path.expanduser("~/OperatorBackups")


@app.post("/api/backup")
def api_backup_create():
    import tarfile
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = f"operator-backup-{time.strftime('%Y%m%d_%H%M%S')}.tar.gz"
    path = os.path.join(BACKUP_DIR, name)
    skip = ("dashboard/venv", "listener.log", "dashboard.log", ".tmp", "__pycache__")
    with tarfile.open(path, "w:gz") as tar:
        for root, dirs, files in os.walk(BOT_DIR):
            rel_root = os.path.relpath(root, BOT_DIR)
            if any(s in rel_root for s in skip):
                dirs[:] = []
                continue
            for fn in files:
                rel = os.path.normpath(os.path.join(rel_root, fn))
                if any(s in rel for s in skip):
                    continue
                tar.add(os.path.join(root, fn), arcname=rel)
    size = os.path.getsize(path)
    audit("dashboard", "backup.create", f"{name} ({size} B)")
    return {"ok": True, "name": name, "size": size}


@app.get("/api/backups")
def api_backup_list():
    if not os.path.isdir(BACKUP_DIR):
        return {"backups": []}
    out = []
    for fn in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if fn.startswith("operator-backup-") and fn.endswith(".tar.gz"):
            p = os.path.join(BACKUP_DIR, fn)
            out.append({"name": fn, "size": os.path.getsize(p),
                        "ts": time.strftime("%Y-%m-%d %H:%M",
                                            time.localtime(os.path.getmtime(p)))})
    return {"backups": out[:20]}


@app.post("/api/backup/restore")
async def api_backup_restore(request: Request):
    """Sicherer Restore: entpackt in Prüf-Verzeichnis, überschreibt NICHTS automatisch."""
    import tarfile
    name = (await request.json()).get("name", "")
    path = os.path.join(BACKUP_DIR, os.path.basename(name))
    if not (name.startswith("operator-backup-") and os.path.exists(path)):
        return err("notfound", "Backup nicht gefunden", 404)
    dest = os.path.join(BACKUP_DIR, "restore-" + time.strftime("%Y%m%d_%H%M%S"))
    with tarfile.open(path) as tar:
        tar.extractall(dest, filter="data")
    audit("dashboard", "backup.restore", f"{name} -> {dest}")
    return {"ok": True, "dest": dest,
            "info": "Entpackt zur Prüfung — Dateien bei Bedarf manuell zurückkopieren"}


@app.get("/api/agents/{name}/export")
def api_agent_export(name: str):
    a = agents_store.get_agent(name)
    if not a:
        return err("notfound", "Agent nicht gefunden", 404)
    path = os.path.join(agents_store.AGENTS_DIR, name + ".md")
    return FileResponse(path, filename=name + ".md",
                        media_type="text/markdown")


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
