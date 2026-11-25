from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from benchbox.core.expected_results.row_count_references import QUERY_IDS, REFERENCE_DIR

STRUCTURAL_BOUNDS_PATH = REFERENCE_DIR / "structural_bounds.json"
STRUCTURAL_BENCHMARKS = ("tpch", "tpcds")


@dataclass(frozen=True)
class StructuralBound:
    limit: int | None = None
    one_row: bool = False

    def __bool__(self) -> bool:
        return self.limit is not None or self.one_row

    def describe(self) -> str:
        if self.one_row:
            return "exactly 1 row (aggregate without GROUP BY)"
        return f"at most {self.limit} rows (LIMIT {self.limit})"


def _aggregates_outer_select(projection: Any, outer: Any) -> bool:
    from benchbox._vendor.sqlglot import exp

    if projection.find(exp.Window) is not None:
        return False
    return any(node.find_ancestor(exp.Select) is outer for node in projection.find_all(exp.AggFunc))


def derive_structural_bound(sql: str) -> StructuralBound:
    from benchbox._vendor.sqlglot import exp, parse

    statements = [statement for statement in parse(sql, read="duckdb") if statement is not None]
    outer = statements[-1] if statements else None
    while isinstance(outer, exp.Subquery):
        outer = outer.this
    if outer is None:
        return StructuralBound()
    limit_node = outer.args.get("limit")
    limit = None
    if isinstance(limit_node, exp.Limit) and isinstance(limit_node.expression, exp.Literal):
        limit = int(limit_node.expression.this)
    one_row = (
        isinstance(outer, exp.Select)
        and not outer.args.get("group")
        and not outer.args.get("having")
        and not outer.args.get("offset")
        and all(_aggregates_outer_select(projection, outer) for projection in outer.expressions)
    )
    return StructuralBound(limit=limit, one_row=one_row)


@lru_cache(maxsize=1)
def _table() -> dict[str, Any]:
    return json.loads(Path(STRUCTURAL_BOUNDS_PATH).read_text(encoding="utf-8"))


def clear_structural_bounds_cache() -> None:
    _table.cache_clear()


def structural_bounds_identity(benchmark: str) -> str | None:
    return _table().get("query_generation", {}).get(benchmark)


def structural_bound(benchmark: str, query_id: str) -> StructuralBound:
    entry = _table().get(benchmark)
    if entry is None:
        return StructuralBound()
    if query_id in entry["one_row"]:
        return StructuralBound(one_row=True)
    return StructuralBound(limit=entry["limit"].get(query_id))


def validate_structural_table(payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("structural_bounds.json must be a schema version 1 table")
    for benchmark in STRUCTURAL_BENCHMARKS:
        entry = payload.get(benchmark)
        ids = set(QUERY_IDS[benchmark])
        if not isinstance(entry, dict) or set(entry) != {"limit", "one_row"}:
            raise ValueError(f"structural_bounds.json {benchmark} must hold limit and one_row")
        if not set(entry["limit"]) <= ids or not set(entry["one_row"]) <= ids:
            raise ValueError(f"structural_bounds.json {benchmark} names an unknown query id")
        if any(type(value) is not int or value < 1 for value in entry["limit"].values()):
            raise ValueError(f"structural_bounds.json {benchmark} limits must be positive integers")
        if set(entry["limit"]) & set(entry["one_row"]):
            raise ValueError(f"structural_bounds.json {benchmark} lists a query as both limit and one_row")
    identities = payload.get("query_generation")
    if not isinstance(identities, dict) or set(identities) != set(STRUCTURAL_BENCHMARKS):
        raise ValueError("structural_bounds.json must record the query generation identity of each benchmark")
