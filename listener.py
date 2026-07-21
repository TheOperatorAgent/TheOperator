#!/usr/bin/env python3
"""Matrix-Listener: wartet per /sync-Long-Poll auf Nachrichten von @michi
und weckt bei jeder neuen Nachricht den Claude-CLI zum Antworten."""
import json
import subprocess
import threading
import time
import urllib.parse
import urllib.request

BOT_DIR = "/Users/michi/.claude/matrix-bot"
CREDS = json.load(open(f"{BOT_DIR}/credentials.json"))
HS = CREDS["homeserver"]
TOKEN = CREDS["access_token"]
ROOM = CREDS["room_id"]
MICHI = CREDS.get("owner_id", "@michi:matrix.vonaschenbrenner.bayern")
ALLOWED_TOOLS = CREDS.get("allowed_tools", ["Bash", "Read", "WebFetch", "WebSearch", "Agent"])
CLAUDE = CREDS.get("claude_bin", "/Users/michi/.npm-global/bin/claude")
WORKSPACE = f"{BOT_DIR}/workspace"

RESPONDER_PROMPT = """Deine Verhaltensregeln, Wissensquellen und wie du antwortest stehen hier \
(strikt befolgen):

{verhalten}

---
{memories}
Michi hat dir soeben im Matrix-Chat geschrieben:

{messages}

Erledige/beantworte das jetzt gemäß den Regeln oben und sende die Antwort in den Raum."""


def recall(text, k=5):
    """Top-k relevante Gedächtnis-Einträge zur eingehenden Nachricht (tokensparend)."""
    try:
        r = subprocess.run(
            ["python3", f"{BOT_DIR}/memory.py", "search", text, "-k", str(k)],
            capture_output=True, text=True, timeout=15,
        )
        hits = r.stdout.strip()
        if hits:
            return f"Relevante Einträge aus deinem Gedächtnis:\n{hits}\n\n"
    except Exception as e:
        log(f"Gedächtnis-Abruf fehlgeschlagen: {e}")
    return ""


def api(path, timeout=90, method="GET", body=None):
    req = urllib.request.Request(
        HS + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
        },
    )
    return json.load(urllib.request.urlopen(req, timeout=timeout))


ROOM_ENC = urllib.parse.quote(ROOM)
BOT_ENC = urllib.parse.quote(CREDS.get("user_id", "@claude:matrix.vonaschenbrenner.bayern"))


def set_typing(on):
    try:
        api(
            f"/_matrix/client/v3/rooms/{ROOM_ENC}/typing/{BOT_ENC}",
            method="PUT",
            body={"typing": on, "timeout": 25000} if on else {"typing": False},
            timeout=10,
        )
    except Exception as e:
        log(f"Typing-Signal fehlgeschlagen: {e}")


def send_message(text):
    try:
        api(
            f"/_matrix/client/v3/rooms/{ROOM_ENC}/send/m.room.message/{time.time_ns()}",
            method="PUT", body={"msgtype": "m.text", "body": text}, timeout=15,
        )
    except Exception as e:
        log(f"Nachricht senden fehlgeschlagen: {e}")


def mark_read(event_id):
    try:
        api(
            f"/_matrix/client/v3/rooms/{ROOM_ENC}/receipt/m.read/{urllib.parse.quote(event_id)}",
            method="POST", body={}, timeout=10,
        )
    except Exception as e:
        log(f"Lesebestätigung fehlgeschlagen: {e}")


def log(msg):
    print(f"[{time.strftime('%F %T')}] {msg}", flush=True)


def answer(bodies, last_event_id):
    messages = "\n".join(f"- {b}" for b in bodies)
    try:
        verhalten = open(f"{BOT_DIR}/VERHALTEN.md").read()
    except OSError:
        verhalten = "(VERHALTEN.md fehlt — antworte hilfsbereit auf Deutsch und sende die Antwort per python3 ~/.claude/matrix-bot/send.py in den Raum.)"
    prompt = RESPONDER_PROMPT.format(
        verhalten=verhalten, messages=messages, memories=recall(" ".join(bodies))
    )
    log(f"Claude wird geweckt für {len(bodies)} Nachricht(en)")
    # Sofort sichtbar machen, dass gearbeitet wird: gelesen + "tippt..."
    mark_read(last_event_id)
    done = threading.Event()

    def keep_typing():
        while not done.is_set():
            set_typing(True)
            done.wait(20)

    t = threading.Thread(target=keep_typing, daemon=True)
    t.start()
    try:
        r = subprocess.run(
            [CLAUDE, "-p", prompt, "--allowedTools", *ALLOWED_TOOLS],
            capture_output=True, text=True, timeout=600, cwd=WORKSPACE,
        )
        log(f"Claude fertig (rc={r.returncode}): {r.stdout[-300:]}")
        if r.returncode != 0:
            out = (r.stdout + r.stderr).lower()
            if "401" in out or "authenticate" in out or "oauth" in out:
                send_message(
                    "⚠️ Ich kann gerade nicht antworten: Mein Claude-CLI-Login ist "
                    "abgelaufen. Bitte am Mac im Terminal `claude /login` ausführen — "
                    "danach beantworte ich deine Nachricht gern nochmal."
                )
            else:
                send_message(
                    "⚠️ Beim Bearbeiten deiner Nachricht ist ein Fehler aufgetreten "
                    "(Details im listener.log am Mac). Probier's gleich nochmal oder "
                    "schau in einer Claude-Code-Session nach."
                )
    except subprocess.TimeoutExpired:
        log("Claude-Lauf abgebrochen (Timeout 600s)")
        send_message(
            "⚠️ Die Aufgabe hat länger als 10 Minuten gedauert — ich habe abgebrochen. "
            "Für so große Sachen besser eine Claude-Code-Session am Mac nutzen."
        )
    finally:
        done.set()
        t.join(timeout=5)
        set_typing(False)


def main():
    # Frischer since-Token: nur Nachrichten ab jetzt beantworten
    since = api("/_matrix/client/v3/sync?timeout=0&filter={\"room\":{\"timeline\":{\"limit\":1}}}")["next_batch"]
    log(f"Listener gestartet, Raum {ROOM}")
    while True:
        try:
            data = api(
                f"/_matrix/client/v3/sync?since={urllib.parse.quote(since)}&timeout=30000",
                timeout=45,
            )
            since = data["next_batch"]
            events = (
                data.get("rooms", {}).get("join", {}).get(ROOM, {})
                .get("timeline", {}).get("events", [])
            )
            new = [
                e
                for e in events
                if e.get("type") == "m.room.message"
                and e.get("sender") == MICHI
                and e["content"].get("body")
            ]
            if new:
                answer([e["content"]["body"] for e in new], new[-1]["event_id"])
        except urllib.error.HTTPError as e:
            if e.code == 401:
                log("FEHLER: Token ungültig (401) — 5 min Pause")
                time.sleep(300)
            else:
                log(f"HTTP {e.code} — 10s Pause")
                time.sleep(10)
        except Exception as e:  # Netz weg, Timeout etc.
            log(f"Fehler: {e} — 10s Pause")
            time.sleep(10)


if __name__ == "__main__":
    main()
