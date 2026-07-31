#!/usr/bin/env python3
"""Operator M365 MCP-Server (stdio) — Standard-MCP des Operators für Microsoft 365.

Eigenbau statt Fremd-Server (Entscheidung 2026-07-21, siehe RECHERCHE im Dev-Repo):
- nutzt die auto-registrierte Connector-App (app-only Client-Credentials aus tokens.py)
- JEDES Tool prüft vorher die im Dashboard vergebene Regler-Matrix (Least Privilege)
- Tools sind bewusst auf /users/{id}-Pfade gebaut (app-only-tauglich, kein /me)

Start (macht der Listener automatisch via --mcp-config):
  ~/.claude/matrix-bot/dashboard/venv/bin/python3 ~/.claude/matrix-bot/mcp_m365.py
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.parse

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
sys.path.insert(0, os.path.join(BOT_DIR, "dashboard"))

import requests  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

import tokens  # noqa: E402
try:
    import reid  # noqa: E402  (Re-ID der Pseudonymisierungs-Surrogate)
except ImportError:
    reid = None


def _rid(s):
    return reid.reidentify(s) if reid and s else s

GRAPH = "https://graph.microsoft.com/v1.0"
mcp = FastMCP("m365")

# ---------------------------------------------------- Lade-nach-Bedarf (#121) --
# Jedes Werkzeug kostet Platz im Prompt. Bei ~45 Werkzeugen (Stand nach #119) wird jede
# Antwort spürbar langsamer — das war Anfang Juli schon einmal der Fall (#99–#102).
# Deshalb: Werkzeuge von Diensten, die im Dashboard AUS sind, gar nicht erst anbieten.
#
# WICHTIG — das hier ist KEINE Sicherheitsgrenze. Die ist und bleibt `require()` im
# Rumpf jeder Funktion. Wäre die Tabelle unten falsch, sähe das Modell ein Werkzeug, das
# `require()` anschließend trotzdem ablehnt. Ein Fehler kostet also Sichtbarkeit, nie ein
# Recht. Die Deckungsgleichheit erzwingt ein AST-Test, der auch #119 überlebt.
_BEDARF = {}          # werkzeugname -> (dienst, modus)


def werkzeug(dienst, modus="read"):
    """Wie @mcp.tool(), merkt sich aber Dienst + Regler AM Werkzeug.

    Registriert IMMER — so sehen Import und Tests die vollständige Liste. Beschnitten
    wird erst beim Serverstart (`_beschneiden`), also nur im echten stdio-Betrieb."""
    def deko(fn):
        _BEDARF[fn.__name__] = (dienst, modus)
        return mcp.tool()(fn)
    return deko


def aktive_werkzeuge(perms):
    """Rechte-Matrix → Menge der Werkzeugnamen, die bleiben. Rein, ohne I/O.

    »read« ist auch durch »write« erfüllt: Wer schreiben darf, darf erst recht lesen —
    genau wie `m365_setup.matrix_to_values()` es beim Anfordern der Rechte handhabt."""
    aktiv = set()
    for name, (dienst, modus) in _BEDARF.items():
        regler = (perms or {}).get(dienst) or {}
        if regler.get(modus) or (modus == "read" and regler.get("write")):
            aktiv.add(name)
    return aktiv


def _beschneiden():
    """Beim Start alles entfernen, wofür keine Rechte gesetzt sind.

    Fail-soft: Ist M365 nicht verbunden oder die Konfiguration kaputt, bleiben null
    Dienste übrig — das ist richtig (ohne Rechte geht ohnehin nichts) und der Nutzer
    bekommt über `m365_hilfe` eine Antwort statt Schweigen.

    Ausgaben NUR nach stderr: stdout ist bei stdio-MCP der Protokollkanal, ein `print()`
    hier zerschießt die Verbindung."""
    try:
        perms = conn().get("permissions", {})
    except Exception as e:
        print(f"[m365] Rechte nicht lesbar ({e}) — nur Hilfe-Werkzeug aktiv.",
              file=sys.stderr)
        perms = {}
    aktiv = aktive_werkzeuge(perms)
    for name in sorted(set(_BEDARF) - aktiv):
        try:
            mcp.remove_tool(name)
        except Exception:
            pass
    print(f"[m365] {len(aktiv)} von {len(_BEDARF)} Werkzeugen aktiv.", file=sys.stderr)

DIENST_DE = {"mail": "Mail", "calendar": "Kalender", "onedrive": "OneDrive",
             "sharepoint": "SharePoint", "planner": "Planner", "teams": "Teams",
             "status": "Status & Berichte"}

# Graph-Zustand -> Klartext + Ampel (#117). Microsoft liefert englische CamelCase-Werte.
ZUSTAND_DE = {
    "serviceOperational": ("läuft normal", "🟢"),
    "investigating": ("Microsoft untersucht etwas", "🟡"),
    "restoringService": ("Wiederherstellung läuft", "🟡"),
    "verifyingService": ("Microsoft prüft die Behebung", "🟡"),
    "serviceRestored": ("wieder hergestellt", "🟢"),
    "postIncidentReviewPublished": ("Nachbericht veröffentlicht", "🟢"),
    "serviceDegradation": ("eingeschränkt", "🔴"),
    "serviceInterruption": ("Störung", "🔴"),
    "extendedRecovery": ("erholt sich noch", "🟡"),
    "falsePositive": ("Fehlalarm", "🟢"),
    "investigationSuspended": ("Untersuchung ausgesetzt", "🟡"),
}


def zustand(wert):
    """Graph-Zustand in Klartext + Ampel — unbekannte Werte werden durchgereicht."""
    return ZUSTAND_DE.get(wert, (wert or "unbekannt", "⚪"))


def conn():
    p = os.path.join(BOT_DIR, "connections", "m365.json")
    if not os.path.exists(p):
        raise RuntimeError("M365 ist nicht verbunden — im Dashboard unter 'Microsoft 365' einrichten.")
    return json.load(open(p))


def require(c, svc, mode):
    if not c.get("permissions", {}).get(svc, {}).get(mode):
        regler = "Lesen" if mode == "read" else "Schreiben"
        raise RuntimeError(f"Fehlendes Recht: {DIENST_DE[svc]} › {regler} — "
                           f"im Dashboard unter 'Microsoft 365' aktivieren.")


def token(c, frisch=False):
    cached = tokens.load("m365_cc_token")
    if not frisch and cached and cached.get("expires_at", 0) > time.time():
        return cached["access_token"]
    r = requests.post(
        f"https://login.microsoftonline.com/{c['tenant_id']}/oauth2/v2.0/token",
        data={"client_id": c["app_client_id"], "client_secret": tokens.load("m365_secret"),
              "scope": "https://graph.microsoft.com/.default",
              "grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status()
    t = r.json()
    tokens.save("m365_cc_token", {"access_token": t["access_token"],
                                  "expires_at": time.time() + t["expires_in"] - 120})
    return t["access_token"]


def g(c, method, path, payload=None):
    """Graph-Aufruf. Bei 403 EINMAL mit frischem Token wiederholen: der Token traegt die
    Rechte in sich, also ist ein zwischengespeicherter Token nach einer Rechte-Aenderung
    veraltet — Graph antwortet dann mit »403 UnknownError« ohne Text (real erlebt 30.07.)."""
    def ruf(frisch=False):
        return requests.request(method, GRAPH + path,
                                headers={"Authorization": "Bearer " + token(c, frisch)},
                                json=payload, timeout=60)
    r = ruf()
    if r.status_code == 403:
        r = ruf(frisch=True)
    if r.status_code >= 400:
        hinweis = ""
        if r.status_code == 403:
            hinweis = ("\n👉 Microsoft verweigert den Zugriff. Prüfe im Dashboard unter "
                       "'Microsoft 365', ob der passende Regler an ist und ob du danach "
                       "auf 'Rechte aktualisieren' geklickt hast.")
        raise RuntimeError(f"Graph {r.status_code}: {r.text[:300]}{hinweis}")
    return r.json() if r.text else {}


def g_text(c, path):
    """Wie g(), aber fuer Endpunkte, die KEIN JSON liefern (Microsofts Nutzungs-Berichte
    kommen ausschliesslich als CSV). Gleicher 403-Wiederholversuch."""
    def ruf(frisch=False):
        return requests.get(GRAPH + path,
                            headers={"Authorization": "Bearer " + token(c, frisch)},
                            timeout=60)
    r = ruf()
    if r.status_code == 403:
        r = ruf(frisch=True)
    if r.status_code >= 400:
        raise RuntimeError(f"Graph {r.status_code}: {r.text[:300]}")
    return r.text


def user(c):
    u = c.get("primary_user")
    if not u:
        raise RuntimeError("Kein Benutzer gewählt — im Dashboard unter 'Microsoft 365' › 'Wessen Daten?' eintragen.")
    return u


def audit(action, target):
    try:
        with open(os.path.join(BOT_DIR, "audit.log"), "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "actor": "mcp_m365", "action": action,
                                "target": target, "ok": True}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _mail_zeilen(mails):
    """Einheitliche Kurzdarstellung — das Suffix in [] ist die Kennung für mail_read."""
    return "\n".join(
        f"[{m['id'][-12:]}] {m['receivedDateTime'][:16]} {'  ' if m.get('isRead') else '● '}"
        f"{m.get('from', {}).get('emailAddress', {}).get('address', '?')} | {m.get('subject', '')}"
        for m in mails)


@werkzeug("mail", "read")
def mail_list(count: int = 10, ordner: str = "") -> str:
    """Letzte Mails auflisten (ID, Datum, Absender, Betreff). ordner: leer = Posteingang;
    sonst ein Name wie inbox, sentitems, drafts, archive oder eine Kennung aus mail_ordner."""
    c = conn(); require(c, "mail", "read"); u = user(c)
    ordner = _rid(ordner or "").strip()
    basis = f"/users/{u}/mailFolders/{urllib.parse.quote(ordner)}/messages" if ordner \
        else f"/users/{u}/messages"
    res = g(c, "GET", f"{basis}?$top={min(count, 50)}&$select=id,subject,from,receivedDateTime,isRead&$orderby=receivedDateTime desc")
    audit("mail_list", ordner or u)
    return _mail_zeilen(res.get("value", [])) or "(keine Mails)"


def _resolve_mail(c, u, mail_id, select):
    """Mail per ID finden: Surrogate zuerst auflösen (Tool-Re-ID-Brücke — die
    Pseudonymisierung kann IDs durch Platzhalter ersetzt haben), lange IDs direkt
    per Graph laden (findet auch Unterordner-Mails), sonst Suffix aus mail_list."""
    mail_id = _rid(mail_id or "").strip()
    if len(mail_id) > 40:                     # volle Graph-ID → direkter Zugriff
        try:
            return g(c, "GET", f"/users/{u}/messages/{mail_id}?$select={select}")
        except Exception:
            pass
    res = g(c, "GET", f"/users/{u}/messages?$top=50&$select={select}"
                      "&$orderby=receivedDateTime desc")
    return next((m for m in res.get("value", []) if m["id"].endswith(mail_id)), None)


@werkzeug("mail", "read")
def mail_read(mail_id: str) -> str:
    """Eine Mail im Volltext lesen. mail_id: Suffix aus mail_list ODER die ID aus einem
    Mail-Watch-Ereignis (exakt übernehmen — auch wenn sie wie ein Name aussieht,
    das ist ein Pseudonymisierungs-Platzhalter und wird automatisch aufgelöst)."""
    c = conn(); require(c, "mail", "read"); u = user(c)
    msg = _resolve_mail(c, u, mail_id, "id,subject,from,body,receivedDateTime")
    if not msg:
        return "Mail-ID nicht gefunden — Suffix aus mail_list verwenden."
    body = re.sub(r"<[^>]+>", " ", msg["body"]["content"])
    audit("mail_read", mail_id)
    return f"Von: {msg.get('from', {}).get('emailAddress', {}).get('address', '?')}\nBetreff: {msg.get('subject', '')}\nDatum: {msg['receivedDateTime'][:16]}\n\n{body[:6000]}"


@werkzeug("mail", "read")
def mail_attachments(mail_id: str) -> str:
    """Anhänge einer Mail lesen: Name, Typ, Größe und — wo möglich — extrahierter Text
    (txt/csv/json/html direkt, PDF via pypdf). mail_id wie bei mail_read (Ereignis-IDs
    exakt übernehmen, Platzhalter werden automatisch aufgelöst)."""
    c = conn(); require(c, "mail", "read"); u = user(c)
    msg = _resolve_mail(c, u, mail_id, "id,subject")
    if not msg:
        return "Mail-ID nicht gefunden — Suffix aus mail_list verwenden."
    atts = g(c, "GET", f"/users/{u}/messages/{msg['id']}/attachments").get("value", [])
    audit("mail_attachments", mail_id)
    if not atts:
        return "(keine Anhänge)"
    import base64
    import io
    out = []
    for a in atts:
        name = a.get("name", "?")
        ctype = a.get("contentType", "?")
        size = a.get("size", 0)
        head = f"— {name} ({ctype}, {size // 1024} KB)"
        blob = a.get("contentBytes")
        if not blob:
            out.append(head + " [Inhalt nicht eingebettet — z. B. Element-Anhang]")
            continue
        raw = base64.b64decode(blob)
        text = None
        try:
            if name.lower().endswith(".pdf") or "pdf" in ctype:
                from pypdf import PdfReader
                pages = PdfReader(io.BytesIO(raw)).pages[:15]
                text = "\n".join(p.extract_text() or "" for p in pages)
            elif any(name.lower().endswith(e) for e in
                     (".txt", ".csv", ".json", ".md", ".log", ".xml", ".html")) \
                    or ctype.startswith("text/"):
                text = raw.decode("utf-8", "replace")
                if name.lower().endswith((".html", ".htm")):
                    text = re.sub(r"<[^>]+>", " ", text)
        except Exception as e:
            out.append(head + f" [Extraktion fehlgeschlagen: {e}]")
            continue
        if text and text.strip():
            out.append(head + "\n" + text.strip()[:4000])
        else:
            out.append(head + " [kein extrahierbarer Text — Binärformat]")
    return "\n\n".join(out)


@werkzeug("mail", "write")
def mail_send(to: str, subject: str, text: str) -> str:
    """Eine Mail im Namen des Nutzers senden (braucht Mail › Schreiben)."""
    c = conn(); require(c, "mail", "write"); u = user(c)
    # Pseudonymisierungs-Surrogate → echte Werte, bevor die Mail real rausgeht
    to, subject, text = _rid(to), _rid(subject), _rid(text)
    g(c, "POST", f"/users/{u}/sendMail", {"message": {
        "subject": subject, "body": {"contentType": "Text", "content": text},
        "toRecipients": [{"emailAddress": {"address": to}}]}})
    audit("mail_send", to)
    return f"Mail an {to} gesendet."


@werkzeug("mail", "read")
def mail_suchen(text: str, anzahl: int = 25) -> str:
    """Das ganze Postfach durchsuchen — nicht nur die letzten Mails. Nutze das, sobald
    jemand nach einer älteren Mail fragt (»die Mail von Petra über die Rechnung«).
    text kann auch gezielt sein: »from:petra@firma.de rechnung« oder »subject:Angebot«."""
    c = conn(); require(c, "mail", "read"); u = user(c)
    text = _rid(text or "").strip()
    if not text:
        return "Suchbegriff fehlt."
    # $search und $orderby dürfen bei Graph NICHT zusammen — Microsoft sortiert nach Relevanz.
    suche = urllib.parse.quote(f'"{text}"')
    res = g(c, "GET", f"/users/{u}/messages?$search={suche}&$top={min(anzahl, 50)}"
                      "&$select=id,subject,from,receivedDateTime,isRead")
    audit("mail_suchen", text[:60])
    treffer = res.get("value", [])
    if not treffer:
        return f"Keine Mail zu »{text}« gefunden."
    return f"{len(treffer)} Treffer (nach Relevanz):\n" + _mail_zeilen(treffer)


@werkzeug("mail", "read")
def mail_ordner(unter: str = "") -> str:
    """Mail-Ordner mit Anzahl und Ungelesenen auflisten. unter: leer = oberste Ebene,
    sonst eine Ordner-Kennung, um dessen Unterordner zu sehen."""
    c = conn(); require(c, "mail", "read"); u = user(c)
    unter = _rid(unter or "").strip()
    pfad = f"/users/{u}/mailFolders/{urllib.parse.quote(unter)}/childFolders" if unter \
        else f"/users/{u}/mailFolders"
    res = g(c, "GET", f"{pfad}?$top=50&$select=id,displayName,totalItemCount,unreadItemCount,childFolderCount")
    audit("mail_ordner", unter or "/")
    zeilen = []
    for f in res.get("value", []):
        mehr = f" · {f['childFolderCount']} Unterordner" if f.get("childFolderCount") else ""
        zeilen.append(f"[{f['id'][-12:]}] {f.get('displayName', '?')}: "
                      f"{f.get('totalItemCount', 0)} Mails, "
                      f"{f.get('unreadItemCount', 0)} ungelesen{mehr}")
    return "\n".join(zeilen) or "(keine Ordner)"


@werkzeug("mail", "write")
def mail_antworten(mail_id: str, text: str, allen: bool = False) -> str:
    """Auf eine Mail ANTWORTEN, statt eine neue zu schreiben — so bleibt der Gesprächs-
    faden zusammen. allen=True antwortet allen Empfängern. Braucht Mail › Schreiben."""
    c = conn(); require(c, "mail", "write"); u = user(c)
    msg = _resolve_mail(c, u, mail_id, "id,subject,from")
    if not msg:
        return "Mail-ID nicht gefunden — Suffix aus mail_list oder mail_suchen verwenden."
    # Surrogate → echte Werte, BEVOR die Antwort real rausgeht
    aktion = "replyAll" if allen else "reply"
    g(c, "POST", f"/users/{u}/messages/{msg['id']}/{aktion}", {"comment": _rid(text)})
    audit(f"mail_{aktion}", msg.get("subject", "")[:60])
    wem = "allen Beteiligten" if allen else \
        msg.get("from", {}).get("emailAddress", {}).get("address", "dem Absender")
    return f"Antwort auf »{msg.get('subject', '')}« an {wem} gesendet."


@werkzeug("mail", "write")
def mail_weiterleiten(mail_id: str, an: str, text: str = "") -> str:
    """Eine Mail weiterleiten (mehrere Empfänger mit Komma trennen).
    Braucht Mail › Schreiben."""
    c = conn(); require(c, "mail", "write"); u = user(c)
    msg = _resolve_mail(c, u, mail_id, "id,subject")
    if not msg:
        return "Mail-ID nicht gefunden — Suffix aus mail_list oder mail_suchen verwenden."
    adressen = [a.strip() for a in _rid(an or "").split(",") if a.strip()]
    if not adressen:
        return "Kein Empfänger angegeben."
    g(c, "POST", f"/users/{u}/messages/{msg['id']}/forward",
      {"comment": _rid(text), "toRecipients":
          [{"emailAddress": {"address": a}} for a in adressen]})
    audit("mail_weiterleiten", ", ".join(adressen))
    return f"»{msg.get('subject', '')}« an {', '.join(adressen)} weitergeleitet."


@werkzeug("calendar", "read")
def calendar_list(days: int = 7) -> str:
    """Termine der nächsten Tage auflisten."""
    c = conn(); require(c, "calendar", "read"); u = user(c)
    start = time.strftime("%Y-%m-%dT00:00:00")
    end = time.strftime("%Y-%m-%dT23:59:59", time.localtime(time.time() + days * 86400))
    res = g(c, "GET", f"/users/{u}/calendarView?startDateTime={start}&endDateTime={end}&$top=30&$select=id,subject,start,end,location&$orderby=start/dateTime")
    audit("calendar_list", u)
    # Das Suffix in [] ist die Kennung für kalender_verschieben / kalender_absagen.
    return "\n".join(
        f"[{e['id'][-12:]}] {e['start']['dateTime'][:16]} – {e['end']['dateTime'][11:16]} | {e.get('subject', '')}"
        + (f" ({e['location']['displayName']})" if e.get("location", {}).get("displayName") else "")
        for e in res.get("value", [])) or "(keine Termine)"


@werkzeug("calendar", "write")
def calendar_add(subject: str, start_iso: str, end_iso: str) -> str:
    """Termin anlegen, Zeiten als ISO z. B. 2026-07-22T14:00:00 (braucht Kalender › Schreiben)."""
    c = conn(); require(c, "calendar", "write"); u = user(c)
    subject = _rid(subject)
    g(c, "POST", f"/users/{u}/events", {"subject": subject,
        "start": {"dateTime": start_iso, "timeZone": "Europe/Berlin"},
        "end": {"dateTime": end_iso, "timeZone": "Europe/Berlin"}})
    audit("calendar_add", subject)
    return f"Termin '{subject}' angelegt."


# ------------------------------------------------- Termin-Koordination (#118) --
# Das eigentliche Kunststueck eines Assistenten ist nicht »Termine auflisten«, sondern
# »wann haben alle Zeit«. Graph liefert dafuer eine Ziffernkette (availabilityView);
# wir rechnen daraus echte Zeitfenster, statt dem Modell Ziffern zuzumuten.

# Ziffern der availabilityView laut Microsoft-Doku
FREI_ZIFFERN = "0"          # frei oder »arbeitet auswaerts«
BELEGT_DE = {"1": "unter Vorbehalt", "2": "belegt", "3": "abwesend", "4": "unbekannt"}


def _freie_fenster(views, start, minuten):
    """Aus den Ziffernketten aller Personen die gemeinsamen freien Fenster berechnen.

    Ein Fenster ist frei, wenn es bei JEDER Person frei ist. Fehlt bei einer Person
    ein Zeichen (kürzere Kette), gilt das bewusst als NICHT frei — lieber ein Termin
    zu wenig vorgeschlagen als einer, der kollidiert."""
    if not views:
        return []
    laenge = max(len(v) for v in views)
    fenster, lauf = [], None
    for i in range(laenge):
        frei = all(i < len(v) and v[i] in FREI_ZIFFERN for v in views)
        if frei and lauf is None:
            lauf = i
        elif not frei and lauf is not None:
            fenster.append((lauf, i)); lauf = None
    if lauf is not None:
        fenster.append((lauf, laenge))
    return [(start + datetime.timedelta(minutes=a * minuten),
             start + datetime.timedelta(minutes=b * minuten)) for a, b in fenster]


@werkzeug("calendar", "read")
def kalender_freibelegt(personen: str, von_iso: str, bis_iso: str,
                        raster_min: int = 30) -> str:
    """WANN HABEN ALLE ZEIT? Frei/Belegt mehrerer Personen abfragen und die gemeinsamen
    freien Fenster nennen. personen: E-Mail-Adressen mit Komma getrennt.
    Zeiten als ISO, z. B. 2026-08-03T08:00:00. raster_min: Taktung (5–1440).

    Zeigt bewusst nur frei/belegt — nicht, WAS die anderen vorhaben."""
    c = conn(); require(c, "calendar", "read")
    adressen = [a.strip() for a in _rid(personen or "").split(",") if a.strip()][:20]
    if not adressen:
        return "Keine E-Mail-Adressen angegeben."
    raster = max(5, min(int(raster_min or 30), 1440))
    try:
        start = datetime.datetime.fromisoformat(von_iso)
        ende = datetime.datetime.fromisoformat(bis_iso)
    except ValueError:
        return "Zeiten bitte als ISO angeben, z. B. 2026-08-03T08:00:00."
    if ende <= start:
        return "Das Ende liegt vor dem Anfang."
    u = user(c)
    res = g(c, "POST", f"/users/{u}/calendar/getSchedule", {
        "schedules": adressen,
        "startTime": {"dateTime": start.isoformat(), "timeZone": "Europe/Berlin"},
        "endTime": {"dateTime": ende.isoformat(), "timeZone": "Europe/Berlin"},
        "availabilityViewInterval": raster})
    audit("kalender_freibelegt", ", ".join(adressen))
    eintraege = res.get("value", [])
    if not eintraege:
        return "Microsoft hat keine Verfügbarkeit geliefert."
    zeilen, views, fehler = [], [], []
    for e in eintraege:
        wer = e.get("scheduleId", "?")
        if e.get("error"):
            fehler.append(f"{wer}: {e['error'].get('message', 'kein Zugriff')}")
            continue
        view = e.get("availabilityView", "")
        views.append(view)
        belegt = sorted({BELEGT_DE[z] for z in view if z in BELEGT_DE})
        zeilen.append(f"{wer}: " + (", ".join(belegt) if belegt else "komplett frei"))
    out = []
    if views:
        fenster = [(a, b) for a, b in _freie_fenster(views, start, raster)
                   if (b - a).total_seconds() >= raster * 60]
        if fenster:
            out.append("Gemeinsam frei:")
            out += [f"  {a.strftime('%a %d.%m. %H:%M')} – {b.strftime('%H:%M')}"
                    f"  ({int((b - a).total_seconds() // 60)} Min)" for a, b in fenster]
        else:
            out.append("Kein gemeinsames freies Fenster in diesem Zeitraum.")
    out.append("")
    out += zeilen
    if fehler:
        out.append("")
        out.append("Nicht abfragbar: " + "; ".join(fehler))
    return "\n".join(out)


def _resolve_event(c, u, tid, tage=60):
    """Termin per ID finden — analog zu _resolve_mail: Surrogat auflösen, lange IDs
    direkt laden, kurze Suffixe im Kalender der nächsten Wochen suchen."""
    tid = _rid(tid or "").strip()
    if len(tid) > 40:
        try:
            return g(c, "GET", f"/users/{u}/events/{tid}?$select=id,subject,start,end,organizer")
        except Exception:
            pass
    start = time.strftime("%Y-%m-%dT00:00:00")
    ende = time.strftime("%Y-%m-%dT23:59:59", time.localtime(time.time() + tage * 86400))
    res = g(c, "GET", f"/users/{u}/calendarView?startDateTime={start}&endDateTime={ende}"
                      "&$top=200&$select=id,subject,start,end,organizer")
    return next((e for e in res.get("value", []) if e["id"].endswith(tid)), None)


@werkzeug("calendar", "write")
def kalender_verschieben(termin_id: str, start_iso: str, ende_iso: str) -> str:
    """Einen Termin auf eine neue Zeit legen. termin_id: Suffix aus calendar_list.
    Braucht Kalender › Schreiben."""
    c = conn(); require(c, "calendar", "write"); u = user(c)
    ev = _resolve_event(c, u, termin_id)
    if not ev:
        return "Termin nicht gefunden — Suffix aus calendar_list verwenden."
    g(c, "PATCH", f"/users/{u}/events/{ev['id']}", {
        "start": {"dateTime": start_iso, "timeZone": "Europe/Berlin"},
        "end": {"dateTime": ende_iso, "timeZone": "Europe/Berlin"}})
    audit("kalender_verschieben", ev.get("subject", "")[:60])
    return f"»{ev.get('subject', '')}« liegt jetzt am {start_iso[:16]}."


@werkzeug("calendar", "write")
def kalender_absagen(termin_id: str, grund: str = "") -> str:
    """Einen Termin absagen und die Teilnehmer benachrichtigen. Geht nur bei Terminen,
    die der Nutzer selbst eingeladen hat. Braucht Kalender › Schreiben."""
    c = conn(); require(c, "calendar", "write"); u = user(c)
    ev = _resolve_event(c, u, termin_id)
    if not ev:
        return "Termin nicht gefunden — Suffix aus calendar_list verwenden."
    g(c, "POST", f"/users/{u}/events/{ev['id']}/cancel", {"Comment": _rid(grund)})
    audit("kalender_absagen", ev.get("subject", "")[:60])
    return f"»{ev.get('subject', '')}« wurde abgesagt, die Teilnehmer sind benachrichtigt."


@werkzeug("onedrive", "read")
def files_list(path: str = "") -> str:
    """OneDrive-Ordner auflisten (leer = Wurzel)."""
    c = conn(); require(c, "onedrive", "read"); u = user(c)
    p = f":/{path}:" if path else ""
    res = g(c, "GET", f"/users/{u}/drive/root{p}/children?$top=50&$select=name,size,folder,lastModifiedDateTime")
    audit("files_list", path or "/")
    return "\n".join(
        f"{'[Ordner]' if 'folder' in f else '[Datei] '} {f['name']} ({f.get('size', 0)} B, {f['lastModifiedDateTime'][:10]})"
        for f in res.get("value", [])) or "(leer)"


@werkzeug("sharepoint", "read")
def sharepoint_search(query: str = "*") -> str:
    """SharePoint-Sites suchen/auflisten."""
    c = conn(); require(c, "sharepoint", "read")
    res = g(c, "GET", f"/sites?search={query}&$select=displayName,webUrl")
    audit("sharepoint_search", query)
    return "\n".join(f"{s.get('displayName', '?')} — {s.get('webUrl', '')}"
                     for s in res.get("value", [])) or "(keine Sites)"


@werkzeug("planner", "read")
def planner_plans() -> str:
    """Planner-Pläne aller Gruppen auflisten."""
    c = conn(); require(c, "planner", "read")
    out = []
    for grp in g(c, "GET", "/groups?$top=20&$select=id,displayName").get("value", []):
        try:
            for p in g(c, "GET", f"/groups/{grp['id']}/planner/plans?$select=id,title").get("value", []):
                out.append(f"{grp['displayName']} › {p['title']} [{p['id']}]")
        except RuntimeError:
            continue
    audit("planner_plans", "")
    return "\n".join(out) or "(keine Pläne)"


@werkzeug("teams", "read")
def teams_list() -> str:
    """Teams und ihre Kanäle auflisten (nur Basisdaten)."""
    c = conn(); require(c, "teams", "read")
    out = []
    for t in g(c, "GET", "/teams?$top=20&$select=id,displayName").get("value", []):
        out.append(f"Team: {t['displayName']}")
        try:
            out += [f"  # {x['displayName']}" for x in
                    g(c, "GET", f"/teams/{t['id']}/channels?$select=displayName").get("value", [])]
        except RuntimeError:
            continue
    audit("teams_list", "")
    return "\n".join(out) or "(keine Teams)"


# ---------------------------------------------------------- Status & Berichte (#117) --
# Alles hier ist reines Nachschauen: es gibt keinen Schreib-Regler, und es geht ohne
# den delegierten Anmeldeweg (app-only reicht). Deshalb der erste Ausbau-Schritt.


@werkzeug("status", "read")
def m365_status() -> str:
    """Läuft Microsoft überhaupt? Zustand aller abonnierten Dienste (Exchange, Teams,
    SharePoint …) mit Ampel. Nutze das, wenn der Nutzer fragt, ob etwas gestört ist,
    oder wenn ein anderes M365-Werkzeug unerwartet scheitert."""
    c = conn(); require(c, "status", "read")
    res = g(c, "GET", "/admin/serviceAnnouncement/healthOverviews")
    audit("m365_status", "")
    # »eingeschränkt« allein ist zu wenig (Michi, 30.07.): zu jeder nicht-grünen
    # Zeile gehört dazu, WAS klemmt — Titel der offenen Störung + Kennung.
    probleme = {}
    try:
        st = g(c, "GET", "/admin/serviceAnnouncement/issues"
                         "?$filter=isResolved eq false"
                         "&$select=id,title,service&$top=50")
        for i in st.get("value", []):
            probleme.setdefault(i.get("service", "?"), []).append(
                f"   ↳ {i.get('title', '')} [{i.get('id', '?')}]")
    except Exception:
        probleme = {}
    zeilen = []
    for d in sorted(res.get("value", []), key=lambda x: x.get("service", "")):
        text, ampel = zustand(d.get("status"))
        name = d.get("service", "?")
        zeilen.append(f"{ampel} {name}: {text}")
        if ampel != "🟢":
            zeilen += probleme.get(name, [])[:3]
    if not zeilen:
        return "(keine Dienste gemeldet)"
    schlecht = [z for z in zeilen if z.startswith(("🔴", "🟡"))]
    kopf = "Alles läuft normal." if not schlecht else f"{len(schlecht)} Dienst(e) nicht normal."
    return kopf + "\n" + "\n".join(zeilen)


@werkzeug("status", "read")
def m365_stoerungen(tage: int = 7) -> str:
    """Offene und kürzlich behobene Störungen der letzten Tage (mit Microsoft-Kennung
    wie MO123456), damit man dem Nutzer sagen kann, was los ist."""
    c = conn(); require(c, "status", "read")
    seit = time.strftime("%Y-%m-%dT00:00:00Z",
                         time.gmtime(time.time() - max(tage, 1) * 86400))
    res = g(c, "GET", "/admin/serviceAnnouncement/issues"
                      f"?$filter=lastModifiedDateTime ge {seit}"
                      "&$select=id,title,service,classification,status,startDateTime,isResolved"
                      "&$orderby=lastModifiedDateTime desc&$top=25")
    audit("m365_stoerungen", str(tage))
    eintraege = res.get("value", [])
    if not eintraege:
        return f"Keine Störungen in den letzten {tage} Tagen."
    out = []
    for e in eintraege:
        text, ampel = zustand(e.get("status"))
        erledigt = "erledigt" if e.get("isResolved") else "OFFEN"
        out.append(f"{ampel} [{e.get('id', '?')}] {e.get('service', '?')} · {erledigt} · {text}\n"
                   f"    {e.get('title', '')} (seit {str(e.get('startDateTime', ''))[:16]})")
    return "\n".join(out)


@werkzeug("status", "read")
def m365_meldungen(tage: int = 14) -> str:
    """Nachrichten aus dem Message Center — was Microsoft an Änderungen ankündigt
    (neue Funktionen, Umstellungen, Handlungsbedarf)."""
    c = conn(); require(c, "status", "read")
    seit = time.strftime("%Y-%m-%dT00:00:00Z",
                         time.gmtime(time.time() - max(tage, 1) * 86400))
    res = g(c, "GET", "/admin/serviceAnnouncement/messages"
                      f"?$filter=lastModifiedDateTime ge {seit}"
                      "&$select=id,title,category,severity,actionRequiredByDateTime,services"
                      "&$orderby=lastModifiedDateTime desc&$top=25")
    audit("m365_meldungen", str(tage))
    eintraege = res.get("value", [])
    if not eintraege:
        return f"Keine neuen Meldungen in den letzten {tage} Tagen."
    out = []
    for e in eintraege:
        frist = str(e.get("actionRequiredByDateTime") or "")[:10]
        out.append(f"[{e.get('id', '?')}] {e.get('category', '')} · {e.get('severity', '')}"
                   + (f" · Handlungsbedarf bis {frist}" if frist else "")
                   + f"\n    {e.get('title', '')}")
    return "\n".join(out)


@werkzeug("status", "read")
def m365_lizenzen() -> str:
    """Welche Microsoft-Lizenzen sind gekauft und wie viele davon sind belegt?
    Zeigt auch, wo es knapp wird."""
    c = conn(); require(c, "status", "read")
    res = g(c, "GET", "/subscribedSkus?$select=skuPartNumber,prepaidUnits,consumedUnits,capabilityStatus")
    audit("m365_lizenzen", "")
    out = []
    for s in res.get("value", []):
        if s.get("capabilityStatus") not in (None, "Enabled", "Warning"):
            continue
        gekauft = (s.get("prepaidUnits") or {}).get("enabled", 0)
        belegt = s.get("consumedUnits", 0)
        frei = gekauft - belegt
        knapp = " ⚠️ knapp" if gekauft and frei <= 0 else ""
        out.append(f"{s.get('skuPartNumber', '?')}: {belegt}/{gekauft} belegt, {frei} frei{knapp}")
    return "\n".join(out) or "(keine Lizenzen gefunden)"


@werkzeug("status", "read")
def m365_nutzung(tage: int = 7) -> str:
    """Wie viele Leute haben Exchange, Teams, SharePoint und OneDrive zuletzt genutzt?
    Grober Überblick, keine Einzelpersonen."""
    c = conn(); require(c, "status", "read")
    zeitraum = {7: "D7", 30: "D30", 90: "D90", 180: "D180"}.get(tage, "D7")
    # Dieser Bericht liefert AUSSCHLIESSLICH CSV — »$format=application/json« lehnt Graph
    # mit »JSON format is not supported« ab (real geprueft 30.07.). Also CSV lesen.
    roh = g_text(c, f"/reports/getOffice365ActiveUserCounts(period='{zeitraum}')")
    audit("m365_nutzung", zeitraum)
    zeilen = [z for z in roh.splitlines() if z.strip()]
    if len(zeilen) < 2:
        return "(keine Nutzungsdaten — Microsoft braucht dafür ein bis zwei Tage)"
    import csv
    reihen = list(csv.DictReader(zeilen))
    # Die letzte Zeile mit Zahlen ist der aktuellste Tag; leere Felder = kein Wert
    letzte = next((r for r in reversed(reihen)
                   if any((r.get(k) or "").strip() for k in
                          ("Exchange", "Teams", "SharePoint", "OneDrive"))), None)
    if not letzte:
        return "(keine Nutzungsdaten — Microsoft braucht dafür ein bis zwei Tage)"
    felder = ["Exchange", "Teams", "SharePoint", "OneDrive", "Yammer", "Skype For Business"]
    out = [f"{f}: {letzte[f]} aktive Nutzer" for f in felder if (letzte.get(f) or "").strip()]
    return (f"Zeitraum {zeitraum}, Stand {letzte.get('Report Date', '?')}\n"
            + ("\n".join(out) or "(an diesem Tag keine Aktivität gemeldet)"))


@mcp.tool()
def m365_hilfe() -> str:
    """Sagt, welche Microsoft-365-Dienste gerade verfügbar sind und was zu tun ist,
    wenn ein gewünschter Dienst fehlt. Immer verfügbar, auch ohne Verbindung."""
    # Bewusst OHNE @werkzeug: Dieses eine Werkzeug wird nie beschnitten. Ein MCP-Server
    # mit null Werkzeugen ist ein unerprobter Randfall — und der Nutzer bekommt eine
    # Antwort statt Schweigen, wenn das Modell ein Werkzeug nicht findet.
    try:
        perms = conn().get("permissions", {})
    except Exception:
        return ("Microsoft 365 ist auf diesem Rechner nicht verbunden. "
                "👉 Im Dashboard unter »Microsoft 365« einrichten — danach stehen Mail, "
                "Kalender, Dateien und die Übersicht zur Verfügung.")
    an, aus = [], []
    for dienst, name in DIENST_DE.items():
        regler = perms.get(dienst) or {}
        if regler.get("read") or regler.get("write"):
            an.append(f"{name} ({'Lesen und Schreiben' if regler.get('write') else 'nur Lesen'})")
        else:
            aus.append(name)
    text = "Verfügbar: " + (", ".join(an) if an else "nichts — alle Regler sind aus")
    if aus:
        text += ("\nAusgeschaltet: " + ", ".join(aus)
                 + "\n👉 Wenn du einen davon brauchst: Dashboard › Microsoft 365, Regler "
                   "umlegen und auf »Rechte aktualisieren« klicken. Ab der nächsten "
                   "Nachricht ist der Dienst da.")
    return text


if __name__ == "__main__":
    _beschneiden()          # #121: nur laden, was im Dashboard an ist
    mcp.run()  # stdio
