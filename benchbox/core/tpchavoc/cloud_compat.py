from __future__ import annotations

from benchbox.utils.dialect_utils import translate_sql_query

_BIGQUERY_FROMLESS_IDS = frozenset({"7_v8", "12_v8", "13_v8", "18_v4"})
_BIGQUERY_HAVING_COLUMNS = {"5_v4": "revenue", "11_v4": "value"}
_SNOWFLAKE_EMPTY_GROUP_IDS = frozenset({"6_v2", "14_v2"})
BIGQUERY_FILTER_IDS = frozenset(
    {
        "1_v6",
        "2_v7",
        "3_v7",
        "4_v7",
        "5_v7",
        "6_v7",
        "7_v7",
        "8_v7",
        "9_v7",
        "10_v7",
        "11_v7",
        "12_v7",
        "13_v7",
        "14_v7",
        "15_v7",
        "16_v7",
        "17_v7",
        "18_v7",
    }
)


def rewrite_cloud_variant(query_id: str, query: str, target_dialect: str) -> str:
    if target_dialect == "databricks":
        from benchbox.core.tpchavoc.dialect_compat import SPARK_VARIANT_IDS, spark_variant_rewrites

        if query_id in SPARK_VARIANT_IDS and (rewrites := spark_variant_rewrites(query_id)):
            return translate_sql_query(
                query, "databricks", source_dialect="databricks", identify=False, rewrites=rewrites
            )
        return query
    if target_dialect != "bigquery":
        return query
    rewrites = []
    if query_id in _BIGQUERY_FROMLESS_IDS:
        rewrites.append(("add_row_source_to_fromless_selects", {"source": "UNNEST([1])"}))
    if column_name := _BIGQUERY_HAVING_COLUMNS.get(query_id):
        rewrites.append(
            ("qualify_columns_by_name", {"tables": {column_name: "combined"}, "clauses": ["having"], "quoted": True})
        )
    if not rewrites:
        return query
    return translate_sql_query(query, "bigquery", source_dialect="bigquery", identify=False, rewrites=rewrites)
