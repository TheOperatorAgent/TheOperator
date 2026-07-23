#!/usr/bin/env python3
"""Operator Listener v2 — Matrix-Multiplexer.

Ein Daemon, ein Thread je Matrix-Bot-Account:
- Owner-Bot (credentials.json): VERHALTEN.md + Gedächtnis-Recall, volles Verhalten wie v1
- Agenten-Bots (bots.json): je ein veröffentlichter Agent mit eigenem Account/Raum,
  Prompt = Agenten-MD, Werkzeuge = Frontmatter ∩ Owner-Freigabe, Modell aus Frontmatter

bots.json wird zur Laufzeit überwacht (mtime) — Publish/Unpublish greift ohne Neustart.
100 % Python-Standardbibliothek (läuft auch ohne Dashboard-venv).
"""
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
WORKSPACE = f"{BOT_DIR}/workspace"

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


CLAUDE = CREDS.get("claude_bin") or shutil.which("claude") or "claude"
OWNER = CREDS.get("owner_id", "")
OWNER_TOOLS = CREDS.get("allowed_tools", ["Bash", "Read", "WebFetch", "WebSearch", "Agent", "Skill"])
# Owner-Verify (#46): opt-in, live umschaltbar über dashboard.json (owner_verify_cfg() liest frisch).
CLAUDE_SLOTS = threading.Semaphore(2)

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
        self.user_enc = urllib.parse.quote(user_id)
        self.stop_event = threading.Event()

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
            self.api(f"/_matrix/client/v3/rooms/{self.room_enc}/send/m.room.message/{time.time_ns()}",
                     method="PUT", body={"msgtype": "m.text", "body": text}, timeout=15)
        except Exception as e:
            log(f"[{self.bot_name}] Senden fehlgeschlagen: {e}")

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
            system = (agent["body"].strip() + "\n\nWICHTIG: Du hast KEINE Werkzeuge — nur Text. "
                      "Antworte direkt, knapp und auf Deutsch; deine Antwort wird 1:1 in den "
                      "Matrix-Chat gesendet. Keine Erklärungen über dich selbst.")
            user = ((history + "\n") if history else "") \
                + f"{OWNER.split(':')[0]} schreibt dir:\n{messages_p}"
            return user, [], agent.get("model"), mapping, messages_p, system, verify
        # Claude-Agent (voller Werkzeugkasten)
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
    def answer(self, bodies, last_event_id):
        prompt, tools, model, mapping, msg_rec, system, verify = self.build(bodies)
        self.mark_read(last_event_id)
        if mapping is False:
            self.send_message("⚠️ Der Pseudonymisierungs-Dienst ist gerade nicht verfügbar. "
                              "Aus Datenschutzgründen habe ich deine Nachricht NICHT an das "
                              "Sprachmodell geschickt. Prüfen oder (bewusst) deaktivieren: "
                              f"{dashboard_link()} — Einmal-Link, 10 Min gültig, funktioniert "
                              "auf dem Rechner, auf dem dein Operator läuft.")
            return
        self.execute(prompt, tools, model, msg_rec, kind="chat", mapping=mapping,
                     system=system, verify=verify)

    def run_automation(self, job):
        framing = f"[Automation „{job['name']}“ wurde planmäßig ausgelöst] {job['prompt']}"
        prompt, tools, model, mapping, msg_rec, system, verify = self.build([framing])
        if mapping is False:
            log(f"[{self.bot_name}] Automation '{job['name']}' abgebrochen (Pseudonymisierung aus)")
            return
        log(f"[{self.bot_name}] Automation '{job['name']}' startet")
        self.execute(prompt, tools, model, msg_rec, kind="cron", mapping=mapping,
                     system=system, verify=verify)

    def run_event(self, name, framing):
        """#47: proaktiver Lauf, von einem externen Ereignis ausgelöst (triggers.drain).
        Gleicher Pfad wie Chat/Automation — Pseudonymisierung, Werkzeuge, Audit inklusive."""
        prompt, tools, model, mapping, msg_rec, system, verify = self.build([framing])
        if mapping is False:
            log(f"[{self.bot_name}] Ereignis '{name}' abgebrochen (Pseudonymisierung aus)")
            return
        log(f"[{self.bot_name}] Ereignis '{name}' startet proaktiven Lauf")
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
                self._run_foreign(plan, prompt, system, messages_label, kind, mapping, verify)
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
                with CLAUDE_SLOTS:
                    rr = subprocess.run(cmd, capture_output=True, text=True,
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
            # M4 — Auto-Fallback: Abo am Limit + hinterlegter API-Key → genau EIN Retry mit Key
            if r.returncode != 0 and providers:
                low = (r.stdout + r.stderr).lower()
                fk = providers.fallback_key()
                if fk and any(k in low for k in ("limit", "429", "rate", "overloaded", "quota")):
                    log(f"[{self.bot_name}] Abo-Limit erkannt → Wiederholung mit Anthropic-API-Key")
                    run_env["ANTHROPIC_API_KEY"] = fk
                    r, result, tok_in, tok_out, dur = _claude_run(run_env)
                    used_fallback = (r.returncode == 0)

            result = redact_text(result)
            messages_label = redact_text(messages_label)
            log(f"[{self.bot_name}] Claude fertig (rc={r.returncode}, {dur}ms, "
                f"{tok_out} out-tok{', Fallback-Key' if used_fallback else ''}): {result[-200:]}")
            if sessions_db:
                try:
                    sessions_db.record(self.bot_name, messages_label, result, r.returncode,
                                       dur, tok_in, tok_out, kind, plan.get("model") or "inherit")
                except Exception as e:
                    log(f"Session-Recording fehlgeschlagen: {e}")
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
                self.send_message("ℹ️ (Lief über deinen Claude-API-Key, weil das Abo gerade am Limit war.)")
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

    def _run_foreign(self, plan, prompt, system, messages_label, kind, mapping, verify=None):
        """Fremd-Modell (Ollama/OpenAI/Azure) über llm_runner (venv): Text holen,
        Surrogate zurückübersetzen, in den Chat senden. Keine lokalen Werkzeuge."""
        label = f"{plan['provider']}/{plan['model_id']}"
        req = json.dumps({"provider": plan["provider"], "base_url": plan["base_url"],
                          "key": plan.get("key", ""), "model_id": plan["model_id"],
                          "prompt": prompt, "system": system or ""})
        start = time.time()
        try:
            r = subprocess.run([VENV_PY, f"{BOT_DIR}/llm_runner.py"], input=req,
                               capture_output=True, text=True, timeout=180, env=dict(os.environ))
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
                raw, revised = self._verify_text(v_model, redact_text(messages_label), redact_text(raw))
                foot = verify_loop.footer(v_model, revised)
            text = redact_text(reidentify(raw, mapping)) + foot   # Surrogate→echt, dann Secrets maskieren
            self.send_message(text)
            log(f"[{self.bot_name}] {label} fertig ({dur}ms): {text[:120]}")
            rc, rec = 0, text[:4000]
        else:
            self.send_message(f"⚠️ {label}: {out.get('error', 'Fehler beim Modell')}. "
                              "Provider im Dashboard prüfen (Tab »Modelle & Provider«).")
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

    def _verify_text(self, v_model, question, answer):
        """Prüferlauf im Surrogat-Raum. Rückgabe: (final_text, revised: bool)."""
        v_plan = providers.resolve(v_model) if (providers and v_model) else {"kind": "claude", "model": None}
        system, user = verify_loop.verifier_prompts(question, answer)
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
        final, revised = self._verify_text(v_model, q_real, a_real)
        text = redact_text(reidentify(final, mapping)) + verify_loop.footer(v_model, revised)
        if used_fallback:
            text += "\n(lief über deinen Claude-API-Key)"
        self.send_message(text)

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
                new = [e for e in events
                       if e.get("type") == "m.room.message"
                       and e.get("sender") == OWNER
                       and e["content"].get("body")]
                if new:
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
            tok = keychain_token("matrix-bot-" + b["agent"], b["access_token"])
            if not tok:
                log(f"[{b['agent']}] Kein Token im Keychain — Bot übersprungen")
                continue
            sessions[b["agent"]] = BotSession(
                "agent", b["agent"], CREDS["homeserver"], tok,
                b["room_id"], b["user_id"])
    return sessions


def main():
    owner_token = keychain_token("matrix-owner", CREDS["access_token"])
    if not owner_token:
        log("FATAL: Owner-Token weder im Keychain noch in credentials.json")
        return
    owner = BotSession("owner", "owner", CREDS["homeserver"], owner_token,
                       CREDS["room_id"], CREDS["user_id"])
    owner.start()
    agents = load_bot_sessions()
    for s in agents.values():
        s.start()
    last_mtime = 0
    try:
        import os
        last_mtime = os.path.getmtime(BOTS_FILE)
    except OSError:
        pass
    while True:
        time.sleep(5)
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
