#!/usr/bin/env python3
"""Operator — Fremdmodell-Runner (läuft im dashboard-venv, braucht das openai-SDK).

Führt einen einzelnen Text-Aufruf gegen ein Fremd-Sprachmodell aus (Ollama, OpenAI,
Azure AI Foundry) — alle drei über die OpenAI-kompatible Chat-Completions-Schnittstelle
(`base_url`). BEWUSST ohne Werkzeug-Schleife: der Agent formuliert Text, der Operator sendet
ihn danach in den Chat. Lokale Werkzeuge (Bash/Datei/MCP) bleiben den Claude-Agenten
vorbehalten (der Gateway-Weg dafür ist von Anthropic nicht supportet, siehe Doku).

Protokoll (JSON über stdin → JSON über stdout):
  → {"provider","base_url","key","model_id","prompt","system"?,"max_tokens"?,"timeout"?}
  ← {"text": "..."}                (Erfolg)
  ← {"error": "..."}   + exit 1    (Fehler; der Listener fällt dann sauber zurück/meldet)
"""
import json
import os
import re
import subprocess
import sys

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)
import net_guard                     # noqa: E402  (#82 Schutz vor Zugriffen ins eigene Netz)

# ---------------------------------------------------------------- Werkzeuge (optional) --
# Kimi & Co. können nativ Werkzeuge aufrufen (OpenAI-Tool-Calling). Security by Design:
# Datei-Werkzeuge nur im Arbeitsordner (Pfad-Käfig), Befehle mit Timeout + Ausgabe-Kappung
# + Sperrliste für destruktive Muster, max. 15 Schritte pro Nachricht, alles protokolliert.
MAX_STEPS = 15
CMD_TIMEOUT = 60
FORBIDDEN_CMD = re.compile(
    r"\bsudo\b|\brm\s+(-\w+\s+)*(/|~)(\s|$)|\bmkfs|\bdd\s+.*of=/dev|"
    r"\bshutdown\b|\breboot\b|\blaunchctl\b|\bkillall\b|\bdiskutil\b", re.IGNORECASE)

TOOLS_SPEC = [
    {"type": "function", "function": {"name": "run_command",
        "description": "Führt einen Shell-Befehl im Arbeitsordner aus (Timeout 60 s).",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}},
    {"type": "function", "function": {"name": "write_file",
        "description": "Schreibt eine Datei (relativer Pfad im Arbeitsordner).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                       "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "read_file",
        "description": "Liest eine Datei aus dem Arbeitsordner.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {"name": "list_files",
        "description": "Listet Dateien im Arbeitsordner (rekursiv, max. 200).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": []}}},
]


# ---------------------------------------------------- Werkzeug-Ergebnisse (#88) --
# Budget je Nachricht. Ohne Grenze könnte eine einzige große Datei die Antwortzeit
# verdoppeln — auf einem Raspberry Pi noch deutlicher. Ein Operator, der datensparsam,
# aber träge ist, wird abgeschaltet; dann ist niemandem gedient (EINFACHHEIT.md).
PII_MAX_RUNDEN = 8          # so oft darf pro Nachricht der Presidio-Dienst gefragt werden
PII_MAX_ZEICHEN = 8000      # so viel Text darf dabei höchstens geprüft werden
_PII_BUDGET = {"runden": 0}


def _presidio(zeilen, conv, schon_ersetzt=()):
    """Zeilen an den laufenden Pseudonymisierungs-Dienst geben.
    Rückgabe (zeilen', neue_paare) oder (None, {}) wenn er nicht erreichbar ist.

    `schon_ersetzt` sind die Surrogate, die Stufe 3 bereits eingesetzt hat. Ohne sie
    entsteht ein Kettenfehler, den der bestehende Egress-Test (#83) aufgedeckt hat:
    Presidio hält ein Surrogat wie »Ingeburg Krause« für einen echten Namen und ersetzt
    es ein ZWEITES Mal. Der Listener bekäme dann ein Paar »Birte Dietz → Ingeburg Krause«,
    übersetzte beim Antworten also ein Surrogat in ein anderes Surrogat zurück — und der
    Nutzer läse nie wieder den echten Namen.
    """
    import platform_compat
    req = json.dumps({"texts": zeilen, "conversation": conv, "mode": "werkzeug",
                      "mapping": {}, "allow": list(schon_ersetzt)})
    try:
        sock, token = platform_compat.ipc_connect(timeout=20)
    except Exception:
        return None, {}
    try:
        if token:
            d = json.loads(req); d["token"] = token; req = json.dumps(d)
        sock.settimeout(20)
        sock.sendall(req.encode() + b"\n")
        roh = b""
        while not roh.endswith(b"\n"):
            teil = sock.recv(65536)
            if not teil:
                break
            roh += teil
    except Exception:
        return None, {}
    finally:
        try:
            sock.close()
        except Exception:
            pass
    try:
        out = json.loads(roh.decode())
    except ValueError:
        return None, {}
    if "error" in out:
        return None, {}
    return out.get("texts"), (out.get("mapping") or {}).get("s2r", {})


def _sanitize_result(text: str, pii_map: dict, conv: str = "", actions=None):
    """#83/#88 Egress-Schutz: Werkzeug-Ergebnisse (Shell, Dateien, Browser) kommen aus der
    ECHTEN Welt und gingen sonst roh ans Fremd-Modell — am pseudonymisierten Prompt vorbei.

    Vier Stufen, in dieser Reihenfolge:
      1. **Secrets maskieren** (redact) — Klartext-Geheimnisse sehen Fremdmodelle nie.
      2. **Strukturiertes entfernen** (Mail, Telefon, IBAN, Karte, IP) per Muster, ohne
         Netz und ohne Modell. Läuft IMMER, auch wenn der Dienst gerade aus ist.
      3. **Bekannte PII** durch dieselben Surrogate ersetzen wie im Prompt — sonst wäre
         »Weber« im Prompt jemand anderes als »Weber« in der gelesenen Datei.
      4. **Unbekannte Namen** über den Presidio-Dienst — das ist #88. Nur für Zeilen, die
         der Vorfilter für Fließtext hält, und nur im Rahmen des Budgets.

    Rückgabe: (text, neue_paare). **Die neuen Paare sind der wichtigste Teil.** Erzeugt
    Presidio hier ein Surrogat, kennt der Listener es nicht — und übersetzt es beim
    Antworten nicht zurück. Der Nutzer läse dann einen erfundenen Namen und hielte ihn für
    echt. Deshalb reicht der Runner sie nach oben durch.

    Ein Fehler in Stufe 4 ist ein SICHTBARER Rückfall (Vermerk in `actions`), kein stiller:
    Vorher schluckte ein `except: pass` das Problem und schickte den Volltext weiter."""
    if not text:
        return text, {}
    vermerk = actions if actions is not None else []
    try:
        import redact
        text = redact.redact(text)
    except Exception:
        pass
    try:
        import pii_vorfilter
        text, _n = pii_vorfilter.strukturiert_entfernen(text)
    except Exception as e:
        vermerk.append({"tool": "datenschutz", "warnung":
                        f"Musterprüfung nicht möglich ({e}) — Ergebnis nur teilweise geprüft"})
    neu = {}
    try:
        s2r = (pii_map or {}).get("s2r", {})
        for surrogat, real in sorted(s2r.items(), key=lambda kv: len(kv[1] or ""), reverse=True):
            if real and len(real) >= 3 and real in text:
                text = text.replace(real, surrogat)
    except Exception:
        pass

    # ---- Stufe 4: unbekannte Namen (#88)
    try:
        import pii_vorfilter
        zeilen = text.splitlines()
        indizes, verworfen_ab = pii_vorfilter.zeilen_pruefen(text, PII_MAX_ZEICHEN)
        if verworfen_ab is not None:
            # Michis Entscheidung 31.07.: Was nicht mehr geprüft werden kann, wird
            # VERWORFEN — nicht ungeprüft durchgereicht. Sichtbar, nicht heimlich.
            zeilen = zeilen[:verworfen_ab] + ["[… gekürzt, ungeprüft entfernt]"]
            indizes = [i for i in indizes if i < verworfen_ab]
            vermerk.append({"tool": "datenschutz", "warnung":
                            "Ergebnis war zu lang für die vollständige Prüfung — der Rest "
                            "wurde entfernt statt ungeprüft weitergegeben."})
        if indizes:
            if _PII_BUDGET["runden"] >= PII_MAX_RUNDEN:
                vermerk.append({"tool": "datenschutz", "warnung":
                                "Namensprüfung für dieses Ergebnis übersprungen (Zeitbudget) "
                                "— Mail, Telefon und Kontodaten wurden trotzdem entfernt."})
            else:
                _PII_BUDGET["runden"] += 1
                # Bereits eingesetzte Surrogate ausnehmen (siehe _presidio).
                schon = list((pii_map or {}).get("s2r", {}).keys())
                geprueft, paare = _presidio([zeilen[i] for i in indizes], conv, schon)
                if geprueft is None:
                    vermerk.append({"tool": "datenschutz", "warnung":
                                    "Datenschutz-Dienst nicht erreichbar — Namen in diesem "
                                    "Ergebnis konnten nicht ersetzt werden."})
                else:
                    for i, zeile in zip(indizes, geprueft):
                        zeilen[i] = zeile
                    neu = paare or {}
        text = "\n".join(zeilen)
    except Exception as e:
        vermerk.append({"tool": "datenschutz", "warnung":
                        f"Namensprüfung fehlgeschlagen ({e}) — Ergebnis nur mit "
                        "Musterprüfung weitergegeben."})
    return text, neu


def _jail(workdir: str, path: str) -> str:
    """Pfad-Käfig: Datei-Zugriffe außerhalb des Arbeitsordners sind technisch unmöglich."""
    p = os.path.realpath(os.path.join(workdir, path or "."))
    root = os.path.realpath(workdir)
    if p != root and not p.startswith(root + os.sep):
        raise ValueError("Pfad liegt außerhalb des Arbeitsordners — nicht erlaubt")
    return p


def _exec_tool(name: str, args: dict, workdir: str, actions: list) -> str:
    try:
        if name == "run_command":
            cmd = str(args.get("command", ""))
            if FORBIDDEN_CMD.search(cmd):
                actions.append("BLOCKIERT: " + cmd[:120])
                return "FEHLER: Dieser Befehl ist aus Sicherheitsgründen gesperrt."
            actions.append("$ " + cmd[:200])
            # #104-A: auch Fremd-Modell-Befehle laufen in der OS-Sandbox — der
            # Pfad-Käfig oben ist Absicht des Codes, die Sandbox ist Durchsetzung
            # des Betriebssystems (greift auch für Kind- und Enkelprozesse).
            argv = ["/bin/sh", "-c", cmd]
            try:
                import sandbox as _sb
                argv = _sb.wrap(argv)
            except Exception:
                pass
            r = subprocess.run(argv, cwd=workdir, capture_output=True,
                               text=True, timeout=CMD_TIMEOUT)
            out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
            return f"exit={r.returncode}\n{out[:8000]}"
        if name == "write_file":
            p = _jail(workdir, args.get("path", ""))
            os.makedirs(os.path.dirname(p) or workdir, exist_ok=True)
            content = str(args.get("content", ""))
            with open(p, "w") as f:
                f.write(content)
            actions.append(f"write {args.get('path')} ({len(content)} B)")
            return "geschrieben"
        if name == "read_file":
            p = _jail(workdir, args.get("path", ""))
            actions.append(f"read {args.get('path')}")
            with open(p) as f:
                return f.read()[:16000]
        if name == "list_files":
            p = _jail(workdir, args.get("path", "."))
            actions.append(f"ls {args.get('path', '.')}")
            names = []
            for base, _dirs, files in os.walk(p):
                rel = os.path.relpath(base, workdir)
                names += [os.path.normpath(os.path.join(rel, fn)) for fn in files]
                if len(names) > 200:
                    break
            return "\n".join(sorted(names)[:200]) or "(leer)"
        return "unbekanntes Werkzeug"
    except subprocess.TimeoutExpired:
        return f"FEHLER: Befehl abgebrochen (über {CMD_TIMEOUT} s)"
    except Exception as e:
        return "FEHLER: " + str(e)[:200]


# ---------------------------------------------------------------- Browser (nur Lesen/Navigieren) --
# Bewusst OHNE Formular-Absenden/Ausfüllen (v1): der Agent kann Seiten öffnen, Links/Buttons
# klicken und Text/Links extrahieren — aber nichts im Web auslösen. Headless, mit Timeouts.
BROWSER_TOOLS = [
    {"type": "function", "function": {"name": "open_page",
        "description": "Öffnet eine Webseite (URL) und gibt Titel, Text und klickbare Elemente zurück. "
                       "Nur Lesen/Navigieren — kein Absenden von Formularen.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "click_link",
        "description": "Klickt ein sichtbares Link/Button-Element anhand seines Textes und gibt die neue "
                       "Seite zurück. Keine Formular-Absendung.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
]

# ---------------------------------------------------- Web-AKTIONEN (#80, opt-in) --
# Auf operator.bayern steht als Sicherheitszusage: »Browser-Agent kann keine Formulare
# absenden«. Diese Werkzeuge sind deshalb NICHT im Grundzustand dabei — sie kommen nur
# dazu, wenn der Nutzer sie im Dashboard ausdrücklich einschaltet. Damit bleibt die Zusage
# für jede Installation wahr, die nichts umgestellt hat, und wer mehr will, entscheidet
# das sichtbar selbst.
#
# Zwei getrennte Werkzeuge statt eines »fill_and_submit«: Ausfüllen ist harmlos und
# umkehrbar (nichts hat den Rechner verlassen), Absenden ist es nicht. Wären beide eins,
# müsste jedes Tippen bestätigt werden — und wer zehnmal hintereinander gefragt wird,
# klickt beim elften Mal blind auf »ja«. Die Trennung IST die Sicherheit.
BROWSER_AKTIONS_TOOLS = [
    {"type": "function", "function": {"name": "fill_field",
        "description": "Füllt ein Formularfeld auf der offenen Seite (Beschriftung oder Platzhalter). "
                       "Sendet NICHTS ab. Passwort- und Zahlungsfelder sind gesperrt.",
        "parameters": {"type": "object", "properties": {
            "feld": {"type": "string"}, "wert": {"type": "string"}},
            "required": ["feld", "wert"]}}},
    {"type": "function", "function": {"name": "submit_form",
        "description": "Sendet das Formular ab (Knopf per Beschriftung). Der Nutzer wird vorher "
                       "im Chat gefragt und muss zustimmen.",
        "parameters": {"type": "object", "properties": {"knopf": {"type": "string"}},
                       "required": ["knopf"]}}},
]
BROWSER_TOOL_NAMES = {"open_page", "click_link"}
BROWSER_AKTIONS_NAMEN = {"fill_field", "submit_form"}


def web_aktionen_erlaubt():
    """Hat der Nutzer Web-Aktionen ausdrücklich eingeschaltet? Standard: nein.

    Fail-closed bei kaputter Konfiguration — eine unlesbare Datei darf nie dazu führen,
    dass der Agent plötzlich Formulare abschicken kann."""
    try:
        with open(os.path.join(BOT_DIR, "dashboard.json"), encoding="utf-8") as f:
            return json.load(f).get("browser_aktionen") is True
    except (OSError, ValueError, AttributeError):
        return False


def browser_werkzeuge():
    """Der Werkzeugsatz fürs Surfen — lesend, plus Aktionen nur bei Freigabe."""
    return BROWSER_TOOLS + (BROWSER_AKTIONS_TOOLS if web_aktionen_erlaubt() else [])


def browser_namen():
    return BROWSER_TOOL_NAMES | (BROWSER_AKTIONS_NAMEN if web_aktionen_erlaubt() else set())

# Felder, die der Agent NIE ausfüllt — auch nicht mit Bestätigung. Ein Passwort oder eine
# Kartennummer gehört nicht durch ein Sprachmodell, egal wie die Frage lautet. Wer so
# etwas eintragen will, tut es selbst im eigenen Browser.
GESPERRTE_FELDER = re.compile(
    r"passwo|password|kennwort|pin\b|cvv|cvc|kreditkart|credit.?card|card.?number|"
    r"kartennummer|iban|bic|konto|sozialversicher|steuer.?id|ausweis|personalausweis",
    re.IGNORECASE)


def _system_chromium():
    """Pfad zu einem bereits installierten Chromium/Chrome, den Playwright mitbenutzen kann.

    Playwright liefert nicht für jede Architektur ein eigenes Chromium mit — auf ARM-Linux
    (z. B. Raspberry Pi) schlägt »playwright install chromium« fehl. Dort ist aber fast
    immer ein System-Chromium da. Der Installer hinterlegt den Fund in browser_path.txt;
    findet er nichts, suchen wir hier noch einmal selbst.
    """
    import shutil
    merk = os.path.join(BOT_DIR, "browser_path.txt")
    try:
        p = open(merk).read().strip()
        if p and os.path.exists(p):
            return p
    except OSError:
        pass
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _browser_page(state):
    if state.get("page"):
        return state["page"]
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        br = pw.chromium.launch(headless=True)
    except Exception:
        eigener = _system_chromium()
        if not eigener:
            raise
        br = pw.chromium.launch(headless=True, executable_path=eigener)
    ctx = br.new_context(accept_downloads=False)

    # #82: JEDE Anfrage prüfen — nicht nur die erste. Eine harmlose Seite kann per
    # Weiterleitung oder eingebetteter Ressource ins eigene Netz zeigen; auch ein
    # DNS-Wechsel zwischen Prüfung und Verbindung wird so beim nächsten Sprung erwischt.
    def _wache(route, request):
        try:
            ok, grund = net_guard.check_url(request.url)
        except Exception:
            ok, grund = False, "Prüfung fehlgeschlagen"
        if ok:
            route.continue_()
        else:
            state.setdefault("blocked", []).append(f"{request.url[:100]} ({grund})")
            route.abort()

    try:
        ctx.route("**/*", _wache)
    except Exception:
        pass                       # ohne Route-Hook bleibt die Vorab-Prüfung in _browse_tool

    page = ctx.new_page()
    page.set_default_timeout(30000)
    state.update(pw=pw, browser=br, page=page)
    return page


def _browser_close(state):
    for k in ("browser", "pw"):
        try:
            obj = state.get(k)
            if obj:
                obj.stop() if k == "pw" else obj.close()
        except Exception:
            pass


def _page_summary(page):
    try:
        text = page.inner_text("body")
    except Exception:
        text = ""
    links, seen = [], set()
    try:
        for el in page.query_selector_all("a, button"):
            t = (el.inner_text() or "").strip().replace("\n", " ")
            if t and len(t) < 80 and t not in seen:
                seen.add(t); links.append(t)
            if len(links) >= 40:
                break
    except Exception:
        pass
    return (f"URL: {page.url}\nTitel: {page.title()}\n\n{text[:6000]}"
            + ("\n\n[Klickbar] " + " · ".join(links) if links else ""))


def _feld_finden(page, beschriftung):
    """Ein Formularfeld über Beschriftung, Platzhalter oder Namen finden."""
    for suche in (lambda: page.get_by_label(beschriftung, exact=False).first,
                  lambda: page.get_by_placeholder(beschriftung, exact=False).first,
                  lambda: page.locator(f"[name='{beschriftung}'], #{beschriftung}").first):
        try:
            el = suche()
            if el and el.count() if hasattr(el, "count") else el:
                el.wait_for(state="visible", timeout=3000)
                return el
        except Exception:
            continue
    return None


def _erlaubte_domains():
    """Optionale Einschränkung: Auf welchen Seiten darf überhaupt abgesendet werden?
    Leer = keine Einschränkung (dann entscheidet allein die Rückfrage im Chat)."""
    try:
        with open(os.path.join(BOT_DIR, "dashboard.json"), encoding="utf-8") as f:
            werte = json.load(f).get("browser_absenden_domains") or []
        return [str(d).strip().lower() for d in werte if str(d).strip()]
    except (OSError, ValueError, AttributeError):
        return []


def _absenden_erlaubt(page, knopf, actions):
    """#80: Absenden ist der Punkt ohne Wiederkehr — eine verschickte Anfrage holt niemand
    zurück. Deshalb hier dieselbe Bestätigung wie bei jeder anderen Aktion nach außen.

    Rückgabe (True, "") oder (False, Klartext-Begründung).

    fail-closed: Ist der Broker nicht erreichbar, wird NICHT abgesendet. Lieber eine
    Aufgabe, die liegen bleibt, als ein Formular, das jemand nie freigegeben hat."""
    url = page.url
    erlaubte = _erlaubte_domains()
    if erlaubte and not any(d in url.lower() for d in erlaubte):
        actions.append(f"🚫 Absenden gesperrt (Domain): {url[:90]}")
        return False, (f"Auf dieser Seite darf ich nichts absenden — sie steht nicht auf "
                       f"deiner Liste erlaubter Adressen. 👉 Im Dashboard unter »System« "
                       f"ergänzen, falls das so sein soll.")
    try:
        sys.path.insert(0, BOT_DIR)
        import permission_broker as pb
    except Exception as e:
        actions.append(f"🚫 Absenden abgebrochen (Broker fehlt: {e})")
        return False, ("Ich konnte dich nicht um Erlaubnis fragen und habe deshalb NICHTS "
                       "abgeschickt. 👉 Bitte kurz den Listener-Dienst prüfen (Tab System).")
    beschreibung = (f"auf »{page.title()[:60]}« ({url[:90]}) das Formular abschicken "
                    f"— Knopf »{knopf}«")
    fp = pb.fingerprint("browser_submit", {"url": url, "knopf": knopf})
    if not pb.ask_owner(beschreibung, fp):
        actions.append(f"🚫 Absenden abgelehnt: {url[:90]}")
        return False, "Du hast nicht zugestimmt — ich habe nichts abgeschickt."
    return True, ""


def _browse_tool(name, args, state, actions):
    try:
        page = _browser_page(state)
        if name == "open_page":
            url = str(args.get("url", "")).strip()
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            ok, grund = net_guard.check_url(url)          # #82: vor dem Verbinden prüfen
            if not ok:
                actions.append(f"🚫 blockiert {url[:90]} ({grund})")
                return net_guard.hinweis(url, grund)
            actions.append("🌐 open " + url[:120])
            page.goto(url, wait_until="domcontentloaded")
            return _page_summary(page)
        if name == "fill_field":
            feld, wert = str(args.get("feld", "")).strip(), str(args.get("wert", ""))
            if GESPERRTE_FELDER.search(feld):
                actions.append(f"🚫 Feld gesperrt: {feld[:60]}")
                return (f"Das Feld »{feld}« sieht nach einem Passwort oder einer Zahlungsangabe "
                        "aus. So etwas trage ich nicht ein — auch nicht, wenn du es mir sagst. "
                        "👉 Bitte im eigenen Browser selbst ausfüllen.")
            ziel = _feld_finden(page, feld)
            if ziel is None:
                return f"Ein Feld »{feld}« finde ich auf dieser Seite nicht."
            # Zweiter Riegel: Auch wenn die Beschriftung harmlos klingt — ein
            # Passwort-Eingabefeld bleibt ein Passwort-Eingabefeld.
            if (ziel.get_attribute("type") or "").lower() == "password":
                actions.append(f"🚫 Passwortfeld gesperrt: {feld[:60]}")
                return ("Das ist ein Passwortfeld. Da trage ich nichts ein. "
                        "👉 Bitte selbst im Browser eingeben.")
            ziel.fill(wert, timeout=15000)
            # Der Wert steht NICHT im Protokoll: Formularinhalte sind Nutzerdaten (#18).
            actions.append(f"⌨️ ausgefüllt: {feld[:60]} ({len(wert)} Zeichen)")
            return f"»{feld}« ausgefüllt."

        if name == "submit_form":
            knopf = str(args.get("knopf", "")).strip() or "Absenden"
            erlaubt, grund = _absenden_erlaubt(page, knopf, actions)
            if not erlaubt:
                return grund
            page.get_by_role("button", name=knopf).first.click(timeout=15000)
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            actions.append(f"📤 abgesendet: {knopf[:60]} auf {page.url[:90]}")
            return "Abgeschickt.\n\n" + _page_summary(page)

        if name == "click_link":
            t = str(args.get("text", "")).strip()
            actions.append("🖱️ click »" + t[:60] + "«")
            page.get_by_text(t, exact=False).first.click(timeout=15000)
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            return _page_summary(page)
        return "unbekanntes Browser-Werkzeug"
    except Exception as e:
        m = str(e)
        if "Executable doesn't exist" in m or "playwright install" in m:
            return ("Mir fehlt noch ein Browser zum Surfen (dein eigener Browser und das "
                    "Dashboard sind davon nicht betroffen). 👉 Ein Update über das Dashboard "
                    "holt ihn nach — Reiter »Aktualisierung«.")
        return "Browser-Fehler: " + m[:200]


# ------------------------------------------------- MCP fuer Fremdmodelle (#140) --
def _mcp_verbinden():
    """Server aus derselben .mcp.json starten, die auch der Claude-Weg benutzt.

    Faellt still aus, wenn nichts konfiguriert ist — ein Agent ohne Microsoft-Anbindung
    soll arbeiten koennen, nicht scheitern.
    """
    try:
        import platform_compat as _pc
        import mcp_client
        pfad = os.path.join(_pc.workspace(), ".mcp.json")
        if not os.path.exists(pfad) or os.path.getsize(pfad) < 20:
            return None
        v = mcp_client.Verbindung.aus_datei(pfad)
        return v if v.server else None
    except Exception as e:
        print(f"llm_runner: MCP nicht verfuegbar: {e}", file=sys.stderr)
        return None


def _mcp_umgebung():
    """Was die Schleuse zum Urteilen braucht. Die Liste der lesenden Werkzeuge kommt
    aus dem Broker — eine zweite Liste waere eine zweite Wahrheit."""
    try:
        import permission_broker as pb
        return {"stufe": pb.stufe(), "lesende_werkzeuge": pb.lesende_werkzeuge(),
                "immer_erlaubt": pb.MCP_LESEND}
    except Exception:
        return {"stufe": "streng"}      # fail-closed: im Zweifel alles bestaetigen


def _mcp_tool(mcp, name, argumente, actions):
    if not mcp:
        return "Diese Anbindung ist gerade nicht verfuegbar."
    antwort = mcp.aufrufen(name, argumente, _mcp_umgebung(), herkunft="fremdmodell")
    actions.append({"tool": name, "ok": "ergebnis" in antwort})
    if antwort.get("bestaetigung_noetig"):
        # Fremd-Agenten haben keinen Draht zum Chat des Besitzers. Statt heimlich
        # auszufuehren wird ehrlich abgelehnt — und der Agent kann es dem Nutzer sagen.
        return ("Dieser Schritt braucht die Zustimmung des Besitzers und wurde deshalb "
                f"nicht ausgefuehrt ({antwort.get('grund','')}). Sag dem Nutzer freundlich "
                "Bescheid und mach ohne diesen Schritt weiter.")
    return str(antwort.get("ergebnis") or antwort.get("fehler") or "")


def main() -> int:
    try:
        req = json.load(sys.stdin)
    except ValueError:
        print(json.dumps({"error": "ungültige Anfrage"}))
        return 1
    provider = req.get("provider", "")
    base_url = (req.get("base_url") or "").rstrip("/")
    model_id = req.get("model_id", "")
    key = req.get("key") or ""
    prompt = req.get("prompt", "")
    system = req.get("system") or ""
    # Höheres Default-Budget: Thinking-Modelle (z. B. Kimi K2.7) verbrauchen einen Teil fürs
    # interne »reasoning«; zu wenig Tokens → Antwort-Text (content) bleibt leer/abgeschnitten.
    max_tokens = int(req.get("max_tokens", 4096))
    timeout = float(req.get("timeout", 90))
    use_tools = bool(req.get("tools"))
    use_browser = bool(req.get("browser"))
    workdir = req.get("workdir") or ""
    pii_map = req.get("pii_map") or {}     # #83: Surrogat-Map für Tool-Ergebnis-Bereinigung
    conv = req.get("conversation") or ""   # #88: damit ein Name in einer gelesenen Datei
    neue_pii = {}                          #      dasselbe Surrogat bekommt wie im Prompt

    # Ollama spricht OpenAI-kompatibel unter /v1; Dummy-Key, da das SDK einen verlangt.
    if provider == "ollama":
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        key = key or "ollama"
    if not base_url:
        print(json.dumps({"error": "keine Server-Adresse für den Provider"}))
        return 1

    try:
        from openai import OpenAI
    except ImportError:
        print(json.dumps({"error": "openai-SDK fehlt im venv (pip install openai)"}))
        return 1

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        client = OpenAI(base_url=base_url, api_key=key, timeout=timeout, max_retries=1)

        # ---------- Werkzeug-Modus: kleine agentische Schleife (Pfad-Käfig + optional Browser) ----------
        if (use_tools and workdir) or use_browser:
            if use_tools and workdir:
                os.makedirs(workdir, exist_ok=True)
            tools_active = (TOOLS_SPEC if (use_tools and workdir) else []) + (browser_werkzeuge() if use_browser else [])
            # #140: Microsoft 365, n8n und die Doku-Suche haengen bis 1.30.0 am Programm
            # »claude« — ein Agent auf Ollama oder OpenAI konnte rechnen und schreiben,
            # aber nicht in den Kalender sehen. Mit dem eigenen MCP-Client geht das jetzt,
            # und zwar durch dieselbe Schleuse wie alles andere.
            mcp = _mcp_verbinden()
            if mcp:
                tools_active += mcp.werkzeuge()
            actions, bstate = [], {}
            try:
                for _ in range(MAX_STEPS):
                    resp = client.chat.completions.create(model=model_id, messages=messages,
                                                          tools=tools_active, max_tokens=max_tokens)
                    msg = resp.choices[0].message
                    if getattr(msg, "tool_calls", None):
                        messages.append({"role": "assistant", "content": msg.content or "",
                                         "tool_calls": [{"id": tc.id, "type": "function",
                                                         "function": {"name": tc.function.name,
                                                                      "arguments": tc.function.arguments}}
                                                        for tc in msg.tool_calls]})
                        for tc in msg.tool_calls:
                            try:
                                targs = json.loads(tc.function.arguments or "{}")
                            except ValueError:
                                targs = {}
                            if tc.function.name.startswith("mcp__"):
                                res = _mcp_tool(mcp, tc.function.name, targs, actions)
                            elif tc.function.name in browser_namen():
                                res = _browse_tool(tc.function.name, targs, bstate, actions)
                            else:
                                res = _exec_tool(tc.function.name, targs, workdir, actions)
                            # #83/#88: bereinigen, BEVOR es das Fremd-Modell sieht
                            res, neue = _sanitize_result(res, pii_map, conv, actions)
                            if neue:
                                # Nach oben durchreichen: Der Listener muss diese Paare
                                # kennen, sonst übersetzt er das Surrogat beim Antworten
                                # nicht zurück und der Nutzer liest einen erfundenen Namen.
                                neue_pii.update(neue)
                                pii_map.setdefault("s2r", {}).update(neue)
                            messages.append({"role": "tool", "tool_call_id": tc.id, "content": res})
                        continue
                    text = (msg.content or "").strip()
                    if text:
                        print(json.dumps({"text": text, "actions": actions, "neue_pii": neue_pii}))
                        return 0
                    break
            finally:
                _browser_close(bstate)
                if mcp:
                    mcp.schliessen()
            print(json.dumps({"error": "Werkzeug-Limit erreicht — bitte die Aufgabe kleiner "
                              "stellen.", "actions": actions, "neue_pii": neue_pii}))
            return 1

        # ---------- Text-Modus (ohne Werkzeuge) ----------
        resp = client.chat.completions.create(model=model_id, messages=messages,
                                              max_tokens=max_tokens)
        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        if not text:
            # Thinking-Modell: hat evtl. das ganze Budget fürs Denken verbraucht → content leer.
            if getattr(choice, "finish_reason", "") == "length":
                print(json.dumps({"error": "Die Antwort wurde abgeschnitten (Token-Limit erreicht) "
                                  "— bitte die Aufgabe kürzer/kleiner stellen."}))
            else:
                print(json.dumps({"error": "Modell lieferte leere Antwort"}))
            return 1
        print(json.dumps({"text": text}))
        return 0
    except Exception as e:  # Provider-/Netz-/Auth-Fehler einheitlich weiterreichen
        msg = str(e)
        low = msg.lower()
        if "401" in msg or "invalid" in low and "key" in low or "authentication" in low:
            msg = "Zugangsschlüssel abgelehnt — bitte im Dashboard prüfen."
        elif "connect" in low or "refused" in low or "timed out" in low:
            msg = f"{provider} nicht erreichbar (läuft der Server? Adresse korrekt?)"
        print(json.dumps({"error": msg[:300]}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
