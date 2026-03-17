"""OAuth client registration and authorization code flow with in-memory storage."""

import secrets
import time

# Module-level storage dicts
clients: dict = {}
auth_codes: dict = {}
tokens: dict = {}

VALID_SCOPES = {"read", "write", "admin"}
CODE_EXPIRY_SECONDS = 300  # 5 minutes


def register_client(client_name: str, redirect_uri: str) -> dict:
    """Register a new OAuth client and return its credentials."""
    client_id = secrets.token_hex(16)
    client_secret = secrets.token_hex(16)

    client_info = {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_name": client_name,
        "redirect_uri": redirect_uri,
    }

    clients[client_id] = client_info

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }


def authorize(client_id: str, user_id: str, scope: str) -> str:
    """Generate a time-limited authorization code for the given client and user.

    Args:
        client_id: The registered client's ID.
        user_id: The user granting authorization.
        scope: One of 'read', 'write', or 'admin'.

    Returns:
        An authorization code string.

    Raises:
        ValueError: If client_id is unknown or scope is invalid.
    """
    if client_id not in clients:
        raise ValueError(f"Unknown client_id: {client_id}")

    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope: {scope}. Must be one of {VALID_SCOPES}")

    code = secrets.token_hex(20)
    auth_codes[code] = {
        "client_id": client_id,
        "user_id": user_id,
        "scope": scope,
        "expires_at": time.time() + CODE_EXPIRY_SECONDS,
        "used": False,
    }

    return code


def exchange_token(client_id: str, client_secret: str, code: str) -> dict:
    """Exchange an authorization code for an access token.

    Args:
        client_id: The registered client's ID.
        client_secret: The client's secret for verification.
        code: The authorization code to exchange.

    Returns:
        A dict with access_token, token_type, expires_in, and scope.

    Raises:
        ValueError: If the code is invalid/expired/used or client_secret is wrong.
    """
    if code not in auth_codes:
        raise ValueError("Invalid authorization code")

    code_data = auth_codes[code]

    if code_data["used"]:
        raise ValueError("Authorization code has already been used")

    if code_data["expires_at"] < time.time():
        raise ValueError("Authorization code has expired")

    if code_data["client_id"] != client_id:
        raise ValueError("Authorization code was not issued to this client")

    if client_id not in clients:
        raise ValueError(f"Unknown client_id: {client_id}")

    if clients[client_id]["client_secret"] != client_secret:
        raise ValueError("Invalid client_secret")

    # Mark code as used (single-use)
    code_data["used"] = True

    access_token = secrets.token_hex(32)
    token_info = {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 3600,
        "scope": code_data["scope"],
    }

    tokens[access_token] = {
        **token_info,
        "client_id": client_id,
        "user_id": code_data["user_id"],
    }

    return token_info


def validate_token(access_token: str) -> dict:
    """Validate an access token and return its associated information.

    Args:
        access_token: The token to validate.

    Returns:
        A dict with user_id, scope, and client_id.

    Raises:
        ValueError: If the token is invalid, expired, or has been revoked.
    """
    if access_token not in tokens:
        raise ValueError("Invalid or revoked access token")

    token_data = tokens[access_token]

    return {
        "user_id": token_data["user_id"],
        "scope": token_data["scope"],
        "client_id": token_data["client_id"],
    }


def revoke_token(access_token: str) -> bool:
    """Revoke an access token by removing it from storage.

    Args:
        access_token: The token to revoke.

    Returns:
        True if the token was found and revoked, False if not found.
    """
    if access_token not in tokens:
        return False

    del tokens[access_token]
    return True
