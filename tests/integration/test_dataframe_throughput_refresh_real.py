from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.schemas import BenchmarkConfig
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.platforms.dataframe.benchmark_mixin import DataFramePhases, DataFrameRunOptions

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.tpch,
]

_SCALE_FACTOR = 0.01
_STREAMS = 2
_SEED = 42
_POWER_ITERATIONS = 2


@pytest.fixture(scope="module")
def tpch_data(tmp_path_factory: pytest.TempPathFactory) -> tuple[TPCHBenchmark, Path]:
    base = tmp_path_factory.mktemp("tpch_dataframe_full_spec")
    bench = TPCHBenchmark(scale_factor=_SCALE_FACTOR, output_dir=str(base / "data"))
    bench.generate_data()
    return bench, base / "data"


def _adapter(engine: str) -> Any:
    if engine == "polars-df":
        pytest.importorskip("polars")
        from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

        return PolarsDataFrameAdapter()
    pytest.importorskip("datafusion")
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    return DataFusionDataFrameAdapter()


@pytest.fixture(scope="module", params=["polars-df", "datafusion-df"])
def full_spec_result(request: pytest.FixtureRequest, tpch_data: tuple[TPCHBenchmark, Path]) -> Any:
    bench, data_dir = tpch_data
    bench.tpch_refresh_functions = True
    config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=_SCALE_FACTOR,
        concurrency=_STREAMS,
        test_execution_type="combined",
        official=True,
        options={
            "seed": _SEED,
            "power_warmup_iterations": 0,
            "power_iterations": _POWER_ITERATIONS,
            "requested_phases": ["load", "power", "throughput"],
            "benchmark_options": {"tpch_refresh_functions": True},
        },
    )
    return _adapter(request.param).run_benchmark(
        bench,
        benchmark_config=config,
        data_dir=data_dir,
        phases=DataFramePhases(load=True, execute=True, power=True, throughput=True),
        options=DataFrameRunOptions(ignore_memory_warnings=True),
    )


class TestDataFrameFullSpecRun:
    def test_the_run_completes_with_a_clean_validation_status(self, full_spec_result: Any) -> None:
        assert full_spec_result.validation_status == "PASSED", full_spec_result.validation_details
        assert full_spec_result.failed_queries == 0

    def test_the_power_phase_records_rf1_and_rf2_on_the_first_set(self, full_spec_result: Any) -> None:
        power = full_spec_result.execution_phases.power_test

        assert power.refresh_first_set == 1
        assert [function["function"] for function in power.refresh_functions] == ["RF1", "RF2"]
        assert all(function["status"] == "SUCCESS" for function in power.refresh_functions)
        assert all(function["check_status"] == "PASSED" for function in power.refresh_functions)
        assert power.query_count == 22
        assert power.power_at_size > 0

    def test_the_throughput_phase_runs_two_streams_and_a_refresh_stream_on_the_next_sets(
        self, full_spec_result: Any
    ) -> None:
        throughput = full_spec_result.execution_phases.throughput_test

        assert throughput.success is True
        assert throughput.num_streams == _STREAMS
        assert [stream.success for stream in throughput.streams] == [True] * _STREAMS
        assert [len(stream.query_executions) for stream in throughput.streams] == [22] * _STREAMS
        refresh = throughput.refresh_stream
        assert refresh["status"] == "COMPLETED"
        assert [pair["update_set"] for pair in refresh["pairs"]] == [2, 3]
        for pair in refresh["pairs"]:
            assert pair["status"] == "SUCCESS"
            assert [function["status"] for function in pair["functions"]] == ["SUCCESS", "SUCCESS"]
            rf1, rf2 = pair["functions"]
            assert rf1["rows_inserted"] > 0
            assert rf2["orders_deleted"] > 0 and rf2["lineitems_deleted"] > 0

    def test_the_refresh_evidence_satisfies_the_official_metric_gates(self, full_spec_result: Any) -> None:
        phases = full_spec_result.execution_phases
        power_valid, first_set = TPCMetricsCalculator._power_refresh_evidence(
            {
                "refresh_first_set": phases.power_test.refresh_first_set,
                "refresh_functions": phases.power_test.refresh_functions,
            }
        )
        throughput_valid, throughput_first_set, streams = TPCMetricsCalculator._throughput_refresh_evidence(
            {
                "num_streams": phases.throughput_test.num_streams,
                "stream_results": [{}] * _STREAMS,
                "refresh_stream": phases.throughput_test.refresh_stream,
            }
        )

        assert power_valid and throughput_valid
        assert throughput_first_set == first_set + 1
        assert streams == _STREAMS
        assert full_spec_result.refresh_included is True
        assert full_spec_result.throughput_at_size and full_spec_result.throughput_at_size > 0

    def test_the_bundle_lists_power_and_throughput_rows_separately(self, full_spec_result: Any) -> None:
        by_type: dict[str, int] = {}
        for row in full_spec_result.query_results:
            by_type[row.get("test_type", "power")] = by_type.get(row.get("test_type", "power"), 0) + 1

        assert by_type["throughput"] == 22 * _STREAMS
        assert by_type["power"] == 22 * _POWER_ITERATIONS
