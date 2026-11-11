from __future__ import annotations

import pytest

from benchbox.utils.toggles import is_probe_requested

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        (True, True),
        (False, False),
        ("false", False),
        ("False", False),
        ("0", False),
        (0, False),
        ("no", False),
        ("off", False),
        ("nope", False),
        ("true", True),
        ("1", True),
        (1, True),
        ("yes", True),
    ],
)
def test_is_probe_requested(value: object, expected: bool) -> None:
    assert is_probe_requested(value) is expected


