"""OAuth2 authorization code flow simulation — in-memory."""
from __future__ import annotations

import secrets
import time

_clients: dict[str, dict] = {}
_auth_codes: dict[str, dict] = {}
_tokens: dict[str, dict] = {}

_VALID_SCOPES = {"read", "write", "admin"}
_CODE_EXPIRY = 300  # 5 minutes


def register_client(client_name: str, redirect_uri: str) -> dict:
    """Register an OAuth2 client."""
    client_id = secrets.token_hex(16)
    client_secret = secrets.token_hex(16)
    client = {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_name": client_name,
        "redirect_uri": redirect_uri,
    }
    _clients[client_id] = client
    return dict(client)


def authorize(client_id: str, user_id: str, scope: str = "read") -> str:
    """Generate an authorization code."""
    if client_id not in _clients:
        raise ValueError(f"Invalid client_id: {client_id!r}")
    if scope not in _VALID_SCOPES:
        raise ValueError(f"Invalid scope: {scope!r}. Must be one of {_VALID_SCOPES}")
    code = secrets.token_hex(16)
    _auth_codes[code] = {
        "client_id": client_id,
        "user_id": user_id,
        "scope": scope,
        "expires_at": time.time() + _CODE_EXPIRY,
        "used": False,
    }
    return code


def exchange_token(client_id: str, client_secret: str, code: str) -> dict:
    """Exchange authorization code for access token."""
    code_data = _auth_codes.get(code)
    if code_data is None or code_data["used"]:
        raise ValueError("Invalid or already used authorization code")
    if code_data["client_id"] != client_id:
        raise ValueError("Client ID mismatch")
    client = _clients.get(client_id)
    if client is None or client["client_secret"] != client_secret:
        raise ValueError("Invalid client credentials")
    if code_data["expires_at"] < time.time():
        raise ValueError("Authorization code expired")
    code_data["used"] = True
    access_token = secrets.token_hex(32)
    _tokens[access_token] = {
        "user_id": code_data["user_id"],
        "scope": code_data["scope"],
        "client_id": client_id,
        "created_at": time.time(),
    }
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 3600,
        "scope": code_data["scope"],
    }


def validate_token(access_token: str) -> dict:
    """Validate an access token and return associated info."""
    token_data = _tokens.get(access_token)
    if token_data is None:
        raise ValueError("Invalid access token")
    return {
        "user_id": token_data["user_id"],
        "scope": token_data["scope"],
        "client_id": token_data["client_id"],
    }


def revoke_token(access_token: str) -> bool:
    """Revoke an access token."""
    if access_token in _tokens:
        del _tokens[access_token]
        return True
    return False
