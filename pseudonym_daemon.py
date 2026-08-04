#!/usr/bin/env python3
"""Operator Pseudonymisierungs-Daemon — lädt das deutsche NER-Modell EINMAL und beantwortet
Anfragen über einen Unix-Socket. So entfällt der ~1,8 s Modell-Kaltstart pro Nachricht
(Issue #33). Der stdlib-Listener spricht den Daemon über denselben Socket an; ist der Daemon
nicht erreichbar, fällt der Listener automatisch auf den Einzel-Subprozess zurück.

Protokoll (newline-delimited JSON über den Socket):
  → {"texts": [...], "mapping": {}, "mode": "standard", "allow": [], "deny": []}
  ← {"texts": [...], "mapping": {}, "stats": {}}

Läuft im dashboard-venv (Presidio/spaCy). launchd hält ihn am Leben.
"""
import json
import os
import sys

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
sys.path.insert(0, BOT_DIR)
import platform_compat as _plat  # noqa: E402  (stdlib-Modul aus BOT_DIR)
_plat.ensure_std_streams(
    os.path.join(os.path.expanduser("~/.claude/matrix-bot"),
                 "pseudonym-daemon.log"))


# Cross-Turn-Konsistenz (Issue #34): pro Konversation ein fortgeführtes Mapping im RAM,
# damit derselbe Kontakt über mehrere Nachrichten denselben Platzhalter behält.
# NUR im Speicher, nie persistiert; begrenzt gegen unbegrenztes Wachstum.
def _log(text: str) -> None:
    """Ins Daemon-Log. `ensure_std_streams` leitet stdout bereits dorthin um."""
    import time
    print(f"[{time.strftime('%F %T')}] {text}", flush=True)


_CONV: dict = {}
_CONV_ORDER: list = []
MAX_CONV = 50            # max. gleichzeitige Konversationen (LRU)
MAX_ENTRIES = 300        # max. PII-Einträge je Konversation (dann frisch beginnen)


def _conv_mapping(conv_id: str) -> dict:
    if not conv_id:
        return {}
    m = _CONV.get(conv_id)
    if m is None:
        m = {"r2s": {}, "s2r": {}}
        _CONV[conv_id] = m
        _CONV_ORDER.append(conv_id)
        while len(_CONV_ORDER) > MAX_CONV:
            _CONV.pop(_CONV_ORDER.pop(0), None)
    else:  # LRU-Refresh
        _CONV_ORDER.remove(conv_id)
        _CONV_ORDER.append(conv_id)
    _verdraengen(conv_id, m)
    return m


def _verdraengen(conv_id: str, m: dict) -> int:
    """Beim Überlauf die ÄLTESTEN Einträge einzeln entfernen — nicht alles wegwerfen (#134).

    Vorher wurde die Zuordnung beim Überschreiten komplett geleert. Ab diesem Moment
    bekam derselbe Kontakt ein neues Surrogat: Das Modell hielt »Frauke Jäkel« und
    »Beate Kunz« für zwei Personen, obwohl beide Frau Zimmermann waren. Und schlimmer —
    die Surrogate aus der alten Zuordnung standen weiter im Gesprächsverlauf, ihre
    Übersetzung war weg. **Der Nutzer las dann einen erfundenen Namen für einen echten
    Menschen.** Genau der Fehler, den #88 gerade beseitigt hatte, nur durch eine andere Tür.

    Verdrängen macht den Fehler nicht unmöglich — ein verdrängtes Surrogat im Verlauf
    bleibt unübersetzbar. Aber es trifft dann den ältesten Kontakt statt aller auf einmal,
    und die aktiven Kontakte eines Gesprächs bleiben stabil.

    Python-Wörterbücher behalten die Einfügereihenfolge; das älteste steht vorn.
    """
    entfernt = 0
    while len(m["s2r"]) > MAX_ENTRIES:
        surrogat = next(iter(m["s2r"]))
        echt = m["s2r"].pop(surrogat, None)
        if echt is not None:
            m["r2s"].pop(echt, None)
        entfernt += 1
    if entfernt:
        # Bisher passierte der Überlauf vollkommen still. Gefunden habe ich ihn nur,
        # weil ich den Code gelesen habe — nicht, weil irgendwo etwas aufgefallen wäre.
        _log(f"Zuordnung »{conv_id[:24]}« voll: {entfernt} alte Einträge verdrängt "
             f"(Grenze {MAX_ENTRIES}). Ältere Platzhalter im Verlauf sind ab jetzt "
             f"nicht mehr rückübersetzbar.")
    return entfernt


def _handle(req: dict, pseudonym) -> dict:
    conv_id = req.get("conversation", "")
    mode = req.get("mode", "standard")
    # #134: Werkzeug-Ergebnisse bekommen eine EIGENE Zuordnung. Eine einzige gelesene
    # Kundenliste kann dutzende Namen enthalten und würde sonst die Gesprächs-Zuordnung
    # in wenigen Schritten volllaufen lassen — mit ihr die Namen, über die gerade
    # gesprochen wird. Getrennt bleibt der Gesprächsfaden stabil, auch wenn ein Agent
    # hundert Dateien liest.
    #
    # Preis: Ein Name, der in beiden vorkommt, hat zwei Platzhalter — für das Modell
    # zwei Personen. Das ist der harmlosere Fehler, weil beide korrekt zurückübersetzt
    # werden. Der andere Weg (eine Zuordnung, die überläuft) macht Platzhalter
    # unübersetzbar, und dann liest der Nutzer einen erfundenen Namen.
    if conv_id and mode == "werkzeug":
        conv_id += ":werkzeug"
    mapping = _conv_mapping(conv_id) if conv_id else req.get("mapping", {})
    allow, deny = req.get("allow", []), req.get("deny", [])
    out_texts, total = [], {}
    for txt in req.get("texts", []):
        p, mapping, st = pseudonym.pseudonymize(txt, mapping, mode, allow, deny)
        out_texts.append(p)
        for k, v in st.items():
            total[k] = total.get(k, 0) + v
    if conv_id:
        _CONV[conv_id] = mapping   # fortgeführten Stand zurückspeichern
    return {"texts": out_texts, "mapping": mapping, "stats": total}


def main():
    import pseudonym
    pseudonym._get_analyzer()   # Modell jetzt laden (einmalig), nicht erst bei Anfrage 1
    srv, token = _plat.ipc_bind()   # POSIX: AF_UNIX (0600); Windows: TCP-Loopback + Token
    print("pseudonym-daemon bereit", flush=True)
    while True:
        conn, _ = srv.accept()
        try:
            conn.settimeout(120)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            if not buf:
                continue
            req = json.loads(buf.split(b"\n", 1)[0])
            # Windows-TCP: nur mit gültigem Token bedienen (kein PII-Mapping-Leak an Fremdprozesse)
            if token and req.get("token") != token:
                resp = {"error": "unauthorized"}
            else:
                resp = _handle(req, pseudonym)
            conn.sendall((json.dumps(resp) + "\n").encode())
        except Exception as e:
            try:
                conn.sendall((json.dumps({"error": str(e)}) + "\n").encode())
            except OSError:
                pass
        finally:
            conn.close()


if __name__ == "__main__":
    main()
