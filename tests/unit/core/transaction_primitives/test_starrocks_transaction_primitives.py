from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.sql_compat.rules.execution_filter.starrocks_transaction_primitives import (
    STARROCKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def txn(tmp_path):
    return TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)


@pytest.mark.parametrize("operation_id", sorted(STARROCKS_TRANSACTION_PRIMITIVES_OPERATION_SKIPS))
def test_unsupported_operations_are_skipped_on_starrocks(txn, monkeypatch, operation_id):
    monkeypatch.setattr(txn, "is_setup", lambda connection: True)
    connection = Mock()
    result = txn.execute_operation(operation_id, connection, platform_key="starrocks", platform_name="starrocks")
    assert result.status == "SKIPPED"
    assert result.skip_reason.startswith(f"Operation '{operation_id}' is skipped on starrocks: StarRocks 3.5")
    connection.execute.assert_not_called()


def test_begin_transaction_is_rewritten_to_begin_on_starrocks(txn):
    sql = txn._rewrite_transactional_sql_for_platform(
        "BEGIN TRANSACTION;\nINSERT INTO t VALUES (1);\nCOMMIT;\n", "starrocks"
    )
    assert sql.startswith("BEGIN;\n")
    assert "TRANSACTION" not in sql


def test_consecutive_inserts_into_one_table_become_a_single_statement_on_starrocks(txn):
    sql = txn._rewrite_transactional_sql_for_platform(
        "BEGIN TRANSACTION;\nINSERT INTO a VALUES (1, 'x');\nINSERT INTO a VALUES (2, 'y');\n"
        "INSERT INTO b VALUES (3, 'z');\nCOMMIT;\n",
        "starrocks",
    )
    assert sql.count("INSERT INTO a") == 1
    assert "VALUES (1, 'x'), (2, 'y')" in sql
    assert "INSERT INTO b VALUES (3, 'z')" in sql


def test_generate_series_is_rewritten_to_the_table_function_on_starrocks(txn):
    sql = txn._rewrite_transactional_sql_for_platform(
        "INSERT INTO t SELECT 9200000 + n FROM (SELECT unnest(generate_series(1, 100)) AS n) t;", "starrocks"
    )
    assert "TABLE(generate_series(1, 100)) AS series(n)" in sql
    assert "unnest" not in sql


def test_write_sql_without_series_or_transaction_is_left_untouched_on_starrocks(txn):
    sql = "UPDATE t SET a = 1 WHERE b = 2"
    assert txn._rewrite_transactional_sql_for_platform(sql, "starrocks") == sql
