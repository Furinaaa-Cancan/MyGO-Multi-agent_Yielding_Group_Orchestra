"""Gold-standard tests for the Data Validation Pipeline."""
import sys
import re
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import Pipeline, PipelineResult, Step, StepResult
from validators import TypeCheck, Required, Range, Transform, Regex, Schema


# ---------------------------------------------------------------------------
# Pipeline creation and chaining
# ---------------------------------------------------------------------------

class TestPipelineCreation:
    def test_empty_pipeline_succeeds(self):
        result = Pipeline().run({"anything": True})
        assert result.success is True
        assert result.steps_executed == 0

    def test_add_step_returns_pipeline(self):
        p = Pipeline()
        ret = p.add_step(TypeCheck(dict))
        assert ret is p

    def test_fluent_chaining(self):
        p = Pipeline().add_step(TypeCheck(dict)).add_step(Required(["name"]))
        result = p.run({"name": "Alice"})
        assert result.success is True
        assert result.steps_executed == 2


# ---------------------------------------------------------------------------
# Pipe operator syntax
# ---------------------------------------------------------------------------

class TestPipeOperator:
    def test_pipe_operator_builds_pipeline(self):
        p = Pipeline() | TypeCheck(dict) | Required(["name"])
        result = p.run({"name": "Bob"})
        assert result.success is True
        assert result.steps_executed == 2

    def test_pipe_operator_returns_pipeline(self):
        p = Pipeline() | TypeCheck(dict)
        assert isinstance(p, Pipeline)


# ---------------------------------------------------------------------------
# TypeCheck validator
# ---------------------------------------------------------------------------

class TestTypeCheck:
    def test_correct_type_passes(self):
        step = TypeCheck(dict)
        r = step.execute({"a": 1})
        assert r.success is True

    def test_wrong_type_fails(self):
        step = TypeCheck(dict)
        r = step.execute("not a dict")
        assert r.success is False
        assert r.error is not None

    def test_int_type(self):
        step = TypeCheck(int)
        assert step.execute(42).success is True
        assert step.execute("42").success is False

    def test_has_name(self):
        step = TypeCheck(dict)
        assert isinstance(step.name, str) and len(step.name) > 0


# ---------------------------------------------------------------------------
# Required validator
# ---------------------------------------------------------------------------

class TestRequired:
    def test_all_fields_present(self):
        step = Required(["name", "age"])
        r = step.execute({"name": "Alice", "age": 30})
        assert r.success is True

    def test_missing_field_fails(self):
        step = Required(["name", "email"])
        r = step.execute({"name": "Alice"})
        assert r.success is False
        assert "email" in r.error

    def test_empty_fields_list(self):
        step = Required([])
        r = step.execute({"anything": True})
        assert r.success is True


# ---------------------------------------------------------------------------
# Range validator
# ---------------------------------------------------------------------------

class TestRange:
    def test_value_in_range(self):
        step = Range("age", 0, 120)
        r = step.execute({"age": 25})
        assert r.success is True

    def test_value_below_range(self):
        step = Range("age", 0, 120)
        r = step.execute({"age": -1})
        assert r.success is False

    def test_value_above_range(self):
        step = Range("age", 0, 120)
        r = step.execute({"age": 200})
        assert r.success is False

    def test_boundary_values_inclusive(self):
        step = Range("val", 10, 20)
        assert step.execute({"val": 10}).success is True
        assert step.execute({"val": 20}).success is True


# ---------------------------------------------------------------------------
# Transform step
# ---------------------------------------------------------------------------

class TestTransform:
    def test_transform_applies_function(self):
        step = Transform(lambda d: {**d, "upper_name": d["name"].upper()})
        r = step.execute({"name": "alice"})
        assert r.success is True
        assert r.data["upper_name"] == "ALICE"

    def test_transform_passes_data_downstream(self):
        p = (
            Pipeline()
            | TypeCheck(dict)
            | Transform(lambda d: {**d, "processed": True})
        )
        result = p.run({"x": 1})
        assert result.success is True
        assert result.data["processed"] is True
        assert result.data["x"] == 1


# ---------------------------------------------------------------------------
# Regex validator
# ---------------------------------------------------------------------------

class TestRegex:
    def test_matching_pattern(self):
        step = Regex("email", r"^[\w.+-]+@[\w-]+\.[\w.]+$")
        r = step.execute({"email": "user@example.com"})
        assert r.success is True

    def test_non_matching_pattern(self):
        step = Regex("email", r"^[\w.+-]+@[\w-]+\.[\w.]+$")
        r = step.execute({"email": "not-an-email"})
        assert r.success is False


# ---------------------------------------------------------------------------
# Schema validator
# ---------------------------------------------------------------------------

class TestSchema:
    def test_valid_schema(self):
        step = Schema({"name": str, "age": int})
        r = step.execute({"name": "Alice", "age": 30})
        assert r.success is True

    def test_wrong_field_type(self):
        step = Schema({"name": str, "age": int})
        r = step.execute({"name": "Alice", "age": "thirty"})
        assert r.success is False

    def test_missing_field_in_schema(self):
        step = Schema({"name": str, "age": int})
        r = step.execute({"name": "Alice"})
        assert r.success is False

    def test_extra_fields_allowed(self):
        step = Schema({"name": str})
        r = step.execute({"name": "Alice", "extra": 999})
        assert r.success is True


# ---------------------------------------------------------------------------
# Fail-fast behaviour
# ---------------------------------------------------------------------------

class TestFailFast:
    def test_stops_on_first_failure(self):
        p = (
            Pipeline()
            | TypeCheck(dict)
            | Required(["missing_field"])
            | Range("age", 0, 100)
        )
        result = p.run({"age": 50})
        assert result.success is False
        assert result.steps_executed == 2  # TypeCheck OK, Required fails, Range skipped

    def test_errors_list_populated(self):
        p = Pipeline() | TypeCheck(int)
        result = p.run("string")
        assert result.success is False
        assert len(result.errors) == 1
        assert isinstance(result.errors[0], str) and len(result.errors[0]) > 0


# ---------------------------------------------------------------------------
# PipelineResult fields
# ---------------------------------------------------------------------------

class TestPipelineResult:
    def test_success_result_fields(self):
        p = Pipeline() | TypeCheck(dict)
        result = p.run({"a": 1})
        assert result.success is True
        assert result.data == {"a": 1}
        assert result.errors == []
        assert result.steps_executed == 1

    def test_failure_result_fields(self):
        p = Pipeline() | TypeCheck(int)
        result = p.run("oops")
        assert result.success is False
        assert result.steps_executed == 1
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# Step reuse
# ---------------------------------------------------------------------------

class TestStepReuse:
    def test_same_step_in_multiple_pipelines(self):
        type_check = TypeCheck(dict)
        p1 = Pipeline() | type_check
        p2 = Pipeline() | type_check | Required(["x"])

        assert p1.run({"x": 1}).success is True
        assert p2.run({"x": 1}).success is True
        assert p2.run({}).success is False


# ---------------------------------------------------------------------------
# Complex integration
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_full_pipeline(self):
        p = (
            Pipeline()
            | TypeCheck(dict)
            | Required(["name", "age", "email"])
            | Range("age", 18, 99)
            | Regex("email", r"^[\w.+-]+@[\w-]+\.[\w.]+$")
            | Transform(lambda d: {**d, "verified": True})
        )
        result = p.run({
            "name": "Alice",
            "age": 30,
            "email": "alice@example.com",
        })
        assert result.success is True
        assert result.data["verified"] is True
        assert result.steps_executed == 5
        # Verify original fields preserved after Transform
        assert result.data["name"] == "Alice"
        assert result.data["age"] == 30
        assert result.data["email"] == "alice@example.com"

    def test_full_pipeline_failure_midway(self):
        p = (
            Pipeline()
            | TypeCheck(dict)
            | Required(["name", "age"])
            | Range("age", 18, 99)
        )
        result = p.run({"name": "Kid", "age": 5})
        assert result.success is False
        assert result.steps_executed == 3  # TypeCheck OK, Required OK, Range fails
        assert len(result.errors) == 1
        assert result.data is None or result.data == {"name": "Kid", "age": 5}
