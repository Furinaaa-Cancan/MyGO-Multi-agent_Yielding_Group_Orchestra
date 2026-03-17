import hashlib
import secrets
import time

VALID_ROLES = {"user", "admin", "moderator"}

_users: dict[str, dict] = {}
_sessions: dict[str, dict] = {}
_next_id: int = 1


def register(username: str, password: str, role: str = "user") -> dict:
    global _next_id

    if username in _users:
        raise ValueError(f"Username '{username}' already exists")

    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}")

    password_hash = hashlib.sha256(password.encode()).hexdigest()

    user = {
        "user_id": _next_id,
        "username": username,
        "role": role,
        "password_hash": password_hash,
    }
    _users[username] = user
    _next_id += 1

    return {"user_id": user["user_id"], "username": username, "role": role}


def login(username: str, password: str) -> dict:
    if username not in _users:
        raise ValueError("Invalid username or password")

    user = _users[username]
    password_hash = hashlib.sha256(password.encode()).hexdigest()

    if user["password_hash"] != password_hash:
        raise ValueError("Invalid username or password")

    session_id = secrets.token_hex(32)
    expires_at = time.time() + 3600

    _sessions[session_id] = {
        "session_id": session_id,
        "user_id": user["user_id"],
        "username": username,
        "role": user["role"],
        "expires_at": expires_at,
    }

    return {
        "session_id": session_id,
        "user_id": user["user_id"],
        "expires_at": expires_at,
    }


def get_session(session_id: str) -> dict | None:
    session = _sessions.get(session_id)
    if session is None:
        return None

    if time.time() > session["expires_at"]:
        del _sessions[session_id]
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


def require_role(session_id: str, required_role: str) -> bool:
    session = get_session(session_id)
    if session is None:
        raise PermissionError("Invalid or expired session")

    user_role = session["role"]
    if user_role == "admin":
        return True

    if user_role == required_role:
        return True

    raise PermissionError(
        f"Role '{user_role}' does not have '{required_role}' permission"
    )


def list_active_sessions(username: str) -> list[dict]:
    now = time.time()
    active = []
    expired = []

    for sid, session in _sessions.items():
        if session["username"] == username:
            if now > session["expires_at"]:
                expired.append(sid)
            else:
                active.append({
                    "session_id": sid,
                    "user_id": session["user_id"],
                    "expires_at": session["expires_at"],
                })

    for sid in expired:
        del _sessions[sid]

    return active
