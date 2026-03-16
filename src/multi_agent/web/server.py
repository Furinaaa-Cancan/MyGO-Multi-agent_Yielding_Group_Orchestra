"""FastAPI web dashboard server for MyGO multi-agent system.

Provides REST API endpoints and SSE event stream for real-time
task monitoring. Start via CLI: ``my dashboard`` or directly::

    uvicorn multi_agent.web.server:app --port 8765
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import StreamingResponse

from multi_agent.config import (
    history_dir,
    root_dir,
    workspace_dir,
)
from multi_agent.workspace import read_lock, release_lock

# task_id validation: must be safe alphanumeric + hyphens (prevent path traversal)
_SAFE_TASK_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")

_log = logging.getLogger(__name__)

app = FastAPI(title="MyGO Dashboard", version="0.10.0")

# ── Auth ─────────────────────────────────────────────────

_AUTH_TOKEN = os.environ.get("MYGO_AUTH_TOKEN", "")

_AUTH_PUBLIC_PATHS = {"/api/auth/check", "/api/auth/login", "/", "/index.html"}


def _safe_equal(a: str, b: str) -> bool:
    """Timing-safe string comparison."""
    return hmac.compare_digest(a.encode(), b.encode())


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Optional token-based auth middleware."""
    path = request.url.path
    if not _AUTH_TOKEN:
        return await call_next(request)
    # Public paths + static files
    if path in _AUTH_PUBLIC_PATHS or not path.startswith("/api/"):
        return await call_next(request)
    # SSE: accept token via query param
    if path == "/api/events":
        qt = request.query_params.get("token", "")
        if _safe_equal(qt, _AUTH_TOKEN):
            return await call_next(request)
    # Bearer token
    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    if not _safe_equal(token, _AUTH_TOKEN):
        return JSONResponse({"error": "Unauthorized", "auth_required": True}, status_code=401)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization"],
)

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


# ── Auth Endpoints ───────────────────────────────────────


@app.get("/api/auth/check")
def api_auth_check() -> JSONResponse:
    return JSONResponse({"auth_required": bool(_AUTH_TOKEN)})


@app.post("/api/auth/login")
async def api_auth_login(request: Request) -> JSONResponse:
    if not _AUTH_TOKEN:
        return JSONResponse({"ok": True})
    body = await request.json()
    token = body.get("token", "")
    if _safe_equal(token, _AUTH_TOKEN):
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "Invalid token"}, status_code=401)


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


# ── FinOps API ───────────────────────────────────────────


@app.get("/api/finops")
def api_finops() -> JSONResponse:
    """Aggregated token usage stats."""
    usage_file = workspace_dir() / "logs" / "token-usage.jsonl"
    empty = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "total_cost": 0,
             "task_count": 0, "entry_count": 0, "by_task": {}, "by_node": {}, "by_agent": {}}
    if not usage_file.exists():
        return JSONResponse(empty)
    try:
        if usage_file.stat().st_size > 10 * 1024 * 1024:
            return JSONResponse({"error": "Usage log too large"}, status_code=413)
    except OSError:
        return JSONResponse(empty)

    entries = _parse_jsonl_file(usage_file)
    totals = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "total_cost": 0.0}
    by_task: dict[str, dict] = {}
    by_node: dict[str, dict] = {}
    by_agent: dict[str, dict] = {}
    task_ids: set[str] = set()

    for e in entries:
        inp = int(e.get("input_tokens", 0))
        out = int(e.get("output_tokens", 0))
        tot = int(e.get("total_tokens", 0)) or (inp + out)
        cost = float(e.get("cost", 0.0))
        tid = e.get("task_id", "unknown")
        node = e.get("node", "unknown")
        agent = e.get("agent_id", "unknown")
        task_ids.add(tid)
        totals["total_tokens"] += tot
        totals["input_tokens"] += inp
        totals["output_tokens"] += out
        totals["total_cost"] += cost
        for bucket, key in [(by_task, tid), (by_node, node), (by_agent, agent)]:
            if key not in bucket:
                bucket[key] = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "count": 0}
            b = bucket[key]
            b["total_tokens"] += tot; b["input_tokens"] += inp; b["output_tokens"] += out
            b["cost"] += cost; b["count"] += 1

    totals["total_cost"] = round(totals["total_cost"], 6)
    for bucket in (by_task, by_node, by_agent):
        for v in bucket.values():
            v["cost"] = round(v["cost"], 6)

    return JSONResponse({**totals, "task_count": len(task_ids), "entry_count": len(entries),
                         "by_task": by_task, "by_node": by_node, "by_agent": by_agent})


# ── Memory API ───────────────────────────────────────────


@app.get("/api/memory")
def api_memory(category: str | None = None) -> JSONResponse:
    mem_file = workspace_dir() / "memory" / "semantic.jsonl"
    if not mem_file.exists():
        return JSONResponse({"entries": [], "total": 0})
    try:
        if mem_file.stat().st_size > 20 * 1024 * 1024:
            return JSONResponse({"error": "Memory file too large"}, status_code=413)
    except OSError:
        return JSONResponse({"entries": [], "total": 0})
    entries = _parse_jsonl_file(mem_file)
    filtered = [e for e in entries if e.get("category") == category] if category else entries
    by_cat: dict[str, int] = {}
    for e in entries:
        c = e.get("category", "general")
        by_cat[c] = by_cat.get(c, 0) + 1
    return JSONResponse({"entries": filtered[-100:], "total": len(entries), "by_category": by_cat})


@app.get("/api/memory/search")
def api_memory_search(q: str = "", k: int = 5) -> JSONResponse:
    if not q.strip():
        return JSONResponse({"results": []})
    mem_file = workspace_dir() / "memory" / "semantic.jsonl"
    if not mem_file.exists():
        return JSONResponse({"results": []})
    entries = _parse_jsonl_file(mem_file)
    q_lower = q.lower()
    q_tokens = [t for t in q_lower.split() if len(t) > 1]
    scored = []
    for e in entries:
        content = (e.get("content", "")).lower()
        tags = [t.lower() for t in e.get("tags", [])]
        score = 0
        if q_lower in content:
            score += 3
        for t in q_tokens:
            if t in content:
                score += 1
            if t in tags:
                score += 1
        if score > 0:
            scored.append({"entry": e, "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return JSONResponse({"results": scored[:k]})


# ── Task Actions API ─────────────────────────────────────


@app.post("/api/actions/cancel")
async def api_action_cancel(request: Request) -> JSONResponse:
    body = await request.json()
    task_id = body.get("task_id", "")
    if not task_id or not _validate_web_task_id(task_id):
        return JSONResponse({"error": "invalid or missing task_id"}, status_code=400)
    reason = (body.get("reason", "cancelled from dashboard") or "")[:500]
    ws = workspace_dir()
    tasks_path = ws / "tasks"
    tasks_path.mkdir(parents=True, exist_ok=True)
    task_file = tasks_path / f"{task_id}.yaml"
    try:
        existing = {}
        if task_file.exists():
            with contextlib.suppress(Exception):
                existing = yaml.safe_load(task_file.read_text(encoding="utf-8")) or {}
        existing["status"] = "cancelled"
        existing["reason"] = reason
        task_file.write_text(yaml.dump(existing), encoding="utf-8")
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    # Release lock (use workspace API to avoid TOCTOU race)
    with contextlib.suppress(OSError):
        if read_lock() == task_id:
            release_lock()
    # Clear runtime
    for sub in ("outbox", "inbox"):
        d = ws / sub
        if d.exists():
            for f in d.iterdir():
                with contextlib.suppress(OSError):
                    f.unlink()
    for f in ("TASK.md", "DASHBOARD.md"):
        with contextlib.suppress(OSError):
            (ws / f).unlink()
    return JSONResponse({"ok": True, "task_id": task_id, "action": "cancelled"})


@app.post("/api/actions/review")
async def api_action_review(request: Request) -> JSONResponse:
    body = await request.json()
    decision = body.get("decision", "")
    if decision not in ("approve", "reject", "request_changes"):
        return JSONResponse({"error": "decision must be approve, reject, or request_changes"}, status_code=400)
    raw_feedback = (body.get("feedback", "") or "")[:2000]
    feedback = raw_feedback or ("Approved from Dashboard" if decision == "approve" else "Rejected from Dashboard")
    summary = ((body.get("summary", "") or "")[:500]) or f"Review {decision} via Dashboard"
    ws = workspace_dir()
    outbox = ws / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    reviewer_output = {
        "decision": decision, "feedback": feedback, "summary": summary,
        "source": "dashboard", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out_file = outbox / "reviewer.json"
    try:
        out_file.write_text(json.dumps(reviewer_output, indent=2), encoding="utf-8")
        return JSONResponse({"ok": True, "action": "review", "decision": decision})
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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


_MAX_TRACE_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def _read_trace_events(task_id: str) -> list[dict[str, Any]]:
    """Read JSONL trace events for a task."""
    hdir = history_dir()

    # Try direct file match first
    for pattern in [f"{task_id}.events.jsonl", f"task-{task_id}.events.jsonl", f"{task_id}.jsonl", f"task-{task_id}.jsonl"]:
        trace_file = hdir / pattern
        try:
            if trace_file.exists():
                if trace_file.stat().st_size > _MAX_TRACE_FILE_SIZE:
                    return []
                return _parse_jsonl_file(trace_file)
        except OSError:
            continue

    # Fallback: scan all JSONL files for matching task_id
    events: list[dict[str, Any]] = []
    if hdir.exists():
        for f in hdir.glob("*.jsonl"):
            try:
                if f.stat().st_size > _MAX_TRACE_FILE_SIZE:
                    continue
            except OSError:
                continue
            for evt in _parse_jsonl_file(f):
                tid = evt.get("task_id", "")
                if tid == task_id or tid == f"task-{task_id}":
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
    try:
        if dashboard_file.exists():
            mtime = dashboard_file.stat().st_mtime
            if mtime > last_dashboard_mtime:
                content = dashboard_file.read_text(encoding="utf-8")
                changes.append(("dashboard_update", {
                    "content": content,
                    "mtime": mtime,
                }))
    except OSError:
        pass

    # Check trace file changes
    hdir = history_dir()
    if hdir.exists():
        for f in hdir.glob("*.jsonl"):
            try:
                size = f.stat().st_size
            except OSError:
                continue
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
