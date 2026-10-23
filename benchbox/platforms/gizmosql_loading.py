from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq

from benchbox.utils.compression import CompressionManager
from benchbox.utils.file_format import (
    TRAILING_DUMMY_COLUMN,
    detect_compression,
    get_column_names_with_trailing,
    has_trailing_delimiter,
    is_parquet_format,
    is_tpc_format,
)

if TYPE_CHECKING:
    from benchbox.platforms.base.data_loading import CsvDialect

DEFAULT_INGEST_BATCH_BYTES = 8 * 1024 * 1024
MAX_INGEST_BATCH_BYTES = 15 * 1024 * 1024
MIN_INGEST_BATCH_BYTES = 64 * 1024
PARQUET_READ_ROWS = 65536
BOOLEAN_TRUE_VALUES = ["1", "True", "true", "TRUE", "t", "T"]
BOOLEAN_FALSE_VALUES = ["0", "False", "false", "FALSE", "f", "F"]


def plain_schema(schema: pa.Schema) -> pa.Schema:
    return pa.schema([pa.field(field.name, field.type, field.nullable) for field in schema])


def open_binary(path: Path) -> BinaryIO:
    compression = detect_compression(path) or "none"
    return cast(BinaryIO, CompressionManager().get_compressor(compression).open_for_read(path, mode="rb"))


def bounded_batches(batches: Iterable[pa.RecordBatch], max_batch_bytes: int) -> Iterator[pa.RecordBatch]:
    for batch in batches:
        pending = [batch]
        while pending:
            current = pending.pop()
            if current.num_rows <= 1 or current.nbytes <= max_batch_bytes:
                if current.num_rows:
                    yield current
                continue
            rows_per_slice = max(1, current.num_rows * max_batch_bytes // current.nbytes)
            if rows_per_slice >= current.num_rows:
                rows_per_slice = current.num_rows // 2
            slices = [
                current.slice(offset, min(rows_per_slice, current.num_rows - offset))
                for offset in range(0, current.num_rows, rows_per_slice)
            ]
            pending.extend(reversed(slices))


def _csv_column_names(path: Path, schema: pa.Schema, dialect: CsvDialect) -> tuple[list[str], list[str]]:
    names = list(schema.names)
    if (is_tpc_format(path) or dialect.delimiter == "|") and has_trailing_delimiter(path, dialect.delimiter, names):
        return get_column_names_with_trailing(names, True), names
    return names, names


def _csv_options(
    path: Path, schema: pa.Schema, dialect: CsvDialect, max_batch_bytes: int
) -> tuple[pa_csv.ReadOptions, pa_csv.ParseOptions, pa_csv.ConvertOptions]:
    file_columns, wanted = _csv_column_names(path, schema, dialect)
    column_types = {field.name: field.type for field in schema}
    if TRAILING_DUMMY_COLUMN in file_columns:
        column_types[TRAILING_DUMMY_COLUMN] = pa.string()
    read_options = pa_csv.ReadOptions(
        column_names=file_columns,
        skip_rows=1 if dialect.has_header else 0,
        block_size=max_batch_bytes,
        encoding="utf8",
    )
    quote_char = dialect.quote if dialect.quote else (False if is_tpc_format(path) else '"')
    parse_options = pa_csv.ParseOptions(
        delimiter=dialect.delimiter,
        quote_char=quote_char,
        double_quote=bool(quote_char),
        escape_char=False,
        newlines_in_values=False,
    )
    null_values = [dialect.null_marker] if dialect.null_marker is not None else []
    convert_options = pa_csv.ConvertOptions(
        column_types=column_types,
        include_columns=wanted,
        null_values=null_values,
        strings_can_be_null=dialect.null_marker is not None,
        true_values=BOOLEAN_TRUE_VALUES,
        false_values=BOOLEAN_FALSE_VALUES,
    )
    return read_options, parse_options, convert_options


def delimited_batches(
    path: Path, schema: pa.Schema, dialect: CsvDialect, max_batch_bytes: int = DEFAULT_INGEST_BATCH_BYTES
) -> Iterator[pa.RecordBatch]:
    read_options, parse_options, convert_options = _csv_options(path, schema, dialect, max_batch_bytes)
    with open_binary(path) as stream:
        reader = pa_csv.open_csv(
            stream, read_options=read_options, parse_options=parse_options, convert_options=convert_options
        )
        target = plain_schema(schema)
        for batch in bounded_batches(reader, max_batch_bytes):
            yield pa.RecordBatch.from_arrays(batch.columns, schema=target)


def parquet_batches(
    path: Path, schema: pa.Schema, max_batch_bytes: int = DEFAULT_INGEST_BATCH_BYTES
) -> Iterator[pa.RecordBatch]:
    parquet_file = pq.ParquetFile(path)
    by_lower = {name.lower(): name for name in parquet_file.schema_arrow.names}
    missing = [name for name in schema.names if name.lower() not in by_lower]
    if missing:
        raise ValueError(f"Parquet file {path.name} is missing columns: {', '.join(missing)}")
    source_columns = [by_lower[name.lower()] for name in schema.names]
    target = plain_schema(schema)
    for batch in bounded_batches(
        parquet_file.iter_batches(batch_size=PARQUET_READ_ROWS, columns=source_columns), max_batch_bytes
    ):
        renamed = pa.Table.from_batches([batch]).rename_columns(schema.names).cast(target)
        yield from renamed.to_batches()


def file_batches(
    path: Path, schema: pa.Schema, dialect: CsvDialect | None, max_batch_bytes: int = DEFAULT_INGEST_BATCH_BYTES
) -> Iterator[pa.RecordBatch]:
    if is_parquet_format(path):
        yield from parquet_batches(path, schema, max_batch_bytes)
        return
    if dialect is None:
        raise ValueError(f"No CSV dialect resolved for {path.name}")
    yield from delimited_batches(path, schema, dialect, max_batch_bytes)


def stream_reader(schema: pa.Schema, batches: Iterable[pa.RecordBatch]) -> pa.RecordBatchReader:
    return pa.RecordBatchReader.from_batches(plain_schema(schema), batches)
