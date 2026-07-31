#!/bin/bash
# =============================================================================
# install.sh — Operator: dein Claude-Assistent im Matrix-Chat (macOS + Linux)
# Geführte Installation in 7 Phasen. Idempotent: erneuter Lauf repariert/aktualisiert
# und merkt sich deine bisherigen Antworten. Kein sudo nötig.
# Für Windows gibt es install.ps1 (PowerShell).
#
# Aufruf:  bash install.sh                                  (aus geklontem Repo)
#          curl -fsSL <RAW-URL>/install.sh | bash           (Remote-Ein-Zeiler)
#          bash install.sh --uninstall                      (alles entfernen)
#
# Robustheit: Der gesamte Ablauf steckt in main() und wird erst in der LETZTEN
# Zeile aufgerufen — ein abgebrochener Download führt nie halbe Aktionen aus.
# Jede Frage validiert die Eingabe und fragt bei Fehlern erneut (nie Sackgasse).
# Geheimnisse (Passwörter/Tokens) laufen NIE als Prozess-Argument (ps-sicher).
# =============================================================================
set -uo pipefail

OS="$(uname)"                       # Darwin (macOS) | Linux
BOT_DIR="$HOME/.claude/matrix-bot"
# #106: Arbeitsordner NICHT unter ~/.claude — Claude Code sperrt dort Schreibzugriffe,
# und genau dort legen Agenten ihre Ergebnisse ab. Nebeneffekt: auffindbar statt versteckt.
WORKSPACE="${OPERATOR_WORKSPACE:-$HOME/Operator}"
STATE_FILE="$BOT_DIR/.install-state.json"
# #131: Der Installer nennt seine eigene Fassung. Bei zwei Auslieferungswegen
# (GitHub sofort, operator.bayern per Handupload) ist Drift sonst unsichtbar.
INSTALLER_VERSION="1.23.1"
# TODO vor GitHub-Publish: Raw-URL auf das GitHub-Repo umstellen
REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/TheOperatorAgent/TheOperator/main}"

# curl|bash-Härtung: Bei Pipe-Start ist stdin die Pipe — interaktive Tools würden den
# restlichen Skript-Text „aufessen". Darum: ohne TTY das Skript vollständig in eine
# Datei laden und mit /dev/tty neu ausführen. Ohne mktemp: sauberer Abbruch (kein
# vorhersagbarer /tmp-Pfad → kein Symlink-Risiko).
if [ ! -t 0 ] && [ -z "${OPERATOR_REEXEC:-}" ] && [ -e /dev/tty ]; then
  _self="$(mktemp)" || { echo "mktemp nicht verfügbar — bitte Skript herunterladen und mit 'bash install.sh' starten." >&2; exit 1; }
  if curl -fsSL "$REPO_RAW/install.sh" -o "$_self" 2>/dev/null && [ -s "$_self" ]; then
    OPERATOR_REEXEC=1 exec bash "$_self" "$@" < /dev/tty
  fi
fi

# ---------------------------------------------------------------- Ausgabe/Eingabe --
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

# 8-Bit-Startbild — bewusst reines ASCII (die Windows-Fassung MUSS ASCII bleiben,
# und beide sollen identisch aussehen). Zu schmales Fenster -> schlichte Zeile.
banner() {
  local cols; cols=$( { tput cols; } 2>/dev/null || echo "${COLUMNS:-80}" )
  case "$cols" in (*[!0-9]*|"") cols=80;; esac
  if [ "$cols" -lt 54 ]; then bold "OPERATOR"; return; fi
  printf '\033[95m  %s\033[0m\n' \
    ' ###  ####  ##### ####   ###  #####  ###  #### ' \
    '#   # #   # #     #   # #   #   #   #   # #   #' \
    '#   # ####  ####  ####  #####   #   #   # #### ' \
    '#   # #     #     #  #  #   #   #   #   # #  # ' \
    ' ###  #     ##### #   # #   #   #    ###  #   #'
  printf '\033[2m  %s\033[0m\n' '-----------------------------------------------'
  printf '\033[92m  %s\033[0m\n' '> your operator inside the matrix_'
  printf '\033[2m  Installer %s\033[0m\n\n' "$INSTALLER_VERSION"
}
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()   { printf '  \033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
say()   { printf '%s\n' "$*" > /dev/tty; }

ask()   { local __var=$1 __prompt=$2 __default=${3:-}; local __in
          printf '  %s%s: ' "$__prompt" "${__default:+ [$__default]}" > /dev/tty
          IFS= read -r __in < /dev/tty || true
          printf -v "$__var" '%s' "${__in:-$__default}"; }
ask_secret() { local __var=$1 __prompt=$2; local __in
          printf '  %s: ' "$__prompt" > /dev/tty
          IFS= read -rs __in < /dev/tty || true; printf '\n' > /dev/tty
          printf -v "$__var" '%s' "$__in"; }

# ask_loop VAR "Frage" "Default" validator — fragt, bis der Validator die Eingabe
# akzeptiert. Der Validator druckt bei Erfolg den (ggf. normalisierten) Wert auf
# stdout und gibt 0 zurück; bei Fehler erklärt er auf /dev/tty, was falsch ist.
ask_loop() {
  local __var=$1 __prompt=$2 __default=$3 __validate=$4 __in __norm
  while true; do
    printf '  %s%s: ' "$__prompt" "${__default:+ [$__default]}" > /dev/tty
    IFS= read -r __in < /dev/tty || true
    __in="${__in:-$__default}"
    if __norm=$("$__validate" "$__in"); then
      printf -v "$__var" '%s' "$__norm"; return 0
    fi
  done
}

# Ja/Nein tolerant: j/ja/y/yes bzw. n/nein/no (beliebige Groß-/Kleinschreibung);
# Unklares wird erneut gefragt. Ergebnis: "ja" oder "nein" in VAR.
ask_yesno() {
  local __var=$1 __prompt=$2 __default=$3 __in
  while true; do
    printf '  %s (ja/nein) [%s]: ' "$__prompt" "$__default" > /dev/tty
    IFS= read -r __in < /dev/tty || true
    __in="${__in:-$__default}"
    case "$(printf '%s' "$__in" | tr '[:upper:]' '[:lower:]')" in
      j|ja|y|yes) printf -v "$__var" 'ja'; return 0;;
      n|nein|no)  printf -v "$__var" 'nein'; return 0;;
      *) say "  ✗ Bitte mit ja oder nein antworten.";;
    esac
  done
}

# ---------------------------------------------------------------- HTTP/JSON --
mx() { local method=$1 url=$2 body=${3:-} token=${4:-}
  curl -sS -m 20 -X "$method" "$url" \
    ${token:+-H "Authorization: Bearer $token"} \
    -H 'Content-Type: application/json' ${body:+-d "$body"}; }
# mx2 METHOD URL BODY TOKEN OUTFILE → druckt HTTP-Code, Body liegt in OUTFILE
mx2() { local method=$1 url=$2 body=${3:-} token=${4:-} out=$5
  curl -sS -m 20 -o "$out" -w '%{http_code}' -X "$method" "$url" \
    ${token:+-H "Authorization: Bearer $token"} \
    -H 'Content-Type: application/json' ${body:+-d "$body"} 2>/dev/null || echo 000; }
jget() { python3 -c "import json,sys;d=json.load(sys.stdin);print(d$1)" 2>/dev/null; }
# JSON-String sicher erzeugen — Wert via ENV, nie argv (ps-sicher)
pw_json() { OP_V="$1" python3 -c 'import json,os;print(json.dumps(os.environ["OP_V"]))'; }

# ---------------------------------------------------------------- Secret-Store --
# Werte laufen via ENV an python (nie argv → nicht in ps sichtbar)
secret_set() { OP_SS_ACC="$1" OP_SS_VAL="$2" python3 -c "import sys,os;sys.path.insert(0,'$BOT_DIR');import secretstore;secretstore.set(os.environ['OP_SS_ACC'],os.environ['OP_SS_VAL'])" 2>/dev/null; }
secret_has() { python3 -c "import sys;sys.path.insert(0,'$BOT_DIR');import secretstore;sys.exit(0 if secretstore.get(sys.argv[1]) else 1)" "$1" 2>/dev/null; }
secret_del() { python3 -c "import sys;sys.path.insert(0,'$BOT_DIR');import secretstore;secretstore.delete(sys.argv[1])" "$1" 2>/dev/null; }
rand_hex()   { python3 -c "import secrets;print(secrets.token_hex(32))"; }

# ---------------------------------------------------------------- Antwort-Cache --
# Nicht-geheime Antworten überleben einen Abbruch: beim nächsten Lauf sind sie die
# Defaults („Enter = übernehmen"). Passwörter werden NIE gespeichert.
state_get() { python3 -c "import json;print(json.load(open('$STATE_FILE')).get('$1',''))" 2>/dev/null; }
state_save() {
  mkdir -p "$BOT_DIR"
  OP_HS="${HS:-}" OP_BU="${BOT_USER:-}" OP_HU="${HUMAN:-}" OP_BO="${BASH_OPTIN:-}" OP_DO="${DASH_OPTIN:-}" \
  python3 -c "
import json, os
p = '$STATE_FILE'
d = {'hs': os.environ['OP_HS'], 'bot_user': os.environ['OP_BU'], 'human': os.environ['OP_HU'],
     'bash_optin': os.environ['OP_BO'], 'dash_optin': os.environ['OP_DO']}
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as f: json.dump(d, f)
" 2>/dev/null || true
}

# ---------------------------------------------------------------- Validatoren --
v_homeserver() {
  local u="$1"
  [ -n "$u" ] || { say "  ✗ Bitte eine Server-Adresse eingeben — Beispiel: https://matrix.org"; return 1; }
  case "$u" in
    @*) say "  ✗ Das sieht wie eine Matrix-ID aus, nicht wie eine Server-Adresse. Beispiel: https://matrix.org"; return 1;;
    *" "*) say "  ✗ Adressen enthalten keine Leerzeichen. Beispiel: https://matrix.org"; return 1;;
  esac
  case "$u" in http://*|https://*) ;; *) u="https://$u";; esac
  u=${u%/}
  if curl -fsS -m 10 "$u/_matrix/client/versions" 2>/dev/null | grep -q '"versions"'; then
    printf '%s' "$u"; return 0
  fi
  say "  ✗ Unter $u antwortet kein Matrix-Server. Tippfehler in der Adresse? (Beispiel: https://matrix.org)"
  say "    Falls dein Server gerade offline ist: erst starten, dann hier Enter mit derselben Adresse."
  return 1
}

v_localpart() {
  local u="$1"
  u="${u#@}"; u="${u%%:*}"                       # @bot:server → bot (freundlich normalisieren)
  u="$(printf '%s' "$u" | tr '[:upper:]' '[:lower:]')"
  [ -n "$u" ] || { say "  ✗ Bitte einen Benutzernamen eingeben (nur der Name, z. B. operator-bot)"; return 1; }
  case "$u" in
    *[!a-z0-9._=/-]*) say "  ✗ Erlaubt sind nur Kleinbuchstaben, Zahlen und . _ = - /  (z. B. operator-bot)"; return 1;;
  esac
  printf '%s' "$u"
}

# Eigene Matrix-ID: Format prüfen + LIVE verifizieren, dass es sie wirklich gibt.
# Gleicher Server wie der Bot → Profil-Lookup (404 = gibt es nicht → erneut fragen).
# Anderer Server → prüfen, dass dieser Server überhaupt existiert/antwortet
# (fängt Tippfehler wie vmatrix.… ab, bevor später die Einladung ins Leere geht).
v_mxid() {
  local m="$1"
  case "$m" in
    @*:*) ;;
    "") say "  ✗ Bitte deine vollständige Matrix-ID eingeben, z. B. @ich:matrix.org"; return 1;;
    *) say "  ✗ Eine Matrix-ID beginnt mit @ und enthält einen Server: @ich:matrix.org (du hast »$m« getippt)"; return 1;;
  esac
  local mserver="${m#*:}"
  local enc; enc=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$m")
  local tmp code; tmp=$(mktemp)
  if [ "$mserver" = "$SERVER_NAME" ]; then
    code=$(mx2 GET "$HS/_matrix/client/v3/profile/$enc" "" "" "$tmp")
    rm -f "$tmp"
    if [ "$code" = "404" ]; then
      say "  ✗ Die Matrix-ID $m gibt es auf diesem Server nicht — Tippfehler? Prüfe sie in deiner Matrix-App (Einstellungen → Profil)."
      return 1
    fi
    printf '%s' "$m"; return 0
  fi
  # anderer Server: existiert er überhaupt?
  if curl -fsS -m 10 "https://$mserver/_matrix/client/versions" >/dev/null 2>&1 \
     || curl -fsS -m 10 "https://$mserver/.well-known/matrix/client" >/dev/null 2>&1; then
    rm -f "$tmp"; printf '%s' "$m"; return 0
  fi
  rm -f "$tmp"
  say "  ✗ Den Server »$mserver« aus deiner Matrix-ID gibt es nicht oder er antwortet nicht — Tippfehler? (du hast $m getippt)"
  return 1
}

# ---------------------------------------------------------------- Dienste --
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
  else
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
uninstall() {
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
  local CONFIRM; ask_yesno CONFIRM "Verzeichnis $BOT_DIR komplett löschen (inkl. Gedächtnis + Tokens)?" "nein"
  [ "$CONFIRM" = "ja" ] && rm -rf "$BOT_DIR" && ok "Dateien gelöscht" || warn "Dateien behalten"
  bold "Fertig."
}

# ---------------------------------------------------------------- Phasen --
# Bestes Python (>=3.10) für das Dashboard-venv finden. Der Chat-Bot selbst läuft mit
# jedem python3 (stdlib) — aber die Dashboard-Pakete (mcp, fastapi …) brauchen 3.10+.
# Wichtig auf frischen Macs: das System-Python ist 3.9, Homebrew-Python liegt aber oft
# schon unter /opt/homebrew — nur eben nicht im PATH des neuen Kontos.
find_venv_python() {
  local c v best="" bestv=0
  for c in python3.13 python3.12 python3.11 python3.10 \
           /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    v=$("$c" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null) || continue
    if [ "${v:-0}" -ge 310 ] && [ "$v" -gt "$bestv" ]; then best="$c"; bestv="$v"; fi
  done
  printf '%s' "$best"
}

phase1_check() {
  banner
  bold "Operator-Installation (macOS/Linux)"
  bold "Phase 1/7 — Voraussetzungen prüfen"
  case "$OS" in
    Darwin) command -v python3 >/dev/null || die "python3 fehlt (xcode-select --install)";
            ok "macOS $(sw_vers -productVersion 2>/dev/null), python3 $(python3 -V | cut -d' ' -f2)";;
    Linux)  command -v python3 >/dev/null || die "python3 fehlt (z. B. apt install python3 python3-venv)";
            ok "Linux $(uname -r), python3 $(python3 -V | cut -d' ' -f2)";;
    *)      die "Nicht unterstütztes OS '$OS' — Windows: install.ps1 (PowerShell) verwenden";;
  esac
  command -v curl >/dev/null || die "curl fehlt"
  # Dashboard-Python bestimmen (>=3.10) und ensurepip prüfen — Probleme JETZT auf
  # Deutsch erklären, nicht erst in Phase 8 mit englischem pip-Fehler.
  PY_VENV=$(find_venv_python)
  local _W
  if [ -z "$PY_VENV" ]; then
    warn "Für das Web-Dashboard wird Python 3.10 oder neuer gebraucht (der Chat-Bot läuft auch ohne)."
    if [ "$OS" = Darwin ]; then
      warn "Installieren mit:  brew install python   (oder von python.org), danach Skript erneut starten."
    else
      warn "Installieren z. B. mit:  sudo apt install python3.12 python3.12-venv, danach Skript erneut starten."
    fi
    ask_yesno _W "Trotzdem ohne Dashboard fortfahren?" "nein"
    [ "$_W" = "ja" ] || die "Okay — Python installieren und dieses Skript danach einfach erneut starten."
    VENV_POSSIBLE=0
  elif ! "$PY_VENV" -c "import ensurepip" 2>/dev/null; then
    PYV=$("$PY_VENV" -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
    warn "Für das Web-Dashboard fehlt ein Systempaket. Bitte VOR dem Fortfahren in"
    warn "einem zweiten Terminal ausführen:  sudo apt install python${PYV}-venv"
    ask_yesno _W "Trotzdem ohne Dashboard fortfahren (Chat-Bot läuft auch so)?" "nein"
    [ "$_W" = "ja" ] || die "Okay — Paket installieren und dieses Skript danach einfach erneut starten."
    VENV_POSSIBLE=0
  else
    ok "Dashboard-Python: $PY_VENV ($("$PY_VENV" -V | cut -d' ' -f2))"
  fi
}

# ~/.local/bin dauerhaft in den PATH der Nutzer-Shell (dort liegen claude UND operator).
# Idempotent; wichtig für frische Konten — sonst findet ein neues Terminal beides nicht.
persist_local_bin_path() {
  local rc
  for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if ! grep -qs '\.local/bin' "$rc" 2>/dev/null; then
      printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
    fi
  done
}

phase2_claude() {
  bold "Phase 2/7 — Claude CLI"
  CLAUDE_READY=1
  if ! command -v claude >/dev/null; then
    warn "Claude CLI nicht gefunden — installiere…"
    curl -fsSL https://claude.ai/install.sh | bash \
      || { command -v npm >/dev/null && npm install -g @anthropic-ai/claude-code; } \
      || die "Installation fehlgeschlagen (weder Installer noch npm verfügbar)"
    export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
    command -v claude >/dev/null || die "claude nicht im PATH — Terminal neu öffnen und Skript erneut ausführen"
  fi
  CLAUDE_BIN=$(command -v claude)
  persist_local_bin_path   # ab jetzt findet auch ein NEUES Terminal-Fenster den Befehl claude
  ok "Claude CLI: $CLAUDE_BIN ($(claude --version </dev/null 2>/dev/null | head -1))"
  # Probe IMMER mit stdin=/dev/null — claude darf nie in den interaktiven Modus kippen.
  # Die Anmeldung läuft bewusst NICHT hier im Installer (verschachtelte Terminal-UI hängt,
  # live gesehen), sondern in einem eigenen Fenster — wir warten und machen automatisch weiter.
  if ! claude -p "Antworte nur mit: OK" </dev/null 2>/dev/null | grep -q "OK"; then
    echo ""; bold "  Anmeldung bei Claude — bitte in einem NEUEN Terminal-Fenster"
    echo "  1. Neues Terminal-Fenster öffnen (Cmd+N bzw. Strg+Shift+N)"
    echo "  2. Dort eingeben:  claude"
    echo "     → beim ersten Start: Farbschema mit Enter bestätigen, dann anmelden (Browser)"
    echo "  3. Danach dort /exit eingeben — HIER geht es automatisch weiter, sobald die"
    echo "     Anmeldung erkannt wird. (Enter hier = ohne Anmeldung fortfahren)"
    local i _skip
    CLAUDE_READY=0
    for i in $(seq 1 120); do    # wartet bis zu ~10 Minuten
      if claude -p "Antworte nur mit: OK" </dev/null 2>/dev/null | grep -q "OK"; then
        CLAUDE_READY=1; break
      fi
      if IFS= read -t 5 -r _skip < /dev/tty; then
        warn "Okay — Installation läuft ohne Claude-Anmeldung weiter. Nachholen: claude /login"
        break
      fi
    done
  fi
  [ "$CLAUDE_READY" = 1 ] && ok "Claude CLI angemeldet und antwortet"
}

phase3_questions() {
  bold "Phase 3/7 — Konfiguration"
  echo "  Dein Bot braucht einen EIGENEN Matrix-Account (nicht deinen persönlichen!)."
  echo "  Noch keinen? Eigener Server: der Wizard kann ihn gleich anlegen (Admin nötig)."
  echo "  Fremder Server (z. B. matrix.org): Account vorher in der Matrix-App registrieren."
  # Defaults aus dem letzten (ggf. abgebrochenen) Lauf
  local d_hs d_bu d_hu d_bo d_do
  d_hs=$(state_get hs); d_bu=$(state_get bot_user); d_hu=$(state_get human)
  d_bo=$(state_get bash_optin); d_do=$(state_get dash_optin)
  while true; do
    ask_loop HS "Matrix-Homeserver-URL des Bot-Accounts" "${d_hs:-https://matrix.org}" v_homeserver
    ok "Homeserver erreichbar: $HS"
    SERVER_NAME=${HS#https://}; SERVER_NAME=${SERVER_NAME#http://}; SERVER_NAME=${SERVER_NAME%%/*}
    ask_loop BOT_USER "Bot-Benutzername (nur der Name, ohne @ und Server)" "${d_bu:-}" v_localpart
    ask_secret BOT_PW "Passwort für @$BOT_USER:$SERVER_NAME"
    ask_loop HUMAN "Deine eigene Matrix-ID (nur diese wird beantwortet)" "${d_hu:-}" v_mxid
    echo ""
    echo "  Werkzeug-Freigabe: Darf dein Bot Shell-Kommandos auf DIESEM Rechner ausführen?"
    ask_yesno BASH_OPTIN "Shell-Zugriff erlauben?" "${d_bo:-nein}"
    ask_yesno DASH_OPTIN "Web-Dashboard installieren (Agenten-GUI, Tresor, Google/M365)?" "${d_do:-ja}"
    # Zusammenfassung + Bestätigung (rustup-Muster): ein Blick, bevor etwas passiert
    echo ""
    bold "  Zusammenfassung"
    echo "    Homeserver      : $HS"
    echo "    Bot-Account     : @$BOT_USER:$SERVER_NAME"
    echo "    Deine Matrix-ID : $HUMAN"
    echo "    Shell-Zugriff   : $BASH_OPTIN"
    echo "    Dashboard       : $DASH_OPTIN"
    local OKGO; ask_yesno OKGO "Stimmt alles?" "ja"
    if [ "$OKGO" = "ja" ]; then break; fi
    d_hs="$HS"; d_bu="$BOT_USER"; d_hu="$HUMAN"; d_bo="$BASH_OPTIN"; d_do="$DASH_OPTIN"
    echo "  Okay — nochmal von vorn (Enter übernimmt den bisherigen Wert)."
  done
  if [ "$BASH_OPTIN" = "ja" ]; then
    ALLOWED_TOOLS='["Bash", "Read", "WebFetch", "WebSearch", "Agent", "Skill", "mcp__m365", "mcp__n8n", "mcp__learn"]'
    TOOLS_TEXT="Du darfst Shell-Kommandos ausführen (Bash), Dateien lesen, im Web recherchieren und an deine Agenten delegieren. Kleine Aufgaben direkt erledigen; Unumkehrbares nur nach Rückfrage im Chat."
  else
    ALLOWED_TOOLS='["Read", "WebFetch", "WebSearch", "Agent", "Skill", "mcp__m365", "mcp__n8n", "mcp__learn"]'
    TOOLS_TEXT="Du darfst Dateien lesen, im Web recherchieren und an deine Agenten delegieren. Shell-Zugriff ist NICHT freigegeben."
  fi
  state_save   # Antworten (ohne Passwort) sichern — ein Abbruch kostet keine Neueingabe
}

# Ein Login-Versuch: setzt TOKEN oder LOGIN_ERRCODE/LOGIN_ERRMSG/LOGIN_RETRY_MS
try_login() {
  local tmp code body
  tmp=$(mktemp)
  body="{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"$BOT_USER\"},\"password\":$(pw_json "$BOT_PW"),\"initial_device_display_name\":\"Operator Listener\"}"
  code=$(mx2 POST "$HS/_matrix/client/v3/login" "$body" "" "$tmp")
  TOKEN=$(jget "['access_token']" < "$tmp")
  LOGIN_ERRCODE=$(jget "['errcode']" < "$tmp")
  LOGIN_ERRMSG=$(jget "['error']" < "$tmp")
  LOGIN_RETRY_MS=$(jget "['retry_after_ms']" < "$tmp")
  rm -f "$tmp"
}

# Bot-User über einen Admin-Account anlegen (Synapse). 0 = ok, 1 = zurück zur Eingabe.
admin_create_user() {
  local ADMIN_USER ADMIN_PW ADMIN_TOKEN tries=0 tmp code
  echo "  Zum Anlegen brauche ich einmalig einen Server-Admin-Account deines Homeservers."
  ask ADMIN_USER "Admin-Benutzername (nur der Name; leer = zurück)" ""
  [ -n "$ADMIN_USER" ] || return 1
  while true; do
    ask_secret ADMIN_PW "Passwort für @$ADMIN_USER:$SERVER_NAME"
    tmp=$(mktemp)
    code=$(mx2 POST "$HS/_matrix/client/v3/login" \
      "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"$ADMIN_USER\"},\"password\":$(pw_json "$ADMIN_PW")}" "" "$tmp")
    ADMIN_TOKEN=$(jget "['access_token']" < "$tmp"); rm -f "$tmp"
    [ -n "$ADMIN_TOKEN" ] && break
    tries=$((tries+1))
    if [ $tries -ge 3 ]; then warn "Admin-Anmeldung 3× fehlgeschlagen — zurück zur Bot-Eingabe."; return 1; fi
    warn "Admin-Passwort stimmt nicht — bitte erneut."
  done
  tmp=$(mktemp)
  code=$(mx2 PUT "$HS/_synapse/admin/v2/users/@$BOT_USER:$SERVER_NAME" \
    "{\"password\":$(pw_json "$BOT_PW"),\"admin\":false}" "$ADMIN_TOKEN" "$tmp")
  local emsg; emsg=$(jget "['error']" < "$tmp"); rm -f "$tmp"
  case "$code" in
    200|201) ok "Bot-User @$BOT_USER:$SERVER_NAME angelegt"; return 0;;
    404) warn "Dein Homeserver ist kein Synapse (oder die Admin-API ist aus) — automatisches Anlegen geht hier nicht."
         warn "Bitte den Bot-Account manuell anlegen (z. B. in der Matrix-App registrieren) und dann hier fortfahren."
         return 1;;
    403) warn "Der Account @$ADMIN_USER ist kein Server-Admin — bitte mit einem Admin-Account versuchen."; return 1;;
    *)   warn "Anlegen fehlgeschlagen (HTTP $code${emsg:+ — $emsg})."; return 1;;
  esac
}

phase4_matrix() {
  bold "Phase 4/7 — Matrix-Anbindung"
  local pw_tries=0
  while true; do
    try_login
    [ -n "$TOKEN" ] && break
    case "${LOGIN_ERRCODE:-}" in
      M_LIMIT_EXCEEDED)
        local wait_s=$(( ${LOGIN_RETRY_MS:-2000} / 1000 + 1 ))
        warn "Zu viele Anmeldeversuche — der Server bittet um ${wait_s}s Pause. Ich warte…"
        sleep "$wait_s";;
      M_USER_DEACTIVATED)
        warn "Der Account @$BOT_USER:$SERVER_NAME wurde deaktiviert und kann sich nicht anmelden."
        local NEU; ask_yesno NEU "Anderen Bot-Benutzernamen eingeben?" "ja"
        [ "$NEU" = "ja" ] || die "Ohne funktionierenden Bot-Account geht es nicht weiter."
        ask_loop BOT_USER "Bot-Benutzername (nur der Name)" "" v_localpart
        ask_secret BOT_PW "Passwort für @$BOT_USER:$SERVER_NAME"
        pw_tries=0;;
      *)
        pw_tries=$((pw_tries+1))
        if [ $pw_tries -lt 3 ]; then
          warn "Anmeldung fehlgeschlagen (${LOGIN_ERRMSG:-Benutzername oder Passwort falsch})."
          echo "  Tipp: Passwort in Ruhe neu eintippen — Groß/Klein beachten."
          ask_secret BOT_PW "Passwort für @$BOT_USER:$SERVER_NAME (erneut)"
        else
          warn "3× fehlgeschlagen. Entweder ist das Passwort falsch — oder den Account gibt es noch nicht."
          local WEG
          ask_yesno WEG "Existiert der Bot-Account noch NICHT und soll jetzt angelegt werden?" "nein"
          if [ "$WEG" = "ja" ]; then
            admin_create_user && pw_tries=0 || {
              ask_loop BOT_USER "Bot-Benutzername (nur der Name)" "$BOT_USER" v_localpart
              ask_secret BOT_PW "Passwort für @$BOT_USER:$SERVER_NAME"
              pw_tries=0
            }
          else
            ask_loop BOT_USER "Bot-Benutzername (nur der Name)" "$BOT_USER" v_localpart
            ask_secret BOT_PW "Passwort für @$BOT_USER:$SERVER_NAME"
            pw_tries=0
          fi
        fi;;
    esac
  done
  # Token-Verifikation (whoami): erst jetzt gilt die Anmeldung als bestanden
  local WHO; WHO=$(mx GET "$HS/_matrix/client/v3/account/whoami" "" "$TOKEN" | jget "['user_id']")
  [ -n "$WHO" ] || die "Anmeldung unerwartet ungültig (whoami leer) — bitte erneut ausführen."
  ok "Angemeldet als $WHO — Zugang geprüft"
  # Branding: Der Bot heißt für den Nutzer IMMER „Operator" — egal wie der Account heißt.
  local ENCW; ENCW=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$WHO")
  mx PUT "$HS/_matrix/client/v3/profile/$ENCW/displayname" '{"displayname":"Operator"}' "$TOKEN" >/dev/null \
    && ok "Anzeigename auf »Operator« gesetzt" || warn "Anzeigename konnte nicht gesetzt werden"

  # Bestehenden gemeinsamen Raum suchen (exakter Mitglieds-Abgleich, kein Substring)
  ROOM=""
  local R ENC
  for R in $(mx GET "$HS/_matrix/client/v3/joined_rooms" "" "$TOKEN" | python3 -c "import json,sys;[print(r) for r in json.load(sys.stdin).get('joined_rooms',[])]"); do
    ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$R")
    if mx GET "$HS/_matrix/client/v3/rooms/$ENC/joined_members" "" "$TOKEN" \
       | python3 -c "import json,sys;d=json.load(sys.stdin);sys.exit(0 if sys.argv[1] in d.get('joined',{}) else 1)" "$HUMAN" 2>/dev/null; then
      ROOM=$R; break
    fi
  done
  if [ -n "$ROOM" ]; then
    ok "Bestehender gemeinsamer Raum gefunden: $ROOM"
    # Branding nachziehen: hieß der Raum noch „Claude" (Altbestand) oder gar nichts,
    # wird er auf „Operator" umbenannt. Einen eigenen Wunschnamen des Nutzers lassen wir stehen.
    local ENCR CURNAME
    ENCR=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$ROOM")
    CURNAME=$(mx GET "$HS/_matrix/client/v3/rooms/$ENCR/state/m.room.name" "" "$TOKEN" | jget "['name']")
    if [ -z "$CURNAME" ] || [ "$CURNAME" = "Claude" ]; then
      mx PUT "$HS/_matrix/client/v3/rooms/$ENCR/state/m.room.name" '{"name":"Operator"}' "$TOKEN" >/dev/null \
        && ok "Raum heißt jetzt »Operator«" || true
    fi
  else
    ROOM=$(mx POST "$HS/_matrix/client/v3/createRoom" \
      "{\"is_direct\":true,\"invite\":[\"$HUMAN\"],\"preset\":\"trusted_private_chat\",\"name\":\"Operator\"}" "$TOKEN" | jget "['room_id']")
    [ -n "$ROOM" ] || die "Raum konnte nicht erstellt werden — Log/Verbindung prüfen und Skript erneut starten (deine Antworten bleiben gespeichert)."
    # Einladung wirklich zugestellt? (fängt kaputte Föderation/falsche ID ab)
    local ENCR ENCH MEMB tmp code
    ENCR=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$ROOM")
    ENCH=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$HUMAN")
    tmp=$(mktemp)
    code=$(mx2 GET "$HS/_matrix/client/v3/rooms/$ENCR/state/m.room.member/$ENCH" "" "$TOKEN" "$tmp")
    MEMB=$(jget "['membership']" < "$tmp"); rm -f "$tmp"
    if [ "$MEMB" = "invite" ] || [ "$MEMB" = "join" ]; then
      ok "Neuer Raum erstellt und Einladung an $HUMAN zugestellt — in deiner Matrix-App annehmen!"
    else
      warn "Raum wurde erstellt, aber die Einladung an $HUMAN konnte nicht bestätigt werden."
      warn "Prüfe: Ist die Matrix-ID korrekt? Ist der Server von $HS aus erreichbar (Föderation)?"
    fi
  fi
}

phase5_files() {
  bold "Phase 5/7 — Dateien einrichten"
  mkdir -p "$BOT_DIR"
  SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" 2>/dev/null && pwd)
  local F A DEST
  for F in listener.py send.py memory.py skills.py sessions.py cron_runner.py redact.py reid.py \
           dienst_start.py pruefung.py diagnose.py migrate_tokens.py vaultwarden.py platform_compat.py secretstore.py servicemgr.py providers.py persona.py matrix_room.py dock_fenster.py update_verify.py update_pubkey.txt sandbox.py claude_health.py throttle.py retention.py permission_broker.py claude_tool_hook.py net_guard.py triggers.py verify_loop.py embeddings.py skillguard.py updater.py audit_log.py; do
    if [ -f "$SCRIPT_DIR/$F" ]; then cp "$SCRIPT_DIR/$F" "$BOT_DIR/$F"
    else curl -fsSL "$REPO_RAW/$F" -o "$BOT_DIR/$F" || die "$F weder lokal noch unter $REPO_RAW gefunden"; fi
    ok "$F installiert"
  done
  # VERSION mitliefern (fürs Self-Update #64; manifest.json/updates.json holt der Updater live)
  if [ -f "$SCRIPT_DIR/VERSION" ]; then cp "$SCRIPT_DIR/VERSION" "$BOT_DIR/VERSION"
  else curl -fsSL "$REPO_RAW/VERSION" -o "$BOT_DIR/VERSION" 2>/dev/null || true; fi
  # Update-Quelle hinterlegen: der Updater (updater.py) aktualisiert aus DERSELBEN Quelle,
  # aus der installiert wurde (GitHub bei Website-Installationen, Gitea intern bei uns).
  printf '%s' "$REPO_RAW" > "$BOT_DIR/repo_raw.txt"
  mkdir -p "$WORKSPACE/.claude/agents"
  local AGENTS="recherche schreiber"
  [ "$BASH_OPTIN" = "ja" ] && AGENTS="$AGENTS sysadmin"
  for A in $AGENTS; do
    DEST="$WORKSPACE/.claude/agents/$A.md"
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
sys.path.insert(0, bot)
import platform_compat
p = os.path.join(platform_compat.workspace(), ".mcp.json")
data = {"mcpServers": {}}
if os.path.exists(p):
    try: data = json.load(open(p))
    except ValueError: pass
data.setdefault("mcpServers", {})["m365"] = {"command": venv_py, "args": [os.path.join(bot, "mcp_m365.py")]}
data["mcpServers"]["n8n"] = {"command": venv_py, "args": [os.path.join(bot, "mcp_n8n.py")]}
# Microsoft Learn: oeffentliche Doku-Suche, kein Konto, kein Schluessel, keine Lizenz (#120).
# Deshalb ab Werk an — der Operator raet dann nicht mehr ueber Microsoft-Themen.
sys.path.insert(0, os.path.join(bot, "dashboard"))
import mcp_catalog
data["mcpServers"].setdefault("learn", dict(mcp_catalog.LEARN_ENTRY))
open(p, "w").write(json.dumps(data, indent=1))
PYMCP
  ok "Standard-MCPs m365 + n8n + learn registriert"
  mkdir -p "$WORKSPACE/.claude/skills"
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
  # Matrix-Token in den OS-Secret-Store; Datei enthält nur den Marker.
  # Token + Referenz laufen via ENV (nie argv → nicht in ps sichtbar).
  TOKEN_REF="keychain"
  secret_set "matrix-owner" "$TOKEN"; secret_has "matrix-owner" \
    || { warn "Secret-Store nicht verfügbar — Token bleibt in der Datei (0600)"; TOKEN_REF="$TOKEN"; }
  OP_CRED_PATH="$BOT_DIR/credentials.json" OP_HS="$HS" OP_UID="@$BOT_USER:$SERVER_NAME" \
  OP_TOKEN_REF="$TOKEN_REF" OP_ROOM="$ROOM" OP_HUMAN="$HUMAN" OP_TOOLS="$ALLOWED_TOOLS" OP_CLAUDE="$CLAUDE_BIN" \
  python3 <<'PY'
import json, os
e = os.environ
open(e["OP_CRED_PATH"], "w").write(json.dumps({
    "homeserver": e["OP_HS"], "user_id": e["OP_UID"], "access_token": e["OP_TOKEN_REF"],
    "room_id": e["OP_ROOM"], "owner_id": e["OP_HUMAN"], "allowed_tools": json.loads(e["OP_TOOLS"]),
    "claude_bin": e["OP_CLAUDE"]}, indent=1))
os.chmod(e["OP_CRED_PATH"], 0o600)
PY
  ok "credentials.json geschrieben"
  python3 "$BOT_DIR/migrate_tokens.py" 2>/dev/null || true
}

phase6_start() {
  bold "Phase 6/7 — Listener-Dienst starten"
  install_service listener "$(command -v python3)" "$BOT_DIR/listener.py" \
    && ok "Listener-Dienst eingerichtet und gestartet" || die "Dienststart fehlgeschlagen — siehe $BOT_DIR/listener.log"
  sleep 3
  tail -1 "$BOT_DIR/listener.log" 2>/dev/null | grep -q "Listener gestartet" \
    && ok "Daemon läuft und lauscht" || warn "Daemon gestartet, Log noch leer — später prüfen: tail -f $BOT_DIR/listener.log"
}

# Browser für den Agenten (nur zum Surfen — NICHT der Browser, mit dem du das Dashboard
# öffnest; das ist immer dein normaler Standardbrowser). Playwright bringt für manche
# Architekturen kein eigenes Chromium mit (z. B. ARM-Linux/Raspberry Pi) — dort nutzen wir
# das System-Chromium und merken uns den Pfad in browser_path.txt.
install_agent_browser() {
  if "$DASH_DIR/venv/bin/playwright" install chromium >/dev/null 2>&1; then
    ok "Browser für den Agenten eingerichtet"
    return 0
  fi
  local sysb=""
  for c in chromium chromium-browser google-chrome google-chrome-stable; do
    command -v "$c" >/dev/null && { sysb=$(command -v "$c"); break; }
  done
  if [ -n "$sysb" ]; then
    printf '%s' "$sysb" > "$BOT_DIR/browser_path.txt"
    ok "Browser für den Agenten: bereits vorhandenes Chromium wird mitbenutzt ($sysb)"
    return 0
  fi
  warn "Kein Browser zum Surfen gefunden — der Agent kann vorerst keine Webseiten öffnen."
  if [ "$OS" = Linux ]; then
    warn "Chromium nachinstallieren (empfohlen):  sudo apt install chromium"
  fi
  warn "Alles andere — Chat, Dashboard, Aufgaben — funktioniert davon unabhängig."
}

phase8_dashboard() {
  bold "Phase 8 — Web-Dashboard"
  [ "$DASH_OPTIN" = "ja" ] || { warn "Dashboard übersprungen (in Phase 3 abgewählt)"; return 0; }
  if [ "${VENV_POSSIBLE:-1}" = 0 ]; then
    PYV=$(python3 -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
    warn "Dashboard übersprungen (python-venv fehlt). Nachholen: sudo apt install python${PYV}-venv,"
    warn "danach dieses Skript erneut ausführen — der Bot läuft schon."
    return 0
  fi
  DASH_DIR="$BOT_DIR/dashboard"
  mkdir -p "$DASH_DIR/static" "$BOT_DIR/connections" "$BOT_DIR/secrets"
  chmod 700 "$BOT_DIR/secrets"
  # Fortschritt in Prozent — dieselben Marken wie in install.ps1 (Wächter-Test).
  step() { printf '  [%3d%%] %s\n' "$1" "$2"; }
  step 5 "Python-Umgebung anlegen ..."
  local VENV_PY="$DASH_DIR/venv/bin/python3" DASH_OK=1 F
  # Selbstheilung: eine venv vom letzten Versuch wird neu gebaut, wenn sie unvollständig
  # ist (kein pip) ODER mit einem zu alten Python (<3.10) erstellt wurde — sonst schlagen
  # die Paket-Installationen (mcp/fastapi brauchen 3.10+) jedes Mal wieder fehl.
  if [ -d "$DASH_DIR/venv" ]; then
    local vver
    vver=$("$DASH_DIR/venv/bin/python3" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null || echo 0)
    if [ ! -x "$DASH_DIR/venv/bin/pip" ] || [ "${vver:-0}" -lt 310 ]; then
      warn "Python-Umgebung vom letzten Versuch ist unvollständig oder zu alt — baue sie neu."
      rm -rf "$DASH_DIR/venv"
    fi
  fi
  if [ ! -x "$VENV_PY" ]; then
    if ! "${PY_VENV:-python3}" -m venv "$DASH_DIR/venv" 2>/dev/null; then
      DASH_OK=0
      warn "Python-Umgebung fürs Dashboard konnte nicht erstellt werden."
      warn "Siehe Hinweis aus Phase 1 (Python 3.10+ bzw. venv-Paket) — danach Skript erneut ausführen."
    fi
  fi
  if [ "$DASH_OK" = "1" ]; then
    step 12 "Paketwerkzeug aktualisieren ..."
    "$DASH_DIR/venv/bin/pip" install -q --upgrade pip 2>/dev/null
    step 18 "Bausteine laden — der längste Schritt, je nach Netz mehrere Minuten ..."
    "$DASH_DIR/venv/bin/pip" install -q "fastapi==0.116.*" "uvicorn==0.35.*" \
      "msal==1.33.*" "cryptography==45.*" "requests==2.32.*" "mcp==1.*" "starlette<0.49" \
      "openai>=1.40" "playwright>=1.40" "pypdf" "fido2>=1.1" "presidio-analyzer" "presidio-anonymizer" "Faker" "pytest" || DASH_OK=0
    step 55 "Deutsches Sprachmodell für den Datenschutz-Filter laden (ca. 500 MB) ..."
    "$DASH_DIR/venv/bin/pip" install -q "https://github.com/explosion/spacy-models/releases/download/de_core_news_lg-3.8.0/de_core_news_lg-3.8.0-py3-none-any.whl" \
      || warn "Deutsches Sprachmodell konnte nicht geladen werden — Pseudonymisierung meldet sich beim ersten Einsatz"
    step 75 "Browser für den Agenten einrichten ..."
    install_agent_browser
    "$VENV_PY" "$BOT_DIR/migrate_sessions.py" 2>/dev/null || true
  fi
  if [ "$DASH_OK" = "1" ]; then
    step 85 "Dashboard-Dateien einrichten ..."
    for F in server.py tokens.py agents_store.py m365_setup.py google_auth.py open.py mcp_catalog.py; do
      if [ -f "$SCRIPT_DIR/dashboard/$F" ]; then cp "$SCRIPT_DIR/dashboard/$F" "$DASH_DIR/$F"
      else curl -fsSL "$REPO_RAW/dashboard/$F" -o "$DASH_DIR/$F" || DASH_OK=0; fi
    done
    # #87: Die Sicherheitspruefungen gehoeren zum Produkt, nicht nur ins Repo —
    # sonst kann niemand nachpruefen, was wir versprechen. conftest.py isoliert
    # den Lauf (#89), damit er die laufende Installation nicht anfasst.
    for F in test_dashboard.py test_petra.py conftest.py; do
      if [ -f "$SCRIPT_DIR/dashboard/$F" ]; then cp "$SCRIPT_DIR/dashboard/$F" "$DASH_DIR/$F"
      else curl -fsSL "$REPO_RAW/dashboard/$F" -o "$DASH_DIR/$F" || warn "$F fehlt — Selbsttest im Dashboard nicht verfuegbar"; fi
    done
    for F in index.html app.js style.css; do
      if [ -f "$SCRIPT_DIR/dashboard/static/$F" ]; then cp "$SCRIPT_DIR/dashboard/static/$F" "$DASH_DIR/static/$F"
      else curl -fsSL "$REPO_RAW/dashboard/static/$F" -o "$DASH_DIR/static/$F" || DASH_OK=0; fi
    done
    for F in m365.py gdrive.py mcp_m365.py vault.py mcp_n8n.py pseudonym.py pseudonym_daemon.py migrate_sessions.py llm_runner.py mail_watch.py; do
      if [ -f "$SCRIPT_DIR/$F" ]; then cp "$SCRIPT_DIR/$F" "$BOT_DIR/$F"
      else curl -fsSL "$REPO_RAW/$F" -o "$BOT_DIR/$F" || DASH_OK=0; fi
    done
  fi
  if [ "$DASH_OK" = "1" ]; then
    secret_has "token-key" || secret_set "token-key" "$(rand_hex)"
    if [ ! -f "$BOT_DIR/dashboard.json" ]; then
      local DTOK; DTOK=$(rand_hex); secret_set "dashboard-token" "$DTOK"
      OP_DTOK="$DTOK" OP_BOT="$BOT_DIR" python3 <<'PY'
import hashlib, json, os
tok, bot = os.environ["OP_DTOK"], os.environ["OP_BOT"]
open(os.path.join(bot, "dashboard.json"), "w").write(json.dumps(
    {"port": 8737, "token_sha256": hashlib.sha256(tok.encode()).hexdigest(), "version": 1}, indent=1))
os.chmod(os.path.join(bot, "dashboard.json"), 0o600)
PY
    fi
    step 92 "Dienste registrieren ..."
    install_service dashboard "$VENV_PY" "$DASH_DIR/server.py" \
      && { DASH_RUNNING=1; ok "Dashboard läuft"; } \
      || warn "Dashboard-Start fehlgeschlagen — Bot läuft trotzdem (Log: $BOT_DIR/dashboard.log)"
    # Datenschutz-Filter (#116): startet AUS. Bewusst so — er braucht ein großes
    # Sprachmodell und System-Bibliotheken, die nicht auf jedem Rechner da sind.
    # Läuft er nicht, blockiert er sonst JEDE Nachricht (fail-safe by design), und
    # der Kunde steht mit einem Operator da, der nichts tut. Stattdessen: erst
    # läuft alles, dann bietet der Operator selbst an, ihn einzuschalten — und
    # hilft bei Problemen, weil er dann schon antworten kann.
    python3 -c "
import json, os
p = os.path.expanduser('~/.claude/matrix-bot/dashboard.json')
try:
    d = json.load(open(p))
except Exception:
    d = {}
d.setdefault('pseudonymize', {}).setdefault('enabled', False)
json.dump(d, open(p, 'w'), indent=1)" 2>/dev/null || true
    install_service pseudonym "$VENV_PY" "$BOT_DIR/pseudonym_daemon.py" >/dev/null 2>&1 || true
    ok "Datenschutz-Filter vorbereitet — dein Operator bietet dir gleich an, ihn einzuschalten"
    # FIDO-Sicherheitsschlüssel (Linux): EINE Frage statt Copy-Paste — wir richten die
    # udev-Regel selbst ein (sudo fragt einmal nach dem Nutzer-Passwort)
    # #109: Semantisches Gedächtnis. Ohne Embedding-Modell findet der Operator Fakten
    # nur über exakte Wörter — das faellt niemandem auf, weil es fail-open weiterlaeuft.
    # Darum EINE Frage, wenn Ollama ohnehin da ist.
    if command -v ollama >/dev/null && ! ollama list 2>/dev/null | grep -q nomic-embed-text; then
      local EMB_SETUP
      ask_yesno EMB_SETUP "Gedaechtnis verbessern? (dein Assistent findet Gemerktes dann auch bei anderer Formulierung, ~270 MB, laeuft lokal)" "ja"
      if [ "$EMB_SETUP" = "ja" ]; then
        if ollama pull nomic-embed-text >/dev/null 2>&1; then
          ok "Semantisches Gedaechtnis eingerichtet"
          "$VENV_PY" "$BOT_DIR/memory.py" reindex >/dev/null 2>&1 || true
        else
          warn "Modell konnte nicht geladen werden — das Gedaechtnis sucht weiter nach Woertern."
        fi
      fi
    fi

    # #104-A: Schutzraum unter jedem Agenten-Lauf. macOS bringt ihn mit; unter Linux
    # braucht es bubblewrap. EINE Frage statt Copy-Paste (EINFACHHEIT.md).
    if [ "$OS" = Linux ] && ! command -v bwrap >/dev/null && command -v sudo >/dev/null; then
      local SB_SETUP
      ask_yesno SB_SETUP "Schutzraum einrichten? (verhindert, dass dein Assistent versehentlich ausserhalb seines Arbeitsordners schreibt)" "ja"
      if [ "$SB_SETUP" = "ja" ]; then
        if (command -v apt-get >/dev/null && sudo apt-get install -y bubblewrap >/dev/null 2>&1) \
           || (command -v dnf >/dev/null && sudo dnf install -y bubblewrap >/dev/null 2>&1) \
           || (command -v pacman >/dev/null && sudo pacman -S --noconfirm bubblewrap >/dev/null 2>&1); then
          ok "Schutzraum eingerichtet (bubblewrap)"
        else
          warn "bubblewrap konnte nicht installiert werden — der Operator fragt trotzdem"
          warn "vor riskanten Befehlen nach. Nachruesten: sudo apt install bubblewrap"
        fi
      fi
    fi
    if [ "$OS" = Linux ] && [ ! -f /etc/udev/rules.d/70-operator-fido.rules ] && command -v sudo >/dev/null; then
      local FIDO_SETUP
      ask_yesno FIDO_SETUP "Möchtest du einen Sicherheitsschlüssel (z. B. YubiKey) für den Passwort-Tresor nutzen?" "nein"
      if [ "$FIDO_SETUP" = "ja" ]; then
        if echo 'KERNEL=="hidraw*", SUBSYSTEM=="hidraw", MODE="0660", TAG+="uaccess"' \
             | sudo tee /etc/udev/rules.d/70-operator-fido.rules >/dev/null \
           && sudo udevadm control --reload-rules && sudo udevadm trigger; then
          ok "Sicherheitsschlüssel-Zugriff eingerichtet"
        else
          warn "Einrichtung nicht möglich — der Tresor funktioniert trotzdem (Master-Passwort)"
        fi
      fi
    fi
  else
    warn "Dashboard-Installation unvollständig — der Chat-Bot läuft davon unabhängig weiter"
  fi
  step 100 "Fertig."
}

# Kurzbefehl »operator« für den Alltag: Dashboard öffnen, Log, Status, Deinstallation —
# nie wieder lange Pfade kopieren.
install_launcher() {
  mkdir -p "$HOME/.local/bin"
  cat > "$HOME/.local/bin/operator" <<LAUNCH
#!/bin/bash
BOT_DIR="\$HOME/.claude/matrix-bot"
VENV="\$BOT_DIR/dashboard/venv/bin/python3"; [ -x "\$VENV" ] || VENV=python3
case "\${1:-dashboard}" in
  dashboard|"") exec "\$VENV" "\$BOT_DIR/dashboard/open.py";;
  chat)         exec "\$VENV" "\$BOT_DIR/dock_fenster.py" "\${2:-}";;
  log)          exec tail -f "\$BOT_DIR/listener.log";;
  pruefen|check) exec "\$VENV" "\$BOT_DIR/pruefung.py";;
  diagnose)     exec "\$VENV" "\$BOT_DIR/diagnose.py";;
  status)
    if [ "\$(uname)" = Darwin ]; then
      for s in listener dashboard pseudonym; do launchctl print "gui/\$(id -u)/com.the-operator.\$s" >/dev/null 2>&1 && echo "✓ \$s läuft" || echo "✗ \$s gestoppt"; done
    else
      for s in listener dashboard pseudonym; do systemctl --user is-active --quiet "operator-\$s" && echo "✓ \$s läuft" || echo "✗ \$s gestoppt"; done
    fi;;
  uninstall)    u=\$(mktemp) && curl -fsSL "$REPO_RAW/install.sh" -o "\$u" && exec bash "\$u" --uninstall;;
  *) echo "Nutzung: operator [dashboard|chat|log|pruefen|diagnose|status|uninstall]";;
esac
LAUNCH
  chmod +x "$HOME/.local/bin/operator"
  persist_local_bin_path
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ok "Kurzbefehl »operator« eingerichtet";;
    *) ok "Kurzbefehl »operator« eingerichtet (neues Terminal-Fenster öffnen, dann »operator« tippen)";;
  esac
}

phase7_test() {
  bold "Phase 7 — Funktionstest"
  python3 "$BOT_DIR/send.py" "✅ Operator einsatzbereit! Schreib mir einfach — ich antworte in Sekunden." >/dev/null \
    && ok "Testnachricht im Raum — auf dem Handy prüfen!" \
    || warn "Testnachricht fehlgeschlagen — Log prüfen: operator log"
  rm -f "$STATE_FILE" 2>/dev/null || true   # Erfolg → gemerkte Antworten aufräumen
  # Kompakte Übersicht statt Textwand — und das Dashboard öffnet sich gleich von selbst
  echo ""
  bold "══════════════════════════════════════════════════"
  bold " Fertig! 🎉  Dein Operator läuft."
  bold "══════════════════════════════════════════════════"
  echo "   💬  Chat        : schreib ihm in deiner Matrix-App"
  echo "   🖥   Dashboard   : Befehl »operator« (öffnet den Browser)"
  echo "   📜  Log         : operator log       ·  Status: operator status"
  echo "   🗑   Entfernen   : operator uninstall"
  if [ "${CLAUDE_READY:-1}" = 0 ]; then
    echo ""
    warn "WICHTIG: Die Claude-Anmeldung fehlt noch — Nachholen mit: claude /login"
  fi
  echo ""
  if [ "${DASH_RUNNING:-0}" = 1 ]; then
    echo "  Öffne das Dashboard…"
    "$BOT_DIR/dashboard/venv/bin/python3" "$BOT_DIR/dashboard/open.py" >/dev/null 2>&1 \
      || warn "Automatisches Öffnen ging nicht — einfach »operator« tippen"
  fi
}

# ---------------------------------------------------------------- main --
main() {
  if [ "${1:-}" = "--uninstall" ]; then uninstall; exit 0; fi
  phase1_check
  phase2_claude
  phase3_questions
  phase4_matrix
  phase5_files
  phase6_start
  phase8_dashboard
  install_launcher
  phase7_test
}

# Einzige ausführende Zeile — schützt gegen teilweise heruntergeladene Skripte:
# bricht der Download vorher ab, wurde nur definiert, nie ausgeführt.
main "$@"
