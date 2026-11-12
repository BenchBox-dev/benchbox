from __future__ import annotations

import logging

import duckdb
import pytest

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.utils.dialect_utils import QUALIFY_ELIMINATING_DIALECTS, apply_rewrite, translate_sql_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PRESTO_FAMILY = ["presto", "trino", "athena"]
ELIMINATING = sorted(QUALIFY_ELIMINATING_DIALECTS)


def _sort_columns(order: exp.Order) -> list[str]:
    names = [column.name for item in order.expressions for column in item.this.find_all(exp.Column)]
    return [name for index, name in enumerate(names) if index == 0 or name != names[index - 1]]


def _outer_order_names(sql: str, dialect: str) -> list[str]:
    tree = sqlglot.parse_one(sql, read=dialect)
    order = tree.args.get("order")
    assert order is not None, f"outermost SELECT has no ORDER BY: {sql}"
    return _sort_columns(order)


@pytest.mark.parametrize("dialect", ELIMINATING)
def test_tpchavoc_1_v8_keeps_the_sort_keys_on_the_outermost_select(dialect):
    sql = TPCHavocBenchmark(scale_factor=0.01).get_query("1_v8", dialect=dialect)

    assert _outer_order_names(sql, dialect) == ["l_returnflag", "l_linestatus"]
    tree = sqlglot.parse_one(sql, read=dialect)
    derived = tree.args["from_"].this
    assert isinstance(derived, exp.Subquery)
    assert derived.alias == "_t"
    assert tree.args.get("where") is not None


@pytest.mark.parametrize("dialect", ELIMINATING)
def test_qualify_with_order_by_is_ordered_outermost(dialect):
    sql = translate_sql_query(
        "SELECT a AS x, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 "
        "ORDER BY x DESC NULLS LAST, b",
        dialect,
        source_dialect="duckdb",
    )

    tree = sqlglot.parse_one(sql, read=dialect)
    order = tree.args["order"]
    assert _sort_columns(order) == ["x", "b"]
    assert any(item.args.get("desc") is True for item in order.expressions)


@pytest.mark.parametrize("dialect", ELIMINATING)
def test_order_by_on_an_aliased_source_column_resolves_to_the_projected_alias(dialect):
    sql = translate_sql_query(
        "SELECT t.a AS x, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY t.a",
        dialect,
        source_dialect="duckdb",
    )

    assert _outer_order_names(sql, dialect) == ["x"]


@pytest.mark.parametrize("dialect", ELIMINATING)
def test_positional_order_by_is_hoisted(dialect):
    sql = translate_sql_query(
        "SELECT a, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY 2, 1",
        dialect,
        source_dialect="duckdb",
    )

    tree = sqlglot.parse_one(sql, read=dialect)
    keys = [item.this.sql() for item in tree.args["order"].expressions if isinstance(item.this, exp.Literal)]
    assert keys == ["2", "1"]


def test_hoisted_order_defines_the_result_order_when_executed():
    source = (
        "SELECT g, SUM(v) OVER (PARTITION BY g) AS total, COUNT(*) OVER (PARTITION BY g) AS n FROM t "
        "QUALIFY COUNT(*) OVER (PARTITION BY g) > 1 ORDER BY g DESC"
    )
    rewritten = apply_rewrite("hoist_qualify_outer_clauses", sqlglot.parse_one(source, read="duckdb"), "trino")
    rendered = rewritten.sql(dialect="duckdb")
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (g INTEGER, v INTEGER)")
    connection.execute("INSERT INTO t VALUES (1, 10), (1, 20), (3, 5), (3, 6), (2, 7), (2, 8), (4, 1)")

    outer = sqlglot.parse_one(rendered, read="duckdb")

    assert outer.args["order"] is not None
    assert "QUALIFY" not in rendered.upper()
    assert [row[0] for row in connection.execute(rendered).fetchall()] == [3, 3, 2, 2, 1, 1]


def test_qualify_without_order_by_is_left_for_the_generator():
    sql = "SELECT a, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1"
    tree = sqlglot.parse_one(sql, read="duckdb")

    rewritten = apply_rewrite("hoist_qualify_outer_clauses", tree, "trino")

    assert rewritten.sql(dialect="duckdb") == tree.sql(dialect="duckdb")
    assert rewritten.args.get("qualify") is not None


def test_unprojected_sort_key_is_reported_rather_than_silently_dropped(caplog):
    sql = "SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY b"

    with caplog.at_level(logging.WARNING, logger="benchbox.utils.dialect_utils"):
        rendered = translate_sql_query(sql, "trino", source_dialect="duckdb")

    assert "cannot hoist" in caplog.text
    assert sqlglot.parse_one(rendered, read="trino").args.get("order") is None


def test_nested_qualify_selects_each_get_their_own_outer_order():
    sql = (
        "SELECT * FROM (SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY a) q "
        "ORDER BY a"
    )

    rendered = translate_sql_query(sql, "trino", source_dialect="duckdb")

    tree = sqlglot.parse_one(rendered, read="trino")
    assert tree.args["order"] is not None
    inner = next(select for select in tree.find_all(exp.Select) if select.args.get("where") is not None)
    assert [item.this.name for item in inner.args["order"].expressions] == ["a"]


@pytest.mark.parametrize("dialect", ["duckdb", "snowflake", "bigquery", "databricks", "clickhouse", "redshift"])
def test_dialects_with_native_qualify_are_not_rewritten(dialect):
    sql = "SELECT DISTINCT a, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY a LIMIT 3"

    rendered = translate_sql_query(sql, dialect, source_dialect="duckdb")

    assert "QUALIFY" in rendered.upper()


def test_eliminating_dialect_set_matches_the_vendored_generators():
    from benchbox._vendor.sqlglot.dialects.dialect import Dialect

    sql = "SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY a) = 1"
    eliminating = set()
    for name in Dialect.classes:
        if not name or name == "starrocks":
            continue
        try:
            rendered = sqlglot.parse_one(sql, read="duckdb").sql(name)
        except Exception:
            continue
        if "QUALIFY" not in rendered.upper():
            eliminating.add(name)

    assert eliminating == QUALIFY_ELIMINATING_DIALECTS - {"starrocks"}


CLAUSES_SQL = (
    "SELECT DISTINCT g AS grp, v FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY g ORDER BY v) <= 2 "
    "ORDER BY grp DESC, v LIMIT 3 OFFSET 1"
)


@pytest.mark.parametrize("dialect", ELIMINATING)
def test_order_distinct_limit_and_offset_land_on_the_outer_select_after_the_filter(dialect):
    rendered = translate_sql_query(CLAUSES_SQL, dialect, source_dialect="duckdb")

    tree = sqlglot.parse_one(rendered, read=dialect)
    assert tree.args.get("qualify") is None
    assert tree.args["where"] is not None
    assert tree.args.get("distinct") is not None
    assert tree.args.get("limit") is not None
    assert tree.args.get("order") is not None
    inner = tree.args["from_"].this.this
    assert isinstance(inner, exp.Select)
    for clause in ("distinct", "limit", "offset", "order"):
        assert inner.args.get(clause) is None, f"{dialect}: {clause} stayed inside the derived table"


def test_hoisted_distinct_order_limit_and_offset_match_native_qualify_in_duckdb():
    source = (
        "SELECT DISTINCT g AS grp, v % 2 AS parity FROM t "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY g ORDER BY v) <= 2 ORDER BY grp DESC, parity LIMIT 3 OFFSET 1"
    )
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (g INTEGER, v INTEGER)")
    connection.execute(
        "INSERT INTO t VALUES (1, 10), (1, 12), (1, 14), (2, 5), (2, 7), (2, 9), (3, 2), (3, 4), (4, 1), (4, 3), (5, 8)"
    )
    expected = connection.execute(source).fetchall()

    rewritten = apply_rewrite("hoist_qualify_outer_clauses", sqlglot.parse_one(source, read="duckdb"), "trino")
    rendered = rewritten.sql(dialect="duckdb")

    assert "QUALIFY" not in rendered.upper()
    assert connection.execute(rendered).fetchall() == expected
    assert len(expected) == 3


def test_limit_applies_after_the_qualify_filter_not_before():
    source = "SELECT g FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY g ORDER BY v) = 1 ORDER BY g LIMIT 2"
    connection = duckdb.connect()
    connection.execute("CREATE TABLE t (g INTEGER, v INTEGER)")
    connection.execute("INSERT INTO t VALUES (1, 1), (1, 2), (1, 3), (2, 1), (3, 1)")

    rewritten = apply_rewrite("hoist_qualify_outer_clauses", sqlglot.parse_one(source, read="duckdb"), "postgres")

    assert connection.execute(rewritten.sql(dialect="duckdb")).fetchall() == [(1,), (2,)]


def test_distinct_on_stays_with_its_own_elimination():
    tree = sqlglot.parse_one(
        "SELECT DISTINCT ON (a) a, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY a",
        read="duckdb",
    )

    rewritten = apply_rewrite("hoist_qualify_outer_clauses", tree, "postgres")

    inner = rewritten.args["from_"].this.this
    assert inner.args["distinct"].args["on"] is not None
    assert rewritten.args["order"] is not None


def test_limit_is_hoisted_even_when_the_sort_key_cannot_be(caplog):
    sql = "SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY b LIMIT 4"

    with caplog.at_level(logging.WARNING, logger="benchbox.utils.dialect_utils"):
        rendered = translate_sql_query(sql, "postgres", source_dialect="duckdb")

    tree = sqlglot.parse_one(rendered, read="postgres")
    assert "cannot hoist" in caplog.text
    assert tree.args["limit"] is not None
    assert tree.args["from_"].this.this.args.get("limit") is None


def test_qualified_order_by_resolves_by_source_not_by_bare_name():
    sql = translate_sql_query(
        "SELECT l.a AS x, r.a FROM l JOIN r ON l.k = r.k "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY l.k ORDER BY r.a) = 1 ORDER BY l.a",
        "trino",
        source_dialect="duckdb",
    )

    assert _outer_order_names(sql, "trino") == ["x"]


def test_qualified_order_by_does_not_bind_to_an_alias_holding_another_column():
    sql = "SELECT b AS a, c FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY c ORDER BY b) = 1 ORDER BY t.a"

    rendered = translate_sql_query(sql, "trino", source_dialect="duckdb")

    assert sqlglot.parse_one(rendered, read="trino").args.get("order") is None


@pytest.mark.parametrize("key", ["COUNT(*) OVER (PARTITION BY a)", "SUM(a)"])
def test_aggregate_and_window_sort_keys_are_not_hoisted(key, caplog):
    sql = f"SELECT a, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY {key}"

    with caplog.at_level(logging.WARNING, logger="benchbox.utils.dialect_utils"):
        rendered = translate_sql_query(sql, "trino", source_dialect="duckdb")

    assert "cannot hoist" in caplog.text
    assert sqlglot.parse_one(rendered, read="trino").args.get("order") is None


def test_columns_inside_an_order_by_expression_bind_to_input_columns_not_aliases():
    sql = "SELECT a AS b, b AS c FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY b + 0"

    rendered = translate_sql_query(sql, "trino", source_dialect="duckdb")

    order = sqlglot.parse_one(rendered, read="trino").args["order"]
    assert order.expressions[0].this.sql() == '"c" + 0'
