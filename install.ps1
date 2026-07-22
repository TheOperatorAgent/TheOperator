# =============================================================================
# install.ps1 — Operator: dein Claude-Assistent im Matrix-Chat (Windows)
# Geführte Installation. Idempotent. Keine Adminrechte nötig.
# Aufruf:  irm <RAW-URL>/install.ps1 | iex        (Remote-Ein-Zeiler in PowerShell)
#          .\install.ps1                          (aus geklontem Repo)
#          .\install.ps1 -Uninstall               (alles entfernen)
# =============================================================================
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"

$BotDir  = Join-Path $HOME ".claude\matrix-bot"
$DashDir = Join-Path $BotDir "dashboard"
# TODO vor GitHub-Publish: Raw-URL auf das GitHub-Repo umstellen
$RepoRaw = if ($env:REPO_RAW) { $env:REPO_RAW } else { "http://192.168.178.53:3000/root/the-operator/raw/branch/main" }
$Tasks   = @{ listener = "OperatorListener"; dashboard = "OperatorDashboard"; pseudonym = "OperatorPseudonym" }

function Bold($m) { Write-Host $m -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "  [x] $m" -ForegroundColor Red; exit 1 }

# Python-Launcher finden (py bevorzugt, sonst python)
function Get-Py {
    foreach ($c in @("py", "python", "python3")) {
        $p = Get-Command $c -ErrorAction SilentlyContinue
        if ($p) { return $p.Source }
    }
    Die "Python nicht gefunden — installiere Python 3 von https://python.org (Haken 'Add to PATH')"
}
$Py = Get-Py

# Secret-Store über secretstore.py (DPAPI). Modul muss in $BotDir liegen.
function Secret-Set($account, $value) {
    $env:OP_SS_VAL = $value
    & $Py -c "import sys,os;sys.path.insert(0,r'$BotDir');import secretstore;secretstore.set(sys.argv[1],os.environ['OP_SS_VAL'])" $account 2>$null
    Remove-Item Env:OP_SS_VAL -ErrorAction SilentlyContinue
}
function Secret-Has($account) {
    & $Py -c "import sys;sys.path.insert(0,r'$BotDir');import secretstore;sys.exit(0 if secretstore.get(sys.argv[1]) else 1)" $account 2>$null
    return ($LASTEXITCODE -eq 0)
}
function Secret-Del($account) {
    & $Py -c "import sys;sys.path.insert(0,r'$BotDir');import secretstore;secretstore.delete(sys.argv[1])" $account 2>$null
}
function Rand-Hex { & $Py -c "import secrets;print(secrets.token_hex(32))" }

# Datei holen: lokal aus dem Skriptordner, sonst vom Repo
function Fetch-File($rel, $dest) {
    $local = Join-Path $PSScriptRoot $rel
    if (Test-Path $local) { Copy-Item $local $dest -Force }
    else { Invoke-WebRequest -Uri "$RepoRaw/$($rel -replace '\\','/')" -OutFile $dest -UseBasicParsing }
}

# Dienst als Task-Scheduler-Aufgabe (onlogon, Neustart bei Fehler ≈ KeepAlive)
function Install-Service($name, $exe, $scriptPath) {
    $task = $Tasks[$name]
    $action  = New-ScheduledTaskAction -Execute $exe -Argument "`"$scriptPath`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $task
}

function Matrix($method, $url, $body, $token) {
    $headers = @{ "Content-Type" = "application/json" }
    if ($token) { $headers["Authorization"] = "Bearer $token" }
    try { return Invoke-RestMethod -Method $method -Uri $url -Headers $headers -Body $body }
    catch { return $null }
}

# ---------------------------------------------------------------- Uninstall --
if ($Uninstall) {
    Bold "Deinstallation"
    foreach ($t in $Tasks.Values) {
        try { Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue } catch {}
        try { Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue; Ok "$t entfernt" } catch {}
    }
    if (Test-Path (Join-Path $BotDir "credentials.json")) {
        $c = Get-Content -Raw (Join-Path $BotDir "credentials.json") | ConvertFrom-Json
        $tok = $c.access_token
        if ($tok -eq "keychain") { $tok = & $Py -c "import sys;sys.path.insert(0,r'$BotDir');import secretstore;print(secretstore.get('matrix-owner') or '')" 2>$null }
        if ($tok) { Matrix POST "$($c.homeserver)/_matrix/client/v3/logout" "{}" $tok | Out-Null; Ok "Matrix-Token invalidiert" }
    }
    Secret-Del "token-key"; Ok "Secret-Store-Schlüssel gelöscht"
    $ans = Read-Host "Verzeichnis $BotDir komplett löschen (inkl. Gedächtnis + Tokens)? (ja/nein)"
    if ($ans -eq "ja") { Remove-Item -Recurse -Force $BotDir; Ok "Dateien gelöscht" } else { Warn "Dateien behalten" }
    Bold "Fertig."; exit 0
}

# ------------------------------------------------------------ Phase 1: PRÜFEN
Bold "Operator-Installation (Windows) — your operator inside the Matrix"
Bold "Phase 1/7 - Voraussetzungen"
Ok "Python: $Py ($(& $Py --version))"

# ------------------------------------------------------------ Phase 2: CLAUDE
Bold "Phase 2/7 - Claude CLI"
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Warn "Claude CLI nicht gefunden - installiere..."
    if (Get-Command npm -ErrorAction SilentlyContinue) { npm install -g @anthropic-ai/claude-code }
    else { try { irm https://claude.ai/install.ps1 | iex } catch { Die "Claude-CLI-Installation fehlgeschlagen - npm oder Installer nötig" } }
}
$ClaudeBin = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $ClaudeBin) { Die "claude nicht im PATH - PowerShell neu öffnen und erneut ausführen" }
Ok "Claude CLI: $ClaudeBin"
Warn "Falls noch nicht angemeldet: in einem Terminal 'claude /login' ausführen (Browser-Login)."

# ------------------------------------------------------------ Phase 3: FRAGEN
Bold "Phase 3/7 - Konfiguration"
$HsUrl = Read-Host "Matrix-Homeserver-URL des Bot-Accounts (z. B. https://matrix.org)"
$HsUrl = $HsUrl.TrimEnd('/')
if (-not (Matrix GET "$HsUrl/_matrix/client/versions" $null $null)) { Die "Homeserver $HsUrl nicht erreichbar" }
Ok "Homeserver erreichbar"
$ServerName = $HsUrl -replace '^https?://',''
$BotUser = Read-Host "Bot-Benutzername (localpart, ohne @ und Server)"
if (-not $BotUser) { Die "Bot-Benutzername wird benötigt" }
$BotPwSec = Read-Host "Passwort für @${BotUser}:${ServerName}" -AsSecureString
$BotPw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($BotPwSec))
$Human = Read-Host "Deine eigene Matrix-ID (nur diese wird beantwortet, z. B. @ich:matrix.org)"
if ($Human -notmatch '^@.+:.+') { Die "Bitte vollständige Matrix-ID angeben (@name:server)" }
$BashOptin = Read-Host "Shell-Zugriff erlauben? (ja/nein) [nein]"
if ($BashOptin -eq "ja") {
    $AllowedTools = '["Bash", "Read", "WebFetch", "WebSearch", "Agent", "Skill", "mcp__m365", "mcp__n8n"]'
    $ToolsText = "Du darfst Shell-Kommandos ausführen (Bash), Dateien lesen, im Web recherchieren und an deine Agenten delegieren. Unumkehrbares nur nach Rückfrage."
} else {
    $AllowedTools = '["Read", "WebFetch", "WebSearch", "Agent", "Skill", "mcp__m365", "mcp__n8n"]'
    $ToolsText = "Du darfst Dateien lesen, im Web recherchieren und an deine Agenten delegieren. Shell-Zugriff ist NICHT freigegeben."
}

# ------------------------------------------------------------ Phase 4: MATRIX
Bold "Phase 4/7 - Matrix-Anbindung"
$pwJson = ($BotPw | ConvertTo-Json)
$loginBody = "{`"type`":`"m.login.password`",`"identifier`":{`"type`":`"m.id.user`",`"user`":`"$BotUser`"},`"password`":$pwJson,`"initial_device_display_name`":`"Operator Listener`"}"
$login = Matrix POST "$HsUrl/_matrix/client/v3/login" $loginBody $null
$Token = $login.access_token
if (-not $Token) { Die "Login als @$BotUser fehlgeschlagen - Bot-User anlegen (Admin) oder Passwort prüfen. Details siehe README." }
Ok "Matrix-Token erzeugt"
$Room = $null
$joined = Matrix GET "$HsUrl/_matrix/client/v3/joined_rooms" $null $Token
foreach ($r in $joined.joined_rooms) {
    $enc = [uri]::EscapeDataString($r)
    $members = Matrix GET "$HsUrl/_matrix/client/v3/rooms/$enc/joined_members" $null $Token
    if ($members.joined -and $members.joined.PSObject.Properties.Name -contains $Human) { $Room = $r; break }
}
if ($Room) { Ok "Bestehender gemeinsamer Raum: $Room" }
else {
    $createBody = "{`"is_direct`":true,`"invite`":[`"$Human`"],`"preset`":`"trusted_private_chat`",`"name`":`"Claude`"}"
    $Room = (Matrix POST "$HsUrl/_matrix/client/v3/createRoom" $createBody $Token).room_id
    if (-not $Room) { Die "Raum konnte nicht erstellt werden" }
    Ok "Neuer Raum erstellt: $Room - Einladung in deiner Matrix-App annehmen!"
}

# ----------------------------------------------------------- Phase 5: DATEIEN
Bold "Phase 5/7 - Dateien einrichten"
New-Item -ItemType Directory -Force -Path $BotDir, "$BotDir\workspace\.claude\agents", "$BotDir\workspace\.claude\skills", "$BotDir\connections", "$BotDir\secrets" | Out-Null
$core = @("listener.py","send.py","memory.py","skills.py","sessions.py","cron_runner.py","redact.py","reid.py",
          "migrate_tokens.py","vaultwarden.py","platform_compat.py","secretstore.py","servicemgr.py")
foreach ($f in $core) { Fetch-File $f (Join-Path $BotDir $f); Ok "$f" }
$agents = @("recherche","schreiber"); if ($BashOptin -eq "ja") { $agents += "sysadmin" }
foreach ($a in $agents) {
    $dest = Join-Path $BotDir "workspace\.claude\agents\$a.md"
    if (-not (Test-Path $dest)) { Fetch-File "agents\$a.md" $dest; Ok "Agent $a" }
}
# VERHALTEN.md aus Template
$verh = Join-Path $BotDir "VERHALTEN.md"
if (-not (Test-Path $verh)) {
    $tmpl = Join-Path $BotDir ".template.tmp"; Fetch-File "VERHALTEN.template.md" $tmpl
    (Get-Content -Raw $tmpl).Replace("{{BOT_MXID}}","@${BotUser}:${ServerName}").Replace("{{HUMAN_MXID}}",$Human).Replace("{{TOOLS_SECTION}}",$ToolsText) | Set-Content -NoNewline $verh
    Remove-Item $tmpl; Ok "VERHALTEN.md aus Template erstellt"
}
# Matrix-Token in DPAPI-Secret-Store
$TokenRef = "keychain"
Secret-Set "matrix-owner" $Token
if (-not (Secret-Has "matrix-owner")) { Warn "Secret-Store nicht verfügbar - Token bleibt in der Datei"; $TokenRef = $Token }
$creds = @{ homeserver=$HsUrl; user_id="@${BotUser}:${ServerName}"; access_token=$TokenRef; room_id=$Room;
           owner_id=$Human; allowed_tools=($AllowedTools|ConvertFrom-Json); claude_bin=$ClaudeBin }
$creds | ConvertTo-Json | Set-Content (Join-Path $BotDir "credentials.json")
Ok "credentials.json geschrieben"

# ------------------------------------------------------------- Phase 6: START
Bold "Phase 6/7 - Listener-Dienst"
Install-Service "listener" $Py (Join-Path $BotDir "listener.py")
Ok "Listener-Aufgabe registriert und gestartet (Task Scheduler: $($Tasks.listener))"

# -------------------------------------------------------- Phase 8: DASHBOARD (optional)
Bold "Phase 8 - Web-Dashboard (optional)"
$dashOptin = Read-Host "Web-Dashboard installieren (Agenten-GUI, Tresor, Google/M365)? (ja/nein) [ja]"
if ($dashOptin -ne "nein") {
    New-Item -ItemType Directory -Force -Path "$DashDir\static" | Out-Null
    $VenvPy = Join-Path $DashDir "venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPy)) { & $Py -m venv (Join-Path $DashDir "venv") }
    $pip = Join-Path $DashDir "venv\Scripts\pip.exe"
    & $pip install -q --upgrade pip
    & $pip install -q "fastapi==0.116.*" "uvicorn==0.35.*" "msal==1.33.*" "cryptography==45.*" `
        "requests==2.32.*" "mcp==1.*" "starlette<0.49" "fido2>=1.1" "presidio-analyzer" "presidio-anonymizer" "Faker"
    try { & $pip install -q "https://github.com/explosion/spacy-models/releases/download/de_core_news_lg-3.8.0/de_core_news_lg-3.8.0-py3-none-any.whl" }
    catch { Warn "Deutsches Sprachmodell nicht geladen - Pseudonymisierung meldet sich beim ersten Einsatz" }
    foreach ($f in @("server.py","tokens.py","agents_store.py","m365_setup.py","google_auth.py","open.py")) { Fetch-File "dashboard\$f" (Join-Path $DashDir $f) }
    foreach ($f in @("index.html","app.js","style.css")) { Fetch-File "dashboard\static\$f" (Join-Path $DashDir "static\$f") }
    foreach ($f in @("m365.py","gdrive.py","mcp_m365.py","vault.py","mcp_n8n.py","pseudonym.py","pseudonym_daemon.py","migrate_sessions.py")) { Fetch-File $f (Join-Path $BotDir $f) }
    if (-not (Secret-Has "token-key")) { Secret-Set "token-key" (Rand-Hex) }
    if (-not (Test-Path (Join-Path $BotDir "dashboard.json"))) {
        $dtok = Rand-Hex; Secret-Set "dashboard-token" $dtok
        $env:OP_DTOK = $dtok
        & $Py -c "import hashlib,json,os;open(r'$BotDir\dashboard.json','w').write(json.dumps({'port':8737,'token_sha256':hashlib.sha256(os.environ['OP_DTOK'].encode()).hexdigest(),'version':1},indent=1))"
        Remove-Item Env:OP_DTOK -ErrorAction SilentlyContinue
    }
    Install-Service "dashboard" $VenvPy (Join-Path $DashDir "server.py"); Ok "Dashboard-Aufgabe registriert"
    Install-Service "pseudonym" $VenvPy (Join-Path $BotDir "pseudonym_daemon.py"); Ok "Pseudonym-Daemon-Aufgabe registriert"
    Ok "Dashboard öffnen mit:  $VenvPy $DashDir\open.py"
}

# -------------------------------------------------------------- Phase 7: TEST
Bold "Phase 7 - Funktionstest"
try { & $Py (Join-Path $BotDir "send.py") "Operator einsatzbereit auf Windows! Schreib mir einfach."; Ok "Testnachricht im Raum - auf dem Handy prüfen!" }
catch { Warn "Testnachricht fehlgeschlagen - Log prüfen: $BotDir\listener.log" }
Bold "Fertig!  Deinstallation:  .\install.ps1 -Uninstall"
