"""IDE-first session service built on top of LangGraph state."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from multi_agent._utils import (
    DEFAULT_RUBBER_STAMP_PHRASES,
    SAFE_TASK_ID_RE,
    TERMINAL_FINAL_STATUSES,
    TERMINAL_STATES,
)
from multi_agent._utils import (
    count_nonempty_entries as _count_nonempty_entries,
)
from multi_agent._utils import (
    now_utc as _now_utc,
)
from multi_agent._utils import (
    positive_int as _positive_int,
)
from multi_agent._utils import (
    validate_agent_id as _validate_agent_id_core,
)
from multi_agent._utils import (
    validate_task_id as _validate_task_id_core,
)
from multi_agent.config import outbox_dir, root_dir, store_db_path, workspace_dir
from multi_agent.memory import add_pending_candidates, promote_pending_candidates
from multi_agent.router import get_defaults
from multi_agent.trace import append_trace_event, render_trace, trace_file
from multi_agent.workspace import (
    acquire_lock,
    clear_runtime,
    ensure_workspace,
    read_lock,
    release_lock,
    save_task_yaml,
    validate_outbox_data,
)

_SAFE_TASK_ID_RE = SAFE_TASK_ID_RE
DEFAULT_REVIEW_POLICY: dict[str, Any] = {
    "rubber_stamp": {
        "generic_phrases": sorted(DEFAULT_RUBBER_STAMP_PHRASES),
        "generic_summary_max_len": 50,
        "shallow_summary_max_len": 30,
        "block_on_strict": True,
    },
    "reviewer": {
        "require_evidence_on_approve": True,
        "min_evidence_items": 1,
    },
}


@dataclass
class SessionRoles:
    orchestrator: str
    builder: str
    reviewer: str

    def as_dict(self) -> dict[str, str]:
        return {
            "orchestrator": self.orchestrator,
            "builder": self.builder,
            "reviewer": self.reviewer,
        }


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"invalid JSON object: {path}")
    return data


def _validate_task_id(task_id: str) -> None:
    _validate_task_id_core(task_id)


def _validate_agent_id(agent_id: str) -> None:
    _validate_agent_id_core(agent_id)


def _clear_task_checkpoint(task_id: str) -> None:
    """Delete existing checkpoint rows for a task thread id."""
    db = store_db_path()
    if not db.exists():
        return
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM writes WHERE thread_id = ?", (task_id,))
        conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (task_id,))
        conn.commit()


def _clear_task_artifacts(task_id: str) -> None:
    """Remove per-task artifacts so a reset starts from a clean audit trail."""
    handoff_dir = root_dir() / "runtime" / "handoffs" / task_id
    shutil.rmtree(handoff_dir, ignore_errors=True)
    trace_path = trace_file(task_id)
    trace_path.unlink(missing_ok=True)


def _task_requirement(task: dict[str, Any]) -> str:
    input_payload = task.get("input_payload")
    if isinstance(input_payload, dict):
        requirement = input_payload.get("requirement")
        if isinstance(requirement, str) and requirement.strip():
            return requirement.strip()
        endpoint = input_payload.get("endpoint")
        framework = input_payload.get("framework")
        if endpoint and framework:
            return f"Implement {endpoint} with {framework}"
    done_criteria = task.get("done_criteria")
    if isinstance(done_criteria, list) and done_criteria:
        first = done_criteria[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    return f"Implement task {task.get('task_id', 'unknown')}"


def _find_project_root_from_path(path: Path) -> Path | None:
    cur = path if path.is_dir() else path.parent
    for candidate in [cur, *cur.parents]:
        if (candidate / "skills").is_dir() and (candidate / "agents").is_dir():
            return candidate.resolve()
    return None


def activate_project_root_for_task_file(task_file: str) -> Path | None:
    """Best-effort root activation from a task file location.

    This makes CLI/subprocess flows stable even when current working directory
    or inherited MA_ROOT is inconsistent.
    """
    task_path = Path(task_file).expanduser().resolve()
    root = _find_project_root_from_path(task_path)
    if root is None:
        return None
    os.environ["MA_ROOT"] = str(root)
    root_dir.cache_clear()
    return root


def _load_mode_cfg(mode: str, config_path: str | None) -> dict[str, Any]:
    if not config_path:
        return {}
    cfg_path = Path(config_path)
    if not cfg_path.is_absolute():
        cfg_path = root_dir() / cfg_path
    if not cfg_path.exists():
        return {}
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(cfg, dict):
        return {}
    modes = cfg.get("modes")
    if not isinstance(modes, dict):
        return {}
    mode_cfg = modes.get(mode)
    if not isinstance(mode_cfg, dict):
        return {}
    return mode_cfg


# _positive_int and _count_nonempty_entries imported from _utils


def _normalize_phrase_list(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    out: list[str] = []
    for item in value:
        text = str(item).strip().lower()
        if text:
            out.append(text)
    return out or list(default)


def _resolve_review_policy(mode: str, config_path: str | None) -> dict[str, Any]:
    mode_cfg = _load_mode_cfg(mode, config_path)
    policy_cfg = mode_cfg.get("review_policy")
    if not isinstance(policy_cfg, dict):
        policy_cfg = mode_cfg.get("policy")
    if not isinstance(policy_cfg, dict):
        policy_cfg = {}

    rubber_raw = policy_cfg.get("rubber_stamp")
    if not isinstance(rubber_raw, dict):
        rubber_raw = {}
    reviewer_raw = policy_cfg.get("reviewer")
    if not isinstance(reviewer_raw, dict):
        reviewer_raw = {}

    default_rubber = DEFAULT_REVIEW_POLICY["rubber_stamp"]
    default_reviewer = DEFAULT_REVIEW_POLICY["reviewer"]

    require_evidence_default = bool(default_reviewer["require_evidence_on_approve"]) if mode == "strict" else False
    require_evidence = bool(reviewer_raw.get("require_evidence_on_approve", require_evidence_default))
    min_evidence_default = int(default_reviewer["min_evidence_items"]) if require_evidence else 0
    min_evidence_items = _positive_int(reviewer_raw.get("min_evidence_items"), max(1, min_evidence_default or 1))
    if not require_evidence:
        min_evidence_items = 0

    return {
        "rubber_stamp": {
            "generic_phrases": _normalize_phrase_list(
                rubber_raw.get("generic_phrases"),
                list(default_rubber["generic_phrases"]),
            ),
            "generic_summary_max_len": _positive_int(
                rubber_raw.get("generic_summary_max_len"),
                int(default_rubber["generic_summary_max_len"]),
            ),
            "shallow_summary_max_len": _positive_int(
                rubber_raw.get("shallow_summary_max_len"),
                int(default_rubber["shallow_summary_max_len"]),
            ),
            "block_on_strict": bool(rubber_raw.get("block_on_strict", default_rubber["block_on_strict"])),
        },
        "reviewer": {
            "require_evidence_on_approve": require_evidence,
            "min_evidence_items": min_evidence_items,
        },
    }


def _resolve_roles(mode: str, config_path: str | None) -> SessionRoles:
    builder = ""
    reviewer = ""
    orchestrator = ""

    mode_cfg = _load_mode_cfg(mode, config_path)
    roles = mode_cfg.get("roles")
    if isinstance(roles, dict):
        builder = str(roles.get("builder", "")).strip()
        reviewer = str(roles.get("reviewer", "")).strip()
        orchestrator = str(roles.get("orchestrator", "")).strip()

    defaults = get_defaults()
    if not builder:
        builder = str(defaults.get("builder", "")).strip()
    if not reviewer:
        reviewer = str(defaults.get("reviewer", "")).strip()
    if not orchestrator:
        orchestrator = "codex"

    if not builder:
        builder = "builder"
    if not reviewer:
        reviewer = "reviewer"
    if not orchestrator:
        orchestrator = "orchestrator"

    return SessionRoles(orchestrator=orchestrator, builder=builder, reviewer=reviewer)


def _config(task_id: str) -> dict[str, Any]:
    from multi_agent.orchestrator import make_config
    return make_config(task_id)


def compile_graph() -> Any:
    """Compatibility wrapper for tests and call sites.

    Delegates to orchestrator.compile_graph(). Kept as a module attribute
    so tests can monkeypatch `multi_agent.session.compile_graph`.
    """
    from multi_agent.orchestrator import compile_graph as _orch_compile
    return _orch_compile()


def _compile_graph_app() -> Any:
    # Indirect call keeps compatibility with tests monkeypatching
    # multi_agent.session.compile_graph while still avoiding stale imports.
    return compile_graph()


def _waiting_info(snapshot: Any) -> tuple[str | None, str | None]:
    from multi_agent.orchestrator import get_waiting_info
    return get_waiting_info(snapshot)


def _state_from_snapshot(snapshot: Any) -> tuple[str, str | None, str | None]:
    # Use orchestrator's structured TaskStatus, then unpack to legacy tuple format
    if not snapshot:
        return "UNKNOWN", None, None
    # We need to reconstruct TaskStatus logic without re-querying the graph.
    # Since _state_from_snapshot receives a snapshot directly, replicate
    # the same logic via orchestrator helpers.
    vals = (snapshot.values if snapshot else {}) or {}
    final_status = str(vals.get("final_status", "")).lower().strip()
    if final_status and final_status in TERMINAL_FINAL_STATUSES:
        mapping = {
            "approved": "DONE",
            "done": "DONE",
            "failed": "FAILED",
            "escalated": "ESCALATED",
            "cancelled": "CANCELLED",
        }
        return mapping.get(final_status, final_status.upper()), None, None
    role, agent = _waiting_info(snapshot)
    if role == "builder":
        return "RUNNING", role, agent
    if role == "reviewer":
        return "VERIFYING", role, agent
    return "ASSIGNED", role, agent


def _task_md_path() -> Path:
    return workspace_dir() / "TASK.md"


def _prompt_path(agent: str) -> Path:
    _validate_agent_id(agent)
    return root_dir() / "prompts" / f"current-{agent}.txt"


def _role_for_agent(roles: SessionRoles, agent: str) -> str:
    if agent == roles.builder:
        return "builder"
    if agent == roles.reviewer:
        return "reviewer"
    if agent == roles.orchestrator:
        return "orchestrator"
    return "observer"


def _outbox_rel_path(role: str | None) -> str:
    if role not in {"builder", "reviewer"}:
        return ".multi-agent/outbox/builder.json"
    return f".multi-agent/outbox/{role}.json"


def _outbox_abs_path(role: str | None) -> str:
    return str((outbox_dir() / f"{role if role in {'builder', 'reviewer'} else 'builder'}.json").resolve())


def _ide_message_for_role(role: str | None) -> str:
    rel = _outbox_rel_path(role)
    return f"帮我完成 @.multi-agent/TASK.md 里的任务，完成后将 JSON 输出保存到 @{rel}"


def _result_template(role: str) -> dict[str, Any]:
    if role == "builder":
        return {
            "status": "completed|blocked",
            "summary": "实现摘要",
            "changed_files": ["/abs/path/file.py"],
            "check_results": {
                "lint": "pass|fail|not_run",
                "unit_test": "pass|fail|not_run",
                "contract_test": "pass|fail|not_run",
                "artifact_checksum": "pass|fail|not_run",
            },
            "risks": ["潜在风险"],
            "handoff_notes": "给 reviewer 的说明",
        }
    if role == "reviewer":
        return {
            "decision": "approve|reject|request_changes",
            "summary": "评审摘要",
            "feedback": "若 reject/request_changes，给出可执行修改建议",
            "issues": ["问题 1"],
            "evidence": ["验证证据"],
            "risks": ["风险 1"],
        }
    return {
        "decision": "merge|close|retry",
        "notes": "编排备注",
    }


def _recommended_event(role: str) -> str:
    if role == "builder":
        return "builder_done"
    if role == "reviewer":
        return "review_pass|review_fail"
    return "none"


def build_agent_prompt(
    task_id: str,
    agent: str,
    *,
    roles_override: SessionRoles | None = None,
) -> tuple[str, dict[str, Any]]:
    _validate_agent_id(agent)
    app = _compile_graph_app()
    snapshot = app.get_state(_config(task_id))
    state, owner_role, owner_agent = _state_from_snapshot(snapshot)
    vals = getattr(snapshot, "values", {}) if snapshot else {}
    roles = roles_override or SessionRoles(
        orchestrator=str(vals.get("orchestrator_id", "")) or "codex",
        builder=str(vals.get("builder_id", "")) or "builder",
        reviewer=str(vals.get("reviewer_id", "")) or "reviewer",
    )

    role = _role_for_agent(roles, agent)
    is_actionable = state not in TERMINAL_STATES and owner_agent == agent and owner_role in {"builder", "reviewer"}
    workflow_mode = str(vals.get("workflow_mode", "")).lower().strip() or "normal"
    review_policy = vals.get("review_policy")
    if not isinstance(review_policy, dict):
        review_policy = _resolve_review_policy(workflow_mode, None)

    task_md = _task_md_path().resolve()
    outbox_file = (outbox_dir() / f"{owner_role if owner_role else role}.json").resolve()

    if not is_actionable:
        standby = {
            "protocol_version": "1.0",
            "task_id": task_id,
            "lane_id": "main",
            "agent": agent,
            "role": role,
            "state_seen": state,
            "status": "standby",
            "reason": f"当前 owner 是 {owner_role or 'none'}/{owner_agent or 'none'}",
            "created_at": _now_utc(),
        }
        prompt = "\n".join(
            [
                "你当前处于待命状态。",
                f"- task_id: {task_id}",
                f"- your_agent_id: {agent}",
                f"- your_role: {role}",
                f"- current_owner_agent: {owner_agent or 'none'}",
                f"- current_owner_role: {owner_role or 'none'}",
                f"- session_state: {state}",
                "",
                "现在不要执行任何改动。",
                "请严格返回下面这个 JSON：",
                json.dumps(standby, ensure_ascii=False, indent=2),
            ]
        )
        return prompt, {
            "task_id": task_id,
            "state": state,
            "current_agent": owner_agent,
            "current_role": owner_role,
            "actionable": False,
            "agent_role": role,
        }

    envelope = {
        "protocol_version": "1.0",
        "task_id": task_id,
        "lane_id": "main",
        "agent": agent,
        "role": owner_role,
        "state_seen": state,
        "result": _result_template(owner_role or "builder"),
        "recommended_event": _recommended_event(owner_role or "builder"),
        "evidence_files": ["/abs/path/evidence.txt"],
        "memory_candidates": ["可写入长期记忆的稳定约定（可选）"],
        "created_at": _now_utc(),
    }
    reviewer_cfg = review_policy.get("reviewer") if isinstance(review_policy, dict) else {}
    if not isinstance(reviewer_cfg, dict):
        reviewer_cfg = {}
    require_evidence = bool(reviewer_cfg.get("require_evidence_on_approve", workflow_mode == "strict"))
    min_evidence = _positive_int(reviewer_cfg.get("min_evidence_items"), 1) if require_evidence else 0
    evidence_note = (
        f"- reviewer 在 decision=approve 时，必须提供至少 {min_evidence} 条证据（result.evidence 或 evidence_files）。"
        if require_evidence and owner_role == "reviewer"
        else ""
    )
    prompt = "\n".join(
        [
            "你是严格多 Agent 协作流程中的当前执行者。",
            f"- task_id: {task_id}",
            f"- your_agent_id: {agent}",
            f"- your_role: {owner_role}",
            f"- session_state: {state}",
            "",
            "操作要求（纯 IDE 模式）：",
            f"1) 读取任务说明文件：`{task_md}`",
            "2) 完成当前角色职责（builder/reviewer）",
            "3) 只输出一个 JSON envelope，并保存到 outbox 文件",
            f"4) outbox 绝对路径：`{outbox_file}`",
            "",
            "返回 JSON 格式（严格）：",
            json.dumps(envelope, ensure_ascii=False, indent=2),
            "",
            "注意：",
            "- 不要执行终端命令。",
            "- 不要省略 protocol_version/task_id/agent/role/result 字段。",
            "- reviewer 的 decision 必须是 approve/reject/request_changes。",
            evidence_note,
        ]
    )
    return prompt, {
        "task_id": task_id,
        "state": state,
        "current_agent": owner_agent,
        "current_role": owner_role,
        "actionable": True,
        "agent_role": role,
    }


def _parse_json_payload(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        raise ValueError("empty payload")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    fence_re = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", flags=re.IGNORECASE)
    for block in reversed(fence_re.findall(text)):
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    raise ValueError("failed to parse JSON object from payload")


def _normalize_reviewer_decision(
    result: dict[str, Any],
    env: dict[str, Any],
    workflow_mode: str,
    review_policy: dict[str, Any] | None,
) -> None:
    """Normalize reviewer decision aliases and validate evidence requirements."""
    decision = str(result.get("decision", "")).lower().strip()
    if decision == "pass":
        result["decision"] = "approve"
    elif decision == "fail":
        result["decision"] = "reject"
    decision = str(result.get("decision", "")).lower().strip()
    reviewer_cfg = (review_policy or {}).get("reviewer")
    if not isinstance(reviewer_cfg, dict):
        reviewer_cfg = {}
    require_evidence = bool(reviewer_cfg.get("require_evidence_on_approve", workflow_mode == "strict"))
    min_evidence = _positive_int(reviewer_cfg.get("min_evidence_items"), 1) if require_evidence else 0
    if decision == "approve" and require_evidence:
        evidence_items = _count_nonempty_entries(result.get("evidence"))
        evidence_items += _count_nonempty_entries(env.get("evidence_files"))
        if evidence_items < min_evidence:
            raise ValueError(
                "reviewer approve requires evidence: "
                f"need >= {min_evidence}, got {evidence_items}. "
                "Provide result.evidence and/or evidence_files."
            )


def _normalize_envelope(
    raw_obj: dict[str, Any],
    *,
    task_id: str,
    agent: str,
    current_role: str,
    current_state: str,
    workflow_mode: str,
    review_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if "protocol_version" in raw_obj and isinstance(raw_obj.get("result"), dict):
        env = dict(raw_obj)
    else:
        env = {
            "protocol_version": "1.0",
            "task_id": task_id,
            "lane_id": "main",
            "agent": agent,
            "role": current_role,
            "state_seen": current_state,
            "result": raw_obj,
            "recommended_event": raw_obj.get("recommended_event", ""),
            "evidence_files": raw_obj.get("evidence_files", []),
            "memory_candidates": raw_obj.get("memory_candidates", []),
            "created_at": _now_utc(),
        }

    env.setdefault("protocol_version", "1.0")
    env.setdefault("lane_id", "main")
    env.setdefault("task_id", task_id)
    env.setdefault("agent", agent)
    env.setdefault("role", current_role)
    env.setdefault("state_seen", current_state)
    env.setdefault("recommended_event", "")
    env.setdefault("evidence_files", [])
    env.setdefault("memory_candidates", [])
    env.setdefault("created_at", _now_utc())

    if env["task_id"] != task_id:
        raise ValueError(f"payload.task_id mismatch ({env['task_id']} != {task_id})")
    if env["agent"] != agent:
        raise ValueError(f"payload.agent mismatch ({env['agent']} != {agent})")
    if env["role"] != current_role:
        raise ValueError(f"payload.role mismatch ({env['role']} != {current_role})")
    seen = str(env.get("state_seen", "")).strip().upper()
    if seen and seen != current_state.upper():
        raise ValueError(f"payload.state_seen mismatch ({seen} != {current_state})")

    result = env.get("result")
    if not isinstance(result, dict):
        raise ValueError("payload.result must be an object")

    if current_role == "reviewer":
        _normalize_reviewer_decision(result, env, workflow_mode, review_policy)

    errors = validate_outbox_data(current_role, result)
    if errors:
        raise ValueError("; ".join(errors))

    # Backward compatibility: some IDE outputs place memory candidates under result.
    top_candidates = env.get("memory_candidates")
    nested_candidates = result.get("memory_candidates")
    if (
        (not isinstance(top_candidates, list) or not top_candidates)
        and isinstance(nested_candidates, list)
        and nested_candidates
    ):
        env["memory_candidates"] = nested_candidates

    env["result"] = result
    return env


def _update_task_yaml_status(task_id: str, status: str) -> None:
    """Merge-update task YAML status, preserving existing metadata.

    Previous implementation overwrote all fields, losing builder/reviewer/
    orchestrator/source_task_file/mode on terminal state transitions.
    """
    from multi_agent.config import tasks_dir
    path = tasks_dir() / f"{task_id}.yaml"
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}
    existing["task_id"] = task_id
    existing["status"] = status
    existing["updated_at"] = _now_utc()
    save_task_yaml(task_id, existing)


def _save_handoff(task_id: str, agent: str, envelope: dict[str, Any]) -> Path:
    handoff_dir = root_dir() / "runtime" / "handoffs" / task_id
    handoff_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    out = handoff_dir / f"{ts}-{agent}.json"
    i = 1
    while out.exists():
        out = handoff_dir / f"{ts}-{agent}-{i}.json"
        i += 1
    out.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _build_task_status(task_id: str, roles: SessionRoles | None = None) -> dict[str, Any]:
    app = _compile_graph_app()
    snapshot = app.get_state(_config(task_id))
    state, role, agent = _state_from_snapshot(snapshot)
    vals = getattr(snapshot, "values", {}) if snapshot else {}

    roles_obj = roles or SessionRoles(
        orchestrator=str(vals.get("orchestrator_id", "")) or "codex",
        builder=str(vals.get("builder_id", "")) or "builder",
        reviewer=str(vals.get("reviewer_id", "")) or "reviewer",
    )

    prompts = {}
    for a in {roles_obj.builder, roles_obj.reviewer, roles_obj.orchestrator}:
        prompts[a] = str(_prompt_path(a))

    active_role = role if role in {"builder", "reviewer"} else None

    return {
        "task_id": task_id,
        "state": state,
        "current_role": role,
        "current_agent": agent,
        "task_md_path": str(_task_md_path().resolve()),
        "active_outbox_path": _outbox_abs_path(active_role) if active_role else None,
        "active_outbox_rel_path": _outbox_rel_path(active_role) if active_role else None,
        "ide_message": _ide_message_for_role(active_role) if active_role else None,
        "roles": roles_obj.as_dict(),
        "final_status": (str(vals.get("final_status", "")).lower() or None),
        "retry_count": int(vals.get("retry_count", 0) or 0),
        "retry_budget": int(vals.get("retry_budget", 0) or 0),
        "mode": str(vals.get("workflow_mode", "")).lower() or None,
        "prompt_paths": prompts,
    }


def _write_all_prompts(task_id: str, roles: SessionRoles) -> dict[str, str]:
    output: dict[str, str] = {}
    for agent in {roles.builder, roles.reviewer, roles.orchestrator}:
        prompt, _ = build_agent_prompt(task_id, agent, roles_override=roles)
        out = _prompt_path(agent)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prompt + "\n", encoding="utf-8")
        output[agent] = str(out)
    return output


def _acquire_session_lock(
    task_id: str, existing: Any, existing_state: str, reset: bool,
) -> bool:
    """Validate lock ownership and acquire if needed. Returns True if acquired here."""
    locked = read_lock()
    if locked and locked != task_id:
        raise ValueError(f"another task is active: {locked}")
    if existing and existing.next and existing_state not in TERMINAL_STATES and not reset:
        raise ValueError(f"task '{task_id}' is already active")
    if not locked:
        acquire_lock(task_id)
        return True
    return False


def _build_initial_state(
    task: dict[str, Any], task_id: str, mode: str,
    review_policy: dict[str, Any] | None, roles: Any,
) -> dict[str, Any]:
    """Construct the initial LangGraph state dict for a new session."""
    return {
        "task_id": task_id,
        "requirement": _task_requirement(task),
        "skill_id": str(task.get("skill_id", "code-implement")),
        "done_criteria": task.get("done_criteria", []),
        "workflow_mode": mode,
        "review_policy": review_policy,
        "timeout_sec": int(task.get("timeout_sec", 1800)),
        "retry_budget": int(task.get("retry_budget", 2)),
        "retry_count": 0,
        "input_payload": task.get("input_payload", {}),
        "builder_explicit": roles.builder,
        "reviewer_explicit": roles.reviewer,
        "orchestrator_id": roles.orchestrator,
        "conversation": [],
    }


def _finalize_session_start(
    task_id: str, task_path: Path, roles: Any, mode: str,
) -> dict[str, Any]:
    """Build status, write prompts, persist YAML, trace, and check terminal."""
    status = _build_task_status(task_id, roles)
    prompts = _write_all_prompts(task_id, roles)
    status["prompt_paths"] = prompts
    status["active_prompt"] = prompts.get(status.get("current_agent") or "", "")
    status["mode"] = mode

    is_terminal = status["state"] in TERMINAL_STATES
    persisted_status = status["state"].lower() if is_terminal else "active"
    save_task_yaml(
        task_id,
        {
            "task_id": task_id,
            "status": persisted_status,
            "mode": "session",
            "builder": roles.builder,
            "reviewer": roles.reviewer,
            "orchestrator": roles.orchestrator,
            "source_task_file": str(task_path.resolve()),
            "final_status": status.get("final_status"),
            "updated_at": _now_utc(),
        },
    )

    append_trace_event(
        task_id=task_id,
        event_type="session_start",
        actor=roles.orchestrator,
        role="orchestrator",
        state=status["state"],
        details={
            "mode": mode,
            "roles": roles.as_dict(),
            "active_prompt": status["active_prompt"],
        },
        lane_id="main",
    )

    if is_terminal:
        if read_lock() == task_id:
            release_lock()
        raise RuntimeError(
            "session "
            f"'{task_id}' entered terminal state during start: {status['state']}"
            f" (final_status={status.get('final_status') or status['state'].lower()})"
        )
    return status


def start_session(
    task_file: str,
    *,
    mode: str = "strict",
    config_path: str | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    # Stabilize root resolution for task-scoped session commands.
    activate_project_root_for_task_file(task_file)
    ensure_workspace()

    task_path = Path(task_file)
    task = _load_json(task_path)
    task_id = str(task.get("task_id", "")).strip()
    if not task_id:
        raise ValueError("task.task_id missing")
    _validate_task_id(task_id)

    roles = _resolve_roles(mode, config_path)
    review_policy = _resolve_review_policy(mode, config_path)
    app = _compile_graph_app()
    cfg = _config(task_id)
    existing = app.get_state(cfg)
    existing_state, _, _ = _state_from_snapshot(existing)

    acquired_here = _acquire_session_lock(task_id, existing, existing_state, reset)

    try:
        if roles.builder == roles.reviewer:
            raise ValueError(
                "invalid role mapping: builder and reviewer must differ "
                f"(both are '{roles.builder}')"
            )

        if reset or (existing and (not existing.next or existing_state in TERMINAL_STATES)):
            _clear_task_checkpoint(task_id)
            _clear_task_artifacts(task_id)
            clear_runtime()

        initial_state = _build_initial_state(task, task_id, mode, review_policy, roles)

        with contextlib.suppress(GraphInterrupt):
            app.invoke(initial_state, cfg)

        status = _finalize_session_start(task_id, task_path, roles, mode)
        return status
    except Exception as exc:
        if (
            isinstance(exc, RuntimeError)
            and "entered terminal state during start" in str(exc)
        ):
            raise
        # Startup failed: avoid leaving a stale lock or active-looking marker.
        save_task_yaml(
            task_id,
            {
                "task_id": task_id,
                "status": "failed",
                "mode": "session",
                "builder": roles.builder,
                "reviewer": roles.reviewer,
                "orchestrator": roles.orchestrator,
                "source_task_file": str(task_path.resolve()),
                "updated_at": _now_utc(),
            },
        )
        if acquired_here and read_lock() == task_id:
            release_lock()
        raise


def session_status(task_id: str) -> dict[str, Any]:
    _validate_task_id(task_id)
    status = _build_task_status(task_id)
    return status


def session_pull(task_id: str, agent: str, *, out: str | None = None) -> dict[str, Any]:
    _validate_task_id(task_id)
    _validate_agent_id(agent)
    prompt, meta = build_agent_prompt(task_id, agent)
    out_path = Path(out) if out else _prompt_path(agent)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(prompt + "\n", encoding="utf-8")

    append_trace_event(
        task_id=task_id,
        event_type="prompt_pull",
        actor=agent,
        role=meta.get("agent_role", "observer"),
        state=meta.get("state", "UNKNOWN"),
        details={
            "actionable": meta.get("actionable", False),
            "prompt_path": str(out_path),
        },
        lane_id="main",
    )

    owner_role = str(meta.get("current_role") or "")
    agent_role = str(meta.get("agent_role") or "observer")
    effective_role = owner_role if owner_role in {"builder", "reviewer"} else (
        agent_role if agent_role in {"builder", "reviewer"} else "builder"
    )

    return {
        "task_id": task_id,
        "agent": agent,
        "agent_role": agent_role,
        "prompt_path": str(out_path),
        "actionable": bool(meta.get("actionable")),
        "state": meta.get("state"),
        "current_agent": meta.get("current_agent"),
        "current_role": meta.get("current_role"),
        "task_md_path": str(_task_md_path().resolve()),
        "outbox_path": _outbox_abs_path(effective_role),
        "outbox_rel_path": _outbox_rel_path(effective_role),
        "ide_message": _ide_message_for_role(effective_role),
        "result_template": _result_template(effective_role),
    }


def session_next_action(task_id: str, *, agent: str | None = None) -> dict[str, Any]:
    """Return the next actionable guidance for an agent in IDE-first workflow."""
    _validate_task_id(task_id)
    status = _build_task_status(task_id)

    roles_map = status.get("roles", {})
    roles = SessionRoles(
        orchestrator=str(roles_map.get("orchestrator", "codex")) or "codex",
        builder=str(roles_map.get("builder", "builder")) or "builder",
        reviewer=str(roles_map.get("reviewer", "reviewer")) or "reviewer",
    )

    current_agent = str(status.get("current_agent") or "")
    current_role = str(status.get("current_role") or "")
    state = str(status.get("state") or "UNKNOWN")
    final_status = status.get("final_status")

    selected_agent = str(agent or current_agent or roles.builder).strip()
    _validate_agent_id(selected_agent)
    agent_role = _role_for_agent(roles, selected_agent)

    actionable = (
        state not in TERMINAL_STATES
        and selected_agent == current_agent
        and current_role in {"builder", "reviewer"}
    )
    effective_role = (
        current_role
        if current_role in {"builder", "reviewer"}
        else (agent_role if agent_role in {"builder", "reviewer"} else "builder")
    )

    if actionable and effective_role == "builder":
        checklist = [
            "读取并执行 .multi-agent/TASK.md 中的 Builder 要求",
            "完成 done_criteria 并提交结构化 JSON（envelope）",
            "将结果保存到 .multi-agent/outbox/builder.json",
        ]
    elif actionable and effective_role == "reviewer":
        checklist = [
            "读取并执行 .multi-agent/TASK.md 中的 Reviewer 要求",
            "独立验证 builder 结果并给出 decision",
            "将结果保存到 .multi-agent/outbox/reviewer.json",
        ]
    elif state in TERMINAL_STATES:
        checklist = ["任务已到终态，无需继续执行。"]
    elif current_agent and current_role:
        checklist = [
            f"当前 owner 是 {current_agent}/{current_role}，本 agent 暂不可执行。",
            "等待 owner 提交后再拉取下一轮动作。",
        ]
    else:
        checklist = ["当前没有可执行 owner，请检查会话状态。"]

    if state in TERMINAL_STATES:
        reason = f"task is terminal ({state.lower()})"
    elif actionable:
        reason = "you are current owner"
    elif current_agent and current_role:
        reason = f"current owner is {current_agent}/{current_role}"
    else:
        reason = "no active owner"

    event_hint = _recommended_event(effective_role) if actionable else "none"

    if actionable:
        ide_message = _ide_message_for_role(effective_role)
    elif state in TERMINAL_STATES:
        ide_message = f"任务 {task_id} 已结束（{state.lower()}），无需继续提交。"
    else:
        ide_message = f"当前轮到 {current_agent or 'unknown'}/{current_role or 'unknown'}，你先待命。"

    return {
        "task_id": task_id,
        "agent": selected_agent,
        "role": current_role or None,
        "state": state,
        "actionable": actionable,
        "checklist": checklist,
        "event_hint": event_hint,
        "reason": reason,
        "next_owner_agent": current_agent or None,
        "current_agent": current_agent or None,
        "current_role": current_role or None,
        "agent_role": agent_role,
        "task_md_path": str(_task_md_path().resolve()),
        "outbox_path": _outbox_abs_path(effective_role),
        "outbox_rel_path": _outbox_rel_path(effective_role),
        "ide_message": ide_message,
        "final_status": final_status,
    }


def _submit_memory_candidates(
    task_id: str, agent: str, envelope: dict[str, Any], result: dict[str, Any],
) -> None:
    """Extract and submit memory candidates from envelope/result."""
    candidates = envelope.get("memory_candidates")
    if not isinstance(candidates, list) or not candidates:
        nested = result.get("memory_candidates")
        if isinstance(nested, list):
            candidates = nested
    if isinstance(candidates, list) and candidates:
        add_pending_candidates(task_id, candidates, actor=agent)


def _post_push_hooks(
    task_id: str, current_role: str, result: dict[str, Any], after: dict[str, Any],
) -> None:
    """Handle memory promotion, terminal cleanup, and state trace after push."""
    decision = str(result.get("decision", "")).lower().strip() if current_role == "reviewer" else ""
    if current_role == "reviewer" and decision == "approve":
        final_status = str(after.get("final_status", "")).lower().strip()
        if final_status == "approved":
            promoted = promote_pending_candidates(task_id, actor="orchestrator")
            append_trace_event(
                task_id=task_id,
                event_type="memory_promote",
                actor="orchestrator",
                role="orchestrator",
                state=after["state"],
                details=promoted,
                lane_id="main",
            )
        else:
            append_trace_event(
                task_id=task_id,
                event_type="memory_skip",
                actor="orchestrator",
                role="orchestrator",
                state=after["state"],
                details={
                    "reason": "final_status_not_approved",
                    "final_status": final_status or None,
                },
                lane_id="main",
            )

    if after["state"] in TERMINAL_STATES:
        _update_task_yaml_status(task_id, after["state"].lower())
        if read_lock() == task_id:
            release_lock()

    append_trace_event(
        task_id=task_id,
        event_type="state_update",
        actor="orchestrator",
        role="orchestrator",
        state=after["state"],
        details={
            "current_agent": after["current_agent"],
            "current_role": after["current_role"],
            "final_status": after.get("final_status"),
        },
        lane_id="main",
    )


def session_push(task_id: str, agent: str, file_path: str) -> dict[str, Any]:
    _validate_task_id(task_id)
    _validate_agent_id(agent)
    app = _compile_graph_app()
    cfg = _config(task_id)
    snapshot = app.get_state(cfg)
    state, current_role, current_agent = _state_from_snapshot(snapshot)
    vals = getattr(snapshot, "values", {}) if snapshot else {}
    workflow_mode = str(vals.get("workflow_mode", "")).lower().strip() or "normal"
    review_policy = vals.get("review_policy")
    if not isinstance(review_policy, dict):
        review_policy = {}
    if state in TERMINAL_STATES:
        raise ValueError(f"task is already terminal: {state}")
    if current_agent != agent:
        raise ValueError(f"current owner is '{current_agent}', not '{agent}'")
    if current_role not in {"builder", "reviewer"}:
        raise ValueError(f"unsupported current role for push: {current_role}")

    # Cap file size to prevent OOM from malicious/accidental huge files
    _max_push_file = 10 * 1024 * 1024  # 10 MB
    fp = Path(file_path)
    try:
        fsize = fp.stat().st_size
    except OSError as e:
        raise ValueError(f"Cannot read file: {e}") from e
    if fsize > _max_push_file:
        raise ValueError(
            f"Push file too large: {fsize} bytes > {_max_push_file} limit"
        )
    raw = fp.read_text(encoding="utf-8")
    raw_obj = _parse_json_payload(raw)
    envelope = _normalize_envelope(
        raw_obj,
        task_id=task_id,
        agent=agent,
        current_role=current_role,
        current_state=state,
        workflow_mode=workflow_mode,
        review_policy=review_policy,
    )
    handoff = _save_handoff(task_id, agent, envelope)

    append_trace_event(
        task_id=task_id,
        event_type="handoff_submit",
        actor=agent,
        role=current_role,
        state=state,
        details={
            "handoff_file": str(handoff),
            "recommended_event": envelope.get("recommended_event", ""),
        },
        lane_id=str(envelope.get("lane_id", "main")),
    )

    result = dict(envelope["result"])
    _submit_memory_candidates(task_id, agent, envelope, result)

    with contextlib.suppress(GraphInterrupt):
        app.invoke(Command(resume=result), cfg)

    after = _build_task_status(task_id)
    _write_all_prompts(
        task_id,
        SessionRoles(
            orchestrator=after["roles"]["orchestrator"],
            builder=after["roles"]["builder"],
            reviewer=after["roles"]["reviewer"],
        ),
    )

    _post_push_hooks(task_id, current_role, result, after)

    return {
        "task_id": task_id,
        "agent": agent,
        "role": current_role,
        "handoff_file": str(handoff),
        "state": after["state"],
        "current_agent": after["current_agent"],
        "current_role": after["current_role"],
        "final_status": after.get("final_status"),
        "active_prompt": after["prompt_paths"].get(after["current_agent"] or "", ""),
    }


def session_trace(task_id: str, fmt: str) -> str:
    _validate_task_id(task_id)
    return render_trace(task_id, fmt)


def normalize_file_path_for_lock(path: str, *, cwd: str | None = None) -> str:
    """Expose canonical path normalizer for lockctl and tests."""
    base = Path(cwd) if cwd else Path.cwd()
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = base / p
    real = os.path.realpath(os.path.abspath(str(p)))  # noqa: PTH100 — normcase has no pathlib equiv
    return os.path.normcase(real)
