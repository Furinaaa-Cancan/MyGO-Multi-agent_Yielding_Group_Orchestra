#!/usr/bin/env python3
"""Compatibility entrypoint for the old langgraph_hub script."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from multi_agent.session import session_push, session_status, start_session


def _load_task_id(task_path: str) -> str:
    data = json.loads(pathlib.Path(task_path).read_text(encoding="utf-8"))
    task_id = data.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task.task_id missing")
    return task_id


def command_start(args: argparse.Namespace) -> int:
    payload = start_session(args.task, mode=args.mode, config_path=args.config, reset=args.requeue)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_status(args: argparse.Namespace) -> int:
    payload = session_status(_load_task_id(args.task))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_submit(args: argparse.Namespace) -> int:
    payload = session_push(_load_task_id(args.task), args.agent, args.result_file)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LangGraph hub compatibility wrapper")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("start")
    p.add_argument("--task", required=True)
    p.add_argument("--mode", default="strict")
    p.add_argument("--config", default=str(ROOT_DIR / "config" / "workmode.yaml"))
    p.add_argument("--requeue", action="store_true")
    p.set_defaults(func=command_start)

    p = sub.add_parser("status")
    p.add_argument("--task", required=True)
    p.set_defaults(func=command_status)

    p = sub.add_parser("submit")
    p.add_argument("--task", required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--result-file", required=True)
    p.set_defaults(func=command_submit)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
