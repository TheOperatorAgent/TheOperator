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
    # Injection-Mechanik: für den Test „sh" erlauben (echt blockiert, s. u.)
    monkeypatch.setattr(vault, "_run_allowlist", lambda: {"sh"})
    rc = vault.run(["sh", "-c", "echo Wert: {{tresor:demo}}; env | grep -c OP_SECRET_DEMO"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Str3ngGeheim!" not in out and "«tresor:demo»" in out
    assert "1" in out                                       # env-Variable war im Kind gesetzt
    rc2 = vault.run(["sh", "-c", "echo {{tresor:gibtsnicht}}"])
    assert rc2 == 3
    vault.lock()
    rc3 = vault.run(["sh", "-c", "echo {{tresor:demo}}"])
    assert rc3 == 2 and "gesperrt" in capsys.readouterr().err


def test_vault_run_allowlist(tmp_path, monkeypatch, capsys):
    vault = _vault(tmp_path, monkeypatch)
    vault.init("master-passwort-test")
    vault.add_entry("demo", "Str3ngGeheim!")
    # echo/sh mit Referenz → abgelehnt (Härtung #22)
    assert vault.run(["echo", "{{tresor:demo}}"]) == 5
    assert vault.run(["sh", "-c", "echo {{tresor:demo}}"]) == 5
    assert "Freigabeliste" in capsys.readouterr().err
    # allowlisted Programm (curl) → nicht wegen Allowlist abgelehnt (scheitert nur am Netz)
    assert vault.run(["curl", "-s", "--max-time", "1", "http://127.0.0.1:1/{{tresor:demo}}"]) != 5
    # Kommando OHNE Referenz → Allowlist irrelevant
    assert vault.run(["echo", "hallo"]) == 0
    # konfigurierbar
    monkeypatch.setattr(vault, "_run_allowlist", lambda: vault.DEFAULT_RUN_ALLOWLIST | {"echo"})
    assert vault.run(["echo", "{{tresor:demo}}"]) == 0


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


# ---------------------------------------------------------------- #35/#20 --
def test_pseudonym_gender_from_anrede():
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import pseudonym
    from faker.providers.person.de_DE import Provider
    fem, mal = set(Provider.first_names_female), set(Provider.first_names_male)
    m = {}
    pf, m, _ = pseudonym.pseudonymize("Schreib an Frau Wagner.", m)
    m2 = {}
    pm, m2, _ = pseudonym.pseudonymize("Schreib an Herrn Berger.", m2)
    ff = [k for k, v in m["s2r"].items() if v == "Wagner"][0].split()[0]
    fm = [k for k, v in m2["s2r"].items() if v == "Berger"][0].split()[0]
    assert ff in fem and fm in mal


def test_migrate_tokens_idempotent(tmp_path, monkeypatch):
    import json as _j
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import migrate_tokens as MT
    monkeypatch.setattr(MT, "BOT_DIR", str(tmp_path))
    monkeypatch.setattr(MT, "_keychain_set", lambda a, v: True)
    (tmp_path / "credentials.json").write_text(_j.dumps({"access_token": "syt_klartext_xyz"}))
    (tmp_path / "bots.json").write_text(_j.dumps({"bots": [{"agent": "b1", "access_token": "syt_bot"}]}))
    assert MT.migrate() == 2
    assert _j.loads((tmp_path / "credentials.json").read_text())["access_token"] == "keychain"
    assert _j.loads((tmp_path / "bots.json").read_text())["bots"][0]["access_token"] == "keychain"
    assert MT.migrate() == 0    # idempotent


def test_migrate_tokens_is_stdlib_only():
    import ast
    src = open(os.path.expanduser("~/.claude/matrix-bot/migrate_tokens.py")).read()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert not (imports & {"fastapi", "presidio_analyzer", "spacy", "faker", "cryptography"})


# ---------------------------------------------------------------- Vaultwarden-Backend --
def _vw(tmp_path, monkeypatch):
    """Vaultwarden-Modul mit tmp-Session/Conn und gemocktem bw laden."""
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import vaultwarden as vw
    monkeypatch.setattr(vw, "CONN_FILE", str(tmp_path / "vaultwarden.json"))
    monkeypatch.setattr(vw, "_session_path", lambda: str(tmp_path / "vw.session"))
    monkeypatch.setattr(vw, "BOT_DIR", str(tmp_path))   # dashboard.json fehlt → autolock 0
    return vw


def test_vaultwarden_unlock_login_then_unlock(tmp_path, monkeypatch):
    import json as _j
    vw = _vw(tmp_path, monkeypatch)
    (tmp_path / "vaultwarden.json").write_text(_j.dumps({"url": "https://vault.example"}))
    calls = []

    def fake_bw(args, session=None, stdin=None, pw=None, timeout=60):
        calls.append((list(args), pw))
        assert "geheim-master-pw" not in args           # Passwort NIE in argv
        if args[:1] == ["status"]:
            state = "unauthenticated" if len(calls) == 1 else "locked"
            return 0, _j.dumps({"status": state}), ""
        if args[:1] == ["login"]:
            assert pw == "geheim-master-pw"              # Passwort nur per env/pw
            return 0, "SESSIONTOKEN-A\n", ""
        if args[:1] == ["unlock"]:
            assert pw == "geheim-master-pw"
            return 0, "SESSIONTOKEN-B\n", ""
        return 1, "", "unerwartet"

    monkeypatch.setattr(vw, "_bw", fake_bw)
    # Erste Anmeldung: Login-Pfad (braucht E-Mail), Token wird gespeichert
    assert vw.unlock("geheim-master-pw", "du@example.de") == "login"
    assert vw.session() == "SESSIONTOKEN-A"
    assert oct(os.stat(str(tmp_path / "vw.session")).st_mode)[-3:] == "600"
    # Zweites Mal (schon angemeldet): Unlock-Pfad, ohne E-Mail
    assert vw.unlock("geheim-master-pw") == "unlock"
    assert vw.session() == "SESSIONTOKEN-B"


def test_vaultwarden_unlock_wrong_pw_and_2fa(tmp_path, monkeypatch):
    import json as _j
    import pytest
    vw = _vw(tmp_path, monkeypatch)
    (tmp_path / "vaultwarden.json").write_text(_j.dumps({"url": "https://vault.example"}))

    def fake_bw(args, session=None, stdin=None, pw=None, timeout=60):
        if args[:1] == ["status"]:
            return 0, _j.dumps({"status": "locked"}), ""
        return 1, "", "Invalid master password."
    monkeypatch.setattr(vw, "_bw", fake_bw)
    with pytest.raises(ValueError, match="Master-Passwort"):
        vw.unlock("falsch")
    assert vw.session() is None                          # kein Token gespeichert

    def fake_bw_2fa(args, session=None, stdin=None, pw=None, timeout=60):
        if args[:1] == ["status"]:
            return 0, _j.dumps({"status": "locked"}), ""
        return 1, "", "Two-step login is enabled on this account."
    monkeypatch.setattr(vw, "_bw", fake_bw_2fa)
    with pytest.raises(ValueError, match="Zwei-Faktor"):
        vw.unlock("egal")


def test_vaultwarden_list_items_readonly(tmp_path, monkeypatch):
    import json as _j
    import pytest
    vw = _vw(tmp_path, monkeypatch)
    items = [
        {"type": 1, "name": "gitea-admin", "login": {"username": "root",
         "uris": [{"uri": "https://gitea.example"}]}},
        {"type": 2, "name": "eine-notiz"},               # Secure Note → ignorieren
        {"type": 1, "name": "smtp", "login": {"username": "", "uris": []}},
    ]
    monkeypatch.setattr(vw, "_bw",
                        lambda a, session=None, stdin=None, pw=None, timeout=60:
                        (0, _j.dumps(items), "") if a[:2] == ["list", "items"] else (1, "", ""))
    out = vw.list_items("SESSIONTOKEN")
    assert [x["name"] for x in out] == ["gitea-admin", "smtp"]     # nur Logins, sortiert
    assert out[0]["username"] == "root" and out[0]["url"] == "https://gitea.example"
    assert all("value" not in x and "password" not in x for x in out)   # keine Passwörter
    # ohne Session → gesperrt
    monkeypatch.setattr(vw, "SESSION_OVERRIDE", "")
    with pytest.raises(PermissionError):
        vw.list_items()


def test_vaultwarden_get_password(tmp_path, monkeypatch):
    vw = _vw(tmp_path, monkeypatch)
    monkeypatch.setattr(vw, "_bw",
                        lambda a, session=None, stdin=None, pw=None, timeout=60:
                        (0, "Str3ngGeheim!\n", "") if a == ["get", "password", "demo"]
                        else (1, "", "Not found."))
    assert vw.get_password("demo", "TOK") == "Str3ngGeheim!"
    assert vw.get_password("gibtsnicht", "TOK") is None
    assert vw.get_password("demo", "") is None            # ohne Session


def test_vault_backend_switch(tmp_path, monkeypatch):
    import json as _j
    vault = _vault(tmp_path, monkeypatch)
    monkeypatch.setattr(vault, "BOT_DIR", str(tmp_path))
    # keine dashboard.json → Default lokal
    assert vault._backend() == "local"
    (tmp_path / "dashboard.json").write_text(_j.dumps({}))
    assert vault._backend() == "local"
    (tmp_path / "dashboard.json").write_text(_j.dumps({"vault_backend": "vaultwarden"}))
    assert vault._backend() == "vaultwarden"
    (tmp_path / "dashboard.json").write_text(_j.dumps({"vault_backend": "quatsch"}))
    assert vault._backend() == "local"                    # unbekannt → lokal


def test_vault_run_via_vaultwarden(tmp_path, monkeypatch, capsys):
    vault = _vault(tmp_path, monkeypatch)
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import vaultwarden as vw
    monkeypatch.setattr(vault, "_backend", lambda: "vaultwarden")
    monkeypatch.setattr(vw, "session", lambda: "SESSIONTOKEN")
    monkeypatch.setattr(vw, "_session_path", lambda: str(tmp_path / "vw.session"))
    open(str(tmp_path / "vw.session"), "w").close()        # für utime im Kern
    secrets = {"demo": "Str3ngGeheim!"}
    monkeypatch.setattr(vw, "get_password", lambda n, s: secrets.get(n))
    monkeypatch.setattr(vault, "_run_allowlist", lambda: {"sh"})
    rc = vault.run(["sh", "-c", "echo Wert: {{tresor:demo}}; env | grep -c OP_SECRET_DEMO"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Str3ngGeheim!" not in out and "«tresor:demo»" in out     # aufgelöst + redacted
    assert "1" in out                                     # env-Variable im Kind gesetzt
    # unbekannter Eintrag → 3
    assert vault.run(["sh", "-c", "echo {{tresor:gibtsnicht}}"]) == 3
    # Allowlist greift auch bei Vaultwarden (echo mit Referenz → 5)
    monkeypatch.setattr(vault, "_run_allowlist", lambda: vault.DEFAULT_RUN_ALLOWLIST)
    assert vault.run(["echo", "{{tresor:demo}}"]) == 5
    # gesperrt (keine Session) → 2
    monkeypatch.setattr(vw, "session", lambda: None)
    assert vault.run(["sh", "-c", "echo {{tresor:demo}}"]) == 2


def test_vaultwarden_is_stdlib_only():
    import ast
    src = open(os.path.expanduser("~/.claude/matrix-bot/vaultwarden.py")).read()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert not (imports & {"fastapi", "presidio_analyzer", "spacy", "faker", "cryptography"})


# ---------------------------------------------------------------- Cross-Platform-Kern --
def _import_plat():
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import platform_compat, secretstore, servicemgr
    return platform_compat, secretstore, servicemgr


def test_platform_modules_are_stdlib_only():
    import ast
    for mod in ("platform_compat", "secretstore", "servicemgr"):
        src = open(os.path.expanduser(f"~/.claude/matrix-bot/{mod}.py")).read()
        imports = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        assert not (imports & {"fastapi", "presidio_analyzer", "spacy", "faker",
                               "cryptography", "keyring", "uvicorn"}), (mod, imports)


def test_platform_venv_python_per_os(monkeypatch):
    pc, _, _ = _import_plat()
    monkeypatch.setattr(pc, "IS_WIN", True)
    assert pc.venv_python("/x").endswith(os.path.join("Scripts", "python.exe"))
    monkeypatch.setattr(pc, "IS_WIN", False)
    assert pc.venv_python("/x").endswith(os.path.join("bin", "python3"))


def test_platform_runtime_file_namespacing(monkeypatch):
    pc, _, _ = _import_plat()
    # per-user Basis (macOS/Windows) → flacher Name
    monkeypatch.setattr(pc, "IS_MAC", True)
    monkeypatch.setattr(pc, "IS_WIN", False)
    monkeypatch.setattr(pc, "IS_LINUX", False)
    assert os.path.basename(pc.runtime_file("x.sock")) == "x.sock"
    # geteiltes /tmp (Linux ohne XDG_RUNTIME_DIR) → Nutzer-Tag angehängt
    monkeypatch.setattr(pc, "IS_MAC", False)
    monkeypatch.setattr(pc, "IS_LINUX", True)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert pc.runtime_file("x.sock").endswith(f"x.sock.{pc.user_tag()}")


def test_platform_user_tag_and_owns():
    pc, _, _ = _import_plat()
    tag = pc.user_tag()
    assert tag and isinstance(tag, str)
    # owns() auf eine eigene Datei ist True
    import tempfile as _tf
    f = _tf.NamedTemporaryFile(delete=False)
    f.close()
    try:
        assert pc.owns(os.stat(f.name)) is True
    finally:
        os.remove(f.name)


def test_ipc_bind_posix_unix_socket(monkeypatch):
    pc, _, _ = _import_plat()
    monkeypatch.setattr(pc, "IS_WIN", False)
    # kurzer Pfad wegen AF_UNIX-104-Zeichen-Limit (pytest-tmp_path ist zu lang)
    short = f"/tmp/op-{os.getpid()}"
    monkeypatch.setattr(pc, "runtime_file", lambda n: f"{short}-{n}")
    srv, tok = pc.ipc_bind()
    try:
        assert tok is None
        assert os.path.exists(f"{short}-operator-pseudonym.sock")
        # Client kann sich verbinden
        sock, ctok = pc.ipc_connect(timeout=2)
        assert ctok is None
        sock.close()
    finally:
        srv.close()
        pc.ipc_cleanup()


def test_secretstore_file_fallback(tmp_path, monkeypatch):
    pc, ss, _ = _import_plat()
    monkeypatch.setattr(pc, "IS_MAC", False)
    monkeypatch.setattr(pc, "IS_WIN", False)
    monkeypatch.setattr(pc, "IS_LINUX", False)
    monkeypatch.setattr(ss, "SECRETS_DIR", str(tmp_path / "secrets"))
    assert ss.get("acct-x") is None
    ss.set("acct-x", "s3cr3t-token")
    assert ss.get("acct-x") == "s3cr3t-token"
    assert ss.get_or("acct-x", "fb") == "s3cr3t-token"
    assert ss.get_or("fehlt", "fb") == "fb"
    p = os.path.join(str(tmp_path / "secrets"), "acct-x.secret")
    assert oct(os.stat(p).st_mode)[-3:] == "600"
    assert ss.available_backend() == "file-0600"
    ss.delete("acct-x")
    assert ss.get("acct-x") is None


def test_servicemgr_labels_per_os(monkeypatch):
    pc, _, sm = _import_plat()
    monkeypatch.setattr(pc, "IS_MAC", True)
    monkeypatch.setattr(pc, "IS_WIN", False)
    monkeypatch.setattr(pc, "IS_LINUX", False)
    assert sm.label("listener") == "com.the-operator.listener"
    monkeypatch.setattr(pc, "IS_MAC", False)
    monkeypatch.setattr(pc, "IS_LINUX", True)
    assert sm.label("dashboard") == "operator-dashboard"
    monkeypatch.setattr(pc, "IS_LINUX", False)
    monkeypatch.setattr(pc, "IS_WIN", True)
    assert sm.label("pseudonym") == "OperatorPseudonym"


def test_vault_run_windows_cmd_substitution(tmp_path, monkeypatch):
    """Windows-Zweig von vault.run: cmd /c + %VAR%-Ersetzung, Klartext nie in argv.
    Plattform-gemockt (kein echtes Windows nötig); subprocess.run wird abgefangen."""
    vault = _vault(tmp_path, monkeypatch)
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import platform_compat as pc
    monkeypatch.setattr(pc, "IS_WIN", True)
    vault.init("master-passwort-test")
    vault.add_entry("demo", "Str3ngGeheim!")
    monkeypatch.setattr(vault, "_run_allowlist", lambda: {"curl"})
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["env"] = kw.get("env", {})
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(vault.subprocess, "run", fake_run)
    rc = vault.run(["curl", "https://example/{{tresor:demo}}"])
    assert rc == 0
    assert captured["argv"][0] == "cmd" and captured["argv"][1] == "/c"      # Windows-Shell
    assert "%OP_SECRET_DEMO%" in captured["argv"][2]                          # %VAR%-Syntax
    assert "Str3ngGeheim!" not in captured["argv"][2]                        # Klartext NIE in argv
    assert captured["env"]["OP_SECRET_DEMO"] == "Str3ngGeheim!"              # Wert nur in env


# ---------------------------------------------------------------- Multi-LLM --
def _providers(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
    import providers
    monkeypatch.setattr(providers, "MODELS_FILE", str(tmp_path / "models.json"))
    return providers


def test_providers_resolve_claude_vs_foreign(tmp_path, monkeypatch):
    p = _providers(tmp_path, monkeypatch)
    assert p.resolve("haiku") == {"kind": "claude", "model": "haiku"}
    assert p.resolve("inherit")["model"] is None
    assert p.resolve("")["kind"] == "claude"
    assert p.resolve("claude-opus-4-8") == {"kind": "claude", "model": "claude-opus-4-8"}
    # Fremd-Modell braucht konfigurierten Provider
    p.set_provider("ollama", base_url="http://localhost:11434", models=["llama3.1"],
                   default="llama3.1", enabled=True)
    r = p.resolve("ollama/llama3.1")
    assert r["kind"] == "foreign" and r["provider"] == "ollama"
    assert r["model_id"] == "llama3.1" and r["base_url"] == "http://localhost:11434"
    # Unbekanntes → sicher als Claude-Standard
    assert p.resolve("quatschprovider/x")["kind"] == "claude"


def test_providers_list_models(tmp_path, monkeypatch):
    p = _providers(tmp_path, monkeypatch)
    vals = [m["value"] for m in p.list_models()]
    assert vals[:4] == ["inherit", "haiku", "sonnet", "opus"]        # nur Claude by default
    p.set_provider("openai", models=["gpt-4o", "gpt-4o-mini"], enabled=True)
    vals = [m["value"] for m in p.list_models()]
    assert "openai/gpt-4o" in vals and "openai/gpt-4o-mini" in vals
    p.set_provider("openai", enabled=False)                          # deaktiviert → raus
    assert "openai/gpt-4o" not in [m["value"] for m in p.list_models()]


def test_providers_fallback_key(tmp_path, monkeypatch):
    p = _providers(tmp_path, monkeypatch)
    monkeypatch.setattr(p.secretstore, "get", lambda a: "sk-ant-test" if a == "anthropic_api_key" else None)
    assert p.fallback_key() is None                                  # deaktiviert
    p.set_anthropic_fallback(enabled=True)
    assert p.fallback_key() == "sk-ant-test"                         # aktiv + Key → einspringbar


def test_providers_is_stdlib_only():
    import ast
    src = open(os.path.expanduser("~/.claude/matrix-bot/providers.py")).read()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert not (imports & {"openai", "anthropic", "fastapi", "cryptography", "requests"})


def test_agents_store_allows_foreign_model(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot/dashboard"))
    import agents_store
    monkeypatch.setattr(agents_store, "AGENTS_DIR", str(tmp_path / "agents"))
    ok, _ = agents_store.save_agent("rechercheur", "Recherche", ["Read"], "ollama/llama3.1", "Du recherchierst.")
    assert ok
    ok, _ = agents_store.save_agent("gpt-bot", "Text", ["Read"], "openai/gpt-4o", "Du schreibst.")
    assert ok
    ok, msg = agents_store.save_agent("kaputt", "x", ["Read"], "gibtsnicht", "y")
    assert not ok and "Modell" in msg


def test_llm_runner_against_mock(tmp_path):
    """Der Runner (venv, openai-SDK) spricht einen OpenAI-kompatiblen Endpoint korrekt an."""
    import http.server
    import json as _j
    import socket
    import subprocess
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = _j.loads(self.rfile.read(n) or b"{}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(_j.dumps({"choices": [{"message": {
                "content": "Echo:" + body.get("model", "?")}}]}).encode())

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        req = _j.dumps({"provider": "openai", "base_url": f"http://127.0.0.1:{port}/v1",
                        "key": "sk-test", "model_id": "gpt-4o", "prompt": "Hi"})
        r = subprocess.run([sys.executable, os.path.expanduser("~/.claude/matrix-bot/llm_runner.py")],
                           input=req, capture_output=True, text=True, timeout=30)
        out = _j.loads(r.stdout)
        assert out.get("text") == "Echo:gpt-4o"
    finally:
        srv.shutdown()
