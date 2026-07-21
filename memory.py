#!/usr/bin/env python3
"""Operator-Gedächtnis: SQLite-FTS5-Volltextsuche, tokensparend (nur Top-k in den Prompt).

Aufrufe:
  memory.py add "Fakt"            Fakt speichern (Dedup bei identischem Text)
  memory.py search "Frage" [-k N] Top-k relevante Fakten (Default 5)
  memory.py list [-n N]           Neueste N Fakten mit IDs (Default 20)
  memory.py forget <id>           Fakt löschen
  memory.py count                 Anzahl Fakten
"""
import os
import re
import sqlite3
import sys

DB = os.path.expanduser("~/.claude/matrix-bot/memory.db")


def db():
    con = sqlite3.connect(DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS memories("
        "id INTEGER PRIMARY KEY, text TEXT NOT NULL, "
        "created TEXT DEFAULT (datetime('now','localtime')), "
        "last_used TEXT, uses INTEGER DEFAULT 0)"
    )
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
        "text, content=memories, content_rowid=id, "
        "tokenize=\"unicode61 remove_diacritics 2\")"
    )
    con.execute(
        "CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN "
        "INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text); END"
    )
    con.execute(
        "CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN "
        "INSERT INTO memories_fts(memories_fts, rowid, text) "
        "VALUES ('delete', old.id, old.text); END"
    )
    return con


def fts_query(text, min_len=3):
    """Freitext -> ODER-verknüpfte FTS-Query (Sonderzeichen-sicher)."""
    words = re.findall(r"[\wäöüßÄÖÜ]+", text)
    words = [w for w in words if len(w) >= min_len]
    if not words:
        return None
    return " OR ".join(f'"{w}"' for w in words[:24])


def cmd_add(text):
    con = db()
    if con.execute("SELECT 1 FROM memories WHERE text = ?", (text,)).fetchone():
        print("schon vorhanden")
        return
    con.execute("INSERT INTO memories(text) VALUES (?)", (text,))
    con.commit()
    print("gespeichert")


def cmd_search(text, k=5):
    con = db()
    q = fts_query(text)
    if not q:
        return
    rows = con.execute(
        "SELECT m.id, m.text FROM memories_fts f JOIN memories m ON m.id = f.rowid "
        "WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?", (q, k)
    ).fetchall()
    for mid, mtext in rows:
        con.execute(
            "UPDATE memories SET last_used = datetime('now','localtime'), "
            "uses = uses + 1 WHERE id = ?", (mid,)
        )
        print(f"- {mtext}")
    con.commit()


def cmd_list(n=20):
    for mid, text, created in db().execute(
        "SELECT id, text, created FROM memories ORDER BY id DESC LIMIT ?", (n,)
    ):
        print(f"[{mid}] ({created}) {text}")


def cmd_forget(mid):
    con = db()
    con.execute("DELETE FROM memories WHERE id = ?", (int(mid),))
    con.commit()
    print("vergessen" if con.total_changes else "id nicht gefunden")


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd, rest = args[0], args[1:]
    if cmd == "add" and rest:
        cmd_add(" ".join(rest))
    elif cmd == "search" and rest:
        k = 5
        if "-k" in rest:
            i = rest.index("-k")
            k = int(rest[i + 1]); rest = rest[:i] + rest[i + 2:]
        cmd_search(" ".join(rest), k)
    elif cmd == "list":
        cmd_list(int(rest[rest.index("-n") + 1]) if "-n" in rest else 20)
    elif cmd == "forget" and rest:
        cmd_forget(rest[0])
    elif cmd == "count":
        print(db().execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
