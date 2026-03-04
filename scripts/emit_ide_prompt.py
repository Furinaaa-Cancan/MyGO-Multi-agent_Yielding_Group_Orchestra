#!/usr/bin/env python3
"""Emit pure-IDE prompts (no terminal command instructions)."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from multi_agent.session import (
    activate_project_root_for_task_file,
    session_pull,
    session_status,
)


def load_json(path: pathlib.Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"invalid JSON object: {path}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="为 IDE agent 生成可复制提示词（纯 IDE 版）")
    parser.add_argument("--task", required=True, help="任务 JSON 路径")
    parser.add_argument("--agent", required=True, help="Agent id")
    parser.add_argument("--out", help="可选：输出到文件")
    parser.add_argument("--json-meta", action="store_true", help="输出元信息 JSON")
    args = parser.parse_args()

    task_path = pathlib.Path(args.task).expanduser().resolve()
    task = load_json(task_path)
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        print("ERROR: task.task_id missing", file=sys.stderr)
        return 1

    # Ensure this subprocess reads the same workspace as the task file.
    root = activate_project_root_for_task_file(str(task_path))
    if root is not None:
        os.environ["MA_ROOT"] = str(root)

    payload = session_pull(task_id, args.agent, out=args.out)
    if args.json_meta:
        # Add current session status for compatibility tooling.
        status = session_status(task_id)
        payload["session"] = status
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    prompt_path = pathlib.Path(payload["prompt_path"])
    text = prompt_path.read_text(encoding="utf-8")
    print(text.rstrip("\n"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
