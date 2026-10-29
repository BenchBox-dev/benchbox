from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from benchbox.core.results.query_plan_models import LogicalOperator, LogicalOperatorType, QueryPlanDAG

ORDERS = "benchbox_plan_orders"
CUSTOMERS = "benchbox_plan_customers"

SHAPE_QUERIES: dict[str, str] = {
    "scan_filter": "SELECT o_id, o_total FROM {orders} WHERE o_total > 100",
    "join": "SELECT c.c_name, o.o_total FROM {orders} o JOIN {customers} c ON o.o_cust = c.c_id",
    "aggregate": "SELECT o_cust, SUM(o_total) AS total FROM {orders} GROUP BY o_cust",
    "union": (
        "SELECT o_id FROM {orders} WHERE o_total > 100 UNION ALL SELECT c_id FROM {customers} WHERE c_region = 'EU'"
    ),
}


@dataclass(frozen=True)
class ShapeTraits:
    union_operator: bool = True


def shape_query(shape: str, orders: str = ORDERS, customers: str = CUSTOMERS) -> str:
    return SHAPE_QUERIES[shape].format(orders=orders, customers=customers)


def _collect(op: LogicalOperator) -> list[LogicalOperator]:
    nodes = [op]
    for child in op.children:
        nodes.extend(_collect(child))
    return nodes


def _scanned_tables(nodes: list[LogicalOperator]) -> set[str]:
    return {
        n.table_name.replace(":", ".").split(".")[-1].strip("`\"'")
        for n in nodes
        if n.operator_type == LogicalOperatorType.SCAN and n.table_name
    }


def assert_plan_semantics(plan: QueryPlanDAG, shape: str, traits: ShapeTraits, orders: str, customers: str) -> None:
    nodes = _collect(plan.logical_root)
    types = {n.operator_type for n in nodes}
    tables = _scanned_tables(nodes)

    if shape == "scan_filter":
        assert orders in tables
        assert customers not in tables
        assert LogicalOperatorType.JOIN not in types
        assert any(n.operator_type == LogicalOperatorType.FILTER or n.filter_expressions for n in nodes)
    elif shape == "join":
        assert {orders, customers} <= tables
        assert LogicalOperatorType.JOIN in types
    elif shape == "aggregate":
        assert orders in tables
        assert LogicalOperatorType.AGGREGATE in types
        assert LogicalOperatorType.JOIN not in types
    elif shape == "union":
        assert {orders, customers} <= tables
        assert LogicalOperatorType.UNION in types or not traits.union_operator


def assert_live_plan_shape(
    adapter: Any,
    connection: Any,
    shape: str,
    *,
    orders: str = ORDERS,
    customers: str = CUSTOMERS,
    traits: ShapeTraits | None = None,
) -> str:
    traits = traits or ShapeTraits()
    query = shape_query(shape, orders, customers)
    raw = adapter.get_query_plan(connection, query)
    assert raw is not None and raw.strip(), "get_query_plan returned empty plan text"
    plan, _ = adapter.capture_query_plan(connection, query, f"live-{shape}")
    assert plan is not None, "capture_query_plan returned None for live output"
    assert plan.logical_root is not None
    assert plan.plan_fingerprint is not None and len(plan.plan_fingerprint) == 64
    assert plan.verify_fingerprint()
    assert_plan_semantics(plan, shape, traits, orders, customers)
    plan2, _ = adapter.capture_query_plan(connection, query, f"live-{shape}")
    assert plan2 is not None
    assert plan2.plan_fingerprint == plan.plan_fingerprint
    return raw


def cursor_executor(connection: Any, *, commit: bool = False) -> Callable[[str], None]:
    def execute(statement: str) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(statement)
            if cursor.description is not None:
                cursor.fetchall()
        finally:
            cursor.close()
        if commit:
            connection.commit()

    return execute


@contextmanager
def plan_tables(
    execute: Callable[[str], Any],
    create_statements: tuple[str, str],
    *,
    orders: str = ORDERS,
    customers: str = CUSTOMERS,
) -> Iterator[None]:
    for table in (orders, customers):
        execute(f"DROP TABLE IF EXISTS {table}")
    execute(create_statements[0].format(orders=orders))
    execute(create_statements[1].format(customers=customers))
    execute(f"INSERT INTO {orders} VALUES (1, 1, 50.0), (2, 1, 150.0), (3, 2, 250.0)")
    execute(f"INSERT INTO {customers} VALUES (1, 'a', 'EU'), (2, 'b', 'US')")
    try:
        yield
    finally:
        for table in (orders, customers):
            execute(f"DROP TABLE IF EXISTS {table}")
