# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.core.dataframe.capabilities import (
    DataFormat,
    get_platform_capabilities,
)
from benchbox.core.dataframe.schema_utils import get_benchmark_schema_columns
from benchbox.core.dataframe.tuning.write_config import (
    DataFrameWriteConfiguration,
)
from benchbox.core.results import normalize_benchmark_id
from benchbox.utils.compression import CompressionError, CompressionManager
from benchbox.utils.file_format import (
    TRAILING_DUMMY_COLUMN,
    detect_compression,
    detect_data_format,
    has_trailing_delimiter,
    strip_compression_suffix,
)
from benchbox.utils.path_utils import get_benchmark_runs_dataframe_path, get_benchmark_runs_datagen_path

if TYPE_CHECKING:
    from benchbox.core.tpch.schema import Table
    from benchbox.platforms.base.data_loading import CsvDialect

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("benchmark_runs") / "datagen"
DATAFRAME_CACHE_VERSION = "v9"

_KNOWN_FORMAT_DIRS = frozenset(f.value for f in DataFormat)

_CACHE_TEMP_FILE_PATTERN = re.compile(r"\.\d+\.[0-9a-f]{32}\.tmp$")


def _is_cache_temp_file(name: str) -> bool:
    return _CACHE_TEMP_FILE_PATTERN.search(name) is not None


def _fsync_path(path: Path) -> None:
    fd = os.open(path, os.O_RDWR if sys.platform == "win32" else os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _atomic_cache_write(target_path: Path) -> Iterator[Path]:
    tmp_path = target_path.with_name(f"{target_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        yield tmp_path
        _fsync_path(tmp_path)
        os.replace(tmp_path, target_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


class ConversionStatus(Enum):
    NOT_NEEDED = "not_needed"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class LoadedTable:
    table_name: str
    file_path: Path
    format: DataFormat
    row_count: int | None = None
    size_bytes: int | None = None
    load_time_seconds: float | None = None


@dataclass
class DataLoadResult:
    tables: dict[str, LoadedTable] = field(default_factory=dict)
    total_load_time_seconds: float = 0.0
    source_format: DataFormat = DataFormat.CSV
    target_format: DataFormat = DataFormat.PARQUET
    conversion_performed: bool = False
    cache_hit: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0 and len(self.tables) > 0


@dataclass
class CacheManifest:
    benchmark: str
    scale_factor: float
    format: str
    created_at: str
    source_hash: str
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark": self.benchmark,
            "scale_factor": self.scale_factor,
            "format": self.format,
            "created_at": self.created_at,
            "source_hash": self.source_hash,
            "tables": self.tables,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CacheManifest:
        return cls(
            benchmark=data["benchmark"],
            scale_factor=data["scale_factor"],
            format=data["format"],
            created_at=data["created_at"],
            source_hash=data["source_hash"],
            tables=data.get("tables", {}),
        )


class SchemaMapper:
    POLARS_TYPE_MAP: dict[str, str] = {
        "INTEGER": "Int64",
        "DECIMAL(15,2)": "Float64",
        "VARCHAR": "Utf8",
        "CHAR": "Utf8",
        "DATE": "Date",
        "TIMESTAMP": "Datetime",
        "TIME": "Utf8",
    }

    PANDAS_TYPE_MAP: dict[str, str] = {
        "INTEGER": "int64",
        "DECIMAL(15,2)": "float64",
        "VARCHAR": "object",
        "CHAR": "object",
        "DATE": "datetime64[ns]",
        "TIMESTAMP": "datetime64[ns]",
        "TIME": "object",
    }

    PYARROW_TYPE_MAP: dict[str, str] = {
        "INTEGER": "int64",
        "DECIMAL(15,2)": "float64",
        "VARCHAR": "string",
        "CHAR": "string",
        "DATE": "date32",
        "TIMESTAMP": "timestamp[us]",
        "TIME": "string",
    }

    _ARROW_TYPE_FAMILIES: dict[str, str] = {
        "TEXT": "string",
        "STRING": "string",
        "CHARACTER": "string",
        "NCHAR": "string",
        "NVARCHAR": "string",
        "CLOB": "string",
        "UUID": "string",
        "JSON": "string",
        "JSONB": "string",
        "ENUM": "string",
        "INT": "int64",
        "INT2": "int64",
        "INT4": "int64",
        "INT8": "int64",
        "TINYINT": "int64",
        "SMALLINT": "int64",
        "MEDIUMINT": "int64",
        "BIGINT": "int64",
        "SERIAL": "int64",
        "BIGSERIAL": "int64",
        "DECIMAL": "float64",
        "NUMERIC": "float64",
        "NUMBER": "float64",
        "DOUBLE": "float64",
        "REAL": "float64",
        "FLOAT": "float64",
        "FLOAT4": "float64",
        "FLOAT8": "float64",
        "MONEY": "float64",
    }

    @classmethod
    def sql_type_to_pyarrow(cls, sql_type: str) -> str | None:
        normalized = sql_type.strip().upper()
        pre_paren = normalized.split("(", 1)[0].strip()
        base = pre_paren.split()[0] if pre_paren.split() else pre_paren

        mapped = cls.PYARROW_TYPE_MAP.get(normalized) or cls.PYARROW_TYPE_MAP.get(base)
        if mapped:
            return mapped

        family = cls._ARROW_TYPE_FAMILIES.get(base)
        if family:
            return family

        if "CHAR" in base:
            return "string"
        if base.startswith("DOUBLE"):
            return "float64"
        if base.startswith("TIMESTAMP") or base.startswith("DATETIME"):
            return "timestamp[us]"
        if base.startswith("TIME"):
            return "string"
        return None

    @classmethod
    def get_column_names(cls, table: Table) -> list[str]:
        return [col.name for col in table.columns]

    @classmethod
    def get_polars_schema(cls, table: Table) -> dict[str, str]:
        schema = {}
        for col in table.columns:
            dtype_value = col.data_type.value
            polars_type = cls.POLARS_TYPE_MAP.get(dtype_value, "Utf8")
            schema[col.name] = polars_type
        return schema

    @classmethod
    def get_pandas_schema(cls, table: Table) -> dict[str, str]:
        schema = {}
        for col in table.columns:
            dtype_value = col.data_type.value
            pandas_type = cls.PANDAS_TYPE_MAP.get(dtype_value, "object")
            schema[col.name] = pandas_type
        return schema

    @classmethod
    def get_pyarrow_schema(cls, table: Table) -> dict[str, str]:
        schema = {}
        for col in table.columns:
            dtype_value = col.data_type.value
            arrow_type = cls.PYARROW_TYPE_MAP.get(dtype_value, "string")
            schema[col.name] = arrow_type
        return schema


class FormatConverter:
    @staticmethod
    def convert_csv_to_parquet(
        source_path: Path,
        target_path: Path,
        column_names: list[str] | None = None,
        delimiter: str = "|",
        compression: str = "zstd",
        write_config: DataFrameWriteConfiguration | None = None,
        column_types: dict[str, str] | None = None,
        null_marker: str | None = "",
        has_header: bool = False,
    ) -> tuple[ConversionStatus, int]:
        try:
            import pyarrow.csv as pv
            import pyarrow.parquet as pq
        except ImportError as e:
            logger.error(f"PyArrow not installed: {e}")
            return ConversionStatus.FAILED, 0

        try:
            format_type = detect_data_format(source_path)
            is_tbl_file = format_type == "tbl"
            has_trailing = (
                is_tbl_file and bool(column_names) and has_trailing_delimiter(source_path, delimiter, column_names)
            )

            actual_column_names = column_names
            if is_tbl_file and column_names and has_trailing:
                actual_column_names = column_names + [TRAILING_DUMMY_COLUMN]

            read_options = pv.ReadOptions(
                column_names=actual_column_names if actual_column_names else None,
                skip_rows=1 if (has_header and actual_column_names) else 0,
            )
            parse_options = pv.ParseOptions(delimiter=delimiter)
            arrow_column_types = FormatConverter._resolve_arrow_types(column_types)

            from benchbox.core.dataframe.csv_dialect import dialect_preserves_empty_strings

            convert_options = pv.ConvertOptions(
                auto_dict_encode=True,
                null_values=[""] if not null_marker else ["", null_marker],
                strings_can_be_null=not dialect_preserves_empty_strings(null_marker),
                column_types=arrow_column_types,
            )

            logger.debug(f"Reading {source_path}")
            table = FormatConverter._read_csv_source(source_path, read_options, parse_options, convert_options, pv)
            if table is None:
                return ConversionStatus.FAILED, 0

            if is_tbl_file and column_names and has_trailing:
                table = table.select(column_names)

            if null_marker and dialect_preserves_empty_strings(null_marker):
                table = FormatConverter._null_marker_strings_to_null(table, null_marker)

            table = FormatConverter._coerce_time_columns_to_string(table)

            if write_config:
                table = FormatConverter._apply_write_config(table, write_config)
                if write_config.compression != "zstd":
                    compression = write_config.compression

            target_path.parent.mkdir(parents=True, exist_ok=True)

            write_kwargs = FormatConverter._build_write_kwargs(compression, write_config, table)

            logger.debug(f"Writing {target_path}")
            with _atomic_cache_write(target_path) as tmp_path:
                pq.write_table(table, tmp_path, **write_kwargs)

            row_count = table.num_rows
            logger.info(f"Converted {source_path.name} → {target_path.name}: {row_count:,} rows")

            return ConversionStatus.SUCCESS, row_count

        except Exception as e:
            logger.error(f"Conversion failed for {source_path}: {e}")
            return ConversionStatus.FAILED, 0

    @staticmethod
    def _resolve_arrow_types(column_types: dict[str, str] | None) -> dict[str, Any] | None:
        if not column_types:
            return None
        import pyarrow as pa

        type_lookup = {
            "date32": pa.date32(),
            "timestamp[us]": pa.timestamp("us"),
            "int64": pa.int64(),
            "float64": pa.float64(),
            "string": pa.string(),
        }
        return {col: type_lookup.get(dtype, pa.string()) for col, dtype in column_types.items()}

    @staticmethod
    def _null_marker_strings_to_null(table: Any, null_marker: str) -> Any:
        import pyarrow as pa
        import pyarrow.compute as pc

        for index, schema_field in enumerate(table.schema):
            value_type = (
                schema_field.type.value_type if pa.types.is_dictionary(schema_field.type) else schema_field.type
            )
            if not (pa.types.is_string(value_type) or pa.types.is_large_string(value_type)):
                continue
            column = table.column(index)
            if value_type != schema_field.type:
                column = column.cast(value_type)
            is_marker = pc.equal(column, null_marker)
            if not pc.any(is_marker).as_py():
                continue
            table = table.set_column(
                index, schema_field.name, pc.if_else(is_marker, pa.scalar(None, type=value_type), column)
            )
        return table

    @staticmethod
    def _coerce_time_columns_to_string(table: Any) -> Any:
        import pyarrow as pa
        import pyarrow.compute as pc

        time_columns = [
            field.name for field in table.schema if pa.types.is_time32(field.type) or pa.types.is_time64(field.type)
        ]
        if not time_columns:
            return table
        logger.info(f"Casting inferred TIME column(s) to string for Parquet compatibility: {time_columns}")
        for name in time_columns:
            index = table.schema.get_field_index(name)
            table = table.set_column(index, name, pc.cast(table.column(name), pa.string()))
        return table

    @staticmethod
    def _build_write_kwargs(compression: str, write_config: Any, table: Any) -> dict[str, Any]:
        write_kwargs: dict[str, Any] = {
            "compression": compression,
            "use_dictionary": True,
        }
        if not write_config:
            return write_kwargs

        if write_config.row_group_size is not None:
            write_kwargs["row_group_size"] = write_config.row_group_size
        if write_config.compression_level is not None:
            write_kwargs["compression_level"] = write_config.compression_level
        if write_config.dictionary_columns:
            write_kwargs["use_dictionary"] = write_config.dictionary_columns
        elif write_config.skip_dictionary_columns:
            all_cols = set(table.column_names)
            write_kwargs["use_dictionary"] = list(all_cols - set(write_config.skip_dictionary_columns))
        if write_config.data_page_version is not None:
            write_kwargs["data_page_version"] = write_config.data_page_version

        return write_kwargs

    @staticmethod
    def _read_csv_source(
        source_path: Path, read_options: Any, parse_options: Any, convert_options: Any, pv: Any
    ) -> Any:
        compression_type = detect_compression(source_path)
        if compression_type:
            manager = CompressionManager()
            try:
                compressor = manager.get_compressor(compression_type)
                with compressor.open_for_read(source_path, mode="rb") as stream:
                    return pv.read_csv(
                        stream,
                        read_options=read_options,
                        parse_options=parse_options,
                        convert_options=convert_options,
                    )
            except CompressionError as err:
                logger.error(f"Failed to read compressed file {source_path}: {err}")
                return None
        return pv.read_csv(
            source_path,
            read_options=read_options,
            parse_options=parse_options,
            convert_options=convert_options,
        )

    @staticmethod
    def _apply_write_config(
        table: Any,
        write_config: DataFrameWriteConfiguration,
    ) -> Any:
        import pyarrow.compute as pc

        if write_config.sort_by:
            sort_keys = []
            for sort_col in write_config.sort_by:
                if sort_col.name not in table.column_names:
                    logger.warning(f"Sort column '{sort_col.name}' not found in table, skipping")
                    continue
                order = "ascending" if sort_col.order == "asc" else "descending"
                sort_keys.append((sort_col.name, order))

            if sort_keys:
                logger.debug(f"Sorting table by {sort_keys}")
                indices = pc.sort_indices(table, sort_keys=sort_keys)
                table = table.take(indices)

        return table


class DataCache:
    def __init__(self, cache_dir: str | Path | None = None):
        env_cache_dir = os.environ.get("BENCHBOX_CACHE_DIR")
        if cache_dir is not None:
            self.cache_dir = Path(cache_dir)
        elif env_cache_dir:
            self.cache_dir = Path(env_cache_dir)
        else:
            self.cache_dir = get_benchmark_runs_dataframe_path()
        self.cache_version = DATAFRAME_CACHE_VERSION

    def get_cache_path(self, benchmark: str, scale_factor: float, format: DataFormat) -> Path:
        base = get_benchmark_runs_datagen_path(benchmark, scale_factor, self.cache_dir)
        return base / format.value / self.cache_version

    def get_manifest_path(self, benchmark: str, scale_factor: float, format: DataFormat) -> Path:
        cache_path = self.get_cache_path(benchmark, scale_factor, format)
        return cache_path / "_manifest.json"

    def has_cached_data(
        self,
        benchmark: str,
        scale_factor: float,
        format: DataFormat,
        source_hash: str | None = None,
    ) -> bool:
        manifest_path = self.get_manifest_path(benchmark, scale_factor, format)

        if not manifest_path.exists():
            return False

        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = CacheManifest.from_dict(json.load(f))

            if source_hash and manifest.source_hash != source_hash:
                logger.debug(f"Cache hash mismatch: {manifest.source_hash} != {source_hash}")
                return False

            cache_dir = manifest_path.parent
            for _table_name, table_info in manifest.tables.items():
                if "files" in table_info:
                    for file_name in table_info["files"]:
                        file_path = cache_dir / file_name
                        if not file_path.exists():
                            logger.debug(f"Cache file missing: {file_path}")
                            return False
                else:
                    file_path = cache_dir / table_info["file"]
                    if not file_path.exists():
                        logger.debug(f"Cache file missing: {file_path}")
                        return False

            return True

        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"Invalid cache manifest: {e}")
            return False

    def get_cached_files(
        self, benchmark: str, scale_factor: float, format: DataFormat
    ) -> dict[str, Path | list[Path]] | None:
        manifest_path = self.get_manifest_path(benchmark, scale_factor, format)

        if not manifest_path.exists():
            return None

        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = CacheManifest.from_dict(json.load(f))

            cache_dir = manifest_path.parent
            resolved: dict[str, Path | list[Path]] = {}
            for table_name, table_info in manifest.tables.items():
                if "files" in table_info:
                    resolved[table_name] = [cache_dir / file_name for file_name in table_info["files"]]
                else:
                    resolved[table_name] = cache_dir / table_info["file"]
            return resolved

        except (json.JSONDecodeError, KeyError):
            return None

    def save_manifest(
        self,
        benchmark: str,
        scale_factor: float,
        format: DataFormat,
        source_hash: str,
        tables: dict[str, dict[str, Any]],
    ) -> None:
        manifest = CacheManifest(
            benchmark=benchmark,
            scale_factor=scale_factor,
            format=format.value,
            created_at=datetime.now().astimezone().isoformat(),
            source_hash=source_hash,
            tables=tables,
        )

        manifest_path = self.get_manifest_path(benchmark, scale_factor, format)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

        with _atomic_cache_write(manifest_path) as tmp_path, open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, indent=2)

    def clear_cache(
        self,
        benchmark: str | None = None,
        scale_factor: float | None = None,
        format: DataFormat | None = None,
    ) -> int:
        if not self.cache_dir.exists():
            return 0

        removed = 0

        if format is not None and benchmark is not None and scale_factor is not None:
            cache_path = self.get_cache_path(benchmark, scale_factor, format)
            if cache_path.exists():
                for f in cache_path.iterdir():
                    f.unlink()
                    removed += 1
                cache_path.rmdir()
        elif benchmark is not None and scale_factor is not None:
            base = get_benchmark_runs_datagen_path(benchmark, scale_factor, self.cache_dir)
            removed += self._remove_format_subdirs(base)
        elif benchmark is not None:
            for child in self.cache_dir.iterdir():
                if child.is_dir() and child.name.startswith(f"{benchmark}_sf"):
                    removed += self._remove_format_subdirs(child)
        else:
            for child in self.cache_dir.iterdir():
                if child.is_dir():
                    removed += self._remove_format_subdirs(child)

        return removed

    def _remove_format_subdirs(self, base_dir: Path) -> int:
        if not base_dir.exists():
            return 0
        removed = 0
        for child in base_dir.iterdir():
            if child.is_dir() and child.name in _KNOWN_FORMAT_DIRS:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        return removed


def _compute_source_hash(source_dir: Path, tables: dict[str, Path | list[Path]]) -> str:
    hash_data = []
    for table_name in sorted(tables.keys()):
        file_paths = tables[table_name]
        file_list = file_paths if isinstance(file_paths, list) else [file_paths]
        for file_path in file_list:
            if file_path.exists():
                stat = file_path.stat()
                hash_data.append(f"{table_name}:{file_path.name}:{stat.st_mtime}:{stat.st_size}")

    combined = "|".join(hash_data)
    return hashlib.md5(combined.encode()).hexdigest()[:12]


class DataFrameDataLoader:
    def __init__(
        self,
        platform: str = "polars",
        cache_dir: str | Path | None = None,
        prefer_parquet: bool = True,
        force_regenerate: bool = False,
        write_config: DataFrameWriteConfiguration | None = None,
    ):
        self.platform = platform.lower().replace("-df", "")
        self.cache = DataCache(cache_dir)
        self.prefer_parquet = prefer_parquet
        self.force_regenerate = force_regenerate
        self.write_config = write_config
        self.applied_write_layout: DataFrameWriteConfiguration | None = None

        try:
            self.capabilities = get_platform_capabilities(self.platform)
        except ValueError:
            self.capabilities = None
            logger.warning(f"Unknown platform '{platform}', using default settings")

    def get_optimal_format(self, scale_factor: float) -> DataFormat:
        if not self.prefer_parquet:
            return DataFormat.CSV

        if self.capabilities:
            recommended = self.capabilities.recommended_data_format
            if isinstance(recommended, DataFormat):
                return recommended
            elif recommended == "parquet":
                return DataFormat.PARQUET
            elif recommended == "arrow":
                return DataFormat.ARROW
            else:
                return DataFormat.CSV

        if scale_factor >= 0.1:
            return DataFormat.PARQUET
        return DataFormat.CSV

    def prepare_benchmark_data(
        self,
        benchmark: Any,
        scale_factor: float,
        data_dir: Path | None = None,
        write_config: DataFrameWriteConfiguration | None = None,
    ) -> dict[str, Path | list[Path]]:
        raw_name = getattr(benchmark, "name", None) or getattr(benchmark, "_name", None) or "unknown"
        benchmark_name = normalize_benchmark_id(raw_name)

        source_files = self._get_source_files(benchmark, data_dir)
        source_files = self._filter_source_files_for_benchmark(benchmark, source_files)
        if not source_files:
            raise ValueError("No source data files found")

        target_format = self.get_optimal_format(scale_factor)

        effective_write_config = write_config or self.write_config
        self.applied_write_layout = None
        layout_applied = bool(
            effective_write_config and not effective_write_config.is_default() and target_format == DataFormat.PARQUET
        )

        source_format = self._detect_source_format(source_files)
        needs_layout_transform = bool(effective_write_config and effective_write_config.sort_by)
        if source_format == target_format and not needs_layout_transform:
            logger.info(f"Source data already in {target_format.value} format")
            return source_files
        if source_format == target_format and needs_layout_transform:
            logger.info(
                f"Source data is already in {target_format.value} format but sort_by is configured; "
                "routing through conversion pipeline to apply physical sort."
            )

        first_entry = next(iter(source_files.values()))
        first_path = first_entry[0] if isinstance(first_entry, list) else first_entry
        source_hash = _compute_source_hash(
            data_dir or Path(first_path).parent,
            source_files,
        )
        table_metadata_hints = self._read_manifest_dialect_hints(data_dir, list(source_files))
        dialects = self._resolve_table_dialects(benchmark, source_files, table_metadata_hints)
        dialect_key = "|".join(
            f"{table}:{dialects[table].delimiter}:{dialects[table].has_header}:"
            f"{dialects[table].null_marker}:{dialects[table].normalize_booleans}"
            for table in sorted(dialects)
        )
        source_hash = hashlib.md5(f"{source_hash}:{dialect_key}".encode()).hexdigest()[:12]

        if effective_write_config and not effective_write_config.is_default():
            import json

            config_str = json.dumps(effective_write_config.to_dict(), sort_keys=True)
            source_hash = hashlib.md5(f"{source_hash}:{config_str}".encode()).hexdigest()[:12]

        if not self.force_regenerate and self.cache.has_cached_data(
            benchmark_name, scale_factor, target_format, source_hash
        ):
            cached = self.cache.get_cached_files(benchmark_name, scale_factor, target_format)
            if cached:
                logger.info(f"Using cached {target_format.value} data")
                if layout_applied:
                    self.applied_write_layout = effective_write_config
                return cached

        converted = self._convert_data(
            benchmark=benchmark,
            benchmark_name=benchmark_name,
            scale_factor=scale_factor,
            source_files=source_files,
            source_format=source_format,
            target_format=target_format,
            source_hash=source_hash,
            write_config=effective_write_config,
            data_dir=data_dir,
        )
        if layout_applied:
            self.applied_write_layout = effective_write_config
        return converted

    def _get_source_files(self, benchmark: Any, data_dir: Path | None) -> dict[str, list[Path]]:
        resolved = self._resolve_table_paths(getattr(benchmark, "tables", None))

        if not resolved and data_dir and data_dir.exists():
            return self._discover_files(data_dir)

        if not resolved:
            return {}

        for name, paths in resolved.items():
            dir_paths = [p for p in paths if p.is_dir()]
            if dir_paths:
                raise ValueError(
                    f"Table '{name}': expected file path(s) but found directory: {dir_paths[0]}. "
                    f"This may indicate stale data from a previous format conversion. "
                    f"Use --force datagen to regenerate."
                )

        return resolved

    def _resolve_table_paths(self, tables: Any) -> dict[str, list[Path]]:
        if not isinstance(tables, dict):
            return {}

        resolved: dict[str, list[Path]] = {}
        for name, path in tables.items():
            entries = path if isinstance(path, list) else [path]
            resolved[name] = [entry if isinstance(entry, Path) else Path(entry) for entry in entries]
        return resolved

    def _expected_benchmark_tables(self, benchmark: Any) -> set[str]:
        expected: set[str] = set()
        benchmark_name = (
            getattr(benchmark, "name", None) or getattr(benchmark, "_name", None) or benchmark.__class__.__name__
        )

        if hasattr(benchmark, "get_schema"):
            try:
                schema = benchmark.get_schema()
                if isinstance(schema, dict):
                    expected.update(str(name).lower() for name in schema.keys())
            except Exception:
                pass

        if hasattr(benchmark, "tables") and isinstance(benchmark.tables, dict):
            expected.update(str(name).lower() for name in benchmark.tables.keys())

        impl = getattr(benchmark, "_impl", None)
        if impl is not None and hasattr(impl, "tables") and isinstance(impl.tables, dict):
            expected.update(str(name).lower() for name in impl.tables.keys())

        if not expected:
            logger.warning(
                "Unable to infer expected source tables for benchmark %s; DataFrame source filtering is disabled.",
                benchmark_name,
            )

        return expected

    def _filter_source_files_for_benchmark(
        self,
        benchmark: Any,
        source_files: dict[str, list[Path]],
    ) -> dict[str, list[Path]]:
        expected_tables = self._expected_benchmark_tables(benchmark)
        if not expected_tables:
            return {str(name).lower(): list(paths) for name, paths in source_files.items()}

        filtered: dict[str, list[Path]] = {}
        dropped: list[str] = []
        for table_name, paths in source_files.items():
            normalized_name = str(table_name).lower()
            if normalized_name not in expected_tables:
                dropped.append(normalized_name)
                continue
            filtered.setdefault(normalized_name, []).extend(paths)

        if dropped:
            logger.info(
                "Filtered %d non-benchmark source tables from DataFrame conversion: %s",
                len(dropped),
                ", ".join(sorted(set(dropped))),
            )
        return filtered

    def _discover_files(self, data_dir: Path) -> dict[str, list[Path]]:
        files: dict[str, list[Path]] = {}

        for pattern in ["*.tbl", "*.csv", "*.parquet"]:
            for file_path in data_dir.glob(pattern):
                if file_path.is_dir():
                    parquet_files = list(file_path.glob("*.parquet"))
                    if parquet_files:
                        for pq_file in parquet_files:
                            if pq_file.is_file():
                                table_name = pq_file.stem.lower()
                                files.setdefault(table_name, []).append(pq_file)
                    continue

                table_name = file_path.stem.lower()
                files.setdefault(table_name, []).append(file_path)

        return files

    def _detect_source_format(self, source_files: dict[str, Path | list[Path]]) -> DataFormat:
        for path in source_files.values():
            paths = path if isinstance(path, list) else [path]
            for entry in paths:
                format_type = detect_data_format(entry)
                if format_type == "parquet":
                    return DataFormat.PARQUET
                if format_type == "tbl":
                    return DataFormat.CSV
                stripped_suffix = strip_compression_suffix(entry).suffix.lower()
                if stripped_suffix == ".arrow":
                    return DataFormat.ARROW

        return DataFormat.CSV

    def _convert_data(
        self,
        benchmark: Any,
        benchmark_name: str,
        scale_factor: float,
        source_files: dict[str, Path | list[Path]],
        source_format: DataFormat,
        target_format: DataFormat,
        source_hash: str,
        write_config: DataFrameWriteConfiguration | None = None,
        data_dir: Path | None = None,
    ) -> dict[str, Path | list[Path]]:
        if target_format != DataFormat.PARQUET:
            logger.warning(f"Conversion to {target_format.value} not supported, using source")
            return source_files

        cache_path = self.cache.get_cache_path(benchmark_name, scale_factor, target_format)
        cache_path.mkdir(parents=True, exist_ok=True)

        if write_config and not write_config.is_default():
            enabled_types = write_config.get_enabled_types()
            logger.info(
                f"Converting {len(source_files)} tables from CSV/TBL to Parquet "
                f"with write tuning: {[t.value for t in enabled_types]}"
            )
        else:
            logger.info(f"Converting {len(source_files)} tables from CSV/TBL to Parquet")

        schema_info = self._get_schema_info(benchmark)
        pyarrow_types = self._get_pyarrow_types(benchmark)
        table_metadata_hints = self._read_manifest_dialect_hints(data_dir, list(source_files))
        dialects = self._resolve_table_dialects(benchmark, source_files, table_metadata_hints)

        converted_files: dict[str, Path | list[Path]] = {}
        table_metadata: dict[str, dict[str, Any]] = {}

        for table_name, source_path in source_files.items():
            source_list = source_path if isinstance(source_path, list) else [source_path]
            column_names = schema_info.get(table_name)
            column_types = pyarrow_types.get(table_name)
            table_write_config = self._get_table_write_config(write_config, table_name, column_names)
            table_dialect = dialects[table_name]

            converted_list, table_entries = self._convert_table_files(
                table_name,
                source_list,
                column_names,
                column_types,
                table_write_config,
                table_dialect.delimiter,
                cache_path,
                null_marker=table_dialect.null_marker,
                has_header=table_dialect.has_header,
            )

            if len(converted_list) == 1:
                converted_files[table_name] = converted_list[0]
                table_metadata[table_name] = table_entries[0]
            else:
                converted_files[table_name] = converted_list
                table_metadata[table_name] = {
                    "files": [entry["file"] for entry in table_entries],
                    "row_counts": [entry["row_count"] for entry in table_entries],
                    "sources": [entry["source"] for entry in table_entries],
                }

            if table_write_config and not table_write_config.is_default():
                table_metadata[table_name]["write_config"] = table_write_config.to_dict()

        self.cache.save_manifest(
            benchmark=benchmark_name,
            scale_factor=scale_factor,
            format=target_format,
            source_hash=source_hash,
            tables=table_metadata,
        )

        tracked_files: set[str] = set()
        for table_info in table_metadata.values():
            if "files" in table_info:
                tracked_files.update(str(name) for name in table_info["files"])
            elif "file" in table_info:
                tracked_files.add(str(table_info["file"]))
        self._prune_cache_leaf_files(cache_path, tracked_files)

        return converted_files

    def _convert_table_files(
        self,
        table_name: str,
        source_list: list[Path],
        column_names: list[str] | None,
        column_types: dict[str, str] | None,
        table_write_config: DataFrameWriteConfiguration | None,
        benchmark_delimiter: str | None,
        cache_path: Path,
        *,
        null_marker: str | None = "",
        has_header: bool = False,
    ) -> tuple[list[Path], list[dict[str, Any]]]:
        converted_list: list[Path] = []
        table_entries: list[dict[str, Any]] = []

        for source_entry in source_list:
            format_type = detect_data_format(source_entry)
            delimiter = "|" if format_type == "tbl" else (benchmark_delimiter or ",")

            stripped_name = strip_compression_suffix(source_entry).name
            if format_type == "tbl":
                base_name = stripped_name.replace(".tbl", "", 1).replace(".dat", "", 1)
            elif format_type == "csv":
                base_name = stripped_name.replace(".csv", "", 1)
            else:
                base_name = Path(stripped_name).stem

            target_path = cache_path / f"{base_name}.parquet"

            status, row_count = FormatConverter.convert_csv_to_parquet(
                source_path=source_entry,
                target_path=target_path,
                column_names=column_names,
                delimiter=delimiter,
                write_config=table_write_config,
                column_types=column_types,
                null_marker=null_marker,
                has_header=has_header and format_type == "csv",
            )

            if status == ConversionStatus.SUCCESS:
                converted_list.append(target_path)
                table_entries.append({"file": target_path.name, "row_count": row_count, "source": source_entry.name})
            else:
                logger.error(f"Failed to convert {table_name}, using source file")
                converted_list.append(source_entry)
                table_entries.append(
                    {"file": source_entry.name, "row_count": 0, "source": source_entry.name, "status": "FAILED"}
                )

        return converted_list, table_entries

    def _prune_cache_leaf_files(self, cache_path: Path, tracked_files: set[str]) -> int:
        if not cache_path.exists():
            return 0

        preserved = set(tracked_files)
        preserved.add("_manifest.json")
        removed = 0
        for child in cache_path.iterdir():
            if child.is_dir() or child.name in preserved or _is_cache_temp_file(child.name):
                continue
            child.unlink(missing_ok=True)
            removed += 1
        return removed

    def _get_table_write_config(
        self,
        write_config: DataFrameWriteConfiguration | None,
        table_name: str,
        column_names: list[str] | None,
    ) -> DataFrameWriteConfiguration | None:
        if write_config is None or write_config.is_default():
            return write_config

        if column_names is None:
            return write_config

        valid_columns = set(column_names)
        filtered_sort_by = [s for s in write_config.sort_by if s.name in valid_columns]

        filtered_partition_by = [p for p in write_config.partition_by if p.name in valid_columns]

        filtered_dict_cols = [c for c in write_config.dictionary_columns if c in valid_columns]
        filtered_skip_dict_cols = [c for c in write_config.skip_dictionary_columns if c in valid_columns]

        return DataFrameWriteConfiguration(
            partition_by=filtered_partition_by,
            sort_by=filtered_sort_by,
            row_group_size=write_config.row_group_size,
            target_file_size_mb=write_config.target_file_size_mb,
            repartition_count=write_config.repartition_count,
            compression=write_config.compression,
            compression_level=write_config.compression_level,
            dictionary_columns=filtered_dict_cols,
            skip_dictionary_columns=filtered_skip_dict_cols,
        )

    @staticmethod
    def _read_manifest_dialect_hints(data_dir: Path | None, table_names: list[str]) -> dict[str, dict[str, Any]]:
        if data_dir is None or not table_names:
            return {}
        from benchbox.platforms.base.data_loading import ManifestFileSource

        manifest_path = Path(data_dir) / "_datagen_manifest.json"
        return ManifestFileSource().read_table_metadata_hints(manifest_path, table_names)

    def _resolve_table_dialects(
        self,
        benchmark: Any,
        source_files: dict[str, Path | list[Path]],
        table_metadata: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, CsvDialect]:
        from benchbox.platforms.base.data_loading import CsvDialect, DataSource, resolve_csv_dialect

        dialects: dict[str, CsvDialect] = {}
        source = DataSource(
            source_type="benchmark_instance",
            tables={},
            table_metadata=dict(table_metadata) if table_metadata else {},
        )
        for table_name, paths in source_files.items():
            first = paths[0] if isinstance(paths, list) else paths
            try:
                dialects[table_name] = resolve_csv_dialect(source, table_name, Path(first), benchmark)
            except Exception:
                logger.debug("Falling back to attribute-only CSV dialect for table '%s'", table_name)
                dialects[table_name] = CsvDialect(
                    delimiter=getattr(benchmark, "csv_delimiter", None) or ",",
                    has_header=bool(getattr(benchmark, "csv_has_header", False)),
                    null_marker="",
                    normalize_booleans=False,
                    quote=None,
                )
        return dialects

    def _get_null_markers(
        self,
        benchmark: Any,
        source_files: dict[str, Path | list[Path]],
        table_metadata: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, str | None]:
        return {
            table_name: dialect.null_marker
            for table_name, dialect in self._resolve_table_dialects(benchmark, source_files, table_metadata).items()
        }

    def _get_schema_info(self, benchmark: Any) -> dict[str, list[str]]:
        schema_info: dict[str, list[str]] = {}

        for table_name, columns in get_benchmark_schema_columns(benchmark).items():
            schema_info[table_name] = [column["name"] for column in columns]

        try:
            from benchbox.core.tpch.schema import TABLES

            for table in TABLES:
                if table.name.lower() not in schema_info:
                    schema_info[table.name.lower()] = [col.name for col in table.columns]
        except ImportError:
            pass

        return schema_info

    def _get_pyarrow_types(self, benchmark: Any) -> dict[str, dict[str, str]]:
        type_info: dict[str, dict[str, str]] = {}

        for table_name, columns in get_benchmark_schema_columns(benchmark).items():
            col_types = self._extract_arrow_types(
                columns,
                get_name=lambda column: column["name"],
                get_sql_type=lambda column: column.get("type", ""),
            )
            if col_types:
                type_info[table_name] = col_types

        try:
            from benchbox.core.tpch.schema import TABLES

            for table in TABLES:
                if table.name.lower() not in type_info:
                    col_types = self._extract_arrow_types(
                        table.columns,
                        get_name=lambda column: column.name,
                        get_sql_type=lambda column: column.data_type.value,
                    )
                    if col_types:
                        type_info[table.name.lower()] = col_types
        except ImportError:
            pass

        return type_info

    def _extract_arrow_types(self, columns: Any, get_name, get_sql_type) -> dict[str, str]:
        col_types: dict[str, str] = {}
        for column in columns:
            arrow_type = SchemaMapper.sql_type_to_pyarrow(str(get_sql_type(column)))
            if arrow_type:
                col_types[get_name(column)] = arrow_type
        return col_types


def get_tpch_column_names() -> dict[str, list[str]]:
    try:
        from benchbox.core.tpch.schema import TABLES

        return {table.name.lower(): [col.name for col in table.columns] for table in TABLES}
    except ImportError:
        return {
            "region": ["r_regionkey", "r_name", "r_comment"],
            "nation": ["n_nationkey", "n_name", "n_regionkey", "n_comment"],
            "supplier": ["s_suppkey", "s_name", "s_address", "s_nationkey", "s_phone", "s_acctbal", "s_comment"],
            "part": [
                "p_partkey",
                "p_name",
                "p_mfgr",
                "p_brand",
                "p_type",
                "p_size",
                "p_container",
                "p_retailprice",
                "p_comment",
            ],
            "partsupp": ["ps_partkey", "ps_suppkey", "ps_availqty", "ps_supplycost", "ps_comment"],
            "customer": [
                "c_custkey",
                "c_name",
                "c_address",
                "c_nationkey",
                "c_phone",
                "c_acctbal",
                "c_mktsegment",
                "c_comment",
            ],
            "orders": [
                "o_orderkey",
                "o_custkey",
                "o_orderstatus",
                "o_totalprice",
                "o_orderdate",
                "o_orderpriority",
                "o_clerk",
                "o_shippriority",
                "o_comment",
            ],
            "lineitem": [
                "l_orderkey",
                "l_partkey",
                "l_suppkey",
                "l_linenumber",
                "l_quantity",
                "l_extendedprice",
                "l_discount",
                "l_tax",
                "l_returnflag",
                "l_linestatus",
                "l_shipdate",
                "l_commitdate",
                "l_receiptdate",
                "l_shipinstruct",
                "l_shipmode",
                "l_comment",
            ],
        }


def get_tpcds_column_names() -> dict[str, list[str]]:
    try:
        from benchbox.core.tpcds.schema import TABLES

        return {table.name.lower(): [col.name for col in table.columns] for table in TABLES}
    except ImportError:
        return {}
