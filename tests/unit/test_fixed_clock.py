from __future__ import annotations

import os
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.utilities.fixed_clock import FIXED_EPOCH, FIXED_NOW, fixed_datetime_class, freeze_datetime, set_mtimes

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def test_now_utcnow_and_today_report_the_fixed_instant() -> None:
    frozen = fixed_datetime_class(FIXED_NOW)
    assert frozen.now() == datetime(2026, 1, 15, 12, 0, 0)
    assert frozen.now(timezone.utc) == FIXED_NOW
    assert frozen.utcnow() == datetime(2026, 1, 15, 12, 0, 0)
    assert frozen.today() == datetime(2026, 1, 15, 12, 0, 0)
    assert frozen.now().tzinfo is None
    assert frozen.utcnow().tzinfo is None


def test_now_converts_to_the_requested_zone() -> None:
    frozen = fixed_datetime_class(FIXED_NOW)
    plus_two = timezone(timedelta(hours=2))
    shifted = frozen.now(plus_two)
    assert shifted == FIXED_NOW
    assert shifted.hour == 14
    assert shifted.utcoffset() == timedelta(hours=2)


def test_an_aware_instant_reports_its_own_wall_fields_when_naive() -> None:
    plus_two = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    frozen = fixed_datetime_class(plus_two)
    assert frozen.now() == datetime(2026, 1, 15, 14, 0, 0)
    assert frozen.utcnow() == datetime(2026, 1, 15, 12, 0, 0)


def test_a_naive_instant_is_read_as_utc() -> None:
    frozen = fixed_datetime_class(datetime(2026, 1, 15, 12, 0, 0))
    assert frozen.now(timezone.utc) == FIXED_NOW
    assert frozen.now() == datetime(2026, 1, 15, 12, 0, 0)


def test_real_datetimes_still_pass_isinstance_and_construction_is_unchanged() -> None:
    frozen = fixed_datetime_class(FIXED_NOW)
    assert isinstance(datetime(2020, 1, 1), frozen)
    assert isinstance(frozen.now(), frozen)
    assert issubclass(datetime, frozen)
    assert frozen(2026, 3, 4, 5, 6, 7) == datetime(2026, 3, 4, 5, 6, 7)
    assert frozen.fromisoformat("2026-02-03T04:05:06") == datetime(2026, 2, 3, 4, 5, 6)


def test_freeze_datetime_changes_only_the_named_module_and_restores_it() -> None:
    consumer = types.ModuleType("fixed_clock_consumer")
    consumer.datetime = datetime
    other = types.ModuleType("fixed_clock_other")
    other.datetime = datetime
    with pytest.MonkeyPatch.context() as patch:
        frozen = freeze_datetime(patch, consumer)
        assert consumer.datetime is frozen
        assert consumer.datetime.now(timezone.utc) == FIXED_NOW
        assert other.datetime is datetime
        assert datetime.now(timezone.utc) != FIXED_NOW
    assert consumer.datetime is datetime


def test_freeze_datetime_accepts_another_name_and_instant() -> None:
    consumer = types.ModuleType("fixed_clock_consumer")
    consumer.dt = datetime
    later = datetime(2027, 6, 7, 8, 9, 10, tzinfo=timezone.utc)
    with pytest.MonkeyPatch.context() as patch:
        freeze_datetime(patch, consumer, later, attr="dt")
        assert consumer.dt.now(timezone.utc) == later


def test_freeze_datetime_rejects_a_name_that_is_not_the_datetime_class() -> None:
    import datetime as datetime_module

    consumer = types.ModuleType("fixed_clock_consumer")
    consumer.datetime = datetime_module
    with pytest.MonkeyPatch.context() as patch:
        with pytest.raises(TypeError, match="not the datetime class"):
            freeze_datetime(patch, consumer)
    assert consumer.datetime is datetime_module


def test_set_mtimes_orders_oldest_first_and_ends_at_newest(tmp_path: Path) -> None:
    files = [tmp_path / name for name in ("a.json", "b.json", "c.json")]
    for path in files:
        path.write_text("{}")
    stamps = set_mtimes(files)
    assert stamps == [FIXED_EPOCH - 200.0, FIXED_EPOCH - 100.0, FIXED_EPOCH]
    assert [os.stat(path).st_mtime for path in files] == stamps


def test_set_mtimes_ignores_creation_order_and_speed(tmp_path: Path) -> None:
    newer, older = tmp_path / "newer.json", tmp_path / "older.json"
    newer.write_text("{}")
    older.write_text("{}")
    set_mtimes([older, newer], newest=1_000_000.0, spacing=5.0)
    assert os.stat(older).st_mtime == 999_995.0
    assert os.stat(newer).st_mtime == 1_000_000.0
    assert sorted([newer, older], key=lambda path: os.stat(path).st_mtime) == [older, newer]


def test_set_mtimes_handles_no_files_and_rejects_a_bad_spacing(tmp_path: Path) -> None:
    assert set_mtimes([]) == []
    path = tmp_path / "a.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="spacing"):
        set_mtimes([path], spacing=0)
    with pytest.raises(ValueError, match="spacing"):
        set_mtimes([path], spacing=-1.0)


