"""Controlled long-term memory for multi-agent sessions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multi_agent._utils import now_utc as _now_utc
from multi_agent.config import history_dir, workspace_dir


def memory_file() -> Path:
    return workspace_dir() / "MEMORY.md"


def pending_file(task_id: str) -> Path:
    return history_dir() / f"{task_id}.memory.pending.json"


def ensure_memory_file() -> Path:
    out = memory_file()
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        out.write_text(
            "# Multi-Agent Memory\n\n"
            "长期稳定约定（仅 orchestrator 在 review_pass 后写入）。\n",
            encoding="utf-8",
        )
    return out


def _normalize_candidate(item: Any) -> str | None:
    if isinstance(item, str):
        text = item.strip()
        return text if text else None
    if isinstance(item, dict):
        text = str(item.get("content", "")).strip()
        if not text:
            return None
        source = str(item.get("source", "")).strip()
        return f"{text} (source={source})" if source else text
    return None


def add_pending_candidates(task_id: str, items: list[Any], *, actor: str) -> dict[str, Any]:
    history_dir().mkdir(parents=True, exist_ok=True)
    p = pending_file(task_id)
    payload: dict[str, Any]
    if p.exists():
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    pending = payload.get("items", [])
    if not isinstance(pending, list):
        pending = []

    normalized: list[str] = []
    for raw in items:
        item = _normalize_candidate(raw)
        if item:
            normalized.append(item)

    existing = set(str(x).strip() for x in pending if isinstance(x, str))
    added = 0
    for item in normalized:
        if item in existing:
            continue
        pending.append(item)
        existing.add(item)
        added += 1

    payload = {
        "task_id": task_id,
        "updated_at": _now_utc(),
        "last_actor": actor,
        "items": pending,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"task_id": task_id, "pending_count": len(pending), "added": added, "pending_file": str(p)}


def promote_pending_candidates(task_id: str, *, actor: str) -> dict[str, Any]:
    ensure_memory_file()
    p = pending_file(task_id)
    if not p.exists():
        return {"task_id": task_id, "applied": 0, "reason": "no pending file"}

    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"task_id": task_id, "applied": 0, "reason": "pending file is invalid JSON"}
    items = payload.get("items", [])
    if not isinstance(items, list):
        return {"task_id": task_id, "applied": 0, "reason": "pending.items is not a list"}

    mem = ensure_memory_file()
    current = mem.read_text(encoding="utf-8")
    existing = set()
    for line in current.splitlines():
        line = line.strip()
        if line.startswith("- "):
            existing.add(line[2:].strip())

    to_apply = []
    for raw in items:
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text or text in existing:
            continue
        to_apply.append(text)
        existing.add(text)

    if not to_apply:
        p.unlink(missing_ok=True)
        return {"task_id": task_id, "applied": 0, "reason": "all pending items were duplicates"}

    section_lines = [
        "",
        f"## {task_id} @ {_now_utc()}",
        f"_promoted_by: {actor}_",
    ]
    for item in to_apply:
        section_lines.append(f"- {item}")

    with mem.open("a", encoding="utf-8") as f:
        f.write("\n".join(section_lines) + "\n")
    p.unlink(missing_ok=True)

    return {"task_id": task_id, "applied": len(to_apply), "memory_file": str(mem)}
