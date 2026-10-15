from __future__ import annotations

from copy import deepcopy

import pytest

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.results.models import ExecutionPhases, PowerTestPhase, SetupPhase, ThroughputTestPhase
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.schema import build_result_payload

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpch]


def _function(function: str, set_id: int) -> dict:
    return {"function": function, "update_set": set_id, "duration_ms": 15, "status": "SUCCESS"}


def _eligible_results() -> tuple[dict, dict]:
    power = {
        "benchmark": {"id": "tpch", "name": "TPC-H", "compliance_class": "official"},
        "summary": {"queries": {"failed": 0}},
        "phases": {
            "power_test": {
                "status": "COMPLETED",
                "query_count": 22,
                "refresh_first_set": 5,
                "refresh_functions": [_function("RF1", 5), _function("RF2", 5)],
            }
        },
    }
    throughput = {
        "benchmark": {"id": "tpch", "name": "TPC-H", "compliance_class": "official"},
        "summary": {"queries": {"failed": 0}},
        "phases": {
            "throughput_test": {
                "status": "COMPLETED",
                "num_streams": 2,
                "refresh_stream": {
                    "first_set": 6,
                    "status": "COMPLETED",
                    "start_time": "2026-01-01T00:00:00",
                    "end_time": "2026-01-01T00:00:01",
                    "pair_count": 2,
                    "pairs": [
                        {
                            "update_set": set_id,
                            "status": "SUCCESS",
                            "functions": [_function("RF1", set_id), _function("RF2", set_id)],
                        }
                        for set_id in (6, 7)
                    ],
                },
            }
        },
    }
    return power, throughput


def _result_payload(scale_factor: float, compliance_class: str, *, power_refresh: bool = True) -> dict:
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name="TPC-H",
            scale_factor=scale_factor,
            test_type="combined",
            benchmark_id="tpch",
            compliance_class=compliance_class,
        ),
        platform=PlatformInfoInput(name="DuckDB", execution_mode="sql"),
    )
    for query_id in range(1, 23):
        builder.add_query_result(
            normalize_query_result(
                {
                    "query_id": str(query_id),
                    "status": "SUCCESS",
                    "execution_time_seconds": 1.0,
                    "iteration": 1,
                    "stream_id": 0,
                    "run_type": "measurement",
                    "test_type": "power",
                }
            )
        )
    for stream_id in (1, 2):
        for query_id in range(1, 23):
            builder.add_query_result(
                normalize_query_result(
                    {
                        "query_id": str(query_id),
                        "status": "SUCCESS",
                        "execution_time_seconds": 1.0,
                        "iteration": 1,
                        "stream_id": stream_id,
                        "run_type": "measurement",
                        "test_type": "throughput",
                    }
                )
            )
    power_functions = [_function("RF1", 5), _function("RF2", 5)]
    throughput_stream = {
        "first_set": 6,
        "status": "COMPLETED",
        "pair_count": 2,
        "pairs": [
            {
                "update_set": set_id,
                "status": "SUCCESS",
                "functions": [_function("RF1", set_id), _function("RF2", set_id)],
            }
            for set_id in (6, 7)
        ],
        "start_time": "2026-01-01T00:00:00",
        "end_time": "2026-01-01T00:00:01",
        "error": None,
    }
    builder.set_execution_phases(
        ExecutionPhases(
            setup=SetupPhase(),
            power_test=PowerTestPhase(
                start_time="2026-01-01T00:00:00",
                end_time="2026-01-01T00:00:01",
                duration_ms=1000,
                query_executions=[],
                geometric_mean_time=1.0,
                power_at_size=100.0,
                refresh_first_set=5 if power_refresh else None,
                refresh_functions=power_functions if power_refresh else None,
                query_count=22,
                success=True,
            ),
            throughput_test=ThroughputTestPhase(
                start_time="2026-01-01T00:00:00",
                end_time="2026-01-01T00:00:01",
                duration_ms=1000,
                num_streams=2,
                streams=[],
                total_queries_executed=44,
                throughput_at_size=200.0,
                success=True,
                refresh_stream=throughput_stream,
            ),
        )
    )
    return build_result_payload(builder.build())


def test_duckdb_sf001_records_refresh_evidence_without_qphh() -> None:
    payload = _result_payload(0.01, "unofficial_subscale")
    metrics = payload["summary"]["tpc_metrics"]

    assert payload["platform"]["name"] == "DuckDB"
    assert payload["benchmark"]["scale_factor"] == 0.01
    assert "qphh_at_size" not in metrics
    assert metrics["refresh_included"] is True
    assert any("not official compliance" in reason for reason in metrics["qphh_unavailable"])
    assert len(payload["phases"]["power_test"]["refresh_functions"]) == 2
    assert len(payload["phases"]["throughput_test"]["refresh_stream"]["pairs"]) == 2
    assert len(payload["queries"]) == 66
    assert [query["id"] for query in payload["queries"] if query["stream"] == 0] == [
        str(query_id) for query_id in range(1, 23)
    ]


def test_result_omits_composite_without_power_refresh_evidence() -> None:
    payload = _result_payload(1.0, "official", power_refresh=False)
    metrics = payload["summary"]["tpc_metrics"]

    assert "qphh_at_size" not in metrics
    assert metrics["qphh_unavailable"]


def test_duckdb_sf1_two_streams_emits_qphh_for_qualified_refresh_evidence() -> None:
    payload = _result_payload(1.0, "official")
    metrics = payload["summary"]["tpc_metrics"]

    assert payload["platform"]["name"] == "DuckDB"
    assert payload["benchmark"]["scale_factor"] == 1.0
    assert payload["run"]["streams"] == 2
    assert metrics["qphh_at_size"] == 141.4
    assert metrics["refresh_included"] is True
    assert payload["phases"]["power_test"]["refresh_first_set"] == 5
    assert payload["phases"]["throughput_test"]["refresh_stream"]["first_set"] == 6
    assert len(payload["queries"]) == 66
    assert [query["id"] for query in payload["queries"] if query["stream"] == 0] == [
        str(query_id) for query_id in range(1, 23)
    ]


def test_sf1_official_two_stream_results_emit_qphh_only_when_refresh_evidence_qualifies() -> None:
    power, throughput = _eligible_results()
    power["summary"]["tpc_metrics"] = {"power_at_size": 100.0}
    throughput["summary"]["tpc_metrics"] = {"throughput_at_size": 200.0}

    result = TPCMetricsCalculator.compute_qphh_result(power, throughput, scale_factor=1.0)

    assert result["qphh_at_size"] == 141.4
    assert result["refresh_included"] is True
    assert len(power["phases"]["power_test"]["refresh_functions"]) == 2
    assert len(throughput["phases"]["throughput_test"]["refresh_stream"]["pairs"]) == 2


def test_sf001_unofficial_results_keep_refresh_evidence_but_withhold_qphh() -> None:
    power, throughput = _eligible_results()
    power["benchmark"]["compliance_class"] = "unofficial_subscale"
    throughput["benchmark"]["compliance_class"] = "unofficial_subscale"
    power["summary"]["tpc_metrics"] = {"power_at_size": 100.0}
    throughput["summary"]["tpc_metrics"] = {"throughput_at_size": 200.0}

    with pytest.raises(ValueError, match="not official compliance"):
        TPCMetricsCalculator.compute_qphh_result(power, throughput, scale_factor=0.01)

    assert len(power["phases"]["power_test"]["refresh_functions"]) == 2
    assert len(throughput["phases"]["throughput_test"]["refresh_stream"]["pairs"]) == 2


def test_official_22_query_power_and_consecutive_refresh_pairs_qualify_qphh() -> None:
    power, throughput = _eligible_results()

    reasons = TPCMetricsCalculator.qphh_unavailable_reasons(power, throughput)

    assert reasons == []


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    [
        (
            lambda power, throughput: power["benchmark"].update(compliance_class="unofficial_subscale"),
            "not official compliance",
        ),
        (
            lambda power, throughput: throughput["benchmark"].update(compliance_class="unofficial_subscale"),
            "not official compliance",
        ),
        (lambda power, throughput: power["benchmark"].update(id="tpcds"), "not TPC-H"),
        (
            lambda power, throughput: power["phases"]["power_test"].update(status="FAILED"),
            "Power phase did not complete",
        ),
        (
            lambda power, throughput: throughput["phases"]["throughput_test"].update(status="FAILED"),
            "Throughput phase did not complete",
        ),
        (lambda power, throughput: power["phases"]["power_test"].update(query_count=21), "all 22 TPC-H queries"),
        (
            lambda power, throughput: power["phases"]["power_test"].update(refresh_functions=[]),
            "successful RF1 and RF2",
        ),
        (
            lambda power, throughput: power["phases"]["power_test"]["refresh_functions"][0].update(status="FAILED"),
            "successful RF1 and RF2",
        ),
        (
            lambda power, throughput: power["phases"]["power_test"]["refresh_functions"][0].update(update_set=6),
            "successful RF1 and RF2",
        ),
        (
            lambda power, throughput: power["phases"]["power_test"]["refresh_functions"][0].update(duration_ms=-1),
            "successful RF1 and RF2",
        ),
        (
            lambda power, throughput: power["summary"]["queries"].update(failed=1),
            "Power results contain 1 failed queries",
        ),
        (
            lambda power, throughput: throughput["summary"]["queries"].update(failed=2),
            "Throughput results contain 2 failed queries",
        ),
        (
            lambda power, throughput: throughput["phases"]["throughput_test"].update(num_streams=1),
            "at least two throughput streams",
        ),
        (
            lambda power, throughput: throughput["phases"]["throughput_test"]["refresh_stream"].update(first_set=7),
            "immediately after the Power set",
        ),
        (
            lambda power, throughput: throughput["phases"]["throughput_test"]["refresh_stream"].update(pair_count=1),
            "successful RF1/RF2 pair per query stream",
        ),
        (
            lambda power, throughput: throughput["phases"]["throughput_test"].update(num_streams=3),
            "successful RF1/RF2 pair per query stream",
        ),
        (
            lambda power, throughput: throughput["phases"]["throughput_test"]["refresh_stream"].update(start_time=""),
            "successful RF1/RF2 pair per query stream",
        ),
        (
            lambda power, throughput: throughput["phases"]["throughput_test"]["refresh_stream"].update(end_time=""),
            "successful RF1/RF2 pair per query stream",
        ),
        (
            lambda power, throughput: throughput["phases"]["throughput_test"]["refresh_stream"]["pairs"][0][
                "functions"
            ][1].update(update_set=88),
            "successful RF1/RF2 pair per query stream",
        ),
        (
            lambda power, throughput: throughput["phases"]["throughput_test"]["refresh_stream"]["pairs"][0][
                "functions"
            ][0].update(status="FAILED"),
            "successful RF1/RF2 pair per query stream",
        ),
        (
            lambda power, throughput: throughput["phases"]["throughput_test"]["refresh_stream"]["pairs"][1].update(
                status="FAILED"
            ),
            "successful RF1/RF2 pair per query stream",
        ),
    ],
    ids=[
        "power-official-compliance-required",
        "throughput-official-compliance-required",
        "official-tpch-required",
        "power-phase-must-complete",
        "throughput-phase-must-complete",
        "all-22-power-queries-required",
        "power-refresh-pair-required",
        "power-refresh-functions-must-succeed",
        "power-refresh-functions-must-use-power-set",
        "power-refresh-durations-must-be-non-negative",
        "power-queries-must-not-fail",
        "throughput-queries-must-not-fail",
        "two-stream-minimum",
        "separate-result-sets-are-consecutive",
        "refresh-pair-count-required",
        "refresh-pairs-match-stream-count",
        "refresh-start-time-required",
        "refresh-end-time-required",
        "refresh-function-set-must-match-pair",
        "throughput-refresh-functions-must-succeed",
        "all-refresh-pairs-succeed",
    ],
)
def test_qphh_refuses_unqualified_results(mutate, expected_reason: str) -> None:
    power, throughput = _eligible_results()
    power = deepcopy(power)
    throughput = deepcopy(throughput)
    mutate(power, throughput)

    reasons = TPCMetricsCalculator.qphh_unavailable_reasons(power, throughput)

    assert any(expected_reason in reason for reason in reasons)


def test_qphh_accepts_zero_duration_successful_refresh() -> None:
    power, throughput = _eligible_results()
    power = deepcopy(power)
    throughput = deepcopy(throughput)
    power["summary"]["tpc_metrics"] = {"power_at_size": 100.0}
    throughput["summary"]["tpc_metrics"] = {"throughput_at_size": 200.0}
    power["phases"]["power_test"]["refresh_functions"][0]["duration_ms"] = 0
    throughput["phases"]["throughput_test"]["refresh_stream"]["pairs"][0]["functions"][0]["duration_ms"] = 0

    reasons = TPCMetricsCalculator.qphh_unavailable_reasons(power, throughput)

    assert reasons == []
    result = TPCMetricsCalculator.compute_qphh_result(power, throughput, scale_factor=1.0)
    assert result["qphh_at_size"] == 141.4
    assert result["refresh_included"] is True
