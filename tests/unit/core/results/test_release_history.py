from __future__ import annotations

import pytest

from benchbox.core.results.release_history import resolve_benchbox_version

pytestmark = pytest.mark.fast


def test_recorded_version_wins() -> None:
    assert resolve_benchbox_version(" 0.5.0rc1 ", "2026-10-28T10:00:00") == ("0.5.0rc1", "recorded")


@pytest.mark.parametrize(
    ("timestamp", "version"),
    [("2026-11-04T00:00:00", "0.5.0"), ("2026-11-03T23:59:59", "0.4.2"), ("2026-12-01T00:00:00Z", "0.5.1")],
)
def test_inferred_version_is_the_latest_release_on_or_before_the_run_date(timestamp: str, version: str) -> None:
    assert resolve_benchbox_version(None, timestamp) == (version, "inferred_from_run_date")


@pytest.mark.parametrize("timestamp", [None, "2025-12-31T00:00:00", "garbage"])
def test_no_version_when_nothing_can_be_inferred(timestamp: object) -> None:
    assert resolve_benchbox_version(None, timestamp) == (None, None)
