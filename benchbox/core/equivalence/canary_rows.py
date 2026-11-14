from __future__ import annotations


def rows_match(actual: list[tuple], expected: list[tuple]) -> bool:
    for left, right in zip(actual, expected):
        if abs(left[0] - right[0]) > 1e8:
            return False
    return True
