from __future__ import annotations

import io
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import pyarrow as pa
import pyarrow.csv as csv

from benchbox.utils.compression import CompressionManager
from benchbox.utils.file_format import (
    detect_compression,
    get_column_names_with_trailing,
    get_delimiter_for_file,
    has_trailing_delimiter,
)

DEFAULT_ROW_GROUP_SIZE = 122_880
DEFAULT_STREAM_BLOCK_SIZE = 1 * 1024 * 1024

_SQL_DDL_TYPE_PREFIXES = (
    "INTEGER",
    "INT",
    "BIGINT",
    "SMALLINT",
    "TINYINT",
    "HUGEINT",
    "VARCHAR",
    "CHAR",
    "TEXT",
    "DECIMAL",
    "NUMERIC",
    "DATE",
    "TIMESTAMP",
    "DATETIME",
    "FLOAT_ARRAY",
    "FLOAT",
    "REAL",
    "DOUBLE",
    "BOOLEAN",
    "BOOL",
    "ARRAY",
    "JSON",
    "BINARY",
    "BLOB",
    "UUID",
    "TIME",
    "INTERVAL",
)

_SQL_COLUMN_DEF_RE = re.compile(
    r"^\s*\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s+"
    r"([A-Za-z_][A-Za-z0-9_]*(?:\s+PRECISION)?(?:\s*\([^)]*\))?)"
    r"(?:\s+(.*))?$",
    re.IGNORECASE,
)


def _parse_sql_column_definition(column_def: str) -> dict[str, Any] | None:
    match = _SQL_COLUMN_DEF_RE.match(column_def)
    if not match:
        return None
    col_name, sql_type, constraints = match.group(1), match.group(2).strip(), (match.group(3) or "").upper()
    sql_type_upper = sql_type.upper()
    if not any(
        sql_type_upper == prefix or sql_type_upper.startswith(f"{prefix}(") or sql_type_upper.startswith(f"{prefix} ")
        for prefix in _SQL_DDL_TYPE_PREFIXES
    ):
        return None
    return {
        "name": col_name,
        "type": sql_type,
        "nullable": "NOT NULL" not in constraints,
    }


def normalize_schema_columns(schema: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not schema:
        raise SchemaError("Schema is empty or None")

    if not isinstance(schema, dict) or "columns" not in schema:
        raise SchemaError("Schema missing 'columns' field")

    columns = schema["columns"]
    if isinstance(columns, dict):
        if not columns:
            raise SchemaError("Schema 'columns' must be a non-empty list or dict")
        normalized: list[dict[str, Any]] = []
        for col_name, col_spec in columns.items():
            if not isinstance(col_spec, dict):
                raise SchemaError(f"Column '{col_name}' specification is not a dictionary")
            if "type" not in col_spec:
                raise SchemaError(f"Column '{col_name}' missing 'type' field")
            normalized.append({"name": str(col_name), **col_spec})
        return normalized

    if not columns or not isinstance(columns, list):
        raise SchemaError("Schema 'columns' must be a non-empty list")

    normalized_list: list[dict[str, Any]] = []
    for i, col in enumerate(columns):
        if isinstance(col, dict):
            if "name" not in col:
                raise SchemaError(f"Column {i} missing 'name' field")
            if "type" not in col:
                raise SchemaError(f"Column {i} ({col.get('name', 'unknown')}) missing 'type' field")
            normalized_list.append(col)
        elif isinstance(col, str):
            parsed = _parse_sql_column_definition(col)
            if parsed is None:
                raise SchemaError(f"Column {i} is not a dictionary")
            normalized_list.append(parsed)
        else:
            raise SchemaError(f"Column {i} is not a dictionary")

    return normalized_list


@dataclass
class ConversionOptions:
    compression: str = "snappy"
    row_group_size: int = DEFAULT_ROW_GROUP_SIZE
    partition_cols: list[str] = field(default_factory=list)
    merge_shards: bool = True
    output_dir: Path | None = None
    preserve_source: bool = True
    validate_row_count: bool = True
    strict_schema: bool = False
    data_page_version: Literal["1.0", "2.0"] | None = None
    delimiter: str | None = None
    has_header: bool | None = None
    expected_row_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        valid_compressions = {"snappy", "gzip", "zstd", "none", None}
        if self.compression not in valid_compressions:
            raise ValueError(f"Invalid compression: {self.compression}. Must be one of {valid_compressions}")

        if self.row_group_size <= 0:
            raise ValueError(f"row_group_size must be positive, got {self.row_group_size}")

        if self.data_page_version is not None and self.data_page_version not in ("1.0", "2.0"):
            raise ValueError(f"Invalid data_page_version: {self.data_page_version!r}. Must be '1.0' or '2.0'.")

        if self.delimiter is not None and len(self.delimiter) != 1:
            raise ValueError(f"delimiter must be a single character, got {self.delimiter!r}")

        self.validate_row_count = bool(self.validate_row_count)


class _CountingBinaryStream(io.RawIOBase):
    def __init__(self, raw_stream: Any) -> None:
        self._raw = raw_stream
        self._cur_nonspace = False
        self.nonempty_lines = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        data = self._raw.read(size)
        if data:
            self._scan(data)
        elif self._cur_nonspace:
            self.nonempty_lines += 1
            self._cur_nonspace = False
        return data

    def readinto(self, b: Any) -> int:
        data = self.read(len(b))
        n = len(data)
        b[:n] = data
        return n

    def _scan(self, data: bytes) -> None:
        if (
            b"\n\n" not in data
            and b"\r\n\r\n" not in data
            and b"\n " not in data
            and b"\n\t" not in data
            and b"\n\r" not in data
            and not data.startswith((b"\n", b"\r", b" ", b"\t"))
        ):
            nl = data.count(b"\n")
            if nl > 0:
                self.nonempty_lines += nl
                self._cur_nonspace = bool(data[data.rfind(b"\n") + 1 :].strip())
                return
            if not self._cur_nonspace and data.strip():
                self._cur_nonspace = True
            return

        start = 0
        while True:
            idx = data.find(b"\n", start)
            if idx == -1:
                if not self._cur_nonspace and data[start:].strip():
                    self._cur_nonspace = True
                break
            if self._cur_nonspace or data[start:idx].strip():
                self.nonempty_lines += 1
            self._cur_nonspace = False
            start = idx + 1


@dataclass
class ConversionResult:
    output_files: list[Path]
    row_count: int
    source_size_bytes: int
    output_size_bytes: int
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    source_format: str = "tbl"
    converted_at: str | None = None
    conversion_options: dict[str, Any] = field(default_factory=dict)

    @property
    def compression_ratio(self) -> float:
        if self.output_size_bytes == 0:
            return 0.0
        return self.source_size_bytes / self.output_size_bytes

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class ConversionError(Exception):
    pass


class SchemaError(ConversionError):
    pass


class ArrowTypeMapper:
    @staticmethod
    def map_sql_type_to_arrow(sql_type: str, *, strict: bool = False) -> pa.DataType:
        sql_type_upper = sql_type.upper().strip()

        if sql_type_upper in ("INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT"):
            return pa.int64()

        if sql_type_upper.startswith(("DECIMAL", "NUMERIC")):
            if "(" in sql_type_upper:
                try:
                    params = sql_type_upper[sql_type_upper.index("(") + 1 : sql_type_upper.index(")")].split(",")
                    precision = int(params[0].strip())
                    scale = int(params[1].strip()) if len(params) > 1 else 0
                    return pa.decimal128(precision, scale)
                except (ValueError, IndexError) as e:
                    raise SchemaError(f"Invalid DECIMAL type specification: {sql_type}") from e
            else:
                return pa.decimal128(15, 2)

        if sql_type_upper == "DATE":
            return pa.date32()

        if sql_type_upper in ("TIMESTAMP", "DATETIME") or sql_type_upper.startswith("TIMESTAMP("):
            return pa.timestamp("us")

        if sql_type_upper.startswith(("VARCHAR", "CHAR")):
            return pa.string()

        if sql_type_upper in ("FLOAT", "REAL"):
            return pa.float32()
        if sql_type_upper in ("DOUBLE", "DOUBLE PRECISION"):
            return pa.float64()

        if sql_type_upper in ("BOOLEAN", "BOOL"):
            return pa.bool_()

        if sql_type_upper in ("TEXT", "FLOAT_ARRAY"):
            return pa.string()

        if strict:
            raise SchemaError(f"Unknown SQL type '{sql_type}' cannot be mapped to Arrow type in strict mode")
        return pa.string()

    @staticmethod
    def build_arrow_schema(schema: dict[str, Any] | None, *, strict: bool = False) -> pa.Schema:
        if not schema or not isinstance(schema, dict) or "columns" not in schema:
            raise SchemaError("Schema must contain 'columns' field")
        columns = normalize_schema_columns(schema)
        fields = []

        for col in columns:
            col_name = col["name"]
            sql_type = col["type"]
            nullable = col.get("nullable", True)

            try:
                arrow_type = ArrowTypeMapper.map_sql_type_to_arrow(sql_type, strict=strict)
                field = pa.field(col_name, arrow_type, nullable=nullable)
                fields.append(field)
            except Exception as e:
                raise SchemaError(f"Failed to map column '{col_name}' with type '{sql_type}': {e}") from e

        return pa.schema(fields)


class FormatConverter(ABC):
    @abstractmethod
    def convert(
        self,
        source_files: list[Path],
        table_name: str,
        schema: dict[str, Any],
        options: ConversionOptions | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> ConversionResult: ...

    @abstractmethod
    def validate_schema(self, schema: dict[str, Any]) -> bool: ...

    def get_output_path(
        self,
        table_name: str,
        source_dir: Path,
        options: ConversionOptions | None = None,
    ) -> Path:
        opts = options or ConversionOptions()
        output_dir = opts.output_dir or source_dir

        extension = self.get_file_extension()

        return output_dir / f"{table_name}{extension}"

    @abstractmethod
    def get_file_extension(self) -> str: ...

    @abstractmethod
    def get_format_name(self) -> str: ...


class BaseFormatConverter(FormatConverter):
    def validate_source_files(self, source_files: list[Path]) -> None:
        if not source_files:
            raise ConversionError("No source files provided")

        for file_path in source_files:
            if not file_path.exists():
                raise ConversionError(f"Source file not found: {file_path}")
            if not file_path.is_file():
                raise ConversionError(f"Source path is not a file: {file_path}")

    def validate_schema(self, schema: dict[str, Any] | None) -> bool:
        normalize_schema_columns(schema)
        return True

    def _map_sql_type_to_arrow(self, sql_type: str, *, strict: bool = False) -> pa.DataType:
        return ArrowTypeMapper.map_sql_type_to_arrow(sql_type, strict=strict)

    def _build_arrow_schema(self, schema: dict[str, Any], *, strict: bool = False) -> pa.Schema:
        return ArrowTypeMapper.build_arrow_schema(schema, strict=strict)

    def _peek_nonempty_lines(self, file_path: Path, count: int = 2) -> list[str]:
        lines: list[str] = []
        compression_type = detect_compression(file_path)
        if compression_type:
            compressor = CompressionManager().get_compressor(compression_type)
            with compressor.open_for_read(file_path, mode="rt") as handle:
                for line in handle:
                    if line.strip():
                        lines.append(line.rstrip("\r\n"))
                        if len(lines) >= count:
                            break
            return lines
        with file_path.open("rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    lines.append(line.rstrip("\r\n"))
                    if len(lines) >= count:
                        break
        return lines

    @staticmethod
    def _line_matches_header(line: str, delimiter: str, column_names: list[str]) -> bool:
        tokens = [tok.strip().strip('"').lower() for tok in line.split(delimiter)]
        if len(tokens) == len(column_names) + 1 and tokens[-1] == "":
            tokens = tokens[:-1]
        return tokens == [name.lower() for name in column_names]

    def _resolve_file_csv_settings(
        self,
        file_path: Path,
        column_names: list[str],
        options: ConversionOptions | None = None,
    ) -> tuple[str, bool, bool, bool]:
        preview = self._peek_nonempty_lines(file_path, count=2)
        if not preview:
            default_delim = (
                options.delimiter if options and options.delimiter is not None else get_delimiter_for_file(file_path)
            )
            return default_delim, False, False, True

        first_line = preview[0]
        if options and options.delimiter is not None:
            delimiter = options.delimiter
        else:
            delimiter = get_delimiter_for_file(file_path)
            if delimiter == "," and len(column_names) > 1:
                comma_parts = first_line.split(",")
                pipe_parts = first_line.split("|")
                if self._line_matches_header(first_line, "|", column_names) or (
                    len(comma_parts) < len(column_names)
                    and len(pipe_parts) in (len(column_names), len(column_names) + 1)
                ):
                    delimiter = "|"

        if options and options.has_header is not None:
            has_header = bool(options.has_header)
        else:
            has_header = self._line_matches_header(first_line, delimiter, column_names)

        if has_header:
            if len(preview) < 2:
                return delimiter, True, False, True
            data_line = preview[1]
            data_parts = data_line.split(delimiter)
            has_trailing = len(data_parts) > len(column_names) and data_line.endswith(delimiter)
        else:
            has_trailing = has_trailing_delimiter(file_path, delimiter, column_names)

        return delimiter, has_header, has_trailing, False

    def iter_record_batches(
        self,
        source_files: list[Path],
        schema: dict[str, Any],
        *,
        options: ConversionOptions | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
        progress_start: float = 0.0,
        progress_end: float = 0.8,
        block_size: int = DEFAULT_STREAM_BLOCK_SIZE,
        source_line_counter: list[int] | None = None,
    ) -> Iterator[pa.RecordBatch]:
        strict = bool(options.strict_schema) if options is not None else False
        arrow_schema = self._build_arrow_schema(schema, strict=strict)
        column_names = [field.name for field in arrow_schema]
        total_files = len(source_files)
        progress_range = progress_end - progress_start
        compression_manager = CompressionManager()

        try:
            for i, file_path in enumerate(source_files):
                if progress_callback:
                    progress = progress_start + (i / total_files) * progress_range
                    progress_callback(f"Reading {file_path.name}", progress)

                delimiter, has_header, has_trailing, is_empty = self._resolve_file_csv_settings(
                    file_path, column_names, options
                )
                if is_empty:
                    continue

                column_names_for_file = get_column_names_with_trailing(column_names, has_trailing)

                read_options = csv.ReadOptions(
                    column_names=column_names_for_file,
                    autogenerate_column_names=False,
                    skip_rows=1 if has_header else 0,
                    block_size=block_size,
                )

                parse_options = csv.ParseOptions(
                    delimiter=delimiter,
                    quote_char='"',
                    escape_char="\\",
                )

                convert_options = csv.ConvertOptions(
                    column_types=arrow_schema,
                    null_values=[""],
                    strings_can_be_null=True,
                    include_columns=column_names,
                )

                compression_type = detect_compression(file_path)
                if compression_type:
                    compressor = compression_manager.get_compressor(compression_type)
                    with compressor.open_for_read(file_path, mode="rb") as raw_stream:
                        counting_stream = _CountingBinaryStream(raw_stream)
                        reader = csv.open_csv(
                            counting_stream,
                            read_options=read_options,
                            parse_options=parse_options,
                            convert_options=convert_options,
                        )
                        for batch in reader:
                            if batch.num_rows > 0:
                                yield (
                                    pa.RecordBatch.from_arrays(batch.columns, schema=arrow_schema)
                                    if batch.schema != arrow_schema
                                    else batch
                                )
                        if source_line_counter is not None:
                            data_lines = counting_stream.nonempty_lines - (1 if has_header else 0)
                            source_line_counter[0] += max(0, data_lines)
                else:
                    with open(file_path, "rb") as raw_stream:
                        counting_stream = _CountingBinaryStream(raw_stream)
                        reader = csv.open_csv(
                            counting_stream,
                            read_options=read_options,
                            parse_options=parse_options,
                            convert_options=convert_options,
                        )
                        for batch in reader:
                            if batch.num_rows > 0:
                                yield (
                                    pa.RecordBatch.from_arrays(batch.columns, schema=arrow_schema)
                                    if batch.schema != arrow_schema
                                    else batch
                                )
                        if source_line_counter is not None:
                            data_lines = counting_stream.nonempty_lines - (1 if has_header else 0)
                            source_line_counter[0] += max(0, data_lines)

        except Exception as e:
            raise ConversionError(f"Failed to read TBL files: {e}") from e

    def read_tbl_files(
        self,
        source_files: list[Path],
        schema: dict[str, Any],
        progress_callback: Callable[[str, float], None] | None = None,
        progress_start: float = 0.0,
        progress_end: float = 0.8,
        options: ConversionOptions | None = None,
        source_line_counter: list[int] | None = None,
    ) -> pa.Table:
        strict = bool(options.strict_schema) if options is not None else False
        arrow_schema = self._build_arrow_schema(schema, strict=strict)
        batches = list(
            self.iter_record_batches(
                source_files,
                schema,
                options=options,
                progress_callback=progress_callback,
                progress_start=progress_start,
                progress_end=progress_end,
                source_line_counter=source_line_counter,
            )
        )
        try:
            if not batches:
                return pa.Table.from_pylist([], schema=arrow_schema)
            if len(source_files) > 1 and progress_callback:
                progress_callback("Merging sharded files", progress_end)
            return pa.Table.from_batches(batches, schema=arrow_schema)
        except Exception as e:
            raise ConversionError(f"Failed to concatenate tables: {e}") from e

    def calculate_file_size(self, file_paths: list[Path]) -> int:
        return sum(f.stat().st_size for f in file_paths if f.exists())

    def count_uncompressed_data_rows(self, file_paths: list[Path], *, has_header: bool = False) -> int:
        total = 0
        for file_path in file_paths:
            with open(file_path, "rb") as raw_stream:
                counting_stream = _CountingBinaryStream(raw_stream)
                while counting_stream.read(4 * 1024 * 1024):
                    pass
            data_lines = counting_stream.nonempty_lines - (1 if has_header else 0)
            total += max(0, data_lines)
        return total

    def _verify_inline_row_count(
        self,
        *,
        expected_rows: int,
        written_rows: int,
        table_name: str,
    ) -> None:
        if expected_rows != written_rows:
            raise ConversionError(
                f"Row count mismatch for table '{table_name}': "
                f"input={expected_rows:,} rows, output={written_rows:,} rows. "
                f"Data loss detected during conversion! "
                f"This violates TPC compliance and indicates a serious conversion error."
            )

    def _detect_source_format(self, source_files: list[Path]) -> str:
        if not source_files:
            return "tbl"

        first_file = source_files[0]
        suffixes = first_file.suffixes
        name_lower = first_file.name.lower()

        if ".parquet" in suffixes:
            return "parquet"
        elif ".tbl" in suffixes or name_lower.endswith(".tbl") or ".dat" in suffixes or name_lower.endswith(".dat"):
            return "tbl"
        elif ".csv" in suffixes or name_lower.endswith(".csv"):
            return "csv"
        else:
            return "tbl"

    @staticmethod
    def get_current_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()
