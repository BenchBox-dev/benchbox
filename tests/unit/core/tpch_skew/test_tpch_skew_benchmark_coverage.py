from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.tpch_skew.benchmark import TPCHSkewBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Adapter:
    def __init__(self, runs):
        self._runs = list(runs)

    def run_benchmark(self, *_args, **_kwargs):
        return SimpleNamespace(query_results=self._runs.pop(0))


def test_compare_with_uniform_builds_summary_and_comparison_stats():
    uniform_results = [
        {"query_id": "1", "execution_time_ms": 1000, "status": "SUCCESS"},
        {"query_id": "1", "execution_time_ms": 1500, "status": "SUCCESS"},
        {"query_id": "2", "execution_time_ms": 2000, "status": "SUCCESS"},
        {"query_id": "2", "execution_time_ms": 3000, "status": "SUCCESS"},
    ]
    skewed_results = [
        {"query_id": "1", "execution_time_ms": 2500, "status": "SUCCESS"},
        {"query_id": "1", "execution_time_ms": 3000, "status": "SUCCESS"},
        {"query_id": "2", "execution_time_ms": 5000, "status": "SUCCESS"},
        {"query_id": "2", "execution_time_ms": 6000, "status": "SUCCESS"},
    ]

    benchmark = TPCHSkewBenchmark(scale_factor=0.01, skew_preset="heavy")
    adapter = _Adapter([uniform_results, skewed_results])

    result = benchmark.compare_with_uniform(adapter, queries=[1, 2], iterations=2)

    assert result["summary"]["queries_compared"] == 2
    assert result["summary"]["avg_ratio"] > 1.1
    assert "slower" in result["summary"]["interpretation"]
    assert "uniform_stdev" in result["comparison"]["1"]
    assert "skewed_stdev" in result["comparison"]["2"]


def test_compare_with_uniform_marks_incomplete_when_timing_data_missing():
    uniform_results = [{"query_id": "1", "execution_time_ms": 1000, "status": "SUCCESS"}]
    skewed_results = [{"query_id": "2", "execution_time_ms": 1000, "status": "SUCCESS"}]

    benchmark = TPCHSkewBenchmark(scale_factor=0.01)
    adapter = _Adapter([uniform_results, skewed_results])

    result = benchmark.compare_with_uniform(adapter, queries=[1, 2])

    assert result["comparison"]["1"]["status"] == "INCOMPLETE"
    assert result["comparison"]["2"]["status"] == "INCOMPLETE"
    assert result["summary"]["queries_compared"] == 0


def test_compare_with_uniform_wraps_adapter_failures():
    class _FailingAdapter:
        def run_benchmark(self, *_args, **_kwargs):
            raise RuntimeError("broken adapter")

    benchmark = TPCHSkewBenchmark(scale_factor=0.01)

    with pytest.raises(RuntimeError, match="Failed to run uniform benchmark"):
        benchmark.compare_with_uniform(_FailingAdapter(), queries=[1])


def test_geometric_mean_handles_empty_and_nonempty_values():
    assert TPCHSkewBenchmark._geometric_mean([]) == 0.0
    assert TPCHSkewBenchmark._geometric_mean([1.0, 4.0]) == pytest.approx(2.0)


