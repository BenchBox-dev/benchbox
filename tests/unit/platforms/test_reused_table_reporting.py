from __future__ import annotations

import pytest

from benchbox.core.results.result_factory import _add_table_statistics
from benchbox.platforms.base.phase_tracking import PhaseTrackingMixin, reused_tables

pytestmark = [pytest.mark.unit, pytest.mark.fast]

STATS = {"alpha": 4, "beta": 3}
TIMINGS = {"alpha": {"total_ms": 0, "reused": True}, "beta": {"total_ms": 120}}


class RecordingBuilder:
    def __init__(self) -> None:
        self.calls: dict[str, dict] = {}

    def add_table_stats(self, name, rows, load_time_ms=0, status="SUCCESS", error_message=None):
        self.calls[name] = {"rows": rows, "load_time_ms": load_time_ms, "status": status}


def test_reused_tables_reads_the_flag():
    assert reused_tables(TIMINGS) == {"alpha"}
    assert reused_tables(None) == set()
    assert reused_tables({"a": {"total_ms": 1}}) == set()


def test_phase_does_not_count_reused_tables_as_loaded():
    phase = PhaseTrackingMixin._create_enhanced_data_loading_phase(object(), STATS, 0.12, TIMINGS)

    assert phase.per_table_stats["alpha"].status == "REUSED"
    assert phase.per_table_stats["alpha"].load_time_ms == 0
    assert phase.per_table_stats["beta"].status == "SUCCESS"
    assert phase.total_rows_loaded == 3
    assert phase.tables_loaded == 1


def test_table_statistics_mark_reused_tables():
    builder = RecordingBuilder()

    _add_table_statistics(builder, {"table_statistics": STATS, "per_table_timings": TIMINGS})

    assert builder.calls["alpha"]["status"] == "REUSED"
    assert builder.calls["beta"]["status"] == "SUCCESS"
    assert builder.calls["beta"]["load_time_ms"] == 120
