"""Built-in validation and transformation steps for the pipeline."""

from __future__ import annotations

import re
from typing import Any, Callable

from pipeline import Step, StepResult


class TypeCheck(Step):
    """Validates that data is of the expected type."""

    def __init__(self, expected_type: type) -> None:
        super().__init__(name=f"TypeCheck({expected_type.__name__})")
        self.expected_type = expected_type

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
        super().__init__(name=f"Required({fields})")
        self.fields = fields

    def execute(self, data: Any) -> StepResult:
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
        super().__init__(name=f"Range({field}, {min_val}, {max_val})")
        self.field = field
        self.min_val = min_val
        self.max_val = max_val

    def execute(self, data: Any) -> StepResult:
        value = data.get(self.field)
        if value is None:
            return StepResult(
                success=False,
                data=None,
                error=f"Field '{self.field}' not found",
            )
        if not (self.min_val <= value <= self.max_val):
            return StepResult(
                success=False,
                data=None,
                error=f"Field '{self.field}' value {value} not in range [{self.min_val}, {self.max_val}]",
            )
        return StepResult(success=True, data=data)


class Transform(Step):
    """Applies a transformation function to the data."""

    def __init__(self, func: Callable[[Any], Any]) -> None:
        super().__init__(name=f"Transform({func.__name__})")
        self.func = func

    def execute(self, data: Any) -> StepResult:
        try:
            result = self.func(data)
            return StepResult(success=True, data=result)
        except Exception as e:
            return StepResult(success=False, data=None, error=f"Transform error: {e}")


class Regex(Step):
    """Validates that a string field matches the given regex pattern."""

    def __init__(self, field: str, pattern: str) -> None:
        super().__init__(name=f"Regex({field}, {pattern})")
        self.field = field
        self.pattern = pattern

    def execute(self, data: Any) -> StepResult:
        value = data.get(self.field)
        if value is None:
            return StepResult(
                success=False,
                data=None,
                error=f"Field '{self.field}' not found",
            )
        if not re.search(self.pattern, str(value)):
            return StepResult(
                success=False,
                data=None,
                error=f"Field '{self.field}' value '{value}' does not match pattern '{self.pattern}'",
            )
        return StepResult(success=True, data=data)


class Schema(Step):
    """Validates that a dict matches a schema of {field: type}."""

    def __init__(self, schema: dict[str, type]) -> None:
        super().__init__(name="Schema")
        self.schema = schema

    def execute(self, data: Any) -> StepResult:
        if not isinstance(data, dict):
            return StepResult(
                success=False, data=None, error="Data is not a dict"
            )
        errors = []
        for field_name, expected_type in self.schema.items():
            if field_name not in data:
                errors.append(f"Missing field '{field_name}'")
            elif not isinstance(data[field_name], expected_type):
                errors.append(
                    f"Field '{field_name}' expected {expected_type.__name__}, "
                    f"got {type(data[field_name]).__name__}"
                )
        if errors:
            return StepResult(success=False, data=None, error="; ".join(errors))
        return StepResult(success=True, data=data)
