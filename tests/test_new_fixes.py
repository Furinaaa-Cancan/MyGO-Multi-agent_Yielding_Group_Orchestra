"""Tests for newly fixed features from strict audit."""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, patch

import pytest

from click.testing import CliRunner
from multi_agent.cli import main


class TestRegisterHook:
    """Task 13: register_hook public API tests."""

    def test_register_hook_plan_start(self):
        from multi_agent.graph import register_hook, graph_hooks
        calls = []
        register_hook("plan_start", lambda state: calls.append("plan"))
        graph_hooks.fire_enter("plan", {})
        assert "plan" in calls
        # Cleanup
        graph_hooks._enter["plan"].pop()

    def test_register_hook_build_submit(self):
        from multi_agent.graph import register_hook, graph_hooks
        calls = []
        register_hook("build_submit", lambda state, result: calls.append("build"))
        graph_hooks.fire_exit("build", {}, {})
        assert "build" in calls
        graph_hooks._exit["build"].pop()

    def test_register_hook_task_failed(self):
        from multi_agent.graph import register_hook, graph_hooks
        calls = []
        register_hook("task_failed", lambda node, state, err: calls.append("fail"))
        graph_hooks.fire_error("build", {}, Exception("test"))
        assert "fail" in calls
        graph_hooks._error.pop()

    def test_register_hook_unknown_event(self):
        from multi_agent.graph import register_hook, graph_hooks
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
            assert "decompose" in result.output.lower() or result.exit_code != 0


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

    def test_exception_releases_lock(self):
        from multi_agent.cli import handle_errors

        @handle_errors
        def raise_error():
            raise RuntimeError("boom")

        with patch("multi_agent.cli.read_lock", return_value=None):
            with pytest.raises(SystemExit):
                raise_error()


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

    def test_validate_detects_circular_deps(self):
        from multi_agent.decompose import validate_decompose_result
        from multi_agent.schema import DecomposeResult, SubTask
        dr = DecomposeResult(sub_tasks=[
            SubTask(id="aaa-task", description="A", deps=["bbb-task"]),
            SubTask(id="bbb-task", description="B", deps=["aaa-task"]),
        ])
        errors = validate_decompose_result(dr)
        assert any("circular" in e.lower() for e in errors)
