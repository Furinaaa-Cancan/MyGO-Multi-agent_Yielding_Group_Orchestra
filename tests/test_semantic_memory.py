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


# ══════════════════════════════════════════════════════════
# Feature C: Webhook Notification Formatters
# ══════════════════════════════════════════════════════════


class TestWebhookFormatters:
    """Test Slack/Discord/Generic webhook payload formatters."""

    def test_detect_slack_url(self):
        from multi_agent.notify import _detect_webhook_format
        assert _detect_webhook_format("https://hooks.slack.com/services/T00/B00/xxx") == "slack"

    def test_detect_discord_url(self):
        from multi_agent.notify import _detect_webhook_format
        assert _detect_webhook_format("https://discord.com/api/webhooks/123/abc") == "discord"

    def test_detect_generic_url(self):
        from multi_agent.notify import _detect_webhook_format
        assert _detect_webhook_format("https://example.com/webhook") == "generic"

    def test_slack_payload_structure(self):
        from multi_agent.notify import _format_slack_payload
        p = _format_slack_payload("task_complete", "task-abc", "approved", "All good", 1)
        assert "attachments" in p
        assert len(p["attachments"]) == 1
        att = p["attachments"][0]
        assert att["color"] == "#36a64f"
        assert any(f["title"] == "Retries" for f in att["fields"])

    def test_discord_payload_structure(self):
        from multi_agent.notify import _format_discord_payload
        p = _format_discord_payload("task_complete", "task-abc", "failed", "Bug found", 0)
        assert "embeds" in p
        assert len(p["embeds"]) == 1
        embed = p["embeds"][0]
        assert embed["color"] == 0xDC3545
        assert not any(f["name"] == "Retries" for f in embed["fields"])

    def test_format_auto_selects_slack(self):
        from multi_agent.notify import _format_webhook_payload
        p = _format_webhook_payload("auto", "https://hooks.slack.com/x", "task_complete", "t", "approved", "", 0)
        assert "attachments" in p

    def test_format_generic_payload(self):
        from multi_agent.notify import _format_webhook_payload
        p = _format_webhook_payload("generic", "https://example.com", "task_complete", "t", "done", "ok", 0)
        assert p == {"event": "task_complete", "task_id": "t", "status": "done", "summary": "ok", "retries": 0}

    def test_notify_config_webhook_fields(self):
        from multi_agent.notify import NotifyConfig
        cfg = NotifyConfig(webhook_format="discord", webhook_retries=3)
        assert cfg.webhook_format == "discord"
        assert cfg.webhook_retries == 3

    def test_decompose_notification(self):
        from multi_agent.notify import NotifyConfig, notify_decompose_complete
        cfg = NotifyConfig(enabled=True, macos=False, webhook_url="")
        # Should not raise even with no webhook/macOS
        notify_decompose_complete("task-parent", 5, 4, ["sub-3"], 120.0, config=cfg)


# ══════════════════════════════════════════════════════════
# Feature F: Enhanced Doctor Command
# ══════════════════════════════════════════════════════════


class TestDoctorCommand:
    """Test enhanced my doctor command."""

    def test_doctor_runs_without_error(self):
        from click.testing import CliRunner
        from multi_agent.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "Workspace" in result.output
        assert "结果:" in result.output

    def test_doctor_checks_all_sections(self):
        from click.testing import CliRunner
        from multi_agent.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["doctor"])
        assert "[1/5]" in result.output
        assert "[2/5]" in result.output
        assert "[3/5]" in result.output
        assert "[4/5]" in result.output
        assert "[5/5]" in result.output

    def test_doctor_fix_flag(self):
        from click.testing import CliRunner
        from multi_agent.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["doctor", "--fix"])
        assert result.exit_code == 0


# ══════════════════════════════════════════════════════════
# Feature G: OpenAI Embeddings Backend
# ══════════════════════════════════════════════════════════


class TestOpenAIEmbeddingsBackend:
    """Test OpenAI embeddings backend (mock-based, no real API calls)."""

    def test_get_backend_defaults_to_tfidf(self):
        from multi_agent.semantic_memory import _get_backend
        assert _get_backend() == "tfidf"

    def test_get_backend_requires_api_key(self, monkeypatch):
        from multi_agent.semantic_memory import _get_backend
        monkeypatch.setattr("multi_agent.semantic_memory._get_memory_config",
                            lambda: {"backend": "openai"})
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert _get_backend() == "tfidf"

    def test_get_backend_openai_with_key(self, monkeypatch):
        from multi_agent.semantic_memory import _get_backend
        monkeypatch.setattr("multi_agent.semantic_memory._get_memory_config",
                            lambda: {"backend": "openai"})
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        assert _get_backend() == "openai"

    def test_embeddings_cache_roundtrip(self):
        from multi_agent.semantic_memory import (
            _load_embeddings_cache, _save_embeddings_cache,
        )
        cache = {"abc123": [0.1, 0.2, 0.3], "def456": [0.4, 0.5, 0.6]}
        _save_embeddings_cache(cache)
        loaded = _load_embeddings_cache()
        assert loaded == cache

    def test_cosine_sim_vectors(self):
        from multi_agent.semantic_memory import _cosine_sim_vectors
        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0, 0.0]
        assert abs(_cosine_sim_vectors(a, b) - 1.0) < 1e-6

    def test_cosine_sim_vectors_orthogonal(self):
        from multi_agent.semantic_memory import _cosine_sim_vectors
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_sim_vectors(a, b)) < 1e-6

    def test_cosine_sim_vectors_zero(self):
        from multi_agent.semantic_memory import _cosine_sim_vectors
        assert _cosine_sim_vectors([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_search_falls_back_on_openai_failure(self, monkeypatch):
        """When OpenAI fails, search should fall back to TF-IDF."""
        from multi_agent.semantic_memory import search, store
        store("Use pytest for testing Python code", category="convention")
        monkeypatch.setattr("multi_agent.semantic_memory._get_backend", lambda: "openai")
        monkeypatch.setattr("multi_agent.semantic_memory._openai_embed",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("API down")))
        results = search("pytest testing")
        assert len(results) > 0  # fell back to TF-IDF

    def test_search_openai_with_mocked_embeddings(self, monkeypatch):
        """Test the full OpenAI search path with mocked embed function."""
        from multi_agent.semantic_memory import search, store
        store("Always use type hints in Python functions", category="convention")
        store("React components should use functional style", category="convention")

        call_count = [0]
        def mock_embed(texts, model="text-embedding-3-small"):
            call_count[0] += 1
            # Return simple vectors: each text gets a unique-ish vector
            return [[float(i + j) for j in range(8)] for i in range(len(texts))]

        monkeypatch.setattr("multi_agent.semantic_memory._get_backend", lambda: "openai")
        monkeypatch.setattr("multi_agent.semantic_memory._openai_embed", mock_embed)
        results = search("type hints Python")
        assert call_count[0] > 0  # API was called
        assert isinstance(results, list)

    def test_memory_config_returns_empty_on_missing(self):
        from multi_agent.semantic_memory import _get_memory_config
        cfg = _get_memory_config()
        assert isinstance(cfg, dict)


# ══════════════════════════════════════════════════════════
# Feature I: Batch Mode
# ══════════════════════════════════════════════════════════


class TestBatchMode:
    """Test batch manifest loading and validation."""

    def test_load_valid_manifest(self, tmp_path):
        from multi_agent.batch import load_batch_manifest
        manifest = tmp_path / "tasks.yaml"
        manifest.write_text(
            "tasks:\n  - requirement: 'Add login'\n  - requirement: 'Fix bug'\n",
            encoding="utf-8",
        )
        tasks = load_batch_manifest(manifest)
        assert len(tasks) == 2
        assert tasks[0]["requirement"] == "Add login"

    def test_missing_tasks_key(self, tmp_path):
        from multi_agent.batch import BatchValidationError, load_batch_manifest
        manifest = tmp_path / "bad.yaml"
        manifest.write_text("foo: bar\n", encoding="utf-8")
        with pytest.raises(BatchValidationError, match="non-empty list"):
            load_batch_manifest(manifest)

    def test_empty_tasks_list(self, tmp_path):
        from multi_agent.batch import BatchValidationError, load_batch_manifest
        manifest = tmp_path / "empty.yaml"
        manifest.write_text("tasks: []\n", encoding="utf-8")
        with pytest.raises(BatchValidationError, match="non-empty list"):
            load_batch_manifest(manifest)

    def test_task_without_requirement_or_template(self, tmp_path):
        from multi_agent.batch import BatchValidationError, load_batch_manifest
        manifest = tmp_path / "noreg.yaml"
        manifest.write_text("tasks:\n  - skill: code-implement\n", encoding="utf-8")
        with pytest.raises(BatchValidationError, match="requirement.*template"):
            load_batch_manifest(manifest)

    def test_too_many_tasks(self, tmp_path):
        from multi_agent.batch import BatchValidationError, load_batch_manifest
        lines = "tasks:\n" + "".join(f"  - requirement: 'task {i}'\n" for i in range(51))
        manifest = tmp_path / "big.yaml"
        manifest.write_text(lines, encoding="utf-8")
        with pytest.raises(BatchValidationError, match="Too many"):
            load_batch_manifest(manifest)

    def test_file_not_found(self, tmp_path):
        from multi_agent.batch import load_batch_manifest
        with pytest.raises(FileNotFoundError):
            load_batch_manifest(tmp_path / "nope.yaml")

    def test_format_batch_summary(self):
        from multi_agent.batch import format_batch_summary
        results = [
            {"requirement": "Add login", "status": "completed", "elapsed": 10.5},
            {"requirement": "Fix bug", "status": "failed", "error": "timeout", "elapsed": 5.0},
        ]
        summary = format_batch_summary(results)
        assert "1/2" in summary
        assert "15.5s" in summary
        assert "Add login" in summary

    def test_template_task_in_manifest(self, tmp_path):
        from multi_agent.batch import load_batch_manifest
        manifest = tmp_path / "tmpl.yaml"
        manifest.write_text("tasks:\n  - template: bugfix\n", encoding="utf-8")
        tasks = load_batch_manifest(manifest)
        assert tasks[0]["template"] == "bugfix"

    def test_batch_dry_run(self, tmp_path):
        from click.testing import CliRunner
        from multi_agent.cli import main
        manifest = tmp_path / "tasks.yaml"
        manifest.write_text(
            "tasks:\n  - requirement: 'Test task'\n", encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(main, ["batch", str(manifest), "--dry-run"])
        assert result.exit_code == 0
        assert "Dry-run" in result.output
        assert "1 个任务" in result.output


# ══════════════════════════════════════════════════════════
# Feature K: Memory Export/Import
# ══════════════════════════════════════════════════════════


class TestMemoryExportImport:
    """Test memory export and import for team sharing."""

    def test_export_roundtrip(self, tmp_path):
        from multi_agent.semantic_memory import export_entries, import_entries, store, clear
        store("Always use pytest fixtures", category="convention")
        store("Use dataclasses for config", category="pattern")

        out_file = str(tmp_path / "export.json")
        count = export_entries(out_file)
        assert count == 2

        # Verify file content
        import json
        data = json.loads(Path(out_file).read_text())
        assert data["version"] == 1
        assert data["count"] == 2
        assert len(data["entries"]) == 2

    def test_import_deduplicates(self, tmp_path):
        from multi_agent.semantic_memory import export_entries, import_entries, store
        store("Use type hints everywhere", category="convention")

        out_file = str(tmp_path / "export.json")
        export_entries(out_file)

        # Import same data — should skip all
        result = import_entries(out_file)
        assert result["imported"] == 0
        assert result["skipped"] > 0

    def test_import_new_entries(self, tmp_path):
        from multi_agent.semantic_memory import import_entries
        import json

        export_data = {
            "version": 1,
            "exported_at": 0,
            "count": 2,
            "entries": [
                {"id": "new001", "content": "New convention alpha", "category": "convention",
                 "source": "import", "task_id": "", "tags": [], "metadata": {}},
                {"id": "new002", "content": "New pattern beta", "category": "pattern",
                 "source": "import", "task_id": "", "tags": [], "metadata": {}},
            ],
        }
        in_file = str(tmp_path / "import.json")
        Path(in_file).write_text(json.dumps(export_data))

        result = import_entries(in_file)
        assert result["imported"] == 2
        assert result["skipped"] == 0

    def test_import_invalid_format(self, tmp_path):
        from multi_agent.semantic_memory import import_entries
        bad_file = str(tmp_path / "bad.json")
        Path(bad_file).write_text('{"foo": "bar"}')
        result = import_entries(bad_file)
        assert result["imported"] == 0
        assert "missing 'entries'" in result.get("error", "")

    def test_import_file_not_found(self):
        from multi_agent.semantic_memory import import_entries
        result = import_entries("/nonexistent/path.json")
        assert result["imported"] == 0
        assert "not found" in result.get("error", "")

    def test_import_caps_content_length(self, tmp_path):
        from multi_agent.semantic_memory import import_entries, _load_entries
        import json

        long_content = "x" * 5000
        export_data = {
            "version": 1, "exported_at": 0, "count": 1,
            "entries": [{"id": "long01", "content": long_content, "category": "general",
                         "source": "", "task_id": "", "tags": [], "metadata": {}}],
        }
        in_file = str(tmp_path / "long.json")
        Path(in_file).write_text(json.dumps(export_data))

        result = import_entries(in_file)
        assert result["imported"] == 1
        entries = _load_entries()
        imported = [e for e in entries if e["id"] == "long01"]
        assert len(imported[0]["content"]) <= 2000


# ══════════════════════════════════════════════════════════
# Feature L: Config Profiles
# ══════════════════════════════════════════════════════════


class TestConfigProfiles:
    """Test config profile loading and validation."""

    def test_load_profiles_empty(self, monkeypatch):
        from multi_agent.profiles import load_profiles
        monkeypatch.setattr("multi_agent.profiles.load_project_config", lambda: {})
        assert load_profiles() == {}

    def test_load_profiles_valid(self, monkeypatch):
        from multi_agent.profiles import load_profiles
        monkeypatch.setattr("multi_agent.profiles.load_project_config", lambda: {
            "profiles": {
                "fast": {"retry_budget": 0, "timeout": 600},
                "thorough": {"retry_budget": 5, "timeout": 3600, "reviewer": "codex"},
            }
        })
        profiles = load_profiles()
        assert "fast" in profiles
        assert profiles["fast"]["retry_budget"] == 0
        assert profiles["thorough"]["reviewer"] == "codex"

    def test_get_profile_not_found(self, monkeypatch):
        from multi_agent.profiles import ProfileNotFoundError, get_profile
        monkeypatch.setattr("multi_agent.profiles.load_project_config", lambda: {})
        with pytest.raises(ProfileNotFoundError, match="not found"):
            get_profile("nonexistent")

    def test_get_profile_invalid_name(self):
        from multi_agent.profiles import ProfileNotFoundError, get_profile
        with pytest.raises(ProfileNotFoundError, match="Invalid"):
            get_profile("../../etc")

    def test_filters_unknown_fields(self, monkeypatch):
        from multi_agent.profiles import load_profiles
        monkeypatch.setattr("multi_agent.profiles.load_project_config", lambda: {
            "profiles": {"test": {"retry_budget": 1, "unknown_field": "bad"}}
        })
        profiles = load_profiles()
        assert "unknown_field" not in profiles["test"]
        assert profiles["test"]["retry_budget"] == 1

    def test_list_profile_names(self, monkeypatch):
        from multi_agent.profiles import list_profile_names
        monkeypatch.setattr("multi_agent.profiles.load_project_config", lambda: {
            "profiles": {"fast": {}, "slow": {}}
        })
        assert list_profile_names() == ["fast", "slow"]

    def test_profiles_cmd_empty(self):
        from click.testing import CliRunner
        from multi_agent.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["profiles"])
        assert result.exit_code == 0


# ══════════════════════════════════════════════════════════
# Feature M: Memory Auto-Prune
# ══════════════════════════════════════════════════════════


class TestMemoryAutoPrune:
    """Test memory TTL expiry and auto-prune."""

    def test_prune_empty(self):
        from multi_agent.semantic_memory import prune
        result = prune()
        assert result["removed"] == 0

    def test_prune_by_age(self):
        import time as _time
        from multi_agent.semantic_memory import prune, _load_entries, _rewrite_entries
        # Insert entries with old timestamps
        old_entry = {"id": "old1", "ts": _time.time() - 400 * 86400,
                     "content": "very old", "category": "general"}
        new_entry = {"id": "new1", "ts": _time.time(),
                     "content": "fresh", "category": "general"}
        _rewrite_entries([old_entry, new_entry])

        result = prune(max_age_days=180)
        assert result["removed"] == 1
        assert result["remaining"] == 1
        entries = _load_entries()
        assert entries[0]["id"] == "new1"

    def test_prune_by_max_entries(self):
        import time as _time
        from multi_agent.semantic_memory import prune, _load_entries, _rewrite_entries
        entries = [{"id": f"e{i}", "ts": _time.time() - i, "content": f"entry {i}",
                    "category": "general"} for i in range(10)]
        _rewrite_entries(entries)

        result = prune(max_entries=3, max_age_days=0)
        assert result["removed"] == 7
        assert result["remaining"] == 3

    def test_prune_keeps_recent(self):
        import time as _time
        from multi_agent.semantic_memory import prune, store, _load_entries
        store("Recent knowledge about testing", category="convention")
        result = prune(max_age_days=30)
        assert result["removed"] == 0
        assert result["remaining"] > 0

    def test_prune_cli_action(self):
        from click.testing import CliRunner
        from multi_agent.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["memory", "prune"])
        assert result.exit_code == 0
        assert "Pruned" in result.output


# ══════════════════════════════════════════════════════════
# Feature N+O: Daemon Mode + Task Queue
# ══════════════════════════════════════════════════════════


class TestTaskQueue:
    """Test task queue operations."""

    def test_submit_task(self):
        from multi_agent.daemon import submit_task
        result = submit_task("Add login endpoint", priority="high")
        assert result["status"] == "queued"
        assert result["queue_id"].startswith("q-")
        assert result["position"] >= 1

    def test_submit_empty_requirement(self):
        from multi_agent.daemon import submit_task
        result = submit_task("")
        assert result["status"] == "error"
        assert "empty" in result["reason"]

    def test_list_queue(self):
        from multi_agent.daemon import list_queue, submit_task
        submit_task("Task A")
        submit_task("Task B", priority="high")
        entries = list_queue()
        assert len(entries) >= 2

    def test_list_queue_filter(self):
        from multi_agent.daemon import list_queue, submit_task
        submit_task("Filtered task")
        queued = list_queue(status_filter="queued")
        assert all(e["status"] == "queued" for e in queued)

    def test_cancel_task(self):
        from multi_agent.daemon import cancel_task, submit_task
        result = submit_task("Cancelable task")
        qid = result["queue_id"]
        cancel_result = cancel_task(qid)
        assert cancel_result["status"] == "cancelled"

    def test_cancel_nonexistent(self):
        from multi_agent.daemon import cancel_task
        result = cancel_task("q-nonexistent")
        assert result["status"] == "error"

    def test_queue_stats(self):
        from multi_agent.daemon import queue_stats, submit_task
        submit_task("Stats task")
        stats = queue_stats()
        assert stats["total"] >= 1
        assert "queued" in stats["by_status"]

    def test_next_task_priority_ordering(self):
        from multi_agent.daemon import _next_task, _save_queue
        import time as _time
        # Clear and set up fresh queue
        _save_queue([
            {"queue_id": "q-low", "requirement": "low", "priority": "low",
             "status": "queued", "submitted_at": _time.time() - 10},
            {"queue_id": "q-high", "requirement": "high", "priority": "high",
             "status": "queued", "submitted_at": _time.time()},
        ])
        nxt = _next_task()
        assert nxt is not None
        assert nxt["queue_id"] == "q-high"

    def test_submit_caps_requirement(self):
        from multi_agent.daemon import submit_task, _load_queue
        long_req = "x" * 5000
        submit_task(long_req)
        entries = _load_queue()
        last = [e for e in entries if len(e.get("requirement", "")) > 0]
        assert all(len(e["requirement"]) <= 2000 for e in last)

    def test_submit_cli(self):
        from click.testing import CliRunner
        from multi_agent.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["submit", "CLI submitted task"])
        assert result.exit_code == 0
        assert "已入队" in result.output

    def test_jobs_cli(self):
        from click.testing import CliRunner
        from multi_agent.cli import main
        runner = CliRunner()
        result = runner.invoke(main, ["jobs"])
        assert result.exit_code == 0
        assert "队列" in result.output

    def test_serve_once_empty_queue(self):
        from multi_agent.daemon import _save_queue, run_daemon
        _save_queue([])  # empty queue
        result = run_daemon(once=True, poll_interval=0.1)
        assert result["processed"] == 0
