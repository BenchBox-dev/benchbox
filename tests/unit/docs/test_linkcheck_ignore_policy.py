from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).parents[3]
IGNORE_FILE = ROOT / "docs/linkcheck_ignore.txt"
COMPARE_PREFIX = "https://benchbox\\.dev/results/compare"


def compare_deep_link_pattern() -> re.Pattern[str]:
    patterns = [
        line for line in IGNORE_FILE.read_text(encoding="utf-8").splitlines() if line.startswith(COMPARE_PREFIX)
    ]
    assert len(patterns) == 1
    return re.compile(patterns[0])


def test_compare_deep_link_exception_matches_published_receipts() -> None:
    pattern = compare_deep_link_pattern()

    assert pattern.fullmatch("https://benchbox.dev/results/compare?ids=e3aaa125,9187e38f")
    assert pattern.fullmatch("https://benchbox.dev/results/compare?ids=f552fd5d,aa8b0fad")


@pytest.mark.parametrize(
    "url",
    [
        "https://benchbox.dev/results/",
        "https://benchbox.dev/results/compare",
        "https://benchbox.dev/results/compare?ids=e3aaa125",
        "https://benchbox.dev/results/compare?ids=e3aaa125,not-an-id",
        "https://benchbox.dev/results/result/e3aaa125",
        "https://benchbox.dev/results/data/results.duckdb",
    ],
)
def test_compare_deep_link_exception_does_not_mask_other_live_routes(url: str) -> None:
    assert compare_deep_link_pattern().fullmatch(url) is None
