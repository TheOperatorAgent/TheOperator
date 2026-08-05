#!/usr/bin/env python3
"""»operator abnahme« — derselbe Durchlauf auf jedem System, Ergebnis als Datei (#131/#36).

Warum es das gibt
-----------------
Alle Tests laufen auf macOS. Am 30.07. traten auf Windows **neun** plattformspezifische
Fehler nacheinander auf — keinen davon hätte die Suite gefunden, weil sie auf einem System
läuft, das die betroffenen Eigenheiten gar nicht hat.

#131 zieht daraus den Schluss: *»Ohne regelmäßige Abnahme auf echten Zielsystemen ist
plattformübergreifende Zusage Behauptung, nicht Wissen.«* Dieses Skript ist die Umsetzung.

Was es NICHT ist
----------------
Kein zweites Testframework. Es prüft nicht Logik, sondern **Verdrahtung** — genau das, was
Quelltext-Tests strukturell nicht können und was an einem einzigen Tag dreimal danebenging:
`BotSession` ohne `.creds`, sechs falsch-grüne Testanker, `os.statvfs` unter Windows.

Der Unterschied zu `pruefung.py`: Die sagt *dir*, ob deine Installation läuft. Diese hier
erzeugt ein **vergleichbares Protokoll**, das ins Repo gehört — damit sichtbar wird, ob
macOS, Linux und Windows dasselbe tun.

Aufruf:  operator abnahme        (oder: venv-python abnahme.py)
Ergebnis: abnahme-<system>-<fassung>.md im Bot-Ordner
"""
import json
import os
import platform
import subprocess
import sys
import time
import platform_compat as _plat

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)

OK, FEHLER, WARN, OFFEN = "ok", "FEHLT", "hinweis", "offen"
_ZEILEN = []


def pruefe(name, fn, erwartet=""):
    """Einen Punkt abarbeiten. Eine Ausnahme ist ein Fehlschlag, kein Abbruch —
    ein Abnahmeprotokoll, das auf halber Strecke endet, ist wertlos."""
    t0 = time.time()
    try:
        stand, hinweis = fn()
    except Exception as e:
        stand, hinweis = FEHLER, f"{type(e).__name__}: {e}"
    dauer = time.time() - t0
    zeichen = {OK: "✅", FEHLER: "❌", WARN: "⚠️", OFFEN: "⏸️"}.get(stand, "❔")
    _ZEILEN.append((zeichen, name, hinweis, f"{dauer:.1f}s"))
    print(f"  {zeichen} {name:44} {hinweis[:70]}")
    return stand


# ---------------------------------------------------------------- Die Prüfpunkte --
def _fassung():
    v = open(os.path.join(BOT_DIR, "VERSION"), encoding="utf-8").read().strip()
    import hashlib
    m = json.load(open(os.path.join(BOT_DIR, "manifest.json"), encoding="utf-8"))
    abweichend = []
    for e in m.get("files", []):
        p = os.path.join(BOT_DIR, e["dst"])
        if not os.path.exists(p):
            abweichend.append(e["dst"])
        elif hashlib.sha256(open(p, "rb").read()).hexdigest() != e["sha256"]:
            abweichend.append(e["dst"])
    if abweichend:
        return WARN, f"{v} — {len(abweichend)} Datei(en) weichen ab"
    return OK, f"{v} — alle {len(m.get('files', []))} Dateien stimmen"


def _zeichensatz():
    """Der Fehler, der den Operator auf Windows NIE antworten ließ (cp1252 statt UTF-8)."""
    import locale
    enc = (locale.getpreferredencoding(False) or "").lower()
    try:
        open(os.path.join(BOT_DIR, "VERHALTEN.md"), encoding="utf-8").read()
    except Exception as e:
        return FEHLER, f"VERHALTEN.md nicht lesbar: {e}"
    if "utf" not in enc:
        return FEHLER, f"Dateien werden als {enc} gelesen — Umlaute brechen den Listener"
    return OK, f"{enc}, VERHALTEN.md lesbar"


def _dienste():
    import servicemgr
    stand = {d: servicemgr.status(d) for d in ("listener", "dashboard", "pseudonym")}
    aus = [d for d, an in stand.items() if not an]
    return (FEHLER, "aus: " + ", ".join(aus)) if aus else (OK, "alle drei laufen")


def _startprotokolle():
    """Ein Dienst, der beim Start stirbt, hinterließ vor 1.20.0 keine Spur."""
    gefunden = []
    for name in ("listener", "dashboard", "pseudonym"):
        p = os.path.join(BOT_DIR, f"{name}-start.log")
        if os.path.exists(p) and os.path.getsize(p) > 0:
            gefunden.append(name)
    if gefunden:
        return WARN, "Fehlstart protokolliert bei: " + ", ".join(gefunden)
    return OK, "keine Fehlstarts protokolliert"


def _claude():
    """`klartext()` gibt ein Paar (Zustand, Satz) zurück — nicht nur den Satz.

    Beim allerersten Lauf dieses Skripts habe ich es als Text behandelt und einen
    Fehlalarm gebaut: ausgerechnet in dem Werkzeug, das Verdrahtungsfehler finden soll.
    Der Vermerk bleibt hier stehen, weil er die Existenzberechtigung des Skripts belegt —
    solche Fehler zeigt kein Quelltext-Test."""
    import claude_health
    zustand, satz = claude_health.klartext()
    return (OK if zustand == "ok" else FEHLER), satz


def _lange_eingabe():
    """Die 8191-Zeichen-Grenze der Windows-Befehlszeile. Der Fehler, der jede Antwort
    nach 67 ms scheitern ließ — und der auf macOS unsichtbar ist."""
    import platform_compat as pc
    creds = json.load(open(os.path.join(BOT_DIR, "credentials.json"), encoding="utf-8"))
    pfad = pc.claude_bin(creds.get("claude_bin") or "")
    gross = "Zeile zur Größenprobe. " * 500 + "\nAntworte nur mit OK"
    r = subprocess.run([pfad, "-p"], input=gross, capture_output=True, text=True,
                       timeout=180, errors="replace", **_plat.OHNE_FENSTER)
    if r.returncode != 0:
        return FEHLER, f"rc={r.returncode} bei {len(gross)} Zeichen: {(r.stderr or '')[:60]}"
    return OK, f"{len(gross)} Zeichen über die Standardeingabe: rc=0"


def _einmal_sperre():
    """#130 Verdacht 1: Zwei Listener gleichzeitig nehmen sich die Nachrichten weg."""
    p = os.path.join(BOT_DIR, "run", "listener.pid")
    if not os.path.exists(p):
        return WARN, "keine Sperrdatei — läuft der Listener?"
    d = json.load(open(p, encoding="utf-8"))
    return OK, f"aktiv, Prozess {d.get('pid')} seit {d.get('seit')}"


def _dashboard():
    import urllib.request
    cfg = json.load(open(os.path.join(BOT_DIR, "dashboard.json"), encoding="utf-8"))
    port = cfg.get("port", 8737)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
        return (OK, f"Port {port} antwortet") if r.status == 200 \
            else (FEHLER, f"Port {port}: HTTP {r.status}")


def _pruefungen():
    """Die ausgelieferte Suite gegen DIESE Installation — der Kern von #87."""
    import platform_compat as pc
    py = pc.venv_python(BOT_DIR) or sys.executable
    tests = [os.path.join(BOT_DIR, "dashboard", n)
             for n in ("test_dashboard.py", "test_petra.py")]
    if not all(os.path.exists(t) for t in tests):
        return FEHLER, "Prüfungen fehlen — Installation vor 1.23.0?"
    r = subprocess.run([py, "-m", "pytest", *tests, "-q", "--tb=no", "-m", "not lieferkette"],
                       capture_output=True, text=True, timeout=900, cwd=BOT_DIR, **_plat.OHNE_FENSTER)
    import re
    p = re.search(r"(\d+) passed", r.stdout)
    f = re.search(r"(\d+) failed", r.stdout)
    if f:
        return FEHLER, f"{f.group(1)} von {int(p.group(1) if p else 0) + int(f.group(1))} durchgefallen"
    return OK, f"{p.group(1) if p else '?'} Prüfungen bestanden"


PUNKTE = [
    ("Fassung und Dateien stimmen überein", _fassung),
    ("Zeichensatz (der cp1252-Fehler)", _zeichensatz),
    ("Dienste laufen", _dienste),
    ("Keine Fehlstarts protokolliert", _startprotokolle),
    ("Claude-Zugang gültig", _claude),
    ("Lange Eingabe (8191-Zeichen-Grenze)", _lange_eingabe),
    ("Einmal-Sperre aktiv", _einmal_sperre),
    ("Dashboard erreichbar", _dashboard),
    ("Ausgelieferte Prüfungen laufen durch", _pruefungen),
]


def main():
    system = platform.system()
    try:
        fassung = open(os.path.join(BOT_DIR, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        fassung = "unbekannt"
    print(f"Operator-Abnahme  {system} {platform.release()}  Fassung {fassung}")
    print(f"Python {platform.python_version()}  {time.strftime('%F %T')}\n")

    for name, fn in PUNKTE:
        pruefe(name, fn)

    fehler = sum(1 for z in _ZEILEN if z[0] == "❌")
    warn = sum(1 for z in _ZEILEN if z[0] == "⚠️")
    datei = os.path.join(BOT_DIR, f"abnahme-{system.lower()}-{fassung}.md")
    try:
        with open(datei, "w", encoding="utf-8") as f:
            f.write(f"# Abnahme {system} — Operator {fassung}\n\n")
            f.write(f"* System: {platform.platform()}\n")
            f.write(f"* Python: {platform.python_version()}\n")
            f.write(f"* Zeitpunkt: {time.strftime('%F %T')}\n\n")
            f.write("| | Prüfpunkt | Ergebnis | Dauer |\n|---|---|---|---|\n")
            for zeichen, name, hinweis, dauer in _ZEILEN:
                f.write(f"| {zeichen} | {name} | {hinweis} | {dauer} |\n")
            f.write(f"\n**{len(_ZEILEN) - fehler - warn} von {len(_ZEILEN)} in Ordnung**")
            if fehler:
                f.write(f", {fehler} Fehler")
            if warn:
                f.write(f", {warn} Hinweis(e)")
            f.write("\n\nDiese Datei gehört ins Repo — nur so wird sichtbar, ob macOS, "
                    "Linux und Windows dasselbe tun (#131, #36).\n")
        print(f"\nProtokoll: {datei}")
    except OSError as e:
        print(f"\nProtokoll konnte nicht geschrieben werden: {e}")

    print("─" * 60)
    if fehler:
        print(f"  {fehler} Fehler — dieses System ist NICHT abgenommen.")
        print("  👉 Die ❌-Zeilen oben zuerst; »operator diagnose« liefert die Details.")
    elif warn:
        print(f"  Keine Fehler, {warn} Hinweis(e). Abnahme mit Vorbehalt.")
    else:
        print("  Alles in Ordnung — dieses System ist abgenommen.")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
