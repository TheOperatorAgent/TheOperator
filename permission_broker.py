#!/usr/bin/env python3
"""Operator — Permission Broker (#65, stdlib-only).

Fragt VOR riskanten Aktionen im Matrix-Chat nach — in einfacher Sprache, mit
klarem Ja/Nein. Sichere Arbeit läuft ohne jede Unterbrechung weiter.

Warum der Umlauf hier passiert und nicht im Listener:
    Während `claude -p` läuft, steckt der Listener-Thread in `subprocess.run` fest
    und kann KEINE neuen Chat-Nachrichten lesen. Der Broker (aufgerufen aus dem
    PreToolUse-Hook, also innerhalb des Claude-Laufs) erledigt Frage und Antwort
    deshalb selbst über die Matrix-API.

Sicherheitsleitplanken:
  * **fail-closed** — keine Antwort, Zeitablauf, Matrix nicht erreichbar → NEIN.
  * Nur der Owner darf entscheiden; fremde Sender werden ignoriert.
  * Nur Antworten NACH der Frage zählen (kein „ja" von vorhin gilt weiter).
  * Jede Freigabe ist an einen Fingerabdruck der konkreten Argumente gebunden und
    wird genau einmal verbraucht (Replay-Schutz).
"""
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

BOT_DIR = os.environ.get("OPERATOR_BOT_DIR", os.path.expanduser("~/.claude/matrix-bot"))
CONSUMED_FILE = os.path.join(BOT_DIR, "run", "permissions.json")
# Antworten, die schon als Freigabe/Ablehnung gezählt haben. Der Listener liest die
# Liste und behandelt diese Nachrichten NICHT nochmal als normalen Chat — sonst
# antwortet der Operator nach jedem »ja« noch zusammenhanglos hinterher.
REPLIES_FILE = os.path.join(BOT_DIR, "run", "broker_replies.json")
WAIT_SECONDS = 180          # so lange wartet der Broker auf deine Antwort
POLL_SECONDS = 3

# ---------------------------------------------------------------- Rückfrage-Stufen (#127) --
# Michi, 30.07.: »dieses fragen nervt … bitte deaktiviere es«. Ganz abschalten geht
# nicht — »OHNE DEIN JA PASSIERT NICHTS« ist Sicherheitskarte 1 auf der Website, und
# ein Schalter, der die Zusage still aufhebt, macht aus dem Versprechen eine Lüge.
#
# Der Kompromiss: drei Stufen, die NUR die unterste Regel betreffen (fail-closed für
# unbekannte Befehle). Was in JEDER Stufe fragt und nie abwählbar ist:
#   * die Sperrliste DESTRUCTIVE_CMD (löschen, sudo, formatieren, Skript aus dem Netz …)
#   * Schreibzugriff in den eigenen Programmordner (Update-Quelle, Signaturschlüssel,
#     der Broker selbst — wer hier schreibt, hebelt ALLES aus)
#   * Schreiben außerhalb des Arbeitsordners
#   * Adressen im eigenen Heimnetz (die werden gar nicht erst gefragt, sondern gesperrt)
STUFEN = ("streng", "normal", "locker")
STUFEN_TEXT = {
    "streng": "Fragt zusätzlich vor jedem Abruf einer Webseite. Für sehr sensible Rechner.",
    "normal": "Fragt bei riskanten Befehlen und bei allem, was ich nicht als harmlos kenne. "
              "Empfohlen.",
    "locker": "Fragt nur noch bei wirklich gefährlichen Befehlen und bei Zugriffen auf meine "
              "eigenen Sicherheitsdateien. Unbekannte, aber harmlos aussehende Befehle laufen "
              "durch — schneller, aber du siehst weniger.",
}


def stufe():
    """Aktuelle Rückfrage-Stufe aus dashboard.json. Unbekannter Wert → »normal«:
    ein Tippfehler in der Konfiguration darf nie zur schwächsten Einstellung führen."""
    try:
        with open(os.path.join(BOT_DIR, "dashboard.json"), encoding="utf-8") as f:
            wert = str(json.load(f).get("rueckfragen", "normal")).strip().lower()
    except (OSError, ValueError, AttributeError):
        return "normal"
    return wert if wert in STUFEN else "normal"

# ---------------------------------------------------------------- Risiko-Einstufung --
# Bewusst eng gefasst: Nur was wirklich Schaden anrichten oder nach außen wirken kann.
# Alles andere läuft ohne Nachfrage — sonst nervt der Operator (Petra-Test).
DESTRUCTIVE_CMD = [
    (r"\brm\s+(-\w+\s+)*(-[rf]\w*)", "Dateien löschen"),
    (r"\bsudo\b", "Administrator-Rechte"),
    (r"\bmkfs\b|\bdiskutil\s+(erase|partition)", "Datenträger formatieren"),
    (r"\bdd\s+[^|]*of=/dev/", "direkt auf ein Laufwerk schreiben"),
    (r"\b(shutdown|reboot|halt)\b", "Rechner herunterfahren/neu starten"),
    (r"\b(launchctl|systemctl)\s+(unload|disable|stop|remove)", "Dienste abschalten"),
    (r"\bkillall\b|\bkill\s+-9\b", "Programme hart beenden"),
    (r"\bchmod\s+(-R\s+)?[0-7]{3,4}\s+/", "Rechte im System ändern"),
    (r"\bchown\b", "Eigentümer ändern"),
    (r"\bgit\s+push\b.*(--force|-f)\b", "Git-Historie überschreiben"),
    (r"\bcurl\b[^|]*\|\s*(bash|sh|zsh)", "Skript aus dem Netz ausführen"),
    (r"\bnpm\s+publish\b|\bpip\s+install\b.*--index-url", "Paket veröffentlichen/fremde Quelle"),
    (r">\s*/etc/|>\s*/System/|>\s*/Library/", "Systemdateien überschreiben"),
    # Security-Review 29.07. — bekannte Umgehungen derselben Absichten:
    (r"\bfind\b.*\s-delete\b", "Dateien löschen (find)"),
    (r"\b(wget|fetch)\b[^|]*\|\s*(bash|sh|zsh)", "Skript aus dem Netz ausführen"),
    (r"\bbase64\b[^|]*\|\s*(bash|sh|zsh)", "kodiertes Skript ausführen"),
    (r"\b(sh|bash|zsh)\s+-c\b.*\b(rm|curl|wget)\b", "Befehl in Unter-Shell verstecken"),
    (r"\bpython3?\s+-c\b.*\b(rmtree|unlink|remove|rmdir)\b", "Dateien löschen (Python)"),
    (r"\bperl\s+-e\b.*\bunlink\b|\bruby\s+-e\b.*\b(delete|unlink)\b", "Dateien löschen (Skriptsprache)"),
    (r"\bgit\s+clean\b.*-\w*[xfd]", "unversionierte Dateien löschen (git clean)"),
    (r"\bgit\s+reset\s+--hard\b", "Arbeitsstand verwerfen (git reset --hard)"),
    (r"\btruncate\b.*\s-s\s*0", "Dateiinhalt leeren (truncate)"),
    (r"\bshred\b|\bsrm\b", "Dateien unwiederbringlich überschreiben"),
    (r"\b(env|command|nohup|nice|time|xargs)\s+(sudo|doas)\b|\bdoas\b", "Administrator-Rechte (verpackt)"),
    (r"\bosascript\b.*\b(delete|empty trash)\b", "Dateien löschen (AppleScript)"),
    (r"\blaunchctl\s+(bootout|bootstrap)\b", "Dienste ändern"),
    (r"\bcrontab\s+(-r|\S+\.txt)", "Zeitpläne ersetzen/löschen"),
]
# Werkzeuge, die nach außen wirken → immer einzeln bestätigen
RISKY_TOOLS = {
    "mcp__m365__mail_send": "eine E-Mail versenden",
    "mcp__m365__calendar_add": "einen Termin eintragen",
    "mcp__m365__files_upload": "eine Datei hochladen",
    "mcp__n8n__workflow_activate": "einen Automations-Workflow scharf schalten",
    "mcp__n8n__webhook_trigger": "einen Webhook auslösen",
}

# ------------------------------------- Fremd-Werkzeuge: fail-closed statt Aufzählung --
# Bis 1.25.0 war RISKY_TOOLS eine ABSCHLUSSLISTE mit fünf Einträgen. Alles, was danach
# dazukam, lief ohne Rückfrage — beim Bau von #119 aufgefallen: `kalender_absagen`,
# `kalender_verschieben`, `mail_antworten` und `mail_weiterleiten` (alle seit 1.16.0
# ausgeliefert) konnten Termine absagen und Mails weiterleiten, ohne je zu fragen.
#
# Eine Positivliste kann mit einem wachsenden Werkzeugkasten nicht Schritt halten. Also
# umgedreht: Von den Integrationen unten sind nur die HIER GENANNTEN als harmlos bekannt;
# jedes andere Werkzeug fragt nach. Ein vergessener Eintrag kostet dann eine überflüssige
# Rückfrage — vorher kostete er eine stillschweigend ausgeführte Aktion.
#
# Nicht enthalten: `mcp__learn__*` (Microsofts Dokumentations-Server, reine Lesequelle
# ohne Bezug zu Nutzerdaten) — bewusst ausgenommen, sonst fragt jede Doku-Suche nach.
# Bis 1.29.0 stand hier eine Aufzaehlung: ("mcp__m365__", "mcp__n8n__"). Alles, was ein
# Kunde selbst eintraegt — und das Eintragen bewerben wir —, fiel durch bis zum
# abschliessenden »nicht riskant« und lief OHNE Rueckfrage. Der Pruefstand (#138) hat es
# am 01.08. bewiesen: ein Server unter mcp__buero__ leitete eine Firmenmail an eine
# externe Adresse weiter und sagte einen Termin ab, beides ungefragt (#148).
#
# Das war #119 eine Ebene hoeher: Damals war die Liste der riskanten WERKZEUGE eine
# Aufzaehlung. Wir haben sie umgedreht — aber die Umkehrung selbst hing an einer
# Aufzaehlung von PRAEFIXEN. Jetzt gilt sie fuer jedes mcp__-Werkzeug.
BESTAETIGUNGSPFLICHTIGE_MCP = ("mcp__",)
# Server, die ausschliesslich oeffentliche Dokumentation liefern und keinerlei Bezug zu
# Nutzerdaten haben. Nur solche duerfen ganz ausgenommen sein — sonst fragt jede
# Doku-Suche nach. Wer hier etwas eintraegt, nimmt einen ganzen Server von der
# Bestaetigungspflicht aus; das ist eine bewusste Entscheidung, keine Bequemlichkeit.
MCP_NUR_LESEQUELLE = ("mcp__learn__",)
MCP_LESEND = {
    # Microsoft 365 — alles, was ausschließlich liest
    "mcp__m365__mail_list", "mcp__m365__mail_read", "mcp__m365__mail_attachments",
    "mcp__m365__mail_suchen", "mcp__m365__mail_ordner",
    "mcp__m365__calendar_list", "mcp__m365__kalender_freibelegt",
    "mcp__m365__files_list", "mcp__m365__datei_lesen",
    "mcp__m365__sharepoint_search", "mcp__m365__sharepoint_listen",
    "mcp__m365__sharepoint_eintraege",
    "mcp__m365__planner_plans", "mcp__m365__planner_aufgaben",
    "mcp__m365__teams_list",
    "mcp__m365__m365_status", "mcp__m365__m365_stoerungen", "mcp__m365__m365_meldungen",
    "mcp__m365__m365_lizenzen", "mcp__m365__m365_nutzung", "mcp__m365__m365_hilfe",
    "mcp__m365__excel_blaetter", "mcp__m365__excel_lesen",
    "mcp__m365__onenote_struktur", "mcp__m365__onenote_seiten",
    "mcp__m365__kontakte_suchen",
    "mcp__m365__erreichbarkeit", "mcp__m365__personen_suchen", "mcp__m365__organigramm",
    # n8n — nur nachsehen
    "mcp__n8n__workflows_list", "mcp__n8n__workflow_get",
    "mcp__n8n__executions_list", "mcp__n8n__execution_get", "mcp__n8n__health",
}
# »Read« stand hier bis 1.29.0 und wurde damit NIE geprueft, waehrend Write/Edit eine
# Pfadpruefung hatten. Die OS-Sandbox faengt es auch nicht: sie setzt »(allow default)«
# plus »(deny file-write*)« — nur Schreiben ist eingesperrt, Lesen war frei (#148).
# Fuer einen Assistenten, der Dateiinhalte an ein Sprachmodell weitergibt, ist die
# Leserichtung die heiklere: Was gelesen wird, verlaesst das Haus.
SAFE_TOOLS = {"Glob", "Grep", "WebSearch", "Skill", "Agent", "TodoWrite"}
LESE_TOOLS = {"Read", "NotebookRead"}

# Oeffentliche Systempfade, deren Inhalt jeder auf dem Rechner ohnehin sehen kann und in
# denen keine persoenlichen Daten liegen. Ohne diese Ausnahme fragt der Operator bei
# jedem »python3 --version«-artigen Blick nach und wird unbenutzbar.
# Bewusst NICHT dabei: /tmp (dort liegen unsere eigenen Zwischendateien, u. a. die
# Pseudonym-Zuordnung) und /etc (Konfiguration, Nutzerliste).
# Zwei Regeln statt einer. Der erste Entwurf haengte alles am ORT, und das war zu grob:
# Er verbot Petra, einen Bericht aus /tmp vorlesen zu lassen, und dem Operator, sein
# eigenes Log zu lesen — beides taegliche, harmlose Arbeit. Entscheidend ist nicht, WO
# eine Datei liegt, sondern WAS sie ist.
OEFFENTLICHE_PFADE = ("/usr/", "/bin/", "/sbin/", "/opt/", "/System/", "/Library/",
                      "/Applications/", "/tmp/", "/private/tmp/",
                      os.path.join(BOT_DIR, ""))
# Bewusst NICHT dabei: /var/folders — das ist auf macOS der TMPDIR, aber unter genau
# diesem Pfad liegt in der Testisolation auch das HOME. Ein Ordner, den man fuer
# Systemkram haelt und der in Wahrheit Nutzerdaten enthaelt, ist die schlechteste
# Sorte Ausnahme. Dort liegen ausserdem unsere eigenen Zwischendateien.

# Diese Dateien sind IMMER eine Rueckfrage wert — unabhaengig davon, wo sie liegen und
# ob der Ordner sonst erlaubt ist. Was hier drinsteht, darf ein Sprachmodell nicht
# beilaeufig zu sehen bekommen: Zugangsdaten, Schluessel, die Zuordnung von Pseudonym
# zu echtem Namen, der Gespraechsverlauf.
GEHEIM_MUSTER = re.compile(
    r"credentials\.json|bots\.json|/secrets?/|\.ssh/|id_(rsa|ed25519|ecdsa)|"
    r"operator-pii-|\.db$|Keychains|\.env$|broker_allow|update-signing|"
    r"connections/|tokens\.json|\.pem$|\.key$", re.IGNORECASE)
_PFAD_IM_BEFEHL = re.compile(r"(?<![\w=])(~/[^\s;|&\)\"']*|/[A-Za-z0-9._\-/]{3,})")


def _fremder_lesepfad(text):
    """Erster Pfad im Text, der eine Rueckfrage verdient — oder None.

    Arbeitet auf dem ganzen Befehl statt auf einzelnen Argumenten: »cat /etc/passwd«,
    »grep -r x ~/.ssh« und »head ~/Documents/steuer.pdf« sehen unterschiedlich aus,
    meinen aber dasselbe."""
    if GEHEIM_MUSTER.search(text or ""):
        return GEHEIM_MUSTER.search(text).group(0)
    for treffer in _PFAD_IM_BEFEHL.findall(text or ""):
        pfad = os.path.expanduser(treffer)
        if pfad.startswith(OEFFENTLICHE_PFADE):
            continue
        if _ausserhalb_arbeitsordner(pfad):
            return treffer
    return None

# ---------------------------------------------------------------- Allowlist (#104-B) --
# Fail-closed statt fail-open: Nur Befehle, die hier (oder in der gelernten Liste)
# stehen, laufen ohne Rückfrage. Alles Unbekannte fragt nach — der Nutzer kann mit
# »immer« antworten, dann merkt sich der Operator den Befehl in broker_allow.txt.
# Die Sperrliste (DESTRUCTIVE_CMD) gewinnt IMMER — auch über gelernte Einträge.
SAFE_COMMANDS = {
    # lesen & anschauen
    "ls", "cat", "head", "tail", "less", "more", "file", "stat", "wc", "tree",
    "grep", "egrep", "fgrep", "rg", "find", "locate", "mdfind",
    # Text verarbeiten
    "awk", "sed", "sort", "uniq", "cut", "tr", "column", "diff", "comm", "jq",
    "basename", "dirname", "realpath", "readlink", "xargs",
    # Umgebung & System anschauen
    "pwd", "cd", "echo", "printf", "date", "cal", "whoami", "id", "uname",
    "hostname", "sw_vers", "df", "du", "ps", "uptime", "env", "printenv",
    "which", "type", "true", "false", "test", "[", "seq", "sleep",
    # Prüfsummen
    "shasum", "sha256sum", "md5", "md5sum", "cksum",
    # Arbeiten im Alltag (Schreiben fängt die Sperrliste bei Systempfaden ab)
    "cp", "mv", "mkdir", "touch", "ln", "tar", "zip", "unzip", "gzip", "gunzip",
    "tee", "open", "pbcopy", "pbpaste",
    # Netz lesen (Pipe in eine Shell fängt die Sperrliste)
    "curl", "wget", "ping", "dig", "host", "nslookup",
    # Entwickeln (riskante Unterbefehle fängt die Sperrliste: clean/reset --hard/push -f)
    "git", "python", "python3", "node", "make",
}
ALLOW_FILE = os.path.join(BOT_DIR, "broker_allow.txt")


def _workspace_real():
    """#106: Der Arbeitsordner liegt seit 1.12 außerhalb von ~/.claude."""
    try:
        import platform_compat
        return os.path.realpath(platform_compat.workspace())
    except Exception:
        return os.path.realpath(os.path.join(BOT_DIR, "workspace"))


# Wrapper, hinter denen der eigentliche Befehl steht (sudo/doas fängt die Sperrliste)
_WRAPPER = {"env", "command", "nohup", "nice", "time", "stdbuf", "timeout", "caffeinate"}


def _gelernte():
    try:
        return {z.strip() for z in open(ALLOW_FILE) if z.strip() and not z.startswith("#")}
    except OSError:
        return set()


def _merke_erlaubt(wort):
    """»immer«-Antwort des Owners: Befehl dauerhaft erlauben (eine Zeile, Datei 0600)."""
    wort = (wort or "").strip()
    if not wort or wort in _gelernte():
        return
    neu = not os.path.exists(ALLOW_FILE)
    with open(ALLOW_FILE, "a") as f:
        f.write(wort + "\n")
    if neu:
        try:
            os.chmod(ALLOW_FILE, 0o600)
        except OSError:
            pass


def _segmente(cmd):
    """Zerlegt eine Befehlszeile in ihre Teil-Befehle: Pipes, &&, ||, ;, Zeilen —
    plus die Inhalte von $(…)-Ersetzungen und Backticks. Jedes Segment muss für
    sich harmlos sein, sonst wird gefragt."""
    teile = []
    for sub in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", cmd):
        teile.extend(t for t in sub if t)
    grob = re.split(r"\|\||&&|;|\||\n", re.sub(r"\$\([^()]*\)|`[^`]*`", " ", cmd))
    teile.extend(grob)
    return [t.strip() for t in teile if t.strip()]


def _befehlswort(segment):
    """Erstes echtes Wort eines Segments: VAR=x-Zuweisungen und Wrapper überspringen,
    Pfad-Präfix entfernen (/usr/bin/ls → ls)."""
    for w in segment.split():
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", w):
            continue                      # Env-Zuweisung
        wort = w.rsplit("/", 1)[-1].lower()
        if wort in _WRAPPER:
            continue
        return wort
    return ""


def _ohne_heredoc(cmd):
    """Heredoc-INHALTE entfernen, bevor Segmente gebildet werden. Realer Fehlgriff
    (Michi, 30.07.): »send.py <<'EOF' Deine letzten 10 Mails …« — der Broker hielt
    die Wörter des Mail-TEXTES für Befehle und fragte nach »deine« und »📧«.
    Heredoc-Body ist Daten, keine Befehle; der Befehl selbst bleibt erhalten."""
    out, pos = [], 0
    for m in re.finditer(r"<<-?\s*(['\"]?)(\w+)\1", cmd):
        if m.start() < pos:
            continue
        rest = cmd[m.end():]
        ende = re.search(rf"^\s*{re.escape(m.group(2))}\s*$", rest, re.M)
        if not ende:
            continue                              # kein Terminator → nichts anfassen
        out.append(cmd[pos:m.end()])              # bis einschließlich <<EOF-Marker
        pos = m.end() + ende.end()                # Body + Terminator überspringen
    out.append(cmd[pos:])
    return "".join(out)


# Eigene, geprüfte Werkzeuge des Operators: dürfen NIE eine Rückfrage auslösen —
# »im Alltag merkst du nichts« ist das Produktversprechen. send.py schreibt nur in
# den eigenen Matrix-Raum; m365.py hat seine eigene Regler-Matrix (die Regler SIND
# die Zustimmung); memory.py arbeitet nur auf der lokalen Gedächtnis-DB.
_EIGENE_WERKZEUGE = ("send.py", "m365.py", "memory.py")


def _eigenes_werkzeug(segment):
    """→ True, wenn das Segment ein python-Aufruf eines eigenen Werkzeugs im
    Bot-Ordner ist (Pfad wird real aufgelöst — kein ../-Ausbruch)."""
    toks = [t for t in segment.split() if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", t)]
    toks = [t for t in toks if t.rsplit("/", 1)[-1].lower() not in _WRAPPER]
    if len(toks) < 2 or toks[0].rsplit("/", 1)[-1].lower() not in ("python", "python3", "py"):
        return False
    pfad = toks[1].strip("'\"").replace("\\", "/")
    if pfad.rsplit("/", 1)[-1] not in _EIGENE_WERKZEUGE:
        return False
    try:
        echt = os.path.realpath(os.path.expanduser(os.path.expandvars(pfad)))
        bd = os.path.realpath(BOT_DIR)
        return os.path.dirname(echt) == bd
    except OSError:
        return False


def unbekannte_befehle(cmd):
    """→ Liste der Befehlsworte, die weder eingebaut noch gelernt erlaubt sind."""
    erlaubt = SAFE_COMMANDS | _gelernte()
    fremd = []
    for seg in _segmente(_ohne_heredoc(cmd)):
        if _eigenes_werkzeug(seg):
            continue
        wort = _befehlswort(seg)
        if wort and wort not in erlaubt and wort not in fremd:
            fremd.append(wort)
    return fremd


_SCHREIB_CMD = {"tee", "cp", "mv", "ln", "dd", "install", "rsync", "truncate",
                "shred", "chmod", "chown", "cat", "sed", "tar", "unzip", "touch",
                "mkdir", "python", "python3", "perl", "ruby", "awk"}


WARTE_DATEI = os.path.join(BOT_DIR, "run", "wartezeit.json")
# #94: Solange eine Rückfrage offen ist, steht sie hier. Grund: Der Dock im Dashboard
# sendet unter dem BOT-Konto (matrix_room.senden_dashboard), der Broker akzeptiert aber
# nur Antworten des Owners. Ein »ja«, das jemand ins Dashboard tippt, wird also
# stillschweigend ignoriert und die Aufgabe läuft nach drei Minuten in den Timeout.
# Sicherheitlich richtig — aber ohne diese Datei völlig unsichtbar.
OFFEN_DATEI = os.path.join(BOT_DIR, "run", "frage_offen.json")


def offene_frage():
    """Läuft gerade eine Rückfrage? → dict mit »seit« und »was«, sonst None.
    Veraltete Einträge (Prozess abgestürzt) laufen nach der doppelten Wartezeit ab."""
    try:
        with open(OFFEN_DATEI, encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - d.get("seit", 0) > 2 * WAIT_SECONDS:
            return None
        return d
    except (OSError, ValueError, AttributeError):
        return None


def _frage_offen(beschreibung):
    try:
        os.makedirs(os.path.dirname(OFFEN_DATEI), exist_ok=True)
        tmp = OFFEN_DATEI + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"seit": time.time(), "was": str(beschreibung)[:200]}, f)
        os.replace(tmp, OFFEN_DATEI)
    except OSError:
        pass


def _frage_erledigt():
    try:
        os.remove(OFFEN_DATEI)
    except OSError:
        pass


def wartezeit_gesamt():
    """Sekunden, die der Broker insgesamt auf MENSCHLICHE Antworten gewartet hat.

    Der Listener zieht das von seiner Laufzeit ab: Wartezeit auf dich ist keine
    Rechenzeit. Vorher lief die 10-Minuten-Uhr mit, während der Operator auf ein »ja«
    wartete — zwei Rückfragen à 3 Minuten fraßen 6 der 10 Minuten, und die Aufgabe
    wurde »wegen Überlänge« abgebrochen, obwohl sie fast fertig war (Michi, 30.07.)."""
    try:
        with open(WARTE_DATEI) as f:
            return float(json.load(f).get("sek", 0))
    except (OSError, ValueError, TypeError):
        return 0.0


def _warten_verbuchen(sekunden):
    """Additiv und prozessübergreifend — der Hook läuft in einem eigenen Prozess."""
    try:
        os.makedirs(os.path.dirname(WARTE_DATEI), exist_ok=True)
        gesamt = wartezeit_gesamt() + max(0.0, float(sekunden))
        tmp = WARTE_DATEI + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"sek": round(gesamt, 1)}, f)
        os.replace(tmp, WARTE_DATEI)
    except OSError:
        pass


def _hat_inplace_schalter(segment):
    """Wird an Ort und Stelle geschrieben (sed -i, perl -i.bak, python -i)?

    Als eigenes Wort prüfen, NICHT als Teilstring: »--id«, »--include«, »--info«
    enthalten alle »-i«, schreiben aber nichts. Genau daran scheiterte
    »m365.py mail send --id 5« mit einer sinnlosen Rückfrage (Michi, 30.07.).
    Ein echter In-Place-Schalter beginnt mit EINEM Bindestrich."""
    for w in segment.split():
        if len(w) > 1 and w[0] == "-" and w[1] != "-" and "i" in w.split("=")[0]:
            return True
    return False


def _schreibt_in_botdir(cmd):
    """#104-B / Security-Review Teil 2: Erkennt Shell-Schreibzugriffe in den
    Operator-Ordner selbst — egal über welchen Umweg. Das ist die Kern-Lücke:
    Das Write/Edit-Werkzeug prüft den Zielpfad (fragt bei Zugriff hierher), die
    Bash-Musterliste kannte aber nur /etc//System//Library/. Ein »echo x > repo_raw.txt«
    ging durch — und wer hier schreibt, kann Update-Quelle, Signatur-Schlüssel,
    den Prüfer und den Broker selbst austauschen, also die GESAMTE Absicherung
    aushebeln, ohne dass je gefragt wird.
    Konservativ: bei jedem Verdacht True (→ Rückfrage). Reines Lesen bleibt frei."""
    try:
        bd = os.path.realpath(BOT_DIR)
        ws = _workspace_real()
    except OSError:
        return True

    def _im_botdir(pfad):
        """Unter dem Bot-Ordner, aber NICHT im Arbeitsordner. Der liegt darunter
        und ist der normale Arbeitsplatz — dort zu schreiben darf nie nerven."""
        return (pfad == bd or pfad.startswith(bd + os.sep)) \
            and not (pfad == ws or pfad.startswith(ws + os.sep))
    # Alle Formen, in denen der Bot-Ordner adressiert sein kann.
    marker = [bd, "~/.claude/matrix-bot", "$HOME/.claude/matrix-bot",
              "${HOME}/.claude/matrix-bot", ".claude/matrix-bot"]
    if not any(m in cmd for m in marker):
        return False
    # Redirect irgendwohin in den Bot-Ordner? (> … , >> …)
    for ziel in re.findall(r">>?\s*([^\s;|&>]+)", cmd):
        if _im_botdir(os.path.realpath(os.path.expanduser(os.path.expandvars(ziel)))):
            return True
    # Schreibendes Kommando, dessen Ziel im Bot-Ordner (außerhalb workspace) liegt?
    for seg in _segmente(cmd):
        if _eigenes_werkzeug(seg):
            continue          # eigene, geprüfte Werkzeuge (Umleitungen prüft der Block oben)
        wort = _befehlswort(seg)
        if wort not in _SCHREIB_CMD:
            continue
        # cat/sed/python nur, wenn sie wirklich schreiben (Umleitung oder -i).
        # WICHTIG: »-i« als eigenes Wort prüfen, nicht als Teilstring — sonst gilt
        # jedes »--id«, »--include«, »--info« als Schreibzugriff. Real passiert
        # (Michi, 30.07.): »m365.py mail send --id 5« löste eine Rückfrage aus,
        # die nichts mit Schreiben im Bot-Ordner zu tun hatte.
        if wort in ("cat", "python", "python3", "perl", "ruby", "awk") \
                and ">" not in seg and not _hat_inplace_schalter(seg):
            continue
        for w in seg.split():
            if not any(m in w for m in marker):
                continue
            p = os.path.realpath(os.path.expanduser(os.path.expandvars(w.strip("'\"" ))))
            if _im_botdir(p) or (w.rstrip("/").endswith("matrix-bot")):
                return True
    return False


def merkbar(tool, tool_input):
    """Das Wort, das eine »immer«-Antwort dauerhaft erlauben würde (None = nicht lernbar).
    Nur für unbekannte Bash-Befehle — Sperrlisten-Treffer sind NIE lernbar."""
    if tool != "Bash":
        return None
    cmd = str((tool_input or {}).get("command", ""))
    for muster, _ in DESTRUCTIVE_CMD:
        if re.search(muster, cmd, re.IGNORECASE):
            return None
    if _schreibt_in_botdir(cmd):          # Selbstschutz ist nie per »immer« abwählbar
        return None
    fremd = unbekannte_befehle(cmd)
    return fremd[0] if len(fremd) == 1 else None
# WebFetch ist NICHT pauschal harmlos: Der Operator läuft in deinem Netz und könnte darüber
# interne Adressen abrufen (Dashboard, Router, Gitea). #82 prüft die Adresse — interne Ziele
# werden gar nicht erst zur Rückfrage, sondern direkt abgelehnt.
WEB_TOOLS = {"WebFetch"}
BLOCK = "__blockieren__"      # Sonderfall: nicht fragen, sondern direkt ablehnen


def _shorten(s, n=110):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def classify(tool, tool_input):
    """→ (riskant?, klartext_beschreibung). Konservativ: im Zweifel NICHT fragen,
    außer es passt auf ein bekanntes Risiko-Muster."""
    tool_input = tool_input or {}
    if tool in RISKY_TOOLS:
        return True, RISKY_TOOLS[tool]
    # Fail-closed für die angebundenen Dienste: Was nicht ausdrücklich als lesend bekannt
    # ist, wird bestätigt. Siehe MCP_LESEND — die alte Positivliste hinkte dem
    # Werkzeugkasten hinterher, und zwar zulasten der Zusage.
    if (tool.startswith(BESTAETIGUNGSPFLICHTIGE_MCP)
            and not tool.startswith(MCP_NUR_LESEQUELLE)
            and tool not in MCP_LESEND):
        teile = tool.split("__")
        was = teile[-1].replace("_", " ")
        dienst = {"m365": "Microsoft 365", "n8n": "n8n"}.get(
            teile[1] if len(teile) > 2 else "", teile[1] if len(teile) > 2 else "einem Dienst")
        return True, f"in {dienst} etwas verändern: »{was}«"
    if tool in WEB_TOOLS:
        # #82: Adressen ins eigene Netz gar nicht erst anbieten — direkt ablehnen.
        try:
            import net_guard
            ok, grund = net_guard.check_url(str(tool_input.get("url", "")))
            if not ok:
                return BLOCK, f"Adresse gesperrt: {grund}"
        except Exception:
            pass
        # Stufe »streng«: auch erlaubte Adressen bestätigen lassen. Wer den Operator auf
        # einem Rechner mit sensiblen Daten betreibt, will jeden Abruf sehen.
        if stufe() == "streng":
            return True, f"eine Webseite abrufen: {_shorten(tool_input.get('url', ''), 80)}"
        return False, ""
    if tool in SAFE_TOOLS:
        return False, ""
    if tool in LESE_TOOLS:
        pfad = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        fremd = _fremder_lesepfad(pfad) if pfad else None
        if fremd:
            return True, f"eine geschützte Datei lesen: {_shorten(pfad, 80)}"
        return False, ""
    if tool == "Bash":
        cmd = str(tool_input.get("command", ""))
        for muster, was in DESTRUCTIVE_CMD:
            if re.search(muster, cmd, re.IGNORECASE):
                return True, f"{was} — Befehl: {_shorten(cmd, 90)}"
        # Gate-Konsistenz mit Write/Edit: Schreibzugriff in den Operator-Ordner selbst
        # ist immer eine Rückfrage (schützt Update-Quelle, Signatur-Schlüssel, Broker).
        if _schreibt_in_botdir(cmd):
            return True, ("in meinen eigenen Programmordner schreiben — das betrifft meine "
                          f"Sicherheitseinstellungen. Befehl: {_shorten(cmd, 80)}")
        # #104-B: fail-closed — was nicht als harmlos bekannt ist, fragt nach.
        # Vorher galt »im Zweifel nicht fragen«; eine Sperrliste gegen einen
        # generativen Prozess ist aber strukturell zu schwach (Security-Review 29.07.).
        # In Stufe »locker« entfällt GENAU diese Regel — und nur sie. Die Sperrliste
        # oben und der Selbstschutz davor haben bereits gegriffen; was hier ankommt,
        # ist unbekannt, aber nicht als gefährlich erkannt.
        if stufe() == "locker":
            return False, ""
        # Lesen ausserhalb des Kaefigs: »cat« ist ein harmloses Befehlswort, »cat
        # ~/.ssh/id_ed25519« ist es nicht. Bis 1.29.0 pruefte hier nur das Wort.
        fremder = _fremder_lesepfad(cmd)
        if fremder:
            return True, ("auf etwas außerhalb des Arbeitsordners zugreifen: "
                          f"{_shorten(fremder, 60)} — Befehl: {_shorten(cmd, 70)}")
        fremd = unbekannte_befehle(cmd)
        if fremd:
            return True, (f"einen Befehl ausführen, den ich nicht als harmlos kenne "
                          f"(»{', '.join(fremd[:3])}«) — Befehl: {_shorten(cmd, 90)}")
        return False, ""
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        pfad = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if pfad and _ausserhalb_arbeitsordner(pfad):
            return True, f"eine Datei außerhalb des Arbeitsordners ändern: {_shorten(pfad, 80)}"
        return False, ""
    return False, ""


def _ausserhalb_arbeitsordner(pfad):
    try:
        ws = _workspace_real()
        p = os.path.realpath(os.path.expanduser(pfad))
        return not (p == ws or p.startswith(ws + os.sep))
    except OSError:
        return True          # im Zweifel: als außerhalb behandeln → fragen


def fingerprint(tool, tool_input):
    """Bindet eine Freigabe an genau diesen Aufruf — geänderte Argumente brauchen
    eine neue Freigabe."""
    roh = json.dumps({"t": tool, "i": tool_input or {}}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(roh.encode()).hexdigest()[:32]


# ---------------------------------------------------------------- Replay-Schutz --
def _consumed():
    try:
        d = json.load(open(CONSUMED_FILE))
        jetzt = time.time()
        return {k: v for k, v in d.items() if v > jetzt - 3600}
    except (OSError, ValueError, AttributeError):
        return {}


def _consume(fp):
    """True, wenn diese Freigabe noch frei war (und jetzt verbraucht ist)."""
    d = _consumed()
    if fp in d:
        return False
    d[fp] = time.time()
    try:
        os.makedirs(os.path.dirname(CONSUMED_FILE), exist_ok=True)
        tmp = CONSUMED_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONSUMED_FILE)
    except OSError:
        pass
    return True


def mark_reply_used(event_id):
    """Merkt, dass diese Chat-Nachricht bereits eine Antwort auf eine Rückfrage war."""
    if not event_id:
        return
    d = used_replies()
    d[event_id] = time.time()
    try:
        os.makedirs(os.path.dirname(REPLIES_FILE), exist_ok=True)
        tmp = REPLIES_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, REPLIES_FILE)
    except OSError:
        pass


def used_replies():
    """Event-IDs verbrauchter Antworten (letzte Stunde) — vom Listener gelesen."""
    try:
        d = json.load(open(REPLIES_FILE))
        jetzt = time.time()
        return {k: v for k, v in d.items() if v > jetzt - 3600}
    except (OSError, ValueError, AttributeError):
        return {}


# ---------------------------------------------------------------- Matrix-Umlauf --
def _matrix():
    """(homeserver, token, raum, owner) — oder None, wenn nicht konfigurierbar."""
    try:
        creds = json.load(open(os.path.join(BOT_DIR, "credentials.json")))
        tok = creds.get("access_token", "")
        if tok == "keychain":
            import sys
            sys.path.insert(0, BOT_DIR)
            import secretstore
            tok = secretstore.get("matrix-owner") or ""
        if not (tok and creds.get("room_id") and creds.get("owner_id")):
            return None
        return creds["homeserver"], tok, creds["room_id"], creds["owner_id"]
    except Exception:
        return None


def _api(hs, tok, pfad, method="GET", body=None, timeout=20):
    req = urllib.request.Request(
        hs + pfad, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


JA = ("ja", "jo", "jep", "ok", "okay", "passt", "mach", "machen", "los", "gerne",
      "erlaubt", "freigabe", "👍", "✅", "y", "yes")
NEIN = ("nein", "ne", "nö", "stop", "stopp", "abbrechen", "nicht", "lass", "❌", "👎",
        "n", "no")


def _antwort_aus_text(text):
    """→ True/False/None. Nur eindeutige kurze Antworten zählen."""
    t = " ".join(str(text or "").lower().split())
    if not t or len(t) > 40:
        return None
    wort = re.split(r"[\s,.!]+", t)[0] if t else ""
    if wort in JA or t in JA:
        return True
    if wort in NEIN or t in NEIN:
        return False
    return None


def ask_owner(beschreibung, fp, wait=WAIT_SECONDS, log=lambda *_: None, merken=None):
    """Fragt im Matrix-Chat nach und wartet auf die Antwort.
    Rückgabe True nur bei ausdrücklichem Ja des Owners — sonst immer False.
    merken (#104-B): Befehlswort, das eine »immer«-Antwort dauerhaft erlaubt."""
    m = _matrix()
    if not m:
        log("Permission-Broker: Matrix nicht konfiguriert → abgelehnt (fail-closed)")
        return False
    hs, tok, raum, owner = m
    raum_q = urllib.parse.quote(raum)
    extra = (f" — oder **immer**, dann merke ich mir »{merken}« dauerhaft als harmlos"
             if merken else "")
    frage = ("🔐 **Kurze Rückfrage — ich brauche dein Okay.**\n"
             f"Ich möchte {beschreibung}.\n\n"
             f"👉 Antworte **ja**, wenn ich das machen soll — oder **nein**, wenn nicht{extra}.\n"
             f"(Ohne Antwort mache ich es nicht. Ich warte {int(wait / 60)} Minuten.)")
    try:
        gesendet = _api(hs, tok, f"/_matrix/client/v3/rooms/{raum_q}/send/m.room.message/"
                        f"{time.time_ns()}", method="PUT",
                        body={"msgtype": "m.text", "body": frage})
        frage_id = gesendet.get("event_id", "")
    except Exception as e:
        log(f"Permission-Broker: Frage konnte nicht gesendet werden ({e}) → abgelehnt")
        return False
    _frage_offen(beschreibung)          # #94: der Dock kann jetzt ehrlich warnen
    ab = time.time()
    ende = ab + wait
    global _WARTE_START
    _WARTE_START = time.time()
    while time.time() < ende:
        time.sleep(POLL_SECONDS)
        try:
            d = _api(hs, tok, f"/_matrix/client/v3/rooms/{raum_q}/messages?dir=b&limit=25")
        except Exception:
            continue
        for e in d.get("chunk", []):
            if e.get("sender") != owner:                     # nur der Owner entscheidet
                continue
            if e.get("origin_server_ts", 0) / 1000 < ab:     # nur Antworten NACH der Frage
                continue
            if e.get("type") == "m.reaction":                # ✅/❌ als Reaktion
                rel = e.get("content", {}).get("m.relates_to", {})
                if rel.get("event_id") == frage_id:
                    key = rel.get("key", "")
                    if key in ("✅", "👍", "🆗"):
                        return _entscheidung(True, fp, log)
                    if key in ("❌", "👎", "🛑"):
                        return _entscheidung(False, fp, log)
            elif e.get("type") == "m.room.message":
                body = e.get("content", {}).get("body")
                if merken and " ".join(str(body or "").lower().split()) in ("immer", "ja immer", "immer ja"):
                    mark_reply_used(e.get("event_id"))
                    _merke_erlaubt(merken)
                    log(f"Permission-Broker: »{merken}« dauerhaft erlaubt (immer)")
                    try:
                        _api(hs, tok, f"/_matrix/client/v3/rooms/{raum_q}/send/"
                             f"m.room.message/{time.time_ns()}", method="PUT",
                             body={"msgtype": "m.text",
                                   "body": f"✅ Gemerkt — »{merken}« führe ich künftig ohne "
                                           "Rückfrage aus. (Ändern: broker_allow.txt "
                                           "im Operator-Ordner.)"})
                    except Exception:
                        pass
                    return _entscheidung(True, fp, log)
                a = _antwort_aus_text(body)
                if a is not None:
                    # Diese Nachricht war die Antwort auf die Rückfrage — der Listener
                    # soll sie nicht zusätzlich als normalen Chat beantworten.
                    mark_reply_used(e.get("event_id"))
                    return _entscheidung(a, fp, log)
    _verbuche_wartezeit()
    _frage_erledigt()
    log("Permission-Broker: keine Antwort in der Wartezeit → abgelehnt (fail-closed)")
    try:
        _api(hs, tok, f"/_matrix/client/v3/rooms/{raum_q}/send/m.room.message/{time.time_ns()}",
             method="PUT", body={"msgtype": "m.text",
                                 "body": "⏳ Keine Antwort bekommen — ich habe es NICHT gemacht. "
                                         "Sag einfach nochmal Bescheid, wenn du möchtest."})
    except Exception:
        pass
    return False


_WARTE_START = None


def _verbuche_wartezeit():
    """Die Zeit, die wir auf den Menschen gewartet haben, aufs Konto legen.
    Der Listener zieht sie von seiner Laufzeit ab — Warten ist keine Rechenzeit."""
    global _WARTE_START
    if _WARTE_START:
        _warten_verbuchen(time.time() - _WARTE_START)
        _WARTE_START = None


def _entscheidung(ja, fp, log):
    _verbuche_wartezeit()
    _frage_erledigt()
    if not ja:
        log("Permission-Broker: vom Owner abgelehnt")
        return False
    if not _consume(fp):
        log("Permission-Broker: Freigabe war schon verbraucht (Replay) → abgelehnt")
        return False
    log("Permission-Broker: vom Owner freigegeben")
    return True
