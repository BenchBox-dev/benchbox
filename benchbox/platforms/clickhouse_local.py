# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchbox.platforms.clickhouse import ClickHouseAdapter
from benchbox.platforms.clickhouse.client import ClickHouseLocalClient

_IN_MEMORY_REQUIRES_PATH_MESSAGE = (
    "ClickHouse Local streams and refresh sessions need a persistent database path: every stream opens its own "
    "chDB session on the same path, and an in-memory chDB database cannot be shared between sessions. "
    "Run with a database path (the default for benchmark runs)."
)
_DECIMAL_TYPE = re.compile(r"^DECIMAL\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)$", re.IGNORECASE)
STREAM_MAX_MEMORY_USAGE = "3GB"
_STREAM_SESSION_SETTINGS = {
    "join_use_nulls": 1,
    "data_type_default_nullable": 1,
    "mutations_sync": 2,
    "lightweight_deletes_sync": 2,
}


def _refresh_column_type(sql_type: str, nullable: bool) -> str:
    normalized = sql_type.strip().upper()
    decimal = _DECIMAL_TYPE.match(normalized)
    if normalized == "INTEGER":
        base = "Int64"
    elif normalized == "DATE":
        base = "Date"
    elif decimal is not None:
        base = f"Decimal({decimal.group(1)}, {decimal.group(2)})"
    else:
        base = "String"
    return f"Nullable({base})" if nullable else base


class ClickHouseLocalAdapter(ClickHouseAdapter):
    plan_capture_phase_eligible = True
    supports_refresh_functions = True
    supports_native_refresh_ingest = True
    supports_refresh_transactions = False
    reports_refresh_delete_counts = False

    def __init__(self, **config: Any) -> None:
        config["deployment_mode"] = "local"
        super().__init__(**config)
        self.deployment_selected = None
        self.deployment_selected_class = None

    @property
    def platform_name(self) -> str:
        return "ClickHouse Local"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ClickHouseLocalAdapter:
        config = dict(config)
        config["deployment_mode"] = "local"
        return super().from_config(config)

    def ensure_stream_sessions_supported(self, connection: Any = None) -> None:
        database_path = getattr(connection, "db_path", None) if connection is not None else self.get_database_path()
        if not database_path:
            raise RuntimeError(_IN_MEMORY_REQUIRES_PATH_MESSAGE)

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        self.ensure_stream_sessions_supported(connection)
        stream = ClickHouseLocalClient(db_path=connection.db_path)
        try:
            self.configure_for_benchmark(stream, benchmark_type or "olap")
            budget = self._parse_memory_setting(
                self.platform_config.get("stream_max_memory_usage", STREAM_MAX_MEMORY_USAGE)
            )
            settings = {
                **_STREAM_SESSION_SETTINGS,
                "max_memory_usage": budget,
                "max_bytes_before_external_group_by": budget // 2,
                "max_bytes_before_external_sort": budget // 2,
            }
            for setting, value in settings.items():
                stream.execute(f"SET {setting} = {value}")
        except Exception:
            stream.close()
            raise
        return stream

    def ingest_refresh_file(
        self,
        connection: Any,
        table_name: str,
        columns: Sequence[tuple[str, str]],
        path: Path,
        null_marker: str | None = None,
    ) -> int:
        nullable = null_marker is not None
        structure = [f"{name} {_refresh_column_type(sql_type, nullable)}" for name, sql_type in columns]
        if null_marker is None:
            with path.open(encoding="utf-8") as source:
                if source.readline().rstrip("\r\n").endswith("|"):
                    structure.append("_trailing_delimiter String")
        column_names = ", ".join(name for name, _ in columns)
        literal_path = str(path).replace("\\", "\\\\").replace("'", "\\'")
        settings = ["format_csv_delimiter = '|'", "input_format_csv_trim_whitespaces = 0"]
        if nullable:
            literal_null = null_marker.replace("\\", "\\\\").replace("'", "\\'")
            settings.append(f"format_csv_null_representation = '{literal_null}'")
        before = self._count_table_rows(connection, table_name)
        connection.execute(
            f"INSERT INTO {table_name} ({column_names}) SELECT {column_names} "
            f"FROM file('{literal_path}', 'CSV', '{', '.join(structure)}') SETTINGS {', '.join(settings)}"
        )
        return self._count_table_rows(connection, table_name) - before

    @staticmethod
    def _count_table_rows(connection: Any, table_name: str) -> int:
        row = connection.execute(f"SELECT count() FROM {table_name}").fetchone()
        if row is None:
            raise RuntimeError(f"ClickHouse Local did not report the row count of {table_name}")
        return int(row[0])


__all__ = ["ClickHouseLocalAdapter"]
