from __future__ import annotations

import sqlite3
from unittest.mock import Mock

import pytest

from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.sql_compat.rules.execution_filter.singlestore_transaction_primitives import (
    SINGLESTORE_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def txn(tmp_path):
    return TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)


@pytest.mark.parametrize("operation_id", sorted(SINGLESTORE_TRANSACTION_PRIMITIVES_OPERATION_SKIPS))
def test_unsupported_operations_are_skipped_on_singlestore(txn, monkeypatch, operation_id):
    monkeypatch.setattr(txn, "is_setup", lambda connection: True)
    connection = Mock()
    result = txn.execute_operation(operation_id, connection, platform_key="singlestore", platform_name="singlestore")
    assert result.status == "SKIPPED"
    assert result.skip_reason.startswith(f"Operation '{operation_id}' is skipped on singlestore: ")
    connection.execute.assert_not_called()


def test_begin_transaction_is_rewritten_to_begin_on_singlestore(txn):
    sql = txn._rewrite_transactional_sql_for_platform(
        "BEGIN TRANSACTION;\nINSERT INTO t VALUES (1);\nROLLBACK;\n", "singlestore"
    )
    assert sql.startswith("BEGIN;\n")
    assert "TRANSACTION" not in sql


def test_read_committed_isolation_statement_is_dropped_on_singlestore(txn):
    sql = txn._rewrite_transactional_sql_for_platform(
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED;\nBEGIN TRANSACTION;\nCOMMIT;\n", "singlestore"
    )
    assert sql == "BEGIN;\nCOMMIT;\n"


def test_create_temp_table_is_rewritten_to_temporary_on_singlestore(txn):
    sql = txn._rewrite_transactional_sql_for_platform("CREATE TEMP TABLE IF NOT EXISTS t AS SELECT 1;", "singlestore")
    assert sql == "CREATE TEMPORARY TABLE IF NOT EXISTS t AS SELECT 1;"


@pytest.mark.parametrize(("lo", "hi"), [(1, 100), (1, 1000), (1, 20)])
def test_generate_series_is_rewritten_to_a_digit_join_on_singlestore(txn, lo, hi):
    sql = txn._rewrite_transactional_sql_for_platform(
        f"SELECT n FROM (SELECT unnest(generate_series({lo}, {hi})) AS n) t", "singlestore"
    )
    assert "unnest" not in sql
    assert "generate_series" not in sql
    values = [row[0] for row in sqlite3.connect(":memory:").execute(sql).fetchall()]
    assert sorted(values) == list(range(lo, hi + 1))


@pytest.mark.parametrize(
    "operation_id", ["transaction_commit_small", "transaction_commit_medium", "transaction_rollback_small"]
)
def test_no_remaining_begin_transaction_or_series_in_singlestore_operations(txn, operation_id):
    operation = txn.operations_manager.get_operation(operation_id)
    sql = txn._rewrite_transactional_sql_for_platform(operation.write_sql, "singlestore")
    assert "BEGIN TRANSACTION" not in sql
    assert "generate_series" not in sql


def test_ansi_date_literals_are_rewritten_to_quoted_strings_on_singlestore(txn):
    sql = txn._rewrite_transactional_sql_for_platform(
        "INSERT INTO t VALUES (1, DATE '1998-01-01', date '1998-01-15');\nSELECT DATE '1998-01-01' AS o_orderdate;",
        "singlestore",
    )
    assert sql == "INSERT INTO t VALUES (1, '1998-01-01', '1998-01-15');\nSELECT '1998-01-01' AS o_orderdate;"


def test_ansi_date_literals_are_preserved_on_other_platforms(txn):
    sql = txn._rewrite_transactional_sql_for_platform("SELECT DATE '1998-01-01'", "postgresql")
    assert sql == "SELECT DATE '1998-01-01'"


def test_no_ansi_date_literals_remain_in_any_singlestore_operation(txn):
    for operation_id, operation in txn.operations_manager.get_all_operations().items():
        sql = txn._rewrite_transactional_sql_for_platform(operation.write_sql, "singlestore")
        assert "DATE '" not in sql.upper(), operation_id
