#!/usr/bin/env python3
"""Operator Sessions-Recorder: jede Claude-Runde landet in sessions.db (SQLite+FTS5).

Stdlib-only (läuft im Listener). Aufrufe als CLI:
  sessions.py list [-n 20]        Neueste Läufe
  sessions.py search "text"       Volltextsuche über Nachrichten+Antworten
  sessions.py usage [stunden]     Läufe/Tokens im Zeitfenster (Default 5h)
"""
import json
import os
import sqlite3
import sys
import time

DB = os.path.expanduser("~/.claude/matrix-bot/sessions.db")


def db():
    con = sqlite3.connect(DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS sessions("
        "id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now','localtime')), "
        "epoch REAL, bot TEXT, kind TEXT DEFAULT 'chat', messages TEXT, result TEXT, "
        "rc INTEGER, duration_ms INTEGER, tokens_in INTEGER, tokens_out INTEGER, "
        "model TEXT)"
    )
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5("
        "messages, result, content=sessions, content_rowid=id, "
        "tokenize=\"unicode61 remove_diacritics 2\")"
    )
    con.execute(
        "CREATE TRIGGER IF NOT EXISTS ses_ai AFTER INSERT ON sessions BEGIN "
        "INSERT INTO sessions_fts(rowid, messages, result) "
        "VALUES (new.id, new.messages, new.result); END"
    )
    return con


def record(bot, messages, result, rc, duration_ms, tokens_in=0, tokens_out=0,
           kind="chat", model=""):
    # Secrets niemals in den (FTS5-durchsuchbaren) Verlauf schreiben
    try:
        import redact
        messages = redact.redact(messages or "")
        result = redact.redact(result or "")
    except ImportError:
        pass
    con = db()
    con.execute(
        "INSERT INTO sessions(epoch, bot, kind, messages, result, rc, duration_ms, "
        "tokens_in, tokens_out, model) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (time.time(), bot, kind, messages[:4000], (result or "")[:4000], rc,
         duration_ms, tokens_in, tokens_out, model or ""))
    con.commit()
    con.close()


def rows_to_dicts(rows):
    keys = ("id", "ts", "bot", "kind", "messages", "result", "rc",
            "duration_ms", "tokens_in", "tokens_out", "model")
    return [dict(zip(keys, r)) for r in rows]


def list_sessions(n=20):
    return rows_to_dicts(db().execute(
        "SELECT id, ts, bot, kind, messages, result, rc, duration_ms, tokens_in, "
        "tokens_out, model FROM sessions ORDER BY id DESC LIMIT ?", (n,)).fetchall())


def search(text, n=20):
    import re
    words = [w for w in re.findall(r"[\wäöüßÄÖÜ]+", text) if len(w) >= 3]
    if not words:
        return []
    q = " OR ".join(f'"{w}"' for w in words[:24])
    return rows_to_dicts(db().execute(
        "SELECT s.id, s.ts, s.bot, s.kind, s.messages, s.result, s.rc, s.duration_ms, "
        "s.tokens_in, s.tokens_out, s.model FROM sessions_fts f "
        "JOIN sessions s ON s.id = f.rowid WHERE sessions_fts MATCH ? "
        "ORDER BY s.id DESC LIMIT ?", (q, n)).fetchall())


# #132: Alle Arten, auf die der Operator VON SICH AUS schreibt. »event« kommt aus der
# Ereignis-Queue (#47, Mail-Watch), »cron« aus Automationen (#4), »proaktiv« aus einem
# direkten send.py-Aufruf ohne vorherigen Lauf.
PROAKTIV_ARTEN = ("event", "cron", "proaktiv")
PROAKTIV = "proaktiv"


def record_proaktiv(bot, quelle, text, meta=None):
    """#132: Eine Meldung, die der Operator VON SICH AUS geschickt hat, in den Verlauf
    schreiben.

    Warum das nötig ist: Michi bekam um 13:03 eine Mail-Zusammenfassung gepusht und
    fragte um 13:10 »hast du den termin schon zugesagt?«. Der Operator hatte dafür
    keinen Kontext — seine eigene Meldung stand nirgends. `sessions.db` wurde bis dahin
    ausschließlich als Reaktion auf eingehende Nachrichten geschrieben; Mail-Watch,
    Automationen und Auslöser sendeten am Verlauf vorbei.

    `kind` ist bewusst NICHT »chat«: `recent_dialog()` filtert darauf, und eine Mailflut
    am Morgen würde sonst den echten Dialog aus dem Sechser-Fenster verdrängen.
    `meta` trägt die Kennungen (Mail-ID, Termin-ID, Absender) mit, damit auf »sag zu«
    gehandelt werden kann, ohne erst wieder zu suchen."""
    kopf = f"(Ich habe von mir aus geschrieben — ausgelöst durch: {quelle})"
    if meta:
        try:
            kopf += "\n" + json.dumps(meta, ensure_ascii=False, sort_keys=True)[:800]
        except (TypeError, ValueError):
            pass
    record(bot, kopf, text, 0, 0, kind=PROAKTIV)


def recent_proaktiv(bot, n=3, max_age_h=12):
    """Die letzten Meldungen, die der Operator von sich aus geschickt hat.

    Der eigentliche Fehler hinter #132: Diese Läufe wurden schon immer protokolliert —
    als `kind='event'` bzw. `'cron'`. Nur gelesen hat sie niemand: `recent_dialog()`
    filtert hart auf `kind='chat'`, und der Prompt kannte keine andere Quelle. Michis
    Mail-Meldung von 13:03 stand also in der Datenbank, während der Operator um 13:10
    antwortete »keinen blassen Schimmer, welchen Termin du meinst«.

    Eigenes, kleines Kontingent statt Aufnahme ins Dialogfenster: eine Mailflut am
    Morgen soll den echten Dialog nicht aus den letzten sechs Runden verdrängen."""
    since = time.time() - max_age_h * 3600
    platzhalter = ",".join("?" * len(PROAKTIV_ARTEN))
    rows = db().execute(
        f"SELECT ts, messages, result FROM sessions WHERE bot=? AND kind IN ({platzhalter}) "
        "AND epoch > ? AND rc = 0 ORDER BY id DESC LIMIT ?",
        (bot, *PROAKTIV_ARTEN, since, n)).fetchall()
    return [(t, m, r) for t, m, r in reversed(rows)]


def recent_dialog(bot, n=6, max_age_h=24):
    """Letzte n Chat-Runden dieses Bots (aufsteigend) für Gesprächskontext."""
    since = time.time() - max_age_h * 3600
    rows = db().execute(
        "SELECT messages, result FROM sessions WHERE bot=? AND kind='chat' "
        "AND epoch > ? AND rc = 0 ORDER BY id DESC LIMIT ?", (bot, since, n)).fetchall()
    return [(m, r) for m, r in reversed(rows)]


def usage(hours=5.0):
    since = time.time() - hours * 3600
    row = db().execute(
        "SELECT COUNT(*), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0), "
        "COALESCE(SUM(duration_ms),0) FROM sessions WHERE epoch > ?", (since,)).fetchone()
    return {"hours": hours, "runs": row[0], "tokens_in": row[1],
            "tokens_out": row[2], "duration_ms": row[3]}


def usage_buckets(hours=24, bucket_h=1):
    """Balken-Daten: Läufe+Tokens je Stunde/Tag rückwärts ab jetzt."""
    now = time.time()
    out = []
    for i in range(int(hours / bucket_h)):
        hi = now - i * bucket_h * 3600
        lo = hi - bucket_h * 3600
        row = db().execute(
            "SELECT COUNT(*), COALESCE(SUM(tokens_out),0) FROM sessions "
            "WHERE epoch > ? AND epoch <= ?", (lo, hi)).fetchone()
        out.append({"offset": i, "runs": row[0], "tokens_out": row[1]})
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "list":
        n = int(args[args.index("-n") + 1]) if "-n" in args else 20
        for s in list_sessions(n):
            print(f"[{s['id']}] {s['ts']} {s['bot']}/{s['kind']} rc={s['rc']} "
                  f"{s['duration_ms']}ms {s['tokens_out']}tok: {s['messages'][:60]}")
    elif args and args[0] == "search" and len(args) > 1:
        for s in search(args[1]):
            print(f"[{s['id']}] {s['ts']} {s['bot']}: {s['messages'][:60]} -> {s['result'][:60]}")
    elif args and args[0] == "usage":
        print(json.dumps(usage(float(args[1]) if len(args) > 1 else 5.0)))
    else:
        sys.exit(__doc__)
