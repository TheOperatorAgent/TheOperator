#!/usr/bin/env python3
"""Operator Listener v2 — Matrix-Multiplexer.

Ein Daemon, ein Thread je Matrix-Bot-Account:
- Owner-Bot (credentials.json): VERHALTEN.md + Gedächtnis-Recall, volles Verhalten wie v1
- Agenten-Bots (bots.json): je ein veröffentlichter Agent mit eigenem Account/Raum,
  Prompt = Agenten-MD, Werkzeuge = Frontmatter ∩ Owner-Freigabe, Modell aus Frontmatter

bots.json wird zur Laufzeit überwacht (mtime) — Publish/Unpublish greift ohne Neustart.
100 % Python-Standardbibliothek (läuft auch ohne Dashboard-venv).
"""
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
sys.path.insert(0, BOT_DIR)
import platform_compat as _plat   # noqa: E402  (stdlib-Modul aus BOT_DIR)
import secretstore                # noqa: E402  (stdlib-Modul aus BOT_DIR)
try:
    import providers              # noqa: E402  (stdlib; Multi-LLM-Registry)
except Exception:
    providers = None
try:
    import sandbox                # noqa: E402  (#104-A OS-Sandbox unter jedem Agenten-Lauf)
except Exception:
    sandbox = None
try:
    import merker                 # noqa: E402  (#110 automatisches Merken)
except Exception:
    merker = None
try:
    import anhaenge               # noqa: E402  (Bilder/Dateien aus dem Chat)
except Exception:
    anhaenge = None
try:
    import verify_loop            # noqa: E402  (stdlib; A1 Verifikations-Schleife #46)
except Exception:
    verify_loop = None


def keychain_token(account, fallback):
    """Token aus dem OS-Secret-Store; Datei-Wert nur als Fallback (Altbestand)."""
    if fallback != "keychain":
        log(f"⚠ Klartext-Token in Datei ({account}) — '{sys.executable} {BOT_DIR}/migrate_tokens.py' "
            f"ausführen, um es in den Secret-Store zu verschieben.")
        return fallback
    return secretstore.get(account) or ""


CREDS = json.load(open(f"{BOT_DIR}/credentials.json"))
BOTS_FILE = f"{BOT_DIR}/bots.json"
CRON_FILE = f"{BOT_DIR}/cron.json"
WORKSPACE = _plat.workspace()   # #106: nicht mehr unter ~/.claude (Claude Code sperrt das)

_sys = sys  # Rückwärtskompatibler Alias (unten weiterhin genutzt)
try:
    import sessions as sessions_db
except Exception:
    sessions_db = None
try:
    import cron_runner
except Exception:
    cron_runner = None
try:
    import triggers                    # noqa: E402  (#47 Event-Proaktivität)
except Exception:
    triggers = None
try:
    import reid as reid_mod            # noqa: E402  (#60 robuste Re-Identifikation)
except Exception:
    reid_mod = None
try:
    import audit_log                   # noqa: E402  (#49 Audit-Integritäts-Siegel)
except Exception:
    audit_log = None
try:
    import redact as redact_mod
except Exception:
    redact_mod = None
try:
    import claude_health               # noqa: E402  (#59 Login-Vorwarnung)
except Exception:
    claude_health = None
try:
    import throttle                    # noqa: E402  (#58 Fair-Use-Drossel)
except Exception:
    throttle = None
try:
    import permission_broker           # noqa: E402  (#65 Rückfrage-Antworten kennen)
except Exception:
    permission_broker = None
try:
    import retention                   # noqa: E402  (#18 Aufbewahrung/Aufräumen)
except Exception:
    retention = None
VENV_PY = _plat.venv_python(BOT_DIR)


def redact_text(text):
    """Secrets aus Text entfernen, bevor er in Log/Verlauf/Prompt landet.
    Bekannte Tresor-Werte via vault.py-Subprocess (best effort, venv),
    generische Muster immer (stdlib)."""
    if not text:
        return text
    try:
        import os as _os
        if _os.path.exists(f"{BOT_DIR}/secrets/vault.enc") and _os.path.exists(VENV_PY):
            r = subprocess.run([VENV_PY, f"{BOT_DIR}/vault.py", "redact"],
                               input=text, capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout:
                return r.stdout
    except Exception:
        pass
    return redact_mod.redact(text) if redact_mod else text


# ---------------------------------------------------------------- Pseudonymisierung --
def _pii_cfg():
    """Konfiguration aus dashboard.json (Default AN)."""
    try:
        c = json.load(open(f"{BOT_DIR}/dashboard.json")).get("pseudonymize", {})
    except Exception:
        c = {}
    return {"enabled": c.get("enabled", True), "mode": c.get("mode", "standard"),
            "allow": c.get("allow", []), "deny": c.get("deny", [])}


def datenschutz_angebot(session):
    """#116: Der Datenschutz-Filter startet AUS (er braucht ein großes Sprachmodell
    und System-Bibliotheken, die nicht überall da sind). Statt die Installation daran
    scheitern zu lassen, bietet der Operator ihn EINMAL selbst an — dann läuft er
    schon und kann bei Problemen helfen. Genau das war Michis Idee (29.07.).

    Wird höchstens einmal pro Installation gesendet."""
    marke = os.path.join(BOT_DIR, "run", "datenschutz-angebot.json")
    try:
        if os.path.exists(marke) or _pii_cfg()["enabled"]:
            return
        # Läuft der Filter auf diesem Rechner überhaupt? Erst prüfen, dann anbieten —
        # nichts versprechen, was die Maschine nicht kann.
        r = subprocess.run([VENV_PY, f"{BOT_DIR}/pseudonym.py", "selftest"],
                           capture_output=True, text=True, timeout=180)
        laeuft = "SELFTEST OK" in (r.stdout or "")
        os.makedirs(os.path.dirname(marke), exist_ok=True)
        with open(marke, "w") as f:
            json.dump({"ts": int(time.time()), "laeuft": laeuft}, f)
        if laeuft:
            session.send_message(
                "🎭 **Noch ein Angebot, dann lasse ich dich in Ruhe.**\n"
                "Ich kann Namen, Telefonnummern und Kontodaten in deinen Nachrichten "
                "durch Platzhalter ersetzen, bevor sie zum Sprachmodell gehen — du "
                "merkst davon nichts, meine Antworten bleiben richtig.\n\n"
                "Ich habe gerade geprüft: Auf diesem Rechner funktioniert das.\n"
                "👉 Schreib **Datenschutz an**, wenn ich das machen soll.")
        else:
            session.send_message(
                "ℹ️ **Kurz zur Einordnung:** Ich könnte Namen und Nummern durch "
                "Platzhalter ersetzen, bevor sie zum Sprachmodell gehen. Auf diesem "
                "Rechner fehlen dafür ein paar Systembibliotheken — deshalb ist die "
                "Funktion aus, und alles andere läuft normal.\n"
                "👉 Frag mich einfach »**wie aktiviere ich den Datenschutz**«, dann "
                "gehe ich das mit dir durch.")
    except Exception as e:
        log(f"Datenschutz-Angebot übersprungen: {e}")


def wants_datenschutz_an(bodies):
    """Kurzbefehl »Datenschutz an« — ohne Modell-Lauf, damit es auch dann geht,
    wenn der Filter gerade alles blockiert."""
    t = " ".join(" ".join(bodies).lower().split())
    return t in ("datenschutz an", "datenschutz ein", "datenschutz aktivieren",
                 "pseudonymisierung an", "pseudonymisierung ein")


def wants_datenschutz_aus(bodies):
    t = " ".join(" ".join(bodies).lower().split())
    return t in ("datenschutz aus", "datenschutz abschalten", "pseudonymisierung aus")


def setze_datenschutz(an):
    """Filter ein-/ausschalten (dashboard.json)."""
    p = f"{BOT_DIR}/dashboard.json"
    d = json.load(open(p))
    d.setdefault("pseudonymize", {})["enabled"] = bool(an)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, p)


def owner_verify_cfg():
    """Owner-Verify (#46) aus dashboard.json — frisch pro Nachricht, damit der
    Dashboard-Umschalter sofort wirkt. Default AUS. Rückgabe: (enabled, model|None)."""
    try:
        c = json.load(open(f"{BOT_DIR}/dashboard.json")).get("owner_verify", {})
    except Exception:
        return False, None
    if not isinstance(c, dict):
        return False, None
    return bool(c.get("enabled")), (c.get("model") or None)


def _pseudonym_via_daemon(req: str):
    """Anfrage an den langlebigen Daemon (Modell schon geladen) — schnell. None = nicht erreichbar.
    IPC plattformübergreifend: POSIX AF_UNIX, Windows TCP-Loopback + Token (via platform_compat)."""
    try:
        s, token = _plat.ipc_connect(timeout=90)
    except Exception:
        return None   # Daemon nicht erreichbar → Aufrufer nutzt Subprozess-Fallback
    try:
        if token:   # Windows: Token ins Request-JSON einweben (Daemon prüft ihn)
            d = json.loads(req)
            d["token"] = token
            req = json.dumps(d)
        s.settimeout(90)
        s.sendall(req.encode() + b"\n")
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf.split(b"\n", 1)[0].decode() if buf else None
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def pseudonymize_segments(segments, conv=""):
    """Nutzerdaten-Segmente (Nachricht/Verlauf/Gedächtnis) pseudonymisieren — NICHT den
    System-Prompt (VERHALTEN/Infrastruktur bleibt echt). Gibt (segments', mapping) zurück.
    Zuerst der Daemon (Modell schon geladen, schnell; führt pro Konversation `conv` das
    Mapping fort → gleicher Kontakt behält seinen Platzhalter), sonst Einzel-Subprozess.
    fail-safe: bei Fehler None → Aufrufer bricht ab (maximale Sicherheit)."""
    cfg = _pii_cfg()
    if not cfg["enabled"] or not any(s.strip() for s in segments):
        return segments, {}
    if not os.path.exists(VENV_PY):
        log(f"Pseudonymisierung nicht möglich: venv-Python fehlt ({VENV_PY})")
        return None, None
    req = json.dumps({"texts": segments, "mapping": {}, "conversation": conv,
                      "mode": cfg["mode"], "allow": cfg["allow"], "deny": cfg["deny"]})
    try:
        raw = _pseudonym_via_daemon(req)
        if raw is None:   # Daemon nicht da → Subprozess-Fallback (lädt Modell einmalig)
            r = subprocess.run([VENV_PY, f"{BOT_DIR}/pseudonym.py", "run"],
                               input=req, capture_output=True, text=True, timeout=60)
            if r.returncode != 0 or not r.stdout:
                log(f"Pseudonymisierung fehlgeschlagen (rc={r.returncode}): {r.stderr[-200:]}")
                return None, None
            raw = r.stdout
        out = json.loads(raw)
        if "error" in out:
            log(f"Pseudonymisierung-Daemon-Fehler: {out['error'][:200]}")
            return None, None
        try:   # Transparenz-Zähler fürs Dashboard (nur Zahlen, keine Realwerte)
            with open(f"{BOT_DIR}/pseudonymize-stats.json", "w") as f:
                json.dump({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "stats": out.get("stats", {})}, f)
        except OSError:
            pass
        return out["texts"], out["mapping"]
    except Exception as e:
        log(f"Pseudonymisierung-Fehler: {e}")
        return None, None


def dashboard_link():
    """Einmal-Link zum Dashboard: enthält ein 10-Minuten-Ticket, das beim ersten Klick
    verbraucht wird — der echte Zugangs-Token landet NIE im Chatverlauf. Wer den Raum
    später liest, kann mit dem Link nichts mehr anfangen."""
    try:
        import hashlib
        import secrets as _sec
        port = 8737
        try:
            port = json.load(open(f"{BOT_DIR}/dashboard.json")).get("port", 8737)
        except Exception:
            pass
        ott = _sec.token_hex(16)
        p = f"{BOT_DIR}/secrets/ott.json"
        try:
            entries = [e for e in json.load(open(p)) if e.get("exp", 0) > time.time()]
        except Exception:
            entries = []
        entries.append({"sha": hashlib.sha256(ott.encode()).hexdigest(),
                        "exp": time.time() + 600})
        os.makedirs(f"{BOT_DIR}/secrets", mode=0o700, exist_ok=True)
        fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(entries, f)
        os.replace(p + ".tmp", p)
        return f"http://127.0.0.1:{port}/#ott={ott}"
    except Exception:
        return "http://127.0.0.1:8737"


def wants_dashboard(bodies):
    """Erkennt eine Bitte um den Dashboard-Zugang (Login-Kurzbefehl im Chat).
    Bewusst eng, damit normale Sätze mit dem Wort »Dashboard« NICHT auslösen."""
    t = " ".join(bodies or []).strip().lower().strip("!.?/ ")
    if not t:
        return False
    if t in ("dashboard", "dashboard link", "dashboard-link", "dashboard öffnen",
             "dashboard entsperren", "zugang", "login", "anmelden", "einloggen"):
        return True
    return "dashboard" in t and any(w in t for w in (
        "link", "öffn", "zugang", "login", "entsperr", "anmeld", "einlogg", "freischalt"))


def reidentify(text, mapping):
    """Surrogate → echte Werte (stdlib). #60: nutzt reid.apply — erfasst auch
    abgeleitete Formen (Nachname allein, kleingeschrieben in Dateinamen) case-insensitiv."""
    s2r = (mapping or {}).get("s2r", {})
    if not text or not s2r:
        return text
    if reid_mod:
        return reid_mod.apply(text, s2r)
    for sur in sorted(s2r, key=len, reverse=True):        # Fallback: exakte Treffer
        if sur in text:
            text = text.replace(sur, s2r[sur])
    return text


# Windows-sicher: which("claude") kann dort die .ps1/Shell-Variante treffen → WinError 193
CLAUDE = CREDS.get("claude_bin") or _plat.claude_bin()
OWNER = CREDS.get("owner_id", "")
# #90 Dock: Dashboard-Eingaben werden vom Bot-Konto mit diesem Inhalts-Schlüssel in den
# Raum gespiegelt (matrix_room.senden_dashboard). Nur der Bot-Token kann so senden.
DASHBOARD_MARKER = "bayern.vonaschenbrenner.operator.dashboard"
OWNER_TOOLS = CREDS.get("allowed_tools", ["Bash", "Read", "WebFetch", "WebSearch", "Agent", "Skill"])
# Owner-Verify (#46): opt-in, live umschaltbar über dashboard.json (owner_verify_cfg() liest frisch).
CLAUDE_SLOTS = threading.Semaphore(2)
# #110: Merken laeuft mit einem guenstigen Modell — es ist eine Kleinstaufgabe,
# und es soll das Kontingent des eigentlichen Gespraechs nicht schmaelern.
MERK_MODELL = "haiku"


def memory_enabled():
    """Automatisches Merken abschaltbar (dashboard.json: {"merken": {"enabled": false}}).
    Standard: an — ein Gedaechtnis, das niemand fuellt, ist wertlos."""
    try:
        c = json.load(open(f"{BOT_DIR}/dashboard.json")).get("merken", {})
        return c.get("enabled", True) is not False
    except Exception:
        return True

# #106: Der Arbeitsordner wird dem Modell EXPLIZIT genannt. Sonst rät es den Pfad
# aus dem Gesprächsverlauf — nach dem Umzug aus ~/.claude war das der alte, was den
# Selbstschutz auslöste und wie ein Fehler aussah.
ARBEITSORDNER_HINWEIS = (
    "Dein Arbeitsordner ist {ws} (dein aktuelles Arbeitsverzeichnis). "
    "Dort — und nur dort — legst du Dateien an; nutze am besten relative Pfade. "
    "Der Programmordner ~/.claude/matrix-bot gehört NICHT dazu.\n\n")

OWNER_PROMPT = """Deine Verhaltensregeln, Wissensquellen und wie du antwortest stehen hier \
(strikt befolgen):

{verhalten}

---
{history}{memories}
Michi hat dir soeben im Matrix-Chat geschrieben:

{messages}

Erledige/beantworte das jetzt gemäß den Regeln oben und sende die Antwort in den Raum. \
Beziehe dich auf den Gesprächsverlauf oben, wenn sich die Nachricht darauf bezieht."""

# Owner-Verify-Variante (#46, opt-in via credentials.json owner_verify): der Owner erledigt
# alles mit vollem Werkzeugkasten, SENDET aber nicht selbst — ein zweites Modell prüft die
# finale Antwort (fängt Fehler wie Verlesen/Verwechslung ab), dann liefert der Listener aus.
OWNER_PROMPT_VERIFY = """Deine Verhaltensregeln, Wissensquellen und wie du antwortest stehen hier \
(strikt befolgen):

{verhalten}

---
{history}{memories}
Michi hat dir soeben im Matrix-Chat geschrieben:

{messages}

Erledige/beantworte das jetzt gemäß den Regeln oben. Nutze Werkzeuge wie gewohnt. \
WICHTIG: Sende die Antwort NICHT selbst (kein send.py) — gib deine FINALE Antwort einfach als \
letzten Text aus. Sie wird von einem zweiten Modell auf Fehler geprüft und danach in den Chat \
gestellt. Beziehe dich auf den Gesprächsverlauf, wenn passend. Keine Meta-Kommentare, nur die \
eigentliche Antwort."""

AGENT_PROMPT = """Du bist der Agent „{name}" und läufst als eigenständiger Matrix-Bot. \
Dein Auftraggeber ist {owner}. Dein Verhalten:

{body}

---
{history}
{owner_short} hat dir soeben geschrieben:

{messages}

Erledige/beantworte das jetzt. Sende deine Antwort am Ende zwingend per Bash:
{py} {bot_dir}/send.py --bot {name} "DEINE ANTWORT"
(mehrzeilig: Text per stdin an send.py --bot {name}). Keine Secrets in den Chat — \
Zugangsdaten existieren nur als Referenz {{{{tresor:name}}}} über den Tresor-Wrapper."""

# Variante für verifizierte Agenten (#46): der Worker SENDET NICHT selbst, sondern gibt
# seine finale Antwort als Text zurück — der Listener prüft sie und liefert sie dann aus.
AGENT_PROMPT_VERIFY = """Du bist der Agent „{name}" und läufst als eigenständiger Matrix-Bot. \
Dein Auftraggeber ist {owner}. Dein Verhalten:

{body}

---
{history}
{owner_short} hat dir soeben geschrieben:

{messages}

Erledige/beantworte das jetzt. WICHTIG: Sende NICHTS selbst (kein send.py) — gib deine \
FINALE Antwort einfach als letzten Text aus. Sie wird von einem zweiten Modell geprüft und \
danach in den Chat gestellt. Keine Meta-Kommentare über dich, nur die eigentliche Antwort. \
Keine Secrets im Klartext — Zugangsdaten nur als Referenz {{{{tresor:name}}}}."""


def log(msg):
    print(f"[{time.strftime('%F %T')}] {msg}", flush=True)


# ---------- Mail-Watch (#62): alle ~5 min pollen, wenn aktive Regeln existieren ----------
_mail_watch_state = {"last": 0.0, "busy": False}
_audit_seal_state = {"last": 0.0}


def _mail_watch_tick(log_fn, interval=300):
    now = time.time()
    if _mail_watch_state["busy"] or now - _mail_watch_state["last"] < interval:
        return
    try:
        rules = json.load(open(f"{BOT_DIR}/mail_watch.json")).get("rules", [])
    except (OSError, ValueError):
        return
    if not any(r.get("enabled") for r in rules):
        return
    _mail_watch_state["last"] = now
    _mail_watch_state["busy"] = True

    def _poll():
        try:
            r = subprocess.run([VENV_PY, f"{BOT_DIR}/mail_watch.py", "check"],
                               capture_output=True, text=True, timeout=120)
            out = (r.stdout or "").strip()
            if out and "nichts Neues" not in out:
                log_fn(f"Mail-Watch: {out}")
            if r.returncode != 0:
                log_fn(f"Mail-Watch-Fehler: {(r.stderr or r.stdout)[:200]}")
        except Exception as e:
            log_fn(f"Mail-Watch-Poll fehlgeschlagen: {e}")
        finally:
            _mail_watch_state["busy"] = False

    threading.Thread(target=_poll, daemon=True).start()


def parse_agent_md(name):
    """Frontmatter + Body eines Agenten lesen (stdlib-Parser)."""
    try:
        text = open(f"{WORKSPACE}/.claude/agents/{name}.md").read()
    except OSError:
        return None
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return {"tools": [], "model": None, "body": text}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    tools = [t.strip() for t in fm.get("tools", "").split(",") if t.strip()]
    return {"tools": tools, "model": fm.get("model"), "body": m.group(2).strip(),
            "verify": fm.get("verify"), "verify_with": fm.get("verify_with")}


class BotSession(threading.Thread):
    """Ein Matrix-Account: Long-Poll-Loop + Claude-Wecker."""

    def __init__(self, kind, name, homeserver, token, room_id, user_id):
        super().__init__(daemon=True, name=f"bot-{name}")
        self.kind = kind          # "owner" | "agent"
        self.bot_name = name
        self.hs = homeserver
        self.token = token
        self.room = room_id
        self.room_enc = urllib.parse.quote(room_id)
        self.user = user_id
        self.user_enc = urllib.parse.quote(user_id)
        self.stop_event = threading.Event()
        # #86: bereits beantwortete Event-IDs — verhindert Doppel-Antworten, wenn der
        # Server nach einem Netzfehler dieselben Events erneut liefert.
        self.seen_events = collections.deque(maxlen=200)

    def _vom_dashboard(self, e):
        """#90 Dock: Ist dieses Ereignis eine gespiegelte Dashboard-Eingabe des Owners?
        Drei Bedingungen, alle hart: (1) nur im Owner-Chat — Agent-Räume nehmen keine
        Dashboard-Eingaben an; (2) Absender ist unser eigenes Bot-Konto — niemand sonst
        kann den Marker setzen, ohne den Bot-Token zu besitzen; (3) der Marker trägt Text."""
        return (self.kind == "owner"
                and e.get("sender") == self.user
                and bool(((e.get("content") or {}).get(DASHBOARD_MARKER) or {}).get("text")))

    # ---------- Matrix-API ----------
    def api(self, path, timeout=90, method="GET", body=None):
        req = urllib.request.Request(
            self.hs + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + self.token,
                     "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=timeout))

    def send_message(self, text):
        try:
            r = self.api(f"/_matrix/client/v3/rooms/{self.room_enc}/send/m.room.message/{time.time_ns()}",
                         method="PUT", body={"msgtype": "m.text", "body": text}, timeout=15)
            return r.get("event_id", "")
        except Exception as e:
            log(f"[{self.bot_name}] Senden fehlgeschlagen: {e}")
            return ""

    def edit_message(self, event_id, text):
        """#100: Nachricht nachträglich ersetzen (m.replace) — für »erst senden, dann
        veredeln«: die Antwort steht sofort im Chat, das Prüf-Ergebnis aktualisiert sie."""
        if not event_id:
            return self.send_message(text)
        try:
            self.api(f"/_matrix/client/v3/rooms/{self.room_enc}/send/m.room.message/{time.time_ns()}",
                     method="PUT", timeout=15,
                     body={"msgtype": "m.text", "body": "* " + text,
                           "m.new_content": {"msgtype": "m.text", "body": text},
                           "m.relates_to": {"rel_type": "m.replace", "event_id": event_id}})
        except Exception as e:
            log(f"[{self.bot_name}] Bearbeiten fehlgeschlagen: {e}")

    def set_typing(self, on):
        try:
            self.api(f"/_matrix/client/v3/rooms/{self.room_enc}/typing/{self.user_enc}",
                     method="PUT",
                     body={"typing": on, "timeout": 25000} if on else {"typing": False},
                     timeout=10)
        except Exception:
            pass

    def mark_read(self, event_id):
        try:
            self.api(f"/_matrix/client/v3/rooms/{self.room_enc}/receipt/m.read/{urllib.parse.quote(event_id)}",
                     method="POST", body={}, timeout=10)
        except Exception:
            pass

    # ---------- Prompt-Bau ----------
    def recall(self, text, k=5):
        try:
            r = subprocess.run([sys.executable, f"{BOT_DIR}/memory.py", "search", text, "-k", str(k)],
                               capture_output=True, text=True, timeout=15)
            hits = r.stdout.strip()
            if hits:
                return f"Relevante Einträge aus deinem Gedächtnis:\n{hits}\n\n"
        except Exception as e:
            log(f"Gedächtnis-Abruf fehlgeschlagen: {e}")
        return ""

    def history_block(self):
        """Letzte Gesprächsrunden als Kontext (aus sessions.db) — löst das
        „jede Nachricht weckt mich neu ohne Verlauf"-Problem."""
        if not sessions_db:
            return ""
        try:
            rounds = sessions_db.recent_dialog(self.bot_name, n=6, max_age_h=24)
        except Exception:
            return ""
        if not rounds:
            return ""
        lines = []
        for msg, res in rounds:
            # Altbestand aus der Zeit vor der Redaction ebenfalls filtern
            if redact_mod:
                msg, res = redact_mod.redact(msg), redact_mod.redact(res)
            lines.append(f"Michi: {msg[:400]}")
            lines.append(f"Du: {res[:400]}")
        return ("Bisheriger Gesprächsverlauf (chronologisch, zur Einordnung von "
                "Rückbezügen wie 'darüber' oder 'das'):\n" + "\n".join(lines) + "\n\n")

    def build(self, bodies):
        """Baut Prompt. Pseudonymisiert die PII-tragenden Nutzer-Segmente (aktuelle
        Nachricht + Gedächtnis-Treffer) gemeinsam — NICHT VERHALTEN/Infrastruktur, NICHT
        den Verlauf (der kommt schon pseudonymisiert aus sessions.db). history bleibt echt.
        Rückgabe: (prompt, tools, model, mapping, messages_for_record) oder (None,…,False,…)
        bei fail-safe-Abbruch."""
        messages = "\n".join(f"- {b}" for b in bodies)
        memories = self.recall(" ".join(bodies)) if self.kind == "owner" else ""
        # erst Secrets (stdlib), dann PII pseudonymisieren
        m_in = redact_mod.redact(messages) if redact_mod else messages
        mem_in = redact_mod.redact(memories) if redact_mod else memories
        segs, mapping = pseudonymize_segments([m_in, mem_in], conv=f"{self.bot_name}:{self.room}")
        if segs is None:
            return None, None, None, False, None, None, None   # fail-safe: Aufrufer bricht ab
        messages_p, memories_p = segs
        history = self.history_block()                # bereits pseudonymisiert in der DB
        if self.kind == "owner":
            try:
                verhalten = open(f"{BOT_DIR}/VERHALTEN.md").read()
            except OSError:
                verhalten = "(VERHALTEN.md fehlt — antworte hilfsbereit auf Deutsch und sende per python3 ~/.claude/matrix-bot/send.py)"
            # Persona (»Soul«) + Nutzerprofil vor VERHALTEN.md — nur was der Owner selbst gesetzt
            # hat, pro Nachricht frisch, fail-open (ein Fehler hier darf den Bot nie blockieren).
            try:
                import persona as _persona
                _pblock = _persona.render_block()
                if _pblock:
                    verhalten = _pblock + "\n\n---\n\n" + verhalten
            except Exception as _e:
                log(f"persona-Block übersprungen: {_e}")
            # #106: Arbeitsordner explizit voranstellen (siehe ARBEITSORDNER_HINWEIS)
            verhalten = ARBEITSORDNER_HINWEIS.format(ws=WORKSPACE) + verhalten
            ov_on, ov_model = owner_verify_cfg() if verify_loop else (False, None)
            if ov_on:
                # Owner erledigt alles mit Werkzeugen, gibt Text zurück (sendet nicht) → Prüfer.
                prompt = OWNER_PROMPT_VERIFY.format(verhalten=verhalten, messages=messages_p,
                                                    history=history, memories=memories_p)
                return prompt, OWNER_TOOLS, None, mapping, messages_p, None, (True, ov_model)
            prompt = OWNER_PROMPT.format(verhalten=verhalten, messages=messages_p,
                                         history=history, memories=memories_p)
            return prompt, OWNER_TOOLS, None, mapping, messages_p, None, None
        agent = parse_agent_md(self.bot_name) or {"tools": [], "model": None,
                                                  "body": "(Agenten-Datei fehlt)"}
        # A1 (#46): Verifikations-Schleife per Frontmatter (verify / verify_with)?
        v_on, v_model = verify_loop.verify_config(agent) if verify_loop else (False, None)
        verify = (v_on, v_model) if v_on else None
        # Fremd-Modell (Ollama/OpenAI/Azure)? → text-orientierter Prompt OHNE Werkzeuge.
        plan = providers.resolve(agent.get("model")) if providers else {"kind": "claude"}
        if plan.get("kind") == "foreign":
            # Werkzeuge für Fremd-Modelle: eigene Runner-Schleife im Pfad-Käfig (llm_runner).
            f_tools = [t for t in agent.get("tools", []) if t in ("Bash", "Read", "Write", "Browser")]
            if f_tools:
                _caps = []
                if any(t in ("Bash", "Read", "Write") for t in f_tools):
                    _caps.append("Befehle ausführen, Dateien lesen/schreiben/auflisten (nur in deinem Arbeitsordner)")
                if "Browser" in f_tools:
                    _caps.append("im Browser navigieren: Seiten öffnen (open_page), Links/Buttons klicken "
                                 "(click_link) und Text/Daten extrahieren — NUR Lesen/Navigieren, KEINE Formulare absenden")
                system = (agent["body"].strip() + "\n\nDu hast Werkzeuge: " + "; ".join(_caps)
                          + ". Arbeite die Aufgabe damit wirklich ab und fasse am Ende kurz auf Deutsch "
                          "zusammen, was du getan und herausgefunden hast. Die Zusammenfassung wird in "
                          "den Matrix-Chat gesendet.")
            else:
                system = (agent["body"].strip() + "\n\nWICHTIG: Du hast KEINE Werkzeuge — nur Text. "
                          "Antworte direkt, knapp und auf Deutsch; deine Antwort wird 1:1 in den "
                          "Matrix-Chat gesendet. Keine Erklärungen über dich selbst.")
            # Identitäts-Ehrlichkeit: manche Fremd-Modelle behaupten fälschlich, »Claude«/»ChatGPT«
            # zu sein. Gegensteuern (passt zur Transparenz-Linie).
            system += ("\n\nWenn du nach deinem Sprachmodell/Hersteller gefragt wirst: Behaupte "
                       "NIEMALS, ein bestimmtes Produkt (Claude, ChatGPT, Gemini o. Ä.) zu sein. "
                       "Sag wahrheitsgemäß, dass du ein Sprachmodell im Operator bist; das konkrete "
                       "Modell verwaltet dein Nutzer im Dashboard.")
            user = ((history + "\n") if history else "") \
                + f"{OWNER.split(':')[0]} schreibt dir:\n{messages_p}"
            return user, f_tools, agent.get("model"), mapping, messages_p, system, verify
        # Claude-Agent (voller Werkzeugkasten)
        agent["body"] = ARBEITSORDNER_HINWEIS.format(ws=WORKSPACE) + agent["body"]
        tools = [t for t in agent["tools"] if t in OWNER_TOOLS or t == "Read"]
        model = agent["model"]   # roh; providers.resolve() in execute() macht Claude-Aliase/-IDs/None
        if verify:
            # Verifizierter Worker gibt Text zurück (sendet NICHT selbst) → Listener liefert aus.
            prompt = AGENT_PROMPT_VERIFY.format(name=self.bot_name, owner=OWNER,
                                                owner_short=OWNER.split(":")[0],
                                                body=agent["body"], messages=messages_p,
                                                history=history)
            return prompt, tools, model, mapping, messages_p, None, verify
        if "Bash" not in tools:
            tools.append("Bash")  # noetig fuer send.py; Agent-Frontmatter bleibt die inhaltliche Leitplanke
        prompt = AGENT_PROMPT.format(name=self.bot_name, owner=OWNER,
                                     owner_short=OWNER.split(":")[0],
                                     body=agent["body"], messages=messages_p,
                                     history=history, bot_dir=BOT_DIR, py=sys.executable)
        return prompt, tools, model, mapping, messages_p, None, None

    # ---------- Claude ----------
    def record_direct(self, bodies, reply):
        """#86: Direkt-Antworten (ohne Modell-Lauf) in den Verlauf schreiben, damit das
        Modell bei Folgefragen den Austausch kennt. kind="chat", weil recent_dialog()
        (Gesprächskontext) genau darauf filtert; model="direkt" kennzeichnet die Runde.
        Fail-open, redact übernimmt sessions.record."""
        if sessions_db:
            try:
                sessions_db.record(self.bot_name, "\n".join(bodies), reply, 0, 0,
                                   kind="chat", model="direkt")
            except Exception:
                pass

    def answer(self, bodies, last_event_id):
        # Login-Kurzbefehl: »dashboard« → SELBST öffnen statt Link schicken (#123).
        # Realer Fehlgriff (30.07.): Michi bat »öffne das Dashboard auf dem Windows-
        # Rechner« und bekam einen 127.0.0.1-Link — den er auf dem MAC las. Der Link
        # kann auf jedem anderen Gerät nur ins Leere gehen. Der Listener läuft aber
        # auf demselben Rechner wie das Dashboard: also öffnet er es einfach selbst.
        if self.kind == "owner" and wants_dashboard(bodies):
            self.mark_read(last_event_id)
            import socket
            rechner = socket.gethostname().split(".")[0] or "diesem Rechner"
            geoeffnet = False
            try:
                geoeffnet = bool(_plat.open_url(dashboard_link()))
            except Exception:
                geoeffnet = False
            if geoeffnet:
                self.send_message(
                    f"✅ Erledigt — das Dashboard ist auf deinem Rechner »{rechner}« im "
                    "Browser geöffnet und schon entsperrt. Schau auf den Bildschirm dort.")
                self.record_direct(bodies, "(Ich habe das lokale Operator-Dashboard "
                                           "direkt im Browser des Operator-Rechners geöffnet.)")
                return
            # Kein Bildschirm (z. B. Pi per SSH) → Link als Fallback, aber mit der
            # ehrlichen Grenze, an der Michi real gescheitert ist.
            self.send_message(
                "🔓 Dein Ein-Klick-Link zum Dashboard (10 Min gültig, einmal verwendbar):\n"
                f"{dashboard_link()}\n"
                f"⚠️ Wichtig: Der Link funktioniert nur auf dem Rechner, auf dem dein "
                f"Operator läuft (»{rechner}«) — auf dem Handy oder einem anderen Rechner "
                "geht er ins Leere. Danach merkt sich der Browser den Zugang dauerhaft.")
            # Verlauf OHNE den Einmal-Link (Token gehört nicht in die durchsuchbare DB)
            self.record_direct(bodies, "(Ich habe dem Nutzer einen Ein-Klick-Login-Link "
                                       "zum lokalen Operator-Dashboard in den Chat geschickt.)")
            return
        # #116: Datenschutz ein/aus als Kurzbefehl — bewusst VOR dem Modell-Lauf,
        # damit es auch dann funktioniert, wenn der Filter gerade jede Nachricht
        # blockiert. Sonst säße der Nutzer in der Falle: Er kann nichts schreiben,
        # weil der Filter klemmt, und den Filter nicht abschalten, weil er nichts
        # schreiben kann.
        if self.kind == "owner" and (wants_datenschutz_an(bodies) or wants_datenschutz_aus(bodies)):
            an = wants_datenschutz_an(bodies)
            self.mark_read(last_event_id)
            try:
                setze_datenschutz(an)
            except Exception as e:
                self.send_message(f"⚠️ Umschalten hat nicht geklappt ({e}). "
                                  "👉 Geht auch im Dashboard unter »Datenschutz«.")
                return
            if an:
                self.send_message(
                    "🎭 Datenschutz-Filter ist **an**. Ab jetzt ersetze ich Namen, "
                    "Nummern und Kontodaten durch Platzhalter, bevor deine Nachricht "
                    "zum Sprachmodell geht — meine Antworten bleiben trotzdem richtig.\n"
                    "Falls etwas klemmt: »Datenschutz aus« schaltet ihn wieder ab.")
            else:
                self.send_message(
                    "Datenschutz-Filter ist **aus**. Deine Nachrichten gehen jetzt "
                    "unverändert zum Sprachmodell. Mit »Datenschutz an« schaltest du "
                    "ihn wieder ein.")
            self.record_direct(bodies, f"(Datenschutz-Filter wurde {'ein' if an else 'aus'}geschaltet.)")
            return
        prompt, tools, model, mapping, msg_rec, system, verify = self.build(bodies)
        self.mark_read(last_event_id)
        if mapping is False:
            self.send_message("⚠️ Der Pseudonymisierungs-Dienst ist gerade nicht verfügbar. "
                              "Aus Datenschutzgründen habe ich deine Nachricht NICHT an das "
                              "Sprachmodell geschickt. Prüfen oder (bewusst) deaktivieren: "
                              f"{dashboard_link()} — Einmal-Link, 10 Min gültig, funktioniert "
                              "auf dem Rechner, auf dem dein Operator läuft.")
            self.record_direct(bodies, "(Pseudonymisierung war nicht verfügbar — Nachricht "
                                       "wurde aus Datenschutzgründen NICHT ans Modell geschickt; "
                                       "Nutzer wurde informiert.)")
            return
        self.execute(prompt, tools, model, msg_rec, kind="chat", mapping=mapping,
                     system=system, verify=verify)

    def run_automation(self, job):
        # #58: Fair-Use — Automationen dürfen das Abo nicht leerlaufen lassen.
        if throttle:
            ok, grund = throttle.allow("cron")
            if not ok:
                log(f"[{self.bot_name}] Automation '{job.get('name')}' verschoben — {grund}")
                return
        framing = f"[Automation „{job['name']}“ wurde planmäßig ausgelöst] {job['prompt']}"
        prompt, tools, model, mapping, msg_rec, system, verify = self.build([framing])
        if mapping is False:
            log(f"[{self.bot_name}] Automation '{job['name']}' abgebrochen (Pseudonymisierung aus)")
            return
        log(f"[{self.bot_name}] Automation '{job['name']}' startet")
        if throttle:
            throttle.record("cron")
        self.execute(prompt, tools, model, msg_rec, kind="cron", mapping=mapping,
                     system=system, verify=verify)

    def run_event(self, name, framing):
        """#47: proaktiver Lauf, von einem externen Ereignis ausgelöst (triggers.drain).
        Gleicher Pfad wie Chat/Automation — Pseudonymisierung, Werkzeuge, Audit inklusive."""
        if throttle:                        # #58: auch Ereignis-Läufe fair drosseln
            ok, grund = throttle.allow("event")
            if not ok:
                log(f"[{self.bot_name}] Ereignis '{name}' verschoben — {grund}")
                return
        prompt, tools, model, mapping, msg_rec, system, verify = self.build([framing])
        if mapping is False:
            log(f"[{self.bot_name}] Ereignis '{name}' abgebrochen (Pseudonymisierung aus)")
            return
        log(f"[{self.bot_name}] Ereignis '{name}' startet proaktiven Lauf")
        if throttle:
            throttle.record("event")
        self.execute(prompt, tools, model, msg_rec, kind="event", mapping=mapping,
                     system=system, verify=verify)

    def execute(self, prompt, tools, model, messages_label, kind, mapping=None, system=None,
                verify=None):
        plan = providers.resolve(model) if (providers and model) else {"kind": "claude", "model": None}
        log(f"[{self.bot_name}] Modell erwacht ({kind}) [{plan.get('kind')}:{model or 'inherit'}]")
        done = threading.Event()

        def keep_typing():
            while not done.is_set():
                self.set_typing(True)
                done.wait(20)

        t = threading.Thread(target=keep_typing, daemon=True)
        t.start()
        map_path = None
        try:
            if plan.get("kind") == "foreign":
                self._run_foreign(plan, prompt, system, messages_label, kind, mapping, verify,
                                  tools=tools)
                return
            # ---------- Claude (voller Werkzeugkasten) ----------
            cmd = [CLAUDE, "-p", prompt, "--output-format", "json", "--allowedTools", *tools]
            if plan.get("model"):
                cmd += ["--model", plan["model"]]
            mcp_file = f"{WORKSPACE}/.mcp.json"     # vom Nutzer konfigurierte MCP-Server laden
            try:
                if os.path.getsize(mcp_file) > 20:
                    cmd += ["--mcp-config", mcp_file]
            except OSError:
                pass
            # Tool-Re-ID-Brücke: Mapping flüchtig ablegen, Pfad per ENV an Claudes Werkzeuge
            run_env, map_path = dict(os.environ), None
            if mapping and mapping.get("s2r"):
                import tempfile
                fd, map_path = tempfile.mkstemp(prefix="operator-pii-", suffix=".json")
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(mapping, f)
                _plat.secure_chmod(map_path)
                run_env["OPERATOR_PII_MAP"] = map_path
            start = time.time()

            def _claude_run(env):
                # #104-A: Der GESAMTE Claude-Lauf läuft in der OS-Sandbox — damit gilt
                # sie automatisch für jeden Befehl, den der Agent startet, und für
                # dessen Kindprozesse. Das ist die Ebene UNTER der Mustererkennung:
                # Sie entscheidet nicht, was ein Befehl bedeutet, sondern setzt durch,
                # was er darf. Ohne verfügbare Sandbox läuft alles wie bisher (der
                # Broker bleibt), und das Dashboard weist das ehrlich aus.
                argv = sandbox.wrap(cmd) if sandbox else cmd
                with CLAUDE_SLOTS:
                    rr = subprocess.run(argv, capture_output=True, text=True,
                                        timeout=600, cwd=WORKSPACE, env=env)
                res, ti, to, du = "", 0, 0, int((time.time() - start) * 1000)
                try:
                    d = json.loads(rr.stdout)
                    res = str(d.get("result", ""))[:4000]
                    u = d.get("usage", {})
                    ti = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                    to = u.get("output_tokens", 0)
                    du = d.get("duration_ms", du)
                except ValueError:
                    res = rr.stdout[-500:]
                return rr, res, ti, to, du

            r, result, tok_in, tok_out, dur = _claude_run(run_env)
            used_fallback = False
            # M4 — Auto-Fallback mit hinterlegtem API-Key: bei Abo-Limit UND (#59) bei
            # abgelaufenem Login. Genau EIN Retry; der Nutzer wird nicht blockiert.
            if r.returncode != 0 and providers:
                out_low = r.stdout + r.stderr
                reason = claude_health.classify(r.returncode, out_low) if claude_health else (
                    "limit" if any(k in out_low.lower() for k in
                                   ("limit", "429", "rate", "overloaded", "quota")) else "unknown")
                fk = providers.fallback_key()
                if fk and reason in ("limit", "expired"):
                    log(f"[{self.bot_name}] {'Login abgelaufen' if reason == 'expired' else 'Abo-Limit'}"
                        f" erkannt → Wiederholung mit Anthropic-API-Key")
                    run_env["ANTHROPIC_API_KEY"] = fk
                    r, result, tok_in, tok_out, dur = _claude_run(run_env)
                    used_fallback = (r.returncode == 0)
                    if used_fallback:
                        self._fallback_reason = reason
            # #59: Zustand des Claude-Logins aus dem echten Lauf verbuchen (kein Probe nötig)
            if claude_health and self.kind == "owner":
                try:
                    claude_health.record(r.returncode, r.stdout + r.stderr)
                except Exception:
                    pass

            result = redact_text(result)
            messages_label = redact_text(messages_label)
            # #18: KEIN Antworttext ins Log — das Log ist zum Fehlersuchen da, nicht zum
            # Mitlesen von Gesprächen. Der Verlauf steht (pseudonymisiert) in sessions.db.
            log(f"[{self.bot_name}] Claude fertig (rc={r.returncode}, {dur}ms, "
                f"{tok_out} out-tok{', Fallback-Key' if used_fallback else ''}, "
                f"{len(result)} Zeichen Antwort)")
            if sessions_db:
                try:
                    sessions_db.record(self.bot_name, messages_label, result, r.returncode,
                                       dur, tok_in, tok_out, kind, plan.get("model") or "inherit")
                except Exception as e:
                    log(f"Session-Recording fehlgeschlagen: {e}")
            if r.returncode == 0 and kind == "chat":
                # #110: NACH dem Senden — merken darf die Antwort nie bremsen.
                # (Steht bewusst außerhalb der if/elif-Kette darunter: es gilt für
                # jeden erfolgreichen Sende-Weg, geprüft oder ungeprüft.)
                self._merk_nachlauf = (messages_label, result, mapping)
            if r.returncode != 0:
                out = (r.stdout + r.stderr).lower()
                if "401" in out or "authenticate" in out or "oauth" in out:
                    self.send_message(
                        "⚠️ Ich kann gerade nicht antworten: Mein Claude-CLI-Login ist "
                        "abgelaufen. Bitte am Mac im Terminal `claude /login` ausführen — "
                        "danach beantworte ich deine Nachricht gern nochmal.")
                elif any(k in out for k in ("limit", "429", "quota")):
                    self.send_message(
                        "⚠️ Mein Claude-Abo ist gerade am Limit. Du kannst im Dashboard unter "
                        "»Modelle & Provider« einen Claude-API-Key als Reserve hinterlegen — "
                        "dann springe ich automatisch darauf um.")
                else:
                    self.send_message(
                        "⚠️ Beim Bearbeiten ist ein Fehler aufgetreten (Details im "
                        "listener.log am Mac). Probier's gleich nochmal.")
            elif verify and verify_loop:
                # A1 (#46): der Worker hat NICHT selbst gesendet — Antwort prüfen und ausliefern.
                self._verify_and_send(verify, messages_label, result, mapping, used_fallback)
            elif used_fallback:
                grund = ("dein Claude-Login abgelaufen ist — bitte bei Gelegenheit am Rechner "
                         "`claude /login` ausführen"
                         if getattr(self, "_fallback_reason", "") == "expired"
                         else "das Abo gerade am Limit war")
                self.send_message(f"ℹ️ (Lief über deinen Claude-API-Key, weil {grund}.)")
                self._fallback_reason = ""
        except subprocess.TimeoutExpired:
            log(f"[{self.bot_name}] Claude-Lauf abgebrochen (Timeout 600s)")
            if sessions_db:
                try:
                    sessions_db.record(self.bot_name, messages_label, "(Timeout 600s)",
                                       -1, 600000, 0, 0, kind, model or "inherit")
                except Exception:
                    pass
            self.send_message("⚠️ Die Aufgabe hat länger als 10 Minuten gedauert — abgebrochen. "
                              "Für so große Sachen besser eine Claude-Code-Session am Mac nutzen.")
        finally:
            done.set()
            t.join(timeout=5)
            self.set_typing(False)
            if map_path:
                try:
                    os.remove(map_path)   # flüchtige PII-Map nach dem Lauf löschen
                except OSError:
                    pass
            # #110: ganz zum Schluss — die Antwort ist längst raus, das Tippen beendet.
            nach = getattr(self, "_merk_nachlauf", None)
            if nach:
                self._merk_nachlauf = None
                self._merken(*nach)

    def _run_foreign(self, plan, prompt, system, messages_label, kind, mapping, verify=None,
                     tools=None):
        """Fremd-Modell (Ollama/OpenAI/Azure) über llm_runner (venv): Text holen,
        Surrogate zurückübersetzen, in den Chat senden. Mit `tools` läuft der Runner
        in einer Werkzeugschleife — begrenzt auf den Arbeitsordner des Agenten."""
        label = f"{plan['provider']}/{plan['model_id']}"
        req_d = {"provider": plan["provider"], "base_url": plan["base_url"],
                 "key": plan.get("key", ""), "model_id": plan["model_id"],
                 "prompt": prompt, "system": system or ""}
        run_timeout = 180
        tools = tools or []
        if any(t in ("Bash", "Read", "Write") for t in tools):
            req_d["tools"] = True
            req_d["workdir"] = f"{WORKSPACE}/agent-{self.bot_name}"
        if "Browser" in tools:
            req_d["browser"] = True      # nur Lesen/Navigieren (open_page/click_link)
        if req_d.get("tools") or req_d.get("browser"):
            req_d["timeout"] = 120       # pro Modell-Aufruf innerhalb der Schleife
            run_timeout = 600            # gesamte Werkzeugschleife
            # #83: Surrogat-Map mitgeben — der Runner bereinigt Tool-Ergebnisse (Secrets raus,
            # bekannte PII → dieselben Surrogate wie im Prompt), bevor das Fremd-Modell sie sieht.
            if isinstance(mapping, dict) and mapping.get("s2r"):
                req_d["pii_map"] = {"s2r": mapping["s2r"]}
        req = json.dumps(req_d)
        start = time.time()
        try:
            r = subprocess.run([VENV_PY, f"{BOT_DIR}/llm_runner.py"], input=req,
                               capture_output=True, text=True, timeout=run_timeout,
                               env=dict(os.environ))
        except subprocess.TimeoutExpired:
            self.send_message(f"⚠️ Das Modell {label} hat zu lange gebraucht — abgebrochen.")
            return
        dur = int((time.time() - start) * 1000)
        try:
            out = json.loads(r.stdout)
        except ValueError:
            out = {"error": (r.stderr or r.stdout or "unbekannter Fehler")[-200:]}
        if "text" in out:
            raw, foot = out["text"], ""     # raw bleibt vorerst im Surrogat-Raum
            if verify and verify_loop:       # A1 (#46): zweites Modell prüft, bevor gesendet wird
                v_model = verify[1]
                # #101: Verlauf mitgeben — im Surrogat-Raum, wie Frage und Antwort hier auch.
                raw, revised = self._verify_text(v_model, redact_text(messages_label),
                                                 redact_text(raw), self.history_block())
                foot = verify_loop.footer(v_model, revised)
            text = redact_text(reidentify(raw, mapping)) + foot   # Surrogate→echt, dann Secrets maskieren
            self.send_message(text)
            for a in out.get("actions", [])[:30]:                 # Audit: jede Werkzeug-Aktion
                log(f"[{self.bot_name}] 🔧 {a}")
            log(f"[{self.bot_name}] {label} fertig ({dur}ms, {len(text)} Zeichen)")   # #18: kein Inhalt
            rc, rec = 0, text[:4000]
        else:
            self.send_message("⚠️ " + str(out.get("error", "Das Modell konnte gerade nicht antworten."))
                              + " 👉 Bitte gleich nochmal versuchen; bleibt es so, prüf den Provider "
                              "im Dashboard (Tab »Modelle & Provider«).")
            log(f"[{self.bot_name}] {label} Fehler: {str(out.get('error', ''))[:160]}")
            rc, rec = 1, "FEHLER: " + str(out.get("error", ""))[:300]
        if sessions_db:
            try:
                sessions_db.record(self.bot_name, redact_text(messages_label), rec, rc,
                                   dur, 0, 0, kind, label)
            except Exception:
                pass

    # ---------- A1 (#46) Verifikations-Schleife ----------
    def _call_model_text(self, plan, system, user):
        """Ruft ein Modell (Claude ODER Fremd) mit system+user auf und gibt reinen Text
        zurück — OHNE Werkzeuge, OHNE Re-ID (läuft im Surrogat-Raum). fail-open: None bei
        jedem Fehler, damit die Prüfung die Antwort nie verschluckt."""
        try:
            if plan.get("kind") == "foreign":
                req = json.dumps({"provider": plan["provider"], "base_url": plan["base_url"],
                                  "key": plan.get("key", ""), "model_id": plan["model_id"],
                                  "prompt": user, "system": system, "max_tokens": 1024})
                r = subprocess.run([VENV_PY, f"{BOT_DIR}/llm_runner.py"], input=req,
                                   capture_output=True, text=True, timeout=180,
                                   env=dict(os.environ))
                return json.loads(r.stdout).get("text")
            # Claude als Prüfer: headless, keine Werkzeuge
            cmd = [CLAUDE, "-p", f"{system}\n\n{user}", "--output-format", "json"]
            if plan.get("model"):
                cmd += ["--model", plan["model"]]
            with CLAUDE_SLOTS:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                                   cwd=WORKSPACE, env=dict(os.environ))
            return str(json.loads(r.stdout).get("result", "")) or None
        except Exception as e:
            log(f"[{self.bot_name}] Verifier-Aufruf fehlgeschlagen (fail-open): {e}")
            return None

    def _merken(self, frage, antwort, mapping):
        """#110: Nach dem Senden prüfen, ob etwas dauerhaft Merkenswertes gefallen ist.

        Leitplanken:
        - läuft NACH der Antwort → verzögert nichts
        - Fair-Use: zählt als automatischer Lauf, damit es kein Kontingent frisst
        - der Extraktor arbeitet im Surrogat-Raum (er sieht dieselben Pseudonyme wie
          der Hauptlauf); GESPEICHERT wird re-identifiziert — das Gedächtnis ist lokal
          und soll echte Namen enthalten, sonst ist es beim nächsten Mal wertlos
        - Dublettenprüfung gegen den Bestand, sonst steht derselbe Fakt zehnmal drin
        - sichtbar: der Fakt wird mit 🧠 im Chat gemeldet und ist im Dashboard löschbar
        """
        if not merker or not memory_enabled():
            return
        if throttle:
            try:
                if not throttle.allow("merken"):
                    return
            except Exception:
                pass
        try:
            system, user = merker.extraktor_prompts(frage, antwort)
            plan = {"kind": "claude", "model": MERK_MODELL}
            fakt = merker.auswerten(self._call_model_text(plan, system, user))
            if not fakt:
                return
            fakt = redact_text(reidentify(fakt, mapping))       # echte Namen, keine Secrets
            bestehende = self._bekannte_fakten()
            vf = None
            try:
                import embeddings
                if embeddings.status()[0]:
                    vf = embeddings.embed
            except Exception:
                pass
            if merker.ist_dublette(fakt, bestehende, vektor_fn=vf):
                log(f"[{self.bot_name}] Merken: schon bekannt, nichts gespeichert")
                return
            r = subprocess.run([sys.executable, f"{BOT_DIR}/memory.py", "add", fakt],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                log(f"[{self.bot_name}] Merken fehlgeschlagen: {r.stderr[:120]}")
                return
            if throttle:
                try:
                    throttle.record("merken")
                except Exception:
                    pass
            log(f"[{self.bot_name}] Gemerkt ({len(fakt)} Zeichen)")   # #18: kein Inhalt ins Log
            self.send_message(f"{merker.MARK} Gemerkt: {fakt}")
        except Exception as e:
            log(f"[{self.bot_name}] Merken übersprungen: {e}")

    def _bekannte_fakten(self, n=200):
        """Bestehende Fakten als Textliste — Grundlage der Dublettenprüfung."""
        try:
            r = subprocess.run([sys.executable, f"{BOT_DIR}/memory.py", "list", "-n", str(n)],
                               capture_output=True, text=True, timeout=30)
            aus = []
            for z in (r.stdout or "").splitlines():
                t = z.strip()
                if t.startswith("["):
                    teil = t.split(")", 1)
                    if len(teil) == 2:
                        aus.append(teil[1].strip())
            return aus
        except Exception:
            return []

    def _verify_text(self, v_model, question, answer, verlauf=""):
        """Prüferlauf. Rückgabe: (final_text, revised: bool)."""
        v_plan = providers.resolve(v_model) if (providers and v_model) else {"kind": "claude", "model": None}
        system, user = verify_loop.verifier_prompts(question, answer, verlauf)
        vout = self._call_model_text(v_plan, system, user)
        final, revised = verify_loop.interpret(vout, answer)
        log(f"[{self.bot_name}] verifiziert von {v_model or 'claude'} "
            f"({'überarbeitet' if revised else 'ok'})")
        return final, revised

    def _verify_and_send(self, verify, question, answer, mapping, used_fallback):
        """Claude-Worker-Pfad: Antwort prüfen, re-identifizieren, senden (+ Fußzeile).
        #63: Der Prüfer bekommt Frage UND Antwort RE-IDENTIFIZIERT — der Worker hat via
        Werkzeugen ohnehin echte Daten gesehen, seine Antwort mischt daher echte Namen mit
        Surrogaten aus dem Prompt. Nur mit einheitlicher (echter) Sicht kann der Prüfer
        Konsistenz beurteilen, statt Pseudonym-Mischungen als »falsche Mail« zu verwerfen.
        (Fremd-LLM-Worker sind toollos → deren Prüfung bleibt im Surrogat-Raum.)"""
        v_model = verify[1]
        q_real = redact_text(reidentify(question, mapping))
        a_real = redact_text(reidentify(answer, mapping))
        anhang = "\n(lief über deinen Claude-API-Key)" if used_fallback else ""
        # #100: Smalltalk (»hä?«, »danke«) braucht keinen zweiten Modell-Lauf.
        if verify_loop.trivial(q_real, a_real):
            self.send_message(a_real + anhang)
            return
        # #100: Erst senden (Antwort steht nach dem Worker-Lauf sofort im Chat), dann
        # prüfen — das Ergebnis aktualisiert die Nachricht per Bearbeitung (✓/✎).
        eid = self.send_message(a_real + anhang)
        # #101: Der Prüfer bekommt den Gesprächsverlauf — ohne ihn beurteilte er
        # Smalltalk ohne Kontext und »korrigierte« völlig richtige Antworten.
        verlauf = redact_text(reidentify(self.history_block(), mapping))
        final, revised = self._verify_text(v_model, q_real, a_real, verlauf)
        text = (redact_text(reidentify(final, mapping)) if revised else a_real) \
            + verify_loop.footer(v_model, revised) + anhang
        self.edit_message(eid, text)

    # ---------- Loop ----------
    def run(self):
        try:
            since = self.api("/_matrix/client/v3/sync?timeout=0&filter=%7B%22room%22%3A%7B%22timeline%22%3A%7B%22limit%22%3A1%7D%7D%7D")["next_batch"]
        except Exception as e:
            log(f"[{self.bot_name}] Start-Sync fehlgeschlagen: {e} — Thread endet")
            return
        log(f"[{self.bot_name}] Listener gestartet, Raum {self.room}")
        while not self.stop_event.is_set():
            try:
                data = self.api(f"/_matrix/client/v3/sync?since={urllib.parse.quote(since)}&timeout=30000",
                                timeout=45)
                since = data["next_batch"]
                events = (data.get("rooms", {}).get("join", {}).get(self.room, {})
                          .get("timeline", {}).get("events", []))
                # #65: »ja«/»nein« auf eine Sicherheits-Rückfrage wurde dort schon
                # verbraucht — nicht zusätzlich als normalen Chat beantworten.
                broker_antworten = set()
                if permission_broker:
                    try:
                        broker_antworten = set(permission_broker.used_replies())
                    except Exception:
                        pass
                new = [e for e in events
                       if e.get("type") == "m.room.message"
                       and (e.get("sender") == OWNER or self._vom_dashboard(e))
                       and e["content"].get("body")
                       and e.get("event_id") not in self.seen_events    # #86: kein Doppel
                       and e.get("event_id") not in broker_antworten]
                # #90 Dock: Dashboard-Eingaben tragen den Rohtext im Marker —
                # den verarbeiten, nicht den 🖥️-Anzeigetext.
                for e in new:
                    m = (e.get("content") or {}).get(DASHBOARD_MARKER) or {}
                    if m.get("text"):
                        e["content"]["body"] = str(m["text"])
                    # Bild/Datei? Herunterladen und dem Modell den Pfad nennen —
                    # sonst sieht es nur den Dateinamen (»IMG_1234.jpg«) und rät.
                    if anhaenge:
                        try:
                            an = anhaenge.empfange(e, self.hs, self.token, log)
                            if an:
                                text = e["content"].get("body") or ""
                                if an["pfad"] and text.strip() == an["name"]:
                                    text = ""       # nur die Datei, kein eigener Text
                                e["content"]["body"] = (text + "\n" + an["hinweis"]).strip()
                        except Exception as ex:
                            log(f"[{self.bot_name}] Anhang übersprungen: {ex}")
                if new:
                    # Erst als gesehen verbuchen, dann antworten — liefert der Server die
                    # Events nach einem Netzfehler erneut, gibt es keine zweite Antwort.
                    self.seen_events.extend(e["event_id"] for e in new)
                    self.answer([e["content"]["body"] for e in new], new[-1]["event_id"])
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    log(f"[{self.bot_name}] Token ungültig (401) — 5 min Pause")
                    self.stop_event.wait(300)
                else:
                    log(f"[{self.bot_name}] HTTP {e.code} — 10s Pause")
                    self.stop_event.wait(10)
            except Exception as e:
                log(f"[{self.bot_name}] Fehler: {e} — 10s Pause")
                self.stop_event.wait(10)
        log(f"[{self.bot_name}] Thread gestoppt")


def load_bot_sessions():
    sessions = {}
    try:
        bots = json.load(open(BOTS_FILE)).get("bots", [])
    except (OSError, ValueError):
        bots = []
    for b in bots:
        if b.get("enabled"):
            if b.get("via") == "main":
                # Massentauglicher Weg: Agent wohnt in einem eigenen RAUM des Operator-Kontos —
                # kein eigenes Matrix-Konto, kein Admin, kein Passwort nötig.
                tok = keychain_token("matrix-owner", CREDS.get("access_token", ""))
                if tok:
                    sessions[b["agent"]] = BotSession(
                        "agent", b["agent"], CREDS["homeserver"], tok,
                        b["room_id"], CREDS["user_id"])
                continue
            tok = keychain_token("matrix-bot-" + b["agent"], b["access_token"])
            if not tok:
                log(f"[{b['agent']}] Kein Token im Keychain — Bot übersprungen")
                continue
            sessions[b["agent"]] = BotSession(
                "agent", b["agent"], CREDS["homeserver"], tok,
                b["room_id"], b["user_id"])
    return sessions


def ensure_workspace_location():
    """#106: Einmaliger Umzug des Arbeitsordners aus ~/.claude heraus. Muss VOR
    allem anderen laufen — Claude Code sperrt Schreibzugriffe unter ~/.claude, und
    genau dort lag der Ordner, in dem Agenten ihre Ergebnisse ablegen sollen."""
    try:
        _plat.workspace_migrieren(log=log)
    except Exception as e:
        log(f"Arbeitsordner-Umzug übersprungen ({e})")


def ensure_private_workspace():
    """#18: Der Arbeitsordner gehört nur dir. Dort legen Agenten ihre Ergebnisse ab —
    das können Auswertungen mit echten Kundendaten sein. Auf einem Rechner mit mehreren
    Konten wäre »für alle lesbar« falsch. Wird bei jedem Start geradegezogen."""
    for pfad in (WORKSPACE, f"{WORKSPACE}/.claude"):
        try:
            if os.path.isdir(pfad) and (os.stat(pfad).st_mode & 0o077):
                os.chmod(pfad, 0o700)
                log(f"Arbeitsordner abgesichert: {os.path.basename(pfad) or 'workspace'} nur für dich lesbar")
        except OSError:
            pass


def ensure_tool_hook():
    """#65: Den PreToolUse-Hook in workspace/.claude/settings.json eintragen — mit dem
    Interpreter, unter dem der Listener selbst läuft (plattformübergreifend korrekt).
    Selbstheilend: stimmt der Eintrag nicht mehr, wird er geradegezogen."""
    hook_py = f"{BOT_DIR}/claude_tool_hook.py"
    if not os.path.exists(hook_py):
        return
    pfad = f"{WORKSPACE}/.claude/settings.json"
    soll = [{"matcher": "*", "hooks": [
        {"type": "command", "command": f'"{sys.executable}" "{hook_py}"', "timeout": 200}]}]
    try:
        cfg = json.load(open(pfad))
    except (OSError, ValueError):
        cfg = {}
    if cfg.get("hooks", {}).get("PreToolUse") == soll:
        return
    cfg.setdefault("hooks", {})["PreToolUse"] = soll
    try:
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        tmp = pfad + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, pfad)
        log("Sicherheits-Rückfrage (PreToolUse-Hook) eingerichtet")
    except OSError as e:
        log(f"Hook-Registrierung fehlgeschlagen: {e}")


_health_state = {"last": 0.0}


def _claude_health_tick(owner, interval=900):
    """#59: Läuft der Claude-Zugang noch? Nachgesehen wird nur, wenn längere Zeit kein
    echter Lauf Beweis geliefert hat (spart Aufrufe). Bei abgelaufenem Login geht EINE
    freundliche Vorwarnung an den Owner — kein Spam, erst nach Erholung wieder."""
    if not claude_health:
        return
    now = time.time()
    if now - _health_state["last"] < interval:
        return
    _health_state["last"] = now
    try:
        if claude_health.needs_probe(now):
            zustand, _ = claude_health.probe()
            log(f"Claude-Login geprüft: {zustand}")
        if claude_health.should_warn():
            owner.send_message(claude_health.WARN_TEXT)
            claude_health.mark_warned()
            log("Claude-Login abgelaufen — Vorwarnung an den Owner geschickt")
    except Exception as e:
        log(f"Claude-Login-Prüfung fehlgeschlagen: {e}")


# ---------- #81: Owner-DM-Räume automatisch finden & Einladungen annehmen ----------
# Hintergrund: Der Anzeigename des Operator-Kontos ist „Operator" → in Element heißt JEDER
# Direktchat mit ihm „Operator". Legt Element (bekannter Client-Bug) einen ZWEITEN „Operator"-
# DM an, tippt der Nutzer dort ins Leere, wenn der Operator nicht Mitglied ist. Diese Helfer
# lassen den Operator dem Owner in alle seine DM-Räume folgen — selbstheilend, aber streng
# abgesichert: NUR Einladungen des Owners, NUR 2-Personen-DMs, NIE Agenten-/Gruppenräume.
def _owner_api(hs, token, path, method="GET", body=None, timeout=30):
    req = urllib.request.Request(
        hs + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _room_members(hs, token, room_id):
    try:
        m = _owner_api(hs, token,
                       f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}/joined_members")
        return set(m.get("joined", {}).keys())
    except Exception:
        return set()


def discover_owner_dm_rooms(hs, token, blocked_rooms):
    """Alle beigetretenen 2-Personen-DMs (self↔OWNER), die noch nicht bedient werden.
    Heilt bestehende Element-Doppel-DMs ohne neue Einladung. Sicherheits-Gate: exakt
    {OWNER, self} — nie Agenten-Räume, nie Gruppen."""
    me = CREDS["user_id"]
    found = []
    if not OWNER:
        return found
    try:
        joined = _owner_api(hs, token, "/_matrix/client/v3/joined_rooms").get("joined_rooms", [])
    except Exception as e:
        log(f"Owner-Raum-Suche fehlgeschlagen: {e}")
        return found
    for rid in joined:
        if rid in blocked_rooms:
            continue
        if _room_members(hs, token, rid) == {me, OWNER}:
            found.append(rid)
    return found


def accept_owner_invites(hs, token, blocked_rooms):
    """Nimmt AUSSCHLIESSLICH Einladungen des OWNER an (Anti-Spam / Anti-Prompt-Injection).
    Fremde Einladungen werden ignoriert; versehentlich beigetretene Nicht-DMs wieder verlassen.
    Gibt die room_ids neuer Owner-DM-Chats zurück."""
    me = CREDS["user_id"]
    new_rooms = []
    if not OWNER:
        return new_rooms
    try:
        data = _owner_api(hs, token, "/_matrix/client/v3/sync?timeout=0")
    except Exception:
        return new_rooms
    for rid, info in data.get("rooms", {}).get("invite", {}).items():
        inviter = None
        for e in info.get("invite_state", {}).get("events", []):
            if (e.get("type") == "m.room.member" and e.get("state_key") == me
                    and e.get("content", {}).get("membership") == "invite"):
                inviter = e.get("sender")
        if inviter != OWNER:
            log(f"Einladung zu {rid} ignoriert (nicht vom Owner: {inviter})")
            continue
        try:
            _owner_api(hs, token,
                       f"/_matrix/client/v3/rooms/{urllib.parse.quote(rid)}/join",
                       method="POST", body={})
        except Exception as e:
            log(f"Auto-Join {rid} fehlgeschlagen: {e}")
            continue
        if rid not in blocked_rooms and _room_members(hs, token, rid) == {me, OWNER}:
            log(f"Owner-Einladung angenommen — neuer Operator-Chat {rid}")
            new_rooms.append(rid)
        else:
            log(f"Beigetretener Raum {rid} ist kein Owner-DM — wird wieder verlassen")
            try:
                _owner_api(hs, token,
                           f"/_matrix/client/v3/rooms/{urllib.parse.quote(rid)}/leave",
                           method="POST", body={})
            except Exception:
                pass
    return new_rooms


def main():
    owner_token = keychain_token("matrix-owner", CREDS["access_token"])
    if not owner_token:
        log("FATAL: Owner-Token weder im Keychain noch in credentials.json")
        return
    ensure_workspace_location()        # #106: Arbeitsordner aus ~/.claude heraus
    ensure_private_workspace()         # #18: Arbeitsordner privat halten
    ensure_tool_hook()                 # #65: Rückfrage-Hook aktuell halten
    hs = CREDS["homeserver"]
    primary_room = CREDS["room_id"]
    owner = BotSession("owner", "owner", hs, owner_token, primary_room, CREDS["user_id"])
    owner.start()
    agents = load_bot_sessions()
    for s in agents.values():
        s.start()

    # #81: mehrere Owner-Chats bedienen. `owner` (primärer Raum) bleibt der Kanal für
    # proaktive Läufe (Mail/Cron/Ereignisse); zusätzliche DM-Räume reagieren nur auf Chats.
    owner_rooms = {primary_room: owner}

    def start_owner_room(rid):
        if rid in owner_rooms:
            return
        s = BotSession("owner", "owner", hs, owner_token, rid, CREDS["user_id"])
        s.start()
        owner_rooms[rid] = s
        log(f"Zusätzlicher Operator-Chat wird bedient: {rid}")

    def agent_rooms():
        return {a.room for a in agents.values()}

    for rid in discover_owner_dm_rooms(hs, owner_token, set(owner_rooms) | agent_rooms()):
        start_owner_room(rid)

    last_mtime = 0
    last_owner_scan = time.time()
    try:
        import os
        last_mtime = os.path.getmtime(BOTS_FILE)
    except OSError:
        pass
    while True:
        time.sleep(5)
        # #81: regelmäßig neue Owner-Einladungen annehmen + Doppel-DMs entdecken (alle ~30 s)
        if time.time() - last_owner_scan >= 30:
            last_owner_scan = time.time()
            try:
                blocked = set(owner_rooms) | agent_rooms()
                fresh = accept_owner_invites(hs, owner_token, blocked)
                fresh += discover_owner_dm_rooms(hs, owner_token, blocked | set(fresh))
                for rid in fresh:
                    start_owner_room(rid)
            except Exception as e:
                log(f"Owner-Raum-Watcher: {e}")
        try:
            import os
            m = os.path.getmtime(BOTS_FILE)
        except OSError:
            m = 0
        if m != last_mtime:
            last_mtime = m
            log("bots.json geändert — Agenten-Threads werden neu aufgebaut")
            for s in agents.values():
                s.stop_event.set()
            agents = load_bot_sessions()
            for s in agents.values():
                s.start()
        if cron_runner:
            try:
                cron_runner.tick(owner, agents, log)
            except Exception as e:
                log(f"Automations-Prüfung fehlgeschlagen: {e}")
        if triggers:
            try:
                # #47: wartende Ereignisse proaktiv abarbeiten (je Ereignis ein Thread)
                triggers.drain(owner, agents, log,
                               run=lambda s, n, p: threading.Thread(
                                   target=s.run_event, args=(n, p), daemon=True).start())
            except Exception as e:
                log(f"Ereignis-Prüfung fehlgeschlagen: {e}")
        _mail_watch_tick(log)
        _claude_health_tick(owner)
        datenschutz_angebot(owner)     # #116: einmalig anbieten, sobald alles läuft
        if retention:                  # #18: einmal täglich alte Daten aufräumen
            try:
                if retention.faellig():
                    retention.aufraeumen(log)
            except Exception as e:
                log(f"Aufräumen fehlgeschlagen: {e}")
        if audit_log:                    # #49: Audit-Log periodisch versiegeln (single writer)
            try:
                now_s = time.time()
                if now_s - _audit_seal_state["last"] >= 300:
                    _audit_seal_state["last"] = now_s
                    audit_log.seal()
            except Exception as e:
                log(f"Audit-Siegel fehlgeschlagen: {e}")


if __name__ == "__main__":
    main()
