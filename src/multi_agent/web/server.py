"""FastAPI web dashboard server for MyGO multi-agent system.

Provides REST API endpoints and SSE event stream for real-time
task monitoring. Start via CLI: ``my dashboard`` or directly::

    uvicorn multi_agent.web.server:app --port 8765
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import StreamingResponse

from multi_agent.config import (
    history_dir,
    root_dir,
    workspace_dir,
)

# task_id validation: must be safe alphanumeric + hyphens (prevent path traversal)
_SAFE_TASK_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")

_log = logging.getLogger(__name__)

app = FastAPI(title="MyGO Dashboard", version="0.8.0")

# ── Static HTML ──────────────────────────────────────────

_STATIC_DIR = Path(__file__).parent / "static"


def _validate_web_task_id(task_id: str) -> bool:
    """Validate task_id for web endpoints to prevent path traversal."""
    return bool(_SAFE_TASK_ID_RE.match(task_id)) and ".." not in task_id


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Serve the dashboard single-page app."""
    html_path = _STATIC_DIR / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Dashboard HTML not found</h1>", status_code=500)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ── REST API ─────────────────────────────────────────────


@app.get("/api/status")
def api_status() -> JSONResponse:
    """Return current active task info (if any)."""
    ws = workspace_dir()
    # Read lock from .lock file (single file, not a directory)
    lock_file = ws / ".lock"
    active_task: str | None = None
    if lock_file.exists():
        with contextlib.suppress(OSError):
            active_task = lock_file.read_text(encoding="utf-8").strip() or None

    # Read dashboard.md for quick summary
    dashboard_md = ws / "dashboard.md"
    dashboard_content = ""
    if dashboard_md.exists():
        dashboard_content = dashboard_md.read_text(encoding="utf-8")

    return JSONResponse({
        "active_task": active_task,
        "root_dir": str(root_dir()),
        "dashboard_md": dashboard_content,
    })


@app.get("/api/tasks")
def api_tasks() -> JSONResponse:
    """List all task YAML files with basic info."""
    ws = workspace_dir()
    tasks_dir = ws / "tasks"
    tasks: list[dict[str, Any]] = []
    if tasks_dir.exists():
        # Cache stat results to avoid double stat() per file
        yaml_files = []
        for f in tasks_dir.glob("*.yaml"):
            try:
                yaml_files.append((f, f.stat().st_mtime))
            except OSError:
                continue
        yaml_files.sort(key=lambda x: x[1], reverse=True)
        for f, mtime in yaml_files:
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                tasks.append({
                    "task_id": f.stem.replace("task-", ""),
                    "file": f.name,
                    "requirement": data.get("requirement", ""),
                    "status": data.get("status", "unknown"),
                    "current_agent": data.get("current_agent", ""),
                    "modified": mtime,
                })
            except Exception:
                tasks.append({"task_id": f.stem, "file": f.name, "error": "parse failed"})
    return JSONResponse({"tasks": tasks, "count": len(tasks)})


@app.get("/api/tasks/{task_id}")
def api_task_detail(task_id: str) -> JSONResponse:
    """Return detailed task info including trace events."""
    if not _validate_web_task_id(task_id):
        return JSONResponse({"error": "invalid task_id"}, status_code=400)

    ws = workspace_dir()

    # Find task YAML
    task_file = ws / "tasks" / f"task-{task_id}.yaml"
    if not task_file.exists():
        task_file = ws / "tasks" / f"{task_id}.yaml"
    task_data: dict[str, Any] = {}
    if task_file.exists():
        task_data = yaml.safe_load(task_file.read_text(encoding="utf-8")) or {}

    # Read trace events
    trace_events = _read_trace_events(task_id)

    return JSONResponse({
        "task_id": task_id,
        "task_data": task_data,
        "trace_events": trace_events,
    })


@app.get("/api/tasks/{task_id}/trace")
def api_task_trace(task_id: str) -> JSONResponse:
    """Return trace events for a specific task."""
    if not _validate_web_task_id(task_id):
        return JSONResponse({"error": "invalid task_id"}, status_code=400)
    return JSONResponse({"task_id": task_id, "events": _read_trace_events(task_id)})


# ── SSE Event Stream ────────────────────────────────────


@app.get("/api/events")
async def api_events(request: Request) -> StreamingResponse:
    """Server-Sent Events stream for real-time dashboard updates.

    Watches the workspace for changes and pushes events to the client.
    """
    async def event_generator():
        # Send initial status
        yield _sse_format("connected", {"ts": time.time()})

        last_check = time.time()
        last_dashboard_mtime = 0.0
        last_trace_sizes: dict[str, int] = {}

        while True:
            if await request.is_disconnected():
                break

            try:
                events = _collect_changes(last_dashboard_mtime, last_trace_sizes)
                for evt_type, evt_data in events:
                    yield _sse_format(evt_type, evt_data)
                    if evt_type == "dashboard_update":
                        last_dashboard_mtime = evt_data.get("mtime", 0.0)
                    elif evt_type == "trace_update":
                        fname = evt_data.get("file", "")
                        last_trace_sizes[fname] = evt_data.get("size", 0)
            except Exception as e:
                _log.debug("SSE poll error: %s", e)

            # Send heartbeat every 15s
            now = time.time()
            if now - last_check > 15:
                yield _sse_format("heartbeat", {"ts": now})
                last_check = now

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Helpers ──────────────────────────────────────────────


def _parse_jsonl_file(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file into a list of dicts, skipping bad lines."""
    results: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return results


def _read_trace_events(task_id: str) -> list[dict[str, Any]]:
    """Read JSONL trace events for a task."""
    hdir = history_dir()

    # Try direct file match first
    for pattern in [f"{task_id}.events.jsonl", f"task-{task_id}.events.jsonl", f"{task_id}.jsonl", f"task-{task_id}.jsonl"]:
        trace_file = hdir / pattern
        if trace_file.exists():
            return _parse_jsonl_file(trace_file)

    # Fallback: scan all JSONL files for matching task_id
    events: list[dict[str, Any]] = []
    if hdir.exists():
        for f in hdir.glob("*.jsonl"):
            for evt in _parse_jsonl_file(f):
                tid = evt.get("task_id", "")
                if tid == task_id or tid.endswith(task_id):
                    events.append(evt)
    return events


def _collect_changes(
    last_dashboard_mtime: float,
    last_trace_sizes: dict[str, int],
) -> list[tuple[str, dict[str, Any]]]:
    """Check for file changes since last poll. Returns list of (event_type, data)."""
    changes: list[tuple[str, dict[str, Any]]] = []
    ws = workspace_dir()

    # Check dashboard.md changes
    dashboard_file = ws / "dashboard.md"
    if dashboard_file.exists():
        mtime = dashboard_file.stat().st_mtime
        if mtime > last_dashboard_mtime:
            content = dashboard_file.read_text(encoding="utf-8")
            changes.append(("dashboard_update", {
                "content": content,
                "mtime": mtime,
            }))

    # Check trace file changes
    hdir = history_dir()
    if hdir.exists():
        for f in hdir.glob("*.jsonl"):
            size = f.stat().st_size
            prev_size = last_trace_sizes.get(f.name, 0)
            if size > prev_size:
                # Read only new lines
                with f.open("r", encoding="utf-8") as fh:
                    fh.seek(prev_size)
                    new_content = fh.read()
                new_events = []
                for line in new_content.splitlines():
                    line = line.strip()
                    if line:
                        try:
                            new_events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                if new_events:
                    changes.append(("trace_update", {
                        "file": f.name,
                        "size": size,
                        "new_events": new_events,
                    }))

    return changes


def _sse_format(event_type: str, data: dict[str, Any]) -> str:
    """Format a Server-Sent Event message."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
