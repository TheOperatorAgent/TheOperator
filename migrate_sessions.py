#!/usr/bin/env python3
"""Säubert den sessions.db-Altbestand (Issue #18): Zeilen aus der Zeit VOR Redaction +
Pseudonymisierung enthalten noch echte Secrets/PII. Dieser Einmal-Lauf schiebt jede alte
Zeile durch redact (Secrets, irreversibel) + Pseudonymisierung (PII → Platzhalter,
irreversibel — Altbestand braucht keine Rückübersetzung) und baut den FTS-Index neu.

Läuft im dashboard-venv (Presidio). Idempotent genug: nach dem Lauf sind keine echten
Personendaten/Secrets mehr im Verlauf; ein erneuter Lauf verändert kaum noch etwas.
"""
import os
import shutil
import sqlite3
import sys

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
sys.path.insert(0, BOT_DIR)
import pseudonym   # noqa: E402  (venv)
import redact      # noqa: E402  (stdlib)

DB = os.path.join(BOT_DIR, "sessions.db")


def _clean(text: str) -> str:
    if not text:
        return text
    text = redact.redact(text)                 # Secrets raus (irreversibel)
    p, _m, _s = pseudonym.pseudonymize(text, {})  # PII → Platzhalter (Mapping verworfen)
    return p


def main() -> int:
    if not os.path.exists(DB):
        print("keine sessions.db")
        return 0
    shutil.copy(DB, DB + ".bak-premigrate")     # Sicherheitskopie
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT id, messages, result FROM sessions").fetchall()
    changed = 0
    for sid, msg, res in rows:
        cm, cr = _clean(msg), _clean(res)
        if cm != (msg or "") or cr != (res or ""):
            con.execute("UPDATE sessions SET messages=?, result=? WHERE id=?", (cm, cr, sid))
            changed += 1
    con.commit()
    con.execute("INSERT INTO sessions_fts(sessions_fts) VALUES('rebuild')")  # FTS neu aufbauen
    con.commit()
    con.close()
    print(f"sessions.db-Altbestand gesäubert: {changed}/{len(rows)} Zeilen neu geschrieben, "
          f"FTS neu aufgebaut (Backup: sessions.db.bak-premigrate)")
    return changed


if __name__ == "__main__":
    main()
