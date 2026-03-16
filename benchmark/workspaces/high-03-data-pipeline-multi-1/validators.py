"""Built-in validation and transformation steps for the pipeline."""

from __future__ import annotations

import re
from typing import Any, Callable

from pipeline import Step, StepResult


class TypeCheck(Step):
    """Validates that data is of the expected type."""

    def __init__(self, expected_type: type) -> None:
        self.expected_type = expected_type
        self.name = f"TypeCheck({expected_type.__name__})"

    def execute(self, data: Any) -> StepResult:
        if isinstance(data, self.expected_type):
            return StepResult(success=True, data=data)
        return StepResult(
            success=False,
            data=None,
            error=f"Expected type {self.expected_type.__name__}, got {type(data).__name__}",
        )


class Required(Step):
    """For dicts: checks that all specified fields are present."""

    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        self.name = f"Required({fields})"

    def execute(self, data: Any) -> StepResult:
        if not isinstance(data, dict):
            return StepResult(
                success=False,
                data=None,
                error="Required check expects a dict",
            )
        missing = [f for f in self.fields if f not in data]
        if missing:
            return StepResult(
                success=False,
                data=None,
                error=f"Missing required fields: {missing}",
            )
        return StepResult(success=True, data=data)


class Range(Step):
    """Validates that a numeric field is within [min_val, max_val]."""

    def __init__(self, field: str, min_val: float, max_val: float) -> None:
        self.field = field
        self.min_val = min_val
        self.max_val = max_val
        self.name = f"Range({field}, {min_val}, {max_val})"

    def execute(self, data: Any) -> StepResult:
        if not isinstance(data, dict):
            return StepResult(
                success=False,
                data=None,
                error="Range check expects a dict",
            )
        if self.field not in data:
            return StepResult(
                success=False,
                data=None,
                error=f"Field '{self.field}' not found",
            )
        value = data[self.field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return StepResult(
                success=False,
                data=None,
                error=f"Field '{self.field}' is not numeric",
            )
        if self.min_val <= value <= self.max_val:
            return StepResult(success=True, data=data)
        return StepResult(
            success=False,
            data=None,
            error=f"Field '{self.field}' value {value} not in range [{self.min_val}, {self.max_val}]",
        )


class Transform(Step):
    """Applies a transformation function to the data."""

    def __init__(self, func: Callable[[Any], Any]) -> None:
        self.func = func
        self.name = f"Transform({func.__name__})"

    def execute(self, data: Any) -> StepResult:
        try:
            result = self.func(data)
            return StepResult(success=True, data=result)
        except Exception as e:
            return StepResult(
                success=False,
                data=None,
                error=f"Transform failed: {e}",
            )


class Regex(Step):
    """Validates that a string field matches a regex pattern."""

    def __init__(self, field: str, pattern: str) -> None:
        self.field = field
        self.pattern = pattern
        self.name = f"Regex({field}, {pattern})"

    def execute(self, data: Any) -> StepResult:
        if not isinstance(data, dict):
            return StepResult(
                success=False,
                data=None,
                error="Regex check expects a dict",
            )
        if self.field not in data:
            return StepResult(
                success=False,
                data=None,
                error=f"Field '{self.field}' not found",
            )
        value = data[self.field]
        if not isinstance(value, str):
            return StepResult(
                success=False,
                data=None,
                error=f"Field '{self.field}' is not a string",
            )
        if re.search(self.pattern, value):
            return StepResult(success=True, data=data)
        return StepResult(
            success=False,
            data=None,
            error=f"Field '{self.field}' does not match pattern '{self.pattern}'",
        )


class Schema(Step):
    """Validates that a dict matches a schema of {field: type}."""

    def __init__(self, schema: dict[str, type]) -> None:
        self.schema = schema
        self.name = f"Schema({{{', '.join(f'{k}: {v.__name__}' for k, v in schema.items())}}})"

    def execute(self, data: Any) -> StepResult:
        if not isinstance(data, dict):
            return StepResult(
                success=False,
                data=None,
                error="Schema check expects a dict",
            )
        for field_name, expected_type in self.schema.items():
            if field_name not in data:
                return StepResult(
                    success=False,
                    data=None,
                    error=f"Schema validation failed: missing field '{field_name}'",
                )
            value = data[field_name]
            if expected_type is int and isinstance(value, bool):
                return StepResult(
                    success=False,
                    data=None,
                    error=f"Schema validation failed: field '{field_name}' expected int, got bool",
                )
            if expected_type is float and isinstance(value, bool):
                return StepResult(
                    success=False,
                    data=None,
                    error=f"Schema validation failed: field '{field_name}' expected float, got bool",
                )
            if not isinstance(value, expected_type):
                return StepResult(
                    success=False,
                    data=None,
                    error=f"Schema validation failed: field '{field_name}' expected {expected_type.__name__}, got {type(value).__name__}",
                )
        return StepResult(success=True, data=data)
