import base64
import hashlib
import hmac
import json
import time

# In-memory user storage
_users: dict[str, dict] = {}
_next_user_id: int = 1

_SECRET_KEY = b"super-secret-key-for-hmac-signing"
_TOKEN_EXPIRY_SECONDS = 3600


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _make_signature(payload_b64: bytes) -> str:
    return hmac.new(_SECRET_KEY, payload_b64, hashlib.sha256).hexdigest()


def register(username: str, password: str) -> dict:
    global _next_user_id

    if username in _users:
        raise ValueError(f"Username '{username}' already exists")

    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    user_id = _next_user_id
    _next_user_id += 1

    _users[username] = {
        "user_id": user_id,
        "username": username,
        "password_hash": _hash_password(password),
    }

    return {"user_id": user_id, "username": username}


def login(username: str, password: str) -> dict:
    if username not in _users:
        raise ValueError("Invalid credentials")

    user = _users[username]
    if user["password_hash"] != _hash_password(password):
        raise ValueError("Invalid credentials")

    payload = {
        "user_id": user["user_id"],
        "username": user["username"],
        "exp": time.time() + _TOKEN_EXPIRY_SECONDS,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes)
    signature = _make_signature(payload_b64)
    token = payload_b64.decode("utf-8") + "." + signature

    return {"token": token, "expires_in": _TOKEN_EXPIRY_SECONDS}


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    # Find user by user_id
    target_user = None
    for user in _users.values():
        if user["user_id"] == user_id:
            target_user = user
            break

    if target_user is None:
        raise ValueError("User not found")

    if target_user["password_hash"] != _hash_password(old_password):
        raise ValueError("Old password is incorrect")

    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters")

    target_user["password_hash"] = _hash_password(new_password)
    return True


def verify_token(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 2:
            raise ValueError("Invalid token")

        payload_b64 = parts[0].encode("utf-8")
        signature = parts[1]

        expected_sig = _make_signature(payload_b64)
        if not hmac.compare_digest(signature, expected_sig):
            raise ValueError("Invalid token")

        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes)

        if payload.get("exp", 0) < time.time():
            raise ValueError("Invalid token")

        return {"user_id": payload["user_id"], "username": payload["username"]}
    except ValueError:
        raise
    except Exception:
        raise ValueError("Invalid token")
