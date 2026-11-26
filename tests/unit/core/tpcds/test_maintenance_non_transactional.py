from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.tpcds import maintenance_test
from benchbox.core.tpcds.maintenance_functions import (
    CLICKHOUSE_DIALECT,
    MAINTENANCE_FUNCTIONS,
    MAINTENANCE_FUNCTIONS_BY_NAME,
)
from benchbox.core.tpcds.maintenance_test import TPCDSMaintenanceTest, maintenance_unsupported_reason
from benchbox.platforms.cedardb import CedarDBAdapter
from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter
from tests.utilities.optional_engines import chdb_skip_reason, chdb_usable

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcds]

RETURNS_DELETES = ("DF_CS", "DF_SS", "DF_WS")


class _Connection:
    def __init__(self, adapter: Any, counts: list[int]) -> None:
        self.platform_adapter = adapter
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._counts = iter(counts)
        self._last: str = ""

    def execute(self, statement: str):
        self.statements.append(statement)
        self._last = statement
        return self

    def fetchone(self):
        return (next(self._counts),) if self._last.startswith("SELECT COUNT(*)") else None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def stubbed_staging(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(maintenance_test, "create_staging_tables", lambda connection, tables: None)
    monkeypatch.setattr(maintenance_test, "drop_staging_tables", lambda connection, tables: None)
    monkeypatch.setattr(maintenance_test, "load_refresh_file", lambda connection, table, path, rows: rows)


def _refresh_set() -> SimpleNamespace:
    return SimpleNamespace(set_id=1, row_counts={"s_inventory": 5}, path_for=lambda name: name)


def _run_lf_i(adapter: Any) -> tuple[Any, _Connection]:
    connection = _Connection(adapter, [10, 15])
    harness = TPCDSMaintenanceTest(benchmark=None, connection_factory=lambda: connection, dialect="duckdb")
    operation = harness._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["LF_I"], _refresh_set())
    return operation, connection


def test_adapter_without_transactions_runs_the_function_without_begin_or_commit(stubbed_staging) -> None:
    operation, connection = _run_lf_i(SimpleNamespace(supports_refresh_transactions=False))

    assert operation.success is True, operation.error
    assert operation.transactional is False
    assert operation.rows_by_table == {"inventory": 5}
    assert "BEGIN" not in connection.statements
    assert connection.commits == 0
    assert connection.rollbacks == 0


def test_default_adapter_still_wraps_the_function_in_a_transaction(stubbed_staging) -> None:
    operation, connection = _run_lf_i(SimpleNamespace())

    assert operation.success is True, operation.error
    assert operation.transactional is True
    assert connection.statements[1] == "BEGIN"
    assert connection.commits >= 1


def test_failed_function_on_a_non_transactional_adapter_does_not_roll_back(
    stubbed_staging, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(connection, table, path, rows):
        raise RuntimeError("load failed")

    monkeypatch.setattr(maintenance_test, "load_refresh_file", boom)

    operation, connection = _run_lf_i(SimpleNamespace(supports_refresh_transactions=False))

    assert operation.success is False
    assert "load failed" in operation.error
    assert connection.rollbacks == 0


class _ImplicitTransactionDriver:
    autocommit = False

    def __init__(self) -> None:
        self.in_transaction = False

    def begin_statement(self, statement: str) -> None:
        if statement == "BEGIN" and self.in_transaction:
            raise RuntimeError("there is already an active transaction")
        self.in_transaction = True


class _ImplicitTransactionConnection(_Connection):
    def __init__(self, adapter: Any, counts: list[int]) -> None:
        super().__init__(adapter, counts)
        self.connection = _ImplicitTransactionDriver()

    def execute(self, statement: str):
        self.connection.begin_statement(statement)
        return super().execute(statement)

    def commit(self) -> None:
        self.connection.in_transaction = False
        super().commit()


def test_a_driver_with_implicit_transactions_gets_no_explicit_begin(stubbed_staging) -> None:
    connection = _ImplicitTransactionConnection(SimpleNamespace(), [10, 15])
    harness = TPCDSMaintenanceTest(benchmark=None, connection_factory=lambda: connection, dialect="duckdb")

    operation = harness._execute_function(MAINTENANCE_FUNCTIONS_BY_NAME["LF_I"], _refresh_set())

    assert operation.success is True, operation.error
    assert operation.rows_by_table == {"inventory": 5}
    assert "BEGIN" not in connection.statements
    assert connection.commits >= 2


@pytest.mark.skipif(not chdb_usable(), reason=chdb_skip_reason() or "chDB is usable")
def test_clickhouse_local_and_cedardb_both_run_tpcds_maintenance() -> None:
    assert maintenance_unsupported_reason(ClickHouseLocalAdapter()) is None
    assert maintenance_unsupported_reason(CedarDBAdapter()) is None


@pytest.mark.parametrize("name", RETURNS_DELETES)
def test_clickhouse_returns_deletes_are_uncorrelated_tuple_in(name: str) -> None:
    statements = MAINTENANCE_FUNCTIONS_BY_NAME[name].statements_for(CLICKHOUSE_DIALECT)

    returns_delete, sales_delete = statements
    assert returns_delete.startswith("DELETE FROM ")
    assert "_returns WHERE (" in returns_delete
    assert ") IN (SELECT " in returns_delete
    assert "EXISTS" not in returns_delete.upper()
    assert sales_delete == MAINTENANCE_FUNCTIONS_BY_NAME[name].statements[1]


def test_other_dialects_keep_the_exists_form() -> None:
    for name in RETURNS_DELETES:
        function = MAINTENANCE_FUNCTIONS_BY_NAME[name]
        assert "EXISTS" in function.statements_for("mysql")[0].upper()
        for dialect in (None, "duckdb", "postgres"):
            assert function.statements_for(dialect) == function.statements
            assert "EXISTS" in function.statements_for(dialect)[0].upper()


def test_only_the_three_returns_deletes_have_a_clickhouse_rewrite() -> None:
    rewritten = {function.name for function in MAINTENANCE_FUNCTIONS if function.dialect_statements}

    assert rewritten == set(RETURNS_DELETES)
