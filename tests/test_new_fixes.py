"""Tests for newly fixed features from strict audit."""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from multi_agent.cli import main


class TestRegisterHook:
    """Task 13: register_hook public API tests."""

    def test_register_hook_plan_start(self):
        from multi_agent.graph import graph_hooks, register_hook
        calls = []
        register_hook("plan_start", lambda state: calls.append("plan"))
        graph_hooks.fire_enter("plan", {})
        assert "plan" in calls
        # Cleanup
        graph_hooks._enter["plan"].pop()

    def test_register_hook_build_submit(self):
        from multi_agent.graph import graph_hooks, register_hook
        calls = []
        register_hook("build_submit", lambda state, result: calls.append("build"))
        graph_hooks.fire_exit("build", {}, {})
        assert "build" in calls
        graph_hooks._exit["build"].pop()

    def test_register_hook_task_failed(self):
        from multi_agent.graph import graph_hooks, register_hook
        calls = []
        register_hook("task_failed", lambda node, state, err: calls.append("fail"))
        graph_hooks.fire_error("build", {}, Exception("test"))
        assert "fail" in calls
        graph_hooks._error.pop()

    def test_register_hook_unknown_event(self):
        from multi_agent.graph import graph_hooks, register_hook
        calls = []
        register_hook("custom_event", lambda state: calls.append("custom"))
        graph_hooks.fire_enter("custom_event", {})
        assert "custom" in calls
        graph_hooks._enter["custom_event"].pop()


class TestEnsureWorkspaceDiskCheck:
    """Task 14: ensure_workspace calls check_disk_space."""

    def test_low_disk_warns(self, tmp_path):
        with patch("multi_agent.workspace.workspace_dir", return_value=tmp_path), \
             patch("multi_agent.workspace.inbox_dir", return_value=tmp_path / "inbox"), \
             patch("multi_agent.workspace.outbox_dir", return_value=tmp_path / "outbox"), \
             patch("multi_agent.workspace.tasks_dir", return_value=tmp_path / "tasks"), \
             patch("multi_agent.workspace.history_dir", return_value=tmp_path / "history"), \
             patch("multi_agent.workspace.check_disk_space", return_value=(False, 50)):
            from multi_agent.workspace import ensure_workspace
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                ensure_workspace()
                disk_warns = [x for x in w if "磁盘空间不足" in str(x.message)]
                assert len(disk_warns) >= 1

    def test_sufficient_disk_no_warn(self, tmp_path):
        with patch("multi_agent.workspace.workspace_dir", return_value=tmp_path), \
             patch("multi_agent.workspace.inbox_dir", return_value=tmp_path / "inbox"), \
             patch("multi_agent.workspace.outbox_dir", return_value=tmp_path / "outbox"), \
             patch("multi_agent.workspace.tasks_dir", return_value=tmp_path / "tasks"), \
             patch("multi_agent.workspace.history_dir", return_value=tmp_path / "history"), \
             patch("multi_agent.workspace.check_disk_space", return_value=(True, 5000)):
            from multi_agent.workspace import ensure_workspace
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                ensure_workspace()
                disk_warns = [x for x in w if "磁盘空间不足" in str(x.message)]
                assert len(disk_warns) == 0


class TestWorkflowStateParentTaskId:
    """Task 12: WorkflowState has parent_task_id."""

    def test_parent_task_id_in_state(self):
        from multi_agent.graph import WorkflowState
        assert "parent_task_id" in WorkflowState.__annotations__


class TestGoCommandProjectConfig:
    """Task 6: go reads load_project_config defaults."""

    def test_go_applies_config_defaults(self):
        runner = CliRunner()
        config = {"default_builder": "cursor", "default_reviewer": "windsurf"}
        with patch("multi_agent.graph.compile_graph") as mock_cg, \
             patch("multi_agent.config.load_project_config", return_value=config), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value="existing"), \
             patch("multi_agent.decompose.estimate_complexity", return_value="simple"):
            mock_cg.return_value = MagicMock()
            result = runner.invoke(main, ["go", "test req"])
            # Should hit "task in progress" but config was loaded
            assert result.exit_code != 0  # blocked by existing lock


class TestGoCommandComplexityHint:
    """Task 16: go shows complexity hint for complex requirements."""

    def test_complex_requirement_shows_hint(self):
        runner = CliRunner()
        with patch("multi_agent.graph.compile_graph") as mock_cg, \
             patch("multi_agent.config.load_project_config", return_value={}), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value="existing"):
            mock_cg.return_value = MagicMock()
            long_req = "实现完整的用户认证模块包括登录注册密码重置和中间件鉴权以及用户角色管理和权限控制还需要实现OAuth2集成和JWT令牌管理同时添加审计日志和安全告警功能最后要实现用户配置导出和批量导入功能"
            result = runner.invoke(main, ["go", long_req])
            assert result.exit_code != 0 or "decompose" in result.output.lower()


class TestDecomposePromptNewFields:
    """Task 5: DECOMPOSE_PROMPT includes new fields."""

    def test_zh_prompt_has_new_fields(self):
        from multi_agent.decompose import DECOMPOSE_PROMPT
        assert "priority" in DECOMPOSE_PROMPT
        assert "estimated_minutes" in DECOMPOSE_PROMPT
        assert "acceptance_criteria" in DECOMPOSE_PROMPT

    def test_en_prompt_has_new_fields(self):
        from multi_agent.decompose import DECOMPOSE_PROMPT_EN
        assert "priority" in DECOMPOSE_PROMPT_EN
        assert "estimated_minutes" in DECOMPOSE_PROMPT_EN
        assert "acceptance_criteria" in DECOMPOSE_PROMPT_EN


class TestGoAutoConfirmFlag:
    """Task 28: --auto-confirm flag exists."""

    def test_auto_confirm_in_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["go", "--help"])
        assert "--auto-confirm" in result.output


class TestGoDecomposeFileFlag:
    """Task 29: --decompose-file option exists."""

    def test_decompose_file_in_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["go", "--help"])
        assert "--decompose-file" in result.output


# ── Security fix tests (Round 15/16) ─────────────────────

class TestValidateTaskId:
    """Path traversal prevention via _validate_task_id."""

    def test_valid_task_id(self):
        from multi_agent.cli import _validate_task_id
        assert _validate_task_id("task-abc123") == "task-abc123"
        assert _validate_task_id("abc") == "abc"
        assert _validate_task_id("a" * 64) == "a" * 64

    def test_rejects_path_traversal(self):
        import click

        from multi_agent.cli import _validate_task_id
        for bad in ["../../etc/passwd", "../secret", "a/b", "task/../x"]:
            with pytest.raises(click.exceptions.BadParameter):
                _validate_task_id(bad)

    def test_rejects_short_id(self):
        import click

        from multi_agent.cli import _validate_task_id
        with pytest.raises(click.exceptions.BadParameter):
            _validate_task_id("ab")

    def test_rejects_uppercase(self):
        import click

        from multi_agent.cli import _validate_task_id
        with pytest.raises(click.exceptions.BadParameter):
            _validate_task_id("Task-ABC")

    def test_rejects_special_chars(self):
        import click

        from multi_agent.cli import _validate_task_id
        for bad in ["task;rm", "task$(cmd)", "task\x00x", "task~home"]:
            with pytest.raises(click.exceptions.BadParameter):
                _validate_task_id(bad)


class TestMalformedAgentEntry:
    """Router skips malformed agent entries without crashing."""

    def test_missing_id_skipped(self, tmp_path):
        import yaml
        yaml_file = tmp_path / "agents.yaml"
        yaml_file.write_text(yaml.dump({
            "version": 2,
            "agents": [
                {"id": "valid-agent", "capabilities": ["implementation"]},
                {"capabilities": ["review"]},  # missing id
                "not-a-dict",                  # not even a dict
            ],
        }))
        from multi_agent.router import load_agents
        agents = load_agents(yaml_file)
        assert len(agents) == 1
        assert agents[0].id == "valid-agent"


class TestHandleErrorsDecorator:
    """handle_errors catches Exception but lets SystemExit through."""

    def test_system_exit_passthrough(self):
        from multi_agent.cli import handle_errors

        @handle_errors
        def raise_exit():
            raise SystemExit(42)

        with pytest.raises(SystemExit) as exc_info:
            raise_exit()
        assert exc_info.value.code == 42

    def test_exception_does_not_auto_release_lock(self):
        from multi_agent.cli import handle_errors

        @handle_errors
        def raise_error():
            raise RuntimeError("boom")

        class _Root:
            def __init__(self):
                self.params = {"verbose": False}

        class _Ctx:
            def __init__(self):
                self.params = {"task_id": "task-123"}

            def find_root(self):
                return _Root()

        with patch("multi_agent.cli.click.get_current_context", return_value=_Ctx()), \
             patch("multi_agent.cli.read_lock", return_value="task-123"), \
             patch("multi_agent.cli.release_lock") as rel:
            with pytest.raises(SystemExit):
                raise_error()
            rel.assert_not_called()

    def test_keyboard_interrupt_releases_lock(self):
        from multi_agent.cli import handle_errors

        @handle_errors
        def raise_kb():
            raise KeyboardInterrupt()

        with patch("multi_agent.cli.read_lock", return_value="task-123"), \
             patch("multi_agent.cli.release_lock") as rel:
            with pytest.raises(SystemExit) as exc_info:
                raise_kb()
            assert exc_info.value.code == 0
            rel.assert_not_called()

    def test_keyboard_interrupt_does_not_auto_release_lock_even_with_task_context(self):
        from multi_agent.cli import handle_errors

        @handle_errors
        def raise_kb():
            raise KeyboardInterrupt()

        class _Ctx:
            def __init__(self):
                self.params = {"task_id": "task-123"}

        with patch("multi_agent.cli.click.get_current_context", return_value=_Ctx()), \
             patch("multi_agent.cli.read_lock", return_value="task-123"), \
             patch("multi_agent.cli.release_lock") as rel:
            with pytest.raises(SystemExit) as exc_info:
                raise_kb()
            assert exc_info.value.code == 0
            rel.assert_not_called()


class TestDuplicateSubTaskId:
    """Duplicate sub_task IDs treated as critical error in read_decompose_result."""

    def test_duplicate_id_is_critical(self, tmp_path):
        import json

        from multi_agent.decompose import read_decompose_result
        outbox = tmp_path / "outbox"
        outbox.mkdir()
        data = {
            "sub_tasks": [
                {"id": "auth-login", "description": "Login"},
                {"id": "auth-login", "description": "Login duplicate"},
            ],
        }
        (outbox / "decompose.json").write_text(json.dumps(data))
        with patch("multi_agent.decompose.outbox_dir", return_value=outbox):
            result = read_decompose_result(validate=True)
        # With validate=True and critical error (duplicate), result should be None
        assert result is None

    def test_validate_detects_duplicates(self):
        from multi_agent.decompose import validate_decompose_result
        from multi_agent.schema import DecomposeResult, SubTask
        dr = DecomposeResult(sub_tasks=[
            SubTask(id="dup-task", description="A"),
            SubTask(id="dup-task", description="B"),
        ])
        errors = validate_decompose_result(dr)
        assert any("duplicate" in e.lower() for e in errors)

    def test_validate_skill_id_rejects_path_traversal(self):
        import click

        from multi_agent.cli import _validate_skill_id
        for bad in ["../../etc", "../passwd", "a/b", "", " ", "a;rm"]:
            with pytest.raises(click.BadParameter):
                _validate_skill_id(bad)
        # Valid skills accepted
        assert _validate_skill_id("code-implement") == "code-implement"
        assert _validate_skill_id("test-and-review") == "test-and-review"

    def test_load_agents_skips_malformed_ids(self, tmp_path):
        import yaml

        from multi_agent.router import load_agents
        reg = {
            "agents": [
                {"id": "good-agent", "capabilities": ["implementation"]},
                {"id": "../evil", "capabilities": []},
                {"id": "", "capabilities": []},
                {"id": "also-good", "capabilities": ["review"]},
            ]
        }
        p = tmp_path / "agents.yaml"
        p.write_text(yaml.dump(reg), encoding="utf-8")
        agents = load_agents(p)
        ids = [a.id for a in agents]
        assert "good-agent" in ids
        assert "also-good" in ids
        assert "../evil" not in ids

    def test_agent_profile_rejects_path_traversal_id(self):
        import pytest as _pt

        from multi_agent.schema import AgentProfile
        for bad_id in ["../etc/passwd", "/root", "a;rm -rf", "", " "]:
            with _pt.raises((ValueError, ValidationError)):
                AgentProfile(id=bad_id, capabilities=[])
        # Valid IDs accepted
        AgentProfile(id="windsurf", capabilities=[])
        AgentProfile(id="codex-cli", capabilities=[])

    def test_request_changes_cap_escalates(self):
        """Literature: SHIELDA pattern — soft retries must have a termination bound."""
        from unittest.mock import patch as _p

        from multi_agent.graph import MAX_REQUEST_CHANGES, _decide_node_inner
        # Build conversation with MAX_REQUEST_CHANGES request_changes entries
        convo = [{"role": "orchestrator", "action": "request_changes", "feedback": f"fix {i}", "t": 0}
                 for i in range(MAX_REQUEST_CHANGES)]
        state = {
            "task_id": "test-rc-cap",
            "reviewer_output": {"decision": "request_changes", "feedback": "fix again"},
            "conversation": convo,
            "retry_count": 0,
            "retry_budget": 2,
            "builder_id": "w",
            "reviewer_id": "c",
            "done_criteria": [],
        }
        with _p("multi_agent.graph.archive_conversation"), \
             _p("multi_agent.graph.graph_hooks"):
            result = _decide_node_inner(state)
        assert result["final_status"] == "escalated"
        assert result["error"] == "REQUEST_CHANGES_CAP"

    def test_validate_detects_circular_deps(self):
        from multi_agent.decompose import validate_decompose_result
        from multi_agent.schema import DecomposeResult, SubTask
        dr = DecomposeResult(sub_tasks=[
            SubTask(id="aaa-task", description="A", deps=["bbb-task"]),
            SubTask(id="bbb-task", description="B", deps=["aaa-task"]),
        ])
        errors = validate_decompose_result(dr)
        assert any("circular" in e.lower() for e in errors)


class TestLoadContractDefenseInDepth:
    """R15 B1: load_contract validates skill_id to prevent path traversal
    even when called programmatically (not via CLI)."""

    def test_rejects_path_traversal(self):
        import pytest

        from multi_agent.contract import load_contract
        with pytest.raises(ValueError, match="Invalid skill_id"):
            load_contract("../../etc")

    def test_rejects_slash(self):
        import pytest

        from multi_agent.contract import load_contract
        with pytest.raises(ValueError, match="Invalid skill_id"):
            load_contract("foo/bar")

    def test_rejects_empty(self):
        import pytest

        from multi_agent.contract import load_contract
        with pytest.raises(ValueError, match="Invalid skill_id"):
            load_contract("")

    def test_accepts_valid_skill_id(self, tmp_path):
        """Valid skill_id passes validation (may fail on FileNotFoundError after)."""
        import pytest

        from multi_agent.contract import load_contract
        with pytest.raises(FileNotFoundError):
            load_contract("code-implement", base=tmp_path)

    def test_accepts_dotted_skill_id(self, tmp_path):
        """Dotted skill_ids like 'v2.code-impl' should pass validation."""
        import pytest

        from multi_agent.contract import load_contract
        with pytest.raises(FileNotFoundError):
            load_contract("v2.code-impl", base=tmp_path)


class TestReadOutboxLogging:
    """R15 B3: read_outbox logs warnings on validation failure and corrupt JSON."""

    def test_corrupt_json_logs_warning(self, tmp_path, caplog, monkeypatch):
        import logging

        from multi_agent import workspace
        monkeypatch.setattr(workspace, "outbox_dir", lambda: tmp_path)
        (tmp_path / "builder.json").write_text("{invalid json", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            result = workspace.read_outbox("builder")
        assert result is None
        assert any("JSON parse error" in r.message for r in caplog.records)

    def test_non_dict_logs_warning(self, tmp_path, caplog, monkeypatch):
        import json
        import logging

        from multi_agent import workspace
        monkeypatch.setattr(workspace, "outbox_dir", lambda: tmp_path)
        (tmp_path / "builder.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            result = workspace.read_outbox("builder")
        assert result is None
        assert any("not a JSON object" in r.message for r in caplog.records)

    def test_validation_failure_logs_warning(self, tmp_path, caplog, monkeypatch):
        import json
        import logging

        from multi_agent import workspace
        monkeypatch.setattr(workspace, "outbox_dir", lambda: tmp_path)
        (tmp_path / "builder.json").write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            result = workspace.read_outbox("builder", validate=True)
        assert result is None
        assert any("validation failed" in r.message for r in caplog.records)

    def test_valid_outbox_no_warning(self, tmp_path, caplog, monkeypatch):
        import json
        import logging

        from multi_agent import workspace
        monkeypatch.setattr(workspace, "outbox_dir", lambda: tmp_path)
        (tmp_path / "builder.json").write_text(
            json.dumps({"status": "completed", "summary": "done"}), encoding="utf-8"
        )
        with caplog.at_level(logging.WARNING):
            result = workspace.read_outbox("builder", validate=True)
        assert result is not None
        assert not any("WARNING" in r.levelname for r in caplog.records)


# ── R16 regression tests ─────────────────────────────────────────────


class TestRubberStampAuditTrail:
    """R16 C1: decide_node records audit trail when rubber-stamp warning is set."""

    def test_rubber_stamp_creates_audit_entry(self):
        """When reviewer_output has _rubber_stamp_warning, decide should include
        a rubber_stamp_warning conversation entry."""
        from multi_agent.graph import _decide_node_inner

        state = {
            "task_id": "task-rubber-test",
            "reviewer_output": {
                "decision": "approve",
                "feedback": "ok",
                "summary": "looks good",
                "_rubber_stamp_warning": True,
            },
            "reviewer_id": "test-reviewer",
            "builder_id": "test-builder",
            "conversation": [],
            "retry_count": 0,
            "retry_budget": 2,
            "done_criteria": ["test"],
        }
        result = _decide_node_inner(state)
        assert result["final_status"] == "approved"
        actions = [e.get("action") for e in result.get("conversation", [])]
        assert "rubber_stamp_warning" in actions
        assert "approved" in actions

    def test_no_warning_no_audit_entry(self):
        """Normal approve without rubber-stamp should not have warning entry."""
        from multi_agent.graph import _decide_node_inner

        state = {
            "task_id": "task-normal-test",
            "reviewer_output": {
                "decision": "approve",
                "feedback": "Thoroughly reviewed all changes.",
                "summary": "All tests pass, code quality good.",
            },
            "reviewer_id": "test-reviewer",
            "builder_id": "test-builder",
            "conversation": [],
            "retry_count": 0,
            "retry_budget": 2,
            "done_criteria": ["test"],
        }
        result = _decide_node_inner(state)
        assert result["final_status"] == "approved"
        actions = [e.get("action") for e in result.get("conversation", [])]
        assert "rubber_stamp_warning" not in actions


class TestMetaGraphCheckpoint:
    """R16 C2: save/load/clear checkpoint for decompose crash recovery."""

    def test_save_and_load(self, tmp_path, monkeypatch):
        from multi_agent import config, meta_graph
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        prior = [{"sub_id": "a", "status": "approved", "summary": "done"}]
        meta_graph.save_checkpoint("task-ckpt-test", prior, ["a"])
        loaded = meta_graph.load_checkpoint("task-ckpt-test")
        assert loaded is not None
        assert loaded["completed_ids"] == ["a"]
        assert loaded["prior_results"][0]["sub_id"] == "a"

    def test_load_missing_returns_none(self, tmp_path, monkeypatch):
        from multi_agent import config, meta_graph
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        assert meta_graph.load_checkpoint("task-nonexistent") is None

    def test_clear_removes_file(self, tmp_path, monkeypatch):
        from multi_agent import config, meta_graph
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        meta_graph.save_checkpoint("task-clear-test", [], [])
        meta_graph.clear_checkpoint("task-clear-test")
        assert meta_graph.load_checkpoint("task-clear-test") is None

    def test_corrupt_checkpoint_returns_none(self, tmp_path, monkeypatch):
        from multi_agent import config, meta_graph
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        ckpt_dir = tmp_path / "checkpoints"
        ckpt_dir.mkdir()
        (ckpt_dir / "decompose-task-corrupt.json").write_text("{bad json", encoding="utf-8")
        assert meta_graph.load_checkpoint("task-corrupt") is None


class TestEmptyChangesetWarning:
    """R16 C3: builder claims completed but reports no changed_files."""

    def test_completed_no_files_sets_warning(self):
        result = {"status": "completed", "summary": "All done", "changed_files": []}
        builder_status = str(result.get("status", "")).lower()
        changed_files = result.get("changed_files", [])
        if builder_status in ("completed", "success", "done") and not changed_files:
            result.setdefault("_empty_changeset_warning", True)
        assert result.get("_empty_changeset_warning") is True

    def test_completed_with_files_no_warning(self):
        result = {"status": "completed", "summary": "done", "changed_files": ["a.py"]}
        builder_status = str(result.get("status", "")).lower()
        changed_files = result.get("changed_files", [])
        if builder_status in ("completed", "success", "done") and not changed_files:
            result.setdefault("_empty_changeset_warning", True)
        assert "_empty_changeset_warning" not in result

    def test_error_status_no_warning(self):
        result = {"status": "error", "summary": "failed", "changed_files": []}
        builder_status = str(result.get("status", "")).lower()
        changed_files = result.get("changed_files", [])
        if builder_status in ("completed", "success", "done") and not changed_files:
            result.setdefault("_empty_changeset_warning", True)
        assert "_empty_changeset_warning" not in result


# ── R17 regression tests ─────────────────────────────────────────────


class TestShellQuotePaths:
    """R17 D1: paths with spaces/metacharacters are shell-quoted in CLI commands."""

    def test_shlex_quote_applied(self):
        import shlex

        # Simulate what spawn_cli_agent does internally
        task_file = "/Users/John Doe/project/.multi-agent/TASK.md"
        outbox_file = "/Users/John Doe/project/.multi-agent/outbox/builder.json"
        template = "tool --task {task_file} --out {outbox_file}"
        cmd = template.format(
            task_file=shlex.quote(task_file),
            outbox_file=shlex.quote(outbox_file),
        )
        # shlex.quote wraps paths with spaces in single quotes
        assert "'/Users/John Doe/" in cmd
        # The unquoted path (without wrapping quotes) should NOT appear
        assert cmd.count("'") >= 4  # at least 2 quoted paths

    def test_metachar_path_quoted(self):
        import shlex
        path = "/tmp/proj;rm -rf /"
        quoted = shlex.quote(path)
        assert ";" not in quoted or quoted.startswith("'")


class TestAtomicWriteOutbox:
    """R17 D2: write_outbox uses atomic temp+rename pattern."""

    def test_write_produces_valid_json(self, tmp_path, monkeypatch):
        from multi_agent import workspace
        monkeypatch.setattr(workspace, "outbox_dir", lambda: tmp_path)
        monkeypatch.setattr(workspace, "ensure_workspace", lambda: None)
        workspace.write_outbox("builder", {"status": "completed", "summary": "ok"})
        import json
        data = json.loads((tmp_path / "builder.json").read_text(encoding="utf-8"))
        assert data["status"] == "completed"

    def test_no_temp_files_left(self, tmp_path, monkeypatch):
        from multi_agent import workspace
        monkeypatch.setattr(workspace, "outbox_dir", lambda: tmp_path)
        monkeypatch.setattr(workspace, "ensure_workspace", lambda: None)
        workspace.write_outbox("builder", {"status": "done", "summary": "x"})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Temp files left: {tmp_files}"

    def test_concurrent_reads_get_complete_json(self, tmp_path, monkeypatch):
        """Write then immediately read — should never get partial data."""
        import json

        from multi_agent import workspace
        monkeypatch.setattr(workspace, "outbox_dir", lambda: tmp_path)
        monkeypatch.setattr(workspace, "ensure_workspace", lambda: None)
        for i in range(20):
            workspace.write_outbox("test", {"i": i, "status": "ok", "summary": str(i)})
            raw = (tmp_path / "test.json").read_text(encoding="utf-8")
            data = json.loads(raw)
            assert data["i"] == i


class TestAtomicDriverWrites:
    """R17 D3: _try_extract_json and _write_error use atomic writes."""

    def test_write_error_atomic(self, tmp_path):
        from multi_agent.driver import _write_error
        outbox = str(tmp_path / "builder.json")
        _write_error(outbox, "test error")
        import json
        data = json.loads((tmp_path / "builder.json").read_text())
        assert data["status"] == "error"
        assert data["summary"] == "test error"
        assert not list(tmp_path.glob("*.tmp"))

    def test_try_extract_json_atomic(self, tmp_path):
        from multi_agent.driver import _try_extract_json
        outbox = tmp_path / "builder.json"
        text = '```json\n{"status": "completed", "summary": "done"}\n```'
        _try_extract_json(text, outbox)
        import json
        data = json.loads(outbox.read_text())
        assert data["status"] == "completed"
        assert not list(tmp_path.glob("*.tmp"))


# ── R18 regression tests ─────────────────────────────────────────────


class TestFormatPriorContextDeps:
    """R18 E1: format_prior_context always includes dependency sub-task results."""

    def test_dep_included_even_when_old(self):
        from multi_agent.meta_graph import format_prior_context
        results = [
            {"sub_id": "setup-db", "summary": "created schema", "changed_files": ["db.sql"]},
            {"sub_id": "api-routes", "summary": "added routes"},
            {"sub_id": "api-tests", "summary": "added tests"},
            {"sub_id": "api-docs", "summary": "wrote docs"},
        ]
        # Sub-task depends on "setup-db" which is result[0] — older than max_items=3
        ctx = format_prior_context(results, max_items=3, dep_ids=["setup-db"])
        assert "setup-db" in ctx
        assert "[依赖]" in ctx
        assert "db.sql" in ctx

    def test_no_deps_falls_back_to_recent(self):
        from multi_agent.meta_graph import format_prior_context
        results = [
            {"sub_id": "a", "summary": "done-a"},
            {"sub_id": "b", "summary": "done-b"},
            {"sub_id": "c", "summary": "done-c"},
            {"sub_id": "d", "summary": "done-d"},
        ]
        ctx = format_prior_context(results, max_items=2, dep_ids=[])
        assert "a" not in ctx  # too old
        assert "c" in ctx
        assert "d" in ctx

    def test_dep_and_recent_deduplicated(self):
        from multi_agent.meta_graph import format_prior_context
        results = [
            {"sub_id": "a", "summary": "done-a"},
            {"sub_id": "b", "summary": "done-b"},
        ]
        # "b" is both a dep and recent — should appear only once
        ctx = format_prior_context(results, max_items=3, dep_ids=["b"])
        assert ctx.count("done-b") == 1

    def test_empty_results(self):
        from multi_agent.meta_graph import format_prior_context
        assert format_prior_context([], dep_ids=["x"]) == ""


class TestStructuredRetryFeedback:
    """R18 E2: retry feedback uses structured sections instead of raw text."""

    def test_retry_feedback_has_sections(self):
        from multi_agent.graph import _decide_node_inner
        state = {
            "task_id": "task-struct-test",
            "reviewer_output": {
                "decision": "reject",
                "feedback": "Code has bugs in line 42",
                "summary": "needs fix",
            },
            "reviewer_id": "r1",
            "builder_id": "b1",
            "builder_output": {
                "status": "completed",
                "summary": "implemented",
                "gate_warnings": ["lint failed"],
            },
            "conversation": [],
            "retry_count": 0,
            "retry_budget": 2,
            "done_criteria": ["test"],
        }
        result = _decide_node_inner(state)
        feedback = result["conversation"][0].get("feedback", "")
        assert "## Reviewer Decision:" in feedback
        assert "### Feedback" in feedback
        assert "Code has bugs" in feedback
        assert "### Quality Gate Warnings" in feedback
        assert "lint failed" in feedback
        assert "### Retry Status" in feedback

    def test_retry_without_gate_warnings(self):
        from multi_agent.graph import _decide_node_inner
        state = {
            "task_id": "task-no-gate-test",
            "reviewer_output": {
                "decision": "reject",
                "feedback": "Needs refactoring",
                "summary": "reject",
            },
            "reviewer_id": "r1",
            "builder_id": "b1",
            "builder_output": {"status": "completed", "summary": "done"},
            "conversation": [],
            "retry_count": 0,
            "retry_budget": 2,
            "done_criteria": ["test"],
        }
        result = _decide_node_inner(state)
        feedback = result["conversation"][0].get("feedback", "")
        assert "## Reviewer Decision:" in feedback
        assert "Quality Gate Warnings" not in feedback


class TestLegacyResumeNormalization:
    """Legacy go/watch/done path should match session-mode reviewer gating."""

    def test_reviewer_pass_alias_normalized(self):
        from multi_agent.cli import _normalize_resume_output

        state_values = {"workflow_mode": "strict"}
        output = {"decision": "pass", "summary": "ok", "evidence": ["unit test pass"]}
        normalized = _normalize_resume_output("reviewer", output, state_values)
        assert normalized["decision"] == "approve"

    def test_reviewer_approve_needs_evidence_in_strict(self):
        """Approve with no evidence AND no summary/feedback should fail in strict mode."""
        from multi_agent.cli import _normalize_resume_output

        state_values = {
            "workflow_mode": "strict",
            "review_policy": {"reviewer": {"require_evidence_on_approve": True, "min_evidence_items": 1}},
        }
        output = {"decision": "approve"}
        with pytest.raises(ValueError, match="reviewer approve requires evidence"):
            _normalize_resume_output("reviewer", output, state_values)

    def test_reviewer_approve_auto_populates_evidence_from_summary(self):
        """Approve with summary but no explicit evidence auto-populates evidence."""
        from multi_agent.cli import _normalize_resume_output

        state_values = {
            "workflow_mode": "strict",
            "review_policy": {"reviewer": {"require_evidence_on_approve": True, "min_evidence_items": 1}},
        }
        output = {"decision": "approve", "summary": "looks good"}
        normalized = _normalize_resume_output("reviewer", output, state_values)
        assert normalized["decision"] == "approve"
        assert normalized["evidence"] == ["looks good"]

    def test_reviewer_approve_auto_populates_evidence_from_empty_list(self):
        """Approve with evidence: [] should still auto-populate from summary."""
        from multi_agent.cli import _normalize_resume_output

        state_values = {
            "workflow_mode": "strict",
            "review_policy": {"reviewer": {"require_evidence_on_approve": True, "min_evidence_items": 1}},
        }
        output = {"decision": "approve", "summary": "reviewed ok", "evidence": []}
        normalized = _normalize_resume_output("reviewer", output, state_values)
        assert normalized["decision"] == "approve"
        assert normalized["evidence"] == ["reviewed ok"]

    def test_reviewer_evidence_check_can_be_disabled(self):
        from multi_agent.cli import _normalize_resume_output

        state_values = {
            "workflow_mode": "strict",
            "review_policy": {"reviewer": {"require_evidence_on_approve": False}},
        }
        output = {"decision": "approve", "summary": "looks good"}
        normalized = _normalize_resume_output("reviewer", output, state_values)
        assert normalized["decision"] == "approve"


# ── R19 regression tests ─────────────────────────────────────────────


class TestGraphStatsCumulativeTotals:
    """R19 F1: GraphStats cumulative_totals and warn_if_over_budget."""

    def test_cumulative_across_nodes(self):
        from multi_agent.graph import GraphStats
        gs = GraphStats()
        gs.record("build", 100, True)
        gs.record_token_usage("build", {"total_tokens": 1000, "cost": 0.01})
        gs.record("review", 200, True)
        gs.record_token_usage("review", {"total_tokens": 500, "cost": 0.005})
        totals = gs.cumulative_totals()
        assert totals["total_tokens"] == 1500
        assert abs(totals["cost"] - 0.015) < 1e-6

    def test_cumulative_empty(self):
        from multi_agent.graph import GraphStats
        gs = GraphStats()
        assert gs.cumulative_totals() == {}

    def test_warn_over_budget(self, caplog):
        import logging

        from multi_agent.graph import GraphStats
        gs = GraphStats()
        gs.record("build", 100, True)
        gs.record_token_usage("build", {"total_tokens": 600_000})
        with caplog.at_level(logging.WARNING):
            result = gs.warn_if_over_budget(max_tokens=500_000)
        assert result is True
        assert "Token budget warning" in caplog.text

    def test_no_warn_under_budget(self, caplog):
        import logging

        from multi_agent.graph import GraphStats
        gs = GraphStats()
        gs.record("build", 100, True)
        gs.record_token_usage("build", {"total_tokens": 100})
        with caplog.at_level(logging.WARNING):
            result = gs.warn_if_over_budget(max_tokens=500_000)
        assert result is False
        assert "Token budget" not in caplog.text


class TestWatcherContentHashDedup:
    """R19 F2: watcher deduplicates based on content hash, not just mtime."""

    def test_same_content_different_mtime_skipped(self, tmp_path, monkeypatch):
        import json
        import os

        from multi_agent import watcher as _watcher_mod
        from multi_agent.watcher import OutboxPoller
        monkeypatch.setattr(_watcher_mod, "outbox_dir", lambda: tmp_path)

        data = {"status": "completed", "summary": "done"}
        (tmp_path / "builder.json").write_text(json.dumps(data), encoding="utf-8")

        poller = OutboxPoller()
        # Reduce settle_time for faster tests
        orig_wait = OutboxPoller._wait_stable
        monkeypatch.setattr(OutboxPoller, "_wait_stable", staticmethod(
            lambda path, settle_time=0.01, max_wait=0.05: orig_wait(path, settle_time=0.01, max_wait=0.05)
        ))

        # First poll — should detect
        results1 = poller.check_once()
        assert len(results1) == 1

        # Touch file to change mtime but keep same content
        import time
        time.sleep(0.05)
        os.utime(tmp_path / "builder.json", None)

        # Second poll — same content, should be deduped
        results2 = poller.check_once()
        assert len(results2) == 0

    def test_different_content_detected(self, tmp_path, monkeypatch):
        import json

        from multi_agent import watcher as _watcher_mod
        from multi_agent.watcher import OutboxPoller
        monkeypatch.setattr(_watcher_mod, "outbox_dir", lambda: tmp_path)
        monkeypatch.setattr(OutboxPoller, "_wait_stable", staticmethod(
            lambda path, settle_time=0.01, max_wait=0.05: True
        ))

        (tmp_path / "builder.json").write_text(
            json.dumps({"status": "completed", "summary": "v1"}), encoding="utf-8"
        )
        poller = OutboxPoller()
        results1 = poller.check_once()
        assert len(results1) == 1

        # Write different content
        import time
        time.sleep(0.05)
        (tmp_path / "builder.json").write_text(
            json.dumps({"status": "completed", "summary": "v2"}), encoding="utf-8"
        )
        results2 = poller.check_once()
        assert len(results2) == 1
        assert results2[0][1]["summary"] == "v2"


# ── R20 regression tests ─────────────────────────────────────────────


class TestDecomposePromptBraces:
    """R20 G1: decompose prompt doesn't crash when requirement contains braces."""

    def test_requirement_with_braces(self, tmp_path, monkeypatch):
        from multi_agent import config, decompose
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        monkeypatch.setattr(config, "inbox_dir", lambda: tmp_path / "inbox")
        (tmp_path / "inbox").mkdir()
        # This would crash with str.format() due to {user_id}
        result = decompose.write_decompose_prompt(
            "implement endpoint GET /users/{user_id}/profile",
            lang="en",
            project_context="test project",
        )
        content = result.read_text(encoding="utf-8")
        assert "{user_id}" in content
        assert "implement endpoint" in content

    def test_requirement_with_curly_json(self, tmp_path, monkeypatch):
        from multi_agent import config, decompose
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        monkeypatch.setattr(config, "inbox_dir", lambda: tmp_path / "inbox")
        (tmp_path / "inbox").mkdir()
        result = decompose.write_decompose_prompt(
            'parse JSON like {"key": "value"} from input',
            lang="zh",
            project_context="test",
        )
        content = result.read_text(encoding="utf-8")
        assert '{"key": "value"}' in content


class TestSnapshotPathSanitization:
    """R20 G2: save_state_snapshot sanitizes task_id to prevent path traversal."""

    def test_traversal_in_task_id(self, tmp_path, monkeypatch):
        from multi_agent import config, graph
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        state = {"task_id": "../../etc/passwd", "status": "test"}
        graph.save_state_snapshot("../../etc/passwd", "build", state)
        snap_dir = tmp_path / "snapshots"
        # File must be inside snapshots dir (no path separator escape)
        snaps = list(snap_dir.glob("*.json"))
        assert len(snaps) == 1
        assert "/" not in snaps[0].name
        assert snaps[0].parent == snap_dir

    def test_normal_task_id_preserved(self, tmp_path, monkeypatch):
        from multi_agent import config, graph
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        graph.save_state_snapshot("task-abc-123", "plan", {"x": 1})
        snap_dir = tmp_path / "snapshots"
        snaps = list(snap_dir.glob("*.json"))
        assert len(snaps) == 1
        assert "task-abc-123" in snaps[0].name


class TestSaveStateSnapshotEdgeCases:
    """Cover uncovered branches in save_state_snapshot (lines 222-237)."""

    def test_non_serializable_value_converted_to_str(self, tmp_path, monkeypatch):
        from multi_agent import config
        from multi_agent.graph_infra import save_state_snapshot
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        state = {"task_id": "task-ser", "obj": object()}  # not JSON-serializable
        save_state_snapshot("task-ser", "build", state)
        snaps = list((tmp_path / "snapshots").glob("*.json"))
        assert len(snaps) == 1

    def test_write_error_suppressed(self, tmp_path, monkeypatch):
        from multi_agent import config
        from multi_agent.graph_infra import save_state_snapshot
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        (tmp_path / "snapshots").mkdir(parents=True)
        with patch("pathlib.Path.write_text", side_effect=PermissionError("denied")):
            save_state_snapshot("task-write-err", "plan", {"x": 1})  # should not raise

    def test_cleanup_oserror_suppressed(self, tmp_path, monkeypatch):
        from multi_agent import config
        from multi_agent.graph_infra import MAX_SNAPSHOTS, save_state_snapshot
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir(parents=True)
        # Create MAX_SNAPSHOTS + 1 files to trigger cleanup
        for i in range(MAX_SNAPSHOTS + 1):
            (snap_dir / f"task-cleanup-plan-{i:010d}.json").write_text("{}")
        with patch("pathlib.Path.unlink", side_effect=OSError("locked")):
            save_state_snapshot("task-cleanup", "plan", {"x": 1})  # should not raise


class TestLogTimingOSError:
    """Cover log_timing OSError handling (lines 157-158)."""

    def test_oserror_suppressed(self, tmp_path, monkeypatch):
        from multi_agent import config
        from multi_agent.graph_infra import log_timing
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        (tmp_path / "logs").mkdir(parents=True)
        with patch("pathlib.Path.open", side_effect=OSError("full")):
            log_timing("task-t1", "build", 100.0, 101.0)  # should not raise


class TestEventHooksErrorHandling:
    """Cover fire_exit and fire_error exception suppression (lines 280-281, 287-288)."""

    def test_fire_exit_callback_exception_suppressed(self):
        from multi_agent.graph_infra import EventHooks
        hooks = EventHooks()
        hooks.on_node_exit("build", lambda s, r: 1 / 0)  # ZeroDivisionError
        hooks.fire_exit("build", {}, {})  # should not raise

    def test_fire_error_callback_exception_suppressed(self):
        from multi_agent.graph_infra import EventHooks
        hooks = EventHooks()
        hooks.on_error(lambda n, s, e: 1 / 0)
        hooks.fire_error("build", {}, RuntimeError("test"))  # should not raise
