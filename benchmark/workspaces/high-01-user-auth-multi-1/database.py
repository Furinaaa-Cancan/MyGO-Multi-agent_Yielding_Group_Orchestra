"""In-memory user storage."""

from typing import Dict, Optional


# In-memory storage: email -> user dict
users_db: Dict[str, dict] = {}


def get_user_by_email(email: str) -> Optional[dict]:
    """Look up a user by email address."""
    return users_db.get(email)


def get_user_by_id(user_id: str) -> Optional[dict]:
    """Look up a user by their UUID."""
    for user in users_db.values():
        if user["id"] == user_id:
            return user
    return None


def create_user(user: dict) -> dict:
    """Store a new user. Key is email."""
    users_db[user["email"]] = user
    return user


def get_all_users() -> list:
    """Return a list of all users."""
    return list(users_db.values())


def clear_db() -> None:
    """Clear the database (for testing)."""
    users_db.clear()
