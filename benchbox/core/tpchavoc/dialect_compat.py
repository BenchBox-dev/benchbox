from __future__ import annotations

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox.core.tpchavoc.cloud_compat import _ordered_array, rewrite_cloud_variant

POSTGRES_ALIAS_VARIANT_IDS = frozenset({"3_v7", "4_v7", "5_v7", "7_v7", "9_v7", "10_v7", "11_v7"})
POSTGRES_QUALIFIED_COLUMNS: dict[str, dict[str, str]] = {
    "2_v5": {"ps_partkey": "partsupp", "ps_supplycost": "partsupp"},
    "17_v2": {"l_partkey": "lineitem", "l_quantity": "lineitem", "l_extendedprice": "lineitem"},
}
POSTGRES_DUAL_VARIANT_IDS = frozenset({"17_v4"})
CLICKHOUSE_FILTER_VARIANT_IDS = frozenset({"1_v6", "12_v7"})
DATAFUSION_EMPTY_GROUP_VARIANT_IDS = frozenset({"6_v2", "14_v2"})
PRESTO_ALIAS_VARIANT_IDS = POSTGRES_ALIAS_VARIANT_IDS
PRESTO_WINDOW_FILTER_VARIANT_IDS = frozenset({"2_v7"})
PRESTO_ARRAY_AGGREGATION_VARIANT_IDS = frozenset({"1_v7"})
_CLOUD_DIALECTS = frozenset({"bigquery", "databricks", "snowflake"})
_POSTGRES_DIALECTS = frozenset({"postgres", "postgresql"})
_PRESTO_TRINO_DIALECTS = frozenset({"presto", "trino", "athena"})


def rewrite_dialect_variant(query_id: str, query: str, target_dialect: str) -> str:
    target = target_dialect.lower()
    if target in _CLOUD_DIALECTS:
        return rewrite_cloud_variant(query_id, query, target)
    if target == "clickhouse" and query_id in CLICKHOUSE_FILTER_VARIANT_IDS:
        return _rewrite_clickhouse_filters(query)
    if target == "datafusion" and query_id in DATAFUSION_EMPTY_GROUP_VARIANT_IDS:
        return _drop_empty_grouping(query)
    if target in _PRESTO_TRINO_DIALECTS:
        if query_id in PRESTO_ALIAS_VARIANT_IDS:
            query = _inline_select_aliases(query, target)
        if query_id in PRESTO_WINDOW_FILTER_VARIANT_IDS:
            query = _rewrite_window_filters(query, target)
        if query_id in PRESTO_ARRAY_AGGREGATION_VARIANT_IDS:
            query = _rewrite_presto_array_aggregation(query, target)
    if target in _POSTGRES_DIALECTS:
        if query_id in POSTGRES_ALIAS_VARIANT_IDS:
            query = _inline_select_aliases(query, "postgres")
        if columns := POSTGRES_QUALIFIED_COLUMNS.get(query_id):
            query = _qualify_postgres_columns(query, columns)
        if query_id in POSTGRES_DUAL_VARIANT_IDS:
            query = _rewrite_postgres_dual(query)
    return query


def _inline_select_aliases(query: str, dialect: str) -> str:
    tree = sqlglot.parse_one(query, read=dialect)
    for select in tree.find_all(exp.Select):
        aliases = {
            projection.alias: projection.this
            for projection in select.expressions
            if isinstance(projection, exp.Alias) and projection.alias
        }
        if not aliases:
            continue
        for clause_name in ("having", "where"):
            clause = select.args.get(clause_name)
            if clause is None:
                continue
            for column in list(clause.find_all(exp.Column)):
                if column.table or column.name not in aliases:
                    continue
                if column.find_ancestor(exp.Select) is select:
                    column.replace(aliases[column.name].copy())
    return tree.sql(dialect=dialect)


def _rewrite_window_filters(query: str, dialect: str) -> str:
    tree = sqlglot.parse_one(query, read=dialect)
    return tree.transform(_window_filter_node).sql(dialect=dialect)


_PRESTO_UNNEST_AGGREGATION = """
SELECT l_returnflag, l_linestatus,
    SUM(quantity) AS sum_qty,
    SUM(price) AS sum_base_price,
    SUM(price * (1 - discount)) AS sum_disc_price,
    SUM(price * (1 - discount) * (1 + tax)) AS sum_charge,
    AVG(quantity) AS avg_qty,
    AVG(price) AS avg_price,
    AVG(discount) AS avg_disc,
    COUNT(*) AS count_order
FROM aggregated
CROSS JOIN UNNEST(quantities, prices, discounts, taxes) AS item(quantity, price, discount, tax)
GROUP BY l_returnflag, l_linestatus
ORDER BY l_returnflag, l_linestatus
"""


def _rewrite_presto_array_aggregation(query: str, dialect: str = "presto") -> str:
    tree = sqlglot.parse_one(query, read=dialect)
    with_ = tree.find(exp.With).transform(
        lambda node: _ordered_array(node.expressions[0], dialect) if isinstance(node, exp.List) else node
    )
    outer = sqlglot.parse_one(_PRESTO_UNNEST_AGGREGATION, read=dialect)
    for cte in with_.expressions:
        outer.with_(cte.alias, as_=cte.this, copy=False)
    return outer.sql(dialect=dialect)


def _window_filter_node(node: exp.Expression) -> exp.Expression:
    if not isinstance(node, exp.Filter) or not isinstance(node.parent, exp.Window):
        return node
    aggregate = node.this.copy()
    if isinstance(aggregate.this, (exp.Star, exp.Distinct)):
        raise NotImplementedError("Window FILTER rewrite does not support COUNT(*) or DISTINCT.")
    condition = node.expression.this.copy()
    aggregate.set("this", exp.If(this=condition, true=aggregate.this.copy(), false=exp.Null()))
    return aggregate


def _qualify_postgres_columns(query: str, columns: dict[str, str]) -> str:
    tree = sqlglot.parse_one(query, read="postgres")
    for column in tree.find_all(exp.Column):
        if not column.table and (table := columns.get(column.name)):
            column.set("table", exp.to_identifier(table))
    return tree.sql(dialect="postgres")


def _rewrite_postgres_dual(query: str) -> str:
    tree = sqlglot.parse_one(query, read="postgres")
    replacement = sqlglot.parse_one("SELECT * FROM (VALUES (1)) AS dual(dual_col)", read="postgres").find(exp.From).this
    for subquery in tree.find_all(exp.Subquery):
        inner = subquery.this
        if subquery.alias != "dual" or not isinstance(inner, exp.Select) or len(inner.expressions) != 1:
            continue
        expression = inner.expressions[0]
        if isinstance(expression, exp.Literal) and expression.this == "1" and not expression.is_string:
            subquery.replace(replacement.copy())
    return tree.sql(dialect="postgres")


def _rewrite_clickhouse_filters(query: str) -> str:
    tree = sqlglot.parse_one(query, read="clickhouse")
    return tree.transform(_clickhouse_filter_node).sql(dialect="clickhouse")


def _clickhouse_filter_node(node: exp.Expression) -> exp.Expression:
    if not isinstance(node, exp.Filter):
        return node
    aggregate = node.this
    condition = node.expression.this.copy()
    if isinstance(aggregate, exp.Count) and isinstance(aggregate.this, exp.Star):
        return exp.Anonymous(this="countIf", expressions=[condition])
    combinator = {exp.Sum: "sumOrNullIf", exp.Avg: "avgOrNullIf"}.get(type(aggregate))
    if combinator is None:
        raise NotImplementedError(
            f"ClickHouse FILTER rewrite handles only COUNT(*), SUM, and AVG; got {type(aggregate).__name__}. "
            "Extend the combinator map before scoping this variant."
        )
    return exp.Anonymous(this=combinator, expressions=[aggregate.this.copy(), condition])


def _drop_empty_grouping(query: str) -> str:
    tree = sqlglot.parse_one(query, read="postgres")
    for select in tree.find_all(exp.Select):
        group = select.args.get("group")
        if group and len(group.expressions) == 1:
            only = group.expressions[0]
            if isinstance(only, exp.Tuple) and not only.expressions:
                select.set("group", None)
    return tree.sql(dialect="postgres")


__all__ = [
    "CLICKHOUSE_FILTER_VARIANT_IDS",
    "DATAFUSION_EMPTY_GROUP_VARIANT_IDS",
    "POSTGRES_ALIAS_VARIANT_IDS",
    "POSTGRES_DUAL_VARIANT_IDS",
    "POSTGRES_QUALIFIED_COLUMNS",
    "PRESTO_ALIAS_VARIANT_IDS",
    "PRESTO_ARRAY_AGGREGATION_VARIANT_IDS",
    "PRESTO_WINDOW_FILTER_VARIANT_IDS",
    "rewrite_dialect_variant",
]
