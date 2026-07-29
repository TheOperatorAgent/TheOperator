#!/usr/bin/env python3
"""Satellit-Fenster für den Operator-Chat (#90): eigenes Fenster auf OS-Ebene.

Kein Electron, kein neues Framework: Wir nutzen den App-Modus des bereits
vorhandenen Browsers (Chrome/Chromium/Edge, `--app=`) — das ergibt ein eigenes,
rahmenloses Fenster mit eigenem Symbol im Dock bzw. in der Taskleiste. Gibt es
keinen solchen Browser, öffnet der Standardbrowser einen normalen Tab (ehrlich
gemeldet, kein stilles Scheitern).

Aufruf:
    python3 dock_fenster.py                  Fenster öffnen
    python3 dock_fenster.py autostart-an     beim Anmelden automatisch öffnen
    python3 dock_fenster.py autostart-aus    Autostart entfernen
    python3 dock_fenster.py autostart-status "an" | "aus"

Sicherheit: Die Adresse zeigt auf 127.0.0.1 — nichts wird nach außen geöffnet.
Der Zugangs-Token wandert einmalig als #-Fragment in den Startlink (wie
dashboard/open.py); Fragmente verlassen den Browser nicht und landen in keinem
Server-Log. stdlib-only.
"""
import json
import os
import shutil
import subprocess
import sys

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)
import platform_compat  # noqa: E402
import secretstore      # noqa: E402


def _url():
    cfg = json.load(open(os.path.join(BOT_DIR, "dashboard.json")))
    port = cfg.get("port", 8737)
    token = secretstore.get("dashboard-token") or ""
    frag = f"#t={token}" if token else ""
    return f"http://127.0.0.1:{port}/dock{frag}"


def _app_browser():
    """Ein Browser, der den App-Modus (--app=) kann — je OS an den üblichen Orten."""
    kandidaten = []
    if platform_compat.IS_MAC:
        kandidaten = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
    elif platform_compat.IS_WIN:
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        kandidaten = [
            os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
    else:
        for name in ("chromium", "chromium-browser", "google-chrome",
                     "google-chrome-stable", "brave-browser", "microsoft-edge"):
            p = shutil.which(name)
            if p:
                kandidaten.append(p)
    for k in kandidaten:
        if os.path.exists(k):
            return k
    return None


def oeffnen():
    url = _url()
    browser = _app_browser()
    if browser:
        subprocess.Popen([browser, f"--app={url}", "--window-size=430,760"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        print("Satellit gestartet (eigenes Fenster).")
        return True
    if platform_compat.open_url(url):
        print("Kein App-fähiger Browser gefunden — Chat im normalen Browser geöffnet.")
        print("👉 Für ein eigenes Fenster: Chrome oder Chromium installieren.")
        return True
    print("Hier ist kein Browser verfügbar (kein Bildschirm — vermutlich per SSH).")
    print("👉 Am Rechner mit Bildschirm »operator chat« ausführen.")
    return False


# ---------------------------------------------------------------- Autostart --
# Bewusst „einmal beim Anmelden öffnen", KEIN Dauerdienst mit Neustart-Zwang —
# ein zugeklapptes Fenster soll zu bleiben, bis der Nutzer es wieder will.
def _autostart_pfad():
    if platform_compat.IS_MAC:
        return os.path.expanduser("~/Library/LaunchAgents/com.the-operator.chat.plist")
    if platform_compat.IS_WIN:
        return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                            "Start Menu", "Programs", "Startup", "operator-chat.cmd")
    return os.path.expanduser("~/.config/autostart/operator-chat.desktop")


def autostart_status():
    return os.path.exists(_autostart_pfad())


def autostart_an():
    pfad = _autostart_pfad()
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    py = sys.executable or "python3"
    selbst = os.path.join(BOT_DIR, "dock_fenster.py")
    if platform_compat.IS_MAC:
        inhalt = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
\t<key>Label</key><string>com.the-operator.chat</string>
\t<key>ProgramArguments</key><array>
\t\t<string>{py}</string><string>{selbst}</string>
\t</array>
\t<key>RunAtLoad</key><true/>
</dict></plist>
"""
    elif platform_compat.IS_WIN:
        inhalt = f'@start "" "{py}" "{selbst}"\r\n'
    else:
        inhalt = ("[Desktop Entry]\nType=Application\nName=Operator Chat\n"
                  f"Exec={py} {selbst}\nX-GNOME-Autostart-enabled=true\n")
    with open(pfad, "w") as f:
        f.write(inhalt)
    return True


def autostart_aus():
    try:
        os.remove(_autostart_pfad())
    except FileNotFoundError:
        pass
    return True


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "autostart-an":
        autostart_an()
        print("Der Operator-Chat öffnet sich künftig beim Anmelden.")
    elif arg == "autostart-aus":
        autostart_aus()
        print("Autostart entfernt.")
    elif arg == "autostart-status":
        print("an" if autostart_status() else "aus")
    else:
        sys.exit(0 if oeffnen() else 1)


if __name__ == "__main__":
    main()
