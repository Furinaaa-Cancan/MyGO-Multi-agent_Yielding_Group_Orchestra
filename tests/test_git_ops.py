"""Tests for multi_agent.git_ops — Git integration and auto-test runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from multi_agent.git_ops import (
    AutoTestConfig,
    AutoTestResult,
    GitConfig,
    auto_commit,
    changed_files,
    create_branch,
    create_tag,
    current_branch,
    has_changes,
    has_git,
    is_clean,
    load_auto_test_config,
    load_git_config,
    register_git_hooks,
    run_tests,
)

# ── GitConfig / AutoTestConfig ────────────────────────────


class TestGitConfig:
    def test_defaults(self) -> None:
        cfg = GitConfig()
        assert cfg.auto_commit is False
        assert cfg.auto_branch is False
        assert cfg.branch_prefix == "task/"
        assert cfg.commit_on == ("build", "approve")
        assert cfg.auto_tag is False

    def test_from_dict_full(self) -> None:
        cfg = GitConfig.from_dict({
            "auto_commit": True,
            "auto_branch": True,
            "branch_prefix": "feat/",
            "commit_on": ["build", "review"],
            "auto_tag": True,
        })
        assert cfg.auto_commit is True
        assert cfg.auto_branch is True
        assert cfg.branch_prefix == "feat/"
        assert cfg.commit_on == ("build", "review")
        assert cfg.auto_tag is True

    def test_from_dict_commit_on_string(self) -> None:
        cfg = GitConfig.from_dict({"commit_on": "approve"})
        assert cfg.commit_on == ("approve",)

    def test_from_dict_empty(self) -> None:
        cfg = GitConfig.from_dict({})
        assert cfg.auto_commit is False


class TestAutoTestConfig:
    def test_defaults(self) -> None:
        cfg = AutoTestConfig()
        assert cfg.enabled is False
        assert "pytest" in cfg.command
        assert cfg.inject_evidence is True
        assert cfg.fail_action == "warn"

    def test_from_dict(self) -> None:
        cfg = AutoTestConfig.from_dict({
            "enabled": True,
            "command": "python -m pytest -x",
            "inject_evidence": False,
            "fail_action": "block",
        })
        assert cfg.enabled is True
        assert cfg.command == "python -m pytest -x"
        assert cfg.inject_evidence is False
        assert cfg.fail_action == "block"


# ── Config Loading ────────────────────────────────────────


class TestLoadConfig:
    def test_load_git_config_no_yaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("multi_agent.git_ops.load_project_config", lambda: {})
        cfg = load_git_config()
        assert cfg == GitConfig()

    def test_load_git_config_with_section(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops.load_project_config",
            lambda: {"git": {"auto_commit": True, "auto_branch": True}},
        )
        cfg = load_git_config()
        assert cfg.auto_commit is True
        assert cfg.auto_branch is True

    def test_load_auto_test_config_no_yaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("multi_agent.git_ops.load_project_config", lambda: {})
        cfg = load_auto_test_config()
        assert cfg == AutoTestConfig()

    def test_load_auto_test_config_with_section(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops.load_project_config",
            lambda: {"auto_test": {"enabled": True, "command": "make test"}},
        )
        cfg = load_auto_test_config()
        assert cfg.enabled is True
        assert cfg.command == "make test"


# ── Git Primitives ────────────────────────────────────────


class TestGitPrimitives:
    def test_has_git_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr("multi_agent.git_ops.root_dir", lambda: tmp_path)
        assert has_git() is True

    def test_has_git_false_no_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("multi_agent.git_ops.root_dir", lambda: tmp_path)
        assert has_git() is False

    def test_has_git_false_no_binary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr("multi_agent.git_ops.root_dir", lambda: tmp_path)
        monkeypatch.setattr("shutil.which", lambda _: None)
        assert has_git() is False

    def test_is_clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops._git",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="", stderr=""),
        )
        assert is_clean() is True

    def test_is_not_clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops._git",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=" M file.py\n", stderr=""),
        )
        assert is_clean() is False

    def test_current_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops._git",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="main\n", stderr=""),
        )
        assert current_branch() == "main"

    def test_current_branch_detached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops._git",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="HEAD\n", stderr=""),
        )
        assert current_branch() is None

    def test_has_changes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops._git",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="?? new.py\n", stderr=""),
        )
        assert has_changes() is True

    def test_no_changes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops._git",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="", stderr=""),
        )
        assert has_changes() is False

    def test_changed_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops._git",
            lambda *a, **kw: subprocess.CompletedProcess(
                a, 0, stdout=" M main.py\n?? new.py\n", stderr="",
            ),
        )
        files = changed_files()
        assert "main.py" in files
        assert "new.py" in files


# ── Git Operations ────────────────────────────────────────


class TestGitOperations:
    def test_auto_commit_no_git(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("multi_agent.git_ops.has_git", lambda: False)
        assert auto_commit("test msg") is None

    def test_auto_commit_no_changes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("multi_agent.git_ops.has_git", lambda: True)
        monkeypatch.setattr("multi_agent.git_ops.has_changes", lambda: False)
        assert auto_commit("test msg") is None

    def test_auto_commit_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("multi_agent.git_ops.has_git", lambda: True)
        monkeypatch.setattr("multi_agent.git_ops.has_changes", lambda: True)
        calls: list[tuple[str, ...]] = []

        def mock_git(*args: str, **kw: Any) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            if args[0] == "rev-parse":
                return subprocess.CompletedProcess(args, 0, stdout="abc1234\n", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr("multi_agent.git_ops._git", mock_git)
        sha = auto_commit("feat: add feature", task_id="t1")
        assert sha == "abc1234"
        assert ("add", "-A") in calls
        assert ("commit", "-m", "feat: add feature") in calls

    def test_auto_commit_with_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("multi_agent.git_ops.has_git", lambda: True)
        monkeypatch.setattr("multi_agent.git_ops.has_changes", lambda: True)
        calls: list[tuple[str, ...]] = []

        def mock_git(*args: str, **kw: Any) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="def5678\n", stderr="")

        monkeypatch.setattr("multi_agent.git_ops._git", mock_git)
        auto_commit("fix", changed=["a.py", "b.py"])
        assert ("add", "a.py") in calls
        assert ("add", "b.py") in calls

    def test_create_branch_new(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("multi_agent.git_ops.current_branch", lambda: "main")

        def mock_git(*args: str, **kw: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr("multi_agent.git_ops._git", mock_git)
        name = create_branch("test-task")
        assert name == "task/test-task"

    def test_create_branch_already_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("multi_agent.git_ops.current_branch", lambda: "task/test-task")
        name = create_branch("test-task")
        assert name == "task/test-task"

    def test_create_tag_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops._git",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="", stderr=""),
        )
        assert create_tag("v1.0") is True

    def test_create_tag_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops._git",
            lambda *a, **kw: subprocess.CompletedProcess(a, 1, stdout="", stderr="already exists"),
        )
        assert create_tag("v1.0") is False


# ── Auto-Test Runner ──────────────────────────────────────


class TestRunTests:
    def test_disabled(self) -> None:
        cfg = AutoTestConfig(enabled=False)
        result = run_tests(cfg)
        assert result.passed is True
        assert "disabled" in result.summary

    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run(cmd, **kw):
            return subprocess.CompletedProcess(
                cmd, 0, stdout="27 passed in 0.03s\n", stderr="",
            )

        monkeypatch.setattr("subprocess.run", mock_run)
        monkeypatch.setattr("multi_agent.git_ops.root_dir", lambda: Path("/tmp"))
        cfg = AutoTestConfig(enabled=True, command="pytest tests/")
        result = run_tests(cfg)
        assert result.passed is True
        assert result.test_count == 27
        assert result.fail_count == 0

    def test_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run(cmd, **kw):
            return subprocess.CompletedProcess(
                cmd, 1, stdout="3 failed, 24 passed\n", stderr="",
            )

        monkeypatch.setattr("subprocess.run", mock_run)
        monkeypatch.setattr("multi_agent.git_ops.root_dir", lambda: Path("/tmp"))
        cfg = AutoTestConfig(enabled=True)
        result = run_tests(cfg)
        assert result.passed is False
        assert result.fail_count == 3
        assert result.test_count == 27

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 300)

        monkeypatch.setattr("subprocess.run", mock_run)
        monkeypatch.setattr("multi_agent.git_ops.root_dir", lambda: Path("/tmp"))
        cfg = AutoTestConfig(enabled=True)
        result = run_tests(cfg)
        assert result.passed is False
        assert "timed out" in result.summary.lower()

    def test_command_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run(cmd, **kw):
            raise FileNotFoundError("not found")

        monkeypatch.setattr("subprocess.run", mock_run)
        monkeypatch.setattr("multi_agent.git_ops.root_dir", lambda: Path("/tmp"))
        cfg = AutoTestConfig(enabled=True, command="nonexistent-cmd")
        result = run_tests(cfg)
        assert result.passed is False
        assert "not found" in result.summary.lower()


class TestAutoTestResult:
    def test_as_evidence_passed(self) -> None:
        r = AutoTestResult(passed=True, summary="27 passed", stdout="27 passed in 0.1s\n")
        ev = r.as_evidence
        assert len(ev) >= 1
        assert "PASSED" in ev[0]

    def test_as_evidence_failed(self) -> None:
        r = AutoTestResult(passed=False, exit_code=1, summary="3 failed", stdout="3 failed\n")
        ev = r.as_evidence
        assert "FAILED" in ev[0]


# ── Hook Handlers ─────────────────────────────────────────


class TestHookHandlers:
    def test_on_build_submit_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("multi_agent.git_ops.load_git_config", lambda: GitConfig())
        from multi_agent.git_ops import _on_build_submit

        mock = MagicMock()
        monkeypatch.setattr("multi_agent.git_ops.auto_commit", mock)
        _on_build_submit({"task_id": "t1"}, {"builder_output": {"summary": "done"}})
        mock.assert_not_called()

    def test_on_build_submit_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops.load_git_config",
            lambda: GitConfig(auto_commit=True, commit_on=("build",)),
        )
        from multi_agent.git_ops import _on_build_submit

        mock = MagicMock()
        monkeypatch.setattr("multi_agent.git_ops.auto_commit", mock)
        _on_build_submit(
            {"task_id": "t1", "builder_id": "codex"},
            {"builder_output": {"summary": "implemented feature", "changed_files": ["a.py"]}},
        )
        mock.assert_called_once()
        call_args = mock.call_args
        assert "codex" in call_args[0][0]
        assert call_args[1]["changed"] == ["a.py"]

    def test_on_decide_approve_commit_and_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops.load_git_config",
            lambda: GitConfig(auto_commit=True, auto_tag=True, commit_on=("approve",)),
        )
        from multi_agent.git_ops import _on_decide_approve

        commit_mock = MagicMock()
        tag_mock = MagicMock()
        monkeypatch.setattr("multi_agent.git_ops.auto_commit", commit_mock)
        monkeypatch.setattr("multi_agent.git_ops.create_tag", tag_mock)
        _on_decide_approve({"task_id": "t1"}, {})
        commit_mock.assert_called_once()
        tag_mock.assert_called_once_with("task/t1", "Task t1 approved")


# ── Hook Registration ─────────────────────────────────────


class TestRegisterHooks:
    def test_register_noop_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("multi_agent.git_ops.load_git_config", lambda: GitConfig())
        import multi_agent.git_ops as mod
        mod._hooks_registered = False
        register_git_hooks()
        # No error, just a no-op

    def test_register_noop_no_git(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops.load_git_config",
            lambda: GitConfig(auto_commit=True),
        )
        monkeypatch.setattr("multi_agent.git_ops.has_git", lambda: False)
        import multi_agent.git_ops as mod
        mod._hooks_registered = False
        register_git_hooks()

    def test_register_hooks_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "multi_agent.git_ops.load_git_config",
            lambda: GitConfig(auto_commit=True, auto_branch=True),
        )
        monkeypatch.setattr("multi_agent.git_ops.has_git", lambda: True)

        from multi_agent.graph_infra import EventHooks
        mock_hooks = EventHooks()
        monkeypatch.setattr("multi_agent.graph_infra.graph_hooks", mock_hooks)

        import multi_agent.git_ops as mod
        mod._hooks_registered = False
        register_git_hooks()

        assert len(mock_hooks._enter.get("plan", [])) == 1
        assert len(mock_hooks._exit.get("build", [])) == 1
        assert len(mock_hooks._exit.get("decide", [])) == 1

        # Second call is no-op
        register_git_hooks()
        assert len(mock_hooks._enter.get("plan", [])) == 1
