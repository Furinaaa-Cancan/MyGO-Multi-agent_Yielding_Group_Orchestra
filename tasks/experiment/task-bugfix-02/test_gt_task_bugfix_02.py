"""Ground truth tests for task-bugfix-02: write_inbox empty content validation."""
import pytest
from unittest.mock import patch
from pathlib import Path


def test_write_inbox_empty_content_raises(tmp_path):
    """write_inbox with empty string must raise ValueError."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    with patch("multi_agent.workspace.inbox_dir", return_value=inbox), \
         patch("multi_agent.workspace.ensure_workspace"):
        from multi_agent.workspace import write_inbox
        with pytest.raises(ValueError, match="content"):
            write_inbox("builder", "")


def test_write_inbox_whitespace_content_raises(tmp_path):
    """write_inbox with only whitespace must raise ValueError."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    with patch("multi_agent.workspace.inbox_dir", return_value=inbox), \
         patch("multi_agent.workspace.ensure_workspace"):
        from multi_agent.workspace import write_inbox
        with pytest.raises(ValueError, match="content"):
            write_inbox("builder", "   \n\t  ")


def test_write_inbox_valid_content_works(tmp_path):
    """Normal content should write successfully."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    with patch("multi_agent.workspace.inbox_dir", return_value=inbox), \
         patch("multi_agent.workspace.ensure_workspace"):
        from multi_agent.workspace import write_inbox
        result = write_inbox("builder", "Implement feature X")
        assert result.exists()
        assert result.read_text() == "Implement feature X"
