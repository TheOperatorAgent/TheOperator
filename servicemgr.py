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
            # NICHT auf das Wort "Running" prüfen: schtasks ist lokalisiert und meldet
            # auf einem deutschen Windows "Wird ausgeführt" (Michi, 30.07. — dadurch
            # stand im Dashboard "läuft nicht", obwohl der Dienst nachweislich lief).
            # /fo csv liefert eine stabile Spalte, aber ebenfalls lokalisierte Werte;
            # eindeutig sprachfrei ist nur: läuft = hat eine Prozess-ID != 0.
            r = subprocess.run(["schtasks", "/query", "/tn", _WIN[logical], "/v",
                                "/fo", "csv", "/nh"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                return False                     # Aufgabe existiert nicht
            import csv as _csv
            import io as _io
            for reihe in _csv.reader(_io.StringIO(r.stdout)):
                for feld in reihe:
                    f = feld.strip()
                    if f.isdigit() and f != "0":     # Spalte "Prozess-ID"
                        return True
            # Fallback: bekannte Zustandswörter mehrerer Sprachen
            low = r.stdout.lower()
            return any(w in low for w in ("running", "wird ausgeführt", "wird ausgefuehrt",
                                          "en cours", "in esecuzione"))
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
