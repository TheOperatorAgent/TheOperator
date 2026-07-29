#!/usr/bin/env python3
"""Anhänge aus dem Matrix-Chat empfangen (stdlib-only).

Schickst du deinem Operator ein Foto, eine PDF oder eine Tabelle, landet die Datei
in deinem Arbeitsordner unter »eingang/« — und der Operator bekommt den Pfad
genannt, damit er sie ansehen kann.

**Vorher war das kaputt:** Der Listener ließ Bild-Nachrichten durch, sah aber nur
den `body` — bei Matrix ist das der DATEINAME. Der Operator antwortete also auf
»IMG_1234.jpg« statt auf das Bild.

Sicherheits-Leitplanken (Dateien aus dem Chat sind Fremddaten):
- **Größenlimit** (Standard 25 MB) — eine versehentlich geschickte Videodatei soll
  nicht den Rechner volllaufen lassen.
- **Dateiname wird entschärft**: keine Pfad-Anteile, keine versteckten Zeichen.
  Ein Name wie »../../.claude/matrix-bot/listener.py« kann nichts überschreiben.
- **Ziel ist ausschließlich der Arbeitsordner.** Genau dort darf der Operator laut
  Sandbox (#104-A) schreiben — außerhalb nicht.
- **Nichts wird ausgeführt.** Die Datei wird abgelegt, mehr nicht. Führt der
  Operator später etwas damit aus, greifen Allowlist und Rückfrage wie immer.
- **Ehrliche Grenze:** Der INHALT einer Datei durchläuft KEINE Pseudonymisierung.
  Wer ein Foto seines Ausweises schickt, schickt es so, wie es ist. Das steht auch
  im Datenschutz-Tab.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)
import platform_compat  # noqa: E402

MAX_BYTES = 25 * 1024 * 1024        # 25 MB
ORDNER = "eingang"                  # unter dem Arbeitsordner
BILD_TYPEN = {"m.image"}
DATEI_TYPEN = {"m.file", "m.audio", "m.video", "m.image"}


def eingang():
    p = os.path.join(platform_compat.workspace(), ORDNER)
    os.makedirs(p, exist_ok=True)
    return p


def _sicherer_name(name, fallback="anhang"):
    """Dateiname entschärfen: nur Basisname, harmlose Zeichen, sinnvolle Länge.
    Ein böswilliger Name darf niemals aus dem Zielordner herausführen."""
    name = os.path.basename(str(name or "").replace("\\", "/").strip())
    name = re.sub(r"[^\w.\- ]+", "_", name, flags=re.UNICODE).strip(" .")
    name = re.sub(r"\.{2,}", ".", name)
    if not name or name in (".", ".."):
        name = fallback
    return name[:120]


def _limit():
    try:
        c = json.load(open(os.path.join(BOT_DIR, "dashboard.json"))).get("anhaenge", {})
        return int(c.get("max_mb", 25)) * 1024 * 1024
    except Exception:
        return MAX_BYTES


def erkenne(event):
    """Ist das eine Datei-Nachricht? → dict oder None.
    Verschlüsselte Anhänge (content['file']) werden bewusst NICHT angefasst — wir
    können sie ohne E2EE-Schlüssel nicht entschlüsseln (#12) und tun nicht so, als ob."""
    inhalt = event.get("content") or {}
    if inhalt.get("msgtype") not in DATEI_TYPEN:
        return None
    if inhalt.get("file") and not inhalt.get("url"):
        return {"verschluesselt": True, "name": _sicherer_name(inhalt.get("body"))}
    url = inhalt.get("url", "")
    if not url.startswith("mxc://"):
        return None
    info = inhalt.get("info") or {}
    return {"verschluesselt": False,
            "mxc": url,
            "name": _sicherer_name(inhalt.get("body")),
            "bild": inhalt.get("msgtype") in BILD_TYPEN,
            "groesse": int(info.get("size") or 0),
            "typ": str(info.get("mimetype") or "")}


def _hole(hs, token, mxc, grenze):
    """Datei vom eigenen Homeserver laden. Erst der authentifizierte Weg (neuere
    Server), sonst der klassische — beides mit hartem Größen-Stopp beim Lesen."""
    server, _, media = mxc[len("mxc://"):].partition("/")
    if not server or not media:
        raise ValueError("ungültige Medien-Adresse")
    s, m = urllib.parse.quote(server), urllib.parse.quote(media)
    wege = [f"/_matrix/client/v1/media/download/{s}/{m}",
            f"/_matrix/media/v3/download/{s}/{m}"]
    letzter = None
    for weg in wege:
        req = urllib.request.Request(hs + weg,
                                     headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                laenge = int(r.headers.get("Content-Length") or 0)
                if laenge and laenge > grenze:
                    raise ValueError("zu groß")
                daten = r.read(grenze + 1)
                if len(daten) > grenze:
                    raise ValueError("zu groß")
                return daten
        except ValueError:
            raise
        except Exception as e:
            letzter = e
    raise RuntimeError(f"Download fehlgeschlagen: {letzter}")


def empfange(event, hs, token, log=lambda *_: None):
    """Anhang eines Ereignisses sichern. → dict mit 'hinweis' für das Modell, oder None.

    Der Hinweis wird der Nachricht angehängt, damit der Operator weiß, dass eine
    Datei da ist UND wo sie liegt — sonst sieht er nur den Dateinamen und rät."""
    a = erkenne(event)
    if not a:
        return None
    if a.get("verschluesselt"):
        return {"pfad": "", "name": a["name"], "bild": False,
                "hinweis": (f"[Der Nutzer hat die Datei »{a['name']}« geschickt, sie ist "
                            "aber Ende-zu-Ende-verschlüsselt und kann von mir nicht "
                            "geöffnet werden. Sag ihm das freundlich.]")}
    grenze = _limit()
    if a["groesse"] and a["groesse"] > grenze:
        mb = grenze // (1024 * 1024)
        return {"pfad": "", "name": a["name"], "bild": a["bild"],
                "hinweis": (f"[Der Nutzer hat »{a['name']}« geschickt — die Datei ist "
                            f"größer als {mb} MB und wurde nicht gespeichert. Sag ihm "
                            "das freundlich; das Limit steht im Dashboard.]")}
    try:
        daten = _hole(hs, token, a["mxc"], grenze)
    except ValueError:
        mb = grenze // (1024 * 1024)
        return {"pfad": "", "name": a["name"], "bild": a["bild"],
                "hinweis": (f"[»{a['name']}« ist größer als {mb} MB und wurde nicht "
                            "gespeichert. Sag dem Nutzer freundlich Bescheid.]")}
    except Exception as e:
        log(f"Anhang »{a['name']}« nicht ladbar: {e}")
        return {"pfad": "", "name": a["name"], "bild": a["bild"],
                "hinweis": (f"[»{a['name']}« konnte nicht geladen werden. Sag dem "
                            "Nutzer freundlich Bescheid und bitte ihn, es erneut zu "
                            "senden.]")}
    ziel = os.path.join(eingang(), f"{time.strftime('%Y%m%d-%H%M%S')}-{a['name']}")
    with open(ziel, "wb") as f:
        f.write(daten)
    try:
        os.chmod(ziel, 0o600)          # #18: gehört nur dir
    except OSError:
        pass
    log(f"Anhang gespeichert ({len(daten)} Bytes)")     # #18: kein Dateiname ins Log
    art = "Bild" if a["bild"] else "Datei"
    return {"pfad": ziel, "name": a["name"], "bild": a["bild"],
            "hinweis": (f"[Der Nutzer hat dir {'ein ' + art if a['bild'] else 'eine ' + art} "
                        f"geschickt: {ziel} — sieh es dir mit dem Read-Werkzeug an und "
                        "geh in deiner Antwort darauf ein.]")}


def aufraeumen(tage=14, log=lambda *_: None):
    """Alte Anhänge löschen (#18: Daten räumen sich selbst auf)."""
    grenze = time.time() - tage * 86400
    weg = 0
    try:
        for n in os.listdir(eingang()):
            p = os.path.join(eingang(), n)
            try:
                if os.path.isfile(p) and os.path.getmtime(p) < grenze:
                    os.remove(p)
                    weg += 1
            except OSError:
                pass
    except OSError:
        return 0
    if weg:
        log(f"{weg} alte Anhänge gelöscht (älter als {tage} Tage)")
    return weg
