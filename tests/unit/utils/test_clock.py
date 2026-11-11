from __future__ import annotations

from datetime import timezone

import pytest

from benchbox.utils.clock import Stopwatch, elapsed_seconds, measure_elapsed, mono_time, utc_now

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_mono_time_is_monotonic() -> None:
    start = mono_time()
    end = mono_time()
    assert end >= start


def test_elapsed_seconds_uses_passed_end() -> None:
    assert elapsed_seconds(10.0, 12.5) == 2.5


def test_utc_now_is_timezone_aware_utc() -> None:
    now = utc_now()
    assert now.tzinfo is not None
    assert now.tzinfo == timezone.utc


def test_stopwatch_tracks_elapsed() -> None:
    sw = Stopwatch.start()
    assert sw.elapsed_seconds() >= 0.0
    assert sw.elapsed_ms() >= 0.0

    end_utc, elapsed = sw.finish()
    assert end_utc.tzinfo == timezone.utc
    assert elapsed >= 0.0


def test_measure_elapsed_context() -> None:
    with measure_elapsed() as sw:
        assert isinstance(sw, Stopwatch)
        assert sw.elapsed_seconds() >= 0.0


