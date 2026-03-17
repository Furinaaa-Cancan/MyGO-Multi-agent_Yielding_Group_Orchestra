"""JWT authentication module — register, login, verify, change password."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

_SECRET_KEY = secrets.token_hex(32)
_users: dict[int, dict] = {}
_next_id: int = 1
_TOKEN_EXPIRY = 3600  # 1 hour


def _hash_password(password: str) -> str:
    """Hash password with SHA-256 + salt."""
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{h}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored hash."""
    salt, h = stored.split(":", 1)
    return hashlib.sha256((salt + password).encode()).hexdigest() == h


def register(username: str, password: str) -> dict:
    """Register a new user."""
    global _next_id
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    for u in _users.values():
        if u["username"] == username:
            raise ValueError(f"Username {username!r} already exists")
    user = {
        "user_id": _next_id,
        "username": username,
        "password_hash": _hash_password(password),
    }
    _users[_next_id] = user
    _next_id += 1
    return {"user_id": user["user_id"], "username": username}


def _make_token(user_id: int, username: str) -> str:
    """Create a simple HMAC-based token (simulating JWT)."""
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": int(time.time()) + _TOKEN_EXPIRY,
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(_SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def login(username: str, password: str) -> dict:
    """Login and return a token."""
    for u in _users.values():
        if u["username"] == username:
            if _verify_password(password, u["password_hash"]):
                token = _make_token(u["user_id"], username)
                return {"token": token, "expires_in": _TOKEN_EXPIRY}
            break
    raise ValueError("Invalid credentials")


def verify_token(token: str) -> dict:
    """Verify a token and return user info."""
    try:
        parts = token.split(".", 1)
        if len(parts) != 2:
            raise ValueError("Invalid token")
        payload_b64, sig = parts
        expected_sig = hmac.new(_SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            raise ValueError("Invalid token")
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            raise ValueError("Invalid token")
        return {"user_id": payload["user_id"], "username": payload["username"]}
    except (ValueError, KeyError, json.JSONDecodeError):
        raise ValueError("Invalid token")


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    """Change user password."""
    user = _users.get(user_id)
    if user is None:
        raise ValueError("User not found")
    if not _verify_password(old_password, user["password_hash"]):
        raise ValueError("Old password is incorrect")
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters")
    user["password_hash"] = _hash_password(new_password)
    return True
