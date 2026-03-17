"""Password hashing and verification helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets

PBKDF2_ALGO = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 120_000
SALT_BYTES = 16


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    if not password:
        raise ValueError("password must not be empty")

    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PBKDF2_ALGO}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False

    try:
        algo, iterations_raw, salt_hex, digest_hex = password_hash.split("$")
        if algo != PBKDF2_ALGO:
            return False
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)
