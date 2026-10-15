# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from typing import Any

from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.introspection import (
    KIND_PARTITION_KEY,
    KIND_SORT_KEY,
    IntrospectedObject,
    IntrospectedState,
    ledger_tables,
    normalize_columns,
    normalize_identifier,
)

logger = logging.getLogger(__name__)

_MAX_TABLE_ROWS = 1000


class ClickHouseTuningIntrospector:
    platform = "clickhouse"

    def introspect(self, connection: Any, ledger: AppliedTuningLedger) -> IntrospectedState:
        tables = ledger_tables(ledger)
        table_filter = ""
        if tables:
            escaped_tables = ", ".join(
                f"'{clean_name}'" for clean_name in (normalize_identifier(t).replace("'", "''") for t in sorted(tables))
            )
            table_filter = f" AND lower(name) IN ({escaped_tables})"
        try:
            result = connection.execute(
                "SELECT name, sorting_key, partition_key FROM system.tables "
                f"WHERE database = currentDatabase(){table_filter} LIMIT {_MAX_TABLE_ROWS}"
            )
            rows = list(result) if result is not None else []
        except Exception as exc:
            logger.debug("clickhouse table introspection degraded: %s", exc)
            return IntrospectedState(platform=self.platform, error=f"system.tables read failed: {exc}")

        truncated = len(rows) >= _MAX_TABLE_ROWS
        relevant_rows = [row for row in rows if not tables or (row and normalize_identifier(row[0] or "") in tables)]
        objects: list[IntrospectedObject] = []
        for row in relevant_rows:
            try:
                name, sorting_key, partition_key = row[0], row[1], row[2]
            except Exception:  # pragma: no cover
                continue
            if sorting_key:
                objects.append(
                    IntrospectedObject(
                        kind=KIND_SORT_KEY,
                        table=name,
                        columns=normalize_columns(sorting_key),
                        evidence={"sorting_key": sorting_key},
                    )
                )
            if partition_key:
                objects.append(
                    IntrospectedObject(
                        kind=KIND_PARTITION_KEY,
                        table=name,
                        columns=normalize_columns(partition_key),
                        evidence={"partition_key": partition_key},
                    )
                )
        return IntrospectedState(platform=self.platform, objects=objects, truncated=truncated)


__all__ = ["ClickHouseTuningIntrospector"]
