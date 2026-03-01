"""LangGraph 4-node graph: plan → build → review → decide."""

from __future__ import annotations

import time
from operator import add
from typing import Annotated, Any

from typing_extensions import TypedDict

from langgraph.errors import GraphInterrupt
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from langgraph.checkpoint.sqlite import SqliteSaver

import logging
_log = logging.getLogger(__name__)

from multi_agent.config import store_db_path
from multi_agent.contract import load_contract, validate_preconditions
from multi_agent.dashboard import write_dashboard
from multi_agent.prompt import render_builder_prompt, render_reviewer_prompt
from multi_agent.router import load_agents, resolve_builder, resolve_reviewer
from multi_agent.schema import (
    BuilderOutput,
    ReviewerOutput,
    Task,
)
from multi_agent.workspace import (
    archive_conversation,
    clear_outbox,
    write_inbox,
)

MAX_SNAPSHOTS = 10
MAX_CONVERSATION_SIZE = 50
MAX_REQUEST_CHANGES = 5  # cap soft retries to prevent infinite loops


class GraphStats:
    """Collect graph execution statistics per node."""

    def __init__(self):
        self._stats: dict[str, dict] = {}

    def record(self, node: str, duration_ms: int, success: bool) -> None:
        if node not in self._stats:
            self._stats[node] = {"count": 0, "total_ms": 0, "errors": 0}
        s = self._stats[node]
        s["count"] += 1
        s["total_ms"] += duration_ms
        if not success:
            s["errors"] += 1

    def summary(self) -> dict[str, dict]:
        result = {}
        for node, s in self._stats.items():
            avg = s["total_ms"] / s["count"] if s["count"] else 0
            error_rate = s["errors"] / s["count"] if s["count"] else 0
            result[node] = {
                "count": s["count"],
                "avg_ms": round(avg),
                "error_rate": round(error_rate, 3),
            }
        return result

    def save(self, path=None) -> None:
        """Save stats to .multi-agent/stats.json."""
        import json as _json
        from multi_agent.config import workspace_dir as _ws
        p = path or (_ws() / "stats.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(_json.dumps(self.summary(), indent=2), encoding="utf-8")
        except OSError:
            pass


graph_stats = GraphStats()


def log_timing(task_id: str, node: str, start: float, end: float) -> None:
    """Append a timing entry to .multi-agent/logs/timing-{task_id}.jsonl."""
    import json as _json
    from multi_agent.config import workspace_dir as _ws
    logs_dir = _ws() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "node": node,
        "start": start,
        "end": end,
        "duration_ms": int((end - start) * 1000),
    }
    path = logs_dir / f"timing-{task_id}.jsonl"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(entry) + "\n")
    except OSError:
        pass


def trim_conversation(conversation: list[dict]) -> list[dict]:
    """Keep conversation within MAX_CONVERSATION_SIZE, preserving first and last entries.

    Includes a lightweight summary of removed entries so downstream AI agents
    retain context awareness (literature: JetBrains context management research).
    """
    if len(conversation) <= MAX_CONVERSATION_SIZE:
        return conversation
    keep_head = 5
    keep_tail = MAX_CONVERSATION_SIZE - keep_head - 1
    removed = conversation[keep_head:-keep_tail]
    # Build lightweight summary of what was removed
    action_counts: dict[str, int] = {}
    feedback_snippets: list[str] = []
    for e in removed:
        a = e.get("action", "unknown")
        action_counts[a] = action_counts.get(a, 0) + 1
        fb = e.get("feedback", "")
        if fb and len(feedback_snippets) < 3:
            feedback_snippets.append(fb[:80])
    summary_parts = [f"{a}×{c}" for a, c in action_counts.items()]
    trimmed_marker = {
        "role": "system", "action": "trimmed",
        "details": f"Removed {len(removed)} entries: {', '.join(summary_parts)}",
        "key_feedback": feedback_snippets,
        "t": time.time(),
    }
    return conversation[:keep_head] + [trimmed_marker] + conversation[-keep_tail:]


def save_state_snapshot(task_id: str, node_name: str, state: dict) -> None:
    """Save a state snapshot for debugging. Keeps only the latest MAX_SNAPSHOTS."""
    import json as _json
    from multi_agent.config import workspace_dir as _ws_dir
    snap_dir = _ws_dir() / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    ts = int(time.time() * 1000)
    safe_state = {}
    for k, v in state.items():
        try:
            _json.dumps(v)
            safe_state[k] = v
        except (TypeError, ValueError):
            safe_state[k] = str(v)

    path = snap_dir / f"{task_id}-{node_name}-{ts}.json"
    try:
        path.write_text(_json.dumps(safe_state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return

    # Cleanup old snapshots
    existing = sorted(snap_dir.glob(f"{task_id}-*.json"), key=lambda p: p.stat().st_mtime)
    while len(existing) > MAX_SNAPSHOTS:
        existing.pop(0).unlink(missing_ok=True)


# ── Event Hooks ──────────────────────────────────────────

import logging as _logging

_hook_logger = _logging.getLogger(__name__ + ".hooks")


class EventHooks:
    """Registry for graph execution event callbacks.

    Usage:
        hooks = EventHooks()
        hooks.on_node_enter("plan", lambda state: print("plan started"))
        hooks.on_node_exit("build", lambda state, result: log(result))
        hooks.on_error(lambda node, state, err: alert(err))
    """

    def __init__(self):
        self._enter: dict[str, list] = {}   # node_name → [callbacks]
        self._exit: dict[str, list] = {}    # node_name → [callbacks]
        self._error: list = []              # global error handlers

    def on_node_enter(self, node: str, callback) -> None:
        self._enter.setdefault(node, []).append(callback)

    def on_node_exit(self, node: str, callback) -> None:
        self._exit.setdefault(node, []).append(callback)

    def on_error(self, callback) -> None:
        self._error.append(callback)

    def fire_enter(self, node: str, state: dict) -> None:
        for cb in self._enter.get(node, []):
            try:
                cb(state)
            except Exception as e:
                _hook_logger.warning("Hook enter/%s error: %s", node, e)

    def fire_exit(self, node: str, state: dict, result: dict) -> None:
        for cb in self._exit.get(node, []):
            try:
                cb(state, result)
            except Exception as e:
                _hook_logger.warning("Hook exit/%s error: %s", node, e)

    def fire_error(self, node: str, state: dict, error: Exception) -> None:
        for cb in self._error:
            try:
                cb(node, state, error)
            except Exception as e:
                _hook_logger.warning("Hook error handler error: %s", e)


# Global hooks instance — importable by consumers
graph_hooks = EventHooks()


def register_hook(event: str, callback) -> None:
    """Register a callback for a graph event (public API).

    Supported events: plan_start, build_submit, review_submit,
    decide_approve, decide_reject, task_failed.
    Maps to EventHooks.on_node_enter/on_node_exit internally.
    """
    _event_map = {
        "plan_start": ("enter", "plan"),
        "build_submit": ("exit", "build"),
        "review_submit": ("exit", "review"),
        "decide_approve": ("exit", "decide"),
        "decide_reject": ("exit", "decide"),
        "task_failed": ("error", None),
    }
    mapping = _event_map.get(event)
    if mapping is None:
        graph_hooks.on_node_enter(event, callback)
        return
    kind, node = mapping
    if kind == "enter":
        graph_hooks.on_node_enter(node, callback)
    elif kind == "exit":
        graph_hooks.on_node_exit(node, callback)
    elif kind == "error":
        graph_hooks.on_error(callback)


# ── State ─────────────────────────────────────────────────

class WorkflowState(TypedDict, total=False):
    # Input (set once at start)
    task_id: str
    requirement: str
    skill_id: str
    done_criteria: list[str]
    timeout_sec: int
    input_payload: dict[str, Any]

    # Flow control
    current_role: str          # "builder" or "reviewer"
    builder_id: str            # IDE name filling builder role (e.g. "windsurf")
    reviewer_id: str           # IDE name filling reviewer role (e.g. "cursor")
    builder_explicit: str      # user-specified builder (from --builder flag)
    reviewer_explicit: str     # user-specified reviewer (from --reviewer flag)
    builder_output: dict | None
    reviewer_output: dict | None
    retry_count: int
    retry_budget: int
    started_at: float
    build_started_at: float | None
    review_started_at: float | None

    # Accumulate
    conversation: Annotated[list[dict], add]

    # Hierarchy
    parent_task_id: str | None

    # Terminal
    error: str | None
    final_status: str | None


# ── TASK.md — Universal Entry Point ──────────────────────

def _write_task_md(state: dict, builder_id: str, reviewer_id: str, current_role: str):
    """Write TASK.md — THE single self-contained file for the IDE AI.

    TASK.md embeds the full prompt content inline so the IDE AI gets
    everything it needs from ONE file reference. No jumping to inbox files.
    """
    from multi_agent.config import workspace_dir, inbox_dir, outbox_dir

    outbox_rel = f".multi-agent/outbox/{current_role}.json"
    outbox_abs = str(outbox_dir() / f"{current_role}.json")

    # Read the inbox prompt that was just written
    inbox_file = inbox_dir() / f"{current_role}.md"
    prompt_content = ""
    if inbox_file.exists():
        prompt_content = inbox_file.read_text(encoding="utf-8")

    lines = [
        prompt_content,
        "",
        "---",
        "",
        "> **完成后，把上面要求的 JSON 结果保存到以下路径，终端会自动推进流程:**",
        f"> `{outbox_rel}`",
        f"> 绝对路径: `{outbox_abs}`",
        "",
    ]

    p = workspace_dir() / "TASK.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")


# ── Node 1: Plan ──────────────────────────────────────────

def plan_node(state: WorkflowState) -> dict:
    """Load skill contract → resolve builder → generate prompt → write inbox."""
    _t0 = time.time()
    _ok = False
    try:
        result = _plan_node_inner(state)
        _ok = result.get("final_status") != "failed"
        return result
    except GraphInterrupt:
        _ok = True
        raise
    except Exception as e:
        _log.exception("plan_node failed: %s", e)
        graph_hooks.fire_error("plan", state, e)
        return {
            "error": f"plan_node: {e}",
            "final_status": "failed",
            "conversation": [{"role": "orchestrator", "action": "internal_error",
                              "details": str(e), "t": time.time()}],
        }
    finally:
        _t1 = time.time()
        tid = state.get("task_id", "")
        log_timing(tid, "plan", _t0, _t1)
        graph_stats.record("plan", int((_t1 - _t0) * 1000), _ok)
        try:
            save_state_snapshot(tid, "plan", dict(state))
        except Exception:
            pass


def _plan_node_inner(state: WorkflowState) -> dict:
    graph_hooks.fire_enter("plan", state)
    skill_id = state["skill_id"]
    contract = load_contract(skill_id)

    # A0: Precondition check — fail early without consuming retry budget
    precondition_errors = validate_preconditions(contract, "RUNNING")
    if precondition_errors:
        msg = "; ".join(precondition_errors)
        result = {
            "error": f"Precondition failed: {msg}",
            "final_status": "failed",
            "conversation": [
                {"role": "orchestrator", "action": "precondition_failed",
                 "details": precondition_errors, "t": time.time()}
            ],
        }
        graph_hooks.fire_exit("plan", state, result)
        return result

    agents = load_agents()

    # On retry, reuse existing role assignments to keep consistency
    existing_builder = state.get("builder_id")
    existing_reviewer = state.get("reviewer_id")

    if existing_builder and existing_reviewer:
        builder_id = existing_builder
        reviewer_id = existing_reviewer
    else:
        # First run: resolve roles
        builder_id = resolve_builder(
            agents, contract,
            explicit=state.get("builder_explicit") or None,
        )
        reviewer_id = resolve_reviewer(
            agents, contract, builder_id,
            explicit=state.get("reviewer_explicit") or None,
        )

    # Build a lightweight Task for prompt rendering
    task = Task(
        task_id=state["task_id"],
        trace_id="0" * 16,
        skill_id=skill_id,
        done_criteria=state.get("done_criteria", []),
        timeout_sec=state.get("timeout_sec", contract.timeouts.run_sec),
        retry_budget=state.get("retry_budget", contract.retry.max_attempts),
        input_payload=state.get("input_payload"),
    )

    retry_count = state.get("retry_count", 0)
    retry_feedback = ""
    previous_summary = ""
    if retry_count > 0 and state.get("reviewer_output"):
        retry_feedback = state["reviewer_output"].get("feedback", "")
    if retry_count > 0 and state.get("builder_output"):
        previous_summary = state["builder_output"].get("summary", "")

    prompt = render_builder_prompt(
        task=task,
        contract=contract,
        agent_id=builder_id,
        retry_count=retry_count,
        retry_feedback=retry_feedback,
        retry_budget=task.retry_budget,
        previous_summary=previous_summary,
    )

    # Write to ROLE-based inbox (builder.md, not windsurf.md)
    clear_outbox("builder")
    write_inbox("builder", prompt)

    # Write TASK.md — single entry point for any IDE
    _write_task_md(state, builder_id, reviewer_id, "builder")

    # Update dashboard
    write_dashboard(
        task_id=state["task_id"],
        done_criteria=state.get("done_criteria", []),
        current_agent=builder_id,
        current_role="builder",
        conversation=state.get("conversation", []),
        status_msg=f"🔵 等待 **{builder_id}** 执行 builder 任务",
    )

    result = {
        "current_role": "builder",
        "builder_id": builder_id,
        "reviewer_id": reviewer_id,
        "started_at": time.time(),
        "conversation": [
            {"role": "orchestrator", "action": "assigned", "agent": builder_id, "t": time.time()}
        ],
    }
    graph_hooks.fire_exit("plan", state, result)
    return result


# ── Node 2: Build ─────────────────────────────────────────

def build_node(state: WorkflowState) -> dict:
    """Interrupt for builder → validate output → prepare reviewer."""
    _t0 = time.time()
    _ok = False
    try:
        result = _build_node_inner(state)
        _ok = result.get("final_status") != "failed"
        return result
    except GraphInterrupt:
        _ok = True
        raise
    except Exception as e:
        _log.exception("build_node failed: %s", e)
        graph_hooks.fire_error("build", state, e)
        return {
            "error": f"build_node: {e}",
            "final_status": "failed",
            "conversation": [{"role": "orchestrator", "action": "internal_error",
                              "details": str(e), "t": time.time()}],
        }
    finally:
        _t1 = time.time()
        tid = state.get("task_id", "")
        log_timing(tid, "build", _t0, _t1)
        graph_stats.record("build", int((_t1 - _t0) * 1000), _ok)
        try:
            save_state_snapshot(tid, "build", dict(state))
        except Exception:
            pass


def _build_node_inner(state: WorkflowState) -> dict:
    graph_hooks.fire_enter("build", state)
    builder_id = state.get("builder_id", "?")
    reviewer_id = state.get("reviewer_id", "?")

    # Interrupt: wait for builder to submit via `ma done`
    # Role-based: inbox is always builder.md regardless of which IDE
    result = interrupt({
        "role": "builder",
        "agent": builder_id,
    })

    # Check for cancellation immediately after interrupt returns
    if _is_cancelled(state.get("task_id", "")):
        return {
            "final_status": "cancelled",
            "conversation": [{"role": "orchestrator", "action": "cancelled", "t": time.time()}],
        }

    # A3: Timeout enforcement — use build_started_at for precise timing
    build_started = time.time()
    # Fallback to started_at for backward compatibility with old state
    ref_time = state.get("build_started_at") or state.get("started_at", 0)
    if not ref_time:
        ref_time = build_started
    timeout = state.get("timeout_sec", 1800)
    if ref_time and timeout:
        elapsed = time.time() - ref_time
        if elapsed > timeout:
            return {
                "error": f"TIMEOUT: builder took {int(elapsed)}s (limit: {timeout}s)",
                "final_status": "failed",
                "conversation": [{"role": "orchestrator", "action": "timeout", "elapsed": int(elapsed), "t": time.time()}],
            }

    # Validate builder output (light-weight)
    errors: list[str] = []
    if not isinstance(result, dict):
        errors.append("output must be a JSON object")
    else:
        if "status" not in result:
            errors.append("missing 'status' field")
        if "summary" not in result:
            errors.append("missing 'summary' field")

    if errors:
        return {
            "error": f"Builder output invalid: {'; '.join(errors)}",
            "final_status": "failed",
            "conversation": [{"role": "builder", "output": "INVALID", "t": time.time()}],
        }

    # Detect CLI driver error output — don't waste reviewer's time
    if result.get("status") == "error":
        error_msg = result.get("summary", "unknown CLI error")
        return {
            "error": f"Builder failed: {error_msg}",
            "final_status": "failed",
            "conversation": [{"role": "builder", "output": f"ERROR: {error_msg}", "t": time.time()}],
        }

    # Validate via Pydantic (non-fatal — we log warnings but proceed)
    try:
        BuilderOutput(**result)
    except Exception:
        pass  # Lenient: proceed even if extra fields exist

    # A4: Quality gate enforcement — check that required gates passed
    skill_id = state["skill_id"]
    contract = load_contract(skill_id)
    check_results = result.get("check_results", {})
    gate_warnings: list[str] = []
    for gate in contract.quality_gates:
        gate_result = check_results.get(gate)
        if gate_result is None:
            gate_warnings.append(f"quality gate '{gate}' not reported")
        elif str(gate_result).lower() not in ("pass", "passed", "ok", "success", "true"):
            gate_warnings.append(f"quality gate '{gate}' failed: {gate_result}")
    # Gate failures go to reviewer as extra context (not hard-fail)
    if gate_warnings:
        result.setdefault("gate_warnings", gate_warnings)

    task = Task(
        task_id=state["task_id"],
        trace_id="0" * 16,
        skill_id=skill_id,
        done_criteria=state.get("done_criteria", []),
        input_payload=state.get("input_payload"),
    )

    reviewer_prompt = render_reviewer_prompt(
        task=task,
        contract=contract,
        agent_id=reviewer_id,
        builder_output=result,
        builder_id=builder_id,
    )

    clear_outbox("reviewer")
    write_inbox("reviewer", reviewer_prompt)

    # Update TASK.md
    _write_task_md(state, builder_id, reviewer_id, "reviewer")

    write_dashboard(
        task_id=state["task_id"],
        done_criteria=state.get("done_criteria", []),
        current_agent=reviewer_id,
        current_role="reviewer",
        conversation=state.get("conversation", []),
        status_msg=f"🟡 等待 **{reviewer_id}** 审查",
    )

    build_result = {
        "builder_output": result,
        "build_started_at": build_started,
        "current_role": "reviewer",
        "conversation": [
            {"role": "builder", "output": result.get("summary", ""), "t": time.time()}
        ],
    }
    graph_hooks.fire_exit("build", state, build_result)
    return build_result


# ── Node 3: Review ────────────────────────────────────────

def review_node(state: WorkflowState) -> dict:
    """Interrupt for reviewer → record decision."""
    _t0 = time.time()
    _ok = False
    try:
        result = _review_node_inner(state)
        _ok = result.get("final_status") != "failed"
        return result
    except GraphInterrupt:
        _ok = True
        raise
    except Exception as e:
        _log.exception("review_node failed: %s", e)
        graph_hooks.fire_error("review", state, e)
        return {
            "error": f"review_node: {e}",
            "final_status": "failed",
            "conversation": [{"role": "orchestrator", "action": "internal_error",
                              "details": str(e), "t": time.time()}],
        }
    finally:
        _t1 = time.time()
        tid = state.get("task_id", "")
        log_timing(tid, "review", _t0, _t1)
        graph_stats.record("review", int((_t1 - _t0) * 1000), _ok)
        try:
            save_state_snapshot(tid, "review", dict(state))
        except Exception:
            pass


def _review_node_inner(state: WorkflowState) -> dict:
    graph_hooks.fire_enter("review", state)
    reviewer_id = state.get("reviewer_id", "?")

    result = interrupt({
        "role": "reviewer",
        "agent": reviewer_id,
    })

    # Check for cancellation immediately after interrupt returns
    if _is_cancelled(state.get("task_id", "")):
        return {
            "final_status": "cancelled",
            "conversation": [{"role": "orchestrator", "action": "cancelled", "t": time.time()}],
        }

    # Timeout enforcement — use review_started_at for precise timing
    review_started = time.time()
    ref_time = state.get("review_started_at") or state.get("started_at", 0)
    if not ref_time:
        ref_time = review_started
    timeout = state.get("timeout_sec", 1800)
    if ref_time and timeout:
        elapsed = time.time() - ref_time
        if elapsed > timeout:
            return {
                "reviewer_output": {"decision": "reject", "feedback": f"TIMEOUT: reviewer took {int(elapsed)}s (limit: {timeout}s)"},
                "review_started_at": review_started,
                "conversation": [{"role": "orchestrator", "action": "timeout", "elapsed": int(elapsed), "t": time.time()}],
            }

    # Basic validation
    if not isinstance(result, dict):
        return {
            "reviewer_output": {"decision": "reject", "feedback": "Invalid reviewer output"},
            "conversation": [{"role": "reviewer", "decision": "reject", "t": time.time()}],
        }

    # Detect CLI driver error output
    if result.get("status") == "error":
        error_msg = result.get("summary", "unknown reviewer CLI error")
        return {
            "reviewer_output": {"decision": "reject", "feedback": f"Reviewer CLI failed: {error_msg}"},
            "conversation": [{"role": "reviewer", "decision": "reject", "t": time.time()}],
        }

    try:
        parsed = ReviewerOutput(**result)
        decision = parsed.decision.value
    except Exception:
        decision = result.get("decision", "reject")

    review_result = {
        "reviewer_output": result,
        "review_started_at": review_started,
        "conversation": [{"role": "reviewer", "decision": decision, "t": time.time()}],
    }
    graph_hooks.fire_exit("review", state, review_result)
    return review_result


# ── Node 4: Decide ────────────────────────────────────────

def decide_node(state: WorkflowState) -> dict:
    """Route based on reviewer decision: approve → end, reject/request_changes → retry or escalate."""
    _t0 = time.time()
    _ok = False
    try:
        result = _decide_node_inner(state)
        _ok = result.get("final_status") != "failed"
        return result
    except GraphInterrupt:
        _ok = True
        raise
    except Exception as e:
        _log.exception("decide_node failed: %s", e)
        graph_hooks.fire_error("decide", state, e)
        return {
            "error": f"decide_node: {e}",
            "final_status": "failed",
            "conversation": [{"role": "orchestrator", "action": "internal_error",
                              "details": str(e), "t": time.time()}],
        }
    finally:
        _t1 = time.time()
        tid = state.get("task_id", "")
        log_timing(tid, "decide", _t0, _t1)
        graph_stats.record("decide", int((_t1 - _t0) * 1000), _ok)
        try:
            save_state_snapshot(tid, "decide", dict(state))
        except Exception:
            pass


def _decide_node_inner(state: WorkflowState) -> dict:
    graph_hooks.fire_enter("decide", state)

    # Task 74: trim conversation if oversized
    convo = state.get("conversation", [])
    trimmed = trim_conversation(convo)
    if len(trimmed) < len(convo):
        state = {**state, "conversation": trimmed}

    reviewer_output = state.get("reviewer_output", {})
    decision = reviewer_output.get("decision", "reject")

    if decision == "approve":
        final_entry = {"role": "orchestrator", "action": "approved", "t": time.time()}
        full_convo = state.get("conversation", []) + [final_entry]
        write_dashboard(
            task_id=state["task_id"],
            done_criteria=state.get("done_criteria", []),
            current_agent=state.get("reviewer_id", ""),
            current_role="done",
            conversation=full_convo,
            status_msg="✅ 审查通过，任务完成",
        )
        archive_conversation(state["task_id"], full_convo)
        approve_result = {
            "final_status": "approved",
            "conversation": [final_entry],
        }
        graph_hooks.fire_exit("decide", state, approve_result)
        return approve_result

    # request_changes: soft reject — does NOT consume retry budget,
    # but capped at MAX_REQUEST_CHANGES to prevent infinite loops (SHIELDA pattern)
    if decision == "request_changes":
        feedback = reviewer_output.get("feedback", "")
        retry_count = state.get("retry_count", 0)  # do NOT increment
        budget = state.get("retry_budget", 2)

        # Count how many request_changes have occurred
        rc_count = sum(
            1 for e in state.get("conversation", [])
            if e.get("action") == "request_changes"
        )
        if rc_count >= MAX_REQUEST_CHANGES:
            _log.warning("request_changes cap reached (%d), escalating", rc_count)
            final_entry = {"role": "orchestrator", "action": "escalated",
                           "reason": f"request_changes cap ({rc_count})", "t": time.time()}
            full_convo = state.get("conversation", []) + [final_entry]
            archive_conversation(state["task_id"], full_convo)
            cap_result = {
                "error": "REQUEST_CHANGES_CAP",
                "final_status": "escalated",
                "conversation": [final_entry],
            }
            graph_hooks.fire_exit("decide", state, cap_result)
            return cap_result

        write_dashboard(
            task_id=state["task_id"],
            done_criteria=state.get("done_criteria", []),
            current_agent=state.get("builder_id", ""),
            current_role="builder",
            conversation=state.get("conversation", []),
            status_msg=f"🔧 需修改 ({retry_count}/{budget})",
        )
        rc_result = {
            "conversation": [
                {"role": "orchestrator", "action": "request_changes",
                 "feedback": feedback, "t": time.time()}
            ],
        }
        graph_hooks.fire_exit("decide", state, rc_result)
        return rc_result

    # Reject → check retry budget (consumes budget)
    retry_count = state.get("retry_count", 0) + 1
    budget = state.get("retry_budget", 2)

    if retry_count > budget:
        final_entry = {"role": "orchestrator", "action": "escalated", "reason": "budget exhausted", "t": time.time()}
        full_convo = state.get("conversation", []) + [final_entry]
        write_dashboard(
            task_id=state["task_id"],
            done_criteria=state.get("done_criteria", []),
            current_agent=state.get("reviewer_id", ""),
            current_role="escalated",
            conversation=full_convo,
            error=f"重试预算耗尽 ({retry_count - 1}/{budget})",
        )
        archive_conversation(state["task_id"], full_convo)
        esc_result = {
            "error": "BUDGET_EXHAUSTED",
            "retry_count": retry_count,
            "final_status": "escalated",
            "conversation": [final_entry],
        }
        graph_hooks.fire_exit("decide", state, esc_result)
        return esc_result

    # Has budget → retry with feedback
    feedback = reviewer_output.get("feedback", "")
    write_dashboard(
        task_id=state["task_id"],
        done_criteria=state.get("done_criteria", []),
        current_agent=state.get("builder_id", ""),
        current_role="builder",
        conversation=state.get("conversation", []),
        status_msg=f"🔄 重试 ❌ 驳回 ({retry_count}/{budget})",
    )

    retry_result = {
        "retry_count": retry_count,
        "conversation": [
            {"role": "orchestrator", "action": "retry", "feedback": feedback, "t": time.time()}
        ],
    }
    graph_hooks.fire_exit("decide", state, retry_result)
    return retry_result


# ── Cancel Detection ──────────────────────────────────

def _is_cancelled(task_id: str) -> bool:
    """Check if a task has been cancelled by reading its YAML status."""
    from multi_agent.config import tasks_dir
    import yaml
    path = tasks_dir() / f"{task_id}.yaml"
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("status") == "cancelled"
    except Exception:
        return False


# ── Routing ───────────────────────────────────────────

def _route_after_build(state: WorkflowState) -> str:
    """Skip review if build_node returned an error."""
    if state.get("error") or state.get("final_status") in ("failed", "cancelled"):
        return "end"
    return "review"


def route_decision(state: WorkflowState) -> str:
    if state.get("error"):
        return "end"
    if state.get("final_status") == "approved":
        return "end"
    return "retry"


# ── Graph Assembly ────────────────────────────────────────

def build_graph() -> StateGraph:
    """Build the 4-node LangGraph workflow (uncompiled)."""
    g = StateGraph(WorkflowState)

    g.add_node("plan", plan_node)
    g.add_node("build", build_node)
    g.add_node("review", review_node)
    g.add_node("decide", decide_node)

    g.add_edge(START, "plan")
    g.add_edge("plan", "build")
    g.add_conditional_edges("build", _route_after_build, {
        "review": "review",
        "end": END,
    })
    g.add_edge("review", "decide")
    g.add_conditional_edges("decide", route_decision, {
        "end": END,
        "retry": "plan",
    })

    return g


# Task 11: singleton connection pool — reuse connections per db_path
_conn_pool: dict[str, "sqlite3.Connection"] = {}
_conn_lock = __import__("threading").Lock()


def _get_connection(path: str) -> "sqlite3.Connection":
    """Get or create a SQLite connection for the given path (singleton per path)."""
    import atexit
    import sqlite3

    with _conn_lock:
        if path in _conn_pool:
            conn = _conn_pool[path]
            # Verify connection is still usable
            try:
                conn.execute("SELECT 1")
                return conn
            except sqlite3.ProgrammingError:
                del _conn_pool[path]
        conn = sqlite3.connect(path, check_same_thread=False)
        _conn_pool[path] = conn
        atexit.register(conn.close)
        return conn


_compiled_cache: dict[str, object] = {}


def reset_graph() -> None:
    """Clear compiled graph cache and connection pool. Used for testing."""
    _compiled_cache.clear()
    with _conn_lock:
        for conn in _conn_pool.values():
            try:
                conn.close()
            except Exception:
                pass
        _conn_pool.clear()


def compile_graph(*, db_path: str | None = None):
    """Compile graph with SQLite checkpointer (connection-pooled, cached)."""
    from pathlib import Path as _Path

    path = db_path or str(store_db_path())

    if path in _compiled_cache:
        return _compiled_cache[path]

    g = build_graph()

    # Ensure parent directory exists
    _Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = _get_connection(path)
    checkpointer = SqliteSaver(conn)
    compiled = g.compile(checkpointer=checkpointer)
    _compiled_cache[path] = compiled
    return compiled
