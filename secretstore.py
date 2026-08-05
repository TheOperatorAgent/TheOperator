#!/usr/bin/env python3
"""Operator — plattformübergreifender Secret-Store (stdlib-only).

Vereinheitlicht das Ablegen/Lesen kleiner Geheimnisse (Matrix-Tokens, Dashboard-Token,
Token-Verschlüsselungs-Master-Key) über Subprozess-Backends je OS — damit die stdlib-only-
Module (Listener, send, migrate_tokens) keine Pip-Lib brauchen:

- macOS   : Schlüsselbund via `security` (Verhalten bitidentisch zum bisherigen Code)
- Windows : DPAPI (PowerShell ConvertTo/From-SecureString, user-gebunden) → secrets/<acct>.dpapi
- Linux   : `secret-tool` (libsecret/Secret-Service) falls vorhanden
- Fallback : 0600-Datei secrets/<acct>.secret (wenn kein OS-Store erreichbar, z. B. headless)

Service-Name überall: "the-operator". Werte sind kurze Strings (Tokens/Hex-Keys).
"""
import os
import shutil
import subprocess

import platform_compat as _plat

SERVICE = "the-operator"
SECRETS_DIR = os.path.join(os.path.expanduser("~/.claude/matrix-bot"), "secrets")


def _file_path(account: str) -> str:
    return os.path.join(SECRETS_DIR, account + ".secret")


def _file_get(account: str):
    try:
        return open(_file_path(account)).read().strip() or None
    except OSError:
        return None


def _file_set(account: str, value: str) -> None:
    os.makedirs(SECRETS_DIR, mode=0o700, exist_ok=True)
    _plat.write_private(_file_path(account), value)


def _file_delete(account: str) -> None:
    try:
        os.remove(_file_path(account))
    except OSError:
        pass


# ---------------------------------------------------------------- macOS (security) --
def _mac_get(account: str):
    r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
                       capture_output=True, text=True, **_plat.OHNE_FENSTER)
    return r.stdout.strip() if r.returncode == 0 else None


def _mac_set(account: str, value: str) -> None:
    subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", account,
                    "-w", value], check=True, capture_output=True, **_plat.OHNE_FENSTER)


def _mac_delete(account: str) -> None:
    subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", account],
                   capture_output=True, **_plat.OHNE_FENSTER)


# ---------------------------------------------------------------- Linux (secret-tool) --
def _has_secret_tool() -> bool:
    return shutil.which("secret-tool") is not None


def _linux_get(account: str):
    r = subprocess.run(["secret-tool", "lookup", "service", SERVICE, "account", account],
                       capture_output=True, text=True, **_plat.OHNE_FENSTER)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _linux_set(account: str, value: str) -> None:
    subprocess.run(["secret-tool", "store", "--label=Operator " + account,
                    "service", SERVICE, "account", account],
                   input=value, capture_output=True, text=True, check=True, **_plat.OHNE_FENSTER)


def _linux_delete(account: str) -> None:
    subprocess.run(["secret-tool", "clear", "service", SERVICE, "account", account],
                   capture_output=True, **_plat.OHNE_FENSTER)


# ---------------------------------------------------------------- Windows (DPAPI) --
def _dpapi_path(account: str) -> str:
    return os.path.join(SECRETS_DIR, account + ".dpapi")


def _ps(script: str, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, env=env, timeout=30, **_plat.OHNE_FENSTER)


def _win_get(account: str):
    p = _dpapi_path(account)
    if not os.path.exists(p):
        return None
    # DPAPI-Blob entschlüsseln (user-gebunden) und Klartext ausgeben
    script = (
        "$b = Get-Content -Raw -LiteralPath $env:OP_BLOB_PATH; "
        "$s = ConvertTo-SecureString $b; "
        "$p = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); "
        "[Runtime.InteropServices.Marshal]::PtrToStringAuto($p)")
    r = _ps(script, {"OP_BLOB_PATH": p})
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _win_set(account: str, value: str) -> None:
    os.makedirs(SECRETS_DIR, mode=0o700, exist_ok=True)
    # Wert per Umgebungsvariable (nicht argv) an PowerShell; DPAPI-verschlüsselt in Datei
    script = ("$s = ConvertTo-SecureString -String $env:OP_VALUE -AsPlainText -Force; "
              "ConvertFrom-SecureString -SecureString $s | "
              "Set-Content -NoNewline -LiteralPath $env:OP_BLOB_PATH")
    r = _ps(script, {"OP_VALUE": value, "OP_BLOB_PATH": _dpapi_path(account)})
    if r.returncode != 0:
        raise RuntimeError("DPAPI-Speichern fehlgeschlagen: " + (r.stderr.strip()[:200]))
    _plat.secure_chmod(_dpapi_path(account))


def _win_delete(account: str) -> None:
    try:
        os.remove(_dpapi_path(account))
    except OSError:
        pass


# ---------------------------------------------------------------- Öffentliche API --
def get(account: str):
    """Geheimnis lesen (oder None). Reihenfolge: OS-Store → Datei-Fallback."""
    try:
        if _plat.IS_MAC:
            v = _mac_get(account)
        elif _plat.IS_WIN:
            v = _win_get(account)
        elif _plat.IS_LINUX and _has_secret_tool():
            v = _linux_get(account)
        else:
            v = None
    except Exception:
        v = None
    if v is not None:
        return v
    return _file_get(account)


def set(account: str, value: str) -> None:
    """Geheimnis ablegen. Nutzt den OS-Store; fällt bei Fehler auf 0600-Datei zurück."""
    try:
        if _plat.IS_MAC:
            return _mac_set(account, value)
        if _plat.IS_WIN:
            return _win_set(account, value)
        if _plat.IS_LINUX and _has_secret_tool():
            return _linux_set(account, value)
    except Exception:
        pass
    _file_set(account, value)


def delete(account: str) -> None:
    """Geheimnis überall entfernen (OS-Store UND Datei-Fallback)."""
    try:
        if _plat.IS_MAC:
            _mac_delete(account)
        elif _plat.IS_WIN:
            _win_delete(account)
        elif _plat.IS_LINUX and _has_secret_tool():
            _linux_delete(account)
    except Exception:
        pass
    _file_delete(account)


def get_or(account: str, fallback):
    """Wie get(), aber liefert `fallback`, wenn nichts gespeichert ist (Kompatibilität
    zum bisherigen keychain_token(account, fallback))."""
    v = get(account)
    return v if v is not None else fallback


def available_backend() -> str:
    """Für Diagnose/Status: welcher Store aktiv ist."""
    if _plat.IS_MAC:
        return "macos-keychain"
    if _plat.IS_WIN:
        return "windows-dpapi"
    if _plat.IS_LINUX and _has_secret_tool():
        return "linux-secret-service"
    return "file-0600"
