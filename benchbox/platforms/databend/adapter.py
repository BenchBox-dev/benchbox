# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from benchbox.platforms.base.tuning import make_informational_constraint_applier
from benchbox.utils.clock import elapsed_seconds, mono_time

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        UnifiedTuningConfiguration,
    )

from benchbox.core.deployment import deployment_class, select_adapter_deployment
from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.base import DriverIsolationCapability, PlatformAdapter
from benchbox.platforms.base.data_loading import FileFormatRegistry, resolve_adapter_data_source
from benchbox.utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)

try:
    import databend_driver

    DATABEND_AVAILABLE = True
except ImportError:
    DATABEND_AVAILABLE = False
    databend_driver = None


DEFAULT_DEPLOYMENT = "cloud"


class DatabendAdapter(PlatformAdapter):
    physical_identifier_case = "lower"

    plan_capture_phase_eligible = True
    default_service_port = 8000

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY

    def __init__(self, **config):
        super().__init__(**config)

        if not DATABEND_AVAILABLE:
            available, missing = check_platform_dependencies("databend", ["databend-driver"])
            if not available:
                error_msg = get_dependency_error_message("databend", missing)
                raise ImportError(error_msg)

        self._dialect = "snowflake"

        self.host = config.get("host") or os.environ.get("DATABEND_HOST")
        self.port = config.get("port") or os.environ.get("DATABEND_PORT")
        self.username = config.get("username") or os.environ.get("DATABEND_USER") or "benchbox"
        self.password = config.get("password") or os.environ.get("DATABEND_PASSWORD")
        self.database = config.get("database") or os.environ.get("DATABEND_DATABASE") or "benchbox"
        self.admin_database = config.get("admin_database") or "default"
        self.dsn = config.get("dsn") or os.environ.get("DATABEND_DSN")
        self.deployment_mode = select_adapter_deployment("databend", config, DEFAULT_DEPLOYMENT)
        self.deployment_selected = self.deployment_mode
        self.deployment_selected_class = deployment_class("databend", self.deployment_mode)
        self.ssl = self._coerce_bool(config.get("ssl"), self.deployment_mode == DEFAULT_DEPLOYMENT)

        self.warehouse = (
            config.get("compute_resource") or config.get("warehouse") or os.environ.get("DATABEND_WAREHOUSE")
        )

        self.disable_result_cache = self._coerce_bool(config.get("disable_result_cache"), True)

        if not self.dsn and not self.host:
            raise ConfigurationError(
                "Databend configuration is incomplete. Provide either:\n"
                "  1. DSN: --platform-option dsn=databend+http://user:pass@host:port/db\n"
                "  2. Individual params: --platform-option host=<host> --platform-option password=<pass>\n"
                "  3. Environment variables: DATABEND_HOST, DATABEND_USER, DATABEND_PASSWORD\n"
                "\n"
                "For Databend Cloud:\n"
                "  Set DATABEND_HOST=tenant--warehouse.gw.databend.com\n"
                "For self-hosted:\n"
                "  Set DATABEND_HOST=localhost and DATABEND_PORT=8000"
            )

    @property
    def platform_name(self) -> str:
        return "Databend"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        databend_group = parser.add_argument_group("Databend Arguments")

        databend_group.add_argument(
            "--host",
            type=str,
            help="Databend host (or use DATABEND_HOST env)",
        )
        databend_group.add_argument(
            "--port",
            type=int,
            help="Databend port (default: 443 for cloud, 8000 for self-hosted)",
        )
        databend_group.add_argument(
            "--username",
            type=str,
            default="benchbox",
            help="Databend username (default: benchbox)",
        )
        databend_group.add_argument(
            "--password",
            type=str,
            help="Databend password (or use DATABEND_PASSWORD env)",
        )
        databend_group.add_argument(
            "--database",
            type=str,
            default="benchbox",
            help="Database name (default: benchbox)",
        )
        databend_group.add_argument(
            "--dsn",
            type=str,
            help="Full Databend DSN (overrides individual connection params)",
        )
        databend_group.add_argument(
            "--warehouse",
            type=str,
            help="Databend Cloud warehouse name",
        )
        databend_group.add_argument(
            "--disable-result-cache",
            action="store_true",
            default=True,
            help="Disable result cache for accurate benchmarking (default: True)",
        )
        databend_group.add_argument(
            "--databend-no-ssl",
            dest="ssl",
            action="store_false",
            default=None,
            help="Disable SSL (default: SSL enabled for cloud, disabled for self-hosted)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.platforms.base.config_utils import build_adapter_config

        return cls(
            **build_adapter_config(
                config,
                platform="databend",
                fields=[
                    "host",
                    "port",
                    "username",
                    "password",
                    "dsn",
                    "ssl",
                    "deployment_mode",
                    "compute_resource",
                    "warehouse",
                    "disable_result_cache",
                    "admin_database",
                ],
                include_none=False,
            )
        )

    def get_target_dialect(self) -> str:
        return "snowflake"

    def _build_dsn(self, database: str | None = None) -> str:
        if self.dsn:
            return self._normalize_dsn_scheme(self.dsn)

        port = self.port
        if not port:
            port = 443 if self.ssl else 8000

        user_part = quote(self.username, safe="") if self.username else ""
        if self.password:
            user_part = f"{user_part}:{quote(self.password, safe='')}"

        scheme = "databend+http" if not self.ssl else "databend+https"

        dsn = f"{scheme}://{user_part}@{self.host}:{port}/{database or self.database}"

        params = []
        if not self.ssl:
            params.append("sslmode=disable")
        if self.warehouse:
            params.append(f"warehouse={self.warehouse}")

        if params:
            dsn += "?" + "&".join(params)

        return dsn

    @staticmethod
    def _get_blocking_connection(client: Any) -> Any:
        if all(hasattr(client, method) for method in ("query_row", "query_iter", "exec")):
            return client
        if hasattr(client, "get_conn"):
            return client.get_conn()
        raise TypeError("BlockingDatabendClient does not expose a usable connection interface")

    @staticmethod
    def _normalize_dsn_scheme(dsn: str) -> str:
        if dsn.startswith("databend+ssl://"):
            return "databend+https://" + dsn[len("databend+ssl://") :]
        if dsn.startswith("databend://"):
            dsn = "databend+http://" + dsn[len("databend://") :]
        if dsn.startswith("databend+http://") and "sslmode=" not in dsn:
            separator = "&" if "?" in dsn else "?"
            return f"{dsn}{separator}sslmode=disable"
        return dsn

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("Databend connection")

        self.handle_existing_database(**connection_config)

        dsn = self._build_dsn(self.admin_database)
        self.log_very_verbose(f"Databend connection: host={self.host}, database={self.database}")

        connection = None
        try:
            from databend_driver import BlockingDatabendClient

            client = BlockingDatabendClient(dsn)
            connection = self._get_blocking_connection(client)

            row = connection.query_row("SELECT 1")
            if row is None:
                raise ConnectionError("Databend connection test returned no result")

            connection.exec(f"CREATE DATABASE IF NOT EXISTS {self._quote_identifier(self.database)}")
            connection.exec(f"USE {self._quote_identifier(self.database)}")

            self.logger.info(f"Connected to Databend at {self.host}")
            self.log_operation_complete("Databend connection", details=f"Connected to {self.host}")

            return connection

        except Exception as e:
            if connection and hasattr(connection, "close"):
                connection.close()
            self.logger.error(f"Failed to connect to Databend: {e}")
            raise

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()

        try:
            connection.exec(f"CREATE DATABASE IF NOT EXISTS {self._quote_identifier(self.database)}")
            connection.exec(f"USE {self._quote_identifier(self.database)}")

            schema_sql = self._create_schema_with_tuning(benchmark, source_dialect=self.get_target_dialect())

            for statement in self._translate_schema_ddl(schema_sql):
                try:
                    connection.exec(statement)
                    self.logger.debug(f"Executed schema statement: {statement[:100]}...")
                except Exception as e:
                    if "already exists" in str(e).lower():
                        table_name = self._extract_table_name(statement)
                        if table_name:
                            connection.exec(f"DROP TABLE IF EXISTS {self._quote_identifier(table_name)}")
                            connection.exec(statement)
                    else:
                        raise

            self.logger.info("Schema created")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise

        return elapsed_seconds(start_time)

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        start_time = mono_time()
        table_stats: dict[str, int] = {}

        try:
            connection.exec(f"USE {self._quote_identifier(self.database)}")
            data_source = self._resolve_data_files(benchmark, data_dir)

            for table_name, file_paths in data_source.tables.items():
                if not isinstance(file_paths, list):
                    file_paths = [file_paths]

                valid_files = [Path(fp) for fp in file_paths if Path(fp).exists() and Path(fp).stat().st_size > 0]

                if not valid_files:
                    self.logger.warning(f"Skipping {table_name} - no valid data files")
                    table_stats[table_name.lower()] = 0
                    continue

                chunk_info = f" from {len(valid_files)} file(s)" if len(valid_files) > 1 else ""
                self.log_verbose(f"Loading data for table: {table_name}{chunk_info}")

                try:
                    load_start = mono_time()
                    table_name_lower = table_name.lower()
                    table_name_quoted = self._quote_identifier(table_name_lower)

                    total_rows_loaded = self._insert_files_batched(
                        connection, table_name_quoted, valid_files, table_name, data_source, benchmark
                    )

                    table_stats[table_name_lower] = total_rows_loaded
                    load_time = elapsed_seconds(load_start)
                    self.logger.info(
                        f"Loaded {total_rows_loaded:,} rows into {table_name_lower}{chunk_info} in {load_time:.2f}s"
                    )

                except Exception as e:
                    self.logger.error(f"Failed to load {table_name}: {str(e)[:100]}...")
                    table_stats[table_name.lower()] = 0

            total_time = elapsed_seconds(start_time)
            total_rows = sum(table_stats.values())
            self.logger.info(f"Loaded {total_rows:,} total rows in {total_time:.2f}s")

        except Exception as e:
            self.logger.error(f"Data loading failed: {e}")
            raise

        return table_stats, elapsed_seconds(start_time), None

    def _insert_files_batched(
        self,
        connection: Any,
        table_name_quoted: str,
        valid_files: list[Path],
        table_name: str,
        data_source: Any,
        benchmark: Any,
    ) -> int:
        total_rows_loaded = 0
        batch_size = 500

        from benchbox.platforms.base.data_loading import resolve_csv_dialect

        for file_path in valid_files:
            dialect = resolve_csv_dialect(data_source, table_name, file_path, benchmark)
            delimiter = dialect.delimiter
            if delimiter not in (",", "|", "\t"):
                raise ValueError(f"Unsafe delimiter character: {delimiter!r}")

            compression_handler = FileFormatRegistry.get_compression_handler(file_path)

            with compression_handler.open(file_path) as f:
                batch_rows: list[str] = []
                column_count: int | None = None

                for raw_line in f:
                    line = raw_line.rstrip("\n")
                    if line and line.endswith(delimiter):
                        line = line[:-1]
                    if not line:
                        continue

                    values = line.split(delimiter)

                    if column_count is None:
                        column_count = len(values)
                    elif len(values) != column_count:
                        raise ValueError(
                            f"Inconsistent column count in {file_path}: expected {column_count}, got {len(values)}"
                        )

                    escaped = []
                    for v in values:
                        if v == "" or v.lower() == "null":
                            escaped.append("NULL")
                        else:
                            escaped.append("'" + v.replace("'", "''") + "'")
                    batch_rows.append("(" + ", ".join(escaped) + ")")

                    if len(batch_rows) >= batch_size:
                        insert_sql = f"INSERT INTO {table_name_quoted} VALUES {', '.join(batch_rows)}"
                        connection.exec(insert_sql)
                        total_rows_loaded += len(batch_rows)
                        batch_rows = []

                if batch_rows:
                    insert_sql = f"INSERT INTO {table_name_quoted} VALUES {', '.join(batch_rows)}"
                    connection.exec(insert_sql)
                    total_rows_loaded += len(batch_rows)

        return total_rows_loaded

    def _resolve_data_files(self, benchmark, data_dir: Path) -> Any:
        return resolve_adapter_data_source(self, benchmark, data_dir)

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
        connection.exec(f"USE {self._quote_identifier(self.database)}")

        start_time = mono_time()

        try:
            rows = connection.query_iter(query)
            result = list(rows)

            execution_time = elapsed_seconds(start_time)
            actual_row_count = len(result)

            query_stats = {"execution_time_seconds": execution_time}

            validation_result = None
            if validate_row_count and benchmark_type:
                from benchbox.core.validation.query_validation import QueryValidator

                validator = QueryValidator()
                validation_result = validator.validate_query_result(
                    benchmark_type=benchmark_type,
                    query_id=query_id,
                    actual_row_count=actual_row_count,
                    scale_factor=scale_factor,
                    stream_id=stream_id,
                )

                if validation_result.warning_message:
                    self.log_verbose(f"Row count validation: {validation_result.warning_message}")
                elif not validation_result.is_valid:
                    self.log_verbose(f"Row count validation FAILED: {validation_result.error_message}")
                else:
                    self.log_very_verbose(
                        f"Row count validation PASSED: {actual_row_count} rows "
                        f"(expected: {validation_result.expected_row_count})"
                    )

            result_dict = self._build_query_result_with_validation(
                query_id=query_id,
                execution_time=execution_time,
                actual_row_count=actual_row_count,
                first_row=result[0] if result else None,
                validation_result=validation_result,
                materialized_rows=result,
            )

            result_dict["query_statistics"] = query_stats
            result_dict["resource_usage"] = query_stats

        except Exception as e:
            execution_time = elapsed_seconds(start_time)
            return {
                "query_id": query_id,
                "status": "FAILED",
                "execution_time_seconds": execution_time,
                "rows_returned": 0,
                "error": str(e),
                "error_type": type(e).__name__,
            }

        self._merge_plan_capture_into_result(result_dict, connection, query, query_id)

        return result_dict

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        try:
            rows = connection.query_iter(f"EXPLAIN {query}")
            plan_rows = list(rows)
            text = "\n".join([str(row.values()[0]) if hasattr(row, "values") else str(row) for row in plan_rows])
            return text or None
        except Exception as e:
            self.logger.debug(f"Failed to get Databend query plan: {e}")
            return None

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.databend import DatabendQueryPlanParser

        return DatabendQueryPlanParser()

    def close_connection(self, connection: Any) -> None:
        try:
            if connection and hasattr(connection, "close"):
                connection.close()
        except Exception as e:
            self.logger.warning(f"Error closing connection: {e}")

    def test_connection(self) -> bool:
        client = None
        connection = None
        try:
            from databend_driver import BlockingDatabendClient

            dsn = self._build_dsn(self.admin_database)
            client = BlockingDatabendClient(dsn)
            connection = self._get_blocking_connection(client)
            row = connection.query_row("SELECT 1")
            return row is not None
        except Exception as e:
            self.logger.debug(f"Connection test failed: {e}")
            return False
        finally:
            if connection and hasattr(connection, "close"):
                connection.close()
            elif client and hasattr(client, "close"):
                client.close()

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "databend",
            "platform_name": self.platform_name,
            "configuration": {
                "database": self.database,
                "host": self.host,
            },
        }

        if self.warehouse:
            platform_info["warehouse"] = self.warehouse

        try:
            import databend_driver as dd

            platform_info["client_library_version"] = getattr(dd, "__version__", None)
        except (ImportError, AttributeError):
            platform_info["client_library_version"] = None

        if connection:
            try:
                row = connection.query_row("SELECT version()")
                platform_info["platform_version"] = str(row.values()[0]) if row else None
            except Exception as e:
                self.logger.debug(f"Error collecting Databend platform info: {e}")
                platform_info["platform_version"] = None
        else:
            platform_info["platform_version"] = None

        return platform_info

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self.log_verbose(f"Configuring Databend for {benchmark_type} benchmark")

        if self.disable_result_cache:
            try:
                connection.exec("SET enable_query_result_cache = 0")
                self.log_verbose("Disabled query result cache for benchmarking")
            except Exception as e:
                self.logger.debug(f"Could not disable query result cache: {e}")

        if benchmark_type.lower() in ["olap", "analytics", "tpch", "tpcds"]:
            self.log_verbose("Databend vectorized engine optimized for analytical workloads")

    def check_server_database_exists(self, **connection_config) -> bool:
        database = connection_config.get("database", self.database)

        client = None
        connection = None
        try:
            from databend_driver import BlockingDatabendClient

            dsn = self._build_dsn(self.admin_database)
            client = BlockingDatabendClient(dsn)
            connection = self._get_blocking_connection(client)

            rows = connection.query_iter("SHOW DATABASES")
            for row in rows:
                db_name = str(row.values()[0]) if hasattr(row, "values") else str(row)
                if db_name.lower() == database.lower():
                    return True
            return False
        except Exception as e:
            self.logger.debug(f"Error checking database existence: {e}")
            return False
        finally:
            if connection and hasattr(connection, "close"):
                connection.close()
            elif client and hasattr(client, "close"):
                client.close()

    def drop_database(self, **connection_config) -> None:
        database = connection_config.get("database", self.database)

        connection = None
        try:
            from databend_driver import BlockingDatabendClient

            dsn = self._build_dsn(self.admin_database)
            client = BlockingDatabendClient(dsn)
            connection = self._get_blocking_connection(client)
            connection.exec(f"DROP DATABASE IF EXISTS {self._quote_identifier(database)}")
            self.logger.info(f"Dropped database {database}")
        except Exception as e:
            raise RuntimeError(f"Failed to drop Databend database {database}: {e}") from e
        finally:
            if connection and hasattr(connection, "close"):
                connection.close()

    _supported_tuning_type_names = ("CLUSTERING",)

    def generate_tuning_clause(self, table_tuning) -> str:
        if not table_tuning or not table_tuning.has_any_tuning():
            return ""

        clauses = []

        try:
            from benchbox.core.tuning.interface import TuningType

            clustering_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
            if clustering_columns:
                sorted_cols = sorted(clustering_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                clauses.append(f"CLUSTER BY ({', '.join(column_names)})")

        except ImportError:
            pass

        return " ".join(clauses) if clauses else ""

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        if not table_tuning or not table_tuning.has_any_tuning():
            return

        table_name = self.resolve_physical_table(table_tuning.table_name, connection)
        self.logger.info(f"Applying Databend tunings for table: {table_name}")

        try:
            from benchbox.core.tuning.interface import TuningType

            clustering_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
            if clustering_columns:
                sorted_cols = sorted(clustering_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                cluster_clause = ", ".join(column_names)
                connection.exec(f"ALTER TABLE {self._quote_identifier(table_name)} CLUSTER BY ({cluster_clause})")
                self.logger.info(f"Applied clustering for {table_name}: {cluster_clause}")

        except ImportError:
            self.logger.warning("Tuning interface not available - skipping tuning application")

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

        apply_standard_unified_tuning(self, unified_config, connection)

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        if not platform_config:
            return

        self.logger.info("Databend platform optimizations noted (engine pre-optimized for analytics)")

    apply_constraint_configuration = make_informational_constraint_applier(
        "Primary key constraints noted for Databend (informational)",
        "Foreign key constraints noted for Databend (not enforced)",
    )

    def analyze_table(self, connection: Any, table_name: str) -> None:
        self.logger.debug(f"Databend collects statistics automatically - skipping explicit ANALYZE for {table_name}")

    def _translate_schema_ddl(self, schema_sql: str) -> list[str]:
        from benchbox.utils.dialect_utils import translate_ddl

        return translate_ddl(
            schema_sql,
            self.get_target_dialect(),
            strip_primary_keys=True,
            strip_foreign_keys=True,
            type_map={"CHAR": "VARCHAR"},
        )

    def _extract_table_name(self, statement: str) -> str | None:
        try:
            match = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)", statement, re.IGNORECASE)
            if match:
                return match.group(1).strip().strip('"').strip("`")
        except Exception:
            pass
        return None

    def _quote_identifier(self, name: str) -> str:
        if not isinstance(name, str) or not name:
            raise ValueError("Identifier must be a non-empty string")
        return "`" + name.replace("`", "``") + "`"

    def _coerce_bool(self, value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip().lower() not in {"false", "0", "no", "off"}
        return bool(value)


__all__ = ["DatabendAdapter"]
