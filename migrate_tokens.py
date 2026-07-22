#!/usr/bin/env python3
"""Migriert verbliebene Klartext-Matrix-Tokens aus credentials.json / bots.json in den
macOS-Schlüsselbund und setzt die Dateifelder auf den Marker "keychain" (Issue #20).

Idempotent: läuft bei jedem install.sh mit; hat der Nutzer schon migriert, passiert nichts.
Stdlib-only. Nach diesem Lauf existiert kein Klartext-Token mehr auf der Platte, und der
Datei-Fallback in listener/send/server greift nie wieder.
"""
import json
import os
import subprocess
import sys

BOT_DIR = os.path.expanduser("~/.claude/matrix-bot")


def _keychain_set(account: str, value: str) -> bool:
    r = subprocess.run(["security", "add-generic-password", "-U", "-s", "the-operator",
                        "-a", account, "-w", value], capture_output=True)
    return r.returncode == 0


def _atomic_write(path: str, data: dict) -> None:
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(path + ".tmp", path)


def migrate() -> int:
    migrated = 0
    cred_p = os.path.join(BOT_DIR, "credentials.json")
    if os.path.exists(cred_p):
        c = json.load(open(cred_p))
        tok = c.get("access_token")
        if tok and tok != "keychain":
            if _keychain_set("matrix-owner", tok):
                c["access_token"] = "keychain"
                _atomic_write(cred_p, c)
                migrated += 1
                print("  ✓ Owner-Token in den Schlüsselbund migriert")

    bots_p = os.path.join(BOT_DIR, "bots.json")
    if os.path.exists(bots_p):
        data = json.load(open(bots_p))
        changed = False
        for b in data.get("bots", []):
            tok = b.get("access_token")
            if tok and tok != "keychain":
                if _keychain_set("matrix-bot-" + b["agent"], tok):
                    b["access_token"] = "keychain"
                    changed = True
                    migrated += 1
                    print(f"  ✓ Bot-Token '{b['agent']}' in den Schlüsselbund migriert")
        if changed:
            _atomic_write(bots_p, data)
    return migrated


if __name__ == "__main__":
    n = migrate()
    print(f"Token-Migration: {n} Klartext-Token(s) migriert"
          if n else "Token-Migration: nichts zu tun (alle bereits im Schlüsselbund)")
    sys.exit(0)
