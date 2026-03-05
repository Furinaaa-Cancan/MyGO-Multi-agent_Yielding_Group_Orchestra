"""Shared utility functions used across multiple modules.

Extracted to eliminate DRY violations where identical helpers were
independently implemented in session.py, cli.py, and other modules.
"""

from __future__ import annotations

import re
from datetime import UTC
from typing import Any

# ── Input Validation ──────────────────────────────────────

SAFE_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
SAFE_AGENT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

TERMINAL_STATES = frozenset({"DONE", "FAILED", "ESCALATED", "CANCELLED"})

TERMINAL_FINAL_STATUSES = frozenset(
    {"approved", "failed", "cancelled", "escalated", "done"}
)


def validate_task_id(task_id: str) -> None:
    """Raise ValueError if task_id is unsafe (path traversal, invalid chars)."""
    if not SAFE_TASK_ID_RE.match(task_id):
        raise ValueError(
            f"invalid task_id: {task_id!r} — "
            f"must match [a-z0-9][a-z0-9-]{{2,63}}"
        )


def validate_agent_id(agent_id: str) -> None:
    """Raise ValueError if agent_id is unsafe (path traversal, invalid chars).

    Agent IDs are used in file paths (inbox/{agent_id}.md, outbox/{agent_id}.json)
    so must be validated to prevent directory traversal attacks.
    """
    if not SAFE_AGENT_ID_RE.match(agent_id):
        raise ValueError(
            f"invalid agent_id: {agent_id!r} — "
            f"must match [a-zA-Z0-9][a-zA-Z0-9._-]{{0,63}}"
        )


# ── Type Coercion ─────────────────────────────────────────

def positive_int(value: Any, default: int) -> int:
    """Coerce *value* to a positive int, returning *default* on failure."""
    try:
        iv = int(value)
        return iv if iv > 0 else default
    except (TypeError, ValueError):
        return default


def count_nonempty_entries(value: Any) -> int:
    """Count non-empty items in a list (strings or dicts)."""
    if not isinstance(value, list):
        return 0
    count = 0
    for item in value:
        if (isinstance(item, str) and item.strip()) or (isinstance(item, dict) and item):
            count += 1
    return count


def is_terminal_final_status(value: object) -> bool:
    """Check if a final_status string represents a terminal state."""
    if not isinstance(value, str):
        return False
    return value.strip().lower() in TERMINAL_FINAL_STATUSES


# ── Timestamps ────────────────────────────────────────────

def now_utc() -> str:
    """Return current UTC time as ISO-8601 string (seconds precision)."""
    from datetime import datetime
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── Review Policy Constants ───────────────────────────────

DEFAULT_RUBBER_STAMP_PHRASES: frozenset[str] = frozenset({
    "lgtm",
    "looks good",
    "no issues",
    "approved",
    "all good",
    "ship it",
    "good to go",
    "looks fine",
    "no comments",
})
