from __future__ import annotations

import ast
import bz2
import contextlib
import csv
import gzip
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Protocol

from benchbox.core.loaded_tables import is_data_loading_skipped
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.file_format import (
    get_column_names_with_trailing,
    get_data_extension,
    get_delimiter_for_file,
    has_trailing_delimiter,
)
from benchbox.utils.printing import quiet_console

logger = logging.getLogger(__name__)


def normalize_table_paths(table_paths: Any) -> list[Path]:
    normalized = table_paths if isinstance(table_paths, list) else [table_paths]
    return [Path(path_like) for path_like in normalized]


_VALID_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_MAX_IDENTIFIER_LENGTH = 128


class DataLoadingError(Exception):
    pass


class ClickHouseServerLoadError(DataLoadingError):
    def __init__(
        self,
        table_name: str,
        source_files: list[Path],
        rows_attempted: int,
        cause: BaseException,
    ) -> None:
        self.table_name = table_name
        self.source_files = tuple(source_files)
        self.rows_attempted = rows_attempted
        self.cause = cause
        sources = ", ".join(path.name for path in source_files)
        super().__init__(
            f"ClickHouse server load failed for {table_name!r} after {rows_attempted:,} streamed row(s) "
            f"from {sources}: {cause}"
        )


def validate_sql_identifier(name: str, context: str = "identifier") -> str:
    if not name:
        raise DataLoadingError(f"Empty {context} is not allowed")

    if len(name) > _MAX_IDENTIFIER_LENGTH:
        raise DataLoadingError(
            f"Invalid {context} '{name[:20]}...': exceeds maximum length of {_MAX_IDENTIFIER_LENGTH}"
        )

    if not _VALID_IDENTIFIER_PATTERN.match(name):
        raise DataLoadingError(
            f"Invalid {context} '{name}': must contain only letters, digits, and underscores, "
            f"and must start with a letter or underscore"
        )

    return name


def escape_sql_string_literal(value: str) -> str:
    return value.replace("'", "''")


@dataclass
class DataSource:
    source_type: str
    tables: dict[str, Any]
    table_formats: dict[str, str] = None
    table_metadata: dict[str, dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.table_formats is None:
            self.table_formats = {}
        if self.table_metadata is None:
            self.table_metadata = {}


@dataclass
class CsvDialect:
    delimiter: str
    has_header: bool
    null_marker: str | None
    normalize_booleans: bool
    quote: str | None


NO_BENCHMARK: Any = object()


def resolve_csv_dialect(
    data_source: DataSource,
    table_name: str,
    file_path: Path,
    benchmark: Any,
) -> CsvDialect:
    name_lower = table_name.lower()

    benchmark_delimiter = _get_optional_str_attr(benchmark, "csv_delimiter")
    benchmark_header = _get_optional_bool_attr(benchmark, "csv_has_header")
    benchmark_booleans = _get_optional_bool_attr(benchmark, "csv_normalize_booleans")
    benchmark_null_marker = _get_optional_str_attr(benchmark, "csv_null_marker")
    ext = get_data_extension(file_path)
    is_tpc = ext in (".tbl", ".dat")
    format_delimiter = "|" if is_tpc else get_delimiter_for_file(file_path)
    format_null_marker = "" if is_tpc else None

    meta = data_source.table_metadata.get(name_lower)
    if meta is not None:
        return CsvDialect(
            delimiter=meta.get("csv_delimiter", benchmark_delimiter or format_delimiter),
            has_header=bool(meta.get("csv_has_header", benchmark_header if benchmark_header is not None else False)),
            null_marker=(
                meta["csv_null_marker"]
                if "csv_null_marker" in meta
                else benchmark_null_marker
                if benchmark_null_marker is not None
                else format_null_marker
            ),
            normalize_booleans=bool(
                meta.get(
                    "csv_normalize_booleans",
                    benchmark_booleans if benchmark_booleans is not None else False,
                )
            ),
            quote=meta.get("csv_quote", None),
        )

    if any(v is not None for v in (benchmark_delimiter, benchmark_header, benchmark_booleans, benchmark_null_marker)):
        logger.warning(
            "table '%s': CSV dialect from benchmark attributes (no manifest metadata). "
            "Annotate the generator with manifest metadata to suppress this warning.",
            table_name,
        )
        return CsvDialect(
            delimiter=benchmark_delimiter if benchmark_delimiter is not None else format_delimiter,
            has_header=bool(benchmark_header) if benchmark_header is not None else False,
            null_marker=benchmark_null_marker if benchmark_null_marker is not None else format_null_marker,
            normalize_booleans=bool(benchmark_booleans) if benchmark_booleans is not None else False,
            quote=None,
        )

    logger.warning(
        "table '%s': CSV dialect from file extension heuristic (no manifest metadata or benchmark attributes). "
        "Annotate the generator with manifest metadata to suppress this warning.",
        table_name,
    )
    if ext in (".tbl", ".dat"):
        return CsvDialect(
            delimiter="|",
            has_header=False,
            null_marker="",
            normalize_booleans=False,
            quote=None,
        )
    return CsvDialect(
        delimiter=get_delimiter_for_file(file_path),
        has_header=False,
        null_marker=None,
        normalize_booleans=False,
        quote=None,
    )


def _get_optional_str_attr(obj: Any, name: str) -> str | None:
    value = getattr(obj, name, None)
    return value if isinstance(value, str) else None


def _get_optional_bool_attr(obj: Any, name: str) -> bool | None:
    value = getattr(obj, name, None)
    return value if isinstance(value, bool) else None


class DataSourceProvider(Protocol):
    def can_provide(self, benchmark: Any, data_dir: Path) -> bool: ...

    def get_data_source(self, benchmark: Any, data_dir: Path) -> DataSource | None: ...


class BenchmarkTablesSource:
    def can_provide(self, benchmark: Any, data_dir: Path) -> bool:
        if not hasattr(benchmark, "tables"):
            return False

        tables = benchmark.tables
        if not tables or not hasattr(tables, "items") or not callable(tables.items):
            return False

        try:
            iter(tables.items())
        except Exception:
            return False

        return True

    def get_data_source(self, benchmark: Any, data_dir: Path) -> DataSource | None:
        if self.can_provide(benchmark, data_dir):
            normalized_tables = {}
            for table_name, table_path in benchmark.tables.items():
                if isinstance(table_path, list):
                    normalized_tables[table_name] = table_path
                else:
                    normalized_tables[table_name] = [table_path]
            return DataSource(source_type="benchmark_tables", tables=normalized_tables)
        return None


class BenchmarkImplTablesSource:
    def can_provide(self, benchmark: Any, data_dir: Path) -> bool:
        if not hasattr(benchmark, "_impl") or not hasattr(benchmark._impl, "tables"):
            return False

        tables = benchmark._impl.tables
        if not tables or not hasattr(tables, "items") or not callable(tables.items):
            return False

        try:
            iter(tables.items())
        except Exception:
            return False

        return True

    def get_data_source(self, benchmark: Any, data_dir: Path) -> DataSource | None:
        if self.can_provide(benchmark, data_dir):
            normalized_tables = {}
            for table_name, table_path in benchmark._impl.tables.items():
                if isinstance(table_path, list):
                    normalized_tables[table_name] = table_path
                else:
                    normalized_tables[table_name] = [table_path]
            return DataSource(source_type="benchmark_impl_tables", tables=normalized_tables)
        return None


class ManifestFileSource:
    def __init__(
        self,
        platform_name: str = "duckdb",
        table_mode: str = "native",
        platform_config: dict[str, Any] | None = None,
        requested_format: str | None = None,
    ):
        self._platform_name = platform_name
        self._table_mode = table_mode
        self._platform_config = platform_config
        self._requested_format = (
            requested_format.strip().lower() if requested_format and requested_format.strip() else None
        )

    def can_provide(self, benchmark: Any, data_dir: Path) -> bool:
        manifest_path = Path(data_dir) / "_datagen_manifest.json"
        return manifest_path.exists()

    def get_data_source(self, benchmark: Any, data_dir: Path) -> DataSource | None:
        try:
            manifest_path = Path(data_dir) / "_datagen_manifest.json"

            v2_source = self._try_manifest_v2(manifest_path, benchmark, data_dir)
            if v2_source is not None:
                return v2_source

            return self._try_manifest_v1(manifest_path, data_dir)

        except Exception as e:
            logger.debug(f"Failed to load manifest file: {e}")

        return None

    @staticmethod
    def _prefer_platform_defaults(platform_name: str, table_mode: str) -> bool:
        normalized_mode = (table_mode or "native").strip().lower()
        return normalized_mode == "native"

    def _resolve_format_for_table(self, manifest: Any, table_name: str, get_preferred_format: Any) -> str | None:
        if self._requested_format:
            table_formats_obj = manifest.tables.get(table_name)
            available = list((table_formats_obj.formats or {}).keys()) if table_formats_obj else []
            if self._requested_format in available:
                return self._requested_format
        return get_preferred_format(
            manifest,
            table_name,
            self._platform_name,
            table_mode=self._table_mode,
            platform_config=self._platform_config,
            prefer_platform_defaults=self._prefer_platform_defaults(self._platform_name, self._table_mode),
        )

    def _try_manifest_v2(self, manifest_path: Path, benchmark: Any, data_dir: Path) -> DataSource | None:
        try:
            from benchbox.core.manifest import ManifestV2, get_files_for_format, get_preferred_format, load_manifest

            manifest = load_manifest(manifest_path)

            if not isinstance(manifest, ManifestV2):
                return None

            mapping = {}
            formats_mapping: dict[str, str] = {}
            table_metadata: dict[str, dict[str, Any]] = {}
            preferred_format = None

            for table_name in manifest.tables.keys():
                preferred_format = self._resolve_format_for_table(manifest, table_name, get_preferred_format)

                table_formats_obj = manifest.tables[table_name]
                if preferred_format:
                    files = get_files_for_format(manifest, table_name, preferred_format)
                    if files:
                        mapping[table_name] = [Path(data_dir) / f for f in files]
                        formats_mapping[table_name.lower()] = preferred_format.lower()
                    format_entries = table_formats_obj.formats.get(preferred_format, [])
                    if format_entries and format_entries[0].metadata:
                        table_metadata[table_name.lower()] = dict(format_entries[0].metadata)
                else:
                    for _format_name, format_files in table_formats_obj.formats.items():
                        if format_files:
                            mapping[table_name] = [Path(data_dir) / f.path for f in format_files]
                            formats_mapping[table_name.lower()] = _format_name.lower()
                            if format_files[0].metadata:
                                table_metadata[table_name.lower()] = dict(format_files[0].metadata)
                            break

            if mapping:
                quiet_console.print(
                    f"Using data files from _datagen_manifest.json (v2, format: {preferred_format or 'auto'})"
                )
                return DataSource(
                    source_type="manifest_v2",
                    tables=mapping,
                    table_formats=formats_mapping,
                    table_metadata=table_metadata,
                )

        except ImportError:
            pass
        except Exception as e:
            logger.debug("_try_manifest_v2 failed with non-import error, falling back to v1: %s", e)

        return None

    @staticmethod
    def _try_manifest_v1(manifest_path: Path, data_dir: Path) -> DataSource | None:
        with open(manifest_path, encoding="utf-8") as f:
            manifest_dict = json.load(f)

        tables = manifest_dict.get("tables") or {}
        mapping = {}
        for table, entries in tables.items():
            if entries:
                table_files = []
                for entry in entries:
                    rel = entry.get("path")
                    if rel:
                        table_files.append(Path(data_dir) / rel)
                if table_files:
                    mapping[table] = table_files

        if mapping:
            quiet_console.print("Using data files from _datagen_manifest.json (v1)")
            return DataSource(source_type="manifest", tables=mapping)

        return None

    def read_format_hints(
        self,
        manifest_path: Path,
        benchmark: Any,
        table_names: list[str],
    ) -> dict[str, str]:
        if not manifest_path.exists():
            return {}
        try:
            from benchbox.core.manifest import ManifestV2, get_preferred_format, load_manifest

            manifest = load_manifest(manifest_path)
            if not isinstance(manifest, ManifestV2):
                return {}
            result: dict[str, str] = {}
            for table_name in table_names:
                fmt = self._resolve_format_for_table(manifest, table_name, get_preferred_format)
                if fmt:
                    result[table_name.lower()] = fmt.lower()
            return result
        except Exception:
            return {}

    def read_table_metadata_hints(
        self,
        manifest_path: Path,
        table_names: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not manifest_path.exists():
            return {}
        try:
            from benchbox.core.manifest import ManifestV2, get_preferred_format, load_manifest

            manifest = load_manifest(manifest_path)
            if not isinstance(manifest, ManifestV2):
                return {}
            result: dict[str, dict[str, Any]] = {}
            for table_name in table_names:
                fmt = self._resolve_format_for_table(manifest, table_name, get_preferred_format)
                table_formats_obj = manifest.tables.get(table_name)
                if not table_formats_obj:
                    continue
                entries = table_formats_obj.formats.get(fmt or "", []) if fmt else []
                if not entries:
                    for format_files in table_formats_obj.formats.values():
                        if format_files:
                            entries = format_files
                            break
                if entries and entries[0].metadata:
                    result[table_name.lower()] = dict(entries[0].metadata)
            return result
        except Exception:
            return {}


class DataSourceResolver:
    def __init__(
        self,
        platform_name: str | None = None,
        table_mode: str | None = None,
        platform_config: dict[str, Any] | None = None,
        requested_format: str | None = None,
    ):
        self._manifest_source = ManifestFileSource(
            platform_name=platform_name or "duckdb",
            table_mode=table_mode or "native",
            platform_config=platform_config,
            requested_format=requested_format,
        )

        self.providers = [
            BenchmarkTablesSource(),
            BenchmarkImplTablesSource(),
            self._manifest_source,
        ]

    def get_manifest_data_source(self, benchmark: Any, data_dir: Path) -> DataSource | None:
        return self._manifest_source.get_data_source(benchmark, data_dir)

    @staticmethod
    def _normalize_paths(table_paths: Any) -> list[Path]:
        return normalize_table_paths(table_paths)

    @staticmethod
    def _get_case_insensitive(mapping: dict[str, Any], key: str) -> Any:
        return mapping.get(key, mapping.get(key.lower()))

    @staticmethod
    def _infer_format_from_paths(paths: set[Path]) -> str | None:
        inferred = [get_data_extension(path) for path in sorted(paths)]
        inferred = [ext[1:] for ext in inferred if ext]
        return inferred[0] if inferred else None

    def _select_manifest_override_tables(
        self,
        source: DataSource,
        benchmark: Any,
        data_dir: Path,
    ) -> None:
        if source.source_type not in {"benchmark_tables", "benchmark_impl_tables"}:
            return

        platform_name = str(getattr(self._manifest_source, "_platform_name", "") or "").strip().lower()
        table_mode = str(getattr(self._manifest_source, "_table_mode", "native") or "native").strip().lower()

        if platform_name == "athena" and table_mode == "external":
            selector = lambda paths: any(path.suffix.lower() != ".parquet" for path in paths)
        elif platform_name == "redshift" and table_mode != "external":
            selector = lambda paths: any(path.exists() and path.is_dir() for path in paths)
        elif platform_name == "bigquery" and table_mode == "native":
            selector = lambda paths: bool(paths)
        else:
            return

        manifest_source = None
        for table_name, table_paths in list(source.tables.items()):
            if not selector(self._normalize_paths(table_paths)):
                continue

            if manifest_source is None:
                manifest_source = self.get_manifest_data_source(benchmark, data_dir)
                if manifest_source is None or not manifest_source.tables:
                    return

            replacement = self._get_case_insensitive(manifest_source.tables, table_name)
            if replacement is None:
                continue

            if platform_name == "bigquery":
                current_paths = set(self._normalize_paths(table_paths))
                replacement_paths = set(self._normalize_paths(replacement))
                replacement_format = self._get_case_insensitive(manifest_source.table_formats, table_name)
                if replacement_format is None:
                    replacement_format = self._infer_format_from_paths(replacement_paths)
                if replacement_format != "tbl" or not current_paths < replacement_paths:
                    continue

            source.tables[table_name] = replacement
            replacement_format = self._get_case_insensitive(manifest_source.table_formats, table_name)
            if replacement_format is None and platform_name == "bigquery":
                replacement_format = self._infer_format_from_paths(set(self._normalize_paths(replacement)))
            if replacement_format:
                source.table_formats[table_name.lower()] = str(replacement_format).lower()

    def resolve(self, benchmark: Any, data_dir: Path) -> DataSource | None:
        source = None
        for provider in self.providers:
            source = provider.get_data_source(benchmark, data_dir)
            if source:
                break

        if source is None:
            return None

        manifest_path = Path(data_dir) / "_datagen_manifest.json"
        table_names = list(source.tables.keys())
        if not source.table_formats:
            source.table_formats = self._manifest_source.read_format_hints(manifest_path, benchmark, table_names)
        if not source.table_metadata:
            source.table_metadata = self._manifest_source.read_table_metadata_hints(manifest_path, table_names)

        self._select_manifest_override_tables(source, benchmark, data_dir)

        return source


def resolve_adapter_data_source(adapter: Any, benchmark: Any, data_dir: Path) -> DataSource:
    resolver = DataSourceResolver(
        platform_name=adapter.platform_name,
        table_mode=adapter.table_mode,
        platform_config=adapter.platform_config,
        requested_format=adapter.requested_table_format,
    )
    data_source = resolver.resolve(benchmark, data_dir)
    if not data_source or not data_source.tables:
        raise ValueError("No data files found. Ensure benchmark.generate_data() was called first.")
    return data_source


class CompressionHandler(ABC):
    @abstractmethod
    @contextmanager
    def open(self, file_path: Path, newline: str | None = None) -> Iterator[Any]:
        pass


class GzipHandler(CompressionHandler):
    @contextmanager
    def open(self, file_path: Path, newline: str | None = None) -> Iterator[Any]:
        with gzip.open(file_path, "rt", newline=newline) as f:
            yield f


class Bzip2Handler(CompressionHandler):
    @contextmanager
    def open(self, file_path: Path, newline: str | None = None) -> Iterator[Any]:
        with bz2.open(file_path, "rt", newline=newline) as f:
            yield f


class ZstdHandler(CompressionHandler):
    def __init__(self, adapter: Any = None):
        self.adapter = adapter

    @contextmanager
    def open(self, file_path: Path, newline: str | None = None) -> Iterator[Any]:
        if shutil.which("zstd") is None:
            raise DataLoadingError(
                f"Cannot load zstd-compressed file '{file_path.name}': the 'zstd' command was not found "
                "on PATH. Install it (e.g. 'brew install zstd' or 'apt-get install zstd') or regenerate "
                "uncompressed data."
            )

        if self.adapter and hasattr(self.adapter, "log_verbose"):
            self.adapter.log_verbose(f"Decompressing {file_path.name} using system zstd command...")

        temp_fd, temp_file_path = tempfile.mkstemp(suffix=".csv")
        os.close(temp_fd)

        try:
            with open(temp_file_path, "w", encoding="utf-8") as temp_file:
                subprocess.run(
                    ["zstd", "-d", str(file_path), "-c"],
                    stdout=temp_file,
                    check=True,
                    text=True,
                )

            if self.adapter and hasattr(self.adapter, "log_very_verbose"):
                self.adapter.log_very_verbose(f"Decompressed to temporary file: {temp_file_path}")

            with open(temp_file_path, encoding="utf-8", newline=newline) as f:
                yield f

        finally:
            with contextlib.suppress(Exception):
                os.unlink(temp_file_path)


class NoCompressionHandler(CompressionHandler):
    @contextmanager
    def open(self, file_path: Path, newline: str | None = None) -> Iterator[Any]:
        with open(file_path, encoding="utf-8", newline=newline) as f:
            yield f


def _resolve_table_schema(schema: Any, table_name: str) -> Any:
    if not isinstance(schema, dict):
        return {}
    return schema.get(table_name, schema.get(table_name.lower(), {}))


def _schema_table_columns(table_schema: Any) -> list[Any] | None:
    if isinstance(table_schema, dict):
        columns = table_schema.get("columns")
    else:
        columns = getattr(table_schema, "columns", None)
    return columns if isinstance(columns, list) else None


class SchemaInspector:
    @staticmethod
    def get_column_count(benchmark: Any, table_name: str, file_handle: Any, delimiter: str) -> int | None:
        schema = benchmark.get_schema() if hasattr(benchmark, "get_schema") else {}
        table_schema = _resolve_table_schema(schema, table_name)

        columns = _schema_table_columns(table_schema)
        if columns is not None:
            return len(columns)

        first_line = file_handle.readline().strip()
        if first_line:
            column_count = len(first_line.split(delimiter))
            file_handle.seek(0)
            return column_count

        return None


_TEXT_TYPE_PREFIXES = ("CHAR", "VARCHAR", "NCHAR", "NVARCHAR", "TEXT", "STRING", "CLOB", "BPCHAR", "CHARACTER")


def _column_field(column: Any, key: str) -> Any:
    if isinstance(column, dict):
        return column.get(key)
    return getattr(column, key, None)


def _is_text_type(type_name: Any) -> bool:
    return isinstance(type_name, str) and type_name.strip().upper().startswith(_TEXT_TYPE_PREFIXES)


def resolve_null_columns(
    benchmark: Any, table_name: str, column_count: int, *, include_text: bool
) -> list[bool] | None:
    schema = benchmark.get_schema() if hasattr(benchmark, "get_schema") else {}
    columns = _schema_table_columns(_resolve_table_schema(schema, table_name))
    if columns is None or len(columns) != column_count:
        return None

    mask: list[bool] = []
    for column in columns:
        nullable = _column_field(column, "nullable")
        if nullable is None:
            nullable = not _column_field(column, "primary_key")
        text = _is_text_type(_column_field(column, "type"))
        mask.append(bool(nullable) and (include_text or not text))
    return mask if any(mask) else None


class RowBatchProcessor:
    def __init__(self, batch_size: int = 1000):
        self.batch_size = batch_size

    def process_file(
        self,
        file_handle: Any,
        delimiter: str,
        column_count: int,
        null_columns: Sequence[bool] | None = None,
        null_token: str = "",
        quote: str | None = None,
        has_header: bool = False,
    ) -> Iterator[tuple[list[tuple], int]]:
        batch_data = []
        row_count = 0
        null_indexes = [i for i, flag in enumerate(null_columns) if flag] if null_columns else []

        records = self._iter_records(file_handle, delimiter, quote)
        if has_header:
            next(records, None)

        for fields in records:
            while len(fields) < column_count:
                fields.append("")

            fields = fields[:column_count]

            for index in null_indexes:
                if fields[index] == null_token:
                    fields[index] = None

            batch_data.append(tuple(fields))
            row_count += 1

            if len(batch_data) >= self.batch_size:
                yield (batch_data, row_count)
                batch_data = []

        if batch_data:
            yield (batch_data, row_count)

    @staticmethod
    def _iter_records(file_handle: Any, delimiter: str, quote: str | None) -> Iterator[list[Any]]:
        if quote:
            for record in csv.reader(file_handle, delimiter=delimiter, quotechar=quote, doublequote=True):
                if record:
                    yield record
            return
        for line in file_handle:
            line = line.strip()
            if line:
                yield line.split(delimiter)


class FileFormatHandler(ABC):
    @abstractmethod
    def get_delimiter(self) -> str:
        pass

    @abstractmethod
    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        pass

    def load_table_bulk(
        self,
        table_name: str,
        file_paths: list[Path],
        connection: Any,
        benchmark: Any,
        logger: Any,
    ) -> int:
        total = 0
        for file_path in file_paths:
            total += self.load_table(table_name, file_path, connection, benchmark, logger)
        return total


class DelimitedFileHandler(FileFormatHandler):
    def __init__(
        self,
        delimiter: str,
        null_marker: str | None = None,
        quote: str | None = None,
        has_header: bool = False,
    ):
        self.delimiter_char = delimiter
        self.null_marker = null_marker
        self.quote = quote
        self.has_header = has_header

    def get_delimiter(self) -> str:
        return self.delimiter_char

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")

        compression_handler = FileFormatRegistry.get_compression_handler(file_path)

        with compression_handler.open(file_path, newline="" if self.quote else None) as f:
            column_count = SchemaInspector.get_column_count(benchmark, validated_table, f, self.delimiter_char)

            if column_count is None:
                logger.debug(f"Could not determine column count for {validated_table}")
                return 0

            placeholders = ",".join(["?" for _ in range(column_count)])
            insert_sql = f"INSERT INTO {validated_table} VALUES ({placeholders})"

            processor = RowBatchProcessor()
            total_rows = 0
            null_columns = resolve_null_columns(
                benchmark,
                validated_table,
                column_count,
                include_text=self.null_marker is not None,
            )

            for batch_data, current_count in processor.process_file(
                f,
                self.delimiter_char,
                column_count,
                null_columns,
                self.null_marker if self.null_marker is not None else "",
                self.quote,
                self.has_header,
            ):
                connection.executemany(insert_sql, batch_data)
                total_rows = current_count

            return total_rows


DUCKDB_NO_NULL_CONVERSION_SENTINEL = "__NULL__"


class DuckDBNativeHandler(FileFormatHandler):
    def __init__(self, delimiter: str, adapter: Any, benchmark: Any, null_marker: str | None = ""):
        self.delimiter_char = delimiter
        self.adapter = adapter
        self.benchmark = benchmark
        self.null_marker = null_marker

    def get_delimiter(self) -> str:
        return self.delimiter_char

    def _get_csv_config(self, table_name: str) -> str:
        config_parts = ["header=false", "auto_detect=true", "ignore_errors=true"]

        if hasattr(self.benchmark, "get_csv_loading_config"):
            try:
                benchmark_config = self.benchmark.get_csv_loading_config(table_name)
                if benchmark_config:
                    config_parts = list(benchmark_config)
            except Exception:
                pass

        return ",\n                                ".join(config_parts)

    def _pipe_nullstr_config(self) -> str:
        null_marker = self.null_marker
        if null_marker is None:
            null_marker = DUCKDB_NO_NULL_CONVERSION_SENTINEL
        return f"nullstr='{escape_sql_string_literal(null_marker)}'"

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")
        escaped_path = escape_sql_string_literal(str(file_path))

        if self.delimiter_char == "|":
            result = connection.execute(
                f"SELECT name FROM pragma_table_info('{validated_table}') ORDER BY cid"
            ).fetchall()
            col_names = [row[0] for row in result] if result else []

            if col_names:
                trailing = has_trailing_delimiter(file_path, "|", col_names)
                all_names = get_column_names_with_trailing(col_names, trailing)
                names_param = ", ".join([f"'{col}'" for col in all_names])
                select_cols = ", ".join([f'"{col}"' for col in col_names])
                insert_sql = f"""
                    INSERT INTO {validated_table}
                    SELECT {select_cols} FROM read_csv('{escaped_path}',
                        delim='|',
                        header=false,
                        {self._pipe_nullstr_config()},
                        ignore_errors=true,
                        null_padding=true,
                        names=[{names_param}]
                    )
                """
            else:
                insert_sql = f"""
                    INSERT INTO {validated_table}
                    SELECT * FROM read_csv('{escaped_path}',
                        delim='|',
                        header=false,
                        {self._pipe_nullstr_config()},
                        ignore_errors=true,
                        auto_detect=true
                    )
                """
        else:
            csv_config = self._get_csv_config(validated_table)
            insert_sql = f"""
                INSERT INTO {validated_table}
                SELECT * FROM read_csv('{escaped_path}',
                    {csv_config}
                )
            """

        if hasattr(self.adapter, "dry_run_mode") and self.adapter.dry_run_mode:
            self.adapter.capture_sql(insert_sql, "load_data", validated_table)
            return 1000
        else:
            before = connection.execute(f"SELECT COUNT(*) FROM {validated_table}").fetchone()[0]
            connection.execute(insert_sql)
            after = connection.execute(f"SELECT COUNT(*) FROM {validated_table}").fetchone()[0]
            return after - before

    def load_table_bulk(
        self,
        table_name: str,
        file_paths: list[Path],
        connection: Any,
        benchmark: Any,
        logger: Any,
    ) -> int:
        if len(file_paths) == 1:
            return self.load_table(table_name, file_paths[0], connection, benchmark, logger)

        validated_table = validate_sql_identifier(table_name, "table name")
        escaped_paths = [escape_sql_string_literal(str(p)) for p in file_paths]
        paths_array = "[" + ", ".join(f"'{p}'" for p in escaped_paths) + "]"

        if self.delimiter_char == "|":
            result = connection.execute(
                f"SELECT name FROM pragma_table_info('{validated_table}') ORDER BY cid"
            ).fetchall()
            col_names = [row[0] for row in result] if result else []

            if col_names:
                trailing = has_trailing_delimiter(file_paths[0], "|", col_names)
                all_names = get_column_names_with_trailing(col_names, trailing)
                names_param = ", ".join([f"'{col}'" for col in all_names])
                select_cols = ", ".join([f'"{col}"' for col in col_names])
                insert_sql = f"""
                    INSERT INTO {validated_table}
                    SELECT {select_cols} FROM read_csv({paths_array},
                        delim='|',
                        header=false,
                        {self._pipe_nullstr_config()},
                        ignore_errors=true,
                        null_padding=true,
                        names=[{names_param}]
                    )
                """
            else:
                insert_sql = f"""
                    INSERT INTO {validated_table}
                    SELECT * FROM read_csv({paths_array},
                        delim='|',
                        header=false,
                        {self._pipe_nullstr_config()},
                        ignore_errors=true,
                        auto_detect=true
                    )
                """
        else:
            csv_config = self._get_csv_config(validated_table)
            insert_sql = f"""
                INSERT INTO {validated_table}
                SELECT * FROM read_csv({paths_array},
                    {csv_config}
                )
            """

        if hasattr(self.adapter, "dry_run_mode") and self.adapter.dry_run_mode:
            self.adapter.capture_sql(insert_sql, "load_data_bulk", validated_table)
            return 1000 * len(file_paths)
        else:
            before = connection.execute(f"SELECT COUNT(*) FROM {validated_table}").fetchone()[0]
            connection.execute(insert_sql)
            after = connection.execute(f"SELECT COUNT(*) FROM {validated_table}").fetchone()[0]
            return after - before


class ParquetFileHandler(FileFormatHandler):
    _LOAD_SAVEPOINT = "benchbox_parquet_load"

    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")

        try:
            import pyarrow.parquet as pq
        except ImportError as e:
            raise RuntimeError("pyarrow is required for Parquet loading") from e

        connection.execute(f"SAVEPOINT {self._LOAD_SAVEPOINT}")
        savepoint_active = True
        try:
            parquet_file = pq.ParquetFile(file_path)
            column_names = parquet_file.schema_arrow.names
            validated_columns = [validate_sql_identifier(col, "column name") for col in column_names]
            placeholders = ",".join(["?" for _ in validated_columns])
            columns_str = ",".join(validated_columns)

            insert_sql = f"INSERT INTO {validated_table} ({columns_str}) VALUES ({placeholders})"

            batch_size = 1000
            row_count = 0
            for batch in parquet_file.iter_batches(batch_size=batch_size):
                data_tuples = [tuple(row[col] for col in column_names) for row in batch.to_pylist()]
                if data_tuples:
                    connection.executemany(insert_sql, data_tuples)
                    row_count += len(data_tuples)

            connection.execute(f"RELEASE SAVEPOINT {self._LOAD_SAVEPOINT}")
            savepoint_active = False
            return row_count
        except Exception:
            if savepoint_active:
                connection.execute(f"ROLLBACK TO SAVEPOINT {self._LOAD_SAVEPOINT}")
                connection.execute(f"RELEASE SAVEPOINT {self._LOAD_SAVEPOINT}")
            raise


class DuckDBParquetHandler(FileFormatHandler):
    def __init__(self, adapter: Any):
        self.adapter = adapter

    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")
        escaped_path = escape_sql_string_literal(str(file_path))

        insert_sql = f"""
            INSERT INTO {validated_table}
            SELECT * FROM read_parquet('{escaped_path}')
        """

        if hasattr(self.adapter, "dry_run_mode") and self.adapter.dry_run_mode:
            self.adapter.capture_sql(insert_sql, "load_data", validated_table)
            return 1000
        else:
            before = connection.execute(f"SELECT COUNT(*) FROM {validated_table}").fetchone()[0]
            connection.execute(insert_sql)
            after = connection.execute(f"SELECT COUNT(*) FROM {validated_table}").fetchone()[0]
            return after - before

    def load_table_bulk(
        self,
        table_name: str,
        file_paths: list[Path],
        connection: Any,
        benchmark: Any,
        logger: Any,
    ) -> int:
        if len(file_paths) == 1:
            return self.load_table(table_name, file_paths[0], connection, benchmark, logger)

        validated_table = validate_sql_identifier(table_name, "table name")
        escaped_paths = [escape_sql_string_literal(str(p)) for p in file_paths]
        paths_array = "[" + ", ".join(f"'{p}'" for p in escaped_paths) + "]"
        insert_sql = f"INSERT INTO {validated_table} SELECT * FROM read_parquet({paths_array})"

        if hasattr(self.adapter, "dry_run_mode") and self.adapter.dry_run_mode:
            self.adapter.capture_sql(insert_sql, "load_data_bulk", validated_table)
            return 1000 * len(file_paths)
        else:
            before = connection.execute(f"SELECT COUNT(*) FROM {validated_table}").fetchone()[0]
            connection.execute(insert_sql)
            after = connection.execute(f"SELECT COUNT(*) FROM {validated_table}").fetchone()[0]
            return after - before


class DeltaFileHandler(FileFormatHandler):
    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")

        try:
            from deltalake import DeltaTable
        except ImportError as e:
            raise RuntimeError(
                "Delta Lake support requires the 'deltalake' package. "
                "Install it with: uv add deltalake --optional table-formats"
            ) from e

        delta_table = DeltaTable(str(file_path))
        arrow_table = delta_table.to_pyarrow_table()
        row_count = arrow_table.num_rows

        if row_count == 0:
            return 0

        data = arrow_table.to_pylist()

        column_names = arrow_table.schema.names
        validated_columns = [validate_sql_identifier(col, "column name") for col in column_names]
        placeholders = ",".join(["?" for _ in validated_columns])
        columns_str = ",".join(validated_columns)

        insert_sql = f"INSERT INTO {validated_table} ({columns_str}) VALUES ({placeholders})"

        data_tuples = [tuple(row[col] for col in column_names) for row in data]

        batch_size = 1000
        for i in range(0, len(data_tuples), batch_size):
            batch = data_tuples[i : i + batch_size]
            connection.executemany(insert_sql, batch)

        return row_count


class DuckDBDeltaHandler(FileFormatHandler):
    def __init__(self, adapter: Any):
        self.adapter = adapter

    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")
        escaped_path = escape_sql_string_literal(str(file_path))

        try:
            connection.execute("INSTALL delta")
            connection.execute("LOAD delta")
        except Exception:
            pass

        insert_sql = f"""
            INSERT INTO {validated_table}
            SELECT * FROM delta_scan('{escaped_path}')
        """

        if hasattr(self.adapter, "dry_run_mode") and self.adapter.dry_run_mode:
            self.adapter.capture_sql(insert_sql, "load_data", validated_table)
            return 1000
        else:
            connection.execute(insert_sql)
            row_count = connection.execute(f"SELECT COUNT(*) FROM {validated_table}").fetchone()[0]
            return row_count


class DuckLakeFileHandler(FileFormatHandler):
    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")

        try:
            import duckdb
        except ImportError as e:
            raise RuntimeError("DuckLake support requires DuckDB. Install it with: uv add duckdb") from e

        metadata_path = file_path / "metadata.ducklake"
        data_path = file_path / "data"

        if not metadata_path.exists():
            raise RuntimeError(f"DuckLake catalog not found at {metadata_path}")

        temp_conn = duckdb.connect(":memory:")
        try:
            temp_conn.execute("INSTALL ducklake")
            temp_conn.execute("LOAD ducklake")

            temp_conn.execute(f"ATTACH 'ducklake:{metadata_path}' AS ducklake_db (DATA_PATH '{data_path}')")

            arrow_table = temp_conn.execute(f"SELECT * FROM ducklake_db.main.{validated_table}").fetch_arrow_table()

            row_count = arrow_table.num_rows

            if row_count == 0:
                return 0

            data = arrow_table.to_pylist()

            column_names = arrow_table.schema.names
            validated_columns = [validate_sql_identifier(col, "column name") for col in column_names]
            placeholders = ",".join(["?" for _ in validated_columns])
            columns_str = ",".join(validated_columns)

            insert_sql = f"INSERT INTO {validated_table} ({columns_str}) VALUES ({placeholders})"

            data_tuples = [tuple(row[col] for col in column_names) for row in data]

            batch_size = 1000
            for i in range(0, len(data_tuples), batch_size):
                batch = data_tuples[i : i + batch_size]
                connection.executemany(insert_sql, batch)

            return row_count

        finally:
            temp_conn.close()


class DuckDBDuckLakeHandler(FileFormatHandler):
    def __init__(self, adapter: Any):
        self.adapter = adapter

    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")

        metadata_path = file_path / "metadata.ducklake"
        data_path = file_path / "data"

        if not metadata_path.exists():
            raise RuntimeError(f"DuckLake catalog not found at {metadata_path}")

        try:
            connection.execute("INSTALL ducklake")
            connection.execute("LOAD ducklake")
        except Exception:
            pass

        import uuid

        catalog_alias = f"ducklake_{uuid.uuid4().hex[:8]}"

        escaped_metadata = escape_sql_string_literal(str(metadata_path))
        escaped_data_path = escape_sql_string_literal(str(data_path))

        try:
            connection.execute(
                f"ATTACH 'ducklake:{escaped_metadata}' AS {catalog_alias} (DATA_PATH '{escaped_data_path}')"
            )

            insert_sql = f"""
                INSERT INTO {validated_table}
                SELECT * FROM {catalog_alias}.main.{validated_table}
            """

            if hasattr(self.adapter, "dry_run_mode") and self.adapter.dry_run_mode:
                self.adapter.capture_sql(insert_sql, "load_data", validated_table)
                return 1000
            else:
                connection.execute(insert_sql)
                row_count = connection.execute(f"SELECT COUNT(*) FROM {validated_table}").fetchone()[0]
                return row_count

        finally:
            try:
                connection.execute(f"DETACH {catalog_alias}")
            except Exception:
                pass


class IcebergFileHandler(FileFormatHandler):
    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")

        try:
            from pyiceberg.catalog.sql import SqlCatalog
        except ImportError as e:
            raise RuntimeError(
                "Iceberg support requires the 'pyiceberg' package with SQL support. "
                "Install it with: uv add 'pyiceberg[sql-sqlite,pyarrow]' --optional table-formats"
            ) from e

        import tempfile

        warehouse_path = str(file_path.parent)
        catalog_fd = None
        catalog_db = None

        try:
            catalog_fd, catalog_db = tempfile.mkstemp(suffix=".db", prefix="benchbox_iceberg_")
            os.close(catalog_fd)
            catalog_fd = None

            catalog = SqlCatalog(
                "benchbox_catalog",
                uri=f"sqlite:///{catalog_db}",
                warehouse=warehouse_path,
            )
            try:
                table_identifier = ("benchbox", validated_table)
                try:
                    iceberg_table = catalog.load_table(table_identifier)
                except Exception:
                    catalog.register_table(table_identifier, str(file_path))
                    iceberg_table = catalog.load_table(table_identifier)

                arrow_table = iceberg_table.scan().to_arrow()
                row_count = arrow_table.num_rows

                if row_count == 0:
                    return 0

                data = arrow_table.to_pylist()

                column_names = arrow_table.schema.names
                validated_columns = [validate_sql_identifier(col, "column name") for col in column_names]
                placeholders = ",".join(["?" for _ in validated_columns])
                columns_str = ",".join(validated_columns)

                insert_sql = f"INSERT INTO {validated_table} ({columns_str}) VALUES ({placeholders})"

                data_tuples = [tuple(row[col] for col in column_names) for row in data]

                batch_size = 1000
                for i in range(0, len(data_tuples), batch_size):
                    batch = data_tuples[i : i + batch_size]
                    connection.executemany(insert_sql, batch)

                return row_count
            finally:
                catalog.engine.dispose()

        finally:
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


class VortexFileHandler(FileFormatHandler):
    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")

        try:
            import vortex
        except ImportError as e:
            raise RuntimeError(
                "Vortex format support requires the 'vortex' package. "
                "Install it with: uv add vortex-data --optional table-formats"
            ) from e

        io_module = getattr(vortex, "io", None)
        read_vortex = getattr(io_module, "read", None)
        if not callable(read_vortex):
            open_vortex = getattr(vortex, "open", None)
            if callable(open_vortex):
                read_vortex = open_vortex

        if not callable(read_vortex):
            providers = importlib_metadata.packages_distributions().get("vortex", [])
            provider_text = f" Found provider(s): {', '.join(providers)}." if providers else ""
            raise RuntimeError(
                "Installed 'vortex' module is incompatible with BenchBox Vortex loading."
                " Install compatible bindings with: uv add vortex-data --optional table-formats."
                f"{provider_text}"
            )

        vortex_array = read_vortex(str(file_path))
        arrow_table = vortex_array.to_arrow()
        row_count = arrow_table.num_rows

        if row_count == 0:
            return 0

        data = arrow_table.to_pylist()

        column_names = arrow_table.schema.names
        validated_columns = [validate_sql_identifier(col, "column name") for col in column_names]
        placeholders = ",".join(["?" for _ in validated_columns])
        columns_str = ",".join(validated_columns)

        insert_sql = f"INSERT INTO {validated_table} ({columns_str}) VALUES ({placeholders})"

        data_tuples = [tuple(row[col] for col in column_names) for row in data]

        batch_size = 1000
        for i in range(0, len(data_tuples), batch_size):
            batch = data_tuples[i : i + batch_size]
            connection.executemany(insert_sql, batch)

        return row_count


class DuckDBVortexHandler(FileFormatHandler):
    def __init__(self, adapter: Any):
        self.adapter = adapter

    def get_delimiter(self) -> str:
        return ""

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")
        escaped_path = escape_sql_string_literal(str(file_path))

        try:
            connection.execute("INSTALL vortex")
            connection.execute("LOAD vortex")
        except Exception:
            logger.debug("DuckDB vortex extension not available, falling back to generic handler")
            return VortexFileHandler().load_table(table_name, file_path, connection, benchmark, logger)

        insert_sql = f"""
            INSERT INTO {validated_table}
            SELECT * FROM read_vortex('{escaped_path}')
        """

        if hasattr(self.adapter, "dry_run_mode") and self.adapter.dry_run_mode:
            self.adapter.capture_sql(insert_sql, "load_data", validated_table)
            return 1000
        else:
            connection.execute(insert_sql)
            row_count = connection.execute(f"SELECT COUNT(*) FROM {validated_table}").fetchone()[0]
            return row_count


_CLICKHOUSE_VECTOR_TYPE_RE = re.compile(r"\[\s*\d*\s*\]")


class ClickHouseNativeHandler(FileFormatHandler):
    def __init__(self, delimiter: str, adapter: Any, benchmark: Any, *, has_header: bool = False):
        self.delimiter_char = delimiter
        self.adapter = adapter
        self.benchmark = benchmark
        self.has_header = has_header

    def get_delimiter(self) -> str:
        return self.delimiter_char

    def _uses_server_mode(self) -> bool:
        return getattr(self.adapter, "deployment_mode", None) == "server"

    def _server_insert_block_size(self) -> int:
        value = getattr(self.adapter, "insert_block_size", 65536)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value == 1000:
            raise DataLoadingError("ClickHouse server insert_block_size must be a positive integer other than 1000")
        return value

    def _server_insert_settings(self) -> dict[str, int]:
        return {"insert_block_size": self._server_insert_block_size()}

    def _get_csv_loading_config(self, table_name: str) -> dict[str, str]:
        config = {"delimiter": self.delimiter_char, "format": "CSVWithNames" if self.has_header else "CSV"}

        if hasattr(self.benchmark, "get_csv_loading_config"):
            try:
                benchmark_config_list = self.benchmark.get_csv_loading_config(table_name)
                if benchmark_config_list:
                    for config_item in benchmark_config_list:
                        normalized_item = config_item.lower()
                        if "delim=" in normalized_item:
                            delim_part = config_item.split("delim=")[1].strip("'\"")
                            config["delimiter"] = delim_part
                        elif "header=true" in normalized_item:
                            config["format"] = "CSVWithNames"
                        elif "header=false" in normalized_item:
                            config["format"] = "CSV"
            except Exception:
                pass

        return config

    def load_table(self, table_name: str, file_path: Path, connection: Any, benchmark: Any, logger: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")

        try:
            if self._uses_server_mode():
                base_ext = FileFormatRegistry.get_base_data_extension(file_path)
                if base_ext == ".parquet":
                    return self._load_parquet_via_client_insert(validated_table, [file_path], connection)
                return self._load_delimited_via_client_insert(
                    validated_table, [file_path], connection, benchmark, logger
                )

            escaped_path = escape_sql_string_literal(str(file_path))

            base_ext = FileFormatRegistry.get_base_data_extension(file_path)
            if base_ext == ".parquet":
                load_query = f"""
                    INSERT INTO {validated_table}
                    SELECT * FROM file('{escaped_path}', 'Parquet')
                """
            else:
                csv_config = self._get_csv_loading_config(validated_table)
                delimiter = csv_config["delimiter"]
                csv_format = csv_config["format"]

                if delimiter == ",":
                    load_query = f"""
                        INSERT INTO {validated_table}
                        SELECT * FROM file('{escaped_path}', '{csv_format}')
                    """
                else:
                    escaped_delimiter = escape_sql_string_literal(delimiter)
                    load_query = f"""
                        INSERT INTO {validated_table}
                        SELECT * FROM file('{escaped_path}', '{csv_format}')
                        SETTINGS format_csv_delimiter='{escaped_delimiter}'
                    """

            before_result = connection.execute(f"SELECT COUNT(*) FROM {validated_table}")
            before = before_result[0][0] if before_result and before_result[0] else 0
            connection.execute(load_query)
            after_result = connection.execute(f"SELECT COUNT(*) FROM {validated_table}")
            after = after_result[0][0] if after_result and after_result[0] else 0

            return after - before

        except Exception as e:
            logger.error(f"ClickHouse file loading failed: {e}")
            raise

    def _load_delimited_via_client_insert(
        self,
        validated_table: str,
        file_paths: list[Path],
        connection: Any,
        benchmark: Any,
        logger: Any,
    ) -> int:
        row_count = 0
        row_generator: Any | None = None
        try:
            column_count = None
            for file_path in file_paths:
                compression_handler = FileFormatRegistry.get_compression_handler(file_path)
                with compression_handler.open(file_path) as file_handle:
                    column_count = SchemaInspector.get_column_count(
                        benchmark, validated_table, file_handle, self.delimiter_char
                    )
                if column_count is not None:
                    break
            if column_count is None:
                logger.debug(f"Could not determine column count for {validated_table}")
                return 0

            column_types = self._get_column_type_names(benchmark, validated_table)
            if not any(column_types):
                column_types = self._describe_server_column_types(connection, validated_table)

            def rows() -> Iterator[tuple[Any, ...]]:
                nonlocal row_count
                for file_path in file_paths:
                    handler = FileFormatRegistry.get_compression_handler(file_path)
                    with handler.open(file_path) as file_handle:
                        if self.has_header:
                            next(file_handle, None)
                        for line in file_handle:
                            line = line.strip()
                            if not line:
                                continue
                            fields = line.split(self.delimiter_char)
                            fields.extend([""] * max(0, column_count - len(fields)))
                            fields = fields[:column_count]
                            row_count += 1
                            yield tuple(
                                self._convert_field_for_clickhouse(
                                    value,
                                    column_types[index] if index < len(column_types) else None,
                                )
                                for index, value in enumerate(fields)
                            )

            row_generator = rows()
            connection.execute(
                f"INSERT INTO {validated_table} VALUES",
                row_generator,
                settings=self._server_insert_settings(),
            )
            return row_count
        except ClickHouseServerLoadError:
            raise
        except Exception as exc:
            raise ClickHouseServerLoadError(validated_table, file_paths, row_count, exc) from exc
        finally:
            if row_generator is not None:
                row_generator.close()

    def _get_column_type_names(self, benchmark: Any, table_name: str) -> list[str | None]:
        schema = benchmark.get_schema() if hasattr(benchmark, "get_schema") else {}
        table_schema = _resolve_table_schema(schema, table_name)
        columns = _schema_table_columns(table_schema)
        if columns is None:
            keyed = table_schema.get("columns") if isinstance(table_schema, dict) else None
            columns = list(keyed.values()) if isinstance(keyed, dict) else []

        type_names: list[str | None] = []
        for column in columns:
            if isinstance(column, dict):
                type_names.append(str(column.get("type") or column.get("data_type") or "").upper() or None)
            elif hasattr(column, "get_sql_type"):
                type_names.append(str(column.get_sql_type()).upper())
            else:
                type_names.append(None)
        return type_names

    @staticmethod
    def _describe_server_column_types(connection: Any, validated_table: str) -> list[str | None]:
        rows = connection.execute(
            "SELECT type FROM system.columns "
            f"WHERE database = currentDatabase() AND table = '{validated_table}' ORDER BY position"
        )
        return [str(row[0]) for row in rows or []]

    def _convert_rows_for_clickhouse(
        self,
        rows: list[tuple[Any, ...]],
        column_types: list[str | None],
    ) -> list[tuple[Any, ...]]:
        if not column_types:
            return rows
        return [
            tuple(
                self._convert_field_for_clickhouse(
                    value,
                    column_types[index] if index < len(column_types) else None,
                )
                for index, value in enumerate(row)
            )
            for row in rows
        ]

    def _convert_field_for_clickhouse(self, value: Any, type_name: str | None) -> Any:
        if type_name is None or not isinstance(value, str):
            return value

        type_upper = type_name.upper()
        while type_upper.startswith(("NULLABLE(", "LOWCARDINALITY(")):
            type_upper = type_upper[type_upper.index("(") + 1 : -1]

        if "ARRAY" in type_upper or _CLICKHOUSE_VECTOR_TYPE_RE.search(type_upper):
            if not value or value.upper() in ("\\N", "NULL"):
                return None
            try:
                parsed = ast.literal_eval(value)
                if isinstance(parsed, (list, tuple)):
                    return list(parsed)
            except (ValueError, SyntaxError):
                pass
            return value

        is_datetime = "DATETIME" in type_upper or "TIMESTAMP" in type_upper
        is_date = not is_datetime and type_upper.startswith("DATE")

        is_bool = type_upper.startswith("BOOL")
        needs_non_string_value = (
            is_bool
            or "INT" in type_upper
            or any(token in type_upper for token in ("DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL"))
            or is_date
            or is_datetime
        )
        if value == "":
            return None if needs_non_string_value else value

        if is_bool:
            return value.strip().lower() in ("true", "t", "1", "yes")
        if "INT" in type_upper:
            return int(value)
        if any(token in type_upper for token in ("DECIMAL", "NUMERIC")):
            return Decimal(value)
        if any(token in type_upper for token in ("DOUBLE", "FLOAT", "REAL")):
            return float(value)
        if is_datetime:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        if is_date:
            return date.fromisoformat(value)
        return value

    def _load_parquet_via_client_insert(self, validated_table: str, file_paths: list[Path], connection: Any) -> int:
        row_count = 0
        row_generator: Any | None = None
        try:
            try:
                import pyarrow.parquet as pq
            except ImportError as exc:
                raise RuntimeError("pyarrow is required for Parquet loading") from exc

            first_table = pq.ParquetFile(file_paths[0])
            column_names = first_table.schema_arrow.names
            validated_columns = [validate_sql_identifier(col, "column name") for col in column_names]
            expected_rows = 0
            for file_path in file_paths:
                parquet_file = pq.ParquetFile(file_path)
                if parquet_file.schema_arrow.names != column_names:
                    raise DataLoadingError(
                        f"ClickHouse server Parquet shards for {validated_table!r} have different columns: {file_path}"
                    )
                expected_rows += parquet_file.metadata.num_rows if parquet_file.metadata is not None else 0
            if expected_rows == 0:
                return 0

            insert_sql = f"INSERT INTO {validated_table} ({','.join(validated_columns)}) VALUES"
            batch_size = self._server_insert_block_size()

            def rows() -> Iterator[tuple[Any, ...]]:
                nonlocal row_count
                for file_path in file_paths:
                    parquet_file = pq.ParquetFile(file_path)
                    for batch in parquet_file.iter_batches(batch_size=batch_size):
                        for row in zip(*(batch.column(name).to_pylist() for name in column_names)):
                            row_count += 1
                            yield row

            row_generator = rows()
            connection.execute(insert_sql, row_generator, settings=self._server_insert_settings())
            if row_count != expected_rows:
                raise DataLoadingError(f"streamed {row_count:,} row(s), expected {expected_rows:,}")
            return row_count
        except ClickHouseServerLoadError:
            raise
        except Exception as exc:
            raise ClickHouseServerLoadError(validated_table, file_paths, row_count, exc) from exc
        finally:
            if row_generator is not None:
                row_generator.close()

    def load_table_bulk(
        self,
        table_name: str,
        file_paths: list[Path],
        connection: Any,
        benchmark: Any,
        logger: Any,
    ) -> int:
        if self._uses_server_mode():
            if not file_paths:
                return 0
            validated_table = validate_sql_identifier(table_name, "table name")
            base_extensions = {FileFormatRegistry.get_base_data_extension(path) for path in file_paths}
            if len(base_extensions) != 1:
                raise ClickHouseServerLoadError(
                    validated_table,
                    file_paths,
                    0,
                    DataLoadingError(f"mixed file formats are not supported: {sorted(base_extensions)}"),
                )
            if hasattr(self.adapter, "dry_run_mode") and self.adapter.dry_run_mode:
                if hasattr(self.adapter, "capture_sql"):
                    extension = next(iter(base_extensions))
                    sql = (
                        f"INSERT INTO {validated_table} VALUES"
                        if extension != ".parquet"
                        else f"INSERT INTO {validated_table}"
                    )
                    self.adapter.capture_sql(sql, "load_data_bulk", validated_table)
                return 0
            if next(iter(base_extensions)) == ".parquet":
                return self._load_parquet_via_client_insert(validated_table, file_paths, connection)
            return self._load_delimited_via_client_insert(validated_table, file_paths, connection, benchmark, logger)

        if len(file_paths) == 1:
            return self.load_table(table_name, file_paths[0], connection, benchmark, logger)

        parents = {p.parent for p in file_paths}
        if len(parents) != 1:
            return super().load_table_bulk(table_name, file_paths, connection, benchmark, logger)

        names = [p.name for p in file_paths]
        common_prefix = os.path.commonprefix(names)
        if not common_prefix:
            return super().load_table_bulk(table_name, file_paths, connection, benchmark, logger)

        parent = file_paths[0].parent
        glob_pattern = str(parent / (common_prefix + "*"))

        validated_table = validate_sql_identifier(table_name, "table name")
        escaped_glob = escape_sql_string_literal(glob_pattern)

        base_ext = FileFormatRegistry.get_base_data_extension(file_paths[0])
        if base_ext == ".parquet":
            insert_sql = f"INSERT INTO {validated_table} SELECT * FROM file('{escaped_glob}', 'Parquet')"
        else:
            csv_config = self._get_csv_loading_config(validated_table)
            delimiter = csv_config["delimiter"]
            csv_format = csv_config["format"]
            if delimiter == ",":
                insert_sql = f"INSERT INTO {validated_table} SELECT * FROM file('{escaped_glob}', '{csv_format}')"
            else:
                escaped_delimiter = escape_sql_string_literal(delimiter)
                insert_sql = (
                    f"INSERT INTO {validated_table} SELECT * FROM file('{escaped_glob}', '{csv_format}')"
                    f" SETTINGS format_csv_delimiter='{escaped_delimiter}'"
                )

        if hasattr(self.adapter, "dry_run_mode") and self.adapter.dry_run_mode:
            if hasattr(self.adapter, "capture_sql"):
                self.adapter.capture_sql(insert_sql, "load_data_bulk", validated_table)
            return 1000 * len(file_paths)

        before_result = connection.execute(f"SELECT COUNT(*) FROM {validated_table}")
        before = before_result[0][0] if before_result and before_result[0] else 0
        connection.execute(insert_sql)
        after_result = connection.execute(f"SELECT COUNT(*) FROM {validated_table}")
        after = after_result[0][0] if after_result and after_result[0] else 0
        return after - before


class InMemoryDataHandler:
    @staticmethod
    def load_table(table_name: str, table_data: Any, connection: Any) -> int:
        validated_table = validate_sql_identifier(table_name, "table name")

        if not (hasattr(table_data, "__iter__") and not isinstance(table_data, str)):
            return 0

        rows = list(table_data)
        if not rows:
            return 0

        columns = rows[0].keys() if hasattr(rows[0], "keys") else range(len(rows[0]))
        placeholders = ",".join(["?" for _ in columns])

        if hasattr(rows[0], "keys"):
            validated_columns = [validate_sql_identifier(col, "column name") for col in columns]
            insert_sql = f"INSERT INTO {validated_table} ({','.join(validated_columns)}) VALUES ({placeholders})"
            data_rows = [tuple(row.values()) for row in rows]
        else:
            insert_sql = f"INSERT INTO {validated_table} VALUES ({placeholders})"
            data_rows = rows

        connection.executemany(insert_sql, data_rows)
        return len(rows)


def is_delta_table_dir(path: Path | str) -> bool:
    candidate = Path(path)
    try:
        return candidate.is_dir() and (candidate / "_delta_log").is_dir()
    except OSError:
        return False


class FileFormatRegistry:
    _format_handlers = {
        ".csv": lambda: DelimitedFileHandler(","),
        ".tbl": lambda: DelimitedFileHandler("|", null_marker=""),
        ".dat": lambda: DelimitedFileHandler("|", null_marker=""),
        ".parquet": lambda: ParquetFileHandler(),
        ".vortex": lambda: VortexFileHandler(),
    }

    _compression_handlers = {
        ".gz": GzipHandler,
        ".bz2": Bzip2Handler,
        ".zst": ZstdHandler,
    }

    @staticmethod
    def get_base_data_extension(file_path: Path) -> str | None:
        return get_data_extension(file_path)

    @classmethod
    def get_handler(cls, file_path: Path) -> FileFormatHandler | None:
        if file_path.is_dir():
            ducklake_metadata = file_path / "metadata.ducklake"
            if ducklake_metadata.exists():
                return DuckLakeFileHandler()

            if is_delta_table_dir(file_path):
                return DeltaFileHandler()

            metadata_dir = file_path / "metadata"
            if metadata_dir.exists() and metadata_dir.is_dir():
                return IcebergFileHandler()

        base_ext = cls.get_base_data_extension(file_path)
        handler_factory = cls._format_handlers.get(base_ext) if base_ext else None
        return handler_factory() if handler_factory else None

    @classmethod
    def get_compression_handler(cls, file_path: Path) -> CompressionHandler:
        suffix = file_path.suffix
        handler_class = cls._compression_handlers.get(suffix, NoCompressionHandler)
        return handler_class()


@contextmanager
def prepare_local_load_file(
    file_path: Path,
    *,
    dialect: CsvDialect,
    strip_trailing_delim: bool,
) -> Iterator[Path]:
    compression_handler = FileFormatRegistry.get_compression_handler(file_path)
    is_compressed = not isinstance(compression_handler, NoCompressionHandler)
    needs_transform = is_compressed or strip_trailing_delim or dialect.normalize_booleans

    if not needs_transform:
        yield file_path
        return

    tmp_path: Path | None = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(suffix=".csv", dir=file_path.parent)
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)

        delim = dialect.delimiter
        with tmp_path.open("w", encoding="utf-8", newline="") as dst:
            with compression_handler.open(file_path) as src:
                for line in src:
                    line = line.rstrip("\n").rstrip("\r")
                    if not line:
                        continue
                    if strip_trailing_delim and line.endswith(delim):
                        line = line[: -len(delim)]
                    if dialect.normalize_booleans:
                        fields = line.split(delim)
                        fields = ["1" if f == "True" else "0" if f == "False" else f for f in fields]
                        line = delim.join(fields)
                    dst.write(line + "\n")
        yield tmp_path
    finally:
        if tmp_path is not None:
            with contextlib.suppress(Exception):
                tmp_path.unlink(missing_ok=True)


def _tables_map_to_no_files(tables: Any) -> bool:
    if not isinstance(tables, Mapping):
        return False
    values = list(tables.values())
    return bool(values) and all(isinstance(value, (list, tuple)) and len(value) == 0 for value in values)


class DataLoader:
    def __init__(
        self,
        adapter: Any,
        benchmark: Any,
        connection: Any,
        data_dir: Path,
        handler_factory: Any | None = None,
        tuning_config: Any | None = None,
    ):
        self.adapter = adapter
        self.benchmark = benchmark
        self.connection = connection
        self.data_dir = data_dir
        self.resolver = DataSourceResolver(
            platform_name=adapter.platform_name,
            table_mode=adapter.table_mode,
            platform_config=adapter.platform_config,
            requested_format=getattr(adapter, "requested_table_format", None),
        )
        self.handler_factory = handler_factory
        self.tuning_config = tuning_config

    def load(self) -> tuple[dict[str, int], float]:
        start_time = mono_time()
        self.adapter.log_operation_start("Data loading", f"benchmark: {self.benchmark.__class__.__name__}")
        self.adapter.log_very_verbose(f"Data directory: {self.data_dir}")

        table_stats = {}

        data_source = self.resolver.resolve(self.benchmark, self.data_dir)
        tables = data_source.tables if data_source else None
        if not data_source or not tables or _tables_map_to_no_files(tables):
            if is_data_loading_skipped(self.benchmark):
                self.adapter.log_very_verbose("No data source found (data loading skipped)")
                return table_stats, elapsed_seconds(start_time)
            raise ValueError("No data files found. Ensure benchmark.generate_data() was called first.")

        table_stats = self._load_file_based_data(data_source)

        if hasattr(self.connection, "commit"):
            self.connection.commit()

        duration = elapsed_seconds(start_time)
        total_rows = sum(table_stats.values())
        self.adapter.log_operation_complete(
            "Data loading", duration, f"{total_rows:,} total rows, {len(table_stats)} tables"
        )

        return table_stats, duration

    def _load_in_memory_data(self, tables: dict[str, Any]) -> dict[str, int]:
        table_stats = {}

        for table_name, table_data in tables.items():
            try:
                row_count = InMemoryDataHandler.load_table(table_name, table_data, self.connection)
                table_stats[table_name] = row_count
            except Exception as e:
                quiet_console.print(f"  ❌ Failed to load {table_name}: {e}")
                table_stats[table_name] = 0

        return table_stats

    def _load_file_based_data(self, data_source: DataSource | dict[str, Any]) -> dict[str, int]:
        table_stats = {}
        if not isinstance(data_source, DataSource):
            data_source = DataSource(source_type="legacy_mapping", tables=data_source)
        data_files = data_source.tables

        if hasattr(self.benchmark, "get_table_loading_order"):
            table_load_order = self.benchmark.get_table_loading_order(list(data_files.keys()))
            self.adapter.log_very_verbose(f"Using benchmark-specified loading order: {table_load_order}")
        else:
            table_load_order = sorted(data_files.keys())
            self.adapter.log_very_verbose(f"Using alphabetical loading order: {table_load_order}")

        for table_name in table_load_order:
            file_path_or_paths = data_files[table_name]
            table_start = mono_time()

            if isinstance(file_path_or_paths, list):
                row_count = self._load_sharded_table(table_name, file_path_or_paths, data_source)
                source_desc = f"{len(file_path_or_paths)} shard(s)"
            else:
                file_path = Path(file_path_or_paths)
                row_count = self._load_single_file(table_name, file_path, data_source)
                source_desc = file_path.name

            table_stats[table_name] = row_count
            if row_count > 0:
                self._log_table_loaded(table_name, row_count, table_start, source_desc)

            if self.tuning_config:
                self.adapter.apply_ctas_sort(table_name, self.tuning_config, self.connection)
                self.adapter.run_post_load_tunings(table_name, self.tuning_config, self.connection)

        return table_stats

    def _log_table_loaded(self, table_name: str, row_count: int, start_time: float, source: str) -> None:
        if self.adapter.verbose_enabled:
            table_time = elapsed_seconds(start_time)
            quiet_console.print(f"  ✅ Loaded {row_count:,} rows into {table_name} in {table_time:.2f}s from {source}")
        else:
            quiet_console.print(f"  ✅ Loaded {row_count:,} rows into {table_name} from {source}")

    def _load_sharded_table(
        self, table_name: str, file_path_or_paths: list, data_source: DataSource | None = None
    ) -> int:
        if data_source is None:
            data_source = DataSource(source_type="legacy_mapping", tables={table_name: file_path_or_paths})

        shard_paths = []
        missing_shards = []
        for p in file_path_or_paths:
            pp = Path(p)
            if pp.is_file():
                shard_paths.append(pp)
            elif pp.is_dir():
                if is_delta_table_dir(pp):
                    shard_paths.append(pp)
                    continue
                data_globs = ["*.tbl*", "*.csv*", "*.parquet*", "*.tsv*", "*.dat*"]
                dir_files: list[Path] = []
                for pattern in data_globs:
                    dir_files.extend(pp.glob(pattern))
                if not dir_files:
                    raise DataLoadingError(
                        f"Table '{table_name}': shard path is a directory with no data files: {pp}. "
                        f"Use --force datagen to regenerate."
                    )
                shard_paths.extend(sorted(dir_files))
            else:
                missing_shards.append(pp)

        if missing_shards:
            raise DataLoadingError(
                f"Table '{table_name}': {len(file_path_or_paths)} shard(s) listed "
                f"but {len(missing_shards)} missing "
                f"({len(shard_paths)} valid files). Use --force datagen to regenerate."
            )

        handler = None
        if self.handler_factory:
            handler = self._create_custom_handler(shard_paths[0], table_name, data_source)
        if not handler:
            handler = FileFormatRegistry.get_handler(shard_paths[0])

        if not handler:
            quiet_console.print(f"⚠️  Skipping {table_name} - unsupported file format: {shard_paths[0].suffix}")
            return 0

        try:
            return handler.load_table_bulk(
                table_name, shard_paths, self.connection, self.benchmark, self.adapter.logger
            )
        except ClickHouseServerLoadError:
            raise
        except Exception as e:
            quiet_console.print(f"  ❌ Failed to bulk-load {table_name}: {e}")
            return 0

    def _load_single_file(self, table_name: str, file_path: Path, data_source: DataSource | None = None) -> int:
        if data_source is None:
            data_source = DataSource(source_type="legacy_mapping", tables={table_name: file_path})

        if file_path.is_dir():
            raise DataLoadingError(
                f"Table '{table_name}': expected file but found directory at {file_path}. "
                f"This may indicate stale data from a previous conversion. "
                f"Use --force datagen to regenerate."
            )
        if not file_path.exists():
            raise DataLoadingError(
                f"Table '{table_name}': data file not found at {file_path}. Use --force datagen to regenerate."
            )

        try:
            handler = None
            if self.handler_factory:
                handler = self._create_custom_handler(file_path, table_name, data_source)

            if not handler:
                handler = FileFormatRegistry.get_handler(file_path)

            if not handler:
                quiet_console.print(f"⚠️  Skipping {table_name} - unsupported file format: {file_path.suffix}")
                return 0

            row_count = handler.load_table(table_name, file_path, self.connection, self.benchmark, self.adapter.logger)

            return row_count

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            quiet_console.print(f"⚠️  Skipping {file_path.name} - decompression/file error: {e}")
            return 0
        except ClickHouseServerLoadError:
            raise
        except Exception as e:
            quiet_console.print(f"  ❌ Failed to load {file_path.name}: {e}")
            return 0

    def _create_custom_handler(self, file_path: Path, table_name: str, data_source: DataSource) -> Any | None:
        if not self.handler_factory:
            return None

        signature = inspect.signature(self.handler_factory)
        supports_extended_context = (
            any(param.kind is inspect.Parameter.VAR_POSITIONAL for param in signature.parameters.values())
            or sum(
                1
                for param in signature.parameters.values()
                if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            )
            >= 5
        )

        if supports_extended_context:
            return self.handler_factory(file_path, self.adapter, self.benchmark, table_name, data_source)
        return self.handler_factory(file_path, self.adapter, self.benchmark)


def run_staged_table_loads(
    adapter: Any,
    *,
    tables: Mapping[str, Any],
    stat_key: Callable[[str], str],
    filter_files: Callable[[Any], list[Path]],
    load_one: Callable[[str, list[Path]], int],
    on_table_loaded: Callable[[str, str, int], None] | None = None,
    record_timings: bool,
    fail_fast: bool,
    success_log: Callable[[str], None] | None = None,
    summary_log: Callable[[str], None] | None = None,
    describe_start: Callable[[str, str], str] | None = None,
    phase_start: float | None = None,
) -> tuple[dict[str, int], float, dict[str, Any] | None]:
    table_stats: dict[str, int] = {}
    per_table_timings: dict[str, Any] = {}
    start_time = mono_time() if phase_start is None else phase_start
    log_success = success_log if success_log is not None else adapter.logger.info
    log_summary = summary_log if summary_log is not None else adapter.logger.info
    if describe_start is None:

        def describe_start(table_name: str, chunk_info: str) -> str:
            return f"Loading data for table: {table_name}{chunk_info}"

    for table_name, file_paths in tables.items():
        key = stat_key(table_name)
        valid_files = filter_files(file_paths)

        if not valid_files:
            adapter.logger.warning(f"Skipping {table_name} - no valid data files")
            table_stats[key] = 0
            if record_timings:
                per_table_timings[key] = {"total_ms": 0}
            continue

        chunk_info = f" from {len(valid_files)} file(s)" if len(valid_files) > 1 else ""
        adapter.log_verbose(describe_start(table_name, chunk_info))

        try:
            load_start = mono_time()
            row_count = load_one(table_name, valid_files)
            table_stats[key] = row_count
            if on_table_loaded is not None:
                on_table_loaded(table_name, key, row_count)
            load_time = elapsed_seconds(load_start)
            if record_timings:
                per_table_timings[key] = {"total_ms": load_time * 1000}
            log_success(f"✅ Loaded {row_count:,} rows into {key}{chunk_info} in {load_time:.2f}s")
        except Exception as exc:
            error_message = str(exc) or repr(exc) or type(exc).__name__
            adapter.logger.error(f"Failed to load {table_name}: {error_message}")
            table_stats[key] = 0
            if record_timings:
                per_table_timings[key] = {"total_ms": 0}
            if fail_fast:
                raise

    total_time = elapsed_seconds(start_time)
    total_rows = sum(table_stats.values())
    log_summary(f"✅ Loaded {total_rows:,} total rows in {total_time:.2f}s")
    return table_stats, total_time, per_table_timings if record_timings else None


class SchemaHelpersMixin:
    def _calculate_data_size(self, data_dir: Path) -> float:
        from benchbox.utils.cloud_storage import is_cloud_path

        total_size = 0
        try:
            if is_cloud_path(str(data_dir)):
                return 0.0

            if not hasattr(data_dir, "rglob"):
                return 0.0

            for file_path in data_dir.rglob("*"):
                if file_path.is_file() and file_path.suffix in [".csv", ".tbl"]:
                    total_size += file_path.stat().st_size
        except (AttributeError, NotImplementedError, OSError):
            return 0.0
        except Exception:
            return 0.0

        return total_size / (1024 * 1024)

    def _get_platform_metadata(self, connection: Any) -> dict[str, Any]:
        metadata = {
            "platform": self.platform_name,
            "connection_type": type(connection).__name__,
            "tuning_enabled": self.tuning_enabled,
        }

        effective_config = self.get_effective_tuning_configuration()
        if self.tuning_enabled and effective_config:
            metadata["tuning_configuration_hash"] = effective_config.get_configuration_hash()
            metadata["tuned_tables"] = list(effective_config.table_tunings.keys())
            metadata["tuning_types_enabled"] = [t.value for t in effective_config.get_enabled_tuning_types()]

        return metadata

    def _hash_connection_config(self, connection_config: dict[str, Any]) -> str:
        sanitized_config = {}
        for key, value in connection_config.items():
            if key not in ["password", "token", "service_account_path"]:
                sanitized_config[key] = value

        config_str = str(sorted(sanitized_config.items()))
        return hashlib.md5(config_str.encode()).hexdigest()[:16]

    def _create_schema_with_tuning(self, benchmark, source_dialect: str = "standard") -> str:
        self.log_operation_start(
            "Schema SQL generation", f"benchmark: {benchmark.__class__.__name__}, target: {self.get_target_dialect()}"
        )

        effective_config = self.get_effective_tuning_configuration()

        tuning_status = "with tuning" if effective_config else "no tuning"
        self.log_verbose(f"Schema generation {tuning_status} - target dialect: {self.get_target_dialect()}")
        self.log_very_verbose(f"Effective tuning config type: {type(effective_config)}")

        try:
            schema_sql = benchmark.get_create_tables_sql(
                dialect=self.get_target_dialect(), tuning_config=effective_config
            )
            self.log_very_verbose("Using standardized schema generation with tuning configuration")
            self.log_verbose(f"Schema SQL from benchmark: {len(schema_sql)} characters")
        except TypeError as e:
            self.logger.warning(
                f"TypeError calling get_create_tables_sql with new signature: {e}. Falling back to legacy."
            )
            schema_sql = benchmark.get_create_tables_sql()
            self.log_very_verbose("Using legacy schema generation (no tuning configuration)")
            self.log_verbose(f"Schema SQL from benchmark (legacy): {len(schema_sql)} characters")
        except Exception as e:
            self.logger.error(f"Unexpected exception in schema generation: {type(e).__name__}: {e}")
            raise

        translation_needed = source_dialect != self.get_target_dialect()
        if translation_needed:
            original_len = len(schema_sql)
            self.log_verbose(f"Translating schema SQL from {source_dialect} to {self.get_target_dialect()}")
            self.log_very_verbose(f"SQL before translation: {original_len} characters")
            schema_sql = self.translate_sql(schema_sql, source_dialect)
            self.log_verbose(f"SQL after translation: {len(schema_sql)} characters (was {original_len})")
            if len(schema_sql) < original_len * 0.5:
                self.logger.warning(
                    f"Translation reduced SQL size significantly: {original_len} -> {len(schema_sql)} characters. "
                    "This may indicate a translation problem."
                )

        self.log_operation_complete(
            "Schema SQL generation",
            details=f"{len(schema_sql)} characters, translation: {'yes' if translation_needed else 'no'}",
        )

        return schema_sql

    def _execute_schema_statements(self, statements: list[str], cursor: Any) -> tuple[int, list[tuple[str, str]]]:
        tables_created = 0
        failed_tables: list[tuple[str, str]] = []

        for i, statement in enumerate(statements, 1):
            if not statement.strip():
                continue

            table_name = "unknown"
            match = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)", statement, re.IGNORECASE)
            if match:
                table_name = match.group(1).strip("`").strip('"')

            try:
                self.log_very_verbose(f"Creating table {table_name} ({i}/{len(statements)})")
                self.log_very_verbose(f"SQL: {statement[:150]}...")

                cursor.execute(statement)
                tables_created += 1
                self.log_very_verbose(f"✅ Created table {table_name}")

            except Exception as e:
                error_msg = str(e)
                self.logger.error(f"❌ Failed to create table {table_name}: {error_msg}")
                self.log_very_verbose(f"Failed SQL: {statement[:200]}...")
                failed_tables.append((table_name, error_msg))

        self.log_verbose(f"Schema creation: {tables_created} tables created, {len(failed_tables)} failed")

        if failed_tables:
            failure_details = "\n".join([f"  - {table}: {error[:100]}" for table, error in failed_tables])
            raise RuntimeError(
                f"Failed to create {len(failed_tables)} table(s) out of {len(statements)}:\n{failure_details}"
            )

        return tables_created, failed_tables

    def _get_constraint_configuration(self) -> tuple[bool, bool]:
        effective_config = self.get_effective_tuning_configuration()
        enable_primary_keys = effective_config.primary_keys.enabled if effective_config else False
        enable_foreign_keys = effective_config.foreign_keys.enabled if effective_config else False

        return enable_primary_keys, enable_foreign_keys

    def _log_constraint_configuration(self, enable_primary_keys: bool, enable_foreign_keys: bool) -> None:
        if enable_primary_keys:
            self.logger.info(f"Primary key constraints enabled for {self.platform_name}")

        if enable_foreign_keys:
            self.logger.info(f"Foreign key constraints enabled for {self.platform_name}")

        if not enable_primary_keys and not enable_foreign_keys:
            self.logger.debug(f"No constraints enabled for {self.platform_name}")

        self.logger.debug(
            f"Schema constraints from tuning config: primary_keys={enable_primary_keys}, foreign_keys={enable_foreign_keys}"
        )


__all__ = [
    "DataLoadingError",
    "ClickHouseServerLoadError",
    "DataSource",
    "CsvDialect",
    "DUCKDB_NO_NULL_CONVERSION_SENTINEL",
    "resolve_csv_dialect",
    "prepare_local_load_file",
    "DataSourceResolver",
    "SchemaHelpersMixin",
    "CompressionHandler",
    "GzipHandler",
    "ZstdHandler",
    "FileFormatHandler",
    "DelimitedFileHandler",
    "ParquetFileHandler",
    "DuckDBNativeHandler",
    "DuckDBParquetHandler",
    "DeltaFileHandler",
    "DuckDBDeltaHandler",
    "DuckLakeFileHandler",
    "DuckDBDuckLakeHandler",
    "IcebergFileHandler",
    "VortexFileHandler",
    "DuckDBVortexHandler",
    "ClickHouseNativeHandler",
    "InMemoryDataHandler",
    "FileFormatRegistry",
    "DataLoader",
    "run_staged_table_loads",
    "validate_sql_identifier",
    "escape_sql_string_literal",
]
