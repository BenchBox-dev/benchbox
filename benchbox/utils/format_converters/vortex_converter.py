from __future__ import annotations

import contextlib
import logging
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

from benchbox.utils.format_converters.base import (
    BaseFormatConverter,
    ConversionError,
    ConversionOptions,
    ConversionResult,
    SchemaError,
    normalize_schema_columns,
)


class VortexConverter(BaseFormatConverter):
    def get_file_extension(self) -> str:
        return ".vortex"

    def get_format_name(self) -> str:
        return "Vortex"

    def _get_vortex_module(self):
        try:
            import vortex

            return vortex
        except ImportError as e:
            raise ConversionError(
                "Vortex format support requires the 'vortex' package. "
                "Install it with: uv add vortex-data --optional table-formats"
            ) from e

    def _write_with_duckdb_vortex(self, combined_table: Any, output_path: Path) -> tuple[bool, str | None]:
        try:
            import duckdb
        except ImportError:
            return False, "DuckDB not installed"

        conn = None
        try:
            conn = duckdb.connect(":memory:")
            conn.execute("INSTALL vortex")
            conn.execute("LOAD vortex")
            conn.register("benchbox_vortex_source", combined_table)
            escaped_path = str(output_path).replace("'", "''")
            conn.execute(f"COPY (SELECT * FROM benchbox_vortex_source) TO '{escaped_path}' (FORMAT VORTEX)")
            return True, None
        except Exception as e:
            logger.warning("DuckDB vortex extension write failed: %s", e)
            return False, str(e)
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()

    def _get_vortex_writer_functions(
        self, vortex_module: Any
    ) -> tuple[Callable[[Any], Any], Callable[[Any, str], None]]:

        array_builder = getattr(vortex_module, "array", None)
        if not callable(array_builder):
            encoding_module = getattr(vortex_module, "encoding", None)
            array_builder = getattr(encoding_module, "array", None)

        io_module = getattr(vortex_module, "io", None)
        writer = getattr(io_module, "write", None)
        if not callable(writer):
            writer = getattr(vortex_module, "write", None)

        if callable(array_builder) and callable(writer):
            return array_builder, writer

        providers = importlib_metadata.packages_distributions().get("vortex", [])
        provider_text = f" Found provider(s): {', '.join(providers)}." if providers else ""
        raise ConversionError(
            "Installed 'vortex' module is incompatible with BenchBox Vortex conversion."
            " Install compatible bindings with: uv add vortex-data --optional table-formats."
            f"{provider_text}"
        )

    def _write_vortex_file(self, combined_table: Any, output_path: Path, opts: ConversionOptions) -> str:
        duckdb_ok, duckdb_error = self._write_with_duckdb_vortex(combined_table, output_path)
        if duckdb_ok:
            return "duckdb-extension"

        if opts.metadata.get("require_duckdb_writer", False):
            raise ConversionError(
                f"DuckDB vortex extension is required for this conversion but failed: {duckdb_error}. "
                "Ensure the DuckDB vortex extension is installed and loadable, "
                "or use --table-mode native to load data into DuckDB tables directly."
            )
        logger.info("DuckDB vortex extension unavailable, using Python Vortex bindings")
        vortex = self._get_vortex_module()
        array_builder, writer = self._get_vortex_writer_functions(vortex)
        vortex_array = array_builder(combined_table)
        writer(vortex_array, str(output_path))
        return "python-bindings"

    def convert(
        self,
        source_files: list[Path],
        table_name: str,
        schema: dict[str, Any],
        options: ConversionOptions | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> ConversionResult:
        opts = options or ConversionOptions()

        self.validate_source_files(source_files)
        self.validate_schema(schema)

        if progress_callback:
            progress_callback(f"Starting Vortex conversion for {table_name}", 0.0)

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
            progress_end=0.8,
            options=opts,
            source_line_counter=source_rows,
        )

        column_names = [col["name"] for col in normalize_schema_columns(schema)]

        source_dir = source_files[0].parent
        output_dir = opts.output_dir if opts.output_dir else source_dir
        output_path = output_dir / f"{table_name}.vortex"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            if progress_callback:
                progress_callback("Writing Vortex file", 0.9)
            vortex_writer = self._write_vortex_file(combined_table, output_path, opts)
        except ConversionError:
            raise
        except Exception as e:
            if output_path.exists():
                with contextlib.suppress(Exception):
                    output_path.unlink()
            raise ConversionError(f"Failed to write Vortex file: {e}") from e

        source_size = self.calculate_file_size(source_files)
        output_size = output_path.stat().st_size
        row_count = combined_table.num_rows

        if opts.validate_row_count:
            try:
                self._verify_inline_row_count(
                    expected_rows=opts.expected_row_count if opts.expected_row_count is not None else source_rows[0],
                    written_rows=row_count,
                    table_name=table_name,
                )
            except ConversionError:
                if output_path.exists():
                    with contextlib.suppress(Exception):
                        output_path.unlink()
                raise

        metadata = {
            "compression": opts.compression,
            "num_columns": len(column_names),
        }

        if progress_callback:
            progress_callback(f"Conversion complete: {row_count:,} rows", 1.0)

        source_format = self._detect_source_format(source_files)

        return ConversionResult(
            output_files=[output_path],
            row_count=row_count,
            source_size_bytes=source_size,
            output_size_bytes=output_size,
            metadata=metadata,
            source_format=source_format,
            converted_at=self.get_current_timestamp(),
            conversion_options={
                "compression": opts.compression,
                "merge_shards": opts.merge_shards,
                "validate_row_count": opts.validate_row_count,
                "vortex_writer": vortex_writer,
            },
        )
