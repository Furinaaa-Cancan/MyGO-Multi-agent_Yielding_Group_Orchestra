import pytest
from config_parser import ConfigParser


class TestInit:
    def test_empty_on_creation(self):
        cp = ConfigParser()
        assert cp.keys() == []
        assert cp.to_dict() == {}


class TestLoad:
    def test_basic_key_value(self):
        cp = ConfigParser()
        cp.load("host = localhost")
        assert cp.get("host") == "localhost"

    def test_multiple_entries(self):
        cp = ConfigParser()
        cp.load("host = localhost\nport = 8080\nname = app")
        assert cp.get("host") == "localhost"
        assert cp.get("port") == "8080"
        assert cp.get("name") == "app"

    def test_ignores_blank_lines(self):
        cp = ConfigParser()
        cp.load("host = localhost\n\n\nport = 8080")
        assert len(cp.keys()) == 2

    def test_ignores_comment_lines(self):
        cp = ConfigParser()
        cp.load("# this is a comment\nhost = localhost\n# another comment")
        assert cp.keys() == ["host"]

    def test_strips_whitespace(self):
        cp = ConfigParser()
        cp.load("  host  =  localhost  ")
        assert cp.get("host") == "localhost"

    def test_value_with_equals_sign(self):
        cp = ConfigParser()
        cp.load("formula = a = b + c")
        assert cp.get("formula") == "a = b + c"

    def test_empty_value_allowed(self):
        cp = ConfigParser()
        cp.load("empty_key = ")
        assert cp.get("empty_key") == ""

    def test_raises_on_no_equals(self):
        cp = ConfigParser()
        with pytest.raises(ValueError):
            cp.load("this has no equals sign")

    def test_raises_on_empty_key(self):
        cp = ConfigParser()
        with pytest.raises(ValueError):
            cp.load("  = some_value")

    def test_later_load_overwrites(self):
        cp = ConfigParser()
        cp.load("key = old")
        cp.load("key = new")
        assert cp.get("key") == "new"

    def test_empty_string(self):
        cp = ConfigParser()
        cp.load("")
        assert cp.keys() == []

    def test_only_comments_and_blanks(self):
        cp = ConfigParser()
        cp.load("# comment\n\n# another\n  \n")
        assert cp.keys() == []

    def test_duplicate_keys_last_wins(self):
        cp = ConfigParser()
        cp.load("key = first\nkey = second")
        assert cp.get("key") == "second"

    def test_raises_on_bare_equals(self):
        cp = ConfigParser()
        with pytest.raises(ValueError):
            cp.load("=")

    def test_raises_on_equals_with_no_key(self):
        cp = ConfigParser()
        with pytest.raises(ValueError):
            cp.load("= value")

    def test_comment_with_leading_whitespace(self):
        cp = ConfigParser()
        cp.load("  # indented comment\nhost = localhost")
        assert cp.keys() == ["host"]

    def test_load_preserves_previous_keys(self):
        cp = ConfigParser()
        cp.load("a = 1")
        cp.load("b = 2")
        assert sorted(cp.keys()) == ["a", "b"]

    def test_key_with_no_spaces_around_equals(self):
        cp = ConfigParser()
        cp.load("key=value")
        assert cp.get("key") == "value"

    def test_whitespace_only_value(self):
        cp = ConfigParser()
        cp.load("key =    ")
        assert cp.get("key") == ""

    def test_windows_line_endings(self):
        cp = ConfigParser()
        cp.load("a = 1\r\nb = 2\r\n")
        assert cp.get("a") == "1"
        assert cp.get("b") == "2"

    def test_value_containing_hash(self):
        cp = ConfigParser()
        cp.load("color = #ff0000")
        assert cp.get("color") == "#ff0000"

    def test_inline_comment_not_supported(self):
        # Hash in value is treated as part of the value, not a comment
        cp = ConfigParser()
        cp.load("path = /usr/bin # system path")
        assert cp.get("path") == "/usr/bin # system path"


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


class TestGetInt:
    def test_valid_int(self):
        cp = ConfigParser()
        cp.load("port = 8080")
        assert cp.get_int("port") == 8080

    def test_negative_int(self):
        cp = ConfigParser()
        cp.load("offset = -5")
        assert cp.get_int("offset") == -5

    def test_missing_key_default(self):
        cp = ConfigParser()
        assert cp.get_int("missing") == 0

    def test_missing_key_custom_default(self):
        cp = ConfigParser()
        assert cp.get_int("missing", 42) == 42

    def test_non_int_value_raises(self):
        cp = ConfigParser()
        cp.load("name = hello")
        with pytest.raises(ValueError):
            cp.get_int("name")

    def test_float_string_raises(self):
        cp = ConfigParser()
        cp.load("ratio = 3.14")
        with pytest.raises(ValueError):
            cp.get_int("ratio")

    def test_empty_value_raises(self):
        cp = ConfigParser()
        cp.load("count = ")
        with pytest.raises(ValueError):
            cp.get_int("count")

    def test_whitespace_int_value_raises(self):
        cp = ConfigParser()
        cp.load("port = 80 80")
        with pytest.raises(ValueError):
            cp.get_int("port")


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

    def test_missing_key_default_false(self):
        cp = ConfigParser()
        assert cp.get_bool("missing") is False

    def test_missing_key_custom_default(self):
        cp = ConfigParser()
        assert cp.get_bool("missing", True) is True

    def test_invalid_bool_raises(self):
        cp = ConfigParser()
        cp.load("flag = maybe")
        with pytest.raises(ValueError):
            cp.get_bool("flag")

    def test_empty_value_raises(self):
        cp = ConfigParser()
        cp.load("flag = ")
        with pytest.raises(ValueError):
            cp.get_bool("flag")

    def test_numeric_bool_values(self):
        cp = ConfigParser()
        cp.load("a = 2")
        with pytest.raises(ValueError):
            cp.get_bool("a")


class TestKeys:
    def test_returns_all_keys(self):
        cp = ConfigParser()
        cp.load("a = 1\nb = 2\nc = 3")
        assert sorted(cp.keys()) == ["a", "b", "c"]

    def test_empty_parser(self):
        cp = ConfigParser()
        assert cp.keys() == []

    def test_keys_returns_copy(self):
        cp = ConfigParser()
        cp.load("a = 1")
        keys = cp.keys()
        keys.append("b")
        assert cp.keys() == ["a"]


class TestToDict:
    def test_returns_dict_copy(self):
        cp = ConfigParser()
        cp.load("x = 10\ny = 20")
        d = cp.to_dict()
        assert d == {"x": "10", "y": "20"}
        # Ensure it's a copy, not a reference
        d["z"] = "30"
        assert cp.get("z") is None

    def test_empty_parser(self):
        cp = ConfigParser()
        assert cp.to_dict() == {}
