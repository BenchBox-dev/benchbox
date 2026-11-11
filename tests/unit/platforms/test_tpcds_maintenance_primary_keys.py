from __future__ import annotations

import sqlite3
from unittest.mock import Mock

import pytest

from benchbox.core.contracts.platform_capabilities import resolve_primary_key_probe
from benchbox.core.tpcds import maintenance_test as maintenance_test_module
from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS
from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest, TPCDSMaintenanceTestConfig
from benchbox.platforms.postgresql import PostgreSQLAdapter
from benchbox.platforms.snowflake import SnowflakeAdapter
from benchbox.platforms.sqlite import SQLiteAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TARGET_TABLES = sorted({table for function in MAINTENANCE_FUNCTIONS for table in function.target_tables})


class _ProbingConnection:
    def __init__(self, enforced: list[str]) -> None:
        self.platform_adapter = Mock()
        self.platform_adapter.enforced_primary_key_tables.return_value = enforced
        self.closes = 0

    def close(self) -> None:
        self.closes += 1


def _harness(connection: _ProbingConnection) -> TPCDSMaintenanceTest:
    return TPCDSMaintenanceTest(benchmark=None, connection_factory=lambda: connection, scale_factor=0.01)


def test_enforced_keys_are_refused_with_the_conflict_and_the_way_out(monkeypatch):
    generated = Mock()
    monkeypatch.setattr(maintenance_test_module, "generate_refresh_sets", generated)
    connection = _ProbingConnection(["catalog_sales", "store_sales"])

    result = _harness(connection).run(TPCDSMaintenanceTestConfig(scale_factor=0.01, validate_integrity=False))

    assert result["success"] is False
    assert result["operations"] == []
    generated.assert_not_called()
    (message,) = result["errors"]
    assert "primary keys are enforced on catalog_sales, store_sales" in message
    assert "overlap the base data" in message
    assert "--tuning notuning" in message
    assert connection.closes == 1
    connection.platform_adapter.enforced_primary_key_tables.assert_called_once_with(connection, TARGET_TABLES)


def test_unenforced_keys_proceed_to_refresh_generation(monkeypatch):
    generated = Mock(side_effect=RuntimeError("generation reached"))
    monkeypatch.setattr(maintenance_test_module, "generate_refresh_sets", generated)

    result = _harness(_ProbingConnection([])).run(TPCDSMaintenanceTestConfig(scale_factor=0.01))

    generated.assert_called_once()
    assert result["errors"] == ["Maintenance Test execution failed: generation reached"]


def test_connection_without_a_probe_is_not_refused(monkeypatch):
    generated = Mock(side_effect=RuntimeError("generation reached"))
    monkeypatch.setattr(maintenance_test_module, "generate_refresh_sets", generated)
    connection = Mock(spec=["close"])

    _harness(connection).run(TPCDSMaintenanceTestConfig(scale_factor=0.01))

    generated.assert_called_once()


def test_connection_without_an_adapter_is_not_closed_by_the_probe(monkeypatch):
    monkeypatch.setattr(maintenance_test_module, "generate_refresh_sets", Mock(side_effect=RuntimeError("reached")))
    connection = Mock(spec=["close"])

    _harness(connection).run(TPCDSMaintenanceTestConfig(scale_factor=0.01))

    connection.close.assert_not_called()


def test_snowflake_declares_no_probe_because_its_keys_are_informational():
    assert resolve_primary_key_probe(SnowflakeAdapter.__new__(SnowflakeAdapter)) is None


def test_sqlite_reports_only_tables_declaring_a_primary_key():
    adapter = SQLiteAdapter()
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE store_sales (ss_item_sk INTEGER, ss_ticket_number INTEGER, PRIMARY KEY (ss_item_sk))"
    )
    connection.execute("CREATE TABLE web_sales (ws_item_sk INTEGER, ws_order_number INTEGER)")

    assert adapter.enforced_primary_key_tables(connection, ["store_sales", "web_sales", "catalog_sales"]) == [
        "store_sales"
    ]


def test_duckdb_reports_only_tables_declaring_a_primary_key():
    duckdb = pytest.importorskip("duckdb")
    from benchbox.platforms.duckdb import DuckDBAdapter

    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE store_sales (ss_item_sk INTEGER, ss_ticket_number INTEGER, PRIMARY KEY (ss_item_sk))"
    )
    connection.execute("CREATE TABLE web_sales (ws_item_sk INTEGER, ws_order_number INTEGER)")

    assert DuckDBAdapter.__new__(DuckDBAdapter).enforced_primary_key_tables(
        connection, ["store_sales", "web_sales", "catalog_sales"]
    ) == ["store_sales"]


def test_postgresql_filters_primary_key_constraints_to_the_maintenance_tables():
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = [("store_sales",), ("customer",), ("Web_Sales",)]

    enforced = PostgreSQLAdapter.__new__(PostgreSQLAdapter).enforced_primary_key_tables(
        connection, ["store_sales", "web_sales", "catalog_sales"]
    )

    assert enforced == ["store_sales", "web_sales"]
    assert "contype = 'p'" in connection.execute.call_args.args[0]


def test_singlestore_supports_tpcds_maintenance_and_tpch_refresh():
    from benchbox.core.tpcds.maintenance_test import maintenance_unsupported_reason
    from benchbox.platforms.singlestore import SingleStoreAdapter

    adapter = SingleStoreAdapter.__new__(SingleStoreAdapter)

    assert maintenance_unsupported_reason(adapter) is None
    assert SingleStoreAdapter.supports_refresh_functions is True


