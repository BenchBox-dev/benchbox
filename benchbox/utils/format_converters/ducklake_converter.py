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


class DuckLakeConverter(BaseFormatConverter):
    def get_file_extension(self) -> str:
        return ""

    def get_format_name(self) -> str:
        return "DuckLake"

    def convert(
        self,
        source_files: list[Path],
        table_name: str,
        schema: dict[str, Any],
        options: ConversionOptions | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> ConversionResult:
        try:
            import duckdb
        except ImportError as e:
            raise ConversionError("DuckLake support requires DuckDB. Install it with: uv add duckdb") from e

        opts = options or ConversionOptions()

        self.validate_source_files(source_files)
        self.validate_schema(schema)

        if progress_callback:
            progress_callback(f"Starting DuckLake conversion for {table_name}", 0.0)

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
            progress_end=0.6,
            options=opts,
            source_line_counter=source_rows,
        )

        column_names = [col["name"] for col in normalize_schema_columns(schema)]

        source_dir = source_files[0].parent
        output_dir = opts.output_dir if opts.output_dir else source_dir
        ducklake_table_path = output_dir / table_name

        if ducklake_table_path.exists():
            shutil.rmtree(ducklake_table_path, ignore_errors=True)

        ducklake_table_path.mkdir(parents=True, exist_ok=True)

        metadata_path, data_path = self._write_ducklake_table(
            duckdb, ducklake_table_path, table_name, combined_table, progress_callback
        )

        row_count = combined_table.num_rows

        if opts.validate_row_count:
            try:
                self._verify_inline_row_count(
                    expected_rows=opts.expected_row_count if opts.expected_row_count is not None else source_rows[0],
                    written_rows=row_count,
                    table_name=table_name,
                )
            except ConversionError:
                if ducklake_table_path.exists():
                    shutil.rmtree(ducklake_table_path, ignore_errors=True)
                raise

        return self._build_conversion_result(
            source_files,
            ducklake_table_path,
            metadata_path,
            data_path,
            column_names,
            row_count,
            opts,
            progress_callback,
        )

    def _write_ducklake_table(
        self,
        duckdb,
        ducklake_table_path: Path,
        table_name: str,
        combined_table,
        progress_callback: Callable[[str, float], None] | None,
    ) -> tuple[Path, Path]:
        conn = None
        try:
            if progress_callback:
                progress_callback("Installing DuckLake extension", 0.65)

            conn = duckdb.connect(":memory:")

            try:
                conn.execute("INSTALL ducklake")
                conn.execute("LOAD ducklake")
            except Exception as e:
                raise ConversionError(
                    f"Failed to load DuckLake extension. Ensure DuckDB >= 1.2.0 is installed. Error: {e}"
                ) from e

            if progress_callback:
                progress_callback("Creating DuckLake catalog", 0.7)

            metadata_path = ducklake_table_path / "metadata.ducklake"
            data_path = ducklake_table_path / "data"
            data_path.mkdir(parents=True, exist_ok=True)

            conn.execute(f"ATTACH 'ducklake:{metadata_path}' AS ducklake_db (DATA_PATH '{data_path}')")

            if progress_callback:
                progress_callback("Writing DuckLake table", 0.8)

            conn.register("source_data", combined_table)
            conn.execute("CREATE SCHEMA IF NOT EXISTS ducklake_db.main")
            conn.execute(f"CREATE OR REPLACE TABLE ducklake_db.main.{table_name} AS SELECT * FROM source_data")

            if progress_callback:
                progress_callback("Finalizing DuckLake table", 0.9)

            conn.close()
            conn = None

            return metadata_path, data_path

        except ConversionError:
            raise
        except Exception as e:
            if ducklake_table_path.exists():
                shutil.rmtree(ducklake_table_path, ignore_errors=True)
            raise ConversionError(f"Failed to write DuckLake table: {e}") from e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _build_conversion_result(
        self,
        source_files: list[Path],
        ducklake_table_path: Path,
        metadata_path: Path,
        data_path: Path,
        column_names: list[str],
        row_count: int,
        opts: ConversionOptions,
        progress_callback: Callable[[str, float], None] | None,
    ) -> ConversionResult:
        source_size = self.calculate_file_size(source_files)
        output_size = sum(f.stat().st_size for f in ducklake_table_path.rglob("*") if f.is_file())

        metadata = {
            "format": "ducklake",
            "partition_cols": opts.partition_cols if opts.partition_cols else [],
            "num_columns": len(column_names),
            "table_path": str(ducklake_table_path),
            "metadata_path": str(metadata_path),
            "data_path": str(data_path),
            "compression": opts.compression,
        }

        if progress_callback:
            progress_callback(f"Conversion complete: {row_count:,} rows", 1.0)

        source_format = self._detect_source_format(source_files)

        return ConversionResult(
            output_files=[ducklake_table_path],
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
