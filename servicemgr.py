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
            # Gefragt ist, ob ein PROZESS läuft — nicht, ob eine Aufgabe eingetragen ist.
            #
            # Bis 1.45.0 stand hier: »irgendein Feld, das eine Zahl ungleich null ist,
            # heißt läuft«. Die Begründung war eine Spalte »Prozess-ID« — **die es bei
            # `schtasks` gar nicht gibt**. Was es gibt, ist das letzte Ergebnis, und das
            # ist im Fehlerfall eine große Zahl. Damit galt: **je schlimmer der Dienst
            # gescheitert war, desto sicherer meldete er »ok«.**
            #
            # Bewiesen am 04.08.2026 auf Michis Rechner: `LastTaskResult 2147946720`
            # (0x800710E0, Start abgelehnt) — und `operator status` sagte dreimal [ok],
            # während seit fünf Tagen keine Logzeile geschrieben wurde.
            #
            # Deshalb jetzt die einzige Frage, die zählt: Läuft ein Python-Prozess mit
            # unserem Skript? `tasklist` ist sprachunabhängig, und der Skriptname steht
            # in der Befehlszeile.
            skript = {"listener": "listener.py", "dashboard": "server.py",
                      "pseudonym": "pseudonym_daemon.py"}.get(logical, "")
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name like 'py%'\" "
                 "| Select-Object -ExpandProperty CommandLine"],
                capture_output=True, text=True)
            if skript and skript.lower() in (r.stdout or "").lower():
                return True
            # Kein Prozess gefunden → die Aufgabe mag eingetragen sein, sie LÄUFT aber
            # nicht. Ein »ok« wäre hier genau die Falschauskunft, die fünf Tage gekostet
            # hat: Der Nutzer sieht grün und wartet auf Antworten, die nie kommen.
            return False
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
