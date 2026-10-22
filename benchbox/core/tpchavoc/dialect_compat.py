from __future__ import annotations

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox.core.tpchavoc.cloud_compat import _ordered_array, rewrite_cloud_variant
from benchbox.utils.dialect_utils import translate_sql_query

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
SCALAR_GROUP_BY_VARIANT_IDS = frozenset({"1_v1", "3_v1", "7_v1", "8_v1", "9_v1", "10_v1", "12_v1", "16_v1"})
GROUP_BY_EMPTY_VARIANT_IDS = frozenset({"6_v2", "14_v2"})
DUAL_VARIANT_IDS = frozenset({"17_v4"})
SPARK_VARIANT_IDS = SCALAR_GROUP_BY_VARIANT_IDS | GROUP_BY_EMPTY_VARIANT_IDS | DUAL_VARIANT_IDS
_CLOUD_DIALECTS = frozenset({"bigquery", "databricks", "snowflake"})
_POSTGRES_DIALECTS = frozenset({"postgres", "postgresql"})
_PRESTO_TRINO_DIALECTS = frozenset({"presto", "trino", "athena"})

Rewrite = tuple[str, dict]


def normalize_variant_id(query_id: str | None) -> str | None:
    if query_id is None:
        return None
    normalized = str(query_id).strip().lower()
    if normalized.startswith("q"):
        normalized = normalized[1:]
    normalized = normalized.replace("-", "_")
    if "_v" in normalized:
        query_number, _, variant = normalized.partition("_v")
        normalized = f"{query_number.lstrip('0') or '0'}_v{variant}"
    return normalized


def spark_variant_rewrites(query_id: str) -> list[Rewrite]:
    if query_id in SCALAR_GROUP_BY_VARIANT_IDS:
        return [("wrap_grouped_scalar_subqueries", {})]
    if query_id in DUAL_VARIANT_IDS:
        return [("name_literal_subquery_columns", {"column": "dual_col"})]
    return []


def rewrite_spark_variant(query_id: str | None, query: str) -> str:
    normalized = normalize_variant_id(query_id)
    if normalized not in SPARK_VARIANT_IDS:
        return query
    return translate_sql_query(
        query, "spark", source_dialect="spark", identify=False, rewrites=spark_variant_rewrites(normalized)
    )


def _variant_rewrites(query_id: str, target: str) -> list[Rewrite]:
    if target == "databricks":
        return spark_variant_rewrites(query_id)
    if target == "clickhouse" and query_id in CLICKHOUSE_FILTER_VARIANT_IDS:
        return [("filtered_aggregates_to_conditional", {})]
    rewrites: list[Rewrite] = []
    if target in _PRESTO_TRINO_DIALECTS:
        if query_id in PRESTO_ALIAS_VARIANT_IDS:
            rewrites.append(("inline_select_aliases", {}))
        if query_id in PRESTO_WINDOW_FILTER_VARIANT_IDS:
            rewrites.append(("filtered_aggregates_to_conditional", {"windows_only": True}))
    if target in _POSTGRES_DIALECTS:
        if query_id in POSTGRES_ALIAS_VARIANT_IDS:
            rewrites.append(("inline_select_aliases", {}))
        if columns := POSTGRES_QUALIFIED_COLUMNS.get(query_id):
            rewrites.append(("qualify_columns_by_name", {"tables": columns}))
        if query_id in POSTGRES_DUAL_VARIANT_IDS:
            rewrites.append(("drop_literal_row_source", {"alias": "dual"}))
    return rewrites


def rewrite_dialect_variant(query_id: str, query: str, target_dialect: str) -> str:
    target = target_dialect.lower()
    if target in _CLOUD_DIALECTS:
        return rewrite_cloud_variant(query_id, query, target)
    rewrites = _variant_rewrites(query_id, target)
    if rewrites:
        query = translate_sql_query(query, target, source_dialect=target, identify=False, rewrites=rewrites)
    if target in _PRESTO_TRINO_DIALECTS and query_id in PRESTO_ARRAY_AGGREGATION_VARIANT_IDS:
        query = _rewrite_presto_array_aggregation(query, target)
    return query


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


__all__ = [
    "CLICKHOUSE_FILTER_VARIANT_IDS",
    "DATAFUSION_EMPTY_GROUP_VARIANT_IDS",
    "DUAL_VARIANT_IDS",
    "GROUP_BY_EMPTY_VARIANT_IDS",
    "POSTGRES_ALIAS_VARIANT_IDS",
    "POSTGRES_DUAL_VARIANT_IDS",
    "POSTGRES_QUALIFIED_COLUMNS",
    "PRESTO_ALIAS_VARIANT_IDS",
    "PRESTO_ARRAY_AGGREGATION_VARIANT_IDS",
    "PRESTO_WINDOW_FILTER_VARIANT_IDS",
    "SCALAR_GROUP_BY_VARIANT_IDS",
    "SPARK_VARIANT_IDS",
    "normalize_variant_id",
    "rewrite_dialect_variant",
    "rewrite_spark_variant",
]
