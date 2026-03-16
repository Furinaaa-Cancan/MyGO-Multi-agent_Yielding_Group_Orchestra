"""Tests for the data validation pipeline."""

import pytest

from pipeline import Pipeline, PipelineResult, Step, StepResult
from validators import TypeCheck, Required, Range, Transform, Regex, Schema


# ---------------------------------------------------------------------------
# StepResult / PipelineResult basics
# ---------------------------------------------------------------------------

class TestStepResult:
    def test_success_result(self):
        r = StepResult(success=True, data=42)
        assert r.success is True
        assert r.data == 42
        assert r.error is None

    def test_failure_result(self):
        r = StepResult(success=False, data=None, error="bad")
        assert r.success is False
        assert r.error == "bad"


class TestPipelineResult:
    def test_fields(self):
        r = PipelineResult(success=True, data={"a": 1}, errors=[], steps_executed=3)
        assert r.success is True
        assert r.data == {"a": 1}
        assert r.errors == []
        assert r.steps_executed == 3


# ---------------------------------------------------------------------------
# Pipeline mechanics
# ---------------------------------------------------------------------------

class TestPipeline:
    def test_empty_pipeline(self):
        result = Pipeline().run({"x": 1})
        assert result.success is True
        assert result.data == {"x": 1}
        assert result.steps_executed == 0

    def test_fluent_api(self):
        p = Pipeline().add_step(TypeCheck(dict)).add_step(Required(["name"]))
        result = p.run({"name": "Alice"})
        assert result.success is True
        assert result.steps_executed == 2

    def test_pipe_syntax(self):
        p = Pipeline() | TypeCheck(dict) | Required(["name"])
        result = p.run({"name": "Bob"})
        assert result.success is True
        assert result.steps_executed == 2

    def test_fail_fast(self):
        """Pipeline should stop on first failure."""
        p = Pipeline() | TypeCheck(dict) | Required(["name"]) | Range("age", 0, 150)
        result = p.run("not a dict")
        assert result.success is False
        assert result.steps_executed == 1
        assert len(result.errors) == 1

    def test_fail_fast_middle_step(self):
        p = Pipeline() | TypeCheck(dict) | Required(["name", "age"]) | Range("age", 0, 150)
        result = p.run({"name": "Alice"})  # missing 'age'
        assert result.success is False
        assert result.steps_executed == 2

    def test_data_flows_through_transform(self):
        p = (
            Pipeline()
            | TypeCheck(dict)
            | Transform(lambda d: {**d, "upper_name": d["name"].upper()})
        )
        result = p.run({"name": "alice"})
        assert result.success is True
        assert result.data["upper_name"] == "ALICE"
        assert result.steps_executed == 2

    def test_steps_reusable_across_pipelines(self):
        type_check = TypeCheck(dict)
        req = Required(["id"])

        p1 = Pipeline() | type_check | req
        p2 = Pipeline() | type_check | req

        r1 = p1.run({"id": 1})
        r2 = p2.run({"id": 2})
        assert r1.success is True
        assert r2.success is True

    def test_pipeline_result_data_is_none_on_failure(self):
        p = Pipeline() | TypeCheck(int)
        result = p.run("hello")
        assert result.success is False
        assert result.data is None


# ---------------------------------------------------------------------------
# TypeCheck
# ---------------------------------------------------------------------------

class TestTypeCheck:
    def test_pass(self):
        r = TypeCheck(dict).execute({"a": 1})
        assert r.success is True

    def test_fail(self):
        r = TypeCheck(dict).execute([1, 2])
        assert r.success is False
        assert "Expected type dict" in r.error

    def test_int_check(self):
        assert TypeCheck(int).execute(42).success is True
        assert TypeCheck(int).execute("42").success is False


# ---------------------------------------------------------------------------
# Required
# ---------------------------------------------------------------------------

class TestRequired:
    def test_all_present(self):
        r = Required(["a", "b"]).execute({"a": 1, "b": 2, "c": 3})
        assert r.success is True

    def test_missing_fields(self):
        r = Required(["a", "b"]).execute({"a": 1})
        assert r.success is False
        assert "b" in r.error


# ---------------------------------------------------------------------------
# Range
# ---------------------------------------------------------------------------

class TestRange:
    def test_in_range(self):
        r = Range("age", 0, 150).execute({"age": 25})
        assert r.success is True

    def test_below_range(self):
        r = Range("age", 0, 150).execute({"age": -1})
        assert r.success is False

    def test_above_range(self):
        r = Range("age", 0, 150).execute({"age": 200})
        assert r.success is False

    def test_boundary_min(self):
        assert Range("x", 0, 10).execute({"x": 0}).success is True

    def test_boundary_max(self):
        assert Range("x", 0, 10).execute({"x": 10}).success is True

    def test_missing_field(self):
        r = Range("age", 0, 150).execute({"name": "Alice"})
        assert r.success is False


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------

class TestTransform:
    def test_transform_success(self):
        r = Transform(lambda d: d * 2).execute(5)
        assert r.success is True
        assert r.data == 10

    def test_transform_dict(self):
        def add_full_name(d):
            return {**d, "full": f"{d['first']} {d['last']}"}

        r = Transform(add_full_name).execute({"first": "Jane", "last": "Doe"})
        assert r.success is True
        assert r.data["full"] == "Jane Doe"

    def test_transform_error(self):
        r = Transform(lambda d: d["missing"]).execute({})
        assert r.success is False
        assert "Transform error" in r.error


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

class TestRegex:
    def test_match(self):
        r = Regex("email", r".+@.+\..+").execute({"email": "a@b.com"})
        assert r.success is True

    def test_no_match(self):
        r = Regex("email", r".+@.+\..+").execute({"email": "invalid"})
        assert r.success is False

    def test_missing_field(self):
        r = Regex("email", r".+").execute({"name": "Alice"})
        assert r.success is False


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_valid_schema(self):
        s = Schema({"name": str, "age": int})
        r = s.execute({"name": "Alice", "age": 30})
        assert r.success is True

    def test_wrong_type(self):
        s = Schema({"name": str, "age": int})
        r = s.execute({"name": "Alice", "age": "thirty"})
        assert r.success is False
        assert "age" in r.error

    def test_missing_field_in_schema(self):
        s = Schema({"name": str, "age": int})
        r = s.execute({"name": "Alice"})
        assert r.success is False
        assert "Missing" in r.error

    def test_not_a_dict(self):
        r = Schema({"x": int}).execute("string")
        assert r.success is False


# ---------------------------------------------------------------------------
# Integration: full pipeline
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_full_user_validation_pipeline(self):
        p = (
            Pipeline()
            | TypeCheck(dict)
            | Schema({"name": str, "age": int, "email": str})
            | Required(["name", "age", "email"])
            | Range("age", 18, 120)
            | Regex("email", r"^[^@]+@[^@]+\.[^@]+$")
            | Transform(lambda d: {**d, "name": d["name"].strip().title()})
        )

        result = p.run({"name": "  jane doe  ", "age": 25, "email": "jane@example.com"})
        assert result.success is True
        assert result.data["name"] == "Jane Doe"
        assert result.steps_executed == 6

    def test_full_pipeline_failure_at_schema(self):
        p = (
            Pipeline()
            | TypeCheck(dict)
            | Schema({"name": str, "age": int})
            | Range("age", 0, 150)
        )

        result = p.run({"name": "Alice", "age": "not_a_number"})
        assert result.success is False
        assert result.steps_executed == 2  # TypeCheck ok, Schema fails

    def test_chained_transforms(self):
        p = (
            Pipeline()
            | Transform(lambda x: x + 1)
            | Transform(lambda x: x * 2)
            | Transform(lambda x: x - 3)
        )
        # (5 + 1) * 2 - 3 = 9
        result = p.run(5)
        assert result.success is True
        assert result.data == 9
        assert result.steps_executed == 3
