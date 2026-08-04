#!/usr/bin/env python3
"""Start-Mantel für die Operator-Dienste — protokolliert AUSNAHMSLOS jeden Fehlstart.

Warum es das gibt (30.07.2026, systematische Lehre aus neun Windows-Fehlern):
Ein Dienst, der beim Start stirbt, hinterließ auf Windows **keine Spur**. Kein
Konsolenfenster (pythonw), keine Log-Datei (die schreibt erst der Dienst selbst,
wenn er lebt), kein Journal (gibt es nur unter systemd). Jede Fehlersuche begann
damit, den Dienst von Hand im Vordergrund zu starten — beim Kunden unmöglich.

Der Fachstand für Dienste unter Windows ist eindeutig: fensterlos starten UND
selbst in eine Datei protokollieren UND alles in try/except fassen. Genau das
Letzte fehlte. Dieser Mantel schließt die Lücke:

  * Er richtet die Protokoll-Datei ein, BEVOR irgendetwas von uns importiert wird
    (ein Fehler in unseren eigenen Modulen kann ihn also nicht mitreißen).
  * Er fängt JEDEN Abbruch — auch SyntaxError und ImportError, die vor jeder
    Zeile Programmlogik auftreten — und schreibt den vollständigen Rückverfolgungs-
    text hinein.
  * Er notiert bei jedem Start die Umgebung (Python-Fassung, Zeichensatz, Pfade).
    Genau diese Angaben haben heute mehrfach gefehlt.

Aufruf:  python dienst_start.py <zielskript.py> [args…]
Nur Standardbibliothek — der Mantel muss auch dann laufen, wenn sonst nichts geht.
"""
import os
import runpy
import sys
import time
import traceback

BOT_DIR = os.path.dirname(os.path.abspath(__file__))


def _protokoll(name):
    return os.path.join(BOT_DIR, f"{name}-start.log")


def _schreib(datei, text):
    """Schreiben darf nie der Grund sein, dass ein Dienst nicht startet.

    Zeichensatz mit Vorzeichen (`utf-8-sig`): Windows PowerShell 5.1 liest Dateien
    ohne Vorzeichen als cp1252 — dann steht in unserer eigenen Diagnosedatei
    »Es lÃ¤uft bereits ein Operator« statt »läuft«, und die Umrandungen werden zu
    »â”€â”€«. Nachgewiesen am 04.08.2026 auf Michis Rechner. **Eine Diagnosedatei,
    die man nicht lesen kann, ist keine** — und sie wird ausgerechnet dann gebraucht,
    wenn ohnehin schon nichts geht.
    """
    try:
        if os.path.exists(datei) and os.path.getsize(datei) > 1_000_000:
            os.replace(datei, datei + ".alt")
        with open(datei, "a", encoding="utf-8-sig") as f:
            f.write(text)
            f.flush()
    except OSError:
        pass


def main():
    if len(sys.argv) < 2:
        sys.exit("Aufruf: dienst_start.py <zielskript.py> [args…]")
    ziel = sys.argv[1]
    if not os.path.isabs(ziel):
        ziel = os.path.join(BOT_DIR, ziel)
    name = os.path.splitext(os.path.basename(ziel))[0]
    datei = _protokoll(name)
    stempel = time.strftime("%F %T")

    # 1) Ausgabekanäle sichern — unter pythonw sind sie None, und dann killt der
    #    erste print() den Dienst. Bewusst OHNE unser platform_compat: der Mantel
    #    darf von keinem eigenen Modul abhängen.
    for kanal in ("stdout", "stderr"):
        if getattr(sys, kanal, None) is None:
            try:
                setattr(sys, kanal, open(datei, "a", encoding="utf-8-sig", buffering=1))
            except OSError:
                try:
                    setattr(sys, kanal, open(os.devnull, "w"))
                except OSError:
                    pass

    _schreib(datei, f"\n[{stempel}] ── Start: {os.path.basename(ziel)}\n"
                    f"    Python   : {sys.version.split()[0]} ({sys.executable})\n"
                    f"    Zeichensatz: {sys.getfilesystemencoding()} / "
                    f"stdout={getattr(sys.stdout, 'encoding', '?')}\n"
                    f"    Arbeitsverz.: {os.getcwd()}\n")

    if not os.path.exists(ziel):
        _schreib(datei, f"    ABBRUCH: Zielskript fehlt: {ziel}\n")
        sys.exit(2)

    sys.argv = [ziel] + sys.argv[2:]
    if BOT_DIR not in sys.path:
        sys.path.insert(0, BOT_DIR)
    try:
        runpy.run_path(ziel, run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        _schreib(datei, f"[{time.strftime('%F %T')}] beendet mit Code {code}\n")
        raise
    except BaseException:                        # ALLES, auch KeyboardInterrupt
        _schreib(datei, f"[{time.strftime('%F %T')}] ABBRUCH — vollständiger "
                        f"Rückverfolgungstext:\n{traceback.format_exc()}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
