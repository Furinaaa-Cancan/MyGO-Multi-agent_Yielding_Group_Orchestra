"""Tests for context_bridge module — AST-based interface extraction & conformance."""

import textwrap
from pathlib import Path

import pytest

from multi_agent.context_bridge import (
    ConformanceViolation,
    InterfaceContract,
    check_conformance,
    extract_interface_contract,
    format_bridge_context,
    format_violations_for_reviewer,
)


@pytest.fixture
def tmp_codebase(tmp_path):
    """Create a temporary codebase with sample Python files."""
    (tmp_path / "auth.py").write_text(textwrap.dedent("""\
        DEFAULT_SCOPE = "read"
        MAX_RETRIES = 3

        def authorize(token: str, scope: str = "read") -> bool:
            \"\"\"Check authorization.\"\"\"
            return True

        def create_token(user_id: int, ttl: int = 3600) -> str:
            return "token"

        class TokenStore:
            def __init__(self, backend: str = "redis"):
                self.backend = backend

        def _private_helper():
            pass
    """))
    (tmp_path / "models.py").write_text(textwrap.dedent("""\
        from dataclasses import dataclass

        @dataclass
        class User:
            id: int
            name: str
    """))
    return tmp_path


class TestExtractInterfaceContract:
    def test_extracts_functions(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        func_names = [e.name for e in contract.exports if e.kind == "function"]
        assert "authorize" in func_names
        assert "create_token" in func_names

    def test_skips_private_functions(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        func_names = [e.name for e in contract.exports]
        assert "_private_helper" not in func_names

    def test_extracts_defaults(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        authorize = next(e for e in contract.exports if e.name == "authorize")
        assert authorize.defaults == {"scope": "'read'"}

    def test_extracts_classes(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        class_names = [e.name for e in contract.exports if e.kind == "class"]
        assert "TokenStore" in class_names

    def test_extracts_constants(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        const_names = [s.name for s in contract.shared_state]
        assert "DEFAULT_SCOPE" in const_names
        assert "MAX_RETRIES" in const_names

    def test_extracts_imports(self, tmp_codebase):
        contract = extract_interface_contract(
            ["models.py"], tmp_codebase, subtask_id="models"
        )
        assert any("dataclass" in imp for imp in contract.imports)

    def test_skips_nonexistent_files(self, tmp_codebase):
        contract = extract_interface_contract(
            ["nonexistent.py"], tmp_codebase, subtask_id="test"
        )
        assert len(contract.exports) == 0

    def test_skips_non_python_files(self, tmp_codebase):
        (tmp_codebase / "readme.md").write_text("# Hello")
        contract = extract_interface_contract(
            ["readme.md"], tmp_codebase, subtask_id="test"
        )
        assert len(contract.exports) == 0

    def test_handles_syntax_errors(self, tmp_codebase):
        (tmp_codebase / "broken.py").write_text("def foo(:\n  pass")
        contract = extract_interface_contract(
            ["broken.py"], tmp_codebase, subtask_id="test"
        )
        assert len(contract.exports) == 0

    def test_signature_includes_return_type(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        authorize = next(e for e in contract.exports if e.name == "authorize")
        assert "-> bool" in authorize.signature


class TestFormatBridgeContext:
    def test_empty_contracts(self):
        assert format_bridge_context([]) == ""

    def test_formats_exports(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        result = format_bridge_context([contract])
        assert "Interface Contracts" in result
        assert "authorize" in result
        assert "MUST conform" in result

    def test_filters_by_dep_ids(self, tmp_codebase):
        c1 = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        c2 = extract_interface_contract(
            ["models.py"], tmp_codebase, subtask_id="models"
        )
        result = format_bridge_context([c1, c2], dep_ids=["auth-core"])
        assert "auth-core" in result
        assert "models" not in result

    def test_highlights_defaults(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        result = format_bridge_context([contract])
        assert "Default values" in result
        assert "scope" in result


class TestCheckConformance:
    def test_no_violations_when_matching(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        # Same file = same signatures = no violations
        violations = check_conformance([contract], ["auth.py"], tmp_codebase)
        assert len(violations) == 0

    def test_detects_signature_mismatch(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        # Create a file that redefines authorize with different signature
        (tmp_codebase / "downstream.py").write_text(textwrap.dedent("""\
            def authorize(token: str, scope: str, extra: bool = False) -> bool:
                return True
        """))
        violations = check_conformance(
            [contract], ["downstream.py"], tmp_codebase
        )
        assert len(violations) > 0
        assert any(v.symbol == "authorize" for v in violations)

    def test_detects_missing_default(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        (tmp_codebase / "downstream.py").write_text(textwrap.dedent("""\
            def authorize(token: str, scope: str) -> bool:
                return True
        """))
        violations = check_conformance(
            [contract], ["downstream.py"], tmp_codebase
        )
        assert any("scope" in v.symbol for v in violations)

    def test_detects_constant_change(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        (tmp_codebase / "downstream.py").write_text(textwrap.dedent("""\
            DEFAULT_SCOPE = "write"
        """))
        violations = check_conformance(
            [contract], ["downstream.py"], tmp_codebase
        )
        assert any(v.symbol == "DEFAULT_SCOPE" for v in violations)

    def test_no_violations_for_new_functions(self, tmp_codebase):
        contract = extract_interface_contract(
            ["auth.py"], tmp_codebase, subtask_id="auth-core"
        )
        (tmp_codebase / "downstream.py").write_text(textwrap.dedent("""\
            def completely_new_function(x: int) -> str:
                return str(x)
        """))
        violations = check_conformance(
            [contract], ["downstream.py"], tmp_codebase
        )
        assert len(violations) == 0


class TestFormatViolationsForReviewer:
    def test_empty_violations(self):
        assert format_violations_for_reviewer([]) == ""

    def test_formats_violations(self):
        violations = [
            ConformanceViolation(
                symbol="authorize",
                expected="def authorize(token: str, scope: str = 'read')",
                actual="def authorize(token: str, scope: str)",
                severity="error",
                file_path="auth.py",
            )
        ]
        result = format_violations_for_reviewer(violations)
        assert "Conformance Warnings" in result
        assert "authorize" in result
        assert "auth.py" in result
