#!/usr/bin/env python3
"""»operator pruefen« — geht die ganze Kette systematisch durch und sagt, wo es klemmt.

Warum (30.07.2026): Neun Windows-Fehler hintereinander, jeder verdeckte den nächsten,
und jede Suche begann bei null. Statt Symptom für Symptom zu raten, prüft dieses
Modul die Kette **in der Reihenfolge, in der sie reißen kann** — vom Python-Start
bis zur echten Modell-Antwort — und nennt bei jedem Halt den nächsten Schritt.

Bewusst Standardbibliothek und bewusst eigenständig: Es muss laufen, wenn sonst
nichts läuft. Aufruf: python pruefung.py
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)

OK, WARN, FEHLER = "  [ok]  ", "  [!]   ", "  [xx]  "
_ergebnis = []


def sagen(zeichen, text, tipp=""):
    print(zeichen + text)
    if tipp:
        print("        👉 " + tipp)
    _ergebnis.append(zeichen)


def _creds():
    try:
        return json.load(open(os.path.join(BOT_DIR, "credentials.json")))
    except Exception:
        return {}


def schritt1_python():
    print("\n1 · Python & Zeichensatz")
    sagen(OK, f"Python {sys.version.split()[0]} — {sys.executable}")
    fs = sys.getfilesystemencoding() or ""
    if os.name == "nt" and "utf" not in fs.lower():
        sagen(FEHLER, f"Dateien werden als »{fs}« gelesen, nicht UTF-8",
              "Dienste müssen mit »-X utf8« starten (Installer erneut ausführen). "
              "Sonst stirbt der Operator beim Lesen von VERHALTEN.md.")
    else:
        sagen(OK, f"Zeichensatz für Dateien: {fs}")


def schritt2_dateien():
    print("\n2 · Dateien & Konfiguration")
    for datei in ("listener.py", "platform_compat.py", "credentials.json",
                  "VERHALTEN.md", "dienst_start.py"):
        p = os.path.join(BOT_DIR, datei)
        sagen(OK if os.path.exists(p) else FEHLER,
              f"{datei}{'' if os.path.exists(p) else ' FEHLT'}",
              "" if os.path.exists(p) else "Installer erneut ausführen.")
    # Umlaute wirklich lesbar? Genau hier starb der Windows-Listener.
    try:
        open(os.path.join(BOT_DIR, "VERHALTEN.md"), encoding="utf-8").read()
        sagen(OK, "VERHALTEN.md ist lesbar (UTF-8)")
    except Exception as e:
        sagen(FEHLER, f"VERHALTEN.md nicht lesbar: {e}",
              "Das ist genau der Fehler, der jede Antwort verhindert.")


def schritt3_claude():
    print("\n3 · Claude CLI")
    try:
        import platform_compat as pc
        pfad = pc.claude_bin(_creds().get("claude_bin") or "")
    except Exception:
        pfad = shutil.which("claude") or ""
    if not pfad:
        sagen(FEHLER, "Claude CLI nicht gefunden",
              "npm install -g @anthropic-ai/claude-code")
        return
    endung = os.path.splitext(pfad)[1].lower()
    if os.name == "nt" and endung not in (".exe", ".cmd", ".bat", ".com"):
        sagen(FEHLER, f"Claude-Pfad ist nicht startbar: {pfad}",
              "Auf Windows braucht es claude.cmd/.exe — Installer erneut ausführen.")
        return
    sagen(OK, f"Claude CLI: {pfad}")
    # Gemerkter Zustand — sagt auch, wann Claude ZULETZT nachweislich funktioniert hat.
    # Ohne das sieht man nur »geht/geht nicht«, aber nicht »läuft bald ab«.
    try:
        import claude_health
        zustand, text = claude_health.klartext()
        sagen(OK if zustand == "ok" else FEHLER if zustand == "expired" else WARN,
              f"Zustand: {text}",
              "" if zustand == "ok" else
              "»claude /login« ausführen — und im Dashboard unter »Modelle & Provider« "
              "einen API-Key als Reserve hinterlegen, dann springt der Operator selbst ein.")
    except Exception:
        pass
    try:
        r = subprocess.run([pfad, "-p", "Antworte nur mit: OK"], capture_output=True,
                           text=True, timeout=90,
                           stdin=subprocess.DEVNULL)
        if "OK" in (r.stdout or ""):
            sagen(OK, "Claude antwortet (Anmeldung gültig)")
        else:
            sagen(FEHLER, f"Claude antwortet nicht (Code {r.returncode})",
                  "»claude« starten, anmelden, /exit — dann hier erneut prüfen. "
                  f"Ausgabe: {(r.stderr or r.stdout or '')[:200].strip()}")
    except subprocess.TimeoutExpired:
        sagen(FEHLER, "Claude antwortet nicht innerhalb von 90 Sekunden",
              "Vermutlich wartet er auf eine Anmeldung: »claude« starten.")
    except Exception as e:
        sagen(FEHLER, f"Claude-Aufruf scheitert: {e}",
              "Auf Windows deutet »WinError 193« auf die falsche Startdatei hin.")


def schritt4_dienste():
    print("\n4 · Dienste")
    try:
        import servicemgr
        for dienst in ("listener", "dashboard", "pseudonym"):
            laeuft = False
            try:
                laeuft = bool(servicemgr.status(dienst))
            except Exception:
                pass
            sagen(OK if laeuft else WARN,
                  f"{dienst}: {'läuft' if laeuft else 'läuft nicht'}",
                  "" if laeuft else "Startprotokoll ansehen: "
                                    f"{dienst}-start.log im Operator-Ordner.")
    except Exception as e:
        sagen(WARN, f"Dienst-Status nicht ermittelbar: {e}")
    # Startprotokolle des Mantels: hier steht der Grund für stumme Fehlstarts
    for name in ("listener", "server", "pseudonym_daemon"):
        p = os.path.join(BOT_DIR, f"{name}-start.log")
        if os.path.exists(p):
            try:
                letzte = open(p, encoding="utf-8", errors="replace").read()[-400:]
            except OSError:
                continue
            if "ABBRUCH" in letzte:
                sagen(FEHLER, f"{name} hat beim Start abgebrochen",
                      f"Der Grund steht in {name}-start.log (letzte Zeilen).")


def schritt5_matrix():
    print("\n5 · Matrix-Verbindung")
    c = _creds()
    hs, tok = c.get("homeserver", ""), c.get("access_token", "")
    if not hs:
        sagen(FEHLER, "Kein Homeserver konfiguriert", "Installer erneut ausführen.")
        return
    try:
        with urllib.request.urlopen(hs.rstrip("/") + "/_matrix/client/versions",
                                    timeout=15) as r:
            r.read(1)
        sagen(OK, f"Homeserver erreichbar: {hs}")
    except Exception as e:
        sagen(FEHLER, f"Homeserver nicht erreichbar: {e}",
              "Internetverbindung bzw. Adresse prüfen.")
    if tok in ("", "keychain"):
        # Der Installer legt ihn unter »matrix-owner« ab (30.07. falsch geraten:
        # ich habe nach »matrix-token« gesucht und einen Fehlalarm erzeugt).
        try:
            import secretstore
            for name in ("matrix-owner", "matrix-token"):
                tok = secretstore.get(name) or ""
                if tok:
                    break
        except Exception:
            tok = ""
    if not tok:
        sagen(WARN, "Kein Matrix-Zugangstoken gefunden",
              "Installer erneut ausführen (Anmeldung wiederholen).")
        return
    try:
        req = urllib.request.Request(hs.rstrip("/") + "/_matrix/client/v3/account/whoami",
                                     headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=15) as r:
            wer = json.load(r).get("user_id", "?")
        sagen(OK, f"Angemeldet als {wer}")
    except Exception as e:
        sagen(FEHLER, f"Matrix-Anmeldung ungültig: {e}",
              "Installer erneut ausführen.")


def schritt6_dashboard():
    print("\n6 · Dashboard")
    try:
        port = json.load(open(os.path.join(BOT_DIR, "dashboard.json"))).get("port", 8737)
    except Exception:
        sagen(WARN, "dashboard.json fehlt — Dashboard nicht eingerichtet")
        return
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=8) as r:
            r.read(1)
        sagen(OK, f"Dashboard antwortet auf 127.0.0.1:{port}")
    except Exception as e:
        sagen(FEHLER, f"Dashboard antwortet nicht ({e})",
              "Startprotokoll server-start.log ansehen.")


def main():
    print("Operator-Selbstprüfung — geht die Kette durch, bis etwas klemmt.")
    print(f"Ordner: {BOT_DIR}")
    for schritt in (schritt1_python, schritt2_dateien, schritt3_claude,
                    schritt4_dienste, schritt5_matrix, schritt6_dashboard):
        try:
            schritt()
        except Exception as e:                   # Eine Prüfung darf nie alles stoppen
            sagen(WARN, f"Prüfung »{schritt.__name__}« selbst gescheitert: {e}")
    fehler = _ergebnis.count(FEHLER)
    warn = _ergebnis.count(WARN)
    print("\n" + "─" * 60)
    if fehler:
        print(f"  {fehler} Problem(e) gefunden — die mit [xx] markierten Zeilen zuerst.")
    elif warn:
        print(f"  Keine Fehler, {warn} Hinweis(e).")
    else:
        print("  Alles in Ordnung — dein Operator ist einsatzbereit.")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
