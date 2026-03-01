"""Tests for README code examples (Task 50)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from multi_agent.cli import main


ROOT = Path(__file__).resolve().parent.parent


class TestReadmeExamples:
    """Task 50: Verify README.md code examples and consistency."""

    def test_ma_help_runs(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Multi-Agent" in result.output

    def test_go_help_contains_options(self):
        runner = CliRunner()
        result = runner.invoke(main, ["go", "--help"])
        assert result.exit_code == 0
        for opt in ["--skill", "--builder", "--reviewer", "--timeout", "--decompose"]:
            assert opt in result.output

    def test_version_matches_pyproject(self):
        from multi_agent import __version__
        pyproject = ROOT / "pyproject.toml"
        if pyproject.exists():
            text = pyproject.read_text(encoding="utf-8")
            assert __version__ in text

    def test_example_json_files_valid(self):
        examples_dir = ROOT / "docs" / "examples"
        if not examples_dir.exists():
            return  # skip if no examples dir
        for jf in examples_dir.glob("*.json"):
            data = json.loads(jf.read_text(encoding="utf-8"))
            assert isinstance(data, dict), f"{jf.name} is not a JSON object"

    def test_badge_test_count_consistent(self):
        """T50: README badge test count matches actual pytest count."""
        import subprocess
        readme = ROOT / "README.md"
        if not readme.exists():
            return
        text = readme.read_text(encoding="utf-8")
        import re
        m = re.search(r"tests-(\d+)%20passed", text)
        if not m:
            return
        badge_count = int(m.group(1))
        proc = subprocess.run(
            [".venv/bin/python", "-m", "pytest", "tests/", "-q", "--co"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        # Last line: "N tests collected"
        lines = proc.stdout.strip().splitlines()
        collected_line = [l for l in lines if "test" in l and "collected" in l]
        if collected_line:
            actual = int(re.search(r"(\d+)\s+test", collected_line[0]).group(1))
            assert badge_count == actual, f"Badge says {badge_count} but {actual} tests collected"

    def test_done_help_runs(self):
        runner = CliRunner()
        result = runner.invoke(main, ["done", "--help"])
        assert result.exit_code == 0
        assert "--file" in result.output
