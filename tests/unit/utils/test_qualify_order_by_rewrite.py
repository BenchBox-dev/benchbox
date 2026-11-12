from __future__ import annotations

import logging

import duckdb
import pytest

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.utils.dialect_utils import apply_rewrite, translate_sql_query

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PRESTO_FAMILY = ["presto", "trino", "athena"]


def _outer_order_names(sql: str, dialect: str) -> list[str]:
    tree = sqlglot.parse_one(sql, read=dialect)
    order = tree.args.get("order")
    assert order is not None, f"outermost SELECT has no ORDER BY: {sql}"
    return [item.this.name for item in order.expressions]


@pytest.mark.parametrize("dialect", PRESTO_FAMILY)
def test_tpchavoc_1_v8_keeps_the_sort_keys_on_the_outermost_select(dialect):
    sql = TPCHavocBenchmark(scale_factor=0.01).get_query("1_v8", dialect=dialect)

    assert _outer_order_names(sql, dialect) == ["l_returnflag", "l_linestatus"]
    tree = sqlglot.parse_one(sql, read=dialect)
    derived = tree.args["from_"].this
    assert isinstance(derived, exp.Subquery)
    assert derived.alias == "_t"
    assert tree.args.get("where") is not None


@pytest.mark.parametrize("dialect", PRESTO_FAMILY)
def test_qualify_with_order_by_is_ordered_outermost_for_the_presto_family(dialect):
    sql = translate_sql_query(
        "SELECT a AS x, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 "
        "ORDER BY x DESC NULLS LAST, b",
        dialect,
        source_dialect="duckdb",
    )

    tree = sqlglot.parse_one(sql, read=dialect)
    order = tree.args["order"]
    assert [item.this.name for item in order.expressions] == ["x", "b"]
    assert order.expressions[0].args["desc"] is True
    assert order.expressions[0].args["nulls_first"] is False


@pytest.mark.parametrize("dialect", PRESTO_FAMILY)
def test_order_by_on_an_aliased_source_column_resolves_to_the_projected_alias(dialect):
    sql = translate_sql_query(
        "SELECT t.a AS x, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY t.a",
        dialect,
        source_dialect="duckdb",
    )

    assert _outer_order_names(sql, dialect) == ["x"]


@pytest.mark.parametrize("dialect", PRESTO_FAMILY)
def test_positional_order_by_is_hoisted(dialect):
    sql = translate_sql_query(
        "SELECT a, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY 2, 1",
        dialect,
        source_dialect="duckdb",
    )

    tree = sqlglot.parse_one(sql, read=dialect)
    assert [item.this.sql() for item in tree.args["order"].expressions] == ["2", "1"]


def test_hoisted_order_defines_the_result_order_when_executed():
    source = (
        "SELECT g, SUM(v) OVER (PARTITION BY g) AS total, COUNT(*) OVER (PARTITION BY g) AS n FROM t "
        "QUALIFY COUNT(*) OVER (PARTITION BY g) > 1 ORDER BY g DESC"
    )
    rewritten = apply_rewrite("hoist_qualify_order_by", sqlglot.parse_one(source, read="duckdb"), "trino")
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

    rewritten = apply_rewrite("hoist_qualify_order_by", tree, "trino")

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


def test_other_dialects_are_not_rewritten():
    sql = "SELECT a, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1 ORDER BY a"

    rendered = translate_sql_query(sql, "postgres", source_dialect="duckdb")

    assert sqlglot.parse_one(rendered, read="postgres").args.get("order") is None


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
