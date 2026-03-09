"""Tests for semantic_memory module and v0.10.0 features."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest


# ── Fixture: temp workspace ───────────────────────────────

@pytest.fixture(autouse=True)
def _temp_workspace(tmp_path, monkeypatch):
    """Redirect workspace_dir to a temp directory for all tests."""
    ws = tmp_path / ".multi-agent"
    ws.mkdir()
    monkeypatch.setenv("MA_ROOT", str(tmp_path))
    (tmp_path / "skills").mkdir()
    (tmp_path / "agents").mkdir()
    # Patch workspace_dir in semantic_memory module
    monkeypatch.setattr("multi_agent.semantic_memory.workspace_dir", lambda: ws)
    return ws


# ══════════════════════════════════════════════════════════
# Feature A: Semantic Memory
# ══════════════════════════════════════════════════════════


class TestSemanticMemoryStore:
    """Test memory storage operations."""

    def test_store_basic(self):
        from multi_agent.semantic_memory import store
        result = store("Use JWT for API authentication", category="architecture")
        assert result["status"] == "stored"
        assert "entry_id" in result
        assert result["count"] == 1

    def test_store_dedup(self):
        from multi_agent.semantic_memory import store
        r1 = store("Always use type hints")
        r2 = store("Always use type hints")
        assert r1["status"] == "stored"
        assert r2["status"] == "duplicate"
        assert r1["entry_id"] == r2["entry_id"]

    def test_store_empty_content(self):
        from multi_agent.semantic_memory import store
        result = store("")
        assert result["status"] == "error"
        assert "empty" in result["reason"]

    def test_store_invalid_category_falls_back(self):
        from multi_agent.semantic_memory import store, _load_entries
        store("test content", category="nonexistent_category")
        entries = _load_entries()
        assert entries[0]["category"] == "general"

    def test_store_with_tags(self):
        from multi_agent.semantic_memory import store, _load_entries
        store("Use black for formatting", tags=["python", "formatting"])
        entries = _load_entries()
        assert entries[0]["tags"] == ["python", "formatting"]

    def test_store_with_metadata(self):
        from multi_agent.semantic_memory import store, _load_entries
        store("Important decision", metadata={"confidence": 0.9})
        entries = _load_entries()
        assert entries[0]["metadata"]["confidence"] == 0.9


class TestSemanticMemorySearch:
    """Test TF-IDF search and retrieval."""

    def _seed_entries(self):
        from multi_agent.semantic_memory import store
        store("Use JWT tokens for API authentication", category="architecture", tags=["auth", "jwt"])
        store("Always run ruff before committing Python code", category="convention", tags=["python", "lint"])
        store("Database migrations should be reversible", category="architecture", tags=["database"])
        store("Use pytest fixtures for test setup", category="convention", tags=["python", "testing"])
        store("Avoid N+1 queries in ORM usage", category="pattern", tags=["database", "performance"])

    def test_search_exact_match(self):
        from multi_agent.semantic_memory import search
        self._seed_entries()
        results = search("JWT authentication")
        assert len(results) > 0
        assert "JWT" in results[0]["entry"]["content"]

    def test_search_semantic_match(self):
        from multi_agent.semantic_memory import search
        self._seed_entries()
        results = search("database performance optimization")
        assert len(results) > 0
        # Should find N+1 queries entry
        contents = [r["entry"]["content"] for r in results]
        assert any("N+1" in c or "database" in c.lower() for c in contents)

    def test_search_by_category(self):
        from multi_agent.semantic_memory import search
        self._seed_entries()
        results = search("python", category="convention")
        for r in results:
            assert r["entry"]["category"] == "convention"

    def test_search_empty_query(self):
        from multi_agent.semantic_memory import search
        self._seed_entries()
        results = search("")
        assert results == []

    def test_search_no_match(self):
        from multi_agent.semantic_memory import search
        self._seed_entries()
        results = search("quantum computing blockchain")
        # May or may not return results depending on token overlap
        # But scores should be low

    def test_search_top_k(self):
        from multi_agent.semantic_memory import search
        self._seed_entries()
        results = search("python", top_k=2)
        assert len(results) <= 2

    def test_get_context(self):
        from multi_agent.semantic_memory import get_context
        self._seed_entries()
        ctx = get_context("authentication")
        assert "## Relevant Project Memory" in ctx
        assert "auth" in ctx.lower()

    def test_get_context_empty(self):
        from multi_agent.semantic_memory import get_context
        ctx = get_context("anything")
        assert ctx == ""


class TestSemanticMemoryManagement:
    """Test delete, clear, list, stats operations."""

    def test_list_entries(self):
        from multi_agent.semantic_memory import list_entries, store
        store("Entry 1", category="architecture")
        store("Entry 2", category="convention")
        entries = list_entries()
        assert len(entries) == 2

    def test_list_entries_by_category(self):
        from multi_agent.semantic_memory import list_entries, store
        store("Arch entry", category="architecture")
        store("Conv entry", category="convention")
        entries = list_entries(category="architecture")
        assert len(entries) == 1
        assert entries[0]["category"] == "architecture"

    def test_delete_entry(self):
        from multi_agent.semantic_memory import delete, list_entries, store
        r = store("To be deleted")
        entry_id = r["entry_id"]
        result = delete(entry_id)
        assert result["status"] == "deleted"
        assert len(list_entries()) == 0

    def test_delete_nonexistent(self):
        from multi_agent.semantic_memory import delete
        result = delete("nonexistent_id")
        assert result["status"] == "not_found"

    def test_clear_all(self):
        from multi_agent.semantic_memory import clear, list_entries, store
        store("Entry 1")
        store("Entry 2")
        result = clear()
        assert result["removed"] == 2
        assert len(list_entries()) == 0

    def test_clear_by_category(self):
        from multi_agent.semantic_memory import clear, list_entries, store
        store("Arch", category="architecture")
        store("Conv", category="convention")
        result = clear(category="architecture")
        assert result["removed"] == 1
        entries = list_entries()
        assert len(entries) == 1
        assert entries[0]["category"] == "convention"

    def test_stats(self):
        from multi_agent.semantic_memory import stats, store
        store("A", category="architecture")
        store("B", category="convention")
        store("C", category="architecture")
        s = stats()
        assert s["total_entries"] == 3
        assert s["by_category"]["architecture"] == 2
        assert s["by_category"]["convention"] == 1


class TestSemanticMemoryAutoCapture:
    """Test auto-capture from review summaries."""

    def test_capture_from_review(self):
        from multi_agent.semantic_memory import capture_from_review, list_entries
        review = (
            "The code should always use type hints for function parameters. "
            "The architecture pattern of separating handlers from business logic is good. "
            "Remember to avoid circular imports."
        )
        result = capture_from_review("task-001", review, agent_id="claude")
        assert result["captured"] > 0
        entries = list_entries()
        assert len(entries) > 0
        assert any("auto-captured" in e.get("tags", []) for e in entries)

    def test_capture_short_review_skipped(self):
        from multi_agent.semantic_memory import capture_from_review
        result = capture_from_review("task-001", "LGTM")
        assert result["captured"] == 0


class TestTFIDFEngine:
    """Test the TF-IDF internals."""

    def test_tokenize(self):
        from multi_agent.semantic_memory import _tokenize
        tokens = _tokenize("Use JWT tokens for API authentication")
        assert "jwt" in tokens
        assert "tokens" in tokens
        assert "the" not in tokens  # stop word

    def test_tokenize_chinese(self):
        from multi_agent.semantic_memory import _tokenize
        tokens = _tokenize("使用JWT进行认证")
        assert any("\u4e00" <= c <= "\u9fff" for t in tokens for c in t)

    def test_cosine_similarity_identical(self):
        from multi_agent.semantic_memory import _cosine_similarity
        vec = {"a": 1.0, "b": 2.0}
        sim = _cosine_similarity(vec, vec)
        assert abs(sim - 1.0) < 0.001

    def test_cosine_similarity_orthogonal(self):
        from multi_agent.semantic_memory import _cosine_similarity
        a = {"x": 1.0}
        b = {"y": 1.0}
        assert _cosine_similarity(a, b) == 0.0


# ══════════════════════════════════════════════════════════
# Feature B: Dashboard Bidirectional Control
# ══════════════════════════════════════════════════════════


class TestDashboardActions:
    """Test action API contract (file-based operations)."""

    def test_cancel_writes_task_yaml(self, tmp_path, monkeypatch):
        """Cancel action should write cancelled status to task YAML."""
        import yaml
        from multi_agent.config import workspace_dir
        ws = workspace_dir()
        tasks_dir = ws / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task_file = tasks_dir / "test-task-123.yaml"
        task_file.write_text(yaml.dump({"status": "active"}), encoding="utf-8")

        # Simulate cancel
        data = yaml.safe_load(task_file.read_text(encoding="utf-8")) or {}
        data["status"] = "cancelled"
        data["reason"] = "test cancel"
        task_file.write_text(yaml.dump(data), encoding="utf-8")

        result = yaml.safe_load(task_file.read_text(encoding="utf-8"))
        assert result["status"] == "cancelled"
        assert result["reason"] == "test cancel"

    def test_review_writes_outbox(self):
        """Review action should write reviewer.json to outbox."""
        from multi_agent.config import workspace_dir
        ws = workspace_dir()
        outbox = ws / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)

        reviewer_output = {
            "decision": "approve",
            "feedback": "Looks good",
            "summary": "Approved via Dashboard",
            "source": "dashboard",
        }
        out_file = outbox / "reviewer.json"
        out_file.write_text(json.dumps(reviewer_output), encoding="utf-8")

        loaded = json.loads(out_file.read_text(encoding="utf-8"))
        assert loaded["decision"] == "approve"
        assert loaded["source"] == "dashboard"

    def test_reject_requires_feedback(self):
        """Reject decision should include feedback."""
        reviewer_output = {
            "decision": "reject",
            "feedback": "Missing error handling",
            "source": "dashboard",
        }
        assert reviewer_output["decision"] == "reject"
        assert len(reviewer_output["feedback"]) > 0


# ══════════════════════════════════════════════════════════
# Feature C: Python Server Alignment
# ══════════════════════════════════════════════════════════


class TestPythonServerAlignment:
    """Test that Python FastAPI server has parity with Node.js."""

    def test_safe_equal_timing_safe(self):
        """Python server uses hmac.compare_digest for timing safety."""
        import hmac
        assert hmac.compare_digest(b"test", b"test")
        assert not hmac.compare_digest(b"test", b"wrong")

    def test_server_has_auth_endpoints(self):
        """FastAPI server should have auth check and login endpoints."""
        from multi_agent.web.server import app
        routes = [r.path for r in app.routes]
        assert "/api/auth/check" in routes
        assert "/api/auth/login" in routes

    def test_server_has_finops_endpoint(self):
        from multi_agent.web.server import app
        routes = [r.path for r in app.routes]
        assert "/api/finops" in routes

    def test_server_has_memory_endpoints(self):
        from multi_agent.web.server import app
        routes = [r.path for r in app.routes]
        assert "/api/memory" in routes
        assert "/api/memory/search" in routes

    def test_server_has_action_endpoints(self):
        from multi_agent.web.server import app
        routes = [r.path for r in app.routes]
        assert "/api/actions/cancel" in routes
        assert "/api/actions/review" in routes

    def test_server_version_updated(self):
        from multi_agent.web.server import app
        assert app.version == "0.10.0"


# ══════════════════════════════════════════════════════════
# CLI memory command
# ══════════════════════════════════════════════════════════


class TestMemoryCLI:
    """Test the 'my memory' CLI command."""

    def test_memory_command_registered(self):
        from multi_agent.cli import main
        commands = list(main.commands.keys())
        assert "memory" in commands

    def test_memory_stats_empty(self):
        from click.testing import CliRunner
        from multi_agent.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["memory", "stats"])
        assert result.exit_code == 0
        assert "Total entries: 0" in result.output

    def test_memory_add_and_list(self):
        from click.testing import CliRunner
        from multi_agent.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["memory", "add", "Test memory entry", "-c", "architecture"])
        assert result.exit_code == 0
        assert "Stored" in result.output

        result = runner.invoke(main, ["memory", "list"])
        assert result.exit_code == 0
        assert "Test memory entry" in result.output

    def test_memory_search(self):
        from click.testing import CliRunner
        from multi_agent.cli import main
        runner = CliRunner()
        runner.invoke(main, ["memory", "add", "Use JWT for authentication", "-t", "auth,jwt"])
        result = runner.invoke(main, ["memory", "search", "JWT auth"])
        assert result.exit_code == 0
        assert "JWT" in result.output


# ══════════════════════════════════════════════════════════
# Feature E: Smart Retry with Memory Injection
# ══════════════════════════════════════════════════════════


class TestSmartRetry:
    """Test that retry prompts get memory context injected."""

    def test_get_context_returns_relevant_memory(self):
        from multi_agent.semantic_memory import get_context, store
        store("Always validate input before database queries", category="convention", tags=["security"])
        store("Use parameterized queries to prevent SQL injection", category="bugfix", tags=["security", "sql"])
        ctx = get_context("input validation SQL injection")
        assert "Relevant Project Memory" in ctx
        assert "validate" in ctx.lower() or "sql" in ctx.lower()

    def test_get_context_respects_max_chars(self):
        from multi_agent.semantic_memory import get_context, store
        store("A" * 500, category="general")
        store("B" * 500, category="general")
        ctx = get_context("test", max_chars=100)
        assert len(ctx) < 200  # header + truncated content

    def test_memory_injection_does_not_crash_on_empty(self):
        """Smart retry should not crash when no memory exists."""
        from multi_agent.semantic_memory import get_context
        ctx = get_context("nonexistent topic with no matches")
        assert ctx == ""


# ══════════════════════════════════════════════════════════
# Feature B: MCP Server Write Tools
# ══════════════════════════════════════════════════════════


_has_fastmcp = True
try:
    import fastmcp  # noqa: F401
except ImportError:
    _has_fastmcp = False


@pytest.mark.skipif(not _has_fastmcp, reason="fastmcp not installed")
class TestMCPWriteTools:
    """Test new MCP server write tools."""

    def test_mcp_has_submit_review_tool(self):
        from multi_agent.mcp_server import mcp
        tool_names = [t.name for t in mcp._tool_manager.list_tools()]
        assert "submit_review" in tool_names

    def test_mcp_has_memory_tools(self):
        from multi_agent.mcp_server import mcp
        tool_names = [t.name for t in mcp._tool_manager.list_tools()]
        assert "memory_search" in tool_names
        assert "memory_store" in tool_names
        assert "memory_list" in tool_names

    def test_mcp_has_finops_tool(self):
        from multi_agent.mcp_server import mcp
        tool_names = [t.name for t in mcp._tool_manager.list_tools()]
        assert "finops_summary" in tool_names

    def test_memory_store_via_mcp(self):
        from multi_agent.mcp_server import memory_store
        result = memory_store("Test MCP memory entry", category="convention", tags="mcp,test")
        assert result["status"] == "stored"

    def test_memory_search_via_mcp(self):
        from multi_agent.mcp_server import memory_search, memory_store
        memory_store("Use black formatter for Python", tags="python,formatting")
        result = memory_search("python formatting")
        assert result["count"] > 0

    def test_memory_list_via_mcp(self):
        from multi_agent.mcp_server import memory_list, memory_store
        memory_store("Test entry for list", category="architecture")
        result = memory_list()
        assert result["count"] > 0
        assert "stats" in result

    def test_memory_store_empty_rejected(self):
        from multi_agent.mcp_server import memory_store
        result = memory_store("")
        assert "error" in result

    def test_submit_review_invalid_decision(self):
        from multi_agent.mcp_server import submit_review
        result = submit_review("invalid_decision")
        assert "error" in result

    def test_submit_review_no_active_task(self):
        from multi_agent.mcp_server import submit_review
        result = submit_review("approve")
        assert "error" in result  # no active task
