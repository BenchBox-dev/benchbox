# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from benchbox.core.config_inheritance import resolve_dialect_for_query_translation
from benchbox.core.deployment import deployment_class, select_adapter_deployment
from benchbox.platforms.base.data_loading import escape_sql_string_literal
from benchbox.utils.cloud_storage import get_cloud_path_info, is_cloud_path
from benchbox.utils.dialect_utils import translate_ddl

from .duckdb import DuckDBAdapter, DuckDBConnectionWrapper
from .postgresql import _build_postgres_connection_kwargs

logger = logging.getLogger(__name__)

_VALID_CATALOGS: tuple[str, ...] = ("duckdb", "sqlite", "postgres")

_S3_SECRET_NAME = "benchbox_ducklake_s3"
_GCS_SECRET_NAME = "benchbox_ducklake_gcs"
_AZURE_SECRET_NAME = "benchbox_ducklake_azure"
_RUN_IDENTITY_TABLE = "__benchbox_run_identity"

_CREDENTIAL_CONFIG_KEYS = frozenset(
    {
        "pg_user",
        "pg_password",
        "s3_key_id",
        "s3_secret",
        "gcs_key_id",
        "gcs_secret",
        "azure_connection_string",
        "azure_account_name",
    }
)

_REDACTED = "****"

_PASSWORD_COMPONENT_RE = re.compile(r"(password\s*=\s*).*?(?=\s+port\s*=|$)", flags=re.IGNORECASE | re.MULTILINE)
_S3_SECRET_CLAUSE_RE = re.compile(r"(\bSECRET\s+)'(?:[^']|'')*'", flags=re.IGNORECASE)
_SECRET_KEY_CLAUSE_RE = re.compile(
    r"(\b(?:KEY_ID|CONNECTION_STRING|ACCOUNT_NAME)\s+)'(?:[^']|'')*'", flags=re.IGNORECASE
)


def _redact_secrets(message: str, *secrets: str | None) -> str:
    redacted: str = message
    encodings: set[str] = set()
    for secret in secrets:
        if not secret:
            continue
        quoted = _libpq_quote_value(secret)
        encodings.update({secret, quoted, escape_sql_string_literal(quoted), escape_sql_string_literal(secret)})
    longest_first: list[str] = sorted(encodings, key=len, reverse=True)
    for encoding in longest_first:
        redacted = redacted.replace(encoding, _REDACTED)
    redacted = _PASSWORD_COMPONENT_RE.sub(rf"\1{_REDACTED}", redacted)
    redacted = _S3_SECRET_CLAUSE_RE.sub(rf"\1{_REDACTED}", redacted)
    return _SECRET_KEY_CLAUSE_RE.sub(rf"\1{_REDACTED}", redacted)


_DEPLOYMENT_MODE_AXES: dict[str, tuple[str | None, bool | None]] = {
    "local": ("duckdb", False),
    "local_catalog_s3": ("duckdb", True),
    "postgres_catalog": ("postgres", False),
    "postgres_catalog_s3": ("postgres", True),
}


def _resolve_catalog_with_deployment_mode(deployment_mode: Any, catalog: Any) -> Any:
    axes = _DEPLOYMENT_MODE_AXES.get(str(deployment_mode or "").strip().lower())
    if axes is None:
        return catalog
    mode_catalog, _ = axes
    if mode_catalog is None:
        return catalog
    if catalog is None:
        return mode_catalog
    if str(catalog).strip().lower() != mode_catalog:
        logger.warning(
            "DuckLake deployment mode %r implies catalog=%s, but catalog=%s was requested explicitly; "
            "using the explicit catalog=%s.",
            deployment_mode,
            mode_catalog,
            catalog,
            catalog,
        )
    return catalog


def _warn_if_deployment_mode_contradicts_storage(deployment_mode: Any, data_path: Any) -> None:
    axes = _DEPLOYMENT_MODE_AXES.get(str(deployment_mode or "").strip().lower())
    if axes is None or data_path is None:
        return
    _, mode_is_cloud = axes
    if mode_is_cloud is None:
        return
    actual_is_cloud = is_cloud_path(str(data_path))
    if actual_is_cloud is not mode_is_cloud:
        expected = "a cloud data_path" if mode_is_cloud else "a local data_path"
        logger.warning(
            "DuckLake deployment mode %r implies %s, but data_path=%s was given; using it as-is. "
            "Pass a matching --platform-option data_path to run the deployment you selected.",
            deployment_mode,
            expected,
            data_path,
        )


def _libpq_quote_value(value: str) -> str:
    if value == "" or any(ch.isspace() for ch in value) or "'" in value or "\\" in value:
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"
    return value


_MIN_DUCKDB_VERSION_FOR_DUCKLAKE: tuple[int, int] = (1, 3)

_VERSION_PREFIX_RE = re.compile(r"(\d+)\.(\d+)")


def _parse_duckdb_major_minor(version: Any) -> tuple[int, int] | None:
    if not version:
        return None
    text = str(version).strip()
    if text.startswith("v") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    match = _VERSION_PREFIX_RE.match(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _duckdb_version_supports_ducklake(version: Any) -> bool:
    parsed = _parse_duckdb_major_minor(version)
    if parsed is None:
        return False
    return parsed >= _MIN_DUCKDB_VERSION_FOR_DUCKLAKE


class _DuckLakeCursorConnection:
    def __init__(self, connection: Any, catalog: str = "lake") -> None:
        self._connection = connection
        self._ducklake_catalog = catalog

    def cursor(self) -> Any:
        cur = self._connection.cursor()
        cur.execute(f"USE {self._ducklake_catalog}")
        return cur

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _unwrap_duckdb_connection(connection: Any) -> Any:
    conn = connection
    while isinstance(conn, (_DuckLakeCursorConnection, DuckDBConnectionWrapper)):
        conn = conn._connection
    return conn


class DuckLakeAdapter(DuckDBAdapter):
    operation_platform_key = "ducklake"

    operation_platform_fallback_key = "duckdb"

    plan_capture_phase_eligible = True
    index_ddl_unsupported_reason = "ducklake: CREATE INDEX unsupported"

    @property
    def platform_name(self) -> str:
        return "DuckLake"

    def get_tuning_introspector(self) -> None:
        return None

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument_group"):
            return
        try:
            ducklake_group = parser.add_argument_group("DuckLake Arguments")
            ducklake_group.add_argument(
                "--ducklake-metadata-path",
                dest="ducklake_metadata_path",
                type=str,
                help="Path to the DuckLake catalog metadata file (.ducklake)",
            )
            ducklake_group.add_argument(
                "--ducklake-data-path",
                dest="ducklake_data_path",
                type=str,
                help="Path to the DuckLake Parquet data directory (local path or cloud URI: s3://, gs://, az://)",
            )
            ducklake_group.add_argument(
                "--ducklake-catalog",
                dest="ducklake_catalog",
                type=str,
                choices=_VALID_CATALOGS,
                help="DuckLake catalog backend: duckdb, sqlite, or postgres (default: duckdb). "
                "The --platform-option catalog=... form is the primary interface; PostgreSQL "
                "catalog connection params (pg_host/pg_port/pg_database/pg_user/pg_password) and "
                "cloud credentials (s3_key_id/s3_secret/s3_region, gcs_key_id/gcs_secret, "
                "azure_connection_string/azure_account_name) are only available via "
                "--platform-option.",
            )
        except argparse.ArgumentError as exc:
            logger.debug("Failed to register DuckLake CLI arguments: %s", exc)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> DuckLakeAdapter:
        from benchbox.utils.database_naming import generate_database_filename
        from benchbox.utils.path_utils import get_benchmark_runs_databases_path

        def _resolve_option(key: str, default: Any = None) -> Any:
            if key in config and config[key] is not None:
                return config[key]
            return (config.get("options") or {}).get(key, default)

        adapter_config: dict[str, Any] = {}

        metadata_path = config.get("ducklake_metadata_path") or _resolve_option("metadata_path")
        data_path = config.get("ducklake_data_path") or _resolve_option("data_path")

        if not metadata_path or not data_path:
            if config.get("output_dir"):
                data_dir = get_benchmark_runs_databases_path(
                    config["benchmark"],
                    config["scale_factor"],
                    base_dir=Path(config["output_dir"]) / "databases",
                )
            else:
                data_dir = get_benchmark_runs_databases_path(config["benchmark"], config["scale_factor"])

            db_filename = generate_database_filename(
                benchmark_name=config["benchmark"],
                scale_factor=config["scale_factor"],
                platform="ducklake",
                tuning_config=config.get("tuning_config"),
            )

            if not metadata_path:
                metadata_path = str(data_dir / db_filename)
            if not data_path:
                data_path = str(data_dir / "ducklake_data" / Path(db_filename).stem)

        adapter_config["metadata_path"] = metadata_path
        adapter_config["data_path"] = data_path
        for key in ("benchmark", "scale_factor"):
            if key in config:
                adapter_config[key] = config[key]

        adapter_config["memory_limit"] = config.get("memory_limit", "4GB")
        adapter_config["force_recreate"] = bool(config.get("force") or _resolve_option("force_recreate", False))
        for key in [
            "tuning_config",
            "tuning_enabled",
            "unified_tuning_configuration",
            "tuning_source",
            "tuning_source_file",
            "verbose_enabled",
            "very_verbose",
        ]:
            if key in config:
                adapter_config[key] = config[key]
        for key in [
            "driver_package",
            "driver_version",
            "driver_version_requested",
            "driver_version_resolved",
            "driver_version_actual",
            "driver_runtime_strategy",
            "driver_runtime_path",
            "driver_runtime_python_executable",
            "driver_auto_install",
            "driver_auto_install_used",
        ]:
            if key in config:
                adapter_config[key] = config[key]

        catalog = config.get("ducklake_catalog") or _resolve_option("catalog")

        deployment_mode = config.get("deployment_mode") or _resolve_option("deployment_mode")
        catalog = _resolve_catalog_with_deployment_mode(deployment_mode, catalog)
        _warn_if_deployment_mode_contradicts_storage(deployment_mode, data_path)
        adapter_config["deployment_mode"] = deployment_mode

        if catalog is not None:
            adapter_config["catalog"] = catalog

        for key in ("pg_host", "pg_port", "pg_database", "pg_user", "pg_password"):
            value = _resolve_option(key)
            if value is not None:
                adapter_config[key] = value

        for key in (
            "s3_key_id",
            "s3_secret",
            "s3_region",
            "gcs_key_id",
            "gcs_secret",
            "azure_connection_string",
            "azure_account_name",
        ):
            value = _resolve_option(key)
            if value is not None:
                adapter_config[key] = value

        return cls(**adapter_config)

    def __init__(self, **config):
        super().__init__(**config)
        self._expected_benchmark = config.get("benchmark")
        self.deployment_mode = select_adapter_deployment("ducklake", config, None)
        self.deployment_selected = self.deployment_mode
        self.deployment_selected_class = (
            deployment_class("ducklake", self.deployment_mode) if self.deployment_mode else None
        )
        self._expected_scale_factor = config.get("scale_factor")

        metadata_path = config.get("metadata_path")
        data_path = config.get("data_path")
        if not metadata_path or not data_path:
            from benchbox.utils.path_utils import get_benchmark_runs_databases_path

            fallback_dir = get_benchmark_runs_databases_path("ducklake", 1.0)
            metadata_path = metadata_path or str(fallback_dir / "ducklake.ducklake")
            data_path = data_path or str(fallback_dir / "ducklake_data" / "default")

        self.catalog = self._validate_catalog(config.get("catalog", "duckdb"))

        self.metadata_path = Path(metadata_path)
        if self.catalog == "sqlite" and self.metadata_path.suffix == ".ducklake":
            self.metadata_path = self.metadata_path.with_suffix(".sqlite")

        self._data_path_is_cloud = is_cloud_path(str(data_path))
        self.data_path = str(data_path) if self._data_path_is_cloud else Path(data_path)

        self.pg_host: str | None = None
        self.pg_port: int | None = None
        self.pg_user: str | None = None
        self.pg_password: str | None = None
        self.pg_database: str | None = None
        if self.catalog == "postgres":
            pg_overrides = {
                key: value
                for key, value in (
                    ("host", config.get("pg_host")),
                    ("port", config.get("pg_port")),
                    ("username", config.get("pg_user")),
                    ("password", config.get("pg_password")),
                )
                if value is not None
            }
            pg_conn = _build_postgres_connection_kwargs(pg_overrides)
            self.pg_host = pg_conn["host"]
            self.pg_port = int(pg_conn["port"])
            self.pg_user = pg_conn["username"]
            self.pg_password = pg_conn["password"]
            self.pg_database = config.get("pg_database") or "ducklake_catalog"

        self.s3_key_id = config.get("s3_key_id")
        self.s3_secret = config.get("s3_secret")
        self.s3_region = config.get("s3_region")

        self.gcs_key_id = config.get("gcs_key_id")
        self.gcs_secret = config.get("gcs_secret")

        self.azure_connection_string = config.get("azure_connection_string")
        self.azure_account_name = config.get("azure_account_name")

    @staticmethod
    def _validate_catalog(catalog: Any) -> str:
        normalized = str(catalog or "duckdb").strip().lower()
        if normalized not in _VALID_CATALOGS:
            raise ValueError(
                f"Unsupported DuckLake catalog backend: {catalog!r}. Supported values: {', '.join(_VALID_CATALOGS)}."
            )
        return normalized

    def _build_postgres_connstring(self) -> str:
        components: list[tuple[str, Any]] = [
            ("dbname", self.pg_database),
            ("host", self.pg_host),
            ("user", self.pg_user),
            ("password", self.pg_password),
            ("port", self.pg_port),
        ]
        parts = [f"{key}={_libpq_quote_value(str(value))}" for key, value in components if value not in (None, "")]
        return " ".join(parts)

    def _build_catalog_attach_target(self) -> str:
        if self.catalog == "duckdb":
            return str(self.metadata_path)
        if self.catalog == "sqlite":
            return f"sqlite:{self.metadata_path}"
        if self.catalog == "postgres":
            return f"postgres:{self._build_postgres_connstring()}"
        raise ValueError(f"Unsupported DuckLake catalog backend: {self.catalog!r}")  # pragma: no cover

    def _build_s3_secret_sql(self) -> str:
        if self.s3_key_id and self.s3_secret:
            parts = [
                f"KEY_ID '{escape_sql_string_literal(self.s3_key_id)}'",
                f"SECRET '{escape_sql_string_literal(self.s3_secret)}'",
            ]
            if self.s3_region:
                parts.append(f"REGION '{escape_sql_string_literal(self.s3_region)}'")
            return f"CREATE OR REPLACE SECRET {_S3_SECRET_NAME} (TYPE s3, {', '.join(parts)})"
        return f"CREATE OR REPLACE SECRET {_S3_SECRET_NAME} (TYPE s3, PROVIDER credential_chain)"

    def _build_gcs_secret_sql(self) -> str:
        if not (self.gcs_key_id and self.gcs_secret):
            raise ValueError(
                "DuckLake GCS DATA_PATH requires HMAC credentials: pass both "
                "--platform-option gcs_key_id=<hmac-access-id> and "
                "--platform-option gcs_secret=<hmac-secret> (Google Cloud "
                "Storage interoperability keys, not service-account keys)."
            )
        return (
            f"CREATE OR REPLACE SECRET {_GCS_SECRET_NAME} (TYPE gcs, "
            f"KEY_ID '{escape_sql_string_literal(self.gcs_key_id)}', "
            f"SECRET '{escape_sql_string_literal(self.gcs_secret)}')"
        )

    def _build_azure_secret_sql(self) -> str:
        if self.azure_connection_string:
            return (
                f"CREATE OR REPLACE SECRET {_AZURE_SECRET_NAME} (TYPE azure, "
                f"CONNECTION_STRING '{escape_sql_string_literal(self.azure_connection_string)}')"
            )
        if self.azure_account_name:
            return (
                f"CREATE OR REPLACE SECRET {_AZURE_SECRET_NAME} (TYPE azure, "
                f"PROVIDER credential_chain, "
                f"ACCOUNT_NAME '{escape_sql_string_literal(self.azure_account_name)}')"
            )
        raise ValueError(
            "DuckLake Azure DATA_PATH requires credentials: pass either "
            "--platform-option azure_connection_string=<connection-string> or "
            "--platform-option azure_account_name=<account> (ambient credential_chain auth)."
        )

    def _build_cloud_secret_sql(self) -> tuple[str, str]:
        from benchbox.utils.cloud_storage import cloud_provider_family

        family = cloud_provider_family(str(self.data_path))
        if family == "gcp":
            return "httpfs", self._build_gcs_secret_sql()
        if family == "azure":
            return "azure", self._build_azure_secret_sql()
        return "httpfs", self._build_s3_secret_sql()

    def get_target_dialect(self) -> str:
        return resolve_dialect_for_query_translation("ducklake")

    def handle_existing_database(self, **connection_config) -> None:
        if self.catalog == "postgres":
            self.log_very_verbose(
                "DuckLake postgres catalog: deferring the reuse/force decision to the post-ATTACH "
                "check (a local metadata_path describes no part of a server-side catalog)"
            )
            return

        if getattr(self, "_validating_database", False) or getattr(self, "_existing_db_decided", False):
            self.log_very_verbose("DuckLake catalog decision already made for this run (or validating) - skipping.")
            return

        if self.is_dry_run:
            self.log_verbose("DuckLake catalog validation skipped (dry run mode)")
            return

        self._existing_db_decided = True

        if not self.metadata_path.exists():
            self.log_very_verbose("DuckLake catalog does not exist yet - nothing to handle")
            return

        if self.force_recreate:
            self.log_verbose(f"Force recreate enabled - removing existing DuckLake catalog: {self.metadata_path}")
            self._reset_ducklake_catalog()
            self.database_was_reused = False
            return

        self.log_verbose(
            f"Existing DuckLake catalog found at {self.metadata_path} - reusing it (pass --force for a clean rebuild)"
        )
        self.database_was_reused = True

    def _reset_ducklake_catalog(self) -> None:
        for sidecar in sorted(self.metadata_path.parent.glob(self.metadata_path.name + "*")):
            if sidecar.is_file():
                try:
                    sidecar.unlink()
                except OSError as exc:
                    logger.debug("Could not remove DuckLake catalog file %s: %s", sidecar, exc)

        self._clear_data_path_for_force()

    def _clear_data_path_for_force(self) -> None:
        if not self._data_path_is_cloud:
            if self.data_path.exists():
                shutil.rmtree(self.data_path, ignore_errors=True)
            self.data_path.mkdir(parents=True, exist_ok=True)
            return

        logger.warning(
            "DuckLake --force rebuilt the catalog but did NOT clear the cloud DATA_PATH %s. "
            "Parquet written by previous runs is now unreferenced by the new catalog: it does not "
            "affect this run's results, but it keeps accruing storage cost until you remove the "
            "prefix yourself (e.g. `aws s3 rm --recursive %s`).",
            self.data_path,
            self.data_path,
        )
        self.log_verbose(
            f"Force recreate left the cloud DATA_PATH {self.data_path} untouched - "
            "superseded Parquet from earlier runs remains and must be cleared manually."
        )

    @staticmethod
    def _quote_identifier(identifier: Any) -> str:
        return '"' + str(identifier).replace('"', '""') + '"'

    def _existing_lake_tables(self, setup_conn: Any) -> list[tuple[str, str]]:
        rows = setup_conn.execute(
            "SELECT schema_name, table_name FROM duckdb_tables() "
            "WHERE database_name = 'lake' ORDER BY schema_name, table_name"
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def _run_identity(self, benchmark: Any | None = None) -> dict[str, str]:
        benchmark_name = self._expected_benchmark
        if benchmark_name is None and benchmark is not None:
            benchmark_name = getattr(benchmark, "_name", None) or benchmark.__class__.__name__
        scale_factor = self._expected_scale_factor
        if scale_factor is None and benchmark is not None:
            scale_factor = getattr(benchmark, "scale_factor", None)
        tuning = self.unified_tuning_configuration
        try:
            from dataclasses import asdict, is_dataclass

            if is_dataclass(tuning):
                tuning = asdict(tuning)
            tuning_payload = json.dumps(tuning, sort_keys=True, default=str, separators=(",", ":"))
        except (TypeError, ValueError):
            tuning_payload = repr(tuning)
        return {
            "benchmark": str(benchmark_name or ""),
            "scale_factor": str(scale_factor if scale_factor is not None else ""),
            "tuning_sha256": hashlib.sha256(tuning_payload.encode("utf-8")).hexdigest(),
        }

    def _write_run_identity(self, connection: Any, benchmark: Any) -> None:
        if self._expected_benchmark is None or self._expected_scale_factor is None:
            return
        identity = self._run_identity(benchmark)
        table = self._quote_identifier(_RUN_IDENTITY_TABLE)
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS lake.main.{table} (identity_key VARCHAR, identity_value VARCHAR)"
        )
        connection.execute(f"DELETE FROM lake.main.{table}")
        connection.executemany(
            f"INSERT INTO lake.main.{table} (identity_key, identity_value) VALUES (?, ?)",
            list(identity.items()),
        )

    def _verify_run_identity(self, setup_conn: Any) -> None:
        if self._expected_benchmark is None or self._expected_scale_factor is None:
            return
        table = self._quote_identifier(_RUN_IDENTITY_TABLE)
        rows = setup_conn.execute(
            f"SELECT identity_key, identity_value FROM lake.main.{table} ORDER BY identity_key"
        ).fetchall()
        actual = {row[0]: row[1] for row in rows}
        expected = self._run_identity()
        if actual != expected:
            raise RuntimeError(
                "DuckLake catalog identity does not match this run "
                f"(expected benchmark={expected['benchmark']!r}, scale_factor={expected['scale_factor']!r}, "
                "and tuning configuration). Use --force or a fresh catalog."
            )

    def _rewrite_schema_statement(self, statement: str) -> str:
        return ";\n".join(translate_ddl(statement, "duckdb", strip_primary_keys=True))

    def create_schema(self, benchmark: Any, connection: Any) -> float:
        elapsed = super().create_schema(benchmark, connection)
        self._write_run_identity(_unwrap_duckdb_connection(connection), benchmark)
        return elapsed

    def _resolve_postgres_catalog_reuse(self, setup_conn: Any) -> None:
        if self.is_dry_run:
            self.log_verbose("DuckLake postgres catalog reuse detection skipped (dry run mode)")
            return

        if getattr(self, "_validating_database", False) or getattr(self, "_existing_db_decided", False):
            self.log_very_verbose("DuckLake postgres catalog decision already made for this run (or validating).")
            return
        self._existing_db_decided = True

        existing = self._existing_lake_tables(setup_conn)
        if not existing:
            self.log_very_verbose("DuckLake postgres catalog holds no tables yet - treating as a fresh run")
            return

        identity_name = _RUN_IDENTITY_TABLE.casefold()
        user_tables = [
            (schema, table) for schema, table in existing if str(table).strip('"').casefold() != identity_name
        ]
        if not user_tables and not self.force_recreate:
            self.log_very_verbose("DuckLake catalog contains only its identity marker - treating as a fresh run")
            return
        if (
            user_tables
            and not self.force_recreate
            and self._expected_benchmark is not None
            and self._expected_scale_factor is not None
        ):
            if not any(str(table).strip('"').casefold() == identity_name for _schema, table in existing):
                raise RuntimeError(
                    "DuckLake catalog is populated but has no BenchBox run identity; "
                    "refusing to reuse it. Use --force or a fresh catalog."
                )
            self._verify_run_identity(setup_conn)

        if self.force_recreate:
            self.log_verbose(
                f"Force recreate enabled - dropping {len(existing)} table(s) from the DuckLake "
                f"postgres catalog: {', '.join(f'{schema}.{table}' for schema, table in existing)}"
            )
            for schema, table in existing:
                setup_conn.execute(
                    f"DROP TABLE IF EXISTS lake.{self._quote_identifier(schema)}.{self._quote_identifier(table)}"
                )
            self._clear_data_path_for_force()
            self.database_was_reused = False
            return

        self.log_verbose(
            f"Existing DuckLake postgres catalog found ({len(existing)} table(s)) - reusing it "
            "(pass --force for a clean rebuild)"
        )
        self.database_was_reused = True

    def create_connection(self, **connection_config) -> Any:
        conn = super().create_connection(**connection_config)

        setup_conn = conn._connection if isinstance(conn, DuckDBConnectionWrapper) else conn

        live_version = self.driver_version_actual or getattr(self._duckdb_module, "__version__", None)
        if not _duckdb_version_supports_ducklake(live_version):
            try:
                setup_conn.close()
            except Exception:
                logger.debug("Failed to close base connection on DuckLake version-guard reject", exc_info=True)
            raise RuntimeError(
                "DuckLake requires DuckDB >= 1.3 (the 'ducklake' extension is not "
                f"available on earlier releases). Detected DuckDB version: "
                f"{live_version or 'unknown'}. Use a duckdb>=1.3 environment "
                "(e.g. `uv add 'duckdb>=1.3,<2.0'` or --platform-option driver_version=1.3.2)."
            )

        if self.catalog in ("duckdb", "sqlite"):
            self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._data_path_is_cloud:
            self.data_path.mkdir(parents=True, exist_ok=True)

        data_path_is_cloud = self._data_path_is_cloud
        if data_path_is_cloud:
            cloud_info = get_cloud_path_info(str(self.data_path))
            self.log_verbose(
                f"DuckLake DATA_PATH is a cloud path: {cloud_info['provider']} bucket '{cloud_info['bucket']}'"
            )
        escaped_data_path = escape_sql_string_literal(str(self.data_path))

        try:
            setup_conn.execute("INSTALL ducklake")
            setup_conn.execute("LOAD ducklake")

            if self.catalog == "sqlite":
                setup_conn.execute("INSTALL sqlite")
                setup_conn.execute("LOAD sqlite")
            elif self.catalog == "postgres":
                setup_conn.execute("INSTALL postgres")
                setup_conn.execute("LOAD postgres")
                self.log_verbose(
                    f"DuckLake postgres catalog: host={self.pg_host} port={self.pg_port} "
                    f"database={self.pg_database} (must already exist - DuckLake does not "
                    "CREATE DATABASE)"
                )

            if data_path_is_cloud:
                storage_extension, secret_sql = self._build_cloud_secret_sql()
                setup_conn.execute(f"INSTALL {storage_extension}")
                setup_conn.execute(f"LOAD {storage_extension}")
                try:
                    setup_conn.execute(secret_sql)
                except Exception as secret_exc:
                    using_explicit_creds = bool(
                        (self.s3_key_id and self.s3_secret)
                        or (self.gcs_key_id and self.gcs_secret)
                        or self.azure_connection_string
                        or self.azure_account_name
                    )
                    raise RuntimeError(
                        "Failed to create the DuckDB cloud secret for DuckLake DATA_PATH "
                        f"(provider={'explicit key/secret' if using_explicit_creds else 'ambient credentials'}). "
                        f"Underlying error type: {type(secret_exc).__name__}."
                        + (
                            ""
                            if using_explicit_creds
                            else f" Underlying error: {_redact_secrets(str(secret_exc), self.azure_account_name)}"
                        )
                    ) from None

            attach_target = self._build_catalog_attach_target()
            escaped_attach_target = escape_sql_string_literal(attach_target)
            setup_conn.execute(f"ATTACH 'ducklake:{escaped_attach_target}' AS lake (DATA_PATH '{escaped_data_path}')")
            setup_conn.execute("USE lake")
            if self.catalog == "postgres":
                self._resolve_postgres_catalog_reuse(setup_conn)
        except Exception as e:
            try:
                setup_conn.close()
            except Exception:
                logger.debug("Failed to close base connection after DuckLake ATTACH failure", exc_info=True)
            holds_secret_material = bool(
                self.pg_password
                or self.s3_secret
                or self.s3_key_id
                or self.gcs_secret
                or self.gcs_key_id
                or self.azure_connection_string
                or self.azure_account_name
            )
            underlying = _redact_secrets(
                str(e),
                self.pg_password,
                self.s3_secret,
                self.s3_key_id,
                self.gcs_secret,
                self.gcs_key_id,
                self.azure_connection_string,
                self.azure_account_name,
            )
            raise RuntimeError(
                "Failed to initialize the DuckLake catalog (INSTALL/LOAD/ATTACH "
                f"'ducklake' extension, catalog={self.catalog}). DuckLake requires "
                f"DuckDB >= 1.3; detected DuckDB version: {live_version or 'unknown'}. "
                f"metadata_path={self.metadata_path if self.catalog != 'postgres' else '(postgres catalog - N/A)'}, "
                f"data_path={self.data_path}. Underlying error: {underlying}"
            ) from (None if holds_secret_material else e)

        return _DuckLakeCursorConnection(conn)

    def get_normalized_result_metadata(
        self,
        *,
        connection: Any = None,
        platform_info: Any = None,
    ) -> dict[str, Any]:
        metadata = super().get_normalized_result_metadata(connection=connection, platform_info=platform_info)

        storage = dict(metadata.get("platform_storage") or {})
        storage.update(
            {
                "catalog_backend": self.catalog,
                "data_path_is_cloud": self._data_path_is_cloud,
                "storage_location": "cloud_object_store" if self._data_path_is_cloud else "local_filesystem",
            }
        )
        metadata["platform_storage"] = storage

        raw_config = metadata.get("platform_raw_config")
        if isinstance(raw_config, dict):
            metadata["platform_raw_config"] = {
                key: value for key, value in raw_config.items() if key not in _CREDENTIAL_CONFIG_KEYS
            }

        return metadata

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = super().get_platform_info(connection)

        platform_info["platform_type"] = "ducklake"
        platform_info["platform_name"] = self.platform_name
        platform_info["catalog_backend"] = self.catalog
        platform_info["metadata_path"] = str(self.metadata_path)
        platform_info["data_path"] = str(self.data_path)
        platform_info["data_path_is_cloud"] = self._data_path_is_cloud
        platform_info["configuration"]["catalog_backend"] = self.catalog
        platform_info["configuration"]["metadata_path"] = str(self.metadata_path)
        platform_info["configuration"]["data_path"] = str(self.data_path)
        platform_info["configuration"]["data_path_is_cloud"] = self._data_path_is_cloud
        if self.catalog == "postgres":
            platform_info["configuration"]["pg_host"] = self.pg_host
            platform_info["configuration"]["pg_port"] = self.pg_port
            platform_info["configuration"]["pg_database"] = self.pg_database

        if connection is not None:
            probe_conn = _unwrap_duckdb_connection(connection)
            try:
                row = probe_conn.execute(
                    "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'ducklake'"
                ).fetchone()
                if row and row[0]:
                    platform_info["ducklake_extension_version"] = row[0]
            except Exception:
                logger.debug("Could not probe ducklake extension version", exc_info=True)

        return platform_info


__all__ = ["DuckLakeAdapter"]
