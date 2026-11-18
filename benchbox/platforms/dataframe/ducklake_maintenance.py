# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

duckdb: Any = None
DUCKDB_AVAILABLE = importlib.util.find_spec("duckdb") is not None

try:
    import pyarrow as pa

    PYARROW_AVAILABLE = True
except ImportError:
    pa = None
    PYARROW_AVAILABLE = False

from benchbox.core.dataframe.maintenance_interface import (
    BaseDataFrameMaintenanceOperations,
    DataFrameMaintenanceCapabilities,
    TransactionIsolation,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


DUCKLAKE_CAPABILITIES = DataFrameMaintenanceCapabilities(
    platform_name="ducklake",
    supports_insert=True,
    supports_delete=True,
    supports_update=True,
    supports_merge=True,
    supports_transactions=True,
    transaction_isolation=TransactionIsolation.SNAPSHOT,
    supports_partitioned_delete=True,
    supports_row_level_delete=True,
    supports_time_travel=True,
    max_batch_size=1000000,
    notes="Full ACID compliance via DuckLake (DuckDB's native table format)",
)


class DuckLakeMaintenanceOperations(BaseDataFrameMaintenanceOperations):
    def __init__(self, working_dir: str | Path | None = None) -> None:
        super().__init__()

        if not DUCKDB_AVAILABLE:
            raise ImportError(
                "DuckDB is not installed. Install with: pip install duckdb\n"
                "For TPC-H/TPC-DS maintenance tests with DuckLake, install:\n"
                "pip install 'benchbox[duckdb]'"
            )

        if not PYARROW_AVAILABLE:
            raise ImportError(
                "PyArrow is not installed. Install with: pip install pyarrow\n"
                "PyArrow is required for DuckLake operations."
            )

        self.working_dir = Path(working_dir) if working_dir else None
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._ducklake_loaded = False

    def _get_capabilities(self) -> DataFrameMaintenanceCapabilities:
        return DUCKLAKE_CAPABILITIES

    def _get_connection(self, table_path: Path | str) -> Any:
        table_path = Path(table_path)

        global duckdb
        if duckdb is None:
            duckdb = importlib.import_module("duckdb")
        conn = duckdb.connect(":memory:")

        try:
            conn.execute("INSTALL ducklake")
            conn.execute("LOAD ducklake")
        except Exception as e:
            conn.close()
            raise RuntimeError(f"Failed to load DuckLake extension: {e}") from e

        metadata_path = table_path / "metadata.ducklake"
        data_path = table_path / "data"

        if not metadata_path.exists():
            conn.close()
            raise RuntimeError(f"DuckLake metadata not found at {metadata_path}")

        try:
            conn.execute(f"ATTACH 'ducklake:{metadata_path}' AS ducklake_db (DATA_PATH '{data_path}')")
        except Exception as e:
            conn.close()
            raise RuntimeError(f"Failed to attach DuckLake catalog: {e}") from e

        return conn

    def _get_table_name_from_path(self, table_path: Path | str) -> str:
        return Path(table_path).name

    def _do_insert(
        self,
        table_path: Path | str,
        dataframe: Any,
        partition_columns: list[str] | None,
        mode: str,
    ) -> int:
        table_path = Path(table_path)
        table_name = self._get_table_name_from_path(table_path)

        arrow_table = self._convert_to_arrow(dataframe)
        row_count = arrow_table.num_rows

        if row_count == 0:
            self.logger.info("No rows to insert")
            return 0

        conn = self._get_connection(table_path)
        try:
            conn.register("source_data", arrow_table)

            if mode == "overwrite":
                conn.execute(f"DELETE FROM ducklake_db.main.{table_name}")

            conn.execute(f"INSERT INTO ducklake_db.main.{table_name} SELECT * FROM source_data")

            self.logger.info(f"Inserted {row_count} rows to DuckLake table at {table_path}")
            return row_count

        finally:
            conn.close()

    def _do_delete(
        self,
        table_path: Path | str,
        condition: str | Any,
    ) -> int:
        table_path = Path(table_path)
        table_name = self._get_table_name_from_path(table_path)

        conn = self._get_connection(table_path)
        try:
            result = conn.execute(f"SELECT COUNT(*) FROM ducklake_db.main.{table_name}").fetchone()
            rows_before = result[0] if result else 0

            conn.execute(f"DELETE FROM ducklake_db.main.{table_name} WHERE {condition}")

            result = conn.execute(f"SELECT COUNT(*) FROM ducklake_db.main.{table_name}").fetchone()
            rows_after = result[0] if result else 0

            rows_deleted = rows_before - rows_after
            self.logger.info(f"Deleted {rows_deleted} rows from DuckLake table at {table_path}")
            return rows_deleted

        finally:
            conn.close()

    def _do_update(
        self,
        table_path: Path | str,
        condition: str | Any,
        updates: dict[str, Any],
    ) -> int:
        table_path = Path(table_path)
        table_name = self._get_table_name_from_path(table_path)

        conn = self._get_connection(table_path)
        try:
            set_clauses = ", ".join([f"{col} = {val}" for col, val in updates.items()])

            result = conn.execute(f"SELECT COUNT(*) FROM ducklake_db.main.{table_name} WHERE {condition}").fetchone()
            matching_rows = result[0] if result else 0

            conn.execute(f"UPDATE ducklake_db.main.{table_name} SET {set_clauses} WHERE {condition}")

            self.logger.info(f"Updated {matching_rows} rows in DuckLake table at {table_path}")
            return matching_rows

        finally:
            conn.close()

    def _do_merge(
        self,
        table_path: Path | str,
        source_dataframe: Any,
        merge_condition: str | Any,
        when_matched: dict[str, Any] | None,
        when_not_matched: dict[str, Any] | None,
    ) -> int:
        table_path = Path(table_path)
        table_name = self._get_table_name_from_path(table_path)

        source_arrow = self._convert_to_arrow(source_dataframe)
        source_rows = source_arrow.num_rows

        conn = self._get_connection(table_path)
        try:
            conn.register("source_data", source_arrow)

            merge_sql = f"MERGE INTO ducklake_db.main.{table_name} AS target\n"
            merge_sql += "USING source_data AS source\n"
            merge_sql += f"ON {merge_condition}\n"

            if when_matched:
                set_clauses = ", ".join([f"{col} = {val}" for col, val in when_matched.items()])
                merge_sql += f"WHEN MATCHED THEN UPDATE SET {set_clauses}\n"

            if when_not_matched:
                columns = ", ".join(when_not_matched.keys())
                values = ", ".join([str(v) for v in when_not_matched.values()])
                merge_sql += f"WHEN NOT MATCHED THEN INSERT ({columns}) VALUES ({values})"

            conn.execute(merge_sql)

            self.logger.info(f"Merged {source_rows} source rows into DuckLake table at {table_path}")
            return source_rows

        finally:
            conn.close()


def get_ducklake_maintenance_operations(
    working_dir: str | Path | None = None,
) -> DuckLakeMaintenanceOperations | None:
    if not DUCKDB_AVAILABLE:
        logger.debug("DuckLake maintenance not available (duckdb not installed)")
        return None

    if not PYARROW_AVAILABLE:
        logger.debug("DuckLake maintenance not available (pyarrow not installed)")
        return None

    return DuckLakeMaintenanceOperations(working_dir=working_dir)
