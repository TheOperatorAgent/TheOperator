#!/usr/bin/env python3
"""Selbst-Update (#64): lokale Version prüfen und per Manifest aktualisieren (stdlib-only).

  updater.py check   → JSON {current, latest, update_available, highlights, date}
  updater.py apply   → Dateien laut manifest.json vom offiziellen Repo holen,
                       Backup (.bak), VERSION schreiben, Dienste neu starten.

Quelle ist ausschließlich das offizielle Repo (REPO_RAW, wie der Installer) — nur die im
Manifest gelisteten Laufzeit-Dateien werden geholt, KEIN willkürlicher Code. Nutzerdaten
(VERHALTEN.md, credentials.json, *.db, secrets/, connections/) werden NIE angefasst.

Der `apply`-Lauf ist so gebaut, dass er sich vom Dashboard **detached** starten lässt:
er aktualisiert erst alle Dateien, startet dann den Listener neu und zuletzt das Dashboard
(dessen Neustart den eigenen Elternprozess beenden kann — der detachte Updater überlebt).
"""
import json
import os
import sys
import urllib.request

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
sys.path.insert(0, BOT_DIR)
RAW_FILE = os.path.join(BOT_DIR, "repo_raw.txt")   # vom Installer geschrieben


def _load_repo_raw():
    """Update-Quelle: 1. Env-Override, 2. vom Installer hinterlegte Quelle
    (repo_raw.txt — so aktualisieren Website-/GitHub-Installationen aus GitHub).

    BEWUSST KEIN eingebauter Notnagel mehr (Security-Review 29.07.): Vorher stand
    hier eine private Heimnetz-Adresse (über unverschlüsseltes HTTP) — fehlte repo_raw.txt, hätte
    sich der Updater Code von dem Gerät geholt, das im jeweiligen Heimnetz zufällig
    auf dieser Adresse antwortet. Ohne bekannte Quelle gibt es KEIN Update; das
    Dashboard erklärt stattdessen den Weg (Installer erneut ausführen)."""
    env = os.environ.get("OPERATOR_REPO_RAW")
    if env:
        return env.rstrip("/")
    try:
        saved = open(RAW_FILE).read().strip()
        if saved.startswith(("http://", "https://")):
            return saved.rstrip("/")
    except OSError:
        pass
    return ""


REPO_RAW = _load_repo_raw()
VERSION_FILE = os.path.join(BOT_DIR, "VERSION")
TIMEOUT = 20


def _parse(v):
    """SemVer-Tupel für korrekten Vergleich (1.10.0 > 1.9.0)."""
    out = []
    for part in str(v or "0").strip().split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out) + (0,) * (3 - len(out))


def local_version():
    try:
        return open(VERSION_FILE).read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _fetch(path, binary=False):
    if not REPO_RAW:
        raise RuntimeError("Update-Quelle unbekannt (repo_raw.txt fehlt) — "
                           "bitte den Installer einmal erneut ausführen.")
    with urllib.request.urlopen(f"{REPO_RAW}/{path}", timeout=TIMEOUT) as r:
        data = r.read()
    return data if binary else data.decode("utf-8")


def remote_info():
    """updates.json vom Repo. None bei Netz-/Parse-Fehler (fail-soft)."""
    try:
        return json.loads(_fetch("updates.json"))
    except Exception:
        return None


def check():
    cur = local_version()
    if not REPO_RAW:
        return {"current": cur, "latest": cur, "update_available": False, "highlights": [],
                "error": "Update-Quelle unbekannt — 👉 Installer einmal erneut ausführen, "
                         "dann weiß ich wieder, woher Updates kommen."}
    info = remote_info()
    if not info:
        return {"current": cur, "latest": cur, "update_available": False,
                "highlights": [], "error": "Update-Server nicht erreichbar"}
    latest = str(info.get("version", cur))
    return {"current": cur, "latest": latest,
            "update_available": _parse(latest) > _parse(cur),
            "highlights": info.get("highlights", []), "date": info.get("date", "")}


def apply(restart=True, log=print):
    """Manifest holen, alle Dateien aktualisieren (Backup), VERSION schreiben, Neustart.
    Rückgabe: (ok, meldung)."""
    try:
        manifest = json.loads(_fetch("manifest.json"))
    except Exception as e:
        return False, f"Manifest nicht ladbar: {e}"
    files = manifest.get("files", [])
    staged = []                       # (zielpfad, neuer_inhalt-bytes)
    for entry in files:
        src, dst = entry.get("src"), entry.get("dst")
        if not src or not dst or ".." in dst or dst.startswith("/"):
            return False, f"Ungültiger Manifest-Eintrag: {entry}"
        try:
            staged.append((os.path.join(BOT_DIR, dst), _fetch(src, binary=True)))
        except Exception as e:
            return False, f"Download fehlgeschlagen ({src}): {e}"   # nichts angefasst
    # Erst nach vollständigem Download schreiben (atomarer als Datei für Datei)
    for path, content in staged:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            try:
                os.replace(path, path + ".bak")
            except OSError:
                pass
        with open(path, "wb") as f:
            f.write(content)
    log(f"{len(staged)} Dateien aktualisiert auf Version {manifest.get('version')}")
    if restart:
        try:
            import servicemgr
            servicemgr.restart("listener")
            log("Listener neu gestartet")
            servicemgr.restart("dashboard")   # zuletzt — beendet ggf. den Elternprozess
            log("Dashboard-Neustart angestoßen")
        except Exception as e:
            log(f"Dienst-Neustart-Hinweis: {e} — ggf. manuell neu starten")
    return True, f"Aktualisiert auf {manifest.get('version')}"


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        print(json.dumps(check(), ensure_ascii=False))
    elif cmd == "apply":
        ok, msg = apply(restart="--no-restart" not in sys.argv)
        print(msg)
        sys.exit(0 if ok else 1)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
