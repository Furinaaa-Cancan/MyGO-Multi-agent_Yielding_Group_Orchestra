"""Context Bridge Protocol — prevent information loss at sub-task boundaries.

Uses Python AST parsing (deterministic, zero-cost) to extract interface
contracts from completed sub-task code changes, then injects structured
contracts into downstream sub-task prompts.

Three mechanisms:
1. Extract: parse changed files for function signatures, defaults, types
2. Inject: format contracts into sub-task requirement as structured context
3. Verify: post-build conformance check against upstream contracts

Reference: experiment-protocol-v2.md §7.2
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


# ── Data Models ──────────────────────────────────────────


@dataclass
class ExportedSymbol:
    """A function, class, or constant exported by a sub-task."""
    name: str
    kind: str  # "function" | "class" | "constant" | "variable"
    signature: str  # full signature string (e.g. "def foo(x: int = 3) -> str")
    file_path: str
    defaults: dict[str, str] = field(default_factory=dict)  # param_name -> default_value_repr


@dataclass
class SharedStateEntry:
    """A module-level constant or default value shared across sub-tasks."""
    name: str
    value: str  # repr of the value
    file_path: str


@dataclass
class InterfaceContract:
    """Structured interface contract extracted from a sub-task's code."""
    subtask_id: str
    exports: list[ExportedSymbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)  # "from X import Y" statements
    shared_state: list[SharedStateEntry] = field(default_factory=list)
    file_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "exports": [
                {"name": e.name, "kind": e.kind, "signature": e.signature,
                 "file_path": e.file_path, "defaults": e.defaults}
                for e in self.exports
            ],
            "imports": self.imports,
            "shared_state": [
                {"name": s.name, "value": s.value, "file_path": s.file_path}
                for s in self.shared_state
            ],
            "file_paths": self.file_paths,
        }


@dataclass
class ConformanceViolation:
    """A mismatch between expected and actual interface."""
    symbol: str
    expected: str
    actual: str
    severity: str  # "error" | "warning"
    file_path: str = ""


# ── Mechanism 1: Interface Contract Extraction ───────────


def _extract_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Reconstruct a function signature string from AST node."""
    args = node.args
    parts: list[str] = []

    # Positional args
    num_defaults = len(args.defaults)
    num_args = len(args.args)
    non_default_count = num_args - num_defaults

    for i, arg in enumerate(args.args):
        param = arg.arg
        if arg.annotation:
            param += f": {ast.unparse(arg.annotation)}"
        if i >= non_default_count:
            default = args.defaults[i - non_default_count]
            param += f" = {ast.unparse(default)}"
        parts.append(param)

    # *args
    if args.vararg:
        va = f"*{args.vararg.arg}"
        if args.vararg.annotation:
            va += f": {ast.unparse(args.vararg.annotation)}"
        parts.append(va)

    # keyword-only
    for i, arg in enumerate(args.kwonlyargs):
        param = arg.arg
        if arg.annotation:
            param += f": {ast.unparse(arg.annotation)}"
        if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
            param += f" = {ast.unparse(args.kw_defaults[i])}"
        parts.append(param)

    # **kwargs
    if args.kwarg:
        kw = f"**{args.kwarg.arg}"
        if args.kwarg.annotation:
            kw += f": {ast.unparse(args.kwarg.annotation)}"
        parts.append(kw)

    ret = ""
    if node.returns:
        ret = f" -> {ast.unparse(node.returns)}"

    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(parts)}){ret}"


def _extract_defaults(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """Extract parameter default values as {name: repr} dict."""
    args = node.args
    defaults: dict[str, str] = {}

    num_defaults = len(args.defaults)
    num_args = len(args.args)
    non_default_count = num_args - num_defaults

    for i in range(non_default_count, num_args):
        arg_name = args.args[i].arg
        default_node = args.defaults[i - non_default_count]
        defaults[arg_name] = ast.unparse(default_node)

    for i, arg in enumerate(args.kwonlyargs):
        if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
            defaults[arg.arg] = ast.unparse(args.kw_defaults[i])

    return defaults


def _parse_file(file_path: Path) -> ast.Module | None:
    """Safely parse a Python file, returning None on failure."""
    try:
        source = file_path.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError, OSError) as e:
        _log.debug("Failed to parse %s: %s", file_path, e)
        return None


def extract_interface_contract(
    changed_files: list[str],
    codebase_root: Path,
    subtask_id: str = "",
) -> InterfaceContract:
    """Extract a structured interface contract from changed Python files.

    Uses AST parsing — deterministic, zero tokens, no LLM calls.
    Extracts: function signatures (with defaults), class definitions,
    module-level constants, and import statements.
    """
    contract = InterfaceContract(subtask_id=subtask_id)

    for rel_path in changed_files:
        file_path = codebase_root / rel_path
        if not file_path.suffix == ".py" or not file_path.exists():
            continue

        contract.file_paths.append(rel_path)
        tree = _parse_file(file_path)
        if tree is None:
            continue

        for node in ast.iter_child_nodes(tree):
            # Functions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue  # skip private
                sig = _extract_function_signature(node)
                defaults = _extract_defaults(node)
                contract.exports.append(ExportedSymbol(
                    name=node.name,
                    kind="function",
                    signature=sig,
                    file_path=rel_path,
                    defaults=defaults,
                ))

            # Classes
            elif isinstance(node, ast.ClassDef):
                if node.name.startswith("_"):
                    continue
                # Extract __init__ signature if present
                init_sig = ""
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                        init_sig = _extract_function_signature(item)
                        break
                sig = f"class {node.name}" + (f"  # {init_sig}" if init_sig else "")
                contract.exports.append(ExportedSymbol(
                    name=node.name,
                    kind="class",
                    signature=sig,
                    file_path=rel_path,
                ))

            # Module-level assignments (constants, config)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        contract.shared_state.append(SharedStateEntry(
                            name=target.id,
                            value=ast.unparse(node.value),
                            file_path=rel_path,
                        ))

            # Annotated assignments
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
                if name.isupper() and node.value:
                    contract.shared_state.append(SharedStateEntry(
                        name=name,
                        value=ast.unparse(node.value),
                        file_path=rel_path,
                    ))

            # Import statements
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    contract.imports.append(f"from {module} import {alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    contract.imports.append(f"import {alias.name}")

    _log.debug(
        "Extracted contract for %s: %d exports, %d shared_state, %d imports",
        subtask_id, len(contract.exports), len(contract.shared_state), len(contract.imports),
    )
    return contract


# ── Mechanism 2: Contract Injection ──────────────────────


def format_bridge_context(
    contracts: list[InterfaceContract],
    dep_ids: list[str] | None = None,
) -> str:
    """Format upstream interface contracts into structured context for injection.

    Only includes contracts from dependency sub-tasks (if dep_ids specified)
    or all contracts if dep_ids is None.

    Returns a Markdown-formatted string ready for prompt injection.
    """
    if not contracts:
        return ""

    dep_set = set(dep_ids) if dep_ids else None
    relevant = [c for c in contracts if dep_set is None or c.subtask_id in dep_set]

    if not relevant:
        return ""

    lines = [
        "## Interface Contracts from Completed Sub-Tasks",
        "",
        "**IMPORTANT**: The following interfaces are already implemented by prior",
        "sub-tasks. You MUST conform to these exact signatures, default values,",
        "and shared constants. Do NOT redefine or change them.",
        "",
    ]

    for contract in relevant:
        lines.append(f"### Sub-task: `{contract.subtask_id}`")
        lines.append("")

        if contract.exports:
            lines.append("**Exported interfaces:**")
            lines.append("```python")
            for sym in contract.exports:
                lines.append(sym.signature)
            lines.append("```")
            lines.append("")

            # Highlight defaults explicitly (these are the most common source of loss)
            defaults_found = [
                (sym.name, param, val)
                for sym in contract.exports
                for param, val in sym.defaults.items()
            ]
            if defaults_found:
                lines.append("**Default values (MUST be preserved):**")
                for func_name, param, val in defaults_found:
                    lines.append(f"- `{func_name}({param}={val})`")
                lines.append("")

        if contract.shared_state:
            lines.append("**Shared constants/config:**")
            lines.append("```python")
            for entry in contract.shared_state:
                lines.append(f"{entry.name} = {entry.value}  # from {entry.file_path}")
            lines.append("```")
            lines.append("")

    return "\n".join(lines)


# ── Mechanism 3: Conformance Checking ────────────────────


def check_conformance(
    upstream_contracts: list[InterfaceContract],
    changed_files: list[str],
    codebase_root: Path,
) -> list[ConformanceViolation]:
    """Verify that new code conforms to upstream interface contracts.

    Checks:
    - If downstream code calls an upstream function, the call matches the signature
    - If downstream code redefines an upstream symbol, signature matches
    - If downstream code references a constant, value matches

    Returns a list of violations (empty = conformant).
    """
    violations: list[ConformanceViolation] = []

    # Build index of upstream exports
    upstream_funcs: dict[str, ExportedSymbol] = {}
    upstream_constants: dict[str, SharedStateEntry] = {}
    for contract in upstream_contracts:
        for sym in contract.exports:
            if sym.kind == "function":
                upstream_funcs[sym.name] = sym
        for entry in contract.shared_state:
            upstream_constants[entry.name] = entry

    if not upstream_funcs and not upstream_constants:
        return violations

    # Parse downstream files and check for redefinitions
    for rel_path in changed_files:
        file_path = codebase_root / rel_path
        if not file_path.suffix == ".py" or not file_path.exists():
            continue

        tree = _parse_file(file_path)
        if tree is None:
            continue

        for node in ast.iter_child_nodes(tree):
            # Check function redefinitions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in upstream_funcs:
                    upstream = upstream_funcs[node.name]
                    new_sig = _extract_function_signature(node)
                    new_defaults = _extract_defaults(node)

                    # Check signature match
                    if new_sig != upstream.signature:
                        violations.append(ConformanceViolation(
                            symbol=node.name,
                            expected=upstream.signature,
                            actual=new_sig,
                            severity="error",
                            file_path=rel_path,
                        ))

                    # Check default values specifically
                    for param, expected_val in upstream.defaults.items():
                        actual_val = new_defaults.get(param)
                        if actual_val is None:
                            violations.append(ConformanceViolation(
                                symbol=f"{node.name}({param}=...)",
                                expected=f"default {param}={expected_val}",
                                actual=f"no default for {param}",
                                severity="error",
                                file_path=rel_path,
                            ))
                        elif actual_val != expected_val:
                            violations.append(ConformanceViolation(
                                symbol=f"{node.name}({param}=...)",
                                expected=f"{param}={expected_val}",
                                actual=f"{param}={actual_val}",
                                severity="warning",
                                file_path=rel_path,
                            ))

            # Check constant redefinitions
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in upstream_constants:
                        expected = upstream_constants[target.id]
                        actual_val = ast.unparse(node.value)
                        if actual_val != expected.value:
                            violations.append(ConformanceViolation(
                                symbol=target.id,
                                expected=f"{target.id} = {expected.value}",
                                actual=f"{target.id} = {actual_val}",
                                severity="warning",
                                file_path=rel_path,
                            ))

    if violations:
        _log.warning(
            "Conformance check found %d violation(s): %s",
            len(violations),
            ", ".join(v.symbol for v in violations),
        )

    return violations


def format_violations_for_reviewer(violations: list[ConformanceViolation]) -> str:
    """Format conformance violations as reviewer prompt injection."""
    if not violations:
        return ""

    lines = [
        "## ⚠ Interface Conformance Warnings",
        "",
        "The following interface mismatches were detected between this sub-task's",
        "code and the contracts established by prior sub-tasks:",
        "",
    ]

    for v in violations:
        icon = "❌" if v.severity == "error" else "⚠"
        lines.append(f"- {icon} **{v.symbol}**")
        lines.append(f"  - Expected: `{v.expected}`")
        lines.append(f"  - Actual: `{v.actual}`")
        if v.file_path:
            lines.append(f"  - File: `{v.file_path}`")
        lines.append("")

    lines.append("Please verify these mismatches and request changes if they break integration.")
    return "\n".join(lines)
