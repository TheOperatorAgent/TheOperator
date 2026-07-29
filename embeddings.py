#!/usr/bin/env python3
"""Embedding-Schicht fürs Hybrid-Gedächtnis (#52) — stdlib-only.

Liefert Text-Embeddings über einen bereits konfigurierten Provider (Ollama lokal
bevorzugt, sonst OpenAI) und die Vektor-Mathematik (packen/entpacken/Kosinus).
Bewusst OHNE native Extension (sqlite-vec o. ä.): bei der Datenmenge eines
persönlichen Gedächtnisses (hunderte bis wenige tausend Fakten) ist Brute-Force-
Kosinus in Python schnell genug (<10 ms) — dafür null neue Abhängigkeiten und
identisches Verhalten auf macOS/Linux/Windows.

Fail-open überall: jede Störung (kein Provider, Netz weg, Modell fehlt) liefert
None und das Gedächtnis arbeitet unverändert mit FTS5 weiter.
"""
import json
import math
import os
import struct
import urllib.request

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
# Standard-Embedding-Modelle je Provider (überschreibbar via dashboard.json:
#   "embeddings": {"enabled": true/false, "model": "..."} )
DEFAULT_MODELS = {"ollama": "nomic-embed-text", "openai": "text-embedding-3-small"}
TIMEOUT = 20


def _dash_cfg():
    try:
        return json.load(open(f"{BOT_DIR}/dashboard.json")).get("embeddings", {}) or {}
    except Exception:
        return {}


def resolve():
    """Aktiven Embedding-Provider bestimmen: Ollama (lokal, privat) vor OpenAI.
    Rückgabe: {"provider","base_url","model","key"} oder None (Vektor-Layer aus)."""
    cfg = _dash_cfg()
    if cfg.get("enabled") is False:            # explizit abgeschaltet
        return None
    try:
        import providers
        pcfg = providers.get_config()
    except Exception:
        return None
    for p in ("ollama", "openai"):             # lokal zuerst (Datenschutz + kostenlos)
        c = pcfg.get(p, {}) if isinstance(pcfg, dict) else {}
        if not c.get("enabled"):
            continue
        model = cfg.get("model") or DEFAULT_MODELS[p]
        key = ""
        if p == "openai":
            try:
                import secretstore
                key = secretstore.get(providers.KEY_ACCOUNT["openai"]) or ""
            except Exception:
                key = ""
            if not key:
                continue
        return {"provider": p, "base_url": c.get("base_url", "").rstrip("/"),
                "model": model, "key": key}
    return None


def embed(text, plan=None):
    """Text -> Vektor (Liste floats) oder None. Ein HTTP-Call, stdlib urllib."""
    plan = plan or resolve()
    if not plan or not (text or "").strip():
        return None
    try:
        if plan["provider"] == "ollama":
            req = urllib.request.Request(
                plan["base_url"] + "/api/embeddings", method="POST",
                data=json.dumps({"model": plan["model"], "prompt": text}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                vec = json.load(r).get("embedding")
        else:                                   # openai-kompatibel
            req = urllib.request.Request(
                plan["base_url"] + "/embeddings", method="POST",
                data=json.dumps({"model": plan["model"], "input": text}).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + plan.get("key", "")})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.load(r).get("data") or []
                vec = data[0].get("embedding") if data else None
        return vec if isinstance(vec, list) and vec else None
    except Exception:
        return None                             # fail-open: FTS5 übernimmt


def pack(vec):
    """Vektor -> BLOB (float32, kompakt für SQLite)."""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob):
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def rrf_merge(rankings, k=60, top=5):
    """Reciprocal Rank Fusion: mehrere Ranglisten von IDs -> eine.
    rankings: Liste von Listen (beste zuerst). Rückgabe: Top-`top`-IDs."""
    scores = {}
    for ranking in rankings:
        for pos, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + pos + 1)
    return [i for i, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:top]]


def status():
    """#109: Ehrlicher Zustand der semantischen Suche → (aktiv: bool, grund: str).

    Warum das nötig ist: Der Rückfall auf reine Wortsuche ist absichtlich fail-open —
    deshalb merkt niemand, wenn der Vektor-Layer stillschweigend nicht läuft. Genau
    das war am 29.07. der Fall: Konfiguration korrekt, Ollama erreichbar, aber das
    Embedding-Modell nie heruntergeladen → 0 Vektoren, ohne jede Meldung. Es
    funktionierte alles, nur schlechter. Diese Funktion macht den Zustand sichtbar.
    """
    if _dash_cfg().get("enabled") is False:
        return False, "Semantische Suche ist im Dashboard abgeschaltet."
    plan = resolve()
    if not plan:
        return False, ("Kein Anbieter für semantische Suche eingerichtet — es wird nur "
                       "nach Wörtern gesucht. 👉 Im Tab »Modelle & Provider« Ollama "
                       "einrichten (lokal und kostenlos).")
    if embed("Testsatz für die Selbstprüfung", plan) is None:
        modell = plan.get("model", "?")
        if plan.get("provider") == "ollama":
            return False, (f"Das Modell »{modell}« fehlt oder Ollama antwortet nicht — "
                           "es wird nur nach Wörtern gesucht. 👉 Einmalig im Terminal: "
                           f"ollama pull {modell}")
        return False, (f"Der Anbieter »{plan.get('provider')}« antwortet nicht — es wird "
                       "nur nach Wörtern gesucht.")
    return True, f"Semantische Suche aktiv ({plan['provider']}/{plan['model']})"


def rueckstand():
    """Wie viele Fakten haben noch keinen Vektor? → (ohne_vektor, gesamt)."""
    import sqlite3
    try:
        con = sqlite3.connect(os.path.join(BOT_DIR, "memory.db"))
        gesamt = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        mit = con.execute("SELECT COUNT(*) FROM mem_vecs").fetchone()[0]
        con.close()
        return max(0, gesamt - mit), gesamt
    except Exception:
        return 0, 0
