"""Ground truth tests for task-bugfix-01: validate_outbox_data reviewer summary check."""


def test_reviewer_missing_summary_reports_error():
    """Reviewer output without summary should report an error."""
    from multi_agent.workspace import validate_outbox_data
    errors = validate_outbox_data("reviewer", {"decision": "approve"})
    assert any("summary" in e for e in errors), f"Expected 'summary' error, got: {errors}"


def test_reviewer_with_summary_no_error():
    """Reviewer output with both decision and summary should have no errors."""
    from multi_agent.workspace import validate_outbox_data
    errors = validate_outbox_data("reviewer", {"decision": "approve", "summary": "LGTM"})
    assert len(errors) == 0, f"Unexpected errors: {errors}"


def test_builder_validation_unchanged():
    """Builder validation should still require status and summary."""
    from multi_agent.workspace import validate_outbox_data
    errors = validate_outbox_data("builder", {})
    assert any("status" in e for e in errors)
    assert any("summary" in e for e in errors)
