from __future__ import annotations

import logging
import re
from collections.abc import Mapping

from benchbox.utils.dialect_utils import apply_rewrite

logger = logging.getLogger(__name__)

_QUERY_START = re.compile(r"\s*(?:(?:--[^\n]*\n|/\*.*?\*/)\s*)*(?:SELECT|WITH)\b", re.IGNORECASE | re.DOTALL)

SESSION_SETTINGS: Mapping[str, str | int] = {"joined_subquery_requires_alias": 0}

LOWERCASE_IDENTIFIER_PATTERNS: tuple[str, ...] = (
    r"SR_RETURN_AMT",
    r"SR_CUSTOMER_SK",
    r"SR_STORE_SK",
    r"SR_RETURNED_DATE_SK",
    r"[A-Z][A-Z_]+_SK",
    r"[A-Z][A-Z_]+_AMT",
    r"[A-Z][A-Z_]+_QTY",
    r"[A-Z][A-Z_]+_FEE",
    r"[A-Z][A-Z_]+_CASH",
    r"[A-Z][A-Z_]+_CREDIT",
    r"[A-Z][A-Z_]+_TAX",
    r"[A-Z][A-Z_]+_CHARGE",
    r"D_YEAR",
    r"D_MOY",
    r"D_DATE_SK",
)

DECIMAL_PRECISION = 15
DECIMAL_SCALE = 2


def rewrite_query_for_clickhouse(
    query: str,
    additional_settings: Mapping[str, str | int] | None = None,
) -> str:
    if not _QUERY_START.match(query):
        return query

    settings = {**SESSION_SETTINGS, **(additional_settings or {})}
    try:
        rewritten = apply_rewrite("lowercase_identifiers", query, "clickhouse", patterns=LOWERCASE_IDENTIFIER_PATTERNS)
        rewritten = apply_rewrite(
            "decimal_zero_literals", rewritten, "clickhouse", precision=DECIMAL_PRECISION, scale=DECIMAL_SCALE
        )
        rewritten = apply_rewrite("safe_division", rewritten, "clickhouse")
        return apply_rewrite("add_query_settings", rewritten, "clickhouse", settings=settings)
    except Exception as error:
        logger.warning("ClickHouse execution rewrites skipped (unparseable ClickHouse SQL): %s", error)
        return query


__all__ = ["rewrite_query_for_clickhouse"]
