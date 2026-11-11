from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import _aggregate_scan_bytes, build_result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _results(query_results: list[dict]) -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name="TPC-H",
        platform="BigQuery",
        scale_factor=1.0,
        execution_id="abc12345",
        timestamp=datetime.now(),
        duration_seconds=10.0,
        total_queries=len(query_results),
        successful_queries=len(query_results),
        failed_queries=0,
        query_results=query_results,
        platform_info={"platform_name": "BigQuery"},
    )


class TestAggregateScanBytes:
    def test_sums_bigquery_billed_and_processed(self) -> None:
        results = _results(
            [
                {
                    "query_id": "1",
                    "execution_time_ms": 100.0,
                    "rows_returned": 1,
                    "status": "SUCCESS",
                    "resource_usage": {"bytes_billed": 100, "bytes_processed": 90},
                },
                {
                    "query_id": "2",
                    "execution_time_ms": 200.0,
                    "rows_returned": 2,
                    "status": "SUCCESS",
                    "resource_usage": {"bytes_billed": 200, "bytes_processed": 180},
                },
            ]
        )
        assert _aggregate_scan_bytes(results) == {
            "total_bytes_billed": 300,
            "total_bytes_scanned": 270,
        }

    def test_supports_athena_scanned_key(self) -> None:
        results = _results(
            [
                {
                    "query_id": "1",
                    "execution_time_ms": 100.0,
                    "rows_returned": 1,
                    "status": "SUCCESS",
                    "resource_usage": {"data_scanned_bytes": 512},
                }
            ]
        )
        assert _aggregate_scan_bytes(results) == {"total_bytes_scanned": 512}

    def test_returns_none_without_byte_metrics(self) -> None:
        results = _results([{"query_id": "1", "execution_time_ms": 100.0, "rows_returned": 1, "status": "SUCCESS"}])
        assert _aggregate_scan_bytes(results) is None

    def test_payload_surfaces_summary_cost(self) -> None:
        results = _results(
            [
                {
                    "query_id": "1",
                    "execution_time_ms": 100.0,
                    "rows_returned": 1,
                    "status": "SUCCESS",
                    "resource_usage": {"bytes_billed": 1024, "bytes_processed": 512},
                }
            ]
        )
        payload = build_result_payload(results)
        assert payload["summary"]["cost"] == {
            "total_bytes_billed": 1024,
            "total_bytes_scanned": 512,
        }

    def test_payload_omits_summary_cost_without_metrics(self) -> None:
        results = _results([{"query_id": "1", "execution_time_ms": 100.0, "rows_returned": 1, "status": "SUCCESS"}])
        payload = build_result_payload(results)
        assert "cost" not in payload["summary"]

    def test_payload_omits_top_level_cost_for_scan_only_summary(self) -> None:
        results = _results([{"query_id": "1", "execution_time_ms": 100.0, "rows_returned": 1, "status": "SUCCESS"}])
        results.cost_summary = {"scan_bytes": {"total_bytes_scanned": 512}}

        payload = build_result_payload(results)

        assert payload["summary"]["cost"] == {"total_bytes_scanned": 512}
        assert "cost" not in payload


