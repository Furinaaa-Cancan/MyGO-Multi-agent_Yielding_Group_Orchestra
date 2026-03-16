"""MyGO MCP Server — expose multi-agent orchestration as MCP tools.

Allows any MCP client (Claude Desktop, Cursor, Windsurf, etc.) to:
- Check task status and pipeline stage
- List task history with filtering
- View task details and trace events
- Read dashboard content
- Cancel active tasks
- Submit reviews (approve/reject/request_changes)
- Store and search semantic memory
- Query project configuration

Usage:
    # stdio transport (for IDE integration)
    python -m multi_agent.mcp_server

    # Or via fastmcp CLI
    fastmcp run multi_agent.mcp_server:mcp
"""

from __future__ import annotations

import json
import logging
import time
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
from multi_agent.workspace import read_lock, write_outbox

_log = logging.getLogger(__name__)

_MAX_TASK_LIST = 100
_MAX_FILE_READ = 1024 * 1024  # 1 MB cap for file reads
_VALID_STATUSES = frozenset({"active", "approved", "done", "failed", "cancelled", "escalated", "unknown"})


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

    if status_filter and status_filter not in _VALID_STATUSES:
        return {"tasks": [], "count": 0, "error": f"Invalid status_filter: {status_filter!r}. Valid: {sorted(_VALID_STATUSES)}"}

    def _safe_mtime(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    tasks: list[dict[str, Any]] = []
    for f in sorted(td.glob("*.yaml"), key=_safe_mtime, reverse=True):
        if len(tasks) >= limit:
            break
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        task_status_val = data.get("status", "unknown")
        if status_filter:
            if task_status_val != status_filter:
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
        return {"error": "Invalid task_id format."}

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
        from multi_agent.workspace import release_lock, update_task_yaml
        update_task_yaml(active, {"status": "cancelled"})
        release_lock()
        return {"status": "cancelled", "task_id": active, "message": f"Task {active} cancelled."}
    except Exception:
        return {"status": "error", "message": "Failed to cancel task."}


@mcp.tool()
def submit_review(decision: str, feedback: str = "", summary: str = "") -> dict[str, Any]:
    """Submit a review decision for the active task (approve/reject/request_changes).

    Writes reviewer.json to outbox — the watcher picks it up to resume the graph.

    Args:
        decision: Must be 'approve', 'reject', or 'request_changes'
        feedback: Review feedback text (required for reject)
        summary: Optional summary of the review
    """
    if decision not in ("approve", "reject", "request_changes"):
        return {"error": "decision must be 'approve', 'reject', or 'request_changes'"}

    active = read_lock()
    if not active:
        return {"error": "No active task to review."}

    feedback = (feedback or "")[:2000]
    if not feedback:
        feedback = "Approved via MCP" if decision == "approve" else "Rejected via MCP"
    summary = (summary or "")[:500] or f"Review {decision} via MCP"

    reviewer_output = {
        "decision": decision, "feedback": feedback, "summary": summary,
        "source": "mcp", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        write_outbox("reviewer", reviewer_output)
        return {"ok": True, "decision": decision, "task_id": active}
    except OSError as e:
        return {"error": f"Failed to write reviewer output: {e}"}


@mcp.tool()
def memory_search(query: str, top_k: int = 5) -> dict[str, Any]:
    """Search semantic memory for relevant project knowledge.

    Args:
        query: Natural language search query
        top_k: Maximum number of results (default 5)
    """
    if not query.strip():
        return {"results": [], "error": "Empty query"}
    top_k = max(1, min(top_k, 20))
    try:
        from multi_agent.semantic_memory import search
        results = search(query, top_k=top_k)
        return {"results": results, "count": len(results)}
    except Exception as e:
        return {"results": [], "error": str(e)}


@mcp.tool()
def memory_store(content: str, category: str = "general", tags: str = "") -> dict[str, Any]:
    """Store a new entry in semantic memory.

    Args:
        content: The knowledge to store (e.g. 'Always use type hints for function params')
        category: One of: architecture, convention, pattern, bugfix, preference, context, general
        tags: Comma-separated tags (e.g. 'python,typing')
    """
    if not content.strip():
        return {"error": "Empty content"}
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    try:
        from multi_agent.semantic_memory import store
        result = store(content, category=category, tags=tag_list, source="mcp")
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def memory_list(category: str = "", limit: int = 20) -> dict[str, Any]:
    """List semantic memory entries with optional category filter.

    Args:
        category: Filter by category (empty for all)
        limit: Maximum entries to return (default 20)
    """
    limit = max(1, min(limit, 100))
    try:
        from multi_agent.semantic_memory import list_entries, stats
        cat = category if category else None
        entries = list_entries(category=cat, limit=limit)
        s = stats()
        return {"entries": entries, "count": len(entries), "stats": s}
    except Exception as e:
        return {"entries": [], "error": str(e)}


@mcp.tool()
def finops_summary() -> dict[str, Any]:
    """Get FinOps token usage and cost summary."""
    try:
        from multi_agent.finops import aggregate_usage
        return aggregate_usage()
    except Exception as e:
        return {"error": str(e), "total_tokens": 0, "total_cost": 0}


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
    """Read dashboard.md content (capped at _MAX_FILE_READ bytes)."""
    path = workspace_dir() / "dashboard.md"
    if path.exists():
        try:
            size = path.stat().st_size
            if size > _MAX_FILE_READ:
                return path.read_bytes()[:_MAX_FILE_READ].decode("utf-8", errors="replace")
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
                content = path.read_text(encoding="utf-8") if path.stat().st_size <= _MAX_FILE_READ else ""
                for line in content.splitlines():
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
