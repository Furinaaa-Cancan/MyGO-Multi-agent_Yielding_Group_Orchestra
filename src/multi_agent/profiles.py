"""Config Profiles — named presets for common task configurations.

Profiles are defined in ``.ma.yaml`` under the ``profiles:`` key::

    profiles:
      fast:
        retry_budget: 0
        timeout: 600
        builder: windsurf
        reviewer: windsurf
      thorough:
        retry_budget: 5
        timeout: 3600
        reviewer: codex
      solo:
        retry_budget: 1
        builder: windsurf
        reviewer: windsurf

Usage::

    my go "task" --profile fast
    my go "task" --profile thorough
"""

from __future__ import annotations

import re
from typing import Any

from multi_agent.config import load_project_config

_SAFE_PROFILE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,31}$")

_PROFILE_FIELDS = frozenset({
    "retry_budget", "timeout", "builder", "reviewer",
    "skill", "mode", "decompose", "visible",
})


class ProfileNotFoundError(Exception):
    """Raised when a profile name doesn't exist."""


def load_profiles() -> dict[str, dict[str, Any]]:
    """Load all profiles from .ma.yaml."""
    proj = load_project_config()
    raw = proj.get("profiles")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, cfg in raw.items():
        if isinstance(cfg, dict) and _SAFE_PROFILE_RE.match(str(name)):
            # Filter to known fields only
            result[str(name)] = {k: v for k, v in cfg.items() if k in _PROFILE_FIELDS}
    return result


def get_profile(name: str) -> dict[str, Any]:
    """Get a single profile by name.

    Raises:
        ProfileNotFoundError: If profile doesn't exist.
    """
    if not _SAFE_PROFILE_RE.match(name):
        raise ProfileNotFoundError(f"Invalid profile name: {name!r}")

    profiles = load_profiles()
    if name not in profiles:
        available = ", ".join(sorted(profiles.keys())) if profiles else "(none)"
        raise ProfileNotFoundError(
            f"Profile '{name}' not found. Available: {available}"
        )
    return profiles[name]


def list_profile_names() -> list[str]:
    """Return sorted list of available profile names."""
    return sorted(load_profiles().keys())
