"""Tests for multi_agent.web.server — Web Dashboard API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from multi_agent.web.server import (  # noqa: E402
    _parse_jsonl_file,
    _read_trace_events,
    _sse_format,
    app,
)

# ── SSE Format ───────────────────────────────────────────


class TestSSEFormat:
    def test_basic_event(self) -> None:
        result = _sse_format("test", {"key": "value"})
        assert result.startswith("event: test\n")
        assert "data:" in result
        assert result.endswith("\n\n")

    def test_json_data(self) -> None:
        result = _sse_format("update", {"count": 42, "name": "MyGO"})
        lines = result.strip().split("\n")
        data_line = next(line for line in lines if line.startswith("data:"))
        parsed = json.loads(data_line.replace("data: ", ""))
        assert parsed["count"] == 42
        assert parsed["name"] == "MyGO"

    def test_unicode(self) -> None:
        result = _sse_format("msg", {"text": "你好世界"})
        assert "你好世界" in result


# ── JSONL Parsing ────────────────────────────────────────


class TestParseJSONL:
    def test_valid_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "test.jsonl"
        f.write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
        result = _parse_jsonl_file(f)
        assert len(result) == 2
        assert result[0] == {"a": 1}
        assert result[1] == {"b": 2}

    def test_empty_lines_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "test.jsonl"
        f.write_text('{"a":1}\n\n\n{"b":2}\n', encoding="utf-8")
        result = _parse_jsonl_file(f)
        assert len(result) == 2

    def test_bad_json_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "test.jsonl"
        f.write_text('{"a":1}\nnot json\n{"b":2}\n', encoding="utf-8")
        result = _parse_jsonl_file(f)
        assert len(result) == 2

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.jsonl"
        f.write_text("", encoding="utf-8")
        result = _parse_jsonl_file(f)
        assert result == []


# ── Read Trace Events ───────────────────────────────────


class TestReadTraceEvents:
    def test_direct_match(self, tmp_path: Path) -> None:
        hdir = tmp_path / "history"
        hdir.mkdir()
        (hdir / "abc123.jsonl").write_text(
            '{"event_type":"start","task_id":"abc123"}\n'
            '{"event_type":"done","task_id":"abc123"}\n',
            encoding="utf-8",
        )
        with patch("multi_agent.web.server.history_dir", return_value=hdir):
            events = _read_trace_events("abc123")
        assert len(events) == 2
        assert events[0]["event_type"] == "start"

    def test_prefixed_match(self, tmp_path: Path) -> None:
        hdir = tmp_path / "history"
        hdir.mkdir()
        (hdir / "task-def456.jsonl").write_text(
            '{"event_type":"plan","task_id":"def456"}\n',
            encoding="utf-8",
        )
        with patch("multi_agent.web.server.history_dir", return_value=hdir):
            events = _read_trace_events("def456")
        assert len(events) == 1

    def test_fallback_scan(self, tmp_path: Path) -> None:
        hdir = tmp_path / "history"
        hdir.mkdir()
        (hdir / "other.jsonl").write_text(
            '{"event_type":"build","task_id":"xyz789"}\n'
            '{"event_type":"review","task_id":"other"}\n',
            encoding="utf-8",
        )
        with patch("multi_agent.web.server.history_dir", return_value=hdir):
            events = _read_trace_events("xyz789")
        assert len(events) == 1
        assert events[0]["task_id"] == "xyz789"

    def test_no_history_dir(self, tmp_path: Path) -> None:
        hdir = tmp_path / "nonexistent"
        with patch("multi_agent.web.server.history_dir", return_value=hdir):
            events = _read_trace_events("missing")
        assert events == []


# ── FastAPI Endpoints ────────────────────────────────────


@pytest.fixture()
def client():
    """Create a test client for the FastAPI app."""
    from starlette.testclient import TestClient
    return TestClient(app)


class TestAPIEndpoints:
    def test_index_html(self, client: Any) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "MyGO" in resp.text
        assert "Dashboard" in resp.text

    def test_api_status(self, client: Any, tmp_path: Path) -> None:
        ws = tmp_path / ".multi-agent"
        ws.mkdir(parents=True)
        with (
            patch("multi_agent.web.server.workspace_dir", return_value=ws),
            patch("multi_agent.web.server.root_dir", return_value=tmp_path),
        ):
            resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_task" in data
        assert "root_dir" in data

    def test_api_status_with_lock(self, client: Any, tmp_path: Path) -> None:
        ws = tmp_path / ".multi-agent"
        ws.mkdir(parents=True)
        # Lock is a single .lock file containing the task_id (not a directory)
        (ws / ".lock").write_text("task-abc", encoding="utf-8")
        with (
            patch("multi_agent.web.server.workspace_dir", return_value=ws),
            patch("multi_agent.web.server.root_dir", return_value=tmp_path),
        ):
            resp = client.get("/api/status")
        assert resp.json()["active_task"] == "task-abc"

    def test_api_tasks_empty(self, client: Any, tmp_path: Path) -> None:
        ws = tmp_path / ".multi-agent"
        ws.mkdir(parents=True)
        with patch("multi_agent.web.server.workspace_dir", return_value=ws):
            resp = client.get("/api/tasks")
        data = resp.json()
        assert data["count"] == 0
        assert data["tasks"] == []

    def test_api_tasks_with_yaml(self, client: Any, tmp_path: Path) -> None:
        ws = tmp_path / ".multi-agent"
        tasks_dir = ws / "tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "task-t1.yaml").write_text(
            "requirement: build login\nstatus: approved\ncurrent_agent: codex\n",
            encoding="utf-8",
        )
        with patch("multi_agent.web.server.workspace_dir", return_value=ws):
            resp = client.get("/api/tasks")
        data = resp.json()
        assert data["count"] == 1
        assert data["tasks"][0]["requirement"] == "build login"
        assert data["tasks"][0]["status"] == "approved"

    def test_api_task_detail(self, client: Any, tmp_path: Path) -> None:
        ws = tmp_path / ".multi-agent"
        tasks_dir = ws / "tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "task-d1.yaml").write_text(
            "requirement: fix bug\nstatus: running\n",
            encoding="utf-8",
        )
        hdir = tmp_path / ".multi-agent" / "history"
        hdir.mkdir(parents=True)
        (hdir / "d1.jsonl").write_text(
            '{"event_type":"start","task_id":"d1"}\n',
            encoding="utf-8",
        )
        with (
            patch("multi_agent.web.server.workspace_dir", return_value=ws),
            patch("multi_agent.web.server.history_dir", return_value=hdir),
        ):
            resp = client.get("/api/tasks/d1")
        data = resp.json()
        assert data["task_id"] == "d1"
        assert data["task_data"]["requirement"] == "fix bug"
        assert len(data["trace_events"]) == 1

    def test_api_task_trace(self, client: Any, tmp_path: Path) -> None:
        hdir = tmp_path / "history"
        hdir.mkdir()
        (hdir / "t2.jsonl").write_text(
            '{"event_type":"build","task_id":"t2"}\n'
            '{"event_type":"review","task_id":"t2"}\n',
            encoding="utf-8",
        )
        with patch("multi_agent.web.server.history_dir", return_value=hdir):
            resp = client.get("/api/tasks/t2/trace")
        data = resp.json()
        assert len(data["events"]) == 2

    def test_api_events_sse_format_coverage(self) -> None:
        """SSE endpoint uses an infinite async loop that cannot be cleanly
        tested via TestClient. We verify the _sse_format helper instead
        (the actual formatting logic used by the SSE stream)."""
        from multi_agent.web.server import _sse_format

        out = _sse_format("connected", {"ts": 1234})
        assert out.startswith("event: connected\n")
        assert "1234" in out
        assert out.endswith("\n\n")
