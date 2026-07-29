# HANDOVER — The Operator · Projekt-Übergabe

> **Stand: 2026-07-29 · Version 1.8.0 · verfasst für den nahtlosen Weiterbetrieb durch einen anderen Agenten.**
> Dieses Dokument ist die **Single Source of Truth** für den aktuellen Stand. Bei Widerspruch zwischen diesem Dokument und dem Code gilt der Code — dann dieses Dokument korrigieren.
> Antwortsprache mit dem Nutzer (Michi Aschenbrenner): **immer Deutsch.**

---

## 0. ⚠️ ZUERST LESEN — Wichtigkeitsstufen

| Stufe | Bedeutung |
|---|---|
| 🔴 **P1** | Erreichbarkeit/Sicherheit/Datenschutz. Sofort, blockiert Nutzer oder Release. |
| 🟠 **P2** | Funktionslücke, spürbar, aber Workaround existiert. |
| 🟡 **P3** | Verbesserung/Backlog. |

**Die drei wichtigsten Dinge, die ein neuer Agent wissen muss:**
1. ✅ **#81 (Doppel-DM) und #86 (Gedächtnis-Lücke) sind GELÖST** — ausgeliefert als 1.7.2/1.7.3, Issues geschlossen. §4 dokumentiert beide (Diagnose-Muster wiederverwendbar). Nächste offene P1: **#13 Release-Blocker** (braucht Michis saubere Maschine). Website-Launch operator.bayern: **30.07.2026, 22:30** (GitHub public + deploy-strato.command = Michi).
2. 🔴 **Leitbild Einfachheit (Petra-Test)** ist verbindlich für JEDES Feature — `EINFACHHEIT.md`. Zielnutzer sind **Büromitarbeitende, keine Techniker**. Kein Terminal-Zwang, einfache Sprache, Fehler mit 👉-nächstem-Schritt, Geheimnisse maskiert.
3. 🔴 **Jede Auslieferung = alle 3 Repos + Version-Disziplin.** Siehe §6. Nie ein Feature „nur lokal".

---

## 1. Was ist The Operator?

Ein **selbst-gehosteter, lokaler KI-Assistent** (macOS/Linux/Windows), den der Nutzer über **Matrix** (Element-Chat) bedient. Kern:
- **`listener.py`** — stdlib-only launchd-Daemon. Pollt Matrix-Sync, baut pro Nachricht den Prompt, ruft **`claude -p`** (Abo, voller Werkzeugkasten) für den Owner bzw. **`llm_runner.py`** für Fremd-Modell-Agenten. Sendet Antworten via `send.py`.
- **Dashboard** — FastAPI (venv) + Vanilla-JS-SPA auf `127.0.0.1:8738`, Bearer-Token-Auth, persistente Sessions.
- **Datenschutz by design** — PII-**Pseudonymisierung** (Presidio-Daemon) vor jedem LLM-Aufruf, **Re-Identifikation** vor dem Senden; **Tresor** (FIDO2/Vaultwarden) für Geheimnisse; Matrix-only (bewusst, kein Telegram).

**Alleinstellung vs. Hermes/OpenClaw** (beide sind self-hosted OSS, NICHT Cloud — Recherche-belegt): PII-Pseudonymisierung, FIDO2-Tresor, Claude-**Abo** statt API-Key-Zwang, Matrix-only. Siehe `docs/SICHERHEIT_UND_ARCHITEKTUR.md` + README-Vergleichstabelle. Epic **#45**.

---

## 2. Verzeichnis, Konten, Infrastruktur

- **Arbeitsverzeichnis (BOT_DIR):** `/Users/michi/.claude/matrix-bot/` — **kein git-Repo** (Sync läuft über Gitea-MCP/Push, siehe §6).
- **Matrix-Homeserver:** Synapse auf **mindelpi** (`matrix.vonaschenbrenner.bayern`). Siehe Memory [[project_matrix_pi]].
  - Owner-Konto: **`@claude:matrix.vonaschenbrenner.bayern`** — **Anzeigename „Operator"** (⚠️ Ursache des Doppel-DM-Bugs, §4).
  - Owner (Nutzer): **`@michi:matrix.vonaschenbrenner.bayern`** (NICHT `@michael` — das ist die E-Mail).
  - Owner-Token: im Secret-Store, `secretstore.get("matrix-owner")`; `credentials.json:access_token == "keychain"` triggert die Keychain-Auflösung.
- **Beobachtete Räume (Stand jetzt, 3):**
  - `!IeJLecpkCqIMYZOYQP` = **Operator** (Owner-Raum, `credentials.json:room_id`)
  - `!vAnfaTnAbiimieqcPw` = **coder** (Agent, via main) · `!mvkNgBTolLxpafMkxN` = **websurfer** (Agent, via main)
  - `!CAfqDozBYELopDYhOY` = recherche (Agent) — in `bots.json`.
- **Gitea:** `http://192.168.178.53:3000` (User `root`). Repos: **`root/the-operator`** (Auslieferung, REPO_RAW-Quelle) · **`root/matrix-claude-bot`** (Entwicklung/Issues). Siehe Memory [[gitea_endpoint_pi]].
- **Auslieferungsquelle:** `updater.py` → `REPO_RAW = http://192.168.178.53:3000/root/the-operator/raw/branch/main`. Nur Dateien aus `manifest.json` werden vom Nutzer-Updater gezogen.

### Betriebsbefehle
```bash
# Listener neu starten (nach Code-Änderung an listener.py):
launchctl kickstart -k gui/$(id -u)/com.the-operator.listener
# Dienste: com.the-operator.listener / .dashboard / .pseudonym (in ~/Library/LaunchAgents/)
# Logs:
tail -f /Users/michi/.claude/matrix-bot/listener.log
# Tests (venv-Python):
cd /Users/michi/.claude/matrix-bot && dashboard/venv/bin/python -m pytest dashboard/test_dashboard.py -q
```
Hinweis: Der Listener baut sich bei Änderung an `bots.json` selbst neu auf; für `listener.py`-Änderungen braucht es den `kickstart`.

---

## 3. Was zuletzt gebaut & ausgeliefert wurde (chronologisch, alles LIVE)

| Version | Feature | Status |
|---|---|---|
| 1.5.x | **Multi-LLM-Subagenten** (Ollama/OpenAI/Azure via `llm_runner.py` + `providers.py`) + Claude-API-Auto-Fallback | ✅ live |
| 1.5.x | **Fremd-Agenten MIT Werkzeugen** — eigene Tool-Schleife im Pfad-Käfig (`_jail`, `FORBIDDEN_CMD`, MAX_STEPS=15). `coder`-Agent auf **Kimi K2.7 Code** (Ollama-Cloud), tool-fähig | ✅ live, E2E bewiesen (fizzbuzz) |
| 1.5.x | **🧭 Assistent-Tab** (Dashboard-Chat-Agent mit Whitelist-Aktionen) | ✅ live |
| 1.5.x | **Login-UX**: `dashboard`-Chatbefehl → Ein-Klick-OTT-Link; freundliche Fehler (`friendlyError`); persistente Sessions | ✅ live |
| 1.6.x | **🎭 Persona & Profil + Onboarding** — transparente Bindung (KEINE verdeckte Abhängigkeit; Michi-Wunsch bewusst umgelenkt). `persona.py` (stdlib), persona.json/profile.json, gitignore-geschützt | ✅ live |
| 1.7.0 | **🌐 Browser-Agent (websurfer)** — Playwright headless, `open_page`/`click_link`, **nur Lesen/Navigieren**, kein Formular-Absenden (Test `test_browser_tools_are_readonly`). v2 (Formulare mit Bestätigung) = Issue **#80** | ✅ live |
| **1.7.1** | **Identitäts-Ehrlichkeit** — Fremd-Modelle (Kimi) behaupteten fälschlich „Ich bin Claude". System-Prompt-Zusatz im foreign-Branch von `build()`: nie ein Produkt behaupten, wahrheitsgemäß „ein Sprachmodell im Operator" | ✅ **live, verifiziert** (websurfer sagt es jetzt korrekt) |
| **1.7.2** | **#81 Auto-Join** — Operator folgt Owner-Einladungen automatisch (`discover_owner_dm_rooms` + `accept_owner_invites`; NUR Owner-DMs, nie Fremde/Gruppen/Agenten-Räume); mehrere Owner-Räume gleichzeitig; launchd-Service war gar nicht geladen → korrekt verankert | ✅ live, 113 Tests |
| **1.7.3** | **#86 Gedächtnis-Lücke + Event-Dedup** — Direkt-Antworten via `record_direct` in den Verlauf (OTT-Token bewusst NICHT in DB; kind="chat"/model="direkt", weil `recent_dialog` hart auf kind='chat' filtert!); `seen_events`-Deque(200) in `run()` gegen Doppel-Antworten nach Netzfehlern | ✅ live, **116 Tests** |
| **1.7.4** | **Launch-Vorbereitung (#13)** — `updater.py` liest die Update-Quelle aus `repo_raw.txt` (vom Installer geschrieben) → Website-/GitHub-Installationen updaten aus GitHub statt aus dem internen Gitea; README-Install-Befehle öffentlich (operator.bayern) | ✅ live |
| **1.7.5** | **#83 Egress-Schutz für Tool-Ergebnisse** — `llm_runner._sanitize_result()`: Secrets maskiert + bekannte PII → Prompt-Surrogate, BEVOR Fremd-Modelle Tool-Ausgaben sehen. Listener reicht die s2r-Map durch | ✅ live, 118 Tests |
| **1.7.6** | **#59 Login-Vorwarnung** — `claude_health.py`: Zustand aus echten Läufen, Probe nur bei >6 h ohne Beweis, GENAU EINE Warnung je Ausfall; Login-Ablauf löst jetzt auch den API-Key-Fallback aus; Dashboard-Kachel »Claude-Zugang«. Ohne jeden Zugangsdaten-Zugriff | ✅ live, 121 Tests |
| **1.7.7** | **#58 Fair-Use-Drossel** — `throttle.py`: max 6/h, 40/Tag für `cron`+`event`; **Chat wird NIE gedrosselt** (test-gesichert); Konfig in dashboard.json, Zahlen im Dashboard-Status | ✅ live, **125 Tests** |
| **1.8.0** | **#65 Permission Broker** — `permission_broker.py` + `claude_tool_hook.py`: PreToolUse-Hook stuft jeden Werkzeug-Aufruf ein; riskante Aktionen (rm -rf, sudo, Systemdateien, Mail senden, curl\|bash) fragen im Matrix-Chat nach (ja/nein oder ✅/❌). **fail-closed**, Owner-gebunden, Replay-Schutz, Argument-Fingerprint. Umlauf läuft IM HOOK, weil der Listener während `claude -p` blockiert ist. Listener registriert den Hook selbst | ✅ live E2E bewiesen, **131 Tests** |

**Multi-LLM-Detail:** siehe Memory [[multi_llm_feature]]. **Wichtig:** Kein lokales Ollama-Modell auf dem MacBook (stürzt ab) — Kimi läuft über **Ollama-Cloud** (`ollama/kimi-k2.7-code:cloud`).

Tests: **131 pytest grün** (Stand 1.8.0; 111 vor 1.7.1) (persona, hints, browser-readonly, wants_dashboard u. a.). Listener bleibt **stdlib-only** — harte Regel, `llm_runner.py`/Dashboard dürfen venv nutzen, `listener.py`/`providers.py`/`persona.py` NICHT.

---

## 4. ✅ GELÖSTE P1-Fälle — #81 Doppel-DM (1.7.2) + #86 Gedächtnis-Lücke (1.7.3)

**Symptom (heute 12:00 real):** Michi schrieb im „Operator"-Chat („…Webserver Agent… suchen?", „Und geht es?", „Hallo?") → **keine Antwort**, obwohl Agenten (coder/websurfer) normal antworteten.

**Root-Cause (BEWIESEN, nicht vermutet):** `@claude`s Anzeigename ist „Operator" → in Element heißt **jeder** DM mit ihm „Operator". Element legte einen **zweiten** „Operator"-DM an; Michi tippte im zweiten, in dem der Operator **kein Mitglied** ist.
Diagnose-Beleg (Matrix CS-API mit gültigem Owner-Token, `whoami` ok):
- `@claude` in **genau 3 Räumen** (alle `/sync`-Buckets). Neuestes Event im Owner-Raum: **10:13** (Robert-Bauer-Mail). Michis 11:49–12:02-Chat liegt **nicht** darin.
- `m.direct` account_data = **404** → DM-Zuordnung nicht gepflegt → Nährboden für Doppel-DMs. Kein pending invite/leave.

**Sofortmaßnahme (getan, KEIN Code):** Beacon in den echten Raum gesendet („👋 Hier bin ich — das ist unser echter Chat", erkennbar an der Robert-Bauer-Mail). Event `$HGfV65tOdHShPLu-oSJouVQADyuQCZ1gkzxp_OMYeJM`. Pflaster, keine Lösung.

**Vorgeschlagene Lösung (in #81 detailliert):** Listener soll **Owner-DM-Einladungen automatisch annehmen** und dem Owner in neue Räume **folgen** (mehrere Owner-Räume statt genau einer). Start-Scan nimmt bestehende 2-Personen-`@claude`↔OWNER-Räume auf → heilt den jetzigen Fall.
**Sicherheits-Leitplanken:** NUR Einladungen von OWNER annehmen (nie Fremde → Anti-Injection); nur 2-Personen-DMs; nie Agenten-Räume doppeln.
**Status #81:** ✅ GEBAUT, ausgeliefert 1.7.2, Issue geschlossen (Details dort).

**#86 (1.7.3, ebenfalls gelöst):** Kurzbefehl-Antworten (dashboard-Link, Pseudonym-Ausfall) wurden nie in sessions.db aufgezeichnet → Modell fehlte Kontext bei Folgefragen; zusätzlich keine Event-Deduplizierung → Doppel-Antworten nach Netzfehlern. Fix: `record_direct()` + `seen_events`. Details in Issue #86.

---

## 5. Offene Gitea-Issues (`root/matrix-claude-bot`) mit Priorität

| # | Titel | Prio | Notiz |
|---|---|---|---|
| 13 | Release-Blocker: Frisch-System-Test + Setup-App | 🔴 **P1** | Braucht **saubere Maschine**. Blockiert echten Release. |
| 12 | E2E-Verschlüsselung des Bot-Raums | 🟠 P2 | pantalaimon-Ansatz teils da (Task #43). |
| 14 | M365-Zugriff auf gewählten Benutzer begrenzen (Least-Privilege) | 🟠 P2 | Security-Härtung. |
| 82/83/84 | Browser-Isolation · Tool-PII (Kern ✅ in 1.7.5) · Petra-Gate | 🔴 P0 | Von der Codex-Session angelegt |
| 66 | Vollständige Werkzeuge in sicherer Agenten-Runtime | 🔴 P0 | Von Codex umgeschrieben |
| 18 | Lokale Datenrechte, Aufbewahrung, strikter Modus | 🟠 P2 | Von Codex umgeschrieben |
| PR 85 | [WIP Security] Codex-PR | ⛔ | **Nicht mergen** (mergeable=false, Basis 1.7.1) — Rosinen picken, s. PR-Kommentar |
| 80 | Browser-Agent v2: Web-AKTIONEN (Formulare) mit Chat-Bestätigung | 🟡 P3 | Folge zu 1.7.0. |
| 36 | Cross-Platform Windows+Linux (macOS bitidentisch) | 🟡 P3 | Installer existiert; echte Fremd-OS-Tests offen. |
| 45 | **Epic** Wettbewerbs-Differenzierung | 🟡 P3 | Dach-Epic; viele Teile erledigt. |
| 53 | Voice-Pipeline (whisper.cpp STT + Kokoro TTS) | 🟡 P3 | Backlog-Feature. |
| 56/57 | RESEARCH Voice / Desktop-Computer-Use | 🟡 P3 | Reine Recherche. |
| 51 | Telegram-Kanal | 🟡 P3 | **Bewusst zurückgestellt** (Matrix-only = Sicherheitsmerkmal). |

**Empfehlung für den nächsten Agenten:** Erst **#81** (aktiver Bug, klein, Fix steht), dann **#13** (Release-Blocker, braucht aber Michis saubere Maschine).

---

## 6. 🔴 Auslieferungs-Disziplin (verbindlich)

Jede Änderung, die beim Nutzer ankommen soll, muss in **alle 3 Repos** + korrekt versioniert:
1. **`root/the-operator`** (Gitea) — Auslieferungsquelle (REPO_RAW). Dateien MÜSSEN in `manifest.json` stehen, sonst zieht der Updater sie nicht.
2. **`root/matrix-claude-bot`** (Gitea) — Entwicklung + Issues.
3. **GitHub** — Mirror.

**Sync-Weg:** Gitea-MCP `create_or_update_file` — ⚠️ **RAW-Text senden, NICHT base64** (sonst Doppel-Encoding; per Readback verifizieren). Token in `~/.claude/.mcp.json` → `mcpServers.gitea.env.GITEA_ACCESS_TOKEN`.

**Version-Disziplin:**
- Neue ausgelieferte Datei → in `manifest.json` **und** in beide Installer-Fetch-Listen (`install.sh` + `install.ps1`, liegen im Repo).
- `VERSION` + `updates.json` bumpen → treibt Update-Banner (#64). `updates.json` aktuell noch auf `1.7.0` im Body-Header, `VERSION`=`1.7.1` — beim nächsten Bump angleichen.
- Memory [[release_both_platforms]]: Release = macOS-DMG **und** Windows-EXE gemeinsam; `TAT_APP_VERSION` erst hoch, wenn beide Downloads liegen. (Firmenstart ai.quantex ~01.10.2026 → bis dahin Windows unsigniert, siehe [[project_aiquantex_launch]].)

**Arbeits-Tree:** BOT_DIR hat parallele In-Progress-Dateien des Nutzers — **nie `git add -A`**, nur eigene Dateien gezielt. Siehe [[project_working_tree_dirty]].

---

## 7. Sicherheits- & Datenschutz-Leitplanken (nicht verhandelbar)

- **PII-Pseudonymisierung** vor JEDEM LLM-Aufruf (auch Fremd-Modelle sehen nur Surrogate), Re-ID vor dem Senden. Fällt der Presidio-Daemon aus → **fail-safe**: Nachricht wird NICHT gesendet, Nutzer wird informiert.
- **Fremd-Agenten-Käfig:** `_jail()` (nur `workspace/agent-<name>/`), `FORBIDDEN_CMD` (sudo, rm -rf /|~, mkfs, dd of=/dev, shutdown/reboot/launchctl/killall/diskutil), MAX_STEPS=15, 60s/Befehl, alle Aktionen als 🔧-Zeile geloggt.
- **Browser-Agent:** nur Lesen/Navigieren, KEIN Formular-Absenden (Test schützt das). v2 nur mit Chat-Bestätigung (#80).
- **Identitäts-Ehrlichkeit** (1.7.1): Agenten dürfen sich nie als „Claude/ChatGPT" ausgeben. Passt zur Transparenz-Marke.
- **Urheber-Kennzeichnung** „von Michi Aschenbrenner" ist fest im Dashboard-Header, **niemals entfernen** (Test schützt das). Siehe [[feedback_attribution_permanent]].
- **Persona = transparente Bindung**, KEINE verdeckten Abhängigkeits-/Engagement-Mechaniken. Alles sichtbar, editierbar, löschbar. Siehe Plan-Datei + [[project_operator_persona]].

---

## 8. Wichtige Dateien (Karte)

| Datei | Rolle |
|---|---|
| `listener.py` | stdlib-Daemon; `build()` (Prompt+Persona+PII), `answer()`/`execute()`, `run()` (Sync-Loop ~825), `load_bot_sessions()` (~858) |
| `providers.py` | stdlib; resolve/list_models/**test** (mit hints down/nourl/nokey/auth/cloud/ok) |
| `llm_runner.py` | venv; Fremd-Modell-Runner + Tool-Schleife (Datei- + Browser-Werkzeuge) |
| `persona.py` | stdlib; persona/profile load/save/render, `is_onboarded()` |
| `dashboard/server.py` | FastAPI; `/api/models`, `/api/assistant`, `/api/persona`, `/api/profil`, persistente Sessions |
| `dashboard/static/{index.html,app.js}` | SPA; Tabs 🧭 Assistent, 🎭 Persona, Provider-Karte; `friendlyError()` |
| `dashboard/agents_store.py` | Agenten-CRUD; `KNOWN_TOOLS` inkl. „Browser" |
| `EINFACHHEIT.md` | 🔴 Petra-Test-Leitbild (verbindlich für jedes Feature) |
| `docs/SICHERHEIT_UND_ARCHITEKTUR.md` | Sicherheits-/Architektur-Doku + Wettbewerbsvergleich |
| `manifest.json` / `VERSION` / `updates.json` | Auslieferung + Update-Banner |
| `.gitignore` | schützt credentials/dashboard/bots/state/persona/profile/secrets/… |

---

## 9. Persistentes Gedächtnis (Memory) — schon gepflegt

Der Projektstand ist zusätzlich in `~/.claude/projects/…/memory/` verankert (Index: `MEMORY.md`). Relevanteste Einträge: [[multi_llm_feature]], [[project_operator_persona]], [[project_operator_login_ux]], [[project_operator_setup_assistant]], [[feedback_einfachheit_petra]], [[project_matrix_pi]], [[gitea_endpoint_pi]], [[release_both_platforms]], [[feedback_attribution_permanent]], [[project_competitive_differentiation]].
**Neu ergänzt für diese Übergabe:** ein Memory-Eintrag zum Doppel-DM-Bug (#81) + Verweis auf dieses HANDOVER.

---

### Nächster empfohlener Schritt
👉 **#13** (Release-Blocker, braucht Michis saubere Maschine) bzw. P2-Reihe: #59 CLI-Login-Vorwarnung → #65 Permission-Gate → #14/#12 mit Michi. Außerdem Website-Launch 30.07. 22:30 (siehe §0).
