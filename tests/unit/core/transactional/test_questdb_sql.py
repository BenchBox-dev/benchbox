# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.core.transactional.questdb_sql import questdb_cleanup_statements, rewrite_questdb_operation_sql
from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_current_timestamp_becomes_now():
    sql = "INSERT INTO t (a, ts) VALUES (1, CURRENT_TIMESTAMP)"
    assert rewrite_questdb_operation_sql(sql) == "INSERT INTO t (a, ts) VALUES (1, now())"


def test_statements_without_rewrites_are_returned_verbatim():
    sql = "BEGIN TRANSACTION;\nINSERT INTO t VALUES (1);\nCOMMIT;"
    assert rewrite_questdb_operation_sql(sql) == sql


def test_each_statement_of_a_script_is_rewritten():
    sql = "BEGIN TRANSACTION;\nUPDATE t SET a = 1 WHERE b BETWEEN (SELECT MIN(b) FROM t) AND 5;\nCOMMIT;"
    rewritten = rewrite_questdb_operation_sql(sql)
    assert "BETWEEN" not in rewritten.upper()
    assert "b >= (SELECT MIN(b) FROM t) AND b <= 5" in rewritten
    assert rewritten.startswith("BEGIN TRANSACTION;")
    assert rewritten.rstrip().endswith("COMMIT;")


def test_create_table_as_select_is_parenthesised():
    assert rewrite_questdb_operation_sql("CREATE TABLE a AS SELECT * FROM b") == "CREATE TABLE a AS (SELECT * FROM b)"


def test_delete_with_predicate_is_emulated_by_rebuilding_the_table():
    assert questdb_cleanup_statements("DELETE FROM txn_orders WHERE o_comment = 'x'\n") == [
        'DROP TABLE IF EXISTS "txn_orders__keep"',
        'CREATE TABLE "txn_orders__keep" AS (SELECT * FROM "txn_orders" WHERE NOT (o_comment = \'x\'))',
        'DROP TABLE "txn_orders"',
        'RENAME TABLE "txn_orders__keep" TO "txn_orders"',
    ]


def test_delete_without_predicate_becomes_truncate():
    assert questdb_cleanup_statements("DELETE FROM insert_ops_orders_summary") == [
        'TRUNCATE TABLE "insert_ops_orders_summary"'
    ]


def test_other_cleanup_statements_pass_through():
    sql = "UPDATE update_ops_orders SET o_comment = '' WHERE o_comment = 'x'"
    assert questdb_cleanup_statements(sql) == [sql]


@pytest.mark.parametrize("benchmark_class", [WritePrimitivesBenchmark, TransactionPrimitivesBenchmark])
def test_questdb_cleanup_runs_the_emulated_statements(benchmark_class, tmp_path):
    benchmark = benchmark_class(scale_factor=0.01, output_dir=tmp_path)
    connection = Mock()
    connection.execute.return_value = SimpleNamespace(platform_result=None)

    benchmark._execute_cleanup_sql(connection, "DELETE FROM t WHERE a = 1", "questdb")

    executed = [call.args[0] for call in connection.execute.call_args_list]
    assert executed[0] == 'DROP TABLE IF EXISTS "t__keep"'
    assert executed[-1] == 'RENAME TABLE "t__keep" TO "t"'
    assert len(executed) == 4


@pytest.mark.parametrize("benchmark_class", [WritePrimitivesBenchmark, TransactionPrimitivesBenchmark])
def test_other_platforms_keep_the_cleanup_statement(benchmark_class, tmp_path):
    benchmark = benchmark_class(scale_factor=0.01, output_dir=tmp_path)
    connection = Mock()
    connection.execute.return_value = SimpleNamespace(platform_result=None)

    benchmark._execute_cleanup_sql(connection, "DELETE FROM t WHERE a = 1", "duckdb")

    connection.execute.assert_called_once_with("DELETE FROM t WHERE a = 1")


def test_questdb_series_select_uses_generate_series_table_function(tmp_path):
    benchmark = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    sql = "INSERT INTO t SELECT n FROM (SELECT unnest(generate_series(1, 100)) AS n) t"
    assert benchmark._rewrite_transactional_sql_for_platform(sql, "questdb") == (
        "INSERT INTO t SELECT n FROM (SELECT generate_series AS n FROM generate_series(1, 100)) t"
    )
