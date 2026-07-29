#!/usr/bin/env python3
"""Raum-Brücke für das Operator-Dock (#90/#91): liest und schreibt den Owner-Chat.

Grundsätze (Sicherheitskonzept in Issue #94):
- EINZIGE Stelle, die für das Dock mit Matrix spricht. Kein Cache, keine eigene
  Nachrichten-Datei — read-through, sonst entstünde ein zweiter Datenbestand
  neben sessions.db, den retention.py mitpflegen müsste.
- Der Raum-Filter steht IN der Sync-Anfrage (serverseitig), nicht erst im Code:
  Ereignisse anderer Räume erreichen diesen Prozess gar nicht erst.
- Der Token bleibt in diesem Modul. Er wird nie zurückgegeben und nie geloggt.
- stdlib-only, wie listener.py — per Test abgesichert.

Dashboard-Eingaben werden als Bot-Nachricht mit einem eigenen Inhalts-Schlüssel
(MARKER) in den Raum gespiegelt. So bleibt das Handy synchron, und der Listener
erkennt sie sicher am Schlüssel statt an zerbrechlichem Text-Parsing. Nur wer den
Bot-Token besitzt, kann als Bot senden — der Marker ist also nicht fälschbar,
ohne dass ohnehin schon alles verloren wäre.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)
import secretstore  # noqa: E402

# Eigener Namensraum nach Matrix-Konvention (umgedrehte Domain).
MARKER = "bayern.vonaschenbrenner.operator.dashboard"


def _cfg():
    c = json.load(open(os.path.join(BOT_DIR, "credentials.json")))
    return c["homeserver"], c["room_id"], c["owner_id"], c["user_id"]


def _token():
    tok = secretstore.get("matrix-owner") or ""
    if not tok:
        c = json.load(open(os.path.join(BOT_DIR, "credentials.json")))
        alt = c.get("access_token", "")
        tok = "" if alt == "keychain" else alt
    return tok


def _api(pfad, body=None, method=None, timeout=35):
    hs = _cfg()[0]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(hs + pfad, data=data,
                                 method=method or ("POST" if data else "GET"),
                                 headers={"Authorization": "Bearer " + _token(),
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _eintrag(ev, owner, bot):
    """Ein Matrix-Ereignis → neutraler Dock-Eintrag. Text bleibt Rohtext —
    die Ausgabe-Seite (app.js) rendert ausschließlich als Text, nie als HTML."""
    if ev.get("type") != "m.room.message":
        return None
    inhalt = ev.get("content") or {}
    text = inhalt.get("body")
    if not isinstance(text, str) or not text:
        return None
    marker = inhalt.get(MARKER) or {}
    if marker.get("text"):
        # Dashboard-Spiegelung: Rohtext aus dem Marker, Anzeige-Prefix weglassen.
        return {"wer": "du", "quelle": "dashboard", "text": str(marker["text"])[:65536],
                "ts": ev.get("origin_server_ts", 0), "event_id": ev.get("event_id", "")}
    sender = ev.get("sender", "")
    if sender == owner:
        return {"wer": "du", "quelle": "handy", "text": text[:65536],
                "ts": ev.get("origin_server_ts", 0), "event_id": ev.get("event_id", "")}
    if sender == bot:
        return {"wer": "operator", "quelle": "operator", "text": text[:65536],
                "ts": ev.get("origin_server_ts", 0), "event_id": ev.get("event_id", "")}
    # Dritte dürfte es nicht geben (Raum-Wächter #98) — ehrlich kennzeichnen statt verstecken.
    return {"wer": "fremd", "quelle": sender, "text": text[:65536],
            "ts": ev.get("origin_server_ts", 0), "event_id": ev.get("event_id", "")}


def verlauf(limit=50):
    """Letzte Nachrichten, chronologisch. Kein Speichern — nur Durchreichen."""
    _, room, owner, bot = _cfg()
    q = urllib.parse.quote(room)
    data = _api(f"/_matrix/client/v3/rooms/{q}/messages?dir=b&limit={int(limit)}")
    aus = []
    for ev in data.get("chunk", []):
        e = _eintrag(ev, owner, bot)
        if e:
            aus.append(e)
    aus.reverse()
    return aus


def _sync_filter(room):
    # Serverseitige Minimierung: NUR dieser Raum, nur Timeline.
    return json.dumps({
        "room": {"rooms": [room], "timeline": {"limit": 30},
                 "state": {"types": []}, "ephemeral": {"types": []},
                 "account_data": {"types": []}},
        "presence": {"types": []}, "account_data": {"types": []}})


def sync_start():
    """Start-Marke holen (nichts Altes doppelt liefern)."""
    _, room, _, _ = _cfg()
    f = urllib.parse.quote(_sync_filter(room))
    return _api(f"/_matrix/client/v3/sync?timeout=0&filter={f}")["next_batch"]


def neue_seit(since, timeout_ms=25000):
    """Long-Poll auf neue Nachrichten. Gibt (einträge, nächste_marke) zurück."""
    _, room, owner, bot = _cfg()
    f = urllib.parse.quote(_sync_filter(room))
    s = urllib.parse.quote(since)
    data = _api(f"/_matrix/client/v3/sync?since={s}&timeout={int(timeout_ms)}&filter={f}",
                timeout=timeout_ms / 1000 + 15)
    events = (data.get("rooms", {}).get("join", {}).get(room, {})
              .get("timeline", {}).get("events", []))
    aus = [e for e in (_eintrag(ev, owner, bot) for ev in events) if e]
    return aus, data["next_batch"]


def senden_dashboard(text):
    """Dashboard-Eingabe in den Raum spiegeln. Ehrlich gekennzeichnet (🖥️-Prefix
    im Anzeigetext), Rohtext im Marker — der Listener verarbeitet den Marker,
    das Handy zeigt die Spiegelung. Gibt die event_id zurück."""
    text = (text or "").strip()
    if not text:
        raise ValueError("leer")
    if len(text) > 8000:
        raise ValueError("zu lang (max. 8000 Zeichen)")
    _, room, _, _ = _cfg()
    q = urllib.parse.quote(room)
    txn = f"dock{int(time.time() * 1000)}"
    body = {"msgtype": "m.text", "body": "🖥️ " + text, MARKER: {"text": text}}
    r = _api(f"/_matrix/client/v3/rooms/{q}/send/m.room.message/{txn}",
             body=body, method="PUT")
    return r.get("event_id", "")
