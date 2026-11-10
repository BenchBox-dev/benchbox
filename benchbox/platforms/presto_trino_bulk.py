from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.platforms.presto_trino_utils import (
    default_dialect,
    has_structured_type,
)
from benchbox.utils.file_format import get_data_extension

if TYPE_CHECKING:
    from benchbox.platforms.base.data_loading import CsvDialect

STAGE_DIR_NAME = "benchbox_bulk_stage"
STAGE_SCHEMA = "benchbox_stage"
_HIVE_BASE_TYPES = frozenset(
    {
        "varchar",
        "char",
        "tinyint",
        "smallint",
        "integer",
        "bigint",
        "decimal",
        "double",
        "real",
        "date",
        "timestamp",
        "varbinary",
    }
)


@dataclass(frozen=True)
class BulkLoadSettings:
    source_catalog: str
    native_compression: frozenset[str]
    null_format_property: str | None
    field_delimiter_property: str = "textfile_field_separator"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def ineligibility_reason(
    columns: Sequence[tuple[str, str]],
    files: Sequence[Path],
    dialect: CsvDialect | None,
    settings: BulkLoadSettings | None = None,
) -> str | None:
    is_parquet = get_data_extension(files[0]) == ".parquet"
    for _, type_name in columns:
        lowered = type_name.strip().lower()
        base = lowered.split("(", 1)[0].strip()
        if has_structured_type([type_name]) and not is_parquet:
            return f"column type {type_name} has no delimited-text representation"
        if base == "varbinary" and not is_parquet:
            return f"column type {type_name} is decoded as base64 from Hive text but as hex by the VALUES loader"
        if "with time zone" in lowered or (base not in _HIVE_BASE_TYPES and not has_structured_type([type_name])):
            return f"column type {type_name} cannot back an external Hive table"
    if is_parquet:
        return _parquet_reason(columns, files)
    resolved = dialect or default_dialect(files[0])
    if resolved.quote:
        return "quoted CSV fields cannot be read by a delimited-text external table"
    if len(resolved.delimiter) != 1:
        return f"delimiter {resolved.delimiter!r} is not a single character"
    if resolved.encoding.lower().replace("_", "-") not in {"utf-8", "utf8"}:
        return f"encoding {resolved.encoding} is not UTF-8"
    if (
        settings is not None
        and resolved.null_marker is not None
        and resolved.null_marker != "\\N"
        and not settings.null_format_property
    ):
        return "source catalog cannot configure a custom null format for delimited text"
    return None


def _parquet_reason(columns: Sequence[tuple[str, str]], files: Sequence[Path]) -> str | None:
    import pyarrow.parquet as pq

    expected = [name.lower() for name, _ in columns]
    for file_path in files:
        names = [name.lower() for name in pq.ParquetFile(file_path).schema_arrow.names]
        if names != expected:
            return f"{file_path.name} columns {names} do not match table columns {expected}"
    return None


def _link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def stage_files(files: Sequence[Path], stage_dir: Path, native_compression: frozenset[str]) -> list[Path]:
    from benchbox.platforms.base.data_loading import FileFormatRegistry, NoCompressionHandler, _file_byte_chunks

    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    staged: list[Path] = []
    for idx, source in enumerate(files):
        handler = FileFormatRegistry.get_compression_handler(source)
        if isinstance(handler, NoCompressionHandler) or source.suffix.lower() in native_compression:
            destination = stage_dir / f"{idx}_{source.name}"
            _link_or_copy(source, destination)
        else:
            destination = stage_dir / f"{idx}_{source.with_suffix('').name}"
            with destination.open("wb") as sink:
                for chunk in _file_byte_chunks(source):
                    sink.write(chunk)
        staged.append(destination)
    return staged


def _table_properties(
    stage_dir: Path, files: Sequence[Path], dialect: CsvDialect | None, settings: BulkLoadSettings
) -> str:
    properties = [f"external_location = 'file://{str(stage_dir).replace(chr(39), chr(39) * 2)}'"]
    if get_data_extension(files[0]) == ".parquet":
        properties.append("format = 'PARQUET'")
    else:
        resolved = dialect or default_dialect(files[0])
        properties.append("format = 'TEXTFILE'")
        properties.append(f"{settings.field_delimiter_property} = '{resolved.delimiter.replace(chr(39), chr(39) * 2)}'")
        if resolved.has_header:
            properties.append("skip_header_line_count = 1")
        if resolved.null_marker is not None and settings.null_format_property:
            marker = resolved.null_marker.replace("'", "''")
            properties.append(f"{settings.null_format_property} = '{marker}'")
    return ", ".join(properties)


def _run(cursor: Any, sql: str) -> list[Any]:
    cursor.execute(sql)
    return list(cursor.fetchall())


def remove_stage_root(data_dir: Path) -> None:
    with contextlib.suppress(OSError):
        (data_dir / STAGE_DIR_NAME).rmdir()


def source_row_count(staged: Sequence[Path], dialect: CsvDialect | None) -> int:
    if get_data_extension(staged[0]) == ".parquet":
        import pyarrow.parquet as pq

        return sum(pq.ParquetFile(path).metadata.num_rows for path in staged)
    from benchbox.platforms.base.data_loading import _file_byte_chunks

    has_header = (dialect or default_dialect(staged[0])).has_header
    total = 0
    for path in staged:
        lines = 0
        last = b""
        for chunk in _file_byte_chunks(path):
            lines += chunk.count(b"\n")
            last = chunk[-1:]
        if last not in (b"", b"\n"):
            lines += 1
        total += max(0, lines - 1) if has_header else lines
    return total


def bulk_load_table(
    cursor: Any,
    *,
    settings: BulkLoadSettings,
    target_table: str,
    ext_name: str,
    columns: Sequence[tuple[str, str]],
    files: Sequence[Path],
    dialect: CsvDialect | None,
    logger: Logger,
) -> int | None:
    reason = ineligibility_reason(columns, files, dialect, settings=settings)
    if reason is not None:
        logger.warning(f"External-table bulk load skipped for {target_table}: {reason}")
        return None

    stage_dir = files[0].parent / STAGE_DIR_NAME / ext_name
    qualified_ext = f"{settings.source_catalog}.{STAGE_SCHEMA}.{ext_name}"
    staged = stage_files(files, stage_dir, settings.native_compression)
    try:
        _run(cursor, f"CREATE SCHEMA IF NOT EXISTS {settings.source_catalog}.{STAGE_SCHEMA}")
        _run(cursor, f"DROP TABLE IF EXISTS {qualified_ext}")
        column_sql = ", ".join(f"{_quote(name)} {type_name}" for name, type_name in columns)
        _run(
            cursor,
            f"CREATE TABLE {qualified_ext} ({column_sql}) WITH ({_table_properties(stage_dir, staged, dialect, settings)})",
        )
        expected = source_row_count(staged, dialect)
        visible = int(_run(cursor, f"SELECT count(*) FROM {qualified_ext}")[0][0])
        if visible != expected:
            raise RuntimeError(
                f"{settings.source_catalog} reads {visible:,} row(s) from {stage_dir} but the staged files hold "
                f"{expected:,}. The server must read the BenchBox data directory at the same absolute path (mount "
                "it into the server container) and the table's file format must match the staged files."
            )
        inserted = int(_run(cursor, f"INSERT INTO {target_table} SELECT * FROM {qualified_ext}")[0][0])
        if inserted != expected:
            raise RuntimeError(f"inserted {inserted:,} row(s) into {target_table}, the staged files hold {expected:,}")
        return inserted
    finally:
        try:
            _run(cursor, f"DROP TABLE IF EXISTS {qualified_ext}")
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)
