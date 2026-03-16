"""Gold-standard tests for config_parser module."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config_parser import ConfigParser


# ── load ──────────────────────────────────────────────────────────────────

class TestLoad:
    def test_basic_key_value(self):
        cp = ConfigParser()
        cp.load("name = Alice")
        assert cp.get("name") == "Alice"

    def test_multiple_lines(self):
        cp = ConfigParser()
        cp.load("name = Alice\nage = 30")
        assert cp.get("name") == "Alice"
        assert cp.get("age") == "30"

    def test_ignores_comments(self):
        cp = ConfigParser()
        cp.load("# this is a comment\nname = Alice")
        assert cp.get("name") == "Alice"
        assert len(cp.keys()) == 1

    def test_ignores_blank_lines(self):
        cp = ConfigParser()
        cp.load("name = Alice\n\n\nage = 30")
        assert len(cp.keys()) == 2

    def test_strips_whitespace(self):
        cp = ConfigParser()
        cp.load("  name  =  Alice  ")
        assert cp.get("name") == "Alice"

    def test_invalid_line_no_equals(self):
        cp = ConfigParser()
        with pytest.raises(ValueError):
            cp.load("this has no equals sign")

    def test_invalid_line_empty_key(self):
        cp = ConfigParser()
        with pytest.raises(ValueError):
            cp.load(" = value")

    def test_value_with_equals_sign(self):
        cp = ConfigParser()
        cp.load("equation = a = b")
        assert cp.get("equation") == "a = b"


# ── get ───────────────────────────────────────────────────────────────────

class TestGet:
    def test_existing_key(self):
        cp = ConfigParser()
        cp.load("color = blue")
        assert cp.get("color") == "blue"

    def test_missing_key_default_none(self):
        cp = ConfigParser()
        assert cp.get("missing") is None

    def test_missing_key_custom_default(self):
        cp = ConfigParser()
        assert cp.get("missing", "fallback") == "fallback"


# ── get_int ───────────────────────────────────────────────────────────────

class TestGetInt:
    def test_valid_int(self):
        cp = ConfigParser()
        cp.load("port = 8080")
        assert cp.get_int("port") == 8080

    def test_invalid_int(self):
        cp = ConfigParser()
        cp.load("name = Alice")
        with pytest.raises(ValueError):
            cp.get_int("name")

    def test_missing_key_default(self):
        cp = ConfigParser()
        assert cp.get_int("missing") == 0

    def test_missing_key_custom_default(self):
        cp = ConfigParser()
        assert cp.get_int("missing", 42) == 42

    def test_negative_int(self):
        cp = ConfigParser()
        cp.load("offset = -5")
        assert cp.get_int("offset") == -5


# ── get_bool ──────────────────────────────────────────────────────────────

class TestGetBool:
    @pytest.mark.parametrize("raw", ["true", "True", "TRUE", "yes", "Yes", "1", "on", "ON"])
    def test_truthy_values(self, raw):
        cp = ConfigParser()
        cp.load(f"flag = {raw}")
        assert cp.get_bool("flag") is True

    @pytest.mark.parametrize("raw", ["false", "False", "FALSE", "no", "No", "0", "off", "OFF"])
    def test_falsy_values(self, raw):
        cp = ConfigParser()
        cp.load(f"flag = {raw}")
        assert cp.get_bool("flag") is False

    def test_invalid_bool(self):
        cp = ConfigParser()
        cp.load("flag = maybe")
        with pytest.raises(ValueError):
            cp.get_bool("flag")

    def test_missing_key_default_false(self):
        cp = ConfigParser()
        assert cp.get_bool("missing") is False

    def test_missing_key_custom_default(self):
        cp = ConfigParser()
        assert cp.get_bool("missing", True) is True


# ── keys & to_dict ────────────────────────────────────────────────────────

class TestKeysAndToDict:
    def test_keys(self):
        cp = ConfigParser()
        cp.load("a = 1\nb = 2\nc = 3")
        assert sorted(cp.keys()) == ["a", "b", "c"]

    def test_to_dict(self):
        cp = ConfigParser()
        cp.load("x = 10\ny = 20")
        d = cp.to_dict()
        assert d == {"x": "10", "y": "20"}

    def test_to_dict_returns_copy(self):
        cp = ConfigParser()
        cp.load("a = 1")
        d = cp.to_dict()
        d["a"] = "changed"
        assert cp.get("a") == "1"


# ── multiple loads ────────────────────────────────────────────────────────

class TestMultipleLoads:
    def test_merge_and_overwrite(self):
        cp = ConfigParser()
        cp.load("a = 1\nb = 2")
        cp.load("b = 99\nc = 3")
        assert cp.get("a") == "1"
        assert cp.get("b") == "99"
        assert cp.get("c") == "3"
