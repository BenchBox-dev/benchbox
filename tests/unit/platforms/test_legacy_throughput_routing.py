from __future__ import annotations

import threading
from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.core.throughput.result import ThroughputResult
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
        self.calls: list[Any] = []
        self.sessions: list[Any] = []
        self._lock = threading.Lock()
        self._last_throughput_test_result = None

    def get_target_dialect(self) -> str | None:
        return None

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        with self._lock:
            self.sessions.append(benchmark_type)
        return connection

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: Any,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self.calls.append(query_id)
        return {"query_id": query_id, "status": "SUCCESS", "rows_returned": 1, "execution_time_seconds": 0.0}


class _UnsupportedDriver(_Driver):
    stream_connection_capability = StreamConnectionCapability.UNSUPPORTED


class _UndeclaredDriver(TestDriversMixin):
    platform_name = "undeclared"

    def __init__(self) -> None:
        self._last_throughput_test_result = None
        self.executed = False

    def _execute_tpch_throughput_test(self, benchmark, connection, run_config):
        self.executed = True
        return []


class _FailingDriver(_Driver):
    def execute_query(self, connection, query, query_id, *args, **kwargs):
        if query_id == 3:
            raise RuntimeError("query 3 failed")
        return super().execute_query(connection, query, query_id, *args, **kwargs)


def _benchmark(name: str = "TPC-H Benchmark") -> Mock:
    benchmark = Mock()
    benchmark.benchmark_name = name
    benchmark.scale_factor = 0.01
    benchmark.get_query.return_value = "SELECT 1"
    return benchmark


class TestRoutesThroughTheSupportedDriver:
    def test_tpch_runs_the_throughput_driver_and_emits_a_deprecation_warning(self):
        driver = _Driver()

        with pytest.warns(DeprecationWarning, match="PlatformAdapter.run_throughput_test is deprecated"):
            result = driver.run_throughput_test(_benchmark(), connection=Mock(), num_streams=2)

        assert result["success"] is True
        assert result["streams_executed"] == 2
        assert result["streams_successful"] == 2
        assert result["throughput_at_size"] is not None
        assert len(driver.calls) == 44
        assert result["total_duration"] == result["total_time"]
        assert isinstance(result["stream_results"][0], dict)
        assert result["error"] is None

    def test_stream_count_alias_is_honoured(self):
        driver = _Driver()

        with pytest.warns(DeprecationWarning):
            result = driver.run_throughput_test(_benchmark(), connection=Mock(), stream_count=3)

        assert result["streams_executed"] == 3
        assert len(driver.calls) == 66

    def test_default_stream_count_is_the_tpc_minimum(self):
        driver = _Driver()

        with pytest.warns(DeprecationWarning):
            result = driver.run_throughput_test(_benchmark(), connection=Mock())

        assert result["streams_executed"] == 2

    def test_each_stream_opens_its_own_session_through_the_adapter_hook(self):
        driver = _Driver()

        with pytest.warns(DeprecationWarning):
            driver.run_throughput_test(_benchmark(), connection=Mock(), num_streams=3, benchmark_type="olap")

        assert driver.sessions == ["olap"] * 3

    def test_result_state_does_not_leak_into_later_runs(self):
        driver = _Driver()

        with pytest.warns(DeprecationWarning):
            driver.run_throughput_test(_benchmark(), connection=Mock(), num_streams=2)

        assert driver._last_throughput_test_result is None

    def test_tpcds_dispatches_to_the_tpcds_driver_with_mapped_options(self):
        driver = _Driver()
        seen: dict[str, Any] = {}

        def fake_tpcds(benchmark, connection, run_config):
            seen.update(run_config)
            driver._last_throughput_test_result = ThroughputResult(
                start_time="s",
                end_time="e",
                total_time=1.0,
                throughput_at_size=5.0,
                streams_executed=2,
                streams_successful=2,
            )
            return []

        with patch.object(driver, "_execute_tpcds_throughput_test", fake_tpcds), pytest.warns(DeprecationWarning):
            driver.run_throughput_test(
                _benchmark("TPC-DS Benchmark"),
                connection=Mock(),
                num_streams=2,
                base_seed=7,
                stream_timeout=120,
                enable_validation=False,
            )

        assert seen["benchmark_name"] == "tpcds"
        assert seen["scale_factor"] == 0.01
        assert seen["seed"] == 7
        assert seen["stream_timeout_seconds"] == 120
        assert seen["validation_mode"] == "disabled"

    def test_non_tpc_benchmarks_keep_the_single_stream_fallback_with_a_warning(self):
        driver = _Driver()
        with (
            patch.object(driver, "run_power_test", return_value={"status": "SUCCESS"}) as power,
            pytest.warns(DeprecationWarning),
        ):
            result = driver.run_throughput_test(Mock(spec=[]), connection="c", stream_count=4)

        assert result == {"status": "SUCCESS"}
        power.assert_called_once()


class TestCapabilityGateOnThePublicEntryPoint:
    def test_unsupported_platform_is_refused_before_any_stream_runs(self):
        driver = _UnsupportedDriver()

        with pytest.warns(DeprecationWarning), pytest.raises(RuntimeError, match="UNSUPPORTED"):
            driver.run_throughput_test(_benchmark(), connection=Mock(), num_streams=2)

        assert driver.calls == []
        assert driver.sessions == []

    def test_undeclared_platform_is_refused_before_any_stream_runs(self):
        driver = _UndeclaredDriver()

        with pytest.warns(DeprecationWarning), pytest.raises(RuntimeError, match="no explicit"):
            driver.run_throughput_test(_benchmark(), connection=Mock(), num_streams=2)

        assert driver.executed is False

    def test_one_stream_is_refused_before_any_stream_runs(self):
        driver = _Driver()

        with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="at least 2"):
            driver.run_throughput_test(_benchmark(), connection=Mock(), num_streams=1)

        assert driver.calls == []

    @pytest.mark.parametrize("key", ["stream_count", "streams", "concurrent_streams"])
    def test_every_stream_count_spelling_obeys_the_minimum(self, key):
        driver = _Driver()

        with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="at least 2"):
            driver.run_throughput_test(_benchmark(), connection=Mock(), **{key: 1})

        assert driver.calls == []

    def test_custom_connection_factory_is_rejected(self):
        driver = _Driver()

        with pytest.warns(DeprecationWarning), pytest.raises(TypeError, match="connection_factory"):
            driver.run_throughput_test(_benchmark(), connection=Mock(), connection_factory=Mock(), num_streams=2)

        assert driver.calls == []

    def test_missing_connection_is_rejected(self):
        driver = _Driver()

        with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="require a connection"):
            driver.run_throughput_test(_benchmark(), num_streams=2)


class TestMetricsAreWithheldOnFailure:
    def test_failed_query_withholds_throughput_at_size(self):
        driver = _FailingDriver()

        with pytest.warns(DeprecationWarning):
            result = driver.run_throughput_test(_benchmark(), connection=Mock(), num_streams=2)

        assert result["success"] is False
        assert result["throughput_at_size"] is None

    def test_a_run_that_cannot_start_raises_instead_of_returning_a_metric(self):
        driver = _Driver()

        def broken(benchmark, connection, run_config):
            driver._last_throughput_test_result = None
            return [{"query_id": "throughput_test_error", "error": "driver exploded"}]

        with (
            patch.object(driver, "_execute_tpch_throughput_test", broken),
            pytest.warns(DeprecationWarning),
            pytest.raises(RuntimeError, match="driver exploded"),
        ):
            driver.run_throughput_test(_benchmark(), connection=Mock(), num_streams=2)


