from __future__ import annotations

import sqlite3

import pytest

from benchbox.core.query_plans.parsers.sqlite import SQLiteQueryPlanParser
from benchbox.core.results.query_plan_models import LogicalOperator, LogicalOperatorType, QueryPlanDAG
from benchbox.platforms.sqlite import _format_sqlite_query_plan

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_parse_explain_output_with_header_and_tree_lines():
    parser = SQLiteQueryPlanParser()
    explain_output = """QUERY PLAN
2|0|SCAN TABLE customer
5|0|SEARCH TABLE orders USING INDEX idx_customer (c_custkey=?)
"""

    plan = parser.parse_explain_output("Q1", explain_output)

    assert plan is not None
    assert plan.query_id == "Q1"
    assert plan.platform == "sqlite"
    assert plan.logical_root is not None


def test_parse_explain_output_empty_returns_none():
    parser = SQLiteQueryPlanParser()
    assert parser.parse_explain_output("Q2", "") is None


def test_operator_type_and_details_helpers():
    parser = SQLiteQueryPlanParser()

    assert parser._infer_operator_type("SCAN TABLE orders") == "SCAN"
    assert parser._infer_operator_type("SEARCH TABLE orders USING INDEX idx_o") == "INDEX_SCAN"
    assert parser._infer_operator_type("USE TEMP B-TREE FOR ORDER BY") == "SORT"
    assert parser._infer_operator_type("COMPOUND SUBQUERY") == "UNION"
    assert parser._infer_operator_type("something else") == "OTHER"

    details = parser._extract_details("SEARCH TABLE orders USING INDEX idx_orders LEFT JOIN")
    assert details["table_name"] == "orders"
    assert details["index_name"] == "idx_orders"
    assert details["join_type"] == "left"


def test_map_and_fallback_operator_paths():
    parser = SQLiteQueryPlanParser()

    assert parser._map_sqlite_operator("SCAN") == LogicalOperatorType.SCAN
    assert parser._map_sqlite_operator("SORT") == LogicalOperatorType.SORT
    assert parser._map_sqlite_operator("AGGREGATE") == LogicalOperatorType.AGGREGATE
    assert parser._map_sqlite_operator("UNION") == LogicalOperatorType.UNION
    assert parser._map_sqlite_operator("SUBQUERY") == LogicalOperatorType.SUBQUERY
    assert parser._map_sqlite_operator("UNKNOWN_KIND") == LogicalOperatorType.OTHER

    fallback = parser._create_fallback_operator()
    assert fallback.operator_type == LogicalOperatorType.OTHER


def test_parse_impl_rejects_no_operator_lines():
    parser = SQLiteQueryPlanParser()

    with pytest.raises(ValueError, match="No plan lines found"):
        parser._parse_impl("Q3", "QUERY PLAN")


@pytest.fixture
def eqp_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE a (x INTEGER, y INTEGER);"
        "CREATE TABLE b (x INTEGER, y INTEGER);"
        "CREATE TABLE c (x INTEGER, y INTEGER);"
    )
    yield conn
    conn.close()


def _parse(conn, sql):
    rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    text = _format_sqlite_query_plan(rows)
    return SQLiteQueryPlanParser().parse_explain_output("Q", text)


def _shape(op: LogicalOperator):
    label = op.table_name or op.operator_type.value
    return (label, [_shape(child) for child in op.children])


def test_union_all_preserves_both_branches(eqp_conn):
    plan = _parse(eqp_conn, "SELECT x FROM a UNION ALL SELECT x FROM b")

    assert _shape(plan.logical_root) == (
        "Union",
        [
            ("Subquery", [("a", [])]),
            ("Union", [("b", [])]),
        ],
    )


def test_subquery_scan_is_child_of_subquery_not_chained(eqp_conn):
    plan = _parse(eqp_conn, "SELECT * FROM a WHERE x IN (SELECT x FROM b)")

    root = plan.logical_root
    assert root.operator_type == LogicalOperatorType.OTHER
    assert [child.table_name or child.operator_type.value for child in root.children] == ["a", "Subquery"]
    scan_a, subquery = root.children
    assert scan_a.children == []
    assert subquery.children[0].table_name == "b"


def test_correlated_subquery_topology(eqp_conn):
    plan = _parse(eqp_conn, "SELECT x FROM a WHERE y = (SELECT max(y) FROM b WHERE b.x = a.x)")

    scan_a, subquery = plan.logical_root.children
    assert scan_a.table_name == "a"
    assert scan_a.children == []
    assert subquery.operator_type == LogicalOperatorType.SUBQUERY
    assert [child.table_name for child in subquery.children] == ["b"]


def test_three_table_join_keeps_sibling_scans(eqp_conn):
    plan = _parse(eqp_conn, "SELECT * FROM a, b, c WHERE a.x = b.x AND b.y = c.y")

    root = plan.logical_root
    assert root.operator_type == LogicalOperatorType.OTHER
    assert all(child.children == [] for child in root.children)
    assert {"a", "b", "c"} <= {child.table_name for child in root.children}


def test_branching_plan_fingerprint_differs_from_chain_and_survives_reload():
    parser = SQLiteQueryPlanParser()
    branching = parser.parse_explain_output("Q", "QUERY PLAN\n1|0|COMPOUND QUERY\n2|1|SCAN a\n3|1|SCAN b")
    chain = parser.parse_explain_output("Q", "QUERY PLAN\n1|0|COMPOUND QUERY\n2|1|SCAN a\n3|2|SCAN b")

    assert branching.plan_fingerprint != chain.plan_fingerprint

    reloaded = QueryPlanDAG.from_dict(branching.to_dict())
    assert [child.table_name for child in reloaded.logical_root.children] == ["a", "b"]
    assert reloaded.plan_fingerprint == branching.plan_fingerprint
    assert reloaded.verify_fingerprint()
    assert reloaded.compute_plan_fingerprint() == branching.plan_fingerprint


@pytest.mark.parametrize(
    "text",
    [
        "QUERY PLAN\n|--SCAN a",
        "QUERY PLAN\n2|0|SCAN a\n2|0|SCAN b",
        "QUERY PLAN\n2|7|SCAN a",
        "QUERY PLAN\n2|3|SCAN a\n3|0|SCAN b",
        "QUERY PLAN\nx|0|SCAN a",
    ],
)
def test_unrepresentable_rows_raise_parse_error(text):
    parser = SQLiteQueryPlanParser()
    with pytest.raises(ValueError):
        parser._parse_impl("Q", text)
    assert parser.parse_explain_output("Q", text) is None
