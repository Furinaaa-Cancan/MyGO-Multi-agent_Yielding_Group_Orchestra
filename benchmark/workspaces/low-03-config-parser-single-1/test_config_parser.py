import pytest
from config_parser import ConfigParser


class TestLoad:
    def test_basic_key_value(self):
        cp = ConfigParser()
        cp.load("name = Alice")
        assert cp.get("name") == "Alice"

    def test_multiple_lines(self):
        cp = ConfigParser()
        cp.load("a = 1\nb = 2\nc = 3")
        assert cp.get("a") == "1"
        assert cp.get("b") == "2"
        assert cp.get("c") == "3"

    def test_ignores_blank_lines(self):
        cp = ConfigParser()
        cp.load("a = 1\n\n\nb = 2")
        assert cp.keys() == ["a", "b"]

    def test_ignores_comments(self):
        cp = ConfigParser()
        cp.load("# this is a comment\na = 1\n# another comment")
        assert cp.keys() == ["a"]

    def test_strips_whitespace(self):
        cp = ConfigParser()
        cp.load("  key  =  value  ")
        assert cp.get("key") == "value"

    def test_invalid_line_no_equals(self):
        cp = ConfigParser()
        with pytest.raises(ValueError):
            cp.load("no_equals_here")

    def test_empty_key_raises(self):
        cp = ConfigParser()
        with pytest.raises(ValueError):
            cp.load("  = value")

    def test_value_with_equals_sign(self):
        cp = ConfigParser()
        cp.load("equation = a = b")
        assert cp.get("equation") == "a = b"


class TestGet:
    def test_existing_key(self):
        cp = ConfigParser()
        cp.load("x = 42")
        assert cp.get("x") == "42"

    def test_missing_key_default_none(self):
        cp = ConfigParser()
        assert cp.get("missing") is None

    def test_missing_key_custom_default(self):
        cp = ConfigParser()
        assert cp.get("missing", "fallback") == "fallback"


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
        assert cp.get_int("missing", 99) == 99


class TestGetBool:
    @pytest.mark.parametrize("val", ["true", "True", "TRUE", "yes", "Yes", "1", "on", "ON"])
    def test_truthy_values(self, val):
        cp = ConfigParser()
        cp.load(f"flag = {val}")
        assert cp.get_bool("flag") is True

    @pytest.mark.parametrize("val", ["false", "False", "FALSE", "no", "No", "0", "off", "OFF"])
    def test_falsy_values(self, val):
        cp = ConfigParser()
        cp.load(f"flag = {val}")
        assert cp.get_bool("flag") is False

    def test_invalid_bool(self):
        cp = ConfigParser()
        cp.load("flag = maybe")
        with pytest.raises(ValueError):
            cp.get_bool("flag")

    def test_missing_key_default(self):
        cp = ConfigParser()
        assert cp.get_bool("missing") is False
        assert cp.get_bool("missing", True) is True


class TestKeysAndToDict:
    def test_keys(self):
        cp = ConfigParser()
        cp.load("a = 1\nb = 2")
        assert cp.keys() == ["a", "b"]

    def test_to_dict(self):
        cp = ConfigParser()
        cp.load("a = 1\nb = 2")
        assert cp.to_dict() == {"a": "1", "b": "2"}

    def test_to_dict_is_copy(self):
        cp = ConfigParser()
        cp.load("a = 1")
        d = cp.to_dict()
        d["a"] = "modified"
        assert cp.get("a") == "1"
