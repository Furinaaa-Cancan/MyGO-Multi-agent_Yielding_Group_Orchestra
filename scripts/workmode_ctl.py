#!/usr/bin/env python3
"""Workmode config validator + compatibility wrapper (deprecated orchestrator)."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

import yaml

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

DEFAULT_CONFIG = str(ROOT_DIR / "config" / "workmode.yaml")


def load_yaml(path: pathlib.Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"invalid yaml object: {path}")
    return data


def validate_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if cfg.get("version") != 1:
        errors.append("config.version must be 1")

    modes = cfg.get("modes")
    if not isinstance(modes, dict) or not modes:
        errors.append("config.modes must be a non-empty map")
    else:
        for mode_name, mode_cfg in modes.items():
            if not isinstance(mode_cfg, dict):
                errors.append(f"mode '{mode_name}' must be a map")
                continue
            roles = mode_cfg.get("roles")
            if not isinstance(roles, dict):
                errors.append(f"mode '{mode_name}' must contain roles map")
                continue
            for role in ("orchestrator", "builder", "reviewer"):
                if role not in roles or not isinstance(roles[role], str) or len(roles[role].strip()) < 2:
                    errors.append(f"mode '{mode_name}' missing roles.{role}")

    return errors


def command_validate_config(args: argparse.Namespace) -> int:
    cfg = load_yaml(pathlib.Path(args.config))
    errors = validate_config(cfg)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print("workmode config validation passed")
    return 0


def _load_task_id(task_path: pathlib.Path) -> str:
    with task_path.open("r", encoding="utf-8") as f:
        task = json.load(f)
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task.task_id missing")
    return task_id


def command_init_session(args: argparse.Namespace) -> int:
    from multi_agent.session import start_session

    payload = start_session(
        task_file=args.task,
        mode=args.mode,
        config_path=args.config,
        reset=False,
    )
    payload["status"] = "session_initialized"
    payload["deprecated_note"] = "workmode_ctl is compatibility-only; use `ma session start`."
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_next_action(args: argparse.Namespace) -> int:
    from multi_agent.session import session_status

    task_id = _load_task_id(pathlib.Path(args.task))
    status = session_status(task_id)
    owner = status.get("current_agent")
    role = status.get("current_role")
    state = status.get("state")
    actionable = owner == args.agent and state not in {"DONE", "FAILED", "ESCALATED", "CANCELLED"}
    payload = {
        "task_id": task_id,
        "agent": args.agent,
        "role": role,
        "state": state,
        "actionable": actionable,
        "reason": "compat mode from session status",
        "next_owner_agent": owner,
        "deprecated_note": "Use `ma session status/pull` instead.",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_auto_progress(args: argparse.Namespace) -> int:
    print(
        "ERROR: auto-progress is deprecated in LangGraph-SSOT mode. "
        "Use `ma session push --task-id ... --agent ... --file ...`.",
        file=sys.stderr,
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Workmode compatibility wrapper")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate-config", help="Validate workmode config")
    p.add_argument("--config", default=DEFAULT_CONFIG, help="Path to workmode config YAML")
    p.set_defaults(func=command_validate_config)

    p = sub.add_parser("init-session", help="Deprecated wrapper to ma session start")
    p.add_argument("--task", required=True, help="Path to task JSON")
    p.add_argument("--config", default=DEFAULT_CONFIG, help="Path to workmode config YAML")
    p.add_argument("--mode", default="strict", help="Mode profile from config")
    p.add_argument("--session-dir", default="runtime/sessions", help="Deprecated compatibility argument")
    p.set_defaults(func=command_init_session)

    p = sub.add_parser("next-action", help="Deprecated wrapper to ma session status/pull")
    p.add_argument("--task", required=True, help="Path to task JSON")
    p.add_argument("--agent", required=True, help="Agent ID")
    p.add_argument("--session-dir", default="runtime/sessions", help="Deprecated compatibility argument")
    p.set_defaults(func=command_next_action)

    p = sub.add_parser("auto-progress", help="Deprecated; use ma session push")
    p.add_argument("--task", required=True, help="Path to task JSON")
    p.add_argument("--event", required=True, help="Deprecated compatibility argument")
    p.add_argument("--actor", required=True, help="Deprecated compatibility argument")
    p.add_argument("--reason", default="workflow", help="Deprecated compatibility argument")
    p.add_argument("--config", default=DEFAULT_CONFIG, help="Path to workmode config YAML")
    p.add_argument("--audit-log", default="runtime/audit.log.ndjson", help="Deprecated compatibility argument")
    p.add_argument("--session-dir", default="runtime/sessions", help="Deprecated compatibility argument")
    p.set_defaults(func=command_auto_progress)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
