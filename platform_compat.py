#!/usr/bin/env python3
"""Operator — Plattform-Abstraktion (stdlib-only).

Kapselt alle OS-Unterschiede zwischen macOS, Linux und Windows an EINER Stelle, damit
Listener & Helfer stdlib-only bleiben (kein Pip-Zwang). Auf macOS liefert das Modul
BITIDENTISCHE Werte zum bisherigen Verhalten — der laufende Mac-Betrieb ändert sich nicht.

Enthält:
- Plattform-Flags IS_MAC / IS_WIN / IS_LINUX
- runtime_dir()/runtime_file() — nutzer-privates Temp-/Laufzeit-Verzeichnis je OS
- user_tag()/owns() — Ersatz für os.getuid()-Nutzung
- venv_python() — venv-Interpreterpfad je OS (bin/python3 vs Scripts\\python.exe)
- open_url() — Browser öffnen (webbrowser statt macOS `open`)
- secure_chmod()/write_private() — 0600 (POSIX) bzw. best-effort-ACL (Windows)
- ipc_bind()/ipc_connect() — Daemon-IPC: AF_UNIX (POSIX) bzw. TCP-Loopback+Token (Windows)
"""
import getpass
import json
import os
import socket
import sys
import tempfile

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

_PSEUDO_SOCK = "operator-pseudonym.sock"
_PSEUDO_RENDEZVOUS = "operator-pseudonym.ipc"


# ---------------------------------------------------------------- Nutzer/Owner --
def user_tag() -> str:
    """Kurzer, nutzer-eindeutiger Tag für Dateinamen — ersetzt os.getuid()."""
    if hasattr(os, "getuid"):
        return str(os.getuid())
    return (os.environ.get("USERNAME") or getpass.getuser() or "user").strip() or "user"


def owns(st) -> bool:
    """Gehört die Datei (os.stat-Ergebnis) dem aktuellen Nutzer? Auf Windows nicht per
    st_uid prüfbar → True (Vertraulichkeit dort über nutzer-privates Temp + ACL)."""
    if hasattr(os, "getuid"):
        return st.st_uid == os.getuid()
    return True


# ---------------------------------------------------------------- Laufzeit-/Temp-Pfade --
def runtime_dir() -> str:
    """Nutzer-privates Laufzeit-/Temp-Basisverzeichnis.
    macOS: CS_DARWIN_USER_TEMP_DIR (wie bisher) → bitidentisch.
    Linux: $XDG_RUNTIME_DIR (nutzer-privat) sonst tempfile.gettempdir().
    Windows: tempfile.gettempdir() (=%LOCALAPPDATA%\\Temp, nutzer-privat)."""
    if IS_MAC:
        try:
            base = os.confstr("CS_DARWIN_USER_TEMP_DIR")
        except (ValueError, OSError, AttributeError):
            base = None
        if base and os.path.isdir(base):
            return base
        return tempfile.gettempdir()
    if IS_LINUX:
        xdg = os.environ.get("XDG_RUNTIME_DIR")
        if xdg and os.path.isdir(xdg):
            return xdg
        return tempfile.gettempdir()
    return tempfile.gettempdir()


def _base_is_per_user() -> bool:
    if IS_MAC or IS_WIN:
        return True
    return bool(os.environ.get("XDG_RUNTIME_DIR") and os.path.isdir(os.environ["XDG_RUNTIME_DIR"]))


def runtime_file(basename: str) -> str:
    """Voller Pfad einer Laufzeitdatei im nutzer-privaten Temp. In geteilten Verzeichnissen
    (Linux /tmp-Fallback) wird der Nutzer-Tag angehängt, damit sich Nutzer nicht kollidieren."""
    base = runtime_dir()
    if _base_is_per_user():
        return os.path.join(base, basename)
    return os.path.join(base, f"{basename}.{user_tag()}")


# ---------------------------------------------------------------- venv / Interpreter --
def venv_python(botdir: str) -> str:
    """Pfad zum venv-Interpreter je OS."""
    if IS_WIN:
        return os.path.join(botdir, "dashboard", "venv", "Scripts", "python.exe")
    return os.path.join(botdir, "dashboard", "venv", "bin", "python3")


# ---------------------------------------------------------------- Browser --
def ensure_std_streams(logdatei: str = "") -> None:
    """Fehlende Ausgabekanäle ersetzen — Pflicht unter pythonw.exe (Windows).

    Realer Ausfall (Michi, 30.07.): Damit kein Konsolenfenster mehr aufgeht, starten
    die Dienste als pythonw.exe. Dort sind sys.stdout/-err aber **None** — der erste
    print() bzw. uvicorns Log-Handler stirbt mit AttributeError, und der Dienst ist
    sofort tot (Dashboard: ERR_CONNECTION_REFUSED). Deshalb: Kanäle auf die Log-Datei
    umbiegen (nicht auf devnull — Ausgaben sollen auffindbar bleiben).

    Ohne Wirkung, wenn Kanäle vorhanden sind; scheitert nie (Dienst-Start hat Vorrang)."""
    import sys
    if sys.stdout is not None and sys.stderr is not None:
        return
    ziel = None
    if logdatei:
        try:
            ziel = open(logdatei, "a", encoding="utf-8", buffering=1)
        except OSError:
            ziel = None
    if ziel is None:
        try:
            ziel = open(os.devnull, "w")
        except OSError:
            return
    if sys.stdout is None:
        sys.stdout = ziel
    if sys.stderr is None:
        sys.stderr = ziel


def claude_bin() -> str:
    """Pfad zum Claude CLI — Windows-sicher aufgelöst.

    npm legt drei Dateien nebeneinander: »claude« (Shell-Skript), »claude.cmd«
    und »claude.ps1«. shutil.which("claude") kann auf Windows eine NICHT startbare
    Variante treffen — dann stirbt jeder Modell-Aufruf mit »WinError 193: %1 ist
    keine zulässige Win32-Anwendung« (Michis Windows, 30.07., live im Log).
    Deshalb: startbare Endungen zuerst, der Rest wie gehabt."""
    import shutil as _sh
    if os.name == "nt":
        for name in ("claude.cmd", "claude.exe", "claude.bat"):
            p = _sh.which(name)
            if p:
                return p
    return _sh.which("claude") or "claude"


def open_url(url: str) -> bool:
    """URL im Standardbrowser öffnen — plattformübergreifend (macOS/Linux/Windows).

    Gibt True zurück, wenn ein Browser gestartet werden konnte. Auf einem Linux-Rechner
    ohne Bildschirm (z. B. per SSH auf einem Raspberry Pi) gibt es keinen — dann False,
    damit der Aufrufer etwas Sinnvolles sagen kann statt stumm nichts zu tun.
    """
    import webbrowser
    if not IS_WIN and not IS_MAC and not (os.environ.get("DISPLAY")
                                          or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


# ---------------------------------------------------------------- Dateirechte --
def secure_chmod(path: str, mode: int = 0o600) -> None:
    """Vertrauliche Rechte setzen. POSIX: chmod. Windows: best-effort-ACL via icacls
    (Vererbung aus, nur aktueller Nutzer Vollzugriff)."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    if IS_WIN:
        import subprocess
        user = os.environ.get("USERNAME") or getpass.getuser()
        try:
            subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", f"{user}:F"],
                           capture_output=True, timeout=10)
        except Exception:
            pass


def write_private(path: str, data: str) -> None:
    """Datei mit 0600 (bzw. Windows-ACL) atomar-nah schreiben."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)
    secure_chmod(path)


# ---------------------------------------------------------------- Daemon-IPC --
def _rendezvous_path() -> str:
    return runtime_file(_PSEUDO_RENDEZVOUS)


def ipc_bind():
    """Server-seitigen IPC-Endpunkt binden (Pseudonym-Daemon).
    Rückgabe: (listen_socket, token_or_None). POSIX = AF_UNIX (0600), token None.
    Windows = TCP 127.0.0.1 (ephemerer Port) + Zufallstoken in 0600-Rendezvous-Datei;
    der Token verhindert, dass fremde lokale Prozesse PII-Mappings abgreifen."""
    if IS_WIN:
        import secrets as _sec
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        port = srv.getsockname()[1]
        token = _sec.token_hex(16)
        write_private(_rendezvous_path(), json.dumps({"port": port, "token": token}))
        return srv, token
    path = runtime_file(_PSEUDO_SOCK)
    try:
        os.unlink(path)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    srv.listen(8)
    return srv, None


def ipc_cleanup() -> None:
    """Rendezvous/Socket-Datei entfernen (Aufräumen bei Uninstall/Shutdown)."""
    for p in (runtime_file(_PSEUDO_SOCK), _rendezvous_path()):
        try:
            os.unlink(p)
        except OSError:
            pass


def ipc_connect(timeout: float = 2.0):
    """Client-seitig zum Daemon verbinden.
    Rückgabe: (connected_socket, token_or_None). Wirft bei nicht erreichbarem Daemon
    (Aufrufer fällt dann auf den Einzel-Subprozess zurück). Der Token gehört in das
    JSON-Request-Feld 'token' (der Daemon prüft ihn auf Windows)."""
    if IS_WIN:
        info = json.loads(open(_rendezvous_path()).read())
        sock = socket.create_connection(("127.0.0.1", int(info["port"])), timeout=timeout)
        return sock, info.get("token")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(runtime_file(_PSEUDO_SOCK))
    return sock, None


# ---------------------------------------------------------------- Arbeitsordner (#106) --
# Der Arbeitsordner lag bis 1.11.0 unter ~/.claude/matrix-bot/workspace. Claude Code
# schützt ALLES unter ~/.claude/ als sensibel — deshalb konnten Agenten dort per
# Shell-Befehl keine Dateien anlegen (über das Datei-Werkzeug schon, was die Sache
# still und verwirrend machte). Die Einstellung lässt sich nicht überschreiben,
# also zieht der Arbeitsordner heraus. Nebeneffekt: Er ist jetzt auch für den
# Nutzer auffindbar, statt in einem versteckten Ordner zu liegen.
WORKSPACE_ALT = os.path.expanduser("~/.claude/matrix-bot/workspace")
WORKSPACE_NEU = os.path.expanduser("~/Operator")


def workspace():
    """Der Arbeitsordner. Bestehende Installationen ziehen einmalig um."""
    if os.environ.get("OPERATOR_WORKSPACE"):
        return os.environ["OPERATOR_WORKSPACE"]
    return WORKSPACE_NEU


def workspace_migrieren(log=lambda *_: None):
    """Einmaliger, idempotenter Umzug alt → neu. Gibt True zurück, wenn umgezogen
    wurde. Konservativ: Gibt es den neuen Ordner schon mit Inhalt, wird NICHTS
    überschrieben — dann bleibt der alte liegen und wird nur gemeldet."""
    alt, neu = WORKSPACE_ALT, workspace()
    if alt == neu or not os.path.isdir(alt):
        return False
    try:
        if os.path.isdir(neu) and os.listdir(neu):
            log(f"Arbeitsordner: {neu} existiert bereits — alter Ordner bleibt unter {alt}")
            return False
        import shutil
        os.makedirs(os.path.dirname(neu), exist_ok=True)
        shutil.move(alt, neu)
        log(f"Arbeitsordner umgezogen: {alt} → {neu} "
            "(dort darf dein Assistent jetzt auch per Befehl Dateien anlegen)")
        return True
    except OSError as e:
        log(f"Arbeitsordner konnte nicht umziehen ({e}) — alles läuft weiter wie bisher")
        return False
