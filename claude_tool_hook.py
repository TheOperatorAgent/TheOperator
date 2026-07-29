#!/usr/bin/env python3
"""Operator — PreToolUse-Hook für den Claude-CLI (#65, stdlib-only).

Claude Code ruft dieses Skript vor JEDEM Werkzeug-Einsatz auf und übergibt den
geplanten Aufruf als JSON über stdin. Wir entscheiden:

  * harmlos  → sofort durchlassen (kein Hinweis, keine Verzögerung)
  * riskant  → im Matrix-Chat nachfragen und auf ein klares Ja warten

Fail-closed: Jeder Fehler auf dem Weg führt zu »nicht ausführen«, nie zu einem
stillen Durchlassen. Ein kaputter Hook darf keine Sicherheitslücke öffnen —
er darf höchstens nerven.

Registriert über workspace/.claude/settings.json (PreToolUse).
"""
import json
import os
import sys

BOT_DIR = os.environ.get("OPERATOR_BOT_DIR", os.path.expanduser("~/.claude/matrix-bot"))
sys.path.insert(0, BOT_DIR)
LOG = os.path.join(BOT_DIR, "listener.log")


def log(msg):
    try:
        with open(LOG, "a") as f:
            import time
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🔐 {msg}\n")
    except OSError:
        pass


def _antwort(erlauben, grund):
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" if erlauben else "deny",
        "permissionDecisionReason": grund}}


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        # Ohne verständliche Eingabe können wir nicht urteilen → durchlassen wäre
        # falsch, blockieren aber auch (könnte harmlos sein). Claude entscheidet
        # dann nach seinen eigenen Regeln weiter: "ask" ist hier das ehrliche Mittel.
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "ask",
            "permissionDecisionReason": "Operator-Hook konnte die Anfrage nicht lesen."}}))
        return 0

    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input", {}) or {}
    try:
        import permission_broker as pb
    except Exception as e:
        log(f"Broker nicht ladbar ({e}) — Aktion abgelehnt (fail-closed)")
        print(json.dumps(_antwort(False, "Sicherheitsprüfung nicht verfügbar.")))
        return 0

    try:
        riskant, beschreibung = pb.classify(tool, tool_input)
    except Exception as e:
        log(f"Einstufung fehlgeschlagen ({e}) — Aktion abgelehnt (fail-closed)")
        print(json.dumps(_antwort(False, "Sicherheitsprüfung fehlgeschlagen.")))
        return 0

    if riskant == pb.BLOCK:
        # #82: Zugriff ins eigene Netz — nicht verhandelbar, also gar nicht erst fragen.
        log(f"Netz-Wächter: {beschreibung}")
        print(json.dumps(_antwort(False, f"{beschreibung}. Der Operator darf nur ins "
                                         "öffentliche Internet, nicht ins Heimnetz. Sag dem "
                                         "Nutzer freundlich Bescheid.")))
        return 0

    if not riskant:
        # Der Normalfall: nichts sagen, nichts bremsen.
        print(json.dumps(_antwort(True, "unkritisch")))
        return 0

    fp = pb.fingerprint(tool, tool_input)
    log(f"Rückfrage im Chat: {beschreibung}")
    try:
        # #104-B: Bei einem unbekannten (nicht gesperrten) Befehl darf der Owner mit
        # »immer« antworten — der Broker merkt sich das Befehlswort dann dauerhaft.
        merken = None
        try:
            merken = pb.merkbar(tool, tool_input)
        except Exception:
            pass
        ok = pb.ask_owner(beschreibung, fp, log=log, merken=merken)
    except Exception as e:
        log(f"Rückfrage fehlgeschlagen ({e}) — Aktion abgelehnt")
        ok = False
    print(json.dumps(_antwort(
        ok,
        "Vom Nutzer im Matrix-Chat freigegeben." if ok else
        "Vom Nutzer nicht freigegeben (oder keine Antwort). Aktion unterblieben — "
        "sag dem Nutzer freundlich Bescheid und mach ohne diesen Schritt weiter.")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
