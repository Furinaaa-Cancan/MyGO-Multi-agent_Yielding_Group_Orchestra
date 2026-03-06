"""Watch loop & waiting display — extracted from cli.py (A2c refactor).

Contains:
  _normalize_resume_output — payload validation/normalization for resume
  _show_waiting — display current waiting state, auto-spawn CLI agents
  _run_watch_loop — shared outbox poll loop, auto-submit output

These are re-exported from cli.py to preserve existing mock paths.
"""

from __future__ import annotations

import time
from typing import Any

import click

from multi_agent._utils import (
    count_nonempty_entries as _count_nonempty_entries,
)
from multi_agent._utils import (
    positive_int as _positive_int,
)
from multi_agent.workspace import (
    clear_runtime,
    release_lock,
    save_task_yaml,
    validate_outbox_data,
)


def _sync_subtask_workspace(subtask_id: str) -> None:
    """Sync global TASK.md + outbox paths into subtask workspace after graph advances."""
    import shutil

    from multi_agent.config import subtask_outbox_dir, subtask_task_file, workspace_dir

    global_task = workspace_dir() / "TASK.md"
    sub_task = subtask_task_file(subtask_id)
    if global_task.exists():
        shutil.copy2(str(global_task), str(sub_task))
    subtask_outbox_dir(subtask_id).mkdir(parents=True, exist_ok=True)


def _normalize_resume_output(role: str, data: dict[str, Any], state_values: dict[str, Any]) -> dict[str, Any]:
    """Normalize/validate resume payload for legacy go/watch/done path."""
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

    workflow_mode = str(state_values.get("workflow_mode", "")).lower().strip() or "normal"
    review_policy = state_values.get("review_policy")
    if not isinstance(review_policy, dict):
        review_policy = {}
    reviewer_cfg = review_policy.get("reviewer")
    if not isinstance(reviewer_cfg, dict):
        reviewer_cfg = {}

    require_evidence = bool(reviewer_cfg.get("require_evidence_on_approve", workflow_mode == "strict"))
    min_evidence = _positive_int(reviewer_cfg.get("min_evidence_items"), 1) if require_evidence else 0

    if decision == "approve" and require_evidence:
        evidence_items = _count_nonempty_entries(out.get("evidence"))
        evidence_items += _count_nonempty_entries(out.get("evidence_files"))
        if evidence_items < min_evidence:
            raise ValueError(
                "reviewer approve requires evidence: "
                f"need >= {min_evidence}, got {evidence_items}. "
                "Provide result.evidence and/or evidence_files."
            )
    return out


def _show_waiting(app: Any, config: dict[str, Any], *, subtask_id: str | None = None, visible: bool = False) -> None:
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
    result = dispatch_agent(agent, role, timeout_sec=status.values.get("timeout_sec", 600), subtask_id=subtask_id, visible=visible)
    click.echo(result.message)
    click.echo()


def _handle_terminal(
    status: Any, task_id: str, ts: str, manage_lock: bool,
) -> None:
    """Handle terminal task status in watch loop."""
    final = status.final_status or "done"
    if final:
        save_task_yaml(task_id, {"task_id": task_id, "status": final})
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
        error = status.error or ""
        click.echo(f"[{ts}] ❌ Task finished. Status: {final}{' — ' + error if error else ''}")


def _show_next_agent(next_status: Any, ts: str, *, visible: bool = False, subtask_id: str | None = None) -> None:
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
    result = dispatch_agent(next_agent, next_role, timeout_sec=next_status.values.get("timeout_sec", 600), visible=visible, subtask_id=subtask_id)
    click.echo(f"[{ts}] {result.message}")


def _process_outbox(poller: Any, role: str, agent: str, status: Any, app: Any, task_id: str, ts: str, manage_lock: bool, *, visible: bool = False, subtask_id: str | None = None) -> str:
    """Check outbox for matching role output, validate, and resume. Returns 'return' to stop loop."""
    from multi_agent.orchestrator import resume_task

    for detected_role, data in poller.check_once():
        if detected_role == role:
            step_label = "Build" if role == "builder" else "Review"
            click.echo(f"[{ts}] 📥 {step_label} 完成 ({agent})")
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
                next_status = resume_task(app, task_id, data)
            except Exception as e:
                if manage_lock:
                    release_lock()
                    clear_runtime()
                click.echo(f"[{ts}] ❌ Error: {e}", err=True)
                save_task_yaml(task_id, {"task_id": task_id, "status": "failed", "error": str(e)})
                return "return"

            # Sync updated TASK.md to subtask workspace for next iteration
            if subtask_id:
                _sync_subtask_workspace(subtask_id)

            if not next_status.is_terminal and next_status.waiting_role:
                _show_next_agent(next_status, ts, visible=visible, subtask_id=subtask_id)
            break
    return "continue"


def _run_watch_loop(app: Any, config: dict[str, Any], task_id: str, interval: float = 2.0, manage_lock: bool = True, *, subtask_id: str | None = None, visible: bool = False) -> None:
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
                _handle_terminal(status, task_id, ts, manage_lock)
                return

            role = status.waiting_role or "builder"
            agent = status.waiting_agent or "?"

            result = _process_outbox(poller, role, agent, status, app, task_id, ts, manage_lock, visible=visible, subtask_id=subtask_id)
            if result == "return":
                return

            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("\n⏹️  Watch stopped. Task still active — resume with: my watch")
