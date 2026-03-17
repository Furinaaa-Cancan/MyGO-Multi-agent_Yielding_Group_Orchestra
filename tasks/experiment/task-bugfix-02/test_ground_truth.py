"""Ground truth tests for task-bugfix-02: clear_outbox with missing directory."""
from unittest.mock import patch
from pathlib import Path


def test_clear_outbox_missing_dir_no_error(tmp_path):
    """clear_outbox must not raise when outbox dir does not exist."""
    nonexistent = tmp_path / "nonexistent_outbox"

    with patch("multi_agent.workspace.outbox_dir", return_value=nonexistent):
        from multi_agent.workspace import clear_outbox
        # Must not raise
        try:
            clear_outbox()
        except FileNotFoundError:
            raise AssertionError("clear_outbox raised FileNotFoundError for missing dir")


def test_clear_outbox_existing_dir_clears_files(tmp_path):
    """clear_outbox should still clear files when outbox exists."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "builder.json").write_text('{"status": "done"}')
    (outbox / "reviewer.json").write_text('{"decision": "approve"}')

    with patch("multi_agent.workspace.outbox_dir", return_value=outbox):
        from multi_agent.workspace import clear_outbox
        clear_outbox()
        remaining = list(outbox.iterdir())
        assert len(remaining) == 0, f"Expected empty outbox, found: {remaining}"


def test_clear_outbox_returns_none(tmp_path):
    """clear_outbox should return None (no return value)."""
    nonexistent = tmp_path / "no_outbox"

    with patch("multi_agent.workspace.outbox_dir", return_value=nonexistent):
        from multi_agent.workspace import clear_outbox
        result = clear_outbox()
        assert result is None
