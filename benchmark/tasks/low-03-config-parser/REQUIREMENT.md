# Task: Simple Config Parser

Implement `config_parser.py` with a `ConfigParser` class:

1. `__init__(self)` - Initialize an empty configuration store.

2. `load(self, text: str) -> None` - Parse `"key = value"` format, one entry per line. Ignore blank lines and lines starting with `#`. Strip whitespace from both keys and values. Raises `ValueError` for invalid lines (no `=` sign, or empty key after stripping).

3. `get(self, key: str, default=None)` - Get value by key. Return `default` if key is not found.

4. `get_int(self, key: str, default: int = 0) -> int` - Get value as an integer. Raises `ValueError` if the value cannot be converted to int.

5. `get_bool(self, key: str, default: bool = False) -> bool` - Interpret value as boolean: `"true"`, `"yes"`, `"1"`, `"on"` → `True`; `"false"`, `"no"`, `"0"`, `"off"` → `False` (case-insensitive). Raises `ValueError` for any other value.

6. `keys(self) -> list[str]` - Return a list of all keys.

7. `to_dict(self) -> dict[str, str]` - Return the configuration as a plain dictionary.

Also create `test_config_parser.py` with tests for your implementation.
