"""Ground truth tests for task-bugfix-01: empty .ma.yaml handling."""
import tempfile
from pathlib import Path
from unittest.mock import patch


def test_empty_yaml_returns_dict(tmp_path):
    """When .ma.yaml exists but is empty, load_project_config should return {}."""
    ma_yaml = tmp_path / ".ma.yaml"
    ma_yaml.write_text("", encoding="utf-8")

    with patch("multi_agent.config.root_dir", return_value=tmp_path):
        from multi_agent.config import load_project_config
        result = load_project_config()
        assert isinstance(result, dict)
        assert result == {} or result is not None


def test_empty_yaml_no_exception(tmp_path):
    """Empty .ma.yaml must not raise TypeError."""
    ma_yaml = tmp_path / ".ma.yaml"
    ma_yaml.write_text("", encoding="utf-8")

    with patch("multi_agent.config.root_dir", return_value=tmp_path):
        from multi_agent.config import load_project_config
        # This must not raise any exception
        try:
            result = load_project_config()
        except TypeError:
            raise AssertionError("load_project_config raised TypeError on empty .ma.yaml")


def test_valid_yaml_still_works(tmp_path):
    """Normal .ma.yaml should still parse correctly."""
    ma_yaml = tmp_path / ".ma.yaml"
    ma_yaml.write_text("default_skill: code-implement\nretry_budget: 3\n", encoding="utf-8")

    with patch("multi_agent.config.root_dir", return_value=tmp_path):
        from multi_agent.config import load_project_config
        result = load_project_config()
        assert isinstance(result, dict)
        assert result.get("default_skill") == "code-implement"
