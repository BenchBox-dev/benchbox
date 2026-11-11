from __future__ import annotations

import gc
import time
import tracemalloc
from datetime import datetime
from pathlib import Path

import pytest

from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.runner import LifecyclePhases, ValidationOptions, run_benchmark_lifecycle
from benchbox.core.schemas import BenchmarkConfig, SystemProfile

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


class DummyBenchmark:
    def __init__(self) -> None:
        self.name = "dummy"
        self.scale_factor = 1.0
        self.queries = None
        self.options: dict[str, object] = {}
        self.concurrency = 1
        self.output_dir = None
        self.tables = None

    def generate_data(self) -> dict[str, object]:
        return {}


class DummyAdapter:
    def __init__(self, platform_name: str, workload: float = 0.001) -> None:
        self.platform_name = platform_name
        self.workload = workload
        self.call_count = 0

    def reset(self) -> None:
        self.call_count = 0

    def run_benchmark(self, benchmark: DummyBenchmark, **_kwargs) -> BenchmarkResults:
        self.call_count += 1
        time.sleep(self.workload)

        return BenchmarkResults(
            benchmark_name=benchmark.name,
            platform=self.platform_name,
            scale_factor=benchmark.scale_factor,
            execution_id=f"{self.platform_name}-{self.call_count}",
            timestamp=datetime.now(),
            duration_seconds=self.workload,
            total_queries=0,
            successful_queries=0,
            failed_queries=0,
            total_execution_time=self.workload,
            average_query_time=0.0,
        )


def _system_profile() -> SystemProfile:
    return SystemProfile(
        os_name="TestOS",
        os_version="1.0",
        architecture="x86_64",
        cpu_model="Test CPU",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        memory_total_gb=16.0,
        memory_available_gb=12.0,
        python_version="3.11",
        disk_space_gb=256.0,
        timestamp=datetime.now(),
        hostname="performance-test",
    )


def _peak_memory(func, *, iterations: int = 25) -> int:
    gc.collect()
    tracemalloc.start()
    for _ in range(iterations):
        result = func()
        del result
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    gc.collect()
    return peak


def _baseline_kwargs(connection_path: str | None) -> dict[str, object]:
    return {
        "query_subset": None,
        "concurrent_streams": 1,
        "test_execution_type": "standard",
        "scale_factor": 1.0,
        "seed": None,
        "connection": {"database_path": connection_path},
        "enable_postload_validation": False,
    }


@pytest.mark.performance
def test_run_benchmark_lifecycle_memory_overhead() -> None:

    adapter = DummyAdapter("mock-memory", workload=0.0005)
    benchmark = DummyBenchmark()
    config = BenchmarkConfig(
        name="dummy",
        display_name="Dummy Benchmark",
        scale_factor=1.0,
        concurrency=1,
        test_execution_type="standard",
        options={},
    )

    baseline_peak = _peak_memory(lambda: adapter.run_benchmark(benchmark, **_baseline_kwargs(None)))
    adapter.reset()

    lifecycle_peak = _peak_memory(
        lambda: run_benchmark_lifecycle(
            benchmark_config=config,
            database_config=None,
            system_profile=_system_profile(),
            platform_config=None,
            phases=LifecyclePhases(generate=False, load=False, execute=True),
            validation_opts=ValidationOptions(),
            platform_adapter=adapter,
            benchmark_instance=benchmark,
        )
    )

    overhead_bytes = max(lifecycle_peak - baseline_peak, 0)
    assert overhead_bytes < 1_000_000, f"Memory overhead too high: {overhead_bytes} bytes"


@pytest.mark.performance
def test_result_exporter_memory_efficiency(tmp_path: Path) -> None:

    query_results = [
        {
            "query_id": f"Q{i}",
            "status": "SUCCESS",
            "execution_time_seconds": 0.05 + (i * 0.001),
            "rows_returned": 10 + i,
        }
        for i in range(20)
    ]

    result = BenchmarkResults(
        benchmark_name="dummy",
        platform="mock",
        scale_factor=1.0,
        execution_id="export-test",
        timestamp=datetime.now(),
        duration_seconds=1.0,
        total_queries=len(query_results),
        successful_queries=len(query_results),
        failed_queries=0,
        query_results=query_results,
        total_execution_time=1.0,
        average_query_time=0.05,
    )

    result.performance_summary = {"counters": {"queries": len(query_results)}}

    exporter = ResultExporter(output_dir=tmp_path, anonymize=False)
    thresholds = {"json": 2_500_000, "csv": 3_000_000}

    for fmt, threshold in thresholds.items():
        gc.collect()
        tracemalloc.start()
        exporter.export_result(result, formats=[fmt])
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak < threshold, f"{fmt} export used too much memory: {peak} bytes"


