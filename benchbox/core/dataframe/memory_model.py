from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

GIB = 1024**3

SUBSET_MODEL_MAX_SF = 10.0


@dataclass(frozen=True)
class TableSize:
    sf10_bytes: int
    exponent: float

    def bytes_at(self, scale_factor: float) -> float:
        return self.sf10_bytes * (scale_factor / 10.0) ** self.exponent


TPCH_TABLE_SIZES: dict[str, TableSize] = {
    "customer": TableSize(243347642, 1.0),
    "lineitem": TableSize(7715741636, 1.0),
    "nation": TableSize(2199, 0.0),
    "orders": TableSize(1734195031, 1.0),
    "part": TableSize(241336157, 1.0),
    "partsupp": TableSize(1196850769, 1.0),
    "region": TableSize(384, 0.0),
    "supplier": TableSize(14076368, 1.0),
}

TPCDS_TABLE_SIZES: dict[str, TableSize] = {
    "call_center": TableSize(7520, 0.6),
    "catalog_page": TableSize(1659048, 0.01),
    "catalog_returns": TableSize(220252418, 1.0),
    "catalog_sales": TableSize(3023483057, 1.0),
    "customer": TableSize(66377545, 0.7),
    "customer_address": TableSize(27457596, 0.7),
    "customer_demographics": TableSize(78739296, 0.0),
    "date_dim": TableSize(10244389, 0.0),
    "household_demographics": TableSize(144453, 0.0),
    "income_band": TableSize(308, 0.0),
    "inventory": TableSize(2629887971, 1.0),
    "item": TableSize(28753325, 0.76),
    "promotion": TableSize(61659, 0.22),
    "reason": TableSize(2504, 0.0),
    "ship_mode": TableSize(1093, 0.0),
    "store": TableSize(26983, 0.93),
    "store_returns": TableSize(335710328, 1.0),
    "store_sales": TableSize(3970120305, 1.0),
    "time_dim": TableSize(5021380, 0.0),
    "warehouse": TableSize(1176, 0.31),
    "web_page": TableSize(19131, 0.52),
    "web_returns": TableSize(101392529, 1.0),
    "web_sales": TableSize(1504223803, 1.0),
    "web_site": TableSize(12125, 0.14),
}

TABLE_SIZE_MODELS: dict[str, dict[str, TableSize]] = {
    "tpch": TPCH_TABLE_SIZES,
    "tpcds": TPCDS_TABLE_SIZES,
}

PLATFORM_BASELINE_GB: dict[str, float] = {
    "polars": 2.0,
    "datafusion": 2.0,
    "pyspark": 4.0,
}

_TEMPLATE_LAYOUT: dict[str, tuple[str, str, str, str]] = {
    "tpch": ("tpc-h", "queries", "{number}.sql", r"^Q?(\d+)$"),
    "tpcds": ("tpc-ds", "query_templates", "query{number}.tpl", r"^Q?(\d+)[A-Z]?$"),
}

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_DEFINE = re.compile(r"^\s*define\b[^;]*;", re.IGNORECASE | re.MULTILINE | re.DOTALL)
_STRING_LITERAL = re.compile(r"'[^']*'")
_DIRECTIVE_LINE = re.compile(r"^\s*:[a-z][^\n]*$", re.IGNORECASE | re.MULTILINE)


@cache
def _template_tables(benchmark: str, number: int) -> frozenset[str] | None:
    layout = _TEMPLATE_LAYOUT.get(benchmark)
    if layout is None:
        return None
    suite, subdir, pattern, _ = layout
    try:
        from benchbox.utils.tpc_compilation import get_tpc_templates_dir

        path: Path = get_tpc_templates_dir(suite) / subdir / pattern.format(number=number)
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, RuntimeError):
        return None
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _LINE_COMMENT.sub(" ", text)
    text = _DEFINE.sub(" ", text)
    text = _DIRECTIVE_LINE.sub(" ", text)
    text = _STRING_LITERAL.sub(" ", text).lower()
    tables = frozenset(name for name in TABLE_SIZE_MODELS[benchmark] if re.search(rf"\b{name}\b", text))
    return tables or None


def query_tables(benchmark: str, query_id: object) -> frozenset[str] | None:
    layout = _TEMPLATE_LAYOUT.get(benchmark)
    if layout is None:
        return None
    match = re.match(layout[3], str(query_id).strip().upper())
    if match is None:
        return None
    return _template_tables(benchmark, int(match.group(1)))


def subset_tables(benchmark: str, queries: Iterable[object] | None) -> tuple[str, ...] | None:
    if isinstance(queries, str):
        queries = [part for part in queries.split(",") if part.strip()]
    if not queries:
        return None
    benchmark = benchmark.lower()
    union: set[str] = set()
    for query_id in queries:
        tables = query_tables(benchmark, query_id)
        if tables is None:
            return None
        union |= tables
    return tuple(sorted(union)) or None


def tables_raw_gb(benchmark: str, tables: Iterable[str], scale_factor: float) -> float:
    sizes = TABLE_SIZE_MODELS[benchmark.lower()]
    return sum(sizes[name].bytes_at(scale_factor) for name in tables) / GIB
