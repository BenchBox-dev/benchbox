# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from typing import Any

from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.introspection import (
    CONSTRAINT_FOREIGN_KEY,
    CONSTRAINT_PRIMARY_KEY,
    CONSTRAINT_UNIQUE,
    KIND_CONSTRAINT,
    KIND_INDEX,
    IntrospectedObject,
    IntrospectedState,
    ledger_tables,
    normalize_columns,
    normalize_identifier,
)

logger = logging.getLogger(__name__)

_MAX_INDEX_ROWS = 1000
_MAX_CONSTRAINT_ROWS = 1000
_CATALOG_CONSTRAINT_TYPES = frozenset({CONSTRAINT_PRIMARY_KEY, CONSTRAINT_UNIQUE, CONSTRAINT_FOREIGN_KEY})
_REQUIRED_CONSTRAINT_COLUMNS = ("table_name", "constraint_type", "constraint_column_names")


class DuckDBTuningIntrospector:
    platform = "duckdb"

    def introspect(self, connection: Any, ledger: AppliedTuningLedger) -> IntrospectedState:
        tables = ledger_tables(ledger)
        try:
            cursor = connection.execute(
                "SELECT table_name, index_name, expressions, is_unique, is_primary "
                f"FROM duckdb_indexes() LIMIT {_MAX_INDEX_ROWS}"
            )
            rows = cursor.fetchall()
        except Exception as exc:
            logger.debug("duckdb index introspection degraded: %s", exc)
            return IntrospectedState(platform=self.platform, error=f"duckdb_indexes read failed: {exc}")

        truncated = len(rows) >= _MAX_INDEX_ROWS
        objects: list[IntrospectedObject] = []
        for row in rows:
            try:
                table_name, index_name, expressions, is_unique, is_primary = (
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                )
            except Exception:  # pragma: no cover
                continue
            if tables and normalize_identifier(table_name or "") not in tables:
                continue
            objects.append(
                IntrospectedObject(
                    kind=KIND_INDEX,
                    table=table_name,
                    columns=normalize_columns(expressions),
                    name=index_name,
                    evidence={
                        "index_name": index_name,
                        "expressions": expressions,
                        "is_unique": bool(is_unique),
                        "is_primary": bool(is_primary),
                    },
                )
            )

        try:
            constraints, constraints_truncated = _read_constraints(connection, tables)
        except Exception as exc:
            logger.debug("duckdb constraint introspection degraded: %s", exc)
            return IntrospectedState(platform=self.platform, error=f"duckdb_constraints read failed: {exc}")
        objects.extend(constraints)
        return IntrospectedState(
            platform=self.platform,
            objects=objects,
            truncated=truncated or constraints_truncated,
            constraint_types=_CATALOG_CONSTRAINT_TYPES,
        )


def _read_constraints(connection: Any, tables: set[str]) -> tuple[list[IntrospectedObject], bool]:
    if not tables:
        return [], False
    ordered_tables = sorted(tables)
    placeholders = ", ".join("?" for _ in ordered_tables)
    type_list = ", ".join(f"'{constraint_type}'" for constraint_type in sorted(_CATALOG_CONSTRAINT_TYPES))
    cursor = connection.execute(
        "SELECT * FROM duckdb_constraints() "
        f"WHERE constraint_type IN ({type_list}) "
        "AND database_name = current_database() AND schema_name = current_schema() "
        f"AND lower(table_name) IN ({placeholders}) "
        f"LIMIT {_MAX_CONSTRAINT_ROWS}",
        ordered_tables,
    )
    names = [str(description[0]).lower() for description in cursor.description or ()]
    missing = [column for column in _REQUIRED_CONSTRAINT_COLUMNS if column not in names]
    if missing:
        raise ValueError(f"duckdb_constraints() lacks columns {missing}")
    rows = cursor.fetchall()
    objects: list[IntrospectedObject] = []
    for row in rows:
        fact = dict(zip(names, row, strict=False))
        table_name = fact["table_name"]
        if normalize_identifier(table_name or "") not in tables:
            continue
        constraint_type = str(fact["constraint_type"] or "").strip().upper()
        if constraint_type not in _CATALOG_CONSTRAINT_TYPES:
            continue
        referenced_table = fact.get("referenced_table") or None
        referenced_columns = normalize_columns(fact.get("referenced_column_names"))
        objects.append(
            IntrospectedObject(
                kind=KIND_CONSTRAINT,
                table=table_name,
                columns=normalize_columns(fact["constraint_column_names"]),
                name=fact.get("constraint_name"),
                constraint_type=constraint_type,
                referenced_table=referenced_table,
                referenced_columns=referenced_columns,
                evidence={
                    "constraint_name": fact.get("constraint_name"),
                    "constraint_type": constraint_type,
                    "constraint_column_names": list(fact["constraint_column_names"] or []),
                    "referenced_table": referenced_table,
                    "referenced_column_names": list(fact.get("referenced_column_names") or []),
                },
            )
        )
    return objects, len(rows) >= _MAX_CONSTRAINT_ROWS


__all__ = ["DuckDBTuningIntrospector"]
