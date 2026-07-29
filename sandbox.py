#!/usr/bin/env python3
"""Betriebssystem-Sandbox für Agenten-Läufe (#104 Variante A, stdlib-only).

**Warum das existiert.** Bis hierher hing die Absicherung an Mustererkennung: Der
Broker liest den geplanten Befehl und entscheidet. Das erkennt bekannte Formen —
aber ein Sprachmodell formuliert von sich aus anders, und eine Liste kann nie
beweisen, dass sie vollständig ist (externe Security-Review, 29.07.). Diese
Sandbox ist die Ebene darunter: Sie entscheidet nicht, was ein Befehl *bedeutet*,
sondern setzt hart durch, was er *darf* — vom Betriebssystem erzwungen, egal wie
der Befehl geschrieben ist.

**Was sie durchsetzt.** Geschrieben werden darf nur im Arbeitsordner und in
temporären Verzeichnissen. Alles andere ist schreibgeschützt — insbesondere der
Operator-Ordner selbst mit Update-Quelle, Signatur-Schlüssel, Prüfer und dem
Broker (genau die Kette aus #105). Lesen und Netz bleiben erlaubt: Der Operator
soll ja arbeiten können, und Lese-/Netz-Grenzen decken bereits die
Pseudonymisierung (#83) und der Netz-Wächter (#82) ab.

**Ehrliche Grenzen.**
- macOS: `sandbox-exec` (Seatbelt) ist an Bord und wird genutzt. Apple markiert
  das Werkzeug als veraltet; es ist seit Jahren funktionsfähig und wird von
  anderen Werkzeugen ebenso verwendet. Fällt es weg, meldet `verfuegbar()` das.
- Linux: `bubblewrap` (bwrap), falls installiert. Sonst KEINE Sandbox — dann
  gilt weiterhin nur die Broker-Prüfung, und der Nutzer erfährt das ehrlich.
- Windows: kein gleichwertiges Bordmittel. Keine Sandbox, ehrlich ausgewiesen.
- Die Sandbox schützt das Dateisystem, nicht vor allem: Ein Befehl darf weiterhin
  im Arbeitsordner tun, was er will, und ins öffentliche Netz.
"""
import os
import shutil
import subprocess
import sys

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)
import platform_compat  # noqa: E402

WORKSPACE = platform_compat.workspace()   # #106


def _real(p):
    try:
        return os.path.realpath(p)
    except OSError:
        return p


def _schreibpfade():
    """Wohin darf geschrieben werden? Arbeitsordner + temporäre Verzeichnisse.
    realpath ist Pflicht: /tmp ist auf macOS ein Symlink auf /private/tmp — ohne
    Auflösung würde die Sandbox den eigenen Arbeitsordner blockieren."""
    pfade = [_real(WORKSPACE), _real("/tmp")]
    for env in ("TMPDIR", "TEMP"):
        if os.environ.get(env):
            pfade.append(_real(os.environ[env]))
    if platform_compat.IS_MAC:
        pfade.append("/private/var/folders")      # macOS-Temp je Nutzer
    # Claude legt eigenen Zustand unter ~/.claude ab (Sitzungen, Konfiguration) —
    # ohne Schreibrecht dort läuft der CLI nicht. Der Operator-Ordner liegt
    # DARUNTER und wird gleich wieder ausgenommen.
    pfade.append(_real(os.path.expanduser("~/.claude")))
    return [p for p in dict.fromkeys(pfade) if p]


def verfuegbar():
    """→ (verfuegbar: bool, klartext_grund). Für Dashboard und Doku."""
    if platform_compat.IS_MAC:
        if os.path.exists("/usr/bin/sandbox-exec"):
            return True, "macOS-Sandbox (sandbox-exec)"
        return False, "sandbox-exec nicht gefunden"
    if platform_compat.IS_LINUX:
        if shutil.which("bwrap"):
            return True, "Linux-Sandbox (bubblewrap)"
        return False, ("bubblewrap ist nicht installiert — ohne sie greift nur die "
                       "Befehls-Prüfung. Nachrüsten: sudo apt install bubblewrap")
    return False, ("Unter Windows gibt es kein gleichwertiges Bordmittel — hier "
                   "schützt die Befehls-Prüfung mit Rückfrage.")


def _mac_profil():
    """Seatbelt-Profil: alles erlaubt, AUSSER Schreiben — das nur in den erlaubten
    Pfaden. Der Operator-Ordner wird danach explizit wieder gesperrt (die letzte
    passende Regel gewinnt), damit workspace/ als Unterordner erlaubt bleibt,
    der Rest des Ordners aber nicht."""
    zeilen = ["(version 1)", "(allow default)", "(deny file-write*)"]
    erlaubt = " ".join(f'(subpath "{p}")' for p in _schreibpfade())
    zeilen.append(f"(allow file-write* {erlaubt})")
    # Der Operator-Ordner selbst bleibt tabu — Update-Quelle, Signatur-Schlüssel,
    # Prüfer und Broker liegen hier (#105). workspace/ liegt darunter und wird
    # anschließend wieder freigegeben.
    zeilen.append(f'(deny file-write* (subpath "{_real(BOT_DIR)}"))')
    zeilen.append(f'(allow file-write* (subpath "{_real(WORKSPACE)}"))')
    return "\n".join(zeilen) + "\n"


def wrap(argv, log=lambda *_: None):
    """Hüllt einen Befehl in die Sandbox. Gibt das neue argv zurück — ist keine
    Sandbox verfügbar, kommt argv unverändert zurück (der Aufrufer erfährt das
    über verfuegbar() und meldet es ehrlich, statt Schutz vorzutäuschen)."""
    ok, _ = verfuegbar()
    if not ok:
        return argv
    if platform_compat.IS_MAC:
        import tempfile
        fd, pfad = tempfile.mkstemp(suffix=".sb", prefix="operator-")
        with os.fdopen(fd, "w") as f:
            f.write(_mac_profil())
        return ["/usr/bin/sandbox-exec", "-f", pfad] + list(argv)
    if platform_compat.IS_LINUX:
        cmd = ["bwrap", "--dev-bind", "/", "/", "--die-with-parent"]
        # Erst alles schreibgeschützt, dann die erlaubten Pfade wieder beschreibbar.
        for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt"):
            if os.path.exists(p):
                cmd += ["--ro-bind", p, p]
        cmd += ["--ro-bind", _real(BOT_DIR), _real(BOT_DIR)]
        for p in _schreibpfade():
            if os.path.exists(p):
                cmd += ["--bind", p, p]
        return cmd + list(argv)
    return argv


def selbsttest():
    """Beweist am laufenden System, dass die Sandbox wirklich greift:
    Schreiben im Arbeitsordner klappt, Schreiben daneben nicht.
    → (ok: bool, meldung). Wird vom Dashboard und von den Tests genutzt."""
    ok, grund = verfuegbar()
    if not ok:
        return False, grund
    os.makedirs(WORKSPACE, exist_ok=True)
    innen = os.path.join(_real(WORKSPACE), ".sandbox-probe")
    aussen = os.path.join(_real(BOT_DIR), ".sandbox-probe-verboten")
    skript = (f'echo ok > "{innen}" && echo INNEN_OK; '
              f'echo boese > "{aussen}" 2>/dev/null && echo AUSSEN_DURCH || echo AUSSEN_BLOCKIERT')
    try:
        r = subprocess.run(wrap(["/bin/sh", "-c", skript]),
                           capture_output=True, text=True, timeout=30)
        aus = r.stdout
    except Exception as e:
        return False, f"Selbsttest fehlgeschlagen: {e}"
    finally:
        for p in (innen, aussen):
            try:
                os.remove(p)
            except OSError:
                pass
    if "INNEN_OK" in aus and "AUSSEN_BLOCKIERT" in aus:
        return True, grund + " — geprüft: Schreiben nur im Arbeitsordner"
    return False, f"Sandbox greift nicht wie erwartet ({aus.strip()[:120]})"


if __name__ == "__main__":
    ok, m = selbsttest()
    print(("✓ " if ok else "✗ ") + m)
    sys.exit(0 if ok else 1)
