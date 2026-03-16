"""Tests for the data validation pipeline."""

import pytest

from pipeline import Pipeline, PipelineResult, Step, StepResult
from validators import TypeCheck, Required, Range, Transform, Regex, Schema


# ── PipelineResult and StepResult basics ──


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
    def test_defaults(self):
        r = PipelineResult(success=True, data="ok")
        assert r.errors == []
        assert r.steps_executed == 0


# ── Pipeline core behaviour ──


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

    def test_fail_fast_stops_on_first_failure(self):
        p = Pipeline() | TypeCheck(dict) | Required(["name"]) | Range("age", 0, 150)
        result = p.run("not a dict")
        assert result.success is False
        assert result.steps_executed == 1
        assert len(result.errors) == 1
        assert result.data is None

    def test_fail_fast_second_step(self):
        p = Pipeline() | TypeCheck(dict) | Required(["name", "age"])
        result = p.run({"name": "Alice"})
        assert result.success is False
        assert result.steps_executed == 2
        assert "age" in result.errors[0]

    def test_steps_reusable_across_pipelines(self):
        type_check = TypeCheck(dict)
        required = Required(["name"])

        p1 = Pipeline() | type_check | required
        p2 = Pipeline() | type_check | required

        r1 = p1.run({"name": "A"})
        r2 = p2.run({"name": "B"})
        assert r1.success is True
        assert r2.success is True

    def test_add_step_returns_self(self):
        p = Pipeline()
        ret = p.add_step(TypeCheck(dict))
        assert ret is p

    def test_pipe_returns_pipeline(self):
        p = Pipeline()
        ret = p | TypeCheck(dict)
        assert isinstance(ret, Pipeline)
        assert ret is p

    def test_add_step_rejects_non_step(self):
        with pytest.raises(TypeError):
            Pipeline().add_step("not a step")

    def test_pipe_rejects_non_step(self):
        with pytest.raises(TypeError):
            Pipeline() | "not a step"


# ── TypeCheck ──


class TestTypeCheck:
    def test_pass(self):
        assert TypeCheck(dict).execute({"a": 1}).success is True

    def test_fail(self):
        r = TypeCheck(dict).execute([1, 2])
        assert r.success is False
        assert "dict" in r.error
        assert "list" in r.error

    def test_int(self):
        assert TypeCheck(int).execute(42).success is True
        assert TypeCheck(int).execute("42").success is False

    def test_name(self):
        assert "dict" in TypeCheck(dict).name


# ── Required ──


class TestRequired:
    def test_all_present(self):
        r = Required(["a", "b"]).execute({"a": 1, "b": 2, "c": 3})
        assert r.success is True

    def test_missing(self):
        r = Required(["a", "b"]).execute({"a": 1})
        assert r.success is False
        assert "b" in r.error

    def test_non_dict(self):
        r = Required(["a"]).execute("string")
        assert r.success is False

    def test_empty_fields_list(self):
        r = Required([]).execute({"a": 1})
        assert r.success is True


# ── Range ──


class TestRange:
    def test_in_range(self):
        r = Range("age", 0, 150).execute({"age": 25})
        assert r.success is True

    def test_below(self):
        r = Range("age", 0, 150).execute({"age": -1})
        assert r.success is False

    def test_above(self):
        r = Range("age", 0, 150).execute({"age": 200})
        assert r.success is False

    def test_boundary_min(self):
        assert Range("x", 0, 10).execute({"x": 0}).success is True

    def test_boundary_max(self):
        assert Range("x", 0, 10).execute({"x": 10}).success is True

    def test_field_missing(self):
        r = Range("age", 0, 150).execute({"name": "A"})
        assert r.success is False

    def test_non_numeric(self):
        r = Range("age", 0, 150).execute({"age": "twenty"})
        assert r.success is False

    def test_non_dict(self):
        r = Range("x", 0, 10).execute(42)
        assert r.success is False

    def test_float_value(self):
        assert Range("score", 0.0, 1.0).execute({"score": 0.5}).success is True

    def test_bool_rejected(self):
        r = Range("x", 0, 10).execute({"x": True})
        assert r.success is False
        assert "not numeric" in r.error


# ── Transform ──


class TestTransform:
    def test_basic(self):
        r = Transform(lambda d: {**d, "upper": d["name"].upper()}).execute(
            {"name": "alice"}
        )
        assert r.success is True
        assert r.data["upper"] == "ALICE"

    def test_exception(self):
        r = Transform(lambda d: d["missing"]).execute({})
        assert r.success is False
        assert "Transform failed" in r.error

    def test_name(self):
        def my_func(x):
            return x

        assert "my_func" in Transform(my_func).name

    def test_data_flows_through_pipeline(self):
        p = Pipeline() | Transform(lambda d: {**d, "new": True})
        result = p.run({"old": True})
        assert result.success is True
        assert result.data == {"old": True, "new": True}


# ── Regex ──


class TestRegex:
    def test_match(self):
        r = Regex("email", r".+@.+\..+").execute({"email": "a@b.com"})
        assert r.success is True

    def test_no_match(self):
        r = Regex("email", r".+@.+\..+").execute({"email": "invalid"})
        assert r.success is False

    def test_field_missing(self):
        r = Regex("email", r".*").execute({"name": "A"})
        assert r.success is False

    def test_non_string_field(self):
        r = Regex("count", r"\d+").execute({"count": 42})
        assert r.success is False

    def test_non_dict(self):
        r = Regex("x", r".*").execute("string")
        assert r.success is False


# ── Schema ──


class TestSchema:
    def test_valid(self):
        r = Schema({"name": str, "age": int}).execute({"name": "A", "age": 30})
        assert r.success is True

    def test_missing_field(self):
        r = Schema({"name": str, "age": int}).execute({"name": "A"})
        assert r.success is False
        assert "age" in r.error

    def test_wrong_type(self):
        r = Schema({"name": str, "age": int}).execute({"name": "A", "age": "30"})
        assert r.success is False
        assert "age" in r.error

    def test_non_dict(self):
        r = Schema({"x": int}).execute([1, 2])
        assert r.success is False

    def test_empty_schema(self):
        r = Schema({}).execute({"anything": "goes"})
        assert r.success is True

    def test_extra_fields_ok(self):
        r = Schema({"name": str}).execute({"name": "A", "extra": 99})
        assert r.success is True

    def test_bool_not_accepted_as_int(self):
        r = Schema({"flag": int}).execute({"flag": True})
        assert r.success is False
        assert "bool" in r.error


# ── Integration / end-to-end ──


class TestIntegration:
    def test_full_pipeline(self):
        p = (
            Pipeline()
            | TypeCheck(dict)
            | Schema({"name": str, "age": int, "email": str})
            | Required(["name", "age", "email"])
            | Range("age", 0, 150)
            | Regex("email", r".+@.+\..+")
            | Transform(lambda d: {**d, "name": d["name"].title()})
        )
        result = p.run({"name": "alice", "age": 30, "email": "alice@example.com"})
        assert result.success is True
        assert result.data["name"] == "Alice"
        assert result.steps_executed == 6

    def test_full_pipeline_failure(self):
        p = (
            Pipeline()
            | TypeCheck(dict)
            | Required(["name", "email"])
            | Regex("email", r".+@.+\..+")
        )
        result = p.run({"name": "A", "email": "bad"})
        assert result.success is False
        assert result.steps_executed == 3
        assert len(result.errors) == 1

    def test_transform_chains(self):
        p = (
            Pipeline()
            | Transform(lambda d: {**d, "step1": True})
            | Transform(lambda d: {**d, "step2": True})
        )
        result = p.run({"init": True})
        assert result.success is True
        assert result.data == {"init": True, "step1": True, "step2": True}
        assert result.steps_executed == 2
