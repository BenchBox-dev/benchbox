from __future__ import annotations

import pytest

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.schema import build_result_payload
from benchbox.core.results.subscale_policy import (
    derive_power_at_size,
    effective_compliance_class,
    subscale_power_at_size,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_TIMES = {"1": 0.02, "2": 0.08}


def _build(compliance_class: str | None, scale_factor: float = 0.01):
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name="TPC-H",
            scale_factor=scale_factor,
            test_type="power",
            benchmark_id="tpch",
            compliance_class=compliance_class,
        ),
        platform=PlatformInfoInput(name="DuckDB", platform_version="1.0.0"),
    )
    for query_id, seconds in _TIMES.items():
        builder.add_query_result(
            normalize_query_result(
                {
                    "query_id": query_id,
                    "execution_time_seconds": seconds,
                    "rows_returned": 1,
                    "status": "SUCCESS",
                    "iteration": 1,
                    "stream_id": 0,
                    "run_type": "measurement",
                }
            )
        )
    return builder.build()


def test_subscale_run_records_power_with_an_unofficial_marker() -> None:
    result = _build("unofficial_subscale")
    expected = TPCMetricsCalculator.calculate_power_at_size(list(_TIMES.values()), 0.01)

    assert result.power_at_size == pytest.approx(expected)
    metrics = build_result_payload(result)["summary"]["tpc_metrics"]
    assert metrics["unofficial"] is True
    assert metrics["reason"] == "compliance_class=unofficial_subscale"
    assert metrics["power_at_size"] == pytest.approx(expected)
    assert "suppressed" not in metrics
    assert "qphh_at_size" not in metrics
    assert metrics["qphh_unavailable"]


def test_subscale_metrics_are_still_refused_for_official_metric_computation() -> None:
    payload = build_result_payload(_build("unofficial_subscale"))

    assert TPCMetricsCalculator.metrics_refusal_reason(payload, "Power") == (
        "Power results are unofficial_subscale and carry no official TPC metrics"
    )


def test_nonstandard_run_keeps_suppressed_metrics() -> None:
    result = _build("unofficial_nonstandard", scale_factor=1.0)

    assert result.power_at_size is None
    metrics = build_result_payload(result)["summary"]["tpc_metrics"]
    assert metrics["suppressed"] is True
    assert "power_at_size" not in metrics


def test_derived_power_matches_the_builder_formula() -> None:
    queries = [
        {"id": "1", "iter": 0, "ms": 90.0, "run_type": "warmup", "status": "SUCCESS"},
        {"id": "1", "iter": 1, "ms": 30.0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "1", "iter": 2, "ms": 20.0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "2", "iter": 2, "ms": 80.0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "3", "iter": 2, "ms": 5.0, "run_type": "measurement", "status": "SUCCESS", "test_type": "throughput"},
    ]

    assert derive_power_at_size(queries, 0.01) == pytest.approx(
        TPCMetricsCalculator.calculate_power_at_size([0.02, 0.08], 0.01)
    )


def test_derived_power_is_unavailable_when_the_final_iteration_failed() -> None:
    queries = [
        {"id": "1", "iter": 1, "ms": 20.0, "run_type": "measurement", "status": "SUCCESS"},
        {"id": "2", "iter": 1, "ms": 80.0, "run_type": "measurement", "status": "FAILED"},
    ]

    assert derive_power_at_size(queries, 0.01) is None


@pytest.mark.parametrize(
    ("benchmark_id", "scale_factor", "recorded", "expected"),
    [
        ("tpch", 0.01, None, "unofficial_subscale"),
        ("tpcds", 0.1, None, "unofficial_subscale"),
        ("tpch", 1.0, None, None),
        ("ssb", 0.01, None, None),
        ("tpch", 0.01, "official", "unofficial_subscale"),
        ("tpcds", 0.1, "unofficial_nonstandard", "unofficial_subscale"),
        ("TPC-H", 0.01, "official", "unofficial_subscale"),
        ("tpc_ds", 0.1, None, "unofficial_subscale"),
        ("tpch", 1.0, "unofficial_nonstandard", "unofficial_nonstandard"),
    ],
)
def test_effective_compliance_class(benchmark_id, scale_factor, recorded, expected) -> None:
    assert effective_compliance_class(benchmark_id, scale_factor, recorded) == expected


def test_derived_power_accepts_pass_statuses() -> None:
    queries = [
        {"id": "1", "iter": 1, "ms": 20.0, "run_type": "measurement", "status": "PASS"},
        {"id": "2", "iter": 1, "ms": 80.0, "run_type": "measurement", "status": "success"},
    ]

    assert derive_power_at_size(queries, 0.01) == pytest.approx(
        TPCMetricsCalculator.calculate_power_at_size([0.02, 0.08], 0.01)
    )


_FULL_RUN = [
    {"id": str(query), "iter": 1, "ms": 20.0, "run_type": "measurement", "status": "SUCCESS"} for query in range(1, 23)
]
_FULL_RUN_POWER = 0.01 * 3600 / 0.02


def test_recorded_subscale_power_within_tolerance_is_kept() -> None:
    assert subscale_power_at_size("tpch", _FULL_RUN_POWER * 1.04, _FULL_RUN, 0.01) == (
        pytest.approx(_FULL_RUN_POWER * 1.04),
        "recorded",
    )


def test_recorded_subscale_power_inconsistent_with_timings_is_not_used() -> None:
    assert subscale_power_at_size("tpch", 1e12, _FULL_RUN, 0.01) == (None, None)


def test_missing_subscale_power_is_derived_from_a_complete_final_iteration() -> None:
    assert subscale_power_at_size("tpch", None, _FULL_RUN, 0.01) == (
        pytest.approx(_FULL_RUN_POWER),
        "derived_from_timings",
    )
    assert subscale_power_at_size("tpch", None, _FULL_RUN[:21], 0.01) == (None, None)


def test_recorded_subscale_power_without_a_complete_final_iteration_is_not_used() -> None:
    assert subscale_power_at_size("tpch", _FULL_RUN_POWER, _FULL_RUN[:21], 0.01) == (None, None)


@pytest.mark.parametrize("bad_ms", [None, 0.0, -1.0, "fast"])
def test_a_final_query_without_a_usable_timing_blocks_derivation(bad_ms) -> None:
    queries = [dict(query) for query in _FULL_RUN]
    queries[5]["ms"] = bad_ms

    assert derive_power_at_size(queries, 0.01, 22) is None
    assert subscale_power_at_size("tpch", _FULL_RUN_POWER, queries, 0.01) == (None, None)
