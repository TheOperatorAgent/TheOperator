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
            "highlights": info.get("highlights", []), "date": info.get("date", ""),
            # #128: Manche Änderungen erreicht das Ein-Klick-Update strukturell nicht.
            "installer_noetig": bool(info.get("installer_noetig")),
            "installer_grund": str(info.get("installer_grund", "")),
            "befehl": _installer_befehl()}


def _installer_befehl():
    """#128: Der Befehl, den der Nutzer einfügen soll — HART kodiert auf die offizielle
    Adresse, niemals aus repo_raw.txt abgeleitet.

    Sonst zeigte das Dashboard eine aus einer Datei gelesene Adresse als Befehl zum
    Einfügen in eine Shell — wer die Datei schreiben kann, könnte dem Nutzer damit
    beliebigen Code unterschieben. Genau die Klasse Fund, die schon
    test_updater_hat_keinen_privaten_fallback abdeckt."""
    try:
        import platform_compat
        windows = platform_compat.IS_WIN
    except Exception:
        windows = os.name == "nt"
    return ("irm https://operator.bayern/install.ps1 | iex" if windows
            else "curl -fsSL https://operator.bayern/install.sh | bash")


PUBKEY_FILE = os.path.join(BOT_DIR, "update_pubkey.txt")


def _venv_python():
    for p in (os.path.join(BOT_DIR, "dashboard", "venv", "bin", "python3"),
              os.path.join(BOT_DIR, "dashboard", "venv", "Scripts", "python.exe")):
        if os.path.exists(p):
            return p
    return None


def _signatur_pruefen(manifest_bytes, log):
    """#103: Signatur des Manifests gegen den gepinnten Schlüssel prüfen.
    Rückgabe (ok, grund). Gepinnter Schlüssel + keine/kaputte Signatur → hart ablehnen.
    Noch kein Schlüssel gepinnt (Alt-Installation vor 1.10): einmalig durchlassen —
    dieses Update liefert Schlüssel und Prüfung mit, danach ist die Tür zu (TOFU)."""
    if not os.path.exists(PUBKEY_FILE):
        log("Hinweis: noch kein Update-Schlüssel gepinnt — dieses Update pinnt ihn.")
        return True, ""
    try:
        sig = _fetch("manifest.sig")
    except Exception as e:
        return False, f"Signatur nicht ladbar ({e}) — Update abgelehnt."
    py = _venv_python()
    if not py:
        return False, ("Signaturprüfung braucht die Dashboard-Umgebung (venv fehlt) — "
                       "Update abgelehnt. 👉 Installer erneut ausführen.")
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        mp, sp = os.path.join(td, "m.json"), os.path.join(td, "m.sig")
        with open(mp, "wb") as f:
            f.write(manifest_bytes)
        with open(sp, "w") as f:
            f.write(sig.strip() + "\n")
        r = subprocess.run([py, os.path.join(BOT_DIR, "update_verify.py"),
                            "verify", PUBKEY_FILE, mp, sp],
                           capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return False, "Signatur UNGÜLTIG — Update abgelehnt (Quelle manipuliert?)."
    return True, ""


def apply(restart=True, log=print):
    """Manifest holen, Signatur + Datei-Hashes prüfen (#103), alle Dateien
    aktualisieren (Backup), VERSION schreiben, Neustart. Rückgabe: (ok, meldung)."""
    try:
        manifest_bytes = _fetch("manifest.json", binary=True)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as e:
        return False, f"Manifest nicht ladbar: {e}"
    ok, grund = _signatur_pruefen(manifest_bytes, log)
    if not ok:
        return False, grund
    # #128: Änderungen an den DIENST-DEFINITIONEN (Aufrufzeile, Umgebungsvariablen,
    # Task-Scheduler-Eintrag) schreibt nur der Installer — der Updater tauscht Dateien.
    # Genau dort steckten die kritischen Windows-Fixes 1.18.3 (PYTHONUTF8) und 1.18.5
    # (pythonw): Ein Kunde klickt »Aktualisieren«, bekommt neue Dateien, und sein
    # Problem bleibt — ohne jeden Hinweis. Deshalb: hier hart abbrechen, VOR jedem
    # Download, und den Nutzer auf den Installer schicken.
    #
    # Das Flag steht bewusst im SIGNIERTEN Manifest, nicht (nur) in updates.json:
    # updates.json ist unsigniert; wer sie manipulieren kann, könnte das Flag sonst
    # entfernen und ein wirkungsloses Datei-Update durchdrücken.
    if manifest.get("installer_noetig"):
        grund = str(manifest.get("installer_grund") or
                    "Diese Fassung ändert, wie dein Operator gestartet wird.")
        return False, (f"{grund}\n👉 Dafür reicht das Ein-Klick-Update nicht — bitte einmal "
                       f"diesen Befehl ausführen:\n{_installer_befehl()}")
    signiert = os.path.exists(PUBKEY_FILE)
    # #103: kein Downgrade — eine alte (verwundbare) Version darf nicht als "Update" kommen.
    ziel = str(manifest.get("version", "0"))
    if _parse(ziel) < _parse(local_version()) \
            and os.environ.get("OPERATOR_ALLOW_DOWNGRADE") != "1":
        return False, (f"Angebotene Version {ziel} ist älter als die installierte "
                       f"{local_version()} — abgelehnt (Downgrade-Schutz).")
    files = manifest.get("files", [])
    staged = []                       # (zielpfad, neuer_inhalt-bytes)
    import hashlib
    for entry in files:
        src, dst = entry.get("src"), entry.get("dst")
        if not src or not dst or ".." in dst or dst.startswith("/"):
            return False, f"Ungültiger Manifest-Eintrag: {entry}"
        try:
            inhalt = _fetch(src, binary=True)
        except Exception as e:
            return False, f"Download fehlgeschlagen ({src}): {e}"   # nichts angefasst
        # Bei signiertem Manifest sichert der Datei-Hash jede einzelne Datei ab.
        soll = entry.get("sha256", "")
        if signiert:
            if not soll:
                return False, f"Manifest ohne Prüfsumme für {src} — Update abgelehnt."
            if hashlib.sha256(inhalt).hexdigest() != soll:
                return False, f"Prüfsumme falsch für {src} — Update abgelehnt."
        staged.append((os.path.join(BOT_DIR, dst), inhalt))
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
