from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.results.metrics import geometric_mean_ms, percentile_ms

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SHARED_TIMINGS: list[float] = [
    12.0,
    45.0,
    8.0,
    120.0,
    33.0,
    5.0,
    67.0,
    89.0,
    15.0,
    200.0,
    42.0,
    7.0,
    310.0,
    55.0,
    23.0,
    98.0,
    140.0,
    11.0,
    76.0,
    29.0,
    64.0,
    180.0,
]


def _cli_shaped_bundle(timings: list[float]) -> dict:
    return {
        "execution": {"platform": "duckdb", "timestamp": "2026-08-04T00:00:00Z", "duration_ms": sum(timings)},
        "benchmark": {"name": "tpch"},
        "configuration": {"scale_factor": 1.0},
        "results": {
            "queries": {
                "details": [
                    {"query_id": str(i + 1), "status": "SUCCESS", "timing": {"execution_ms": ms}}
                    for i, ms in enumerate(timings)
                ]
            }
        },
    }


def _mcp_shaped_bundle(timings: list[float]) -> dict:
    return {
        "run": {"platform": "duckdb", "benchmark": "tpch", "timestamp": "2026-08-04T00:00:00Z"},
        "benchmark": {"scale_factor": 1.0},
        "queries": [{"query_id": str(i + 1), "run_type": "measurement", "ms": ms} for i, ms in enumerate(timings)],
    }


class TestCrossSurfaceStatisticsEquivalence:
    def test_cli_and_mcp_report_the_same_percentiles(self):
        from benchbox.cli.commands.aggregate import _extract_result_row
        from benchbox.core.results.metrics import calculate_named_metric
        from benchbox.mcp.tools.analytics import _extract_measurement_timings

        cli_row = _extract_result_row(_cli_shaped_bundle(SHARED_TIMINGS), Path("run.json"))
        mcp_timings = _extract_measurement_timings(_mcp_shaped_bundle(SHARED_TIMINGS))

        assert cli_row["p50_ms"] == calculate_named_metric(mcp_timings, "p50")
        assert cli_row["p95_ms"] == calculate_named_metric(mcp_timings, "p95")
        assert cli_row["p99_ms"] == calculate_named_metric(mcp_timings, "p99")
        assert cli_row["geometric_mean_ms"] == calculate_named_metric(mcp_timings, "geometric_mean")

    def test_both_surfaces_extract_the_same_measurement_set(self):
        from benchbox.cli.commands.aggregate import _successful_query_times_ms
        from benchbox.mcp.tools.analytics import _extract_measurement_timings

        cli_bundle = _cli_shaped_bundle(SHARED_TIMINGS)
        cli_times = _successful_query_times_ms(cli_bundle["results"]["queries"]["details"])
        mcp_times = _extract_measurement_timings(_mcp_shaped_bundle(SHARED_TIMINGS))

        assert cli_times == mcp_times == SHARED_TIMINGS

    def test_both_surfaces_drop_the_same_non_measurements(self):
        from benchbox.cli.commands.aggregate import _successful_query_times_ms
        from benchbox.mcp.tools.analytics import _extract_measurement_timings

        cli_details = [
            {"query_id": "1", "status": "SUCCESS", "timing": {"execution_ms": 10.0}},
            {"query_id": "2", "status": "FAILED", "timing": {"execution_ms": 999.0}},
            {"query_id": "3", "status": "SUCCESS", "timing": {"execution_ms": 0}},
            {"query_id": "4", "status": "SUCCESS", "timing": {"execution_ms": 20.0}},
        ]
        mcp_queries = {
            "queries": [
                {"query_id": "1", "run_type": "measurement", "ms": 10.0},
                {"query_id": "2", "run_type": "warmup", "ms": 999.0},
                {"query_id": "3", "run_type": "measurement", "ms": 0},
                {"query_id": "4", "run_type": "measurement", "ms": 20.0},
            ]
        }

        assert _successful_query_times_ms(cli_details) == [10.0, 20.0]
        assert _extract_measurement_timings(mcp_queries) == [10.0, 20.0]

    def test_aggregate_row_uses_the_core_implementation(self):
        from benchbox.cli.commands.aggregate import _extract_result_row

        row = _extract_result_row(_cli_shaped_bundle(SHARED_TIMINGS), Path("run.json"))

        assert row["p50_ms"] == percentile_ms(SHARED_TIMINGS, 0.50)
        assert row["p95_ms"] == percentile_ms(SHARED_TIMINGS, 0.95)
        assert row["p99_ms"] == percentile_ms(SHARED_TIMINGS, 0.99)
        assert row["geometric_mean_ms"] == geometric_mean_ms(SHARED_TIMINGS)

    def test_mcp_group_stats_use_the_core_implementation(self):
        from benchbox.core.results.metrics import sample_stdev_ms
        from benchbox.mcp.tools.analytics import _compute_group_stats

        runs = [{"timings": SHARED_TIMINGS, "total_time": sum(SHARED_TIMINGS), "file": "run.json"}]
        stats = _compute_group_stats(runs)["query_stats"]

        assert stats["p50_ms"] == round(percentile_ms(SHARED_TIMINGS, 0.50), 2)
        assert stats["p95_ms"] == round(percentile_ms(SHARED_TIMINGS, 0.95), 2)
        assert stats["std_ms"] == round(sample_stdev_ms(SHARED_TIMINGS), 2)

    def test_aggregate_csv_column_contract_is_unchanged(self):
        from benchbox.cli.commands.aggregate import _extract_result_row

        row = _extract_result_row(_cli_shaped_bundle(SHARED_TIMINGS), Path("run.json"))

        assert list(row) == [
            "timestamp",
            "benchmark",
            "platform",
            "scale_factor",
            "total_time_s",
            "geometric_mean_ms",
            "p50_ms",
            "p95_ms",
            "p99_ms",
            "num_queries",
            "file",
        ]

    def test_no_surface_keeps_a_private_statistics_implementation(self):
        import benchbox.cli.commands.aggregate as aggregate_module
        import benchbox.mcp.tools.analytics as analytics_module

        for module in (aggregate_module, analytics_module):
            for banned in ("_percentile", "_std_dev", "_calculate_metric", "_compute_query_statistics"):
                assert not hasattr(module, banned), f"{module.__name__}.{banned}"

    def test_written_csv_matches_the_computed_row(self, tmp_path: Path):
        from benchbox.cli.commands.aggregate import _extract_result_row, _write_aggregated_csv

        row = _extract_result_row(_cli_shaped_bundle(SHARED_TIMINGS), Path("run.json"))
        output = tmp_path / "trends.csv"
        _write_aggregated_csv(output, [row])

        header, values = output.read_text(encoding="utf-8").splitlines()[:2]
        columns = dict(zip(header.split(","), values.split(","), strict=True))

        assert float(columns["p95_ms"]) == percentile_ms(SHARED_TIMINGS, 0.95)
        assert json.loads(columns["num_queries"]) == len(SHARED_TIMINGS)


