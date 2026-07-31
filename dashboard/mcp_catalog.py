"""Kuratierte MCP-Integrationen (#55) — reine, testbare Katalog-Logik (stdlib-only).

Der Katalog beschreibt geprüfte MCP-Server mit hohem Privatnutzer-Wert. Jeder Eintrag
nennt die benötigten Nutzer-Angaben (Feld-Definitionen); `build_entry()` setzt daraus den
fertigen `.mcp.json`-Eintrag zusammen (command/args/env). So bleibt die Konstruktion ohne
Seiteneffekte unit-testbar; das Schreiben der Datei macht der Server.

Bewusste Grenze: Wir betreiben damit FREMD-Code (npx-Pakete) mit eigenen Rechten. Der
Katalog kuratiert vertrauenswürdige, aktiv gepflegte Server — ersetzt aber keine eigene
Prüfung. Tokens landen (MCP-Standard) in der `.mcp.json` bzw. deren env; darauf weist die UI hin.
"""

# Jeder Eintrag: id, emoji, label, desc, homepage, setup (Kurzanleitung), fields[].
# fields[i]: key, label, secret(optional). build_entry() kennt die Templates je id.
CATALOG = [
    {
        # Sonderfall: braucht KEINE Nutzer-Angaben und keinen Login — deshalb wird dieser
        # Eintrag beim Installieren gleich mit verdrahtet (Gitea #120). Native HTTP statt
        # »npx mcp-remote«, damit auf einem Raspberry Pi kein Node.js nötig ist.
        "id": "learn", "emoji": "📘", "label": "Microsoft Learn",
        "desc": "Die echte Microsoft-Doku durchsuchen und zitieren, statt zu raten "
                "(Suche, Volltext, Code-Beispiele).",
        "homepage": "https://learn.microsoft.com/en-us/training/support/mcp",
        "setup": "Nichts einzurichten — kein Konto, kein Schlüssel, keine Lizenz. "
                 "Ist bei einer neuen Installation schon an.",
        "fields": [],
    },
    {
        "id": "notion", "emoji": "📝", "label": "Notion",
        "desc": "Seiten & Datenbanken durchsuchen, lesen und anlegen.",
        "homepage": "https://github.com/makenotion/notion-mcp-server",
        "setup": "In Notion unter Settings → Connections eine interne Integration anlegen, "
                 "den Token kopieren und die gewünschten Seiten mit der Integration teilen.",
        "fields": [{"key": "token", "label": "Notion-Integration-Token", "secret": True}],
    },
    {
        "id": "homeassistant", "emoji": "🏠", "label": "Home Assistant",
        "desc": "Smart-Home steuern und Zustände abfragen (offizielle MCP-Server-Integration).",
        "homepage": "https://www.home-assistant.io/integrations/mcp_server/",
        "setup": "In Home Assistant die Integration »Model Context Protocol Server« hinzufügen; "
                 "sie zeigt dir die genaue SSE-Adresse. Dann Profil → Sicherheit → Long-Lived "
                 "Access Token erstellen.",
        "fields": [
            {"key": "url", "label": "MCP-SSE-Adresse (zeigt HA nach dem Hinzufügen an)"},
            {"key": "token", "label": "Long-Lived Access Token", "secret": True},
        ],
    },
    {
        "id": "obsidian", "emoji": "🗒️", "label": "Obsidian",
        "desc": "Deinen Vault durchsuchen, Notizen lesen und anlegen.",
        "homepage": "https://github.com/coddingtonbear/obsidian-local-rest-api",
        "setup": "In Obsidian das Community-Plugin »Local REST API« (coddingtonbear) installieren "
                 "und aktivieren, dann den API-Key aus dessen Einstellungen kopieren.",
        "fields": [
            {"key": "url", "label": "Adresse", "default": "https://127.0.0.1:27124/mcp/"},
            {"key": "key", "label": "Local-REST-API-Key", "secret": True},
        ],
    },
    {
        "id": "calendar", "emoji": "📅", "label": "Kalender (CalDAV)",
        "desc": "Termine lesen und anlegen — iCloud, Nextcloud, Google (via CalDAV) u. a.",
        "homepage": "https://github.com/dominik1001/caldav-mcp",
        "setup": "CalDAV-Adresse deines Anbieters + Benutzer + Passwort. Bei iCloud/Google ein "
                 "App-spezifisches Passwort verwenden (nicht das Hauptpasswort).",
        "fields": [
            {"key": "url", "label": "CalDAV-Adresse"},
            {"key": "user", "label": "Benutzername"},
            {"key": "pass", "label": "Passwort / App-Passwort", "secret": True},
        ],
    },
    # ---------------------------------------------------------- #120: Microsoft-Rest --
    {
        "id": "ms_enterprise", "emoji": "🏢", "label": "Microsoft 365 Enterprise (Vorschau)",
        "desc": "Microsofts eigener Server für Unternehmensdaten. Ergänzt unsere Anbindung "
                "um Suche über SharePoint und Teams-Inhalte.",
        "homepage": "https://learn.microsoft.com/en-us/microsoft-365/enterprise/mcp",
        "setup": "Nur Anmeldung mit deinem Geschäftskonto — kein Schlüssel nötig.",
        # Ehrlich vorne dran statt im Kleingedruckten: Wer das einschaltet, soll vorher
        # wissen, was er NICHT bekommt.
        "grenze": "Öffentliche Vorschau von Microsoft. Ausschließlich LESEND, höchstens "
                  "100 Anfragen pro Minute, und nur in der normalen Microsoft-Cloud "
                  "(nicht in Behörden- oder China-Umgebungen). Schreiben geht über "
                  "unsere eigene Anbindung, nicht hierüber.",
        "fields": [],
    },
    {
        "id": "ms_workiq", "emoji": "🧠", "label": "Microsoft Work IQ",
        "desc": "Microsofts Wissens-Ebene über deine Arbeit: Wer arbeitet woran, welche "
                "Dokumente gehören zusammen, was war zuletzt wichtig.",
        "homepage": "https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/",
        "setup": "Braucht eine Microsoft-365-Copilot-Lizenz pro Person und eine "
                 "App-Registrierung durch die IT-Abteilung.",
        # Der Grund, warum diese Karte gesperrt ausgeliefert wird: Sie beim Klick
        # scheitern zu lassen wäre schlechter — der Nutzer sucht dann den Fehler bei sich.
        "gesperrt": "Ohne Microsoft-365-Copilot-Lizenz liefert diese Schnittstelle nichts — "
                    "das ist eine Grenze von Microsoft, keine Einstellung bei dir. Wir "
                    "können sie nicht umgehen. Alles andere, was dein Operator mit "
                    "Microsoft 365 kann, funktioniert ohne diese Lizenz.",
        "fields": [],
    },
    {
        "id": "ms_privat", "emoji": "🏡", "label": "Microsoft Privatkonto (outlook.com)",
        "desc": "Mail, Kalender und Dateien eines PRIVATEN Microsoft-Kontos — für alle, "
                "die kein Geschäftskonto haben.",
        "homepage": "https://github.com/softeria/ms-365-mcp-server",
        "setup": "Beim ersten Start meldest du dich einmal bei Microsoft an. "
                 "Node.js wird benötigt.",
        "grenze": "Bewusst nur LESEND ausgeliefert (Mail, Kalender, Dateien). Ein "
                  "Privatkonto hat keine Firmen-Verwaltung im Rücken — dort etwas "
                  "verändern zu lassen, wäre ohne Sicherheitsnetz.",
        "fields": [],
    },
]

_BY_ID = {c["id"]: c for c in CATALOG}

# Der eine Eintrag, der bei jeder Installation vorbelegt wird (#120). Öffentlicher
# Microsoft-Endpunkt ohne Anmeldung — hier landet also kein Geheimnis in der .mcp.json.
LEARN_ENTRY = {"type": "http", "url": "https://learn.microsoft.com/api/mcp"}


def get(cid):
    """Katalogeintrag per id (oder None)."""
    return _BY_ID.get(cid)


def public_catalog():
    """Katalog ohne interne Build-Details — für die Dashboard-Anzeige."""
    return CATALOG


def build_entry(cid, fields):
    """Baut den fertigen .mcp.json-Eintrag (command/args/env) aus den Nutzer-Feldern.

    Wirft ValueError bei unbekannter Integration oder fehlenden Pflichtfeldern.
    """
    if cid not in _BY_ID:
        raise ValueError("Unbekannte Integration")
    f = {k: str(v).strip() for k, v in (fields or {}).items()}

    def need(*keys):
        miss = [k for k in keys if not f.get(k)]
        if miss:
            raise ValueError("Bitte ausfüllen: " + ", ".join(miss))

    if cid == "learn":
        return dict(LEARN_ENTRY)

    if cid == "ms_enterprise":
        # Microsofts gehosteter Server, Anmeldung übernimmt mcp-remote im Browser.
        return {"command": "npx", "args": ["-y", "mcp-remote",
                                           "https://mcp.microsoft.com/enterprise/mcp"]}

    if cid == "ms_workiq":
        # Absichtlich nicht baubar: ohne Copilot-Lizenz liefert die Schnittstelle nichts.
        # Ein Eintrag, der beim Start scheitert, sähe für den Nutzer wie ein Fehler bei
        # ihm aus — und würde bei jedem Chat-Lauf Zeit kosten.
        raise ValueError(_BY_ID["ms_workiq"]["gesperrt"])

    if cid == "ms_privat":
        # --read-only und ein fester Preset: Der Umfang wird HIER festgelegt und nicht
        # dem Server überlassen. Fällt der Schalter weg, könnte ein Privatkonto plötzlich
        # beschrieben werden, ohne dass jemand zugestimmt hat.
        return {"command": "npx", "args": ["-y", "@softeria/ms-365-mcp-server",
                                           "--read-only",
                                           "--preset", "mail,calendar,files"]}

    if cid == "notion":
        need("token")
        return {"command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"],
                "env": {"NOTION_TOKEN": f["token"]}}

    if cid == "homeassistant":
        need("url", "token")
        return {"command": "npx", "args": ["-y", "mcp-remote", f["url"],
                                           "--header", f"Authorization: Bearer {f['token']}"]}

    if cid == "obsidian":
        need("key")
        url = f.get("url") or "https://127.0.0.1:27124/mcp/"
        return {"command": "npx", "args": ["-y", "mcp-remote", url,
                                           "--header", f"Authorization: Bearer {f['key']}"]}

    if cid == "calendar":
        need("url", "user", "pass")
        return {"command": "npx", "args": ["-y", "caldav-mcp"],
                "env": {"CALDAV_BASE_URL": f["url"], "CALDAV_USERNAME": f["user"],
                        "CALDAV_PASSWORD": f["pass"]}}

    raise ValueError("Unbekannte Integration")   # pragma: no cover
