from __future__ import annotations

import contextlib

from benchbox.core.tpchavoc.cloud_compat import rewrite_cloud_variant
from benchbox.utils.dialect_utils import UnknownSqlDialectError, resolve_sql_dialect, translate_sql_query

POSTGRES_ALIAS_VARIANT_IDS = frozenset({"3_v7", "4_v7", "5_v7", "7_v7", "9_v7", "10_v7", "11_v7"})
POSTGRES_QUALIFIED_COLUMNS: dict[str, dict[str, str]] = {
    "2_v5": {"ps_partkey": "partsupp", "ps_supplycost": "partsupp"},
    "17_v2": {"l_partkey": "lineitem", "l_quantity": "lineitem", "l_extendedprice": "lineitem"},
}
POSTGRES_DUAL_VARIANT_IDS = frozenset({"17_v4"})
CLICKHOUSE_FILTER_VARIANT_IDS = frozenset({"1_v6", "3_v7", "7_v7", "10_v7", "12_v7"})
CLICKHOUSE_OUTER_ALIAS_VARIANTS: dict[str, tuple[str, str]] = {
    "2_v8": ("partsupp", "partsupp_outer"),
    "10_v8": ("lineitem", "lineitem_outer"),
}
CLICKHOUSE_COALESCED_COUNT_SUBQUERY_VARIANT_IDS = frozenset({"13_v1"})
DORIS_FILTER_VARIANT_IDS = frozenset({"1_v6", *(f"{number}_v7" for number in range(2, 19))})
SINGLESTORE_FILTER_VARIANT_IDS = frozenset({"1_v6", *(f"{number}_v7" for number in range(2, 19))})
SINGLESTORE_GROUPING_SET_VARIANT_IDS = frozenset({"1_v9"})
SINGLESTORE_SOURCELESS_WHERE_VARIANT_IDS = frozenset({"7_v8", "12_v8", "13_v8", "18_v4"})
SINGLESTORE_ALIAS_QUALIFIED_COLUMNS: dict[str, dict[str, str]] = {
    "5_v4": {"revenue": "combined"},
    "11_v4": {"value": "combined"},
}
SINGLESTORE_ORDER_SUBQUERY_VARIANT_IDS = frozenset({"3_v1", "5_v1", "10_v1", "16_v1"})
SINGLESTORE_AGGREGATE_SUBQUERY_VARIANT_IDS = frozenset({"4_v7", "4_v10", "14_v8", "16_v7", "16_v10", "17_v7", "17_v10"})
SINGLESTORE_CORRELATED_GROUP_VARIANTS: dict[str, tuple[str, ...]] = {"2_v2": ("ps_partkey",)}
DATAFUSION_EMPTY_GROUP_VARIANT_IDS = frozenset({"6_v2", "14_v2"})
PRESTO_ALIAS_VARIANT_IDS = POSTGRES_ALIAS_VARIANT_IDS
PRESTO_WINDOW_FILTER_VARIANT_IDS = frozenset({"2_v7"})
LIST_AGGREGATE_VARIANT_IDS = frozenset({"1_v7"})
LIST_AGGREGATE_DIALECTS = frozenset(
    {"presto", "trino", "athena", "bigquery", "spark", "databricks", "sqlite", "postgres", "datafusion"}
)
LIST_AGGREGATE_ORDER = ("l_orderkey", "l_linenumber")
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
    if target == "clickhouse":
        return _clickhouse_variant_rewrites(query_id)
    if target == "doris" and query_id in DORIS_FILTER_VARIANT_IDS:
        return [("filtered_aggregates_to_conditional", {})]
    if target == "singlestore":
        return _singlestore_variant_rewrites(query_id)
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


def _singlestore_variant_rewrites(query_id: str) -> list[Rewrite]:
    rewrites: list[Rewrite] = []
    if query_id in SINGLESTORE_FILTER_VARIANT_IDS:
        rewrites.append(("filtered_aggregates_to_conditional", {}))
    if query_id in GROUP_BY_EMPTY_VARIANT_IDS:
        rewrites.append(("drop_empty_group_by", {}))
    if query_id in SINGLESTORE_GROUPING_SET_VARIANT_IDS:
        rewrites.append(("unwrap_single_grouping_set", {}))
    if query_id in SINGLESTORE_SOURCELESS_WHERE_VARIANT_IDS:
        rewrites.append(("add_dual_source_to_filtered_selects", {}))
    if columns := SINGLESTORE_ALIAS_QUALIFIED_COLUMNS.get(query_id):
        rewrites.append(("qualify_aggregated_columns", {"tables": columns}))
    if query_id in SINGLESTORE_ORDER_SUBQUERY_VARIANT_IDS:
        rewrites.append(("wrap_order_by_subquery_aliases", {}))
    if query_id in SINGLESTORE_AGGREGATE_SUBQUERY_VARIANT_IDS:
        rewrites.append(("hoist_aggregate_subqueries", {}))
    if group_columns := SINGLESTORE_CORRELATED_GROUP_VARIANTS.get(query_id):
        rewrites.append(("drop_correlated_scalar_group_by", {"columns": list(group_columns)}))
    return rewrites


def _clickhouse_variant_rewrites(query_id: str) -> list[Rewrite]:
    rewrites: list[Rewrite] = []
    if query_id in CLICKHOUSE_FILTER_VARIANT_IDS:
        rewrites.append(("filtered_aggregates_to_conditional", {}))
    if outer_alias := CLICKHOUSE_OUTER_ALIAS_VARIANTS.get(query_id):
        table, alias = outer_alias
        rewrites.append(("alias_outer_table_references", {"table": table, "alias": alias}))
    if query_id in CLICKHOUSE_COALESCED_COUNT_SUBQUERY_VARIANT_IDS:
        rewrites.append(("coalesce_count_subqueries", {}))
    return rewrites


def rewrite_dialect_variant(query_id: str, query: str, target_dialect: str) -> str:
    target = target_dialect.lower()
    if target in _CLOUD_DIALECTS:
        return rewrite_cloud_variant(query_id, query, target)
    rewrites = _variant_rewrites(query_id, target)
    if rewrites:
        query = translate_sql_query(query, target, source_dialect=target, identify=False, rewrites=rewrites)
    if target == "doris" and query_id in GROUP_BY_EMPTY_VARIANT_IDS:
        query = query.replace(" GROUP BY ()", "")
    return query


def list_aggregate_variant_rewrites(target_dialect: str) -> list[Rewrite]:
    resolved = resolve_sql_dialect(target_dialect)
    return [
        ("order_list_aggregates", {"order_by": list(LIST_AGGREGATE_ORDER), "dialect": resolved}),
        ("reduce_list_aggregates", {"dialect": resolved}),
    ]


def translate_list_aggregate_variant(query_id: str, duckdb_query: str, target_dialect: str) -> str | None:
    target = target_dialect.lower()
    with contextlib.suppress(UnknownSqlDialectError):
        target = resolve_sql_dialect(target)
    if query_id not in LIST_AGGREGATE_VARIANT_IDS or target not in LIST_AGGREGATE_DIALECTS:
        return None
    return translate_sql_query(
        duckdb_query,
        target,
        source_dialect="duckdb",
        identify=False,
        rewrites=list_aggregate_variant_rewrites(target),
    )


__all__ = [
    "CLICKHOUSE_COALESCED_COUNT_SUBQUERY_VARIANT_IDS",
    "CLICKHOUSE_FILTER_VARIANT_IDS",
    "CLICKHOUSE_OUTER_ALIAS_VARIANTS",
    "DATAFUSION_EMPTY_GROUP_VARIANT_IDS",
    "DORIS_FILTER_VARIANT_IDS",
    "DUAL_VARIANT_IDS",
    "GROUP_BY_EMPTY_VARIANT_IDS",
    "LIST_AGGREGATE_DIALECTS",
    "LIST_AGGREGATE_ORDER",
    "LIST_AGGREGATE_VARIANT_IDS",
    "POSTGRES_ALIAS_VARIANT_IDS",
    "POSTGRES_DUAL_VARIANT_IDS",
    "POSTGRES_QUALIFIED_COLUMNS",
    "PRESTO_ALIAS_VARIANT_IDS",
    "PRESTO_WINDOW_FILTER_VARIANT_IDS",
    "SCALAR_GROUP_BY_VARIANT_IDS",
    "SINGLESTORE_AGGREGATE_SUBQUERY_VARIANT_IDS",
    "SINGLESTORE_ALIAS_QUALIFIED_COLUMNS",
    "SINGLESTORE_CORRELATED_GROUP_VARIANTS",
    "SINGLESTORE_FILTER_VARIANT_IDS",
    "SINGLESTORE_GROUPING_SET_VARIANT_IDS",
    "SINGLESTORE_ORDER_SUBQUERY_VARIANT_IDS",
    "SINGLESTORE_SOURCELESS_WHERE_VARIANT_IDS",
    "SPARK_VARIANT_IDS",
    "list_aggregate_variant_rewrites",
    "normalize_variant_id",
    "rewrite_dialect_variant",
    "rewrite_spark_variant",
    "translate_list_aggregate_variant",
]
