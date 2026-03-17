"""Session event tracing and renderers (tree/mermaid)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from multi_agent._utils import now_utc as _now_utc
from multi_agent._utils import validate_task_id as _validate_task_id
from multi_agent.config import history_dir

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    _fcntl = None  # type: ignore[assignment]
fcntl = _fcntl


def trace_file(task_id: str) -> Path:
    _validate_task_id(task_id)
    return history_dir() / f"{task_id}.events.jsonl"


def _read_last_event_id_from_handle(handle: Any) -> str | None:
    handle.seek(0)
    last = None
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("event_id"), str):
            last = obj["event_id"]
    return last


def append_trace_event(
    *,
    task_id: str,
    event_type: str,
    actor: str,
    role: str,
    state: str,
    details: dict[str, Any] | None = None,
    lane_id: str = "main",
    parent_id: str | None = None,
) -> dict[str, Any]:
    if not event_type or not event_type.strip():
        raise ValueError("event_type must not be empty")
    history_dir().mkdir(parents=True, exist_ok=True)
    path = trace_file(task_id)
    event_id = uuid4().hex[:12]
    with path.open("a+", encoding="utf-8") as f:
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            if parent_id is None:
                parent_id = _read_last_event_id_from_handle(f)
            payload = {
                "event_id": event_id,
                "parent_id": parent_id,
                "task_id": task_id,
                "lane_id": lane_id,
                "event_type": event_type,
                "actor": actor,
                "role": role,
                "state": state,
                "details": details or {},
                "created_at": _now_utc(),
            }
            f.seek(0, os.SEEK_END)
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            f.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return payload


def read_trace(task_id: str) -> list[dict[str, Any]]:
    path = trace_file(task_id)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _event_label(event: dict[str, Any]) -> str:
    when = str(event.get("created_at", ""))
    short_when = when.replace("T", " ").replace("+00:00", "Z")
    event_type = str(event.get("event_type", "unknown"))
    actor = str(event.get("actor", "unknown"))
    state = str(event.get("state", "UNKNOWN"))
    return f"{short_when} | {event_type} | {actor} | {state}"


def render_trace_tree(task_id: str) -> str:
    events = read_trace(task_id)
    if not events:
        return f"# Trace: {task_id}\n\n(no events)\n"

    lines = [f"# Trace: {task_id}", ""]
    for idx, event in enumerate(events, start=1):
        lines.append(f"{idx}. {_event_label(event)}")
    lines.append("")
    return "\n".join(lines)


def render_trace_mermaid(task_id: str) -> str:
    events = read_trace(task_id)
    if not events:
        return "graph TD\n  A[\"No events\"]\n"

    lines = ["graph TD"]
    for event in events:
        eid = str(event.get("event_id", ""))
        if not eid:
            continue
        node = f"E{eid}"
        label = _event_label(event).replace('"', "'")
        lines.append(f"  {node}[\"{label}\"]")
        parent = event.get("parent_id")
        if isinstance(parent, str) and parent:
            lines.append(f"  E{parent} --> {node}")
    return "\n".join(lines) + "\n"


def render_trace(task_id: str, fmt: str) -> str:
    if fmt == "mermaid":
        return render_trace_mermaid(task_id)
    if fmt == "tree":
        return render_trace_tree(task_id)
    raise ValueError(f"unsupported trace format: {fmt}")
