# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) H (TPC-H) - Copyright (c) Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import re
import threading
from datetime import date, timedelta
from typing import Any


class ParameterExtractionError(ValueError):
    pass


def extract_tpch_parameters(
    seed: int,
    scale_factor: float = 1.0,
) -> dict[int, dict[str, Any]]:
    from benchbox.core.tpch.queries import QGenBinary

    qgen = QGenBinary()
    return {
        query_id: _extract_query_params(query_id, qgen.generate(query_id, seed=seed, scale_factor=scale_factor))
        for query_id in range(1, 23)
    }


_query_cache: dict[tuple[int, int, float], dict[str, Any]] = {}
_query_qgen: Any = None
_query_qgen_lock = threading.Lock()


def _shared_qgen() -> Any:
    global _query_qgen
    with _query_qgen_lock:
        if _query_qgen is None:
            from benchbox.core.tpch.queries import QGenBinary

            _query_qgen = QGenBinary()
        return _query_qgen


def get_tpch_query_extracted_parameters(query_id: int, seed: int, scale_factor: float = 1.0) -> dict[str, Any]:
    cache_key = (query_id, seed, scale_factor)
    if cache_key not in _query_cache:
        sql = _shared_qgen().generate(query_id, seed=seed, scale_factor=scale_factor)
        _query_cache[cache_key] = _extract_query_params(query_id, sql)
    return _query_cache[cache_key]


def _extract_query_params(query_id: int, sql: str) -> dict[str, Any]:
    extractor = _EXTRACTORS.get(query_id)
    if extractor is None:
        raise ParameterExtractionError(f"No parameter extractor for TPC-H query {query_id}")
    try:
        return extractor(sql)
    except ParameterExtractionError as exc:
        raise ParameterExtractionError(f"TPC-H Q{query_id}: {exc}") from None


def _search(pattern: str, sql: str, flags: int = 0) -> re.Match[str]:
    match = re.search(pattern, sql, flags)
    if match is None:
        raise ParameterExtractionError(f"pattern {pattern!r} not found in qgen output")
    return match


def _date_after(column: str, operator: str, sql: str) -> date:
    return _parse_date(_search(rf"{column}\s*{operator}\s*date\s+'(\d{{4}}-\d{{2}}-\d{{2}})'", sql).group(1))


def _q1(sql: str) -> dict[str, Any]:
    delta = int(_search(r"interval\s+'(\d+)'\s+day", sql, re.IGNORECASE).group(1))
    return {"delta": delta, "cutoff_date": date(1998, 12, 1) - timedelta(days=delta)}


def _q2(sql: str) -> dict[str, Any]:
    return {
        "size": int(_search(r"p_size\s*=\s*(\d+)", sql).group(1)),
        "type_suffix": _search(r"p_type\s+like\s+'%(\w+)'", sql, re.IGNORECASE).group(1),
        "region_name": _search(r"r_name\s*=\s*'([^']+)'", sql).group(1),
    }


def _q3(sql: str) -> dict[str, Any]:
    return {
        "segment": _search(r"c_mktsegment\s*=\s*'([^']+)'", sql).group(1),
        "order_date": _date_after("o_orderdate", "<", sql),
    }


def _q4(sql: str) -> dict[str, Any]:
    start_date = _date_after("o_orderdate", ">=", sql)
    return {"start_date": start_date, "end_date": _add_months(start_date, 3)}


def _q5(sql: str) -> dict[str, Any]:
    start_date = _date_after("o_orderdate", ">=", sql)
    return {
        "region_name": _search(r"r_name\s*=\s*'([^']+)'", sql).group(1),
        "start_date": start_date,
        "end_date": _add_years(start_date, 1),
    }


def _q6(sql: str) -> dict[str, Any]:
    start_date = _date_after("l_shipdate", ">=", sql)
    discount = float(
        _search(r"l_discount\s+between\s+([\d.]+)\s*-\s*[\d.]+\s+and\s+([\d.]+)\s*\+", sql, re.IGNORECASE).group(1)
    )
    return {
        "start_date": start_date,
        "end_date": _add_years(start_date, 1),
        "discount_low": round(discount - 0.01, 2),
        "discount_high": round(discount + 0.01, 2),
        "discount": discount,
        "quantity_limit": int(_search(r"l_quantity\s*<\s*(\d+)", sql).group(1)),
    }


def _q7(sql: str) -> dict[str, Any]:
    m = _search(r"n1\.n_name\s*=\s*'([^']+)'\s+and\s+n2\.n_name\s*=\s*'([^']+)'", sql, re.IGNORECASE)
    return {"nation1": m.group(1), "nation2": m.group(2)}


def _q8(sql: str) -> dict[str, Any]:
    return {
        "target_nation": _search(r"when\s+nation\s*=\s*'([^']+)'", sql, re.IGNORECASE).group(1),
        "target_region": _search(r"r_name\s*=\s*'([^']+)'", sql).group(1),
        "target_type": _search(r"p_type\s*=\s*'([^']+)'", sql).group(1),
    }


def _q9(sql: str) -> dict[str, Any]:
    return {"color": _search(r"p_name\s+like\s+'%([^%]+)%'", sql, re.IGNORECASE).group(1)}


def _q10(sql: str) -> dict[str, Any]:
    start_date = _date_after("o_orderdate", ">=", sql)
    return {"start_date": start_date, "end_date": _add_months(start_date, 3)}


def _q11(sql: str) -> dict[str, Any]:
    return {
        "nation_name": _search(r"n_name\s*=\s*'([^']+)'", sql).group(1),
        "fraction": float(_search(r"\*\s*([\d.]+)\s*$", sql, re.MULTILINE).group(1)),
    }


def _q12(sql: str) -> dict[str, Any]:
    modes = _search(r"l_shipmode\s+in\s+\('([^']+)',\s*'([^']+)'\)", sql, re.IGNORECASE)
    start_date = _date_after("l_receiptdate", ">=", sql)
    return {
        "shipmode1": modes.group(1),
        "shipmode2": modes.group(2),
        "start_date": start_date,
        "end_date": _add_years(start_date, 1),
    }


def _q13(sql: str) -> dict[str, Any]:
    m = _search(r"o_comment\s+not\s+like\s+'%([^%]+)%([^%]+)%'", sql, re.IGNORECASE)
    return {"word1": m.group(1), "word2": m.group(2)}


def _q14(sql: str) -> dict[str, Any]:
    start_date = _date_after("l_shipdate", ">=", sql)
    return {"start_date": start_date, "end_date": _add_months(start_date, 1)}


def _q15(sql: str) -> dict[str, Any]:
    start_date = _date_after("l_shipdate", ">=", sql)
    return {"start_date": start_date, "end_date": _add_months(start_date, 3)}


def _q16(sql: str) -> dict[str, Any]:
    sizes = _search(r"p_size\s+in\s+\(([^)]+)\)", sql, re.IGNORECASE).group(1)
    return {
        "brand": _search(r"p_brand\s*<>\s*'([^']+)'", sql).group(1),
        "type_prefix": _search(r"p_type\s+not\s+like\s+'([^%]+)%'", sql, re.IGNORECASE).group(1).rstrip(),
        "sizes": [int(size.strip()) for size in sizes.split(",")],
    }


def _q17(sql: str) -> dict[str, Any]:
    return {
        "brand": _search(r"p_brand\s*=\s*'([^']+)'", sql).group(1),
        "container": _search(r"p_container\s*=\s*'([^']+)'", sql).group(1),
    }


def _q18(sql: str) -> dict[str, Any]:
    return {"quantity_threshold": int(_search(r"sum\(l_quantity\)\s*>\s*(\d+)", sql).group(1))}


def _q19(sql: str) -> dict[str, Any]:
    brands = re.findall(r"p_brand\s*=\s*'([^']+)'", sql)
    quantities = re.findall(r"l_quantity\s*>=\s*(\d+)", sql)
    if len(brands) != 3 or len(quantities) != 3:
        raise ParameterExtractionError(
            f"expected 3 brands and 3 quantities in qgen output, found {len(brands)} and {len(quantities)}"
        )
    return {
        "brand1": brands[0],
        "brand2": brands[1],
        "brand3": brands[2],
        "quantity1": int(quantities[0]),
        "quantity2": int(quantities[1]),
        "quantity3": int(quantities[2]),
    }


def _q20(sql: str) -> dict[str, Any]:
    start_date = _date_after("l_shipdate", ">=", sql)
    return {
        "color_prefix": _search(r"p_name\s+like\s+'([^%]+)%'", sql, re.IGNORECASE).group(1),
        "nation_name": _search(r"n_name\s*=\s*'([^']+)'", sql).group(1),
        "start_date": start_date,
        "end_date": _add_years(start_date, 1),
    }


def _q21(sql: str) -> dict[str, Any]:
    return {"nation_name": _search(r"n_name\s*=\s*'([^']+)'", sql).group(1)}


def _q22(sql: str) -> dict[str, Any]:
    codes = _search(r"for\s+2\)\s+in\s*\n?\s*\(([^)]+)\)", sql, re.IGNORECASE).group(1)
    return {"country_codes": re.findall(r"'(\d+)'", codes)}


def _parse_date(s: str) -> date:
    parts = s.split("-")
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def _add_months(d: date, months: int) -> date:
    month = d.month + months
    year = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, d.day)


def _add_years(d: date, years: int) -> date:
    return date(d.year + years, d.month, d.day)


_EXTRACTORS: dict[int, Any] = {
    1: _q1,
    2: _q2,
    3: _q3,
    4: _q4,
    5: _q5,
    6: _q6,
    7: _q7,
    8: _q8,
    9: _q9,
    10: _q10,
    11: _q11,
    12: _q12,
    13: _q13,
    14: _q14,
    15: _q15,
    16: _q16,
    17: _q17,
    18: _q18,
    19: _q19,
    20: _q20,
    21: _q21,
    22: _q22,
}


_cache: dict[tuple[int, float], dict[int, dict[str, Any]]] = {}


def get_tpch_extracted_parameters(
    seed: int,
    scale_factor: float = 1.0,
    *,
    use_cache: bool = True,
) -> dict[int, dict[str, Any]]:
    cache_key = (seed, scale_factor)
    if use_cache and cache_key in _cache:
        return _cache[cache_key]

    params = extract_tpch_parameters(seed, scale_factor)
    if use_cache:
        _cache[cache_key] = params
    return params


def clear_cache() -> None:
    _cache.clear()
    _query_cache.clear()
