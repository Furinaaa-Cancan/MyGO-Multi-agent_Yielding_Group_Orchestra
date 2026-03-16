"""Agent router — IDE-agnostic role assignment.

Supports two strategies:
  - manual: user specifies which IDE fills each role (default, best UX)
  - auto:   system picks based on capabilities (legacy)

Works with ANY IDE: Windsurf, Cursor, Codex, Kiro, Antigravity, Copilot, Aider, ...
"""

from __future__ import annotations

import logging
import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

# ── Backward-compat re-exports from agent_registry ────────
# These were moved to multi_agent.agent_registry to break the
# circular dependency between router.py and driver.py.
from multi_agent.agent_registry import (  # noqa: F401  # backward compat
    get_defaults,
    get_strategy,
    load_agents,
    load_registry,
)
from multi_agent.schema import AgentProfile, SkillContract

_log = logging.getLogger(__name__)


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
    candidates.sort(key=lambda a: (-(a.reliability * a.queue_health), a.cost))
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


def _default_login_hint_for_binary(binary: str) -> str:
    b = binary.strip().lower()
    if not b:
        return ""
    if b == "claude":
        return "Run: claude auth login (or configure ANTHROPIC_API_KEY)"
    if b == "aider":
        return "Configure OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY, then retry"
    if b == "codex":
        return "Run: codex login (or configure OPENAI_API_KEY)"
    if b == "gemini":
        return "Configure GEMINI_API_KEY (or run provider login flow)"
    return f"Check '{binary}' CLI authentication setup"


def _default_login_hint_for_gui(app_name: str) -> str:
    target = app_name.strip() or "GUI agent"
    return (
        f"Ensure '{target}' is installed and grant Accessibility permission to your terminal "
        "(System Settings → Privacy & Security → Accessibility)"
    )


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
    login_hint = str(agent.login_hint or "").strip()
    result: dict[str, Any] = {
        "id": agent.id,
        "driver": agent.driver,
        "ready": True,
        "status": "ready",
        "issues": [],
        "warnings": [],
        "login_hint": login_hint,
    }

    if agent.driver == "file":
        result["status"] = "manual"
        return result

    if agent.driver == "gui":
        if not agent.app_name:
            result["ready"] = False
            result["status"] = "misconfigured"
            result["issues"].append("GUI driver but no app_name configured")
            result["login_hint"] = result["login_hint"] or _default_login_hint_for_gui(agent.app_name)
        else:
            if platform.system() != "Darwin":
                result["ready"] = False
                result["status"] = "gui_unavailable"
                result["issues"].append("GUI driver requires macOS (osascript)")
                result["login_hint"] = result["login_hint"] or _default_login_hint_for_gui(agent.app_name)
            elif shutil.which("osascript") is None:
                result["ready"] = False
                result["status"] = "gui_unavailable"
                result["issues"].append("osascript not found on PATH")
                result["login_hint"] = result["login_hint"] or _default_login_hint_for_gui(agent.app_name)
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
    if not result["login_hint"]:
        result["login_hint"] = _default_login_hint_for_binary(binary)
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
            readiness = probe_agent_readiness(agent)
            info["readiness"] = readiness
            info["auth_status"] = readiness.get("status")
            info["cli_available"] = bool(readiness.get("cli_available", False))
            info["cli_binary"] = readiness.get("cli_binary", "")
            for warn in readiness.get("warnings", []):
                warnings.append(str(warn))
            for issue in readiness.get("issues", []):
                issues.append(str(issue))
    elif agent.driver == "gui":
        readiness = probe_agent_readiness(agent)
        info["readiness"] = readiness
        info["auth_status"] = readiness.get("status")
        info["app_name"] = agent.app_name
        for warn in readiness.get("warnings", []):
            warnings.append(str(warn))
        for issue in readiness.get("issues", []):
            issues.append(str(issue))
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
