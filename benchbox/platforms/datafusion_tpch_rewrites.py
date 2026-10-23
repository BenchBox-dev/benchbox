from __future__ import annotations

from functools import cache

from benchbox.utils.dialect_utils import translate_sql_query

DATAFUSION_TPCH_REWRITE_NAMES: dict[str, str] = {
    "11": "decorrelate_subqueries",
    "16": "not_in_subquery_to_not_exists",
    "18": "in_aggregate_subquery_to_exists",
    "20": "decorrelate_subqueries",
}
_NON_NULL_COLUMNS = ["ps_suppkey", "s_suppkey"]


@cache
def _tpch_schema() -> dict[str, dict[str, str]]:
    from benchbox.core.tpch.schema import TABLES

    return {table.name: {column.name: column.get_sql_type() for column in table.columns} for table in TABLES}


def _rewrite_args(query_id: str) -> dict:
    if query_id in {"11", "20"}:
        return {"schema": _tpch_schema()}
    if query_id == "16":
        return {"non_null_columns": _NON_NULL_COLUMNS}
    return {"alias": "t"}


def normalize_tpch_query_id(query_id: str | int | None) -> str | None:
    if query_id is None:
        return None
    normalized = str(query_id).strip().upper()
    return normalized[1:] if normalized.startswith("Q") else normalized


def rewrite_datafusion_tpch_query(query: str, query_id: str | int | None) -> str:
    normalized = normalize_tpch_query_id(query_id)
    name = DATAFUSION_TPCH_REWRITE_NAMES.get(normalized or "")
    if name is None:
        return query
    return translate_sql_query(
        query,
        "datafusion",
        source_dialect="datafusion",
        identify=False,
        rewrites=[(name, _rewrite_args(normalized))],
    )


__all__ = ["DATAFUSION_TPCH_REWRITE_NAMES", "normalize_tpch_query_id", "rewrite_datafusion_tpch_query"]
