#!/usr/bin/env python3
"""Audit-Integrität (#49) — manipulations-EVIDENTES Siegel für audit.log (stdlib-only).

Das audit.log wird von mehreren Prozessen (Dashboard, MCP-Server, Tresor, Listener)
append-only geschrieben — eine Hash-Kette PRO Zeile wäre dadurch race-anfällig. Stattdessen
versiegelt EIN Schreiber (Listener-Tick) das Log periodisch: ein Siegel hält Größe + Hash
des bisherigen Log-Präfixes + Hash des vorherigen Siegels (Kette). Wird eine ALTE Zeile
nachträglich geändert oder das Log gekürzt, passt der Präfix-Hash nicht mehr → erkennbar.

Neue Einträge nach dem letzten Siegel sind noch nicht versiegelt (werden es beim nächsten
Tick) — das ist bewusst: wir beweisen, dass die Vergangenheit unverändert ist, nicht dass
gerade nichts angehängt wird.

CLI:  audit_log.py seal    |    audit_log.py verify
"""
import hashlib
import json
import os
import sys
import time

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")
AUDIT = os.path.join(BOT_DIR, "audit.log")
SEAL = os.path.join(BOT_DIR, "audit.seal")


def _hash_prefix(nbytes):
    """SHA-256 der ersten nbytes des audit.log (streamend, speicherschonend)."""
    h = hashlib.sha256()
    remaining = nbytes
    try:
        with open(AUDIT, "rb") as f:
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _last_seal():
    try:
        with open(SEAL) as f:
            lines = [l for l in f if l.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


def seal():
    """Aktuellen Log-Stand versiegeln. Rückgabe: Siegel-dict oder None (kein Log)."""
    try:
        size = os.path.getsize(AUDIT)
    except OSError:
        return None
    prefix = _hash_prefix(size)
    if prefix is None:
        return None
    prev = _last_seal()
    prev_sha = prev.get("sha", "") if prev else ""
    sha = hashlib.sha256((prev_sha + prefix).encode()).hexdigest()
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "size": size,
             "prefix": prefix, "sha": sha}
    fd = os.open(SEAL, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def verify():
    """Prüfen, ob der versiegelte Teil des Logs unverändert ist.
    Rückgabe: {ok, reason, sealed_size, current_size, sealed_at}."""
    last = _last_seal()
    if not last:
        return {"ok": True, "reason": "noch kein Siegel", "sealed_size": 0,
                "current_size": (os.path.getsize(AUDIT) if os.path.exists(AUDIT) else 0),
                "sealed_at": None}
    cur = os.path.getsize(AUDIT) if os.path.exists(AUDIT) else 0
    res = {"sealed_size": last["size"], "current_size": cur,
           "sealed_at": last.get("ts")}
    if cur < last["size"]:
        res.update(ok=False, reason="Log wurde gekürzt (kleiner als versiegelt)")
        return res
    actual = _hash_prefix(last["size"])
    if actual != last.get("prefix"):
        res.update(ok=False, reason="Versiegelter Teil wurde nachträglich verändert")
        return res
    res.update(ok=True, reason="unverändert seit letztem Siegel")
    return res


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "seal":
        s = seal()
        print(json.dumps(s) if s else "kein audit.log zum Versiegeln")
    else:
        print(json.dumps(verify(), ensure_ascii=False))


if __name__ == "__main__":
    main()
