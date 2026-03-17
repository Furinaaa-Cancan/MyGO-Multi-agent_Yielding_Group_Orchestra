"""Session-based authentication management module."""

import hashlib
import secrets
import time

VALID_ROLES = {"user", "admin", "moderator"}

_users: dict[int, dict] = {}
_usernames: dict[str, int] = {}
_sessions: dict[str, dict] = {}
_next_user_id = 1


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def register(username: str, password: str, role: str = "user") -> dict:
    global _next_user_id

    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}. Must be one of {VALID_ROLES}")

    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    if username in _usernames:
        raise ValueError(f"Username '{username}' already exists")

    user_id = _next_user_id
    _next_user_id += 1

    _users[user_id] = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "password_hash": _hash_password(password),
    }
    _usernames[username] = user_id

    return {"user_id": user_id, "username": username, "role": role}


def login(username: str, password: str) -> dict:
    user_id = _usernames.get(username)
    if user_id is None:
        raise ValueError("Invalid username or password")

    user = _users[user_id]
    if user["password_hash"] != _hash_password(password):
        raise ValueError("Invalid username or password")

    session_id = secrets.token_hex(32)
    expires_at = int(time.time()) + 3600

    _sessions[session_id] = {
        "session_id": session_id,
        "user_id": user_id,
        "username": username,
        "role": user["role"],
        "expires_at": expires_at,
    }

    return {"session_id": session_id, "user_id": user_id, "expires_at": expires_at}


def get_session(session_id: str) -> dict | None:
    session = _sessions.get(session_id)
    if session is None:
        return None

    return {
        "user_id": session["user_id"],
        "username": session["username"],
        "role": session["role"],
        "expires_at": session["expires_at"],
    }


def logout(session_id: str) -> bool:
    if session_id in _sessions:
        del _sessions[session_id]
        return True
    return False


def list_active_sessions(user_id: int) -> list[dict]:
    now = int(time.time())
    return [
        {
            "session_id": s["session_id"],
            "user_id": s["user_id"],
            "username": s["username"],
            "role": s["role"],
            "expires_at": s["expires_at"],
        }
        for s in _sessions.values()
        if s["user_id"] == user_id and s["expires_at"] > now
    ]


def require_role(session_id: str, required_role: str) -> bool:
    session = _sessions.get(session_id)
    if session is None:
        raise PermissionError("Invalid or expired session")

    user_role = session["role"]

    if user_role == "admin":
        return True

    if user_role == required_role:
        return True

    raise PermissionError(
        f"Insufficient permissions: requires '{required_role}', has '{user_role}'"
    )
