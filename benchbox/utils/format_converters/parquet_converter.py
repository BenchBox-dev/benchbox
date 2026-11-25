from __future__ import annotations

import contextlib
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from benchbox.utils.file_format import detect_compression
from benchbox.utils.format_converters.base import (
    BaseFormatConverter,
    ConversionError,
    ConversionOptions,
    ConversionResult,
    SchemaError,
    normalize_schema_columns,
)

logger = logging.getLogger(__name__)

_DUCKDB_SUPPORTED_COMPRESSIONS = frozenset({None, "gzip", "zstd"})
_DUCKDB_MIN_ROW_GROUP_SIZE = 2048
_DUCKDB_STREAM_MEMORY_LIMIT = "320MB"
PARQUET_WRITER_DUCKDB = "benchbox-duckdb-streaming"
PARQUET_WRITER_PYARROW = "benchbox-pyarrow-streaming"


def _arrow_type_to_duckdb(arrow_type: pa.DataType) -> str | None:
    if pa.types.is_int8(arrow_type):
        return "TINYINT"
    if pa.types.is_int16(arrow_type):
        return "SMALLINT"
    if pa.types.is_int32(arrow_type):
        return "INTEGER"
    if pa.types.is_int64(arrow_type):
        return "BIGINT"
    if pa.types.is_float32(arrow_type):
        return "FLOAT"
    if pa.types.is_float64(arrow_type):
        return "DOUBLE"
    if pa.types.is_boolean(arrow_type):
        return "BOOLEAN"
    if pa.types.is_date32(arrow_type) or pa.types.is_date64(arrow_type):
        return "DATE"
    if pa.types.is_timestamp(arrow_type):
        return "TIMESTAMP"
    if pa.types.is_decimal(arrow_type):
        return f"DECIMAL({arrow_type.precision},{arrow_type.scale})"
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return "VARCHAR"
    return None


class ParquetConverter(BaseFormatConverter):
    def get_file_extension(self) -> str:
        return ".parquet"

    def get_format_name(self) -> str:
        return "Parquet"

    def _validate_partition_columns(self, partition_cols: list[str], schema: dict[str, Any]) -> None:
        available_columns = {col["name"] for col in normalize_schema_columns(schema)}
        for col in partition_cols:
            if col not in available_columns:
                raise ConversionError(
                    f"Partition column '{col}' not found in schema. Available columns: {sorted(available_columns)}"
                )

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

        if opts.partition_cols:
            self._validate_partition_columns(opts.partition_cols, schema)

        if progress_callback:
            progress_callback(f"Starting Parquet conversion for {table_name}", 0.0)

        try:
            arrow_schema = self._build_arrow_schema(schema, strict=opts.strict_schema)
        except SchemaError:
            raise
        except Exception as e:
            raise SchemaError(f"Failed to build Arrow schema: {e}") from e

        column_names = [field.name for field in arrow_schema]
        source_dir = source_files[0].parent
        output_dir = opts.output_dir if opts.output_dir else source_dir

        compression_map = {
            "snappy": "SNAPPY",
            "gzip": "GZIP",
            "zstd": "ZSTD",
            "none": None,
            None: None,
        }
        compression = compression_map.get(opts.compression, "SNAPPY")

        if opts.partition_cols:
            return self._write_partitioned(
                source_files=source_files,
                schema=schema,
                arrow_schema=arrow_schema,
                table_name=table_name,
                output_dir=output_dir,
                column_names=column_names,
                opts=opts,
                compression=compression,
                progress_callback=progress_callback,
            )
        return self._write_single_file(
            source_files=source_files,
            schema=schema,
            arrow_schema=arrow_schema,
            table_name=table_name,
            output_dir=output_dir,
            column_names=column_names,
            opts=opts,
            compression=compression,
            progress_callback=progress_callback,
        )

    def _try_write_single_file_duckdb(
        self,
        *,
        source_files: list[Path],
        output_path: Path,
        arrow_schema: pa.Schema,
        column_names: list[str],
        opts: ConversionOptions,
        compression: str | None,
        progress_callback: Callable[[str, float], None] | None,
    ) -> tuple[int, int] | None:
        if opts.data_page_version is not None or opts.row_group_size < _DUCKDB_MIN_ROW_GROUP_SIZE:
            return None

        col_types: list[tuple[str, str]] = []
        for field in arrow_schema:
            duck_type = _arrow_type_to_duckdb(field.type)
            if duck_type is None:
                return None
            col_types.append((field.name, duck_type))

        active_files: list[Path] = []
        framing: tuple[str, bool, bool] | None = None
        all_uncompressed = True
        for file_path in source_files:
            compression_type = detect_compression(file_path)
            if compression_type not in _DUCKDB_SUPPORTED_COMPRESSIONS:
                return None
            if compression_type is not None:
                all_uncompressed = False
            delimiter, has_header, has_trailing, is_empty = self._resolve_file_csv_settings(
                file_path, column_names, opts
            )
            if is_empty:
                continue
            current_framing = (delimiter, has_header, has_trailing)
            if framing is None:
                framing = current_framing
            elif framing != current_framing:
                return None
            active_files.append(file_path)

        if not active_files or framing is None:
            return None
        if opts.validate_row_count and opts.expected_row_count is None and not all_uncompressed:
            return None

        try:
            import duckdb
        except ImportError:
            return None

        delimiter, has_header, has_trailing = framing
        duck_comp = compression if compression is not None else "UNCOMPRESSED"
        quoted_files = ", ".join("'" + str(p).replace("'", "''") + "'" for p in active_files)
        col_defs = [f"'{name.replace(chr(39), chr(39) * 2)}': '{dtype}'" for name, dtype in col_types]
        if has_trailing:
            col_defs.append("'_trailing_empty': 'VARCHAR'")
        select_cols = ", ".join('"' + name.replace('"', '""') + '"' for name, _ in col_types)
        escaped_delim = delimiter.replace("'", "''")
        escaped_out = str(output_path).replace("'", "''")
        copy_sql = (
            f"COPY (SELECT {select_cols} FROM read_csv([{quoted_files}], "
            f"delim='{escaped_delim}', header={'true' if has_header else 'false'}, "
            f"quote='\"', escape='\\', nullstr='', columns={{{', '.join(col_defs)}}}, ignore_errors=false)) "
            f"TO '{escaped_out}' (FORMAT PARQUET, COMPRESSION {duck_comp}, ROW_GROUP_SIZE {int(opts.row_group_size)})"
        )

        if progress_callback:
            progress_callback(f"Reading {active_files[0].name}", 0.1)
            progress_callback("Writing Parquet file", 0.9)

        con = duckdb.connect(":memory:", config={"memory_limit": _DUCKDB_STREAM_MEMORY_LIMIT})
        try:
            row = con.execute(copy_sql).fetchone()
            copied_rows = int(row[0]) if row is not None else int(pq.read_metadata(output_path).num_rows)
        except Exception as exc:
            logger.warning(
                "DuckDB streaming Parquet path failed for %s (%s); falling back to PyArrow streaming writer",
                output_path.name,
                exc,
            )
            if output_path.exists():
                with contextlib.suppress(Exception):
                    output_path.unlink()
            return None
        finally:
            con.close()

        expected_rows = (
            opts.expected_row_count
            if opts.expected_row_count is not None
            else (
                self.count_uncompressed_data_rows(active_files, has_header=has_header)
                if opts.validate_row_count
                else copied_rows
            )
        )
        return copied_rows, expected_rows

    def _write_single_file_pyarrow(
        self,
        *,
        source_files: list[Path],
        output_path: Path,
        schema: dict[str, Any],
        arrow_schema: pa.Schema,
        opts: ConversionOptions,
        compression: str | None,
        progress_callback: Callable[[str, float], None] | None,
        source_line_counter: list[int],
    ) -> int:
        writer_kwargs: dict[str, Any] = {
            "compression": compression,
            "use_dictionary": True,
            "write_statistics": True,
        }
        if opts.data_page_version is not None:
            writer_kwargs["data_page_version"] = opts.data_page_version

        rows_read = 0
        rows_written = 0
        row_group_target = opts.row_group_size
        buffer: list[pa.RecordBatch] = []
        buffered_rows = 0

        try:
            with pq.ParquetWriter(output_path, arrow_schema, **writer_kwargs) as writer:
                for batch in self.iter_record_batches(
                    source_files,
                    schema,
                    options=opts,
                    progress_callback=progress_callback,
                    progress_start=0.0,
                    progress_end=0.8,
                    source_line_counter=source_line_counter,
                ):
                    rows_read += batch.num_rows
                    buffer.append(batch)
                    buffered_rows += batch.num_rows

                    if buffered_rows >= row_group_target:
                        if progress_callback and rows_written == 0:
                            progress_callback("Writing Parquet file", 0.9)
                        table = pa.Table.from_batches(buffer, schema=arrow_schema)
                        buffer.clear()
                        offset = 0
                        total = table.num_rows
                        while total - offset >= row_group_target:
                            chunk = table.slice(offset, row_group_target)
                            writer.write_table(chunk, row_group_size=row_group_target)
                            rows_written += chunk.num_rows
                            offset += row_group_target
                        remainder = table.slice(offset)
                        if remainder.num_rows > 0:
                            compacted = remainder.combine_chunks()
                            buffer = compacted.to_batches()
                            buffered_rows = compacted.num_rows
                        else:
                            buffered_rows = 0
                        del table, remainder
                        pa.default_memory_pool().release_unused()

                if progress_callback and rows_written == 0:
                    progress_callback("Writing Parquet file", 0.9)

                if buffer:
                    tail = pa.Table.from_batches(buffer, schema=arrow_schema)
                    buffer.clear()
                    writer.write_table(tail, row_group_size=row_group_target)
                    rows_written += tail.num_rows
                    del tail
                    pa.default_memory_pool().release_unused()
                elif rows_written == 0:
                    writer.write_table(pa.Table.from_pylist([], schema=arrow_schema), row_group_size=row_group_target)
        except ConversionError:
            if output_path.exists():
                with contextlib.suppress(Exception):
                    output_path.unlink()
            raise
        except Exception as e:
            if output_path.exists():
                with contextlib.suppress(Exception):
                    output_path.unlink()
            raise ConversionError(f"Failed to write Parquet file: {e}") from e
        return rows_read

    def _write_single_file(
        self,
        source_files: list[Path],
        schema: dict[str, Any],
        arrow_schema: pa.Schema,
        table_name: str,
        output_dir: Path,
        column_names: list[str],
        opts: ConversionOptions,
        compression: str | None,
        progress_callback: Callable[[str, float], None] | None,
    ) -> ConversionResult:
        output_path = output_dir / f"{table_name}.parquet"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        duckdb_counts = self._try_write_single_file_duckdb(
            source_files=source_files,
            output_path=output_path,
            arrow_schema=arrow_schema,
            column_names=column_names,
            opts=opts,
            compression=compression,
            progress_callback=progress_callback,
        )

        source_line_counter = [0]
        if duckdb_counts is not None:
            rows_read, expected_source_rows = duckdb_counts
            writer_name = PARQUET_WRITER_DUCKDB
        else:
            rows_read = self._write_single_file_pyarrow(
                source_files=source_files,
                output_path=output_path,
                schema=schema,
                arrow_schema=arrow_schema,
                opts=opts,
                compression=compression,
                progress_callback=progress_callback,
                source_line_counter=source_line_counter,
            )
            expected_source_rows = (
                opts.expected_row_count if opts.expected_row_count is not None else source_line_counter[0]
            )
            writer_name = PARQUET_WRITER_PYARROW

        source_size = self.calculate_file_size(source_files)
        output_size = output_path.stat().st_size
        parquet_meta = pq.read_metadata(output_path)

        if opts.validate_row_count:
            try:
                self._verify_inline_row_count(
                    expected_rows=expected_source_rows,
                    written_rows=parquet_meta.num_rows,
                    table_name=table_name,
                )
                if rows_read != expected_source_rows:
                    self._verify_inline_row_count(
                        expected_rows=expected_source_rows,
                        written_rows=rows_read,
                        table_name=table_name,
                    )
            except ConversionError:
                if output_path.exists():
                    with contextlib.suppress(Exception):
                        output_path.unlink()
                raise

        metadata = {
            "row_groups": parquet_meta.num_row_groups,
            "compression": opts.compression,
            "num_columns": len(column_names),
            "partitioned": False,
            "parquet_writer": writer_name,
        }

        if progress_callback:
            progress_callback(f"Conversion complete: {rows_read:,} rows", 1.0)

        source_format = self._detect_source_format(source_files)

        return ConversionResult(
            output_files=[output_path],
            row_count=rows_read,
            source_size_bytes=source_size,
            output_size_bytes=output_size,
            metadata=metadata,
            source_format=source_format,
            converted_at=self.get_current_timestamp(),
            conversion_options={
                "compression": opts.compression,
                "row_group_size": opts.row_group_size,
                "merge_shards": opts.merge_shards,
                "partition_cols": opts.partition_cols,
                "validate_row_count": opts.validate_row_count,
            },
        )

    def _write_partitioned(
        self,
        source_files: list[Path],
        schema: dict[str, Any],
        arrow_schema: pa.Schema,
        table_name: str,
        output_dir: Path,
        column_names: list[str],
        opts: ConversionOptions,
        compression: str | None,
        progress_callback: Callable[[str, float], None] | None,
    ) -> ConversionResult:
        partitioned_dir = output_dir / table_name
        partitioned_dir.mkdir(parents=True, exist_ok=True)

        rows_read = 0
        source_line_counter = [0]
        partition_values: dict[str, set[Any]] = {col: set() for col in opts.partition_cols}

        def _stream_batches() -> Iterator[pa.RecordBatch]:
            nonlocal rows_read
            for batch in self.iter_record_batches(
                source_files,
                schema,
                options=opts,
                progress_callback=progress_callback,
                progress_start=0.0,
                progress_end=0.8,
                source_line_counter=source_line_counter,
            ):
                rows_read += batch.num_rows
                for col in opts.partition_cols:
                    partition_values[col].update(batch.column(col).unique().to_pylist())
                yield batch

        try:
            if progress_callback:
                progress_callback(
                    f"Writing partitioned Parquet (by {', '.join(opts.partition_cols)})",
                    0.9,
                )

            partition_fields = [arrow_schema.field(col) for col in opts.partition_cols]
            partitioning = ds.partitioning(pa.schema(partition_fields), flavor="hive")
            batch_reader = pa.RecordBatchReader.from_batches(arrow_schema, _stream_batches())

            ds.write_dataset(
                batch_reader,
                partitioned_dir,
                format="parquet",
                partitioning=partitioning,
                basename_template="part-{i}.parquet",
                existing_data_behavior="overwrite_or_ignore",
                max_rows_per_group=opts.row_group_size,
                file_options=ds.ParquetFileFormat().make_write_options(
                    compression=compression,
                    write_statistics=True,
                    **({"data_page_version": opts.data_page_version} if opts.data_page_version else {}),
                ),
            )

        except ConversionError:
            if partitioned_dir.exists():
                shutil.rmtree(partitioned_dir, ignore_errors=True)
            raise
        except Exception as e:
            if partitioned_dir.exists():
                shutil.rmtree(partitioned_dir, ignore_errors=True)
            raise ConversionError(f"Failed to write partitioned Parquet: {e}") from e

        output_files = sorted(partitioned_dir.rglob("*.parquet"))

        source_size = self.calculate_file_size(source_files)
        output_size = sum(f.stat().st_size for f in output_files)

        if opts.validate_row_count:
            written_rows = sum(pq.read_metadata(f).num_rows for f in output_files)
            expected_rows = opts.expected_row_count if opts.expected_row_count is not None else source_line_counter[0]
            try:
                self._verify_inline_row_count(
                    expected_rows=expected_rows,
                    written_rows=written_rows,
                    table_name=table_name,
                )
                if rows_read != expected_rows:
                    self._verify_inline_row_count(
                        expected_rows=expected_rows,
                        written_rows=rows_read,
                        table_name=table_name,
                    )
            except ConversionError:
                if partitioned_dir.exists():
                    shutil.rmtree(partitioned_dir, ignore_errors=True)
                raise

        partition_counts = {col: len(vals) for col, vals in partition_values.items()}

        metadata = {
            "compression": opts.compression,
            "num_columns": len(column_names),
            "parquet_writer": PARQUET_WRITER_PYARROW,
            "partitioned": True,
            "partition_cols": opts.partition_cols,
            "partition_counts": partition_counts,
            "num_files": len(output_files),
            "table_path": str(partitioned_dir),
        }

        if progress_callback:
            progress_callback(
                f"Conversion complete: {rows_read:,} rows across {len(output_files)} files",
                1.0,
            )

        source_format = self._detect_source_format(source_files)

        return ConversionResult(
            output_files=[partitioned_dir],
            row_count=rows_read,
            source_size_bytes=source_size,
            output_size_bytes=output_size,
            metadata=metadata,
            source_format=source_format,
            converted_at=self.get_current_timestamp(),
            conversion_options={
                "compression": opts.compression,
                "row_group_size": opts.row_group_size,
                "merge_shards": opts.merge_shards,
                "partition_cols": opts.partition_cols,
                "validate_row_count": opts.validate_row_count,
            },
        )
