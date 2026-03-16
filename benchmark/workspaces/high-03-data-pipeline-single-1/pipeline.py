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

    def __init__(self, name: str = "") -> None:
        self.name: str = name or self.__class__.__name__

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
    """A composable, fail-fast data validation and transformation pipeline."""

    def __init__(self) -> None:
        self._steps: list[Step] = []

    def add_step(self, step: Step) -> Pipeline:
        """Add a step to the pipeline. Returns self for fluent chaining."""
        self._steps.append(step)
        return self

    def __or__(self, other: Step) -> Pipeline:
        """Allow pipeline | step syntax."""
        return self.add_step(other)

    def run(self, data: Any) -> PipelineResult:
        """Execute all steps in order, stopping on first failure."""
        current_data = data
        steps_executed = 0

        for step in self._steps:
            result = step.execute(current_data)
            steps_executed += 1

            if not result.success:
                return PipelineResult(
                    success=False,
                    data=None,
                    errors=[result.error] if result.error else [],
                    steps_executed=steps_executed,
                )

            current_data = result.data

        return PipelineResult(
            success=True,
            data=current_data,
            errors=[],
            steps_executed=steps_executed,
        )
