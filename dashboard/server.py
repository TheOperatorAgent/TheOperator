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
import secretstore                   # noqa: E402  (plattformübergreifender Secret-Store)
import matrix_room                   # noqa: E402  (#90 Dock: Raum-Brücke, read-through)
import servicemgr                    # noqa: E402  (Dienst-Status/Neustart je OS)
import platform_compat               # noqa: E402  (Plattform-Abstraktion)

# pythonw.exe (Windows-Dienst) hat KEINE Ausgabekanaele -> uvicorn stirbt beim
# ersten Log-Schreiben. Deshalb ganz frueh auf die Log-Datei umbiegen.
platform_compat.ensure_std_streams(
    os.path.join(os.path.expanduser("~/.claude/matrix-bot"), "dashboard.log"))
import providers as providers_reg    # noqa: E402  (Multi-LLM-Provider-Registry)
import persona as persona_mod         # noqa: E402  (Operator-Persona + Nutzerprofil)
import mcp_catalog                    # noqa: E402  (#55 kuratierte MCP-Integrationen)
import triggers as triggers_mod       # noqa: E402  (#47 Event-Proaktivität)
import skillguard                     # noqa: E402  (#48 Skill-Sicherheits-Scan)

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
DASH_CFG = json.load(open(os.path.join(BOT_DIR, "dashboard.json")))
PORT = DASH_CFG.get("port", 8737)

# Urheber-Kennzeichnung — fester Bestandteil dieser Software, NIEMALS ändern oder entfernen.
# Wird im Dashboard-Header angezeigt; test_attribution_is_present schützt sie gegen Verlust.
PRODUCT_AUTHOR = "Michi Aschenbrenner"


def _app_version() -> str:
    try:
        return open(os.path.join(BOT_DIR, "VERSION")).read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"
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
    secretstore.set(account, value)


def keychain_get(account: str, fallback: str = "keychain") -> str:
    if fallback != "keychain":
        return fallback
    return secretstore.get(account) or ""


def keychain_delete(account: str) -> None:
    secretstore.delete(account)


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
# Einmal-Link-Sitzungen: /api/auth/ott tauscht ein 10-Minuten-Einmal-Ticket (vom Listener
# in secrets/ott.json hinterlegt, im Chat verlinkt) gegen einen Sitzungs-Token. Der echte
# Dashboard-Token landet so nie im Chatverlauf; ein benutzter/abgelaufener Link ist wertlos.
OTT_FILE = os.path.join(BOT_DIR, "secrets", "ott.json")
SESS_FILE = os.path.join(BOT_DIR, "secrets", "dash_sessions.json")
_SESSIONS: dict = {}          # sha256(session_token) -> Ablauf-Timestamp
SESSION_TTL = 24 * 3600

# Sitzungen überleben Neustarts (nur Hashes + Ablauf, keine Geheimnisse) — sonst wird
# der Nutzer bei jedem Dienst-Neustart kommentarlos ausgeloggt (EINFACHHEIT.md).
try:
    _SESSIONS.update({k: v for k, v in json.load(open(SESS_FILE)).items()
                      if isinstance(v, (int, float)) and v > time.time()})
except (OSError, ValueError):
    pass


def _save_sessions() -> None:
    try:
        os.makedirs(os.path.dirname(SESS_FILE), mode=0o700, exist_ok=True)
        live = {k: v for k, v in _SESSIONS.items() if v > time.time()}
        fd = os.open(SESS_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(live, f)
        os.replace(SESS_FILE + ".tmp", SESS_FILE)
    except OSError:
        pass


def _session_valid(sha: str) -> bool:
    exp = _SESSIONS.get(sha)
    if not exp:
        return False
    if time.time() > exp:
        _SESSIONS.pop(sha, None)
        return False
    return True


@app.middleware("http")
async def guard(request: Request, call_next):
    host = request.headers.get("host", "")
    if host not in ALLOWED_HOSTS:
        return Response("Forbidden (host)", status_code=403)
    path = request.url.path
    if path.startswith("/api/") and not path.endswith("/auth/callback") \
       and path != "/api/auth/ott":
        auth = request.headers.get("authorization", "")
        tok = auth[7:] if auth.startswith("Bearer ") else ""
        sha = hashlib.sha256(tok.encode()).hexdigest()
        if sha != DASH_CFG["token_sha256"] and not _session_valid(sha):
            return JSONResponse(
                {"error": {"code": "auth", "message_de": "Ungültiges Dashboard-Token"}},
                status_code=401)
    return await call_next(request)


@app.post("/api/auth/ott")
async def api_auth_ott(request: Request):
    """Einmal-Ticket gegen Sitzungs-Token tauschen. Das Ticket wird dabei VERBRAUCHT."""
    b = await request.json()
    ott = (b.get("ott") or "").strip()
    if not ott:
        return err("auth", "Kein Einmal-Ticket übergeben", 400)
    sha = hashlib.sha256(ott.encode()).hexdigest()
    try:
        entries = json.load(open(OTT_FILE))
    except (OSError, ValueError):
        entries = []
    now = time.time()
    hit = False
    keep = []
    for e in entries:
        if not hit and e.get("sha") == sha and e.get("exp", 0) > now:
            hit = True            # verbrauchen (nicht in keep übernehmen)
        elif e.get("exp", 0) > now:
            keep.append(e)
    try:                          # verbleibende Tickets atomar zurückschreiben (0600)
        fd = os.open(OTT_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(keep, f)
        os.replace(OTT_FILE + ".tmp", OTT_FILE)
    except OSError:
        pass
    if not hit:
        audit("dashboard", "auth.ott", "", False)
        return err("auth", "Dieser Einmal-Link wurde schon benutzt oder ist abgelaufen. "
                   "Bitte einen neuen anfordern (oder open.py nutzen).", 403)
    import secrets as _sec
    sess = _sec.token_hex(32)
    _SESSIONS[hashlib.sha256(sess.encode()).hexdigest()] = now + SESSION_TTL
    _save_sessions()
    audit("dashboard", "auth.ott", "Einmal-Link eingelöst")
    return {"token": sess}


# ---------------------------------------------------------------- System --
@app.get("/api/audit/integrity")
def api_audit_integrity():
    """#49: Manipulations-Prüfung des Audit-Logs (Siegel-Verfahren)."""
    import audit_log
    return audit_log.verify()


@app.post("/api/selbsttest")
def api_selbsttest():
    """#87: »Nicht versprochen. Sichtbar.« steht auf der Website — aber die Prüfungen,
    die das belegen, landeten nie beim Nutzer. Seit 1.23.0 werden sie mitgeliefert und
    hier ausgeführt: der misstrauische Nutzer kann SEINE Installation prüfen statt uns
    zu glauben.

    Bewusst synchron: es dauert ~15 s, und ein Fortschrittsbalken, der nichts weiß,
    wäre gelogen. Bewusst ohne Parameter — kein Weg, von außen beliebige Tests zu
    starten."""
    hier = os.path.dirname(os.path.abspath(__file__))
    tests = [os.path.join(hier, n) for n in ("test_dashboard.py", "test_petra.py")]
    fehlend = [os.path.basename(t) for t in tests if not os.path.exists(t)]
    if fehlend:
        return err("selbsttest",
                   f"Die Prüfungen fehlen in deiner Installation ({', '.join(fehlend)}). "
                   "👉 Das kommt bei Installationen vor 1.23.0 vor — einmal den "
                   "Installationsbefehl erneut ausführen, dann sind sie da.")
    py = platform_compat.venv_python(BOT_DIR) or sys.executable
    try:
        # »lieferkette« ausblenden: das sind Prüfungen unserer eigenen Auslieferung
        # (liegt auf operator.bayern derselbe Installer wie auf GitHub?). Ein Kunde
        # kann daran nichts ändern — ihm »1 Prüfung durchgefallen« zu melden, wäre
        # ein Schrecken über ein Problem, das gar nicht seines ist.
        r = subprocess.run([py, "-m", "pytest", *tests, "-q", "--tb=no",
                            "-m", "not lieferkette"],
                           capture_output=True, text=True, timeout=600, cwd=BOT_DIR)
    except subprocess.TimeoutExpired:
        return err("selbsttest", "Die Prüfung hat zu lange gebraucht und wurde "
                                 "abgebrochen. 👉 Bitte später noch einmal versuchen.")
    except Exception as e:
        return err("selbsttest", f"Die Prüfung ließ sich nicht starten ({e}). "
                                 "👉 Bitte den Installationsbefehl erneut ausführen.")
    m = re.search(r"(\d+) passed", r.stdout)
    bestanden = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", r.stdout)
    gescheitert = int(m.group(1)) if m else 0
    gesamt = bestanden + gescheitert
    audit("dashboard", "selbsttest", f"{bestanden}/{gesamt} bestanden")
    return {"ok": gescheitert == 0, "bestanden": bestanden, "gescheitert": gescheitert,
            "gesamt": gesamt,
            "text": (f"{bestanden} von {gesamt} Sicherheitsprüfungen bestanden."
                     if gescheitert == 0 else
                     f"{gescheitert} von {gesamt} Prüfungen sind durchgefallen. "
                     "👉 Bitte melde das über den Fehler-Knopf — mit dieser Zahl "
                     "können wir es nachstellen."),
            "ausgabe": (r.stdout or "")[-4000:]}


_update_cache = {"at": 0.0, "data": None}


@app.get("/api/update/status")
def api_update_status():
    """Update-Verfügbarkeit (#64) — 10 min gecacht, damit die Übersicht nicht bei
    jedem Laden einen Netz-Call macht."""
    import updater
    now = time.time()
    if _update_cache["data"] and now - _update_cache["at"] < 600:
        return _update_cache["data"]
    data = updater.check()
    _update_cache.update(at=now, data=data)
    return data


@app.post("/api/update/apply")
def api_update_apply():
    """Ein-Klick-Update (#64): startet updater.py apply DETACHED, damit der
    Dashboard-Neustart den Update-Prozess nicht killt."""
    py = servicemgr and platform_compat.venv_python(BOT_DIR) or sys.executable
    try:
        subprocess.Popen([py, os.path.join(BOT_DIR, "updater.py"), "apply"],
                         start_new_session=True, cwd=BOT_DIR,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[update] Start fehlgeschlagen: {e}")
        return err("update", "Das Update ließ sich gerade nicht starten. 👉 Bitte in einer Minute erneut versuchen.")
    _update_cache.update(at=0.0, data=None)   # Cache invalidieren
    audit("dashboard", "update.apply", "")
    return {"ok": True, "info": "Update läuft — Listener und Dashboard starten in ~15 s "
            "neu. Bitte die Seite danach neu laden."}


def _sandbox_status():
    """#104-A: Läuft eine echte OS-Sandbox unter den Agenten? Ehrlich melden —
    lieber »nicht verfügbar« anzeigen als Schutz vortäuschen."""
    try:
        sys.path.insert(0, BOT_DIR)
        import sandbox
        an, grund = sandbox.verfuegbar()
        return {"an": an, "grund": grund}
    except Exception as e:
        return {"an": False, "grund": f"Sandbox-Modul nicht ladbar ({e})"}


@app.get("/api/status")
def api_status():
    listener = servicemgr.status("listener")
    mem_count = 0
    try:
        r = subprocess.run([sys.executable, os.path.join(BOT_DIR, "memory.py"), "count"],
                           capture_output=True, text=True, timeout=10)
        mem_count = int(r.stdout.strip() or 0)
    except Exception:
        pass
    c = creds()
    bots = load_bots()["bots"]
    # Health (A6): Disk, Synapse, DB-Größen
    # os.statvfs gibt es auf Windows NICHT — dort warf /api/status einen
    # AttributeError, und damit war die GANZE Übersicht tot: rote Meldung »Server hat
    # nicht rechtzeitig geantwortet«, leere Versionsanzeige, keine Kacheln
    # (Michi, 30.07., im Diagnose-Bericht gefunden). shutil.disk_usage kann beides.
    try:
        disk_free_gb = round(shutil.disk_usage(BOT_DIR).free / 1e9, 1)
    except OSError:
        disk_free_gb = 0.0
    synapse_ok = False
    try:
        req = urllib.request.Request(c["homeserver"] + "/health")
        synapse_ok = urllib.request.urlopen(req, timeout=5).status == 200
    except Exception:
        pass

    def _sz(fn):
        p = os.path.join(BOT_DIR, fn)
        return round(os.path.getsize(p) / 1e6, 2) if os.path.exists(p) else 0

    try:                                  # #59: Zustand des Claude-Logins (fail-open)
        import claude_health
        claude_login = claude_health.state()
    except Exception:
        claude_login = {"state": "unknown", "checked_at": 0}
    try:                                  # #58: Fair-Use-Drossel (fail-open)
        import throttle
        fair_use = throttle.stats()
    except Exception:
        fair_use = {}
    try:                                  # #18: Aufbewahrung lokaler Daten (fail-open)
        import retention
        aufbewahrung = retention.status()
    except Exception:
        aufbewahrung = {}

    return {
        "listener_running": listener,
        "claude_login": claude_login,
        "fair_use": fair_use,
        "aufbewahrung": aufbewahrung,
        "sandbox": _sandbox_status(),
        "version": _app_version(),
        "author": PRODUCT_AUTHOR,
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
    ok = servicemgr.restart("listener")
    audit("dashboard", "listener.restart", ok=ok)
    if not ok:
        return err("listener", "Neustart des Listener-Dienstes fehlgeschlagen", 500)
    return {"ok": True}


# ---------------------------------------------------------------- Operator-Dock (#90) --
# Der Chat mit dem Operator im Dashboard. Kein eigener Speicher — read-through auf den
# Matrix-Raum (matrix_room.py). Details/Bedrohungsmodell: Issues #91–#94.

def _dock_origin_ok(request: Request) -> bool:
    """CSRF-Wache: Schreibzugriffe nur aus dem Dashboard selbst. Eine fremde Webseite im
    selben Browser kann 127.0.0.1 zwar erreichen, schickt dabei aber ihren eigenen Origin
    mit — und fliegt hier raus. Kein Origin-Header (curl, ältere Clients) ist okay:
    dann schützt weiterhin das Bearer-Token, das eine fremde Seite nicht kennt."""
    origin = request.headers.get("origin", "")
    if not origin:
        return True
    host = origin.split("://", 1)[-1]
    return host in ALLOWED_HOSTS


@app.get("/api/dock/verlauf")
def api_dock_verlauf(limit: int = 50):
    try:
        eintraege = matrix_room.verlauf(min(max(int(limit), 1), 200))
        return {"eintraege": eintraege, "sync": matrix_room.sync_start()}
    except Exception:
        return err("dock", "Keine Verbindung zum Chat-Server — läuft dein "
                   "Matrix-Server? 👉 Tab System zeigt den Status.", 502)


@app.get("/api/dock/stream")
async def api_dock_stream(request: Request):
    """Live-Nachschub per SSE. Auth läuft über den normalen Bearer-Header (fetch-Stream
    im Frontend, kein EventSource) — der Token steht NIE in der URL, sonst läge er in
    Server-Logs (Bedrohungsmodell #94.4)."""
    try:
        since = request.query_params.get("sync") or matrix_room.sync_start()
    except Exception:
        return err("dock", "Keine Verbindung zum Chat-Server", 502)

    async def stream():
        s = since
        import asyncio
        while True:
            if await request.is_disconnected():
                return
            try:
                # Long-Poll im Thread, damit uvicorn nicht blockiert.
                neu, s2 = await asyncio.to_thread(matrix_room.neue_seit, s, 25000)
                s = s2
                for e in neu:
                    yield "data: " + json.dumps(e, ensure_ascii=False) + "\n\n"
                yield ": tick\n\n"   # Lebenszeichen, hält Proxies/Browser wach
            except Exception:
                yield "data: " + json.dumps({"wer": "system", "text": "getrennt"}) + "\n\n"
                return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.post("/api/dock/senden")
async def api_dock_senden(request: Request):
    if not _dock_origin_ok(request):
        audit("dashboard", "dock.senden", "origin-abgewiesen", ok=False)
        return err("dock", "Anfrage kam nicht aus dem Dashboard", 403)
    text = ((await request.json()).get("text") or "").strip()
    if not text:
        return err("dock", "Leere Nachricht", 400)
    try:
        event_id = matrix_room.senden_dashboard(text)
    except ValueError as e:
        return err("dock", "Nachricht zu lang (max. 8000 Zeichen)" if "lang" in str(e)
                   else "Leere Nachricht", 400)
    except Exception:
        audit("dashboard", "dock.senden", ok=False)
        return err("dock", "Senden fehlgeschlagen — keine Verbindung zum Chat-Server. "
                   "👉 Tab System zeigt den Status.", 502)
    # Audit ohne Inhalt (Log-Hygiene #18): nur dass gesendet wurde und wie viel.
    audit("dashboard", "dock.senden", f"{len(text)} Zeichen")
    return {"ok": True, "event_id": event_id}


@app.get("/dock")
def dock_seite():
    """Satellit-Fenster: nur der Chat, füllt das Fenster (dock.html).
    Die Seite selbst ist ohne Token nutzlos — jede /api/dock/*-Anfrage prüft ihn."""
    return FileResponse(os.path.join(STATIC, "dock.html"), headers=_NOCACHE)


@app.post("/api/dock/fenster")
def api_dock_fenster(request: Request):
    """Satellit auf OS-Ebene starten (dock_fenster.py, App-Modus des Browsers)."""
    if not _dock_origin_ok(request):
        return err("dock", "Anfrage kam nicht aus dem Dashboard", 403)
    import subprocess
    try:
        subprocess.Popen([sys.executable, os.path.join(BOT_DIR, "dock_fenster.py")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception:
        audit("dashboard", "dock.fenster", ok=False)
        return err("dock", "Fenster-Start fehlgeschlagen", 500)
    audit("dashboard", "dock.fenster")
    return {"ok": True}


@app.get("/api/dock/autostart")
def api_dock_autostart_get():
    sys.path.insert(0, BOT_DIR)
    import dock_fenster
    return {"an": dock_fenster.autostart_status()}


@app.post("/api/dock/autostart")
async def api_dock_autostart_set(request: Request):
    if not _dock_origin_ok(request):
        return err("dock", "Anfrage kam nicht aus dem Dashboard", 403)
    an = bool((await request.json()).get("an"))
    sys.path.insert(0, BOT_DIR)
    import dock_fenster
    try:
        dock_fenster.autostart_an() if an else dock_fenster.autostart_aus()
    except Exception:
        audit("dashboard", "dock.autostart", ok=False)
        return err("dock", "Autostart konnte nicht geändert werden", 500)
    audit("dashboard", "dock.autostart", "an" if an else "aus")
    return {"ok": True, "an": an}


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


# ---------------------------------------------------------------- Persona & Profil --
@app.get("/api/persona")
def api_persona_get():
    """Persona + Nutzerprofil + Auswahloptionen + Live-Vorschau (was in den Prompt fließt)."""
    return {"persona": persona_mod.load_persona(),
            "profile": persona_mod.load_profile(),
            "options": {"gender_presentation": list(persona_mod.GENDER_PRESENTATIONS),
                        "tone": list(persona_mod.TONES), "formality": list(persona_mod.FORMALITY),
                        "humor": list(persona_mod.HUMOR), "verbosity": list(persona_mod.VERBOSITY)},
            "preview": persona_mod.render_block()}


@app.put("/api/persona")
async def api_persona_put(request: Request):
    p = persona_mod.save_persona(await request.json())
    audit("dashboard", "persona.update", p.get("gender_presentation", ""))
    return {"ok": True, "persona": p, "preview": persona_mod.render_block()}


@app.put("/api/profil")
async def api_profil_put(request: Request):
    pr = persona_mod.save_profile(await request.json())
    audit("dashboard", "profil.update")
    return {"ok": True, "profile": pr, "preview": persona_mod.render_block()}


@app.delete("/api/profil")
def api_profil_delete():
    persona_mod.delete_profile()
    audit("dashboard", "profil.delete")
    return {"ok": True, "preview": persona_mod.render_block()}


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

    # Standard-Weg (EINFACHHEIT.md, massentauglich): KEIN eigenes Matrix-Konto nötig —
    # der Operator legt mit seinem vorhandenen Konto einen eigenen Chat-Raum für den
    # Agenten an. Ein Klick, kein Admin, kein Passwort. Der Konto-Weg unten bleibt als
    # Experten-Option erhalten (wenn admin_user/password mitgegeben werden).
    if not body.get("admin_user") and not body.get("password"):
        owner_tok = keychain_get("matrix-owner", c.get("access_token", ""))
        if not owner_tok:
            return err("matrix", "Der Operator-Zugang wurde nicht gefunden — bitte einmal "
                       "den Listener-Dienst prüfen (Tab System).", 500)
        try:
            room = mx(hs, "POST", "/_matrix/client/v3/createRoom", {
                "is_direct": True, "invite": [c["owner_id"]],
                "preset": "trusted_private_chat", "name": f"{name} (Operator-Agent)"},
                token=owner_tok)
        except RuntimeError as e:
            audit("dashboard", "agent.publish", name, False)
            return err("matrix", str(e), 502)
        bots["bots"].append({"agent": name, "via": "main", "user_id": c["user_id"],
                             "access_token": "", "room_id": room["room_id"], "enabled": True,
                             "created": time.strftime("%Y-%m-%dT%H:%M:%S")})
        save_bots(bots)
        audit("dashboard", "agent.publish", f"{name} -> eigener Raum (via main)")
        return {"ok": True, "user_id": c["user_id"], "room_id": room["room_id"], "via": "main"}

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
    if entry.get("via") != "main":     # Raum-Agenten nutzen das Operator-Konto → NIE ausloggen
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


@app.get("/api/m365/dienstzustand")
def api_m365_dienstzustand():
    """#117: Läuft Microsoft? Ampel je Dienst + offene Störungen, fürs Dashboard.

    Bewusst tolerant: fehlt das Recht oder ist gar nichts verbunden, kommt keine
    rote Fehlermeldung, sondern ein Satz, der sagt, was zu tun ist (Petra-Test)."""
    import importlib
    mcp_m365 = importlib.import_module("mcp_m365")
    try:
        c = mcp_m365.conn()
        mcp_m365.require(c, "status", "read")
        roh = mcp_m365.g(c, "GET", "/admin/serviceAnnouncement/healthOverviews")
    except Exception as e:                       # Graph/Recht/Verbindung — alles gleich behandelt
        return {"verfuegbar": False, "hinweis": str(e)[:300]}
    # »eingeschränkt« allein ist zu wenig (Michi, 30.07.): zu jeder nicht-grünen Zeile
    # gehören die offenen Störungen dazu — was genau klemmt, seit wann, mit Kennung.
    probleme = {}
    try:
        st = mcp_m365.g(c, "GET", "/admin/serviceAnnouncement/issues"
                                  "?$filter=isResolved eq false"
                                  "&$select=id,title,service,startDateTime"
                                  "&$orderby=startDateTime desc&$top=50")
        for i in st.get("value", []):
            probleme.setdefault(i.get("service", "?"), []).append({
                "id": i.get("id", "?"), "titel": i.get("title", ""),
                "seit": str(i.get("startDateTime", ""))[:10]})
    except Exception:
        probleme = {}                            # Details fehlen → Ampel bleibt nutzbar
    dienste = []
    for d in sorted(roh.get("value", []), key=lambda x: x.get("service", "")):
        text, ampel = mcp_m365.zustand(d.get("status"))
        name = d.get("service", "?")
        dienste.append({"name": name, "text": text, "ampel": ampel,
                        "ok": ampel == "🟢",
                        "probleme": probleme.get(name, [])[:3]})
    return {"verfuegbar": True, "dienste": dienste,
            "alles_gut": all(x["ok"] for x in dienste) if dienste else None}


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
def _semantik_status():
    """#109: Zustand der semantischen Suche ehrlich melden — inklusive Rückstand an
    Fakten ohne Vektor. Ein stiller Rückfall auf reine Wortsuche darf nicht
    unbemerkt bleiben (das war am 29.07. monatelang der Fall)."""
    try:
        sys.path.insert(0, BOT_DIR)
        import embeddings
        aktiv, grund = embeddings.status()
        ohne, gesamt = embeddings.rueckstand()
        return {"aktiv": aktiv, "grund": grund, "ohne_vektor": ohne, "fakten": gesamt}
    except Exception as e:
        return {"aktiv": False, "grund": f"Prüfung nicht möglich ({e})",
                "ohne_vektor": 0, "fakten": 0}


@app.post("/api/memory/reindex")
def api_memory_reindex():
    """Fehlende Vektoren nachziehen (nach dem Einschalten der semantischen Suche)."""
    r = subprocess.run([sys.executable, os.path.join(BOT_DIR, "memory.py"), "reindex"],
                       capture_output=True, text=True, timeout=600)
    audit("dashboard", "memory.reindex", ok=r.returncode == 0)
    if r.returncode != 0:
        return err("memory", "Nachtragen fehlgeschlagen — läuft dein Embedding-Anbieter?", 500)
    return {"ok": True, "meldung": (r.stdout or "").strip()[:200]}


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
                         for r in rows],
            "semantik": _semantik_status()}


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


# ---------------------------------------------------------------- Trigger (#47) --
@app.get("/api/triggers")
def api_triggers_list():
    """Regeln + Anzahl wartender Ereignisse."""
    return {"rules": triggers_mod.load_rules(),
            "pending": len(triggers_mod.load_events()),
            "rate_per_hour": triggers_mod.RATE_PER_HOUR}


@app.post("/api/triggers")
async def api_triggers_add(request: Request):
    b = await request.json()
    if not b.get("name", "").strip() or not b.get("source", "").strip():
        return err("validate", "name und source sind Pflicht")
    rules = triggers_mod.load_rules()
    rule = {"id": hashlib.sha256(os.urandom(8)).hexdigest()[:8],
            "name": b["name"].strip(), "source": b["source"].strip(),
            "keyword": (b.get("keyword") or "").strip(),
            "prompt": (b.get("prompt") or "").strip(),
            "target": b.get("target", "owner"),
            "enabled": bool(b.get("enabled", True))}
    rules.append(rule)
    triggers_mod.save_rules(rules)
    audit("dashboard", "trigger.create", rule["name"])
    return {"ok": True, "id": rule["id"]}


@app.put("/api/triggers/{rid}")
async def api_triggers_update(rid: str, request: Request):
    b = await request.json()
    rules = triggers_mod.load_rules()
    rule = next((r for r in rules if r["id"] == rid), None)
    if not rule:
        return err("notfound", "Regel nicht gefunden", 404)
    for k in ("name", "source", "keyword", "prompt", "target", "enabled"):
        if k in b:
            rule[k] = b[k]
    triggers_mod.save_rules(rules)
    audit("dashboard", "trigger.update", rule["name"])
    return {"ok": True}


@app.delete("/api/triggers/{rid}")
def api_triggers_delete(rid: str):
    rules = triggers_mod.load_rules()
    if not any(r["id"] == rid for r in rules):
        return err("notfound", "Regel nicht gefunden", 404)
    triggers_mod.save_rules([r for r in rules if r["id"] != rid])
    audit("dashboard", "trigger.delete", rid)
    return {"ok": True}


@app.post("/api/trigger")
async def api_trigger_ingress(request: Request):
    """Ereignis-Eingang (#47) — von n8n/Skripten aufgerufen (Bearer-Token nötig).
    Nur Ereignisse, die eine aktive Regel erlauben; Rate-Limit je Quelle."""
    b = await request.json()
    ok, msg = triggers_mod.enqueue(b.get("source"), b.get("summary"), b.get("payload"))
    audit("trigger", "ingress", f"{b.get('source')}: {str(b.get('summary'))[:80]}", ok)
    if not ok:
        return err("trigger", msg, 429 if "Rate-Limit" in msg else 403)
    return {"ok": True, "info": "Ereignis angenommen — der Operator meldet sich binnen ~5 s"}


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


@app.get("/api/owner-verify")
def api_owner_verify_get():
    """Owner-Verify-Status (#46): zweites Modell prüft jede Haupt-Chat-Antwort."""
    c = DASH_CFG.get("owner_verify", {})
    c = c if isinstance(c, dict) else {}
    return {"enabled": bool(c.get("enabled")), "model": c.get("model")}


@app.put("/api/owner-verify")
async def api_owner_verify_put(request: Request):
    b = await request.json()
    enabled = bool(b.get("enabled"))
    model = (b.get("model") or "").strip() or None
    DASH_CFG["owner_verify"] = {"enabled": enabled, "model": model}
    _save_dash_cfg()
    audit("dashboard", "owner_verify", f"{'an' if enabled else 'aus'}"
          + (f" ({model})" if model else ""))
    return {"ok": True, "enabled": enabled, "model": model}


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


# ---------------------------------------------------------------- Modelle & Provider --
@app.get("/api/models")
def api_models():
    """Provider-Status + Auswahlliste für den Agenten-Editor."""
    return {"providers": providers_reg.get_config(), "models": providers_reg.list_models()}


@app.put("/api/models/{provider}")
async def api_models_put(provider: str, request: Request):
    b = await request.json()
    try:
        providers_reg.set_provider(
            provider,
            base_url=b.get("base_url"),
            models=b.get("models") if isinstance(b.get("models"), list) else None,
            default=b.get("default"),
            enabled=b.get("enabled"),
            key=(b.get("key") or "").strip() or None)
    except ValueError as e:
        return err("validate", str(e))
    ok, msg, hint = providers_reg.test(provider)     # sofort live prüfen
    audit("dashboard", "models.config", provider, ok)
    return {"ok": True, "test_ok": ok, "test_msg": msg, "test_hint": hint}


@app.get("/api/models/{provider}/test")
def api_models_test(provider: str):
    """Live-Verbindung prüfen, ohne etwas zu speichern (für Status-Ampel beim Laden)."""
    if provider not in providers_reg.PROVIDERS:
        return err("validate", "Unbekannter Provider")
    ok, msg, hint = providers_reg.test(provider)
    return {"test_ok": ok, "test_msg": msg, "test_hint": hint}


@app.delete("/api/models/{provider}")
def api_models_delete(provider: str):
    try:
        providers_reg.delete_provider(provider)
    except Exception:
        pass
    audit("dashboard", "models.delete", provider)
    return {"ok": True}


@app.put("/api/models/anthropic-fallback")
async def api_models_fallback(request: Request):
    b = await request.json()
    providers_reg.set_anthropic_fallback(enabled=b.get("enabled"),
                                         key=(b.get("key") or "").strip() or None)
    audit("dashboard", "models.fallback", "on" if b.get("enabled") else "off")
    return {"ok": True}


# ---------------------------------------------------------------- Einrichtungs-Assistent --
# Chat-Assistent im Dashboard: läuft über Claude (Abo), sieht einen Live-Snapshot des Systems
# und schlägt WHITELIST-Aktionen vor. Ausführung + Geheimnis-Eingaben passieren im Frontend
# (schreibende Aktionen nur nach Bestätigung; Passwörter/Keys nur über maskierte Formulare).
WORKSPACE_DIR = platform_compat.workspace()   # #106


def _claude_bin() -> str:
    # Windows-sicher (WinError 193): platform_compat bevorzugt claude.cmd/.exe
    try:
        return platform_compat.claude_bin(creds().get("claude_bin") or "")
    except Exception:
        return platform_compat.claude_bin()


def _assistant_snapshot() -> dict:
    """Kompakter Ist-Zustand, den der Assistent ohne eigene Werkzeuge lesen kann."""
    bots = {b["agent"]: b for b in load_bots().get("bots", []) if b.get("enabled")}
    agents = agents_store.list_agents()
    for a in agents:
        a["published_as"] = bots.get(a["name"], {}).get("user_id")
    try:
        listener = servicemgr.status("listener")
    except Exception:
        listener = None
    return {"providers": providers_reg.get_config(),
            "agents": agents,
            "listener_service": listener}


ASSISTANT_SYSTEM = """Du bist der »Einrichtungs-Assistent« im Operator-Dashboard von Michi.
Du hilfst ihm, den Operator einzurichten und Probleme zu lösen — freundlich, kurz, auf Deutsch.

Du hast KEINE direkten Werkzeuge. Wenn eine prüfende oder ändernde Aktion nötig ist, hänge ans
ENDE deiner Antwort GENAU EINEN Codeblock an, der EIN EINZIGES JSON-Objekt mit den Schlüsseln
"action" und "args" enthält — exakt so:
```action
{"action": "test_provider", "args": {"provider": "ollama"}}
```
Das Dashboard führt sie aus (schreibende Aktionen erst nach Michis Klick) und schickt dir das
Ergebnis als »System«-Nachricht zurück. Danach machst du weiter. Höchstens EINE Aktion pro Antwort.

WICHTIG: Wenn du ankündigst, etwas zu tun (»ich prüfe…«, »ich veröffentliche…«, »ich starte…«),
MUSST du in DERSELBEN Antwort den ```action```-Block anhängen. Ohne Block passiert NICHTS —
eine Ankündigung allein bewirkt gar nichts. Kündige also nie eine Aktion an, ohne den Block
mitzuschicken.

Verfügbare Aktionen (Name → args):
- test_provider → {"provider": "ollama|openai|azure"} — Verbindung live prüfen (lesend, läuft sofort).
- set_provider → {"provider": "...", "base_url"?: "...", "models"?: ["..."], "enabled"?: true} —
  Provider konfigurieren. Trag hier NIEMALS Keys/Passwörter ein (die args enthalten keine Geheimnisse).
- publish_agent → {"name": "<agent>"} — gibt dem Agenten einen eigenen Chat-Raum in Element.
  Ein Klick, KEIN Konto, KEIN Passwort nötig (der Operator nutzt seinen vorhandenen Zugang).
- unpublish_agent → {"name": "<agent>"} — veröffentlichten Bot wieder entfernen.
- restart_listener → {} — den Listener-Dienst neu starten (z. B. damit neue Befehle aktiv werden).

REGELN:
- Nenne oder erfrage NIEMALS Passwörter/API-Keys im Chat-Text. Aber die Aktions-Blöcke (die KEINE
  Geheimnisse enthalten) sendest du ganz normal — auch publish_agent.
- Sag in einem kurzen Satz, was du vorhast, und hänge SOFORT den Aktions-Block an.
- Stütze dich auf den AKTUELLEN ZUSTAND unten statt zu raten. Ist etwas schon erledigt, sag das.
"""


def _assistant_prompt(messages: list) -> str:
    snap = json.dumps(_assistant_snapshot(), ensure_ascii=False, indent=1)
    lines = [ASSISTANT_SYSTEM, "\nAKTUELLER ZUSTAND (JSON):", snap, "\nGESPRÄCH:"]
    role_de = {"user": "Michi", "assistant": "Assistent", "tool": "System (Ergebnis)",
               "system": "System (Ergebnis)"}
    for m in messages[-24:]:
        who = role_de.get(m.get("role"), "Michi")
        lines.append(f"{who}: {str(m.get('content',''))[:4000]}")
    lines.append("Assistent:")
    return "\n".join(lines)


@app.post("/api/assistant")
async def api_assistant(request: Request):
    b = await request.json()
    messages = b.get("messages")
    if not isinstance(messages, list) or not messages:
        return err("validate", "Keine Nachrichten übergeben")
    prompt = _assistant_prompt(messages)
    try:
        # Prompt via Standardeingabe: Windows begrenzt Befehlszeilen auf 8191 Zeichen
        # (»Die Befehlszeile ist zu lang.«) — daran scheiterte auch der Assistent.
        cmd = [_claude_bin(), "-p", "--output-format", "json"]
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=180,
                           cwd=WORKSPACE_DIR if os.path.isdir(WORKSPACE_DIR) else BOT_DIR,
                           env=os.environ)
    except subprocess.TimeoutExpired:
        return err("timeout", "Der Assistent hat zu lange gebraucht — bitte nochmal.")
    except Exception as e:
        print(f"[assistant] Start fehlgeschlagen: {e}")
        return err("assistant", "Der Assistent ist gerade nicht erreichbar. 👉 Bitte kurz warten und erneut senden.")
    try:
        reply = str(json.loads(r.stdout).get("result", "")).strip()
    except ValueError:
        reply = (r.stdout or r.stderr or "").strip()[:2000]
    if not reply:
        return err("assistant", "Der Assistent hat keine Antwort geliefert.")
    action = None
    mm = re.search(r"```action\s*(.*?)```", reply, re.DOTALL)
    if mm:
        jm = re.search(r"\{.*\}", mm.group(1), re.DOTALL)   # erstes/größtes JSON-Objekt im Block
        if jm:
            try:
                cand = json.loads(jm.group(0))
                if isinstance(cand, dict) and cand.get("action"):
                    action = {"action": cand["action"], "args": cand.get("args") or {}}
            except ValueError:
                action = None
        reply = (reply[:mm.start()] + reply[mm.end():]).strip()
    audit("dashboard", "assistant.reply", (action or {}).get("action", "-"))
    return {"reply": reply, "action": action}


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
    props = skills_store.load_proposals()
    for p in props:                     # #48: Ampel je Vorschlag (auch Bot-Vorschläge prüfen)
        p["scan"] = skillguard.scan((p.get("content") or "") + "\n" + (p.get("description") or ""))
    return {"skills": skills_store.list_skills(), "proposals": props}


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


@app.post("/api/skills/scan")
async def api_skill_scan(request: Request):
    """SkillGuard (#48): Text auf gefährliche Muster prüfen (Ampel + Befunde)."""
    b = await request.json()
    return skillguard.scan((b.get("body") or "") + "\n" + (b.get("description") or ""))


@app.post("/api/skills/import")
async def api_skill_import(request: Request):
    """SkillGuard (#48): Skill von URL/Text holen, parsen, scannen — NICHT speichern.
    Der Nutzer sieht Skill-Card + Scan-Ampel und entscheidet dann bewusst."""
    b = await request.json()
    url = (b.get("url") or "").strip()
    text = b.get("text") or ""
    if url:
        if not url.startswith(("http://", "https://")):
            return err("validate", "Nur http(s)-Adressen")
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=15) as r:
                text = r.read(200_000).decode("utf-8", "replace")   # 200-KB-Limit
        except Exception as e:
            print(f"[import] URL-Abruf fehlgeschlagen: {e}")
            return err("import", "Diese Adresse ließ sich nicht laden. 👉 Bitte prüfe den Link und versuch es erneut.")
    if not text.strip():
        return err("validate", "url oder text angeben")
    p = skills_store.parse(text)
    fm = p.get("frontmatter", {})
    scan = skillguard.scan(text)
    audit("dashboard", "skill.import_preview", url or "(text)", scan["level"] != "gefahr")
    return {"name": fm.get("name", ""), "description": fm.get("description", ""),
            "body": p.get("body", "").strip(), "source_url": url, "scan": scan}


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
MCP_FILE = os.path.join(platform_compat.workspace(), ".mcp.json")


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


@app.get("/api/mcp/catalog")
def api_mcp_catalog():
    """Kuratierte, geprüfte MCP-Integrationen (#55) + welche schon eingerichtet sind."""
    installed = set(load_mcp().get("mcpServers", {}))
    return {"catalog": mcp_catalog.public_catalog(), "installed": sorted(installed)}


@app.post("/api/mcp/catalog/{cid}")
async def api_mcp_catalog_add(cid: str, request: Request):
    """Eine kuratierte Integration mit den Nutzer-Angaben einrichten."""
    fields = (await request.json()).get("fields", {})
    try:
        entry = mcp_catalog.build_entry(cid, fields)
    except ValueError as e:
        return err("validate", str(e))
    data = load_mcp()
    if cid in data.get("mcpServers", {}):
        return err("validate", "Diese Integration ist bereits eingerichtet")
    data.setdefault("mcpServers", {})[cid] = entry
    os.makedirs(os.path.dirname(MCP_FILE), exist_ok=True)
    open(MCP_FILE, "w").write(json.dumps(data, indent=1))
    audit("dashboard", "mcp.catalog_add", cid)
    return {"ok": True, "name": cid}


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


@app.post("/api/aufbewahrung/{aktion}")
def api_aufbewahrung(aktion: str):
    """#18: Aufräumen anstoßen, Daten exportieren oder Verlauf komplett löschen."""
    import retention
    if aktion == "aufraeumen":
        erg = retention.aufraeumen(force=True)
        audit("dashboard", "aufbewahrung.aufraeumen", str(erg))
        return {"ok": True, "ergebnis": erg}
    if aktion == "export":
        # Nur eigene Daten, keine Geheimnisse: Verlauf + Gedächtnis + Profil/Persona.
        import sqlite3
        daten = {"exportiert_am": time.strftime("%Y-%m-%dT%H:%M:%S"), "verlauf": []}
        try:
            db = sqlite3.connect(os.path.join(BOT_DIR, "sessions.db"))
            db.row_factory = sqlite3.Row
            daten["verlauf"] = [dict(r) for r in db.execute(
                "SELECT epoch, bot, kind, model, messages, result FROM sessions "
                "ORDER BY epoch DESC LIMIT 5000")]
            db.close()
        except Exception as e:
            daten["verlauf_fehler"] = str(e)
        for name, datei in (("persona", "persona.json"), ("profil", "profile.json")):
            try:
                daten[name] = json.load(open(os.path.join(BOT_DIR, datei)))
            except Exception:
                pass
        audit("dashboard", "aufbewahrung.export", f"{len(daten['verlauf'])} Runden")
        return daten
    if aktion == "loeschen":
        import sqlite3
        n = 0
        try:
            db = sqlite3.connect(os.path.join(BOT_DIR, "sessions.db"))
            n = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            db.execute("DELETE FROM sessions")
            db.commit()
            try:
                db.execute("VACUUM")
            except sqlite3.Error:
                pass
            db.close()
        except Exception as e:
            raise HTTPException(500, str(e))
        audit("dashboard", "aufbewahrung.loeschen", f"{n} Runden geloescht")
        return {"ok": True, "geloescht": n}
    raise HTTPException(400, "unbekannte Aktion")


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
# no-cache = vor Gebrauch per ETag revalidieren (unverändert → 304, geändert → frisch).
# So bekommt der Browser App-Updates sofort, statt eine alte app.js/style.css zu behalten.
_NOCACHE = {"Cache-Control": "no-cache"}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"), headers=_NOCACHE)


@app.get("/{fn}")
def static_file(fn: str):
    p = os.path.join(STATIC, os.path.basename(fn))
    if os.path.exists(p) and os.path.isfile(p):
        return FileResponse(p, headers=_NOCACHE)
    return Response(status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
