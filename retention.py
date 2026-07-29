#!/usr/bin/env python3
"""Operator — Aufbewahrung und Aufräumen lokaler Daten (#18, stdlib-only).

Grundsatz: Was nicht mehr gebraucht wird, soll auch nicht mehr da sein. Ohne
Aufräumen sammeln sich Gesprächsverlauf und Protokolle jahrelang an — auch wenn
sie niemand je wieder liest. Das ist weder nötig noch im Sinne des Datenschutzes.

Standard-Fristen (in dashboard.json unter "retention" änderbar):
  * sessions_days = 30 — Gesprächsverlauf (pseudonymisiert in sessions.db)
  * logs_days     = 14 — Betriebsprotokolle (listener.log)
  * audit_days    = 90 — Sicherheits-Audit (audit.log, längere Frist mit Absicht:
                         Nachvollziehbarkeit von Zugriffen ist ein Schutzgut)

Läuft einmal täglich aus dem Listener-Tick. Alles fail-open: Ein Fehler beim
Aufräumen darf den Betrieb nie stören.
"""
import json
import os
import time

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
CONFIG_FILE = os.path.join(BOT_DIR, "dashboard.json")
STATE_FILE = os.path.join(BOT_DIR, "run", "retention.json")
DEFAULTS = {"enabled": True, "sessions_days": 30, "logs_days": 14, "audit_days": 90}
INTERVAL = 86400          # einmal täglich reicht


def config():
    cfg = dict(DEFAULTS)
    try:
        c = json.load(open(CONFIG_FILE)).get("retention", {})
        if isinstance(c, dict):
            for k in DEFAULTS:
                if k in c:
                    cfg[k] = c[k]
    except (OSError, ValueError):
        pass
    return cfg


def _kuerzen(pfad, tage, log):
    """Log-Datei: Zeilen älter als `tage` verwerfen. Erkennt das Zeitformat
    »[YYYY-MM-DD HH:MM:SS]« am Zeilenanfang; Zeilen ohne Datum gehören zur
    vorherigen (mehrzeilige Tracebacks) und werden mit dieser behandelt."""
    if not os.path.exists(pfad):
        return 0
    grenze = time.time() - tage * 86400
    behalten, entfernt, nimm = [], 0, True
    try:
        with open(pfad, errors="replace") as f:
            for zeile in f:
                if zeile.startswith("[") and len(zeile) > 20 and zeile[5] == "-":
                    try:
                        ts = time.mktime(time.strptime(zeile[1:20], "%Y-%m-%d %H:%M:%S"))
                        nimm = ts >= grenze
                    except ValueError:
                        nimm = True
                if nimm:
                    behalten.append(zeile)
                else:
                    entfernt += 1
        if entfernt:
            tmp = pfad + ".tmp"
            with open(tmp, "w") as f:
                f.writelines(behalten)
            os.chmod(tmp, 0o600)
            os.replace(tmp, pfad)
            log(f"Aufbewahrung: {entfernt} alte Zeilen aus {os.path.basename(pfad)} entfernt")
    except OSError as e:
        log(f"Aufbewahrung ({os.path.basename(pfad)}): {e}")
    return entfernt


def _sessions_kuerzen(tage, log):
    """Alte Gesprächsrunden aus sessions.db löschen (inkl. FTS-Index)."""
    db_pfad = os.path.join(BOT_DIR, "sessions.db")
    if not os.path.exists(db_pfad):
        return 0
    grenze = time.time() - tage * 86400
    try:
        import sqlite3
        db = sqlite3.connect(db_pfad)
        n = db.execute("SELECT COUNT(*) FROM sessions WHERE epoch < ?", (grenze,)).fetchone()[0]
        if n:
            db.execute("DELETE FROM sessions WHERE epoch < ?", (grenze,))
            db.commit()
            try:
                db.execute("VACUUM")       # Platz wirklich freigeben
            except sqlite3.Error:
                pass
            log(f"Aufbewahrung: {n} Gesprächsrunden älter als {tage} Tage gelöscht")
        db.close()
        return n
    except Exception as e:
        log(f"Aufbewahrung (sessions.db): {e}")
        return 0


def status():
    """Für die Dashboard-Anzeige: was liegt hier, wie alt, wie lange noch?"""
    cfg = config()
    out = {"config": cfg, "daten": []}

    def _groesse(p):
        try:
            return round(os.path.getsize(p) / 1024, 1)
        except OSError:
            return 0

    db_pfad = os.path.join(BOT_DIR, "sessions.db")
    runden, aeltester = 0, None
    try:
        import sqlite3
        db = sqlite3.connect(db_pfad)
        runden = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        a = db.execute("SELECT MIN(epoch) FROM sessions").fetchone()[0]
        aeltester = int((time.time() - a) / 86400) if a else None
        db.close()
    except Exception:
        pass
    out["daten"] = [
        {"name": "Gesprächsverlauf", "datei": "sessions.db", "kb": _groesse(db_pfad),
         "eintraege": runden, "aeltester_tage": aeltester, "frist_tage": cfg["sessions_days"]},
        {"name": "Betriebsprotokoll", "datei": "listener.log",
         "kb": _groesse(os.path.join(BOT_DIR, "listener.log")), "frist_tage": cfg["logs_days"]},
        {"name": "Sicherheits-Audit", "datei": "audit.log",
         "kb": _groesse(os.path.join(BOT_DIR, "audit.log")), "frist_tage": cfg["audit_days"]},
    ]
    try:
        out["letzter_lauf"] = json.load(open(STATE_FILE)).get("last", 0)
    except (OSError, ValueError):
        out["letzter_lauf"] = 0
    return out


def aufraeumen(log=print, force=False):
    """Einmal aufräumen. Gibt zurück, was entfernt wurde."""
    cfg = config()
    if not cfg.get("enabled", True) and not force:
        return {"uebersprungen": "Aufbewahrung ist ausgeschaltet"}
    ergebnis = {
        "sessions": _sessions_kuerzen(int(cfg["sessions_days"]), log),
        "log_zeilen": _kuerzen(os.path.join(BOT_DIR, "listener.log"), int(cfg["logs_days"]), log),
        "audit_zeilen": _kuerzen(os.path.join(BOT_DIR, "audit.log"), int(cfg["audit_days"]), log),
    }
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump({"last": int(time.time()), "letztes_ergebnis": ergebnis}, f)
    except OSError:
        pass
    return ergebnis


def faellig(now=None):
    """Einmal täglich — nicht bei jedem Tick."""
    now = now or time.time()
    try:
        letzter = json.load(open(STATE_FILE)).get("last", 0)
    except (OSError, ValueError):
        letzter = 0
    return (now - letzter) > INTERVAL


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "clean":
        print(json.dumps(aufraeumen(force=True), indent=2, ensure_ascii=False))
    print(json.dumps(status(), indent=2, ensure_ascii=False))
