#!/bin/bash
# =============================================================================
# install.sh — Operator: dein Claude-Assistent im Matrix-Chat (macOS)
# Geführte Installation in 7 Phasen (siehe MACHBARKEITSSTUDIE.md).
# Idempotent: erneuter Lauf repariert/aktualisiert. Kein sudo nötig.
#
# Aufruf:  bash install.sh              (aus geklontem Repo)
#          curl -fsSL <RAW-URL>/install.sh | bash        (Remote)
#          bash install.sh --uninstall  (alles entfernen)
# =============================================================================
set -uo pipefail

BOT_DIR="$HOME/.claude/matrix-bot"
PLIST_LABEL="com.the-operator.listener"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
# TODO vor GitHub-Publish: Raw-URL auf das GitHub-Repo umstellen
REPO_RAW="${REPO_RAW:-http://192.168.178.53:3000/root/the-operator/raw/branch/main}"
GUI_DOMAIN="gui/$(id -u)"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()   { printf '  \033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
# Eingaben immer vom Terminal lesen (funktioniert auch bei curl | bash)
ask()   { local __var=$1 __prompt=$2 __default=${3:-}; local __in
          printf '  %s%s: ' "$__prompt" "${__default:+ [$__default]}" > /dev/tty
          IFS= read -r __in < /dev/tty || true
          printf -v "$__var" '%s' "${__in:-$__default}"; }
ask_secret() { local __var=$1 __prompt=$2; local __in
          printf '  %s: ' "$__prompt" > /dev/tty
          IFS= read -rs __in < /dev/tty || true; printf '\n' > /dev/tty
          printf -v "$__var" '%s' "$__in"; }

mx() { # mx <METHOD> <URL> [JSON-Body] [Token] — Matrix-API-Call, Antwort auf stdout
  local method=$1 url=$2 body=${3:-} token=${4:-}
  curl -sS -X "$method" "$url" \
    ${token:+-H "Authorization: Bearer $token"} \
    -H 'Content-Type: application/json' \
    ${body:+-d "$body"}
}
jget() { python3 -c "import json,sys;d=json.load(sys.stdin);print(d$1)" 2>/dev/null; }

# ---------------------------------------------------------------- Uninstall --
if [ "${1:-}" = "--uninstall" ]; then
  bold "Deinstallation"
  launchctl bootout "$GUI_DOMAIN" "$PLIST_PATH" 2>/dev/null && ok "Daemon gestoppt" || warn "Daemon war nicht geladen"
  rm -f "$PLIST_PATH" && ok "LaunchAgent entfernt"
  if [ -f "$BOT_DIR/credentials.json" ]; then
    HS=$(jget "['homeserver']" < "$BOT_DIR/credentials.json")
    TK=$(jget "['access_token']" < "$BOT_DIR/credentials.json")
    [ -n "$TK" ] && mx POST "$HS/_matrix/client/v3/logout" '{}' "$TK" >/dev/null && ok "Matrix-Token invalidiert"
  fi
  ask CONFIRM "Verzeichnis $BOT_DIR komplett löschen? (ja/nein)" "nein"
  [ "$CONFIRM" = "ja" ] && rm -rf "$BOT_DIR" && ok "Dateien gelöscht" || warn "Dateien behalten"
  bold "Fertig."; exit 0
fi

# ------------------------------------------------------------ Phase 1: PRÜFEN
bold "Operator-Installation — your operator inside the Matrix"
bold "Phase 1/7 — Voraussetzungen prüfen"
[ "$(uname)" = "Darwin" ] || die "Dieses Skript ist für macOS (für Windows/Linux siehe MACHBARKEITSSTUDIE.md)"
command -v python3 >/dev/null || die "python3 fehlt (Xcode Command Line Tools installieren: xcode-select --install)"
command -v curl >/dev/null || die "curl fehlt"
ok "macOS $(sw_vers -productVersion), python3 $(python3 -V | cut -d' ' -f2)"

# ------------------------------------------------------------ Phase 2: CLAUDE
bold "Phase 2/7 — Claude CLI"
if ! command -v claude >/dev/null; then
  warn "Claude CLI nicht gefunden — installiere…"
  curl -fsSL https://claude.ai/install.sh | bash \
    || { command -v npm >/dev/null && npm install -g @anthropic-ai/claude-code; } \
    || die "Installation fehlgeschlagen (weder Installer noch npm verfügbar)"
  export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
  command -v claude >/dev/null || die "claude nach Installation nicht im PATH — Terminal neu öffnen und Skript erneut ausführen"
fi
CLAUDE_BIN=$(command -v claude)
ok "Claude CLI: $CLAUDE_BIN ($(claude --version 2>/dev/null | head -1))"
# Anmeldung: läuft über den Browser (OAuth). Bis zu 3 Versuche, danach klare Anleitung.
if ! claude -p "Antworte nur mit: OK" 2>/dev/null | grep -q "OK"; then
  echo ""
  bold "  Anmeldung bei Claude"
  echo "  Gleich öffnet sich dein Browser. Melde dich dort mit deinem Claude-Konto an"
  echo "  (ein Claude-Abo ist Voraussetzung). Danach geht es hier automatisch weiter."
  ask _ENTER "Weiter mit Enter" ""
  ATTEMPT=0
  until claude -p "Antworte nur mit: OK" 2>/dev/null | grep -q "OK"; do
    ATTEMPT=$((ATTEMPT+1))
    [ $ATTEMPT -gt 3 ] && die "Anmeldung nach 3 Versuchen nicht erfolgreich. Bitte 'claude /login' manuell im Terminal ausführen und dieses Skript erneut starten."
    claude /login < /dev/tty > /dev/tty 2>&1 || true
  done
fi
ok "Claude CLI angemeldet und antwortet"

# ------------------------------------------------------------ Phase 3: FRAGEN
bold "Phase 3/7 — Konfiguration"
echo "  Dein Bot braucht einen EIGENEN Matrix-Account (nicht deinen persönlichen!)."
echo "  Eigener Homeserver: der Wizard kann den Account gleich anlegen (Admin nötig)."
echo "  Fremder Server (z. B. matrix.org): Account vorher manuell registrieren."
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
echo "  (Mächtig — Serververwaltung etc. —, aber jede Chat-Nachricht von dir kann dann"
echo "  Kommandos auslösen. Ohne Freigabe kann er nur lesen und im Web recherchieren.)"
ask BASH_OPTIN "Shell-Zugriff erlauben? (ja/nein)" "nein"
if [ "$BASH_OPTIN" = "ja" ]; then
  ALLOWED_TOOLS='["Bash", "Read", "WebFetch", "WebSearch"]'
  TOOLS_TEXT="Du darfst Shell-Kommandos ausführen (Bash), Dateien lesen und im Web recherchieren. Kleine Aufgaben direkt erledigen; Unumkehrbares nur nach Rückfrage im Chat."
else
  ALLOWED_TOOLS='["Read", "WebFetch", "WebSearch"]'
  TOOLS_TEXT="Du darfst Dateien lesen und im Web recherchieren. Shell-Zugriff ist NICHT freigegeben — wenn eine Aufgabe das bräuchte, sag das ehrlich."
fi

# ------------------------------------------------------------ Phase 4: MATRIX
bold "Phase 4/7 — Matrix-Anbindung"
LOGIN=$(mx POST "$HS/_matrix/client/v3/login" \
  "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"$BOT_USER\"},\"password\":$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$BOT_PW"),\"initial_device_display_name\":\"Mac Listener\"}")
TOKEN=$(printf '%s' "$LOGIN" | jget "['access_token']")
if [ -z "$TOKEN" ]; then
  warn "Login als @$BOT_USER fehlgeschlagen: $(printf '%s' "$LOGIN" | jget "['error']")"
  warn "Existiert der Bot-User noch nicht? Er kann per Admin-Account angelegt werden."
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
    "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"$BOT_USER\"},\"password\":$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$BOT_PW"),\"initial_device_display_name\":\"Mac Listener\"}" | jget "['access_token']")
  [ -n "$TOKEN" ] || die "Login nach Anlage weiterhin fehlgeschlagen"
fi
ok "Matrix-Token erzeugt"

# Bestehenden DM-Raum mit dem Menschen suchen, sonst neu anlegen
ROOM=""
for R in $(mx GET "$HS/_matrix/client/v3/joined_rooms" "" "$TOKEN" | python3 -c "import json,sys;[print(r) for r in json.load(sys.stdin).get('joined_rooms',[])]"); do
  ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$R")
  if mx GET "$HS/_matrix/client/v3/rooms/$ENC/joined_members" "" "$TOKEN" | grep -q "$HUMAN"; then ROOM=$R; break; fi
done
if [ -n "$ROOM" ]; then
  ok "Bestehender gemeinsamer Raum gefunden: $ROOM"
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
for F in listener.py send.py; do
  if [ -f "$SCRIPT_DIR/$F" ]; then cp "$SCRIPT_DIR/$F" "$BOT_DIR/$F"
  else curl -fsSL "$REPO_RAW/$F" -o "$BOT_DIR/$F" || die "$F weder lokal noch unter $REPO_RAW gefunden"; fi
  ok "$F installiert"
done
# VERHALTEN.md aus Template personalisieren (bestehende Datei wird nie überschrieben)
if [ -f "$BOT_DIR/VERHALTEN.md" ]; then
  ok "VERHALTEN.md existiert — bleibt unverändert"
else
  if [ -f "$SCRIPT_DIR/VERHALTEN.template.md" ]; then cp "$SCRIPT_DIR/VERHALTEN.template.md" "$BOT_DIR/.template.tmp"
  else curl -fsSL "$REPO_RAW/VERHALTEN.template.md" -o "$BOT_DIR/.template.tmp" || die "VERHALTEN.template.md nicht gefunden"; fi
  python3 - "$BOT_DIR/.template.tmp" "$BOT_DIR/VERHALTEN.md" "@$BOT_USER:$SERVER_NAME" "$HUMAN" "$TOOLS_TEXT" <<'PY'
import sys
t = open(sys.argv[1]).read()
t = t.replace("{{BOT_MXID}}", sys.argv[3]).replace("{{HUMAN_MXID}}", sys.argv[4])
t = t.replace("{{TOOLS_SECTION}}", sys.argv[5])
open(sys.argv[2], "w").write(t)
PY
  rm -f "$BOT_DIR/.template.tmp"
  ok "VERHALTEN.md aus Template erstellt — dort später eigenes Wissen eintragen!"
fi
python3 - "$BOT_DIR/credentials.json" "$HS" "@$BOT_USER:$SERVER_NAME" "$TOKEN" "$ROOM" "$HUMAN" "$ALLOWED_TOOLS" "$CLAUDE_BIN" <<'PY'
import json, sys
open(sys.argv[1], "w").write(json.dumps({
    "homeserver": sys.argv[2], "user_id": sys.argv[3],
    "access_token": sys.argv[4], "room_id": sys.argv[5],
    "owner_id": sys.argv[6], "allowed_tools": json.loads(sys.argv[7]),
    "claude_bin": sys.argv[8]}, indent=1))
PY
chmod 600 "$BOT_DIR/credentials.json"
ok "credentials.json geschrieben (chmod 600)"
# Plist mit dynamischen Pfaden erzeugen
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>$PLIST_LABEL</string>
	<key>ProgramArguments</key>
	<array><string>$(command -v python3)</string><string>$BOT_DIR/listener.py</string></array>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><true/>
	<key>ThrottleInterval</key><integer>15</integer>
	<key>StandardOutPath</key><string>$BOT_DIR/listener.log</string>
	<key>StandardErrorPath</key><string>$BOT_DIR/listener.log</string>
	<key>EnvironmentVariables</key>
	<dict><key>PATH</key><string>$(dirname "$CLAUDE_BIN"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
</dict>
</plist>
PLIST
ok "LaunchAgent-Plist erzeugt"

# ------------------------------------------------------------- Phase 6: START
bold "Phase 6/7 — Daemon starten"
launchctl bootout "$GUI_DOMAIN" "$PLIST_PATH" 2>/dev/null || true
launchctl bootstrap "$GUI_DOMAIN" "$PLIST_PATH" || die "launchctl bootstrap fehlgeschlagen"
sleep 3
launchctl print "$GUI_DOMAIN/$PLIST_LABEL" >/dev/null 2>&1 || die "Daemon läuft nicht — siehe $BOT_DIR/listener.log"
tail -1 "$BOT_DIR/listener.log" 2>/dev/null | grep -q "Listener gestartet" \
  && ok "Daemon läuft und lauscht" || warn "Daemon gestartet, Log noch leer — gleich prüfen: tail -f $BOT_DIR/listener.log"

# -------------------------------------------------------------- Phase 7: TEST
bold "Phase 7/7 — Funktionstest"
python3 "$BOT_DIR/send.py" "✅ Operator einsatzbereit! Ich bin dein Operator auf diesem Mac. Schreib mir einfach — ich antworte in Sekunden. (Verhalten anpassen: $BOT_DIR/VERHALTEN.md)" >/dev/null \
  && ok "Testnachricht im Raum — auf dem Handy prüfen!" \
  || warn "Testnachricht fehlgeschlagen — Log prüfen"
bold "Fertig! 🎉  Log: tail -f $BOT_DIR/listener.log  ·  Deinstallation: bash install.sh --uninstall"
