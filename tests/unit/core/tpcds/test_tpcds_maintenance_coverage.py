from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.results.maintenance_method import MAINTENANCE_METHOD
from benchbox.core.tpcds.maintenance_functions import MAINTENANCE_FUNCTIONS, MAINTENANCE_FUNCTIONS_BY_NAME
from benchbox.core.tpcds.maintenance_test import (
    TPCDSMaintenanceOperation,
    TPCDSMaintenanceTest,
    TPCDSMaintenanceTestConfig,
    maintenance_unsupported_reason,
)
from benchbox.core.tpcds.refresh_data import SOURCE_TABLES, SOURCE_TABLES_BY_NAME, RefreshSet

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

ROWS = 5

RESULT_KEYS = {
    "config",
    "maintenance_method",
    "seed",
    "refresh_sets",
    "start_time",
    "end_time",
    "total_time",
    "insert_operations",
    "delete_operations",
    "total_operations",
    "successful_operations",
    "failed_operations",
    "operations",
    "overall_throughput",
    "success",
    "errors",
    "integrity_valid",
}


class _Cursor:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return (self._value,)


class _IntegrityConnection:
    def __init__(self, values):
        self._values = list(values)
        self.closed = False

    def execute(self, _sql):
        if not self._values:
            return _Cursor(0)
        return _Cursor(self._values.pop(0))

    def close(self):
        self.closed = True


class _RefreshConnection:
    def __init__(self, transactional: bool = True):
        self.counts: dict[str, int] = {}
        self.closed = False
        self.commits = 0
        if transactional:
            self.commit = self._commit

    def _commit(self):
        self.commits += 1

    def execute(self, sql):
        if sql.startswith("SELECT COUNT(*) FROM "):
            return _Cursor(self.counts.get(sql.removeprefix("SELECT COUNT(*) FROM "), 50))
        if sql.startswith("INSERT INTO "):
            table = sql.removeprefix("INSERT INTO ").split(" ", 1)[0]
            self.counts[table] = self.counts.get(table, 50) + ROWS
        elif sql.startswith("DELETE FROM "):
            table = sql.removeprefix("DELETE FROM ").split(" ", 1)[0]
            self.counts[table] = self.counts.get(table, 50) - 2
        return None

    def close(self):
        self.closed = True


def _refresh_set(set_id: int = 1) -> RefreshSet:
    return RefreshSet(
        set_id=set_id,
        directory=Path("/refresh"),
        files={table.name: Path(f"/refresh/{table.file_stem}_{set_id}.dat") for table in SOURCE_TABLES},
        row_counts={table.name: ROWS for table in SOURCE_TABLES},
    )


@pytest.fixture
def refresh_run(monkeypatch):
    generated: list[tuple[Any, ...]] = []
    loaded: list[tuple[str, int]] = []
    connections: list[_RefreshConnection] = []

    def generate_refresh_sets(scale_factor, first_set, count, output_dir, seed=None):
        generated.append((scale_factor, first_set, count, output_dir))
        return {set_id: _refresh_set(set_id) for set_id in range(first_set, first_set + count)}

    def load_refresh_file(connection, table, path, expected_rows):
        loaded.append((table.name, expected_rows))
        return expected_rows

    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.generate_refresh_sets", generate_refresh_sets)
    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.load_refresh_file", load_refresh_file)

    def factory():
        connection = _RefreshConnection()
        connections.append(connection)
        return connection

    test = TPCDSMaintenanceTest(benchmark=object(), connection_factory=factory, scale_factor=0.01)
    return SimpleNamespace(test=test, generated=generated, loaded=loaded, connections=connections)


def test_config_defaults_and_field_names():
    config = TPCDSMaintenanceTestConfig()

    assert [field.name for field in fields(config)] == [
        "scale_factor",
        "refresh_runs",
        "first_refresh_set",
        "validate_integrity",
        "verbose",
        "output_dir",
        "seed",
    ]
    assert config.scale_factor == 1.0
    assert config.refresh_runs == 1
    assert config.first_refresh_set == 1
    assert config.validate_integrity is True
    assert config.verbose is False
    assert config.output_dir is None
    assert config.seed is None


def test_operation_defaults_describe_a_function_run():
    operation = TPCDSMaintenanceOperation("INSERT", "store_sales", 1.0, 2.0, 1.0, 10, True)

    assert operation.error is None
    assert operation.function == ""
    assert operation.refresh_set == 0
    assert operation.staged_rows == {}
    assert operation.rows_by_table == {}
    assert operation.statements_executed == 0


def test_unsupported_reason_follows_the_adapter_flag():
    assert maintenance_unsupported_reason(SimpleNamespace(supports_refresh_functions=True)) is None

    reason = maintenance_unsupported_reason(SimpleNamespace(supports_refresh_functions=False, platform_name="citus"))
    assert reason == "TPC-DS maintenance functions are not supported by platform 'citus'"
    assert "SimpleNamespace" in maintenance_unsupported_reason(SimpleNamespace())
    assert maintenance_unsupported_reason(SimpleNamespace(supports_refresh_functions=1, platform_name="x")) is not None


def test_get_maintenance_operations_sql_lists_every_function_with_staging_and_statements():
    test = TPCDSMaintenanceTest(benchmark=object(), connection_factory=lambda: None)

    operations = test.get_maintenance_operations_sql()

    assert list(operations) == [function.name for function in MAINTENANCE_FUNCTIONS]
    assert len(operations) == 11
    for name, statements in operations.items():
        function = MAINTENANCE_FUNCTIONS_BY_NAME[name]
        assert statements[0].startswith("-- ")
        assert name in statements[0]
        for staging in function.staging_tables:
            assert SOURCE_TABLES_BY_NAME[staging].create_sql() in statements
        assert list(function.statements) == statements[-len(function.statements) :]
        assert all(
            statement.startswith("CREATE TABLE s_") for statement in statements[1 : 1 + len(function.staging_tables)]
        )


def test_validate_data_integrity_detects_orphans():
    conn = _IntegrityConnection([2, 0, 0, 0])
    test = TPCDSMaintenanceTest(benchmark=object(), connection_factory=lambda: conn)

    assert test.validate_data_integrity() is False
    assert conn.closed is True


def test_validate_data_integrity_passes_when_no_orphans_exist():
    conn = _IntegrityConnection([0, 0, 0, 0])
    test = TPCDSMaintenanceTest(benchmark=object(), connection_factory=lambda: conn)

    assert test.validate_data_integrity() is True
    assert conn.closed is True


def test_validate_data_integrity_returns_false_when_connection_cannot_open():
    def factory():
        raise RuntimeError("no connection")

    assert TPCDSMaintenanceTest(benchmark=object(), connection_factory=factory).validate_data_integrity() is False


def test_run_result_has_documented_keys_and_counts(refresh_run):
    result = refresh_run.test.run(TPCDSMaintenanceTestConfig(scale_factor=0.01, validate_integrity=False))

    assert set(result) == RESULT_KEYS
    assert result["maintenance_method"] == MAINTENANCE_METHOD
    assert result["refresh_sets"] == [1]
    assert result["total_operations"] == 11
    assert result["successful_operations"] == 11
    assert result["failed_operations"] == 0
    assert result["insert_operations"] == 7
    assert result["delete_operations"] == 4
    assert result["success"] is True
    assert result["errors"] == []
    assert result["integrity_valid"] is None
    assert result["end_time"] >= result["start_time"]
    assert [op.function for op in result["operations"]] == [function.name for function in MAINTENANCE_FUNCTIONS]
    assert all(connection.closed for connection in refresh_run.connections[1:])
    assert len(refresh_run.connections) == 12


def test_run_records_row_deltas_staged_rows_and_refresh_set(refresh_run):
    result = refresh_run.test.run(TPCDSMaintenanceTestConfig(scale_factor=0.01, validate_integrity=False))
    operations = {op.function: op for op in result["operations"]}

    assert operations["LF_SS"].rows_by_table == {"store_sales": ROWS}
    assert operations["LF_SS"].rows_affected == ROWS
    assert operations["LF_SS"].staged_rows == {"s_purchase": ROWS, "s_purchase_lineitem": ROWS}
    assert operations["DF_SS"].rows_by_table == {"store_returns": -2, "store_sales": -2}
    assert operations["DF_SS"].rows_affected == 4
    assert operations["DF_I"].statements_executed == 1
    assert {op.refresh_set for op in result["operations"]} == {1}
    assert operations["LF_CS"].table_name == "catalog_sales"
    assert operations["DF_CS"].table_name == "catalog_returns, catalog_sales"


def test_run_passes_refresh_options_to_generation(refresh_run):
    refresh_run.test.run(
        TPCDSMaintenanceTestConfig(scale_factor=0.5, refresh_runs=2, first_refresh_set=4, validate_integrity=False)
    )

    assert len(refresh_run.generated) == 1
    scale_factor, first_set, count, output_dir = refresh_run.generated[0]
    assert (scale_factor, first_set, count) == (0.5, 4, 2)
    assert isinstance(output_dir, Path)


def test_run_without_config_uses_constructor_scale_factor_and_validates_integrity(refresh_run, monkeypatch):
    monkeypatch.setattr(refresh_run.test, "validate_data_integrity", lambda: True)

    result = refresh_run.test.run()

    assert refresh_run.generated[0][0] == 0.01
    assert result["integrity_valid"] is True


def test_run_skips_integrity_validation_after_a_failure(refresh_run, monkeypatch):
    monkeypatch.setattr(refresh_run.test, "validate_data_integrity", lambda: pytest.fail("must not validate"))
    monkeypatch.setattr(
        "benchbox.core.tpcds.maintenance_test.load_refresh_file",
        lambda *args: (_ for _ in ()).throw(RuntimeError("load failed")),
    )

    result = refresh_run.test.run(TPCDSMaintenanceTestConfig(scale_factor=0.01))

    assert result["success"] is False
    assert result["integrity_valid"] is None
    assert result["failed_operations"] == 1
    assert result["errors"] == ["DF_CS on refresh set 1 failed: load failed"]
    assert refresh_run.connections[1].closed is True


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (TPCDSMaintenanceTestConfig(refresh_runs=0), "refresh_runs must be positive, got 0"),
        (TPCDSMaintenanceTestConfig(first_refresh_set=-1), "first_refresh_set must be positive, got -1"),
        (TPCDSMaintenanceTestConfig(refresh_runs=True), "refresh_runs must be an integer, got bool"),
        (TPCDSMaintenanceTestConfig(refresh_runs=1.5), "refresh_runs must be an integer, got float"),
    ],
)
def test_run_rejects_invalid_refresh_options(refresh_run, config, message):
    result = refresh_run.test.run(config)

    assert result["success"] is False
    assert result["errors"] == [f"Maintenance Test execution failed: {message}"]
    assert result["operations"] == []
    assert refresh_run.generated == []


def test_run_reports_generation_failure(refresh_run, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("dsdgen exploded")

    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.generate_refresh_sets", broken)

    result = refresh_run.test.run(TPCDSMaintenanceTestConfig(scale_factor=0.01))

    assert result["success"] is False
    assert result["errors"] == ["Maintenance Test execution failed: dsdgen exploded"]
    assert result["end_time"]
    assert len(refresh_run.connections) == 1
    assert refresh_run.connections[0].closed is False


def test_function_fails_when_connection_has_no_commit(monkeypatch):
    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.load_refresh_file", lambda *args: ROWS)
    connection = _RefreshConnection(transactional=False)
    test = TPCDSMaintenanceTest(benchmark=object(), connection_factory=lambda: connection)

    operation = test._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["LF_I"], _refresh_set())

    assert operation.success is False
    assert "commit() support" in operation.error
    assert connection.closed is True


def test_function_fails_when_connection_close_fails(monkeypatch):
    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.load_refresh_file", lambda *args: ROWS)
    connection = _RefreshConnection()

    def close():
        raise RuntimeError("close boom")

    connection.close = close
    test = TPCDSMaintenanceTest(benchmark=object(), connection_factory=lambda: connection)

    operation = test._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["LF_I"], _refresh_set())

    assert operation.success is False
    assert operation.error == "Connection close failed: close boom"


def test_function_reports_connection_open_failure():
    def factory():
        raise RuntimeError("cannot connect")

    test = TPCDSMaintenanceTest(benchmark=object(), connection_factory=factory)

    operation = test._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["LF_I"], _refresh_set())

    assert operation.success is False
    assert operation.error == "cannot connect"
    assert operation.start_time == 0.0


def test_total_time_spans_first_start_to_last_end_and_throughput_uses_it(refresh_run, monkeypatch):
    ticks = iter(range(100, 200))
    monkeypatch.setattr("benchbox.core.tpcds.maintenance_test.mono_time", lambda: float(next(ticks)))

    result = refresh_run.test.run(TPCDSMaintenanceTestConfig(scale_factor=0.01, validate_integrity=False))

    first, last = result["operations"][0], result["operations"][-1]
    assert result["total_time"] == last.end_time - first.start_time == 21.0
    assert result["overall_throughput"] == pytest.approx(11 / 21.0)
