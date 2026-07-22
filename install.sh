#!/bin/bash
# =============================================================================
# install.sh — Operator: dein Claude-Assistent im Matrix-Chat (macOS + Linux)
# Geführte Installation in 7 Phasen. Idempotent: erneuter Lauf repariert/aktualisiert.
# Kein sudo nötig. Für Windows gibt es install.ps1 (PowerShell).
#
# Aufruf:  bash install.sh                                  (aus geklontem Repo)
#          curl -fsSL <RAW-URL>/install.sh | bash           (Remote-Ein-Zeiler)
#          bash install.sh --uninstall                      (alles entfernen)
# =============================================================================
set -uo pipefail

OS="$(uname)"                       # Darwin (macOS) | Linux
BOT_DIR="$HOME/.claude/matrix-bot"
# TODO vor GitHub-Publish: Raw-URL auf das GitHub-Repo umstellen
REPO_RAW="${REPO_RAW:-http://192.168.178.53:3000/root/the-operator/raw/branch/main}"

# curl|bash-Härtung: Bei Pipe-Start ist stdin die Pipe, nicht das Terminal — interaktive
# Tools (claude) würden den restlichen Skript-Text aus der Pipe „aufessen" und der Lauf bräche
# lautlos ab. Darum: ohne TTY das Skript in eine Datei laden und mit /dev/tty neu ausführen.
if [ ! -t 0 ] && [ -z "${OPERATOR_REEXEC:-}" ] && [ -e /dev/tty ]; then
  _self="$(mktemp 2>/dev/null || echo /tmp/operator-install.$$.sh)"
  if curl -fsSL "$REPO_RAW/install.sh" -o "$_self" 2>/dev/null && [ -s "$_self" ]; then
    OPERATOR_REEXEC=1 exec bash "$_self" "$@" < /dev/tty
  fi
fi

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()   { printf '  \033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
ask()   { local __var=$1 __prompt=$2 __default=${3:-}; local __in
          printf '  %s%s: ' "$__prompt" "${__default:+ [$__default]}" > /dev/tty
          IFS= read -r __in < /dev/tty || true
          printf -v "$__var" '%s' "${__in:-$__default}"; }
ask_secret() { local __var=$1 __prompt=$2; local __in
          printf '  %s: ' "$__prompt" > /dev/tty
          IFS= read -rs __in < /dev/tty || true; printf '\n' > /dev/tty
          printf -v "$__var" '%s' "$__in"; }

mx() { local method=$1 url=$2 body=${3:-} token=${4:-}
  curl -sS -X "$method" "$url" \
    ${token:+-H "Authorization: Bearer $token"} \
    -H 'Content-Type: application/json' ${body:+-d "$body"}; }
jget() { python3 -c "import json,sys;d=json.load(sys.stdin);print(d$1)" 2>/dev/null; }

# ---------------------------------------------------------------- Plattform-Helfer --
# Secret-Store (macOS Keychain / Linux Secret-Service / Datei-Fallback) über secretstore.py
secret_set() {  # secret_set <account> <value>
  python3 -c "import sys;sys.path.insert(0,'$BOT_DIR');import secretstore;secretstore.set(sys.argv[1],sys.argv[2])" "$1" "$2" 2>/dev/null; }
secret_has() {  # returncode 0 = vorhanden
  python3 -c "import sys;sys.path.insert(0,'$BOT_DIR');import secretstore;sys.exit(0 if secretstore.get(sys.argv[1]) else 1)" "$1" 2>/dev/null; }
secret_del() {
  python3 -c "import sys;sys.path.insert(0,'$BOT_DIR');import secretstore;secretstore.delete(sys.argv[1])" "$1" 2>/dev/null; }
rand_hex()   { python3 -c "import secrets;print(secrets.token_hex(32))"; }

# Dienst installieren+starten. install_service <name> <exec> [args…]  (name: listener|dashboard|pseudonym)
install_service() {
  local name=$1; shift
  local logf="$BOT_DIR/$name.log"
  [ "$name" = pseudonym ] && logf="$BOT_DIR/pseudonym-daemon.log"
  local claudedir; claudedir=$(dirname "${CLAUDE_BIN:-$(command -v claude 2>/dev/null || echo /usr/bin)}")
  if [ "$OS" = Darwin ]; then
    local label="com.the-operator.$name"
    local plist="$HOME/Library/LaunchAgents/$label.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    { printf '<?xml version="1.0" encoding="UTF-8"?>\n'
      printf '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
      printf '<plist version="1.0"><dict>\n'
      printf '\t<key>Label</key><string>%s</string>\n' "$label"
      printf '\t<key>ProgramArguments</key><array>\n'
      for a in "$@"; do printf '\t\t<string>%s</string>\n' "$a"; done
      printf '\t</array>\n'
      printf '\t<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>\n'
      printf '\t<key>ThrottleInterval</key><integer>15</integer>\n'
      printf '\t<key>StandardOutPath</key><string>%s</string>\n' "$logf"
      printf '\t<key>StandardErrorPath</key><string>%s</string>\n' "$logf"
      printf '\t<key>EnvironmentVariables</key><dict><key>PATH</key><string>%s:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>\n' "$claudedir"
      printf '</dict></plist>\n'; } > "$plist"
    launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
  else  # Linux: systemd user service
    local unit="operator-$name"
    mkdir -p "$HOME/.config/systemd/user"
    { printf '[Unit]\nDescription=Operator %s\nAfter=network-online.target\n\n' "$name"
      printf '[Service]\nType=simple\nExecStart='
      for a in "$@"; do printf '%q ' "$a"; done; printf '\n'
      printf 'Restart=always\nRestartSec=15\n'
      printf 'Environment=PATH=%s:/usr/local/bin:/usr/bin:/bin\n' "$claudedir"
      printf 'StandardOutput=append:%s\nStandardError=append:%s\n\n' "$logf" "$logf"
      printf '[Install]\nWantedBy=default.target\n'; } > "$HOME/.config/systemd/user/$unit.service"
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now "$unit.service"
    loginctl enable-linger "$(id -un)" 2>/dev/null || warn "loginctl enable-linger nicht möglich — Dienste laufen evtl. nur bei angemeldetem Nutzer"
  fi
}

# ---------------------------------------------------------------- Uninstall --
if [ "${1:-}" = "--uninstall" ]; then
  bold "Deinstallation"
  for name in listener dashboard pseudonym; do
    if [ "$OS" = Darwin ]; then
      pl="$HOME/Library/LaunchAgents/com.the-operator.$name.plist"
      launchctl bootout "gui/$(id -u)" "$pl" 2>/dev/null && ok "$name gestoppt" || true
      rm -f "$pl"
    else
      systemctl --user disable --now "operator-$name.service" 2>/dev/null && ok "$name gestoppt" || true
      rm -f "$HOME/.config/systemd/user/operator-$name.service"
    fi
  done
  [ "$OS" = Linux ] && systemctl --user daemon-reload 2>/dev/null || true
  # Laufzeit-/Sitzungsdateien entfernen (plattformübergreifend über platform_compat)
  python3 -c "import sys;sys.path.insert(0,'$BOT_DIR')
try:
    import platform_compat as p, os
    for n in ('operator-vault.dek','operator-vaultwarden.session','operator-pseudonym.sock','operator-pseudonym.ipc'):
        try: os.unlink(p.runtime_file(n))
        except OSError: pass
except Exception: pass" 2>/dev/null || true
  if [ -f "$BOT_DIR/credentials.json" ]; then
    HS=$(jget "['homeserver']" < "$BOT_DIR/credentials.json")
    TK=$(jget "['access_token']" < "$BOT_DIR/credentials.json")
    [ "$TK" = "keychain" ] && TK=$(python3 -c "import sys;sys.path.insert(0,'$BOT_DIR');import secretstore;print(secretstore.get('matrix-owner') or '')" 2>/dev/null || true)
    [ -n "$TK" ] && mx POST "$HS/_matrix/client/v3/logout" '{}' "$TK" >/dev/null && ok "Matrix-Token (Owner) invalidiert"
    if [ -f "$BOT_DIR/bots.json" ]; then
      python3 - "$BOT_DIR" "$HS" <<'PY'
import json, sys, urllib.request
bot_dir, hs = sys.argv[1], sys.argv[2]
for b in json.load(open(bot_dir + "/bots.json")).get("bots", []):
    try:
        req = urllib.request.Request(hs + "/_matrix/client/v3/logout", method="POST",
            data=b"{}", headers={"Authorization": "Bearer " + b["access_token"],
                                 "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10); print("  ✓ Bot-Token invalidiert:", b["user_id"])
    except Exception as e:
        print("  ! Bot-Logout fehlgeschlagen:", b.get("user_id"), e)
PY
    fi
  fi
  if [ -x "$BOT_DIR/dashboard/venv/bin/python3" ] && [ -f "$BOT_DIR/connections/google.json" ]; then
    "$BOT_DIR/dashboard/venv/bin/python3" -c "
import sys; sys.path.insert(0, '$BOT_DIR/dashboard')
import google_auth; google_auth.disconnect()" 2>/dev/null && ok "Google-Token widerrufen" || true
  fi
  secret_del "token-key" && ok "Secret-Store-Schlüssel gelöscht" || true
  [ -f "$BOT_DIR/connections/m365.json" ] && warn "Hinweis: Die Entra-App 'Operator M365 Connector' im M365-Tenant ggf. manuell löschen"
  ask CONFIRM "Verzeichnis $BOT_DIR komplett löschen (inkl. Gedächtnis + Tokens)? (ja/nein)" "nein"
  [ "$CONFIRM" = "ja" ] && rm -rf "$BOT_DIR" && ok "Dateien gelöscht" || warn "Dateien behalten"
  bold "Fertig."; exit 0
fi

# ------------------------------------------------------------ Phase 1: PRÜFEN
bold "Operator-Installation — your operator inside the Matrix"
bold "Phase 1/7 — Voraussetzungen prüfen"
case "$OS" in
  Darwin) command -v python3 >/dev/null || die "python3 fehlt (xcode-select --install)";
          ok "macOS $(sw_vers -productVersion 2>/dev/null), python3 $(python3 -V | cut -d' ' -f2)";;
  Linux)  command -v python3 >/dev/null || die "python3 fehlt (z. B. apt install python3 python3-venv)";
          python3 -c "import venv" 2>/dev/null || warn "python3-venv fehlt evtl. (apt install python3-venv)";
          ok "Linux $(uname -r), python3 $(python3 -V | cut -d' ' -f2)";;
  *)      die "Nicht unterstütztes OS '$OS' — Windows: install.ps1 (PowerShell) verwenden";;
esac
command -v curl >/dev/null || die "curl fehlt"

# ------------------------------------------------------------ Phase 2: CLAUDE
bold "Phase 2/7 — Claude CLI"
if ! command -v claude >/dev/null; then
  warn "Claude CLI nicht gefunden — installiere…"
  curl -fsSL https://claude.ai/install.sh | bash \
    || { command -v npm >/dev/null && npm install -g @anthropic-ai/claude-code; } \
    || die "Installation fehlgeschlagen (weder Installer noch npm verfügbar)"
  export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
  command -v claude >/dev/null || die "claude nicht im PATH — Terminal neu öffnen und Skript erneut ausführen"
fi
CLAUDE_BIN=$(command -v claude)
ok "Claude CLI: $CLAUDE_BIN ($(claude --version 2>/dev/null | head -1))"
if ! claude -p "Antworte nur mit: OK" </dev/null 2>/dev/null | grep -q "OK"; then
  echo ""; bold "  Anmeldung bei Claude"
  echo "  Gleich öffnet sich dein Browser. Melde dich mit deinem Claude-Konto an."
  ask _ENTER "Weiter mit Enter" ""
  ATTEMPT=0
  until claude -p "Antworte nur mit: OK" </dev/null 2>/dev/null | grep -q "OK"; do
    ATTEMPT=$((ATTEMPT+1))
    [ $ATTEMPT -gt 3 ] && die "Anmeldung nach 3 Versuchen nicht erfolgreich. 'claude /login' manuell ausführen und Skript erneut starten."
    claude /login < /dev/tty > /dev/tty 2>&1 || true
  done
fi
ok "Claude CLI angemeldet und antwortet"

# ------------------------------------------------------------ Phase 3: FRAGEN
bold "Phase 3/7 — Konfiguration"
echo "  Dein Bot braucht einen EIGENEN Matrix-Account (nicht deinen persönlichen!)."
ask HS "Matrix-Homeserver-URL des Bot-Accounts" "https://matrix.org"
HS=${HS%/}
mx GET "$HS/_matrix/client/versions" | grep -q versions || die "Homeserver $HS nicht erreichbar"
ok "Homeserver erreichbar"
SERVER_NAME=${HS#https://}; SERVER_NAME=${SERVER_NAME#http://}
ask BOT_USER "Bot-Benutzername (localpart, ohne @ und Server)" ""
[ -n "$BOT_USER" ] || die "Bot-Benutzername wird benötigt"
ask_secret BOT_PW "Passwort für @$BOT_USER:$SERVER_NAME"
ask HUMAN "Deine eigene Matrix-ID (nur diese wird beantwortet, z. B. @ich:matrix.org)" ""
case "$HUMAN" in @*:*) ;; *) die "Bitte vollständige Matrix-ID angeben (@name:server)";; esac
echo ""
echo "  Werkzeug-Freigabe: Darf dein Bot Shell-Kommandos auf DIESEM Rechner ausführen?"
ask BASH_OPTIN "Shell-Zugriff erlauben? (ja/nein)" "nein"
if [ "$BASH_OPTIN" = "ja" ]; then
  ALLOWED_TOOLS='["Bash", "Read", "WebFetch", "WebSearch", "Agent", "Skill", "mcp__m365", "mcp__n8n"]'
  TOOLS_TEXT="Du darfst Shell-Kommandos ausführen (Bash), Dateien lesen, im Web recherchieren und an deine Agenten delegieren. Kleine Aufgaben direkt erledigen; Unumkehrbares nur nach Rückfrage im Chat."
else
  ALLOWED_TOOLS='["Read", "WebFetch", "WebSearch", "Agent", "Skill", "mcp__m365", "mcp__n8n"]'
  TOOLS_TEXT="Du darfst Dateien lesen, im Web recherchieren und an deine Agenten delegieren. Shell-Zugriff ist NICHT freigegeben."
fi

# ------------------------------------------------------------ Phase 4: MATRIX
bold "Phase 4/7 — Matrix-Anbindung"
DEV_NAME="Operator Listener"
LOGIN=$(mx POST "$HS/_matrix/client/v3/login" \
  "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"$BOT_USER\"},\"password\":$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$BOT_PW"),\"initial_device_display_name\":\"$DEV_NAME\"}")
TOKEN=$(printf '%s' "$LOGIN" | jget "['access_token']")
if [ -z "$TOKEN" ]; then
  warn "Login als @$BOT_USER fehlgeschlagen: $(printf '%s' "$LOGIN" | jget "['error']")"
  ask ADMIN_USER "Admin-Benutzername (localpart, leer = abbrechen)" ""
  [ -n "$ADMIN_USER" ] || die "Ohne Bot-User geht es nicht weiter"
  ask_secret ADMIN_PW "Passwort für @$ADMIN_USER:$SERVER_NAME"
  ADMIN_TOKEN=$(mx POST "$HS/_matrix/client/v3/login" \
    "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"$ADMIN_USER\"},\"password\":$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$ADMIN_PW")}" | jget "['access_token']")
  [ -n "$ADMIN_TOKEN" ] || die "Admin-Login fehlgeschlagen"
  mx PUT "$HS/_synapse/admin/v2/users/@$BOT_USER:$SERVER_NAME" \
    "{\"password\":$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$BOT_PW"),\"admin\":false}" "$ADMIN_TOKEN" >/dev/null \
    || die "Bot-User konnte nicht angelegt werden (ist @$ADMIN_USER Server-Admin?)"
  ok "Bot-User @$BOT_USER:$SERVER_NAME angelegt"
  TOKEN=$(mx POST "$HS/_matrix/client/v3/login" \
    "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"$BOT_USER\"},\"password\":$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$BOT_PW"),\"initial_device_display_name\":\"$DEV_NAME\"}" | jget "['access_token']")
  [ -n "$TOKEN" ] || die "Login nach Anlage weiterhin fehlgeschlagen"
fi
ok "Matrix-Token erzeugt"
ROOM=""
for R in $(mx GET "$HS/_matrix/client/v3/joined_rooms" "" "$TOKEN" | python3 -c "import json,sys;[print(r) for r in json.load(sys.stdin).get('joined_rooms',[])]"); do
  ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$R")
  if mx GET "$HS/_matrix/client/v3/rooms/$ENC/joined_members" "" "$TOKEN" | grep -q "$HUMAN"; then ROOM=$R; break; fi
done
if [ -n "$ROOM" ]; then ok "Bestehender gemeinsamer Raum gefunden: $ROOM"
else
  ROOM=$(mx POST "$HS/_matrix/client/v3/createRoom" \
    "{\"is_direct\":true,\"invite\":[\"$HUMAN\"],\"preset\":\"trusted_private_chat\",\"name\":\"Claude\"}" "$TOKEN" | jget "['room_id']")
  [ -n "$ROOM" ] || die "Raum konnte nicht erstellt werden"
  ok "Neuer Raum erstellt: $ROOM — Einladung in deiner Matrix-App annehmen!"
fi

# ----------------------------------------------------------- Phase 5: DATEIEN
bold "Phase 5/7 — Dateien einrichten"
mkdir -p "$BOT_DIR"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" 2>/dev/null && pwd)
for F in listener.py send.py memory.py skills.py sessions.py cron_runner.py redact.py reid.py \
         migrate_tokens.py vaultwarden.py platform_compat.py secretstore.py servicemgr.py; do
  if [ -f "$SCRIPT_DIR/$F" ]; then cp "$SCRIPT_DIR/$F" "$BOT_DIR/$F"
  else curl -fsSL "$REPO_RAW/$F" -o "$BOT_DIR/$F" || die "$F weder lokal noch unter $REPO_RAW gefunden"; fi
  ok "$F installiert"
done
mkdir -p "$BOT_DIR/workspace/.claude/agents"
AGENTS="recherche schreiber"
[ "$BASH_OPTIN" = "ja" ] && AGENTS="$AGENTS sysadmin"
for A in $AGENTS; do
  DEST="$BOT_DIR/workspace/.claude/agents/$A.md"
  if [ -f "$DEST" ]; then ok "Agent $A existiert — bleibt unverändert"; continue; fi
  if [ -f "$SCRIPT_DIR/agents/$A.md" ]; then cp "$SCRIPT_DIR/agents/$A.md" "$DEST"
  else curl -fsSL "$REPO_RAW/agents/$A.md" -o "$DEST" || die "Agent-Vorlage $A.md nicht gefunden"; fi
  ok "Agent $A installiert"
done
python3 - "$BOT_DIR" <<'PYMCP'
import json, os, sys
bot = sys.argv[1]
venv_py = os.path.join(bot, "dashboard", "venv",
                       "Scripts" if os.name == "nt" else "bin",
                       "python.exe" if os.name == "nt" else "python3")
p = os.path.join(bot, "workspace", ".mcp.json")
data = {"mcpServers": {}}
if os.path.exists(p):
    try: data = json.load(open(p))
    except ValueError: pass
data.setdefault("mcpServers", {})["m365"] = {"command": venv_py, "args": [os.path.join(bot, "mcp_m365.py")]}
data["mcpServers"]["n8n"] = {"command": venv_py, "args": [os.path.join(bot, "mcp_n8n.py")]}
open(p, "w").write(json.dumps(data, indent=1))
PYMCP
ok "Standard-MCPs m365 + n8n registriert"
mkdir -p "$BOT_DIR/workspace/.claude/skills"
python3 - "$BOT_DIR" <<'PYSCOUT'
import hashlib, json, os, sys
bot = sys.argv[1]; p = os.path.join(bot, "cron.json"); jobs = []
if os.path.exists(p):
    try: jobs = json.load(open(p)).get("jobs", [])
    except ValueError: pass
if not any(j.get("name") == "Skill-Scout" for j in jobs):
    prompt = ("Du bist jetzt der Skill-Scout des Operators. (1) Lies die Aufgaben der letzten 7 Tage: "
              "python3 ~/.claude/matrix-bot/skills.py history 7  (2) Lies vorhandene Skills: skills.py list "
              "(3) Erkenne WIEDERKEHRENDE Muster (>=3x, noch nicht abgedeckt). (4) Lege je Muster (max 3) einen "
              "Vorschlag an: skills.py propose <name> -d \"<wann>\" -r \"<warum>\". (5) Melde kurz das Ergebnis. "
              "Lege KEINE Skills direkt an.")
    jobs.append({"id": hashlib.sha256(os.urandom(8)).hexdigest()[:8], "name": "Skill-Scout",
                 "schedule": "30 18 * * 0", "prompt": prompt, "target": "owner", "enabled": True})
    fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump({"jobs": jobs}, f, indent=1)
    os.replace(p + ".tmp", p)
PYSCOUT
ok "Skills aktiviert (Ordner + wöchentlicher Skill-Scout, So 18:30)"
if [ -f "$BOT_DIR/VERHALTEN.md" ]; then ok "VERHALTEN.md existiert — bleibt unverändert"
else
  if [ -f "$SCRIPT_DIR/VERHALTEN.template.md" ]; then cp "$SCRIPT_DIR/VERHALTEN.template.md" "$BOT_DIR/.template.tmp"
  else curl -fsSL "$REPO_RAW/VERHALTEN.template.md" -o "$BOT_DIR/.template.tmp" || die "VERHALTEN.template.md nicht gefunden"; fi
  python3 - "$BOT_DIR/.template.tmp" "$BOT_DIR/VERHALTEN.md" "@$BOT_USER:$SERVER_NAME" "$HUMAN" "$TOOLS_TEXT" <<'PY'
import sys
t = open(sys.argv[1]).read()
t = t.replace("{{BOT_MXID}}", sys.argv[3]).replace("{{HUMAN_MXID}}", sys.argv[4]).replace("{{TOOLS_SECTION}}", sys.argv[5])
open(sys.argv[2], "w").write(t)
PY
  rm -f "$BOT_DIR/.template.tmp"; ok "VERHALTEN.md aus Template erstellt"
fi
# Matrix-Token in den OS-Secret-Store (Härtung); Datei enthält nur den Marker
TOKEN_REF="keychain"
secret_set "matrix-owner" "$TOKEN"; secret_has "matrix-owner" \
  || { warn "Secret-Store nicht verfügbar — Token bleibt in der Datei (0600)"; TOKEN_REF="$TOKEN"; }
python3 - "$BOT_DIR/credentials.json" "$HS" "@$BOT_USER:$SERVER_NAME" "$TOKEN_REF" "$ROOM" "$HUMAN" "$ALLOWED_TOOLS" "$CLAUDE_BIN" <<'PY'
import json, os, sys
open(sys.argv[1], "w").write(json.dumps({
    "homeserver": sys.argv[2], "user_id": sys.argv[3], "access_token": sys.argv[4],
    "room_id": sys.argv[5], "owner_id": sys.argv[6], "allowed_tools": json.loads(sys.argv[7]),
    "claude_bin": sys.argv[8]}, indent=1))
os.chmod(sys.argv[1], 0o600)
PY
ok "credentials.json geschrieben"
python3 "$BOT_DIR/migrate_tokens.py" 2>/dev/null || true

# ------------------------------------------------------------- Phase 6: START
bold "Phase 6/7 — Listener-Dienst starten"
install_service listener "$(command -v python3)" "$BOT_DIR/listener.py" \
  && ok "Listener-Dienst eingerichtet und gestartet" || die "Dienststart fehlgeschlagen — siehe $BOT_DIR/listener.log"
sleep 3
tail -1 "$BOT_DIR/listener.log" 2>/dev/null | grep -q "Listener gestartet" \
  && ok "Daemon läuft und lauscht" || warn "Daemon gestartet, Log noch leer — später prüfen: tail -f $BOT_DIR/listener.log"

# -------------------------------------------------------- Phase 8: DASHBOARD (optional)
bold "Phase 8 — Web-Dashboard (optional)"
ask DASH_OPTIN "Web-Dashboard installieren (Agenten-GUI, Tresor, Google/M365-Anbindung)? (ja/nein)" "ja"
if [ "$DASH_OPTIN" = "ja" ]; then
  DASH_DIR="$BOT_DIR/dashboard"
  mkdir -p "$DASH_DIR/static" "$BOT_DIR/connections" "$BOT_DIR/secrets"
  chmod 700 "$BOT_DIR/secrets"
  VENV_PY="$DASH_DIR/venv/bin/python3"
  DASH_OK=1
  if [ ! -x "$VENV_PY" ]; then python3 -m venv "$DASH_DIR/venv" || DASH_OK=0; fi
  if [ "$DASH_OK" = "1" ]; then
    "$DASH_DIR/venv/bin/pip" install -q --upgrade pip 2>/dev/null
    "$DASH_DIR/venv/bin/pip" install -q "fastapi==0.116.*" "uvicorn==0.35.*" \
      "msal==1.33.*" "cryptography==45.*" "requests==2.32.*" "mcp==1.*" "starlette<0.49" \
      "fido2>=1.1" "presidio-analyzer" "presidio-anonymizer" "Faker" || DASH_OK=0
    "$DASH_DIR/venv/bin/pip" install -q "https://github.com/explosion/spacy-models/releases/download/de_core_news_lg-3.8.0/de_core_news_lg-3.8.0-py3-none-any.whl" \
      || warn "Deutsches Sprachmodell konnte nicht geladen werden — Pseudonymisierung meldet sich beim ersten Einsatz"
    "$VENV_PY" "$BOT_DIR/migrate_sessions.py" 2>/dev/null || true
  fi
  if [ "$DASH_OK" = "1" ]; then
    for F in server.py tokens.py agents_store.py m365_setup.py google_auth.py open.py; do
      if [ -f "$SCRIPT_DIR/dashboard/$F" ]; then cp "$SCRIPT_DIR/dashboard/$F" "$DASH_DIR/$F"
      else curl -fsSL "$REPO_RAW/dashboard/$F" -o "$DASH_DIR/$F" || DASH_OK=0; fi
    done
    for F in index.html app.js style.css; do
      if [ -f "$SCRIPT_DIR/dashboard/static/$F" ]; then cp "$SCRIPT_DIR/dashboard/static/$F" "$DASH_DIR/static/$F"
      else curl -fsSL "$REPO_RAW/dashboard/static/$F" -o "$DASH_DIR/static/$F" || DASH_OK=0; fi
    done
    for F in m365.py gdrive.py mcp_m365.py vault.py mcp_n8n.py pseudonym.py pseudonym_daemon.py migrate_sessions.py; do
      if [ -f "$SCRIPT_DIR/$F" ]; then cp "$SCRIPT_DIR/$F" "$BOT_DIR/$F"
      else curl -fsSL "$REPO_RAW/$F" -o "$BOT_DIR/$F" || DASH_OK=0; fi
    done
  fi
  if [ "$DASH_OK" = "1" ]; then
    secret_has "token-key" || secret_set "token-key" "$(rand_hex)"
    if [ ! -f "$BOT_DIR/dashboard.json" ]; then
      DTOK=$(rand_hex); secret_set "dashboard-token" "$DTOK"
      python3 - "$DTOK" "$BOT_DIR" <<'PY'
import hashlib, json, os, sys
tok, bot = sys.argv[1], sys.argv[2]
open(os.path.join(bot, "dashboard.json"), "w").write(json.dumps(
    {"port": 8737, "token_sha256": hashlib.sha256(tok.encode()).hexdigest(), "version": 1}, indent=1))
os.chmod(os.path.join(bot, "dashboard.json"), 0o600)
PY
    fi
    install_service dashboard "$VENV_PY" "$DASH_DIR/server.py" \
      && ok "Dashboard läuft — öffnen mit: $VENV_PY $DASH_DIR/open.py" \
      || warn "Dashboard-Start fehlgeschlagen — Bot läuft trotzdem (Log: $BOT_DIR/dashboard.log)"
    install_service pseudonym "$VENV_PY" "$BOT_DIR/pseudonym_daemon.py" \
      && ok "Pseudonymisierungs-Daemon läuft (schnelle PII-Ersetzung)" \
      || warn "Pseudonym-Daemon-Start fehlgeschlagen — Listener nutzt den Fallback"
    # Linux: FIDO-Sicherheitsschlüssel brauchen eine udev-Regel für HID-Zugriff (optional)
    if [ "$OS" = Linux ] && [ ! -f /etc/udev/rules.d/70-operator-fido.rules ]; then
      warn "Für Sicherheitsschlüssel-Entsperrung (optional) einmalig als root:"
      echo "        sudo tee /etc/udev/rules.d/70-operator-fido.rules >/dev/null <<'RULE'"
      echo '        KERNEL=="hidraw*", SUBSYSTEM=="hidraw", MODE="0660", TAG+="uaccess"'
      echo "        RULE"
      echo "        sudo udevadm control --reload-rules && sudo udevadm trigger"
    fi
  else
    warn "Dashboard-Installation unvollständig — der Chat-Bot läuft davon unabhängig weiter"
  fi
fi

# -------------------------------------------------------------- Phase 7: TEST
bold "Phase 7 — Funktionstest"
python3 "$BOT_DIR/send.py" "✅ Operator einsatzbereit! Schreib mir einfach — ich antworte in Sekunden. (Verhalten anpassen: $BOT_DIR/VERHALTEN.md)" >/dev/null \
  && ok "Testnachricht im Raum — auf dem Handy prüfen!" \
  || warn "Testnachricht fehlgeschlagen — Log prüfen"
bold "Fertig! 🎉  Log: tail -f $BOT_DIR/listener.log  ·  Deinstallation: bash install.sh --uninstall"
