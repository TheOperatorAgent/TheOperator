#!/usr/bin/env python3
"""Signaturprüfung für Updates (#103) — läuft im Dashboard-venv (cryptography).

Bewusst KEINE eigene Krypto: ed25519 kommt aus der cryptography-Bibliothek.
updater.py (stdlib) ruft dieses Skript mit dem venv-Python auf; ist das venv
nicht da, gibt es bei gepinntem Schlüssel KEIN Update (fail-closed).

  update_verify.py verify <pubkey.txt> <manifest.json> <manifest.sig>
      → Exit 0 nur bei gültiger Signatur.
  update_verify.py sign <manifest.json> <manifest.sig>
      → Signiert (privater Schlüssel via Env OPERATOR_SIGN_KEY, hex) —
        nur für den Release-Ablauf des Autors relevant.
  update_verify.py keygen
      → Neues Schlüsselpaar: privat (hex) auf stdout Zeile 1, öffentlich Zeile 2.

Formate: Schlüssel und Signatur als Hex-Text (eine Zeile). Signiert werden die
ROHEN Manifest-Bytes — jede Änderung am Manifest bricht die Signatur, und die
sha256-Einträge im Manifest sichern dann jede einzelne Datei.
"""
import os
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)
from cryptography.exceptions import InvalidSignature


def _lies(pfad, binaer=False):
    with open(pfad, "rb" if binaer else "r") as f:
        return f.read()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "verify":
        pub_hex = _lies(sys.argv[2]).strip()
        daten = _lies(sys.argv[3], binaer=True)
        sig_hex = _lies(sys.argv[4]).strip()
        try:
            pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
            pub.verify(bytes.fromhex(sig_hex), daten)
        except (InvalidSignature, ValueError):
            print("Signatur UNGÜLTIG")
            return 1
        print("Signatur gültig")
        return 0
    if cmd == "sign":
        key_hex = os.environ.get("OPERATOR_SIGN_KEY", "").strip()
        if not key_hex:
            print("OPERATOR_SIGN_KEY fehlt (Env)")
            return 1
        priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_hex))
        daten = _lies(sys.argv[2], binaer=True)
        sig = priv.sign(daten)
        with open(sys.argv[3], "w") as f:
            f.write(sig.hex() + "\n")
        print("signiert")
        return 0
    if cmd == "keygen":
        priv = Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization
        raw = priv.private_bytes(serialization.Encoding.Raw,
                                 serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
        pub = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                             serialization.PublicFormat.Raw)
        print(raw.hex())
        print(pub.hex())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
