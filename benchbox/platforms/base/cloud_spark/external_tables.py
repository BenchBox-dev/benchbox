# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.base.cloud_spark.mixins import SparkTableFormat
from benchbox.platforms.base.phase_tracking import _resolve_benchmark_table_names
from benchbox.utils.clock import elapsed_seconds, mono_time

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")

_ALLOWED_STAGING_SCHEMES = ("s3://", "s3a://", "gs://", "abfss://", "file://")

_SOURCE_FORMAT_BY_EXTENSION = {
    ".parquet": "parquet",
    ".csv": "csv",
    ".tbl": "tbl",
    ".json": "json",
}


class SparkExternalTableMixin:
    supports_external_tables: bool = True
    external_staging_instruction: str = "Set the adapter's s3_staging_dir (AWS) or gcs_staging_dir (Dataproc)."

    def _external_staging_root(self) -> str | None:
        return getattr(self, "s3_staging_dir", None) or getattr(self, "gcs_staging_dir", None)

    def validate_external_table_requirements(self) -> None:
        root = self._external_staging_root()
        platform = getattr(self, "platform_name", type(self).__name__)
        if not root:
            raise ValueError(
                f"{platform} external mode requires a staging location. {self.external_staging_instruction}"
            )
        normalized = str(root).strip()
        lowered = normalized.lower()
        if not lowered.startswith(_ALLOWED_STAGING_SCHEMES):
            raise ValueError(
                f"{platform} external mode staging location must use a supported staging URI "
                f"({', '.join(_ALLOWED_STAGING_SCHEMES)}), got {root!r}."
            )
        remainder = normalized.split("://", 1)[1] if "://" in normalized else ""
        if not remainder or remainder.strip("/") == "":
            raise ValueError(
                f"{platform} external mode staging location must include a bucket or container path, got {root!r}."
            )

    def _external_table_format(self) -> str:
        requested = getattr(self, "requested_table_format", None)
        configured = getattr(self, "table_format", None)
        file_format = str(requested or configured or "parquet").lower()
        allowed = {member.value for member in SparkTableFormat} | {"csv"}
        if file_format not in allowed:
            raise ConfigurationError(
                f"Unsupported external table format {file_format!r}: expected one of {sorted(allowed)}."
            )
        return file_format

    @staticmethod
    def _detect_table_source_format(source_dir: Path, table: str) -> str | None:
        candidates = [c for c in sorted(source_dir.glob(f"{table}*")) if c.is_file() or c.is_dir()]
        if not candidates:
            return None
        for candidate in candidates:
            if candidate.is_dir():
                if (candidate / "_delta_log").is_dir():
                    return "delta"
                metadata_dir = candidate / "metadata"
                if metadata_dir.is_dir() and (
                    (metadata_dir / "version-hint.text").exists() or list(metadata_dir.glob("*.metadata.json"))
                ):
                    return "iceberg"
        recognized = {
            _SOURCE_FORMAT_BY_EXTENSION[candidate.suffix.lower()]
            for candidate in candidates
            if candidate.is_file() and candidate.suffix.lower() in _SOURCE_FORMAT_BY_EXTENSION
        }
        if len(recognized) == 1:
            return next(iter(recognized))
        return None

    def _resolve_external_source_format(self, source_dir: Path, tables: list[str]) -> str:
        requested = getattr(self, "requested_table_format", None)
        detected = {table: self._detect_table_source_format(source_dir, table) for table in tables}
        known = {table: fmt for table, fmt in detected.items() if fmt is not None}
        if requested is not None:
            mismatched = {table: fmt for table, fmt in known.items() if fmt != str(requested).lower()}
            if mismatched:
                detail = ", ".join(f"{table} ({fmt})" for table, fmt in sorted(mismatched.items()))
                raise ConfigurationError(
                    f"External table format {str(requested).lower()!r} does not match staged sources: "
                    f"{detail}. Provide sources in the requested format or drop --table-format."
                )
            return self._external_table_format()
        distinct = set(known.values())
        if not distinct:
            return self._external_table_format()
        if len(distinct) > 1:
            detail = ", ".join(f"{table} ({fmt})" for table, fmt in sorted(known.items()))
            raise ConfigurationError(
                f"External table sources have mixed formats: {detail}. "
                "Stage a single format or pass --table-format explicitly."
            )
        only = next(iter(distinct))
        if only in {"parquet", "delta", "iceberg"}:
            return only
        if only == "csv":
            raise ConfigurationError(
                "External table sources are CSV; pass --table-format csv explicitly to register "
                "them (headers required), or stage Parquet sources instead."
            )
        raise ConfigurationError(
            f"External table sources are {only.upper()} files, which Spark cannot register; "
            "stage Parquet/CSV/Delta/Iceberg sources instead."
        )

    def _staged_dataset_fingerprint(self, benchmark: Any, file_format: str, source_dir: Path | None = None) -> str:
        name = getattr(benchmark, "name", None) or type(benchmark).__name__
        scale = getattr(benchmark, "scale_factor", getattr(benchmark, "scale", "unknown"))
        seed = getattr(benchmark, "seed", "unknown")
        tables = ",".join(sorted(_resolve_benchmark_table_names(benchmark)))
        content = self._staged_source_identity(source_dir, _resolve_benchmark_table_names(benchmark), file_format)
        raw = f"{name}|{scale}|{seed}|{file_format}|{tables}|{content}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _staged_source_identity(source_dir: Path | None, tables: list[str], file_format: str) -> str:
        if source_dir is None:
            return "unknown-source"
        parts = []
        for table in sorted(tables):
            matches = sorted(source_dir.glob(f"{table}*.{file_format}")) or sorted(source_dir.glob(f"{table}*"))
            for path in matches:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                parts.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16] if parts else "empty-source"

    def _register_external_table(self, table_name: str, location: str, file_format: str) -> None:
        raise NotImplementedError(
            f"{type(self).__name__} must implement _register_external_table() to support --table-mode external."
        )

    @staticmethod
    def _validate_external_identifier(value: str, label: str) -> str:
        if not isinstance(value, str) or not _IDENTIFIER_RE.match(value):
            raise ValueError(f"Invalid external table {label} {value!r}: must match {_IDENTIFIER_RE.pattern}.")
        return value

    @staticmethod
    def _escape_external_location(location: str) -> str:
        return str(location).replace("'", "''")

    def _external_count_sql(self, table_name: str) -> str:
        database = getattr(self, "database", "") or ""
        self._validate_external_identifier(table_name, "table name")
        if database:
            self._validate_external_identifier(database, "database name")
            qualified = f"{database}.{table_name}"
        else:
            qualified = table_name
        return f"SELECT COUNT(*) AS row_count FROM {qualified}"

    def _count_external_table_rows(self, connection: Any, table_name: str) -> int:
        result = self.execute_query(
            connection,
            self._external_count_sql(table_name),
            query_id=f"external-count-{table_name}",
        )
        if result.get("status") != "SUCCESS":
            raise RuntimeError(
                f"External table row count failed for '{table_name}': {result.get('error') or result.get('status')}"
            )
        rows = result.get("results") or []
        if not rows:
            raise RuntimeError(f"External table row count returned no rows for '{table_name}'")
        for row in rows:
            values = list(row.values()) if isinstance(row, dict) else [row]
            if not values:
                raise RuntimeError(f"External table row count returned an empty row for '{table_name}'")
            for value in values:
                if value is None:
                    continue
                if isinstance(value, bool):
                    continue
                if isinstance(value, int):
                    return value
                if isinstance(value, float):
                    if value.is_integer():
                        return int(value)
                    continue
                text = str(value).strip().replace(",", "")
                if not text:
                    continue
                candidates = [text] if "|" not in text else [part.strip() for part in text.split("|")]
                for candidate in candidates:
                    if not candidate:
                        continue
                    try:
                        return int(candidate)
                    except (ValueError, TypeError):
                        continue
        raise RuntimeError(f"External table row count returned no parseable integer for '{table_name}': {rows!r}")

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        self.validate_external_table_requirements()
        start_time = mono_time()
        source_path = Path(data_dir)
        tables = _resolve_benchmark_table_names(benchmark)
        if not tables:
            raise ConfigurationError("No benchmark tables resolved for external table mode.")
        if not source_path.exists():
            raise ConfigurationError(f"Source directory not found: {data_dir}")
        file_format = self._resolve_external_source_format(source_path, tables)
        self.external_format = file_format

        staging = getattr(self, "_staging", None)
        if staging is None:
            platform = getattr(self, "platform_name", type(self).__name__)
            raise ConfigurationError(
                f"{platform} external mode requires an initialized staging client; "
                "check the staging directory configuration."
            )

        self.create_schema(benchmark, connection)

        fingerprint = self._staged_dataset_fingerprint(benchmark, file_format, source_path)
        if staging.tables_exist(tables, file_format, fingerprint):
            logger.info("Tables already exist in staging, skipping upload")
            table_uris = {table: staging.get_table_uri(table) for table in tables}
        else:
            logger.info(f"Uploading {len(tables)} tables to staging for external mode")
            uploaded = staging.upload_tables(
                tables=tables,
                source_dir=source_path,
                file_format=file_format,
                fingerprint=fingerprint,
            )
            uploaded = uploaded or {}
            for table in tables:
                if table not in uploaded:
                    if not staging.table_has_fingerprint(table, fingerprint):
                        raise ConfigurationError(
                            f"No source files found for table '{table}' and the staged copy does not "
                            f"match the current dataset (fingerprint {fingerprint}); refusing to register "
                            "stale data. Clear the staging root or stage the missing table, then retry."
                        )
                    logger.warning(f"No source files uploaded for table '{table}'; registering staged location as-is")
            table_uris = {table: uploaded.get(table) or staging.get_table_uri(table) for table in tables}

        table_stats: dict[str, int] = {}
        for table in tables:
            location = table_uris[table]
            self._register_external_table(table, location, file_format)
            table_stats[table] = self._count_external_table_rows(connection, table)
            logger.info(f"Registered external table '{table}' ({table_stats[table]:,} rows)")

        return table_stats, elapsed_seconds(start_time), {"table_uris": table_uris}
