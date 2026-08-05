# =============================================================================
# install.ps1 - Operator: dein Claude-Assistent im Matrix-Chat (Windows)
# Gefuehrte Installation. Idempotent. Keine Adminrechte noetig.
# Aufruf:  irm <RAW-URL>/install.ps1 | iex        (Remote-Ein-Zeiler in PowerShell)
#          .\install.ps1                          (aus geklontem Repo)
#          .\install.ps1 -Uninstall               (alles entfernen)
# =============================================================================
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"
# Ohne das erscheinen Umlaute als "fr" / "geprft" (Windows-Konsole nutzt sonst
# eine Codepage, die unsere UTF-8-Texte falsch deutet).
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

# UTF-8 fuer JEDES Python auf diesem Rechner - auch fuer Unterprozesse (mcp_m365,
# llm_runner), die nicht ueber den Task Scheduler starten. Ohne das: cp1252-Fallen.
try { [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User") } catch {}
$env:PYTHONUTF8 = "1"
$BotDir  = Join-Path $HOME ".claude\matrix-bot"
$DashDir = Join-Path $BotDir "dashboard"
# #106: Arbeitsordner NICHT unter ~/.claude (Claude Code sperrt dort Schreibzugriffe)
$Workspace = if ($env:OPERATOR_WORKSPACE) { $env:OPERATOR_WORKSPACE } else { Join-Path $HOME "Operator" }
# TODO vor GitHub-Publish: Raw-URL auf das GitHub-Repo umstellen
# #131: Der Installer nennt seine eigene Fassung (siehe install.sh).
$InstallerVersion = "1.50.0"
$RepoRaw = if ($env:REPO_RAW) { $env:REPO_RAW } else { "https://raw.githubusercontent.com/TheOperatorAgent/TheOperator/main" }
$Tasks   = @{ listener = "OperatorListener"; dashboard = "OperatorDashboard"; pseudonym = "OperatorPseudonym" }

function Bold($m) { Write-Host $m -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "  [x] $m" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- Python bereitstellen --
# Auf macOS/Linux ist Python immer da - unter Windows oft nicht. Ein Kunde soll EINEN
# Befehl eingeben und fertig sein (EINFACHHEIT.md). Deshalb installiert der Installer
# Python bei Bedarf selbst: eine Ja/Nein-Frage, danach kein Handgriff mehr.
function Test-Python($pfad) {
    # Windows legt unter WindowsApps eine ATTRAPPE namens python.exe ab, die nur den
    # Microsoft Store oeffnet. Sie meldet keine Version - deshalb jeden Kandidaten
    # wirklich AUSFUEHREN statt nur seine Existenz zu pruefen.
    if (-not $pfad) { return $null }
    $ver = ""
    try { $ver = (& $pfad -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null | Out-String).Trim() } catch { return $null }
    if ($ver -match '^(\d+)\.(\d+)$') { return $pfad }
    return $null
}

function Find-Python {
    foreach ($c in @("py", "python", "python3")) {
        $p = Get-Command $c -ErrorAction SilentlyContinue
        if ($p -and (Test-Python $p.Source)) { return $p.Source }
    }
    # Frisch installiertes Python ist im PATH dieser Sitzung noch nicht sichtbar -
    # deshalb zusaetzlich an den ueblichen Orten nachsehen.
    $orte = @()
    foreach ($basis in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles\Python",
                         "${env:ProgramFiles(x86)}\Python", "$env:LOCALAPPDATA\Programs\Python\Launcher")) {
        if (Test-Path $basis) {
            $orte += (Get-ChildItem $basis -Directory -ErrorAction SilentlyContinue |
                      Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName "python.exe" })
        }
    }
    $orte += "$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe"
    foreach ($o in $orte) { if ((Test-Path $o) -and (Test-Python $o)) { return $o } }
    return $null
}

function Update-PathFromRegistry {
    # Nach der Installation kennt die LAUFENDE Sitzung den neuen PATH noch nicht.
    # Ohne diese Auffrischung muesste der Nutzer PowerShell neu oeffnen - genau der
    # Handgriff, den wir ihm ersparen wollen.
    try {
        $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $u = [Environment]::GetEnvironmentVariable("Path", "User")
        $env:Path = (($m, $u) -ne $null) -join ";"
    } catch {}
}

function Install-Python {
    # Weg 1: winget (auf aktuellen Windows-Versionen an Bord, sauberste Loesung)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "  Lade Python herunter und installiere es (dauert 1-2 Minuten) ..."
        try {
            winget install --id Python.Python.3.13 --source winget --silent `
                --accept-package-agreements --accept-source-agreements `
                --scope user 2>$null | Out-Null
        } catch {}
        Update-PathFromRegistry
        $p = Find-Python
        if ($p) { return $p }
    }
    # Weg 2: offizielles Installationsprogramm still ausfuehren (auch ohne winget)
    Write-Host "  Lade Python direkt von python.org ..."
    $arch = if ([Environment]::Is64BitOperatingSystem) { "amd64" } else { "win32" }
    $url  = "https://www.python.org/ftp/python/3.13.1/python-3.13.1-$arch.exe"
    $exe  = Join-Path $env:TEMP "python-setup-operator.exe"
    try {
        Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
        # PrependPath=1 setzt den Suchpfad; InstallLauncherAllUsers=0 vermeidet die
        # Administrator-Abfrage - der Nutzer soll nichts bestaetigen muessen.
        Start-Process -FilePath $exe -Wait -ArgumentList @(
            "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1",
            "Include_test=0", "SimpleInstall=1")
    } catch {
        return $null
    } finally {
        Remove-Item $exe -ErrorAction SilentlyContinue
    }
    Update-PathFromRegistry
    return (Find-Python)
}

function Ensure-Python {
    $p = Find-Python
    if ($p) { return $p }
    Warn "Auf diesem Rechner ist noch kein Python installiert - das braucht dein Operator."
    Write-Host "  (Falls Windows dir gerade den Store angeboten hat: Das ist nur ein"
    Write-Host "   Platzhalter, kein echtes Python.)"
    $ja = Ask-YesNo "Soll ich Python jetzt fuer dich installieren?" "ja"
    if ($ja -ne "ja") {
        Die @"
Ohne Python kann der Operator nicht laufen.
Du kannst es spaeter selbst installieren (https://www.python.org/downloads/,
Haken 'Add python.exe to PATH') und diesen Befehl danach erneut ausfuehren.
"@
    }
    $p = Install-Python
    if (-not $p) {
        Die @"
Die automatische Installation hat nicht geklappt.
So geht es von Hand:
  1. https://www.python.org/downloads/ oeffnen und Python 3 herunterladen
  2. Beim Installieren den Haken 'Add python.exe to PATH' setzen
  3. PowerShell schliessen, neu oeffnen und diesen Befehl erneut ausfuehren
"@
    }
    Ok "Python installiert: $p"
    return $p
}

# Secret-Store ueber secretstore.py (DPAPI). Modul muss in $BotDir liegen.
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
    # Der Ein-Zeiler "irm ... | iex" fuehrt das Skript aus dem Speicher aus - dabei ist
    # $PSScriptRoot LEER, und Join-Path wirft dann einen Fehler. Genau daran ist die
    # Installation am 29.07. in Phase 5 abgebrochen. Lokale Kopie nur pruefen, wenn es
    # ueberhaupt einen Skriptordner gibt.
    if ($PSScriptRoot) {
        $local = Join-Path $PSScriptRoot $rel
        if (Test-Path $local) { Copy-Item $local $dest -Force; return }
    }
    Invoke-WebRequest -Uri "$RepoRaw/$($rel -replace '\\','/')" -OutFile $dest -UseBasicParsing
}

# Dienst als Task-Scheduler-Aufgabe (onlogon, Neustart bei Fehler ~ KeepAlive)
function Install-Service($name, $exe, $scriptPath) {
    $task = $Tasks[$name]
    # FENSTERLOS starten (pythonw/pyw statt python/py). Realer Ausfall (Michi, 30.07.,
    # LastTaskResult 0xC000013A = "Konsole geschlossen"): Der Task Scheduler oeffnete
    # ein sichtbares Konsolenfenster - es sieht aus wie Muell, jemand schliesst es,
    # und der Dienst ist tot. Ohne Fenster gibt es nichts zu schliessen.
    # (Ausgaben landen weiter in den Log-Dateien; die Konsole braucht niemand.)
    $exeDir = Split-Path $exe -Parent
    $exeName = [IO.Path]::GetFileNameWithoutExtension($exe)
    $leise = Join-Path $exeDir ($exeName + "w.exe")
    if (Test-Path $leise) { $exe = $leise }
    # -X utf8: Windows-Python nutzt sonst cp1252 als Datei-Zeichensatz. Der Listener
    # stuerzte damit beim Lesen der VERHALTEN.md (Umlaute!) ab - der Operator hat auf
    # Windows NIE auf echte Nachrichten geantwortet (Michi, 30.07.). Ein Schalter,
    # alle Dienste kuriert.
    # Ueber den Start-Mantel starten: er protokolliert AUSNAHMSLOS jeden Fehlstart
    # (auch Import-/Syntaxfehler) nach <name>-start.log. Ohne ihn stirbt ein Dienst
    # unter pythonw voellig spurlos - genau das hat den 30.07. gekostet.
    $mantel = Join-Path $BotDir "dienst_start.py"
    if (Test-Path $mantel) {
        $action = New-ScheduledTaskAction -Execute $exe -Argument "-X utf8 `"$mantel`" `"$scriptPath`""
    } else {
        $action = New-ScheduledTaskAction -Execute $exe -Argument "-X utf8 `"$scriptPath`""
    }
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
        # Fehler NICHT verschlucken: errcode/error aus der Matrix-Antwort fuer Diagnose merken
        $script:MatrixErr = $null
        try {
            $raw = $_.ErrorDetails.Message
            if ($raw) { $script:MatrixErr = $raw | ConvertFrom-Json }
        } catch {}
        if (-not $script:MatrixErr) { $script:MatrixErr = @{ errcode = "NETZWERK"; error = $_.Exception.Message } }
        return $null
    }
}

# Frage in Schleife stellen, bis der Validator (ScriptBlock: param($v) -> normalisierter Wert oder $null) zufrieden ist
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
    Secret-Del "token-key"; Ok "Secret-Store-Schluessel geloescht"
    $ans = Read-Host "Verzeichnis $BotDir komplett loeschen (inkl. Gedaechtnis + Tokens)? (ja/nein)"
    if ($ans -eq "ja") { Remove-Item -Recurse -Force $BotDir; Ok "Dateien geloescht" } else { Warn "Dateien behalten" }
    Bold "Fertig."; exit 0
}

# ------------------------------------------------------------ Phase 1: PRUeFEN
# 8-Bit-Startbild - dieselben Zeilen wie in install.sh (Waechter-Test prueft die
# Gleichheit). Reines ASCII, Farben ueber Write-Host (laeuft auch in PowerShell 5.1,
# wo ANSI-Codes nicht sicher funktionieren). Zu schmales Fenster -> schlichte Zeile.
function Banner {
    $rows = @(
        ' ###  ####  ##### ####   ###  #####  ###  #### ',
        '#   # #   # #     #   # #   #   #   #   # #   #',
        '#   # ####  ####  ####  #####   #   #   # #### ',
        '#   # #     #     #  #  #   #   #   #   # #  # ',
        ' ###  #     ##### #   # #   #   #    ###  #   #'
    )
    $w = 80; try { $w = $Host.UI.RawUI.WindowSize.Width } catch {}
    if ($w -lt 54) { Bold "OPERATOR"; return }
    foreach ($r in $rows) { Write-Host ("  " + $r) -ForegroundColor Magenta }
    Write-Host ("  " + "-----------------------------------------------") -ForegroundColor DarkGray
    Write-Host "  > your operator inside the matrix_" -ForegroundColor Green
    Write-Host ("  Installer " + $InstallerVersion) -ForegroundColor DarkGray
    Write-Host ""
}
Banner
Bold "Operator-Installation (Windows)"
Bold "Phase 1/7 - Voraussetzungen"
# Python bei Bedarf selbst installieren - der Kunde soll nur EINEN Befehl eingeben.
# (Steht hier und nicht oben, weil Ask-YesNo erst weiter oben im Skript definiert wird.)
$Py = Ensure-Python
Ok "Python: $Py ($((& $Py --version 2>&1 | Out-String).Trim()))"

# ------------------------------------------------------------ Phase 2: CLAUDE
Bold "Phase 2/7 - Claude CLI"
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Warn "Claude CLI nicht gefunden - installiere..."
    if (Get-Command npm -ErrorAction SilentlyContinue) { npm install -g @anthropic-ai/claude-code }
    else { try { irm https://claude.ai/install.ps1 | iex } catch { Die "Claude-CLI-Installation fehlgeschlagen - npm oder Installer noetig" } }
}
# STARTBARE Variante bevorzugen. npm legt claude (Shell-Skript, fuer Unix),
# claude.cmd und claude.ps1 nebeneinander. Get-Command liefert auf Windows gern die
# .ps1 - die landete in credentials.json (claude_bin) und JEDER Modell-Aufruf starb
# mit "WinError 193: %1 ist keine zulaessige Win32-Anwendung" (Michi, 30.07.).
# Der Fix in platform_compat.claude_bin() greift NICHT, weil credentials.json Vorrang hat.
$ClaudeBin = $null
foreach ($n in @("claude.cmd", "claude.exe", "claude.bat")) {
    $c = Get-Command $n -ErrorAction SilentlyContinue
    if ($c) { $ClaudeBin = $c.Source; break }
}
if (-not $ClaudeBin) { $ClaudeBin = (Get-Command claude -ErrorAction SilentlyContinue).Source }
if (-not $ClaudeBin) { Die "claude nicht im PATH - PowerShell neu oeffnen und erneut ausfuehren" }
Ok "Claude CLI: $ClaudeBin"
# Anmeldung wirklich PRUeFEN (wie install.sh) - sonst "gelingt" die Installation und der Bot schweigt.
# WICHTIG: mit Zeitlimit. Realer Haenger (Michis Windows, 30.07.): "claude -p" wollte
# interaktiv etwas fragen (abgelaufene Anmeldung) und wartete endlos auf Eingabe -
# der Installer stand ohne jede Meldung. Ein Installer darf NIE stumm haengen.
function Claude-Probe([int]$TimeoutSec) {
    $out = Join-Path $env:TEMP ("op_probe_" + [guid]::NewGuid().ToString("N") + ".txt")
    $cmd = Join-Path $env:TEMP ("op_probe_" + [guid]::NewGuid().ToString("N") + ".ps1")
    # Leerer stdin, damit ein fragendes claude nie auf Eingabe hoffen kann; eigener
    # Prozess, damit wir nach Ablauf hart abbrechen koennen statt mitzuhaengen.
    Set-Content -Path $cmd -Value "'' | claude -p 'Antworte nur mit: OK' 2>`$null | Set-Content -LiteralPath '$out'" -Encoding ASCII
    $p = Start-Process powershell -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$cmd) -WindowStyle Hidden -PassThru
    if (-not $p.WaitForExit($TimeoutSec * 1000)) { try { $p.Kill() } catch {} }
    $r = ""
    if (Test-Path $out) { $r = [string](Get-Content -LiteralPath $out -Raw -ErrorAction SilentlyContinue) }
    Remove-Item $out, $cmd -ErrorAction SilentlyContinue
    return $r
}
$ClaudeReady = $true
Write-Host "  Pruefe die Claude-Anmeldung - kann bis zu einer Minute dauern ..."
$probe = Claude-Probe 75
if ($probe -notmatch "OK") {
    Bold "  Anmeldung bei Claude"
    Write-Host "  Claude hat nicht geantwortet - vermutlich ist die Anmeldung abgelaufen."
    Write-Host "  Gleich oeffnet sich die Anmeldung. Danach im Claude-Fenster /exit eingeben."
    $attempt = 0
    while ($true) {
        $attempt++
        if ($attempt -gt 3) {
            $ClaudeReady = $false
            Warn "Claude-Anmeldung noch nicht bestaetigt - Installation laeuft trotzdem weiter."
            Warn "Nachholen: 'claude' im Terminal starten, anmelden, /exit - dann antwortet dein Operator."
            break
        }
        & claude /login
        Write-Host "  Pruefe erneut - kann bis zu einer Minute dauern ..."
        $probe = Claude-Probe 75
        if ($probe -match "OK") { break }
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
$BotPwSec = Read-Host "Passwort fuer @${BotUser}:${ServerName}" -AsSecureString
$BotPw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($BotPwSec))
$Human = Ask-Loop "Deine eigene Matrix-ID (nur diese wird beantwortet)" "" {
    param($v)
    if ($v -notmatch '^@.+:.+') { Warn "Eine Matrix-ID sieht so aus: @ich:matrix.org (du hast '$v' getippt)"; return $null }
    $srv = $v -replace '^@[^:]+:',''
    if ($srv -eq $ServerName) {
        # Existenz live pruefen (faengt Tippfehler wie @vmichi ab)
        $enc = [uri]::EscapeDataString($v)
        if (-not (Matrix GET "$HsUrl/_matrix/client/v3/profile/$enc" $null $null)) {
            if ($script:MatrixErr.errcode -eq "M_NOT_FOUND") { Warn "Die Matrix-ID $v gibt es auf diesem Server nicht - Tippfehler?"; return $null }
        }
        return $v
    }
    # anderer Server: existiert er ueberhaupt? (faengt vmatrix.-Tippfehler ab)
    if (Matrix GET "https://$srv/_matrix/client/versions" $null $null) { return $v }
    Warn "Den Server '$srv' aus deiner Matrix-ID gibt es nicht oder er antwortet nicht - Tippfehler?"
    return $null
}
$BashOptin = Ask-YesNo "Shell-Zugriff erlauben?" "nein"
if ($BashOptin -eq "ja") {
    $AllowedTools = '["Bash", "Read", "WebFetch", "WebSearch", "Agent", "Skill", "mcp__m365", "mcp__n8n", "mcp__learn"]'
    $ToolsText = "Du darfst Shell-Kommandos ausfuehren (Bash), Dateien lesen, im Web recherchieren und an deine Agenten delegieren. Unumkehrbares nur nach Rueckfrage."
} else {
    $AllowedTools = '["Read", "WebFetch", "WebSearch", "Agent", "Skill", "mcp__m365", "mcp__n8n", "mcp__learn"]'
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
                $BotPwSec = Read-Host "Passwort fuer @${BotUser}:${ServerName} (erneut)" -AsSecureString
                $BotPw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($BotPwSec))
            } else {
                Warn "3x fehlgeschlagen. Entweder ist das Passwort falsch - oder den Account gibt es noch nicht."
                Warn "Bot-Account anlegen: auf dem Homeserver registrieren (App/Admin), dann hier fortfahren."
                $BotUser = Ask-Loop "Bot-Benutzername (nur der Name)" $BotUser { param($v) if ($v) { return $v.ToLower() } ; Warn "Bitte einen Namen eingeben"; return $null }
                $BotPwSec = Read-Host "Passwort fuer @${BotUser}:${ServerName}" -AsSecureString
                $BotPw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($BotPwSec))
                $pwTries = 0
            }
        }
    }
}
# Token-Verifikation (whoami) - erst jetzt gilt die Anmeldung als bestanden
$who = Matrix GET "$HsUrl/_matrix/client/v3/account/whoami" $null $Token
if (-not $who.user_id) { Die "Anmeldung unerwartet ungueltig (whoami leer) - bitte erneut ausfuehren." }
Ok "Angemeldet als $($who.user_id) - Zugang geprueft"
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
New-Item -ItemType Directory -Force -Path $BotDir, "$Workspace\.claude\agents", "$Workspace\.claude\skills", "$BotDir\connections", "$BotDir\secrets" | Out-Null
$core = @("listener.py","send.py","memory.py","skills.py","sessions.py","cron_runner.py","redact.py","reid.py","pii_vorfilter.py",
          "migrate_tokens.py","vaultwarden.py","platform_compat.py","secretstore.py","servicemgr.py","providers.py","matrix_room.py","dock_fenster.py","update_verify.py","update_pubkey.txt","sandbox.py", "claude_health.py", "raumwaechter.py", "throttle.py", "retention.py", "permission_broker.py", "claude_tool_hook.py", "net_guard.py","persona.py","triggers.py","verify_loop.py","embeddings.py","skillguard.py","updater.py","audit_log.py")
foreach ($f in $core) { Fetch-File $f (Join-Path $BotDir $f); Ok "$f" }
try { Fetch-File "VERSION" (Join-Path $BotDir "VERSION") } catch {}   # Self-Update #64
# Update-Quelle hinterlegen: Updater zieht aus derselben Quelle wie die Installation
Set-Content -Path (Join-Path $BotDir "repo_raw.txt") -Value $RepoRaw -NoNewline
$agents = @("recherche","schreiber"); if ($BashOptin -eq "ja") { $agents += "sysadmin" }
foreach ($a in $agents) {
    $dest = Join-Path $Workspace ".claude\agents\$a.md"
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
if (-not (Secret-Has "matrix-owner")) { Warn "Secret-Store nicht verfuegbar - Token bleibt in der Datei"; $TokenRef = $Token }
$creds = @{ homeserver=$HsUrl; user_id="@${BotUser}:${ServerName}"; access_token=$TokenRef; room_id=$Room;
           owner_id=$Human; allowed_tools=($AllowedTools|ConvertFrom-Json); claude_bin=$ClaudeBin }
$credPath = Join-Path $BotDir "credentials.json"
$creds | ConvertTo-Json | Set-Content $credPath
# Datei-Rechte haerten: Vererbung aus, nur der aktuelle Nutzer hat Zugriff
try { icacls $credPath /inheritance:r /grant:r "${env:USERNAME}:F" | Out-Null } catch {}
Ok "credentials.json geschrieben (Zugriff nur fuer dich)"

# ------------------------------------------------------------- Phase 6: START
Bold "Phase 6/7 - Listener-Dienst"
Install-Service "listener" $Py (Join-Path $BotDir "listener.py")
Ok "Listener-Aufgabe registriert und gestartet (Task Scheduler: $($Tasks.listener))"

# -------------------------------------------------------- Phase 8: DASHBOARD (optional)
Bold "Phase 8 - Web-Dashboard (optional)"
$dashOptin = Ask-YesNo "Web-Dashboard installieren (Agenten-GUI, Tresor, Google/M365)?" "ja"
if ($dashOptin -eq "ja") {
    # Fortschritt in Prozent (Michi, 30.07.): nach dem "ja" liefen pip und das
    # Sprachmodell minutenlang OHNE jede Ausgabe - der Nutzer weiss nicht, was los
    # ist. Prozent = abgeschlossene Schritte, ehrlich mit Dauer-Hinweis beim langen.
    function Step($pct, $msg) { Write-Host ("  [{0,3}%] {1}" -f $pct, $msg) -ForegroundColor DarkCyan }
    Step 5 "Python-Umgebung anlegen ..."
    New-Item -ItemType Directory -Force -Path "$DashDir\static" | Out-Null
    $VenvPy = Join-Path $DashDir "venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPy)) { & $Py -m venv (Join-Path $DashDir "venv") }
    # Die kompilierten Teile von numpy/spacy brauchen die Microsoft-Visual-C++-Laufzeit.
    # Auf frischem Windows fehlt sie - dann scheitert der Datenschutz-Filter mit
    # "DLL load failed while importing numpy_ops" (Michis Rechner, 29.07.).
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install --id Microsoft.VCRedist.2015+.x64 --source winget --silent `
                --accept-package-agreements --accept-source-agreements 2>$null | Out-Null
        } catch {}
    }
    # WICHTIG: pip immer als "python.exe -m pip" aufrufen, NIE als pip.exe.
    # Realer Abbruch (Michis Windows, 30.07.): "pip.exe install --upgrade pip" bricht mit
    # "ERROR: To modify pip, please run the following command: ...python.exe -m pip install
    # --upgrade pip" ab - Windows kann die laufende pip.exe nicht ersetzen. Damit stand die
    # Installation in Phase 8 und der Kunde hatte kein Dashboard.
    Step 12 "Paketwerkzeug aktualisieren ..."
    & $VenvPy -m pip install -q --upgrade pip
    Step 18 "Bausteine laden - der laengste Schritt, je nach Netz mehrere Minuten ..."
    & $VenvPy -m pip install -q "fastapi==0.116.*" "uvicorn==0.35.*" "msal==1.33.*" "cryptography==45.*" `
        "requests==2.32.*" "mcp==1.*" "starlette<0.49" "openai>=1.40" "playwright>=1.40" "pypdf" "fido2>=1.1" "presidio-analyzer" "presidio-anonymizer" "Faker" "pytest"
    Step 55 "Deutsches Sprachmodell fuer den Datenschutz-Filter laden (ca. 500 MB) ..."
    try { & $VenvPy -m pip install -q "https://github.com/explosion/spacy-models/releases/download/de_core_news_lg-3.8.0/de_core_news_lg-3.8.0-py3-none-any.whl" }
    catch { Warn "Deutsches Sprachmodell nicht geladen - Pseudonymisierung meldet sich beim ersten Einsatz" }
    # Browser fuer den Agenten (nur zum Surfen - das Dashboard oeffnest du weiter mit deinem
    # normalen Standardbrowser). Schlaegt der Download fehl, nutzen wir ein vorhandenes
    # Chrome/Chromium/Edge und merken uns den Pfad in browser_path.txt.
    Step 75 "Browser fuer den Agenten einrichten ..."
    $pwexe = Join-Path $DashDir "venv\Scripts\playwright.exe"
    $browserOk = $false
    if (Test-Path $pwexe) {
        & $pwexe install chromium 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { Ok "Browser fuer den Agenten eingerichtet"; $browserOk = $true }
    }
    if (-not $browserOk) {
        $cands = @(
            "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
            "$env:ProgramFiles\Chromium\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe")
        $found = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($found) {
            Set-Content -Path (Join-Path $BotDir "browser_path.txt") -Value $found -NoNewline
            Ok "Browser fuer den Agenten: vorhandener Browser wird mitbenutzt ($found)"
        } else {
            Warn "Kein Browser zum Surfen gefunden - der Agent kann vorerst keine Webseiten oeffnen."
            Warn "Chrome oder Chromium installieren, danach dieses Skript erneut ausfuehren."
            Warn "Alles andere - Chat, Dashboard, Aufgaben - funktioniert davon unabhaengig."
        }
    }
    Step 85 "Dashboard-Dateien einrichten ..."
    foreach ($f in @("server.py","tokens.py","agents_store.py","m365_setup.py","google_auth.py","open.py","mcp_catalog.py")) { Fetch-File "dashboard\$f" (Join-Path $DashDir $f) }
    # #87: Sicherheitspruefungen mitliefern (siehe install.sh).
    foreach ($f in @("test_dashboard.py","test_petra.py","conftest.py")) { Fetch-File "dashboard\$f" (Join-Path $DashDir $f) }
    foreach ($f in @("index.html","app.js","style.css","dock.js","dock.html")) { Fetch-File "dashboard\static\$f" (Join-Path $DashDir "static\$f") }
    foreach ($f in @("dienst_start.py","pruefung.py","diagnose.py","abnahme.py","m365.py","gdrive.py","mcp_m365.py","vault.py","mcp_n8n.py","pseudonym.py","pseudonym_daemon.py","migrate_sessions.py","llm_runner.py","mcp_client.py","schleuse.py","werkzeuge.py","anbieter.py","kern.py","protokoll.py","mcp_rechte.py","merker.py","anhaenge.py","mail_watch.py")) { Fetch-File $f (Join-Path $BotDir $f) }
    if (-not (Secret-Has "token-key")) { Secret-Set "token-key" (Rand-Hex) }
    if (-not (Test-Path (Join-Path $BotDir "dashboard.json"))) {
        $dtok = Rand-Hex; Secret-Set "dashboard-token" $dtok
        $env:OP_DTOK = $dtok
        & $Py -c "import hashlib,json,os;open(r'$BotDir\dashboard.json','w').write(json.dumps({'port':8737,'token_sha256':hashlib.sha256(os.environ['OP_DTOK'].encode()).hexdigest(),'version':1},indent=1))"
        Remove-Item Env:OP_DTOK -ErrorAction SilentlyContinue
    }
    Step 92 "Dienste registrieren ..."
    Install-Service "dashboard" $VenvPy (Join-Path $DashDir "server.py"); Ok "Dashboard-Aufgabe registriert"
    # Datenschutz-Filter (#116): startet AUS. Bewusst so - er braucht ein grosses
    # Sprachmodell und System-Bibliotheken, die nicht auf jedem Rechner da sind
    # (auf frischem Windows fehlt z. B. die Visual-C++-Laufzeit). Laeuft er nicht,
    # blockiert er sonst JEDE Nachricht (fail-safe by design) - und der Kunde steht
    # mit einem Operator da, der nichts tut. Stattdessen: erst laeuft alles, dann
    # bietet der Operator selbst an, ihn einzuschalten, und hilft bei Problemen.
    & $Py -c "import json;p=r'$BotDir\dashboard.json';d=json.load(open(p));d.setdefault('pseudonymize',{}).setdefault('enabled',False);json.dump(d,open(p,'w'),indent=1)"
    Install-Service "pseudonym" $VenvPy (Join-Path $BotDir "pseudonym_daemon.py")
    Ok "Datenschutz-Filter vorbereitet - dein Operator bietet dir gleich an, ihn einzuschalten"
    # Standard-MCPs registrieren (#120). Fehlte auf Windows komplett: die Werkzeuge
    # mcp__m365 / mcp__n8n standen in der Erlaubnisliste, waren aber nie eingetragen -
    # der Operator hatte auf Windows also gar keine Microsoft-365-Werkzeuge.
    # learn = oeffentliche Microsoft-Doku, kein Konto, kein Schluessel, keine Lizenz.
    $McpPy = @'
import json, os, sys
bot = sys.argv[1]
venv_py = os.path.join(bot, "dashboard", "venv", "Scripts", "python.exe")
sys.path.insert(0, bot); sys.path.insert(0, os.path.join(bot, "dashboard"))
import platform_compat, mcp_catalog
p = os.path.join(platform_compat.workspace(), ".mcp.json")
data = {"mcpServers": {}}
if os.path.exists(p):
    try: data = json.load(open(p))
    except ValueError: pass
s = data.setdefault("mcpServers", {})
s["m365"] = {"command": venv_py, "args": [os.path.join(bot, "mcp_m365.py")]}
s["n8n"] = {"command": venv_py, "args": [os.path.join(bot, "mcp_n8n.py")]}
s.setdefault("learn", dict(mcp_catalog.LEARN_ENTRY))
os.makedirs(os.path.dirname(p), exist_ok=True)
open(p, "w").write(json.dumps(data, indent=1))
'@
    $McpTmp = Join-Path $env:TEMP ("op_mcp_" + [guid]::NewGuid().ToString("N") + ".py")
    Set-Content -Path $McpTmp -Value $McpPy -Encoding ASCII
    try { & $VenvPy $McpTmp $BotDir; Ok "Standard-MCPs m365 + n8n + learn registriert" }
    catch { Warn "Standard-MCPs konnten nicht registriert werden - im Dashboard unter System nachtragen" }
    finally { Remove-Item $McpTmp -ErrorAction SilentlyContinue }
    # Kurzbefehl 'operator' - auf macOS/Linux gab es ihn laengst, auf Windows fehlte er
    # komplett (Michi, 30.07.: "operator : Die Benennung wurde nicht als Name eines Cmdlet
    # erkannt"). Die Entsperr-Karte im Dashboard nennt ihn aber - also muss er da sein.
    $BinDir = Join-Path $env:LOCALAPPDATA "Operator\bin"
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $launcher = @"
@echo off
setlocal
set BOT=%USERPROFILE%\.claude\matrix-bot
set PY=%BOT%\dashboard\venv\Scripts\python.exe
if not exist "%PY%" set PY=py
if "%1"=="" goto dashboard
if /i "%1"=="dashboard" goto dashboard
if /i "%1"=="chat" goto chat
if /i "%1"=="log" goto log
if /i "%1"=="status" goto status
if /i "%1"=="pruefen" goto pruefen
if /i "%1"=="diagnose" goto diagnose
if /i "%1"=="abnahme" goto abnahme
if /i "%1"=="check" goto pruefen
if /i "%1"=="stop" goto stop
if /i "%1"=="start" goto start
if /i "%1"=="neustart" goto neustart
if /i "%1"=="uninstall" goto uninstall
echo Nutzung: operator [dashboard^|chat^|log^|pruefen^|diagnose^|abnahme^|status^|stop^|start^|neustart^|uninstall]
goto :eof
:dashboard
"%PY%" "%BOT%\dashboard\open.py"
goto :eof
:chat
"%PY%" "%BOT%\dock_fenster.py" %2
goto :eof
:log
powershell -NoProfile -Command "Get-Content -Wait -Tail 40 '%BOT%\listener.log'"
goto :eof
:pruefen
"%PY%" "%BOT%\pruefung.py"
goto :eof
:diagnose
"%PY%" "%BOT%\diagnose.py"
goto :eof
:abnahme
"%PY%" "%BOT%\abnahme.py"
goto :eof
:status
"%PY%" -c "import sys;sys.path.insert(0,r'%BOT%');import servicemgr as s;[print(('[ok] ' if s.status(d) else '[--] laeuft nicht: ')+d) for d in ('listener','dashboard','pseudonym')]"
goto :eof
:stop
schtasks /end /tn OperatorListener >nul 2>&1
schtasks /end /tn OperatorDashboard >nul 2>&1
schtasks /end /tn OperatorPseudonym >nul 2>&1
echo Operator gestoppt. Starten mit: operator start
goto :eof
:start
schtasks /run /tn OperatorListener >nul 2>&1
schtasks /run /tn OperatorDashboard >nul 2>&1
schtasks /run /tn OperatorPseudonym >nul 2>&1
echo Operator gestartet.
goto :eof
:neustart
call "%~f0" stop
call "%~f0" start
goto :eof

:uninstall
powershell -NoProfile -Command "irm $RepoRaw/install.ps1 -OutFile `$env:TEMP\op_uninstall.ps1; & `$env:TEMP\op_uninstall.ps1 -Uninstall"
goto :eof
"@
    Set-Content -Path (Join-Path $BinDir "operator.cmd") -Value $launcher -Encoding ASCII
    # Dauerhaft in den PATH des Nutzers (nicht des Systems - kein Admin noetig)
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$BinDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User")
        Ok "Kurzbefehl 'operator' eingerichtet (neues PowerShell-Fenster oeffnen, dann 'operator' tippen)"
    } else {
        Ok "Kurzbefehl 'operator' eingerichtet"
    }
    $env:Path = "$env:Path;$BinDir"
    Ok "Dashboard oeffnen mit:  operator    (oder: $VenvPy $DashDir\open.py)"
    # #124: Die Installation endet mit einem OFFENEN, entsperrten Dashboard - kein
    # Befehl zum Abtippen, keine Entsperr-Karte (open.py haengt den Dauer-Token an).
    # Fehler hier sind NIE fatal: die Installation ist trotzdem gelungen.
    Step 100 "Fertig."
    Write-Host "  Dein Dashboard oeffnet sich gleich im Browser ..."
    Start-Sleep -Seconds 4
    try { & $VenvPy (Join-Path $DashDir "open.py") | Out-Null } catch {}
}

# -------------------------------------------------------------- Phase 7: TEST
Bold "Phase 7 - Funktionstest"
# send.py gibt die Matrix-Kennung der Nachricht aus (beginnt mit $). Die sah im Installer
# wie ein durchgesickertes Geheimnis aus (Michi, 30.07.) - deshalb wegwerfen.
try { & $Py (Join-Path $BotDir "send.py") "Operator einsatzbereit auf Windows! Schreib mir einfach." | Out-Null; Ok "Testnachricht im Raum - auf dem Handy pruefen!" }
catch { Warn "Testnachricht fehlgeschlagen - Log pruefen: $BotDir\listener.log" }
Bold "Fertig!  Deinstallation:  .\install.ps1 -Uninstall"
