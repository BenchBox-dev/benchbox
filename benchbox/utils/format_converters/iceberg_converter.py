from __future__ import annotations

import importlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from benchbox.utils.format_converters.base import (
    BaseFormatConverter,
    ConversionError,
    ConversionOptions,
    ConversionResult,
    SchemaError,
)
from benchbox.utils.iceberg_layout import local_iceberg_location


class IcebergConverter(BaseFormatConverter):
    def get_file_extension(self) -> str:
        return ""

    def get_format_name(self) -> str:
        return "Apache Iceberg"

    def _validate_partition_columns(self, partition_cols: list[str], schema: dict[str, Any]) -> None:
        available_columns = {col["name"] for col in schema["columns"]}
        for col in partition_cols:
            if col not in available_columns:
                raise ConversionError(
                    f"Partition column '{col}' not found in schema. Available columns: {sorted(available_columns)}"
                )

    def _build_partition_spec(self, iceberg_schema: Any, partition_cols: list[str]) -> Any:
        from pyiceberg.partitioning import PartitionField, PartitionSpec
        from pyiceberg.transforms import IdentityTransform

        partition_fields = []
        for idx, col_name in enumerate(partition_cols):
            source_field = iceberg_schema.find_field(col_name)
            partition_fields.append(
                PartitionField(
                    source_id=source_field.field_id,
                    field_id=1000 + idx,
                    transform=IdentityTransform(),
                    name=col_name,
                )
            )

        return PartitionSpec(*partition_fields)

    _COMPRESSION_MAP = {
        "snappy": "snappy",
        "gzip": "gzip",
        "zstd": "zstd",
        "none": "uncompressed",
        None: "snappy",
    }

    def _resolve_output_dir(self, opts: ConversionOptions, source_files: list[Path]) -> Path:
        source_dir = source_files[0].parent
        output_dir = opts.output_dir if opts.output_dir else source_dir
        return Path(output_dir).resolve()

    def _build_arrow_schema_safely(self, schema: dict[str, Any]):
        try:
            return self._build_arrow_schema(schema)
        except SchemaError:
            raise
        except Exception as e:
            raise SchemaError(f"Failed to build Arrow schema: {e}") from e

    def _build_iceberg_schema_and_partition_spec(self, arrow_schema, partition_cols: list[str]):
        from pyiceberg.io.pyarrow import (
            _ConvertToIcebergWithoutIDs,
            visit_pyarrow,
        )
        from pyiceberg.schema import assign_fresh_schema_ids

        visitor = _ConvertToIcebergWithoutIDs()
        iceberg_schema_no_ids = visit_pyarrow(arrow_schema, visitor)
        iceberg_schema = assign_fresh_schema_ids(iceberg_schema_no_ids)
        partition_spec = self._build_partition_spec(iceberg_schema, partition_cols)
        return iceberg_schema, partition_spec

    def _write_iceberg_table(
        self,
        catalog_db: str,
        output_dir: Path,
        iceberg_table_path: Path,
        table_name: str,
        arrow_schema,
        combined_table,
        opts: ConversionOptions,
    ) -> None:
        from pyiceberg.catalog.sql import SqlCatalog

        catalog = SqlCatalog(
            "benchbox_catalog",
            uri=f"sqlite:///{catalog_db}",
            warehouse=str(output_dir),
        )
        try:
            catalog.create_namespace_if_not_exists("benchbox")

            table_identifier = ("benchbox", table_name)
            try:
                catalog.drop_table(table_identifier)
            except Exception:
                pass

            compression_codec = self._COMPRESSION_MAP.get(opts.compression, "snappy")

            iceberg_schema = None
            partition_spec = None
            if opts.partition_cols:
                iceberg_schema, partition_spec = self._build_iceberg_schema_and_partition_spec(
                    arrow_schema, opts.partition_cols
                )

            create_kwargs: dict[str, Any] = {
                "identifier": table_identifier,
                "schema": iceberg_schema if iceberg_schema else arrow_schema,
                "location": local_iceberg_location(iceberg_table_path),
                "properties": {
                    "format-version": "2",
                    "write.format.default": "parquet",
                    "write.parquet.compression-codec": compression_codec,
                },
            }
            if partition_spec:
                create_kwargs["partition_spec"] = partition_spec

            iceberg_table = catalog.create_table(**create_kwargs)
            iceberg_table.overwrite(combined_table)
        finally:
            catalog.engine.dispose()

    def _cleanup_catalog_artifacts(self, catalog_fd, catalog_db: str | None) -> None:
        if catalog_fd is not None:
            try:
                os.close(catalog_fd)
            except Exception:
                pass
        if catalog_db and os.path.exists(catalog_db):
            try:
                os.unlink(catalog_db)
            except Exception:
                pass

    def _build_iceberg_metadata(
        self,
        opts: ConversionOptions,
        combined_table,
        column_names: list[str],
        iceberg_table_path: Path,
    ) -> dict[str, Any]:
        partition_counts: dict[str, int] = {}
        if opts.partition_cols:
            for col in opts.partition_cols:
                partition_counts[col] = len(combined_table.column(col).unique())

        return {
            "format": "iceberg",
            "partitioned": bool(opts.partition_cols),
            "partition_cols": opts.partition_cols if opts.partition_cols else [],
            "partition_counts": partition_counts if partition_counts else None,
            "num_columns": len(column_names),
            "table_path": str(iceberg_table_path),
            "format_version": 2,
            "compression": opts.compression,
        }

    def convert(
        self,
        source_files: list[Path],
        table_name: str,
        schema: dict[str, Any],
        options: ConversionOptions | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> ConversionResult:
        try:
            importlib.import_module("pyiceberg.catalog.sql")
        except ImportError as e:
            raise ConversionError(
                "Iceberg support requires the 'pyiceberg' package. "
                "Install it with: uv add pyiceberg --optional table-formats"
            ) from e

        opts = options or ConversionOptions()

        self.validate_source_files(source_files)
        self.validate_schema(schema)

        if opts.partition_cols:
            self._validate_partition_columns(opts.partition_cols, schema)

        if progress_callback:
            progress_callback(f"Starting Iceberg conversion for {table_name}", 0.0)

        arrow_schema = self._build_arrow_schema_safely(schema)

        combined_table = self.read_tbl_files(
            source_files, schema, progress_callback, progress_start=0.0, progress_end=0.6
        )
        combined_table = combined_table.cast(arrow_schema)

        column_names = [col["name"] for col in schema["columns"]]
        output_dir = self._resolve_output_dir(opts, source_files)
        iceberg_table_path = output_dir / table_name
        iceberg_table_path.mkdir(parents=True, exist_ok=True)

        catalog_fd = None
        catalog_db: str | None = None
        try:
            if progress_callback:
                progress_callback("Creating Iceberg catalog", 0.7)

            catalog_fd, catalog_db = tempfile.mkstemp(suffix=".db", prefix="benchbox_iceberg_")
            os.close(catalog_fd)
            catalog_fd = None

            if progress_callback:
                progress_callback("Writing Iceberg table", 0.8)

            self._write_iceberg_table(
                catalog_db, output_dir, iceberg_table_path, table_name, arrow_schema, combined_table, opts
            )
        except ConversionError:
            raise
        except Exception as e:
            if iceberg_table_path.exists():
                shutil.rmtree(iceberg_table_path, ignore_errors=True)
            raise ConversionError(f"Failed to write Iceberg table: {e}") from e
        finally:
            self._cleanup_catalog_artifacts(catalog_fd, catalog_db)

        source_size = self.calculate_file_size(source_files)
        row_count = combined_table.num_rows

        if opts.validate_row_count:
            try:
                self.validate_row_count(source_files, row_count, table_name)
            except ConversionError:
                if iceberg_table_path.exists():
                    shutil.rmtree(iceberg_table_path, ignore_errors=True)
                raise

        output_size = sum(f.stat().st_size for f in iceberg_table_path.rglob("*") if f.is_file())
        metadata = self._build_iceberg_metadata(opts, combined_table, column_names, iceberg_table_path)

        if progress_callback:
            progress_callback(f"Conversion complete: {row_count:,} rows", 1.0)

        source_format = self._detect_source_format(source_files)

        return ConversionResult(
            output_files=[iceberg_table_path],
            row_count=row_count,
            source_size_bytes=source_size,
            output_size_bytes=output_size,
            metadata=metadata,
            source_format=source_format,
            converted_at=self.get_current_timestamp(),
            conversion_options={
                "compression": opts.compression,
                "merge_shards": opts.merge_shards,
                "partition_cols": opts.partition_cols,
                "validate_row_count": opts.validate_row_count,
            },
        )
