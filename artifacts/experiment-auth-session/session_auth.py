"""Session-based authentication with RBAC — in-memory."""
from __future__ import annotations

import hashlib
import secrets
import time

_users: dict[int, dict] = {}
_sessions: dict[str, dict] = {}
_next_id: int = 1

_VALID_ROLES = {"user", "admin", "moderator"}
_ROLE_HIERARCHY = {"admin": {"user", "moderator", "admin"}, "moderator": {"user", "moderator"}, "user": {"user"}}
_SESSION_EXPIRY = 3600  # 1 hour


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{h}"


def _verify_password(password: str, stored: str) -> bool:
    salt, h = stored.split(":", 1)
    return hashlib.sha256((salt + password).encode()).hexdigest() == h


def register(username: str, password: str, role: str = "user") -> dict:
    """Register a new user with a role."""
    global _next_id
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    if role not in _VALID_ROLES:
        raise ValueError(f"Invalid role: {role!r}. Must be one of {_VALID_ROLES}")
    for u in _users.values():
        if u["username"] == username:
            raise ValueError(f"Username {username!r} already exists")
    user = {
        "user_id": _next_id,
        "username": username,
        "password_hash": _hash_password(password),
        "role": role,
    }
    _users[_next_id] = user
    _next_id += 1
    return {"user_id": user["user_id"], "username": username, "role": role}


def login(username: str, password: str) -> dict:
    """Login and create a session."""
    for u in _users.values():
        if u["username"] == username:
            if _verify_password(password, u["password_hash"]):
                session_id = secrets.token_hex(32)
                expires_at = time.time() + _SESSION_EXPIRY
                _sessions[session_id] = {
                    "session_id": session_id,
                    "user_id": u["user_id"],
                    "expires_at": expires_at,
                }
                return {"session_id": session_id, "user_id": u["user_id"], "expires_at": expires_at}
            break
    raise ValueError("Invalid credentials")


def get_session(session_id: str) -> dict | None:
    """Get session info including user details."""
    sess = _sessions.get(session_id)
    if sess is None:
        return None
    user = _users.get(sess["user_id"])
    if user is None:
        return None
    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "role": user["role"],
        "expires_at": sess["expires_at"],
    }


def logout(session_id: str) -> bool:
    """Destroy a session."""
    if session_id in _sessions:
        del _sessions[session_id]
        return True
    return False


def list_active_sessions(user_id: int) -> list[dict]:
    """List all active sessions for a user."""
    return [
        {"session_id": s["session_id"], "expires_at": s["expires_at"]}
        for s in _sessions.values()
        if s["user_id"] == user_id
    ]


def require_role(session_id: str, required_role: str) -> bool:
    """Check if session user has the required role. Admin has all roles."""
    info = get_session(session_id)
    if info is None:
        raise PermissionError("Invalid or expired session")
    user_role = info["role"]
    allowed = _ROLE_HIERARCHY.get(user_role, set())
    if required_role not in allowed:
        raise PermissionError(f"Role {user_role!r} does not have {required_role!r} permission")
    return True
