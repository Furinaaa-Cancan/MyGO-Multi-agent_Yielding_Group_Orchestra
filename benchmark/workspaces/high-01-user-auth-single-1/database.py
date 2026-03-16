"""In-memory user storage."""

from typing import Dict, Optional

# In-memory storage: email -> user dict
_users_db: Dict[str, dict] = {}
# Also index by id for quick lookup
_users_by_id: Dict[str, dict] = {}


def get_user_by_email(email: str) -> Optional[dict]:
    return _users_db.get(email)


def get_user_by_id(user_id: str) -> Optional[dict]:
    return _users_by_id.get(user_id)


def create_user(user: dict) -> dict:
    _users_db[user["email"]] = user
    _users_by_id[user["id"]] = user
    return user


def get_all_users() -> list:
    return list(_users_db.values())


def clear_db():
    """Clear all data (useful for testing)."""
    _users_db.clear()
    _users_by_id.clear()
