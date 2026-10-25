# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import re
from typing import Any

from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.introspection import (
    KIND_CLUSTER_KEY,
    IntrospectedObject,
    IntrospectedState,
    ledger_tables,
    normalize_columns,
    normalize_identifier,
)

logger = logging.getLogger(__name__)

_MAX_TABLE_ROWS = 1000

_CLUSTERING_KEY_RE = re.compile(r"^\s*(?:linear\s*)?\((?P<cols>.*)\)\s*$", re.IGNORECASE | re.DOTALL)
_UNQUOTED_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def parse_clustering_key(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    text = str(raw).strip()
    if not text:
        return ()
    match = _CLUSTERING_KEY_RE.match(text)
    if match:
        text = match.group("cols")
    return normalize_columns(text)


def normalize_snowflake_schema(raw: Any) -> str:
    text = str(raw).strip()
    if not _UNQUOTED_IDENTIFIER_RE.fullmatch(text):
        raise ValueError("Snowflake schema must be a valid unquoted identifier")
    return text.upper()


class SnowflakeTuningIntrospector:
    platform = "snowflake"

    def __init__(self, schema: str | None = None) -> None:
        self._schema = schema

    def introspect(self, connection: Any, ledger: AppliedTuningLedger) -> IntrospectedState:
        tables = ledger_tables(ledger)
        table_params = tuple(sorted(table.upper() for table in tables))
        table_filter = ""
        if table_params:
            placeholders = ", ".join("%s" for _table in table_params)
            table_filter = f" AND UPPER(TABLE_NAME) IN ({placeholders})"
        try:
            query = (
                "SELECT TABLE_NAME, CLUSTERING_KEY FROM INFORMATION_SCHEMA.TABLES "
                f"WHERE CLUSTERING_KEY IS NOT NULL{table_filter} LIMIT {_MAX_TABLE_ROWS}"
            )
            params: tuple[Any, ...] = table_params
            if self._schema:
                query = (
                    "SELECT TABLE_NAME, CLUSTERING_KEY FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND CLUSTERING_KEY IS NOT NULL"
                    f"{table_filter} "
                    f"LIMIT {_MAX_TABLE_ROWS}"
                )
                params = (normalize_snowflake_schema(self._schema), *table_params)
            rows = _fetch(connection, query, params)
        except Exception as exc:
            logger.debug("snowflake clustering introspection degraded: %s", exc)
            return IntrospectedState(platform=self.platform, error=f"INFORMATION_SCHEMA read failed: {exc}")

        relevant_rows = [row for row in rows if not tables or (row and normalize_identifier(row[0] or "") in tables)]
        truncated = len(relevant_rows) >= _MAX_TABLE_ROWS
        objects: list[IntrospectedObject] = []
        for row in relevant_rows:
            try:
                name, clustering_key = row[0], row[1]
            except Exception:
                continue
            columns = parse_clustering_key(clustering_key)
            if not columns:
                continue
            objects.append(
                IntrospectedObject(
                    kind=KIND_CLUSTER_KEY,
                    table=name,
                    columns=columns,
                    evidence={"clustering_key": str(clustering_key)},
                )
            )
        return IntrospectedState(platform=self.platform, objects=objects, truncated=truncated)


def _fetch(connection: Any, query: str, params: tuple[Any, ...] = ()) -> list[Any]:
    cursor_factory = getattr(connection, "cursor", None)
    if callable(cursor_factory):
        cursor = cursor_factory()
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            return list(cursor.fetchall() or [])
        finally:
            close = getattr(cursor, "close", None)
            if callable(close):
                close()
    result = connection.execute(query, params) if params else connection.execute(query)
    return list(result) if result is not None else []


__all__ = ["SnowflakeTuningIntrospector", "normalize_snowflake_schema", "parse_clustering_key"]
