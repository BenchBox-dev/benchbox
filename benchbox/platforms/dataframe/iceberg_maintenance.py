# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from benchbox.utils.clock import mono_time

try:
    from pyiceberg.catalog import Catalog, load_catalog
    from pyiceberg.expressions import (
        AlwaysTrue,
        And,
        EqualTo,
        GreaterThan,
        GreaterThanOrEqual,
        IsNull,
        LessThan,
        LessThanOrEqual,
        NotEqualTo,
    )
    from pyiceberg.table import Table

    ICEBERG_AVAILABLE = True
except ImportError:
    Catalog = None
    load_catalog = None
    Table = None
    ICEBERG_AVAILABLE = False

try:
    import pyarrow as pa

    PYARROW_AVAILABLE = True
except ImportError:
    pa = None
    PYARROW_AVAILABLE = False

from benchbox.core.dataframe.maintenance_interface import (
    ICEBERG_CAPABILITIES,
    BaseDataFrameMaintenanceOperations,
    DataFrameMaintenanceCapabilities,
    MaintenanceOperationType,
    MaintenanceResult,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class IcebergMaintenanceOperations(BaseDataFrameMaintenanceOperations):
    def __init__(
        self,
        catalog_name: str = "default",
        catalog_config: dict[str, Any] | None = None,
        working_dir: str | Path | None = None,
    ) -> None:
        super().__init__()

        if not ICEBERG_AVAILABLE:
            raise ImportError(
                "pyiceberg is not installed. Install with: pip install pyiceberg\n"
                "For TPC-H/TPC-DS maintenance tests with Iceberg, install:\n"
                "pip install 'benchbox[iceberg]'"
            )

        if not PYARROW_AVAILABLE:
            raise ImportError(
                "PyArrow is not installed. Install with: pip install pyarrow\n"
                "PyArrow is required for Iceberg operations."
            )

        self.working_dir = Path(working_dir) if working_dir else Path.cwd() / "iceberg_warehouse"
        self.catalog_name = catalog_name
        self.catalog_config = catalog_config or self._default_catalog_config()
        self._catalog: Catalog | None = None
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def _default_catalog_config(self) -> dict[str, Any]:
        warehouse_path = str(self.working_dir / "warehouse")
        return {
            "type": "sql",
            "uri": f"sqlite:///{self.working_dir}/iceberg_catalog.db",
            "warehouse": warehouse_path,
        }

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self.working_dir.mkdir(parents=True, exist_ok=True)
            self._catalog = load_catalog(self.catalog_name, **self.catalog_config)
        return self._catalog

    def _get_capabilities(self) -> DataFrameMaintenanceCapabilities:
        return ICEBERG_CAPABILITIES

    def _normalize_table_identifier(self, table_path: str) -> str:
        if "." in table_path and "/" not in table_path and "\\" not in table_path:
            return table_path

        path = Path(table_path)
        table_name = path.name

        import re

        table_name = re.sub(r"[^a-zA-Z0-9_]", "_", table_name)

        return f"default.{table_name}"

    def _get_or_create_table(
        self,
        table_identifier: str,
        schema: Any,
        partition_columns: list[str] | None = None,
    ) -> Table:
        normalized_id = self._normalize_table_identifier(table_identifier)

        try:
            return self.catalog.load_table(normalized_id)
        except Exception:
            self.logger.info(f"Creating new Iceberg table: {normalized_id}")

            if "." in normalized_id:
                namespace, _ = normalized_id.rsplit(".", 1)
            else:
                namespace = "default"

            try:
                self.catalog.create_namespace(namespace)
            except Exception:
                pass

            return self.catalog.create_table(
                identifier=normalized_id,
                schema=schema,
            )

    def _do_insert(
        self,
        table_path: Path | str,
        dataframe: Any,
        partition_columns: list[str] | None,
        mode: str,
    ) -> int:
        table_identifier = str(table_path)

        arrow_table = self._convert_to_arrow(dataframe)
        row_count = arrow_table.num_rows

        if row_count == 0:
            self.logger.info("No rows to insert")
            return 0

        iceberg_table = self._get_or_create_table(
            table_identifier,
            arrow_table.schema,
            partition_columns,
        )

        if mode == "overwrite":
            iceberg_table.overwrite(arrow_table)
        else:
            iceberg_table.append(arrow_table)

        self.logger.info(f"Inserted {row_count} rows to Iceberg table {table_identifier}")
        return row_count

    def _parse_condition(self, condition: str) -> Any:
        condition = condition.strip()

        operators = [
            (">=", GreaterThanOrEqual),
            ("<=", LessThanOrEqual),
            ("!=", NotEqualTo),
            ("<>", NotEqualTo),
            ("=", EqualTo),
            (">", GreaterThan),
            ("<", LessThan),
        ]

        for op_str, op_class in operators:
            if op_str in condition:
                parts = condition.split(op_str, 1)
                if len(parts) == 2:
                    column = parts[0].strip()
                    value = parts[1].strip()

                    if (value.startswith("'") and value.endswith("'")) or (
                        value.startswith('"') and value.endswith('"')
                    ):
                        value = value[1:-1]
                    else:
                        try:
                            if "." in value:
                                value = float(value)
                            else:
                                value = int(value)
                        except ValueError:
                            pass

                    return cast(Any, op_class)(column, value)

        self.logger.warning(f"Could not parse condition '{condition}', using AlwaysTrue")
        return AlwaysTrue()

    def _dict_to_expression(self, condition: dict[str, Any]) -> Any:
        if not condition:
            raise ValueError("Iceberg condition dictionary cannot be empty")
        terms: list[Any] = []
        for raw_column, value in condition.items():
            column = str(raw_column)
            if value is None:
                terms.append(IsNull(term=column))
            elif isinstance(value, bool | int | float | str):
                terms.append(EqualTo(term=column, literal=value))
            else:
                raise TypeError(f"Unsupported Iceberg condition value for {column!r}: {value!r}")
        predicate: Any = terms[0]
        for term in terms[1:]:
            predicate = And(predicate, term)
        return predicate

    def _do_delete(
        self,
        table_path: Path | str,
        condition: str | Any,
    ) -> int:
        table_identifier = self._normalize_table_identifier(str(table_path))

        try:
            iceberg_table = self.catalog.load_table(table_identifier)
        except Exception as e:
            self.logger.warning(f"Could not load Iceberg table {table_identifier}: {e}")
            return 0

        scan = iceberg_table.scan()
        rows_before = sum(1 for _ in scan.to_arrow().to_batches())
        rows_before = iceberg_table.scan().to_arrow().num_rows

        if isinstance(condition, dict):
            delete_filter = self._dict_to_expression(condition)
        elif isinstance(condition, str):
            delete_filter = self._parse_condition(condition)
        else:
            delete_filter = condition

        iceberg_table.delete(delete_filter=delete_filter)

        rows_after = iceberg_table.scan().to_arrow().num_rows
        rows_deleted = rows_before - rows_after

        self.logger.info(f"Deleted {rows_deleted} rows from Iceberg table {table_identifier}")
        return rows_deleted

    def _do_update(
        self,
        table_path: Path | str,
        condition: str | Any,
        updates: dict[str, Any],
    ) -> int:
        table_identifier = self._normalize_table_identifier(str(table_path))

        try:
            iceberg_table = self.catalog.load_table(table_identifier)
        except Exception as e:
            raise RuntimeError(f"Could not load Iceberg table {table_identifier}: {e}") from e

        if isinstance(condition, dict):
            update_filter = self._dict_to_expression(condition)
        elif isinstance(condition, str):
            update_filter = self._parse_condition(condition)
        else:
            update_filter = condition

        matching_rows = iceberg_table.scan(row_filter=update_filter).to_arrow()
        row_count = matching_rows.num_rows

        if row_count == 0:
            self.logger.info("No rows match update condition")
            return 0

        df = matching_rows.to_pandas()
        for column, value in updates.items():
            if isinstance(value, str):
                if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
                    value = value[1:-1]
            df[column] = value

        updated_arrow = pa.Table.from_pandas(df)

        iceberg_table.delete(delete_filter=update_filter)

        iceberg_table.append(updated_arrow)

        self.logger.info(f"Updated {row_count} rows in Iceberg table {table_identifier}")
        return row_count

    def _do_merge(
        self,
        table_path: Path | str,
        source_dataframe: Any,
        merge_condition: str | Any,
        when_matched: dict[str, Any] | None,
        when_not_matched: dict[str, Any] | None,
    ) -> int:
        table_identifier = self._normalize_table_identifier(str(table_path))

        source_arrow = self._convert_to_arrow(source_dataframe)

        try:
            iceberg_table = self.catalog.load_table(table_identifier)
        except Exception as e:
            raise RuntimeError(f"Could not load Iceberg table {table_identifier}: {e}") from e

        target_arrow = iceberg_table.scan().to_arrow()

        target_df = target_arrow.to_pandas()
        source_df = source_arrow.to_pandas()

        merge_key = self._parse_merge_key(merge_condition)

        rows_updated = 0
        rows_inserted = 0

        if when_matched:
            matched_mask = target_df[merge_key].isin(source_df[merge_key])
            for col, val in when_matched.items():
                if isinstance(val, str) and val.startswith("source."):
                    source_col = val[7:]
                    source_mapping = dict(zip(source_df[merge_key], source_df[source_col]))
                    target_df.loc[matched_mask, col] = target_df.loc[matched_mask, merge_key].map(source_mapping)
                else:
                    target_df.loc[matched_mask, col] = val
            rows_updated = matched_mask.sum()

        if when_not_matched:
            not_matched_mask = ~source_df[merge_key].isin(target_df[merge_key])
            new_rows = source_df[not_matched_mask]
            rows_inserted = len(new_rows)

            if rows_inserted > 0:
                import pandas as pd

                insert_data: dict[str, Any] = {}
                for column in target_arrow.schema.names:
                    if column in when_not_matched:
                        mapping = when_not_matched[column]
                        if isinstance(mapping, str) and mapping.startswith("source."):
                            source_column = mapping[len("source.") :]
                            if source_column not in new_rows.columns:
                                raise ValueError(
                                    f"Merge insert mapping for {column!r} references "
                                    f"missing source column {source_column!r}"
                                )
                            insert_data[column] = new_rows[source_column].reset_index(drop=True)
                        else:
                            insert_data[column] = mapping
                    elif column in new_rows.columns:
                        insert_data[column] = new_rows[column].reset_index(drop=True)
                    else:
                        insert_data[column] = None
                new_rows = pd.DataFrame(insert_data)
                target_df = pa.concat_tables(
                    [
                        pa.Table.from_pandas(target_df, schema=target_arrow.schema),
                        pa.Table.from_pandas(new_rows, schema=target_arrow.schema),
                    ]
                ).to_pandas()

        result_arrow = pa.Table.from_pandas(target_df, schema=target_arrow.schema)
        iceberg_table.overwrite(result_arrow)

        total_affected = int(rows_updated) + int(rows_inserted)
        self.logger.info(
            f"Merged into Iceberg table {table_identifier}: {int(rows_updated)} updated, {int(rows_inserted)} inserted"
        )
        return total_affected

    def _parse_merge_key(self, merge_condition: str) -> str:
        condition = str(merge_condition).strip()

        if "=" in condition:
            left = condition.split("=")[0].strip()
            if "." in left:
                return left.split(".")[-1]
            return left

        return condition

    def optimize_table(
        self,
        table_path: Path | str,
        *,
        strategy: str = "compact",
        columns: list[str] | None = None,
        partition_filter: Any | None = None,
    ) -> MaintenanceResult:
        _ = (table_path, strategy, columns, partition_filter)
        raise NotImplementedError(
            "Iceberg OPTIMIZE is not implemented: pyiceberg exposes no binpack rewrite. "
            "Use Spark rewrite_data_files for Iceberg file layout work."
        )

    _DEFAULT_MAX_SNAPSHOT_AGE_MS = 5 * 24 * 3600 * 1000
    _DEFAULT_MIN_SNAPSHOTS_TO_KEEP = 1

    def _retention_window(self, table: Any, retention_hours: int | None) -> tuple[int, int]:
        if retention_hours is not None:
            cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=retention_hours)).timestamp() * 1000)
            return cutoff_ms, 1
        properties = table.properties or {}
        try:
            max_age_ms = int(properties.get("history.expire.max-snapshot-age-ms", self._DEFAULT_MAX_SNAPSHOT_AGE_MS))
        except (TypeError, ValueError):
            max_age_ms = self._DEFAULT_MAX_SNAPSHOT_AGE_MS
        try:
            min_keep = int(properties.get("history.expire.min-snapshots-to-keep", self._DEFAULT_MIN_SNAPSHOTS_TO_KEEP))
        except (TypeError, ValueError):
            min_keep = self._DEFAULT_MIN_SNAPSHOTS_TO_KEEP
        cutoff_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - max(0, max_age_ms)
        return cutoff_ms, max(1, min_keep)

    @staticmethod
    def _local_path(location: str) -> Path | None:
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        parsed = urlparse(location)
        if len(parsed.scheme) <= 1:
            return Path(location)
        if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
            return Path(url2pathname(parsed.path))
        return None

    @staticmethod
    def _normalize_file_ref(uri: str) -> str:
        local = IcebergMaintenanceOperations._local_path(uri)
        if local is None:
            return uri
        return f"path:{local.resolve()}"

    def _snapshot_file_refs(self, table: Any, snapshot_ids: set[int]) -> set[str]:
        from pyiceberg.manifest import read_manifest_list

        refs: set[str] = set()
        wanted = set(snapshot_ids)
        for snapshot in table.snapshots() or []:
            if snapshot.snapshot_id not in wanted:
                continue
            refs.add(self._normalize_file_ref(snapshot.manifest_list))
            for manifest in read_manifest_list(table.io.new_input(snapshot.manifest_list)):
                refs.add(self._normalize_file_ref(manifest.manifest_path))
                for entry in manifest.fetch_manifest_entry(table.io, discard_deleted=False):
                    refs.add(self._normalize_file_ref(entry.data_file.file_path))
        return refs

    @staticmethod
    def _local_table_dir(location: str) -> Path | None:
        directory = IcebergMaintenanceOperations._local_path(location)
        return directory if directory is not None and directory.is_dir() else None

    def _reclaim_orphan_files(self, table: Any, remaining_ids: set[int]) -> dict[str, Any]:
        table.refresh()
        location = table.location()
        directory = self._local_table_dir(location)
        if directory is None:
            self.logger.warning(f"Iceberg vacuum skips file cleanup for non-local table location: {location}")
            return {"file_cleanup": "metadata-only", "reclaimed_files": 0, "reclaimed_bytes": 0}
        referenced = self._snapshot_file_refs(table, remaining_ids)
        referenced.add(self._normalize_file_ref(table.metadata_location))
        reclaimed_files = 0
        reclaimed_bytes = 0
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name == "version-hint.text":
                continue
            if self._normalize_file_ref(path.as_uri()) in referenced:
                continue
            reclaimed_bytes += path.stat().st_size
            reclaimed_files += 1
            path.unlink()
        return {"file_cleanup": "full", "reclaimed_files": reclaimed_files, "reclaimed_bytes": reclaimed_bytes}

    def _reclaimable_files(self, table: Any, remaining_ids: set[int]) -> tuple[int, int]:
        location = table.location()
        directory = self._local_table_dir(location)
        if directory is None:
            return 0, 0
        referenced = self._snapshot_file_refs(table, remaining_ids)
        referenced.add(self._normalize_file_ref(table.metadata_location))
        files = 0
        total_bytes = 0
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name == "version-hint.text":
                continue
            if self._normalize_file_ref(path.as_uri()) in referenced:
                continue
            files += 1
            total_bytes += path.stat().st_size
        return files, total_bytes

    def vacuum_table(
        self,
        table_path: Path | str,
        *,
        retention_hours: int | None = None,
        dry_run: bool = True,
        enforce_retention: bool = True,
    ) -> MaintenanceResult:
        if not enforce_retention:
            self.logger.warning(
                "Iceberg vacuum ignores enforce_retention=False; snapshot expiration always enforces retention."
            )
        operation = MaintenanceOperationType.VACUUM
        start_time = mono_time()
        try:
            self._check_capability(operation)
            identifier = self._normalize_table_identifier(str(table_path))
            try:
                table = self.catalog.load_table(identifier)
            except Exception as e:
                raise RuntimeError(f"Could not open Iceberg table {identifier}: {e}") from e
            snapshots = list(table.snapshots() or [])
            current_id = table.current_snapshot().snapshot_id if table.current_snapshot() else None
            protected = {ref.snapshot_id for ref in table.refs().values()}
            cutoff_ms, min_keep = self._retention_window(table, retention_hours)
            eligible = sorted(
                (
                    snapshot
                    for snapshot in snapshots
                    if snapshot.snapshot_id != current_id
                    and snapshot.snapshot_id not in protected
                    and snapshot.timestamp_ms < cutoff_ms
                ),
                key=lambda snapshot: snapshot.timestamp_ms,
            )
            expirable = [snapshot.snapshot_id for snapshot in eligible[: max(0, len(snapshots) - min_keep)]]
            metrics: dict[str, Any] = {"dry_run": dry_run, "expired_snapshot_ids": expirable}
            remaining_ids = {snapshot.snapshot_id for snapshot in snapshots} - set(expirable)
            if dry_run:
                reclaimable_files, reclaimable_bytes = self._reclaimable_files(table, remaining_ids)
                metrics["reclaimable_files"] = reclaimable_files
                metrics["reclaimable_bytes"] = reclaimable_bytes
            elif expirable:
                table.maintenance.expire_snapshots().by_ids(expirable).commit()
                table = self.catalog.load_table(identifier)
                metrics.update(self._reclaim_orphan_files(table, remaining_ids))
            end_time = mono_time()
            self.logger.info(f"Vacuumed Iceberg table {identifier} (dry_run={dry_run}): {len(expirable)} snapshots")
            return MaintenanceResult(
                operation_type=operation,
                success=True,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                rows_affected=len(expirable),
                metrics=metrics,
            )
        except NotImplementedError:
            raise
        except Exception as e:
            self.logger.error(f"VACUUM failed: {e}")
            end_time = mono_time()
            return MaintenanceResult(
                operation_type=operation,
                success=False,
                start_time=start_time,
                end_time=end_time,
                duration=end_time - start_time,
                rows_affected=0,
                error_message=str(e),
            )


def get_iceberg_maintenance_operations(
    catalog_name: str = "default",
    catalog_config: dict[str, Any] | None = None,
    working_dir: str | Path | None = None,
) -> IcebergMaintenanceOperations | None:
    if not ICEBERG_AVAILABLE:
        logger.debug("Iceberg maintenance not available (pyiceberg not installed)")
        return None

    if not PYARROW_AVAILABLE:
        logger.debug("Iceberg maintenance not available (pyarrow not installed)")
        return None

    return IcebergMaintenanceOperations(
        catalog_name=catalog_name,
        catalog_config=catalog_config,
        working_dir=working_dir,
    )
