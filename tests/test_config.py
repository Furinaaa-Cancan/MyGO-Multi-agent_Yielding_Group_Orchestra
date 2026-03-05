"""Tests for config module."""

import warnings
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from multi_agent.config import load_project_config, root_dir, _find_root, agents_profile_path


class TestLoadProjectConfig:
    """Task 6: Verify .ma.yaml project-level configuration."""

    def test_config_exists(self, tmp_path):
        """Valid .ma.yaml is read correctly."""
        config_data = {
            "workspace_dir": ".multi-agent",
            "default_timeout": 1800,
            "default_retry_budget": 2,
            "default_builder": "windsurf",
            "default_reviewer": "cursor",
            "decompose_timeout": 900,
            "poll_interval": 2.0,
        }
        ma_yaml = tmp_path / ".ma.yaml"
        ma_yaml.write_text(yaml.dump(config_data), encoding="utf-8")
        with patch("multi_agent.config.root_dir", return_value=tmp_path):
            result = load_project_config()
        assert result["default_timeout"] == 1800
        assert result["default_builder"] == "windsurf"
        assert result["poll_interval"] == 2.0

    def test_config_not_exists(self, tmp_path):
        """Missing .ma.yaml returns empty dict without error."""
        with patch("multi_agent.config.root_dir", return_value=tmp_path):
            result = load_project_config()
        assert result == {}

    def test_config_malformed_yaml(self, tmp_path):
        """Malformed YAML issues warning and returns empty dict."""
        ma_yaml = tmp_path / ".ma.yaml"
        ma_yaml.write_text(":::\n  bad: [yaml", encoding="utf-8")
        with patch("multi_agent.config.root_dir", return_value=tmp_path):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = load_project_config()
        assert result == {}
        assert any(".ma.yaml" in str(warning.message) for warning in w)

    def test_config_not_a_dict(self, tmp_path):
        """YAML that parses to a non-dict (e.g. a list) issues warning."""
        ma_yaml = tmp_path / ".ma.yaml"
        ma_yaml.write_text("- item1\n- item2\n", encoding="utf-8")
        with patch("multi_agent.config.root_dir", return_value=tmp_path):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = load_project_config()
        assert result == {}
        assert any("not a valid mapping" in str(warning.message) for warning in w)

    def test_config_empty_file(self, tmp_path):
        """Empty .ma.yaml returns empty dict."""
        ma_yaml = tmp_path / ".ma.yaml"
        ma_yaml.write_text("", encoding="utf-8")
        with patch("multi_agent.config.root_dir", return_value=tmp_path):
            result = load_project_config()
        assert result == {}


class TestFindRoot:
    """Task 39 (partial): Verify root finding logic."""

    def test_find_root_with_env(self, tmp_path, monkeypatch):
        """MA_ROOT env var is used when set."""
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            result = _find_root()
            assert result == tmp_path.resolve()
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()

    def test_find_root_fallback_warning(self, tmp_path, monkeypatch):
        """When no project root found, warning is issued."""
        monkeypatch.delenv("MA_ROOT", raising=False)
        monkeypatch.chdir(tmp_path)
        root_dir.cache_clear()
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = _find_root()
            assert any("Could not find" in str(warning.message) for warning in w)
        finally:
            root_dir.cache_clear()

    def test_root_dir_cached(self, tmp_path, monkeypatch):
        """lru_cache returns same object on second call."""
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            r1 = root_dir()
            r2 = root_dir()
            assert r1 is r2
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()


class TestValidateConfig:
    """Task 64: Config validation tests."""

    def test_valid_config(self):
        from multi_agent.config import validate_config
        data = {"default_skill": "code-implement", "retry_budget": 3}
        assert validate_config(data) == []

    def test_unknown_keys(self):
        from multi_agent.config import validate_config
        data = {"unknown_key": "value", "another": 42}
        warnings_list = validate_config(data)
        assert any("Unknown config keys" in w for w in warnings_list)

    def test_invalid_retry_budget_type(self):
        from multi_agent.config import validate_config
        data = {"retry_budget": "not_an_int"}
        warnings_list = validate_config(data)
        assert any("retry_budget" in w for w in warnings_list)

    def test_invalid_timeout_type(self):
        from multi_agent.config import validate_config
        data = {"timeout_sec": "bad"}
        warnings_list = validate_config(data)
        assert any("timeout_sec" in w for w in warnings_list)

    def test_empty_config_valid(self):
        from multi_agent.config import validate_config
        assert validate_config({}) == []

    def test_retry_budget_out_of_range(self):
        from multi_agent.config import validate_config
        assert any("out of range" in w for w in validate_config({"retry_budget": -1}))
        assert any("out of range" in w for w in validate_config({"retry_budget": 21}))
        assert validate_config({"retry_budget": 3}) == []

    def test_timeout_sec_nonpositive(self):
        from multi_agent.config import validate_config
        assert any("positive" in w for w in validate_config({"timeout_sec": 0}))
        assert any("positive" in w for w in validate_config({"timeout_sec": -5}))
        assert validate_config({"timeout_sec": 300}) == []

    def test_watch_interval_too_small(self):
        from multi_agent.config import validate_config
        assert any("0.1" in w for w in validate_config({"watch_interval": 0}))
        assert any("0.1" in w for w in validate_config({"watch_interval": -1}))
        assert validate_config({"watch_interval": 0.5}) == []


class TestFindRootDiagnostics:
    """Task 65: root_dir diagnostic improvement tests."""

    def test_ma_root_nonexistent_raises(self, tmp_path, monkeypatch):
        nonexistent = tmp_path / "does_not_exist"
        monkeypatch.setenv("MA_ROOT", str(nonexistent))
        root_dir.cache_clear()
        try:
            with pytest.raises(FileNotFoundError, match="does not exist"):
                _find_root()
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()

    def test_ma_root_relative_resolved(self, tmp_path, monkeypatch):
        (tmp_path / "myproj" / "skills").mkdir(parents=True)
        (tmp_path / "myproj" / "agents").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MA_ROOT", "myproj")
        root_dir.cache_clear()
        try:
            result = _find_root()
            assert result.is_absolute()
            assert (result / "skills").exists()
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()

    def test_fallback_warning_includes_scanned(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MA_ROOT", raising=False)
        monkeypatch.chdir(tmp_path)
        root_dir.cache_clear()
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                _find_root()
            msgs = [str(warning.message) for warning in w]
            assert any("Scanned" in m for m in msgs)
            assert any("ma init" in m for m in msgs)
        finally:
            root_dir.cache_clear()


class TestMaRootWarning:
    """Cover lines 24-25: MA_ROOT exists but missing skills/agents dirs."""

    def test_missing_skills_agents_warns(self, tmp_path, monkeypatch):
        # Create dir without skills/ or agents/ subdirs
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                _find_root()
            assert any("skills" in str(warning.message) and "agents" in str(warning.message) for warning in w)
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()


class TestAgentsProfilePath:
    def test_returns_expected_path(self, tmp_path, monkeypatch):
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            result = agents_profile_path()
            assert result == tmp_path.resolve() / "agents" / "profiles.json"
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()


class TestProjectSettings:
    """Cover ProjectSettings (MergedConfig) class — lines 194-245."""

    def test_defaults_applied(self, tmp_path, monkeypatch):
        from multi_agent.config import ProjectSettings
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            ps = ProjectSettings()
            assert ps.get("retry_budget") == 2
            assert ps.get("timeout_sec") == 1800
            assert ps.get("workflow_mode") == "strict"
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()

    def test_overrides_take_precedence(self, tmp_path, monkeypatch):
        from multi_agent.config import ProjectSettings
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            ps = ProjectSettings(overrides={"retry_budget": 5, "builder": "cursor"})
            assert ps.get("retry_budget") == 5
            assert ps.get("builder") == "cursor"
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()

    def test_ma_yaml_applied(self, tmp_path, monkeypatch):
        from multi_agent.config import ProjectSettings
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        (tmp_path / ".ma.yaml").write_text(
            yaml.dump({"default_builder": "windsurf", "retry_budget": 3}),
            encoding="utf-8",
        )
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            ps = ProjectSettings()
            assert ps.get("default_builder") == "windsurf"
            assert ps.get("retry_budget") == 3
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()

    def test_workmode_yaml_applied(self, tmp_path, monkeypatch):
        from multi_agent.config import ProjectSettings
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "workmode.yaml").write_text(yaml.dump({
            "modes": {
                "strict": {
                    "roles": {"builder": "ws", "reviewer": "cursor"},
                    "review_policy": {"require_evidence": True},
                }
            }
        }), encoding="utf-8")
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            ps = ProjectSettings(mode="strict")
            assert ps.get("builder") == "ws"
            assert ps.get("reviewer") == "cursor"
            assert ps.get("review_policy") == {"require_evidence": True}
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()

    def test_getitem_and_contains(self, tmp_path, monkeypatch):
        from multi_agent.config import ProjectSettings
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            ps = ProjectSettings()
            assert ps["retry_budget"] == 2
            assert "retry_budget" in ps
            assert "nonexistent_key" not in ps
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()

    def test_as_dict(self, tmp_path, monkeypatch):
        from multi_agent.config import ProjectSettings
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            ps = ProjectSettings()
            d = ps.as_dict()
            assert isinstance(d, dict)
            assert "retry_budget" in d
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()

    def test_workmode_yaml_missing_graceful(self, tmp_path, monkeypatch):
        from multi_agent.config import ProjectSettings
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        # no config/workmode.yaml
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            ps = ProjectSettings(mode="strict")
            # Should not fail, just skip workmode
            assert ps.get("workflow_mode") == "strict"
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()

    def test_workmode_yaml_invalid_mode_cfg(self, tmp_path, monkeypatch):
        from multi_agent.config import ProjectSettings
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "workmode.yaml").write_text(yaml.dump({
            "modes": {"strict": "not_a_dict"}
        }), encoding="utf-8")
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            ps = ProjectSettings(mode="strict")
            assert ps.get("retry_budget") == 2  # defaults still applied
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()

    def test_overrides_empty_values_skipped(self, tmp_path, monkeypatch):
        from multi_agent.config import ProjectSettings
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            ps = ProjectSettings(overrides={"builder": "", "reviewer": None})
            assert ps.get("builder") == ""  # default is ""
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()


class TestPathFunctions:
    """Task 39 (partial): Verify all path helper functions."""

    def test_all_paths(self, tmp_path, monkeypatch):
        from multi_agent.config import (
            workspace_dir, skills_dir, inbox_dir, outbox_dir,
            tasks_dir, history_dir, dashboard_path, store_db_path,
        )
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        root_dir.cache_clear()
        try:
            assert workspace_dir() == tmp_path.resolve() / ".multi-agent"
            assert skills_dir() == tmp_path.resolve() / "skills"
            assert inbox_dir() == tmp_path.resolve() / ".multi-agent" / "inbox"
            assert outbox_dir() == tmp_path.resolve() / ".multi-agent" / "outbox"
            assert tasks_dir() == tmp_path.resolve() / ".multi-agent" / "tasks"
            assert history_dir() == tmp_path.resolve() / ".multi-agent" / "history"
            assert dashboard_path() == tmp_path.resolve() / ".multi-agent" / "dashboard.md"
            assert store_db_path() == tmp_path.resolve() / ".multi-agent" / "store.db"
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()
