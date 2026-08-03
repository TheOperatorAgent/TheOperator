"""Testisolation (#89) — die Suite arbeitet auf einer Momentaufnahme, nie am Original.

Warum es das gibt
-----------------
Bei 1.8.6 meldete ein Lauf einmalig »1 failed, 151 passed«, sechzehn Wiederholungen
danach waren grün. Die Ursache ist strukturell und nicht der einzelne Test: die Suite
liest an rund 28 Stellen direkt aus ``~/.claude/matrix-bot`` — **während** Listener,
``retention.py`` (das dort Logs per ``os.replace`` verschiebt und löscht),
``claude_health`` und ``throttle`` in genau diesen Ordner schreiben. Wer den Zustand
eines laufenden Systems prüft, prüft eine Wackelmenge.

Wie es gelöst ist
-----------------
Beim Import dieser Datei — also **vor** allen Testmodulen — wird eine Kopie des
Bot-Ordners in ein Temp-Verzeichnis gelegt und ``HOME`` dorthin umgebogen. Damit zeigen
die 56 vorhandenen ``sys.path.insert(0, expanduser("~/.claude/matrix-bot"))`` von allein
auf die Kopie; **keine einzige Testzeile musste angefasst werden.**

Modulebene, nicht Fixture: ``test_petra.py`` wertet ``BOT = expanduser(...)`` schon beim
Import aus, und ``test_dashboard.py`` importiert ``agents_store``/``m365_setup`` ebenfalls
beim Import. Eine session-scoped Fixture liefe zu spät.

Was bewusst NICHT umgelenkt wird
--------------------------------
* Die Auslieferungs-Repos ``/Users/Shared/operator-release/_diff_op``, ``/Users/Shared/operator-release/_rel10``, ``/Users/Shared/operator-release/_rel10gh`` und
  ``/Users/Shared/operator-release/operator-site`` — absolute Pfade ohne HOME-Bezug. Diese Tests **sollen** gegen
  das echte Auslieferungsergebnis laufen; ``test_isolation_fasst_die_auslieferung_nicht_an``
  hält das fest.
* Das venv (1 GB) wird verlinkt statt kopiert — sonst kostet jeder Lauf eine Minute und
  ``platform_compat.venv_python()`` zeigt ins Leere.
* Wer den echten Ordner braucht, setzt ``OPERATOR_TEST_NO_ISOLATION=1``.

Geheimnisse
-----------
Zugangsdaten werden **mit denselben Schlüsseln, aber ohne echte Werte** kopiert. Eine
Testmomentaufnahme darf nie ein lebendes Matrix-Token enthalten — sie liegt in /tmp,
weltlesbar-nah und ohne die Rechte des Originals.
"""
import atexit
import hashlib
import json
import os
import shutil
import sys
import tempfile

ECHT = os.path.expanduser("~/.claude/matrix-bot")   # VOR dem HOME-Patch auswerten!
AKTIV = os.environ.get("OPERATOR_TEST_NO_ISOLATION") != "1" and os.path.isdir(ECHT)

# Diese Pfade sind absolut und dürfen niemals in die Kopie zeigen — sie prüfen, was
# tatsächlich an Kunden ausgeliefert wird.
AUSLIEFERUNG = (("/Users/Shared/operator-release/_diff_op"), ("/Users/Shared/operator-release/_rel10"), ("/Users/Shared/operator-release/_rel10gh"), ("/Users/Shared/operator-release/operator-site"))

# Was nicht mitkopiert wird. Logs und Laufzeitstände sind genau das, was der laufende
# Betrieb unter uns verändert; das venv ist 1 GB und wird stattdessen verlinkt.
NICHT_KOPIEREN = shutil.ignore_patterns(
    "venv", "__pycache__", ".pytest_cache", ".git",
    "*.log", "*.log.vorher", "*.db", "*.db-shm", "*.db-wal",
    "audit.seal", "diagnose-bericht.txt",
)

# Dateien mit echten Geheimnissen: Struktur ja, Inhalt nein.
_ATTRAPPEN = {
    "credentials.json": {
        "homeserver": "https://matrix.example.invalid",
        "user_id": "@operator:example.invalid",
        "owner_id": "@owner:example.invalid",
        "room_id": "!testraum:example.invalid",
        "access_token": "syt_testkopie_kein_echtes_token",
        "allowed_tools": [],
        "claude_bin": "/usr/local/bin/claude",
    },
    "bots.json": {"bots": []},
}

ZIEL = None
_ECHT_VORHER = None


def _fingerabdruck(wurzel):
    """Hash über die Dateien, die die Suite NICHT verändern darf. Logs, run/ und die
    Datenbanken sind ausgenommen — die schreibt der laufende Betrieb legitim."""
    h = hashlib.sha256()
    for ordner, unter, dateien in os.walk(wurzel):
        unter[:] = sorted(u for u in unter
                          if u not in ("venv", "__pycache__", ".pytest_cache", "run",
                                       "workspace", ".git"))
        for name in sorted(dateien):
            if name.endswith((".log", ".vorher", ".db", ".db-shm", ".db-wal", ".seal")):
                continue
            pfad = os.path.join(ordner, name)
            h.update(os.path.relpath(pfad, wurzel).encode())
            try:
                with open(pfad, "rb") as f:
                    h.update(f.read())
            except OSError:
                h.update(b"<unlesbar>")
    return h.hexdigest()


def _momentaufnahme():
    global ZIEL, _ECHT_VORHER
    tmp = tempfile.mkdtemp(prefix="operator-testkopie-")
    heim = os.path.join(tmp, "home")
    ZIEL = os.path.join(heim, ".claude", "matrix-bot")
    os.makedirs(os.path.dirname(ZIEL))
    shutil.copytree(ECHT, ZIEL, ignore=NICHT_KOPIEREN, symlinks=True)

    # Geheimnisse durch strukturgleiche Attrappen ersetzen.
    for name, inhalt in _ATTRAPPEN.items():
        with open(os.path.join(ZIEL, name), "w", encoding="utf-8") as f:
            json.dump(inhalt, f, indent=2)
    tresor = os.path.join(ZIEL, "secrets")
    if os.path.isdir(tresor):
        shutil.rmtree(tresor)
    os.makedirs(tresor, exist_ok=True)

    # Das venv wird verlinkt: 1 GB kopieren wäre absurd, und venv_python() muss zeigen.
    echtes_venv = os.path.join(ECHT, "dashboard", "venv")
    if os.path.isdir(echtes_venv):
        os.symlink(echtes_venv, os.path.join(ZIEL, "dashboard", "venv"))

    # #106: Der Arbeitsordner liegt außerhalb von ~/.claude. Kopiert wird nur seine
    # Konfiguration (Agenten, Skills, MCP) — nicht die Arbeitsergebnisse des Nutzers,
    # die dort ebenfalls liegen und ihn nichts angehen.
    ws_echt = os.environ.get("OPERATOR_WORKSPACE") or os.path.expanduser("~/Operator")
    ws_ziel = os.path.join(heim, "Operator")
    os.makedirs(ws_ziel, mode=0o700, exist_ok=True)
    os.chmod(ws_ziel, 0o700)                       # test_arbeitsordner_ist_privat
    for teil in (".claude", ".mcp.json"):
        quelle = os.path.join(ws_echt, teil)
        if os.path.isdir(quelle):
            shutil.copytree(quelle, os.path.join(ws_ziel, teil))
        elif os.path.isfile(quelle):
            shutil.copy2(quelle, os.path.join(ws_ziel, teil))
    os.makedirs(os.path.join(ZIEL, "run"), exist_ok=True)

    _ECHT_VORHER = _fingerabdruck(ECHT)

    os.environ["HOME"] = heim
    os.environ["USERPROFILE"] = heim          # Windows-expanduser
    os.environ["HOMEPATH"] = heim
    os.environ["OPERATOR_BOT_DIR"] = ZIEL     # lesen permission_broker + claude_tool_hook
    os.environ["OPERATOR_WORKSPACE"] = ws_ziel
    sys.path.insert(0, ZIEL)
    sys.path.insert(0, os.path.join(ZIEL, "dashboard"))
    atexit.register(shutil.rmtree, tmp, True)


if AKTIV:
    _momentaufnahme()


def pytest_configure(config):
    config.addinivalue_line("markers",
                            "echt_bot_dir: braucht den echten Bot-Ordner statt der Kopie")
    # Prüft unsere Auslieferungsdisziplin, nicht die Installation des Nutzers. Der
    # Selbsttest-Knopf im Dashboard (#87) blendet diese Marke aus: ein Kunde, dem
    # »1 Prüfung durchgefallen« angezeigt wird, weil WIR die Website nicht hochgeladen
    # haben, bekommt einen Schrecken über etwas, das er gar nicht beheben kann.
    config.addinivalue_line("markers",
                            "lieferkette: prueft unsere Auslieferung, nicht die Installation")
    if AKTIV:
        print(f"\n[Testisolation] Momentaufnahme in {ZIEL} — das Original bleibt unberührt.")


def pytest_sessionfinish(session, exitstatus):
    """Der Beweis, der #89 rechtfertigt: die Suite hat das laufende System nicht angefasst."""
    if not AKTIV:
        return
    if _fingerabdruck(ECHT) != _ECHT_VORHER:
        print("\n[Testisolation] ACHTUNG: Der echte Bot-Ordner hat sich während des Laufs "
              "verändert. Entweder hat ein Test daneben geschrieben, oder du hast parallel "
              "gearbeitet. 👉 Lauf ohne parallele Arbeit wiederholen.", file=sys.stderr)
