from __future__ import annotations

import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.results.fetch_mode import ConversionClock
from benchbox.core.results.timer_scope import TIMING_METHOD_QUERY_ONLY
from benchbox.platforms import postgresql as postgresql_module
from benchbox.platforms.base.psycopg2_mixin import PsycopgConnectionMixin
from benchbox.platforms.base.timing_contract import TimingContractMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _PsycopgAdapter(PsycopgConnectionMixin, TimingContractMixin):
    def __init__(self):
        self.logger = Mock()

    def log_verbose(self, message):
        return None

    def _build_query_result_with_validation(self, **kwargs):
        timing_fields = kwargs.pop("timing_fields", {})
        return {"status": "SUCCESS", "execution_time_seconds": kwargs["execution_time"], **timing_fields}


def _psycopg_connection(*, autocommit, transaction_status, rows=((1, "a"), (2, "b"))):
    cursor = MagicMock()
    cursor.fetchall.return_value = list(rows)
    cursor.description = [("c1",), ("c2",)]
    connection = MagicMock()
    connection.cursor.return_value = cursor
    connection.autocommit = autocommit
    connection.info.transaction_status = transaction_status
    return connection


@pytest.mark.parametrize(
    ("autocommit", "transaction_status", "expected_requests"),
    [
        (False, 0, 2),
        (False, 2, 1),
        (True, 0, 1),
    ],
)
def test_psycopg_counts_implicit_begin_only_when_driver_sends_it(autocommit, transaction_status, expected_requests):
    connection = _psycopg_connection(autocommit=autocommit, transaction_status=transaction_status)

    result = _PsycopgAdapter().execute_query(connection, "SELECT 1", "q1")

    assert result["request_count"] == expected_requests
    assert result["result_bytes"] > 0


def test_psycopg_unknown_connection_state_counts_only_the_query():
    connection = MagicMock()
    connection.cursor.return_value.fetchall.return_value = [(1,)]
    connection.cursor.return_value.description = [("c",)]

    result = _PsycopgAdapter().execute_query(connection, "SELECT 1", "q1")

    assert result["request_count"] == 1


def test_psycopg_validation_runs_outside_the_query_timer():
    connection = _psycopg_connection(autocommit=True, transaction_status=0)
    clock = ConversionClock().start()

    def slow_validation(**kwargs):
        time.sleep(0.05)

    with patch("benchbox.core.validation.query_validation.QueryValidator") as validator:
        validator.return_value.validate_query_result.side_effect = slow_validation
        result = _PsycopgAdapter().execute_query(connection, "SELECT 1", "q1", benchmark_type="tpch")
    excluded = clock.finish()

    assert result["status"] == "SUCCESS"
    assert excluded >= 0.05
    assert result["execution_time_seconds"] < 0.05


@pytest.mark.parametrize(
    "path",
    [
        ("benchbox.platforms.postgresql", "PostgreSQLAdapter"),
        ("benchbox.platforms.timescaledb", "TimescaleDBAdapter"),
        ("benchbox.platforms.citus", "CitusAdapter"),
        ("benchbox.platforms.paradedb", "ParadeDBAdapter"),
        ("benchbox.platforms.cedardb", "CedarDBAdapter"),
        ("benchbox.platforms.pg_duckdb", "PgDuckDBAdapter"),
        ("benchbox.platforms.pg_mooncake", "PgMooncakeAdapter"),
        ("benchbox.platforms.questdb", "QuestDBAdapter"),
        ("benchbox.platforms.singlestore", "SingleStoreAdapter"),
        ("benchbox.platforms.doris", "DorisAdapter"),
        ("benchbox.platforms.starrocks.adapter", "StarRocksAdapter"),
        ("benchbox.platforms.gizmosql", "GizmoSQLAdapter"),
        ("benchbox.platforms.influxdb.adapter", "InfluxDBAdapter"),
        ("benchbox.platforms.databend.adapter", "DatabendAdapter"),
        ("benchbox.platforms.firebolt", "FireboltAdapter"),
    ],
    ids=lambda path: path[1],
)
def test_sql_family_adapters_use_query_only_timer(path):
    import importlib

    adapter_class = getattr(importlib.import_module(path[0]), path[1])

    assert adapter_class.timing_method == TIMING_METHOD_QUERY_ONLY
    assert adapter_class.platform_timing_source is None


def test_pg_mooncake_retry_counts_the_failed_attempt():
    from benchbox.platforms import pg_mooncake as pg_mooncake_module
    from benchbox.platforms.pg_mooncake import PgMooncakeAdapter

    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"
    failed = {"query_id": "1", "status": "FAILED", "error": "DuckDB execution is not supported inside functions"}
    succeeded = {"query_id": "1", "status": "SUCCESS", "request_count": 2}

    with (
        patch.object(pg_mooncake_module, "psycopg", mock_psycopg),
        patch.object(postgresql_module, "psycopg", mock_psycopg),
    ):
        adapter = PgMooncakeAdapter()
        with patch.object(postgresql_module.PostgreSQLAdapter, "execute_query", side_effect=[failed, succeeded]):
            result = adapter.execute_query(Mock(), "SELECT 1", "1")

    assert result["request_count"] == 4


def test_pg_mooncake_transaction_closers_are_excluded_from_the_query_timer():
    from benchbox.platforms import pg_mooncake as pg_mooncake_module
    from benchbox.platforms.pg_mooncake import PgMooncakeAdapter

    mock_psycopg = Mock()
    mock_psycopg.__version__ = "3.1.0"
    connection = Mock()
    connection.commit.side_effect = lambda: time.sleep(0.05)
    clock = ConversionClock().start()

    with (
        patch.object(pg_mooncake_module, "psycopg", mock_psycopg),
        patch.object(postgresql_module, "psycopg", mock_psycopg),
    ):
        adapter = PgMooncakeAdapter()
        adapter._close_mooncake_query_transaction(connection, action="commit", phase="test")
    excluded = clock.finish()

    assert excluded >= 0.05


def _databend_adapter():
    from benchbox.platforms.databend.adapter import DatabendAdapter

    with patch("benchbox.platforms.databend.adapter.DATABEND_AVAILABLE", True):
        try:
            return DatabendAdapter(host="localhost", ssl=False, password="")
        except ImportError:
            pytest.skip("databend-driver not installed")


def test_databend_records_one_request_and_result_bytes_without_the_use_statement():
    adapter = _databend_adapter()
    connection = Mock()
    connection.query_iter.return_value = [(1, "test"), (2, "test2")]

    result = adapter.execute_query(connection, "SELECT * FROM test", "q1")

    assert result["status"] == "SUCCESS"
    assert result["request_count"] == 1
    assert result["result_bytes"] > 0
    connection.exec.assert_called_once()


def test_databend_failed_query_has_no_timing_fields():
    adapter = _databend_adapter()
    connection = Mock()
    connection.query_iter.side_effect = RuntimeError("boom")

    result = adapter.execute_query(connection, "SELECT 1", "q1")

    assert result["status"] == "FAILED"
    assert "request_count" not in result


def test_firebolt_records_one_request_and_creates_cursor_outside_the_timer():
    from benchbox.platforms.firebolt import FireboltAdapter

    try:
        adapter = FireboltAdapter()
    except ImportError:
        pytest.skip("Firebolt SDK not installed")
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [(1, "test"), (2, "test2")]

    result = adapter.execute_query(connection, "SELECT * FROM test", "q1")

    assert result["status"] == "SUCCESS"
    assert result["request_count"] == 1
    assert result["result_bytes"] > 0
    cursor.close.assert_called_once()
