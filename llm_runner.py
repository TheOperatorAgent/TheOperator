#!/usr/bin/env python3
"""Operator — Fremdmodell-Runner (läuft im dashboard-venv, braucht das openai-SDK).

Führt einen einzelnen Text-Aufruf gegen ein Fremd-Sprachmodell aus (Ollama, OpenAI,
Azure AI Foundry) — alle drei über die OpenAI-kompatible Chat-Completions-Schnittstelle
(`base_url`). BEWUSST ohne Werkzeug-Schleife: der Agent formuliert Text, der Operator sendet
ihn danach in den Chat. Lokale Werkzeuge (Bash/Datei/MCP) bleiben den Claude-Agenten
vorbehalten (der Gateway-Weg dafür ist von Anthropic nicht supportet, siehe Doku).

Protokoll (JSON über stdin → JSON über stdout):
  → {"provider","base_url","key","model_id","prompt","system"?,"max_tokens"?,"timeout"?}
  ← {"text": "..."}                (Erfolg)
  ← {"error": "..."}   + exit 1    (Fehler; der Listener fällt dann sauber zurück/meldet)
"""
import json
import os
import re
import subprocess
import sys

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)
import net_guard                     # noqa: E402  (#82 Schutz vor Zugriffen ins eigene Netz)

# ---------------------------------------------------------------- Werkzeuge (optional) --
# Kimi & Co. können nativ Werkzeuge aufrufen (OpenAI-Tool-Calling). Security by Design:
# Datei-Werkzeuge nur im Arbeitsordner (Pfad-Käfig), Befehle mit Timeout + Ausgabe-Kappung
# + Sperrliste für destruktive Muster, max. 15 Schritte pro Nachricht, alles protokolliert.
MAX_STEPS = 15
CMD_TIMEOUT = 60
FORBIDDEN_CMD = re.compile(
    r"\bsudo\b|\brm\s+(-\w+\s+)*(/|~)(\s|$)|\bmkfs|\bdd\s+.*of=/dev|"
    r"\bshutdown\b|\breboot\b|\blaunchctl\b|\bkillall\b|\bdiskutil\b", re.IGNORECASE)

TOOLS_SPEC = [
    {"type": "function", "function": {"name": "run_command",
        "description": "Führt einen Shell-Befehl im Arbeitsordner aus (Timeout 60 s).",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}},
    {"type": "function", "function": {"name": "write_file",
        "description": "Schreibt eine Datei (relativer Pfad im Arbeitsordner).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                       "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "read_file",
        "description": "Liest eine Datei aus dem Arbeitsordner.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {"name": "list_files",
        "description": "Listet Dateien im Arbeitsordner (rekursiv, max. 200).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": []}}},
]


def _sanitize_result(text: str, pii_map: dict) -> str:
    """#83 Egress-Schutz: Tool-Ergebnisse (Shell/Dateien/Browser) kommen aus der ECHTEN
    Welt und würden sonst roh ans Fremd-Modell gehen — am pseudonymisierten Prompt vorbei.
    1. Secrets maskieren (redact: bekannte Werte + Muster) — NIE Klartext-Secrets ans Modell.
    2. Bekannte PII durch DIESELBEN Surrogate ersetzen wie im Prompt (konsistent, s2r-Map
       invertiert, längster Realwert zuerst). Grenze (dokumentiert): NEUE, der Map unbekannte
       PII aus Tool-Ausgaben wird hier nicht erkannt — das bleibt Teil von #83 (Presidio-Pass)."""
    if not text:
        return text
    try:
        import redact
        text = redact.redact(text)
    except Exception:
        pass
    try:
        s2r = (pii_map or {}).get("s2r", {})
        for surrogat, real in sorted(s2r.items(), key=lambda kv: len(kv[1] or ""), reverse=True):
            if real and len(real) >= 3 and real in text:
                text = text.replace(real, surrogat)
    except Exception:
        pass
    return text


def _jail(workdir: str, path: str) -> str:
    """Pfad-Käfig: Datei-Zugriffe außerhalb des Arbeitsordners sind technisch unmöglich."""
    p = os.path.realpath(os.path.join(workdir, path or "."))
    root = os.path.realpath(workdir)
    if p != root and not p.startswith(root + os.sep):
        raise ValueError("Pfad liegt außerhalb des Arbeitsordners — nicht erlaubt")
    return p


def _exec_tool(name: str, args: dict, workdir: str, actions: list) -> str:
    try:
        if name == "run_command":
            cmd = str(args.get("command", ""))
            if FORBIDDEN_CMD.search(cmd):
                actions.append("BLOCKIERT: " + cmd[:120])
                return "FEHLER: Dieser Befehl ist aus Sicherheitsgründen gesperrt."
            actions.append("$ " + cmd[:200])
            r = subprocess.run(["/bin/sh", "-c", cmd], cwd=workdir, capture_output=True,
                               text=True, timeout=CMD_TIMEOUT)
            out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
            return f"exit={r.returncode}\n{out[:8000]}"
        if name == "write_file":
            p = _jail(workdir, args.get("path", ""))
            os.makedirs(os.path.dirname(p) or workdir, exist_ok=True)
            content = str(args.get("content", ""))
            with open(p, "w") as f:
                f.write(content)
            actions.append(f"write {args.get('path')} ({len(content)} B)")
            return "geschrieben"
        if name == "read_file":
            p = _jail(workdir, args.get("path", ""))
            actions.append(f"read {args.get('path')}")
            with open(p) as f:
                return f.read()[:16000]
        if name == "list_files":
            p = _jail(workdir, args.get("path", "."))
            actions.append(f"ls {args.get('path', '.')}")
            names = []
            for base, _dirs, files in os.walk(p):
                rel = os.path.relpath(base, workdir)
                names += [os.path.normpath(os.path.join(rel, fn)) for fn in files]
                if len(names) > 200:
                    break
            return "\n".join(sorted(names)[:200]) or "(leer)"
        return "unbekanntes Werkzeug"
    except subprocess.TimeoutExpired:
        return f"FEHLER: Befehl abgebrochen (über {CMD_TIMEOUT} s)"
    except Exception as e:
        return "FEHLER: " + str(e)[:200]


# ---------------------------------------------------------------- Browser (nur Lesen/Navigieren) --
# Bewusst OHNE Formular-Absenden/Ausfüllen (v1): der Agent kann Seiten öffnen, Links/Buttons
# klicken und Text/Links extrahieren — aber nichts im Web auslösen. Headless, mit Timeouts.
BROWSER_TOOLS = [
    {"type": "function", "function": {"name": "open_page",
        "description": "Öffnet eine Webseite (URL) und gibt Titel, Text und klickbare Elemente zurück. "
                       "Nur Lesen/Navigieren — kein Absenden von Formularen.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "click_link",
        "description": "Klickt ein sichtbares Link/Button-Element anhand seines Textes und gibt die neue "
                       "Seite zurück. Keine Formular-Absendung.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
]
BROWSER_TOOL_NAMES = {"open_page", "click_link"}


def _system_chromium():
    """Pfad zu einem bereits installierten Chromium/Chrome, den Playwright mitbenutzen kann.

    Playwright liefert nicht für jede Architektur ein eigenes Chromium mit — auf ARM-Linux
    (z. B. Raspberry Pi) schlägt »playwright install chromium« fehl. Dort ist aber fast
    immer ein System-Chromium da. Der Installer hinterlegt den Fund in browser_path.txt;
    findet er nichts, suchen wir hier noch einmal selbst.
    """
    import shutil
    merk = os.path.join(BOT_DIR, "browser_path.txt")
    try:
        p = open(merk).read().strip()
        if p and os.path.exists(p):
            return p
    except OSError:
        pass
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _browser_page(state):
    if state.get("page"):
        return state["page"]
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        br = pw.chromium.launch(headless=True)
    except Exception:
        eigener = _system_chromium()
        if not eigener:
            raise
        br = pw.chromium.launch(headless=True, executable_path=eigener)
    ctx = br.new_context(accept_downloads=False)

    # #82: JEDE Anfrage prüfen — nicht nur die erste. Eine harmlose Seite kann per
    # Weiterleitung oder eingebetteter Ressource ins eigene Netz zeigen; auch ein
    # DNS-Wechsel zwischen Prüfung und Verbindung wird so beim nächsten Sprung erwischt.
    def _wache(route, request):
        try:
            ok, grund = net_guard.check_url(request.url)
        except Exception:
            ok, grund = False, "Prüfung fehlgeschlagen"
        if ok:
            route.continue_()
        else:
            state.setdefault("blocked", []).append(f"{request.url[:100]} ({grund})")
            route.abort()

    try:
        ctx.route("**/*", _wache)
    except Exception:
        pass                       # ohne Route-Hook bleibt die Vorab-Prüfung in _browse_tool

    page = ctx.new_page()
    page.set_default_timeout(30000)
    state.update(pw=pw, browser=br, page=page)
    return page


def _browser_close(state):
    for k in ("browser", "pw"):
        try:
            obj = state.get(k)
            if obj:
                obj.stop() if k == "pw" else obj.close()
        except Exception:
            pass


def _page_summary(page):
    try:
        text = page.inner_text("body")
    except Exception:
        text = ""
    links, seen = [], set()
    try:
        for el in page.query_selector_all("a, button"):
            t = (el.inner_text() or "").strip().replace("\n", " ")
            if t and len(t) < 80 and t not in seen:
                seen.add(t); links.append(t)
            if len(links) >= 40:
                break
    except Exception:
        pass
    return (f"URL: {page.url}\nTitel: {page.title()}\n\n{text[:6000]}"
            + ("\n\n[Klickbar] " + " · ".join(links) if links else ""))


def _browse_tool(name, args, state, actions):
    try:
        page = _browser_page(state)
        if name == "open_page":
            url = str(args.get("url", "")).strip()
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            ok, grund = net_guard.check_url(url)          # #82: vor dem Verbinden prüfen
            if not ok:
                actions.append(f"🚫 blockiert {url[:90]} ({grund})")
                return net_guard.hinweis(url, grund)
            actions.append("🌐 open " + url[:120])
            page.goto(url, wait_until="domcontentloaded")
            return _page_summary(page)
        if name == "click_link":
            t = str(args.get("text", "")).strip()
            actions.append("🖱️ click »" + t[:60] + "«")
            page.get_by_text(t, exact=False).first.click(timeout=15000)
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            return _page_summary(page)
        return "unbekanntes Browser-Werkzeug"
    except Exception as e:
        m = str(e)
        if "Executable doesn't exist" in m or "playwright install" in m:
            return ("Mir fehlt noch ein Browser zum Surfen (dein eigener Browser und das "
                    "Dashboard sind davon nicht betroffen). 👉 Ein Update über das Dashboard "
                    "holt ihn nach — Reiter »Aktualisierung«.")
        return "Browser-Fehler: " + m[:200]


def main() -> int:
    try:
        req = json.load(sys.stdin)
    except ValueError:
        print(json.dumps({"error": "ungültige Anfrage"}))
        return 1
    provider = req.get("provider", "")
    base_url = (req.get("base_url") or "").rstrip("/")
    model_id = req.get("model_id", "")
    key = req.get("key") or ""
    prompt = req.get("prompt", "")
    system = req.get("system") or ""
    # Höheres Default-Budget: Thinking-Modelle (z. B. Kimi K2.7) verbrauchen einen Teil fürs
    # interne »reasoning«; zu wenig Tokens → Antwort-Text (content) bleibt leer/abgeschnitten.
    max_tokens = int(req.get("max_tokens", 4096))
    timeout = float(req.get("timeout", 90))
    use_tools = bool(req.get("tools"))
    use_browser = bool(req.get("browser"))
    workdir = req.get("workdir") or ""
    pii_map = req.get("pii_map") or {}     # #83: Surrogat-Map für Tool-Ergebnis-Bereinigung

    # Ollama spricht OpenAI-kompatibel unter /v1; Dummy-Key, da das SDK einen verlangt.
    if provider == "ollama":
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        key = key or "ollama"
    if not base_url:
        print(json.dumps({"error": "keine Server-Adresse für den Provider"}))
        return 1

    try:
        from openai import OpenAI
    except ImportError:
        print(json.dumps({"error": "openai-SDK fehlt im venv (pip install openai)"}))
        return 1

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        client = OpenAI(base_url=base_url, api_key=key, timeout=timeout, max_retries=1)

        # ---------- Werkzeug-Modus: kleine agentische Schleife (Pfad-Käfig + optional Browser) ----------
        if (use_tools and workdir) or use_browser:
            if use_tools and workdir:
                os.makedirs(workdir, exist_ok=True)
            tools_active = (TOOLS_SPEC if (use_tools and workdir) else []) + (BROWSER_TOOLS if use_browser else [])
            actions, bstate = [], {}
            try:
                for _ in range(MAX_STEPS):
                    resp = client.chat.completions.create(model=model_id, messages=messages,
                                                          tools=tools_active, max_tokens=max_tokens)
                    msg = resp.choices[0].message
                    if getattr(msg, "tool_calls", None):
                        messages.append({"role": "assistant", "content": msg.content or "",
                                         "tool_calls": [{"id": tc.id, "type": "function",
                                                         "function": {"name": tc.function.name,
                                                                      "arguments": tc.function.arguments}}
                                                        for tc in msg.tool_calls]})
                        for tc in msg.tool_calls:
                            try:
                                targs = json.loads(tc.function.arguments or "{}")
                            except ValueError:
                                targs = {}
                            if tc.function.name in BROWSER_TOOL_NAMES:
                                res = _browse_tool(tc.function.name, targs, bstate, actions)
                            else:
                                res = _exec_tool(tc.function.name, targs, workdir, actions)
                            # #83: Tool-Ergebnis bereinigen, BEVOR es das Fremd-Modell sieht
                            res = _sanitize_result(res, pii_map)
                            messages.append({"role": "tool", "tool_call_id": tc.id, "content": res})
                        continue
                    text = (msg.content or "").strip()
                    if text:
                        print(json.dumps({"text": text, "actions": actions}))
                        return 0
                    break
            finally:
                _browser_close(bstate)
            print(json.dumps({"error": "Werkzeug-Limit erreicht — bitte die Aufgabe kleiner "
                              "stellen.", "actions": actions}))
            return 1

        # ---------- Text-Modus (ohne Werkzeuge) ----------
        resp = client.chat.completions.create(model=model_id, messages=messages,
                                              max_tokens=max_tokens)
        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        if not text:
            # Thinking-Modell: hat evtl. das ganze Budget fürs Denken verbraucht → content leer.
            if getattr(choice, "finish_reason", "") == "length":
                print(json.dumps({"error": "Die Antwort wurde abgeschnitten (Token-Limit erreicht) "
                                  "— bitte die Aufgabe kürzer/kleiner stellen."}))
            else:
                print(json.dumps({"error": "Modell lieferte leere Antwort"}))
            return 1
        print(json.dumps({"text": text}))
        return 0
    except Exception as e:  # Provider-/Netz-/Auth-Fehler einheitlich weiterreichen
        msg = str(e)
        low = msg.lower()
        if "401" in msg or "invalid" in low and "key" in low or "authentication" in low:
            msg = "Zugangsschlüssel abgelehnt — bitte im Dashboard prüfen."
        elif "connect" in low or "refused" in low or "timed out" in low:
            msg = f"{provider} nicht erreichbar (läuft der Server? Adresse korrekt?)"
        print(json.dumps({"error": msg[:300]}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
