from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from benchbox.core.results import corpus_score
from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.results.models import ExecutionPhases, TableLoadingStats
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.schema import build_result_payload
from tests.unit.core.results.test_qphds import STREAMS, _evaluate, _loading, _maintenance, _phases
from tests.unit.scripts.explorer_pipeline.conftest import combined_bundle

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _tpcds_payload(phases: ExecutionPhases) -> dict:
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name="TPC-DS", scale_factor=1.0, test_type="combined", benchmark_id="tpcds", compliance_class="official"
        ),
        platform=PlatformInfoInput(name="platform-a", execution_mode="sql"),
    )
    builder.set_execution_phases(phases)
    return build_result_payload(builder.build())


def _reused_flag(phases: ExecutionPhases) -> ExecutionPhases:
    return replace(phases, setup=replace(phases.setup, database_reused=True))


def _reused_table(phases: ExecutionPhases) -> ExecutionPhases:
    return replace(phases, setup=_loading(table_status="REUSED"))


def _one_refresh_run(phases: ExecutionPhases) -> ExecutionPhases:
    first = _maintenance(1, 10.0)
    first.maintenance_operations = first.maintenance_operations[:1]
    return replace(phases, maintenance_test=first)


def _gap_between_windows(phases: ExecutionPhases) -> ExecutionPhases:
    return replace(phases, maintenance_test_2=_maintenance(STREAMS // 2 + 2, 20.0))


def _short_power(phases: ExecutionPhases) -> ExecutionPhases:
    return replace(phases, power_test=replace(phases.power_test, query_count=99))


def _failed_power(phases: ExecutionPhases) -> ExecutionPhases:
    return replace(phases, power_test=replace(phases.power_test, success=False))


def _failed_maintenance(phases: ExecutionPhases) -> ExecutionPhases:
    return replace(phases, maintenance_test=_maintenance(1, 10.0, success=False))


def _failed_load(phases: ExecutionPhases) -> ExecutionPhases:
    return replace(phases, setup=_loading(status="FAILED"))


def _clean(phases: ExecutionPhases) -> ExecutionPhases:
    return phases


TPCDS_CASES = {
    "clean": _clean,
    "load reused flag": _reused_flag,
    "load reused per table": _reused_table,
    "one refresh run per maintenance test": _one_refresh_run,
    "refresh windows not consecutive": _gap_between_windows,
    "power statement count": _short_power,
    "power failed": _failed_power,
    "maintenance failed": _failed_maintenance,
    "load failed": _failed_load,
}


@pytest.mark.parametrize("case", sorted(TPCDS_CASES))
def test_tpcds_runtime_and_corpus_agree_on_availability_and_value(case: str) -> None:
    phases = TPCDS_CASES[case](_phases())
    runtime = _evaluate(phases)
    corpus = corpus_score.combined_score(_tpcds_payload(phases))

    assert (runtime.qphds_at_size is None) == (corpus is None), runtime.unavailable
    if corpus is not None:
        assert corpus == runtime.qphds_at_size
    assert (case == "clean") == (corpus is not None)


def _no_refresh_configured(data: dict) -> None:
    data["phases"]["power_test"].pop("refresh_first_set")
    data["phases"]["power_test"].pop("refresh_functions")


def _failed_refresh_function(data: dict) -> None:
    data["phases"]["power_test"]["refresh_functions"][1]["status"] = "FAILED"


def _throughput_reuses_the_power_set(data: dict) -> None:
    refresh_stream = data["phases"]["throughput_test"]["refresh_stream"]
    refresh_stream["first_set"] = 1
    for offset, pair in enumerate(refresh_stream["pairs"]):
        pair["update_set"] = 1 + offset
        for function in pair["functions"]:
            function["update_set"] = 1 + offset


def _pair_count_mismatch(data: dict) -> None:
    refresh_stream = data["phases"]["throughput_test"]["refresh_stream"]
    refresh_stream["pairs"].pop()
    refresh_stream["pair_count"] = len(refresh_stream["pairs"])


def _short_power_phase(data: dict) -> None:
    data["phases"]["power_test"]["query_count"] = 21


def _unfinished_throughput(data: dict) -> None:
    data["phases"]["throughput_test"]["status"] = "FAILED"


def _single_stream(data: dict) -> None:
    data["phases"]["throughput_test"]["stream_results"].pop()


def _clean_tpch(data: dict) -> None:
    return None


TPCH_CASES = {
    "clean": _clean_tpch,
    "failed refresh function": _failed_refresh_function,
    "throughput reuses the power set": _throughput_reuses_the_power_set,
    "refresh pair count": _pair_count_mismatch,
    "power query count": _short_power_phase,
    "throughput not completed": _unfinished_throughput,
    "single stream": _single_stream,
}


def _runtime_tpch(data: dict) -> float | None:
    if TPCMetricsCalculator.qphh_unavailable_reasons(data, data):
        return None
    seconds = [query["ms"] / 1000.0 for query in data["queries"] if query.get("test_type") == "power"]
    refresh = [
        max(0.001, function["duration_ms"] / 1000.0) for function in data["phases"]["power_test"]["refresh_functions"]
    ]
    power = TPCMetricsCalculator.calculate_power_at_size([refresh[0], *seconds, refresh[1]], 1.0)
    block = data["phases"]["throughput_test"]
    streams = block["num_streams"]
    throughput = TPCMetricsCalculator.calculate_throughput_at_size(
        22 * streams, block["duration_ms"] / 1000.0, 1.0, streams
    )
    return TPCMetricsCalculator.calculate_qphh_at_size(power, throughput)


@pytest.mark.parametrize("case", sorted(TPCH_CASES))
def test_tpch_runtime_and_corpus_agree_on_availability_and_value(case: str) -> None:
    data = combined_bundle(benchmark="tpch")
    TPCH_CASES[case](data)
    runtime = _runtime_tpch(copy.deepcopy(data))
    corpus = corpus_score.combined_score(data)

    assert (runtime is None) == (corpus is None)
    if corpus is not None:
        assert corpus == pytest.approx(runtime)
    assert (case == "clean") == (corpus is not None)


def test_tpch_power_is_withheld_when_refresh_was_configured_but_its_evidence_is_invalid() -> None:
    data = combined_bundle(benchmark="tpch")
    _failed_refresh_function(data)

    assert corpus_score.power_at_size(data) is None


def test_tpch_power_uses_the_queries_alone_when_refresh_was_not_configured() -> None:
    data = combined_bundle(benchmark="tpch")
    _no_refresh_configured(data)
    seconds = [query["ms"] / 1000.0 for query in data["queries"] if query.get("test_type") == "power"]

    assert corpus_score.power_at_size(data) == pytest.approx(TPCMetricsCalculator.calculate_power_at_size(seconds, 1.0))
