"""LangGraph 4-node graph: plan → build → review → decide."""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import sqlite3
import time
from collections.abc import Callable, Mapping
from typing import Annotated, Any

import yaml
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from multi_agent._utils import DEFAULT_RUBBER_STAMP_PHRASES as _RUBBER_STAMP_PHRASES
from multi_agent._utils import (
    count_nonempty_entries as _count_nonempty_entries,
)
from multi_agent._utils import (
    positive_int as _positive_int,
)
from multi_agent.agent_registry import load_agents
from multi_agent.config import store_db_path
from multi_agent.contract import load_contract, validate_preconditions
from multi_agent.dashboard import write_dashboard

# A3 refactor: infrastructure extracted to graph_infra.py
from multi_agent.graph_infra import (  # noqa: F401 — re-exported for backward compat
    MAX_CONVERSATION_SIZE,
    MAX_SNAPSHOTS,
    EventHooks,
    GraphStats,
    graph_hooks,
    graph_stats,
    log_timing,
    register_hook,
    save_state_snapshot,
    trim_conversation,
)
from multi_agent.prompt import render_builder_prompt, render_reviewer_prompt
from multi_agent.router import resolve_builder, resolve_reviewer
from multi_agent.schema import (
    BuilderOutput,
    ReviewerOutput,
    Task,
    make_event,
)
from multi_agent.workspace import (
    archive_conversation,
    clear_outbox,
    write_inbox,
)

_log = logging.getLogger(__name__)

MAX_REQUEST_CHANGES = 3  # DDI research: effectiveness decays 60-80% after 2-3 attempts
MAX_TASK_DURATION_SEC = 7200  # 2h total task guard (OWASP LLM10:2025 DoW prevention)
_PASS_GATE_VALUES = frozenset({"pass", "passed", "ok", "success", "true"})
_FALLBACK_MARKERS = (
    "adapter fallback",
    "_adapter_fallback",
    "codex rc=",
    "rc=124",
    "fallback used rc=",
)


# ── Node Decorator ────────────────────────────────────────


_NodeFn = Callable[["WorkflowState"], dict[str, Any]]


def _graph_node(node_name: str) -> Callable[[_NodeFn], _NodeFn]:
    """Decorator that wraps a node's inner function with standardised
    timing, stats collection, snapshot saving, and error handling.

    Eliminates the ~15-line boilerplate that was previously copy-pasted
    across plan_node, build_node, review_node, and decide_node.
    """

    def decorator(inner_fn: _NodeFn) -> _NodeFn:
        @functools.wraps(inner_fn)
        def wrapper(state: WorkflowState) -> dict[str, Any]:
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
                        make_event("orchestrator", action="internal_error",
                                   details=str(e))
                    ],
                }
            finally:
                _t1 = time.time()
                tid = state.get("task_id", "")
                log_timing(tid, node_name, _t0, _t1)
                graph_stats.record(node_name, int((_t1 - _t0) * 1000), _ok)
                with contextlib.suppress(Exception):
                    save_state_snapshot(tid, node_name, dict(state))

        # Preserve the original docstring from inner_fn but keep
        # the wrapper name matching the expected LangGraph node name.
        wrapper.__qualname__ = f"{node_name}_node"
        return wrapper

    return decorator


# ── State ─────────────────────────────────────────────────


def _conversation_reducer(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Custom reducer that caps conversation size at MAX_CONVERSATION_SIZE.

    Unlike the default ``add`` reducer which accumulates unboundedly,
    this applies trim_conversation() to keep checkpoint size bounded.
    """
    combined = (existing or []) + (new or [])
    if len(combined) > MAX_CONVERSATION_SIZE:
        return trim_conversation(combined)
    return combined


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
    builder_output: dict[str, Any] | None
    reviewer_output: dict[str, Any] | None
    retry_count: int
    retry_budget: int
    request_changes_count: int
    started_at: float
    task_started_at: float | None  # total duration anchor (DoW guard)
    build_started_at: float | None
    review_started_at: float | None

    # Accumulate
    conversation: Annotated[list[dict[str, Any]], _conversation_reducer]

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
def plan_node(state: WorkflowState) -> dict[str, Any]:
    graph_hooks.fire_enter("plan", state)

    # Reset stats on first run of a new task to prevent cross-task contamination
    # (MAST NeurIPS 2025 SD-4; MAS-FIRE 2026 reliability evaluation).
    if state.get("retry_count", 0) == 0:
        graph_stats.reset()
        # Plugin hook: task start
        with contextlib.suppress(Exception):
            from multi_agent.hooks import emit
            emit("on_task_start", {"task_id": state.get("task_id", ""), "requirement": str(state.get("requirement", ""))[:200]})

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
                make_event("orchestrator", action="precondition_failed",
                          details=precondition_errors)
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

    # Smart Retry: inject relevant semantic memory on retries
    if retry_count > 0:
        with contextlib.suppress(Exception):
            from multi_agent.semantic_memory import get_context
            req_short = state.get("requirement", "")[:300]
            fb_short = retry_feedback[:300]
            query = f"{req_short} {fb_short}"
            mem_ctx = get_context(query, top_k=3, max_chars=1500)
            if mem_ctx:
                prompt += f"\n\n{mem_ctx}"
                _log.info("Smart retry: injected %d chars of memory context", len(mem_ctx))

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
        # Reset per-node timestamps so retry cycles measure from plan_node,
        # not from the *previous* build/review (stale ref_time → false timeout).
        "build_started_at": None,  # type: ignore[dict-item]  # LangGraph partial state
        "review_started_at": None,  # type: ignore[dict-item]  # LangGraph partial state
        "conversation": [
            make_event("orchestrator", action="assigned", agent=builder_id)
        ],
    }
    graph_hooks.fire_exit("plan", state, result)
    return result


def _check_total_timeout(state: WorkflowState, node: str) -> dict[str, Any] | None:
    """Return error dict if total task duration exceeded, else None."""
    task_started = state.get("task_started_at")
    if not task_started:
        return None
    total_elapsed = time.time() - task_started
    if total_elapsed <= MAX_TASK_DURATION_SEC:
        return None
    agent_key = "builder_id" if node == "build" else "reviewer_id"
    return {
        "error": (f"TOTAL_TIMEOUT: task running {int(total_elapsed)}s exceeds "
                  f"{MAX_TASK_DURATION_SEC}s limit (node={node}, "
                  f"agent={state.get(agent_key, '?')})"),
        "final_status": "failed",
        "conversation": [make_event("orchestrator", action="total_timeout",
                                   node=node, elapsed=int(total_elapsed))],
    }


def _validate_builder_output(result: Any) -> dict[str, Any] | None:
    """Validate builder output structure. Returns error dict or None if valid."""
    errors: list[str] = []
    if not isinstance(result, dict):
        errors.append("output must be a JSON object")
    else:
        if "status" not in result:
            errors.append("missing 'status' field")
        if "summary" not in result:
            errors.append("missing 'summary' field")
        if "changed_files" in result and not isinstance(result["changed_files"], list):
            errors.append("'changed_files' must be a list")
        if "check_results" in result and not isinstance(result["check_results"], dict):
            errors.append("'check_results' must be a dict")
    if errors:
        return {
            "error": f"Builder output invalid: {'; '.join(errors)}",
            "final_status": "failed",
            "conversation": [make_event("builder", output="INVALID")],
        }
    return None


def _enrich_builder_result(result: dict[str, Any], state: WorkflowState) -> None:
    """Add semantic warnings and quality gate checks to builder result (in-place)."""
    # C3: Semantic validation — completed with no changed_files is suspicious
    builder_status = str(result.get("status", "")).lower()
    changed_files = result.get("changed_files", [])
    if builder_status in ("completed", "success", "done") and not changed_files:
        _log.warning(
            "Builder claims '%s' but reported no changed_files — "
            "forwarding to reviewer with warning.", builder_status,
        )
        result.setdefault("_empty_changeset_warning", True)

    # A4: Quality gate enforcement
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
    if gate_warnings:
        result.setdefault("gate_warnings", gate_warnings)


# ── Node 2: Build ─────────────────────────────────────────

@_graph_node("build")
def build_node(state: WorkflowState) -> dict[str, Any]:
    graph_hooks.fire_enter("build", state)

    # Total task duration guard (OWASP LLM10:2025 — DoW prevention)
    timeout_err = _check_total_timeout(state, "build")
    if timeout_err:
        return timeout_err

    builder_id = state.get("builder_id", "?")
    reviewer_id = state.get("reviewer_id", "?")
    build_started = time.time()

    # Interrupt: wait for builder to submit via `my done`
    # Role-based: inbox is always builder.md regardless of which IDE
    result = interrupt({
        "role": "builder",
        "agent": builder_id,
    })

    # Check for cancellation immediately after interrupt returns
    if _is_cancelled(state.get("task_id", "")):
        return {
            "final_status": "cancelled",
            "conversation": [make_event("orchestrator", action="cancelled")],
        }

    # A3: Timeout enforcement — use build_started_at for precise timing
    build_finished = time.time()
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
                "conversation": [make_event("orchestrator", action="timeout", elapsed=int(elapsed))],
            }

    # Validate builder output
    validation_error = _validate_builder_output(result)
    if validation_error:
        return validation_error

    # Record optional token usage from IDE driver (FinOps)
    if isinstance(result.get("token_usage"), dict):
        graph_stats.record_token_usage("build", result["token_usage"])
        # Persist to FinOps log for cross-task aggregation
        with contextlib.suppress(Exception):
            from multi_agent.finops import record_task_usage
            tu = result["token_usage"]
            record_task_usage(
                task_id=state.get("task_id", ""),
                node="build",
                agent_id=state.get("builder_id", ""),
                input_tokens=int(tu.get("input_tokens", 0)),
                output_tokens=int(tu.get("output_tokens", 0)),
                total_tokens=int(tu.get("total_tokens", 0)),
                cost=float(tu.get("cost", 0.0)),
                model=str(tu.get("model", "")),
            )

    # Detect CLI driver blocked/error output — don't waste reviewer's time
    status_lower = str(result.get("status", "")).lower().strip()
    if status_lower in {"error", "blocked"}:
        error_msg = result.get("summary", "unknown CLI error")
        return {
            "error": f"Builder failed: {error_msg}",
            "final_status": "failed",
            "conversation": [make_event("builder", output=f"ERROR: {error_msg}")],
        }

    # Validate via Pydantic (non-fatal — we log warnings but proceed)
    with contextlib.suppress(Exception):
        BuilderOutput(**result)

    # C3 + A4: Semantic validation + quality gate enforcement
    _enrich_builder_result(result, state)

    skill_id = state["skill_id"]
    contract = load_contract(skill_id)
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
            make_event("builder", output=result.get("summary", ""))
        ],
    }
    # Plugin hook: build complete
    with contextlib.suppress(Exception):
        from multi_agent.hooks import emit
        emit("on_build_complete", {"task_id": state.get("task_id", ""), "builder": builder_id, "summary": str(result.get("summary", ""))[:200]})
    graph_hooks.fire_exit("build", state, build_result)
    return build_result


# ── Node 3: Review ────────────────────────────────────────

@_graph_node("review")
def review_node(state: WorkflowState) -> dict[str, Any]:
    graph_hooks.fire_enter("review", state)

    # Total task duration guard (OWASP LLM10:2025 — DoW prevention)
    timeout_err = _check_total_timeout(state, "review")
    if timeout_err:
        return timeout_err

    reviewer_id = state.get("reviewer_id", "?")

    result = interrupt({
        "role": "reviewer",
        "agent": reviewer_id,
    })

    # Check for cancellation immediately after interrupt returns
    if _is_cancelled(state.get("task_id", "")):
        return {
            "final_status": "cancelled",
            "conversation": [make_event("orchestrator", action="cancelled")],
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
                "conversation": [make_event("orchestrator", action="timeout", elapsed=int(elapsed))],
            }

    # Basic validation
    if not isinstance(result, dict):
        return {
            "reviewer_output": {"decision": "reject", "feedback": "Invalid reviewer output"},
            "conversation": [make_event("reviewer", decision="reject")],
        }

    # Record optional token usage from IDE driver (FinOps)
    if isinstance(result.get("token_usage"), dict):
        graph_stats.record_token_usage("review", result["token_usage"])
        # Persist to FinOps log for cross-task aggregation
        with contextlib.suppress(Exception):
            from multi_agent.finops import record_task_usage
            tu = result["token_usage"]
            record_task_usage(
                task_id=state.get("task_id", ""),
                node="review",
                agent_id=state.get("reviewer_id", ""),
                input_tokens=int(tu.get("input_tokens", 0)),
                output_tokens=int(tu.get("output_tokens", 0)),
                total_tokens=int(tu.get("total_tokens", 0)),
                cost=float(tu.get("cost", 0.0)),
                model=str(tu.get("model", "")),
            )

    # Detect CLI driver error output
    if result.get("status") == "error":
        error_msg = result.get("summary", "unknown reviewer CLI error")
        return {
            "reviewer_output": {"decision": "reject", "feedback": f"Reviewer CLI failed: {error_msg}"},
            "conversation": [make_event("reviewer", decision="reject")],
        }

    try:
        parsed = ReviewerOutput(**result)
        decision = parsed.decision.value
    except Exception:
        decision = result.get("decision", "reject")

    # Enrich reviewer result: inject fallback feedback + rubber-stamp detection
    _enrich_reviewer_result(result, decision, state)

    # Auto-capture insights from review summary into semantic memory
    with contextlib.suppress(Exception):
        from multi_agent.semantic_memory import capture_from_review
        review_text = result.get("summary", "") or result.get("feedback", "")
        if review_text:
            capture_from_review(
                task_id=state.get("task_id", ""),
                review_summary=review_text,
                agent_id=state.get("reviewer_id", ""),
            )

    review_result = {
        "reviewer_output": result,
        "review_started_at": review_started,
        "conversation": [make_event("reviewer", decision=decision)],
    }
    # Plugin hook: review complete
    with contextlib.suppress(Exception):
        from multi_agent.hooks import emit
        emit("on_review_complete", {"task_id": state.get("task_id", ""), "reviewer": reviewer_id, "decision": decision})
    graph_hooks.fire_exit("review", state, review_result)
    return review_result


def _enrich_reviewer_result(
    result: dict[str, Any], decision: str, state: WorkflowState,
) -> None:
    """Inject fallback feedback for empty reject/request_changes + rubber-stamp detection (in-place)."""
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

    # Rubber-stamp detection (MAST NeurIPS 2025 TV-1)
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
        result["_rubber_stamp_warning"] = True


def _decide_approve(state: WorkflowState, rubber_stamp: bool) -> dict[str, Any]:
    """Handle approve decision in decide_node."""
    convo_entries: list[dict[str, Any]] = []
    if rubber_stamp:
        _log.warning(
            "Task %s approved with rubber-stamp warning — review may lack "
            "independent verification (MAST TV-1). Approving with audit note.",
            state.get("task_id", "?"),
        )
        convo_entries.append(make_event(
            "orchestrator", action="rubber_stamp_warning",
            details="Reviewer approved without substantive reasoning. "
                    "Approval accepted but flagged for audit.",
            reviewer_id=state.get("reviewer_id", "?"),
        ))
    convo_entries.append(make_event("orchestrator", action="approved"))
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
    return {"final_status": "approved", "conversation": convo_entries}


def _decide_request_changes(
    state: WorkflowState, reviewer_output: dict[str, Any],
    *, original_convo: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Handle request_changes decision in decide_node."""
    feedback = reviewer_output.get("feedback", "")
    retry_count = state.get("retry_count", 0)
    budget = state.get("retry_budget", 2)

    # Use state counter (resilient to conversation trimming) with fallback
    # to counting conversation entries for backwards compatibility.
    convo_for_count = original_convo if original_convo is not None else state.get("conversation", [])
    rc_count = state.get("request_changes_count", sum(
        1 for e in convo_for_count
        if e.get("action") == "request_changes"
    ))
    if rc_count >= MAX_REQUEST_CHANGES:
        _log.warning("request_changes cap reached (%d), escalating", rc_count)
        final_entry = make_event("orchestrator", action="escalated",
                                reason=f"request_changes cap ({rc_count})")
        full_convo = [*convo_for_count, final_entry]
        archive_conversation(state["task_id"], full_convo)
        return {"error": "REQUEST_CHANGES_CAP", "final_status": "escalated",
                "conversation": [final_entry]}

    # Use original (pre-trim) conversation for dashboard to avoid truncated display
    dashboard_convo = original_convo if original_convo is not None else state.get("conversation", [])
    write_dashboard(
        task_id=state["task_id"],
        done_criteria=state.get("done_criteria", []),
        current_agent=state.get("builder_id", ""),
        current_role="builder",
        conversation=dashboard_convo,
        status_msg=f"🔧 需修改 ({retry_count}/{budget})",
    )
    rc_total = state.get("request_changes_count", 0) + 1
    return {"request_changes_count": rc_total, "conversation": [
        make_event("orchestrator", action="request_changes", feedback=feedback)
    ]}


def _decide_reject_retry(
    state: WorkflowState, reviewer_output: dict[str, Any],
    *, original_convo: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Handle reject decision with retry budget in decide_node."""
    retry_count = state.get("retry_count", 0) + 1
    budget = state.get("retry_budget", 2)

    if retry_count > budget:
        last_feedback = reviewer_output.get("feedback", "")
        feedback_summary = (last_feedback[:200] + "…") if len(last_feedback) > 200 else last_feedback
        final_entry = make_event("orchestrator", action="escalated",
                                reason="budget exhausted",
                                last_feedback=feedback_summary)
        full_convo = [*state.get("conversation", []), final_entry]
        write_dashboard(
            task_id=state["task_id"],
            done_criteria=state.get("done_criteria", []),
            current_agent=state.get("reviewer_id", ""),
            current_role="escalated",
            conversation=full_convo,
            error=f"重试预算耗尽 ({retry_count - 1}/{budget})",
        )
        archive_conversation(state["task_id"], full_convo)
        return {
            "error": f"BUDGET_EXHAUSTED: {feedback_summary}" if feedback_summary else "BUDGET_EXHAUSTED",
            "retry_count": retry_count,
            "final_status": "escalated",
            "conversation": [final_entry],
        }

    # E2: Structure retry feedback
    raw_feedback = reviewer_output.get("feedback", "")
    decision_label = reviewer_output.get("decision", "reject")
    sections = [f"## Reviewer Decision: {decision_label.upper()}"]
    sections.append(f"### Feedback\n{raw_feedback}")
    prev_builder = state.get("builder_output")
    if isinstance(prev_builder, dict):
        gw = prev_builder.get("gate_warnings")
        if gw:
            sections.append("### Quality Gate Warnings\n" + "\n".join(f"- {w}" for w in gw))
    sections.append(f"### Retry Status\nAttempt {retry_count}/{budget}")
    feedback = "\n\n".join(sections)

    # DDI decay warning
    if retry_count >= 2:
        _log.warning(
            "DDI decay: retry %d/%d — effectiveness likely degraded 60-80%%. "
            "Consider fresh approach instead of incremental patching.",
            retry_count, budget,
        )
        feedback += (
            f"\n\n⚠️ 注意: 这是第 {retry_count} 次重试。研究表明调试效果在 2-3 次后衰减 60-80%。"
            " 建议: 考虑从头重新实现而非继续修补同一代码 (fresh start strategy)。"
        )

    # Use original (pre-trim) conversation for dashboard to avoid truncated display
    dashboard_convo = original_convo if original_convo is not None else state.get("conversation", [])
    write_dashboard(
        task_id=state["task_id"],
        done_criteria=state.get("done_criteria", []),
        current_agent=state.get("builder_id", ""),
        current_role="builder",
        conversation=dashboard_convo,
        status_msg=f"🔄 重试 ❌ 驳回 ({retry_count}/{budget})",
    )

    retry_entry: dict[str, Any] = make_event("orchestrator", action="retry", feedback=feedback)
    if isinstance(prev_builder, dict):
        changed = prev_builder.get("changed_files")
        if changed:
            retry_entry["prev_changed_files"] = changed
        gates = prev_builder.get("gate_warnings")
        if gates:
            retry_entry["prev_gate_warnings"] = gates

    return {"retry_count": retry_count, "conversation": [retry_entry]}


# ── Node 4: Decide ────────────────────────────────────────


def _contains_fallback_marker(payload: dict[str, Any] | None) -> bool:
    """Detect synthetic/fallback outputs that should not be auto-approved."""
    if not isinstance(payload, dict):
        return False
    if payload.get("_adapter_fallback") is True:
        return True
    with contextlib.suppress(Exception):
        raw = json.dumps(payload, ensure_ascii=False).lower()
        return any(marker in raw for marker in _FALLBACK_MARKERS)
    return False


def _is_gate_pass(value: Any) -> bool:
    return str(value).strip().lower() in _PASS_GATE_VALUES


def _reviewer_evidence_requirements(
    state: WorkflowState, *, strict_mode: bool,
) -> tuple[bool, int]:
    review_policy = state.get("review_policy")
    reviewer_cfg = review_policy.get("reviewer") if isinstance(review_policy, dict) else None
    if not isinstance(reviewer_cfg, dict):
        reviewer_cfg = {}
    require = bool(reviewer_cfg.get("require_evidence_on_approve", strict_mode))
    minimum = _positive_int(reviewer_cfg.get("min_evidence_items"), 1) if require else 0
    return require, minimum


def _approve_hard_gate_violations(
    state: WorkflowState, reviewer_output: dict[str, Any], *, strict_mode: bool,
) -> list[str]:
    """Validate approve decision against strict semantic gates."""
    if not strict_mode:
        return []

    violations: list[str] = []
    builder_output = state.get("builder_output")
    if not isinstance(builder_output, dict):
        # Direct unit invocations of decide_node may skip builder context.
        # Enforce hard gates only when builder_output is available.
        return []

    builder_status = str(builder_output.get("status", "")).lower().strip()
    if builder_status in {"error", "blocked", "failed"}:
        violations.append(f"builder status is {builder_status!r}")

    changed_files = builder_output.get("changed_files")
    if not isinstance(changed_files, list) or not any(str(p).strip() for p in changed_files):
        violations.append("builder changed_files is empty")

    check_results = builder_output.get("check_results")
    if not isinstance(check_results, dict):
        violations.append("builder check_results missing")
    else:
        required_gates = ["lint", "unit_test", "artifact_checksum"]
        with contextlib.suppress(Exception):
            contract = load_contract(state.get("skill_id", "code-implement"))
            required_gates = list(contract.quality_gates)  # type: ignore[assignment]

        for gate in required_gates:
            gate_value = check_results.get(gate)
            if gate_value is None:
                violations.append(f"missing quality gate: {gate}")
            elif not _is_gate_pass(gate_value):
                violations.append(f"quality gate {gate} failed: {gate_value}")

    if builder_output.get("_empty_changeset_warning"):
        violations.append("builder flagged empty changeset warning")

    gate_warnings = builder_output.get("gate_warnings")
    if isinstance(gate_warnings, list) and _count_nonempty_entries(gate_warnings) > 0:
        violations.append("builder has unresolved gate_warnings")

    if _contains_fallback_marker(builder_output):
        violations.append("builder output contains fallback marker")
    if _contains_fallback_marker(reviewer_output):
        violations.append("reviewer output contains fallback marker")

    require_evidence, min_evidence = _reviewer_evidence_requirements(state, strict_mode=strict_mode)
    if require_evidence:
        evidence_count = _count_nonempty_entries(reviewer_output.get("evidence"))
        evidence_count += _count_nonempty_entries(reviewer_output.get("evidence_files"))
        if evidence_count < min_evidence:
            violations.append(
                f"review evidence too weak: need >= {min_evidence}, got {evidence_count}"
            )

    return violations


def _block_approve_on_hard_gate(
    task_id: str, reviewer_output: dict[str, Any], violations: list[str],
) -> tuple[dict[str, Any], str]:
    _log.warning(
        "Strict mode hard-gate blocked approve for task %s: %s",
        task_id, "; ".join(violations),
    )
    reviewer_output = dict(reviewer_output)
    reviewer_output["decision"] = "request_changes"
    reviewer_output["feedback"] = (
        "审批被 strict 硬门禁拦截，请先修复以下问题后再提交 approve:\n"
        + "\n".join(f"- {item}" for item in violations)
    )
    reviewer_output["_hard_gate_blocked"] = True
    return reviewer_output, "request_changes"


def _block_rubber_stamp(
    task_id: str, reviewer_output: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Force request_changes when strict mode detects rubber-stamp approval."""
    _log.warning(
        "Strict mode blocks rubber-stamp approval for task %s; forcing request_changes.",
        task_id,
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
    return reviewer_output, "request_changes"


@_graph_node("decide")
def decide_node(state: WorkflowState) -> dict[str, Any]:
    graph_hooks.fire_enter("decide", state)

    # Early exit if state is already terminal
    fs = state.get("final_status")
    if fs and fs not in ("approved",):
        passthrough = {
            "final_status": fs,
            "conversation": [make_event("orchestrator", action="terminal_passthrough",
                                       original_status=fs)],
        }
        if state.get("error"):
            passthrough["error"] = state["error"]  # type: ignore[assignment]  # LangGraph partial state
        graph_hooks.fire_exit("decide", state, passthrough)
        return passthrough

    # Track retry effectiveness (DDI measurement)
    # NOTE: count from ORIGINAL conversation BEFORE trimming, to avoid
    # undercounting retry rounds when trim removes middle entries.
    original_convo = state.get("conversation", [])
    review_round = sum(
        1 for e in original_convo
        if e.get("action") in ("retry", "request_changes")
    )

    # Task 74: trim conversation if oversized
    trimmed = trim_conversation(original_convo)
    if len(trimmed) < len(original_convo):
        state = {**state, "conversation": trimmed}

    reviewer_output: dict[str, Any] = state.get("reviewer_output") or {}
    review_policy = state.get("review_policy")
    rubber_policy = review_policy.get("rubber_stamp") if isinstance(review_policy, dict) else None
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
        reviewer_output, decision = _block_rubber_stamp(
            state.get("task_id", "?"), reviewer_output,
        )
    if decision == "approve":
        hard_gate_violations = _approve_hard_gate_violations(
            state, reviewer_output, strict_mode=strict_mode,
        )
        if hard_gate_violations:
            reviewer_output, decision = _block_approve_on_hard_gate(
                state.get("task_id", "?"), reviewer_output, hard_gate_violations,
            )
    if review_round > 0:
        graph_stats.record_retry_outcome(review_round, decision)

    if decision == "approve":
        result = _decide_approve(state, rubber_stamp)
        # Plugin hook: task complete
        with contextlib.suppress(Exception):
            from multi_agent.hooks import emit
            task_started = state.get("task_started_at") or state.get("started_at")
            elapsed = round(time.time() - task_started, 1) if task_started else 0
            emit("on_task_complete", {"task_id": state.get("task_id", ""), "elapsed": round(elapsed, 1)})
    elif decision == "request_changes":
        result = _decide_request_changes(state, reviewer_output, original_convo=original_convo)
        # Plugin hook: retry
        with contextlib.suppress(Exception):
            from multi_agent.hooks import emit
            emit("on_retry", {"task_id": state.get("task_id", ""), "attempt": state.get("retry_count", 0) + 1})
    else:
        result = _decide_reject_retry(state, reviewer_output, original_convo=original_convo)
        fs = result.get("final_status")
        if fs == "failed" or fs == "escalated":
            # Plugin hook: task failed
            with contextlib.suppress(Exception):
                from multi_agent.hooks import emit
                emit("on_task_failed", {"task_id": state.get("task_id", ""), "error": str(result.get("error", ""))[:200]})
        else:
            # Plugin hook: retry
            with contextlib.suppress(Exception):
                from multi_agent.hooks import emit
                emit("on_retry", {"task_id": state.get("task_id", ""), "attempt": state.get("retry_count", 0) + 1})

    graph_hooks.fire_exit("decide", state, result)
    return result


# ── Cancel Detection ──────────────────────────────────

def _is_cancelled(task_id: str) -> bool:
    """Check if a task has been cancelled by reading its YAML status.

    Opens the file directly (no exists() guard) to eliminate the TOCTOU race
    where the file could be deleted between check and open.  A concurrent
    write (e.g. from ``my cancel``) could yield a partial/corrupt read; any
    parse failure is treated as "not cancelled" — the next poll cycle will
    re-check.
    """
    from multi_agent.config import tasks_dir
    path = tasks_dir() / f"{task_id}.yaml"
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("status") == "cancelled"
    except (FileNotFoundError, OSError, yaml.YAMLError):
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

def build_graph() -> StateGraph:  # type: ignore[type-arg]
    """Build the 4-node LangGraph workflow (uncompiled)."""
    g = StateGraph(WorkflowState)

    g.add_node("plan", plan_node)  # type: ignore[call-overload]  # LangGraph stub limitation
    g.add_node("build", build_node)  # type: ignore[call-overload]
    g.add_node("review", review_node)  # type: ignore[call-overload]
    g.add_node("decide", decide_node)  # type: ignore[call-overload]

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
_conn_pool: dict[str, sqlite3.Connection] = {}
_conn_lock = __import__("threading").RLock()
_atexit_registered = False


def _close_all_connections() -> None:
    """Close all pooled SQLite connections at interpreter exit."""
    with _conn_lock:
        for conn in _conn_pool.values():
            with contextlib.suppress(Exception):
                conn.close()
        _conn_pool.clear()


def _get_connection(path: str) -> sqlite3.Connection:
    """Get or create a SQLite connection for the given path (singleton per path).

    THREAD SAFETY NOTE: check_same_thread=False allows cross-thread reuse,
    but SQLite itself serializes writes. Concurrent invoke() calls sharing
    this connection are safe for LangGraph checkpoint reads/writes because
    SqliteSaver uses transactions internally. However, if future code adds
    raw SQL outside SqliteSaver, wrap it in _conn_lock or use a dedicated
    connection to avoid "database is locked" under heavy concurrency.
    """
    import atexit

    global _atexit_registered

    with _conn_lock:
        if not _atexit_registered:
            atexit.register(_close_all_connections)
            _atexit_registered = True
        if path in _conn_pool:
            conn = _conn_pool[path]
            # Verify connection is still usable
            try:
                conn.execute("SELECT 1")
                return conn
            except sqlite3.ProgrammingError:
                del _conn_pool[path]
        conn = sqlite3.connect(path, check_same_thread=False)
        # Performance pragmas — WAL mode enables concurrent readers,
        # synchronous=NORMAL balances durability vs speed for checkpoint data.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-8000")  # 8 MB page cache
        _conn_pool[path] = conn
        return conn


_compiled_cache: dict[str, object] = {}


def reset_graph() -> None:
    """Clear compiled graph cache, connection pool, and stats. Used for testing."""
    _compiled_cache.clear()
    graph_stats.reset()
    with _conn_lock:
        for conn in _conn_pool.values():
            with contextlib.suppress(Exception):
                conn.close()
        _conn_pool.clear()


def compile_graph(*, db_path: str | None = None) -> Any:
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
_plan_node_inner = plan_node.__wrapped__     # type: ignore[attr-defined]  # functools.wraps
_build_node_inner = build_node.__wrapped__   # type: ignore[attr-defined]  # functools.wraps
_review_node_inner = review_node.__wrapped__ # type: ignore[attr-defined]  # functools.wraps
_decide_node_inner = decide_node.__wrapped__ # type: ignore[attr-defined]  # functools.wraps
