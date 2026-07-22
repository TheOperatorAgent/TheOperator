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
import sys


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
    max_tokens = int(req.get("max_tokens", 2000))
    timeout = float(req.get("timeout", 90))

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
        resp = client.chat.completions.create(model=model_id, messages=messages,
                                              max_tokens=max_tokens)
        text = (resp.choices[0].message.content or "").strip()
        if not text:
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
