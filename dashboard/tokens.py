"""Operator Dashboard — Token-Tresor.

Master-Key liegt im OS-Secret-Store (macOS Keychain / Windows DPAPI / Linux Secret-Service,
Service 'the-operator', Account 'token-key'). Nutzdaten (OAuth-Tokens, Secrets) werden
AES-256-GCM-verschlüsselt als *.enc in ~/.claude/matrix-bot/secrets/ abgelegt (0600).
"""
import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/matrix-bot"))
import secretstore  # noqa: E402  (stdlib-Modul aus BOT_DIR)

SECRETS_DIR = os.path.expanduser("~/.claude/matrix-bot/secrets")


def _master_key() -> bytes:
    v = secretstore.get("token-key")
    if not v:
        raise RuntimeError("Master-Key nicht im Secret-Store — Installer erneut ausführen")
    return bytes.fromhex(v)


def save(name: str, data: dict | str) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if isinstance(data, dict):
        data = json.dumps(data)
    aes = AESGCM(_master_key())
    nonce = os.urandom(12)
    blob = nonce + aes.encrypt(nonce, data.encode(), None)
    os.makedirs(SECRETS_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(SECRETS_DIR, name + ".enc")
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
    os.replace(path + ".tmp", path)


def load(name: str):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    path = os.path.join(SECRETS_DIR, name + ".enc")
    if not os.path.exists(path):
        return None
    blob = open(path, "rb").read()
    aes = AESGCM(_master_key())
    raw = aes.decrypt(blob[:12], blob[12:], None).decode()
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def delete(name: str) -> None:
    path = os.path.join(SECRETS_DIR, name + ".enc")
    if os.path.exists(path):
        os.remove(path)
