# Task: Data Validation Pipeline

Implement a composable data validation and transformation pipeline.

Files: `pipeline.py`, `validators.py`, `test_pipeline.py`

## Core Classes

1. `Pipeline`:
   - `add_step(step: Step) -> Pipeline` - Chain a step, returns self for fluent API
   - `run(data: Any) -> PipelineResult` - Execute all steps in order
   - `__or__(other: Step) -> Pipeline` - Allow `pipeline | step` syntax

2. `PipelineResult`:
   - `success: bool`, `data: Any` (transformed data or None), `errors: list[str]`, `steps_executed: int`

3. `Step` (base class):
   - `name: str`, `execute(data: Any) -> StepResult`

4. `StepResult`:
   - `success: bool`, `data: Any`, `error: str | None`

## Built-in Steps (in validators.py)

1. `TypeCheck(expected_type)` - Validates data is expected type
2. `Required(fields: list[str])` - For dicts: checks all fields present
3. `Range(field: str, min_val, max_val)` - Validates numeric field in range
4. `Transform(func: Callable)` - Apply transformation function
5. `Regex(field: str, pattern: str)` - Validates string field matches regex
6. `Schema(schema: dict)` - Validate dict matches schema: `{"field": type}`

## Requirements

- Pipeline stops on first failure (fail-fast)
- All results include which steps ran
- Steps are reusable across pipelines
- Fluent API: `Pipeline().add_step(TypeCheck(dict)).add_step(Required(["name"]))`
- Or pipe syntax: `Pipeline() | TypeCheck(dict) | Required(["name"])`
