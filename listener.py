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
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

BOT_DIR = "/Users/michi/.claude/matrix-bot"


def keychain_token(account, fallback):
    """Token aus dem macOS-Schlüsselbund; Datei-Wert nur als Fallback (Altbestand)."""
    if fallback != "keychain":
        return fallback
    r = subprocess.run(["security", "find-generic-password", "-s", "the-operator",
                        "-a", account, "-w"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


CREDS = json.load(open(f"{BOT_DIR}/credentials.json"))
BOTS_FILE = f"{BOT_DIR}/bots.json"
CRON_FILE = f"{BOT_DIR}/cron.json"
WORKSPACE = f"{BOT_DIR}/workspace"

import sys as _sys
_sys.path.insert(0, BOT_DIR)
try:
    import sessions as sessions_db
except Exception:
    sessions_db = None
try:
    import cron_runner
except Exception:
    cron_runner = None
CLAUDE = CREDS.get("claude_bin", "/Users/michi/.npm-global/bin/claude")
OWNER = CREDS.get("owner_id", "@michi:matrix.vonaschenbrenner.bayern")
OWNER_TOOLS = CREDS.get("allowed_tools", ["Bash", "Read", "WebFetch", "WebSearch", "Agent", "Skill"])
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

AGENT_PROMPT = """Du bist der Agent „{name}" und läufst als eigenständiger Matrix-Bot. \
Dein Auftraggeber ist {owner}. Dein Verhalten:

{body}

---
{history}
{owner_short} hat dir soeben geschrieben:

{messages}

Erledige/beantworte das jetzt. Sende deine Antwort am Ende zwingend per Bash:
python3 {bot_dir}/send.py --bot {name} "DEINE ANTWORT"
(mehrzeilig: Text per stdin an send.py --bot {name}). Keine Secrets in den Chat."""


def log(msg):
    print(f"[{time.strftime('%F %T')}] {msg}", flush=True)


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
    return {"tools": tools, "model": fm.get("model"), "body": m.group(2).strip()}


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
            r = subprocess.run(["python3", f"{BOT_DIR}/memory.py", "search", text, "-k", str(k)],
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
            lines.append(f"Michi: {msg[:400]}")
            lines.append(f"Du: {res[:400]}")
        return ("Bisheriger Gesprächsverlauf (chronologisch, zur Einordnung von "
                "Rückbezügen wie 'darüber' oder 'das'):\n" + "\n".join(lines) + "\n\n")

    def build(self, bodies):
        messages = "\n".join(f"- {b}" for b in bodies)
        if self.kind == "owner":
            try:
                verhalten = open(f"{BOT_DIR}/VERHALTEN.md").read()
            except OSError:
                verhalten = "(VERHALTEN.md fehlt — antworte hilfsbereit auf Deutsch und sende per python3 ~/.claude/matrix-bot/send.py)"
            return (OWNER_PROMPT.format(verhalten=verhalten, messages=messages,
                                        history=self.history_block(),
                                        memories=self.recall(" ".join(bodies))),
                    OWNER_TOOLS, None)
        agent = parse_agent_md(self.bot_name) or {"tools": [], "model": None,
                                                  "body": "(Agenten-Datei fehlt)"}
        tools = [t for t in agent["tools"] if t in OWNER_TOOLS or t == "Read"]
        if "Bash" not in tools:
            tools.append("Bash")  # noetig fuer send.py; Agent-Frontmatter bleibt die inhaltliche Leitplanke
        model = agent["model"] if agent["model"] in ("haiku", "sonnet", "opus") else None
        return (AGENT_PROMPT.format(name=self.bot_name, owner=OWNER,
                                    owner_short=OWNER.split(":")[0],
                                    body=agent["body"], messages=messages,
                                    history=self.history_block(),
                                    bot_dir=BOT_DIR),
                tools, model)

    # ---------- Claude ----------
    def answer(self, bodies, last_event_id):
        prompt, tools, model = self.build(bodies)
        self.mark_read(last_event_id)
        self.execute(prompt, tools, model, " | ".join(bodies), kind="chat")

    def run_automation(self, job):
        framing = f"[Automation „{job['name']}“ wurde planmäßig ausgelöst] {job['prompt']}"
        prompt, tools, model = self.build([framing])
        log(f"[{self.bot_name}] Automation '{job['name']}' startet")
        self.execute(prompt, tools, model, framing, kind="cron")

    def execute(self, prompt, tools, model, messages_label, kind):
        log(f"[{self.bot_name}] Claude wird geweckt ({kind})"
            + (f" (model={model})" if model else ""))
        done = threading.Event()

        def keep_typing():
            while not done.is_set():
                self.set_typing(True)
                done.wait(20)

        t = threading.Thread(target=keep_typing, daemon=True)
        t.start()
        cmd = [CLAUDE, "-p", prompt, "--output-format", "json", "--allowedTools", *tools]
        if model:
            cmd += ["--model", model]
        # Vom Nutzer im Dashboard konfigurierte MCP-Server explizit laden
        mcp_file = f"{WORKSPACE}/.mcp.json"
        try:
            import os as _os
            if _os.path.getsize(mcp_file) > 20:
                cmd += ["--mcp-config", mcp_file]
        except OSError:
            pass
        start = time.time()
        try:
            with CLAUDE_SLOTS:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=600, cwd=WORKSPACE)
            result, tok_in, tok_out, dur = "", 0, 0, int((time.time() - start) * 1000)
            try:
                data = json.loads(r.stdout)
                result = str(data.get("result", ""))[:4000]
                u = data.get("usage", {})
                tok_in = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                tok_out = u.get("output_tokens", 0)
                dur = data.get("duration_ms", dur)
            except ValueError:
                result = r.stdout[-500:]
            log(f"[{self.bot_name}] Claude fertig (rc={r.returncode}, {dur}ms, "
                f"{tok_out} out-tok): {result[-200:]}")
            if sessions_db:
                try:
                    sessions_db.record(self.bot_name, messages_label, result,
                                       r.returncode, dur, tok_in, tok_out, kind,
                                       model or "inherit")
                except Exception as e:
                    log(f"Session-Recording fehlgeschlagen: {e}")
            if r.returncode != 0:
                out = (r.stdout + r.stderr).lower()
                if "401" in out or "authenticate" in out or "oauth" in out:
                    self.send_message(
                        "⚠️ Ich kann gerade nicht antworten: Mein Claude-CLI-Login ist "
                        "abgelaufen. Bitte am Mac im Terminal `claude /login` ausführen — "
                        "danach beantworte ich deine Nachricht gern nochmal.")
                else:
                    self.send_message(
                        "⚠️ Beim Bearbeiten ist ein Fehler aufgetreten (Details im "
                        "listener.log am Mac). Probier's gleich nochmal.")
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


if __name__ == "__main__":
    main()
