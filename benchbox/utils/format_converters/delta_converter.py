from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from benchbox.utils.format_converters.base import (
    BaseFormatConverter,
    ConversionError,
    ConversionOptions,
    ConversionResult,
    SchemaError,
    normalize_schema_columns,
)


class DeltaConverter(BaseFormatConverter):
    def get_file_extension(self) -> str:
        return ""

    def get_format_name(self) -> str:
        return "Delta Lake"

    def convert(
        self,
        source_files: list[Path],
        table_name: str,
        schema: dict[str, Any],
        options: ConversionOptions | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> ConversionResult:
        try:
            from deltalake import write_deltalake
        except ImportError as e:
            raise ConversionError(
                "Delta Lake support requires the 'deltalake' package. "
                "Install it with: uv add deltalake --optional table-formats"
            ) from e

        opts = options or ConversionOptions()

        self.validate_source_files(source_files)
        self.validate_schema(schema)

        if progress_callback:
            progress_callback(f"Starting Delta Lake conversion for {table_name}", 0.0)

        try:
            self._build_arrow_schema(schema)
        except SchemaError:
            raise
        except Exception as e:
            raise SchemaError(f"Failed to build Arrow schema: {e}") from e

        source_rows = [0]
        combined_table = self.read_tbl_files(
            source_files,
            schema,
            progress_callback,
            progress_start=0.0,
            progress_end=0.7,
            options=opts,
            source_line_counter=source_rows,
        )

        column_names = [col["name"] for col in normalize_schema_columns(schema)]

        source_dir = source_files[0].parent
        output_dir = opts.output_dir if opts.output_dir else source_dir
        delta_table_path = output_dir / table_name
        delta_table_path.mkdir(parents=True, exist_ok=True)

        try:
            from deltalake.writer import WriterProperties

            if progress_callback:
                progress_callback("Writing Delta Lake table", 0.9)

            partition_by = opts.partition_cols if opts.partition_cols else None

            compression_map = {
                "snappy": "SNAPPY",
                "gzip": "GZIP",
                "zstd": "ZSTD",
                "none": "UNCOMPRESSED",
                None: "SNAPPY",
            }
            compression_codec = compression_map.get(opts.compression, "SNAPPY")

            writer_properties = WriterProperties(compression=compression_codec)

            write_deltalake(
                str(delta_table_path),
                combined_table,
                mode="overwrite",
                partition_by=partition_by,
                name=table_name,
                description=f"TPC benchmark table: {table_name}",
                writer_properties=writer_properties,
            )

        except Exception as e:
            if delta_table_path.exists():
                shutil.rmtree(delta_table_path, ignore_errors=True)
            raise ConversionError(f"Failed to write Delta Lake table: {e}") from e

        source_size = self.calculate_file_size(source_files)
        row_count = combined_table.num_rows

        if opts.validate_row_count:
            try:
                self._verify_inline_row_count(
                    expected_rows=opts.expected_row_count if opts.expected_row_count is not None else source_rows[0],
                    written_rows=row_count,
                    table_name=table_name,
                )
            except ConversionError:
                if delta_table_path.exists():
                    shutil.rmtree(delta_table_path, ignore_errors=True)
                raise

        output_size = sum(f.stat().st_size for f in delta_table_path.rglob("*") if f.is_file())

        metadata = {
            "format": "delta",
            "partition_cols": opts.partition_cols if opts.partition_cols else [],
            "num_columns": len(column_names),
            "table_path": str(delta_table_path),
            "compression": opts.compression,
        }

        if progress_callback:
            progress_callback(f"Conversion complete: {row_count:,} rows", 1.0)

        source_format = self._detect_source_format(source_files)

        return ConversionResult(
            output_files=[delta_table_path],
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
