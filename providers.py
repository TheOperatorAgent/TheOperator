#!/usr/bin/env python3
"""Operator — Modell-Provider-Registry (stdlib-only).

Verwaltet, welche Sprachmodelle den Subagenten zur Verfügung stehen:
- Claude-Modelle laufen weiter über den Claude CLI (voller Werkzeugkasten, Abo).
- Fremd-Modelle (Ollama, OpenAI, Azure AI Foundry) laufen über llm_runner.py (venv) —
  reiner Text/Recherche-Betrieb, keine lokalen Werkzeuge.

Konfiguration: connections/models.json (Klartext: enabled/base_url/Modelle/Default).
API-Keys liegen im OS-Secret-Store (secretstore.py) unter <provider>_api_key — so kann der
stdlib-Listener den Anthropic-Fallback-Key lesen und der venv-Runner die Provider-Keys.
Ollama braucht keinen Key.

Ein Agenten-Modellname ist entweder ein Claude-Alias/-ID (haiku|sonnet|opus|fable|claude-…)
oder mit Provider-Präfix `<provider>/<modell>` (z. B. ollama/llama3.1, openai/gpt-4o,
azure/<deployment>).
"""
import json
import os
import socket
import sys
import urllib.error
import urllib.request

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
MODELS_FILE = os.path.join(BOT_DIR, "connections", "models.json")
sys.path.insert(0, BOT_DIR)
import secretstore  # noqa: E402  (stdlib-Modul aus BOT_DIR)

CLAUDE_ALIASES = ("haiku", "sonnet", "opus", "fable", "inherit")
PROVIDERS = ("ollama", "openai", "azure")
KEY_ACCOUNT = {"openai": "openai_api_key", "azure": "azure_api_key",
               "anthropic": "anthropic_api_key"}
DEFAULT_BASE = {"ollama": "http://localhost:11434", "openai": "https://api.openai.com/v1",
                "azure": ""}


# ---------------------------------------------------------------- Konfiguration --
def _load() -> dict:
    try:
        return json.load(open(MODELS_FILE))
    except (OSError, ValueError):
        return {}


def _save(cfg: dict) -> None:
    os.makedirs(os.path.dirname(MODELS_FILE), exist_ok=True)
    fd = os.open(MODELS_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(MODELS_FILE + ".tmp", MODELS_FILE)


def get_config() -> dict:
    """Vollständige Provider-Konfiguration (ohne Keys) für das Dashboard."""
    cfg = _load()
    out = {}
    for p in PROVIDERS:
        c = cfg.get(p, {})
        out[p] = {"enabled": bool(c.get("enabled")),
                  "base_url": c.get("base_url", DEFAULT_BASE[p]),
                  "models": c.get("models", []),
                  "default": c.get("default", ""),
                  "has_key": bool(secretstore.get(KEY_ACCOUNT[p])) if p != "ollama" else True}
    fb = cfg.get("anthropic_fallback", {})
    out["anthropic_fallback"] = {"enabled": bool(fb.get("enabled")),
                                 "has_key": bool(secretstore.get(KEY_ACCOUNT["anthropic"]))}
    return out


def set_provider(provider: str, base_url: str = None, models=None, default: str = None,
                 enabled: bool = None, key: str = None) -> None:
    if provider not in PROVIDERS:
        raise ValueError("Unbekannter Provider")
    cfg = _load()
    c = cfg.setdefault(provider, {})
    if base_url is not None:
        c["base_url"] = base_url.rstrip("/")
    if models is not None:
        c["models"] = [m.strip() for m in models if str(m).strip()]
    if default is not None:
        c["default"] = default.strip()
    if enabled is not None:
        c["enabled"] = bool(enabled)
    _save(cfg)
    if key:                       # Key nur wenn mitgeliefert (leer = unverändert)
        secretstore.set(KEY_ACCOUNT[provider], key)


def delete_provider(provider: str) -> None:
    cfg = _load()
    cfg.pop(provider, None)
    _save(cfg)
    if provider in KEY_ACCOUNT:
        secretstore.delete(KEY_ACCOUNT[provider])


def set_anthropic_fallback(enabled: bool = None, key: str = None) -> None:
    cfg = _load()
    fb = cfg.setdefault("anthropic_fallback", {})
    if enabled is not None:
        fb["enabled"] = bool(enabled)
    _save(cfg)
    if key:
        secretstore.set(KEY_ACCOUNT["anthropic"], key)


def fallback_key():
    """Anthropic-API-Key für den Auto-Fallback — nur wenn aktiviert UND hinterlegt."""
    if not _load().get("anthropic_fallback", {}).get("enabled"):
        return None
    return secretstore.get(KEY_ACCOUNT["anthropic"]) or None


# ---------------------------------------------------------------- Modell-Auflösung --
def list_models() -> list:
    """Auswahlliste fürs Dashboard: Claude-Aliase + aktivierte Provider-Modelle.
    Jeder Eintrag: {value, label, kind}."""
    out = [{"value": a, "label": f"Claude · {a}", "kind": "claude"}
           for a in ("haiku", "sonnet", "opus")]
    out.insert(0, {"value": "inherit", "label": "Claude · Standard (geerbt)", "kind": "claude"})
    cfg = _load()
    for p in PROVIDERS:
        c = cfg.get(p, {})
        if not c.get("enabled"):
            continue
        for m in c.get("models", []):
            out.append({"value": f"{p}/{m}", "label": f"{p} · {m}", "kind": "foreign"})
    return out


def resolve(agent_model: str) -> dict:
    """Modellname → Ausführungsplan.
    Claude: {'kind':'claude','model': <alias|id|None>}.
    Fremd:  {'kind':'foreign','provider','model_id','base_url','key'}."""
    m = (agent_model or "").strip()
    if not m or m in CLAUDE_ALIASES:
        return {"kind": "claude", "model": None if m in ("", "inherit") else m}
    if m.startswith("claude"):
        return {"kind": "claude", "model": m}
    if "/" in m:
        provider, model_id = m.split("/", 1)
        if provider in PROVIDERS:
            cfg = _load().get(provider, {})
            return {"kind": "foreign", "provider": provider, "model_id": model_id,
                    "base_url": (cfg.get("base_url") or DEFAULT_BASE[provider]).rstrip("/"),
                    "key": secretstore.get(KEY_ACCOUNT.get(provider, "")) or ""}
    # Unbekannt → sicher als Claude-Standard behandeln (kein Fehler zur Laufzeit)
    return {"kind": "claude", "model": None}


# ---------------------------------------------------------------- Live-Test --
def _http(url: str, headers: dict, timeout: float = 8.0):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def test(provider: str) -> tuple:
    """Live-Erreichbarkeit prüfen. Rückgabe: (ok: bool, meldung: str)."""
    cfg = _load().get(provider, {})
    base = (cfg.get("base_url") or DEFAULT_BASE[provider]).rstrip("/")
    if not base:
        return False, "Keine Server-Adresse hinterlegt."
    try:
        if provider == "ollama":
            # OpenAI-Kompatschicht liegt unter /v1 — der native Tags-Endpoint unter der Wurzel
            root = base[:-3] if base.endswith("/v1") else base
            st, _ = _http(root + "/api/tags", {})
            return (st == 200), ("Ollama erreichbar" if st == 200 else f"HTTP {st}")
        key = secretstore.get(KEY_ACCOUNT[provider]) or ""
        if not key:
            return False, "Kein API-Key hinterlegt."
        if provider == "openai":
            st, _ = _http(base + "/models", {"Authorization": "Bearer " + key})
        else:  # azure (v1-Endpoint, api-key-Header)
            st, _ = _http(base + "/models", {"api-key": key})
        return (st == 200), ("Verbindung OK" if st == 200 else f"HTTP {st}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "Zugangsschlüssel abgelehnt (401/403) — bitte prüfen."
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return False, f"Nicht erreichbar: {str(getattr(e, 'reason', e))[:120]}"
