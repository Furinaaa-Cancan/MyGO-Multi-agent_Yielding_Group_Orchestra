"""Tests for scripts/codex_exec_agent.py strict fallback semantics."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parent.parent / "scripts" / "codex_exec_agent.py"
    spec = importlib.util.spec_from_file_location("codex_exec_agent", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_fallback_is_blocked():
    m = _load_module()
    payload = m._fallback_payload("builder", Path("/tmp/task.md"), 124)
    assert payload["status"] == "blocked"
    assert payload["_adapter_fallback"] is True
    assert payload["check_results"]["unit_test"] == "skip"


def test_reviewer_fallback_requests_changes():
    m = _load_module()
    payload = m._fallback_payload("reviewer", Path("/tmp/task.md"), 124)
    assert payload["decision"] == "request_changes"
    assert payload["_adapter_fallback"] is True
    assert payload["recommended_event"] == "review_fail"


def test_normalize_defaults_are_conservative():
    m = _load_module()
    out = m._normalize_builder({}, Path("/tmp/task.md"))
    assert out["status"] == "blocked"
    assert out["changed_files"] == []
    assert out["check_results"]["lint"] == "skip"

    rv = m._normalize_reviewer({})
    assert rv["decision"] == "request_changes"
    assert rv["evidence"] == []
    assert rv["recommended_event"] == "review_fail"


def test_nonzero_rc_with_parsed_json_keeps_payload_not_fallback():
    m = _load_module()
    candidates = [
        {
            "status": "completed",
            "summary": "implemented core flow",
            "changed_files": ["/tmp/app/main.py"],
            "check_results": {"lint": "pass", "unit_test": "pass", "artifact_checksum": "pass"},
        }
    ]
    payload = m._select_payload("builder", Path("/tmp/task.md"), 124, candidates)
    assert payload["status"] == "completed"
    assert payload["changed_files"] == ["/tmp/app/main.py"]
    assert payload.get("_adapter_fallback") is not True
    assert payload.get("_adapter_nonzero_rc") is True
    assert payload.get("_adapter_exit_code") == 124
    assert any("rc=124" in str(x) for x in payload.get("risks", []))


def test_nonzero_rc_without_json_uses_fallback():
    m = _load_module()
    payload = m._select_payload("builder", Path("/tmp/task.md"), 124, [])
    assert payload["status"] == "blocked"
    assert payload["_adapter_fallback"] is True


def test_builder_prompt_requires_real_implementation():
    m = _load_module()
    prompt = m._prompt_for_role("builder", Path("/tmp/task.md"), Path("/tmp/outbox/builder.json"))
    assert "实际完成实现" in prompt
    assert "不要只做总结" in prompt
