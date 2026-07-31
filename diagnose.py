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


def lauf(argv, timeout=60, eingabe=None, cwd=None):
    """Befehl ausführen und ALLES zurückgeben — auch bei Fehlern.

    `cwd` ist für #130 nötig: Der Listener startet Claude im Arbeitsordner des Agenten,
    und ein MCP-Server, der relative Pfade nutzt, verhält sich anderswo anders."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           input=eingabe, cwd=cwd,
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
    # Das Manifest liegt bewusst NICHT auf der Platte (der Updater holt es live) —
    # also hier genauso: aus der hinterlegten Update-Quelle. Sonst bleibt dieser Teil
    # auf jedem Kundenrechner blind (Michis Bericht: »Manifest nicht auswertbar«).
    m = None
    try:
        m = json.load(open(os.path.join(BOT_DIR, "manifest.json")))
        sag("Manifest      : lokal gefunden")
    except Exception:
        quelle = ""
        try:
            quelle = open(os.path.join(BOT_DIR, "repo_raw.txt")).read().strip()
        except OSError:
            pass
        sag(f"Update-Quelle : {quelle or '(keine hinterlegt)'}")
        if quelle:
            import urllib.request
            try:
                with urllib.request.urlopen(quelle.rstrip("/") + "/manifest.json",
                                            timeout=20) as r:
                    m = json.loads(r.read().decode("utf-8"))
                sag("Manifest      : aus der Update-Quelle geladen")
            except Exception as e:
                sag(f"Manifest      : Quelle nicht erreichbar ({e})")
    try:
        if m is None:
            raise ValueError("kein Manifest verfügbar")
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


def teil7_echter_lauf():
    """#130: Der Lauf, den der Bericht am 30.07. nicht hatte.

    Damals war die Diagnose komplett grün — und der Listener hing trotzdem nach »Modell
    erwacht«. Grund: Alle bisherigen Teile prüfen VORAUSSETZUNGEN (Pfade, Rechte, Grenzen).
    Keiner davon macht das, was der Listener wirklich tut: derselbe Aufruf, mit denselben
    MCP-Servern, demselben Rückfrage-Hook und einer realistischen Prompt-Größe.

    Deshalb hier vier Läufe, die sich nur in EINER Sache unterscheiden — so zeigt der
    Bericht beim ersten Mal, WELCHE Zutat den Hänger verursacht, statt Verdachtsmomente
    zu hinterlassen."""
    titel("7 · ECHTER ENDE-ZU-ENDE-LAUF (was der Listener wirklich tut)")
    try:
        import platform_compat as pc
        creds = json.load(open(os.path.join(BOT_DIR, "credentials.json")))
        pfad = pc.claude_bin(creds.get("claude_bin") or "")
        ws = pc.workspace()
    except Exception:
        import shutil
        pfad = shutil.which("claude") or "claude"
        ws = os.path.expanduser("~/Operator")

    mcp = os.path.join(ws, ".mcp.json")
    hook_da = os.path.exists(os.path.join(ws, ".claude", "settings.json"))
    prompt = ("Du bist ein Testlauf der Operator-Diagnose. " * 200
              + "\nAntworte NUR mit dem Wort: OK")
    sag(f"Prompt: {len(prompt)} Zeichen   MCP-Konfiguration: "
        f"{'vorhanden' if os.path.exists(mcp) else 'FEHLT'}   "
        f"Hook: {'vorhanden' if hook_da else 'fehlt'}")
    sag(f"Arbeitsordner: {ws}")

    # Jede Stufe nimmt genau EINE Zutat dazu. Wo es hängt, ist die Zutat der Täter.
    stufen = [("nackt (nur Modell)", [pfad, "-p", "--output-format", "json"], None)]
    if os.path.exists(mcp):
        stufen.append(("mit MCP-Servern",
                       [pfad, "-p", "--output-format", "json", "--mcp-config", mcp], None))
        stufen.append(("mit MCP + Arbeitsordner",
                       [pfad, "-p", "--output-format", "json", "--mcp-config", mcp], ws))

    for name, argv, cwd in stufen:
        sag("", f"--- {name} ---")
        t0 = time.time()
        rc, out, err = lauf(argv, 180, eingabe=prompt, cwd=cwd)
        dauer = time.time() - t0
        sag(f"rc={rc}  {dauer:.1f}s  stdout: {out[:180]}")
        if err:
            sag(f"stderr: {err[:400]}")
        if rc != 0 or dauer > 120:
            sag("")
            sag(f"BEFUND: Hier klemmt es — bei »{name}«.")
            if "MCP" in name:
                sag("Die Stufe davor lief. Damit ist ein MCP-Server der Verdächtige.")
                sag("👉 In der .mcp.json einzelne Server auskommentieren und wiederholen.")
            else:
                sag("Schon der nackte Aufruf hängt — das liegt am Claude-CLI selbst, "
                    "nicht an unserer Verdrahtung.")
                sag("👉 'claude /login' prüfen und 'operator pruefen' Schritt 3 ansehen.")
            return
    sag("")
    sag("BEFUND: Alle Stufen liefen durch. Ein Hänger im Betrieb kommt dann NICHT vom "
        "Claude-Aufruf — als Nächstes den Rückfrage-Hook prüfen (Teil 8).")


def teil8_hook():
    """#130 Verdacht 4: Der PreToolUse-Hook läuft als eigener Prozess und wartet
    gegebenenfalls auf eine Chat-Antwort. Hängt er, hängt der ganze Lauf — und zwar genau
    nach »Modell erwacht«, weil der Hook erst beim ersten Werkzeug greift."""
    titel("8 · RÜCKFRAGE-HOOK (läuft er, und antwortet er?)")
    hook = os.path.join(BOT_DIR, "claude_tool_hook.py")
    if not os.path.exists(hook):
        sag("claude_tool_hook.py fehlt — dann kann der Hook nicht hängen, aber es "
            "wird auch nichts geprüft. 👉 Installationsbefehl erneut ausführen.")
        return
    import platform_compat as pc
    py = pc.venv_python(BOT_DIR) or sys.executable
    # Ein harmloser Aufruf: Der Hook muss ihn OHNE Rückfrage durchwinken.
    eingabe = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}})
    t0 = time.time()
    rc, out, err = lauf([py, hook], 30, eingabe=eingabe)
    dauer = time.time() - t0
    sag(f"harmloser Aufruf: rc={rc}  {dauer:.1f}s  stdout: {out[:200]}")
    if err:
        sag(f"stderr: {err[:300]}")
    if dauer > 20:
        sag("BEFUND: Der Hook antwortet nicht zügig. Genau das würde jeden Lauf nach "
            "»Modell erwacht« einfrieren.")
    elif rc != 0:
        sag("BEFUND: Der Hook scheitert schon bei einem harmlosen Aufruf — dann wird "
            "jede Werkzeugnutzung abgelehnt (fail-closed).")
    else:
        sag("BEFUND: Der Hook winkt Harmloses zügig durch. Als Ursache für einen "
            "Hänger scheidet er aus.")
    offen = os.path.join(BOT_DIR, "run", "frage_offen.json")
    if os.path.exists(offen):
        try:
            d = json.load(open(offen, encoding="utf-8"))
            sag(f"HINWEIS: Es steht gerade eine Rückfrage offen ({d.get('was', '?')}). "
                "Solange die unbeantwortet ist, wartet der laufende Auftrag — das ist "
                "kein Fehler, sieht aber wie einer aus.")
        except (OSError, ValueError):
            pass


def main():
    sag(f"Operator-Diagnose  {time.strftime('%F %T')}")
    sag(f"Bericht wird zusätzlich geschrieben nach: {BERICHT}")
    for f in (teil1_fassung, teil2_umgebung, teil3_protokolle, teil4_dienste,
              teil5_claude, teil6_grenze, teil7_echter_lauf, teil8_hook):
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
