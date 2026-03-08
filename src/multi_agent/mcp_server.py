"""MyGO MCP Server — expose multi-agent orchestration as MCP tools.

Allows any MCP client (Claude Desktop, Cursor, Windsurf, etc.) to:
- Start tasks (go)
- Check status
- Cancel tasks
- List task history
- Read dashboard

Usage:
    # stdio transport (for IDE integration)
    python -m multi_agent.mcp_server

    # Or via fastmcp CLI
    fastmcp run multi_agent.mcp_server:mcp
"""

from __future__ import annotations

import json
import logging
from typing import Any

import yaml
from fastmcp import FastMCP

from multi_agent._utils import SAFE_TASK_ID_RE as _SAFE_ID_RE
from multi_agent.config import (
    history_dir,
    root_dir,
    tasks_dir,
    workspace_dir,
)
from multi_agent.workspace import read_lock

_log = logging.getLogger(__name__)

_MAX_TASK_LIST = 100


def _is_safe_id(task_id: str) -> bool:
    """Validate task_id against path traversal."""
    return bool(_SAFE_ID_RE.match(task_id)) and ".." not in task_id

mcp = FastMCP(
    name="MyGO",
    instructions=(
        "MyGO is a multi-agent orchestration system. "
        "Use these tools to manage AI coding tasks: start tasks, check status, "
        "view history, and monitor the dashboard."
    ),
)


# ── Tools ────────────────────────────────────────────────


@mcp.tool()
def task_status() -> dict[str, Any]:
    """Get current active task status including pipeline stage, builder, reviewer, and retry count."""
    active = read_lock()
    if not active:
        return {"active": False, "message": "No active task."}

    # Validate lock content before using as file path
    if not _is_safe_id(active):
        return {"active": True, "task_id": active, "error": "invalid task_id in lock"}

    # Read task YAML for details
    task_data = _read_task_yaml(active)
    dashboard_md = _read_dashboard()

    result: dict[str, Any] = {
        "active": True,
        "task_id": active,
        "status": task_data.get("status", "unknown"),
        "skill": task_data.get("skill", ""),
        "dashboard": dashboard_md[:500] if dashboard_md else "",
    }

    # Try to get graph state for richer info
    try:
        from multi_agent.graph import compile_graph
        from multi_agent.orchestrator import get_task_status

        app = compile_graph()
        ts = get_task_status(app, active)
        result.update({
            "state": ts.state,
            "waiting_role": ts.waiting_role,
            "waiting_agent": ts.waiting_agent,
            "retry_count": ts.values.get("retry_count", 0),
            "retry_budget": ts.values.get("retry_budget", 2),
            "final_status": ts.final_status,
        })
    except Exception:
        result["graph_error"] = "failed to read graph state"

    return result


@mcp.tool()
def task_list(limit: int = 20, status_filter: str = "") -> dict[str, Any]:
    """List task history with optional status filter.

    Args:
        limit: Maximum number of tasks to return (default 20)
        status_filter: Filter by status: 'active', 'approved', 'failed', 'cancelled', or empty for all
    """
    # Cap limit to prevent resource exhaustion
    limit = max(1, min(limit, _MAX_TASK_LIST))

    td = tasks_dir()
    if not td.exists():
        return {"tasks": [], "count": 0}

    tasks: list[dict[str, Any]] = []
    for f in sorted(td.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True):
        if len(tasks) >= limit:
            break
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        task_status_val = data.get("status", "unknown")
        if status_filter and task_status_val != status_filter:
            continue
        tasks.append({
            "task_id": data.get("task_id", f.stem),
            "status": task_status_val,
            "skill": data.get("skill", ""),
            "requirement": data.get("requirement", ""),
        })

    return {"tasks": tasks, "count": len(tasks)}


@mcp.tool()
def task_detail(task_id: str) -> dict[str, Any]:
    """Get detailed information about a specific task including trace events.

    Args:
        task_id: The task ID to look up (e.g. 'task-abc123')
    """
    if not _is_safe_id(task_id):
        return {"error": f"Invalid task_id: {task_id}"}

    task_data = _read_task_yaml(task_id)
    trace_events = _read_trace_events(task_id)

    return {
        "task_id": task_id,
        "task_data": task_data,
        "trace_events": trace_events[:50],
        "trace_count": len(trace_events),
    }


@mcp.tool()
def dashboard() -> dict[str, str]:
    """Read the current dashboard content (markdown format with task progress, status, and conversation history)."""
    content = _read_dashboard()
    active = read_lock()
    return {
        "active_task": active or "",
        "content": content or "No active task.",
        "project": str(root_dir()),
    }


@mcp.tool()
def task_cancel() -> dict[str, str]:
    """Cancel the currently active task and release the lock."""
    active = read_lock()
    if not active:
        return {"status": "no_task", "message": "No active task to cancel."}
    if not _is_safe_id(active):
        return {"status": "error", "message": "Invalid task_id in lock file."}

    try:
        from multi_agent.workspace import release_lock, save_task_yaml
        save_task_yaml(active, {"status": "cancelled", "task_id": active})
        release_lock()
        return {"status": "cancelled", "task_id": active, "message": f"Task {active} cancelled."}
    except Exception:
        return {"status": "error", "message": "Failed to cancel task."}


@mcp.tool()
def project_info() -> dict[str, Any]:
    """Get project information: root directory, workspace status, agent configuration."""
    ws = workspace_dir()
    agents_file = root_dir() / "agents" / "agents.yaml"

    agents: list[dict[str, str]] = []
    if agents_file.exists():
        try:
            data = yaml.safe_load(agents_file.read_text(encoding="utf-8")) or {}
            for a in data.get("agents", []):
                if isinstance(a, dict):
                    agents.append({
                        "id": a.get("id", "?"),
                        "driver": a.get("driver", "file"),
                    })
        except Exception:
            pass

    return {
        "root": str(root_dir()),
        "workspace": str(ws),
        "workspace_exists": ws.exists(),
        "lock": read_lock(),
        "agents": agents,
    }


# ── Resources ────────────────────────────────────────────


@mcp.resource("mygo://dashboard")
def resource_dashboard() -> str:
    """Current dashboard markdown content."""
    return _read_dashboard() or "No active task."


@mcp.resource("mygo://status")
def resource_status() -> str:
    """Current task status as JSON."""
    return json.dumps(task_status(), ensure_ascii=False, indent=2)


# ── Helpers ──────────────────────────────────────────────


def _read_task_yaml(task_id: str) -> dict[str, Any]:
    """Read task YAML file, trying multiple naming patterns.

    Caller must validate task_id before calling.
    """
    if not _is_safe_id(task_id):
        return {}
    td = tasks_dir()
    for name in [f"{task_id}.yaml", f"task-{task_id}.yaml"]:
        path = td / name
        if path.exists():
            try:
                return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                return {}
    return {}


def _read_dashboard() -> str:
    """Read dashboard.md content."""
    path = workspace_dir() / "dashboard.md"
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


def _read_trace_events(task_id: str) -> list[dict[str, Any]]:
    """Read JSONL trace events for a task.

    Caller must validate task_id before calling.
    """
    if not _is_safe_id(task_id):
        return []
    hdir = history_dir()
    for pattern in [
        f"{task_id}.events.jsonl",
        f"task-{task_id}.events.jsonl",
        f"{task_id}.jsonl",
        f"task-{task_id}.jsonl",
    ]:
        path = hdir / pattern
        if path.exists():
            events: list[dict[str, Any]] = []
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass
            return events
    return []


# ── Entry Point ──────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
