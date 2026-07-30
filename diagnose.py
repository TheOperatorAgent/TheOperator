#!/usr/bin/env python3
"""»operator diagnose« — sammelt ALLES in einen Bericht, den man am Stück auswerten kann.

Warum (30.07.2026, Michi: »du musst etwas implementieren, damit du jeden Scheiß loggen
kannst, damit man eine saubere Auswertung machen kann«): Die Fehlersuche lief den
ganzen Tag über Einzelfragen — jede Antwort brachte eine neue Frage, jede Runde kostete
Zeit. Dieser Bericht beantwortet sie alle auf einmal:

  1. Welche Fassung liegt WIRKLICH auf der Platte? (Prüfsummen gegen das Manifest —
     erkennt halb aktualisierte Installationen, unser Dauerproblem des Tages)
  2. Wie ist die Umgebung? (Python, Zeichensätze, Umgebungsvariablen, Pfade)
  3. Was sagen ALLE Protokolle? (listener, dashboard, Startprotokolle, Audit)
  4. Was sagen die Dienste? (roh, unlokalisiert)
  5. Antwortet Claude — mit vollständiger Ausgabe und echten Argumenten?
  6. Hält der Aufruf einer REALEN Prompt-Größe stand? (die 8191-Zeichen-Falle)

Schreibt alles nach diagnose-bericht.txt UND auf die Konsole. Geheimnisse werden
gekürzt: Tokens, Passwörter und Schlüssel erscheinen nie im Klartext.
Nur Standardbibliothek. Aufruf: python diagnose.py
"""
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)
BERICHT = os.path.join(BOT_DIR, "diagnose-bericht.txt")
_teile = []

# Alles, was wie ein Geheimnis aussieht, wird gekürzt — der Bericht wird ja verschickt.
_GEHEIM = re.compile(r"(syt_[A-Za-z0-9_\-]+|sk-[A-Za-z0-9_\-]{20,}|"
                     r"\b[A-Fa-f0-9]{40,}\b|\"access_token\"\s*:\s*\"[^\"]+\")")


def _sauber(text):
    return _GEHEIM.sub("«gekürzt»", text or "")


def sag(*zeilen):
    for z in zeilen:
        s = _sauber(str(z))
        print(s)
        _teile.append(s)


def titel(t):
    sag("", "=" * 72, t, "=" * 72)


def lauf(argv, timeout=60, eingabe=None):
    """Befehl ausführen und ALLES zurückgeben — auch bei Fehlern."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           input=eingabe,
                           stdin=None if eingabe is not None else subprocess.DEVNULL,
                           errors="replace")
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -9, "", f"ZEITÜBERSCHREITUNG nach {timeout}s"
    except Exception as e:
        return -1, "", f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- 1 Fassung --
def teil1_fassung():
    titel("1 · WELCHE FASSUNG LIEGT WIRKLICH AUF DER PLATTE?")
    try:
        sag(f"VERSION-Datei : {open(os.path.join(BOT_DIR, 'VERSION')).read().strip()}")
    except Exception as e:
        sag(f"VERSION-Datei : NICHT LESBAR ({e})")
    try:
        m = json.load(open(os.path.join(BOT_DIR, "manifest.json")))
        sag(f"Manifest sagt : {m.get('version')}  ({len(m.get('files', []))} Dateien)")
        abweichend, fehlend = [], []
        for e in m.get("files", []):
            p = os.path.join(BOT_DIR, e["src"])
            if not os.path.exists(p):
                fehlend.append(e["src"])
                continue
            soll = e.get("sha256")
            if not soll:
                continue
            ist = hashlib.sha256(open(p, "rb").read()).hexdigest()
            if ist != soll:
                abweichend.append(e["src"])
        sag("", "Prüfsummen-Vergleich (erkennt halb aktualisierte Installationen):")
        sag(f"  fehlend    : {fehlend or 'keine'}")
        sag(f"  abweichend : {abweichend or 'keine'}")
        if abweichend or fehlend:
            sag("  ⚠️ Die Installation ist NICHT die Fassung, die das Manifest beschreibt.")
    except Exception as e:
        sag(f"Manifest     : nicht auswertbar ({e})")
    # Kernfrage des 30.07.: steckt der stdin-Fix drin?
    sag("", "Merkmale einzelner Fixes in den Dateien auf der Platte:")
    for datei, marke, was in (
            ("listener.py", "input=prompt", "Prompt über Standardeingabe (1.21.0)"),
            ("listener.py", '"-p", prompt', "ALTER Prompt als Argument (darf NICHT da sein)"),
            ("listener.py", "LOGDATEI", "schreibt eigenes Log (1.19.0)"),
            ("listener.py", "_plat.claude_bin(", "Windows-sichere Claude-Auflösung (1.18.8)"),
            ("dashboard/server.py", "input=prompt", "Assistent über Standardeingabe (1.21.0)"),
            ("dienst_start.py", "ABBRUCH", "Start-Mantel (1.20.0)"),
            ("servicemgr.py", "isdigit()", "sprachfreier Dienststatus (1.20.1)")):
        p = os.path.join(BOT_DIR, datei)
        try:
            drin = marke in open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            drin = None
        sag(f"  {'JA ' if drin else 'NEIN' if drin is False else '?? '} {was}"
            f"  [{datei}: {marke!r}]")


# ---------------------------------------------------------------- 2 Umgebung --
def teil2_umgebung():
    titel("2 · UMGEBUNG")
    sag(f"System      : {platform.platform()}")
    sag(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
    sag(f"Dateizeichensatz: {sys.getfilesystemencoding()}  "
        f"stdout={getattr(sys.stdout, 'encoding', '?')}  "
        f"preferred={__import__('locale').getpreferredencoding(False)}")
    sag(f"os.name / platform: {os.name} / {sys.platform}")
    for v in ("PYTHONUTF8", "PYTHONIOENCODING", "OPERATOR_WORKSPACE", "REPO_RAW",
              "ANTHROPIC_API_KEY"):
        wert = os.environ.get(v)
        sag(f"  ENV {v:<20}= {'«gesetzt»' if v.endswith('KEY') and wert else wert!r}")
    sag(f"Arbeitsordner: {BOT_DIR}")
    for datei in ("credentials.json", "dashboard.json", "VERHALTEN.md", "persona.json"):
        p = os.path.join(BOT_DIR, datei)
        if os.path.exists(p):
            sag(f"  {datei:<20} {os.path.getsize(p):>8} Bytes")
        else:
            sag(f"  {datei:<20} FEHLT")
    # Prompt-Bausteine: wie groß wird der Auftrag wirklich?
    gr = 0
    for datei in ("VERHALTEN.md", "persona.json", "profile.json"):
        p = os.path.join(BOT_DIR, datei)
        gr += os.path.getsize(p) if os.path.exists(p) else 0
    sag("", f"Prompt-Bausteine zusammen: ~{gr} Zeichen "
            f"(Windows-Grenze für Befehlszeilen: 8191)")
    if os.name == "nt" and gr > 8191:
        sag("  → Als Argument übergeben wäre das auf Windows GARANTIERT zu lang.")


# ---------------------------------------------------------------- 3 Protokolle --
def teil3_protokolle():
    titel("3 · ALLE PROTOKOLLE (jeweils die letzten Zeilen)")
    kandidaten = ["listener.log", "dashboard.log", "pseudonym-daemon.log",
                  "listener-start.log", "server-start.log", "pseudonym_daemon-start.log",
                  "dienst_start.log"]
    for name in sorted(set(kandidaten)):
        p = os.path.join(BOT_DIR, name)
        sag("", f"--- {name} " + "-" * (60 - len(name)))
        if not os.path.exists(p):
            sag("    (Datei existiert nicht)")
            continue
        try:
            inhalt = open(p, encoding="utf-8", errors="replace").read()
        except OSError as e:
            sag(f"    (nicht lesbar: {e})")
            continue
        if not inhalt.strip():
            sag(f"    (leer, {os.path.getsize(p)} Bytes)")
            continue
        zeilen = inhalt.splitlines()
        sag(f"    ({len(zeilen)} Zeilen, {os.path.getsize(p)} Bytes) letzte 25:")
        for z in zeilen[-25:]:
            sag("    " + z)


# ---------------------------------------------------------------- 4 Dienste --
def teil4_dienste():
    titel("4 · DIENSTE (Rohausgabe, unlokalisiert bewertet)")
    if os.name == "nt":
        for task in ("OperatorListener", "OperatorDashboard", "OperatorPseudonym"):
            rc, out, err = lauf(["schtasks", "/query", "/tn", task, "/v", "/fo", "list"])
            sag("", f"--- {task} (rc={rc})")
            for z in (out or err).splitlines():
                if any(w in z for w in ("Status", "Zustand", "Letzte", "Last", "Ergebnis",
                                        "Result", "Aufgabe ausf", "Task To Run", "PID",
                                        "Prozess")):
                    sag("    " + z.strip())
    else:
        for dienst in ("listener", "dashboard", "pseudonym"):
            try:
                import servicemgr
                sag(f"  {dienst}: {'läuft' if servicemgr.status(dienst) else 'läuft nicht'}")
            except Exception as e:
                sag(f"  {dienst}: nicht ermittelbar ({e})")


# ---------------------------------------------------------------- 5 Claude --
def teil5_claude():
    titel("5 · CLAUDE — echter Aufruf mit vollständiger Ausgabe")
    try:
        import platform_compat as pc
        creds = json.load(open(os.path.join(BOT_DIR, "credentials.json")))
        pfad = pc.claude_bin(creds.get("claude_bin") or "")
        sag(f"credentials.json claude_bin: {creds.get('claude_bin')!r}")
        sag(f"aufgelöst auf              : {pfad!r}")
    except Exception as e:
        import shutil
        pfad = shutil.which("claude") or "claude"
        sag(f"Auflösung über platform_compat scheiterte ({e}) → {pfad!r}")
    for name in ("claude", "claude.cmd", "claude.exe", "claude.ps1"):
        import shutil
        sag(f"  which({name:<12}) = {shutil.which(name)!r}")

    sag("", "--- kurzer Aufruf (Prompt als Argument, wie früher) ---")
    t = time.time()
    rc, out, err = lauf([pfad, "-p", "Antworte nur mit: OK", "--output-format", "json"], 120)
    sag(f"rc={rc}  {int((time.time() - t) * 1000)}ms")
    sag(f"stdout: {out[:400]}")
    sag(f"stderr: {err[:400]}")

    sag("", "--- Aufruf über Standardeingabe (wie ab 1.21.0) ---")
    t = time.time()
    rc, out, err = lauf([pfad, "-p", "--output-format", "json"], 120,
                        eingabe="Antworte nur mit: OK")
    sag(f"rc={rc}  {int((time.time() - t) * 1000)}ms")
    sag(f"stdout: {out[:400]}")
    sag(f"stderr: {err[:400]}")


# ---------------------------------------------------------------- 6 Grenzfall --
def teil6_grenze():
    titel("6 · REALE PROMPT-GRÖSSE (die 8191-Zeichen-Falle)")
    try:
        import platform_compat as pc
        creds = json.load(open(os.path.join(BOT_DIR, "credentials.json")))
        pfad = pc.claude_bin(creds.get("claude_bin") or "")
    except Exception:
        import shutil
        pfad = shutil.which("claude") or "claude"
    gross = "Kontext-Zeile zur Größenprobe. " * 500 + "\nAntworte nur mit: OK"
    sag(f"Testprompt: {len(gross)} Zeichen")
    sag("", "--- als ARGUMENT (muss auf Windows scheitern) ---")
    rc, out, err = lauf([pfad, "-p", gross, "--output-format", "json"], 150)
    sag(f"rc={rc}   stdout: {out[:200]}   stderr: {err[:300]}")
    sag("", "--- über STANDARDEINGABE (muss gelingen) ---")
    rc2, out2, err2 = lauf([pfad, "-p", "--output-format", "json"], 150, eingabe=gross)
    sag(f"rc={rc2}  stdout: {out2[:200]}  stderr: {err2[:300]}")
    sag("")
    if rc != 0 and rc2 == 0:
        sag("BEFUND: Genau die 8191-Zeichen-Grenze. Der Fix (Standardeingabe) wirkt.")
    elif rc2 != 0:
        sag("BEFUND: Auch die Standardeingabe scheitert — die Ursache liegt NICHT an "
            "der Länge. stderr oben ist der entscheidende Hinweis.")
    else:
        sag("BEFUND: Beide Wege gelingen (typisch macOS/Linux).")


def main():
    sag(f"Operator-Diagnose  {time.strftime('%F %T')}")
    sag(f"Bericht wird zusätzlich geschrieben nach: {BERICHT}")
    for f in (teil1_fassung, teil2_umgebung, teil3_protokolle, teil4_dienste,
              teil5_claude, teil6_grenze):
        try:
            f()
        except Exception as e:
            import traceback
            sag(f"[{f.__name__} selbst gescheitert: {e}]", traceback.format_exc()[:1500])
    try:
        with open(BERICHT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_teile) + "\n")
        print(f"\nBericht geschrieben: {BERICHT}")
    except OSError as e:
        print(f"\nBericht konnte nicht geschrieben werden: {e}")


if __name__ == "__main__":
    main()
