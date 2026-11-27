from __future__ import annotations

import contextlib
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from benchbox.utils.datagen_version import PARQUET_WRITER, PARTITIONED_PARQUET_WRITER
from benchbox.utils.format_converters.base import (
    BaseFormatConverter,
    ConversionError,
    ConversionOptions,
    ConversionResult,
    SchemaError,
    normalize_schema_columns,
)


def _row_groups(batches: Iterator[pa.RecordBatch], schema: pa.Schema, size: int) -> Iterator[pa.Table]:
    buffer: list[pa.RecordBatch] = []
    buffered_rows = 0
    for batch in batches:
        buffer.append(batch)
        buffered_rows += batch.num_rows
        if buffered_rows < size:
            continue
        table = pa.Table.from_batches(buffer, schema=schema).combine_chunks()
        buffer = []
        offset = 0
        while table.num_rows - offset >= size:
            yield table.slice(offset, size)
            offset += size
        buffered_rows = table.num_rows - offset
        if buffered_rows:
            buffer = table.slice(offset).combine_chunks().to_batches()
        del table
    if buffered_rows:
        yield pa.Table.from_batches(buffer, schema=schema).combine_chunks()


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

        def _count_rows() -> Iterator[pa.RecordBatch]:
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
                yield batch

        try:
            with pq.ParquetWriter(output_path, arrow_schema, **writer_kwargs) as writer:
                for group in _row_groups(_count_rows(), arrow_schema, row_group_target):
                    if progress_callback and rows_written == 0:
                        progress_callback("Writing Parquet file", 0.9)
                    writer.write_table(group, row_group_size=row_group_target)
                    rows_written += group.num_rows
                    pa.default_memory_pool().release_unused()

                if rows_written == 0:
                    if progress_callback:
                        progress_callback("Writing Parquet file", 0.9)
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

        source_line_counter = [0]
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
            "parquet_writer": PARQUET_WRITER,
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
                preserve_order=True,
                min_rows_per_group=opts.row_group_size,
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
            "parquet_writer": PARTITIONED_PARQUET_WRITER,
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
