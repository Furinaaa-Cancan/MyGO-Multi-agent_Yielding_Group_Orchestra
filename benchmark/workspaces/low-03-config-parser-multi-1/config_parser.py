class ConfigParser:
    """A simple key=value configuration parser."""

    def __init__(self):
        """Initialize an empty configuration store."""
        self._store: dict[str, str] = {}

    def load(self, text: str) -> None:
        """Parse 'key = value' format, one entry per line.

        Ignores blank lines and lines starting with '#'.
        Strips whitespace from both keys and values.
        Raises ValueError for invalid lines (no '=' sign, or empty key after stripping).
        """
        for line in text.splitlines():
            stripped = line.strip()

            # Skip blank lines and comments
            if not stripped or stripped.startswith("#"):
                continue

            # Must contain '='
            if "=" not in stripped:
                raise ValueError(f"Invalid line (no '=' sign): {line!r}")

            # Split on first '=' only
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()

            if not key:
                raise ValueError(f"Empty key in line: {line!r}")

            self._store[key] = value

    def get(self, key: str, default=None):
        """Get value by key. Return default if key is not found."""
        return self._store.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        """Get value as an integer.

        Raises ValueError if the value cannot be converted to int.
        Returns default if key is not found.
        """
        value = self._store.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            raise ValueError(
                f"Cannot convert value {value!r} for key {key!r} to int"
            )

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Interpret value as boolean (case-insensitive).

        'true', 'yes', '1', 'on'  -> True
        'false', 'no', '0', 'off' -> False
        Raises ValueError for any other value.
        Returns default if key is not found.
        """
        value = self._store.get(key)
        if value is None:
            return default

        lower = value.lower()
        if lower in ("true", "yes", "1", "on"):
            return True
        if lower in ("false", "no", "0", "off"):
            return False

        raise ValueError(
            f"Cannot convert value {value!r} for key {key!r} to bool"
        )

    def keys(self) -> list[str]:
        """Return a list of all keys."""
        return list(self._store.keys())

    def to_dict(self) -> dict[str, str]:
        """Return the configuration as a plain dictionary (copy)."""
        return dict(self._store)
