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
    catch {
        # Fehler NICHT verschlucken: errcode/error aus der Matrix-Antwort für Diagnose merken
        $script:MatrixErr = $null
        try {
            $raw = $_.ErrorDetails.Message
            if ($raw) { $script:MatrixErr = $raw | ConvertFrom-Json }
        } catch {}
        if (-not $script:MatrixErr) { $script:MatrixErr = @{ errcode = "NETZWERK"; error = $_.Exception.Message } }
        return $null
    }
}

# Frage in Schleife stellen, bis der Validator (ScriptBlock: param($v) → normalisierter Wert oder $null) zufrieden ist
function Ask-Loop($prompt, $default, $validator) {
    while ($true) {
        $suffix = if ($default) { " [$default]" } else { "" }
        $in = Read-Host "$prompt$suffix"
        if (-not $in) { $in = $default }
        $norm = & $validator $in
        if ($null -ne $norm) { return $norm }
    }
}

function Ask-YesNo($prompt, $default) {
    while ($true) {
        $in = Read-Host "$prompt (ja/nein) [$default]"
        if (-not $in) { $in = $default }
        switch -Regex ($in.ToLower()) {
            '^(j|ja|y|yes)$' { return "ja" }
            '^(n|nein|no)$'  { return "nein" }
            default { Write-Host "  Bitte mit ja oder nein antworten." -ForegroundColor Yellow }
        }
    }
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
# Anmeldung wirklich PRÜFEN (wie install.sh) — sonst „gelingt" die Installation und der Bot schweigt
$ClaudeReady = $true
$probe = & claude -p "Antworte nur mit: OK" 2>$null
if ($probe -notmatch "OK") {
    Bold "  Anmeldung bei Claude"
    Write-Host "  Gleich öffnet sich dein Browser. Danach im Claude-Fenster /exit eingeben."
    $attempt = 0
    while ($true) {
        $probe = & claude -p "Antworte nur mit: OK" 2>$null
        if ($probe -match "OK") { break }
        $attempt++
        if ($attempt -gt 3) {
            $ClaudeReady = $false
            Warn "Claude-Anmeldung noch nicht bestätigt - Installation läuft trotzdem weiter."
            Warn "Nachholen: 'claude /login' im Terminal, dann antwortet dein Operator."
            break
        }
        & claude /login
    }
}
if ($ClaudeReady) { Ok "Claude CLI angemeldet und antwortet" }

# ------------------------------------------------------------ Phase 3: FRAGEN
Bold "Phase 3/7 - Konfiguration"
$HsUrl = Ask-Loop "Matrix-Homeserver-URL des Bot-Accounts" "https://matrix.org" {
    param($v)
    if (-not $v) { Warn "Bitte eine Server-Adresse eingeben - Beispiel: https://matrix.org"; return $null }
    if ($v.StartsWith("@")) { Warn "Das sieht wie eine Matrix-ID aus, nicht wie eine Server-Adresse."; return $null }
    if ($v -notmatch '^https?://') { $v = "https://$v" }
    $v = $v.TrimEnd('/')
    if (Matrix GET "$v/_matrix/client/versions" $null $null) { return $v }
    Warn "Unter $v antwortet kein Matrix-Server - Tippfehler? (Beispiel: https://matrix.org)"
    return $null
}
Ok "Homeserver erreichbar: $HsUrl"
$ServerName = ($HsUrl -replace '^https?://','') -replace '/.*$',''
$BotUser = Ask-Loop "Bot-Benutzername (nur der Name, ohne @ und Server)" "" {
    param($v)
    if (-not $v) { Warn "Bitte einen Benutzernamen eingeben (z. B. operator-bot)"; return $null }
    $v = ($v -replace '^@','') -replace ':.*$',''
    $v = $v.ToLower()
    if ($v -notmatch '^[a-z0-9._=/-]+$') { Warn "Erlaubt sind nur Kleinbuchstaben, Zahlen und . _ = - /"; return $null }
    return $v
}
$BotPwSec = Read-Host "Passwort für @${BotUser}:${ServerName}" -AsSecureString
$BotPw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($BotPwSec))
$Human = Ask-Loop "Deine eigene Matrix-ID (nur diese wird beantwortet)" "" {
    param($v)
    if ($v -notmatch '^@.+:.+') { Warn "Eine Matrix-ID sieht so aus: @ich:matrix.org (du hast '$v' getippt)"; return $null }
    $srv = $v -replace '^@[^:]+:',''
    if ($srv -eq $ServerName) {
        # Existenz live prüfen (fängt Tippfehler wie @vmichi ab)
        $enc = [uri]::EscapeDataString($v)
        if (-not (Matrix GET "$HsUrl/_matrix/client/v3/profile/$enc" $null $null)) {
            if ($script:MatrixErr.errcode -eq "M_NOT_FOUND") { Warn "Die Matrix-ID $v gibt es auf diesem Server nicht - Tippfehler?"; return $null }
        }
        return $v
    }
    # anderer Server: existiert er überhaupt? (fängt vmatrix.-Tippfehler ab)
    if (Matrix GET "https://$srv/_matrix/client/versions" $null $null) { return $v }
    Warn "Den Server '$srv' aus deiner Matrix-ID gibt es nicht oder er antwortet nicht - Tippfehler?"
    return $null
}
$BashOptin = Ask-YesNo "Shell-Zugriff erlauben?" "nein"
if ($BashOptin -eq "ja") {
    $AllowedTools = '["Bash", "Read", "WebFetch", "WebSearch", "Agent", "Skill", "mcp__m365", "mcp__n8n"]'
    $ToolsText = "Du darfst Shell-Kommandos ausführen (Bash), Dateien lesen, im Web recherchieren und an deine Agenten delegieren. Unumkehrbares nur nach Rückfrage."
} else {
    $AllowedTools = '["Read", "WebFetch", "WebSearch", "Agent", "Skill", "mcp__m365", "mcp__n8n"]'
    $ToolsText = "Du darfst Dateien lesen, im Web recherchieren und an deine Agenten delegieren. Shell-Zugriff ist NICHT freigegeben."
}

# ------------------------------------------------------------ Phase 4: MATRIX
Bold "Phase 4/7 - Matrix-Anbindung"
$pwTries = 0
while ($true) {
    $pwJson = ($BotPw | ConvertTo-Json)
    $loginBody = "{`"type`":`"m.login.password`",`"identifier`":{`"type`":`"m.id.user`",`"user`":`"$BotUser`"},`"password`":$pwJson,`"initial_device_display_name`":`"Operator Listener`"}"
    $login = Matrix POST "$HsUrl/_matrix/client/v3/login" $loginBody $null
    $Token = $login.access_token
    if ($Token) { break }
    switch ($script:MatrixErr.errcode) {
        "M_LIMIT_EXCEEDED" {
            $waitS = [math]::Ceiling((($script:MatrixErr.retry_after_ms, 2000 | Where-Object { $_ })[0]) / 1000) + 1
            Warn "Zu viele Anmeldeversuche - der Server bittet um ${waitS}s Pause. Ich warte..."
            Start-Sleep -Seconds $waitS
        }
        "M_USER_DEACTIVATED" { Die "Der Account @$BotUser wurde deaktiviert - bitte einen anderen Bot-Account verwenden." }
        default {
            $pwTries++
            if ($pwTries -lt 3) {
                Warn "Anmeldung fehlgeschlagen ($($script:MatrixErr.error)). Passwort in Ruhe neu eintippen."
                $BotPwSec = Read-Host "Passwort für @${BotUser}:${ServerName} (erneut)" -AsSecureString
                $BotPw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($BotPwSec))
            } else {
                Warn "3x fehlgeschlagen. Entweder ist das Passwort falsch - oder den Account gibt es noch nicht."
                Warn "Bot-Account anlegen: auf dem Homeserver registrieren (App/Admin), dann hier fortfahren."
                $BotUser = Ask-Loop "Bot-Benutzername (nur der Name)" $BotUser { param($v) if ($v) { return $v.ToLower() } ; Warn "Bitte einen Namen eingeben"; return $null }
                $BotPwSec = Read-Host "Passwort für @${BotUser}:${ServerName}" -AsSecureString
                $BotPw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($BotPwSec))
                $pwTries = 0
            }
        }
    }
}
# Token-Verifikation (whoami) - erst jetzt gilt die Anmeldung als bestanden
$who = Matrix GET "$HsUrl/_matrix/client/v3/account/whoami" $null $Token
if (-not $who.user_id) { Die "Anmeldung unerwartet ungültig (whoami leer) - bitte erneut ausführen." }
Ok "Angemeldet als $($who.user_id) - Zugang geprüft"
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
$credPath = Join-Path $BotDir "credentials.json"
$creds | ConvertTo-Json | Set-Content $credPath
# Datei-Rechte härten: Vererbung aus, nur der aktuelle Nutzer hat Zugriff
try { icacls $credPath /inheritance:r /grant:r "${env:USERNAME}:F" | Out-Null } catch {}
Ok "credentials.json geschrieben (Zugriff nur für dich)"

# ------------------------------------------------------------- Phase 6: START
Bold "Phase 6/7 - Listener-Dienst"
Install-Service "listener" $Py (Join-Path $BotDir "listener.py")
Ok "Listener-Aufgabe registriert und gestartet (Task Scheduler: $($Tasks.listener))"

# -------------------------------------------------------- Phase 8: DASHBOARD (optional)
Bold "Phase 8 - Web-Dashboard (optional)"
$dashOptin = Ask-YesNo "Web-Dashboard installieren (Agenten-GUI, Tresor, Google/M365)?" "ja"
if ($dashOptin -eq "ja") {
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
