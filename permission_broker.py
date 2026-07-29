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
SAFE_TOOLS = {"Read", "Glob", "Grep", "WebSearch", "Skill", "Agent", "TodoWrite"}
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
    if tool in WEB_TOOLS:
        # #82: Adressen ins eigene Netz gar nicht erst anbieten — direkt ablehnen.
        try:
            import net_guard
            ok, grund = net_guard.check_url(str(tool_input.get("url", "")))
            if not ok:
                return BLOCK, f"Adresse gesperrt: {grund}"
        except Exception:
            pass
        return False, ""
    if tool in SAFE_TOOLS:
        return False, ""
    if tool == "Bash":
        cmd = str(tool_input.get("command", ""))
        for muster, was in DESTRUCTIVE_CMD:
            if re.search(muster, cmd, re.IGNORECASE):
                return True, f"{was} — Befehl: {_shorten(cmd, 90)}"
        return False, ""
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        pfad = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if pfad and _ausserhalb_arbeitsordner(pfad):
            return True, f"eine Datei außerhalb des Arbeitsordners ändern: {_shorten(pfad, 80)}"
        return False, ""
    return False, ""


def _ausserhalb_arbeitsordner(pfad):
    try:
        ws = os.path.realpath(os.path.join(BOT_DIR, "workspace"))
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


def ask_owner(beschreibung, fp, wait=WAIT_SECONDS, log=lambda *_: None):
    """Fragt im Matrix-Chat nach und wartet auf die Antwort.
    Rückgabe True nur bei ausdrücklichem Ja des Owners — sonst immer False."""
    m = _matrix()
    if not m:
        log("Permission-Broker: Matrix nicht konfiguriert → abgelehnt (fail-closed)")
        return False
    hs, tok, raum, owner = m
    raum_q = urllib.parse.quote(raum)
    frage = ("🔐 **Kurze Rückfrage — ich brauche dein Okay.**\n"
             f"Ich möchte {beschreibung}.\n\n"
             "👉 Antworte **ja**, wenn ich das machen soll — oder **nein**, wenn nicht.\n"
             f"(Ohne Antwort mache ich es nicht. Ich warte {int(wait / 60)} Minuten.)")
    try:
        gesendet = _api(hs, tok, f"/_matrix/client/v3/rooms/{raum_q}/send/m.room.message/"
                        f"{time.time_ns()}", method="PUT",
                        body={"msgtype": "m.text", "body": frage})
        frage_id = gesendet.get("event_id", "")
    except Exception as e:
        log(f"Permission-Broker: Frage konnte nicht gesendet werden ({e}) → abgelehnt")
        return False
    ab = time.time()
    ende = ab + wait
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
                a = _antwort_aus_text(e.get("content", {}).get("body"))
                if a is not None:
                    # Diese Nachricht war die Antwort auf die Rückfrage — der Listener
                    # soll sie nicht zusätzlich als normalen Chat beantworten.
                    mark_reply_used(e.get("event_id"))
                    return _entscheidung(a, fp, log)
    log("Permission-Broker: keine Antwort in der Wartezeit → abgelehnt (fail-closed)")
    try:
        _api(hs, tok, f"/_matrix/client/v3/rooms/{raum_q}/send/m.room.message/{time.time_ns()}",
             method="PUT", body={"msgtype": "m.text",
                                 "body": "⏳ Keine Antwort bekommen — ich habe es NICHT gemacht. "
                                         "Sag einfach nochmal Bescheid, wenn du möchtest."})
    except Exception:
        pass
    return False


def _entscheidung(ja, fp, log):
    if not ja:
        log("Permission-Broker: vom Owner abgelehnt")
        return False
    if not _consume(fp):
        log("Permission-Broker: Freigabe war schon verbraucht (Replay) → abgelehnt")
        return False
    log("Permission-Broker: vom Owner freigegeben")
    return True
