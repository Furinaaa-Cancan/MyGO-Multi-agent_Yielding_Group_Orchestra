"""Composable data validation and transformation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepResult:
    """Result of executing a single pipeline step."""

    success: bool
    data: Any
    error: str | None = None


class Step:
    """Base class for pipeline steps."""

    name: str = "Step"

    def execute(self, data: Any) -> StepResult:
        raise NotImplementedError


@dataclass
class PipelineResult:
    """Result of executing an entire pipeline."""

    success: bool
    data: Any
    errors: list[str] = field(default_factory=list)
    steps_executed: int = 0


class Pipeline:
    """Composable data validation and transformation pipeline.

    Supports fluent API via add_step() and pipe syntax via |.
    Executes steps in order with fail-fast semantics.
    """

    def __init__(self) -> None:
        self._steps: list[Step] = []

    def add_step(self, step: Step) -> Pipeline:
        """Add a step to the pipeline. Returns self for fluent chaining."""
        if not isinstance(step, Step):
            raise TypeError(f"Expected a Step instance, got {type(step).__name__}")
        self._steps.append(step)
        return self

    def __or__(self, other: Step) -> Pipeline:
        """Allow pipeline | step syntax."""
        return self.add_step(other)

    def run(self, data: Any) -> PipelineResult:
        """Execute all steps in order. Stops on first failure (fail-fast)."""
        current_data = data
        errors: list[str] = []
        steps_executed = 0

        for step in self._steps:
            result = step.execute(current_data)
            steps_executed += 1

            if not result.success:
                errors.append(result.error or f"Step '{step.name}' failed")
                return PipelineResult(
                    success=False,
                    data=None,
                    errors=errors,
                    steps_executed=steps_executed,
                )

            current_data = result.data

        return PipelineResult(
            success=True,
            data=current_data,
            errors=errors,
            steps_executed=steps_executed,
        )
