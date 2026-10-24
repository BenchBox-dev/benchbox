# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from benchbox.utils.dialect_utils import translate_ddl

from ..utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)
from ..utils.file_format import get_data_extension
from .base import DriverIsolationCapability, PlatformAdapter
from .base.data_loading import (
    CsvDialect,
    DataSourceResolver,  # noqa: F401
    prepare_local_load_file,
    resolve_csv_dialect,
)
from .base.mysql_wire import MySqlWireLifecycleMixin, NoOpTableTuningMixin, build_database_config
from .base.sql_execution import execute_sql_query  # noqa: F401

SINGLESTORE_DIALECT = "mysql"

try:
    import singlestoredb as _s2
except ImportError:
    _s2 = None

_DEFAULT_PORT = 3306

_TPCH_SHARD_KEYS: dict[str, str] = dict(  # noqa: C408
    lineitem="l_orderkey",
    orders="o_orderkey",
    customer="c_custkey",
    part="p_partkey",
    supplier="s_suppkey",
    partsupp="ps_partkey",
)

_TPCH_SORT_KEYS: dict[str, list[str]] = {
    **{table: [key] for table, key in _TPCH_SHARD_KEYS.items()},
    "lineitem": ["l_orderkey", "l_linenumber"],
    "partsupp": ["ps_partkey", "ps_suppkey"],
}

_REFERENCE_TABLES = {"nation", "region"}

_PARQUET_EXTENSIONS = {".parquet"}


class SingleStoreAdapter(NoOpTableTuningMixin, MySqlWireLifecycleMixin, PlatformAdapter):
    plan_capture_phase_eligible = True
    supports_refresh_functions = True
    default_service_port = _DEFAULT_PORT

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    connection_operation_name = "SingleStore connection"
    database_exists_rethrow_error_code = 2003
    connection_runtime_error_code = 2003
    empty_load_details: ClassVar[dict[str, Any]] = {}
    reset_table_rows_on_load_error = True

    @property
    def platform_name(self) -> str:
        return "SingleStore"

    def get_target_dialect(self) -> str:
        return SINGLESTORE_DIALECT

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument"):
            return
        try:
            parser.add_argument(
                "--singlestore-host",
                dest="host",
                default=None,
                help="SingleStore node hostname or Helios endpoint",
            )
            parser.add_argument(
                "--singlestore-port",
                dest="port",
                type=int,
                default=None,
                help="SingleStore MySQL protocol port (default: 3306)",
            )
            parser.add_argument(
                "--singlestore-database",
                dest="database",
                help="SingleStore database name (auto-generated if not specified)",
            )
            parser.add_argument(
                "--singlestore-username",
                dest="username",
                default=None,
                help="SingleStore username (default: root)",
            )
            parser.add_argument(
                "--singlestore-password",
                dest="password",
                help="SingleStore password",
            )
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug("Failed to register CLI arguments: %s", e)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> SingleStoreAdapter:
        adapter_config: dict[str, Any] = {}

        for key in ["host", "port", "username", "password"]:
            if config.get(key) is not None:
                adapter_config[key] = config[key]

        if config.get("database"):
            adapter_config["database"] = config["database"]
        elif db_env := os.environ.get("SINGLESTORE_DATABASE"):
            adapter_config["database"] = db_env
        elif config.get("benchmark") and config.get("scale_factor") is not None:
            from benchbox.utils.scale_factor import format_benchmark_name

            benchmark_name = format_benchmark_name(config["benchmark"], config["scale_factor"])
            adapter_config["database"] = f"benchbox_{benchmark_name}".lower().replace("-", "_")

        for key in [
            "force_recreate",
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
        if "force" in config:
            adapter_config["force_recreate"] = config["force"]

        return cls(**adapter_config)

    def __init__(self, **config):
        super().__init__(**config)

        if _s2 is None:
            available, missing = check_platform_dependencies("singlestore")
            if not available:
                error_msg = get_dependency_error_message("singlestore", missing)
                raise ImportError(error_msg)

        self._dialect = SINGLESTORE_DIALECT

        self.host = config.get("host", "localhost")
        self.port = config.get("port", _DEFAULT_PORT)
        self.database = config.get("database", "benchbox")
        self.username = config.get("username", "root")
        self.password = config.get("password")

        if not self._validate_identifier(self.database):
            raise ValueError(f"Invalid database identifier: {self.database}")

    def _admin_connect(self) -> Any:
        return _s2.connect(
            host=self.host,
            port=self.port,
            user=self.username,
            password=self.password,
            connect_timeout=10,
        )

    def _connect_database(self, **connection_config: Any) -> Any:
        return _s2.connect(
            host=self.host,
            port=self.port,
            user=self.username,
            password=self.password,
            database=self.database,
            connect_timeout=10,
            local_infile=True,
        )

    def _schema_statements(self, benchmark: Any) -> list[str]:
        schema_sql = self._create_schema_with_tuning(benchmark, source_dialect=self.get_target_dialect())
        return translate_ddl(schema_sql, "singlestore", "singlestore", **self._ddl_options())

    def _load_table_name(self, table_name: str) -> str:
        return table_name.lower()

    def _handle_invalid_load_table(self, table_name: str, table_stats: dict[str, int]) -> bool:
        raise ValueError(f"Invalid table identifier: {table_name!r}")

    def _load_resolved_data_file(
        self,
        benchmark: Any,
        connection: Any,
        table_name: str,
        source_table_name: str,
        data_file: Path,
        data_source: Any,
    ) -> int:
        if data_file.suffix.lower() in _PARQUET_EXTENSIONS:
            self.logger.warning(f"Skipping {data_file.name}: LOAD DATA LOCAL INFILE does not support Parquet")
            return 0
        dialect = resolve_csv_dialect(data_source, source_table_name, data_file, benchmark)
        strip_trailing_delim = get_data_extension(data_file) in (".tbl", ".dat")
        return self._load_data_infile(connection, table_name, data_file, dialect, strip_trailing_delim)

    def _load_data_infile(
        self,
        connection: Any,
        table_name: str,
        data_file: Path,
        dialect: CsvDialect,
        strip_trailing_delim: bool = False,
    ) -> int:
        with prepare_local_load_file(
            data_file, dialect=dialect, strip_trailing_delim=strip_trailing_delim
        ) as load_path:
            cursor = connection.cursor()
            try:
                cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
                pre_row = cursor.fetchone()
                pre_count = int(pre_row[0]) if pre_row else 0

                escaped_path = str(load_path).replace("\\", "\\\\").replace("'", "\\'")
                escaped_delim = dialect.delimiter.replace("\\", "\\\\").replace("'", "\\'")
                clauses = [
                    f"LOAD DATA LOCAL INFILE '{escaped_path}'",
                    f"INTO TABLE `{table_name}`",
                    f"FIELDS TERMINATED BY '{escaped_delim}' OPTIONALLY ENCLOSED BY '\"'",
                ]
                if dialect.null_marker is not None:
                    escaped_null = dialect.null_marker.replace("\\", "\\\\").replace("'", "\\'")
                    clauses.append(f"NULL DEFINED BY '{escaped_null}'")
                clauses.append("LINES TERMINATED BY '\\n'")
                if dialect.has_header:
                    clauses.append("IGNORE 1 LINES")
                sql = " ".join(clauses)
                cursor.execute(sql)

                cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
                post_row = cursor.fetchone()
                post_count = int(post_row[0]) if post_row else 0
                return post_count - pre_count
            finally:
                cursor.close()

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        cursor = connection.cursor()
        try:
            try:
                cursor.execute("SET SESSION query_cache_type = 0")
            except Exception as e:
                self.logger.debug(f"Could not disable query cache: {e}")

            try:
                cursor.execute("SET SESSION max_allowed_packet = 1073741824")
            except Exception as e:
                self.logger.debug(f"Could not set max_allowed_packet: {e}")
        finally:
            cursor.close()

    def _format_query_plan_rows(self, rows: Any) -> str:
        return "\n".join(str(row[0]) if isinstance(row, (tuple, list)) else str(row) for row in rows)

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.singlestore import SingleStoreQueryPlanParser

        return SingleStoreQueryPlanParser()

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        return self.execute_query_with_plan_capture(
            super().execute_query,
            connection,
            query,
            query_id,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info: dict[str, Any] = {
            "platform_type": "singlestore",
            "platform_name": "SingleStore",
            "host": self.host,
            "port": self.port,
            "dialect": SINGLESTORE_DIALECT,
            "configuration": {
                "database": self.database,
                "singlestoredb_available": _s2 is not None,
            },
        }

        if connection:
            try:
                cursor = connection.cursor()

                cursor.execute("SELECT @@memsql_version")
                version_row = cursor.fetchone()
                if version_row:
                    platform_info["platform_version"] = version_row[0]

                cursor.execute("SELECT DATABASE()")
                db_row = cursor.fetchone()
                if db_row:
                    platform_info["configuration"]["current_database"] = db_row[0]

                cursor.close()
            except Exception as e:
                self.logger.debug(f"Error getting platform info: {e}")

        if _s2:
            platform_info["client_library_version"] = getattr(_s2, "__version__", "unknown")

        return platform_info

    def _ddl_options(self) -> dict[str, Any]:
        return {
            "strip_foreign_keys": True,
            "table_case": "lower",
            "type_map": "singlestore",
            "singlestore": {
                "shard_keys": _TPCH_SHARD_KEYS,
                "sort_keys": _TPCH_SORT_KEYS,
                "reference_tables": _REFERENCE_TABLES,
            },
        }

    def validate_platform_capabilities(self, benchmark_type: str):
        errors = []
        warnings = []

        if _s2 is None:
            errors.append("singlestoredb library not available - install with 'uv add singlestoredb'")
        else:
            try:
                version = getattr(_s2, "__version__", "unknown")
                self.logger.debug(f"singlestoredb version: {version}")
            except Exception:
                pass

        platform_info = {
            "platform": self.platform_name,
            "benchmark_type": benchmark_type,
            "dry_run_mode": self.dry_run_mode,
            "singlestoredb_available": _s2 is not None,
            "host": self.host,
            "port": self.port,
            "database": self.database,
        }

        try:
            from benchbox.core.validation import ValidationResult

            return ValidationResult(
                is_valid=len(errors) == 0,
                errors=errors,
                warnings=warnings,
                details=platform_info,
            )
        except ImportError:
            return None

    def _populate_connection_health_details(
        self,
        cursor: Any,
        warnings: list[str],
        connection_info: dict[str, Any],
    ) -> None:
        try:
            cursor.execute("SELECT @@memsql_version")
            version_result = cursor.fetchone()
            if version_result:
                connection_info["server_version"] = version_result[0]
        except Exception:
            warnings.append("Could not query SingleStore version")

    _supported_tuning_type_names = ("SORTING", "DISTRIBUTION")


def _build_singlestore_config(
    platform: str,
    options: dict[str, Any],
    overrides: dict[str, Any],
    info: Any,
) -> Any:
    return build_database_config(
        platform=platform,
        options=options,
        overrides=overrides,
        info=info,
        default_name="SingleStore",
        default_driver_package="singlestoredb",
        fields={
            "host": lambda m: m.get("host") or os.environ.get("SINGLESTORE_HOST", "localhost"),
            "port": lambda m: (
                m.get("port")
                if m.get("port") is not None
                else int(os.environ.get("SINGLESTORE_PORT", str(_DEFAULT_PORT)))
            ),
            "username": lambda m: (
                m.get("username")
                or os.environ.get("SINGLESTORE_USER")
                or os.environ.get("SINGLESTORE_USERNAME", "root")
            ),
            "password": lambda m: m.get("password") or os.environ.get("SINGLESTORE_PASSWORD"),
            "database": lambda m: m.get("database") or os.environ.get("SINGLESTORE_DATABASE"),
        },
    )
