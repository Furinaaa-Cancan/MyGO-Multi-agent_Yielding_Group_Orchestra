class ConfigParser:
    def __init__(self):
        self._store = {}

    def load(self, text: str) -> None:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                raise ValueError(f"Invalid line (no '=' sign): {line!r}")
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if not key:
                raise ValueError(f"Empty key in line: {line!r}")
            self._store[key] = value

    def get(self, key: str, default=None):
        return self._store.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        if key not in self._store:
            return default
        value = self._store[key]
        try:
            return int(value)
        except (ValueError, TypeError):
            raise ValueError(f"Cannot convert {value!r} to int")

    def get_bool(self, key: str, default: bool = False) -> bool:
        if key not in self._store:
            return default
        value = self._store[key].lower()
        if value in ("true", "yes", "1", "on"):
            return True
        if value in ("false", "no", "0", "off"):
            return False
        raise ValueError(f"Cannot interpret {self._store[key]!r} as boolean")

    def keys(self) -> list[str]:
        return list(self._store.keys())

    def to_dict(self) -> dict[str, str]:
        return dict(self._store)
