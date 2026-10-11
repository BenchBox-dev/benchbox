# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import polars as pl

    POLARS_AVAILABLE = True
except ImportError:
    pl = None
    POLARS_AVAILABLE = False

from benchbox.core.dataframe.maintenance_interface import (
    POLARS_CAPABILITIES,
    BaseDataFrameMaintenanceOperations,
    DataFrameMaintenanceCapabilities,
)
from benchbox.utils.polars_collection import collect_frame

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class PolarsMaintenanceOperations(BaseDataFrameMaintenanceOperations):
    def __init__(self, working_dir: str | Path | None = None, execution_engine: str = "default") -> None:
        super().__init__()

        if not POLARS_AVAILABLE:
            raise ImportError(
                "Polars is not installed. Install with: pip install polars\n"
                "For TPC-H/TPC-DS maintenance tests, install the full package:\n"
                "pip install 'benchbox[dataframe]'"
            )

        self.working_dir = Path(working_dir) if working_dir else None
        self.execution_engine = execution_engine
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def _collect(self, frame: Any) -> Any:
        return collect_frame(frame, self.execution_engine)

    def _get_capabilities(self) -> DataFrameMaintenanceCapabilities:
        return POLARS_CAPABILITIES

    def _do_insert(
        self,
        table_path: Path | str,
        dataframe: Any,
        partition_columns: list[str] | None,
        mode: str,
    ) -> int:
        table_path = Path(table_path)

        if isinstance(dataframe, pl.LazyFrame):
            df = self._collect(dataframe)
        elif isinstance(dataframe, pl.DataFrame):
            df = dataframe
        elif hasattr(dataframe, "to_parquet") and hasattr(dataframe, "columns"):
            df = pl.from_pandas(dataframe)
        else:
            raise TypeError(f"Expected Polars DataFrame, LazyFrame, or Pandas DataFrame, got {type(dataframe)}")

        row_count = df.height

        if row_count == 0:
            self.logger.info("No rows to insert")
            return 0

        table_path.mkdir(parents=True, exist_ok=True)

        if mode == "overwrite":
            for f in table_path.glob("*.parquet"):
                f.unlink()

        if partition_columns:
            self._write_partitioned(df, table_path, partition_columns, mode)
        else:
            self._write_single(df, table_path, mode)

        self.logger.info(f"Inserted {row_count} rows to {table_path}")
        return row_count

    def _write_single(self, df: Any, table_path: Path, mode: str) -> None:
        if mode == "append":
            existing = list(table_path.glob("part-*.parquet"))
            part_num = len(existing)
            file_path = table_path / f"part-{part_num:05d}.parquet"
        else:
            file_path = table_path / "part-00000.parquet"

        df.write_parquet(file_path)
        self.logger.debug(f"Wrote {df.height} rows to {file_path}")

    def _write_partitioned(
        self,
        df: Any,
        table_path: Path,
        partition_columns: list[str],
        mode: str,
    ) -> None:
        for partition_vals, partition_df in df.group_by(partition_columns):
            if isinstance(partition_vals, tuple):
                parts = zip(partition_columns, partition_vals)
            else:
                parts = [(partition_columns[0], partition_vals)]

            partition_path = table_path
            for col, val in parts:
                partition_path = partition_path / f"{col}={val}"

            partition_path.mkdir(parents=True, exist_ok=True)

            self._write_single(partition_df, partition_path, mode)

    def _do_delete(
        self,
        table_path: Path | str,
        condition: str | Any,
    ) -> int:
        table_path = Path(table_path)

        if not table_path.exists():
            self.logger.warning(f"Table path does not exist: {table_path}")
            return 0

        parquet_files = list(table_path.glob("**/*.parquet"))
        if not parquet_files:
            self.logger.warning(f"No Parquet files found in {table_path}")
            return 0

        self.logger.debug(f"Reading {len(parquet_files)} files from {table_path}")
        df = self._collect(pl.scan_parquet(parquet_files))
        original_count = df.height

        if original_count == 0:
            return 0

        try:
            df_with_ctx = pl.SQLContext(register_globals=True)
            df_with_ctx.register("__table__", df)

            delete_query = f"SELECT COUNT(*) as cnt FROM __table__ WHERE {condition}"
            delete_count_df = self._collect(df_with_ctx.execute(delete_query))
            delete_count = delete_count_df["cnt"][0]

            if delete_count == 0:
                self.logger.info("No rows match delete condition")
                return 0

            keep_query = f"SELECT * FROM __table__ WHERE NOT ({condition})"
            remaining_df = self._collect(df_with_ctx.execute(keep_query))

        except Exception as e:
            self.logger.warning(f"SQL condition parsing failed: {e}. Trying expression parse.")
            raise ValueError(
                f"Failed to parse delete condition: {condition}\n"
                f'Use SQL-like syntax: "column > value" or "column = \'value\'"\n'
                f"Error: {e}"
            ) from e

        rows_deleted = original_count - remaining_df.height

        if rows_deleted > 0:
            backup_path = table_path.parent / f"{table_path.name}_backup_{os.getpid()}"
            try:
                shutil.move(str(table_path), str(backup_path))

                table_path.mkdir(parents=True, exist_ok=True)
                if remaining_df.height > 0:
                    remaining_df.write_parquet(table_path / "part-00000.parquet")

                shutil.rmtree(backup_path)

            except Exception as e:
                if backup_path.exists():
                    shutil.rmtree(table_path, ignore_errors=True)
                    shutil.move(str(backup_path), str(table_path))
                raise RuntimeError(f"Delete failed, restored from backup: {e}") from e

        self.logger.info(f"Deleted {rows_deleted} rows from {table_path}")
        return rows_deleted

    def _do_update(
        self,
        table_path: Path | str,
        condition: str | Any,
        updates: dict[str, Any],
    ) -> int:
        table_path = Path(table_path)

        if not table_path.exists():
            self.logger.warning(f"Table path does not exist: {table_path}")
            return 0

        parquet_files = list(table_path.glob("**/*.parquet"))
        if not parquet_files:
            self.logger.warning(f"No Parquet files found in {table_path}")
            return 0

        df = self._collect(pl.scan_parquet(parquet_files))
        original_count = df.height

        if original_count == 0:
            return 0

        try:
            df_with_ctx = pl.SQLContext(register_globals=True)
            df_with_ctx.register("__table__", df)

            count_query = f"SELECT COUNT(*) as cnt FROM __table__ WHERE {condition}"
            match_count = self._collect(df_with_ctx.execute(count_query))["cnt"][0]

            if match_count == 0:
                self.logger.info("No rows match update condition")
                return 0

            select_cols = []
            for col_name in df.columns:
                if col_name in updates:
                    value = updates[col_name]
                    select_cols.append(f"CASE WHEN ({condition}) THEN {value} ELSE {col_name} END AS {col_name}")
                else:
                    select_cols.append(col_name)

            update_query = f"SELECT {', '.join(select_cols)} FROM __table__"
            updated_df = self._collect(df_with_ctx.execute(update_query))

        except Exception as e:
            raise ValueError(
                f"Failed to parse update condition or values: {condition}\n"
                f"Updates: {updates}\n"
                f"Use SQL-like syntax for conditions and values.\n"
                f"Error: {e}"
            ) from e

        self._atomic_rewrite(table_path, updated_df)

        self.logger.info(f"Updated {match_count} rows in {table_path}")
        return match_count

    def _do_merge(
        self,
        table_path: Path | str,
        source_dataframe: Any,
        merge_condition: str | Any,
        when_matched: dict[str, Any] | None,
        when_not_matched: dict[str, Any] | None,
    ) -> int:
        table_path = Path(table_path)

        if isinstance(source_dataframe, pl.LazyFrame):
            source_df = self._collect(source_dataframe)
        elif isinstance(source_dataframe, pl.DataFrame):
            source_df = source_dataframe
        elif hasattr(source_dataframe, "to_parquet") and hasattr(source_dataframe, "columns"):
            source_df = pl.from_pandas(source_dataframe)
        else:
            raise TypeError(f"Expected Polars DataFrame, LazyFrame, or Pandas DataFrame, got {type(source_dataframe)}")

        source_count = source_df.height
        if source_count == 0:
            self.logger.info("Source DataFrame is empty, nothing to merge")
            return 0

        if not table_path.exists():
            self.logger.info(f"Target table doesn't exist, inserting {source_count} rows")
            return self._do_insert(table_path, source_df, None, "append")

        parquet_files = list(table_path.glob("**/*.parquet"))
        if not parquet_files:
            return self._do_insert(table_path, source_df, None, "append")

        target_df = self._collect(pl.scan_parquet(parquet_files))

        merge_key = self._parse_merge_key(str(merge_condition))

        rows_updated = 0
        rows_inserted = 0

        source_keys = set(source_df[merge_key].to_list())
        target_keys = set(target_df[merge_key].to_list())

        matching_keys = source_keys & target_keys
        new_keys = source_keys - target_keys

        if when_matched and matching_keys:
            for key_val in matching_keys:
                source_row = source_df.filter(pl.col(merge_key) == key_val)

                for col, expr in when_matched.items():
                    if isinstance(expr, str) and expr.startswith("source."):
                        source_col = expr[7:]
                        new_value = source_row[source_col][0]
                        target_df = target_df.with_columns(
                            pl.when(pl.col(merge_key) == key_val)
                            .then(pl.lit(new_value))
                            .otherwise(pl.col(col))
                            .alias(col)
                        )
                rows_updated += 1

        if when_not_matched and new_keys:
            new_rows_df = source_df.filter(pl.col(merge_key).is_in(list(new_keys)))
            rows_inserted = new_rows_df.height

            for col in target_df.columns:
                if col not in new_rows_df.columns:
                    new_rows_df = new_rows_df.with_columns(pl.lit(None).alias(col))

            new_rows_df = new_rows_df.select(target_df.columns)

            target_df = pl.concat([target_df, new_rows_df])

        self._atomic_rewrite(table_path, target_df)

        total_affected = rows_updated + rows_inserted
        self.logger.info(f"Merged {total_affected} rows ({rows_updated} updated, {rows_inserted} inserted)")
        return total_affected

    def _parse_merge_key(self, merge_condition: str) -> str:
        condition = merge_condition.strip()

        if "=" in condition:
            left, right = condition.split("=", 1)
            left = left.strip()
            right = right.strip()

            if "." in left:
                return left.split(".", 1)[1]
            return left

        return condition

    def _atomic_rewrite(self, table_path: Path, df: Any) -> None:
        backup_path = table_path.parent / f"{table_path.name}_backup_{os.getpid()}"

        try:
            if table_path.exists():
                shutil.move(str(table_path), str(backup_path))

            table_path.mkdir(parents=True, exist_ok=True)
            if df.height > 0:
                df.write_parquet(table_path / "part-00000.parquet")

            if backup_path.exists():
                shutil.rmtree(backup_path)

        except Exception as e:
            if backup_path.exists():
                shutil.rmtree(table_path, ignore_errors=True)
                shutil.move(str(backup_path), str(table_path))
            raise RuntimeError(f"Atomic rewrite failed, restored from backup: {e}") from e


def get_polars_maintenance_operations(
    working_dir: str | Path | None = None, execution_engine: str = "default"
) -> PolarsMaintenanceOperations | None:
    if not POLARS_AVAILABLE:
        logger.debug("Polars not available, cannot create maintenance operations")
        return None

    return PolarsMaintenanceOperations(working_dir=working_dir, execution_engine=execution_engine)
