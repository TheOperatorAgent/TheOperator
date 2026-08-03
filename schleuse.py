#!/usr/bin/env python3
"""Die Schleuse — die eine Stelle, durch die jede Handlung des Operator muss (#139).

Warum es das gibt
-----------------
OpenClaw und Hermes bauen einen Agenten und hängen Sicherheit daneben: Regelwerke je
Sitzung, Allowlists, Sandbox-Optionen — an mehreren Stellen. Je mehr Stellen, desto
sicherer ist, dass eine davon beim nächsten Feature vergessen wird.

Genau so entstand am 30.07. der Fall, dass zwölf schreibende Microsoft-Werkzeuge ohne
Rückfrage liefen: Die Liste der riskanten Werkzeuge war eine **Aufzählung**, und alles,
was danach dazukam, stand nicht drin.

Wir drehen es um: **eine Schleuse, und der Agent hängt daneben.** Jede Handlung bekommt
hier ein Urteil, bevor irgendetwas passiert.

Was diese Datei NICHT tut
-------------------------
Sie führt nichts aus, sie fragt nichts, sie schreibt nichts. Sie **urteilt**. Alles, was
sie zum Urteilen braucht, wird ihr übergeben (`umgebung`). Damit ist sie ohne Homeserver,
ohne Modell, ohne Netz und ohne Dateisystem prüfbar — in Millisekunden, hunderttausendfach.

Sobald sie anfängt, selbst etwas zu tun, ist genau das verloren. Deshalb ist der einzige
erlaubte Import `re`, und ein Wächter-Test hält das fest.

Der Vertrag mit dem Aufrufer
----------------------------
Pfade müssen **schon aufgelöst** hereinkommen (absolut, ohne Symlinks). Eine rein
textliche Prüfung ließe sich sonst über einen Symlink aushebeln, und Auflösen bräuchte
das Dateisystem — was die Reinheit zerstören würde. Ein nicht absoluter Pfad wird
deshalb abgelehnt: lieber eine Ablehnung zu viel als eine Prüfung, die nur so aussieht.

Die fünf Stufen
---------------
1. Bekannt?          — unbekannte Art → nein; unbekanntes Werkzeug → nachfragen
2. Gesperrt?         — die Sperrliste gewinnt IMMER, auch in Stufe »locker«
3. Käfig             — Pfade und Netzziele
4. Bestätigung?      — alles, was nach außen wirkt oder schwer rückholbar ist
5. Protokollpflicht  — jedes Urteil, auch jedes ablehnende (#146)
"""
import re

# --------------------------------------------------------------------- Urteile --
JA = "ja"                  # darf laufen
FRAGEN = "fragen"          # darf laufen, wenn der Besitzer zustimmt
NEIN = "nein"              # wird nicht ausgeführt, auch nicht auf Zuruf

ARTEN = ("werkzeug", "befehl", "datei_lesen", "datei_schreiben", "netz")

# Die Sperrliste. Sie gewinnt gegen alles: gegen die gelernte Erlaubnisliste, gegen die
# Stufe »locker«, gegen jede Bestätigung. Es gibt Handlungen, zu denen der Operator nicht
# einmal fragen soll — weil eine Ja-Antwort im Chat für so etwas keine tragfähige
# Grundlage ist. Übernommen aus permission_broker.DESTRUCTIVE_CMD; dort bleibt sie für
# den heutigen Claude-Pfad in Kraft, bis K6 (#144) den Pfad zusammenführt.
GESPERRT = [
    (r"\brm\s+(-\w+\s+)*(-[rf]\w*)", "Dateien löschen"),
    (r"\bsudo\b|\bdoas\b", "Administrator-Rechte"),
    (r"\bmkfs\b|\bdiskutil\s+(erase|partition)", "Datenträger formatieren"),
    (r"\bdd\s+[^|]*of=/dev/", "direkt auf ein Laufwerk schreiben"),
    (r"\b(shutdown|reboot|halt)\b", "Rechner herunterfahren/neu starten"),
    (r"\b(launchctl|systemctl)\s+(unload|disable|stop|remove)", "Dienste abschalten"),
    (r"\blaunchctl\s+(bootout|bootstrap)\b", "Dienste ändern"),
    (r"\bkillall\b|\bkill\s+-9\b", "Programme hart beenden"),
    (r"\bchmod\s+(-R\s+)?[0-7]{3,4}\s+/", "Rechte im System ändern"),
    (r"\bchown\b", "Eigentümer ändern"),
    (r"\bgit\s+push\b.*(--force|-f)\b", "Git-Historie überschreiben"),
    (r"\bgit\s+clean\b.*-\w*[xfd]", "unversionierte Dateien löschen"),
    (r"\bgit\s+reset\s+--hard\b", "Arbeitsstand verwerfen"),
    (r"\b(curl|wget|fetch)\b[^|]*\|\s*(bash|sh|zsh)", "Skript aus dem Netz ausführen"),
    (r"\bbase64\b[^|]*\|\s*(bash|sh|zsh)", "kodiertes Skript ausführen"),
    (r"\b(sh|bash|zsh)\s+-c\b.*\b(rm|curl|wget)\b", "Befehl in Unter-Shell verstecken"),
    (r"\bpython3?\s+-c\b.*\b(rmtree|unlink|remove|rmdir)\b", "Dateien löschen (Python)"),
    (r"\bperl\s+-e\b.*\bunlink\b|\bruby\s+-e\b.*\b(delete|unlink)\b",
     "Dateien löschen (Skriptsprache)"),
    (r"\bfind\b.*\s-delete\b", "Dateien löschen (find)"),
    (r"\btruncate\b.*\s-s\s*0", "Dateiinhalt leeren"),
    (r"\bshred\b|\bsrm\b", "Dateien unwiederbringlich überschreiben"),
    (r"\bnpm\s+publish\b|\bpip\s+install\b.*--index-url",
     "Paket veröffentlichen/fremde Quelle"),
    (r">\s*/etc/|>\s*/System/|>\s*/Library/", "Systemdateien überschreiben"),
    (r"\bosascript\b.*\b(delete|empty trash)\b", "Dateien löschen (AppleScript)"),
    (r"\bcrontab\s+(-r|\S+\.txt)", "Zeitpläne ersetzen/löschen"),
    (r"\b(env|command|nohup|nice|time|xargs)\s+(sudo|doas)\b",
     "Administrator-Rechte (verpackt)"),
]
_GESPERRT = [(re.compile(m, re.IGNORECASE), g) for m, g in GESPERRT]

# Werkzeuge, die nach außen wirken. Diese Liste ist ausdrücklich **keine** abschließende
# Aufzählung — sie ist nur die Begründungshilfe für schöne Rückfragetexte. Ob gefragt
# wird, entscheidet der Umkehrschluss in `_werkzeug()`: Was nicht als lesend bekannt ist,
# wird gefragt. Das ist der Unterschied, der bei #119 gefehlt hat.
NACH_AUSSEN = {
    "mail_send": "eine E-Mail versenden",
    "mail_antworten": "auf eine E-Mail antworten",
    "mail_weiterleiten": "eine E-Mail weiterleiten",
    "calendar_add": "einen Termin eintragen",
    "kalender_absagen": "einen Termin absagen",
    "kalender_verschieben": "einen Termin verschieben",
    "files_upload": "eine Datei hochladen",
    "workflow_activate": "eine Automation scharf schalten",
    "webhook_trigger": "einen Webhook auslösen",
}

# Netzziele, die nie erlaubt sind: das eigene Netz. Ein Agent, der Adressen im Heim- oder
# Firmennetz abrufen kann, ist ein Werkzeug zum Ausspähen genau dieses Netzes (#82).
# Die Prüfung arbeitet hier textlich auf dem Hostnamen; die Auflösung von Namen zu
# Adressen macht `net_guard` VOR dem Aufruf — auch das gehört zum Vertrag.
PRIVATE_ZIELE = re.compile(
    r"^(localhost|127\.|0\.0\.0\.0|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|"
    r"169\.254\.|\[::1\]|::1$|.*\.local$|.*\.internal$)", re.IGNORECASE)

STUFEN = ("streng", "normal", "locker")


def _urteil(entscheidung, grund, regel, handlung):
    """Ein Urteil ist immer vollständig: Entscheidung, Begründung in Alltagssprache,
    die Regel die griff, und der Protokolleintrag. Ohne Protokolleintrag gäbe es
    abgelehnte Handlungen, von denen später niemand weiß — und genau die Frage
    »was hat es versucht und nicht gedurft?« ist unser Compliance-Argument (#146)."""
    return {
        "entscheidung": entscheidung,
        "erlaubt": entscheidung != NEIN,
        "bestaetigung_noetig": entscheidung == FRAGEN,
        "grund": grund,
        "regel": regel,
        "protokoll": {
            "art": handlung.get("art"),
            "name": handlung.get("name"),
            "herkunft": handlung.get("herkunft"),
            "sitzung": handlung.get("sitzung"),
            "urteil": entscheidung,
            "grund": grund,
            "regel": regel,
        },
    }


def _gesperrt(text):
    for muster, grund in _GESPERRT:
        if muster.search(text or ""):
            return grund
    return None


def _im_ordner(pfad, ordner):
    """Liegt `pfad` innerhalb von `ordner`? Rein textlich — der Aufrufer hat beide
    bereits aufgelöst (siehe Vertrag im Kopf dieser Datei)."""
    if not ordner:
        return False
    a = pfad.rstrip("/")
    b = ordner.rstrip("/")
    return a == b or a.startswith(b + "/")


# ------------------------------------------------------------ Die fünf Stufen --
def _befehl(handlung, umgebung):
    cmd = handlung.get("argumente", {}).get("befehl", "")
    if not cmd.strip():
        return _urteil(NEIN, "Kein Befehl angegeben.", "leer", handlung)

    grund = _gesperrt(cmd)
    if grund:
        return _urteil(NEIN, f"Das würde {grund} — das macht der Operator nie, "
                             "auch nicht auf Zuruf.", "sperrliste", handlung)

    # Fail-closed: nur bekannte Befehlswörter laufen ohne Rückfrage. Alles andere fragt.
    # Ein vergessener Eintrag kostet eine überflüssige Rückfrage; vorher kostete er eine
    # stillschweigend ausgeführte Aktion (#104-B).
    # Der Pfad zaehlt, nicht nur das Befehlswort. »cat« ist harmlos, »cat
    # ~/.ssh/id_ed25519« ist es nicht. Der Pruefstand hat genau das aufgedeckt: Ein
    # Modell umging den Lese-Kaefig, indem es statt »lies« einfach »befehl« mit
    # »cat /etc/passwd« nahm — dieselbe Luecke, die im alten Broker am 03.08. geschlossen
    # wurde (#148) und hier zunaechst fehlte. Genau der stille Sicherheits-Rueckschritt,
    # vor dem das Epic warnt: Wer eine Regel an ZWEI Stellen pflegen muss, verliert sie
    # an einer.
    fremd_pfad = _fremder_pfad(cmd, umgebung)
    if fremd_pfad:
        return _urteil(FRAGEN, f"Zugriff außerhalb des Arbeitsordners: {fremd_pfad}",
                       "befehl_fremder_pfad", handlung)

    sicher = set(umgebung.get("sichere_befehle") or ()) | set(umgebung.get("gelernte_befehle") or ())
    unbekannt = [w for w in _befehlswoerter(cmd) if w not in sicher]
    if unbekannt:
        return _urteil(FRAGEN, "Unbekannter Befehl: " + ", ".join(sorted(set(unbekannt))),
                       "befehl_unbekannt", handlung)
    if umgebung.get("stufe") == "streng":
        return _urteil(FRAGEN, "Stufe »streng«: Befehle werden immer bestätigt.",
                       "stufe_streng", handlung)
    return _urteil(JA, "Bekannter, ungefährlicher Befehl.", "befehl_bekannt", handlung)


# Geheimnisse sind UEBERALL geschuetzt, auch im sonst erlaubten Ordner — dieselbe Regel
# wie im Broker (#148): entscheidend ist, WAS eine Datei ist, nicht wo sie liegt.
_GEHEIM = re.compile(r"credentials\.json|bots\.json|/secrets?/|\.ssh/|id_(rsa|ed25519|ecdsa)"
                     r"|operator-pii-|\.db$|Keychains|\.env$|update-signing|tokens\.json"
                     r"|\.pem$|\.key$", re.IGNORECASE)
_PFAD_IM_BEFEHL = re.compile(r"(?<![\w=])(~/[^\s;|&)\"']*|/[A-Za-z0-9._\-/]{3,})")
# Oeffentliche Systempfade: ohne sie fragt der Operator bei jedem »python3 --version« nach
# und wird unbenutzbar. Bewusst NICHT dabei: /etc und /tmp.
_OEFFENTLICH = ("/usr/", "/bin/", "/sbin/", "/opt/", "/System/", "/Library/", "/Applications/")


def _fremder_pfad(cmd, umgebung):
    """Erster Pfad im Befehl, der eine Rueckfrage verdient — oder None.

    Rein textlich, wie die ganze Schleuse: kein Dateisystem, damit sie ohne Umgebung
    testbar bleibt. Das Aufloesen von »~« und relativen Pfaden ist Aufgabe des
    Werkzeugkastens (#141), nicht ihre.
    """
    if _GEHEIM.search(cmd or ""):
        return _GEHEIM.search(cmd).group(0)
    ordner = umgebung.get("arbeitsordner") or ""
    for treffer in _PFAD_IM_BEFEHL.findall(cmd or ""):
        if treffer.startswith(_OEFFENTLICH):
            continue
        if treffer.startswith("~/") or not _im_ordner(treffer, ordner):
            return treffer
    return None


_TRENNER = re.compile(r"\|\||&&|\||;|\n")
_VERPACKUNG = {"env", "command", "nohup", "nice", "time", "stdbuf", "timeout",
               "caffeinate", "xargs"}


def _befehlswoerter(cmd):
    """Das erste echte Wort je Teilbefehl — Verpackungen wie `env` oder `timeout`
    übersprungen, sonst versteckt sich jeder Befehl hinter einem harmlosen Vorwort."""
    woerter = []
    for teil in _TRENNER.split(cmd):
        stuecke = [s for s in teil.strip().split() if s]
        i = 0
        while i < len(stuecke):
            wort = stuecke[i].split("/")[-1]
            if wort in _VERPACKUNG or "=" in stuecke[i]:
                i += 1
                continue
            woerter.append(wort)
            break
    return woerter


def _datei(handlung, umgebung, schreibend):
    pfad = handlung.get("argumente", {}).get("pfad", "")
    # Nicht absolut ODER mit »..« darin heißt: nicht aufgelöst. Der eigene Wächter-Test
    # hat hier ein echtes Loch gefunden — »/arbeitsordner/../../etc/hosts« bestand die
    # Präfixprüfung und wäre als »im Arbeitsordner« durchgegangen. Ein Käfig, den man
    # mit zwei Punkten verlässt, ist keiner.
    if not pfad.startswith("/") or ".." in pfad.split("/"):
        return _urteil(NEIN, "Der Pfad ist nicht eindeutig aufgelöst — so lässt sich "
                             "nicht sicher prüfen, wo die Datei wirklich liegt.",
                       "pfad_unaufgeloest", handlung)
    ordner = umgebung.get("arbeitsordner")
    if _im_ordner(pfad, ordner):
        return _urteil(JA, "Im Arbeitsordner.", "im_arbeitsordner", handlung)
    if schreibend:
        return _urteil(NEIN, "Außerhalb des Arbeitsordners wird nichts geschrieben.",
                       "schreiben_ausserhalb", handlung)
    return _urteil(FRAGEN, "Diese Datei liegt außerhalb des Arbeitsordners.",
                   "lesen_ausserhalb", handlung)


def _netz(handlung, umgebung):
    ziel = handlung.get("argumente", {}).get("ziel", "")
    host = ziel.split("//")[-1].split("/")[0].split("@")[-1]
    if not host:
        return _urteil(NEIN, "Kein Ziel angegeben.", "leer", handlung)
    if PRIVATE_ZIELE.match(host):
        return _urteil(NEIN, "Adressen im eigenen Netz ruft der Operator nicht ab.",
                       "eigenes_netz", handlung)
    if not ziel.lower().startswith(("http://", "https://")):
        return _urteil(NEIN, "Nur http und https.", "schema", handlung)
    if umgebung.get("stufe") == "streng":
        return _urteil(FRAGEN, "Stufe »streng«: Abrufe aus dem Netz werden bestätigt.",
                       "stufe_streng", handlung)
    return _urteil(JA, "Öffentliche Adresse.", "netz_oeffentlich", handlung)


def _werkzeug(handlung, umgebung):
    name = handlung.get("name") or ""
    if not name:
        return _urteil(NEIN, "Kein Werkzeugname.", "leer", handlung)

    # DER Umkehrschluss. Bekannt-lesend läuft durch; alles andere fragt — auch ein
    # Werkzeug, das es gestern noch nicht gab. Eine Positivliste riskanter Werkzeuge
    # kann mit einem wachsenden Werkzeugkasten nicht Schritt halten (#119).
    if name in set(umgebung.get("lesende_werkzeuge") or ()):
        if umgebung.get("stufe") == "streng" and name not in set(
                umgebung.get("immer_erlaubt") or ()):
            return _urteil(FRAGEN, "Stufe »streng«: auch Lesezugriffe werden bestätigt.",
                           "stufe_streng", handlung)
        return _urteil(JA, "Liest nur.", "werkzeug_lesend", handlung)

    kurz = name.split("__")[-1]
    was = NACH_AUSSEN.get(kurz)
    if was:
        return _urteil(FRAGEN, f"Der Operator möchte {was}.", "werkzeug_nach_aussen",
                       handlung)
    return _urteil(FRAGEN, f"»{kurz}« ist nicht als reines Nachschauen bekannt.",
                   "werkzeug_unbekannt", handlung)


def pruefen(handlung, umgebung=None):
    """Das Urteil über eine einzelne Handlung.

    handlung: {"art", "name", "argumente", "herkunft", "sitzung"}
    umgebung: {"stufe", "arbeitsordner", "lesende_werkzeuge", "sichere_befehle",
               "gelernte_befehle", "immer_erlaubt"}

    Gibt immer ein vollständiges Urteil zurück — auch im Fehlerfall. Eine Schleuse, die
    bei unerwarteter Eingabe eine Ausnahme wirft, ist eine Schleuse, die man mit
    unerwarteter Eingabe öffnen kann.
    """
    umgebung = umgebung or {}
    if not isinstance(handlung, dict):
        return _urteil(NEIN, "Unverständliche Handlung.", "kein_dict", {})
    art = handlung.get("art")
    if art not in ARTEN:
        return _urteil(NEIN, f"Unbekannte Art von Handlung: {art!r}.", "art_unbekannt",
                       handlung)
    if umgebung.get("stufe") not in STUFEN and umgebung.get("stufe") is not None:
        # Eine kaputte Einstellung darf nicht die schwächste Auslegung bedeuten.
        umgebung = dict(umgebung, stufe="streng")

    if art == "befehl":
        return _befehl(handlung, umgebung)
    if art == "datei_lesen":
        return _datei(handlung, umgebung, schreibend=False)
    if art == "datei_schreiben":
        return _datei(handlung, umgebung, schreibend=True)
    if art == "netz":
        return _netz(handlung, umgebung)
    return _werkzeug(handlung, umgebung)
