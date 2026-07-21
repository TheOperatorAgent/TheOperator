"""Kern-Tests für das Operator Dashboard (im venv: venv/bin/python3 -m pytest)."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agents_store
import m365_setup


# ---------------------------------------------------------------- Frontmatter --
GOLDEN = """---
name: recherche
description: Web-Recherchen und Faktenchecks
tools: WebSearch, WebFetch, Read
model: haiku
custom_key: bleibt-erhalten
---

Du bist der Recherche-Agent.
"""


def test_frontmatter_roundtrip_preserves_unknown_keys():
    p = agents_store.parse(GOLDEN)
    assert p["frontmatter"]["custom_key"] == "bleibt-erhalten"
    out = agents_store.serialize(p["frontmatter"], "\n\n" + p["body"].strip() + "\n")
    p2 = agents_store.parse(out)
    assert p2["frontmatter"] == p["frontmatter"]
    assert p2["body"].strip() == p["body"].strip()


def test_frontmatter_parse_all_fields():
    p = agents_store.parse(GOLDEN)
    fm = p["frontmatter"]
    assert fm["name"] == "recherche"
    assert fm["model"] == "haiku"
    assert "WebFetch" in fm["tools"]


def test_agent_name_validation():
    assert agents_store.NAME_RE.match("recherche")
    assert agents_store.NAME_RE.match("mein-agent-2")
    assert not agents_store.NAME_RE.match("Böse Namen")
    assert not agents_store.NAME_RE.match("a")
    assert not agents_store.NAME_RE.match("x" * 40)
    assert not agents_store.NAME_RE.match("../../etc/passwd")


# ---------------------------------------------------------------- Scope-Mapping --
def test_scope_mapping_read_only():
    m = {"mail": {"read": True, "write": False}}
    assert m365_setup.matrix_to_values(m) == ["Mail.Read"]


def test_scope_mapping_write_implies_read():
    m = {"mail": {"read": False, "write": True}}
    v = m365_setup.matrix_to_values(m)
    assert "Mail.Read" in v and "Mail.ReadWrite" in v and "Mail.Send" in v


def test_scope_mapping_teams_no_write():
    m = {"teams": {"read": True, "write": True}}
    v = m365_setup.matrix_to_values(m)
    assert v == ["Channel.ReadBasic.All", "Team.ReadBasic.All"]


def test_scope_mapping_all_off_is_empty():
    m = {s: {"read": False, "write": False} for s in m365_setup.PERMISSION_MAP}
    assert m365_setup.matrix_to_values(m) == []


def test_scope_mapping_full_matrix():
    m = {s: {"read": True, "write": True} for s in m365_setup.PERMISSION_MAP}
    v = m365_setup.matrix_to_values(m)
    assert "Files.ReadWrite.All" in v and "Sites.ReadWrite.All" in v
    assert "Tasks.ReadWrite.All" in v and "Calendars.ReadWrite" in v
    assert not any("ChannelMessage" in x for x in v)


# ---------------------------------------------------------------- Krypto --
def test_token_crypto_roundtrip(tmp_path, monkeypatch):
    import tokens
    monkeypatch.setattr(tokens, "SECRETS_DIR", str(tmp_path))
    monkeypatch.setattr(tokens, "_master_key", lambda: b"\x01" * 32)
    tokens.save("t1", {"secret": "wert", "n": 5})
    assert tokens.load("t1") == {"secret": "wert", "n": 5}
    tokens.save("t2", "nur-string")
    assert tokens.load("t2") == "nur-string"
    tokens.delete("t1")
    assert tokens.load("t1") is None
    # Datei ist wirklich verschluesselt
    blob = open(tmp_path / "t2.enc", "rb").read()
    assert b"nur-string" not in blob


# ---------------------------------------------------------------- Listener (stdlib) --
def test_listener_is_stdlib_only():
    """listener.py darf keine venv-Abhängigkeiten importieren."""
    import ast
    src = open(os.path.expanduser("~/.claude/matrix-bot/listener.py")).read()
    tree = ast.parse(src)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    forbidden = {"fastapi", "uvicorn", "msal", "cryptography", "requests"}
    assert not (imports & forbidden), f"venv-Import im Listener: {imports & forbidden}"


# ---------------------------------------------------------------- Cron (A3) --
def test_cron_parser():
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import time as _t
    import cron_runner
    t = _t.struct_time((2026, 7, 21, 7, 0, 0, 1, 202, 1))  # Di 07:00 (tm_wday 1=Di)
    assert cron_runner.cron_match("0 7 * * *", t)
    assert cron_runner.cron_match("0 7 * * 2", t)          # dow 2 = Dienstag
    assert cron_runner.cron_match("*/15 * * * *", t)
    assert cron_runner.cron_match("0 6-8 * * 1-5", t)
    assert not cron_runner.cron_match("30 7 * * *", t)
    assert not cron_runner.cron_match("0 7 * * 0", t)      # Sonntag
    assert not cron_runner.cron_match("kaputt", t)
    assert not cron_runner.cron_match("0 7 * *", t)        # nur 4 Felder


# ---------------------------------------------------------------- Sessions (A1) --
def test_sessions_roundtrip(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import sessions
    monkeypatch.setattr(sessions, "DB", str(tmp_path / "s.db"))
    sessions.record("owner", "Wie ist das Wetter?", "Sonnig, 25 Grad", 0, 1234, 100, 50)
    sessions.record("recherche", "Suche Synapse-Doku", "Gefunden: matrix.org", 0, 999, 10, 20, kind="chat")
    assert len(sessions.list_sessions()) == 2
    hits = sessions.search("Wetter")
    assert len(hits) == 1 and hits[0]["bot"] == "owner"
    u = sessions.usage(5)
    assert u["runs"] == 2 and u["tokens_out"] == 70
    buckets = sessions.usage_buckets(24, 1)
    assert buckets[0]["runs"] == 2


def test_sessions_is_stdlib_only():
    import ast
    for fn in ("sessions.py", "cron_runner.py"):
        src = open(os.path.expanduser("~/.claude/matrix-bot/" + fn)).read()
        imports = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        assert not (imports & {"fastapi", "uvicorn", "msal", "cryptography", "requests"}), fn


def test_listener_agent_md_parser():
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    listener = importlib.import_module("listener")
    a = listener.parse_agent_md("recherche")
    assert a and a["model"] == "haiku"
    assert "WebSearch" in a["tools"]
    assert "Recherche-Agent" in a["body"]
