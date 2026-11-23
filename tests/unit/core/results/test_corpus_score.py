from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.core.results import corpus_rule, corpus_score
from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.results.models import (
    ExecutionPhases,
    PowerTestPhase,
    QueryExecution,
    SetupPhase,
    ThroughputStream,
    ThroughputTestPhase,
)
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.schema import build_result_payload
from benchbox.core.tpch.compliance import validate_tpch_scale
from benchbox.platforms.dataframe.benchmark_mixin import dataframe_compliance_class
from tests.fixtures.result_dict_fixtures import make_benchmark_results
from tests.unit.scripts.explorer_pipeline.conftest import combined_bundle

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[4]
BUNDLES = REPO_ROOT / "results-data" / "bundles"
SNAPSHOT = REPO_ROOT / "results-data" / "compliance-classes.json"
RANKED_TOLERANCE = 0.005


def _full_run(seconds: float = 0.02, status: str = "SUCCESS") -> list[dict]:
    return [
        {"id": str(number), "iter": 1, "ms": seconds * 1000, "run_type": "measurement", "status": status}
        for number in range(1, 23)
    ]


def _data(queries: list[dict], scale: float = 0.01, benchmark: str = "tpch") -> dict:
    return {"benchmark": {"id": benchmark, "scale_factor": scale}, "queries": queries}


def test_power_matches_the_calculator_formula() -> None:
    queries = _full_run()
    queries[0] = {**queries[0], "ms": 40.0}

    expected = TPCMetricsCalculator.calculate_power_at_size([0.04] + [0.02] * 21, 0.01)

    assert corpus_score.power_at_size(_data(queries)) == pytest.approx(expected)


def test_power_uses_the_final_measured_iteration_only() -> None:
    queries = [
        {"id": "1", "iter": 0, "ms": 9000.0, "run_type": "warmup", "status": "SUCCESS"},
        *[{**query, "iter": 1, "ms": 900.0} for query in _full_run()],
        *[{**query, "iter": 2} for query in _full_run(0.02)],
        {"id": "1", "iter": 2, "ms": 5.0, "run_type": "measurement", "status": "SUCCESS", "test_type": "throughput"},
    ]

    assert corpus_score.power_at_size(_data(queries)) == pytest.approx(0.01 * 3600 / 0.02)


@pytest.mark.parametrize("status", ["PASS", "success"])
def test_power_accepts_pass_statuses(status: str) -> None:
    assert corpus_score.power_at_size(_data(_full_run(status=status))) == pytest.approx(0.01 * 3600 / 0.02)


@pytest.mark.parametrize("bad_ms", [None, 0.0, -1.0, "fast"])
def test_power_is_unavailable_when_a_final_query_has_no_usable_timing(bad_ms) -> None:
    queries = _full_run()
    queries[5] = {**queries[5], "ms": bad_ms}

    assert corpus_score.power_at_size(_data(queries)) is None


def test_power_is_unavailable_when_the_final_iteration_failed_or_is_incomplete() -> None:
    failed = _full_run()
    failed[3] = {**failed[3], "status": "FAILED"}

    assert corpus_score.power_at_size(_data(failed)) is None
    assert corpus_score.power_at_size(_data(_full_run()[:21])) is None


def test_power_is_defined_for_the_tpc_benchmarks_only() -> None:
    assert corpus_score.power_at_size(_data(_full_run(), benchmark="ssb")) is None
    assert corpus_score.power_at_size(_data(_full_run(), scale=0)) is None
    assert corpus_score.power_at_size({"queries": _full_run()}) is None


def test_throughput_is_executed_queries_over_the_phase_duration() -> None:
    data = _data(
        [
            {"id": "1", "iter": 1, "ms": 5.0, "run_type": "measurement", "status": "SUCCESS", "test_type": "throughput"}
            for _ in range(44)
        ],
        scale=1.0,
    )
    data["phases"] = {"throughput_test": {"duration_ms": 2000, "status": "COMPLETED"}}

    assert corpus_score.throughput_at_size(data) == pytest.approx(44 * 3600 / 2)
    data["phases"]["throughput_test"]["duration_ms"] = 0
    assert corpus_score.throughput_at_size(data) is None


def _stub_executor_results() -> list[dict]:
    return [
        {
            "query_id": str(number),
            "execution_time_seconds": 0.01 * number,
            "rows_returned": 1,
            "status": "SUCCESS",
            "iteration": 1,
            "stream_id": 0,
            "run_type": "measurement",
        }
        for number in range(1, 23)
    ]


def _build_through_the_cli_classification(scale: float, official: bool = False):
    config = SimpleNamespace(name="tpch", scale_factor=scale, official=official)
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name="TPC-H",
            scale_factor=scale,
            test_type="power",
            benchmark_id="tpch",
            compliance_class=dataframe_compliance_class(None, config),
        ),
        platform=PlatformInfoInput(name="platform-a", platform_version="1.0.0"),
    )
    for raw in _stub_executor_results():
        builder.add_query_result(normalize_query_result(raw))
    return builder.build()


def test_a_query_only_result_at_a_standard_scale_has_a_recomputable_score() -> None:
    assert validate_tpch_scale(1.0) is None

    payload = build_result_payload(_build_through_the_cli_classification(1.0))

    recorded = payload["summary"]["tpc_metrics"]["power_at_size"]
    assert recorded > 0
    assert "suppressed" not in payload["summary"]["tpc_metrics"]
    assert "compliance_class" not in payload["benchmark"]
    assert corpus_score.power_at_size(payload) == pytest.approx(recorded, rel=1e-4)
    outcome = corpus_rule.derive_outcome(corpus_rule.facts_from_result(payload))
    assert outcome.class_name == corpus_rule.CLASS_POWER_ONLY
    assert outcome.lanes == (corpus_rule.LANE_POWER_ONLY,)


def test_a_result_at_a_non_standard_scale_records_its_timings_and_scores() -> None:
    payload = build_result_payload(_build_through_the_cli_classification(2.0))

    assert payload["benchmark"]["compliance_class"] == "unofficial_nonstandard"
    recorded = payload["summary"]["tpc_metrics"]["power_at_size"]
    assert corpus_score.power_at_size(payload) == pytest.approx(recorded, rel=1e-4)
    assert payload["phases"]["power_test"]["power_at_size"] == pytest.approx(recorded)


def test_the_written_class_follows_the_facts_not_the_requested_class() -> None:
    requested_official = build_result_payload(_build_through_the_cli_classification(1.0, official=True))

    assert "compliance_class" not in requested_official["benchmark"]


def _throughput_run(streams: int, query_streams: set[int]) -> dict:
    stream_ids = range(1, streams + 1)
    queries = [
        QueryExecution(
            query_id=str(number),
            execution_time_ms=10.0,
            rows_returned=1,
            status="SUCCESS",
            stream_id=str(stream),
            iteration=1,
            run_type="measurement",
            test_type="throughput",
        )
        for stream in query_streams
        for number in range(1, 23)
    ]
    phase = ThroughputTestPhase(
        start_time="2026-01-01T00:00:00",
        end_time="2026-01-01T00:00:10",
        duration_ms=10_000,
        num_streams=streams,
        streams=[
            ThroughputStream(stream_id=stream, start_time="", end_time="", duration_ms=1000, query_executions=[])
            for stream in stream_ids
        ],
        total_queries_executed=len(queries),
        throughput_at_size=1.0,
    )
    power = PowerTestPhase(
        start_time="2026-01-01T00:00:00",
        end_time="2026-01-01T00:00:10",
        duration_ms=1000,
        query_executions=[],
        geometric_mean_time=0.01,
        power_at_size=1.0,
        query_count=22,
        success=True,
    )
    result = make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="tpch",
        scale_factor=1.0,
        timestamp=datetime(2026, 1, 1),
        query_results=queries,
        execution_phases=ExecutionPhases(setup=SetupPhase(), power_test=power, throughput_test=phase),
        test_execution_type="throughput",
    )
    return build_result_payload(result)


def test_the_writer_records_the_executed_stream_count() -> None:
    payload = _throughput_run(streams=3, query_streams={1})

    assert len(payload["phases"]["throughput_test"]["stream_results"]) == 3
    assert payload["run"]["streams"] == 3
    assert corpus_rule.executed_stream_count(payload) == 3


def test_a_ranked_standard_scale_result_recomputes_within_tolerance_of_its_recorded_score() -> None:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["results"]
    compared = 0
    for name, entry in snapshot.items():
        rule = entry["rule"]
        if "skipped" in rule or not rule["lanes"] or entry["identity"]["benchmark"] not in ("tpch", "tpcds"):
            continue
        data = json.loads((BUNDLES / name).read_text(encoding="utf-8"))
        metrics = (data.get("summary") or {}).get("tpc_metrics") or {}
        for lane, rank in rule["ranks"].items():
            if rank["rank"] is None:
                continue
            recorded = metrics.get("throughput_at_size" if lane == "throughput" else "power_at_size")
            recomputed = (
                corpus_score.throughput_at_size(data) if lane == "throughput" else corpus_score.power_at_size(data)
            )
            if recorded is None:
                assert recomputed is not None, name
                continue
            compared += 1
            assert abs(recomputed / recorded - 1) <= RANKED_TOLERANCE, name
    assert compared > 0


def _independent_qphh(power: float, throughput: float) -> float:
    return round((power * throughput) ** 0.5, 1)


def _independent_qphds(data: dict) -> float:
    phases = data["phases"]
    streams = phases["throughput_test"]["num_streams"]
    load_seconds = (
        sum(phases[name]["duration_ms"] for name in ("schema_creation", "data_loading") if name in phases) / 1000.0
    )
    hours = (
        phases["power_test"]["duration_ms"] / 1000.0 * streams / 3600.0,
        (phases["throughput_test"]["duration_ms"] + phases["throughput_test_2"]["duration_ms"]) / 3600000.0,
        (phases["maintenance_test"]["duration_ms"] + phases["maintenance_test_2"]["duration_ms"]) / 3600000.0,
        0.01 * streams * load_seconds / 3600.0,
    )
    product = hours[0] * hours[1] * hours[2] * hours[3]
    return float(int(data["benchmark"]["scale_factor"] * 99 * streams / product**0.25))


def test_tpch_combined_score_is_the_square_root_of_power_times_throughput() -> None:
    data = combined_bundle(benchmark="tpch", qph=321.5, power=1234.5)

    power = corpus_score.power_at_size(data)
    throughput = corpus_score.throughput_at_size(data)

    assert power == pytest.approx(1234.5)
    assert corpus_score.combined_score(data) == _independent_qphh(power, throughput) == pytest.approx(321.5)


def test_tpcds_combined_score_follows_the_load_power_throughput_and_maintenance_timings() -> None:
    data = combined_bundle(benchmark="tpcds", qph=321.5)

    assert corpus_score.combined_score(data) == _independent_qphds(data) == 321.0


@pytest.mark.parametrize("family", ["tpch", "tpcds"])
def test_combined_score_ignores_recorded_metrics(family: str) -> None:
    data = combined_bundle(benchmark=family, qph=300.0)
    expected = corpus_score.combined_score(data)
    data["summary"]["tpc_metrics"] = {"qphh_at_size": 1.0, "qphds_at_size": 1.0, "refresh_included": False}

    assert expected is not None
    assert corpus_score.combined_score(data) == expected


def _drop_throughput_duration(data: dict) -> None:
    data["phases"]["throughput_test"].pop("duration_ms")


def _drop_refresh_evidence(data: dict) -> None:
    data["phases"]["power_test"].pop("refresh_functions")


def _drop_power_queries(data: dict) -> None:
    data["queries"] = [query for query in data["queries"] if query.get("test_type") == "throughput"]


@pytest.mark.parametrize("mutate", [_drop_throughput_duration, _drop_refresh_evidence, _drop_power_queries])
def test_tpch_combined_score_is_undetermined_when_a_timing_or_refresh_fact_is_missing(mutate) -> None:
    data = combined_bundle(benchmark="tpch")
    mutate(data)

    assert corpus_score.combined_score(data) is None


def _drop_load(data: dict) -> None:
    data["phases"].pop("data_loading")


def _drop_second_throughput_duration(data: dict) -> None:
    data["phases"]["throughput_test_2"].pop("duration_ms")


def _drop_maintenance_duration(data: dict) -> None:
    data["phases"]["maintenance_test_2"].pop("duration_ms")


def _drop_power_duration(data: dict) -> None:
    data["phases"]["power_test"].pop("duration_ms")


def _odd_streams(data: dict) -> None:
    data["phases"]["throughput_test"]["num_streams"] = 3


def _partial_power_statements(data: dict) -> None:
    data["phases"]["power_test"]["query_count"] = 99


@pytest.mark.parametrize(
    "mutate",
    [
        _drop_load,
        _drop_second_throughput_duration,
        _drop_maintenance_duration,
        _drop_power_duration,
        _odd_streams,
        _partial_power_statements,
    ],
)
def test_tpcds_combined_score_is_undetermined_when_a_timing_fact_is_missing(mutate) -> None:
    data = combined_bundle(benchmark="tpcds")
    mutate(data)

    assert corpus_score.combined_score(data) is None


def test_combined_score_is_undetermined_for_a_non_tpc_benchmark() -> None:
    data = combined_bundle(benchmark="tpch")
    data["benchmark"]["id"] = "ssb"

    assert corpus_score.combined_score(data) is None
