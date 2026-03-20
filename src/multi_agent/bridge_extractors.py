"""Multi-Language Bridge Extractors — pluggable interface extraction.

Extends the Python-only AST context bridge with regex-based extractors
for JavaScript/TypeScript, Go, Java, and Rust. Uses lightweight regex
parsing (zero dependencies) to extract function signatures, class/struct
definitions, and exported constants.

Inspired by:
- AutoCodeRover (ISSTA 2024): AST-aware code search
- SWE-agent (NeurIPS 2024): ACI with structure-aware navigation
- tree-sitter ecosystem (used by many code analysis tools)

Novel contribution: deterministic, zero-LLM-cost interface extraction
across multiple languages for multi-agent sub-task context bridging.
Current approach uses regex (zero dependency) with optional tree-sitter
upgrade path.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from multi_agent.context_bridge import (
    ExportedSymbol,
    InterfaceContract,
    SharedStateEntry,
    extract_interface_contract,
)

_log = logging.getLogger(__name__)


# ── Protocol ────────────────────────────────────────────────


@runtime_checkable
class LanguageExtractor(Protocol):
    """Protocol for language-specific interface extractors.

    Each extractor knows how to parse files in its language and return
    a list of ``ExportedSymbol`` instances representing the public API.
    """

    def extract(self, file_path: Path) -> list[ExportedSymbol]:
        """Extract exported symbols from *file_path*."""
        ...

    def supported_extensions(self) -> list[str]:
        """Return file extensions this extractor handles (e.g. ``['.py']``)."""
        ...


# ── Helpers ─────────────────────────────────────────────────


def _read_source(file_path: Path) -> str | None:
    """Read file contents, returning ``None`` on failure."""
    try:
        return file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _log.debug("Failed to read %s: %s", file_path, exc)
        return None


# ── Python Extractor (delegates to existing AST logic) ──────


class PythonExtractor:
    """Wraps the existing AST-based extraction from ``context_bridge``."""

    def supported_extensions(self) -> list[str]:
        return [".py"]

    def extract(self, file_path: Path) -> list[ExportedSymbol]:
        """Delegate to ``extract_interface_contract`` for a single file.

        Uses the file's parent directory as codebase_root and the filename
        as the relative path, ensuring correct resolution for any nesting.
        """
        # extract_interface_contract expects relative paths from codebase_root.
        # Use parent as root and name as relative path so root/name = file_path.
        contract = extract_interface_contract(
            changed_files=[file_path.name],
            codebase_root=file_path.parent,
            subtask_id="",
        )
        # Fix file_path references in exports: replace filename-only with full path
        for sym in contract.exports:
            if sym.file_path == file_path.name:
                sym.file_path = str(file_path)
        return contract.exports


# ── JavaScript Extractor ────────────────────────────────────

# Patterns for JS exports
_JS_EXPORT_FUNC = re.compile(
    r"^export\s+(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)",
    re.MULTILINE,
)
_JS_EXPORT_CLASS = re.compile(
    r"^export\s+class\s+(\w+)",
    re.MULTILINE,
)
_JS_EXPORT_CONST = re.compile(
    r"^export\s+const\s+(\w+)\s*=",
    re.MULTILINE,
)
_JS_EXPORT_DEFAULT_FUNC = re.compile(
    r"^export\s+default\s+(?:async\s+)?function\s+(\w+)?\s*\(([^)]*)\)",
    re.MULTILINE,
)
_JS_EXPORT_DEFAULT_CLASS = re.compile(
    r"^export\s+default\s+class\s+(\w+)?",
    re.MULTILINE,
)


class JavaScriptExtractor:
    """Regex-based extractor for JavaScript (``.js`` / ``.jsx``) files.

    Recognises:
    - ``export function name(params)``
    - ``export class Name``
    - ``export const NAME = ...``
    - ``export default function/class``
    """

    def supported_extensions(self) -> list[str]:
        return [".js", ".jsx"]

    def extract(self, file_path: Path) -> list[ExportedSymbol]:
        source = _read_source(file_path)
        if source is None:
            return []

        rel = file_path.name
        symbols: list[ExportedSymbol] = []

        # Named function exports
        for m in _JS_EXPORT_FUNC.finditer(source):
            name = m.group(1)
            params = m.group(2).strip()
            is_async = "async" in m.group(0)
            prefix = "async function" if is_async else "function"
            symbols.append(ExportedSymbol(
                name=name,
                kind="function",
                signature=f"export {prefix} {name}({params})",
                file_path=rel,
            ))

        # Class exports
        for m in _JS_EXPORT_CLASS.finditer(source):
            name = m.group(1)
            symbols.append(ExportedSymbol(
                name=name,
                kind="class",
                signature=f"export class {name}",
                file_path=rel,
            ))

        # Const exports
        for m in _JS_EXPORT_CONST.finditer(source):
            name = m.group(1)
            symbols.append(ExportedSymbol(
                name=name,
                kind="constant",
                signature=f"export const {name}",
                file_path=rel,
            ))

        # Default function export
        for m in _JS_EXPORT_DEFAULT_FUNC.finditer(source):
            name = m.group(1) or "default"
            params = m.group(2).strip()
            is_async = "async" in m.group(0)
            prefix = "async function" if is_async else "function"
            symbols.append(ExportedSymbol(
                name=name,
                kind="function",
                signature=f"export default {prefix} {name}({params})",
                file_path=rel,
            ))

        # Default class export
        for m in _JS_EXPORT_DEFAULT_CLASS.finditer(source):
            name = m.group(1) or "default"
            symbols.append(ExportedSymbol(
                name=name,
                kind="class",
                signature=f"export default class {name}",
                file_path=rel,
            ))

        return symbols


# ── TypeScript Extractor ────────────────────────────────────

_TS_EXPORT_FUNC = re.compile(
    r"^export\s+(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)(?:\s*:\s*(\S+))?",
    re.MULTILINE,
)
_TS_EXPORT_INTERFACE = re.compile(
    r"^export\s+interface\s+(\w+)",
    re.MULTILINE,
)
_TS_EXPORT_TYPE = re.compile(
    r"^export\s+type\s+(\w+)\s*=",
    re.MULTILINE,
)


class TypeScriptExtractor:
    """Regex-based extractor for TypeScript (``.ts`` / ``.tsx``) files.

    Extends JavaScript patterns with type annotations:
    - ``export function name(params: Type): ReturnType``
    - ``export interface Name``
    - ``export type Name = ...``

    Also inherits all JavaScript export patterns via composition.
    """

    def __init__(self) -> None:
        self._js_extractor = JavaScriptExtractor()

    def supported_extensions(self) -> list[str]:
        return [".ts", ".tsx"]

    def extract(self, file_path: Path) -> list[ExportedSymbol]:
        source = _read_source(file_path)
        if source is None:
            return []

        rel = file_path.name
        symbols: list[ExportedSymbol] = []
        seen_names: set[str] = set()

        # TypeScript-specific: functions with type annotations
        for m in _TS_EXPORT_FUNC.finditer(source):
            name = m.group(1)
            params = m.group(2).strip()
            ret = m.group(3)
            is_async = "async" in m.group(0)
            prefix = "async function" if is_async else "function"
            ret_part = f": {ret}" if ret else ""
            symbols.append(ExportedSymbol(
                name=name,
                kind="function",
                signature=f"export {prefix} {name}({params}){ret_part}",
                file_path=rel,
            ))
            seen_names.add(name)

        # Interfaces (reported as "class" kind per spec)
        for m in _TS_EXPORT_INTERFACE.finditer(source):
            name = m.group(1)
            symbols.append(ExportedSymbol(
                name=name,
                kind="class",
                signature=f"export interface {name}",
                file_path=rel,
            ))
            seen_names.add(name)

        # Type aliases
        for m in _TS_EXPORT_TYPE.finditer(source):
            name = m.group(1)
            symbols.append(ExportedSymbol(
                name=name,
                kind="constant",
                signature=f"export type {name}",
                file_path=rel,
            ))
            seen_names.add(name)

        # Fall back to JS patterns for class/const/default exports
        js_symbols = self._js_extractor.extract(file_path)
        for sym in js_symbols:
            if sym.name not in seen_names:
                symbols.append(sym)
                seen_names.add(sym.name)

        return symbols


# ── Go Extractor ────────────────────────────────────────────

_GO_FUNC = re.compile(
    r"^func\s+(\w+)\s*\(([^)]*)\)\s*([\w*\[\]().]+)?",
    re.MULTILINE,
)
_GO_METHOD = re.compile(
    r"^func\s+\([^)]+\)\s+(\w+)\s*\(([^)]*)\)\s*([\w*\[\]().]+)?",
    re.MULTILINE,
)
_GO_TYPE_STRUCT = re.compile(
    r"^type\s+(\w+)\s+struct\b",
    re.MULTILINE,
)
_GO_TYPE_INTERFACE = re.compile(
    r"^type\s+(\w+)\s+interface\b",
    re.MULTILINE,
)
_GO_CONST = re.compile(
    r"^\s*(\w+)\s*(?:\w+)?\s*=\s*",
    re.MULTILINE,
)
_GO_CONST_SINGLE = re.compile(
    r"^const\s+(\w+)\s*(?:\w+)?\s*=\s*(.+)",
    re.MULTILINE,
)


class GoExtractor:
    """Regex-based extractor for Go (``.go``) files.

    Recognises exported (capitalized) identifiers:
    - ``func Name(params) returnType``
    - ``type Name struct``
    - ``const Name = value``
    """

    def supported_extensions(self) -> list[str]:
        return [".go"]

    def extract(self, file_path: Path) -> list[ExportedSymbol]:
        source = _read_source(file_path)
        if source is None:
            return []

        rel = file_path.name
        symbols: list[ExportedSymbol] = []

        # Package-level functions (not methods)
        for m in _GO_FUNC.finditer(source):
            # Skip methods (they are caught by _GO_METHOD)
            name = m.group(1)
            if not name[0].isupper():
                continue  # unexported
            params = m.group(2).strip()
            ret = m.group(3) or ""
            ret_part = f" {ret}" if ret else ""
            symbols.append(ExportedSymbol(
                name=name,
                kind="function",
                signature=f"func {name}({params}){ret_part}",
                file_path=rel,
            ))

        # Struct types
        for m in _GO_TYPE_STRUCT.finditer(source):
            name = m.group(1)
            if not name[0].isupper():
                continue
            symbols.append(ExportedSymbol(
                name=name,
                kind="class",
                signature=f"type {name} struct",
                file_path=rel,
            ))

        # Interface types
        for m in _GO_TYPE_INTERFACE.finditer(source):
            name = m.group(1)
            if not name[0].isupper():
                continue
            symbols.append(ExportedSymbol(
                name=name,
                kind="class",
                signature=f"type {name} interface",
                file_path=rel,
            ))

        # Single-line constants
        for m in _GO_CONST_SINGLE.finditer(source):
            name = m.group(1)
            if not name[0].isupper():
                continue
            symbols.append(ExportedSymbol(
                name=name,
                kind="constant",
                signature=f"const {name}",
                file_path=rel,
            ))

        return symbols


# ── Rust Extractor ──────────────────────────────────────────

_RS_PUB_FN = re.compile(
    r"^pub\s+(?:async\s+)?fn\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*(\S+))?",
    re.MULTILINE,
)
_RS_PUB_STRUCT = re.compile(
    r"^pub\s+struct\s+(\w+)",
    re.MULTILINE,
)
_RS_PUB_ENUM = re.compile(
    r"^pub\s+enum\s+(\w+)",
    re.MULTILINE,
)
_RS_PUB_CONST = re.compile(
    r"^pub\s+const\s+(\w+)\s*:\s*(\S+)\s*=",
    re.MULTILINE,
)
_RS_PUB_TRAIT = re.compile(
    r"^pub\s+trait\s+(\w+)",
    re.MULTILINE,
)


class RustExtractor:
    """Regex-based extractor for Rust (``.rs``) files.

    Recognises public items:
    - ``pub fn name(params) -> ReturnType``
    - ``pub struct Name``
    - ``pub const NAME: Type = value``
    - ``pub enum Name``
    - ``pub trait Name``
    """

    def supported_extensions(self) -> list[str]:
        return [".rs"]

    def extract(self, file_path: Path) -> list[ExportedSymbol]:
        source = _read_source(file_path)
        if source is None:
            return []

        rel = file_path.name
        symbols: list[ExportedSymbol] = []

        # Public functions
        for m in _RS_PUB_FN.finditer(source):
            name = m.group(1)
            params = m.group(2).strip()
            ret = m.group(3)
            is_async = "async" in m.group(0)
            prefix = "pub async fn" if is_async else "pub fn"
            ret_part = f" -> {ret}" if ret else ""
            symbols.append(ExportedSymbol(
                name=name,
                kind="function",
                signature=f"{prefix} {name}({params}){ret_part}",
                file_path=rel,
            ))

        # Public structs
        for m in _RS_PUB_STRUCT.finditer(source):
            name = m.group(1)
            symbols.append(ExportedSymbol(
                name=name,
                kind="class",
                signature=f"pub struct {name}",
                file_path=rel,
            ))

        # Public enums
        for m in _RS_PUB_ENUM.finditer(source):
            name = m.group(1)
            symbols.append(ExportedSymbol(
                name=name,
                kind="class",
                signature=f"pub enum {name}",
                file_path=rel,
            ))

        # Public traits
        for m in _RS_PUB_TRAIT.finditer(source):
            name = m.group(1)
            symbols.append(ExportedSymbol(
                name=name,
                kind="class",
                signature=f"pub trait {name}",
                file_path=rel,
            ))

        # Public constants
        for m in _RS_PUB_CONST.finditer(source):
            name = m.group(1)
            type_name = m.group(2)
            symbols.append(ExportedSymbol(
                name=name,
                kind="constant",
                signature=f"pub const {name}: {type_name}",
                file_path=rel,
            ))

        return symbols


# ── Java Extractor ──────────────────────────────────────────

_JAVA_PUBLIC_METHOD = re.compile(
    r"^\s*public\s+(?:static\s+)?(?:(?:final|synchronized|abstract)\s+)*"
    r"([\w<>\[\]?,\s]+?)\s+(\w+)\s*\(([^)]*)\)",
    re.MULTILINE,
)
_JAVA_PUBLIC_CLASS = re.compile(
    r"^\s*public\s+(?:abstract\s+|final\s+)?class\s+(\w+)",
    re.MULTILINE,
)
_JAVA_PUBLIC_INTERFACE = re.compile(
    r"^\s*public\s+interface\s+(\w+)",
    re.MULTILINE,
)
_JAVA_PUBLIC_CONSTANT = re.compile(
    r"^\s*public\s+static\s+final\s+(\w+)\s+(\w+)\s*=",
    re.MULTILINE,
)


class JavaExtractor:
    """Regex-based extractor for Java (``.java``) files.

    Recognises:
    - ``public ... returnType methodName(params)``
    - ``public class Name``
    - ``public interface Name``
    - ``public static final TYPE NAME = value``
    """

    def supported_extensions(self) -> list[str]:
        return [".java"]

    def extract(self, file_path: Path) -> list[ExportedSymbol]:
        source = _read_source(file_path)
        if source is None:
            return []

        rel = file_path.name
        symbols: list[ExportedSymbol] = []
        seen_names: set[str] = set()

        # Public constants (check first, so method regex doesn't grab them)
        for m in _JAVA_PUBLIC_CONSTANT.finditer(source):
            type_name = m.group(1)
            name = m.group(2)
            symbols.append(ExportedSymbol(
                name=name,
                kind="constant",
                signature=f"public static final {type_name} {name}",
                file_path=rel,
            ))
            seen_names.add(name)

        # Public classes
        for m in _JAVA_PUBLIC_CLASS.finditer(source):
            name = m.group(1)
            symbols.append(ExportedSymbol(
                name=name,
                kind="class",
                signature=f"public class {name}",
                file_path=rel,
            ))
            seen_names.add(name)

        # Public interfaces (reported as "class" kind)
        for m in _JAVA_PUBLIC_INTERFACE.finditer(source):
            name = m.group(1)
            symbols.append(ExportedSymbol(
                name=name,
                kind="class",
                signature=f"public interface {name}",
                file_path=rel,
            ))
            seen_names.add(name)

        # Public methods
        for m in _JAVA_PUBLIC_METHOD.finditer(source):
            ret_type = m.group(1).strip()
            name = m.group(2)
            params = m.group(3).strip()
            if name in seen_names:
                continue  # constructor or already captured
            # Skip if return type looks like a class name (constructor match)
            if name[0].isupper() and ret_type == name:
                continue
            symbols.append(ExportedSymbol(
                name=name,
                kind="function",
                signature=f"public {ret_type} {name}({params})",
                file_path=rel,
            ))

        return symbols


# ── Factory ─────────────────────────────────────────────────

_EXTRACTORS: list[LanguageExtractor] = [
    PythonExtractor(),
    JavaScriptExtractor(),
    TypeScriptExtractor(),
    GoExtractor(),
    RustExtractor(),
    JavaExtractor(),
]

_EXTENSION_MAP: dict[str, LanguageExtractor] = {}
for _ext in _EXTRACTORS:
    for _suffix in _ext.supported_extensions():
        _EXTENSION_MAP[_suffix] = _ext


def get_extractor(file_path: Path) -> LanguageExtractor | None:
    """Return the appropriate extractor for *file_path* based on extension.

    Returns ``None`` if no extractor is registered for the file type.
    """
    return _EXTENSION_MAP.get(file_path.suffix)


# ── Multi-Language Extraction Entry Point ───────────────────


def extract_multi_language(
    changed_files: list[str],
    codebase_root: Path,
    subtask_id: str = "",
) -> InterfaceContract:
    """Extract an interface contract from changed files across multiple languages.

    Works like ``extract_interface_contract`` but dispatches to the
    appropriate language extractor for each file.  Python files are
    handled by the original AST-based extractor; other languages use
    regex-based extractors.

    Args:
        changed_files: Relative file paths (from *codebase_root*).
        codebase_root: Root directory of the codebase.
        subtask_id: Identifier for the sub-task producing these files.

    Returns:
        An ``InterfaceContract`` with all extracted symbols.
    """
    contract = InterfaceContract(subtask_id=subtask_id)

    # Separate Python files (use full AST extraction) from others
    python_files: list[str] = []
    other_files: list[str] = []

    for rel_path in changed_files:
        file_path = codebase_root / rel_path
        if file_path.suffix == ".py":
            python_files.append(rel_path)
        else:
            other_files.append(rel_path)

    # Python: delegate to the battle-tested AST extractor
    if python_files:
        py_contract = extract_interface_contract(
            python_files, codebase_root, subtask_id,
        )
        contract.exports.extend(py_contract.exports)
        contract.imports.extend(py_contract.imports)
        contract.shared_state.extend(py_contract.shared_state)
        contract.file_paths.extend(py_contract.file_paths)

    # Other languages: use regex extractors
    for rel_path in other_files:
        file_path = codebase_root / rel_path
        if not file_path.exists():
            _log.debug("Skipping non-existent file: %s", file_path)
            continue

        extractor = get_extractor(file_path)
        if extractor is None:
            _log.debug("No extractor for %s", file_path.suffix)
            continue

        contract.file_paths.append(rel_path)
        symbols = extractor.extract(file_path)
        contract.exports.extend(symbols)

    _log.debug(
        "Multi-language extraction for %s: %d exports from %d files (%d Python, %d other)",
        subtask_id,
        len(contract.exports),
        len(contract.file_paths),
        len(python_files),
        len(other_files),
    )
    return contract
