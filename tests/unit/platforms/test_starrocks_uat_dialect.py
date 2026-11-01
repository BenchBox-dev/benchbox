from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from benchbox.core.transactional.benchmark_base import _starrocks_cleanup_statement
from benchbox.core.transactional.staging_keys import leading_key_columns
from benchbox.platforms.starrocks.setup import _StarRocksConnectionWrapper
from benchbox.utils.dialect_utils import translate_sql_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_drop_table_skips_the_recycle_bin():
    wrapper = _StarRocksConnectionWrapper(MagicMock())
    assert wrapper._optimize_if_create("DROP TABLE IF EXISTS t") == "DROP TABLE IF EXISTS t FORCE"
    assert wrapper._optimize_if_create("DROP TABLE t;") == "DROP TABLE t FORCE"
    assert wrapper._optimize_if_create("DROP TABLE t FORCE") == "DROP TABLE t FORCE"


def test_index_statements_wait_for_the_running_schema_change():
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchall.side_effect = [
        [("1", "t", "", "", "", "", "", "", "", "RUNNING")],
        [("1", "t", "", "", "", "", "", "", "", "FINISHED")],
    ] * 3
    wrapper = _StarRocksConnectionWrapper(conn)
    wrapper.execute("CREATE INDEX i ON t(v)")
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert statements.count("SHOW ALTER TABLE COLUMN") >= 2
    assert statements.index("CREATE INDEX i ON t(v)") < len(statements) - 1


def test_staging_tables_use_leading_key_columns():
    assert leading_key_columns("update_ops_orders", ["o_orderkey", "o_custkey"]) == ["o_orderkey"]
    assert leading_key_columns("insert_ops_lineitem", ["l_orderkey", "l_partkey", "l_suppkey", "l_linenumber"]) == [
        "l_orderkey",
        "l_partkey",
        "l_suppkey",
        "l_linenumber",
    ]
    assert leading_key_columns("orders", ["o_orderkey"]) == []


def test_cleanup_deletes_avoid_between_and_unconditional_delete():
    assert (
        _starrocks_cleanup_statement("DELETE FROM t WHERE k BETWEEN 1 AND 100")
        == "DELETE FROM t WHERE k >= 1 AND k <= 100"
    )
    assert _starrocks_cleanup_statement("DELETE FROM t") == "DELETE FROM t WHERE 1 = 1"
    assert _starrocks_cleanup_statement("UPDATE t SET a = 1") == "UPDATE t SET a = 1"


def test_qualify_becomes_a_filtered_subquery():
    sql = translate_sql_query(
        "SELECT a, ROW_NUMBER() OVER (PARTITION BY b ORDER BY c) AS rn FROM t QUALIFY rn <= 3",
        "starrocks",
        source_dialect="duckdb",
    )
    assert "QUALIFY" not in sql
    assert "ROW_NUMBER()" in sql
