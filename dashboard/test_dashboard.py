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
    # Gesprächskontext: nur eigener Bot, chronologisch, nur erfolgreiche Chat-Runden
    dialog = sessions.recent_dialog("owner")
    assert dialog == [("Wie ist das Wetter?", "Sonnig, 25 Grad")]
    sessions.record("owner", "kaputt", "err", 1, 10, 0, 0)          # rc!=0 -> raus
    sessions.record("owner", "cronlauf", "x", 0, 10, 0, 0, kind="cron")  # cron -> raus
    assert len(sessions.recent_dialog("owner")) == 1


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


# ---------------------------------------------------------------- Skills --
def test_skills_crud_and_protection(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import skills
    monkeypatch.setattr(skills, "SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(skills, "PROPOSALS_FILE", str(tmp_path / "props.json"))
    # Validierung
    assert not skills.save("Böser Name!", "d", "b")[0]
    assert not skills.save("ok-name", "", "b")[0]
    # Bot legt an, Bot darf eigene überschreiben
    assert skills.save("auto", "Beschr", "Anleitung", source="bot")[0]
    assert skills.save("auto", "Beschr2", "Anleitung2", source="bot")[0]
    # Dashboard übernimmt -> Bot darf NICHT mehr überschreiben (Hermes-Lektion)
    assert skills.save("auto", "Beschr3", "Anleitung3", source="dashboard")[0]
    ok, msg = skills.save("auto", "hijack", "x", source="bot")
    assert not ok and "geschützt" in msg
    s = skills.get("auto")
    assert s["description"] == "Beschr3" and s["source"] == "dashboard"
    # Frontmatter-Roundtrip
    assert "Anleitung3" in s["body"]
    assert len(skills.list_skills()) == 1
    assert skills.delete("auto") and not skills.get("auto")


def test_skills_proposals_flow(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import skills
    monkeypatch.setattr(skills, "SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(skills, "PROPOSALS_FILE", str(tmp_path / "props.json"))
    assert skills.propose("brief", "Briefing", "Schritte", reason="3x gefragt")[0]
    # Dedup: gleicher Name ersetzt
    assert skills.propose("brief", "Briefing v2", "Schritte v2")[0]
    props = skills.load_proposals()
    assert len(props) == 1 and props[0]["description"] == "Briefing v2"
    # Ablehnen
    assert skills.reject(props[0]["id"])[0] and not skills.load_proposals()
    # Annehmen -> Skill mit source=dashboard (gehört ab da Michi)
    skills.propose("brief", "Briefing", "Schritte")
    pid = skills.load_proposals()[0]["id"]
    assert skills.accept(pid)[0]
    assert not skills.load_proposals()
    assert skills.get("brief")["source"] == "dashboard"
    assert not skills.accept("gibtsnicht")[0]


def test_skills_is_stdlib_only():
    import ast
    src = open(os.path.expanduser("~/.claude/matrix-bot/skills.py")).read()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert not (imports & {"fastapi", "uvicorn", "msal", "cryptography", "requests"})


# ---------------------------------------------------------------- Tresor --
def _vault(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import vault
    monkeypatch.setattr(vault, "VAULT_FILE", str(tmp_path / "vault.enc"))
    monkeypatch.setattr(vault, "DEK_PATH_OVERRIDE", str(tmp_path / "s.dek"))
    monkeypatch.setattr(vault, "SCRYPT_N", 2 ** 14)  # Testtempo
    return vault


def test_vault_roundtrip(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    rk = vault.init("master-passwort-test")
    vault.add_entry("gitea-admin", "SuperGeheim123", description="Gitea root")
    entries = vault.list_entries()
    assert [e["name"] for e in entries] == ["gitea-admin"]
    assert "value" not in entries[0]                      # write-only-Garantie
    blob = open(str(tmp_path / "vault.enc"), "rb").read()
    assert b"SuperGeheim123" not in blob and b"gitea-admin" not in blob
    vault.remove_entry("gitea-admin")
    assert vault.list_entries() == []
    st = vault.status()
    assert st["exists"] and not st["locked"] and st["entries"] == 0 and st["fido_keys"] == 0
    assert vault.normalize_recovery_key(rk) == rk


def test_vault_unlock_paths(tmp_path, monkeypatch):
    import pytest
    vault = _vault(tmp_path, monkeypatch)
    rk = vault.init("master-passwort-test")
    vault.lock()
    assert vault.status()["locked"]
    with pytest.raises(PermissionError):
        vault.list_entries()
    with pytest.raises(ValueError):
        vault.unlock("falsches-passwort")
    vault.unlock("master-passwort-test")
    assert not vault.status()["locked"]
    # Recovery-Pfad entsperrt ebenfalls (setzt neues PW)
    vault.lock()
    rk2 = vault.recover(rk, "neues-master-passwort")
    assert not vault.status()["locked"] and rk2 != rk


def test_vault_rotate_and_recover_semantics(tmp_path, monkeypatch):
    import pytest
    vault = _vault(tmp_path, monkeypatch)
    rk = vault.init("master-passwort-test")
    vault.add_entry("eintrag", "wert1234")
    vault.rotate_master("master-passwort-test", "zweites-passwort-x")
    vault.lock()
    with pytest.raises(ValueError):
        vault.unlock("master-passwort-test")               # altes PW tot
    vault.unlock("zweites-passwort-x")
    assert len(vault.list_entries()) == 1                  # Einträge unangetastet
    # recover: alter Recovery-Key verfällt, neuer gilt
    rk2 = vault.recover(rk, "drittes-passwort-x")
    with pytest.raises(ValueError):
        vault.recover(rk, "viertes-passwort-x")
    assert vault.recover(rk2, "viertes-passwort-x")


def test_recovery_key_format(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    rk = vault._gen_recovery_key()
    groups = rk.split("-")
    assert len(groups) == 6 and all(len(g) == 5 for g in groups)
    assert all(c in vault.B32 + "-" + vault.CHECK for c in rk)
    # Normalisierung: Kleinschreibung, Leerzeichen, i/l→1, o→0
    assert vault.normalize_recovery_key(rk.lower().replace("-", " ")) == rk
    messy = rk.replace("1", "l", 1) if "1" in rk else rk.replace("0", "O", 1) if "0" in rk else rk
    assert vault.normalize_recovery_key(messy) == rk
    # Zeichen-Verfälschung → Prüfsymbol schlägt an
    c0 = rk[0]
    swapped = ("9" if c0 != "9" else "3") + rk[1:]
    assert vault.normalize_recovery_key(swapped) is None
    assert vault.normalize_recovery_key("zu-kurz") is None


def test_vault_run_injection(tmp_path, monkeypatch, capsys):
    vault = _vault(tmp_path, monkeypatch)
    vault.init("master-passwort-test")
    vault.add_entry("demo", "Str3ngGeheim!")
    rc = vault.run(["sh", "-c", "echo Wert: {{tresor:demo}}; env | grep -c OP_SECRET_DEMO"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Str3ngGeheim!" not in out and "«tresor:demo»" in out
    assert "1" in out                                       # env-Variable war im Kind gesetzt
    rc2 = vault.run(["echo", "{{tresor:gibtsnicht}}"])
    assert rc2 == 3
    vault.lock()
    rc3 = vault.run(["echo", "{{tresor:demo}}"])
    assert rc3 == 2 and "gesperrt" in capsys.readouterr().err


def test_redact_patterns():
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import redact
    t = redact.redact(
        "Bearer abcdefghij1234567890ABCD und syt_bWljaGk_AbCdEfGhIjKlMnOpQrSt und "
        "AKIAABCDEFGHIJKLMNOP und ghp_abcdefghij1234567890 und "
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc12345 und password: SehrGeheim99",
        extra_values=("MeinTresorWert",))
    for leak in ("abcdefghij1234567890ABCD", "syt_", "AKIA", "ghp_", "SehrGeheim99"):
        assert leak not in t, leak
    assert redact.redact("x MeinTresorWert y", ("MeinTresorWert",)) == "x [REDACTED:tresor] y"
    assert redact.redact("harmloser Text 123") == "harmloser Text 123"


def test_sessions_record_redacts(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import sessions
    monkeypatch.setattr(sessions, "DB", str(tmp_path / "s.db"))
    sessions.record("owner", "hier mein Token syt_abcdefghijklmnopqrstuvwx",
                    "ok, Bearer abcdefghij1234567890XYZAB genutzt", 0, 10)
    s = sessions.list_sessions()[0]
    assert "syt_" not in s["messages"] and "abcdefghij1234567890XYZAB" not in s["result"]
    assert sessions.search("syt_abcdefghijklmnopqrstuvwx") == []


def test_redact_is_stdlib_only():
    import ast
    src = open(os.path.expanduser("~/.claude/matrix-bot/redact.py")).read()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert not (imports & {"fastapi", "uvicorn", "msal", "cryptography", "requests"})


def test_scrypt_production_params():
    import hashlib
    key = hashlib.scrypt(b"pw", salt=b"x" * 16, n=2 ** 17, r=8, p=1,
                         maxmem=192 * 1024 * 1024, dklen=32)
    assert len(key) == 32


# ---------------------------------------------------------------- Tresor: FIDO2 --
def _fake_fido(vault):
    """Installiert einen Software-Authenticator (HMAC-basiert) in vault.py.
    ACTIVE['key'] bestimmt, welcher „Schlüssel" gerade angesteckt ist."""
    import hashlib, hmac
    ACTIVE = {}
    class FakeKey:
        def __init__(self, name):
            self.cred_id = ("cred-" + name).encode().ljust(16, b"\0")
            self.cr = hashlib.sha256(name.encode()).digest()
        def secret(self, salt):
            return hmac.new(self.cr, salt, hashlib.sha256).digest()
    def make_hook(salt):
        k = ACTIVE["key"]; return k.cred_id, k.secret(salt)
    def get_hook(cred_ids, salt):
        k = ACTIVE["key"]; return k.cred_id, k.secret(salt)
    vault._FIDO_MAKE_HOOK = make_hook
    vault._FIDO_GET_HOOK = get_hook
    return ACTIVE, FakeKey


def test_fido_enroll_and_unlock(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    ACTIVE, FakeKey = _fake_fido(vault)
    vault.init("master-passwort-test")
    vault.add_entry("gitea", "GEHEIM-123")
    keyA = FakeKey("A"); ACTIVE["key"] = keyA
    vault.fido_enroll("YubiKey blau")
    assert vault.status()["fido_keys"] == 1
    assert [k["label"] for k in vault.fido_list()] == ["YubiKey blau"]
    # Entsperren ohne Master-Passwort, nur mit dem Key
    vault.lock()
    assert vault.status()["locked"]
    vault.fido_unlock()
    assert not vault.status()["locked"]
    assert [e["name"] for e in vault.list_entries()] == ["gitea"]
    # vault.enc bleibt klartextfrei
    assert b"GEHEIM-123" not in open(str(tmp_path / "vault.enc"), "rb").read()


def test_fido_multi_key_and_isolation(tmp_path, monkeypatch):
    import pytest
    vault = _vault(tmp_path, monkeypatch)
    ACTIVE, FakeKey = _fake_fido(vault)
    vault.init("master-passwort-test")
    a, b = FakeKey("A"), FakeKey("B")
    ACTIVE["key"] = a; vault.fido_enroll("Haupt")
    ACTIVE["key"] = b; vault.fido_enroll("Backup")
    assert vault.status()["fido_keys"] == 2
    # Beide Keys öffnen (gemeinsames Salt, je eigener Wrap)
    for k in (a, b):
        vault.lock(); ACTIVE["key"] = k; vault.fido_unlock()
        assert not vault.status()["locked"]
    # Fremder Key wird abgewiesen
    vault.lock(); ACTIVE["key"] = FakeKey("boese")
    with pytest.raises(ValueError):
        vault.fido_unlock()


def test_fido_remove_and_backward_compat(tmp_path, monkeypatch):
    import json
    vault = _vault(tmp_path, monkeypatch)
    ACTIVE, FakeKey = _fake_fido(vault)
    vault.init("master-passwort-test")
    ACTIVE["key"] = FakeKey("A"); vault.fido_enroll("Haupt")
    vault.fido_remove("Haupt")
    assert vault.fido_list() == []
    # Vault ohne fido-Feld bleibt lesbar (Bestandsdatei)
    v = json.load(open(str(tmp_path / "vault.enc")))
    assert "fido" not in v or v["fido"] == []
    assert vault.status()["fido_keys"] == 0
    # Entsperren per Master-PW weiter möglich
    vault.lock(); vault.unlock("master-passwort-test")
    assert not vault.status()["locked"]


# ---------------------------------------------------------------- Pseudonymisierung --
def test_pseudonym_roundtrip_and_detection():
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import pseudonym
    m = {}
    src = "Schreib an Thomas Müller (thomas@kanzlei.de) und ruf Frau Wagner an."
    p, m, st = pseudonym.pseudonymize(src, m)
    assert "Thomas Müller" not in p and "thomas@kanzlei.de" not in p and "Wagner" not in p
    assert "Schreib" in p and "ruf" in p and "Frau" in p
    assert pseudonym.reidentify(p, m) == src
    assert st.get("PERSON", 0) >= 2 and st.get("EMAIL_ADDRESS", 0) == 1


def test_pseudonym_consistency_and_allow():
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import pseudonym
    m = {}
    p, m, _ = pseudonym.pseudonymize(
        "Anna Klein und Anna Klein sind dieselbe. Michi bleibt.", m, allow=["Michi"])
    fake = [k for k, v in m["s2r"].items() if v == "Anna Klein"][0]
    assert p.count(fake) == 2 and "Anna Klein" not in p
    assert "Michi" in p


def test_pseudonym_deny_list():
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import pseudonym
    m = {}
    p, m, _ = pseudonym.pseudonymize("Ruf bei Firma Delphin GmbH an.", m,
                                     deny=["Firma Delphin GmbH"])
    assert "Firma Delphin GmbH" not in p


def test_reid_stdlib_and_env(tmp_path, monkeypatch):
    import json as _j
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import reid
    monkeypatch.delenv("OPERATOR_PII_MAP", raising=False)
    assert reid.reidentify("Hallo Fake Name") == "Hallo Fake Name"
    mp = tmp_path / "m.json"
    mp.write_text(_j.dumps({"s2r": {"Fake Name": "Echt Person", "x@fake.de": "y@real.de"}}))
    monkeypatch.setenv("OPERATOR_PII_MAP", str(mp))
    assert reid.reidentify("Mail an Fake Name (x@fake.de)") == "Mail an Echt Person (y@real.de)"


def test_reid_is_stdlib_only():
    import ast
    src = open(os.path.expanduser("~/.claude/matrix-bot/reid.py")).read()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert not (imports & {"fastapi", "presidio_analyzer", "spacy", "faker", "cryptography"})
