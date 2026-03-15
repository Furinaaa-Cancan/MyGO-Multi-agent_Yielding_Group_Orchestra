"""Watch loop & waiting display — extracted from cli.py (A2c refactor).

Contains:
  _normalize_resume_output — payload validation/normalization for resume
  _show_waiting — display current waiting state, auto-spawn CLI agents
  _run_watch_loop — shared outbox poll loop, auto-submit output

These are re-exported from cli.py to preserve existing mock paths.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any

import click

from multi_agent._utils import (
    count_nonempty_entries as _count_nonempty_entries,
)
from multi_agent._utils import (
    positive_int as _positive_int,
)
from multi_agent.trace import append_trace_event
from multi_agent.workspace import (
    clear_runtime,
    release_lock,
    validate_outbox_data,
)
from multi_agent.workspace import (
    update_task_yaml as save_task_yaml,
)

# Serializes resume_task + sync so parallel subtasks don't corrupt global TASK.md/outbox
_resume_lock = threading.Lock()


def _state_label_from_status(status: Any) -> str:
    """Best-effort normalized state label for trace events."""
    state = getattr(status, "state", None)
    if isinstance(state, str) and state.strip():
        return state.strip().upper()
    final = str(getattr(status, "final_status", "") or "").lower().strip()
    mapping = {
        "approved": "DONE",
        "done": "DONE",
        "failed": "FAILED",
        "escalated": "ESCALATED",
        "cancelled": "CANCELLED",
    }
    if final in mapping:
        return mapping[final]
    waiting_role = str(getattr(status, "waiting_role", "") or "").lower().strip()
    if waiting_role == "builder":
        return "RUNNING"
    if waiting_role == "reviewer":
        return "VERIFYING"
    return "UNKNOWN"


def _terminal_next_steps(task_id: str, final: str, error: str) -> list[str]:
    """Actionable next-step hints for failed/escalated/cancelled terminal states."""
    final_l = (final or "").lower().strip()
    if final_l in {"approved", "done"}:
        return []

    tips = [
        f"             下一步: my trace --task-id {task_id} --format tree",
        f"             下一步: my status --task-id {task_id}",
    ]
    err_l = (error or "").lower()
    if "rc=124" in err_l or "timeout" in err_l:
        tips.append("             建议: 调大 CLI 超时（--timeout 或适配器 --timeout-sec）后重试")
    if "not ready" in err_l or "auth" in err_l:
        tips.append("             建议: 先执行 my auth doctor --agent <agent>")
    return tips


def _sync_subtask_workspace(subtask_id: str) -> None:
    """Sync global TASK.md + outbox paths into subtask workspace after graph advances."""
    import os
    import tempfile

    from multi_agent.config import subtask_outbox_dir, subtask_task_file, workspace_dir

    global_task = workspace_dir() / "TASK.md"
    sub_task = subtask_task_file(subtask_id)
    if global_task.exists():
        # Atomic copy: write to temp file then rename
        from pathlib import Path

        fd, tmp = tempfile.mkstemp(dir=str(sub_task.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(global_task.read_text(encoding="utf-8"))
                f.flush()
                os.fsync(f.fileno())
            Path(tmp).replace(sub_task)
        except Exception:
            with contextlib.suppress(OSError):
                Path(tmp).unlink()
            raise
    subtask_outbox_dir(subtask_id).mkdir(parents=True, exist_ok=True)


def _reviewer_evidence_cfg(state_values: dict[str, Any]) -> tuple[bool, int]:
    """Extract (require_evidence, min_evidence) from state's review_policy."""
    workflow_mode = str(state_values.get("workflow_mode", "")).lower().strip() or "normal"
    review_policy = state_values.get("review_policy")
    if not isinstance(review_policy, dict):
        review_policy = {}
    reviewer_cfg = review_policy.get("reviewer")
    if not isinstance(reviewer_cfg, dict):
        reviewer_cfg = {}
    require = bool(reviewer_cfg.get("require_evidence_on_approve", workflow_mode == "strict"))
    minimum = _positive_int(reviewer_cfg.get("min_evidence_items"), 1) if require else 0
    return require, minimum


def _ensure_evidence(out: dict[str, Any], min_evidence: int) -> None:
    """Ensure approve output has enough evidence, auto-populating from feedback if needed."""
    evidence_items = _count_nonempty_entries(out.get("evidence"))
    evidence_items += _count_nonempty_entries(out.get("evidence_files"))
    if evidence_items >= min_evidence:
        return
    feedback = out.get("feedback", "") or out.get("summary", "")
    if feedback and isinstance(feedback, str) and feedback.strip():
        if not out.get("evidence"):
            out["evidence"] = [feedback.strip()]
        evidence_items = 1
    if evidence_items < min_evidence:
        raise ValueError(
            "reviewer approve requires evidence: "
            f"need >= {min_evidence}, got {evidence_items}. "
            "Provide result.evidence and/or evidence_files."
        )


def _unwrap_protocol_envelope(role: str, data: dict[str, Any], state_values: dict[str, Any]) -> dict[str, Any]:
    """Accept session envelope payloads in legacy go/watch flow.

    This keeps backward compatibility with old flat JSON while allowing IDEs
    to always emit the new envelope schema.
    """
    result = data.get("result")
    if "protocol_version" not in data or not isinstance(result, dict):
        return data

    env_role = str(data.get("role", "")).strip().lower()
    if env_role and env_role != role:
        raise ValueError(f"envelope.role mismatch ({env_role} != {role})")

    env_task = str(data.get("task_id", "")).strip()
    state_task = str(state_values.get("task_id", "")).strip()
    if env_task and state_task and env_task != state_task:
        raise ValueError(f"envelope.task_id mismatch ({env_task} != {state_task})")

    out = dict(result)
    if role == "reviewer":
        top_level_evidence_files = data.get("evidence_files")
        if isinstance(top_level_evidence_files, list) and top_level_evidence_files:
            cur = out.get("evidence_files")
            merged = list(cur) if isinstance(cur, list) else []
            for item in top_level_evidence_files:
                if item not in merged:
                    merged.append(item)
            out["evidence_files"] = merged
    return out


def _normalize_resume_output(role: str, data: dict[str, Any], state_values: dict[str, Any]) -> dict[str, Any]:
    """Normalize/validate resume payload for legacy go/watch/done path."""
    data = _unwrap_protocol_envelope(role, data, state_values)

    if role != "reviewer":
        return data

    out = dict(data)
    decision = str(out.get("decision", "")).lower().strip()
    if decision == "pass":
        out["decision"] = "approve"
        decision = "approve"
    elif decision == "fail":
        out["decision"] = "reject"
        decision = "reject"

    require_evidence, min_evidence = _reviewer_evidence_cfg(state_values)
    if decision == "approve" and require_evidence:
        _ensure_evidence(out, min_evidence)
    return out


def _show_waiting(app: Any, config: dict[str, Any], *, subtask_id: str | None = None, visible: bool = False, terminal_slot: int | None = None) -> None:
    """Show current waiting state — auto-spawn CLI agents or show manual instructions."""
    from multi_agent.orchestrator import get_task_status

    task_id = config["configurable"]["thread_id"]
    status = get_task_status(app, task_id)

    if status.is_terminal:
        final = status.final_status or "done"
        if final in ("approved", "done"):
            click.echo(f"✅ Task finished. Status: {final}")
        else:
            error = status.error or ""
            click.echo(f"❌ Task finished. Status: {final}{' — ' + error if error else ''}")
        return

    role = status.waiting_role or "builder"
    agent = status.waiting_agent or "?"

    from multi_agent.driver import dispatch_agent
    result = dispatch_agent(agent, role, timeout_sec=status.values.get("timeout_sec", 600), subtask_id=subtask_id, visible=visible, terminal_slot=terminal_slot)
    click.echo(result.message)
    click.echo()


def _handle_terminal(
    status: Any, task_id: str, ts: str, manage_lock: bool,
) -> None:
    """Handle terminal task status in watch loop."""
    final = status.final_status or "done"
    error = status.error or ""
    with contextlib.suppress(Exception):
        append_trace_event(
            task_id=task_id,
            event_type="state_update",
            actor="orchestrator",
            role="orchestrator",
            state=_state_label_from_status(status),
            details={
                "final_status": final,
                "error": error or None,
                "source": "watch",
            },
            lane_id="main",
        )
    if final:
        save_task_yaml(task_id, {"status": final})
    if manage_lock:
        release_lock()
        clear_runtime()
    if final in ("approved", "done"):
        summary = ""
        bo = status.values.get("builder_output")
        if isinstance(bo, dict):
            summary = bo.get("summary", "")
        retries = status.values.get("retry_count", 0)
        click.echo(f"[{ts}] ✅ Task finished. Status: {final}")
        if summary:
            click.echo(f"             {summary}")
        if retries:
            click.echo(f"             (经过 {retries} 次重试)")
    else:
        click.echo(f"[{ts}] ❌ Task finished. Status: {final}{' — ' + error if error else ''}")
        for tip in _terminal_next_steps(task_id, final, error):
            click.echo(tip)


def _show_next_agent(next_status: Any, ts: str, *, visible: bool = False, subtask_id: str | None = None, terminal_slot: int | None = None) -> None:
    """Show next waiting state: retry feedback + auto-spawn or manual instructions."""
    next_role = next_status.waiting_role
    next_agent = next_status.waiting_agent or "?"
    retry_n = next_status.values.get("retry_count", 0)
    if retry_n > 0 and next_role == "builder":
        reviewer_out = next_status.values.get("reviewer_output") or {}
        feedback = reviewer_out.get("feedback", "") if isinstance(reviewer_out, dict) else ""
        budget = next_status.values.get("retry_budget", 2)
        click.echo(f"[{ts}] 🔄 Reviewer 要求修改 ({retry_n}/{budget}):")
        if feedback:
            click.echo(f"             {feedback}")
    from multi_agent.driver import dispatch_agent
    result = dispatch_agent(next_agent, next_role, timeout_sec=next_status.values.get("timeout_sec", 600), visible=visible, subtask_id=subtask_id, terminal_slot=terminal_slot)
    click.echo(f"[{ts}] {result.message}")


def _process_outbox(poller: Any, role: str, agent: str, status: Any, app: Any, task_id: str, ts: str, manage_lock: bool, *, visible: bool = False, subtask_id: str | None = None, terminal_slot: int | None = None) -> str:
    """Check outbox for matching role output, validate, and resume. Returns 'return' to stop loop."""
    from multi_agent.orchestrator import resume_task

    for detected_role, data in poller.check_once():
        if detected_role == role:
            step_label = "Build" if role == "builder" else "Review"
            click.echo(f"[{ts}] 📥 {step_label} 完成 ({agent})")
            with contextlib.suppress(Exception):
                append_trace_event(
                    task_id=task_id,
                    event_type="handoff_submit",
                    actor=agent,
                    role=role,
                    state=_state_label_from_status(status),
                    details={"source": "watch", "detected_role": detected_role},
                    lane_id="main",
                )
            try:
                data = _normalize_resume_output(role, data, status.values)
            except ValueError as e:
                click.echo(f"[{ts}] ❌ {e}", err=True)
                click.echo(f"[{ts}] 🔁 请修复 outbox/{role}.json 后重试", err=True)
                continue
            v_errors = validate_outbox_data(role, data)
            if v_errors:
                click.echo(f"[{ts}] ⚠️  Output warnings:", err=True)
                for ve in v_errors:
                    click.echo(f"             - {ve}", err=True)
            try:
                with _resume_lock:
                    next_status = resume_task(app, task_id, data)
                    if subtask_id:
                        _sync_subtask_workspace(subtask_id)
            except Exception as e:
                if manage_lock:
                    release_lock()
                    clear_runtime()
                click.echo(f"[{ts}] ❌ Error: {e}", err=True)
                save_task_yaml(task_id, {"status": "failed", "error": str(e)})
                with contextlib.suppress(Exception):
                    append_trace_event(
                        task_id=task_id,
                        event_type="state_update",
                        actor="orchestrator",
                        role="orchestrator",
                        state="FAILED",
                        details={
                            "final_status": "failed",
                            "error": str(e),
                            "source": "watch_resume_exception",
                        },
                        lane_id="main",
                    )
                return "return"

            if not next_status.is_terminal and next_status.waiting_role:
                _show_next_agent(next_status, ts, visible=visible, subtask_id=subtask_id, terminal_slot=terminal_slot)
            break
    return "continue"


def _run_watch_loop(app: Any, config: dict[str, Any], task_id: str, interval: float = 2.0, manage_lock: bool = True, *, subtask_id: str | None = None, visible: bool = False, terminal_slot: int | None = None) -> None:
    """Shared watch loop — polls outbox/ and auto-submits output.

    When *subtask_id* is provided, polls the subtask-specific outbox directory
    instead of the global outbox/ for parallel execution support.
    When *visible* is True, CLI agents open in new Terminal.app windows.
    """
    from multi_agent.orchestrator import get_task_status
    from multi_agent.watcher import OutboxPoller

    watch_dir = None
    if subtask_id:
        from multi_agent.config import subtask_outbox_dir
        watch_dir = subtask_outbox_dir(subtask_id)
        watch_dir.mkdir(parents=True, exist_ok=True)
    poller = OutboxPoller(poll_interval=interval, watch_dir=watch_dir)
    start_time = time.time()

    click.echo("👁️  等待 IDE 完成任务… (Ctrl-C 停止)")
    click.echo()

    try:
        while True:
            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            ts = f"{mins:02d}:{secs:02d}"

            status = get_task_status(app, task_id)

            if status.is_terminal:
                # Don't close slot-based terminals here — they persist across subtasks
                if visible and subtask_id and terminal_slot is None:
                    from multi_agent.driver import close_visible_terminal
                    close_visible_terminal(subtask_id=subtask_id)
                _handle_terminal(status, task_id, ts, manage_lock)
                return

            role = status.waiting_role or "builder"
            agent = status.waiting_agent or "?"

            result = _process_outbox(poller, role, agent, status, app, task_id, ts, manage_lock, visible=visible, subtask_id=subtask_id, terminal_slot=terminal_slot)
            if result == "return":
                return

            time.sleep(interval)
    except KeyboardInterrupt:
        if visible and subtask_id and terminal_slot is None:
            from multi_agent.driver import close_visible_terminal
            close_visible_terminal(subtask_id=subtask_id)
        click.echo("\n⏹️  Watch stopped. Task still active — resume with: my watch")
