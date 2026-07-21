"""M365-Anbindung: MSAL-PKCE-Setup-Flow + idempotente Entra-App-Registrierung.

Portiert nach dem TAT-Muster (Tenant_Analyse_Tool/backend/modules/planner_app.py):
find-or-create App -> requiredResourceAccess -> ServicePrincipal -> appRoleAssignments
(= programmatischer Admin-Consent, 409-tolerant) -> addPassword.

Die Permission-GUIDs werden zur Laufzeit aus den appRoles des Graph-ServicePrincipals
im Kunden-Tenant aufgeloest (kein GUID-Hardcoding).
"""
import base64
import json
import os
import time

import msal
import requests


def _tid_from_token(token: str) -> str:
    """Tenant-ID aus dem JWT-Payload lesen (kein extra Graph-Recht nötig)."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload)).get("tid", "")

import tokens

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
CONN_FILE = os.path.join(BOT_DIR, "connections", "m365.json")
GRAPH = "https://graph.microsoft.com/v1.0"
GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
APP_DISPLAY_NAME = "Operator M365 Connector"
SETUP_SCOPES = ["Application.ReadWrite.All", "AppRoleAssignment.ReadWrite.All"]
# "The Operator Setup" — Multi-Tenant-App im Hersteller-Tenant (ai.quantex,
# angelegt 2026-07-21 per az ad app create). Ihr Name erscheint auf dem
# Microsoft-Consent-Screen. Bewusst KEINE fremde/first-party Client-ID —
# der Admin muss beim Consent sehen, WEM er Rechte gibt (Security by Design).
# Über dashboard.json (m365_setup_client_id) überschreibbar.
DEFAULT_SETUP_CLIENT_ID = "25be2732-6e55-46cd-a9cc-22dc2817c276"
PLACEHOLDER_CLIENT_ID = "ERSETZEN-DURCH-THE-OPERATOR-SETUP-CLIENT-ID"  # Abwärtskompat.

# Dienst + Regler -> Graph-Application-Permission-VALUES (Schreiben impliziert Lesen)
PERMISSION_MAP = {
    "mail":       {"read": ["Mail.Read"],           "write": ["Mail.ReadWrite", "Mail.Send"]},
    "calendar":   {"read": ["Calendars.Read"],      "write": ["Calendars.ReadWrite"]},
    "onedrive":   {"read": ["Files.Read.All"],      "write": ["Files.ReadWrite.All"]},
    "sharepoint": {"read": ["Sites.Read.All"],      "write": ["Sites.ReadWrite.All"]},
    "planner":    {"read": ["Tasks.Read.All"],      "write": ["Tasks.ReadWrite.All"]},
    # Teams v1: nur Basisdaten; ChannelMessage.Read.All = Protected API, Senden app-only unmoeglich
    "teams":      {"read": ["Team.ReadBasic.All", "Channel.ReadBasic.All"], "write": []},
}


def setup_client_id() -> str:
    cfg = json.load(open(os.path.join(BOT_DIR, "dashboard.json")))
    cid = cfg.get("m365_setup_client_id")
    return cid if cid and cid != PLACEHOLDER_CLIENT_ID else DEFAULT_SETUP_CLIENT_ID


def matrix_to_values(perm_matrix: dict) -> list:
    """Toggle-Matrix -> sortierte Liste Permission-Values (dedupliziert)."""
    values = set()
    for svc, toggles in perm_matrix.items():
        m = PERMISSION_MAP.get(svc)
        if not m:
            continue
        if toggles.get("write") and m["write"]:
            values.update(m["read"])
            values.update(m["write"])
        elif toggles.get("read"):
            values.update(m["read"])
    return sorted(values)


# ---------------------------------------------------------------- PKCE-Setup-Flow --
def _msal_app():
    cache = msal.SerializableTokenCache()
    stored = tokens.load("m365_setup_cache")
    if stored:
        cache.deserialize(stored if isinstance(stored, str) else json.dumps(stored))
    app = msal.PublicClientApplication(
        setup_client_id(), authority="https://login.microsoftonline.com/common",
        token_cache=cache,
    )
    return app, cache


def start_auth(redirect_uri: str) -> dict:
    if setup_client_id() == PLACEHOLDER_CLIENT_ID:
        raise RuntimeError(
            "Setup-Client-ID fehlt: Entra-App 'The Operator Setup' anlegen und die "
            "Client-ID in dashboard.json unter m365_setup_client_id eintragen"
        )
    app, _ = _msal_app()
    # prompt=select_account: verhindert stilles SSO mit dem falschen Konto —
    # der Admin bekommt IMMER die Microsoft-Kontoauswahl angezeigt
    flow = app.initiate_auth_code_flow(SETUP_SCOPES, redirect_uri=redirect_uri,
                                       prompt="select_account")
    if "auth_uri" not in flow:
        raise RuntimeError(f"MSAL-Flow-Fehler: {flow}")
    return flow  # enthaelt auth_uri + state; Server haelt den Flow in der Pending-Registry


def complete_auth(flow: dict, query_params: dict) -> dict:
    app, cache = _msal_app()
    result = app.acquire_token_by_auth_code_flow(flow, query_params)
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description", str(result)))
    if cache.has_state_changed:
        tokens.save("m365_setup_cache", cache.serialize())
    return {"tenant_id": result.get("id_token_claims", {}).get("tid"),
            "account": result.get("id_token_claims", {}).get("preferred_username")}


def _setup_token() -> str:
    app, cache = _msal_app()
    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError("Nicht angemeldet — zuerst den M365-Anmelde-Schritt durchlaufen")
    result = app.acquire_token_silent(SETUP_SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError("Setup-Token abgelaufen — bitte erneut anmelden")
    if cache.has_state_changed:
        tokens.save("m365_setup_cache", cache.serialize())
    return result["access_token"]


# ---------------------------------------------------------------- Graph-Client --
class Graph:
    def __init__(self, token: str):
        self.h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def req(self, method: str, path: str, payload=None, ok_codes=(200, 201, 204)):
        r = requests.request(method, GRAPH + path, headers=self.h,
                             json=payload, timeout=30)
        if r.status_code not in ok_codes:
            raise RuntimeError(f"Graph {method} {path}: HTTP {r.status_code} {r.text[:300]}")
        return r.json() if r.text else {}


# ---------------------------------------------------------------- ensure-Pipeline --
def _load_conn() -> dict:
    if os.path.exists(CONN_FILE):
        return json.load(open(CONN_FILE))
    return {}


def _save_conn(conn: dict) -> None:
    os.makedirs(os.path.dirname(CONN_FILE), exist_ok=True)
    fd = os.open(CONN_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(conn, f, indent=1)
    os.replace(CONN_FILE + ".tmp", CONN_FILE)


def _graph_sp_roles(g: Graph) -> tuple[str, dict]:
    """(sp_object_id, {permission_value: role_guid}) des Graph-SP im Tenant."""
    res = g.req("GET", f"/servicePrincipals?$filter=appId eq '{GRAPH_APP_ID}'")
    sp = res["value"][0]
    return sp["id"], {r["value"]: r["id"] for r in sp["appRoles"]}


def ensure_pipeline(perm_matrix: dict):
    """Generator: liefert Fortschritts-Events (fuer SSE)."""
    def ev(step, status, detail=""):
        return {"step": step, "status": status, "detail": detail}

    token = _setup_token()
    g = Graph(token)
    conn = _load_conn()

    yield ev("resolve", "running", "Graph-Berechtigungs-IDs im Tenant auflösen")
    values = matrix_to_values(perm_matrix)
    if not values:
        yield ev("resolve", "error",
                 "Kein Dienst ausgewählt — bitte zuerst mindestens einen Regler "
                 "aktivieren (z. B. Mail › Lesen) und dann erneut starten.")
        return
    graph_sp_id, roles = _graph_sp_roles(g)
    missing = [v for v in values if v not in roles]
    if missing:
        yield ev("resolve", "error", f"Unbekannte Permissions im Tenant: {missing}")
        return
    rra = [{"resourceAppId": GRAPH_APP_ID,
            "resourceAccess": [{"id": roles[v], "type": "Role"} for v in values]}]
    yield ev("resolve", "done", f"{len(values)} Berechtigungen aufgelöst")

    yield ev("app", "running", f"App '{APP_DISPLAY_NAME}' suchen/anlegen")
    res = g.req("GET", f"/applications?$filter=displayName eq '{APP_DISPLAY_NAME}'")
    if res["value"]:
        app = res["value"][0]
        g.req("PATCH", f"/applications/{app['id']}", {"requiredResourceAccess": rra})
        yield ev("app", "done", f"Bestehende App aktualisiert ({app['appId']})")
    else:
        app = g.req("POST", "/applications", {
            "displayName": APP_DISPLAY_NAME,
            "signInAudience": "AzureADMyOrg",
            "requiredResourceAccess": rra,
        })
        yield ev("app", "done", f"App angelegt ({app['appId']})")

    yield ev("sp", "running", "ServicePrincipal sicherstellen")
    res = g.req("GET", f"/servicePrincipals?$filter=appId eq '{app['appId']}'")
    if res["value"]:
        sp = res["value"][0]
    else:
        sp = g.req("POST", "/servicePrincipals", {"appId": app["appId"]})
    yield ev("sp", "done", sp["id"])
    # App-Identität sofort persistieren — macht Wiederholungsläufe voll idempotent
    # (Secret wird dann wiederverwendet statt bei jedem Lauf neu erzeugt)
    conn.update({"app_object_id": app["id"], "app_client_id": app["appId"],
                 "sp_id": sp["id"]})
    _save_conn(conn)

    yield ev("consent", "running", "Admin-Consent je Berechtigung erteilen")
    existing = g.req("GET", f"/servicePrincipals/{sp['id']}/appRoleAssignments")
    have = {a["appRoleId"] for a in existing.get("value", [])}
    granted = 0
    for v in values:
        if roles[v] in have:
            continue
        try:
            g.req("POST", f"/servicePrincipals/{sp['id']}/appRoleAssignments", {
                "principalId": sp["id"], "resourceId": graph_sp_id, "appRoleId": roles[v],
            })
            granted += 1
        except RuntimeError as e:
            if "409" not in str(e) and "Permission being assigned already" not in str(e):
                yield ev("consent", "error", f"{v}: {e}")
                return
    yield ev("consent", "done", f"{granted} neu erteilt, {len(have)} bestanden")

    yield ev("secret", "running", "Client-Secret prüfen/erzeugen")
    if tokens.load("m365_secret") and conn.get("app_client_id") == app["appId"]:
        yield ev("secret", "done", "vorhandenes Secret wird weiterverwendet")
    else:
        pw = g.req("POST", f"/applications/{app['id']}/addPassword",
                   {"passwordCredential": {"displayName": "operator-connector"}})
        tokens.save("m365_secret", pw["secretText"])
        conn["secret_expires"] = pw.get("endDateTime", "")
        yield ev("secret", "done", f"neues Secret bis {conn['secret_expires'][:10]}")

    # Tenant-ID direkt aus dem Token (GET /organization braeuchte ein extra Leserecht)
    conn.update({
        "tenant_id": _tid_from_token(token),
        "graph_sp_id": graph_sp_id,
        "permissions": perm_matrix, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    _save_conn(conn)
    yield ev("finish", "done", "Verbindung gespeichert")


def update_permissions(new_matrix: dict):
    """Regler-Diff anwenden: RRA patchen, Assignments nachziehen UND aktiv widerrufen."""
    token = _setup_token()
    g = Graph(token)
    conn = _load_conn()
    if not conn.get("app_object_id"):
        raise RuntimeError("Noch keine Connector-App — zuerst Einrichtung durchlaufen")

    graph_sp_id, roles = _graph_sp_roles(g)
    values = matrix_to_values(new_matrix)
    # Leere Auswahl ist hier ERLAUBT: bedeutet „alle Rechte entziehen"
    rra = [] if not values else [{"resourceAppId": GRAPH_APP_ID,
            "resourceAccess": [{"id": roles[v], "type": "Role"} for v in values]}]
    g.req("PATCH", f"/applications/{conn['app_object_id']}", {"requiredResourceAccess": rra})

    wanted_ids = {roles[v] for v in values}
    existing = g.req("GET", f"/servicePrincipals/{conn['sp_id']}/appRoleAssignments")
    added = removed = 0
    for a in existing.get("value", []):
        if a["appRoleId"] not in wanted_ids:
            g.req("DELETE", f"/servicePrincipals/{conn['sp_id']}/appRoleAssignments/{a['id']}")
            removed += 1
    have = {a["appRoleId"] for a in existing.get("value", [])}
    for rid in wanted_ids - have:
        g.req("POST", f"/servicePrincipals/{conn['sp_id']}/appRoleAssignments", {
            "principalId": conn["sp_id"], "resourceId": graph_sp_id, "appRoleId": rid,
        })
        added += 1
    conn["permissions"] = new_matrix
    conn["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save_conn(conn)
    return {"added": added, "removed": removed, "active_values": values}


def delete_connector():
    token = _setup_token()
    g = Graph(token)
    conn = _load_conn()
    if conn.get("app_object_id"):
        g.req("DELETE", f"/applications/{conn['app_object_id']}", ok_codes=(204, 404))
    tokens.delete("m365_secret")
    tokens.delete("m365_setup_cache")
    tokens.delete("m365_cc_token")
    if os.path.exists(CONN_FILE):
        os.remove(CONN_FILE)


def status() -> dict:
    conn = _load_conn()
    return {
        "configured": setup_client_id() != PLACEHOLDER_CLIENT_ID,
        "connected": bool(conn.get("app_client_id")),
        "tenant_id": conn.get("tenant_id"),
        "app_client_id": conn.get("app_client_id"),
        "secret_expires": conn.get("secret_expires"),
        "permissions": conn.get("permissions", {}),
        "active_values": matrix_to_values(conn.get("permissions", {})),
        "primary_user": conn.get("primary_user", ""),
    }


def set_primary_user(upn: str) -> None:
    conn = _load_conn()
    conn["primary_user"] = upn.strip()
    _save_conn(conn)
