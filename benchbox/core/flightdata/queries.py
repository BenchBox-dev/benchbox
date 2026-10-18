# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any, Optional

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox.core.static_query_catalog import load_static_query_catalog
from benchbox.sql_compat.local_exemptions import compat_local

QUERIES: dict[str, dict[str, Any]] = load_static_query_catalog(__package__)["QUERIES"]


class FlightDataQueryManager:
    def __init__(self, start_date: str, end_date: str) -> None:
        self.start_date = start_date
        self.end_date = end_date

    @compat_local(
        kind="rendering",
        platform_specific=True,
        reason=(
            "Renders FlightData SQL for PostgreSQL-family engines by casting ROUND inputs "
            "to DECIMAL where PostgreSQL does not accept ROUND(double precision, integer)."
        ),
    )
    def get_query(
        self,
        query_key: str,
        params: Optional[dict[str, Any]] = None,
        dialect: str | None = None,
    ) -> str:
        if query_key not in QUERIES:
            for key, qdef in QUERIES.items():
                if str(qdef.get("id")) == str(query_key):
                    query_key = key
                    break
            else:
                raise ValueError(f"Unknown query: {query_key!r}. Valid keys: {list(QUERIES.keys())}")

        query_def = QUERIES[query_key]
        sql = query_def["sql"].strip()

        effective_params = {
            "start_date": self.start_date,
            "end_date": self.end_date,
        }
        if params:
            effective_params.update(params)

        try:
            rendered = sql.format(**effective_params)
        except KeyError as exc:
            raise ValueError(
                f"Query {query_key!r} requires parameter {exc} not found in: {list(effective_params)}"
            ) from exc

        if dialect in {"postgres", "postgresql"}:
            return _render_postgres_query(rendered)
        return rendered

    def get_queries(self, dialect: str | None = None) -> dict[str, str]:
        return {key: self.get_query(key, dialect=dialect) for key in QUERIES}

    def get_query_count(self) -> int:
        return len(QUERIES)

    def get_categories(self) -> list[str]:
        return sorted(set(q["category"] for q in QUERIES.values()))

    def get_queries_by_category(self, category: str) -> list[str]:
        return [key for key, q in QUERIES.items() if q["category"] == category]

    def get_query_info(self, query_key: str) -> dict[str, Any]:
        if query_key not in QUERIES:
            raise ValueError(f"Unknown query: {query_key!r}")
        q = QUERIES[query_key]
        return {
            "id": q["id"],
            "name": q["name"],
            "description": q["description"],
            "category": q["category"],
            "key": query_key,
        }


def _render_postgres_query(sql: str) -> str:
    tree = sqlglot.parse_one(sql, read="duckdb")

    def cast_round_input(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Round):
            node = node.copy()
            node.set("this", exp.cast(node.this.copy(), "DECIMAL"))
        return node

    return tree.transform(cast_round_input).sql(dialect="postgres", identify=True)
