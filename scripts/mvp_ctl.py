#!/usr/bin/env python3
"""Strict MVP control script for task validation, routing, and state transitions."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal environments
    yaml = None

STATE_TRANSITIONS = {
    "DRAFT": {"QUEUED", "CANCELLED"},
    "QUEUED": {"ASSIGNED", "CANCELLED"},
    "ASSIGNED": {"RUNNING", "FAILED", "CANCELLED"},
    "RUNNING": {"VERIFYING", "FAILED", "RETRY", "ESCALATED", "CANCELLED"},
    "VERIFYING": {"APPROVED", "FAILED", "RETRY", "ESCALATED", "CANCELLED"},
    "APPROVED": {"MERGED", "FAILED"},
    "MERGED": {"DONE"},
    "FAILED": {"RETRY", "ESCALATED", "CANCELLED"},
    "RETRY": {"QUEUED", "ASSIGNED"},
    "ESCALATED": {"ASSIGNED", "CANCELLED"},
    "DONE": set(),
    "CANCELLED": set(),
}

TASK_REQUIRED = {
    "task_id",
    "trace_id",
    "skill_id",
    "skill_version",
    "producer",
    "consumer",
    "idempotency_key",
    "input_digest",
    "artifact_uri",
    "expected_checks",
    "timeout_sec",
    "retry_budget",
    "priority",
    "required_capabilities",
    "state",
    "created_at",
    "updated_at",
}

TASK_ALLOWED = TASK_REQUIRED | {
    "deps",
    "done_criteria",
    "owner",
    "input_payload",
    "metadata",
    "error",
}

CHECKS = {
    "lint",
    "unit_test",
    "integration_test",
    "contract_test",
    "security_scan",
    "artifact_checksum",
}

CAPABILITIES = {
    "planning",
    "implementation",
    "testing",
    "review",
    "security",
    "docs",
}

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
TRACE_RE = re.compile(r"^[a-f0-9-]{16,64}$")
DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


def now_utc() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: pathlib.Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=2, sort_keys=True)
        f.write("\n")


def parse_time(value: str) -> bool:
    try:
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate_task(task: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    missing = TASK_REQUIRED - task.keys()
    extra = task.keys() - TASK_ALLOWED
    for field in sorted(missing):
        errors.append(f"missing required field: {field}")
    for field in sorted(extra):
        errors.append(f"unknown field: {field}")

    if missing:
        return errors

    if not isinstance(task["task_id"], str) or not ID_RE.match(task["task_id"]):
        errors.append("task_id must match ^[a-z0-9][a-z0-9-]{2,63}$")
    if not isinstance(task["trace_id"], str) or not TRACE_RE.match(task["trace_id"]):
        errors.append("trace_id must be hex/hyphen string with length 16..64")
    if not isinstance(task["skill_id"], str) or not ID_RE.match(task["skill_id"]):
        errors.append("skill_id must match ^[a-z0-9][a-z0-9-]{2,63}$")
    if not isinstance(task["skill_version"], str) or not SEMVER_RE.match(task["skill_version"]):
        errors.append("skill_version must be semver")
    for field in ("producer", "consumer"):
        if not isinstance(task[field], str) or len(task[field].strip()) < 2:
            errors.append(f"{field} must be a non-empty string")
    if not isinstance(task["idempotency_key"], str) or len(task["idempotency_key"]) < 8:
        errors.append("idempotency_key must be a string with length >= 8")
    if not isinstance(task["input_digest"], str) or not DIGEST_RE.match(task["input_digest"]):
        errors.append("input_digest must match sha256:<64 hex chars>")
    if not isinstance(task["artifact_uri"], str) or not task["artifact_uri"].strip():
        errors.append("artifact_uri must be a non-empty string")

    expected_checks = task["expected_checks"]
    if not isinstance(expected_checks, list) or not expected_checks:
        errors.append("expected_checks must be a non-empty list")
    else:
        unknown_checks = sorted(set(expected_checks) - CHECKS)
        if unknown_checks:
            errors.append(f"expected_checks contains unsupported checks: {unknown_checks}")
        if len(set(expected_checks)) != len(expected_checks):
            errors.append("expected_checks must not contain duplicates")

    if not isinstance(task["timeout_sec"], int) or task["timeout_sec"] < 30:
        errors.append("timeout_sec must be an integer >= 30")
    if not isinstance(task["retry_budget"], int) or task["retry_budget"] < 0:
        errors.append("retry_budget must be an integer >= 0")

    if task["priority"] not in {"low", "normal", "high", "urgent"}:
        errors.append("priority must be one of: low, normal, high, urgent")

    required_caps = task["required_capabilities"]
    if not isinstance(required_caps, list) or not required_caps:
        errors.append("required_capabilities must be a non-empty list")
    else:
        unknown_caps = sorted(set(required_caps) - CAPABILITIES)
        if unknown_caps:
            errors.append(f"required_capabilities contains unsupported capabilities: {unknown_caps}")
        if len(set(required_caps)) != len(required_caps):
            errors.append("required_capabilities must not contain duplicates")

    if task["state"] not in STATE_TRANSITIONS:
        errors.append(f"state must be one of: {sorted(STATE_TRANSITIONS)}")

    for field in ("created_at", "updated_at"):
        if not isinstance(task[field], str) or not parse_time(task[field]):
            errors.append(f"{field} must be an ISO-8601 datetime")

    if "deps" in task and (not isinstance(task["deps"], list) or any(not isinstance(x, str) for x in task["deps"])):
        errors.append("deps must be a list of strings")
    if "done_criteria" in task and (
        not isinstance(task["done_criteria"], list) or any(not isinstance(x, str) for x in task["done_criteria"])
    ):
        errors.append("done_criteria must be a list of strings")

    if "error" in task:
        error = task["error"]
        if not isinstance(error, dict) or set(error.keys()) != {"code", "message"}:
            errors.append("error must contain exactly code and message")

    return errors


def parse_frontmatter(skill_md: pathlib.Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, ["SKILL.md must start with YAML frontmatter"]

    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, ["SKILL.md frontmatter is not properly terminated"]

    if yaml is None:
        return {}, ["PyYAML is required to parse SKILL.md frontmatter"]

    data = yaml.safe_load(parts[1])
    if not isinstance(data, dict):
        return {}, ["SKILL.md frontmatter must parse into a map"]

    if set(data.keys()) != {"name", "description"}:
        errors.append("SKILL.md frontmatter must contain exactly: name, description")
    if "name" in data and (not isinstance(data["name"], str) or not ID_RE.match(data["name"])):
        errors.append("SKILL.md frontmatter name must match ^[a-z0-9][a-z0-9-]{2,63}$")
    if "description" in data and (not isinstance(data["description"], str) or len(data["description"].strip()) < 20):
        errors.append("SKILL.md frontmatter description must be a meaningful string")

    return data, errors


def validate_skill_contract(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "id",
        "version",
        "description",
        "triggers",
        "inputs",
        "outputs",
        "preconditions",
        "postconditions",
        "quality_gates",
        "timeouts",
        "retry",
        "fallback",
        "compatibility",
        "handoff",
    }

    missing = required - contract.keys()
    extra = contract.keys() - required
    for field in sorted(missing):
        errors.append(f"contract missing required field: {field}")
    for field in sorted(extra):
        errors.append(f"contract unknown field: {field}")

    if missing:
        return errors

    if not isinstance(contract["id"], str) or not ID_RE.match(contract["id"]):
        errors.append("contract.id must match ^[a-z0-9][a-z0-9-]{2,63}$")
    if not isinstance(contract["version"], str) or not SEMVER_RE.match(contract["version"]):
        errors.append("contract.version must be semver")
    if not isinstance(contract["description"], str) or len(contract["description"].strip()) < 10:
        errors.append("contract.description must be a non-empty string")

    for key in ("triggers", "preconditions", "postconditions"):
        if not isinstance(contract[key], list) or any(not isinstance(x, str) for x in contract[key]):
            errors.append(f"contract.{key} must be a list of strings")

    for key in ("inputs", "outputs"):
        value = contract[key]
        if not isinstance(value, list) or not value:
            errors.append(f"contract.{key} must be a non-empty list")
            continue
        for item in value:
            if not isinstance(item, dict) or "name" not in item or "schema" not in item:
                errors.append(f"contract.{key} items must include name and schema")

    quality_gates = contract["quality_gates"]
    if not isinstance(quality_gates, list) or not quality_gates:
        errors.append("contract.quality_gates must be a non-empty list")
    else:
        unknown_checks = sorted(set(quality_gates) - CHECKS)
        if unknown_checks:
            errors.append(f"contract.quality_gates contains unsupported checks: {unknown_checks}")

    timeouts = contract["timeouts"]
    if not isinstance(timeouts, dict) or {"run_sec", "verify_sec"} - timeouts.keys():
        errors.append("contract.timeouts must contain run_sec and verify_sec")

    retry = contract["retry"]
    if not isinstance(retry, dict) or {"max_attempts", "backoff"} - retry.keys():
        errors.append("contract.retry must contain max_attempts and backoff")

    fallback = contract["fallback"]
    if not isinstance(fallback, dict) or fallback.get("on_failure") not in {"escalate_to_reviewer", "retry", "abort"}:
        errors.append("contract.fallback.on_failure must be one of escalate_to_reviewer/retry/abort")

    compatibility = contract["compatibility"]
    if not isinstance(compatibility, dict) or "min_orchestrator_version" not in compatibility:
        errors.append("contract.compatibility.min_orchestrator_version is required")

    handoff = contract["handoff"]
    if (
        not isinstance(handoff, dict)
        or "artifact_path" not in handoff
        or "required_fields" not in handoff
        or not isinstance(handoff["required_fields"], list)
    ):
        errors.append("contract.handoff must contain artifact_path and required_fields[]")

    return errors


def validate_skill_dir(skill_dir: pathlib.Path) -> list[str]:
    errors: list[str] = []
    required_paths = [
        skill_dir / "SKILL.md",
        skill_dir / "contract.yaml",
        skill_dir / "tests" / "acceptance.yaml",
        skill_dir / "agents" / "openai.yaml",
        skill_dir / "CHANGELOG.md",
    ]
    for path in required_paths:
        if not path.exists():
            errors.append(f"missing required file: {path}")

    if errors:
        return errors

    frontmatter, fm_errors = parse_frontmatter(skill_dir / "SKILL.md")
    errors.extend(fm_errors)

    if yaml is None:
        errors.append("PyYAML is required to parse contract.yaml")
        return errors

    contract = yaml.safe_load((skill_dir / "contract.yaml").read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        errors.append("contract.yaml must parse into a map")
        return errors

    errors.extend(validate_skill_contract(contract))

    if isinstance(frontmatter, dict) and frontmatter.get("name") and contract.get("id"):
        if frontmatter["name"] != contract["id"]:
            errors.append("SKILL.md frontmatter name must equal contract.id")

    return errors


def route_task(task: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
    req = set(task["required_capabilities"])
    candidates: list[dict[str, Any]] = []

    for agent in agents.get("agents", []):
        caps = set(agent.get("capabilities", []))
        missing = sorted(req - caps)
        if missing:
            continue

        reliability = float(agent.get("reliability", 0))
        queue_health = float(agent.get("queue_health", 0))
        cost = float(agent.get("cost", 1))
        score = 40.0 + reliability * 30.0 + queue_health * 20.0 + (1.0 - cost) * 10.0
        candidates.append(
            {
                "id": agent["id"],
                "score": round(score, 3),
                "reliability": reliability,
                "queue_health": queue_health,
                "cost": cost,
            }
        )

    candidates.sort(key=lambda x: x["score"], reverse=True)
    if not candidates:
        raise ValueError("no eligible agent found for required_capabilities")

    return {
        "selected": candidates[0],
        "candidates": candidates,
    }


def append_audit(event: dict[str, Any], audit_log: pathlib.Path) -> None:
    audit_log.parent.mkdir(parents=True, exist_ok=True)
    with audit_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=True) + "\n")


def apply_transition(
    task_path: pathlib.Path,
    to_state: str,
    actor: str,
    reason: str,
    error_code: str | None,
    error_message: str | None,
    audit_log: pathlib.Path,
) -> dict[str, Any]:
    task = load_json(task_path)
    errors = validate_task(task)
    if errors:
        raise ValueError("task is invalid: " + "; ".join(errors))

    from_state = task["state"]
    allowed = STATE_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise ValueError(f"invalid transition: {from_state} -> {to_state}; allowed: {sorted(allowed)}")

    task["state"] = to_state
    task["updated_at"] = now_utc()

    if to_state == "FAILED":
        task["error"] = {
            "code": error_code or "UNKNOWN",
            "message": error_message or "task failed",
        }
    elif "error" in task:
        del task["error"]

    save_json(task_path, task)
    append_audit(
        {
            "event": "state_transition",
            "task_id": task["task_id"],
            "trace_id": task["trace_id"],
            "actor": actor,
            "from": from_state,
            "to": to_state,
            "reason": reason,
            "at": task["updated_at"],
        },
        audit_log,
    )
    return task


def check_pass(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"pass", "passed", "ok", "true"}
    if isinstance(value, dict):
        if "status" in value:
            return str(value["status"]).lower() in {"pass", "passed", "ok", "true"}
        if "result" in value:
            return str(value["result"]).lower() in {"pass", "passed", "ok", "true"}
        if "pass" in value:
            return bool(value["pass"])
    return False


def verify_checks(task: dict[str, Any], results: dict[str, Any]) -> list[str]:
    expected = set(task["expected_checks"])
    actual_keys = set(results.keys())

    errors: list[str] = []
    missing = sorted(expected - actual_keys)
    if missing:
        errors.append(f"missing check results: {missing}")

    for check in sorted(expected & actual_keys):
        if not check_pass(results[check]):
            errors.append(f"check failed: {check}")

    return errors


def command_validate_task(args: argparse.Namespace) -> int:
    task = load_json(pathlib.Path(args.task))
    errors = validate_task(task)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print("task validation passed")
    return 0


def command_validate_skill(args: argparse.Namespace) -> int:
    errors = validate_skill_dir(pathlib.Path(args.skill_dir))
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print("skill validation passed")
    return 0


def command_route(args: argparse.Namespace) -> int:
    task = load_json(pathlib.Path(args.task))
    task_errors = validate_task(task)
    if task_errors:
        for err in task_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    result = route_task(task, load_json(pathlib.Path(args.agents)))
    print(json.dumps(result, ensure_ascii=True, indent=2))

    if args.write_consumer:
        task["consumer"] = result["selected"]["id"]
        task["updated_at"] = now_utc()
        save_json(pathlib.Path(args.task), task)

    return 0


def command_transition(args: argparse.Namespace) -> int:
    try:
        task = apply_transition(
            task_path=pathlib.Path(args.task),
            to_state=args.to_state,
            actor=args.actor,
            reason=args.reason,
            error_code=args.error_code,
            error_message=args.error_message,
            audit_log=pathlib.Path(args.audit_log),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"transition applied: {task['task_id']} -> {task['state']}")
    return 0


def command_verify_checks(args: argparse.Namespace) -> int:
    task = load_json(pathlib.Path(args.task))
    task_errors = validate_task(task)
    if task_errors:
        for err in task_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    results = load_json(pathlib.Path(args.results))
    errors = verify_checks(task, results)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("all expected checks passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strict Multi-Agent MVP control utility")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate-task", help="Validate a task JSON file")
    p.add_argument("--task", required=True, help="Path to task JSON")
    p.set_defaults(func=command_validate_task)

    p = sub.add_parser("validate-skill", help="Validate a skill directory")
    p.add_argument("--skill-dir", required=True, help="Path to skill directory")
    p.set_defaults(func=command_validate_skill)

    p = sub.add_parser("route", help="Route task to an eligible agent")
    p.add_argument("--task", required=True, help="Path to task JSON")
    p.add_argument("--agents", default="agents/profiles.json", help="Path to agent profile JSON")
    p.add_argument("--write-consumer", action="store_true", help="Persist selected consumer into task file")
    p.set_defaults(func=command_route)

    p = sub.add_parser("transition", help="Apply strict state transition")
    p.add_argument("--task", required=True, help="Path to task JSON")
    p.add_argument("--to-state", required=True, help="Target state")
    p.add_argument("--actor", default="orchestrator", help="Actor id")
    p.add_argument("--reason", default="manual", help="Transition reason")
    p.add_argument("--error-code", help="Failure code for FAILED state")
    p.add_argument("--error-message", help="Failure message for FAILED state")
    p.add_argument("--audit-log", default="runtime/audit.log.ndjson", help="Path to audit log")
    p.set_defaults(func=command_transition)

    p = sub.add_parser("verify-checks", help="Verify expected quality checks")
    p.add_argument("--task", required=True, help="Path to task JSON")
    p.add_argument("--results", required=True, help="Path to results JSON")
    p.set_defaults(func=command_verify_checks)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
