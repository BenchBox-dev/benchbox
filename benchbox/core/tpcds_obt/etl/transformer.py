from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchbox.core.tpcds.schema.registry import TABLES_BY_NAME
from benchbox.core.tpcds_obt.schema import (
    CHANNELS,
    DEFAULT_MODE,
    INCOME_BAND_KEY,
    INCOME_BAND_PRIMARY_KEY,
    INCOME_BAND_TABLE,
    OBT_TABLE_NAME,
    get_channels,
    get_coverage,
    get_dimension_roles,
    get_fact_specs,
    get_obt_columns,
    source_tables,
)

logger = logging.getLogger(__name__)

SUPPORTED_CHANNELS = CHANNELS
COLUMN_THREAD_BUDGET = 3000


class OBTCoverageError(RuntimeError):
    pass


class TPCDSOBTTransformer:
    def __init__(self, duckdb_module: Any | None = None) -> None:
        self._duckdb = duckdb_module

    def transform(
        self,
        tpcds_dir: Path,
        output_dir: Path,
        *,
        mode: str = "full",
        channels: Sequence[str] | None = None,
        output_format: str = "parquet",
        scale_factor: float | None = None,
    ) -> dict[str, Path]:
        mode = mode.lower()
        channel_list = [c.lower() for c in (channels or SUPPORTED_CHANNELS)]
        self._validate_channels(channel_list)

        columns = get_obt_columns(mode)

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{OBT_TABLE_NAME}.{output_format}"
        manifest_path = output_dir / f"{OBT_TABLE_NAME}_manifest.json"

        tmp_dir = tempfile.mkdtemp(prefix="benchbox_obt_")
        tmp_db_path = Path(tmp_dir) / "obt.duckdb"

        conn = self._connect(tmp_db_path, len(columns))
        try:
            self._load_source_tables(conn, tpcds_dir, channel_list, mode)
            for statement in self._build_statements(columns, channel_list, mode):
                conn.execute(statement)
            coverage = self._check_coverage(conn, channel_list, mode, scale_factor)

            self._export_table(conn, output_path, output_format)
            manifest = self._build_manifest(
                conn,
                output_path,
                mode=mode,
                channels=channel_list,
                scale_factor=scale_factor,
                column_count=len(columns),
                output_format=output_format,
                coverage=coverage,
            )
            manifest_path.write_text(json.dumps(manifest, indent=2))

            logger.info(
                "Generated %s with %s rows (%s format)",
                OBT_TABLE_NAME,
                manifest["rows_total"],
                output_format,
            )

            return {"table": output_path, "manifest": manifest_path}
        finally:
            conn.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _connect(self, db_path: Path | None = None, column_count: int = 1) -> Any:
        target = str(db_path) if db_path else ":memory:"
        if self._duckdb is None:
            import duckdb

            conn = duckdb.connect(target)
        else:
            conn = self._duckdb.connect(target)

        conn.execute("SET preserve_insertion_order = false")
        conn.execute(f"SET threads = {self._thread_limit(column_count)}")
        return conn

    @staticmethod
    def _thread_limit(column_count: int) -> int:
        return max(1, min(os.cpu_count() or 1, COLUMN_THREAD_BUDGET // max(column_count, 1)))

    def _validate_channels(self, channels: Sequence[str]) -> None:
        invalid = [c for c in channels if c not in SUPPORTED_CHANNELS]
        if invalid:
            raise ValueError(f"Unsupported channels: {invalid}. Supported: {SUPPORTED_CHANNELS}")

    def _load_source_tables(
        self, conn: Any, tpcds_dir: Path, channels: Sequence[str], mode: str = DEFAULT_MODE
    ) -> None:
        for table_name in sorted(source_tables(channels, mode)):
            path = self._resolve_source_path(tpcds_dir, table_name)
            columns = [col.name for col in TABLES_BY_NAME[table_name].columns]
            conn.execute(self._read_csv_sql(path, table_name, columns))

    def _check_coverage(
        self, conn: Any, channels: Sequence[str], mode: str, scale_factor: float | None
    ) -> dict[str, Any]:
        coverage = get_coverage()
        missing_dates = int(
            conn.execute(
                f"SELECT COUNT(*) FROM date_dim d WHERE d.d_date BETWEEN DATE '{coverage.sold_date_from}' "
                f"AND DATE '{coverage.sold_date_to}' "
                f"AND NOT EXISTS (SELECT 1 FROM {OBT_TABLE_NAME} o WHERE o.sold_date_sk = d.d_date_sk)"
            ).fetchone()[0]
        )
        missing_items = int(
            conn.execute(
                f"SELECT COUNT(*) FROM item i "
                f"WHERE NOT EXISTS (SELECT 1 FROM {OBT_TABLE_NAME} o WHERE o.item_sk = i.i_item_sk)"
            ).fetchone()[0]
        )
        enforced = (
            set(channels) == set(SUPPORTED_CHANNELS)
            and mode == DEFAULT_MODE
            and (scale_factor is None or scale_factor >= 1.0)
        )
        report = {
            "sold_date_range": [str(coverage.sold_date_from), str(coverage.sold_date_to)],
            "missing_sold_dates": missing_dates,
            "missing_items": missing_items if coverage.every_item_sold else 0,
            "enforced": enforced,
        }
        if enforced and (report["missing_sold_dates"] or report["missing_items"]):
            raise OBTCoverageError(
                f"{OBT_TABLE_NAME} breaks the coverage the TPC-DS OBT queries rely on: "
                f"{report['missing_sold_dates']} calendar days between {coverage.sold_date_from} and "
                f"{coverage.sold_date_to} have no sale and {report['missing_items']} items never appear"
            )
        return report

    def _resolve_source_path(self, base_dir: Path, table_name: str) -> Path | list[str]:
        candidates = [
            base_dir / f"{table_name}.dat",
            base_dir / f"{table_name}.dat.zst",
            base_dir / f"{table_name}.dat.gz",
            base_dir / f"{table_name}.tbl",
            base_dir / f"{table_name}.tbl.zst",
            base_dir / f"{table_name}.tbl.gz",
            base_dir / table_name,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate

        for extension in [".dat", ".dat.zst", ".dat.gz"]:
            parallel_files = self._find_parallel_files(base_dir, table_name, extension)
            if parallel_files:
                file_list = [str(f) for f in sorted(parallel_files)]
                return file_list

        raise FileNotFoundError(f"Source file for {table_name} not found in {base_dir}")

    def _find_parallel_files(self, base_dir: Path, table_name: str, extension: str) -> list[Path]:
        pattern = re.compile(rf"^{re.escape(table_name)}_(\d+)_(\d+){re.escape(extension)}$")

        matching_files: list[Path] = []
        for candidate in base_dir.iterdir():
            if candidate.is_file() and pattern.match(candidate.name):
                matching_files.append(candidate)

        return matching_files

    def _read_csv_sql(self, path: Path | str | list[str], table_name: str, columns: list[str]) -> str:
        col_list = ", ".join(f"'{c}'" for c in columns)

        if isinstance(path, list):
            file_list_str = ", ".join(f"'{f}'" for f in path)
            path_arg = f"[{file_list_str}]"
        else:
            path_arg = f"'{path}'"

        return f"""
            CREATE OR REPLACE TABLE {table_name} AS
            SELECT * FROM read_csv(
                {path_arg},
                delim='|',
                header=false,
                names=[{col_list}],
                ignore_errors=false
            );
        """

    def _build_statements(self, columns: Sequence[Any], channels: Sequence[str], mode: str = DEFAULT_MODE) -> list[str]:
        selects = [self._channel_select(channel, columns, mode) for channel in channels]
        return [
            f"CREATE OR REPLACE TABLE {OBT_TABLE_NAME} AS\n{selects[0]}",
            *(f"INSERT INTO {OBT_TABLE_NAME}\n{select}" for select in selects[1:]),
        ]

    def _channel_select(self, channel: str, columns: Sequence[Any], mode: str = DEFAULT_MODE) -> str:
        spec = get_channels()[channel]
        fact_sources = {fact.column.name: fact.sources for fact in get_fact_specs()}
        aliases, income_band_aliases, joins = self._channel_plan(channel, mode)

        select_parts: list[str] = []
        for col in columns:
            if col.role == "fact":
                expr = fact_sources[col.name].get(channel)
            elif col.source_table == INCOME_BAND_TABLE:
                alias = income_band_aliases.get(col.role or "")
                expr = f"{alias}.{(col.source_column or '').lower()}" if alias else None
            else:
                alias = aliases.get(col.role or "")
                expr = f"{alias}.{(col.source_column or '').lower()}" if alias else None
            select_parts.append(f"{self._cast_expr(expr, col) if expr else self._null_expr(col)} AS {col.name}")

        select_sql = ",\n    ".join(select_parts)
        return f"SELECT\n    {select_sql}\nFROM {spec.sales_table} {spec.sales_alias}\n    " + "\n    ".join(joins)

    def _channel_plan(self, channel: str, mode: str) -> tuple[dict[str, str], dict[str, str], list[str]]:
        spec = get_channels()[channel]
        joins = [f"LEFT JOIN {spec.returns_table} {spec.returns_alias} ON {spec.returns_join}"]
        aliases: dict[str, str] = {}
        income_band_aliases: dict[str, str] = {}
        joined: dict[tuple[str, str], str] = {}

        def join(table: str, key: str, primary_key: str, preferred_alias: str) -> str:
            alias = joined.get((table, key))
            if alias is None:
                alias = preferred_alias
                joined[(table, key)] = alias
                joins.append(f"LEFT JOIN {table} {alias} ON {key} = {alias}.{primary_key}")
            return alias

        for role in get_dimension_roles(mode):
            if role.via_role is not None:
                parent = aliases.get(role.via_role)
                key = f"{parent}.{role.via_column}" if parent else None
            else:
                key = (role.keys or {}).get(channel)
            if key is None:
                continue
            aliases[role.name] = join(role.table.name, key, role.primary_key, f"j_{role.name}")
            if role.income_band:
                income_band_aliases[role.name] = join(
                    INCOME_BAND_TABLE,
                    f"{aliases[role.name]}.{INCOME_BAND_KEY}",
                    INCOME_BAND_PRIMARY_KEY,
                    f"ib_{role.name}",
                )
        return aliases, income_band_aliases, joins

    def _cast_expr(self, expr: str, column: Any) -> str:
        return f"CAST({expr} AS {column.sql_type()})"

    def _null_expr(self, column: Any) -> str:
        return f"CAST(NULL AS {column.sql_type()})"

    def _export_table(self, conn: Any, output_path: Path, output_format: str) -> None:
        if output_format == "parquet":
            conn.execute(f"COPY {OBT_TABLE_NAME} TO '{output_path}' (FORMAT PARQUET)")
        elif output_format == "dat":
            conn.execute(f"COPY {OBT_TABLE_NAME} TO '{output_path}' (DELIMITER '|', HEADER FALSE, NULL '')")
        else:
            raise ValueError(f"Unsupported output_format: {output_format}")

    def _build_manifest(
        self,
        conn: Any,
        output_path: Path,
        *,
        mode: str,
        channels: Sequence[str],
        scale_factor: float | None,
        column_count: int,
        output_format: str,
        coverage: dict[str, Any],
    ) -> dict[str, Any]:
        rows_total = int(conn.execute(f"SELECT COUNT(*) FROM {OBT_TABLE_NAME}").fetchone()[0])
        rows_by_channel = dict(
            conn.execute(f"SELECT channel, COUNT(*) FROM {OBT_TABLE_NAME} GROUP BY channel").fetchall()
        )
        return_rows = int(conn.execute(f"SELECT COUNT(*) FROM {OBT_TABLE_NAME} WHERE has_return = 'Y'").fetchone()[0])

        file_size = output_path.stat().st_size if output_path.exists() else 0

        return {
            "table": OBT_TABLE_NAME,
            "mode": mode,
            "channels": list(channels),
            "column_count": column_count,
            "rows_total": rows_total,
            "rows_by_channel": rows_by_channel,
            "rows_with_returns": return_rows,
            "output_file": str(output_path.name),
            "output_format": output_format,
            "file_size_bytes": file_size,
            "scale_factor": scale_factor,
            "coverage": coverage,
        }
