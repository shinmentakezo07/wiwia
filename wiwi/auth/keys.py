"""Key service: generation, hashing, verification (constant-time)."""

from __future__ import annotations

import hashlib
import secrets

PREFIX_VIRTUAL = "sk-wiwi-"


def generate_virtual_key() -> str:
    return PREFIX_VIRTUAL + secrets.token_urlsafe(32)


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def mask_key(plaintext_or_last4: str) -> str:
    """Render a secret safe to log or return over the API.

    Shows a short prefix plus the last 4 characters so an admin can tell two
    keys apart in the UI, and fully hides anything too short to mask without
    exposing most of it.
    """
    if len(plaintext_or_last4) <= 12:
        return "***"
    return plaintext_or_last4[:5] + "…" + plaintext_or_last4[-4:]

