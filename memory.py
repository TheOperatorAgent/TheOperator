#!/usr/bin/env python3
"""Operator-Gedächtnis: Hybrid-Suche (#52) — FTS5-Volltext + semantische Vektorsuche.

FTS5 findet exakte Wörter; der Vektor-Layer (embeddings.py, optional) findet Fakten
auch bei ANDERER Formulierung („welchen Wagen fährt er?" → „Michis Auto ist ein Golf").
Beide Ranglisten werden per Reciprocal Rank Fusion vereint. Ohne Embedding-Provider
(kein Ollama/OpenAI konfiguriert) arbeitet alles unverändert mit FTS5 — fail-open.

Aufrufe:
  memory.py add "Fakt"            Fakt speichern (Dedup bei identischem Text)
  memory.py search "Frage" [-k N] Top-k relevante Fakten (Default 5)
  memory.py list [-n N]           Neueste N Fakten mit IDs (Default 20)
  memory.py forget <id>           Fakt löschen
  memory.py count                 Anzahl Fakten
  memory.py reindex               fehlende Embeddings nachziehen (Backfill)
"""
import os
import re
import sqlite3
import sys

try:
    import embeddings as emb            # stdlib-Modul, gleiche Verzeichnisebene
except Exception:
    emb = None
try:
    import skillguard                   # #49: Injection-Scan für neue Fakten
except Exception:
    skillguard = None

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
    # #52: Embedding je Fakt (float32-BLOB); Löschen räumt mit auf
    con.execute("CREATE TABLE IF NOT EXISTS mem_vecs("
                "id INTEGER PRIMARY KEY REFERENCES memories(id), vec BLOB)")
    con.execute("CREATE TRIGGER IF NOT EXISTS mem_vd AFTER DELETE ON memories BEGIN "
                "DELETE FROM mem_vecs WHERE id = old.id; END")
    # #49: Quarantäne-Flag für Fakten mit Injection-Verdacht (migrationssicher hinzufügen)
    if "flagged" not in [r[1] for r in con.execute("PRAGMA table_info(memories)")]:
        con.execute("ALTER TABLE memories ADD COLUMN flagged INTEGER DEFAULT 0")
    return con


def _injection_verdict(text):
    """#49: Prüft einen zu speichernden Fakt auf Prompt-Injection/Exfil-Muster.
    Rückgabe: (flagged: bool, grund: str). Fail-open ohne skillguard."""
    if not skillguard:
        return False, ""
    r = skillguard.scan(text)
    if r["level"] == "gefahr":
        return True, "; ".join(f["msg"] for f in r["findings"][:3])
    return False, ""


def _store_vec(con, mid, text, plan=None):
    """Embedding berechnen + ablegen — fail-open (kein Provider = kein Eintrag)."""
    if not emb:
        return False
    vec = emb.embed(text, plan)
    if not vec:
        return False
    con.execute("INSERT OR REPLACE INTO mem_vecs(id, vec) VALUES (?, ?)",
                (mid, emb.pack(vec)))
    return True


def _vec_ranking(con, query, k=20):
    """IDs nach Kosinus-Ähnlichkeit zur Frage (beste zuerst); [] ohne Vektor-Layer."""
    if not emb:
        return []
    qv = emb.embed(query)
    if not qv:
        return []
    scored = []
    for mid, blob in con.execute("SELECT id, vec FROM mem_vecs"):
        scored.append((emb.cosine(qv, emb.unpack(blob)), mid))
    scored.sort(reverse=True)
    return [mid for score, mid in scored[:k] if score > 0.3]


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
    flagged, grund = _injection_verdict(text)
    cur = con.execute("INSERT INTO memories(text, flagged) VALUES (?, ?)",
                      (text, 1 if flagged else 0))
    _store_vec(con, cur.lastrowid, text)
    con.commit()
    if flagged:
        print(f"⚠️ QUARANTÄNE: Fakt zeigt Injection-/Exfil-Muster ({grund}) — gespeichert, "
              "aber aus der Gedächtnis-Suche ausgeschlossen, bis du ihn im Dashboard freigibst.")
    else:
        print("gespeichert")


def hybrid_ids(con, text, k=5):
    """Hybrid-Ranking (#52): FTS5-Rangliste + Vektor-Rangliste -> RRF -> Top-k-IDs.
    #49: quarantänte Fakten (flagged=1) sind ausgeschlossen — sie beeinflussen KEINE Antwort."""
    flagged = {r[0] for r in con.execute("SELECT id FROM memories WHERE flagged = 1")}
    fts_ids = []
    q = fts_query(text)
    if q:
        fts_ids = [r[0] for r in con.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ? "
            "ORDER BY bm25(memories_fts) LIMIT 20", (q,)) if r[0] not in flagged]
    vec_ids = [i for i in _vec_ranking(con, text) if i not in flagged]
    if not vec_ids:
        return fts_ids[:k]
    if not fts_ids:
        return vec_ids[:k]
    return emb.rrf_merge([fts_ids, vec_ids], top=k)


def cmd_search(text, k=5):
    con = db()
    for mid in hybrid_ids(con, text, k):
        row = con.execute("SELECT text FROM memories WHERE id = ?", (mid,)).fetchone()
        if not row:
            continue
        con.execute(
            "UPDATE memories SET last_used = datetime('now','localtime'), "
            "uses = uses + 1 WHERE id = ?", (mid,)
        )
        print(f"- {row[0]}")
    con.commit()


def cmd_reindex():
    """Backfill: Embeddings für Fakten ohne Vektor nachziehen (ein Provider-Call je Fakt)."""
    con = db()
    plan = emb.resolve() if emb else None
    if not plan:
        print("kein Embedding-Provider aktiv (Ollama/OpenAI im Dashboard einrichten) — nichts zu tun")
        return
    rows = con.execute("SELECT id, text FROM memories WHERE id NOT IN "
                       "(SELECT id FROM mem_vecs)").fetchall()
    done = sum(1 for mid, text in rows if _store_vec(con, mid, text, plan))
    con.commit()
    print(f"{done}/{len(rows)} Embeddings ergänzt ({plan['provider']}/{plan['model']})")


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
    elif cmd == "reindex":
        cmd_reindex()
    elif cmd == "flagged":                              # #49: Quarantäne anzeigen
        for mid, text, created in db().execute(
                "SELECT id, text, created FROM memories WHERE flagged = 1 ORDER BY id DESC"):
            print(f"[{mid}] ({created}) {text}")
    elif cmd == "release" and rest:                     # #49: Fakt aus Quarantäne freigeben
        con = db()
        con.execute("UPDATE memories SET flagged = 0 WHERE id = ?", (int(rest[0]),))
        con.commit()
        print("freigegeben" if con.total_changes else "id nicht gefunden")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
