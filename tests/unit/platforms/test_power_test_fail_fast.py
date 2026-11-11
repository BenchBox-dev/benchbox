from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGenericPowerTestFailFast:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.duckdb import DuckDBAdapter

        a = DuckDBAdapter()
        a._execute_all_queries = MagicMock()
        return a

    @pytest.fixture
    def bench_instance(self):
        b = MagicMock()
        b.scale_factor = 1.0
        return b

    @pytest.fixture
    def connection(self):
        return MagicMock()

    def test_all_queries_failed_aborts_remaining_iterations(self, adapter, bench_instance, connection):

        run_config = {
            "benchmark_name": "clickbench",
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
        }

        adapter._execute_all_queries.return_value = [
            {"query_id": "Q1", "status": "FAILED", "execution_time": 0.0},
            {"query_id": "Q2", "status": "FAILED", "execution_time": 0.0},
        ]

        results = adapter._execute_generic_power_test(bench_instance, connection, run_config)

        assert adapter._execute_all_queries.call_count == 1
        assert len(results) == 2

    def test_partial_failure_continues_without_fail_fast(self, adapter, bench_instance, connection):

        run_config = {
            "benchmark_name": "clickbench",
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
            "power_fail_fast": False,
        }

        def mixed_results():
            return [
                {"query_id": "Q1", "status": "SUCCESS", "execution_time": 1.0},
                {"query_id": "Q2", "status": "FAILED", "execution_time": 0.0},
            ]

        adapter._execute_all_queries.side_effect = [
            mixed_results(),
            mixed_results(),
            mixed_results(),
        ]

        results = adapter._execute_generic_power_test(bench_instance, connection, run_config)

        assert adapter._execute_all_queries.call_count == 3
        assert len(results) == 6

    def test_partial_failure_aborts_with_fail_fast(self, adapter, bench_instance, connection):

        run_config = {
            "benchmark_name": "clickbench",
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
            "power_fail_fast": True,
        }

        adapter._execute_all_queries.return_value = [
            {"query_id": "Q1", "status": "SUCCESS", "execution_time": 1.0},
            {"query_id": "Q2", "status": "FAILED", "execution_time": 0.0},
        ]

        results = adapter._execute_generic_power_test(bench_instance, connection, run_config)

        assert adapter._execute_all_queries.call_count == 1
        assert len(results) == 2

    def test_all_success_completes_all_iterations(self, adapter, bench_instance, connection):

        run_config = {
            "benchmark_name": "clickbench",
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
        }

        def success_results():
            return [
                {"query_id": "Q1", "status": "SUCCESS", "execution_time": 1.0},
            ]

        adapter._execute_all_queries.side_effect = [
            success_results(),
            success_results(),
            success_results(),
        ]

        results = adapter._execute_generic_power_test(bench_instance, connection, run_config)

        assert adapter._execute_all_queries.call_count == 3
        assert len(results) == 3


def _make_power_test_result(
    *, success: bool, queries_successful: int, queries_executed: int, errors: list[str] | None = None
):

    return SimpleNamespace(
        success=success,
        queries_successful=queries_successful,
        queries_executed=queries_executed,
        power_at_size=0.0,
        total_time=1.0,
        errors=errors if errors is not None else (["Query X failed: timeout"] if not success else []),
        query_results=[
            {
                "query_id": str(i + 1),
                "execution_time_seconds": 1.0 if i < queries_successful else 0.0,
                "success": i < queries_successful,
                "result_count": 10 if i < queries_successful else 0,
                "error": None if i < queries_successful else "timeout",
                "stream_id": 0,
                "position": i,
            }
            for i in range(queries_executed)
        ],
    )


class TestTPCHPowerTestFailFast:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.duckdb import DuckDBAdapter

        a = DuckDBAdapter()
        a.get_target_dialect = MagicMock(return_value="duckdb")
        return a

    @pytest.fixture
    def bench_instance(self):
        return MagicMock()

    @pytest.fixture
    def connection(self):
        return MagicMock()

    @patch("benchbox.core.tpch.power_test.TPCHPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_all_queries_failed_aborts(self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection):

        run_config = {
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 0.01,
            "stream_id": 0,
        }

        all_failed = _make_power_test_result(success=False, queries_successful=0, queries_executed=22)
        mock_pt_cls.return_value.run.return_value = all_failed

        results = adapter._execute_tpch_power_test(bench_instance, connection, run_config)

        assert mock_pt_cls.call_count == 1
        assert len(results) == 22

    @patch("benchbox.core.tpch.power_test.TPCHPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_partial_failure_continues_without_fail_fast(
        self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection
    ):

        run_config = {
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 0.01,
            "stream_id": 0,
            "power_fail_fast": False,
        }

        partial_fail = _make_power_test_result(success=False, queries_successful=20, queries_executed=22)
        mock_pt_cls.return_value.run.return_value = partial_fail

        results = adapter._execute_tpch_power_test(bench_instance, connection, run_config)

        assert mock_pt_cls.call_count == 3
        assert len(results) == 66

    @patch("benchbox.core.tpch.power_test.TPCHPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_partial_failure_aborts_with_fail_fast(
        self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection
    ):

        run_config = {
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 0.01,
            "stream_id": 0,
            "power_fail_fast": True,
        }

        partial_fail = _make_power_test_result(success=False, queries_successful=20, queries_executed=22)
        mock_pt_cls.return_value.run.return_value = partial_fail

        results = adapter._execute_tpch_power_test(bench_instance, connection, run_config)

        assert mock_pt_cls.call_count == 1
        assert len(results) == 22

    @patch("benchbox.core.tpch.power_test.TPCHPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_all_success_completes_all_iterations(
        self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection
    ):

        run_config = {
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 0.01,
            "stream_id": 0,
        }

        all_ok = _make_power_test_result(success=True, queries_successful=22, queries_executed=22)
        mock_pt_cls.return_value.run.return_value = all_ok

        results = adapter._execute_tpch_power_test(bench_instance, connection, run_config)

        assert mock_pt_cls.call_count == 3
        assert len(results) == 66

    @patch("benchbox.core.tpch.power_test.TPCHPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_factory_crash_produces_sentinel_failed_entry(
        self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection
    ):

        run_config = {
            "iterations": 1,
            "warm_up_iterations": 0,
            "scale_factor": 0.01,
            "stream_id": 0,
        }

        factory_error_msg = "Power Test execution failed: connection refused"
        factory_crashed = _make_power_test_result(
            success=False,
            queries_successful=0,
            queries_executed=0,
            errors=[factory_error_msg],
        )
        mock_pt_cls.return_value.run.return_value = factory_crashed

        results = adapter._execute_tpch_power_test(bench_instance, connection, run_config)

        assert len(results) > 0, "results must not be empty when power test factory crashes"

        sentinel = results[-1]
        assert sentinel["status"] == "FAILED"
        assert sentinel["query_id"] == "power_test_error"
        assert factory_error_msg in sentinel["error"]

    @patch("benchbox.core.tpch.power_test.TPCHPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_factory_crash_does_not_produce_sentinel_when_queries_ran(
        self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection
    ):

        run_config = {
            "iterations": 1,
            "warm_up_iterations": 0,
            "scale_factor": 0.01,
            "stream_id": 0,
        }

        all_failed = _make_power_test_result(success=False, queries_successful=0, queries_executed=22)
        mock_pt_cls.return_value.run.return_value = all_failed

        results = adapter._execute_tpch_power_test(bench_instance, connection, run_config)

        assert len(results) == 22
        assert not any(r.get("query_id") == "power_test_error" for r in results)


def _make_tpcds_power_test_result(
    *, success: bool, queries_successful: int, queries_executed: int, errors: list[str] | None = None
):

    return SimpleNamespace(
        success=success,
        queries_successful=queries_successful,
        queries_executed=queries_executed,
        power_at_size=0.0,
        total_time=1.0,
        errors=errors if errors is not None else (["Query X failed"] if not success else []),
        query_results=[
            {
                "query_id": str(i + 1),
                "execution_time_seconds": 1.0 if i < queries_successful else 0.0,
                "success": i < queries_successful,
                "result_count": 10 if i < queries_successful else 0,
                "error": None if i < queries_successful else "timeout",
                "stream_id": 0,
                "position": i,
            }
            for i in range(queries_executed)
        ],
    )


class TestTPCDSPowerTestFailFast:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.duckdb import DuckDBAdapter

        a = DuckDBAdapter()
        a.get_target_dialect = MagicMock(return_value="duckdb")
        return a

    @pytest.fixture
    def bench_instance(self):
        return MagicMock()

    @pytest.fixture
    def connection(self):
        return MagicMock()

    @patch("benchbox.core.tpcds.power_test.TPCDSPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_all_queries_failed_aborts_remaining_iterations(
        self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection
    ):

        run_config = {
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
            "stream_id": 0,
        }

        all_failed = _make_tpcds_power_test_result(success=False, queries_successful=0, queries_executed=99)
        mock_pt_cls.return_value.run.return_value = all_failed

        results = adapter._execute_tpcds_power_test(bench_instance, connection, run_config)

        assert mock_pt_cls.call_count == 1
        assert all(r["status"] == "FAILED" for r in results)

    @patch("benchbox.core.tpcds.power_test.TPCDSPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_partial_failure_continues_without_fail_fast(
        self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection
    ):

        run_config = {
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
            "stream_id": 0,
            "power_fail_fast": False,
        }

        partial_fail = _make_tpcds_power_test_result(success=False, queries_successful=80, queries_executed=99)
        mock_pt_cls.return_value.run.return_value = partial_fail

        results = adapter._execute_tpcds_power_test(bench_instance, connection, run_config)

        assert mock_pt_cls.call_count == 3
        assert len(results) == 99 * 3

    @patch("benchbox.core.tpcds.power_test.TPCDSPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_partial_failure_aborts_with_fail_fast(
        self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection
    ):

        run_config = {
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
            "stream_id": 0,
            "power_fail_fast": True,
        }

        partial_fail = _make_tpcds_power_test_result(success=False, queries_successful=80, queries_executed=99)
        mock_pt_cls.return_value.run.return_value = partial_fail

        results = adapter._execute_tpcds_power_test(bench_instance, connection, run_config)

        assert mock_pt_cls.call_count == 1
        assert len(results) == 99

    @patch("benchbox.core.tpcds.power_test.TPCDSPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_all_success_completes_all_iterations(
        self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection
    ):

        run_config = {
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
            "stream_id": 0,
        }

        all_ok = _make_tpcds_power_test_result(success=True, queries_successful=99, queries_executed=99)
        mock_pt_cls.return_value.run.return_value = all_ok

        results = adapter._execute_tpcds_power_test(bench_instance, connection, run_config)

        assert mock_pt_cls.call_count == 3
        assert len(results) == 99 * 3

    @patch("benchbox.core.tpcds.power_test.TPCDSPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_execute_only_connection_does_not_raise(self, mock_conn_cls, mock_pt_cls, adapter, bench_instance):

        sentinel = object()
        execute_only_conn = SimpleNamespace(execute=lambda *a, **kw: sentinel)
        assert not hasattr(execute_only_conn, "cursor")

        run_config = {
            "iterations": 1,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
            "stream_id": 0,
        }

        all_ok = _make_tpcds_power_test_result(success=True, queries_successful=1, queries_executed=1)
        mock_pt_cls.return_value.run.return_value = all_ok

        adapter._execute_tpcds_power_test(bench_instance, execute_only_conn, run_config)

        factory = mock_pt_cls.call_args.kwargs["connection_factory"]
        factory()

        proxy_wrapper, _ = mock_conn_cls.call_args.args
        assert proxy_wrapper is not execute_only_conn, "raw connection must be wrapped"

        assert proxy_wrapper.execute("SELECT 1") is sentinel

        proxy_wrapper.close()
        assert proxy_wrapper.execute("SELECT 1") is sentinel, "connection still alive after proxy.close()"

    @patch("benchbox.core.tpcds.power_test.TPCDSPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_cursor_connection_uses_cursor_not_connection(self, mock_conn_cls, mock_pt_cls, adapter, bench_instance):

        mock_cursor = MagicMock()
        cursor_conn = MagicMock()
        cursor_conn.cursor.return_value = mock_cursor

        run_config = {
            "iterations": 1,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
            "stream_id": 0,
        }

        all_ok = _make_tpcds_power_test_result(success=True, queries_successful=1, queries_executed=1)
        mock_pt_cls.return_value.run.return_value = all_ok

        adapter._execute_tpcds_power_test(bench_instance, cursor_conn, run_config)

        factory = mock_pt_cls.call_args.kwargs["connection_factory"]
        factory()

        mock_conn_cls.assert_called_with(mock_cursor, adapter)

    @patch("benchbox.core.tpcds.power_test.TPCDSPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_factory_crash_produces_sentinel_failed_entry(
        self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection
    ):

        run_config = {
            "iterations": 1,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
            "stream_id": 0,
        }

        factory_error_msg = "Power Test execution failed: 'ClickHouseLocalClient' object has no attribute 'cursor'"
        factory_crashed = _make_tpcds_power_test_result(
            success=False,
            queries_successful=0,
            queries_executed=0,
            errors=[factory_error_msg],
        )
        mock_pt_cls.return_value.run.return_value = factory_crashed

        results = adapter._execute_tpcds_power_test(bench_instance, connection, run_config)

        assert len(results) > 0, "results must not be empty when power test factory crashes"

        sentinel = results[-1]
        assert sentinel["status"] == "FAILED"
        assert sentinel["query_id"] == "power_test_error"
        assert factory_error_msg in sentinel["error"]

    @patch("benchbox.core.tpcds.power_test.TPCDSPowerTest")
    @patch("benchbox.platforms.base.execution.PlatformAdapterConnection")
    def test_factory_crash_does_not_produce_sentinel_when_queries_ran(
        self, mock_conn_cls, mock_pt_cls, adapter, bench_instance, connection
    ):

        run_config = {
            "iterations": 1,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
            "stream_id": 0,
        }

        all_failed = _make_tpcds_power_test_result(success=False, queries_successful=0, queries_executed=99)
        mock_pt_cls.return_value.run.return_value = all_failed

        results = adapter._execute_tpcds_power_test(bench_instance, connection, run_config)

        assert len(results) == 99
        assert not any(r.get("query_id") == "power_test_error" for r in results)


class TestDetermineBenchmarkStatusZeroQueries:
    def test_zero_total_queries_is_failed(self):

        from benchbox.cli.output import ConsoleResultFormatter

        result = SimpleNamespace(total_queries=0, successful_queries=0, validation_status="PASSED")
        status, color = ConsoleResultFormatter._determine_benchmark_status(result)
        assert status == "FAILED"
        assert color == "red"


