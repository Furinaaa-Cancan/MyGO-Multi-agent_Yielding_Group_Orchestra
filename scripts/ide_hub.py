#!/usr/bin/env python3
"""Compatibility wrapper for the new `my session` workflow."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tempfile
from typing import Any

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from multi_agent.session import session_push, session_status, start_session

DEFAULT_CONFIG = str(ROOT_DIR / "config" / "workmode.yaml")


def _load_task(path: pathlib.Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"invalid task JSON object: {path}")
    return data


def _parse_json_payload(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if not raw:
        raise ValueError("empty result text")
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    blocks = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, flags=re.IGNORECASE)
    for block in reversed(blocks):
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise ValueError("failed to parse JSON payload")


def command_start(args: argparse.Namespace) -> int:
    task_path = pathlib.Path(args.task)
    task = _load_task(task_path)
    task_id = str(task.get("task_id", ""))

    payload = start_session(
        task_file=str(task_path),
        mode=args.mode,
        config_path=args.config,
        reset=args.requeue,
    )
    payload = {
        "status": "started",
        "task_id": task_id or payload.get("task_id", ""),
        "state": payload.get("state"),
        "current_agent": payload.get("current_agent"),
        "current_role": payload.get("current_role"),
        "active_prompt": payload.get("active_prompt", ""),
        "prompts": payload.get("prompt_paths", {}),
        "note": "ide_hub is now a thin wrapper over `my session`.",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_status(args: argparse.Namespace) -> int:
    task = _load_task(pathlib.Path(args.task))
    task_id = str(task.get("task_id", ""))
    if not task_id:
        print("ERROR: task.task_id missing", file=sys.stderr)
        return 1
    payload = session_status(task_id)
    payload["note"] = "Use `my session status --task-id ...` directly."
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_submit(args: argparse.Namespace) -> int:
    task = _load_task(pathlib.Path(args.task))
    task_id = str(task.get("task_id", ""))
    if not task_id:
        print("ERROR: task.task_id missing", file=sys.stderr)
        return 1

    if args.result_file:
        source = pathlib.Path(args.result_file)
        payload = session_push(task_id, args.agent, str(source))
    else:
        if args.result:
            raw = args.result
        else:
            raw = sys.stdin.read()
        obj = _parse_json_payload(raw)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tf:
            json.dump(obj, tf, ensure_ascii=False, indent=2)
            tf.write("\n")
            temp_path = tf.name
        try:
            payload = session_push(task_id, args.agent, temp_path)
        finally:
            pathlib.Path(temp_path).unlink(missing_ok=True)

    payload["status"] = "submitted"
    payload["note"] = "Use `my session push --task-id ... --agent ... --file ...` directly."
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tri-IDE wrapper (delegates to my session)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("start", help="初始化会话并生成提示词")
    p.add_argument("--task", required=True, help="任务 JSON 路径")
    p.add_argument("--config", default=DEFAULT_CONFIG, help="workmode 配置路径")
    p.add_argument("--mode", default="strict", help="模式名")
    p.add_argument("--requeue", action="store_true", help="兼容参数：等效 reset")
    p.set_defaults(func=command_start)

    p = sub.add_parser("status", help="查看当前 owner 和状态")
    p.add_argument("--task", required=True, help="任务 JSON 路径")
    p.set_defaults(func=command_status)

    p = sub.add_parser("submit", help="提交 IDE 输出并推进")
    p.add_argument("--task", required=True, help="任务 JSON 路径")
    p.add_argument("--agent", required=True, help="提交结果的 agent")
    p.add_argument("--result", help="结果文本（可含 markdown JSON 代码块）")
    p.add_argument("--result-file", help="结果文件路径")
    p.add_argument("--reason", default="agent submitted result", help="兼容参数（已忽略）")
    p.set_defaults(func=command_submit)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
