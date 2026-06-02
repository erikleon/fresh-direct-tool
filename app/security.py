"""Encryption for the persisted FreshDirect session.

A stored ``storage_state`` (cookies + localStorage) is equivalent to being logged
into the user's FreshDirect account, so it is encrypted at rest with Fernet
(AES-128-CBC + HMAC). The key comes from ``FDPLANNER_ENCRYPTION_KEY`` when set;
otherwise we generate one and store it beside the data with 0600 perms. The blob
and the key must not live in the same backup if you can avoid it.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


class DecryptionError(RuntimeError):
    """Raised when the session blob cannot be decrypted (wrong/rotated key)."""


def _load_or_create_key(settings: Settings) -> bytes:
    if settings.encryption_key:
        return settings.encryption_key.encode()

    key_path = settings.key_path
    if key_path.exists():
        return key_path.read_bytes().strip()

    key = Fernet.generate_key()
    settings.ensure_dirs()
    # Write with restrictive perms before anyone else can read it.
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def encrypt(data: bytes, settings: Settings) -> bytes:
    return Fernet(_load_or_create_key(settings)).encrypt(data)


def decrypt(token: bytes, settings: Settings) -> bytes:
    try:
        return Fernet(_load_or_create_key(settings)).decrypt(token)
    except InvalidToken as exc:  # pragma: no cover - defensive
        raise DecryptionError(
            "Could not decrypt the saved session. The encryption key may have "
            "changed; re-run `fdplanner login` to capture a fresh session."
        ) from exc
