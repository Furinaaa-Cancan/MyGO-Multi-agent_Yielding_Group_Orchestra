"""Agent router — IDE-agnostic role assignment.

Supports two strategies:
  - manual: user specifies which IDE fills each role (default, best UX)
  - auto:   system picks based on capabilities (legacy)

Works with ANY IDE: Windsurf, Cursor, Codex, Kiro, Antigravity, Copilot, Aider, ...
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from multi_agent.config import agents_profile_path, load_yaml, root_dir
from multi_agent.schema import AgentProfile, SkillContract

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


def get_defaults(path: Path | None = None) -> dict[str, Any]:
    """Get default role assignments from registry."""
    reg = load_registry(path)
    result = reg.get("defaults", {})
    return result if isinstance(result, dict) else {}


def get_strategy(path: Path | None = None) -> str:
    """Get role assignment strategy: 'manual' or 'auto'."""
    reg = load_registry(path)
    return str(reg.get("role_strategy", "manual"))


# ── Role Assignment ───────────────────────────────────────

def resolve_builder(
    agents: list[AgentProfile],
    contract: SkillContract,
    explicit: str | None = None,
) -> str:
    """Resolve which IDE fills the builder role.

    Priority: explicit flag > defaults > auto-pick.
    Returns agent ID string (not AgentProfile — we don't need metadata for role-based flow).
    """
    if explicit:
        known_ids = {a.id for a in agents}
        if known_ids and explicit not in known_ids:
            _log.warning(
                "Explicit builder '%s' not in registry %s — task may hang if agent unavailable.",
                explicit, sorted(known_ids),
            )
        return explicit

    defaults = get_defaults()
    if defaults.get("builder"):
        return str(defaults["builder"])

    # Auto fallback: pick by capabilities
    candidates = _eligible(agents, contract, ["implementation"])
    if candidates:
        return candidates[0].id

    # Last resort: any agent (bypasses health filter)
    if agents:
        _log.warning(
            "No healthy agent with 'implementation' capability — falling back to '%s' "
            "(health/capability filters bypassed).",
            agents[0].id,
        )
        return agents[0].id
    raise ValueError("No agent configured for builder role")


def resolve_reviewer(
    agents: list[AgentProfile],
    contract: SkillContract,
    builder_id: str,
    explicit: str | None = None,
) -> str:
    """Resolve which IDE fills the reviewer role (must differ from builder).

    Priority: explicit flag > defaults > auto-pick.
    """
    if explicit:
        if explicit == builder_id:
            from multi_agent.driver import get_agent_driver
            drv = get_agent_driver(explicit)
            if drv["driver"] == "file":
                raise ValueError(
                    f"Reviewer cannot be the same as builder ({builder_id}) in file mode. "
                    f"Cross-model adversarial review requires different IDEs."
                )
            _log.info("Same agent '%s' for builder and reviewer (cli/gui mode)", builder_id)
        known_ids = {a.id for a in agents}
        if known_ids and explicit not in known_ids:
            _log.warning(
                "Explicit reviewer '%s' not in registry %s — task may hang if agent unavailable.",
                explicit, sorted(known_ids),
            )
        return explicit

    defaults = get_defaults()
    default_reviewer = defaults.get("reviewer")
    if default_reviewer and default_reviewer != builder_id:
        return str(default_reviewer)

    # Auto fallback: pick by capabilities, exclude builder
    candidates = _eligible(agents, contract, ["review"], exclude=[builder_id])
    if candidates:
        return candidates[0].id

    # Last resort: any agent that isn't the builder
    others = [a for a in agents if a.id != builder_id]
    if others:
        _log.warning(
            "No healthy agent with 'review' capability — falling back to '%s' "
            "(health/capability filters bypassed).",
            others[0].id,
        )
        return others[0].id

    raise ValueError(
        f"No agent available for reviewer role (builder={builder_id}). "
        f"Add at least 2 agents to agents/agents.yaml."
    )


# ── Internal helpers ──────────────────────────────────────

def _eligible(
    agents: list[AgentProfile],
    contract: SkillContract,
    required_capabilities: list[str],
    exclude: list[str] | None = None,
) -> list[AgentProfile]:
    """Filter agents by contract compatibility and capabilities."""
    exclude = exclude or []
    candidates: list[AgentProfile] = []
    for agent in agents:
        if agent.id in exclude:
            continue
        if contract.supported_agents and agent.id not in contract.supported_agents:
            continue
        if not all(cap in agent.capabilities for cap in required_capabilities):
            continue
        candidates.append(agent)
    # Filter out agents with critically low health score (literature: health-based routing)
    MIN_HEALTH_SCORE = 0.3
    candidates = [a for a in candidates if a.reliability * a.queue_health >= MIN_HEALTH_SCORE]
    candidates.sort(key=lambda a: (a.reliability * a.queue_health, -a.cost), reverse=True)
    return candidates


def _check_cli_available(command: str) -> tuple[bool, str]:
    """Check if CLI binary is available on PATH. Returns (available, binary_name)."""
    import shutil
    binary = command.split()[0] if command.strip() else ""
    if not binary:
        return False, ""
    return shutil.which(binary) is not None, binary


def _missing_required_env(agent: AgentProfile) -> list[str]:
    missing: list[str] = []
    for key in agent.required_env:
        if not isinstance(key, str):
            continue
        env_key = key.strip()
        if not env_key:
            continue
        if not os.environ.get(env_key):
            missing.append(env_key)
    return missing


def _run_auth_check(cmd: str, *, timeout_sec: int = 6) -> tuple[str, str]:
    """Run auth check command. Returns (status, detail).

    status:
      - ready: check command exited 0
      - failed: check command exited non-zero
      - timeout: command timed out
      - invalid: command parse failed
      - error: command execution failed
    """
    try:
        cmd_list = shlex.split(cmd)
    except ValueError as exc:
        return "invalid", f"invalid auth_check command: {exc}"
    if not cmd_list:
        return "invalid", "empty auth_check command"

    try:
        proc = subprocess.run(
            cmd_list,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout", f"auth_check timed out after {timeout_sec}s"
    except OSError as exc:
        return "error", f"auth_check execution error: {exc}"

    if proc.returncode == 0:
        return "ready", "auth_check passed"
    stderr = (proc.stderr or "").strip()
    stdout = (proc.stdout or "").strip()
    detail = stderr or stdout or f"auth_check exited {proc.returncode}"
    return "failed", detail[:300]


def probe_agent_readiness(agent: AgentProfile, *, timeout_sec: int = 6) -> dict[str, Any]:
    """Probe runtime readiness for a single agent.

    Designed for UX diagnostics and fail-fast routing decisions.
    """
    result: dict[str, Any] = {
        "id": agent.id,
        "driver": agent.driver,
        "ready": True,
        "status": "ready",
        "issues": [],
        "warnings": [],
        "login_hint": agent.login_hint or "",
    }

    if agent.driver == "file":
        result["status"] = "manual"
        return result

    if agent.driver == "gui":
        if not agent.app_name:
            result["ready"] = False
            result["status"] = "misconfigured"
            result["issues"].append("GUI driver but no app_name configured")
        else:
            result["status"] = "gui_ready"
        return result

    if agent.driver != "cli":
        result["ready"] = False
        result["status"] = "misconfigured"
        result["issues"].append(f"unknown driver: {agent.driver}")
        return result

    if not agent.command:
        result["ready"] = False
        result["status"] = "misconfigured"
        result["issues"].append("CLI driver but no command configured")
        return result

    available, binary = _check_cli_available(agent.command)
    result["cli_binary"] = binary
    result["cli_available"] = available
    if not available:
        result["ready"] = False
        result["status"] = "binary_missing"
        result["issues"].append(f"CLI binary '{binary}' not found on PATH")
        return result

    missing_env = _missing_required_env(agent)
    result["missing_env"] = missing_env
    if missing_env:
        result["ready"] = False
        result["status"] = "missing_env"
        result["issues"].append(f"missing required env: {', '.join(missing_env)}")
        return result

    auth_check = agent.auth_check.strip()
    if not auth_check:
        result["status"] = "ready_unverified"
        result["warnings"].append("auth_check not configured; login status not verified")
        return result

    auth_status, detail = _run_auth_check(auth_check, timeout_sec=timeout_sec)
    result["auth_check"] = auth_check
    result["auth_check_status"] = auth_status
    result["auth_check_detail"] = detail
    if auth_status == "ready":
        result["status"] = "ready"
        return result

    result["ready"] = False
    result["status"] = "auth_failed"
    result["issues"].append(f"auth_check failed: {detail}")
    return result


def get_agent_profile(agent_id: str, path: Path | None = None) -> AgentProfile | None:
    for agent in load_agents(path):
        if agent.id == agent_id:
            return agent
    return None


def _check_single_agent(agent: AgentProfile) -> dict[str, Any]:
    """Check health of a single agent. Returns info dict with status and issues."""
    issues: list[str] = []
    warnings: list[str] = []
    info: dict[str, Any] = {"id": agent.id, "driver": agent.driver, "capabilities": agent.capabilities}

    if agent.reliability < 0.5:
        issues.append(f"low reliability: {agent.reliability}")
    if agent.queue_health < 0.5:
        issues.append(f"low queue_health: {agent.queue_health}")
    if not agent.capabilities:
        issues.append("no capabilities defined")

    if agent.driver == "cli":
        if not agent.command:
            issues.append("CLI driver but no command configured")
            info["cli_available"] = False
        else:
            available, binary = _check_cli_available(agent.command)
            info["cli_available"] = available
            info["cli_binary"] = binary
            if not available:
                issues.append(f"CLI binary '{binary}' not found on PATH")
            else:
                readiness = probe_agent_readiness(agent)
                info["readiness"] = readiness
                info["auth_status"] = readiness.get("status")
                for warn in readiness.get("warnings", []):
                    warnings.append(str(warn))
                for issue in readiness.get("issues", []):
                    issues.append(str(issue))
    elif agent.driver == "gui":
        if not agent.app_name:
            issues.append("GUI driver but no app_name configured")
        info["app_name"] = agent.app_name
    elif agent.driver == "file":
        info["mode"] = "manual"

    info["status"] = "healthy" if not issues else "degraded"
    info["issues"] = issues
    info["warnings"] = warnings
    return info


def check_agent_health(agents: list[AgentProfile]) -> list[dict[str, Any]]:
    """Check health of all registered agents.

    Returns list of {id, status, driver, issues, cli_available, capabilities} for each agent.
    """
    results = [_check_single_agent(a) for a in agents]
    if len(agents) < 2:
        results.append({
            "id": "_system", "status": "warning", "driver": "system",
            "capabilities": [], "issues": [
                "Only 1 agent configured. Cross-model adversarial review "
                "requires ≥2 agents backed by different LLMs (Brilliant 2026)."
            ],
        })
    return results


# Legacy API kept for test compatibility
def eligible_agents(
    agents: list[AgentProfile],
    contract: SkillContract,
    required_capabilities: list[str],
    role: str = "builder",
) -> list[AgentProfile]:
    return _eligible(agents, contract, required_capabilities)


def pick_agent(
    agents: list[AgentProfile],
    contract: SkillContract,
    required_capabilities: list[str],
    role: str = "builder",
    exclude: list[str] | None = None,
) -> AgentProfile:
    candidates = _eligible(agents, contract, required_capabilities, exclude)
    if not candidates:
        raise ValueError(
            f"No eligible agent for skill={contract.id}, "
            f"caps={required_capabilities}, role={role}, exclude={exclude}"
        )
    return candidates[0]


def pick_reviewer(
    agents: list[AgentProfile],
    contract: SkillContract,
    builder_id: str,
) -> AgentProfile:
    return pick_agent(
        agents, contract, ["review"], role="reviewer", exclude=[builder_id],
    )
