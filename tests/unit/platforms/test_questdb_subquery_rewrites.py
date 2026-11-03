from __future__ import annotations

import re

import duckdb
import pytest

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox.core.tpch.schema import TABLES
from benchbox.utils.dialect_utils import translate_sql_query
from benchbox.utils.questdb_subqueries import rewrite_subqueries

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _has_exists(sql: str) -> bool:
    return re.search(r"\bEXISTS\s*\(", sql, re.IGNORECASE) is not None


def _questdb(sql: str) -> str:
    return translate_sql_query(sql, "questdb", source_dialect="netezza", identify=False, strict=True)


def _duckdb_rows(sql: str, rewrite: bool) -> list[tuple]:
    connection = duckdb.connect()
    for table in TABLES:
        columns = ", ".join(f"{column.name} {column.get_sql_type()}" for column in table.columns)
        connection.execute(f"CREATE TABLE {table.name} ({columns})")
    connection.execute(
        "INSERT INTO lineitem (l_orderkey, l_linenumber, l_suppkey, l_receiptdate, l_commitdate) VALUES "
        "(1, 1, 10, DATE '1995-01-02', DATE '1995-01-01'),"
        "(1, 2, 10, DATE '1995-01-02', DATE '1995-01-03'),"
        "(2, 1, 10, DATE '1995-01-02', DATE '1995-01-01'),"
        "(2, 2, 11, DATE '1995-01-02', DATE '1995-01-01'),"
        "(3, 1, 10, DATE '1995-01-02', DATE '1995-01-01'),"
        "(3, 2, 11, DATE '1995-01-02', DATE '1995-01-05'),"
        "(4, 1, NULL, DATE '1995-01-02', DATE '1995-01-01'),"
        "(4, 2, 12, DATE '1995-01-02', DATE '1995-01-01')"
    )
    connection.execute("UPDATE lineitem SET l_partkey = l_orderkey, l_quantity = l_linenumber, l_discount = 0.0")
    connection.execute("UPDATE lineitem SET l_extendedprice = 10 * l_orderkey + l_linenumber")
    connection.execute("INSERT INTO supplier (s_suppkey, s_name) VALUES (10, 'a'), (11, 'b'), (12, 'c')")
    connection.execute(
        "INSERT INTO orders (o_orderkey, o_orderstatus, o_totalprice) VALUES "
        "(1, 'F', 100.0), (2, 'O', 200.0), (3, 'F', 300.0), (4, NULL, 400.0), (5, 'F', 500.0), (6, NULL, 600.0)"
    )
    connection.execute("INSERT INTO part (p_partkey, p_name) VALUES (1, 'a'), (2, 'b'), (3, 'c'), (4, 'd')")
    connection.execute(
        "INSERT INTO partsupp (ps_partkey, ps_suppkey, ps_supplycost) VALUES "
        "(1, 10, 5.0), (1, 11, 3.0), (2, 10, 7.0), (3, 12, 2.0), (4, 11, 9.0), (4, 10, 9.0)"
    )
    tree = sqlglot.parse_one(sql, read="netezza")
    if rewrite:
        tree = rewrite_subqueries(tree)
    return sorted(connection.execute(tree.sql(dialect="duckdb")).fetchall(), key=repr)


NOT_EXISTS_OTHER_SUPPLIER = """
SELECT l1.l_orderkey, l1.l_linenumber FROM lineitem l1
WHERE l1.l_receiptdate > l1.l_commitdate
AND NOT EXISTS (SELECT * FROM lineitem l3 WHERE l3.l_orderkey = l1.l_orderkey
                AND l3.l_suppkey <> l1.l_suppkey AND l3.l_receiptdate > l3.l_commitdate)
"""

EXISTS_OTHER_SUPPLIER = """
SELECT l1.l_orderkey, l1.l_linenumber FROM lineitem l1
WHERE EXISTS (SELECT * FROM lineitem l2 WHERE l2.l_orderkey = l1.l_orderkey AND l2.l_suppkey <> l1.l_suppkey)
"""


@pytest.mark.parametrize("sql", [NOT_EXISTS_OTHER_SUPPLIER, EXISTS_OTHER_SUPPLIER])
def test_inequality_exists_lowering_preserves_results(sql):
    assert _duckdb_rows(sql, rewrite=True) == _duckdb_rows(sql, rewrite=False)
    assert _duckdb_rows(sql, rewrite=False)


@pytest.mark.parametrize("sql", [NOT_EXISTS_OTHER_SUPPLIER, EXISTS_OTHER_SUPPLIER])
def test_inequality_exists_leaves_no_subquery_predicate(sql):
    rewritten = _questdb(sql)
    assert not _has_exists(rewritten)
    assert "_qx_ne_1" in rewritten


def test_unqualified_correlation_is_resolved_from_the_schema():
    rewritten = _questdb(
        "SELECT o_orderpriority FROM orders WHERE EXISTS "
        "(SELECT * FROM lineitem WHERE l_orderkey = o_orderkey AND l_commitdate < l_receiptdate)"
    )
    assert not _has_exists(rewritten)
    assert "lineitem.l_orderkey" in rewritten


def test_scalar_correlated_subquery_becomes_a_join():
    rewritten = _questdb(
        "SELECT SUM(l_extendedprice) FROM lineitem, part WHERE p_partkey = l_partkey "
        "AND l_quantity < (SELECT 0.2 * AVG(l_quantity) FROM lineitem WHERE l_partkey = p_partkey)"
    )
    assert "(SELECT 0.2 * AVG" in rewritten
    assert "WHERE p_partkey" not in rewritten
    assert "CROSS JOIN" not in rewritten
    assert "JOIN part AS part ON" in rewritten


def test_not_in_over_non_null_columns_becomes_an_anti_join():
    rewritten = _questdb(
        "SELECT ps_suppkey FROM partsupp WHERE ps_suppkey NOT IN (SELECT s_suppkey FROM supplier WHERE s_acctbal > 0)"
    )
    assert " IN (SELECT" not in rewritten
    assert "LEFT JOIN" in rewritten


def test_comma_joins_with_subqueries_keep_their_join_conditions():
    rewritten = _questdb(
        "SELECT p_partkey FROM part, partsupp, supplier WHERE p_partkey = ps_partkey AND s_suppkey = ps_suppkey "
        "AND ps_supplycost = (SELECT MIN(ps_supplycost) FROM partsupp WHERE p_partkey = ps_partkey)"
    )
    assert "CROSS JOIN" not in rewritten
    assert "JOIN partsupp AS partsupp ON part.p_partkey = partsupp.ps_partkey" in rewritten
    assert "JOIN supplier AS supplier ON supplier.s_suppkey = partsupp.ps_suppkey" in rewritten


def test_extremum_of_a_cte_is_taken_over_one_evaluation():
    rewritten = _questdb(
        "WITH revenue AS (SELECT l_suppkey AS supplier_no, SUM(l_extendedprice) AS total_revenue "
        "FROM lineitem GROUP BY l_suppkey) "
        "SELECT s_suppkey, total_revenue FROM supplier, revenue "
        "WHERE s_suppkey = supplier_no AND total_revenue = (SELECT MAX(total_revenue) FROM revenue)"
    )
    assert "MAX(revenue.total_revenue) OVER ()" in rewritten
    assert "(SELECT MAX(revenue.total_revenue) AS" not in rewritten
    assert rewritten.count("FROM revenue") == 1


def test_query_without_subquery_predicates_or_comma_joins_is_untouched():
    sql = "SELECT l_orderkey FROM lineitem JOIN orders ON l_orderkey = o_orderkey"
    tree = sqlglot.parse_one(sql, read="netezza")
    assert rewrite_subqueries(tree) is tree


def test_query_over_tables_of_no_known_benchmark_is_untouched():
    tree = sqlglot.parse_one("SELECT a FROM t1 WHERE EXISTS (SELECT 1 FROM t2 WHERE t2.b = t1.a)", read="netezza")
    assert rewrite_subqueries(tree) is tree


def test_inequality_exists_over_a_join_is_lowered():
    rewritten = _questdb(
        "SELECT l1.l_orderkey FROM lineitem l1 WHERE NOT EXISTS (SELECT 1 FROM lineitem l3 "
        "JOIN orders o3 ON l3.l_orderkey = o3.o_orderkey WHERE l3.l_orderkey = l1.l_orderkey "
        "AND l3.l_suppkey <> l1.l_suppkey AND o3.o_orderstatus = 'F')"
    )
    assert not _has_exists(rewritten)
    assert "JOIN orders AS o3 ON" in rewritten


def test_count_greater_than_zero_is_an_exists():
    rewritten = _questdb(
        "SELECT o_orderkey FROM orders WHERE (SELECT COUNT(*) FROM lineitem WHERE l_orderkey = o_orderkey "
        "AND (l_quantity = 1 OR l_quantity = 2)) > 0"
    )
    assert "COUNT" not in rewritten.upper()
    assert "SELECT" not in rewritten.split("JOIN", 1)[0].split("FROM", 1)[1]


def test_count_equals_zero_is_a_not_exists():
    sql = "SELECT o_orderkey FROM orders WHERE (SELECT COUNT(*) FROM lineitem WHERE l_orderkey = o_orderkey) = 0"
    rewritten = _questdb(sql)
    assert "COUNT" not in rewritten.upper()
    assert "LEFT JOIN" in rewritten


def test_correlation_shared_by_every_or_branch_is_factored_out():
    rewritten = _questdb(
        "SELECT o_orderkey FROM orders WHERE EXISTS (SELECT 1 FROM lineitem WHERE "
        "(l_orderkey = o_orderkey AND l_quantity = 1) OR (l_orderkey = o_orderkey AND l_quantity = 2))"
    )
    assert not _has_exists(rewritten)
    assert "orders.o_orderkey = _qx_exists_1._qx_exists_1_k0" in rewritten


def test_comma_joins_without_subqueries_are_qualified_from_the_schema():
    rewritten = _questdb("SELECT l_orderkey FROM lineitem, orders WHERE l_orderkey = o_orderkey")
    assert "JOIN orders AS orders ON lineitem.l_orderkey = orders.o_orderkey" in rewritten


ORDERING_QUERIES = [
    "SELECT l1.l_orderkey, l1.l_linenumber FROM lineitem l1 WHERE NOT EXISTS "
    "(SELECT 1 FROM lineitem l2 WHERE l2.l_orderkey = l1.l_orderkey AND l2.l_suppkey < l1.l_suppkey)",
    "SELECT l1.l_orderkey, l1.l_linenumber FROM lineitem l1 WHERE EXISTS "
    "(SELECT 1 FROM lineitem l2 WHERE l2.l_orderkey = l1.l_orderkey AND l2.l_suppkey <= l1.l_suppkey)",
    "SELECT l1.l_orderkey, l1.l_linenumber FROM lineitem l1 WHERE EXISTS "
    "(SELECT 1 FROM lineitem l2 WHERE l1.l_orderkey = l2.l_orderkey AND l1.l_suppkey < l2.l_suppkey)",
    "SELECT l1.l_orderkey, l1.l_linenumber FROM lineitem l1 WHERE NOT EXISTS "
    "(SELECT 1 FROM lineitem l2 WHERE l2.l_orderkey = l1.l_orderkey AND l2.l_suppkey >= l1.l_suppkey)",
    "SELECT l1.l_orderkey, l1.l_linenumber FROM lineitem l1 WHERE NOT EXISTS "
    "(SELECT 1 FROM lineitem l2 WHERE l2.l_orderkey = l1.l_orderkey AND l2.l_suppkey > l1.l_suppkey)",
]


@pytest.mark.parametrize("sql", ORDERING_QUERIES)
def test_ordering_exists_lowering_preserves_results(sql):
    assert _duckdb_rows(sql, rewrite=True) == _duckdb_rows(sql, rewrite=False)


@pytest.mark.parametrize("sql", ORDERING_QUERIES)
def test_ordering_exists_leaves_no_subquery_predicate(sql):
    assert not _has_exists(_questdb(sql))


NOT_IN_QUERIES = [
    "SELECT l_orderkey, l_linenumber FROM lineitem WHERE l_suppkey NOT IN (SELECT l_suppkey FROM lineitem WHERE l_orderkey = 2)",
    "SELECT l_orderkey, l_linenumber FROM lineitem WHERE l_suppkey NOT IN (SELECT l_suppkey FROM lineitem WHERE l_orderkey = 4)",
    "SELECT l_orderkey, l_linenumber FROM lineitem WHERE l_suppkey NOT IN (SELECT l_suppkey FROM lineitem WHERE l_orderkey = 99)",
    "SELECT l_orderkey, l_linenumber FROM lineitem WHERE l_suppkey NOT IN (SELECT s FROM (SELECT l_suppkey AS s FROM lineitem WHERE l_orderkey = 3) AS d)",
]


@pytest.mark.parametrize("sql", NOT_IN_QUERIES)
def test_not_in_over_nullable_columns_keeps_three_valued_results(sql):
    assert _duckdb_rows(sql, rewrite=True) == _duckdb_rows(sql, rewrite=False)


@pytest.mark.parametrize("sql", NOT_IN_QUERIES)
def test_not_in_subquery_is_replaced_by_joins(sql):
    rewritten = _questdb(sql)
    assert " IN (SELECT" not in rewritten
    assert "_qx_ni_1" in rewritten


def test_uncorrelated_scalar_subqueries_without_from_get_a_single_row_source():
    rewritten = _questdb(
        "SELECT 100.00 * COALESCE((SELECT SUM(l_quantity) FROM lineitem WHERE l_orderkey = 1), 0) "
        "/ (SELECT SUM(l_quantity) FROM lineitem) AS ratio"
    )
    assert "_qx_dual" in rewritten
    assert rewritten.count("CROSS JOIN") == 2
    assert "(SELECT" not in rewritten.split("FROM (SELECT 1 AS _qx_one)")[0]


def test_correlation_keys_of_hoisted_subqueries_reject_nulls():
    rewritten = _questdb(
        "SELECT o_orderkey FROM orders o1 WHERE o_totalprice > "
        "(SELECT AVG(o_totalprice) FROM orders o2 WHERE o2.o_orderstatus = o1.o_orderstatus)"
    )
    assert "NOT o2.o_orderstatus IS NULL GROUP BY o2.o_orderstatus" in rewritten


def test_cross_joins_are_ordered_so_each_one_has_a_join_condition():
    rewritten = _questdb(
        "SELECT p_partkey FROM part, supplier, partsupp WHERE p_partkey = ps_partkey AND s_suppkey = ps_suppkey "
        "AND ps_supplycost = (SELECT MIN(ps_supplycost) FROM partsupp WHERE p_partkey = ps_partkey)"
    )
    assert "CROSS JOIN" not in rewritten
    assert rewritten.index("JOIN partsupp") < rewritten.index("JOIN supplier")


CTE_EXTREMUM = (
    "WITH revenue AS (SELECT l_suppkey AS supplier_no, SUM(l_extendedprice * (1 - l_discount)) AS total_revenue "
    "FROM lineitem WHERE l_suppkey IS NOT NULL GROUP BY l_suppkey) "
    "SELECT s_suppkey, s_name, total_revenue FROM supplier, revenue "
    "WHERE s_suppkey = supplier_no AND total_revenue = (SELECT MAX(total_revenue) FROM revenue)"
)
CTE_MINIMUM = CTE_EXTREMUM.replace("MAX(", "MIN(")

COUNT_TESTS = [
    "SELECT o_orderkey FROM orders WHERE (SELECT COUNT(*) FROM lineitem WHERE l_orderkey = o_orderkey) > 0",
    "SELECT o_orderkey FROM orders WHERE (SELECT COUNT(*) FROM lineitem WHERE l_orderkey = o_orderkey) = 0",
    "SELECT o_orderkey FROM orders WHERE (SELECT COUNT(*) FROM lineitem WHERE l_orderkey = o_orderkey "
    "AND l_quantity > 1) > 0",
    "SELECT o_orderkey FROM orders WHERE 0 < (SELECT COUNT(*) FROM lineitem WHERE l_orderkey = o_orderkey)",
    "SELECT o_orderkey FROM orders WHERE (SELECT COUNT(*) FROM lineitem WHERE l_orderkey = o_orderkey) >= 1",
    "SELECT o_orderkey FROM orders WHERE (SELECT COUNT(*) FROM lineitem WHERE l_orderkey = o_orderkey) < 1",
]

OR_FACTORED = [
    "SELECT o_orderkey FROM orders WHERE EXISTS (SELECT 1 FROM lineitem WHERE "
    "(l_orderkey = o_orderkey AND l_quantity = 1) OR (l_orderkey = o_orderkey AND l_quantity = 2))",
    "SELECT o_orderkey FROM orders WHERE NOT EXISTS (SELECT 1 FROM lineitem WHERE "
    "(l_orderkey = o_orderkey AND l_quantity = 1) OR (l_orderkey = o_orderkey AND l_suppkey = 12))",
    "SELECT o_orderkey FROM orders WHERE (SELECT COUNT(*) FROM lineitem WHERE "
    "(l_orderkey = o_orderkey AND l_quantity = 2) OR (l_orderkey = o_orderkey AND l_suppkey = 12)) > 0",
]

COMMA_JOIN_SUBQUERIES = [
    "SELECT p_partkey, s_suppkey, ps_supplycost FROM part, supplier, partsupp "
    "WHERE p_partkey = ps_partkey AND s_suppkey = ps_suppkey "
    "AND ps_supplycost = (SELECT MIN(ps_supplycost) FROM partsupp WHERE p_partkey = ps_partkey)",
    "SELECT p_partkey, s_suppkey FROM part, supplier, partsupp "
    "WHERE s_suppkey = ps_suppkey AND p_partkey = ps_partkey AND p_partkey > 1 "
    "AND ps_supplycost >= (SELECT AVG(ps_supplycost) FROM partsupp WHERE p_partkey = ps_partkey)",
]

NULL_KEY_SCALAR = (
    "SELECT o1.o_orderkey FROM orders o1 WHERE o1.o_totalprice > "
    "(SELECT AVG(o2.o_totalprice) FROM orders o2 WHERE o2.o_orderstatus = o1.o_orderstatus)"
)


@pytest.mark.parametrize("sql", [CTE_EXTREMUM, CTE_MINIMUM])
def test_cte_extremum_rewrite_preserves_results(sql):
    assert _duckdb_rows(sql, rewrite=True) == _duckdb_rows(sql, rewrite=False)
    assert _duckdb_rows(sql, rewrite=False)


@pytest.mark.parametrize("sql", COUNT_TESTS)
def test_count_test_rewrite_preserves_results(sql):
    assert _duckdb_rows(sql, rewrite=True) == _duckdb_rows(sql, rewrite=False)
    assert _duckdb_rows(sql, rewrite=False)


@pytest.mark.parametrize("sql", OR_FACTORED)
def test_or_branch_factoring_preserves_results(sql):
    assert _duckdb_rows(sql, rewrite=True) == _duckdb_rows(sql, rewrite=False)
    assert _duckdb_rows(sql, rewrite=False)


@pytest.mark.parametrize("sql", COMMA_JOIN_SUBQUERIES)
def test_cross_join_reordering_preserves_results(sql):
    assert _duckdb_rows(sql, rewrite=True) == _duckdb_rows(sql, rewrite=False)
    assert _duckdb_rows(sql, rewrite=False)


def test_null_correlation_key_guard_preserves_results():
    assert _duckdb_rows(NULL_KEY_SCALAR, rewrite=True) == _duckdb_rows(NULL_KEY_SCALAR, rewrite=False)
    assert _duckdb_rows(NULL_KEY_SCALAR, rewrite=False)


def test_uncorrelated_scalar_without_from_preserves_results():
    sql = (
        "SELECT 100.00 * COALESCE((SELECT SUM(l_quantity) FROM lineitem WHERE l_orderkey = 1), 0) "
        "/ (SELECT SUM(l_quantity) FROM lineitem) AS ratio"
    )
    assert _duckdb_rows(sql, rewrite=True) == _duckdb_rows(sql, rewrite=False)
    assert _duckdb_rows(sql, rewrite=False)


def test_subquery_rewrite_failure_is_raised_in_strict_mode(monkeypatch):
    import benchbox.utils.questdb_subqueries as subqueries
    from benchbox._vendor.sqlglot.errors import SqlglotError
    from benchbox.utils.dialect_utils import SQLTranslationError

    def fail(expression):
        raise SqlglotError("boom")

    monkeypatch.setattr(subqueries, "rewrite_subqueries", fail)
    with pytest.raises(SQLTranslationError):
        translate_sql_query("SELECT 1 FROM orders", "questdb", source_dialect="netezza", strict=True)


def test_query_with_columns_outside_the_benchmark_schema_is_untouched():
    tree = sqlglot.parse_one(
        "SELECT x FROM orders WHERE EXISTS (SELECT 1 FROM lineitem WHERE l_orderkey = o_orderkey)", read="netezza"
    )
    assert rewrite_subqueries(tree) is tree


def _where_subqueries(sql: str) -> list:
    tree = sqlglot.parse_one(sql, read="questdb")
    found = []
    for where in tree.find_all(exp.Where):
        found.extend(where.find_all(exp.Exists))
        found.extend(predicate for predicate in where.find_all(exp.In) if predicate.args.get("query"))
        found.extend(sub for sub in where.find_all(exp.Subquery) if isinstance(sub.parent, (exp.Binary, exp.Between)))
    return found


@pytest.mark.parametrize("query_id", ["1", "10", "30", "32", "35", "41", "69", "81", "92"])
def test_tpcds_correlated_queries_leave_no_subquery_predicate(query_id):
    from benchbox.core.tpcds import TPCDSBenchmark

    sql = TPCDSBenchmark(scale_factor=0.01).get_queries(dialect="questdb")[query_id]
    assert _where_subqueries(sql) == []


@pytest.mark.parametrize("query_id", ["2", "4", "17", "20", "21"])
def test_datavault_correlated_queries_leave_no_subquery_predicate(query_id):
    from benchbox.core.datavault import DataVaultBenchmark

    sql = DataVaultBenchmark(scale_factor=0.01).get_queries(dialect="questdb")[query_id]
    assert _where_subqueries(sql) == []


@pytest.mark.parametrize("query_id", ["2", "4", "15", "16", "17", "20", "21", "22"])
def test_tpch_correlated_queries_leave_no_subquery_predicate(query_id):
    from benchbox.core.tpch import TPCHBenchmark

    sql = TPCHBenchmark(scale_factor=0.01).get_queries(dialect="questdb")[query_id]
    assert _where_subqueries(sql) == []


def test_schema_registry_is_the_union_of_every_registered_benchmark():
    from benchbox.core.datavault.schema import TABLES as DATAVAULT_TABLES
    from benchbox.core.tpcds.schema.registry import TABLES as TPCDS_TABLES
    from benchbox.utils.questdb_subquery_schemas import registered_schema

    columns, non_null = registered_schema()
    assert {table.name.lower() for table in [*TABLES, *TPCDS_TABLES, *DATAVAULT_TABLES]} <= columns.keys()
    assert {"c_custkey", "c_customer_sk"} <= columns["customer"].keys()
    assert "c_customer_sk" in non_null


def test_column_nullable_in_any_benchmark_is_not_treated_as_non_null():
    from types import SimpleNamespace

    from benchbox.utils import questdb_subquery_schemas as registry

    def table(nullable):
        column = SimpleNamespace(name="shared_probe_col", nullable=nullable, get_sql_type=lambda: "INTEGER")
        return SimpleNamespace(name="probe_table", columns=[column])

    saved = (dict(registry._COLUMNS), set(registry._NULLABLE), set(registry._NOT_NULL))
    try:
        registry.register_benchmark_schema([table(False)])
        registry.register_benchmark_schema([table(True)])
        assert "shared_probe_col" not in registry.registered_schema()[1]
    finally:
        registry._COLUMNS.clear()
        registry._COLUMNS.update(saved[0])
        registry._NULLABLE.clear()
        registry._NULLABLE.update(saved[1])
        registry._NOT_NULL.clear()
        registry._NOT_NULL.update(saved[2])


def test_core_schema_modules_do_not_import_the_vendored_sqlglot():
    import subprocess
    import sys

    code = (
        "import sys, benchbox.core.tpch.schema, benchbox.core.datavault.schema, "
        "benchbox.core.tpcds.schema.registry\n"
        "assert not any(name.startswith('benchbox._vendor.sqlglot') for name in sys.modules)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
