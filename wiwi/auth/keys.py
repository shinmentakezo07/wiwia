"""Key service: generation, hashing, verification (constant-time)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

PREFIX_VIRTUAL = "sk-wiwi-"
PREFIX_MASTER = "sk-wiwi-master-"


def generate_virtual_key() -> str:
    return PREFIX_VIRTUAL + secrets.token_urlsafe(32)


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def verify_key(plaintext: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_key(plaintext), key_hash)


def mask_key(plaintext_or_last4: str) -> str:
    if len(plaintext_or_last4) <= 4:
        return "***" + plaintext_or_last4
    return plaintext_or_last4[:6] + "…" + plaintext_or_last4[-4:]
