#!/usr/bin/env python3
"""Operator Passwort-Tresor — optionales Vaultwarden/Bitwarden-Backend (bw-CLI).

Alternative zum lokalen ``vault.enc``: löst ``{{tresor:name}}`` über eine selbst
gehostete Vaultwarden-Instanz (Bitwarden-kompatibel) auf. Aktiv, sobald
``dashboard.json`` › ``vault_backend`` auf ``"vaultwarden"`` steht — sonst bleibt
der lokale Tresor die Quelle (Standard, für Produkt-Nutzer ohne Vaultwarden).

Sitzungs-Semantik wie beim lokalen Tresor: ``bw unlock`` liefert ein
``BW_SESSION``-Token; das wird — genau wie der lokale DEK — als flüchtige
0600-Datei im nutzer-privaten Temp-Verzeichnis abgelegt (verschwindet beim
Reboot). Das Master-Passwort wird NIE persistiert; es wird nur transient per
Umgebungsvariable an ``bw`` gereicht (nicht via argv → nicht in ``ps`` sichtbar).

Nur stdlib — läuft im Listener-Kontext (kein venv nötig). ``bw`` ist eine externe
Node-CLI (``brew install bitwarden-cli`` oder ``npm i -g @bitwarden/cli``).
"""
import json
import os
import shutil
import subprocess
import time

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
CONN_FILE = os.path.join(BOT_DIR, "connections", "vaultwarden.json")
# Eigenes bw-Datenverzeichnis, isoliert von einer evtl. privaten bw-Nutzung des Users.
APPDATA_DIR = os.path.join(BOT_DIR, "secrets", "bw-data")

LOCKED_MSG = ("Vaultwarden-Tresor ist gesperrt. Michi kann ihn im Dashboard "
              "(http://127.0.0.1:8737, Tab „Tresor“) entsperren.")

# Testhaken: für Tests ohne bw-CLI/Server überschreibbar.
#   _bw(args, session=None, stdin=None, pw=None, timeout=60) -> (rc, stdout, stderr)
SESSION_OVERRIDE = None  # None = Datei nutzen; "" = gesperrt; "<tok>" = fester Token (Tests)


# ---------------------------------------------------------------- bw-Aufruf --
def _bw(args, session=None, stdin=None, pw=None, timeout=60):
    """Ein ``bw``-Kommando ausführen. Master-Passwort NUR per Umgebungsvariable
    (BW_MASTERPW), nie in argv. Rückgabe: (returncode, stdout, stderr)."""
    exe = shutil.which("bw")
    if not exe:
        raise RuntimeError("bw-CLI nicht gefunden — installieren mit "
                           "`brew install bitwarden-cli` oder `npm install -g @bitwarden/cli`.")
    env = dict(os.environ)
    env["BITWARDENCLI_APPDATA_DIR"] = APPDATA_DIR
    env["BW_NOINTERACTION"] = "true"
    if session:
        env["BW_SESSION"] = session
    if pw is not None:
        env["BW_MASTERPW"] = pw
    os.makedirs(APPDATA_DIR, mode=0o700, exist_ok=True)
    r = subprocess.run([exe] + args, env=env, capture_output=True, text=True,
                       input=stdin, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def bw_installed() -> bool:
    return shutil.which("bw") is not None


# ---------------------------------------------------------------- Session-Datei --
def _session_path() -> str:
    try:
        base = os.confstr("CS_DARWIN_USER_TEMP_DIR")
    except (ValueError, OSError):
        base = None
    if base and os.path.isdir(base):
        return os.path.join(base, "operator-vaultwarden.session")
    return f"/private/tmp/operator-vaultwarden-{os.getuid()}.session"


def _store_session(tok: str) -> None:
    p = _session_path()
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok)


def _autolock_minutes() -> int:
    try:
        return int(json.load(open(os.path.join(BOT_DIR, "dashboard.json"))).get(
            "vault_autolock_minutes", 0))
    except Exception:
        return 0


def session() -> str | None:
    """Aktuelles BW_SESSION-Token (oder None, wenn gesperrt/abgelaufen)."""
    if SESSION_OVERRIDE is not None:
        return SESSION_OVERRIDE or None
    p = _session_path()
    try:
        st = os.stat(p)
        if st.st_uid != os.getuid():
            return None
        mins = _autolock_minutes()
        if mins > 0 and time.time() - st.st_mtime > mins * 60:
            os.remove(p)
            return None
        return open(p).read().strip() or None
    except OSError:
        return None


def _drop_session() -> None:
    try:
        os.remove(_session_path())
    except OSError:
        pass


def _touch_session() -> None:
    """Idle-Reset für Auto-Lock (analog utime des lokalen DEK)."""
    try:
        os.utime(_session_path())
    except OSError:
        pass


# ---------------------------------------------------------------- Konfiguration --
def config() -> dict:
    try:
        return json.load(open(CONN_FILE))
    except (OSError, ValueError):
        return {}


def set_server(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("Die Adresse muss mit http:// oder https:// beginnen")
    rc, _out, errtxt = _bw(["config", "server", url])
    if rc != 0:
        raise RuntimeError("Server-Adresse konnte nicht gesetzt werden "
                           f"(bw: {errtxt.strip()[:160] or 'unbekannter Fehler'}). "
                           "Ist evtl. noch eine andere Anmeldung aktiv? Dann zuerst trennen.")
    os.makedirs(os.path.dirname(CONN_FILE), exist_ok=True)
    fd = os.open(CONN_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"url": url}, f)
    os.replace(CONN_FILE + ".tmp", CONN_FILE)
    return url


def _auth_status(session_tok: str | None = None) -> str:
    """bw-Status: 'unauthenticated' | 'locked' | 'unlocked' | '' (unbekannt)."""
    rc, out, _err = _bw(["status"], session=session_tok)
    if rc != 0:
        return ""
    try:
        return json.loads(out).get("status", "")
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------- Entsperren/Sperren --
def unlock(master_pw: str, email: str = "") -> str:
    """Vaultwarden entsperren. Erste Anmeldung braucht die E-Mail (Login), danach
    genügt das Master-Passwort (Unlock). Speichert nur das Session-Token."""
    if not master_pw:
        raise ValueError("Master-Passwort fehlt")
    if not config().get("url"):
        raise RuntimeError("Kein Vaultwarden-Server konfiguriert — zuerst die Serveradresse eintragen.")
    st = _auth_status()
    if st in ("unauthenticated", ""):
        if not (email or "").strip():
            raise ValueError("Für die erste Anmeldung fehlt die E-Mail-Adresse")
        rc, out, errtxt = _bw(["login", email.strip(), "--passwordenv", "BW_MASTERPW", "--raw"],
                              pw=master_pw)
        action = "login"
    else:  # locked oder unlocked → frisches Session-Token holen
        rc, out, errtxt = _bw(["unlock", "--passwordenv", "BW_MASTERPW", "--raw"], pw=master_pw)
        action = "unlock"
    if rc != 0:
        msg = (errtxt.strip() or out.strip() or "unbekannter Fehler")
        low = msg.lower()
        if "two-step" in low or "two step" in low or "2fa" in low:
            raise ValueError("Vaultwarden verlangt Zwei-Faktor für die CLI. Für den Operator "
                             "die CLI-Zwei-Faktor deaktivieren oder einen API-Key verwenden.")
        if "master password" in low or "invalid" in low or "incorrect" in low:
            raise ValueError("Master-Passwort oder E-Mail stimmt nicht.")
        raise ValueError(f"Vaultwarden-Anmeldung fehlgeschlagen: {msg[:200]}")
    tok = out.strip()
    if not tok:
        raise RuntimeError("bw hat kein Session-Token geliefert")
    _store_session(tok)
    return action


def lock() -> None:
    _drop_session()
    try:
        _bw(["lock"])
    except Exception:
        pass


def disconnect() -> None:
    """Abmelden und lokale bw-Daten entfernen (Server-Anbindung trennen)."""
    _drop_session()
    try:
        _bw(["logout"])
    except Exception:
        pass
    try:
        os.remove(CONN_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------- Auflösen/Auflisten --
def get_password(name: str, session_tok: str) -> str | None:
    """Passwort eines Eintrags holen (None = nicht gefunden/mehrdeutig/gesperrt)."""
    if not session_tok:
        return None
    try:
        rc, out, _err = _bw(["get", "password", name], session=session_tok)
    except (RuntimeError, subprocess.SubprocessError):
        return None
    if rc != 0:
        return None
    val = out.strip()
    return val or None


def list_items(session_tok: str | None = None) -> list:
    """Login-Einträge als Metadaten (Name/Benutzer/URL) — read-only, ohne Passwörter."""
    session_tok = session_tok or session()
    if not session_tok:
        raise PermissionError(LOCKED_MSG)
    rc, out, errtxt = _bw(["list", "items"], session=session_tok)
    if rc != 0:
        raise PermissionError(LOCKED_MSG)
    try:
        items = json.loads(out)
    except (ValueError, TypeError):
        return []
    res = []
    for it in items:
        if it.get("type") != 1:  # 1 = Login
            continue
        login = it.get("login") or {}
        uris = login.get("uris") or []
        res.append({"name": it.get("name", ""),
                    "username": login.get("username", "") or "",
                    "url": (uris[0].get("uri", "") if uris else "")})
    return sorted(res, key=lambda x: x["name"].lower())


# ---------------------------------------------------------------- Status --
def status() -> dict:
    cfg = config()
    installed = bw_installed()
    configured = bool(cfg.get("url")) and installed
    sess = session()
    unlocked = False
    count = None
    if sess and installed:
        try:
            if _auth_status(sess) == "unlocked":
                unlocked = True
                try:
                    count = len(list_items(sess))
                except Exception:
                    count = None
        except Exception:
            unlocked = False
    return {"backend": "vaultwarden", "bw_installed": installed,
            "configured": configured, "url": cfg.get("url", ""),
            "unlocked": unlocked, "items": count,
            "autolock_minutes": _autolock_minutes()}


# ---------------------------------------------------------------- CLI --
def main() -> int:
    import getpass
    import sys
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    try:
        if cmd == "server" and len(a) > 1:
            print("Server gesetzt:", set_server(a[1]))
        elif cmd == "unlock":
            email = a[1] if len(a) > 1 else ""
            act = unlock(getpass.getpass("Vaultwarden Master-Passwort: "), email)
            print("Entsperrt" if act == "unlock" else "Angemeldet und entsperrt")
        elif cmd == "lock":
            lock()
            print("Gesperrt")
        elif cmd == "disconnect":
            disconnect()
            print("Getrennt")
        elif cmd == "status":
            print(json.dumps(status(), ensure_ascii=False))
        elif cmd == "list":
            for e in list_items():
                extra = f" ({e['username']})" if e["username"] else ""
                print(f"{e['name']}{extra}")
        elif cmd == "get" and len(a) > 1:
            sess = session()
            if not sess:
                print(LOCKED_MSG, file=sys.stderr)
                return 2
            val = get_password(a[1], sess)
            if val is None:
                print("Nicht gefunden", file=sys.stderr)
                return 3
            print(val)
        else:
            print(__doc__, file=sys.stderr)
            return 1
    except (ValueError, KeyError, RuntimeError, PermissionError) as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
