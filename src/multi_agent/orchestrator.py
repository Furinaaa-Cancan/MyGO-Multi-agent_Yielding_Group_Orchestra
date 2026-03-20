"""Orchestrator — shared graph coordination primitives.

Architecture fix (defect A1): Previously cli.py and session.py independently
managed the LangGraph workflow (compile, invoke, resume, state introspection),
leading to duplicated and inconsistent orchestration logic.

This module provides a thin coordination layer that both entry points delegate
to, ensuring a single source of truth for task lifecycle management.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langgraph.errors import GraphInterrupt

from multi_agent._utils import TERMINAL_FINAL_STATUSES
from multi_agent.graph_infra import TaskContext

_log = logging.getLogger(__name__)


# ── Task Status ──────────────────────────────────────────


@dataclass(frozen=True)
class TaskStatus:
    """Structured snapshot of a task's current state.

    Replaces ad-hoc dict/tuple returns scattered across cli.py and session.py.
    """

    state: str  # RUNNING, VERIFYING, DONE, FAILED, CANCELLED, ESCALATED, UNKNOWN
    is_terminal: bool
    waiting_role: str | None = None  # "builder" | "reviewer" | None
    waiting_agent: str | None = None  # agent id waiting for input
    final_status: str | None = None  # raw final_status from graph
    error: str | None = None  # error message if failed
    values: dict[str, Any] = field(default_factory=dict)


def _is_terminal(final_status: str | None) -> bool:
    if not final_status:
        return False
    return final_status.strip().lower() in TERMINAL_FINAL_STATUSES


# ── Graph Compilation ────────────────────────────────────


def compile_graph() -> Any:
    """Compile the LangGraph workflow — single call-site.

    Both cli.py and session.py previously had their own compile wrappers.
    """
    from multi_agent.graph import compile_graph as _compile

    return _compile()


# ── Config ───────────────────────────────────────────────


def make_config(task_id: str) -> dict[str, Any]:
    """Build the LangGraph config dict for a given task.

    Replaces cli.py._make_config() and session.py._config().
    """
    return {"configurable": {"thread_id": task_id}}


# ── State Introspection ──────────────────────────────────


def get_waiting_info(snapshot: Any) -> tuple[str | None, str | None]:
    """Extract (role, agent) from a graph snapshot's interrupt.

    Replaces session.py._waiting_info() and inline logic in cli.py._show_waiting().
    Returns (None, None) if the graph is not waiting for input.
    """
    if not snapshot or not snapshot.next:
        return None, None
    if snapshot.tasks and snapshot.tasks[0].interrupts:
        info = snapshot.tasks[0].interrupts[0].value
        if isinstance(info, dict):
            role = info.get("role")
            agent = info.get("agent")
            if isinstance(role, str) and isinstance(agent, str):
                return role, agent
    return None, None


def get_task_status(app: Any, task_id: str) -> TaskStatus:
    """Inspect current task state from the graph checkpoint.

    Single source of truth for task state — replaces duplicated logic in
    cli.py (inline in _show_waiting, _run_watch_loop, done) and
    session.py (_state_from_snapshot, build_agent_prompt).
    """
    config = make_config(task_id)
    snapshot = app.get_state(config)
    vals = (snapshot.values if snapshot else {}) or {}

    final_status = str(vals.get("final_status", "")).lower().strip() or None
    error = str(vals.get("error", "")).strip() or None

    if _is_terminal(final_status):
        state_map = {
            "approved": "DONE",
            "done": "DONE",
            "failed": "FAILED",
            "escalated": "ESCALATED",
            "cancelled": "CANCELLED",
        }
        state = state_map.get(final_status, final_status.upper()) if final_status else "DONE"
        return TaskStatus(
            state=state,
            is_terminal=True,
            final_status=final_status,
            error=error,
            values=dict(vals),
        )

    role, agent = get_waiting_info(snapshot)
    if role == "builder":
        state = "RUNNING"
    elif role == "reviewer":
        state = "VERIFYING"
    elif not snapshot or not snapshot.next:
        # Graph completed without setting final_status
        state = "DONE"
        return TaskStatus(
            state=state,
            is_terminal=True,
            final_status=final_status or "done",
            error=error,
            values=dict(vals),
        )
    else:
        state = "ASSIGNED"

    return TaskStatus(
        state=state,
        is_terminal=False,
        waiting_role=role,
        waiting_agent=agent,
        final_status=final_status,
        error=error,
        values=dict(vals),
    )


# ── Task Lifecycle ───────────────────────────────────────


class TaskStartError(RuntimeError):
    """Raised when a task fails to start."""

    def __init__(self, message: str, *, task_id: str, cause: Exception | None = None):
        self.task_id = task_id
        self.cause = cause
        super().__init__(message)


def start_task(
    app: Any,
    task_id: str,
    initial_state: dict[str, Any],
) -> TaskStatus:
    """Invoke the graph with initial state, running until first interrupt.

    Replaces the duplicated try/except GraphInterrupt pattern in
    cli.py._run_single_task() and session.py.session_start_impl().

    Each task runs inside a ``TaskContext`` so that graph_stats and
    graph_hooks are isolated per-task (OpenClaw Gateway-inspired).

    Returns the TaskStatus after the first interrupt (typically waiting
    for builder input).

    Raises TaskStartError on failure.
    """
    config = make_config(task_id)
    ctx = TaskContext(task_id=task_id)
    try:
        with ctx:
            app.invoke(initial_state, config)
    except GraphInterrupt:
        pass  # Normal — graph paused at interrupt()
    except Exception as e:
        raise TaskStartError(
            f"Task {task_id} failed to start: {e}",
            task_id=task_id,
            cause=e,
        ) from e

    status = get_task_status(app, task_id)
    # Attach context for resume_task to reuse
    status = TaskStatus(
        state=status.state,
        is_terminal=status.is_terminal,
        waiting_role=status.waiting_role,
        waiting_agent=status.waiting_agent,
        final_status=status.final_status,
        error=status.error,
        values={**status.values, "_task_context": ctx},
    )
    return status


def resume_task(
    app: Any,
    task_id: str,
    output_data: dict[str, Any],
    *,
    task_context: TaskContext | None = None,
) -> TaskStatus:
    """Resume the graph with agent output, advancing to the next interrupt.

    Replaces the duplicated invoke(Command(resume=...)) pattern in
    cli.py.done() and session.py.submit_output().

    When *task_context* is provided, the resume executes inside that
    context so stats/hooks remain isolated per-task.

    Returns the TaskStatus after resume.
    """
    from langgraph.types import Command

    config = make_config(task_id)
    ctx = task_context or TaskContext(task_id=task_id)
    try:
        with ctx:
            app.invoke(Command(resume=output_data), config)
    except GraphInterrupt:
        pass  # Normal — graph paused at next interrupt()
    except Exception as e:
        _log.exception("Graph error during resume for task %s: %s", task_id, e)
        raise

    return get_task_status(app, task_id)
