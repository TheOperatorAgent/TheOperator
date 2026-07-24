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
    workdir = req.get("workdir") or ""

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

        # ---------- Werkzeug-Modus: kleine agentische Schleife im Pfad-Käfig ----------
        if use_tools and workdir:
            os.makedirs(workdir, exist_ok=True)
            actions = []
            for _ in range(MAX_STEPS):
                resp = client.chat.completions.create(model=model_id, messages=messages,
                                                      tools=TOOLS_SPEC, max_tokens=max_tokens)
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
                        res = _exec_tool(tc.function.name, targs, workdir, actions)
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": res})
                    continue
                text = (msg.content or "").strip()
                if text:
                    print(json.dumps({"text": text, "actions": actions}))
                    return 0
                break
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
