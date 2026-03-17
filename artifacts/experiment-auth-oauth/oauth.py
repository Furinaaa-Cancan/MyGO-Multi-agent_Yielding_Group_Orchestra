"""OAuth2 Authorization Code Flow simulation module (in-memory storage)."""

import secrets
import time

VALID_SCOPES = {"read", "write", "admin"}
CODE_EXPIRY_SECONDS = 300  # 5 minutes
TOKEN_EXPIRY_SECONDS = 3600

# In-memory stores
_clients: dict[str, dict] = {}
_auth_codes: dict[str, dict] = {}
_tokens: dict[str, dict] = {}


def register_client(client_name: str, redirect_uri: str) -> dict:
    """Register a new OAuth2 client."""
    client_id = secrets.token_hex(16)
    client_secret = secrets.token_hex(16)
    _clients[client_id] = {
        "client_name": client_name,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }


def authorize(client_id: str, user_id: str, scope: str = "read") -> str:
    """Issue an authorization code for a valid client and scope."""
    if client_id not in _clients:
        raise ValueError("Invalid client_id")
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope: {scope}. Must be one of {VALID_SCOPES}")

    code = secrets.token_hex(16)
    _auth_codes[code] = {
        "client_id": client_id,
        "user_id": user_id,
        "scope": scope,
        "created_at": time.time(),
        "used": False,
    }
    return code


def exchange_token(client_id: str, client_secret: str, code: str) -> dict:
    """Exchange an authorization code for an access token."""
    if code not in _auth_codes:
        raise ValueError("Invalid authorization code")

    code_data = _auth_codes[code]

    if code_data["used"]:
        raise ValueError("Authorization code already used")

    if code_data["client_id"] != client_id:
        raise ValueError("Invalid authorization code")

    if time.time() - code_data["created_at"] > CODE_EXPIRY_SECONDS:
        raise ValueError("Authorization code expired")

    client = _clients.get(client_id)
    if client is None or client["client_secret"] != client_secret:
        raise ValueError("Invalid client_secret")

    # Mark code as used
    code_data["used"] = True

    access_token = secrets.token_hex(16)
    _tokens[access_token] = {
        "user_id": code_data["user_id"],
        "scope": code_data["scope"],
        "client_id": client_id,
        "created_at": time.time(),
    }

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": TOKEN_EXPIRY_SECONDS,
        "scope": code_data["scope"],
    }


def validate_token(access_token: str) -> dict:
    """Validate an access token and return its associated data."""
    if access_token not in _tokens:
        raise ValueError("Invalid access token")

    token_data = _tokens[access_token]
    return {
        "user_id": token_data["user_id"],
        "scope": token_data["scope"],
        "client_id": token_data["client_id"],
    }


def revoke_token(access_token: str) -> bool:
    """Revoke an access token. Returns True if revoked, False if not found."""
    if access_token not in _tokens:
        return False
    del _tokens[access_token]
    return True
