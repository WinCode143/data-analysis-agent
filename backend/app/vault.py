"""Encryption of secrets at rest (database passwords and API keys).

It uses Fernet (AES-128-CBC + HMAC). The key comes from INSIGHT_SECRET_KEY or, if that's not set, it's generated
once in workspace/.secret.key with 600 permissions. It protects against leaking sources.json / settings.json
(backups, Git, screenshots); not against someone with full access to your user, who can read the key too.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from . import config

PREFIX = "enc:v1:"
_KEY_FILE = config.WORKSPACE_DIR / ".secret.key"
_fernet: Fernet | None = None


def _get() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.getenv("INSIGHT_SECRET_KEY")
        if not key:
            if not _KEY_FILE.exists():
                _KEY_FILE.write_bytes(Fernet.generate_key())
                _KEY_FILE.chmod(0o600)
            key = _KEY_FILE.read_text().strip()
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(value: str) -> str:
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + _get().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypts; old plaintext values come back as they are (they get encrypted when saved again)."""
    if not value or not value.startswith(PREFIX):
        return value
    try:
        return _get().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        raise ValueError("Couldn't decrypt a secret: the encryption key changed (INSIGHT_SECRET_KEY / .secret.key).") from e
