"""LangGraph 4-node graph: plan → build → review → decide."""

from __future__ import annotations

import functools
import json
import logging
import re
import sqlite3
import time
from collections.abc import Mapping
from operator import add
from typing import Annotated, Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from multi_agent._utils import DEFAULT_RUBBER_STAMP_PHRASES as _RUBBER_STAMP_PHRASES
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

_log = logging.getLogger(__name__)

MAX_SNAPSHOTS = 10
MAX_CONVERSATION_SIZE = 50
MAX_REQUEST_CHANGES = 3  # DDI research: effectiveness decays 60-80% after 2-3 attempts
MAX_TASK_DURATION_SEC = 7200  # 2h total task guard (OWASP LLM10:2025 DoW prevention)


class GraphStats:
    """Collect graph execution statistics per node."""

    def __init__(self):
        self._stats: dict[str, dict] = {}
        self._retry_outcomes: list[dict] = []

    def record(self, node: str, duration_ms: int, success: bool) -> None:
        if node not in self._stats:
            self._stats[node] = {"count": 0, "total_ms": 0, "errors": 0}
        s = self._stats[node]
        s["count"] += 1
        s["total_ms"] += duration_ms
        if not success:
            s["errors"] += 1

    def record_retry_outcome(self, retry_round: int, decision: str) -> None:
        """Track retry effectiveness per round (DDI measurement, Nature 2025)."""
        self._retry_outcomes.append({"round": retry_round, "decision": decision})

    def record_token_usage(self, node: str, usage: dict) -> None:
        """Track token usage from IDE driver output (FinOps, Zylos 2026).

        ``usage`` may contain: input_tokens, output_tokens, total_tokens, cost.
        Only recorded if the IDE driver reports it — non-breaking.
        """
        if node not in self._stats:
            self._stats[node] = {"count": 0, "total_ms": 0, "errors": 0}
        s = self._stats[node]
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            val = usage.get(key)
            if isinstance(val, (int, float)):
                s[key] = s.get(key, 0) + int(val)
        cost = usage.get("cost")
        if isinstance(cost, (int, float)):
            s["cost"] = round(s.get("cost", 0.0) + float(cost), 6)

    def summary(self) -> dict[str, dict]:
        result = {}
        for node, s in self._stats.items():
            avg = s["total_ms"] / s["count"] if s["count"] else 0
            error_rate = s["errors"] / s["count"] if s["count"] else 0
            entry = {
                "count": s["count"],
                "avg_ms": round(avg),
                "error_rate": round(error_rate, 3),
            }
            # Include token usage stats if recorded (FinOps)
            for tk in ("input_tokens", "output_tokens", "total_tokens", "cost"):
                if tk in s:
                    entry[tk] = s[tk]
            result[node] = entry
        # Retry effectiveness metrics (DDI / Agentless cost tracking)
        if self._retry_outcomes:
            total_retries = len(self._retry_outcomes)
            approves = sum(1 for r in self._retry_outcomes if r["decision"] == "approve")
            result["_retry_effectiveness"] = {
                "total_retries": total_retries,
                "retry_success_rate": round(approves / total_retries, 3) if total_retries else 0,
                "per_round": self._retry_outcomes,
            }
        return result

    def cumulative_totals(self) -> dict[str, int | float]:
        """Aggregate token/cost totals across all nodes (SWE-agent cost tracking).

        Returns dict with total_tokens, input_tokens, output_tokens, cost.
        """
        totals: dict[str, int | float] = {}
        for s in self._stats.values():
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                if key in s:
                    totals[key] = totals.get(key, 0) + s[key]
            if "cost" in s:
                totals["cost"] = round(totals.get("cost", 0.0) + s["cost"], 6)
        return totals

    def warn_if_over_budget(self, max_tokens: int = 500_000) -> bool:
        """Log warning if cumulative token usage exceeds threshold.

        Inspired by SWE-agent cost limits. Returns True if over budget.
        Does NOT hard-fail — the orchestrator doesn't control LLM calls,
        but warns for observability (FinOps).
        """
        totals = self.cumulative_totals()
        used = totals.get("total_tokens", 0)
        if used > max_tokens:
            _log.warning(
                "Token budget warning: %d tokens used (threshold: %d). "
                "Consider reviewing task complexity or retry count.",
                used, max_tokens,
            )
            return True
        return False

    def reset(self) -> None:
        """Clear all accumulated stats. Call at task start to prevent cross-task contamination
        (MAST NeurIPS 2025 — system design failure mode SD-4; MAS-FIRE 2026)."""
        self._stats.clear()
        self._retry_outcomes.clear()

    def save(self, path=None) -> None:
        """Save stats to .multi-agent/stats.json."""
        from multi_agent.config import workspace_dir as _ws
        p = path or (_ws() / "stats.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(json.dumps(self.summary(), indent=2), encoding="utf-8")
        except OSError:
            pass


graph_stats = GraphStats()


def log_timing(task_id: str, node: str, start: float, end: float) -> None:
    """Append a timing entry to .multi-agent/logs/timing-{task_id}.jsonl."""
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
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def trim_conversation(conversation: list[dict]) -> list[dict]:
    """Keep conversation within MAX_CONVERSATION_SIZE, preserving first and last entries.

    NOTE: This trims a *local copy* used for prompt rendering, dashboard display,
    and history archiving. The LangGraph internal state (Annotated[list, add])
    accumulates all entries and is NOT trimmed by this function. For long-running
    tasks, this distinction matters: prompts stay bounded, but the checkpoint DB
    may grow large (Context Rot — Chroma/Hong et al. 2025).

    Includes a lightweight summary of removed entries so downstream AI agents
    retain context awareness (literature: JetBrains context management research).
    """
    if len(conversation) <= MAX_CONVERSATION_SIZE:
        return conversation
    keep_head = 5
    keep_tail = MAX_CONVERSATION_SIZE - keep_head - 1
    removed = conversation[keep_head:-keep_tail]
    # Build lightweight summary of what was removed.
    # Keep ALL retry/request_changes feedback (not just 3) to prevent
    # critical bug context loss (RepairAgent ICSE 2025, Redis 2026).
    action_counts: dict[str, int] = {}
    feedback_snippets: list[str] = []
    for e in removed:
        a = e.get("action", "unknown")
        action_counts[a] = action_counts.get(a, 0) + 1
        fb = e.get("feedback", "")
        if fb and isinstance(fb, str) and a in ("retry", "request_changes"):
            feedback_snippets.append(fb[:120])
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
    from multi_agent.config import workspace_dir as _ws_dir
    snap_dir = _ws_dir() / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    # G2: Sanitize task_id/node_name to prevent path traversal
    # (e.g. task_id="../../etc/passwd" would write outside snapshots dir)
    safe_tid = re.sub(r"[^a-zA-Z0-9._-]", "_", task_id)[:64]
    safe_node = re.sub(r"[^a-zA-Z0-9._-]", "_", node_name)[:32]

    ts = int(time.time() * 1000)
    safe_state = {}
    for k, v in state.items():
        try:
            json.dumps(v)
            safe_state[k] = v
        except (TypeError, ValueError):
            safe_state[k] = str(v)

    path = snap_dir / f"{safe_tid}-{safe_node}-{ts}.json"
    try:
        path.write_text(json.dumps(safe_state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return

    # Cleanup old snapshots — sort by filename (embeds timestamp) for reliability
    try:
        existing = sorted(snap_dir.glob(f"{safe_tid}-*.json"), key=lambda p: p.name)
        while len(existing) > MAX_SNAPSHOTS:
            existing.pop(0).unlink(missing_ok=True)
    except OSError:
        pass


# ── Event Hooks ──────────────────────────────────────────

_hook_logger = logging.getLogger(__name__ + ".hooks")


class EventHooks:
    """Registry for graph execution event callbacks.

    Usage:
        hooks = EventHooks()
        hooks.on_node_enter("plan", lambda state: print("plan started"))
        hooks.on_node_exit("build", lambda state, result: log(result))
        hooks.on_error(lambda node, state, err: alert(err))
    """

    def __init__(self) -> None:
        self._enter: dict[str, list[Any]] = {}   # node_name → [callbacks]
        self._exit: dict[str, list[Any]] = {}    # node_name → [callbacks]
        self._error: list[Any] = []              # global error handlers

    def on_node_enter(self, node: str, callback: Any) -> None:
        self._enter.setdefault(node, []).append(callback)

    def on_node_exit(self, node: str, callback: Any) -> None:
        self._exit.setdefault(node, []).append(callback)

    def on_error(self, callback: Any) -> None:
        self._error.append(callback)

    def fire_enter(self, node: str, state: Mapping[str, Any]) -> None:
        for cb in self._enter.get(node, []):
            try:
                cb(state)
            except Exception as e:
                _hook_logger.warning("Hook enter/%s error: %s", node, e)

    def fire_exit(self, node: str, state: Mapping[str, Any], result: dict[str, Any] | None = None) -> None:
        for cb in self._exit.get(node, []):
            try:
                cb(state, result)
            except Exception as e:
                _hook_logger.warning("Hook exit/%s error: %s", node, e)

    def fire_error(self, node: str, state: Mapping[str, Any], error: Exception) -> None:
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
    if kind == "enter" and node is not None:
        graph_hooks.on_node_enter(node, callback)
    elif kind == "exit" and node is not None:
        graph_hooks.on_node_exit(node, callback)
    elif kind == "error":
        graph_hooks.on_error(callback)


# ── Node Decorator ────────────────────────────────────────


def _graph_node(node_name: str):
    """Decorator that wraps a node's inner function with standardised
    timing, stats collection, snapshot saving, and error handling.

    Eliminates the ~15-line boilerplate that was previously copy-pasted
    across plan_node, build_node, review_node, and decide_node.
    """

    def decorator(inner_fn):
        @functools.wraps(inner_fn)
        def wrapper(state: "WorkflowState") -> dict:
            _t0 = time.time()
            _ok = False
            try:
                result: dict[str, Any] = inner_fn(state)
                _ok = result.get("final_status") != "failed"
                return result
            except GraphInterrupt:
                _ok = True
                raise
            except Exception as e:
                _log.exception("%s failed: %s", node_name, e)
                graph_hooks.fire_error(node_name, state, e)
                return {
                    "error": f"{node_name}_node: {e}",
                    "final_status": "failed",
                    "conversation": [
                        {"role": "orchestrator", "action": "internal_error",
                         "details": str(e), "t": time.time()}
                    ],
                }
            finally:
                _t1 = time.time()
                tid = state.get("task_id", "")
                log_timing(tid, node_name, _t0, _t1)
                graph_stats.record(node_name, int((_t1 - _t0) * 1000), _ok)
                try:
                    save_state_snapshot(tid, node_name, dict(state))
                except Exception:
                    pass

        # Preserve the original docstring from inner_fn but keep
        # the wrapper name matching the expected LangGraph node name.
        wrapper.__qualname__ = f"{node_name}_node"
        return wrapper

    return decorator


# ── State ─────────────────────────────────────────────────

class WorkflowState(TypedDict, total=False):
    # Input (set once at start)
    task_id: str
    requirement: str
    skill_id: str
    done_criteria: list[str]
    timeout_sec: int
    input_payload: dict[str, Any]
    workflow_mode: str         # "strict" | "normal"
    review_policy: dict[str, Any]

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
    task_started_at: float | None  # total duration anchor (DoW guard)
    build_started_at: float | None
    review_started_at: float | None

    # Accumulate
    conversation: Annotated[list[dict], add]

    # Hierarchy
    parent_task_id: str | None

    # Orchestrator (not stored in graph but used for status queries)
    orchestrator_id: str | None

    # Terminal
    error: str | None
    final_status: str | None


def _is_rubber_stamp_approval(reviewer_output: dict[str, Any]) -> bool:
    """Detect shallow reviewer approvals that lack independent verification."""
    decision = str(reviewer_output.get("decision", "")).lower().strip()
    if decision != "approve":
        return False
    summary = str(reviewer_output.get("summary", ""))
    reasoning = str(reviewer_output.get("reasoning", ""))
    policy = reviewer_output.get("_rubber_policy")
    if isinstance(policy, dict):
        phrases_raw = policy.get("generic_phrases")
        if isinstance(phrases_raw, list):
            phrases = [str(p).strip().lower() for p in phrases_raw if str(p).strip()]
        else:
            phrases = list(_RUBBER_STAMP_PHRASES)
        generic_max = policy.get("generic_summary_max_len", 50)
        shallow_max = policy.get("shallow_summary_max_len", 30)
    else:
        phrases = list(_RUBBER_STAMP_PHRASES)
        generic_max = 50
        shallow_max = 30
    try:
        generic_max = int(generic_max)
    except (TypeError, ValueError):
        generic_max = 50
    try:
        shallow_max = int(shallow_max)
    except (TypeError, ValueError):
        shallow_max = 30
    if generic_max <= 0:
        generic_max = 50
    if shallow_max <= 0:
        shallow_max = 30
    is_generic = any(p in summary.lower() for p in phrases) and len(summary) < generic_max
    is_shallow = not reasoning.strip() and len(summary) < shallow_max
    return is_generic or is_shallow


# ── TASK.md — Universal Entry Point ──────────────────────

def _write_task_md(state: Mapping[str, Any], builder_id: str, reviewer_id: str, current_role: str) -> None:
    """Write TASK.md — THE single self-contained file for the IDE AI.

    TASK.md embeds the full prompt content inline so the IDE AI gets
    everything it needs from ONE file reference. No jumping to inbox files.
    """
    from multi_agent.config import inbox_dir, outbox_dir, workspace_dir

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

@_graph_node("plan")
def plan_node(state: WorkflowState) -> dict:
    graph_hooks.fire_enter("plan", state)

    # Reset stats on first run of a new task to prevent cross-task contamination
    # (MAST NeurIPS 2025 SD-4; MAS-FIRE 2026 reliability evaluation).
    if state.get("retry_count", 0) == 0:
        graph_stats.reset()

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
    rev_out = state.get("reviewer_output") or {}
    bld_out = state.get("builder_output") or {}
    if retry_count > 0 and rev_out:
        retry_feedback = rev_out.get("feedback", "")
    if retry_count > 0 and bld_out:
        previous_summary = bld_out.get("summary", "")

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

    now = time.time()
    result = {
        "current_role": "builder",
        "builder_id": builder_id,
        "reviewer_id": reviewer_id,
        "started_at": now,  # type: ignore[dict-item]  # LangGraph partial state
        "task_started_at": state.get("task_started_at") or now,  # type: ignore[dict-item]
        "conversation": [
            {"role": "orchestrator", "action": "assigned", "agent": builder_id, "t": now}
        ],
    }
    graph_hooks.fire_exit("plan", state, result)
    return result


# ── Node 2: Build ─────────────────────────────────────────

@_graph_node("build")
def build_node(state: WorkflowState) -> dict:
    graph_hooks.fire_enter("build", state)

    # Total task duration guard (OWASP LLM10:2025 — DoW prevention)
    task_started = state.get("task_started_at")
    if task_started:
        total_elapsed = time.time() - task_started
        if total_elapsed > MAX_TASK_DURATION_SEC:
            return {
                "error": (f"TOTAL_TIMEOUT: task running {int(total_elapsed)}s exceeds "
                          f"{MAX_TASK_DURATION_SEC}s limit (node=build, "
                          f"agent={state.get('builder_id', '?')})"),
                "final_status": "failed",
                "conversation": [{"role": "orchestrator", "action": "total_timeout",
                                  "node": "build", "elapsed": int(total_elapsed),
                                  "t": time.time()}],
            }

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

    # Validate builder output (structured output enforcement — Lanham 2026, ACL 2025)
    errors: list[str] = []
    if not isinstance(result, dict):
        errors.append("output must be a JSON object")
    else:
        if "status" not in result:
            errors.append("missing 'status' field")
        if "summary" not in result:
            errors.append("missing 'summary' field")
        # Type-check optional but structurally important fields
        if "changed_files" in result and not isinstance(result["changed_files"], list):
            errors.append("'changed_files' must be a list")
        if "check_results" in result and not isinstance(result["check_results"], dict):
            errors.append("'check_results' must be a dict")

    if errors:
        return {
            "error": f"Builder output invalid: {'; '.join(errors)}",
            "final_status": "failed",
            "conversation": [{"role": "builder", "output": "INVALID", "t": time.time()}],
        }

    # Record optional token usage from IDE driver (FinOps)
    if isinstance(result.get("token_usage"), dict):
        graph_stats.record_token_usage("build", result["token_usage"])

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

    # C3: Semantic validation — completed with no changed_files is suspicious
    # (MAST NeurIPS 2025 TV: task verification must be independent of claim)
    builder_status = str(result.get("status", "")).lower()
    changed_files = result.get("changed_files", [])
    if builder_status in ("completed", "success", "done") and not changed_files:
        _log.warning(
            "Builder claims '%s' but reported no changed_files — "
            "forwarding to reviewer with warning.", builder_status,
        )
        result.setdefault("_empty_changeset_warning", True)

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

@_graph_node("review")
def review_node(state: WorkflowState) -> dict:
    graph_hooks.fire_enter("review", state)

    # Total task duration guard (OWASP LLM10:2025 — DoW prevention)
    task_started = state.get("task_started_at")
    if task_started:
        total_elapsed = time.time() - task_started
        if total_elapsed > MAX_TASK_DURATION_SEC:
            return {
                "error": (f"TOTAL_TIMEOUT: task running {int(total_elapsed)}s exceeds "
                          f"{MAX_TASK_DURATION_SEC}s limit (node=review, "
                          f"agent={state.get('reviewer_id', '?')})"),
                "final_status": "failed",
                "conversation": [{"role": "orchestrator", "action": "total_timeout",
                                  "node": "review", "elapsed": int(total_elapsed),
                                  "t": time.time()}],
            }

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

    # Record optional token usage from IDE driver (FinOps)
    if isinstance(result.get("token_usage"), dict):
        graph_stats.record_token_usage("review", result["token_usage"])

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

    # Empty feedback on reject/request_changes is actionless — force a fallback message
    if decision in ("reject", "request_changes"):
        feedback = result.get("feedback", "")
        if not feedback or not feedback.strip():
            _log.warning(
                "Reviewer %s without feedback — injecting generic prompt.",
                decision,
            )
            result["feedback"] = (
                "Reviewer did not provide specific feedback. "
                "Please re-examine your implementation against the done criteria."
            )

    # Rubber-stamp detection (MAST NeurIPS 2025 TV-1; collusion-aware oversight):
    # Flag approvals that lack substantive independent verification evidence.
    review_policy = state.get("review_policy")
    rubber_policy = review_policy.get("rubber_stamp") if isinstance(review_policy, dict) else None
    detector_input = dict(result)
    if isinstance(rubber_policy, dict):
        detector_input["_rubber_policy"] = rubber_policy

    if _is_rubber_stamp_approval(detector_input):
        reasoning = str(result.get("reasoning", ""))
        summary = str(result.get("summary", ""))
        _log.warning(
            "Rubber-stamp approve detected: reasoning=%r, summary=%r (len=%d). "
            "Collusion risk — reviewer may not have performed independent verification "
            "(MAST NeurIPS 2025 TV-1).",
            reasoning[:60] if reasoning else "", summary[:60], len(summary),
        )
        # Inject warning into output so decide_node can see it
        result["_rubber_stamp_warning"] = True

    review_result = {
        "reviewer_output": result,
        "review_started_at": review_started,
        "conversation": [{"role": "reviewer", "decision": decision, "t": time.time()}],
    }
    graph_hooks.fire_exit("review", state, review_result)
    return review_result


# ── Node 4: Decide ────────────────────────────────────────

@_graph_node("decide")
def decide_node(state: WorkflowState) -> dict:
    graph_hooks.fire_enter("decide", state)

    # Early exit if state is already terminal (e.g., review_node detected cancellation
    # or TOTAL_TIMEOUT). Without this, decide processes stale reviewer_output.
    fs = state.get("final_status")
    if fs and fs not in ("approved",):
        passthrough = {
            "final_status": fs,
            "conversation": [{"role": "orchestrator", "action": "terminal_passthrough",
                              "original_status": fs, "t": time.time()}],
        }
        if state.get("error"):
            passthrough["error"] = state["error"]  # type: ignore[assignment]  # LangGraph partial state
        graph_hooks.fire_exit("decide", state, passthrough)
        return passthrough

    # Task 74: trim conversation if oversized
    convo = state.get("conversation", [])
    trimmed = trim_conversation(convo)
    if len(trimmed) < len(convo):
        state = {**state, "conversation": trimmed}

    reviewer_output: dict[str, Any] = state.get("reviewer_output") or {}
    review_policy = state.get("review_policy")
    rubber_policy = review_policy.get("rubber_stamp") if isinstance(review_policy, dict) else {}
    reviewer_for_detect = dict(reviewer_output)
    if isinstance(rubber_policy, dict):
        reviewer_for_detect["_rubber_policy"] = rubber_policy
    decision = str(reviewer_output.get("decision", "reject")).lower().strip()
    strict_mode = str(state.get("workflow_mode", "")).lower().strip() == "strict"
    rubber_stamp = bool(reviewer_output.get("_rubber_stamp_warning")) or _is_rubber_stamp_approval(reviewer_for_detect)
    block_on_strict = True
    if isinstance(rubber_policy, dict):
        block_on_strict = bool(rubber_policy.get("block_on_strict", True))

    if decision == "approve" and rubber_stamp and strict_mode and block_on_strict:
        _log.warning(
            "Strict mode blocks rubber-stamp approval for task %s; forcing request_changes.",
            state.get("task_id", "?"),
        )
        reviewer_output = dict(reviewer_output)
        feedback = str(reviewer_output.get("feedback", "")).strip()
        if not feedback:
            feedback = (
                "审批被 strict 模式拦截：检测到 rubber-stamp 评审。"
                "请给出独立验证证据（失败/通过用例、风险点、文件级检查结论）后再提交 approve。"
            )
        reviewer_output["decision"] = "request_changes"
        reviewer_output["feedback"] = feedback
        reviewer_output["_rubber_stamp_warning"] = True
        decision = "request_changes"

    # Track retry effectiveness (DDI measurement) — count all review rounds,
    # not just reject retries. request_changes doesn't increment retry_count
    # but still represents a review round for DDI tracking.
    review_round = sum(
        1 for e in state.get("conversation", [])
        if e.get("action") in ("retry", "request_changes")
    )
    if review_round > 0:
        graph_stats.record_retry_outcome(review_round, decision)

    if decision == "approve":
        # C1: Check rubber-stamp warning from review_node (MAST NeurIPS 2025 TV-1).
        # If reviewer approved without substantive reasoning, record audit trail.
        convo_entries: list[dict] = []
        if rubber_stamp:
            _log.warning(
                "Task %s approved with rubber-stamp warning — review may lack "
                "independent verification (MAST TV-1). Approving with audit note.",
                state.get("task_id", "?"),
            )
            convo_entries.append({
                "role": "orchestrator", "action": "rubber_stamp_warning",
                "details": "Reviewer approved without substantive reasoning. "
                           "Approval accepted but flagged for audit.",
                "reviewer_id": state.get("reviewer_id", "?"),
                "t": time.time(),
            })

        final_entry = {"role": "orchestrator", "action": "approved", "t": time.time()}
        convo_entries.append(final_entry)
        full_convo = state.get("conversation", []) + convo_entries
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
            "conversation": convo_entries,
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
        last_feedback = reviewer_output.get("feedback", "")
        # Truncate for diagnostic summary (Auto-Diagnose, ICSE 2026 SEIP)
        feedback_summary = (last_feedback[:200] + "…") if len(last_feedback) > 200 else last_feedback
        final_entry = {"role": "orchestrator", "action": "escalated",
                       "reason": "budget exhausted",
                       "last_feedback": feedback_summary, "t": time.time()}
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
            "error": f"BUDGET_EXHAUSTED: {feedback_summary}" if feedback_summary else "BUDGET_EXHAUSTED",
            "retry_count": retry_count,
            "final_status": "escalated",
            "conversation": [final_entry],
        }
        graph_hooks.fire_exit("decide", state, esc_result)
        return esc_result

    # Has budget → retry with feedback
    raw_feedback = reviewer_output.get("feedback", "")

    # E2: Structure retry feedback (FeedbackEval arXiv 2504.06939:
    # structured feedback >> free-text for LLM comprehension).
    decision_label = reviewer_output.get("decision", "reject")
    sections = [f"## Reviewer Decision: {decision_label.upper()}"]
    sections.append(f"### Feedback\n{raw_feedback}")
    # Include gate warnings if present
    prev_builder = state.get("builder_output")
    if isinstance(prev_builder, dict):
        gw = prev_builder.get("gate_warnings")
        if gw:
            sections.append("### Quality Gate Warnings\n" + "\n".join(f"- {w}" for w in gw))
    sections.append(f"### Retry Status\nAttempt {retry_count}/{budget}")
    feedback = "\n\n".join(sections)

    # DDI decay warning (Nature Sci Rep 2025, NeurIPS 2024 explore-exploit):
    # Debugging effectiveness decays 60-80% after 2-3 attempts.
    # Suggest fresh approach instead of incremental patching.
    if retry_count >= 2:
        _log.warning(
            "DDI decay: retry %d/%d — effectiveness likely degraded 60-80%%. "
            "Consider fresh approach instead of incremental patching.",
            retry_count, budget,
        )
        feedback += (
            "\n\n⚠️ 注意: 这是第 {rc} 次重试。研究表明调试效果在 2-3 次后衰减 60-80%。"
            " 建议: 考虑从头重新实现而非继续修补同一代码 (fresh start strategy)。"
        ).format(rc=retry_count)

    write_dashboard(
        task_id=state["task_id"],
        done_criteria=state.get("done_criteria", []),
        current_agent=state.get("builder_id", ""),
        current_role="builder",
        conversation=state.get("conversation", []),
        status_msg=f"🔄 重试 ❌ 驳回 ({retry_count}/{budget})",
    )

    # Preserve previous round context to prevent inter-round information loss
    # (Agent Error Taxonomy ICLR 2026; MAST NeurIPS 2025 IA-2).
    retry_entry: dict[str, Any] = {
        "role": "orchestrator", "action": "retry", "feedback": feedback, "t": time.time(),
    }
    # prev_builder already fetched above (E2 structured feedback)
    if isinstance(prev_builder, dict):
        changed = prev_builder.get("changed_files")
        if changed:
            retry_entry["prev_changed_files"] = changed
        gates = prev_builder.get("gate_warnings")
        if gates:
            retry_entry["prev_gate_warnings"] = gates

    retry_result = {
        "retry_count": retry_count,
        "conversation": [retry_entry],
    }
    graph_hooks.fire_exit("decide", state, retry_result)
    return retry_result


# ── Cancel Detection ──────────────────────────────────

def _is_cancelled(task_id: str) -> bool:
    """Check if a task has been cancelled by reading its YAML status.

    NOTE: This performs a non-locked file read. A concurrent write (e.g. from
    ``ma cancel``) could yield a partial/corrupt read. The broad ``except``
    below treats any parse failure as "not cancelled", which is the safe
    default — the next poll cycle will re-check.
    """
    import yaml

    from multi_agent.config import tasks_dir
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
    fs = state.get("final_status")
    if fs in ("approved", "failed", "cancelled", "escalated"):
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


# Task 11: singleton connection pool — reuse connections per db_path.
# Use RLock because compile_graph() may call _get_connection() while already
# holding this lock during cold-start path compilation.
_conn_pool: dict[str, "sqlite3.Connection"] = {}
_conn_lock = __import__("threading").RLock()


def _get_connection(path: str) -> "sqlite3.Connection":
    """Get or create a SQLite connection for the given path (singleton per path).

    THREAD SAFETY NOTE: check_same_thread=False allows cross-thread reuse,
    but SQLite itself serializes writes. Concurrent invoke() calls sharing
    this connection are safe for LangGraph checkpoint reads/writes because
    SqliteSaver uses transactions internally. However, if future code adds
    raw SQL outside SqliteSaver, wrap it in _conn_lock or use a dedicated
    connection to avoid "database is locked" under heavy concurrency.
    """
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
    """Clear compiled graph cache, connection pool, and stats. Used for testing."""
    _compiled_cache.clear()
    graph_stats.reset()
    with _conn_lock:
        for conn in _conn_pool.values():
            try:
                conn.close()
            except Exception:
                pass
        _conn_pool.clear()


def compile_graph(*, db_path: str | None = None):
    """Compile graph with SQLite checkpointer (connection-pooled, cached).

    G3: Protected by _conn_lock to prevent concurrent threads from
    compiling the graph simultaneously (double-checked locking pattern).
    """
    from pathlib import Path as _Path

    path = db_path or str(store_db_path())

    # Fast path — no lock needed if already cached (CPython GIL makes dict read safe)
    if path in _compiled_cache:
        return _compiled_cache[path]

    with _conn_lock:
        # Double-check after acquiring lock
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


# ── Backward-compatible aliases for test code ────────────
# The @_graph_node decorator merged *_node and _*_node_inner into a single
# decorated function.  functools.wraps preserves __wrapped__ pointing to the
# original inner function.  Expose _*_node_inner names so existing tests that
# call the inner logic directly (bypassing timing/stats) keep working.
_plan_node_inner = plan_node.__wrapped__     # type: ignore[attr-defined]
_build_node_inner = build_node.__wrapped__   # type: ignore[attr-defined]
_review_node_inner = review_node.__wrapped__ # type: ignore[attr-defined]
_decide_node_inner = decide_node.__wrapped__ # type: ignore[attr-defined]
