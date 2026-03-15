"""Agent registry — load and look up agent profiles.

This module owns the agent-loading logic (previously in router.py).
It depends only on Layer 0-1 (config, schema) and has NO dependency on
router.py or driver.py, breaking the circular import chain.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from multi_agent.config import agents_profile_path, load_yaml, root_dir
from multi_agent.schema import AgentProfile

_log = logging.getLogger(__name__)

# ── Registry Loading ──────────────────────────────────────


def _agents_yaml_path() -> Path:
    return root_dir() / "agents" / "agents.yaml"


def load_registry(path: Path | None = None) -> dict[str, Any]:
    """Load agents.yaml v2 registry. Falls back to profiles.json."""
    yaml_path = path or _agents_yaml_path()
    if yaml_path.exists():
        data = load_yaml(yaml_path)
    else:
        # Fallback: legacy profiles.json
        json_path = agents_profile_path()
        if json_path.exists():
            with json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
    # Normalize: ensure all expected keys exist
    data.setdefault("version", 1)
    data.setdefault("agents", [])
    data.setdefault("role_strategy", "auto" if data["version"] < 2 else "manual")
    data.setdefault("defaults", {})
    return data


def load_agents(path: Path | None = None) -> list[AgentProfile]:
    """Load agent profiles from registry."""
    reg = load_registry(path)
    agents_data = reg.get("agents", [])
    result = []
    for a in agents_data:
        if not isinstance(a, dict) or "id" not in a:
            continue  # skip malformed entries
        try:
            # agents.yaml v2 uses simpler format (no reliability/cost fields required)
            profile = AgentProfile(
                id=a["id"],
                driver=a.get("driver", "file"),
                command=a.get("command", ""),
                app_name=a.get("app_name", ""),
                auth_check=a.get("auth_check", ""),
                login_hint=a.get("login_hint", ""),
                required_env=a.get("required_env", []),
                capabilities=a.get("capabilities", []),
                reliability=a.get("reliability", 0.9),
                queue_health=a.get("queue_health", 0.9),
                cost=a.get("cost", 0.5),
            )
            result.append(profile)
        except Exception as exc:
            _log.warning("Skipping malformed agent entry %r: %s", a.get("id", a), exc)
            continue
    return result


def get_agent(agent_id: str, path: Path | None = None) -> AgentProfile:
    """Look up a single agent by ID. Raises KeyError if not found."""
    for agent in load_agents(path):
        if agent.id == agent_id:
            return agent
    raise KeyError(f"Agent '{agent_id}' not found in registry")


def list_agents(path: Path | None = None) -> list[AgentProfile]:
    """Alias for load_agents() — returns all registered agent profiles."""
    return load_agents(path)


def get_defaults(path: Path | None = None) -> dict[str, Any]:
    """Get default role assignments from registry."""
    reg = load_registry(path)
    result = reg.get("defaults", {})
    return result if isinstance(result, dict) else {}


def get_strategy(path: Path | None = None) -> str:
    """Get role assignment strategy: 'manual' or 'auto'."""
    reg = load_registry(path)
    return str(reg.get("role_strategy", "manual"))
