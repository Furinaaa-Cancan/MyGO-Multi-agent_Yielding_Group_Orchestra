"""Daemon Mode + Task Queue — background task processing.

Provides a persistent task queue and a long-running daemon that
processes queued tasks sequentially.

Queue storage: ``.multi-agent/queue/tasks.jsonl``

Usage::

    my submit "Add login endpoint" --priority high
    my queue                       # list queued tasks
    my serve                       # start daemon (foreground)
    my serve --once                # process one task and exit
"""

from __future__ import annotations

import fcntl
import json
import logging
import signal
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

from multi_agent.config import workspace_dir

_log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────

_MAX_QUEUE_SIZE = 200
_MAX_REQUIREMENT_LENGTH = 2000
_POLL_INTERVAL = 2.0  # seconds


class TaskPriority(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


# ── Queue Storage ────────────────────────────────────────

def _queue_dir() -> Path:
    return workspace_dir() / "queue"


def _queue_file() -> Path:
    return _queue_dir() / "tasks.jsonl"


def _queue_lock():
    """Context manager providing exclusive file lock on the queue."""
    lock_path = _queue_dir() / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield lock_fd
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


_queue_lock = __import__("contextlib").contextmanager(_queue_lock)


def _load_queue() -> list[dict[str, Any]]:
    """Load all queued tasks from disk."""
    path = _queue_file()
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def _save_queue(entries: list[dict[str, Any]]) -> None:
    """Save queue entries to disk (atomic write)."""
    import tempfile
    path = _queue_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            Path(tmp).replace(path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        _log.warning("Failed to save queue: %s", exc)


# ── Queue Operations ────────────────────────────────────


def submit_task(
    requirement: str,
    *,
    priority: str = "normal",
    skill: str = "code-implement",
    builder: str = "",
    reviewer: str = "",
    template: str = "",
    retry_budget: int = 2,
    timeout: int = 1800,
    after: str = "",
) -> dict[str, Any]:
    """Submit a new task to the queue.

    Args:
        after: Queue ID of a task that must complete before this one starts.

    Returns:
        Dict with queue_id, status, and position.
    """
    requirement = str(requirement).strip()
    if not requirement and not template:
        return {"status": "error", "reason": "empty requirement"}
    if requirement:
        requirement = requirement[:_MAX_REQUIREMENT_LENGTH]

    entries = _load_queue()
    active = [e for e in entries if e.get("status") in ("queued", "running")]
    if len(active) >= _MAX_QUEUE_SIZE:
        return {"status": "error", "reason": f"queue full ({_MAX_QUEUE_SIZE})"}

    # Validate dependency
    if after:
        dep_ids = {e.get("queue_id") for e in entries}
        if after not in dep_ids:
            return {"status": "error", "reason": f"dependency {after} not found in queue"}

    # Validate priority
    if priority not in [p.value for p in TaskPriority]:
        priority = "normal"

    import hashlib
    queue_id = "q-" + hashlib.sha256(
        f"{requirement}{time.time()}".encode()
    ).hexdigest()[:8]

    entry = {
        "queue_id": queue_id,
        "requirement": requirement,
        "priority": priority,
        "skill": skill,
        "builder": builder,
        "reviewer": reviewer,
        "template": template,
        "retry_budget": retry_budget,
        "timeout": timeout,
        "depends_on": after or None,
        "status": TaskStatus.QUEUED.value,
        "submitted_at": time.time(),
        "started_at": None,
        "completed_at": None,
        "error": None,
    }

    entries.append(entry)
    _save_queue(entries)

    position = sum(
        1 for e in entries
        if e["status"] == "queued" and e["queue_id"] != queue_id
    ) + 1

    return {"status": "queued", "queue_id": queue_id, "position": position}


def list_queue(*, status_filter: str | None = None) -> list[dict[str, Any]]:
    """List queued tasks, optionally filtered by status."""
    entries = _load_queue()
    if status_filter:
        entries = [e for e in entries if e.get("status") == status_filter]
    return entries


def cancel_task(queue_id: str) -> dict[str, Any]:
    """Cancel a queued task by ID."""
    entries = _load_queue()
    for e in entries:
        if e.get("queue_id") == queue_id:
            if e["status"] == "queued":
                e["status"] = TaskStatus.CANCELLED.value
                _save_queue(entries)
                return {"status": "cancelled", "queue_id": queue_id}
            return {"status": "error", "reason": f"task is {e['status']}, cannot cancel"}
    return {"status": "error", "reason": "not found"}


def clean_queue() -> dict[str, Any]:
    """Remove completed, failed, and cancelled entries from the queue.

    Returns:
        Dict with removed count and remaining count.
    """
    entries = _load_queue()
    keep = [e for e in entries if e.get("status") in ("queued", "running")]
    removed = len(entries) - len(keep)
    if removed > 0:
        _save_queue(keep)
    return {"removed": removed, "remaining": len(keep)}


def queue_stats() -> dict[str, Any]:
    """Get queue statistics."""
    entries = _load_queue()
    by_status: dict[str, int] = {}
    for e in entries:
        s = e.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    return {
        "total": len(entries),
        "by_status": by_status,
        "queued": by_status.get("queued", 0),
        "running": by_status.get("running", 0),
    }


def _next_task() -> dict[str, Any] | None:
    """Get the next queued task (highest priority, oldest first).

    Respects depends_on: a task is only eligible if its dependency
    has status 'completed' (or dependency doesn't exist).
    """
    entries = _load_queue()
    status_map = {e.get("queue_id"): e.get("status") for e in entries}

    eligible = []
    for e in entries:
        if e.get("status") != "queued":
            continue
        dep = e.get("depends_on")
        if dep:
            dep_status = status_map.get(dep)
            if dep_status != "completed":
                continue  # dependency not yet done
        eligible.append(e)

    if not eligible:
        return None
    # Sort by priority then by submitted_at
    eligible.sort(key=lambda e: (
        _PRIORITY_ORDER.get(e.get("priority", "normal"), 1),
        e.get("submitted_at", 0),
    ))
    return eligible[0]


def _mark_running(queue_id: str) -> None:
    """Mark a task as running."""
    with _queue_lock():
        entries = _load_queue()
        for e in entries:
            if e.get("queue_id") == queue_id:
                e["status"] = TaskStatus.RUNNING.value
                e["started_at"] = time.time()
                break
        _save_queue(entries)


def _mark_completed(queue_id: str) -> None:
    """Mark a task as completed."""
    with _queue_lock():
        entries = _load_queue()
        for e in entries:
            if e.get("queue_id") == queue_id:
                e["status"] = TaskStatus.COMPLETED.value
                e["completed_at"] = time.time()
                break
        _save_queue(entries)


def _mark_failed(queue_id: str, error: str) -> None:
    """Mark a task as failed."""
    with _queue_lock():
        entries = _load_queue()
        for e in entries:
            if e.get("queue_id") == queue_id:
                e["status"] = TaskStatus.FAILED.value
                e["completed_at"] = time.time()
                e["error"] = error[:500]
                break
        _save_queue(entries)


# ── Daemon ───────────────────────────────────────────────


def run_daemon(*, once: bool = False, poll_interval: float = _POLL_INTERVAL) -> dict[str, Any]:
    """Run the daemon loop — process queued tasks.

    Args:
        once: If True, process one task and exit.
        poll_interval: Seconds between queue polls.

    Returns:
        Summary dict with processed count.
    """
    _log.info("Daemon starting (once=%s, poll=%.1fs)", once, poll_interval)
    processed = 0
    _shutdown = [False]

    def _handle_signal(signum: int, frame: Any) -> None:
        _shutdown[0] = True

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    else:
        _log.warning("Daemon not on main thread; signal handlers not installed")

    while not _shutdown[0]:
        task = _next_task()
        if task is None:
            if once:
                break
            time.sleep(poll_interval)
            continue

        queue_id = task["queue_id"]
        requirement = task.get("requirement", "")
        _log.info("Processing task %s: %s", queue_id, requirement[:60])
        _mark_running(queue_id)

        try:
            _execute_task(task)
            _mark_completed(queue_id)
            processed += 1
        except Exception as exc:
            _mark_failed(queue_id, str(exc))
            _log.warning("Task %s failed: %s", queue_id, exc)

        if once:
            break

    return {"processed": processed, "shutdown": _shutdown[0]}


def _execute_task(task: dict[str, Any]) -> None:
    """Execute a single queued task via the graph."""
    from multi_agent.cli import _generate_task_id, _run_single_task
    from multi_agent.graph import compile_graph
    from multi_agent.session import _resolve_review_policy

    requirement = task.get("requirement", "")
    template_id = task.get("template")

    if template_id:
        from multi_agent.task_templates import load_template, resolve_variables
        tmpl = load_template(template_id)
        tmpl = resolve_variables(tmpl)
        requirement = requirement or tmpl.requirement

    if not requirement:
        raise ValueError("No requirement resolved")

    app = compile_graph()
    task_id = _generate_task_id(requirement)
    review_policy = _resolve_review_policy("strict", "config/workmode.yaml")

    _run_single_task(
        app, task_id, requirement,
        task.get("skill", "code-implement"),
        task.get("builder", ""),
        task.get("reviewer", ""),
        task.get("retry_budget", 2),
        task.get("timeout", 1800),
        False,  # no_watch
        "strict",
        review_policy,
    )
