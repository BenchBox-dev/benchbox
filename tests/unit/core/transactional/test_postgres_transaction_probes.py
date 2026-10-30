from __future__ import annotations

import re
from pathlib import Path

import pytest

from benchbox.core.primitives_benchmark_utils import table_exists
from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Cursor:
    def __init__(self, rows: list[tuple]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _AbortOnErrorConnection:
    def __init__(self, tables: set[str]):
        self.tables = set(tables)
        self.aborted = False
        self.statements: list[str] = []

    def execute(self, sql: str):
        self.statements.append(sql)
        if self.aborted:
            raise RuntimeError("current transaction is aborted, commands ignored until end of transaction block")
        regclass = re.search(r"to_regclass\('\"?(\w+)\"?'\)", sql)
        if regclass:
            return _Cursor([(regclass.group(1) in self.tables,)])
        created = re.match(r"\s*CREATE TABLE IF NOT EXISTS\s+\"?(\w+)\"?", sql)
        if created:
            self.tables.add(created.group(1))
            return _Cursor([])
        referenced = re.search(r"FROM\s+\"?(\w+)\"?", sql)
        if referenced and referenced.group(1) not in self.tables:
            self.aborted = True
            raise RuntimeError(f'relation "{referenced.group(1)}" does not exist')
        if "benchbox_staging_manifest" in sql:
            return _Cursor([(0,)])
        return _Cursor([(1,)])


def test_table_exists_on_postgres_does_not_abort_the_transaction(tmp_path: Path) -> None:
    connection = _AbortOnErrorConnection({"orders"})

    assert table_exists(connection, "orders", lambda _msg: None, "postgres") is True
    assert table_exists(connection, "insert_ops_lineitem", lambda _msg: None, "postgres") is False
    assert connection.aborted is False


def test_write_primitives_is_setup_on_postgres_does_not_abort_the_transaction(tmp_path: Path) -> None:
    benchmark = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    benchmark._setup_dialect = "postgres"
    connection = _AbortOnErrorConnection({"orders", "lineitem"})

    assert benchmark.is_setup(connection) is False
    assert connection.aborted is False


def test_staging_manifest_probe_on_postgres_does_not_abort_the_transaction(tmp_path: Path) -> None:
    benchmark = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    benchmark._setup_dialect = "postgres"
    connection = _AbortOnErrorConnection({"orders", "lineitem"})

    assert benchmark._staging_manifest_matches(connection, ["orders", "lineitem"]) is False
    assert connection.aborted is False
    assert not any(s.startswith("CREATE TABLE") for s in connection.statements)


def test_transaction_primitives_is_setup_on_postgres_does_not_abort_the_transaction(tmp_path: Path) -> None:
    benchmark = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    benchmark._setup_dialect = "postgres"
    connection = _AbortOnErrorConnection({"orders", "lineitem", "customer"})

    assert benchmark.is_setup(connection) is False
    assert connection.aborted is False
