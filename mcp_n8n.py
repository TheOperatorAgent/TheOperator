#!/usr/bin/env python3
"""Operator n8n MCP-Server (stdio) — Standard-MCP des Operators für n8n-Automatisierung.

Gleiches Muster wie mcp_m365.py: Verbindung wird EINMAL im Dashboard eingetragen
(URL + API-Key; der Key liegt AES-verschlüsselt in secrets/, nie als Klartext-Env in
.mcp.json wie bei fertigen npm-MCPs). Jedes Tool antwortet mit einem freundlichen
Dashboard-Hinweis, solange n8n nicht konfiguriert ist.

Start (macht der Listener automatisch via --mcp-config):
  ~/.claude/matrix-bot/dashboard/venv/bin/python3 ~/.claude/matrix-bot/mcp_n8n.py
"""
import json
import os
import sys
import time

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
sys.path.insert(0, os.path.join(BOT_DIR, "dashboard"))
sys.path.insert(0, BOT_DIR)

import requests  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

import tokens  # noqa: E402
try:
    import reid  # noqa: E402  (Pseudonymisierungs-Surrogate vor echten Aktionen auflösen)
except ImportError:
    reid = None

mcp = FastMCP("n8n")


def _rid(s):
    return reid.reidentify(s) if reid and s else s


def conn():
    p = os.path.join(BOT_DIR, "connections", "n8n.json")
    if not os.path.exists(p):
        raise RuntimeError("n8n ist nicht verbunden — im Dashboard unter 'System › n8n' "
                           "die Server-Adresse und den API-Key eintragen.")
    c = json.load(open(p))
    key = tokens.load("n8n_api_key")
    if not key:
        raise RuntimeError("n8n-API-Key fehlt — im Dashboard unter 'System › n8n' eintragen.")
    return c["url"].rstrip("/"), key


def api(method: str, path: str, payload=None, params=None):
    url, key = conn()
    r = requests.request(method, f"{url}/api/v1{path}",
                         headers={"X-N8N-API-KEY": key, "Content-Type": "application/json"},
                         json=payload, params=params, timeout=30)
    if r.status_code == 401:
        raise RuntimeError("n8n lehnt den API-Key ab — im Dashboard neu eintragen "
                           "(n8n: Settings › API › Create Key).")
    if r.status_code >= 400:
        raise RuntimeError(f"n8n {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def audit(action, target):
    try:
        with open(os.path.join(BOT_DIR, "audit.log"), "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "actor": "mcp_n8n", "action": action,
                                "target": str(target)[:80], "ok": True},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


@mcp.tool()
def workflows_list(active_only: bool = False) -> str:
    """Alle n8n-Workflows auflisten (ID, Name, aktiv/inaktiv, Tags)."""
    res = api("GET", "/workflows", params={"limit": 100})
    rows = res.get("data", [])
    if active_only:
        rows = [w for w in rows if w.get("active")]
    audit("workflows_list", f"{len(rows)} Workflows")
    return "\n".join(
        f"[{w['id']}] {'🟢' if w.get('active') else '⚪'} {w['name']}"
        + (f" ({', '.join(t['name'] for t in w.get('tags', []))})" if w.get("tags") else "")
        for w in rows) or "(keine Workflows)"


@mcp.tool()
def workflow_get(workflow_id: str) -> str:
    """Details eines Workflows: Trigger, Knoten, Status."""
    w = api("GET", f"/workflows/{workflow_id}")
    nodes = w.get("nodes", [])
    audit("workflow_get", workflow_id)
    lines = [f"{w['name']} — {'aktiv' if w.get('active') else 'inaktiv'}, {len(nodes)} Knoten"]
    lines += [f"  • {n.get('name')} [{n.get('type', '').split('.')[-1]}]" for n in nodes[:30]]
    return "\n".join(lines)


@mcp.tool()
def workflow_activate(workflow_id: str, active: bool = True) -> str:
    """Einen Workflow ein- oder ausschalten."""
    api("POST", f"/workflows/{workflow_id}/{'activate' if active else 'deactivate'}")
    audit("workflow_activate", f"{workflow_id}={active}")
    return f"Workflow {workflow_id} ist jetzt {'aktiv' if active else 'inaktiv'}."


@mcp.tool()
def executions_list(workflow_id: str = "", limit: int = 10) -> str:
    """Letzte Workflow-Läufe (Status, Zeit) — optional für einen bestimmten Workflow."""
    params = {"limit": min(limit, 50)}
    if workflow_id:
        params["workflowId"] = workflow_id
    res = api("GET", "/executions", params=params)
    audit("executions_list", workflow_id or "alle")
    out = []
    for e in res.get("data", []):
        status = "✅" if e.get("finished") else ("❌" if e.get("stoppedAt") else "⏳")
        out.append(f"[{e['id']}] {status} wf={e.get('workflowId')} "
                   f"start={str(e.get('startedAt', ''))[:19]}")
    return "\n".join(out) or "(keine Läufe)"


@mcp.tool()
def execution_get(execution_id: str) -> str:
    """Details eines Laufs — bei Fehlern inkl. Fehlermeldung des Knotens."""
    e = api("GET", f"/executions/{execution_id}", params={"includeData": "true"})
    audit("execution_get", execution_id)
    info = [f"Lauf {e['id']}: wf={e.get('workflowId')} finished={e.get('finished')} "
            f"start={str(e.get('startedAt', ''))[:19]} stop={str(e.get('stoppedAt', ''))[:19]}"]
    err = (e.get("data", {}).get("resultData", {}) or {}).get("error")
    if err:
        info.append(f"FEHLER: {str(err.get('message', err))[:400]}")
    return "\n".join(info)


@mcp.tool()
def webhook_trigger(path: str, payload_json: str = "{}") -> str:
    """Einen Webhook-Workflow anstoßen (path = Webhook-Pfad aus dem Workflow, z. B. 'mein-hook').
    payload_json = JSON-Daten für den Webhook."""
    url, _ = conn()
    path = _rid(path).lstrip("/")
    try:
        payload = json.loads(_rid(payload_json) or "{}")
    except ValueError:
        raise RuntimeError("payload_json ist kein gültiges JSON")
    r = requests.post(f"{url}/webhook/{path}", json=payload, timeout=60)
    audit("webhook_trigger", path)
    if r.status_code >= 400:
        raise RuntimeError(f"Webhook {r.status_code}: {r.text[:300]}")
    return f"Webhook '{path}' ausgelöst (HTTP {r.status_code}): {r.text[:400] or 'ok'}"


@mcp.tool()
def health() -> str:
    """Erreichbarkeit des n8n-Servers prüfen."""
    url, _ = conn()
    r = requests.get(f"{url}/healthz", timeout=10)
    audit("health", url)
    return f"n8n unter {url}: {'erreichbar ✅' if r.status_code == 200 else f'HTTP {r.status_code}'}"


if __name__ == "__main__":
    mcp.run()  # stdio
