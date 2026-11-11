from __future__ import annotations

import pytest

from benchbox.core.benchmark_loader import get_benchmark_class
from benchbox.core.benchmark_registry import list_loader_benchmark_ids
from benchbox.core.contracts.benchmark_runtime import (
    EXTENDED_RUNTIME_METHODS,
    REQUIRED_RUNTIME_METHODS,
    get_missing_methods,
    has_runtime_contract,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


LOADER_RESOLVED_BENCHMARK_IDS: tuple[str, ...] = tuple(list_loader_benchmark_ids())


EXPECTED_REQUIRED_CONTRACT_GAPS: set[str] = set()


EXPECTED_EXTENDED_CONTRACT_GAPS: set[str] = set()


def _instantiate_loader_benchmark(benchmark_id: str):

    benchmark_class = get_benchmark_class(benchmark_id)
    kwargs: dict[str, object] = {"scale_factor": 0.01}

    if benchmark_id in {"tpcds", "tpcds_obt", "joinorder", "clickbench"}:
        kwargs["scale_factor"] = 1.0

    if benchmark_id == "nyctaxi":
        kwargs["months"] = [1]

    return benchmark_class(**kwargs)


def test_required_runtime_contract_coverage_for_loader_benchmarks() -> None:

    missing_by_benchmark: dict[str, list[str]] = {}

    for benchmark_id in LOADER_RESOLVED_BENCHMARK_IDS:
        benchmark = _instantiate_loader_benchmark(benchmark_id)
        missing = get_missing_methods(benchmark, REQUIRED_RUNTIME_METHODS)
        if missing:
            missing_by_benchmark[benchmark_id] = missing

    assert set(missing_by_benchmark.keys()) == EXPECTED_REQUIRED_CONTRACT_GAPS
    for benchmark_id, missing in missing_by_benchmark.items():
        assert "create_enhanced_benchmark_result" in missing, benchmark_id


def test_extended_runtime_contract_coverage_for_loader_benchmarks() -> None:

    extended_missing_by_benchmark: dict[str, list[str]] = {}

    for benchmark_id in LOADER_RESOLVED_BENCHMARK_IDS:
        benchmark = _instantiate_loader_benchmark(benchmark_id)
        missing = get_missing_methods(benchmark, REQUIRED_RUNTIME_METHODS + EXTENDED_RUNTIME_METHODS)
        if missing:
            extended_missing_by_benchmark[benchmark_id] = missing

    assert set(extended_missing_by_benchmark.keys()) == EXPECTED_EXTENDED_CONTRACT_GAPS

    assert not extended_missing_by_benchmark


@pytest.mark.parametrize("benchmark_id", sorted(set(LOADER_RESOLVED_BENCHMARK_IDS) - EXPECTED_REQUIRED_CONTRACT_GAPS))
def test_required_runtime_contract_helper_returns_true_for_conforming_loader_benchmarks(benchmark_id: str) -> None:

    benchmark = _instantiate_loader_benchmark(benchmark_id)
    assert has_runtime_contract(benchmark)


