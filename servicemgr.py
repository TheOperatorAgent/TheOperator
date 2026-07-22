#!/usr/bin/env python3
"""Operator — plattformübergreifendes Dienst-Management (stdlib-only).

Kapselt Status/Neustart der drei Hintergrunddienste (listener, dashboard, pseudonym)
je OS. Das Anlegen der Dienste macht der jeweilige Installer (launchd-plist / systemd-
user-unit / Windows-Task); dieses Modul spricht sie nur zur Laufzeit an (Dashboard).

- macOS   : launchctl (gui/<uid>/com.the-operator.<name>)
- Linux   : systemctl --user (operator-<name>.service)
- Windows : schtasks (Operator<Name>)
"""
import os
import subprocess

import platform_compat as _plat

# Logischer Name → OS-spezifische Kennung
_MAC = {"listener": "com.the-operator.listener",
        "dashboard": "com.the-operator.dashboard",
        "pseudonym": "com.the-operator.pseudonym"}
_LINUX = {"listener": "operator-listener",
          "dashboard": "operator-dashboard",
          "pseudonym": "operator-pseudonym"}
_WIN = {"listener": "OperatorListener",
        "dashboard": "OperatorDashboard",
        "pseudonym": "OperatorPseudonym"}


def status(logical: str) -> bool:
    """Läuft der Dienst? (Best-effort, für die Dashboard-Statuskachel.)"""
    try:
        if _plat.IS_MAC:
            r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{_MAC[logical]}"],
                               capture_output=True)
            return r.returncode == 0
        if _plat.IS_LINUX:
            r = subprocess.run(["systemctl", "--user", "is-active", _LINUX[logical]],
                               capture_output=True, text=True)
            return r.stdout.strip() == "active"
        if _plat.IS_WIN:
            r = subprocess.run(["schtasks", "/query", "/tn", _WIN[logical], "/fo", "list"],
                               capture_output=True, text=True)
            return r.returncode == 0 and "Running" in r.stdout
    except Exception:
        return False
    return False


def restart(logical: str) -> bool:
    """Dienst neu starten. Rückgabe: True wenn der Neustart-Befehl abgesetzt werden konnte."""
    try:
        if _plat.IS_MAC:
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{_MAC[logical]}"],
                           capture_output=True, check=True)
            return True
        if _plat.IS_LINUX:
            subprocess.run(["systemctl", "--user", "restart", _LINUX[logical]],
                           capture_output=True, check=True)
            return True
        if _plat.IS_WIN:
            subprocess.run(["schtasks", "/end", "/tn", _WIN[logical]], capture_output=True)
            subprocess.run(["schtasks", "/run", "/tn", _WIN[logical]], capture_output=True, check=True)
            return True
    except Exception:
        return False
    return False


def label(logical: str) -> str:
    """OS-spezifische Dienstkennung (für Logs/Diagnose)."""
    if _plat.IS_MAC:
        return _MAC.get(logical, logical)
    if _plat.IS_LINUX:
        return _LINUX.get(logical, logical)
    if _plat.IS_WIN:
        return _WIN.get(logical, logical)
    return logical
