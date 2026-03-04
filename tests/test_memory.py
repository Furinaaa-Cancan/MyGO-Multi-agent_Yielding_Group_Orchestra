from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent.memory import (
    add_pending_candidates,
    ensure_memory_file,
    memory_file,
    pending_file,
    promote_pending_candidates,
)


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    (tmp_path / "skills").mkdir()
    (tmp_path / "agents").mkdir()
    monkeypatch.setenv("MA_ROOT", str(tmp_path))
    from multi_agent.config import root_dir

    root_dir.cache_clear()
    yield tmp_path
    root_dir.cache_clear()


def test_memory_pending_and_promote(memory_root: Path):
    ensure_memory_file()
    assert memory_file().exists()

    add_result = add_pending_candidates(
        "task-memory-1",
        ["Use absolute paths in outbox artifacts", {"content": "Reviewer must provide evidence", "source": "policy"}],
        actor="antigravity",
    )
    assert add_result["added"] == 2
    assert pending_file("task-memory-1").exists()

    promote_result = promote_pending_candidates("task-memory-1", actor="orchestrator")
    assert promote_result["applied"] == 2
    text = memory_file().read_text(encoding="utf-8")
    assert "Use absolute paths in outbox artifacts" in text
    assert "Reviewer must provide evidence" in text


def test_memory_deduplicates_items(memory_root: Path):
    ensure_memory_file()
    add_pending_candidates("task-memory-2", ["A", "A", "  A  "], actor="builder")
    promote_pending_candidates("task-memory-2", actor="orchestrator")
    before = memory_file().read_text(encoding="utf-8")

    add_pending_candidates("task-memory-2", ["A"], actor="builder")
    result = promote_pending_candidates("task-memory-2", actor="orchestrator")
    assert result["applied"] == 0
    after = memory_file().read_text(encoding="utf-8")
    assert before == after
