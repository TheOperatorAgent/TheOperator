#!/usr/bin/env python3
"""Raum-Wächter (#98, stdlib-only) — prüft laufend die vier Türen zum Operator-Chat.

Warum laufend und nicht einmal
------------------------------
Beim Einrichten stimmen die Raum-Einstellungen. Danach kann sich das ändern: ein
Element-Update, ein Fehlgriff im Menü, eine versehentliche Einladung — oder jemand, der
gezielt mitlesen will. Einmal prüfen heißt, ab dem zweiten Tag nichts mehr zu wissen.

Die vier Türen
--------------
1. **Wer ist im Raum?** Der Owner-DM darf exakt zwei Mitglieder haben: dich und den Bot.
2. **Wie kommt man rein?** ``join_rule=invite``, ``guest_access=forbidden``,
   ``history_visibility=invited``.
3. **Welche Geräte nutzen das Bot-Konto?** Eine neue, unbekannte Sitzung ist ein Alarm.
4. **Kann sich jemand auf dem Homeserver neu registrieren?**

Was der Wächter selbst repariert — und was nicht
------------------------------------------------
Selbstheilung **nur** für die Raum-Einstellungen (Tür 2). Mitglieder wirft er nie hinaus
und Geräte meldet er nur ab, wenn du es sagst: Jemanden aus einem Raum zu entfernen ist
eine Entscheidung über Menschen, und die trifft der Nutzer (Bestätigungs-Prinzip aus #65).
Ein Wächter-Test prüft, dass in dieser Datei kein ``/kick``, ``/ban`` oder ``/leave`` steht.

Warum HTTP injiziert wird
-------------------------
``bewerten()`` ist rein — kein Netz, kein Zustand. Alles I/O läuft über ein Callable
``api(pfad, method, body)``. Damit lässt sich jede der vier Abweichungen im Test
nachstellen, ohne je einen Homeserver zu starten.

Fail-open fürs Chatten: Ein Fehler im Wächter darf den Operator nie stummschalten.
"""
import hashlib
import json
import os
import time
import urllib.parse

BOT_DIR = os.environ.get("OPERATOR_BOT_DIR", os.path.expanduser("~/.claude/matrix-bot"))
STATE_FILE = os.path.join(BOT_DIR, "run", "raumwaechter.json")
INTERVALL = 1800          # 30 Minuten

SOLL = {
    "m.room.join_rules":         ("join_rule", "invite"),
    "m.room.guest_access":       ("guest_access", "forbidden"),
    "m.room.history_visibility": ("history_visibility", "invited"),
}
# Klartext für die Chat-Meldung — der Nutzer soll nicht Matrix lernen müssen.
KLARTEXT = {
    "m.room.join_rules":         "Wer den Raum betreten darf",
    "m.room.guest_access":       "Ob Gäste ohne Konto mitlesen dürfen",
    "m.room.history_visibility": "Wie weit Neue in der Vergangenheit lesen dürfen",
}


# ---------------------------------------------------------------- Zustand --
def _state():
    try:
        d = json.load(open(STATE_FILE, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------- Bewertung (rein) --
def bewerten(zustand):
    """Aus einer Momentaufnahme eine Liste von Befunden machen. Kein I/O, kein Zustand —
    das ist der Teil, den die Tests vollständig durchspielen können.

    zustand = {
      "raeume": {room_id: {"mitglieder": {...}, "erwartet": {...},
                           "einstellungen": {typ: wert|None}}},
      "geraete": [id, ...] | None,          # None = konnte nicht ermittelt werden
      "bekannte_geraete": [id, ...] | None, # None = Basislinie fehlt noch (TOFU)
      "registrierung_offen": True|False|None,   # None = konnte ich nicht feststellen
    }
    """
    befunde = []
    for room_id, r in (zustand.get("raeume") or {}).items():
        erwartet = set(r.get("erwartet") or ())
        gefunden = r.get("mitglieder")
        if gefunden is not None:
            fremde = set(gefunden) - erwartet
            if fremde:
                befunde.append({
                    "art": "fremde_mitglieder", "raum": room_id,
                    "heilbar": False, "details": sorted(fremde),
                    "text": ("⚠️ In unserem Chat ist jemand, der nicht dazugehört: "
                             + ", ".join(sorted(fremde))
                             + ". Warst du das? 👉 Wenn nein: In Element den Raum öffnen "
                               "und die Person entfernen. Ich mache das nicht von selbst.")})
        for typ, (schluessel, soll) in SOLL.items():
            ist = (r.get("einstellungen") or {}).get(typ)
            if ist is None:
                continue                     # nicht lesbar → keine Behauptung aufstellen
            if ist != soll:
                befunde.append({
                    "art": "einstellung", "raum": room_id, "typ": typ,
                    "schluessel": schluessel, "soll": soll, "ist": ist, "heilbar": True,
                    "text": (f"🔧 Eine Raum-Einstellung stand offener als sie soll — "
                             f"{KLARTEXT[typ]}: »{ist}« statt »{soll}«.")})

    geraete, bekannt = zustand.get("geraete"), zustand.get("bekannte_geraete")
    if geraete is not None and bekannt is not None:
        neu = set(geraete) - set(bekannt)
        if neu:
            befunde.append({
                "art": "neue_geraete", "heilbar": False, "details": sorted(neu),
                "text": ("⚠️ Ein neues Gerät nutzt das Konto deines Operators "
                         f"({', '.join(sorted(neu))}). Warst du das? 👉 Wenn nein: In "
                         "Element unter Einstellungen › Sitzungen abmelden und das "
                         "Passwort ändern.")})

    if zustand.get("registrierung_offen") is True:
        befunde.append({
            "art": "registrierung", "heilbar": False,
            "text": ("⚠️ Auf deinem Matrix-Server kann sich gerade jeder neu registrieren. "
                     "👉 Das solltest du schließen — sonst kann sich jemand ein Konto "
                     "anlegen und dich anschreiben.")})
    return befunde


def fingerabdruck(befunde):
    """Damit dieselbe Lage nicht alle 30 Minuten erneut gemeldet wird. Nur die Art und
    die Betroffenen zählen, nicht der Meldetext."""
    kern = sorted(f"{b['art']}|{b.get('raum', '')}|{b.get('typ', '')}|"
                  f"{','.join(b.get('details', []))}" for b in befunde)
    return hashlib.sha256("\n".join(kern).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- Erhebung (I/O) --
def _raum_zustand(api, room_id, erwartet):
    r = {"erwartet": sorted(erwartet), "mitglieder": None, "einstellungen": {}}
    raum = urllib.parse.quote(room_id)
    try:
        m = api(f"/_matrix/client/v3/rooms/{raum}/joined_members")
        r["mitglieder"] = sorted((m or {}).get("joined", {}).keys())
    except Exception:
        pass                                  # nicht lesbar → keine Behauptung
    for typ, (schluessel, _soll) in SOLL.items():
        try:
            s = api(f"/_matrix/client/v3/rooms/{raum}/state/{typ}/")
            r["einstellungen"][typ] = (s or {}).get(schluessel)
        except Exception:
            r["einstellungen"][typ] = None
    return r


def _registrierung_offen(api):
    """Vorsichtig geprüft: über den LESENDEN Endpunkt ``register/available``.

    Der naheliegende Weg wäre ein ``POST /register`` mit leerem Body — der liefert die
    Anmelde-Flows. Aber das ist ein Schreib-Endpunkt, und etwas, das alle 30 Minuten
    läuft, sollte nichts anlegen können. Unbekannte Antwort heißt »konnte ich nicht
    feststellen« und nicht »ist offen«: Synapse, Conduit und matrix.org antworten hier
    unterschiedlich, und ein Fehlalarm alle 30 Minuten wäre schlimmer als keine Meldung.
    """
    name = "operator-probe-" + hashlib.sha256(str(int(time.time() // 86400))
                                              .encode()).hexdigest()[:12]
    try:
        antwort = api("/_matrix/client/v3/register/available?username=" + name)
    except Exception as e:
        code = getattr(e, "code", None)
        if code == 403:
            return False              # M_FORBIDDEN → Registrierung ist zu. Gut so.
        return None                   # alles andere: keine Aussage
    return True if (antwort or {}).get("available") is True else None


def erheben(api, raeume, bekannte_geraete):
    """Momentaufnahme einsammeln.

    ``raeume`` ist ``{room_id: erwartete_mitglieder}`` — oder, wenn ein Raum einen
    EIGENEN Zugang braucht, ``{room_id: {"erwartet": [...], "api": callable}}``.

    Warum das nötig ist: Agenten-Räume gehören eigenen Bot-Konten. Der Owner-Token kann
    sie gar nicht lesen — ein Versuch damit liefert 403 und der Wächter würde für jeden
    Agenten-Raum schweigen, ohne dass jemand merkt, dass er nichts prüft.
    """
    zustand = {"raeume": {}, "geraete": None, "bekannte_geraete": bekannte_geraete}
    for room_id, wert in (raeume or {}).items():
        if isinstance(wert, dict):
            zustand["raeume"][room_id] = _raum_zustand(
                wert.get("api") or api, room_id, wert.get("erwartet") or ())
        else:
            zustand["raeume"][room_id] = _raum_zustand(api, room_id, wert)
    try:
        zustand["geraete"] = sorted(d.get("device_id") for d
                                    in (api("/_matrix/client/v3/devices") or {}).get("devices", [])
                                    if d.get("device_id"))
    except Exception:
        pass
    zustand["registrierung_offen"] = _registrierung_offen(api)
    return zustand


def _api_fuer(raeume, room_id, api):
    """Der Zugang, mit dem dieser Raum gelesen/geschrieben wird (siehe erheben())."""
    wert = (raeume or {}).get(room_id)
    if isinstance(wert, dict) and wert.get("api"):
        return wert["api"]
    return api


def heilen(api, befunde, raeume=None):
    """Nur Raum-Einstellungen zurücksetzen. Gibt (geheilt, gescheitert) zurück.

    Realistischer Ausfall: In einem Owner-DM, den Element auf Nutzer-Seite angelegt hat,
    hat der Bot womöglich Machtstufe 0 — dann gibt das PUT eine 403. Das ist kein Fehler
    im Wächter, sondern eine Grenze, die ehrlich gemeldet gehört (und dank Dedup nur
    einmal, nicht alle 30 Minuten)."""
    geheilt, gescheitert = [], []
    for b in befunde:
        if b.get("art") != "einstellung":
            continue
        raum = urllib.parse.quote(b["raum"])
        raum_api = _api_fuer(raeume, b["raum"], api)
        try:
            raum_api(f"/_matrix/client/v3/rooms/{raum}/state/{b['typ']}/",
                method="PUT", body={b["schluessel"]: b["soll"]})
            geheilt.append(b)
        except Exception as e:
            b["heilfehler"] = str(e)[:120]
            gescheitert.append(b)
    return geheilt, gescheitert


# ---------------------------------------------------------------- Der Tick --
def tick(api, raeume, melden=lambda _t: None, log=print, jetzt=None):
    """Ein Durchgang. Gibt die Befunde zurück (auch die geheilten, fürs Log).

    Meldet im Chat nur, wenn die Lage NEU ist — ins Log geht immer alles.
    """
    jetzt = jetzt if jetzt is not None else time.time()
    st = _state()
    bekannt = st.get("geraete")

    zustand = erheben(api, raeume, bekannt)
    befunde = bewerten(zustand)

    # TOFU: Beim allerersten Lauf gelten die vorhandenen Geräte als bekannt. Ohne das
    # würde jede Bestandsinstallation am Tag eins über ihre eigenen Sitzungen schreien.
    if bekannt is None and zustand.get("geraete") is not None:
        st["geraete"] = zustand["geraete"]
        log(f"[Raum-Wächter] Basislinie gesetzt: {len(st['geraete'])} bekannte Gerät(e)")
    elif zustand.get("geraete") is not None:
        st["geraete"] = sorted(set(bekannt) | set(zustand["geraete"]))

    geheilt, gescheitert = heilen(api, befunde, raeume)
    for b in geheilt:
        log(f"[Raum-Wächter] repariert: {b['typ']} in {b['raum']} → {b['soll']}")

    offen = [b for b in befunde if b not in geheilt]
    fp = fingerabdruck(offen)
    if offen and fp != st.get("fingerabdruck"):
        texte = [b["text"] for b in offen]
        for b in gescheitert:
            texte.append("(Diese Einstellung konnte ich nicht selbst zurücksetzen — mir "
                         "fehlen die Rechte im Raum. 👉 Bitte in Element nachsehen.)")
        melden("\n\n".join(texte))
    elif geheilt:
        log(f"[Raum-Wächter] {len(geheilt)} Einstellung(en) selbst repariert — "
            "keine Meldung nötig, es ist ja wieder in Ordnung")
    st["fingerabdruck"] = fp
    st["geprueft_at"] = jetzt
    _write(st)
    return befunde
