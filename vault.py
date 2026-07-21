#!/usr/bin/env python3
"""Operator Passwort-Tresor — lokaler Tresor mit Master-Passwort + Wiederherstellungsschlüssel.

Design (siehe SICHERHEIT.md):
- EINE Datei secrets/vault.enc: zufälliger 32-B-DEK verschlüsselt die Eintrags-DB
  (AES-256-GCM); der DEK ist zweifach gewrappt (age-Envelope-Muster):
  KEK aus Master-Passwort und KEK aus Recovery-Key (beide via scrypt, OWASP-Parameter).
  Master-Wechsel/Recovery = nur Rewrap — Einträge bleiben unangetastet.
- Recovery-Key: Crockford Base32, 6 Gruppen à 5 Zeichen (145 bit + 1 Prüfsymbol).
  Wird bei init/recover GENAU EINMAL ausgegeben, nie gespeichert.
- Entsperrt = DEK (hex) als 0600-Datei im nutzer-privaten Temp-Verzeichnis
  (DARWIN_USER_TEMP_DIR) — verschwindet beim Reboot: Sitzungs-Semantik.
- Nutzung durch den Operator NUR per Referenz: `vault.py run -- cmd {{tresor:name}}`
  injiziert Werte als Umgebungsvariablen (nie argv) und redacted die Ausgabe.

Läuft im dashboard-venv (braucht cryptography):
  ~/.claude/matrix-bot/dashboard/venv/bin/python3 ~/.claude/matrix-bot/vault.py <kommando>
"""
import base64
import getpass
import json
import os
import re
import secrets as pysecrets
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
import redact as redact_mod  # noqa: E402  (stdlib-Modul aus BOT_DIR)

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
VAULT_FILE = os.path.join(BOT_DIR, "secrets", "vault.enc")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
REF_RE = re.compile(r"\{\{\s*tresor:([a-z0-9._-]+)\s*\}\}")

# scrypt (OWASP-Fallback-Empfehlung); maxmem großzügig über 128*r*N
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 17, 8, 1
SCRYPT_MAXMEM = 192 * 1024 * 1024

AAD_DEK = b"operator-vault-dek-v1"
AAD_DATA = b"operator-vault-data-v1"

# Crockford Base32 (ohne I, L, O, U)
B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CHECK = B32 + "*~$=U"  # mod-37-Prüfalphabet nach Crockford
DEK_PATH_OVERRIDE = None

LOCKED_MSG = ("Tresor ist gesperrt. Michi kann ihn im Dashboard "
              "(http://127.0.0.1:8737, Tab „Tresor“) entsperren.")


# ---------------------------------------------------------------- Krypto --
def _aesgcm(key: bytes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key)


def _kdf(secret: str, salt: bytes) -> bytes:
    import hashlib
    return hashlib.scrypt(secret.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
                          p=SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=32)


def _wrap(kek: bytes, dek: bytes) -> dict:
    nonce = os.urandom(12)
    return {"wrap_nonce": base64.b64encode(nonce).decode(),
            "wrapped_dek": base64.b64encode(_aesgcm(kek).encrypt(nonce, dek, AAD_DEK)).decode()}


def _unwrap(kek: bytes, blob: dict) -> bytes:
    from cryptography.exceptions import InvalidTag
    try:
        return _aesgcm(kek).decrypt(base64.b64decode(blob["wrap_nonce"]),
                                    base64.b64decode(blob["wrapped_dek"]), AAD_DEK)
    except InvalidTag:
        raise ValueError("falscher Schlüssel")


# ---------------------------------------------------------------- Recovery-Key --
def _gen_recovery_key() -> str:
    """29 Datenzeichen Crockford Base32 (145 bit) + 1 Prüfsymbol, Gruppen à 5."""
    chars = [B32[pysecrets.randbelow(32)] for _ in range(29)]
    val = 0
    for c in chars:
        val = val * 32 + B32.index(c)
    chars.append(CHECK[val % 37])
    raw = "".join(chars)
    return "-".join(raw[i:i + 5] for i in range(0, 30, 5))


def normalize_recovery_key(text: str) -> str | None:
    """Normalisiert Eingabe (Groß/Klein, i/l→1, o→0, Trenner) und prüft das Prüfsymbol.
    None = Format/Prüfsymbol falsch (Feedback VOR dem teuren scrypt-Lauf)."""
    t = re.sub(r"[\s-]", "", text.upper()).replace("I", "1").replace("L", "1").replace("O", "0")
    if len(t) != 30 or any(c not in B32 for c in t[:29]) or t[29] not in CHECK:
        return None
    val = 0
    for c in t[:29]:
        val = val * 32 + B32.index(c)
    if CHECK[val % 37] != t[29]:
        return None
    return "-".join(t[i:i + 5] for i in range(0, 30, 5))


# ---------------------------------------------------------------- Datei/Session --
def _read_vault() -> dict:
    if not os.path.exists(VAULT_FILE):
        raise RuntimeError("Kein Tresor vorhanden — im Dashboard unter „Tresor“ anlegen.")
    return json.load(open(VAULT_FILE))


def _write_vault(v: dict) -> None:
    v["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(os.path.dirname(VAULT_FILE), mode=0o700, exist_ok=True)
    fd = os.open(VAULT_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(v, f, indent=1)
    os.replace(VAULT_FILE + ".tmp", VAULT_FILE)


def _dek_path() -> str:
    if DEK_PATH_OVERRIDE:
        return DEK_PATH_OVERRIDE
    try:
        base = os.confstr("CS_DARWIN_USER_TEMP_DIR")
    except (ValueError, OSError):
        base = None
    if base and os.path.isdir(base):
        return os.path.join(base, "operator-vault.dek")
    return f"/private/tmp/operator-vault-{os.getuid()}.dek"


def _store_dek(dek: bytes) -> None:
    p = _dek_path()
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(dek.hex())


def _autolock_minutes() -> int:
    try:
        return int(json.load(open(os.path.join(BOT_DIR, "dashboard.json"))).get(
            "vault_autolock_minutes", 0))
    except Exception:
        return 0


def _load_dek() -> bytes | None:
    p = _dek_path()
    try:
        st = os.stat(p)
        if st.st_uid != os.getuid():
            return None
        mins = _autolock_minutes()
        if mins > 0 and time.time() - st.st_mtime > mins * 60:
            os.remove(p)
            return None
        return bytes.fromhex(open(p).read().strip())
    except (OSError, ValueError):
        return None


def _drop_dek() -> None:
    try:
        os.remove(_dek_path())
    except OSError:
        pass


def _entries(dek: bytes, v: dict) -> dict:
    from cryptography.exceptions import InvalidTag
    try:
        pt = _aesgcm(dek).decrypt(base64.b64decode(v["data"]["nonce"]),
                                  base64.b64decode(v["data"]["ct"]), AAD_DATA)
    except InvalidTag:
        raise ValueError("Tresor-Daten beschädigt oder falscher Schlüssel")
    return json.loads(pt)


def _write_entries(dek: bytes, v: dict, entries: dict) -> None:
    nonce = os.urandom(12)
    ct = _aesgcm(dek).encrypt(nonce, json.dumps(entries, ensure_ascii=False).encode(), AAD_DATA)
    v["data"] = {"nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}
    _write_vault(v)


# ---------------------------------------------------------------- Öffentliche API --
def init(master_pw: str) -> str:
    """Tresor anlegen. Gibt den Recovery-Key zurück — die einzige Stelle, die ihn je sieht."""
    if os.path.exists(VAULT_FILE):
        raise RuntimeError("Tresor existiert bereits")
    if len(master_pw) < 10:
        raise ValueError("Master-Passwort muss mindestens 10 Zeichen haben")
    dek = os.urandom(32)
    recovery = _gen_recovery_key()
    m_salt, r_salt = os.urandom(16), os.urandom(16)
    v = {"version": 1,
         "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P},
         "master": {"salt": base64.b64encode(m_salt).decode(), **_wrap(_kdf(master_pw, m_salt), dek)},
         "recovery": {"salt": base64.b64encode(r_salt).decode(), **_wrap(_kdf(recovery, r_salt), dek)},
         "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _write_entries(dek, v, {})
    _store_dek(dek)
    return recovery


def unlock(master_pw: str) -> None:
    v = _read_vault()
    kek = _kdf(master_pw, base64.b64decode(v["master"]["salt"]))
    try:
        dek = _unwrap(kek, v["master"])
    except ValueError:
        raise ValueError("Falsches Master-Passwort")
    _store_dek(dek)


def lock() -> None:
    _drop_dek()


def status() -> dict:
    exists = os.path.exists(VAULT_FILE)
    dek = _load_dek() if exists else None
    n = None
    if dek:
        try:
            n = len(_entries(dek, _read_vault()))
        except Exception:
            n = None
    return {"exists": exists, "locked": dek is None,
            "entries": n, "autolock_minutes": _autolock_minutes(),
            "updated": _read_vault().get("updated") if exists else None}


def _require_dek() -> tuple:
    v = _read_vault()
    dek = _load_dek()
    if not dek:
        raise PermissionError(LOCKED_MSG)
    return dek, v


def add_entry(name: str, value: str, description: str = "", username: str = "", url: str = "") -> None:
    if not NAME_RE.match(name):
        raise ValueError("Name muss ^[a-z0-9][a-z0-9._-]{1,63}$ entsprechen (z. B. gitea-admin)")
    if not value:
        raise ValueError("Wert fehlt")
    dek, v = _require_dek()
    entries = _entries(dek, v)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    old = entries.get(name, {})
    entries[name] = {"value": value, "description": description or old.get("description", ""),
                     "username": username or old.get("username", ""),
                     "url": url or old.get("url", ""),
                     "created": old.get("created", now), "updated": now}
    _write_entries(dek, v, entries)


def update_meta(name: str, description=None, username=None, url=None) -> None:
    dek, v = _require_dek()
    entries = _entries(dek, v)
    if name not in entries:
        raise KeyError("Eintrag nicht gefunden")
    for k, val in (("description", description), ("username", username), ("url", url)):
        if val is not None:
            entries[name][k] = val
    entries[name]["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_entries(dek, v, entries)


def list_entries() -> list:
    """Metadaten ohne Werte — die write-only-Garantie liegt HIER (und in den API-Routen)."""
    dek, v = _require_dek()
    return [{"name": k, "description": e.get("description", ""),
             "username": e.get("username", ""), "url": e.get("url", ""),
             "created": e.get("created", ""), "updated": e.get("updated", "")}
            for k, e in sorted(_entries(dek, v).items())]


def remove_entry(name: str) -> None:
    dek, v = _require_dek()
    entries = _entries(dek, v)
    if name not in entries:
        raise KeyError("Eintrag nicht gefunden")
    del entries[name]
    _write_entries(dek, v, entries)


def rotate_master(old_pw: str, new_pw: str) -> None:
    if len(new_pw) < 10:
        raise ValueError("Neues Master-Passwort muss mindestens 10 Zeichen haben")
    v = _read_vault()
    try:
        dek = _unwrap(_kdf(old_pw, base64.b64decode(v["master"]["salt"])), v["master"])
    except ValueError:
        raise ValueError("Falsches Master-Passwort")
    salt = os.urandom(16)
    v["master"] = {"salt": base64.b64encode(salt).decode(), **_wrap(_kdf(new_pw, salt), dek)}
    _write_vault(v)
    _store_dek(dek)


def recover(recovery_key: str, new_master_pw: str) -> str:
    """Recovery: neues Master-Passwort setzen. Stellt IMMER einen neuen Recovery-Key aus."""
    if len(new_master_pw) < 10:
        raise ValueError("Neues Master-Passwort muss mindestens 10 Zeichen haben")
    norm = normalize_recovery_key(recovery_key)
    if not norm:
        raise ValueError("Der Wiederherstellungsschlüssel enthält einen Tippfehler (Prüfzeichen passt nicht)")
    v = _read_vault()
    try:
        dek = _unwrap(_kdf(norm, base64.b64decode(v["recovery"]["salt"])), v["recovery"])
    except ValueError:
        raise ValueError("Wiederherstellungsschlüssel ist nicht der richtige für diesen Tresor")
    new_recovery = _gen_recovery_key()
    m_salt, r_salt = os.urandom(16), os.urandom(16)
    v["master"] = {"salt": base64.b64encode(m_salt).decode(), **_wrap(_kdf(new_master_pw, m_salt), dek)}
    v["recovery"] = {"salt": base64.b64encode(r_salt).decode(), **_wrap(_kdf(new_recovery, r_salt), dek)}
    _write_vault(v)
    _store_dek(dek)
    return new_recovery


# ---------------------------------------------------------------- run (Injection) --
def run(argv: list, timeout: int = 120) -> int:
    """Führt ein Kommando aus und ersetzt {{tresor:name}}-Referenzen durch env-Variablen.
    Klartext landet nie in argv (ps!) und wird aus stdout/stderr redacted."""
    try:
        dek, v = _require_dek()
    except PermissionError:
        print(LOCKED_MSG, file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    entries = _entries(dek, v)
    cmd = shlex.join(argv)
    names = set(REF_RE.findall(cmd))
    unknown = [n for n in names if n not in entries]
    if unknown:
        print(f"Unbekannte Tresor-Einträge: {', '.join(sorted(unknown))} — "
              f"verfügbare Namen zeigt vault.py list", file=sys.stderr)
        return 3
    env = dict(os.environ)
    for n in names:
        var = "OP_SECRET_" + re.sub(r"[.-]", "_", n).upper()
        env[var] = entries[n]["value"]
        cmd = re.sub(r"\{\{\s*tresor:" + re.escape(n) + r"\s*\}\}", f'"${var}"', cmd)
    try:
        r = subprocess.run(["/bin/bash", "-c", cmd], env=env, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"Kommando nach {timeout}s abgebrochen", file=sys.stderr)
        return 124
    # ALLE Tresor-Werte redacten (nicht nur referenzierte) + generische Muster
    all_values = [(e["value"], k) for k, e in entries.items() if e.get("value")]
    sys.stdout.write(_redact_known(r.stdout, all_values))
    sys.stderr.write(_redact_known(r.stderr, all_values))
    # Idle-Reset für Auto-Lock
    try:
        os.utime(_dek_path())
    except OSError:
        pass
    _audit("vault.run", ", ".join(sorted(names)) or "(ohne Referenz)")
    return r.returncode


def _redact_known(text: str, values: list) -> str:
    for value, name in sorted(values, key=lambda x: -len(x[0])):
        if len(value) >= 4:
            text = text.replace(value, f"«tresor:{name}»")
    return redact_mod.redact(text)


def redact_stream() -> None:
    """stdin → stdout: bekannte Tresor-Werte + generische Muster ersetzen (Listener-Hook)."""
    text = sys.stdin.read()
    dek = _load_dek()
    if dek and os.path.exists(VAULT_FILE):
        try:
            entries = _entries(dek, _read_vault())
            text = _redact_known(text, [(e["value"], k) for k, e in entries.items()
                                        if e.get("value")])
        except Exception:
            text = redact_mod.redact(text)
    else:
        text = redact_mod.redact(text)
    sys.stdout.write(text)


def _audit(action: str, target: str) -> None:
    try:
        with open(os.path.join(BOT_DIR, "audit.log"), "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "actor": "vault",
                                "action": action, "target": target, "ok": True},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- CLI --
def _flag(args, *names, default=""):
    for n in names:
        if n in args:
            return args[args.index(n) + 1]
    return default


def main() -> int:
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    try:
        if cmd == "init":
            pw = getpass.getpass("Master-Passwort: ")
            if getpass.getpass("Wiederholen: ") != pw:
                print("Passwörter stimmen nicht überein", file=sys.stderr)
                return 1
            rk = init(pw)
            print("Tresor angelegt. Dein Wiederherstellungsschlüssel (JETZT sicher aufbewahren,"
                  " wird NIE wieder angezeigt):\n\n  " + rk + "\n")
        elif cmd == "unlock":
            unlock(getpass.getpass("Master-Passwort: "))
            print("Tresor entsperrt")
        elif cmd == "lock":
            lock()
            print("Tresor gesperrt")
        elif cmd == "status":
            print(json.dumps(status(), ensure_ascii=False))
        elif cmd == "add" and len(a) > 1:
            value = sys.stdin.read().strip() if not sys.stdin.isatty() else getpass.getpass("Wert: ")
            add_entry(a[1], value, _flag(a, "-d", "--description"),
                      _flag(a, "-u", "--username"), _flag(a, "--url"))
            print(f"Eintrag '{a[1]}' gespeichert — im Chat nutzbar als {{{{tresor:{a[1]}}}}}")
            _audit("vault.entry.save", a[1])
        elif cmd == "list":
            for e in list_entries():
                extra = f" ({e['username']})" if e["username"] else ""
                print(f"{e['name']}{extra} — {e['description']}")
        elif cmd == "remove" and len(a) > 1:
            remove_entry(a[1])
            print("Gelöscht")
            _audit("vault.entry.delete", a[1])
        elif cmd == "rotate-master":
            rotate_master(getpass.getpass("Altes Master-Passwort: "),
                          getpass.getpass("Neues Master-Passwort: "))
            print("Master-Passwort geändert")
        elif cmd == "recover":
            rk = recover(input("Wiederherstellungsschlüssel: "),
                         getpass.getpass("Neues Master-Passwort: "))
            print("Neues Master-Passwort gesetzt. NEUER Wiederherstellungsschlüssel "
                  "(der alte ist ab jetzt ungültig):\n\n  " + rk + "\n")
        elif cmd == "run":
            rest = a[1:]
            timeout = 120
            if rest and rest[0] == "--timeout":
                timeout = int(rest[1])
                rest = rest[2:]
            if rest and rest[0] == "--":
                rest = rest[1:]
            if not rest:
                print("Nutzung: vault.py run [--timeout N] -- <kommando…>", file=sys.stderr)
                return 1
            return run(rest, timeout)
        elif cmd == "redact":
            redact_stream()
        else:
            print(__doc__, file=sys.stderr)
            return 1
    except (ValueError, KeyError, RuntimeError, PermissionError) as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
