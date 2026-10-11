from __future__ import annotations

import re
from collections.abc import Callable


def _rewrite_correlated_exists_to_semijoins(
    query_id: int,
    query: str,
    patterns: tuple[tuple[str, str, str], ...],
    date_filter_pattern: str,
    render: Callable[[re.Match[str], str, str, str], str],
) -> str:
    rewritten = query
    replacements = 0
    for table, customer_key, date_key in patterns:
        pattern = re.compile(
            rf"EXISTS\s*\(SELECT\s+\*\s+FROM\s+{table}\s*,\s*date_dim\s+"
            rf"WHERE\s+c\.c_customer_sk\s*=\s*{customer_key}\s+"
            rf"AND\s+{date_key}\s*=\s*d_date_sk\s+"
            rf"AND\s+(?:{date_filter_pattern})\s*\)",
            flags=re.IGNORECASE,
        )

        def replace(
            match: re.Match[str],
            *,
            table: str = table,
            customer_key: str = customer_key,
            date_key: str = date_key,
        ) -> str:
            return render(match, table, customer_key, date_key)

        rewritten, count = pattern.subn(replace, rewritten, count=1)
        replacements += count

    if replacements != len(patterns):
        raise ValueError(f"Unsupported ClickHouse Q{query_id} shape: expected {len(patterns)} semi-join predicates")
    return rewritten


_Q10_Q35_PATTERNS = (
    ("store_sales", "ss_customer_sk", "ss_sold_date_sk"),
    ("web_sales", "ws_bill_customer_sk", "ws_sold_date_sk"),
    ("catalog_sales", "cs_ship_customer_sk", "cs_sold_date_sk"),
)


def _render_q35_semijoin(match: re.Match[str], table: str, customer_key: str, date_key: str) -> str:
    return (
        f"c.c_customer_sk IN (SELECT {customer_key} FROM {table}, date_dim "
        f"WHERE {date_key} = d_date_sk AND d_year = {match.group('year')} "
        f"AND d_qoy < {match.group('qoy')})"
    )


def rewrite_q35_for_clickhouse(query: str) -> str:
    return _rewrite_correlated_exists_to_semijoins(
        35,
        query,
        _Q10_Q35_PATTERNS,
        r"d_year\s*=\s*(?P<year>\d+)\s+AND\s+d_qoy\s*<\s*(?P<qoy>\d+)",
        _render_q35_semijoin,
    )


def _render_q10_semijoin(match: re.Match[str], table: str, customer_key: str, date_key: str) -> str:
    date_filter = re.sub(r"\s+", " ", match.group("date_filter")).strip()
    return (
        f"c.c_customer_sk IN (SELECT {customer_key} FROM {table}, date_dim "
        f"WHERE {date_key} = d_date_sk AND {date_filter})"
    )


def rewrite_q10_for_clickhouse(query: str) -> str:
    return _rewrite_correlated_exists_to_semijoins(
        10,
        query,
        _Q10_Q35_PATTERNS,
        r"(?P<date_filter>d_year\s*=\s*\d+\s+AND\s+d_moy\s+between\s+\d+\s+and\s+\d+\s*\+\s*3)",
        _render_q10_semijoin,
    )
