from __future__ import annotations

import pytest

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.schema import build_result_payload
from benchbox.core.results.subscale_policy import effective_compliance_class

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
        "Power results are below the standard scale and carry no official TPC metrics"
    )


def test_nonstandard_run_records_power_without_a_suppression_marker() -> None:
    result = _build("unofficial_nonstandard", scale_factor=2.0)
    expected = TPCMetricsCalculator.calculate_power_at_size(list(_TIMES.values()), 2.0)

    assert result.power_at_size == pytest.approx(expected)
    metrics = build_result_payload(result)["summary"]["tpc_metrics"]
    assert metrics["power_at_size"] == pytest.approx(expected)
    assert "suppressed" not in metrics


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
