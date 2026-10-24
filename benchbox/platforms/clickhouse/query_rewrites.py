from __future__ import annotations

from collections.abc import Mapping

from benchbox.utils.dialect_utils import SQLITE_DISCOUNT_BOUNDS, apply_rewrites

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
    settings = {**SESSION_SETTINGS, **(additional_settings or {})}
    return apply_rewrites(
        query,
        "clickhouse",
        [
            ("lowercase_identifiers", {"patterns": LOWERCASE_IDENTIFIER_PATTERNS}),
            ("decimal_zero_literals", {"precision": DECIMAL_PRECISION, "scale": DECIMAL_SCALE}),
            ("safe_division", {}),
            ("decimal_division_to_double", {}),
            ("fold_between_literal_arithmetic", dict(SQLITE_DISCOUNT_BOUNDS)),
            ("use_nulls_for_rollups", {}),
            ("add_query_settings", {"settings": settings}),
        ],
    )


__all__ = ["rewrite_query_for_clickhouse"]
