import hashlib
import hmac
import base64
import json
import time

SECRET_KEY = "super-secret-key-for-jwt-signing"

_users: dict[int, dict] = {}
_username_index: dict[str, int] = {}
_next_id = 1


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _create_token(user_id: int) -> str:
    payload = json.dumps({"user_id": user_id, "exp": int(time.time()) + 3600})
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode()
    signature = hmac.new(
        SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def register(username: str, password: str) -> dict:
    global _next_id
    if username in _username_index:
        raise ValueError("Username already exists")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    user_id = _next_id
    _next_id += 1
    _users[user_id] = {
        "user_id": user_id,
        "username": username,
        "password_hash": _hash_password(password),
    }
    _username_index[username] = user_id
    return {"user_id": user_id, "username": username}


def login(username: str, password: str) -> dict:
    user_id = _username_index.get(username)
    if user_id is None:
        raise ValueError("Invalid credentials")
    user = _users[user_id]
    if user["password_hash"] != _hash_password(password):
        raise ValueError("Invalid credentials")
    token = _create_token(user_id)
    return {"token": token, "expires_in": 3600}


def verify_token(token: str) -> dict:
    try:
        payload_b64, signature = token.rsplit(".", 1)
    except ValueError:
        raise ValueError("Invalid token")
    expected_sig = hmac.new(
        SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        raise ValueError("Invalid token")
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        raise ValueError("Invalid token")
    if payload.get("exp", 0) < time.time():
        raise ValueError("Invalid token")
    user_id = payload.get("user_id")
    user = _users.get(user_id)
    if user is None:
        raise ValueError("Invalid token")
    return {"user_id": user["user_id"], "username": user["username"]}


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    user = _users.get(user_id)
    if user is None:
        raise ValueError("User not found")
    if user["password_hash"] != _hash_password(old_password):
        raise ValueError("Invalid old password")
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters")
    user["password_hash"] = _hash_password(new_password)
    return True
