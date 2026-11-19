from __future__ import annotations

import pytest

from benchbox.validation.bundle import _measurement_ms_by_query, throughput_stream_defects
from tests.unit.scripts.explorer_pipeline.conftest import throughput_bundle

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _first_stream_query(payload: dict) -> dict:
    return next(query for query in payload["queries"] if query["stream"] == 1)


def test_successful_sub_millisecond_execution_is_covered() -> None:
    payload = throughput_bundle("duckdb", streams=3)
    _first_stream_query(payload)["ms"] = 0.4

    assert throughput_stream_defects(payload) == []


def test_successful_zero_duration_execution_is_covered() -> None:
    payload = throughput_bundle("duckdb", streams=3)
    _first_stream_query(payload)["ms"] = 0.0

    assert throughput_stream_defects(payload) == []


def test_missing_execution_still_fails_coverage() -> None:
    payload = throughput_bundle("duckdb", streams=3)
    dropped = _first_stream_query(payload)
    payload["queries"].remove(dropped)

    defects = throughput_stream_defects(payload)

    assert any("throughput stream 1 covers" in item and str(dropped["id"]) in item for item in defects)


def test_failed_execution_does_not_cover() -> None:
    payload = throughput_bundle("duckdb", streams=3)
    failed = _first_stream_query(payload)
    failed["status"] = "FAILED"
    failed["ms"] = 0.4

    assert any("throughput stream 1 covers" in item for item in throughput_stream_defects(payload))


def test_timing_aggregation_still_excludes_sub_millisecond_values() -> None:
    payload = throughput_bundle("duckdb", streams=3)
    sub_ms = _first_stream_query(payload)
    sub_ms["ms"] = 0.4
    qid = sub_ms["id"]

    grouped = _measurement_ms_by_query(payload)

    assert all(value >= 1.0 for samples in grouped.values() for value in samples)
    assert len(grouped[qid]) == sum(1 for query in payload["queries"] if query["id"] == qid) - 1
