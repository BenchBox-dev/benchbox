# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from benchbox.core.tuning.generators.postgresql import PostgreSQLDDLGenerator


class PgDuckDBDDLGenerator(PostgreSQLDDLGenerator):
    @property
    def platform_name(self) -> str:
        return "pg_duckdb"


__all__ = [
    "PgDuckDBDDLGenerator",
]
