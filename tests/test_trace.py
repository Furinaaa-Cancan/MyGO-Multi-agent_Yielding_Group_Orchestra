from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from multi_agent.trace import (
    append_trace_event,
    read_trace,
    render_trace,
    trace_file,
)


@pytest.fixture
def trace_root(tmp_path, monkeypatch):
    (tmp_path / "skills").mkdir()
    (tmp_path / "agents").mkdir()
    monkeypatch.setenv("MA_ROOT", str(tmp_path))
    from multi_agent.config import root_dir

    root_dir.cache_clear()
    yield tmp_path
    root_dir.cache_clear()


def test_trace_append_and_render(trace_root):
    append_trace_event(
        task_id="task-trace-1",
        event_type="session_start",
        actor="codex",
        role="orchestrator",
        state="RUNNING",
        details={"x": 1},
    )
    append_trace_event(
        task_id="task-trace-1",
        event_type="handoff_submit",
        actor="windsurf",
        role="builder",
        state="RUNNING",
        details={"y": 2},
    )

    path = trace_file("task-trace-1")
    assert path.exists()

    events = read_trace("task-trace-1")
    assert len(events) == 2
    assert events[1]["parent_id"] == events[0]["event_id"]

    tree = render_trace("task-trace-1", "tree")
    assert "session_start" in tree
    assert "handoff_submit" in tree

    mermaid = render_trace("task-trace-1", "mermaid")
    assert "graph TD" in mermaid
    assert "-->" in mermaid


def test_trace_concurrent_append_keeps_linear_parent_chain(trace_root):
    task_id = "task-trace-concurrent"

    def _emit(i: int):
        append_trace_event(
            task_id=task_id,
            event_type="event",
            actor=f"agent-{i}",
            role="builder",
            state="RUNNING",
            details={"i": i},
        )

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(_emit, range(30)))

    events = read_trace(task_id)
    assert len(events) == 30
    assert len({e["event_id"] for e in events}) == 30
    for idx in range(1, len(events)):
        assert events[idx]["parent_id"] == events[idx - 1]["event_id"]
