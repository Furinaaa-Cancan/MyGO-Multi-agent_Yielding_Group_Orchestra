from __future__ import annotations

import pytest

from multi_agent._utils import format_duration


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s"),
        (45, "45s"),
        (59.9, "59s"),
        (150, "2m 30s"),
        (3661, "1h 1m 1s"),
        (86400, "1d 0h 0m 0s"),
        (90061, "1d 1h 1m 1s"),
        (-3, "0s"),
    ],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected
