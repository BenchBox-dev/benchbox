from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from benchbox.core.data_fetch.logical_hash import (
    LOGICAL_CONTENT_VERSION,
    LogicalColumn,
    LogicalTableHash,
    aggregate_logical_content_hash,
    is_integer_postgres_type,
    logical_table_hash_from_parquet as _shared_logical_table_hash_from_parquet,
    update_logical_row_hash,
    update_sized_hash_part,
)
from benchbox.core.data_fetch.manifest import TableEntry, compute_manifest_identity_hash

DATASET_VERSION = "joinorder-imdb-2013-v1"
SOURCE_DOI = "10.7910/DVN/2QYZBT"
SOURCE_PERSISTENT_ID = f"doi:{SOURCE_DOI}"
DATAVERSE_DATASET_API = f"https://dataverse.harvard.edu/api/datasets/:persistentId?persistentId={SOURCE_PERSISTENT_ID}"
DATAVERSE_FILE_ACCESS_TEMPLATE = "https://dataverse.harvard.edu/api/access/datafile/{file_id}"
DEFAULT_POSTGRES_IMAGE = "postgres:16.2"
DEFAULT_POSTGRES_DB = "imdb"
DEFAULT_POSTGRES_USER = "postgres"
GITHUB_REPO = "joeharris76/BenchBox"
GITHUB_RELEASE_TAG = f"data/{DATASET_VERSION}"
ARCHIVE_NAME = f"{DATASET_VERSION}.tar.zst"
ARCHIVE_SHA_NAME = f"{ARCHIVE_NAME}.sha256"
GREGRAHN_REPO = "https://github.com/gregrahn/join-order-benchmark.git"
GREGRAHN_COMMIT = "a39603662e023e449cb2121997a5034df9e02ebf"
EXPECTED_QUERY_COUNT = 113
BUILD_MANIFEST_NAME = "build_manifest.json"
PGDUMP_MAGIC = b"PGDMP"
ROW_COUNT_TOLERANCE = 0.01
CSV_NULL = r"\N"

QUERY_ID_RE = re.compile(r"^\d+[a-z]$")

STRING_SAMPLE_COLUMNS: dict[str, str] = {
    "aka_name": "name",
    "name": "name",
    "aka_title": "title",
    "title": "title",
    "cast_info": "note",
    "movie_companies": "note",
}

UTF8_FIDELITY_SAMPLE_COLUMNS: dict[str, str] = {
    "aka_title": "title",
    "char_name": "name",
    "name": "name",
    "title": "title",
}

DUCKDB_INTEGER_RANGES: dict[str, tuple[int, int]] = {
    "TINYINT": (-(2**7), 2**7 - 1),
    "SMALLINT": (-(2**15), 2**15 - 1),
    "INTEGER": (-(2**31), 2**31 - 1),
    "BIGINT": (-(2**63), 2**63 - 1),
    "HUGEINT": (-(2**127), 2**127 - 1),
    "UTINYINT": (0, 2**8 - 1),
    "USMALLINT": (0, 2**16 - 1),
    "UINTEGER": (0, 2**32 - 1),
    "UBIGINT": (0, 2**64 - 1),
}

KNOWN_ZERO_UNDERLYING_QUERIES = frozenset({"2c", "5a", "5b", "10b", "32a"})

CANONICAL_NULL_COUNTS: dict[tuple[str, str], tuple[int, int]] = {
    ("movie_companies", "note"): (1_271_989, 2_609_129),
    ("cast_info", "note"): (22_007_252, 36_244_344),
    ("title", "episode_of_id"): (985_048, 2_528_312),
}

EXPECTED_ROW_COUNTS: dict[str, int] = {
    "aka_name": 901_343,
    "aka_title": 361_472,
    "cast_info": 36_244_344,
    "char_name": 3_140_339,
    "comp_cast_type": 4,
    "company_name": 234_997,
    "company_type": 4,
    "complete_cast": 135_086,
    "info_type": 113,
    "keyword": 134_170,
    "kind_type": 7,
    "link_type": 18,
    "movie_companies": 2_609_129,
    "movie_info": 14_835_720,
    "movie_info_idx": 1_380_035,
    "movie_keyword": 4_523_930,
    "movie_link": 29_997,
    "name": 4_167_491,
    "person_info": 2_963_664,
    "role_type": 12,
    "title": 2_528_312,
}

TABLE_NAMES = tuple(sorted(EXPECTED_ROW_COUNTS))


CLI_DESCRIPTION = (
    "Build canonical JoinOrder IMDb data artifacts.\n"
    "\n"
    "This script is intentionally outside the BenchBox runtime package. It owns the\n"
    "network, Docker, and PostgreSQL tooling needed to turn the upstream Harvard\n"
    "Dataverse pg_dump into build artifacts consumed by the cutover TODO.\n"
    "\n"
    "Foundation scope:\n"
    "  - resolve and download the Harvard Dataverse pg_dump for doi:10.7910/DVN/2QYZBT\n"
    "  - compute and record the upstream sha256 in a local build manifest\n"
    "  - restore the custom-format pg_dump into a pinned PostgreSQL container\n"
    "  - validate expected JOB tables and row counts within the TODO's +/-1% gate\n"
    "  - extract CSV with explicit UTF-8/NULL semantics\n"
    "  - convert to zstd Parquet, assemble manifests, gates, cardinalities, archive\n"
    "  - stage the draft data release and emit the runtime data_manifest.toml\n"
)


class JoinOrderBuildError(RuntimeError):
    pass


class DataverseMetadataError(JoinOrderBuildError):
    pass


class DownloadIntegrityError(JoinOrderBuildError):
    pass


class DockerUnavailableError(JoinOrderBuildError):
    pass


class QueryImportError(JoinOrderBuildError):
    pass


@dataclasses.dataclass(frozen=True)
class DataverseFile:
    file_id: int
    label: str
    filesize: int
    checksum_type: str
    checksum_value: str
    download_url: str


@dataclasses.dataclass(frozen=True)
class PgDumpArtifact:
    path: Path
    size: int
    sha256: str
    md5: str
    dataverse_file: DataverseFile


@dataclasses.dataclass(frozen=True)
class RowCountFailure:
    table: str
    expected: int
    actual: int
    allowed_delta: int


@dataclasses.dataclass(frozen=True)
class RestoreValidation:
    row_counts: dict[str, int]
    missing_tables: list[str]
    unexpected_tables: list[str]
    row_count_failures: list[RowCountFailure]

    @property
    def ok(self) -> bool:
        return not self.missing_tables and not self.row_count_failures


@dataclasses.dataclass(frozen=True)
class ColumnSchema:
    table: str
    name: str
    postgres_type: str
    udt_name: str
    ordinal_position: int
    is_nullable: bool

    @property
    def duckdb_type(self) -> str:
        return postgres_type_to_duckdb(self.postgres_type, self.udt_name)


@dataclasses.dataclass(frozen=True)
class TableFile:
    table: str
    path: Path
    sha256: str
    bytes: int
    row_count: int


@dataclasses.dataclass(frozen=True)
class CsvLogicalValue:
    value: str
    quoted: bool


@dataclasses.dataclass(frozen=True)
class LogicalContentReport:
    aggregate_hash: str
    tables: list[dict[str, Any]]
    failures: list[str]


@dataclasses.dataclass(frozen=True)
class QueryParts:
    query_id: str
    select_part: str
    from_part: str
    where_part: str


@dataclasses.dataclass(frozen=True)
class QueryAlias:
    table: str
    alias: str


@dataclasses.dataclass(frozen=True)
class ForeignKey:
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str


def utc_now_iso() -> str:

    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_build_root() -> Path:

    output_root = os.environ.get("BENCHBOX_OUTPUT_DIR")
    base = Path(output_root).expanduser() if output_root else Path("~/Developer/benchmark_runs").expanduser()
    return base / "joinorder" / "build" / DATASET_VERSION


def work_dir_from_arg(raw: str | None) -> Path:
    return Path(raw).expanduser().resolve() if raw else default_build_root().resolve()


def build_manifest_path(work_dir: Path) -> Path:
    return work_dir / BUILD_MANIFEST_NAME


def load_build_manifest(work_dir: Path) -> dict[str, Any]:
    path = build_manifest_path(work_dir)
    if not path.exists():
        return {
            "dataset_version": DATASET_VERSION,
            "source_doi": SOURCE_DOI,
            "created_at": utc_now_iso(),
        }
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise JoinOrderBuildError(f"{path} must contain a JSON object")
    return data


def write_build_manifest(work_dir: Path, updates: Mapping[str, Any]) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    data = load_build_manifest(work_dir)
    data.update(updates)
    data["updated_at"] = utc_now_iso()

    path = build_manifest_path(work_dir)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)
    return path


def _load_json_url(url: str, *, timeout: float = 60.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "BenchBox-JoinOrder-Builder/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise DataverseMetadataError(f"Unable to read Dataverse metadata from {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DataverseMetadataError(f"Dataverse metadata from {url} must be a JSON object")
    return payload


def select_pgdump_file(metadata: Mapping[str, Any]) -> DataverseFile:

    try:
        files = metadata["data"]["latestVersion"]["files"]
    except KeyError as exc:
        raise DataverseMetadataError("Dataverse response is missing data.latestVersion.files") from exc
    if not isinstance(files, list):
        raise DataverseMetadataError("Dataverse response field data.latestVersion.files must be a list")

    candidates: list[DataverseFile] = []
    for entry in files:
        if not isinstance(entry, Mapping):
            continue
        data_file = entry.get("dataFile")
        if not isinstance(data_file, Mapping):
            continue
        label = str(entry.get("label") or data_file.get("filename") or "")
        checksum = data_file.get("checksum")
        if not label or not isinstance(checksum, Mapping):
            continue
        if label != "imdb_pg11":
            continue
        file_id = data_file.get("id")
        filesize = data_file.get("filesize")
        checksum_type = checksum.get("type")
        checksum_value = checksum.get("value")
        if not isinstance(file_id, int) or not isinstance(filesize, int):
            raise DataverseMetadataError(f"Dataverse file {label!r} is missing integer id/filesize")
        if not isinstance(checksum_type, str) or not isinstance(checksum_value, str):
            raise DataverseMetadataError(f"Dataverse file {label!r} is missing checksum metadata")
        candidates.append(
            DataverseFile(
                file_id=file_id,
                label=label,
                filesize=filesize,
                checksum_type=checksum_type.upper(),
                checksum_value=checksum_value.lower(),
                download_url=DATAVERSE_FILE_ACCESS_TEMPLATE.format(file_id=file_id),
            )
        )

    if len(candidates) != 1:
        raise DataverseMetadataError(
            f"Expected exactly one Dataverse pg_dump file named imdb_pg11; found {len(candidates)}"
        )
    return candidates[0]


def resolve_dataverse_file() -> DataverseFile:
    metadata = _load_json_url(DATAVERSE_DATASET_API)
    return select_pgdump_file(metadata)


def hash_file(path: Path) -> tuple[str, str, int]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
            md5.update(chunk)
            size += len(chunk)
    return sha256.hexdigest(), md5.hexdigest(), size


def sha256_file(path: Path) -> str:
    return hash_file(path)[0]


def repo_root() -> Path:
    result = run_command(["git", "rev-parse", "--show-toplevel"])
    return Path(result.stdout.strip()).resolve()


def git_head_sha() -> str:
    result = run_command(["git", "rev-parse", "HEAD"])
    return result.stdout.strip()


def github_release_asset_url() -> str:
    encoded_tag = urllib.parse.quote(GITHUB_RELEASE_TAG, safe="")
    return f"https://github.com/{GITHUB_REPO}/releases/download/{encoded_tag}/{ARCHIVE_NAME}"


def manifest_hash_input(raw: bytes) -> bytes:

    excluded_top_level_keys = {b"archive_sha256", b"manifest_hash"}
    lines: list[bytes] = []
    in_top_level = True
    for line in raw.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(b"["):
            in_top_level = False
        if in_top_level:
            before_equals = stripped.split(b"=", 1)[0].strip()
            if before_equals in excluded_top_level_keys:
                continue
        lines.append(line)
    return b"".join(lines)


def compute_manifest_hash(path: Path) -> str:
    return hashlib.sha256(manifest_hash_input(path.read_bytes())).hexdigest()


def rendered_manifest_identity(manifest_path: Path) -> tuple[str, str]:
    raw = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    return str(raw["manifest_hash"]), str(raw["data_archive_hash"])


def aggregate_table_hash(table_files: Sequence[TableFile]) -> str:

    payload = "\n".join(f"{entry.table}:{entry.sha256}" for entry in sorted(table_files, key=lambda f: f.table))
    return hashlib.sha256((payload + "\n").encode("utf-8")).hexdigest()


def _logical_columns(columns: Sequence[ColumnSchema]) -> list[LogicalColumn]:
    return [
        LogicalColumn(name=column.name, is_integer=is_integer_postgres_type(column.postgres_type)) for column in columns
    ]


def _csv_value_to_plain(value: CsvLogicalValue) -> str | None:
    if value.value == CSV_NULL and not value.quoted:
        return None
    return value.value


def parse_postgres_csv_record(record: str) -> list[CsvLogicalValue]:

    fields: list[CsvLogicalValue] = []
    field: list[str] = []
    quoted = False
    in_quotes = False
    at_field_start = True
    index = 0
    while index < len(record):
        char = record[index]
        if in_quotes:
            if char == '"':
                if index + 1 < len(record) and record[index + 1] == '"':
                    field.append('"')
                    index += 2
                    continue
                in_quotes = False
            else:
                field.append(char)
            index += 1
            continue

        if at_field_start and char == '"':
            quoted = True
            in_quotes = True
            at_field_start = False
        elif char == ",":
            fields.append(CsvLogicalValue("".join(field), quoted))
            field = []
            quoted = False
            at_field_start = True
        else:
            field.append(char)
            at_field_start = False
        index += 1

    if in_quotes:
        raise JoinOrderBuildError("CSV record ended inside a quoted field")
    fields.append(CsvLogicalValue("".join(field), quoted))
    return fields


def iter_postgres_csv_records(path: Path) -> Iterator[list[CsvLogicalValue]]:

    record_parts: list[str] = []
    in_quotes = False
    at_field_start = True
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line in handle:
            record_parts.append(line)
            index = 0
            while index < len(line):
                char = line[index]
                if in_quotes:
                    if char == '"':
                        if index + 1 < len(line) and line[index + 1] == '"':
                            index += 2
                            continue
                        in_quotes = False
                    index += 1
                    continue
                if at_field_start and char == '"':
                    in_quotes = True
                    at_field_start = False
                elif char == ",":
                    at_field_start = True
                elif char not in "\r\n":
                    at_field_start = False
                index += 1
            if not in_quotes:
                record = "".join(record_parts)
                if record.endswith("\n"):
                    record = record[:-1]
                if record.endswith("\r"):
                    record = record[:-1]
                record_parts = []
                at_field_start = True
                if record == "":
                    continue
                yield parse_postgres_csv_record(record)
        if record_parts:
            raise JoinOrderBuildError(f"CSV file {path} ended inside a quoted record")


def logical_content_report_from_hashes(
    *,
    work_dir: Path,
    table_reports: list[dict[str, Any]],
    parquet_hashes: Sequence[LogicalTableHash],
    failures: list[str],
) -> LogicalContentReport:
    aggregate_hash = aggregate_logical_content_hash(parquet_hashes)
    report = LogicalContentReport(aggregate_hash=aggregate_hash, tables=table_reports, failures=failures)
    write_build_manifest(
        work_dir,
        {
            "logical_content": {
                "verified_at": utc_now_iso(),
                "hash_version": "joinorder-logical-content-v1",
                "logical_content_rebuild": "PASS" if not failures else "FAIL",
                "aggregate_hash": aggregate_hash,
                "tables": table_reports,
                "failures": failures,
            }
        },
    )
    if failures:
        raise JoinOrderBuildError(f"Logical content validation failed with {len(failures)} issue(s): {failures[:5]}")
    return report


def logical_content_table_report(
    csv_hash: LogicalTableHash, parquet_hash: LogicalTableHash
) -> tuple[dict[str, Any], list[str]]:
    table_report = {
        "table": parquet_hash.table,
        "row_count": parquet_hash.row_count,
        "csv_row_count": csv_hash.row_count,
        "csv_logical_sha256": csv_hash.sha256,
        "parquet_logical_sha256": parquet_hash.sha256,
        "status": "PASS" if csv_hash == parquet_hash else "FAIL",
    }
    failures: list[str] = []
    if csv_hash.row_count != parquet_hash.row_count:
        failures.append(
            f"{parquet_hash.table}: row count mismatch csv={csv_hash.row_count} parquet={parquet_hash.row_count}"
        )
    if csv_hash.sha256 != parquet_hash.sha256:
        failures.append(
            f"{parquet_hash.table}: logical content hash mismatch csv={csv_hash.sha256} parquet={parquet_hash.sha256}"
        )
    return table_report, failures


def logical_table_hash_from_csv(csv_path: Path, columns: Sequence[ColumnSchema]) -> LogicalTableHash:

    logical_columns = _logical_columns(columns)
    hasher = hashlib.sha256()
    update_sized_hash_part(hasher, "V", LOGICAL_CONTENT_VERSION.encode("ascii"))
    update_sized_hash_part(hasher, "T", columns[0].table.encode("utf-8"))
    column_names = [column.name for column in columns]
    last_id: int | None = None
    row_count = 0
    records = iter_postgres_csv_records(csv_path)
    try:
        header = next(records)
    except StopIteration as exc:
        raise JoinOrderBuildError(f"CSV file {csv_path} is empty") from exc
    fieldnames = [field.value for field in header]
    if fieldnames != column_names:
        raise JoinOrderBuildError(f"CSV header mismatch for {csv_path}: expected {column_names}, got {fieldnames}")
    for row in records:
        if len(row) != len(columns):
            raise JoinOrderBuildError(
                f"CSV row for {columns[0].table} has {len(row)} values for {len(columns)} columns"
            )
        row_values = dict(zip(column_names, row, strict=True))
        row_id = int(row_values["id"].value)
        if last_id is not None and row_id <= last_id:
            raise JoinOrderBuildError(f"CSV rows for {columns[0].table} are not strictly ordered by id")
        last_id = row_id
        update_logical_row_hash(
            hasher, logical_columns, [_csv_value_to_plain(row_values[column.name]) for column in columns]
        )
        row_count += 1
    return LogicalTableHash(table=columns[0].table, row_count=row_count, sha256=hasher.hexdigest())


def logical_table_hash_from_parquet(
    *,
    con: Any,
    parquet_path: Path,
    table: str,
    columns: Sequence[ColumnSchema],
    batch_size: int = 100_000,
) -> LogicalTableHash:

    return _shared_logical_table_hash_from_parquet(
        con=con,
        parquet_path=parquet_path,
        table=table,
        columns=_logical_columns(columns),
        batch_size=batch_size,
    )


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def duckdb_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def postgres_type_to_duckdb(postgres_type: str, udt_name: str) -> str:
    normalized = postgres_type.lower()
    udt = udt_name.lower()
    if normalized in {"smallint", "integer", "bigint"} or udt in {"int2", "int4", "int8"}:
        return "BIGINT"
    if normalized in {"real", "double precision"} or udt in {"float4", "float8"}:
        return "DOUBLE"
    if normalized == "numeric":
        return "DOUBLE"
    if normalized in {"boolean"} or udt == "bool":
        return "BOOLEAN"
    if normalized == "date":
        return "DATE"
    if "timestamp" in normalized:
        return "TIMESTAMP"
    return "VARCHAR"


def is_postgres_string_column(column: ColumnSchema) -> bool:
    normalized = column.postgres_type.lower()
    udt = column.udt_name.lower()
    return normalized in {"character varying", "character", "text"} or udt in {"varchar", "bpchar", "text"}


def is_postgres_integer_column(column: ColumnSchema) -> bool:
    normalized = column.postgres_type.lower()
    udt = column.udt_name.lower()
    return normalized in {"smallint", "integer", "bigint"} or udt in {"int2", "int4", "int8"}


def duckdb_integer_type_can_store_range(duckdb_type: str, min_value: int | None, max_value: int | None) -> bool:
    if min_value is None or max_value is None:
        return True
    normalized = duckdb_type.upper().split("(", 1)[0].strip()
    allowed = DUCKDB_INTEGER_RANGES.get(normalized)
    if allowed is None:
        return False
    return allowed[0] <= min_value and max_value <= allowed[1]


def format_toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return format_toml_string(str(value))


def validate_pgdump_file(path: Path, dataverse_file: DataverseFile) -> PgDumpArtifact:
    if not path.exists():
        raise JoinOrderBuildError(f"pg_dump file does not exist: {path}")
    sha256, md5, size = hash_file(path)
    if size != dataverse_file.filesize:
        raise DownloadIntegrityError(
            f"Downloaded pg_dump size mismatch: expected {dataverse_file.filesize} bytes, got {size}"
        )
    if dataverse_file.checksum_type == "MD5" and md5 != dataverse_file.checksum_value:
        raise DownloadIntegrityError(
            f"Downloaded pg_dump MD5 mismatch: expected {dataverse_file.checksum_value}, got {md5}"
        )
    with path.open("rb") as handle:
        if handle.read(len(PGDUMP_MAGIC)) != PGDUMP_MAGIC:
            raise DownloadIntegrityError(f"{path} is not a PostgreSQL custom-format pg_dump")
    return PgDumpArtifact(path=path, size=size, sha256=sha256, md5=md5, dataverse_file=dataverse_file)


def download_pgdump(work_dir: Path, *, force: bool = False) -> PgDumpArtifact:

    dataverse_file = resolve_dataverse_file()
    source_dir = work_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    destination = source_dir / dataverse_file.label
    tmp_path = destination.with_name(destination.name + ".part")

    if destination.exists() and not force:
        try:
            artifact = validate_pgdump_file(destination, dataverse_file)
        except DownloadIntegrityError:
            if tmp_path.exists():
                tmp_path.unlink()
            destination.replace(tmp_path)
        else:
            write_source_manifest(work_dir, artifact, reused=True)
            return artifact
    elif force and tmp_path.exists():
        tmp_path.unlink()

    print(
        f"Downloading Dataverse file {dataverse_file.file_id} to {tmp_path} ({dataverse_file.filesize} bytes expected)",
        flush=True,
    )
    download_to_file(dataverse_file.download_url, tmp_path)
    artifact = validate_pgdump_file(tmp_path, dataverse_file)
    tmp_path.replace(destination)
    artifact = dataclasses.replace(artifact, path=destination)
    write_source_manifest(work_dir, artifact, reused=False)
    return artifact


def download_to_file(url: str, destination: Path) -> None:
    if shutil.which("curl"):
        stream_command(
            [
                "curl",
                "--fail",
                "--location",
                "--show-error",
                "--retry",
                "5",
                "--retry-delay",
                "5",
                "--retry-all-errors",
                "--connect-timeout",
                "30",
                "--continue-at",
                "-",
                "--output",
                str(destination),
                url,
            ]
        )
        return

    existing_size = destination.stat().st_size if destination.exists() else 0
    headers = {"User-Agent": "BenchBox-JoinOrder-Builder/1.0"}
    if existing_size:
        headers["Range"] = f"bytes={existing_size}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            mode = "ab" if existing_size and getattr(response, "status", None) == 206 else "wb"
            with destination.open(mode) as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise JoinOrderBuildError(f"Unable to download Dataverse pg_dump: {exc}") from exc


def write_source_manifest(work_dir: Path, artifact: PgDumpArtifact, *, reused: bool) -> Path:
    return write_build_manifest(
        work_dir,
        {
            "source": {
                "doi": SOURCE_DOI,
                "persistent_id": SOURCE_PERSISTENT_ID,
                "metadata_url": DATAVERSE_DATASET_API,
                "download_url": artifact.dataverse_file.download_url,
                "dataverse_file_id": artifact.dataverse_file.file_id,
                "label": artifact.dataverse_file.label,
                "bytes": artifact.size,
                "dataverse_checksum_type": artifact.dataverse_file.checksum_type,
                "dataverse_checksum": artifact.dataverse_file.checksum_value,
                "md5": artifact.md5,
                "sha256": artifact.sha256,
                "path": str(artifact.path),
                "reused_existing_file": reused,
                "retrieved_at": utc_now_iso(),
            }
        },
    )


def container_cli() -> str:
    return os.environ.get("BENCHBOX_CONTAINER_CLI", "docker")


def require_docker() -> None:
    cli = container_cli()
    if shutil.which(cli) is None:
        raise DockerUnavailableError(f"Container CLI {cli!r} is not available on PATH")
    result = run_command([cli, "version", "--format", "{{.Server.Version}}"], check=False)
    if result.returncode != 0:
        raise DockerUnavailableError(
            f"Container runtime {cli!r} is not available: "
            + (result.stderr.strip() or result.stdout.strip() or f"{cli} version failed")
        )


def run_command(args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        command = " ".join(args)
        tail = (result.stderr or result.stdout).strip()
        raise JoinOrderBuildError(f"Command failed ({result.returncode}): {command}\n{tail}")
    return result


def stream_command(args: Sequence[str], *, check: bool = True, stdin: Any = None) -> subprocess.CompletedProcess[None]:
    result = subprocess.run(args, check=False, stdin=stdin)
    if check and result.returncode != 0:
        command = " ".join(args)
        raise JoinOrderBuildError(f"Command failed ({result.returncode}): {command}")
    return result


def ensure_postgres_image(image: str) -> None:
    cli = container_cli()
    if run_command([cli, "image", "inspect", image], check=False).returncode == 0:
        return
    stream_command([cli, "pull", image])


def default_container_name() -> str:
    return f"benchbox-joinorder-w4-{os.getpid()}"


def remove_container(container_name: str) -> None:
    subprocess.run(
        [container_cli(), "rm", "-f", container_name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def start_postgres_container(
    *,
    container_name: str,
    image: str,
    database: str,
    user: str,
    replace_existing: bool,
) -> None:
    if replace_existing:
        remove_container(container_name)
    stream_command(
        [
            container_cli(),
            "run",
            "--detach",
            "--name",
            container_name,
            "--shm-size",
            "1g",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "-e",
            f"POSTGRES_DB={database}",
            "-e",
            f"POSTGRES_USER={user}",
            image,
        ]
    )
    wait_for_postgres(container_name=container_name, database=database, user=user)


def wait_for_postgres(*, container_name: str, database: str, user: str, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_message = ""
    while time.monotonic() < deadline:
        result = run_command(
            [container_cli(), "exec", container_name, "pg_isready", "-U", user, "-d", database],
            check=False,
        )
        if result.returncode == 0:
            return
        last_message = result.stderr.strip() or result.stdout.strip()
        time.sleep(1)
    raise JoinOrderBuildError(f"PostgreSQL did not become ready within {timeout_seconds}s: {last_message}")


def restore_pgdump(
    artifact: PgDumpArtifact,
    *,
    work_dir: Path,
    postgres_image: str = DEFAULT_POSTGRES_IMAGE,
    container_name: str | None = None,
    database: str = DEFAULT_POSTGRES_DB,
    user: str = DEFAULT_POSTGRES_USER,
    keep_container: bool = False,
    replace_existing: bool = True,
) -> RestoreValidation:

    require_docker()
    ensure_postgres_image(postgres_image)

    actual_container_name = container_name or default_container_name()
    started = False
    try:
        start_postgres_container(
            container_name=actual_container_name,
            image=postgres_image,
            database=database,
            user=user,
            replace_existing=replace_existing,
        )
        started = True
        with artifact.path.open("rb") as dump:
            stream_command(
                [
                    container_cli(),
                    "exec",
                    "-i",
                    actual_container_name,
                    "pg_restore",
                    "-U",
                    user,
                    "-d",
                    database,
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                ],
                stdin=dump,
            )
        validation = validate_restored_database(
            container_name=actual_container_name,
            database=database,
            user=user,
        )
        write_restore_manifest(
            work_dir,
            artifact,
            validation,
            postgres_image=postgres_image,
            container_name=actual_container_name,
            database=database,
            keep_container=keep_container,
        )
        if not validation.ok:
            raise JoinOrderBuildError(format_restore_failures(validation))
        return validation
    finally:
        if started and not keep_container:
            remove_container(actual_container_name)


def write_restore_manifest(
    work_dir: Path,
    artifact: PgDumpArtifact,
    validation: RestoreValidation,
    *,
    postgres_image: str,
    container_name: str,
    database: str,
    keep_container: bool,
) -> Path:
    return write_build_manifest(
        work_dir,
        {
            "restore": {
                "postgres_image": postgres_image,
                "database": database,
                "container_name": container_name,
                "container_kept": keep_container,
                "pg_dump_sha256": artifact.sha256,
                "validated_at": utc_now_iso(),
                "row_count_tolerance": ROW_COUNT_TOLERANCE,
                "expected_table_count": len(EXPECTED_ROW_COUNTS),
                "actual_table_count": len(validation.row_counts),
                "missing_tables": validation.missing_tables,
                "unexpected_tables": validation.unexpected_tables,
                "row_count_failures": [dataclasses.asdict(failure) for failure in validation.row_count_failures],
                "row_counts": validation.row_counts,
            }
        },
    )


def validate_restored_database(*, container_name: str, database: str, user: str) -> RestoreValidation:
    table_sql = (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
    )
    result = psql(container_name=container_name, database=database, user=user, sql=table_sql)
    restored_tables = [line.strip() for line in result.splitlines() if line.strip()]
    restored_table_set = set(restored_tables)

    row_counts: dict[str, int] = {}
    missing_tables = sorted(set(EXPECTED_ROW_COUNTS) - restored_table_set)
    unexpected_tables = sorted(restored_table_set - set(EXPECTED_ROW_COUNTS))
    for table in sorted(set(EXPECTED_ROW_COUNTS) & restored_table_set):
        count_sql = f"SELECT count(*) FROM public.{quote_ident(table)}"
        count_raw = psql(container_name=container_name, database=database, user=user, sql=count_sql).strip()
        try:
            row_counts[table] = int(count_raw)
        except ValueError as exc:
            raise JoinOrderBuildError(f"Unable to parse row count for {table}: {count_raw!r}") from exc

    return validate_row_counts(row_counts, missing_tables=missing_tables, unexpected_tables=unexpected_tables)


def validate_row_counts(
    row_counts: Mapping[str, int],
    *,
    missing_tables: Sequence[str] | None = None,
    unexpected_tables: Sequence[str] | None = None,
) -> RestoreValidation:

    missing = sorted(set(missing_tables or []) | (set(EXPECTED_ROW_COUNTS) - set(row_counts)))
    failures: list[RowCountFailure] = []
    for table, expected in EXPECTED_ROW_COUNTS.items():
        if table not in row_counts:
            continue
        actual = int(row_counts[table])
        allowed_delta = math.floor(expected * ROW_COUNT_TOLERANCE)
        if abs(actual - expected) > allowed_delta:
            failures.append(
                RowCountFailure(
                    table=table,
                    expected=expected,
                    actual=actual,
                    allowed_delta=allowed_delta,
                )
            )

    return RestoreValidation(
        row_counts=dict(sorted((table, int(count)) for table, count in row_counts.items())),
        missing_tables=missing,
        unexpected_tables=sorted(unexpected_tables or []),
        row_count_failures=failures,
    )


def quote_ident(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise JoinOrderBuildError(f"Unsafe SQL identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def psql(*, container_name: str, database: str, user: str, sql: str) -> str:
    sql_to_run = f"SET max_parallel_workers_per_gather = 0; {sql}"
    result = run_command(
        [
            container_cli(),
            "exec",
            container_name,
            "psql",
            "-U",
            user,
            "-d",
            database,
            "-Atq",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql_to_run,
        ]
    )
    return result.stdout


def psql_json_rows(*, container_name: str, database: str, user: str, sql: str) -> list[dict[str, Any]]:
    wrapped = f"SELECT COALESCE(json_agg(row_to_json(_benchbox_q)), '[]'::json)::text FROM ({sql}) AS _benchbox_q"
    raw = psql(container_name=container_name, database=database, user=user, sql=wrapped).strip()
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        raise JoinOrderBuildError("PostgreSQL JSON wrapper did not return a list")
    rows: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            raise JoinOrderBuildError("PostgreSQL JSON wrapper returned a non-object row")
        rows.append(row)
    return rows


def postgres_schema(*, container_name: str, database: str, user: str) -> dict[str, list[ColumnSchema]]:
    rows = psql_json_rows(
        container_name=container_name,
        database=database,
        user=user,
        sql=(
            "SELECT table_name, column_name, data_type, udt_name, ordinal_position, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "ORDER BY table_name, ordinal_position"
        ),
    )
    schema: dict[str, list[ColumnSchema]] = {table: [] for table in TABLE_NAMES}
    for row in rows:
        table = str(row["table_name"])
        if table not in schema:
            continue
        schema[table].append(
            ColumnSchema(
                table=table,
                name=str(row["column_name"]),
                postgres_type=str(row["data_type"]),
                udt_name=str(row["udt_name"]),
                ordinal_position=int(row["ordinal_position"]),
                is_nullable=str(row["is_nullable"]).upper() == "YES",
            )
        )
    missing = [table for table, columns in schema.items() if not columns]
    if missing:
        raise JoinOrderBuildError(f"Missing PostgreSQL schema metadata for tables: {', '.join(missing)}")
    return schema


def postgres_foreign_keys(*, container_name: str, database: str, user: str) -> list[ForeignKey]:
    rows = psql_json_rows(
        container_name=container_name,
        database=database,
        user=user,
        sql=(
            "SELECT tc.table_name AS child_table, kcu.column_name AS child_column, "
            "ccu.table_name AS parent_table, ccu.column_name AS parent_column "
            "FROM information_schema.table_constraints AS tc "
            "JOIN information_schema.key_column_usage AS kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            " AND tc.table_schema = kcu.table_schema "
            "JOIN information_schema.constraint_column_usage AS ccu "
            "  ON ccu.constraint_name = tc.constraint_name "
            " AND ccu.table_schema = tc.table_schema "
            "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public' "
            "ORDER BY child_table, child_column"
        ),
    )
    keys: list[ForeignKey] = []
    for row in rows:
        child_table = str(row["child_table"])
        parent_table = str(row["parent_table"])
        if child_table in EXPECTED_ROW_COUNTS and parent_table in EXPECTED_ROW_COUNTS:
            keys.append(
                ForeignKey(
                    child_table=child_table,
                    child_column=str(row["child_column"]),
                    parent_table=parent_table,
                    parent_column=str(row["parent_column"]),
                )
            )
    return keys


def copy_table_to_csv(
    *,
    container_name: str,
    database: str,
    user: str,
    table: str,
    destination: Path,
    sql_where: str | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = f"SELECT * FROM public.{quote_ident(table)}"
    if sql_where:
        source = f"{source} WHERE {sql_where}"
    source = f"({source} ORDER BY {quote_ident('id')})"
    copy_sql = (
        f"COPY {source} TO STDOUT WITH "
        "(FORMAT csv, ENCODING 'UTF8', HEADER true, QUOTE '\"', ESCAPE '\"', FORCE_QUOTE *, NULL '\\N')"
    )
    with destination.open("wb") as handle:
        result = subprocess.run(
            [
                container_cli(),
                "exec",
                container_name,
                "psql",
                "-U",
                user,
                "-d",
                database,
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                copy_sql,
            ],
            check=False,
            stdout=handle,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        destination.unlink(missing_ok=True)
        raise JoinOrderBuildError(
            f"PostgreSQL CSV COPY failed for {table} ({result.returncode}): "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )


def find_csv_value(csv_path: Path, *, row_id: int, column: str) -> str:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("id") == str(row_id):
                return row[column]
    raise JoinOrderBuildError(f"Could not find id={row_id} in {csv_path}")


def verify_csv_utf8_fidelity(
    *,
    container_name: str,
    database: str,
    user: str,
    csv_dir: Path,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for table, column in {"name": "name", "title": "title"}.items():
        rows = psql_json_rows(
            container_name=container_name,
            database=database,
            user=user,
            sql=(
                f"SELECT id, {quote_ident(column)} AS value, "
                f"encode(convert_to({quote_ident(column)}, 'UTF8'), 'hex') AS utf8_hex "
                f"FROM public.{quote_ident(table)} "
                f"WHERE {quote_ident(column)} IS NOT NULL AND {quote_ident(column)} ~ '[^ -~]' "
                "ORDER BY id LIMIT 1"
            ),
        )
        if not rows:
            raise JoinOrderBuildError(f"No non-ASCII UTF-8 sample found in {table}.{column}")
        sample = rows[0]
        csv_value = find_csv_value(csv_dir / f"{table}.csv", row_id=int(sample["id"]), column=column)
        csv_hex = csv_value.encode("utf-8").hex()
        if csv_hex != sample["utf8_hex"]:
            raise JoinOrderBuildError(
                f"CSV UTF-8 drift for {table}.{column} id={sample['id']}: postgres {sample['utf8_hex']} csv {csv_hex}"
            )
        samples.append({"table": table, "column": column, "id": sample["id"], "utf8_hex": csv_hex})
    return samples


def verify_csv_utf8_fidelity_for_table(
    *,
    container_name: str,
    database: str,
    user: str,
    csv_path: Path,
    table: str,
    column: str,
) -> dict[str, Any]:
    rows = psql_json_rows(
        container_name=container_name,
        database=database,
        user=user,
        sql=(
            f"SELECT id, {quote_ident(column)} AS value, "
            f"encode(convert_to({quote_ident(column)}, 'UTF8'), 'hex') AS utf8_hex "
            f"FROM public.{quote_ident(table)} "
            f"WHERE {quote_ident(column)} IS NOT NULL AND {quote_ident(column)} ~ '[^ -~]' "
            "ORDER BY id LIMIT 1"
        ),
    )
    if not rows:
        raise JoinOrderBuildError(f"No non-ASCII UTF-8 sample found in {table}.{column}")
    sample = rows[0]
    csv_value = find_csv_value(csv_path, row_id=int(sample["id"]), column=column)
    csv_hex = csv_value.encode("utf-8").hex()
    if csv_hex != sample["utf8_hex"]:
        raise JoinOrderBuildError(
            f"CSV UTF-8 drift for {table}.{column} id={sample['id']}: postgres {sample['utf8_hex']} csv {csv_hex}"
        )
    return {"table": table, "column": column, "id": sample["id"], "utf8_hex": csv_hex}


def extract_csv_files(
    *,
    work_dir: Path,
    container_name: str,
    database: str,
    user: str,
) -> list[TableFile]:
    csv_dir = work_dir / "csv"
    row_counts = load_build_manifest(work_dir).get("restore", {}).get("row_counts", EXPECTED_ROW_COUNTS)
    files: list[TableFile] = []
    for table in TABLE_NAMES:
        destination = csv_dir / f"{table}.csv"
        copy_table_to_csv(
            container_name=container_name,
            database=database,
            user=user,
            table=table,
            destination=destination,
        )
        sha256, _md5, size = hash_file(destination)
        files.append(
            TableFile(
                table=table,
                path=destination,
                sha256=sha256,
                bytes=size,
                row_count=int(row_counts.get(table, EXPECTED_ROW_COUNTS[table])),
            )
        )
        print(f"csv {table}: {size} bytes sha256={sha256}", flush=True)

    utf8_samples = verify_csv_utf8_fidelity(
        container_name=container_name,
        database=database,
        user=user,
        csv_dir=csv_dir,
    )
    write_build_manifest(
        work_dir,
        {
            "csv_extract": {
                "csv_dir": str(csv_dir),
                "null_string": CSV_NULL,
                "extracted_at": utc_now_iso(),
                "files": [dataclasses.asdict(f) | {"path": str(f.path)} for f in files],
                "utf8_samples": utf8_samples,
            }
        },
    )
    return files


def duckdb_cast_expression(column: ColumnSchema) -> str:
    quoted = '"' + column.name.replace('"', '""') + '"'
    if column.duckdb_type == "VARCHAR":
        return f"NULLIF({quoted}, '{CSV_NULL}') AS {quoted}"
    return f"CAST(NULLIF({quoted}, '{CSV_NULL}') AS {column.duckdb_type}) AS {quoted}"


def convert_csv_table_to_parquet(
    *,
    con: Any,
    csv_path: Path,
    parquet_path: Path,
    table: str,
    columns: Sequence[ColumnSchema],
) -> TableFile:
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    select_list = ", ".join(duckdb_cast_expression(column) for column in columns)
    con.execute(
        f"""
        COPY (
          SELECT {select_list}
          FROM read_csv_auto(
            {duckdb_literal(csv_path)},
            header=true,
            all_varchar=true,
            sample_size=-1
          )
        )
        TO {duckdb_literal(parquet_path)}
        (FORMAT 'parquet', COMPRESSION 'zstd', ROW_GROUP_SIZE 100000)
        """
    )
    row_count = con.execute(f"SELECT count(*) FROM read_parquet({duckdb_literal(parquet_path)})").fetchone()[0]
    expected = EXPECTED_ROW_COUNTS[table]
    if int(row_count) != expected:
        raise JoinOrderBuildError(f"Parquet row count mismatch for {table}: expected {expected}, got {row_count}")
    sha256, _md5, size = hash_file(parquet_path)
    return TableFile(table=table, path=parquet_path, sha256=sha256, bytes=size, row_count=int(row_count))


def convert_csv_to_parquet(
    *,
    work_dir: Path,
    schema: Mapping[str, Sequence[ColumnSchema]],
) -> list[TableFile]:
    try:
        import duckdb
    except ImportError as exc:
        raise JoinOrderBuildError("duckdb is required in _project/scripts to convert JoinOrder CSV files") from exc

    csv_dir = work_dir / "csv"
    parquet_dir = work_dir / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    files: list[TableFile] = []
    forced_types: dict[str, dict[str, str]] = {}
    try:
        for table in TABLE_NAMES:
            csv_path = csv_dir / f"{table}.csv"
            parquet_path = parquet_dir / f"{table}.parquet"
            if not csv_path.exists():
                raise JoinOrderBuildError(f"CSV input missing for {table}: {csv_path}")
            columns = list(schema[table])
            forced_types[table] = {column.name: column.duckdb_type for column in columns}
            table_file = convert_csv_table_to_parquet(
                con=con,
                csv_path=csv_path,
                parquet_path=parquet_path,
                table=table,
                columns=columns,
            )
            files.append(table_file)
            print(
                f"parquet {table}: {table_file.row_count} rows {table_file.bytes} bytes sha256={table_file.sha256}",
                flush=True,
            )
    finally:
        con.close()

    write_build_manifest(
        work_dir,
        {
            "parquet": {
                "parquet_dir": str(parquet_dir),
                "converted_at": utc_now_iso(),
                "duckdb_version": duckdb.__version__,
                "forced_types": forced_types,
                "files": [dataclasses.asdict(f) | {"path": str(f.path)} for f in files],
            }
        },
    )
    return files


def extract_and_convert_parquet_streaming(
    *,
    work_dir: Path,
    schema: Mapping[str, Sequence[ColumnSchema]],
    container_name: str,
    database: str,
    user: str,
) -> tuple[list[TableFile], LogicalContentReport]:
    try:
        import duckdb
    except ImportError as exc:
        raise JoinOrderBuildError("duckdb is required in _project/scripts to convert JoinOrder CSV files") from exc

    csv_dir = work_dir / "csv"
    parquet_dir = work_dir / "parquet"
    row_counts = load_build_manifest(work_dir).get("restore", {}).get("row_counts", EXPECTED_ROW_COUNTS)
    csv_files: list[TableFile] = []
    parquet_files: list[TableFile] = []
    logical_table_reports: list[dict[str, Any]] = []
    logical_parquet_hashes: list[LogicalTableHash] = []
    logical_failures: list[str] = []
    utf8_samples: list[dict[str, Any]] = []
    forced_types: dict[str, dict[str, str]] = {}
    con = duckdb.connect()
    try:
        for table in TABLE_NAMES:
            csv_path = csv_dir / f"{table}.csv"
            parquet_path = parquet_dir / f"{table}.parquet"
            copy_table_to_csv(
                container_name=container_name,
                database=database,
                user=user,
                table=table,
                destination=csv_path,
            )
            csv_logical_hash = logical_table_hash_from_csv(csv_path, schema[table])
            csv_sha, _md5, csv_size = hash_file(csv_path)
            csv_files.append(
                TableFile(
                    table=table,
                    path=csv_path,
                    sha256=csv_sha,
                    bytes=csv_size,
                    row_count=int(row_counts.get(table, EXPECTED_ROW_COUNTS[table])),
                )
            )
            if table in {"name", "title"}:
                utf8_samples.append(
                    verify_csv_utf8_fidelity_for_table(
                        container_name=container_name,
                        database=database,
                        user=user,
                        csv_path=csv_path,
                        table=table,
                        column=STRING_SAMPLE_COLUMNS[table],
                    )
                )
            forced_types[table] = {column.name: column.duckdb_type for column in schema[table]}
            table_file = convert_csv_table_to_parquet(
                con=con,
                csv_path=csv_path,
                parquet_path=parquet_path,
                table=table,
                columns=schema[table],
            )
            parquet_logical_hash = logical_table_hash_from_parquet(
                con=con,
                parquet_path=parquet_path,
                table=table,
                columns=schema[table],
            )
            logical_report, table_failures = logical_content_table_report(csv_logical_hash, parquet_logical_hash)
            logical_table_reports.append(logical_report)
            logical_parquet_hashes.append(parquet_logical_hash)
            logical_failures.extend(table_failures)
            parquet_files.append(table_file)
            csv_path.unlink()
            print(
                f"streamed {table}: csv_sha256={csv_sha} parquet_sha256={table_file.sha256} rows={table_file.row_count}",
                flush=True,
            )
    finally:
        con.close()

    write_build_manifest(
        work_dir,
        {
            "csv_extract": {
                "csv_dir": str(csv_dir),
                "csv_retained": False,
                "null_string": CSV_NULL,
                "extracted_at": utc_now_iso(),
                "files": [dataclasses.asdict(f) | {"path": str(f.path)} for f in csv_files],
                "utf8_samples": utf8_samples,
            },
            "parquet": {
                "parquet_dir": str(parquet_dir),
                "converted_at": utc_now_iso(),
                "duckdb_version": duckdb.__version__,
                "forced_types": forced_types,
                "files": [dataclasses.asdict(f) | {"path": str(f.path)} for f in parquet_files],
            },
        },
    )
    logical_content_report = logical_content_report_from_hashes(
        work_dir=work_dir,
        table_reports=logical_table_reports,
        parquet_hashes=logical_parquet_hashes,
        failures=logical_failures,
    )
    return parquet_files, logical_content_report


def load_table_files_from_manifest(work_dir: Path, key: str) -> list[TableFile]:
    section = load_build_manifest(work_dir).get(key, {})
    raw_files = section.get("files", [])
    files: list[TableFile] = []
    for entry in raw_files:
        if not isinstance(entry, Mapping):
            continue
        files.append(
            TableFile(
                table=str(entry["table"]),
                path=Path(str(entry["path"])),
                sha256=str(entry["sha256"]),
                bytes=int(entry["bytes"]),
                row_count=int(entry["row_count"]),
            )
        )
    if len(files) != len(TABLE_NAMES):
        raise JoinOrderBuildError(f"Manifest section {key!r} does not contain {len(TABLE_NAMES)} table files")
    return sorted(files, key=lambda item: item.table)


def render_data_manifest(
    *,
    work_dir: Path,
    schema: Mapping[str, Sequence[ColumnSchema]],
    table_files: Sequence[TableFile],
    url: str,
    archive_sha256: str,
    output_path: Path,
) -> Path:
    manifest = load_build_manifest(work_dir)
    source = manifest.get("source", {})
    restore = manifest.get("restore", {})
    parquet = manifest.get("parquet", {})
    query_import = manifest.get("query_import", {})
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logical_content = manifest.get("logical_content", {})
    logical_by_table = {t["table"]: t["parquet_logical_sha256"] for t in logical_content.get("tables", [])}
    logical_aggregate = logical_content.get("aggregate_hash")
    ordered_files = sorted(table_files, key=lambda f: f.table)
    logical_mode = (
        logical_content.get("logical_content_rebuild") == "PASS"
        and bool(logical_aggregate)
        and all(tf.table in logical_by_table for tf in ordered_files)
    )

    if logical_mode:
        data_archive_hash = str(logical_aggregate)
        entries = [
            TableEntry(
                name=tf.table,
                file=tf.path.name,
                sha256=tf.sha256,
                row_count=tf.row_count,
                logical_sha256=str(logical_by_table[tf.table]),
                schema={column.name: column.postgres_type for column in schema[tf.table]},
            )
            for tf in ordered_files
        ]
        manifest_hash = compute_manifest_identity_hash(
            dataset_version=DATASET_VERSION,
            data_archive_hash=data_archive_hash,
            url=url,
            license_file="DATA-LICENSE.md",
            tables=entries,
        )
    else:
        data_archive_hash = aggregate_table_hash(table_files)
        manifest_hash = "0"

    lines: list[str] = [
        f"dataset_version = {format_toml_string(DATASET_VERSION)}",
        f"manifest_hash = {format_toml_string(manifest_hash)}",
        f"data_archive_hash = {format_toml_string(data_archive_hash)}",
        f"url = {format_toml_string(url)}",
        f"archive_sha256 = {format_toml_string(archive_sha256)}",
        'license_file = "DATA-LICENSE.md"',
        "",
    ]
    for table_file in ordered_files:
        lines.extend(
            [
                "[[tables]]",
                f"name = {format_toml_string(table_file.table)}",
                f"file = {format_toml_string(table_file.path.name)}",
                f"sha256 = {format_toml_string(table_file.sha256)}",
            ]
        )
        if logical_mode:
            lines.append(f"logical_sha256 = {format_toml_string(str(logical_by_table[table_file.table]))}")
        lines.append(f"row_count = {table_file.row_count}")
        for column in schema[table_file.table]:
            lines.append(f"schema.{column.name} = {format_toml_string(column.postgres_type)}")
        lines.append("")

    provenance = {
        "source_doi": SOURCE_DOI,
        "retrieval_timestamp": source.get("retrieved_at", ""),
        "pg_dump_sha256": source.get("sha256", restore.get("pg_dump_sha256", "")),
        "postgres_image": restore.get("postgres_image", DEFAULT_POSTGRES_IMAGE),
        "duckdb_version": parquet.get("duckdb_version", ""),
        "gregrahn_commit": query_import.get("gregrahn_commit", GREGRAHN_COMMIT),
        "script_git_sha": git_head_sha(),
    }
    lines.append("[provenance]")
    for key, value in provenance.items():
        lines.append(f"{key} = {format_toml_value(value)}")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    if not logical_mode:
        digest = compute_manifest_hash(output_path)
        text = output_path.read_text(encoding="utf-8")
        output_path.write_text(text.replace('manifest_hash = "0"', f'manifest_hash = "{digest}"'), encoding="utf-8")
    return output_path


def assemble_manifest(
    *,
    work_dir: Path,
    schema: Mapping[str, Sequence[ColumnSchema]],
    table_files: Sequence[TableFile],
    archive_sha256: str = "",
) -> Path:
    manifest_path = work_dir / "manifest" / "data_manifest.toml"
    render_data_manifest(
        work_dir=work_dir,
        schema=schema,
        table_files=table_files,
        url=github_release_asset_url(),
        archive_sha256=archive_sha256,
        output_path=manifest_path,
    )
    manifest_hash, data_archive_hash = rendered_manifest_identity(manifest_path)
    write_build_manifest(
        work_dir,
        {
            "data_manifest": {
                "path": str(manifest_path),
                "manifest_hash": manifest_hash,
                "data_archive_hash": data_archive_hash,
                "archive_sha256": archive_sha256,
                "assembled_at": utc_now_iso(),
            }
        },
    )
    return manifest_path


def query_sort_key(query_id: str) -> tuple[int, str]:
    match = re.fullmatch(r"(\d+)([a-z])", query_id)
    if match is None:
        return (10_000, query_id)
    return (int(match.group(1)), match.group(2))


def query_files(query_dir: Path) -> list[Path]:
    files = sorted(query_dir.glob("*.sql"), key=lambda p: query_sort_key(p.stem))
    return [p for p in files if QUERY_ID_RE.fullmatch(p.stem)]


def strip_query_semicolon(sql: str) -> str:
    return sql.strip().removesuffix(";").strip()


def duckdb_compatible_query_sql(sql: str) -> str:

    sql = re.sub(r"\bAS\s+at\b", 'AS "at"', sql, flags=re.IGNORECASE)
    return re.sub(r"\bat\.", '"at".', sql)


def underlying_count_failure(query_id: str, count: int) -> dict[str, Any] | None:
    if query_id in KNOWN_ZERO_UNDERLYING_QUERIES:
        if count != 0:
            return {
                "query_id": query_id,
                "expected_underlying_row_count": 0,
                "actual_underlying_row_count": count,
                "reason": "known_zero_drift",
            }
        return None
    if count < 1:
        return {
            "query_id": query_id,
            "expected_underlying_row_count": ">=1",
            "actual_underlying_row_count": count,
            "reason": "unexpected_empty",
        }
    return None


def split_query(query_id: str, sql: str) -> QueryParts:
    stripped = strip_query_semicolon(sql)
    from_match = re.search(r"\bFROM\b", stripped, flags=re.IGNORECASE)
    where_match = re.search(r"\bWHERE\b", stripped, flags=re.IGNORECASE)
    if from_match is None or where_match is None or where_match.start() <= from_match.end():
        raise QueryImportError(f"Query {query_id} is not a flat SELECT/FROM/WHERE JOB query")
    return QueryParts(
        query_id=query_id,
        select_part=stripped[: from_match.start()].strip(),
        from_part=stripped[from_match.end() : where_match.start()].strip(),
        where_part=stripped[where_match.end() :].strip(),
    )


def split_from_items(from_part: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0
    for char in from_part:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def query_aliases(sql: str, query_id: str = "?") -> list[QueryAlias]:
    parts = split_query(query_id, sql)
    aliases: list[QueryAlias] = []
    for item in split_from_items(parts.from_part):
        cleaned = " ".join(item.split())
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)", cleaned, re.I)
        if match is None:
            raise QueryImportError(f"Unable to parse FROM item in query {query_id}: {item!r}")
        aliases.append(QueryAlias(table=match.group(1), alias=match.group(2)))
    return aliases


def underlying_count_sql(sql: str, query_id: str = "?") -> str:
    parts = split_query(query_id, sql)
    return f"SELECT count(*) FROM {parts.from_part} WHERE {parts.where_part}"


def alias_id_sql(sql: str, query_id: str = "?", *, limit: int = 3) -> str:
    parts = split_query(query_id, sql)
    aliases = query_aliases(sql, query_id=query_id)
    select_list = ", ".join(f"{alias.alias}.id AS {quote_ident(alias.alias + '__id')}" for alias in aliases)
    order_list = ", ".join(f"{alias.alias}.id" for alias in aliases)
    return f"SELECT {select_list} FROM {parts.from_part} WHERE {parts.where_part} ORDER BY {order_list} LIMIT {limit}"


def import_canonical_queries(*, work_dir: Path, destination: Path | None = None) -> Path:
    try:
        import sqlglot
    except ImportError as exc:
        raise JoinOrderBuildError("sqlglot is required in _project/scripts to validate JOB SQL") from exc

    dest_dir = destination or repo_root() / "_project" / "joinorder" / "build-inputs" / "queries"
    dest_dir.mkdir(parents=True, exist_ok=True)
    clone_dir = work_dir / "upstream" / f"join-order-benchmark-{GREGRAHN_COMMIT[:12]}"
    if not clone_dir.exists():
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        stream_command(["git", "clone", "--quiet", GREGRAHN_REPO, str(clone_dir)])
    run_command(["git", "-C", str(clone_dir), "fetch", "--quiet", "origin", GREGRAHN_COMMIT])
    run_command(["git", "-C", str(clone_dir), "checkout", "--quiet", GREGRAHN_COMMIT])

    source_files = sorted(clone_dir.glob("*.sql"), key=lambda p: query_sort_key(p.stem))
    source_files = [p for p in source_files if QUERY_ID_RE.fullmatch(p.stem)]
    if len(source_files) != EXPECTED_QUERY_COUNT:
        raise QueryImportError(f"Expected {EXPECTED_QUERY_COUNT} canonical JOB queries, found {len(source_files)}")

    for stale in dest_dir.glob("*.sql"):
        stale.unlink()
    for source in source_files:
        text = source.read_text(encoding="utf-8")
        sqlglot.parse_one(text, read="postgres")
        split_query(source.stem, text)
        if not text.endswith("\n"):
            text += "\n"
        (dest_dir / source.name).write_text(text, encoding="utf-8")

    write_build_manifest(
        work_dir,
        {
            "query_import": {
                "source_repo": GREGRAHN_REPO,
                "gregrahn_commit": GREGRAHN_COMMIT,
                "query_count": len(source_files),
                "destination": str(dest_dir),
                "imported_at": utc_now_iso(),
            }
        },
    )
    return dest_dir


def validate_predicate_domain(
    *,
    work_dir: Path,
    query_dir: Path,
    container_name: str,
    database: str,
    user: str,
    expected_query_count: int | None = EXPECTED_QUERY_COUNT,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    known_zero_counts: dict[str, int] = {}
    for path in query_files(query_dir):
        sql = path.read_text(encoding="utf-8")
        count_raw = psql(
            container_name=container_name,
            database=database,
            user=user,
            sql=underlying_count_sql(sql, query_id=path.stem),
        ).strip()
        count = int(count_raw)
        counts[path.stem] = count
        if path.stem in KNOWN_ZERO_UNDERLYING_QUERIES:
            known_zero_counts[path.stem] = count
        failure = underlying_count_failure(path.stem, count)
        if failure is not None:
            failures.append(failure)
    query_count_failures: list[dict[str, Any]] = []
    if expected_query_count is not None and len(counts) != expected_query_count:
        query_count_failures.append(
            {
                "expected_query_count": expected_query_count,
                "actual_query_count": len(counts),
            }
        )
    missing_known_zero_queries = sorted(set(KNOWN_ZERO_UNDERLYING_QUERIES) - set(counts), key=query_sort_key)
    if expected_query_count is not None and missing_known_zero_queries:
        query_count_failures.append({"missing_known_zero_queries": missing_known_zero_queries})

    null_fractions: dict[str, float] = {}
    null_failures: list[dict[str, Any]] = []
    null_counts: dict[str, dict[str, Any]] = {}
    for (table, column), (expected_nulls, expected_rows) in CANONICAL_NULL_COUNTS.items():
        rows = psql_json_rows(
            container_name=container_name,
            database=database,
            user=user,
            sql=(
                "SELECT count(*)::bigint AS row_count, "
                f"count(*) FILTER (WHERE {quote_ident(column)} IS NULL)::bigint AS null_count "
                f"FROM public.{quote_ident(table)}"
            ),
        )
        if len(rows) != 1:
            raise JoinOrderBuildError(f"Null-count gate returned {len(rows)} rows for {table}.{column}")
        actual_rows = int(rows[0]["row_count"])
        actual_nulls = int(rows[0]["null_count"])
        actual = actual_nulls / actual_rows
        key = f"{table}.{column}"
        null_fractions[key] = actual
        null_counts[key] = {
            "null_count": actual_nulls,
            "row_count": actual_rows,
            "null_fraction": actual,
            "expected_null_count": expected_nulls,
            "expected_row_count": expected_rows,
            "expected_null_fraction": expected_nulls / expected_rows,
        }
        if actual_nulls != expected_nulls or actual_rows != expected_rows:
            null_failures.append(
                {
                    "column": key,
                    "expected_null_count": expected_nulls,
                    "actual_null_count": actual_nulls,
                    "expected_row_count": expected_rows,
                    "actual_row_count": actual_rows,
                    "expected_null_fraction": expected_nulls / expected_rows,
                    "actual_null_fraction": actual,
                }
            )

    report = {
        "query_underlying_row_counts": counts,
        "known_zero_underlying_queries": sorted(KNOWN_ZERO_UNDERLYING_QUERIES, key=query_sort_key),
        "known_zero_underlying_row_counts": dict(
            sorted(known_zero_counts.items(), key=lambda item: query_sort_key(item[0]))
        ),
        "query_count_failures": query_count_failures,
        "query_failures": failures,
        "unexpected_empty_query_failures": [failure for failure in failures if failure["reason"] == "unexpected_empty"],
        "known_zero_drift_failures": [failure for failure in failures if failure["reason"] == "known_zero_drift"],
        "null_counts": null_counts,
        "null_fractions": null_fractions,
        "null_count_failures": null_failures,
        "null_fraction_failures": null_failures,
        "validated_at": utc_now_iso(),
    }
    write_build_manifest(work_dir, {"predicate_gate": report})
    if query_count_failures or failures or null_failures:
        raise JoinOrderBuildError(
            "Predicate-domain gate failed: "
            f"{len(query_count_failures)} query-count failures, "
            f"{len(report['unexpected_empty_query_failures'])} unexpected empty query predicates, "
            f"{len(report['known_zero_drift_failures'])} known-zero drifts, "
            f"{len(null_failures)} null-count drifts"
        )
    return report


def validate_encoding_round_trip(
    *,
    work_dir: Path,
    query_sample_size: int,
    container_name: str,
    database: str,
    user: str,
) -> dict[str, Any]:
    try:
        import duckdb
    except ImportError as exc:
        raise JoinOrderBuildError("duckdb is required in _project/scripts for encoding round-trip validation") from exc

    con = duckdb.connect()
    parquet_dir = work_dir / "parquet"
    results: dict[str, Any] = {"samples": [], "validated_at": utc_now_iso()}
    try:
        for table, column in STRING_SAMPLE_COLUMNS.items():
            predicate = f"{quote_ident(column)} IS NOT NULL"
            if column in {"name", "title"}:
                predicate += f" AND {quote_ident(column)} ~ '[^ -~]'"
            rows = psql_json_rows(
                container_name=container_name,
                database=database,
                user=user,
                sql=(
                    f"SELECT id, {quote_ident(column)} AS value, "
                    f"encode(convert_to({quote_ident(column)}, 'UTF8'), 'hex') AS utf8_hex "
                    f"FROM public.{quote_ident(table)} WHERE {predicate} ORDER BY id LIMIT {query_sample_size}"
                ),
            )
            if not rows:
                raise JoinOrderBuildError(f"No encoding-gate samples found for {table}.{column}")
            for row in rows:
                parquet_value = con.execute(
                    f"SELECT {quote_ident(column)} FROM read_parquet({duckdb_literal(parquet_dir / f'{table}.parquet')}) "
                    "WHERE id = ?",
                    [int(row["id"])],
                ).fetchone()
                if parquet_value is None:
                    raise JoinOrderBuildError(f"Encoding sample id={row['id']} missing from {table}.parquet")
                value = parquet_value[0]
                actual_hex = value.encode("utf-8").hex() if value is not None else ""
                if actual_hex != row["utf8_hex"]:
                    raise JoinOrderBuildError(
                        f"Encoding round-trip drift for {table}.{column} id={row['id']}: "
                        f"postgres {row['utf8_hex']} parquet {actual_hex}"
                    )
            results["samples"].append({"table": table, "column": column, "count": len(rows)})
    finally:
        con.close()
    write_build_manifest(work_dir, {"encoding_round_trip": results})
    return results


def duckdb_parquet_schema(con: Any, parquet_path: Path) -> dict[str, str]:
    rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet({duckdb_literal(parquet_path)})").fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def validate_conversion_fidelity(
    *,
    work_dir: Path,
    container_name: str,
    database: str,
    user: str,
    utf8_sample_size: int = 100,
) -> dict[str, Any]:
    try:
        import duckdb
    except ImportError as exc:
        raise JoinOrderBuildError("duckdb is required in _project/scripts for conversion fidelity validation") from exc

    parquet_dir = work_dir / "parquet"
    if not parquet_dir.exists():
        raise JoinOrderBuildError(f"Parquet directory missing: {parquet_dir}")

    schema = postgres_schema(container_name=container_name, database=database, user=user)
    row_count_checks: dict[str, dict[str, int]] = {}
    string_checks: dict[str, dict[str, Any]] = {}
    integer_checks: dict[str, dict[str, Any]] = {}
    utf8_checks: dict[str, dict[str, Any]] = {}
    failures: list[str] = []

    con = duckdb.connect()
    try:
        for table in TABLE_NAMES:
            parquet_path = parquet_dir / f"{table}.parquet"
            if not parquet_path.exists():
                failures.append(f"{table}: missing Parquet file {parquet_path}")
                continue

            parquet_schema = duckdb_parquet_schema(con, parquet_path)
            pg_row = psql_json_rows(
                container_name=container_name,
                database=database,
                user=user,
                sql=f"SELECT count(*)::bigint AS row_count FROM public.{quote_ident(table)}",
            )[0]
            parquet_row_count = int(
                con.execute(f"SELECT count(*) FROM read_parquet({duckdb_literal(parquet_path)})").fetchone()[0]
            )
            postgres_row_count = int(pg_row["row_count"])
            expected_row_count = EXPECTED_ROW_COUNTS[table]
            row_count_checks[table] = {
                "postgres_row_count": postgres_row_count,
                "parquet_row_count": parquet_row_count,
                "expected_row_count": expected_row_count,
            }
            if postgres_row_count != parquet_row_count or parquet_row_count != expected_row_count:
                failures.append(
                    f"{table}: row count mismatch postgres={postgres_row_count} "
                    f"parquet={parquet_row_count} expected={expected_row_count}"
                )

            for column in schema[table]:
                if is_postgres_string_column(column) and column.is_nullable:
                    pg_counts = psql_json_rows(
                        container_name=container_name,
                        database=database,
                        user=user,
                        sql=(
                            "SELECT "
                            f"count(*) FILTER (WHERE {quote_ident(column.name)} IS NULL)::bigint AS null_count, "
                            f"count(*) FILTER (WHERE {quote_ident(column.name)} = '')::bigint AS empty_count "
                            f"FROM public.{quote_ident(table)}"
                        ),
                    )[0]
                    parquet_counts = con.execute(
                        "SELECT "
                        f"count(*) FILTER (WHERE {quote_ident(column.name)} IS NULL) AS null_count, "
                        f"count(*) FILTER (WHERE {quote_ident(column.name)} = '') AS empty_count "
                        f"FROM read_parquet({duckdb_literal(parquet_path)})"
                    ).fetchone()
                    key = f"{table}.{column.name}"
                    check = {
                        "postgres_null_count": int(pg_counts["null_count"]),
                        "postgres_empty_count": int(pg_counts["empty_count"]),
                        "parquet_null_count": int(parquet_counts[0]),
                        "parquet_empty_count": int(parquet_counts[1]),
                    }
                    string_checks[key] = check
                    if (
                        check["postgres_null_count"] != check["parquet_null_count"]
                        or check["postgres_empty_count"] != check["parquet_empty_count"]
                    ):
                        failures.append(f"{key}: null/empty string mismatch {check}")

                if is_postgres_integer_column(column):
                    pg_range = psql_json_rows(
                        container_name=container_name,
                        database=database,
                        user=user,
                        sql=(
                            f"SELECT min({quote_ident(column.name)})::bigint AS min_value, "
                            f"max({quote_ident(column.name)})::bigint AS max_value "
                            f"FROM public.{quote_ident(table)}"
                        ),
                    )[0]
                    parquet_range = con.execute(
                        f"SELECT min({quote_ident(column.name)}) AS min_value, "
                        f"max({quote_ident(column.name)}) AS max_value "
                        f"FROM read_parquet({duckdb_literal(parquet_path)})"
                    ).fetchone()
                    pg_min = int(pg_range["min_value"]) if pg_range["min_value"] is not None else None
                    pg_max = int(pg_range["max_value"]) if pg_range["max_value"] is not None else None
                    parquet_min = int(parquet_range[0]) if parquet_range[0] is not None else None
                    parquet_max = int(parquet_range[1]) if parquet_range[1] is not None else None
                    parquet_type = parquet_schema.get(column.name, "")
                    key = f"{table}.{column.name}"
                    check = {
                        "postgres_min": pg_min,
                        "postgres_max": pg_max,
                        "parquet_min": parquet_min,
                        "parquet_max": parquet_max,
                        "parquet_type": parquet_type,
                    }
                    integer_checks[key] = check
                    if pg_min != parquet_min or pg_max != parquet_max:
                        failures.append(f"{key}: integer min/max mismatch {check}")
                    if not duckdb_integer_type_can_store_range(parquet_type, pg_min, pg_max):
                        failures.append(f"{key}: Parquet type {parquet_type!r} cannot represent postgres range")

            utf8_column = UTF8_FIDELITY_SAMPLE_COLUMNS.get(table)
            if utf8_column is not None:
                rows = psql_json_rows(
                    container_name=container_name,
                    database=database,
                    user=user,
                    sql=(
                        f"SELECT id, encode(convert_to({quote_ident(utf8_column)}, 'UTF8'), 'hex') AS utf8_hex "
                        f"FROM public.{quote_ident(table)} "
                        f"WHERE {quote_ident(utf8_column)} IS NOT NULL "
                        f"AND {quote_ident(utf8_column)} ~ '[^ -~]' "
                        f"ORDER BY id LIMIT {utf8_sample_size}"
                    ),
                )
                key = f"{table}.{utf8_column}"
                utf8_checks[key] = {"sample_count": len(rows)}
                if not rows:
                    failures.append(f"{key}: no non-ASCII UTF-8 samples found")
                for row in rows:
                    parquet_value = con.execute(
                        f"SELECT {quote_ident(utf8_column)} "
                        f"FROM read_parquet({duckdb_literal(parquet_path)}) WHERE id = ?",
                        [int(row["id"])],
                    ).fetchone()
                    if parquet_value is None:
                        failures.append(f"{key}: sample id={row['id']} missing from Parquet")
                        continue
                    actual_hex = parquet_value[0].encode("utf-8").hex() if parquet_value[0] is not None else ""
                    if actual_hex != row["utf8_hex"]:
                        failures.append(
                            f"{key}: UTF-8 drift for id={row['id']} postgres={row['utf8_hex']} parquet={actual_hex}"
                        )
    finally:
        con.close()

    report = {
        "validated_at": utc_now_iso(),
        "parquet_dir": str(parquet_dir),
        "row_count_checks": row_count_checks,
        "string_null_empty_checks": string_checks,
        "integer_width_checks": integer_checks,
        "utf8_round_trip_checks": utf8_checks,
        "failures": failures,
    }
    write_build_manifest(work_dir, {"conversion_fidelity": report})
    if failures:
        raise JoinOrderBuildError(
            f"Conversion fidelity validation failed with {len(failures)} issue(s): {failures[:5]}"
        )
    return report


def verify_logical_content_hashes(
    *,
    work_dir: Path,
    schema: Mapping[str, Sequence[ColumnSchema]],
) -> LogicalContentReport:

    try:
        import duckdb
    except ImportError as exc:
        raise JoinOrderBuildError("duckdb is required in _project/scripts to verify logical content hashes") from exc

    csv_dir = work_dir / "csv"
    parquet_dir = work_dir / "parquet"
    if not csv_dir.exists():
        raise JoinOrderBuildError(f"CSV directory missing: {csv_dir}")
    if not parquet_dir.exists():
        raise JoinOrderBuildError(f"Parquet directory missing: {parquet_dir}")

    table_reports: list[dict[str, Any]] = []
    parquet_hashes: list[LogicalTableHash] = []
    failures: list[str] = []
    con = duckdb.connect()
    try:
        for table in TABLE_NAMES:
            columns = list(schema[table])
            csv_hash = logical_table_hash_from_csv(csv_dir / f"{table}.csv", columns)
            parquet_hash = logical_table_hash_from_parquet(
                con=con,
                parquet_path=parquet_dir / f"{table}.parquet",
                table=table,
                columns=columns,
            )
            parquet_hashes.append(parquet_hash)
            table_report, table_failures = logical_content_table_report(csv_hash, parquet_hash)
            table_reports.append(table_report)
            failures.extend(table_failures)
    finally:
        con.close()

    return logical_content_report_from_hashes(
        work_dir=work_dir,
        table_reports=table_reports,
        parquet_hashes=parquet_hashes,
        failures=failures,
    )


def compute_reference_cardinalities(
    *,
    work_dir: Path,
    query_dir: Path,
    output_path: Path | None,
    container_name: str,
    database: str,
    user: str,
) -> Path:
    manifest = load_build_manifest(work_dir)
    queries: dict[str, dict[str, Any]] = {}
    postgres_version = psql(
        container_name=container_name, database=database, user=user, sql="SHOW server_version"
    ).strip()
    for path in query_files(query_dir):
        sql = strip_query_semicolon(path.read_text(encoding="utf-8"))
        row_count = int(
            psql(
                container_name=container_name,
                database=database,
                user=user,
                sql=f"SELECT count(*) FROM ({sql}) AS benchbox_q",
            ).strip()
        )
        first_row_json = psql(
            container_name=container_name,
            database=database,
            user=user,
            sql=f"SELECT row_to_json(benchbox_q)::text FROM ({sql}) AS benchbox_q ORDER BY 1 LIMIT 1",
        ).strip()
        first_row_sha256 = hashlib.sha256(first_row_json.encode("utf-8")).hexdigest() if first_row_json else None
        underlying_count = int(
            psql(
                container_name=container_name,
                database=database,
                user=user,
                sql=underlying_count_sql(sql, query_id=path.stem),
            ).strip()
        )
        queries[path.stem] = {
            "row_count": row_count,
            "underlying_row_count": underlying_count,
            "first_row_sha256": first_row_sha256,
        }
        print(f"cardinality {path.stem}: rows={row_count} underlying={underlying_count}", flush=True)

    if len(queries) != EXPECTED_QUERY_COUNT:
        raise JoinOrderBuildError(f"Expected {EXPECTED_QUERY_COUNT} cardinalities, computed {len(queries)}")

    out = output_path or repo_root() / "_project" / "joinorder" / "reference_cardinalities.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "postgres_version": postgres_version,
        "dataset_version": DATASET_VERSION,
        "manifest_hash": manifest.get("data_manifest", {}).get("manifest_hash"),
        "data_archive_hash": manifest.get("data_manifest", {}).get("data_archive_hash"),
        "gregrahn_commit": manifest.get("query_import", {}).get("gregrahn_commit", GREGRAHN_COMMIT),
        "queries": dict(sorted(queries.items(), key=lambda item: query_sort_key(item[0]))),
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_build_manifest(work_dir, {"reference_cardinalities": {"path": str(out), "query_count": len(queries)}})
    return out


def verify_reference_results(
    *,
    work_dir: Path,
    query_dir: Path,
    reference_path: Path,
    container_name: str,
    database: str,
    user: str,
    expected_query_count: int | None = EXPECTED_QUERY_COUNT,
) -> dict[str, Any]:
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    expected_queries = reference.get("queries")
    if not isinstance(expected_queries, dict):
        raise JoinOrderBuildError(f"{reference_path} must contain a 'queries' object")

    paths = {path.stem: path for path in query_files(query_dir)}
    failures: list[dict[str, Any]] = []
    missing_queries = sorted(set(paths) - set(expected_queries), key=query_sort_key)
    unexpected_queries = sorted(set(expected_queries) - set(paths), key=query_sort_key)

    for query_id in missing_queries:
        failures.append({"query_id": query_id, "kind": "missing_reference_query"})
    for query_id in unexpected_queries:
        failures.append({"query_id": query_id, "kind": "unexpected_reference_query"})
    if expected_query_count is not None and len(paths) != expected_query_count:
        failures.append(
            {
                "kind": "query_count",
                "expected_query_count": expected_query_count,
                "actual_query_count": len(paths),
            }
        )

    postgres_version = psql(
        container_name=container_name, database=database, user=user, sql="SHOW server_version"
    ).strip()
    verified: dict[str, dict[str, Any]] = {}

    for query_id, path in sorted(paths.items(), key=lambda item: query_sort_key(item[0])):
        expected = expected_queries.get(query_id)
        if expected is None:
            continue
        if not isinstance(expected, dict):
            failures.append(
                {
                    "query_id": query_id,
                    "kind": "malformed_reference_query",
                    "actual_type": type(expected).__name__,
                }
            )
            continue
        sql = strip_query_semicolon(path.read_text(encoding="utf-8"))
        row_count = int(
            psql(
                container_name=container_name,
                database=database,
                user=user,
                sql=f"SELECT count(*) FROM ({sql}) AS benchbox_q",
            ).strip()
        )
        row_json = psql(
            container_name=container_name,
            database=database,
            user=user,
            sql=f"SELECT row_to_json(benchbox_q)::text FROM ({sql}) AS benchbox_q ORDER BY 1 LIMIT 1",
        ).strip()
        actual_hash = hashlib.sha256(row_json.encode("utf-8")).hexdigest() if row_json else None
        underlying_count = int(
            psql(
                container_name=container_name,
                database=database,
                user=user,
                sql=underlying_count_sql(sql, query_id=query_id),
            ).strip()
        )

        verified[query_id] = {
            "row_count": row_count,
            "underlying_row_count": underlying_count,
            "first_row_sha256": actual_hash,
        }

        checks = (
            ("row_count", row_count),
            ("underlying_row_count", underlying_count),
            ("first_row_sha256", actual_hash),
        )
        for field, actual in checks:
            expected_value = expected.get(field)
            if actual != expected_value:
                failures.append(
                    {
                        "query_id": query_id,
                        "kind": field,
                        "expected": expected_value,
                        "actual": actual,
                    }
                )

    report = {
        "reference_path": str(reference_path),
        "query_dir": str(query_dir),
        "dataset_version": reference.get("dataset_version"),
        "manifest_hash": reference.get("manifest_hash"),
        "data_archive_hash": reference.get("data_archive_hash"),
        "gregrahn_commit": reference.get("gregrahn_commit"),
        "expected_postgres_version": reference.get("postgres_version"),
        "actual_postgres_version": postgres_version,
        "verified_query_count": len(verified),
        "full_result_hashes_verified": sum(
            1
            for query_id, values in verified.items()
            if values.get("first_row_sha256") == expected_queries.get(query_id, {}).get("first_row_sha256")
        ),
        "failures": failures,
        "validated_at": utc_now_iso(),
    }
    write_build_manifest(work_dir, {"reference_result_verification": report})
    if failures:
        raise JoinOrderBuildError(
            f"Reference result verification failed: {len(failures)} failure(s); "
            f"see {build_manifest_path(work_dir)}:reference_result_verification"
        )
    return report


def write_cardinality_cross_check(*, work_dir: Path, cardinalities_path: Path, output_path: Path | None = None) -> Path:
    out = output_path or repo_root() / "_project" / "joinorder" / "cardinality-cross-check.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    cardinalities = json.loads(cardinalities_path.read_text(encoding="utf-8"))
    query_count = len(cardinalities.get("queries", {}))
    out.write_text(
        "\n".join(
            [
                "# JoinOrder Cardinality Cross-check",
                "",
                "The Leis et al. 2015 JOB paper and the commonly mirrored JOB companion materials publish optimizer",
                "error distributions and the canonical query text, but not a complete stable table of per-query",
                "answer-set cardinalities for all 113 aggregate queries.",
                "",
                "BenchBox therefore treats the PostgreSQL run captured in",
                "`_project/joinorder/reference_cardinalities.json` as the authoritative oracle for this dataset",
                "build. The oracle is tied to the Harvard Dataverse pg_dump SHA256, the gregrahn query commit,",
                "the data manifest hash, and PostgreSQL version recorded in that JSON file.",
                "",
                f"Computed oracle coverage: {query_count}/113 queries.",
                "",
                "Traceable published per-query row counts found: none.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_build_manifest(work_dir, {"cardinality_cross_check": {"path": str(out), "traceable_counts": 0}})
    return out


def write_data_license_template(work_dir: Path) -> Path:
    path = work_dir / "package" / "DATA-LICENSE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# JoinOrder IMDb 2013 Dataset License Notice",
                "",
                "Dataset provenance: Harvard Dataverse DOI 10.7910/DVN/2QYZBT, May 2013 IMDb list-file",
                "snapshot parsed with imdbpy into the 21-table JOB relational schema and converted by BenchBox",
                "to Parquet for benchmark reproducibility.",
                "",
                "Information courtesy of IMDb (https://www.imdb.com). Used with permission.",
                "",
                "Scope: this derivative archive is provided for research and database optimizer benchmarking.",
                "",
                "BenchBox redistributes a derivative form of this dataset as a convenience for benchmark",
                "reproducibility. BenchBox makes no claim of ownership over the underlying IMDb data and",
                "will honor takedown requests.",
                "",
                "Takedown contact: finalized by the cutover TODO before the release is published.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def write_checksums(table_files: Sequence[TableFile], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(f"{entry.sha256}  {entry.path.name}\n" for entry in sorted(table_files, key=lambda f: f.table)),
        encoding="utf-8",
    )
    return output_path


def add_tar_entry(tar: tarfile.TarFile, path: Path, arcname: str) -> None:
    info = tar.gettarinfo(str(path), arcname=arcname)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    with path.open("rb") as handle:
        tar.addfile(info, handle)


def create_tar_zst(entries: Sequence[tuple[Path, str]], output_path: Path) -> Path:
    try:
        import zstandard as zstd
    except ImportError as exc:
        raise JoinOrderBuildError("zstandard is required in _project/scripts to package JoinOrder data") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    compressor = zstd.ZstdCompressor(level=19, threads=0)
    with tmp_path.open("wb") as raw:
        with compressor.stream_writer(raw) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as tar:
                for path, arcname in sorted(entries, key=lambda item: item[1]):
                    add_tar_entry(tar, path, arcname)
    tmp_path.replace(output_path)
    return output_path


def package_archive(*, work_dir: Path, manifest_path: Path, cardinalities_path: Path) -> tuple[Path, Path]:
    table_files = load_table_files_from_manifest(work_dir, "parquet")
    package_dir = work_dir / "package"
    checksums_path = write_checksums(table_files, package_dir / "checksums.txt")
    license_path = write_data_license_template(work_dir)
    package_manifest = package_dir / "data_manifest.toml"
    shutil.copyfile(manifest_path, package_manifest)
    package_cardinalities = package_dir / "reference_cardinalities.json"
    shutil.copyfile(cardinalities_path, package_cardinalities)

    entries: list[tuple[Path, str]] = [(entry.path, entry.path.name) for entry in table_files]
    entries.extend(
        [
            (package_manifest, "data_manifest.toml"),
            (license_path, "DATA-LICENSE.md"),
            (checksums_path, "checksums.txt"),
            (package_cardinalities, "reference_cardinalities.json"),
        ]
    )
    archive_path = work_dir / "archive" / ARCHIVE_NAME
    create_tar_zst(entries, archive_path)
    archive_sha = sha256_file(archive_path)
    sha_path = archive_path.with_name(ARCHIVE_SHA_NAME)
    sha_path.write_text(f"{archive_sha}  {ARCHIVE_NAME}\n", encoding="utf-8")
    write_build_manifest(
        work_dir,
        {
            "archive": {
                "path": str(archive_path),
                "sha256_path": str(sha_path),
                "sha256": archive_sha,
                "bytes": archive_path.stat().st_size,
                "packaged_at": utc_now_iso(),
            }
        },
    )
    return archive_path, sha_path


def load_queries_for_duckdb(con: Any, fixture_dir: Path) -> None:
    for table in TABLE_NAMES:
        con.execute(
            f"CREATE OR REPLACE VIEW {quote_ident(table)} AS "
            f"SELECT * FROM read_parquet({duckdb_literal(fixture_dir / f'{table}.parquet')})"
        )


def collect_initial_tiny_ids(
    *,
    query_dir: Path,
    container_name: str,
    database: str,
    user: str,
    sample_per_table: int,
) -> dict[str, set[int]]:
    ids: dict[str, set[int]] = {table: set() for table in TABLE_NAMES}
    for table in TABLE_NAMES:
        rows = psql_json_rows(
            container_name=container_name,
            database=database,
            user=user,
            sql=f"SELECT id FROM public.{quote_ident(table)} ORDER BY id LIMIT {sample_per_table}",
        )
        ids[table].update(int(row["id"]) for row in rows)

    for path in query_files(query_dir):
        sql = path.read_text(encoding="utf-8")
        rows = psql_json_rows(
            container_name=container_name,
            database=database,
            user=user,
            sql=alias_id_sql(sql, query_id=path.stem, limit=3),
        )
        if not rows:
            if path.stem in KNOWN_ZERO_UNDERLYING_QUERIES:
                continue
            raise JoinOrderBuildError(f"Query {path.stem} produced no rows for tiny fixture sampling")
        if path.stem in KNOWN_ZERO_UNDERLYING_QUERIES:
            raise JoinOrderBuildError(f"Known-zero query {path.stem} produced rows for tiny fixture sampling")
        aliases = {alias.alias: alias.table for alias in query_aliases(sql, query_id=path.stem)}
        for row in rows:
            for key, value in row.items():
                if value is None or not key.endswith("__id"):
                    continue
                alias = key[: -len("__id")]
                table = aliases[alias]
                ids[table].add(int(value))
    return ids


def close_tiny_ids_over_foreign_keys(
    *,
    ids: dict[str, set[int]],
    foreign_keys: Sequence[ForeignKey],
    container_name: str,
    database: str,
    user: str,
) -> None:
    changed = True
    while changed:
        changed = False
        for fk in foreign_keys:
            child_ids = sorted(ids.get(fk.child_table, set()))
            if not child_ids or fk.parent_column != "id":
                continue
            child_id_sql = ", ".join(str(value) for value in child_ids)
            rows = psql_json_rows(
                container_name=container_name,
                database=database,
                user=user,
                sql=(
                    f"SELECT DISTINCT {quote_ident(fk.child_column)} AS parent_id "
                    f"FROM public.{quote_ident(fk.child_table)} "
                    f"WHERE id IN ({child_id_sql}) AND {quote_ident(fk.child_column)} IS NOT NULL"
                ),
            )
            before = len(ids[fk.parent_table])
            ids[fk.parent_table].update(int(row["parent_id"]) for row in rows if row["parent_id"] is not None)
            changed = changed or len(ids[fk.parent_table]) != before


def export_tiny_fixture(
    *,
    work_dir: Path,
    query_dir: Path,
    output_dir: Path,
    schema: Mapping[str, Sequence[ColumnSchema]],
    container_name: str,
    database: str,
    user: str,
    sample_per_table: int = 50,
) -> Path:
    try:
        import duckdb
    except ImportError as exc:
        raise JoinOrderBuildError("duckdb is required in _project/scripts to generate tiny fixture") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    tiny_csv_dir = work_dir / "tiny-csv"
    ids = collect_initial_tiny_ids(
        query_dir=query_dir,
        container_name=container_name,
        database=database,
        user=user,
        sample_per_table=sample_per_table,
    )
    close_tiny_ids_over_foreign_keys(
        ids=ids,
        foreign_keys=postgres_foreign_keys(container_name=container_name, database=database, user=user),
        container_name=container_name,
        database=database,
        user=user,
    )

    table_files: list[TableFile] = []
    con = duckdb.connect()
    try:
        for table in TABLE_NAMES:
            table_ids = sorted(ids[table])
            if not table_ids:
                raise JoinOrderBuildError(f"Tiny fixture selected zero rows for {table}")
            sql_where = f"id IN ({', '.join(str(value) for value in table_ids)})"
            csv_path = tiny_csv_dir / f"{table}.csv"
            parquet_path = output_dir / f"{table}.parquet"
            copy_table_to_csv(
                container_name=container_name,
                database=database,
                user=user,
                table=table,
                destination=csv_path,
                sql_where=sql_where,
            )
            select_list = ", ".join(duckdb_cast_expression(column) for column in schema[table])
            con.execute(
                f"""
                COPY (
                  SELECT {select_list}
                  FROM read_csv_auto({duckdb_literal(csv_path)}, header=true, all_varchar=true, sample_size=-1)
                )
                TO {duckdb_literal(parquet_path)}
                (FORMAT 'parquet', COMPRESSION 'zstd', ROW_GROUP_SIZE 100000)
                """
            )
            row_count = con.execute(f"SELECT count(*) FROM read_parquet({duckdb_literal(parquet_path)})").fetchone()[0]
            sha256, _md5, size = hash_file(parquet_path)
            table_files.append(
                TableFile(table=table, path=parquet_path, sha256=sha256, bytes=size, row_count=row_count)
            )
        load_queries_for_duckdb(con, output_dir)
        tiny_cardinalities: dict[str, dict[str, Any]] = {}
        for path in query_files(query_dir):
            sql = strip_query_semicolon(path.read_text(encoding="utf-8"))
            duckdb_sql = duckdb_compatible_query_sql(sql)
            underlying = con.execute(
                duckdb_compatible_query_sql(underlying_count_sql(sql, query_id=path.stem))
            ).fetchone()[0]
            failure = underlying_count_failure(path.stem, int(underlying))
            if failure is not None:
                raise JoinOrderBuildError(
                    f"Tiny fixture predicate contract failed for query {path.stem}: {failure['reason']}"
                )
            row_count = con.execute(f"SELECT count(*) FROM ({duckdb_sql}) AS benchbox_q").fetchone()[0]
            first_row = con.execute(f"SELECT * FROM ({duckdb_sql}) AS benchbox_q LIMIT 1").fetchone()
            first_row_json = json.dumps(first_row, default=str, ensure_ascii=False) if first_row else ""
            tiny_cardinalities[path.stem] = {
                "row_count": int(row_count),
                "underlying_row_count": int(underlying),
                "first_row_sha256": hashlib.sha256(str(first_row_json).encode("utf-8")).hexdigest()
                if first_row_json
                else None,
            }
    finally:
        con.close()

    manifest_path = output_dir / "tiny_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "dataset_version": f"{DATASET_VERSION}-tiny",
                "source_dataset_version": DATASET_VERSION,
                "table_rows": {entry.table: entry.row_count for entry in table_files},
                "table_sha256": {entry.table: entry.sha256 for entry in table_files},
                "generated_at": utc_now_iso(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    cardinalities_path = repo_root() / "_project" / "joinorder" / "tiny_reference_cardinalities.json"
    cardinalities_path.parent.mkdir(parents=True, exist_ok=True)
    cardinalities_path.write_text(
        json.dumps(
            {
                "postgres_version": DEFAULT_POSTGRES_IMAGE.removeprefix("postgres:"),
                "dataset_version": f"{DATASET_VERSION}-tiny",
                "manifest_hash": None,
                "data_archive_hash": aggregate_table_hash(table_files),
                "gregrahn_commit": GREGRAHN_COMMIT,
                "queries": dict(sorted(tiny_cardinalities.items(), key=lambda item: query_sort_key(item[0]))),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_build_manifest(
        work_dir,
        {
            "tiny_fixture": {
                "fixture_dir": str(output_dir),
                "manifest": str(manifest_path),
                "cardinalities": str(cardinalities_path),
                "table_rows": {entry.table: entry.row_count for entry in table_files},
                "generated_at": utc_now_iso(),
            }
        },
    )
    return output_dir


def verify_tiny_fixture(*, fixture_dir: Path, query_dir: Path, cardinalities_path: Path) -> None:
    query_paths = query_files(query_dir)
    cardinalities = json.loads(cardinalities_path.read_text(encoding="utf-8"))
    expected_queries = cardinalities.get("queries")
    if not isinstance(expected_queries, Mapping):
        raise JoinOrderBuildError(f"Cardinality oracle {cardinalities_path} is missing a queries object")
    if len(query_paths) != EXPECTED_QUERY_COUNT:
        raise JoinOrderBuildError(
            f"Expected {EXPECTED_QUERY_COUNT} canonical query files in {query_dir}, found {len(query_paths)}"
        )
    if len(expected_queries) != EXPECTED_QUERY_COUNT:
        raise JoinOrderBuildError(
            f"Expected {EXPECTED_QUERY_COUNT} cardinality oracle entries in {cardinalities_path}, "
            f"found {len(expected_queries)}"
        )
    query_ids = {path.stem for path in query_paths}
    expected_query_ids = {str(query_id) for query_id in expected_queries}
    missing = sorted(expected_query_ids - query_ids, key=query_sort_key)
    unexpected = sorted(query_ids - expected_query_ids, key=query_sort_key)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing query files: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected query files: {', '.join(unexpected)}")
        raise JoinOrderBuildError("Tiny fixture query coverage mismatch: " + "; ".join(details))

    try:
        import duckdb
    except ImportError as exc:
        raise JoinOrderBuildError("duckdb is required in _project/scripts to verify tiny fixture") from exc

    con = duckdb.connect()
    try:
        load_queries_for_duckdb(con, fixture_dir)
        for path in query_paths:
            sql = strip_query_semicolon(path.read_text(encoding="utf-8"))
            underlying = int(
                con.execute(duckdb_compatible_query_sql(underlying_count_sql(sql, query_id=path.stem))).fetchone()[0]
            )
            expected = int(expected_queries[path.stem]["underlying_row_count"])
            failure = underlying_count_failure(path.stem, underlying)
            if underlying != expected or failure is not None:
                raise JoinOrderBuildError(
                    f"Tiny fixture cardinality mismatch for {path.stem}: expected {expected}, got {underlying}"
                )
    finally:
        con.close()


def write_release_notes(*, work_dir: Path, archive_path: Path, archive_sha_path: Path) -> Path:
    table_files = load_table_files_from_manifest(work_dir, "parquet")
    notes_path = repo_root() / "_project" / "joinorder" / "release-notes.md"
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"- `{entry.table}`: {entry.row_count:,} rows, sha256 `{entry.sha256}`" for entry in table_files)
    archive_sha = archive_sha_path.read_text(encoding="utf-8").split()[0]
    notes_path.write_text(
        "\n".join(
            [
                "# JoinOrder canonical IMDb 2013 dataset (v1)",
                "",
                f"- Source: Harvard Dataverse DOI {SOURCE_DOI}, file `imdb_pg11`.",
                "- Snapshot: May 2013 IMDb list-file data parsed into the JOB 21-table schema.",
                "- Attribution: Information courtesy of IMDb (https://www.imdb.com). Used with permission.",
                "- Scope: research and database optimizer benchmarking.",
                "- BenchBox redistribution disclaimer and takedown commitment are included in DATA-LICENSE.md.",
                f"- Archive: `{archive_path.name}` sha256 `{archive_sha}`.",
                "",
                "## Tables",
                "",
                rows,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return notes_path


def stage_draft_release(*, work_dir: Path, archive_path: Path, archive_sha_path: Path, notes_path: Path) -> None:
    view = run_command(["gh", "release", "view", GITHUB_RELEASE_TAG, "--json", "isDraft"], check=False)
    if view.returncode != 0:
        stream_command(
            [
                "gh",
                "release",
                "create",
                GITHUB_RELEASE_TAG,
                "--draft",
                "--title",
                "JoinOrder canonical IMDb 2013 dataset (v1)",
                "--notes-file",
                str(notes_path),
                str(archive_path),
                str(archive_sha_path),
            ]
        )
    else:
        stream_command(
            [
                "gh",
                "release",
                "edit",
                GITHUB_RELEASE_TAG,
                "--draft=true",
                "--title",
                "JoinOrder canonical IMDb 2013 dataset (v1)",
                "--notes-file",
                str(notes_path),
            ]
        )
        stream_command(
            ["gh", "release", "upload", GITHUB_RELEASE_TAG, "--clobber", str(archive_path), str(archive_sha_path)]
        )
    write_build_manifest(
        work_dir,
        {
            "github_release": {
                "tag": GITHUB_RELEASE_TAG,
                "draft": True,
                "archive": str(archive_path),
                "archive_sha256": str(archive_sha_path),
                "staged_at": utc_now_iso(),
            }
        },
    )


def write_runtime_manifest(*, work_dir: Path, schema: Mapping[str, Sequence[ColumnSchema]]) -> Path:
    table_files = load_table_files_from_manifest(work_dir, "parquet")
    archive = load_build_manifest(work_dir).get("archive", {})
    archive_sha = str(archive.get("sha256") or "")
    if not archive_sha:
        raise JoinOrderBuildError("Archive sha256 missing; run package before write-runtime-manifest")
    output_path = repo_root() / "benchbox" / "core" / "joinorder" / "data_manifest.toml"
    render_data_manifest(
        work_dir=work_dir,
        schema=schema,
        table_files=table_files,
        url=github_release_asset_url(),
        archive_sha256=archive_sha,
        output_path=output_path,
    )
    runtime_manifest_hash, _ = rendered_manifest_identity(output_path)
    write_build_manifest(
        work_dir,
        {
            "runtime_manifest": {
                "path": str(output_path),
                "manifest_hash": runtime_manifest_hash,
                "archive_sha256": archive_sha,
                "written_at": utc_now_iso(),
            }
        },
    )
    return output_path


def format_restore_failures(validation: RestoreValidation) -> str:
    parts: list[str] = ["JoinOrder PostgreSQL restore validation failed"]
    if validation.missing_tables:
        parts.append(f"missing tables: {', '.join(validation.missing_tables)}")
    if validation.row_count_failures:
        failure_lines = [
            f"{failure.table}: expected {failure.expected}, got {failure.actual}, allowed_delta {failure.allowed_delta}"
            for failure in validation.row_count_failures
        ]
        parts.append("row count failures: " + "; ".join(failure_lines))
    return "\n".join(parts)


def print_source_summary(artifact: PgDumpArtifact, manifest_path: Path) -> None:
    print("Dataverse pg_dump ready")
    print(f"  doi:      {SOURCE_DOI}")
    print(f"  file id:  {artifact.dataverse_file.file_id}")
    print(f"  label:    {artifact.dataverse_file.label}")
    print(f"  bytes:    {artifact.size}")
    print(f"  md5:      {artifact.md5}")
    print(f"  sha256:   {artifact.sha256}")
    print(f"  manifest: {manifest_path}")


def print_restore_summary(validation: RestoreValidation, manifest_path: Path) -> None:
    print("PostgreSQL restore validation complete")
    print(f"  tables:   {len(validation.row_counts)}/{len(EXPECTED_ROW_COUNTS)} expected")
    print(f"  rows:     {sum(validation.row_counts.values())}")
    print(f"  manifest: {manifest_path}")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--work-dir",
        default=None,
        help=(
            "Build directory. Defaults to $BENCHBOX_OUTPUT_DIR/joinorder/build/"
            f"{DATASET_VERSION}, or ~/Developer/benchmark_runs if BENCHBOX_OUTPUT_DIR is unset."
        ),
    )


def add_restore_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--postgres-image", default=DEFAULT_POSTGRES_IMAGE, help="Pinned PostgreSQL image tag.")
    parser.add_argument("--container-name", default=None, help="Optional Docker container name.")
    parser.add_argument("--database", default=DEFAULT_POSTGRES_DB, help="Database created by the Postgres image.")
    parser.add_argument("--user", default=DEFAULT_POSTGRES_USER, help="PostgreSQL superuser inside the container.")
    parser.add_argument(
        "--keep-container",
        action="store_true",
        help="Leave the restored container running for manual inspection. Default tears it down.",
    )
    parser.add_argument(
        "--no-replace-container",
        action="store_true",
        help="Do not remove an existing container with the requested name before starting.",
    )


def add_existing_postgres_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--container-name", required=True, help="Running PostgreSQL container with restored IMDb data.")
    parser.add_argument("--database", default=DEFAULT_POSTGRES_DB, help="PostgreSQL database name.")
    parser.add_argument("--user", default=DEFAULT_POSTGRES_USER, help="PostgreSQL user.")


def add_query_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--queries",
        default=None,
        help="Canonical JOB query directory. Defaults to _project/joinorder/build-inputs/queries.",
    )


def query_dir_from_arg(raw: str | None) -> Path:
    return (
        Path(raw).expanduser().resolve() if raw else repo_root() / "_project" / "joinorder" / "build-inputs" / "queries"
    )


ALLOWED_DANGLING_FKS_SCHEMA_VERSION = 1
FK_DECLARATION_RE = re.compile(
    r"FOREIGN KEY\s*\(\s*(\w+)\s*\)\s*REFERENCES\s*(\w+)\s*\(\s*(\w+)\s*\)",
    re.IGNORECASE,
)


def runtime_manifest_path() -> Path:

    return repo_root() / "benchbox" / "core" / "joinorder" / "data_manifest.toml"


def allowed_dangling_fks_path() -> Path:

    return repo_root() / "_project" / "joinorder" / "allowed_dangling_fks.json"


def job_declared_foreign_keys() -> list[ForeignKey]:

    from benchbox.core.joinorder.schema import JoinOrderSchema

    schema = JoinOrderSchema()
    keys: list[ForeignKey] = []
    for table in schema.get_table_names():
        for declaration in schema.get_table_info(table).get("foreign_keys", []) or []:
            match = FK_DECLARATION_RE.search(declaration)
            if match is None:
                raise JoinOrderBuildError(f"Unparseable FK declaration on {table}: {declaration!r}")
            child_column, parent_table, parent_column = match.groups()
            keys.append(
                ForeignKey(
                    child_table=table,
                    child_column=child_column,
                    parent_table=parent_table,
                    parent_column=parent_column,
                )
            )
    keys.sort(key=lambda fk: (fk.child_table, fk.child_column, fk.parent_table, fk.parent_column))
    return keys


def _require_canonical_parquet(parquet_dir: Path) -> None:
    missing = [table for table in TABLE_NAMES if not (parquet_dir / f"{table}.parquet").exists()]
    if missing:
        raise JoinOrderBuildError(f"Canonical Parquet directory {parquet_dir} is missing tables: {', '.join(missing)}")


def _verify_canonical_parquet_hashes(parquet_dir: Path) -> None:
    with runtime_manifest_path().open("rb") as handle:
        manifest = tomllib.load(handle)
    declared = {
        str(entry.get("name")): (str(entry.get("file")), str(entry.get("sha256")))
        for entry in manifest.get("tables", [])
        if isinstance(entry, dict)
    }
    for table in TABLE_NAMES:
        if table not in declared:
            raise JoinOrderBuildError(f"Runtime manifest has no canonical hash for table {table}")
        filename, expected_hash = declared[table]
        path = parquet_dir / filename
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise JoinOrderBuildError(
                f"Canonical Parquet hash mismatch for {table}: expected {expected_hash}, got {actual_hash}"
            )


def resolve_canonical_parquet_dir(data_dir: str | None) -> Path:

    if data_dir:
        resolved = Path(data_dir).expanduser().resolve()
        _require_canonical_parquet(resolved)
        _verify_canonical_parquet_hashes(resolved)
        return resolved

    from benchbox.core.joinorder.benchmark import JoinOrderBenchmark

    cache = default_build_root() / "canonical-verified"
    cache.mkdir(parents=True, exist_ok=True)
    JoinOrderBenchmark(output_dir=cache).generate_data()
    _require_canonical_parquet(cache)
    return cache


def compute_fk_dangling_census(parquet_dir: Path) -> list[dict[str, Any]]:

    try:
        import duckdb
    except ImportError as exc:
        raise JoinOrderBuildError("duckdb is required in _project/scripts for the FK dangling census") from exc

    census: list[dict[str, Any]] = []
    con = duckdb.connect()
    try:
        for fk in job_declared_foreign_keys():
            child_path = parquet_dir / f"{fk.child_table}.parquet"
            parent_path = parquet_dir / f"{fk.parent_table}.parquet"
            child_col = quote_ident(fk.child_column)
            parent_col = quote_ident(fk.parent_column)
            stats = con.execute(
                f"SELECT count(*) AS child_rows, count({child_col}) AS child_non_null "
                f"FROM read_parquet({duckdb_literal(child_path)})"
            ).fetchone()
            dangling_row = con.execute(
                f"SELECT count(*) FROM read_parquet({duckdb_literal(child_path)}) AS c "
                f"WHERE c.{child_col} IS NOT NULL AND NOT EXISTS ("
                f"SELECT 1 FROM read_parquet({duckdb_literal(parent_path)}) AS p "
                f"WHERE p.{parent_col} = c.{child_col})"
            ).fetchone()
            if stats is None or dangling_row is None:
                raise JoinOrderBuildError(f"Empty census result for {fk.child_table}.{fk.child_column}")
            child_rows, child_non_null, dangling = int(stats[0]), int(stats[1]), int(dangling_row[0])
            census.append(
                {
                    "child_table": fk.child_table,
                    "child_column": fk.child_column,
                    "parent_table": fk.parent_table,
                    "parent_column": fk.parent_column,
                    "child_row_count": child_rows,
                    "child_non_null_count": child_non_null,
                    "dangling_count": dangling,
                }
            )
            print(
                f"fk {fk.child_table}.{fk.child_column} -> {fk.parent_table}.{fk.parent_column}: "
                f"dangling={dangling} non_null={child_non_null} rows={child_rows}",
                flush=True,
            )
    finally:
        con.close()
    return census


def build_allowed_dangling_fks_payload(parquet_dir: Path) -> dict[str, Any]:

    manifest_hash, data_archive_hash = rendered_manifest_identity(runtime_manifest_path())
    return {
        "schema_version": ALLOWED_DANGLING_FKS_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "manifest_hash": manifest_hash,
        "data_archive_hash": data_archive_hash,
        "fk_source": "benchbox/core/joinorder/schema_specs.yaml",
        "generator": "_project/scripts/build_joinorder_data.py fk-dangling-census",
        "foreign_keys": compute_fk_dangling_census(parquet_dir),
    }


def load_allowed_dangling_fks() -> dict[str, Any]:

    artifact = json.loads(allowed_dangling_fks_path().read_text(encoding="utf-8"))
    _, data_archive_hash = rendered_manifest_identity(runtime_manifest_path())
    if artifact.get("data_archive_hash") != data_archive_hash:
        raise JoinOrderBuildError(
            "allowed_dangling_fks.json is stale: its data_archive_hash "
            f"{artifact.get('data_archive_hash')!r} != manifest {data_archive_hash!r}"
        )
    return artifact


def run_fk_dangling_census(args: argparse.Namespace) -> int:
    parquet_dir = resolve_canonical_parquet_dir(args.data_dir)
    serialized = json.dumps(build_allowed_dangling_fks_payload(parquet_dir), indent=2, sort_keys=True) + "\n"
    out = allowed_dangling_fks_path()

    if args.check:
        if not out.exists():
            raise JoinOrderBuildError(f"No committed census to check at {out}")
        if out.read_text(encoding="utf-8") != serialized:
            raise JoinOrderBuildError(
                f"census does NOT match committed artifact ({out}); "
                "regenerate with `fk-dangling-census` and review the change"
            )
        print("census matches committed artifact")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(serialized, encoding="utf-8")
    payload = json.loads(serialized)
    total_dangling = sum(fk["dangling_count"] for fk in payload["foreign_keys"])
    print(f"Wrote {out} ({len(payload['foreign_keys'])} FKs, {total_dangling} total dangling child rows)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download-pgdump", help="Download and verify the Dataverse pg_dump.")
    add_common_args(download)
    download.add_argument("--force", action="store_true", help="Re-download even when the pg_dump already exists.")

    restore = subparsers.add_parser(
        "restore-postgres",
        help="Download, restore into PostgreSQL, validate tables/row counts, and tear down.",
    )
    add_common_args(restore)
    restore.add_argument("--force-download", action="store_true", help="Re-download before restoring.")
    add_restore_args(restore)

    w4 = subparsers.add_parser("w4", help="Run the complete w4 download + PostgreSQL restore validation gate.")
    add_common_args(w4)
    w4.add_argument("--force-download", action="store_true", help="Re-download before restoring.")
    add_restore_args(w4)

    extract = subparsers.add_parser("extract-csv", help="w5: Extract all restored PostgreSQL tables to CSV.")
    add_common_args(extract)
    add_existing_postgres_args(extract)

    convert = subparsers.add_parser("convert-parquet", help="w6: Convert extracted CSV files to zstd Parquet.")
    add_common_args(convert)
    add_existing_postgres_args(convert)

    manifest = subparsers.add_parser("assemble-manifest", help="w7: Assemble data_manifest.toml provenance.")
    add_common_args(manifest)
    add_existing_postgres_args(manifest)

    encoding = subparsers.add_parser("encoding-gate", help="w8: Verify UTF-8 round trip from Postgres to Parquet.")
    add_common_args(encoding)
    add_existing_postgres_args(encoding)
    encoding.add_argument("--sample-size", type=int, default=100)

    predicate = subparsers.add_parser("predicate-gate", help="w9: Verify canonical predicate oracle.")
    add_common_args(predicate)
    add_existing_postgres_args(predicate)
    add_query_dir_arg(predicate)

    import_queries = subparsers.add_parser("import-queries", help="w10: Import canonical gregrahn JOB SQL files.")
    add_common_args(import_queries)
    import_queries.add_argument("--destination", default=None)

    cardinalities = subparsers.add_parser("cardinalities", help="w11: Compute PostgreSQL oracle cardinalities.")
    add_common_args(cardinalities)
    add_existing_postgres_args(cardinalities)
    add_query_dir_arg(cardinalities)
    cardinalities.add_argument("--output", default=None)

    verify_reference = subparsers.add_parser(
        "verify-reference-results",
        help="Verify committed PostgreSQL full-result hashes against a restored source database.",
    )
    add_common_args(verify_reference)
    add_existing_postgres_args(verify_reference)
    add_query_dir_arg(verify_reference)
    verify_reference.add_argument("--reference", default=None)

    fidelity = subparsers.add_parser(
        "verify-conversion-fidelity",
        help="Verify Postgres-to-Parquet row-count, null/empty, integer-width, and UTF-8 fidelity.",
    )
    add_common_args(fidelity)
    add_existing_postgres_args(fidelity)
    fidelity.add_argument("--utf8-sample-size", type=int, default=100)

    logical_content = subparsers.add_parser(
        "verify-logical-content",
        help="Verify canonical CSV rows and Parquet rows have identical logical content hashes.",
    )
    add_common_args(logical_content)
    add_existing_postgres_args(logical_content)

    rebuild_local = subparsers.add_parser(
        "rebuild-local",
        help="Rebuild the Parquet archive from an existing restored PostgreSQL container without staging a release.",
    )
    add_common_args(rebuild_local)
    add_existing_postgres_args(rebuild_local)
    rebuild_local.add_argument("--cardinalities", default=None)

    cross_check = subparsers.add_parser("cross-check", help="w12: Write published-cardinality cross-check report.")
    add_common_args(cross_check)
    cross_check.add_argument("--cardinalities", default=None)
    cross_check.add_argument("--output", default=None)

    package = subparsers.add_parser(
        "package", help="w13: Package Parquets, manifest, license, checksums, cardinalities."
    )
    add_common_args(package)
    package.add_argument("--manifest", default=None)
    package.add_argument("--cardinalities", default=None)

    tiny = subparsers.add_parser("tiny-fixture", help="w14: Generate tiny fixture preserving predicate oracle.")
    add_common_args(tiny)
    add_existing_postgres_args(tiny)
    add_query_dir_arg(tiny)
    tiny.add_argument("--fixture", default=None)
    tiny.add_argument("--sample-per-table", type=int, default=50)

    verify_tiny = subparsers.add_parser("verify-tiny-fixture", help="Verify tiny fixture against canonical queries.")
    verify_tiny.add_argument("--fixture", required=True)
    add_query_dir_arg(verify_tiny)
    verify_tiny.add_argument("--cardinalities", required=True)

    release = subparsers.add_parser("stage-release", help="w15: Stage DRAFT GitHub Release with archive assets.")
    add_common_args(release)

    runtime_manifest = subparsers.add_parser("write-runtime-manifest", help="w16: Write joinorder data_manifest.toml.")
    add_common_args(runtime_manifest)
    add_existing_postgres_args(runtime_manifest)

    foundation = subparsers.add_parser(
        "foundation", help="Run w5-w16 after restoring PostgreSQL from the cached pg_dump."
    )
    add_common_args(foundation)
    foundation.add_argument("--force-download", action="store_true", help="Re-download before restoring.")
    add_restore_args(foundation)

    fk_census = subparsers.add_parser(
        "fk-dangling-census",
        help="Generate/verify the allowed-dangling FK census over the sha256-verified canonical archive.",
    )
    fk_census.add_argument(
        "--data-dir",
        default=None,
        help=(
            "Directory of already-extracted, sha256-verified canonical Parquet files. "
            "Default: fetch and verify the shipped archive (reusing a pre-placed archive if present)."
        ),
    )
    fk_census.add_argument(
        "--check",
        action="store_true",
        help="Recompute the census and compare to the committed artifact instead of writing it.",
    )

    return parser


def run_download(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    artifact = download_pgdump(work_dir, force=bool(args.force))
    print_source_summary(artifact, build_manifest_path(work_dir))
    return 0


def run_restore(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    artifact = download_pgdump(work_dir, force=bool(args.force_download))
    print_source_summary(artifact, build_manifest_path(work_dir))
    validation = restore_pgdump(
        artifact,
        work_dir=work_dir,
        postgres_image=args.postgres_image,
        container_name=args.container_name,
        database=args.database,
        user=args.user,
        keep_container=bool(args.keep_container),
        replace_existing=not bool(args.no_replace_container),
    )
    print_restore_summary(validation, build_manifest_path(work_dir))
    return 0


def run_extract_csv(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    files = extract_csv_files(
        work_dir=work_dir,
        container_name=args.container_name,
        database=args.database,
        user=args.user,
    )
    print(f"Extracted {len(files)} CSV files to {work_dir / 'csv'}")
    return 0


def run_convert_parquet(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    schema = postgres_schema(container_name=args.container_name, database=args.database, user=args.user)
    files = convert_csv_to_parquet(work_dir=work_dir, schema=schema)
    print(f"Converted {len(files)} Parquet files to {work_dir / 'parquet'}")
    return 0


def run_assemble_manifest(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    schema = postgres_schema(container_name=args.container_name, database=args.database, user=args.user)
    table_files = load_table_files_from_manifest(work_dir, "parquet")
    manifest_path = assemble_manifest(work_dir=work_dir, schema=schema, table_files=table_files)
    print(f"Assembled manifest: {manifest_path}")
    return 0


def run_encoding_gate(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    result = validate_encoding_round_trip(
        work_dir=work_dir,
        query_sample_size=args.sample_size,
        container_name=args.container_name,
        database=args.database,
        user=args.user,
    )
    print(f"Encoding round-trip gate passed for {len(result['samples'])} sampled column groups")
    return 0


def run_predicate_gate(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    report = validate_predicate_domain(
        work_dir=work_dir,
        query_dir=query_dir_from_arg(args.queries),
        container_name=args.container_name,
        database=args.database,
        user=args.user,
    )
    print(
        "Predicate-domain gate passed for "
        f"{len(report['query_underlying_row_counts'])} queries "
        f"({len(report['known_zero_underlying_queries'])} canonical known-zero underlying predicates)"
    )
    return 0


def run_import_queries(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    destination = Path(args.destination).expanduser().resolve() if args.destination else None
    dest_dir = import_canonical_queries(work_dir=work_dir, destination=destination)
    print(f"Imported {len(query_files(dest_dir))} canonical queries to {dest_dir}")
    return 0


def run_cardinalities(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    output = Path(args.output).expanduser().resolve() if args.output else None
    out = compute_reference_cardinalities(
        work_dir=work_dir,
        query_dir=query_dir_from_arg(args.queries),
        output_path=output,
        container_name=args.container_name,
        database=args.database,
        user=args.user,
    )
    print(f"Wrote reference cardinalities: {out}")
    return 0


def run_verify_reference_results(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    reference = (
        Path(args.reference).expanduser().resolve()
        if args.reference
        else repo_root() / "_project" / "joinorder" / "reference_cardinalities.json"
    )
    report = verify_reference_results(
        work_dir=work_dir,
        query_dir=query_dir_from_arg(args.queries),
        reference_path=reference,
        container_name=args.container_name,
        database=args.database,
        user=args.user,
    )
    print(
        "Reference result verification passed: "
        f"{report['full_result_hashes_verified']}/{report['verified_query_count']} aggregate row hashes matched"
    )
    return 0


def run_verify_conversion_fidelity(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    report = validate_conversion_fidelity(
        work_dir=work_dir,
        container_name=args.container_name,
        database=args.database,
        user=args.user,
        utf8_sample_size=args.utf8_sample_size,
    )
    print(
        "Conversion fidelity verification passed: "
        f"{len(report['row_count_checks'])} row-count checks, "
        f"{len(report['string_null_empty_checks'])} null/empty checks, "
        f"{len(report['integer_width_checks'])} integer-width checks, "
        f"{len(report['utf8_round_trip_checks'])} UTF-8 column groups"
    )
    return 0


def run_verify_logical_content(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    schema = postgres_schema(container_name=args.container_name, database=args.database, user=args.user)
    report = verify_logical_content_hashes(work_dir=work_dir, schema=schema)
    total_rows = sum(int(table["row_count"]) for table in report.tables)
    print(
        "logical_content_rebuild=PASS "
        f"tables={len(report.tables)} rows={total_rows} aggregate_hash={report.aggregate_hash}"
    )
    return 0


def run_rebuild_local(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    schema = postgres_schema(container_name=args.container_name, database=args.database, user=args.user)
    parquet_files, logical_report = extract_and_convert_parquet_streaming(
        work_dir=work_dir,
        schema=schema,
        container_name=args.container_name,
        database=args.database,
        user=args.user,
    )
    manifest_path = assemble_manifest(work_dir=work_dir, schema=schema, table_files=parquet_files)
    cardinalities = (
        Path(args.cardinalities).expanduser().resolve()
        if args.cardinalities
        else repo_root() / "_project" / "joinorder" / "reference_cardinalities.json"
    )
    archive_path, sha_path = package_archive(
        work_dir=work_dir,
        manifest_path=manifest_path,
        cardinalities_path=cardinalities,
    )
    print(f"Local rebuild archive: {archive_path}")
    print(f"Local rebuild sha256: {sha_path.read_text(encoding='utf-8').split()[0]}")
    print(
        "logical_content_rebuild=PASS "
        f"tables={len(logical_report.tables)} aggregate_hash={logical_report.aggregate_hash}"
    )
    return 0


def run_cross_check(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    cardinalities = (
        Path(args.cardinalities).expanduser().resolve()
        if args.cardinalities
        else repo_root() / "_project" / "joinorder" / "reference_cardinalities.json"
    )
    output = Path(args.output).expanduser().resolve() if args.output else None
    out = write_cardinality_cross_check(work_dir=work_dir, cardinalities_path=cardinalities, output_path=output)
    print(f"Wrote cardinality cross-check report: {out}")
    return 0


def run_package(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    manifest_path = (
        Path(args.manifest).expanduser().resolve() if args.manifest else work_dir / "manifest" / "data_manifest.toml"
    )
    cardinalities = (
        Path(args.cardinalities).expanduser().resolve()
        if args.cardinalities
        else repo_root() / "_project" / "joinorder" / "reference_cardinalities.json"
    )
    archive_path, sha_path = package_archive(
        work_dir=work_dir, manifest_path=manifest_path, cardinalities_path=cardinalities
    )
    print(f"Packaged archive: {archive_path}")
    print(f"Archive sha256:   {sha_path}")
    return 0


def run_tiny_fixture(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    schema = postgres_schema(container_name=args.container_name, database=args.database, user=args.user)
    fixture_dir = (
        Path(args.fixture).expanduser().resolve()
        if args.fixture
        else repo_root() / "tests" / "fixtures" / "joinorder_canonical_tiny"
    )
    export_tiny_fixture(
        work_dir=work_dir,
        query_dir=query_dir_from_arg(args.queries),
        output_dir=fixture_dir,
        schema=schema,
        container_name=args.container_name,
        database=args.database,
        user=args.user,
        sample_per_table=args.sample_per_table,
    )
    print(f"Wrote tiny fixture: {fixture_dir}")
    return 0


def run_verify_tiny_fixture(args: argparse.Namespace) -> int:
    verify_tiny_fixture(
        fixture_dir=Path(args.fixture).expanduser().resolve(),
        query_dir=query_dir_from_arg(args.queries),
        cardinalities_path=Path(args.cardinalities).expanduser().resolve(),
    )
    print("Tiny fixture verified: all 113 query predicate cardinalities validated")
    return 0


def run_stage_release(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    archive = load_build_manifest(work_dir).get("archive", {})
    archive_path = Path(str(archive.get("path", work_dir / "archive" / ARCHIVE_NAME)))
    sha_path = Path(str(archive.get("sha256_path", work_dir / "archive" / ARCHIVE_SHA_NAME)))
    notes_path = write_release_notes(work_dir=work_dir, archive_path=archive_path, archive_sha_path=sha_path)
    stage_draft_release(work_dir=work_dir, archive_path=archive_path, archive_sha_path=sha_path, notes_path=notes_path)
    print(f"Draft release staged: {GITHUB_RELEASE_TAG}")
    return 0


def run_write_runtime_manifest(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    schema = postgres_schema(container_name=args.container_name, database=args.database, user=args.user)
    out = write_runtime_manifest(work_dir=work_dir, schema=schema)
    print(f"Wrote runtime manifest: {out}")
    return 0


def run_foundation(args: argparse.Namespace) -> int:
    work_dir = work_dir_from_arg(args.work_dir)
    artifact = download_pgdump(work_dir, force=bool(args.force_download))
    print_source_summary(artifact, build_manifest_path(work_dir))
    actual_container_name = args.container_name or default_container_name()
    validation = restore_pgdump(
        artifact,
        work_dir=work_dir,
        postgres_image=args.postgres_image,
        container_name=actual_container_name,
        database=args.database,
        user=args.user,
        keep_container=True,
        replace_existing=not bool(args.no_replace_container),
    )
    print_restore_summary(validation, build_manifest_path(work_dir))
    try:
        schema = postgres_schema(container_name=actual_container_name, database=args.database, user=args.user)
        parquet_files, _logical_report = extract_and_convert_parquet_streaming(
            work_dir=work_dir,
            schema=schema,
            container_name=actual_container_name,
            database=args.database,
            user=args.user,
        )
        manifest_path = assemble_manifest(work_dir=work_dir, schema=schema, table_files=parquet_files)
        query_dir = import_canonical_queries(work_dir=work_dir)
        validate_encoding_round_trip(
            work_dir=work_dir,
            query_sample_size=100,
            container_name=actual_container_name,
            database=args.database,
            user=args.user,
        )
        validate_predicate_domain(
            work_dir=work_dir,
            query_dir=query_dir,
            container_name=actual_container_name,
            database=args.database,
            user=args.user,
        )
        cardinalities = compute_reference_cardinalities(
            work_dir=work_dir,
            query_dir=query_dir,
            output_path=None,
            container_name=actual_container_name,
            database=args.database,
            user=args.user,
        )
        write_cardinality_cross_check(work_dir=work_dir, cardinalities_path=cardinalities)
        archive_path, sha_path = package_archive(
            work_dir=work_dir,
            manifest_path=manifest_path,
            cardinalities_path=cardinalities,
        )
        write_runtime_manifest(work_dir=work_dir, schema=schema)
        export_tiny_fixture(
            work_dir=work_dir,
            query_dir=query_dir,
            output_dir=repo_root() / "tests" / "fixtures" / "joinorder_canonical_tiny",
            schema=schema,
            container_name=actual_container_name,
            database=args.database,
            user=args.user,
        )
        notes = write_release_notes(work_dir=work_dir, archive_path=archive_path, archive_sha_path=sha_path)
        stage_draft_release(work_dir=work_dir, archive_path=archive_path, archive_sha_path=sha_path, notes_path=notes)
    finally:
        if not args.keep_container:
            remove_container(actual_container_name)
    print(f"Foundation w5-w16 complete. Manifest: {build_manifest_path(work_dir)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "download-pgdump": run_download,
        "restore-postgres": run_restore,
        "w4": run_restore,
        "extract-csv": run_extract_csv,
        "convert-parquet": run_convert_parquet,
        "assemble-manifest": run_assemble_manifest,
        "encoding-gate": run_encoding_gate,
        "predicate-gate": run_predicate_gate,
        "import-queries": run_import_queries,
        "cardinalities": run_cardinalities,
        "verify-reference-results": run_verify_reference_results,
        "verify-conversion-fidelity": run_verify_conversion_fidelity,
        "verify-logical-content": run_verify_logical_content,
        "rebuild-local": run_rebuild_local,
        "cross-check": run_cross_check,
        "package": run_package,
        "tiny-fixture": run_tiny_fixture,
        "verify-tiny-fixture": run_verify_tiny_fixture,
        "stage-release": run_stage_release,
        "write-runtime-manifest": run_write_runtime_manifest,
        "foundation": run_foundation,
        "fk-dangling-census": run_fk_dangling_census,
    }
    try:
        handler = handlers.get(args.command)
        if handler is not None:
            return handler(args)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except JoinOrderBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
