from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTestConfig
from benchbox.platforms.base import PlatformAdapterConnection
from tests.unit.platforms.test_base_adapter import MockPlatformAdapterWithDialect

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class _RefreshAdapter(MockPlatformAdapterWithDialect):
    supports_refresh_functions = True


class _UnsupportedAdapter(MockPlatformAdapterWithDialect):
    supports_refresh_functions = False


def _operation(function: str, operation_type: str, table: str, rows: int, success: bool = True, error=None):
    return SimpleNamespace(
        function=function,
        operation_type=operation_type,
        table_name=table,
        duration=0.5,
        rows_affected=rows,
        success=success,
        error=error,
    )


def _run_dict(operations: list[Any], success: bool = True) -> dict[str, Any]:
    return {
        "success": success,
        "maintenance_method": "dsdgen_refresh_sets",
        "refresh_sets": [1],
        "operations": operations,
        "insert_operations": sum(1 for op in operations if op.operation_type == "INSERT"),
        "delete_operations": sum(1 for op in operations if op.operation_type == "DELETE"),
        "total_operations": len(operations),
        "successful_operations": sum(1 for op in operations if op.success),
        "failed_operations": sum(1 for op in operations if not op.success),
        "total_time": 1.0,
        "overall_throughput": 2.0,
        "errors": [] if success else ["LF_SS on refresh set 1 failed: boom"],
    }


class _FakeMaintenanceTest:
    result: dict[str, Any] = {}
    instances: list[_FakeMaintenanceTest] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.config: TPCDSMaintenanceTestConfig | None = None
        self.output_dir = kwargs["output_dir"]
        _FakeMaintenanceTest.instances.append(self)

    def run(self, config: TPCDSMaintenanceTestConfig | None = None) -> dict[str, Any]:
        self.config = config
        return _FakeMaintenanceTest.result


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeMaintenanceTest.instances = []
    _FakeMaintenanceTest.result = {}
    yield


@patch("benchbox.platforms.base.execution.quiet_console")
def test_unsupported_adapter_returns_single_skipped_row_and_runs_nothing(_console):
    adapter = _UnsupportedAdapter()

    with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", _FakeMaintenanceTest):
        rows = adapter._execute_tpcds_maintenance_test(Mock(), Mock(), {"scale_factor": 0.01})

    assert _FakeMaintenanceTest.instances == []
    assert adapter._last_maintenance_test_result is None
    assert len(rows) == 1
    assert rows[0]["query_id"] == "maintenance_not_supported"
    assert rows[0]["status"] == "SKIPPED"
    assert rows[0]["test_type"] == "maintenance"
    assert rows[0]["rows_returned"] == 0
    assert "not supported" in rows[0]["error"]


@patch("benchbox.platforms.base.execution.quiet_console")
def test_supported_adapter_returns_one_row_per_function_with_function_name_as_query_id(_console, tmp_path):
    adapter = _RefreshAdapter()
    _FakeMaintenanceTest.result = _run_dict(
        [
            _operation("DF_SS", "DELETE", "store_returns, store_sales", 157),
            _operation("LF_SS", "INSERT", "store_sales", 144, success=False, error="lock timeout"),
        ],
        success=False,
    )

    with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", _FakeMaintenanceTest):
        rows = adapter._execute_tpcds_maintenance_test(
            Mock(), Mock(), {"scale_factor": 0.01, "output_dir": str(tmp_path / "maintenance")}
        )

    assert rows == [
        {
            "query_id": "DF_SS",
            "execution_time_seconds": 0.5,
            "status": "SUCCESS",
            "rows_returned": 157,
            "test_type": "maintenance",
            "operation_type": "DELETE",
            "table_name": "store_returns, store_sales",
        },
        {
            "query_id": "LF_SS",
            "execution_time_seconds": 0.5,
            "status": "FAILED",
            "rows_returned": 144,
            "test_type": "maintenance",
            "operation_type": "INSERT",
            "table_name": "store_sales",
            "error": "lock timeout",
        },
    ]
    assert _FakeMaintenanceTest.instances[0].kwargs["output_dir"] == tmp_path / "maintenance"
    assert _FakeMaintenanceTest.instances[0].kwargs["dialect"] == "mock_dialect"


@patch("benchbox.platforms.base.execution.quiet_console")
def test_failed_operation_without_error_text_reports_unknown_error(_console):
    adapter = _RefreshAdapter()
    _FakeMaintenanceTest.result = _run_dict(
        [_operation("LF_I", "INSERT", "inventory", 0, success=False)], success=False
    )

    with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", _FakeMaintenanceTest):
        rows = adapter._execute_tpcds_maintenance_test(Mock(), Mock(), {"scale_factor": 0.01})

    assert rows[0]["status"] == "FAILED"
    assert rows[0]["error"] == "Unknown error"


@patch("benchbox.platforms.base.execution.quiet_console")
def test_last_maintenance_result_is_none_at_start_and_holds_the_run_dict_afterwards(_console):
    adapter = _RefreshAdapter()
    assert adapter._last_maintenance_test_result is None
    adapter._last_maintenance_test_result = {"stale": True}
    run_dict = _run_dict([_operation("LF_SS", "INSERT", "store_sales", 144)])
    _FakeMaintenanceTest.result = run_dict
    seen_at_run: list[Any] = []

    class _Observing(_FakeMaintenanceTest):
        def run(self, config=None):
            seen_at_run.append(adapter._last_maintenance_test_result)
            return super().run(config)

    with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", _Observing):
        adapter._execute_tpcds_maintenance_test(Mock(), Mock(), {"scale_factor": 0.01})

    assert seen_at_run == [None]
    assert adapter._last_maintenance_test_result is run_dict


@patch("benchbox.platforms.base.execution.quiet_console")
def test_connection_factory_wraps_stream_connection_in_maintenance_mode(_console):
    adapter = _RefreshAdapter()
    connection = Mock()
    connection.cursor.return_value = Mock(name="maintenance_cursor")
    _FakeMaintenanceTest.result = _run_dict([_operation("LF_SS", "INSERT", "store_sales", 1)])

    with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", _FakeMaintenanceTest):
        adapter._execute_tpcds_maintenance_test(Mock(), connection, {"scale_factor": 0.25})

    factory = _FakeMaintenanceTest.instances[0].kwargs["connection_factory"]
    wrapped = factory()

    assert isinstance(wrapped, PlatformAdapterConnection)
    assert wrapped._maintenance_mode is True
    assert wrapped.platform_adapter is adapter
    assert wrapped.benchmark_type == "tpcds"
    assert wrapped.scale_factor == 0.25
    assert wrapped.connection is connection.cursor.return_value


@patch("benchbox.platforms.base.execution.quiet_console")
def test_run_config_refresh_options_reach_maintenance_config(_console):
    adapter = _RefreshAdapter()
    _FakeMaintenanceTest.result = _run_dict([_operation("LF_SS", "INSERT", "store_sales", 1)])
    run_config = {
        "scale_factor": 0.01,
        "tpcds_refresh_runs": 3,
        "tpcds_refresh_first_set": 5,
        "validate_integrity": False,
    }

    with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", _FakeMaintenanceTest):
        adapter._execute_tpcds_maintenance_test(Mock(), Mock(), run_config)

    config = _FakeMaintenanceTest.instances[0].config
    assert config.scale_factor == 0.01
    assert config.refresh_runs == 3
    assert config.first_refresh_set == 5
    assert config.validate_integrity is False


@patch("benchbox.platforms.base.execution.quiet_console")
def test_default_refresh_options_are_one_run_from_set_one(_console):
    adapter = _RefreshAdapter()
    _FakeMaintenanceTest.result = _run_dict([_operation("LF_SS", "INSERT", "store_sales", 1)])
    benchmark = SimpleNamespace()

    with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", _FakeMaintenanceTest):
        adapter._execute_tpcds_maintenance_test(benchmark, Mock(), {"scale_factor": 0.01})

    config = _FakeMaintenanceTest.instances[0].config
    assert (config.refresh_runs, config.first_refresh_set, config.validate_integrity) == (1, 1, True)
    assert isinstance(_FakeMaintenanceTest.instances[0].output_dir, Path)


@patch("benchbox.platforms.base.execution.quiet_console")
def test_run_exception_becomes_error_row_and_leaves_no_result(_console):
    adapter = _RefreshAdapter()

    class _Broken(_FakeMaintenanceTest):
        def run(self, config=None):
            raise RuntimeError("refresh boom")

    with patch("benchbox.core.tpcds.maintenance_test.TPCDSMaintenanceTest", _Broken):
        rows = adapter._execute_tpcds_maintenance_test(Mock(), Mock(), {"scale_factor": 0.01})

    assert adapter._last_maintenance_test_result is None
    assert rows == [
        {
            "query_id": "maintenance_test_error",
            "execution_time_seconds": 0.0,
            "status": "FAILED",
            "rows_returned": 0,
            "error": "refresh boom",
            "test_type": "maintenance",
        }
    ]
