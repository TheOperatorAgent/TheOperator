# Architektur — Operator

Operator ist ein persönlicher, lokal laufender KI-Assistent: Er beantwortet Matrix-Nachrichten
in Echtzeit über dein eigenes Claude-Abo, verwaltet Agenten, Skills und Zugangsdaten und bindet
Microsoft 365 und Google Drive per Berechtigungs-Reglern an. Kein Hersteller-Server, keine
API-Kosten, Ein-Befehl-Installation.

## Überblick

```
Matrix (Element)  ──►  Homeserver (Synapse)  ──►  listener.py (launchd-Daemon, Mac)
                                                      │  weckt bei jeder Nachricht:
                                                      ▼
                                              claude -p (headless, dein Abo)
                                                      │  Werkzeuge: Bash, Read, Web,
                                                      │  Agent, Skill, mcp__m365
                                                      ▼
                            memory.db · sessions.db · skills · vault · m365-MCP
                                                      ▲
Dashboard (127.0.0.1:8737, FastAPI + Vanilla-JS) ────┘  Verwaltung von allem
```

## Kernkomponenten

### listener.py — der Daemon (stdlib-only)
- launchd-LaunchAgent `com.the-operator.listener`, hält per Matrix-`/sync`-Long-Poll eine
  Echtzeit-Verbindung; bei neuer Nachricht → Tipp-Indikator + `claude -p --output-format json`.
- **BotSession-Multiplexing:** eine Session für den Owner-Bot (`@claude`), je eine pro
  veröffentlichtem Agenten-Bot (Hot-Reload aus `bots.json`).
- Baut den Prompt aus VERHALTEN.md + **Gesprächsverlauf** (`sessions.recent_dialog`) +
  **Gedächtnis-Treffer** (`memory.py` FTS5) + aktueller Nachricht. Antworten werden **redacted**
  (siehe SICHERHEIT.md), dann protokolliert.
- Ruft minütlich `cron_runner.tick()` für Automationen. Stdlib-only (pytest erzwingt es) —
  der Dashboard-venv ist optional.

### Dashboard — dashboard/ (FastAPI im venv, Vanilla-JS-SPA)
Bindet nur an 127.0.0.1, Bearer-Token, Host-Whitelist, kein Cookie/CSRF. Tabs:

| Tab | Zweck |
|---|---|
| Übersicht | Status-Kacheln (Listener, Agenten, Gedächtnis, Skills, Tresor, M365, Disk, Läufe) |
| Agenten | MD-Agenten CRUD, Modell tauschen, als eigenen Matrix-Bot veröffentlichen |
| Skills | Fähigkeiten (SKILL.md) verwalten + Vorschläge des Skill-Scouts annehmen |
| **Tresor** | **Passwort-Tresor: anlegen/entsperren, Einträge, Notfall-Kit, Recovery, FIDO-Keys; Backend-Umschalter lokal/Vaultwarden** |
| Verlauf | sessions.db mit FTS5-Volltextsuche |
| Automationen | Cron-Jobs (Zeitplan/Prompt/Ziel) + „Jetzt ausführen" |
| Nutzung | 5h-Fenster + 24h/7d-Balken aus sessions.db |
| Gedächtnis | Fakten-Browser (memory.db) |
| Microsoft 365 | Entra-Auto-Registrierung + Read/Write-Regler je Dienst |
| Google Drive | eigener OAuth-Client + Read/Write |
| Logs / System / Verhalten / Datenschutz | Log-Viewer, Backup/MCP, VERHALTEN.md-Editor, Datenschutz-Übersicht |

### Agenten — workspace/.claude/agents/*.md
Claude-Code-Subagenten (Frontmatter name/description/tools/model). Der Owner delegiert an sie;
Standard: recherche, schreiber (haiku), sysadmin (nur bei Shell-Opt-in). Über „Als Bot
veröffentlichen" bekommt ein Agent einen eigenen Matrix-Account (Synapse-Admin-API), Token im
Schlüsselbund.

### Skills — workspace/.claude/skills/<name>/SKILL.md (skills.py)
Wiederverwendbare Anleitungen, headless auto-geladen. Der Operator erkennt wiederkehrende
Aufgaben und legt selbst Skills an bzw. schlägt sie vor; der **Skill-Scout** (Standard-Cron,
So 18:30) analysiert die Historie. Manuell gepflegte Skills sind vor Auto-Überschreiben
geschützt.

### Passwort-Tresor — vault.py + redact.py + vaultwarden.py
Lokaler, verschlüsselter Tresor; Nutzung nur per `{{tresor:name}}`-Referenz. Entsperren per
Master-Passwort, Wiederherstellungsschlüssel **oder FIDO2-Hardware-Key** (hmac-secret via
`python-fido2`, Touch-only, mehrere Keys möglich — jeweils ein eigener DEK-Wrap).
Optional statt lokal: **Vaultwarden-Backend** (`vaultwarden.py`, `bw`-CLI) — umschaltbar im
Dashboard (`vault_backend` in `dashboard.json`, Standard `local`). Der `run`-Wrapper löst
Referenzen backend-abhängig auf; Allowlist-Härtung und Redaction gelten in beiden Fällen.
Details in SICHERHEIT.md.

### Pseudonymisierung — pseudonym.py + reid.py
Vor dem Senden an Claude werden PII in den Nutzer-Segmenten (Nachricht, Gedächtnis) durch
realistische Ersatzwerte ersetzt (Presidio + deutsches NER + Faker, im venv), die Antwort und
Tool-Argumente werden über `reid.py` (stdlib) zurückübersetzt. Reihenfolge: nach der
Secret-Redaction, im `listener.execute()`. Standard AN, im Datenschutz-Tab steuerbar
(Schutzstufe, Eigen-Identität-Allowlist, Deny-Liste). Details in SICHERHEIT.md.

### Standard-MCPs — mcp_m365.py & mcp_n8n.py
Eigene FastMCP-stdio-Server, per Default in `workspace/.mcp.json` registriert:
- **m365** (9 Task-Tools mail/cal/files/sharepoint/planner/teams), app-only über die
  auto-registrierte Connector-App, jedes Tool prüft die Dashboard-Regler.
- **n8n** (workflows/executions/webhook/health) — Nutzer trägt im Dashboard nur Server-URL +
  API-Key ein (Key AES-verschlüsselt in `secrets/`, nicht als Klartext-Env wie fertige
  npm-MCPs); Verbindung wird beim Speichern live getestet.
Beide lösen Pseudonymisierungs-Ersatzwerte vor echten Schreib-Aktionen über `reid.py` auf.

## Datenablage (BOT_DIR = ~/.claude/matrix-bot)

| Datei | Inhalt |
|---|---|
| `credentials.json` | Homeserver, Owner/Bot-IDs, `allowed_tools` (Token = Schlüsselbund-Marker) |
| `bots.json` | veröffentlichte Agenten-Bots |
| `cron.json` | Automationen (inkl. Skill-Scout) |
| `memory.db` | Langzeit-Gedächtnis (SQLite + FTS5) |
| `sessions.db` | Gesprächsverlauf + Token-Nutzung (redacted) |
| `secrets/*.enc` | OAuth-Tokens (Google/M365) + `vault.enc` (Passwort-Tresor) |
| `audit.log` | strukturiertes Aktions-Protokoll (redacted) |
| `VERHALTEN.md` | Verhaltensregeln (pro Wecken frisch geladen) |
| `workspace/` | Arbeitsverzeichnis für claude -p (.claude/agents, .claude/skills, .mcp.json) |

## Plattform-Abstraktion — platform_compat.py + secretstore.py + servicemgr.py (stdlib)
Ein Modul-Trio kapselt ALLE OS-Unterschiede, damit Listener & Helfer stdlib-only bleiben und
macOS bitidentisch weiterläuft:
- **platform_compat**: OS-Flags; `runtime_dir()/runtime_file()` (nutzer-privates Temp: macOS
  `$TMPDIR`, Linux `$XDG_RUNTIME_DIR`, Windows `%TEMP%`); `user_tag()/owns()` (Ersatz für
  `os.getuid()`); `venv_python()` (bin/python3 vs Scripts\\python.exe); `open_url()`
  (`webbrowser`); `secure_chmod()` (0600 bzw. Windows-ACL); `ipc_bind()/ipc_connect()`
  (AF_UNIX auf POSIX, TCP-Loopback+Token auf Windows).
- **secretstore**: `get/set/delete(account)` — macOS `security`, Windows DPAPI, Linux
  `secret-tool`, sonst 0600-Datei. Löst das gesamte frühere `security`/Keychain-Layer ab.
- **servicemgr**: `status()/restart()` je OS — launchd / systemd-user / Task Scheduler.

## Secret-Store-Konvention (Service `the-operator`)
`token-key` (AES-Master für secrets/), `matrix-owner`, `matrix-bot-<agent>`, `dashboard-token`
— je OS im passenden Store (siehe secretstore). Der Passwort-Tresor nutzt bewusst **keinen**
Store-Schlüssel, sondern das Master-Passwort (sitzungsgebunden + backup-portabel).

## Installation — install.sh (macOS/Linux) + install.ps1 (Windows), idempotent
Prüft Voraussetzungen, legt Bot-User an (Admin-API), installiert Listener + Helfer + Dashboard
(venv), registriert Standard-MCP + Skill-Scout, richtet den Autostart je OS ein (launchd /
systemd-user / Task Scheduler). Ein Installationslink je OS (Doku/README). `--uninstall` bzw.
`-Uninstall` stoppt Dienste, widerruft alle Tokens (Art.-17-Löschkette) und entfernt Schlüssel
inkl. Tresor-Sitzungsschlüssel.

