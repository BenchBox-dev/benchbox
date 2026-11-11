from __future__ import annotations

import warnings
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchbox import TPCH
from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.core.tpcds.official_benchmark import TPCDSOfficialBenchmark, TPCDSOfficialBenchmarkConfig
from benchbox.core.tpch.official_benchmark import TPCHOfficialBenchmark, TPCHOfficialBenchmarkConfig
from benchbox.platforms.base.connection_wrappers import StreamConnectionCapability
from benchbox.platforms.base.execution import TestDriversMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Driver(TestDriversMixin):
    stream_connection_capability = StreamConnectionCapability.SHARED_CURSOR
    platform_name = "stub"
    very_verbose = False

    def __init__(self) -> None:
        self._last_throughput_test_result = None

    def get_target_dialect(self) -> str | None:
        return None

    def new_stream_connection(self, connection, *, benchmark_type=None):
        return connection

    def execute_query(self, connection, query, query_id, *args, **kwargs):
        return {"query_id": query_id, "status": "SUCCESS", "rows_returned": 1, "execution_time_seconds": 0.0}


def _routed_result():
    return SimpleNamespace(
        total_time=1.0,
        streams_executed=2,
        streams_successful=2,
        stream_results=[],
        throughput_at_size=5.0,
        success=True,
        errors=[],
        outstanding_stream_ids=[],
    )


def _adapter():
    adapter = Mock()
    adapter._run_routed_throughput.return_value = _routed_result()
    return adapter


def _tpch_benchmark():
    benchmark = Mock()
    benchmark.benchmark_name = "TPC-H Benchmark"
    benchmark.scale_factor = 0.01
    benchmark.get_query.return_value = "SELECT 1"
    return benchmark


def _entry_points(tmp_path) -> dict[str, tuple[Callable[[], object], Callable[[object], object]]]:
    tpcds = TPCDSBenchmark(scale_factor=0.01, output_dir=tmp_path)
    tpcds_official = TPCDSOfficialBenchmark(scale_factor=0.01, output_dir=tmp_path)
    tpcds_config = TPCDSOfficialBenchmarkConfig(
        scale_factor=0.01, power_test_enabled=False, maintenance_test_enabled=False, output_dir=tmp_path
    )
    tpch_official = TPCHOfficialBenchmark(scale_factor=0.01, output_dir=tmp_path)
    tpch_config = TPCHOfficialBenchmarkConfig(
        scale_factor=0.01, power_test_enabled=False, maintenance_test_enabled=False, output_dir=tmp_path
    )
    facade = TPCH(scale_factor=0.01, output_dir=tmp_path)
    driver = _Driver()
    return {
        "TPCDSBenchmark.run_throughput_test": (
            lambda: tpcds.run_throughput_test(num_streams=2),
            lambda adapter: tpcds.run_throughput_test(num_streams=2, adapter=adapter, connection=object()),
        ),
        "TPCDSBenchmark.run_official_benchmark": (
            lambda: tpcds.run_official_benchmark(object(), power_test=False, maintenance_test=False),
            lambda adapter: tpcds.run_official_benchmark(
                object(), power_test=False, maintenance_test=False, adapter=adapter
            ),
        ),
        "TPCDSOfficialBenchmark.run_official_benchmark": (
            lambda: tpcds_official.run_official_benchmark(lambda: Mock(), tpcds_config),
            lambda adapter: tpcds_official.run_official_benchmark(lambda: Mock(), tpcds_config, adapter=adapter),
        ),
        "TPCHOfficialBenchmark.run_official_benchmark": (
            lambda: tpch_official.run_official_benchmark(lambda: Mock(), tpch_config),
            lambda adapter: tpch_official.run_official_benchmark(lambda: Mock(), tpch_config, adapter=adapter),
        ),
        "TPCH.run_official_benchmark": (
            lambda: facade.run_official_benchmark(lambda: Mock(), tpch_config),
            lambda adapter: facade.run_official_benchmark(lambda: Mock(), tpch_config, adapter=adapter),
        ),
        "PlatformAdapter.run_throughput_test": (
            lambda: driver.run_throughput_test(_tpch_benchmark(), num_streams=2),
            lambda adapter: driver.run_throughput_test(_tpch_benchmark(), connection=Mock(), num_streams=2),
        ),
    }


ENTRY_POINTS = [
    "TPCDSBenchmark.run_throughput_test",
    "TPCDSBenchmark.run_official_benchmark",
    "TPCDSOfficialBenchmark.run_official_benchmark",
    "TPCHOfficialBenchmark.run_official_benchmark",
    "TPCH.run_official_benchmark",
    "PlatformAdapter.run_throughput_test",
]


def _deprecations(caught):
    return [item for item in caught if issubclass(item.category, DeprecationWarning)]


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_warning_precedes_the_missing_adapter_refusal_and_points_at_the_caller(name, tmp_path):
    without_adapter, _with_adapter = _entry_points(tmp_path)[name]
    expected_error = ValueError if name == "PlatformAdapter.run_throughput_test" else TypeError

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(expected_error):
            without_adapter()

    deprecations = _deprecations(caught)
    assert len(deprecations) == 1
    assert name in str(deprecations[0].message)
    assert deprecations[0].filename == __file__


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_one_warning_attributed_to_the_caller_when_the_call_runs(name, tmp_path):
    _without_adapter, with_adapter = _entry_points(tmp_path)[name]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with_adapter(_adapter())

    deprecations = _deprecations(caught)
    assert len(deprecations) == 1, [str(item.message) for item in deprecations]
    assert name in str(deprecations[0].message)
    assert deprecations[0].filename == __file__


def test_official_run_without_throughput_still_warns(tmp_path):
    tpcds = TPCDSBenchmark(scale_factor=0.01, output_dir=tmp_path)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tpcds.run_official_benchmark(object(), power_test=False, throughput_test=False, maintenance_test=False)

    deprecations = _deprecations(caught)
    assert len(deprecations) == 1
    assert deprecations[0].filename == __file__


