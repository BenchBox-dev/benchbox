# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import argparse
import importlib
import subprocess
from typing import Any

from benchbox.core.deployment import deployment_class, select_adapter_deployment
from benchbox.utils.clock import elapsed_seconds, mono_time

from ..utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)
from ._spark_helpers import (
    LAKESAIL_SPARK_MAP_DROP_REASON,
    SparkLikeAdapterMixin,
    analyze_spark_table,
    drop_tuned_spark_session_config,
    get_spark_query_plan,
    is_spark_connect_reachable,
    list_spark_tables,
    parse_spark_connect_endpoint,
    purge_orphaned_warehouse_directory,
    run_spark_schema_creation_loop,
    spark_schema_statements,
    spark_session_tuning_conf_entries,
    validate_spark_identifier,
)
from .base import DriverIsolationCapability, PlatformAdapter
from .base.config_utils import make_registered_platform_config_builder
from .base.spark_execution_mixin import SparkDataLoadMixin, SparkQueryExecutionMixin

try:
    from pyspark.sql import SparkSession
    from pyspark.sql.types import (
        DoubleType,
        IntegerType,
        LongType,
        StringType,
        StructField,
        StructType,
    )
except ImportError:
    SparkSession = None
    StructType = None
    StructField = None
    StringType = None
    IntegerType = None
    LongType = None
    DoubleType = None


class LakeSailAdapter(SparkLikeAdapterMixin, SparkDataLoadMixin, SparkQueryExecutionMixin, PlatformAdapter):
    plan_capture_phase_eligible = True
    default_service_port = 50051

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

    _csv_compression_codecs: frozenset[str] = SparkDataLoadMixin._csv_compression_codecs | frozenset({"zstd"})

    _requires_csv_extension: bool = True

    _df_caching_supported: bool = False

    _catalog_clear_cache_supported: bool = False

    def __init__(self, **config):
        super().__init__(**config)

        if not SparkSession:
            available, missing = check_platform_dependencies("spark")
            if not available:
                error_msg = get_dependency_error_message("spark", missing)
                raise ImportError(error_msg)

        self._dialect = "spark"

        self.endpoint = config.get("endpoint") or "sc://localhost:50051"
        self.app_name = config.get("app_name") or "BenchBox-LakeSail"

        self.database = config.get("database") or "default"

        self.driver_memory = config.get("driver_memory") or "4g"
        self.shuffle_partitions = config.get("shuffle_partitions")
        self.adaptive_enabled = config.get("adaptive_enabled")

        self.sail_mode = select_adapter_deployment("lakesail", config, config.get("sail_mode") or "local")
        self.deployment_selected = self.sail_mode
        self.deployment_selected_class = deployment_class("lakesail", self.sail_mode)
        self._warn_unapplied_workers(config.get("sail_workers"))
        self.table_format = config.get("table_format") or "parquet"

        self.spark_config = config.get("spark_config") or {}

        self.disable_cache = config.get("disable_cache") if config.get("disable_cache") is not None else True

        self._spark_session = None

        self._managed_server_process = None

    def _warn_unapplied_workers(self, sail_workers: Any) -> None:
        if sail_workers is None:
            return
        from benchbox.core.option_notices import warn_option_not_applied

        if self.sail_mode == "local":
            reason = "the local Sail server that BenchBox starts has no worker-count setting"
        else:
            reason = "BenchBox connects to an existing Sail server and cannot change its workers"
        warn_option_not_applied("lakesail", "sail_workers", reason)

    @property
    def platform_name(self) -> str:
        return "LakeSail"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        lakesail_group = parser.add_argument_group("LakeSail Arguments")
        lakesail_group.add_argument(
            "--lakesail-endpoint",
            type=str,
            default="sc://localhost:50051",
            help="LakeSail Sail server endpoint (Spark Connect URL, e.g., sc://host:port)",
        )
        lakesail_group.add_argument(
            "--lakesail-mode",
            type=str,
            choices=["local", "distributed"],
            default="local",
            help="LakeSail deployment mode (local may start a server; distributed connects to an external one)",
        )
        lakesail_group.add_argument(
            "--lakesail-workers",
            type=int,
            help="Accepted for compatibility; has no effect because Sail takes no worker count from BenchBox",
        )
        lakesail_group.add_argument(
            "--app-name",
            type=str,
            default="BenchBox-LakeSail",
            help="Application name for the Spark Connect session",
        )
        lakesail_group.add_argument(
            "--driver-memory",
            type=str,
            default="4g",
            help="Driver memory (e.g., 4g, 8g)",
        )
        lakesail_group.add_argument(
            "--shuffle-partitions",
            type=int,
            default=None,
            help="Number of shuffle partitions (spark.sql.shuffle.partitions)",
        )
        lakesail_group.add_argument(
            "--table-format",
            type=str,
            choices=["parquet", "orc"],
            default="parquet",
            help="Table format for creating benchmark tables",
        )
        lakesail_group.add_argument(
            "--adaptive-enabled",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Enable or disable Adaptive Query Execution (AQE)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.platforms.base.config_utils import build_adapter_config

        return cls(
            **build_adapter_config(
                config,
                platform="lakesail",
                fields=[
                    "endpoint",
                    "app_name",
                    "driver_memory",
                    "sail_mode",
                    "deployment_mode",
                    "sail_workers",
                    "shuffle_partitions",
                    "adaptive_enabled",
                    "table_format",
                    "spark_config",
                    "disable_cache",
                ],
            )
        )

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "lakesail",
            "platform_name": "LakeSail Sail",
            "connection_mode": self.sail_mode,
            "endpoint": self.endpoint,
            "configuration": {
                "database": self.database,
                "table_format": self.table_format,
                "driver_memory": self.driver_memory,
                "shuffle_partitions": self.shuffle_partitions,
                "adaptive_enabled": self.adaptive_enabled,
                "sail_mode": self.sail_mode,
            },
        }

        if SparkSession:
            try:
                import pyspark

                platform_info["client_library_version"] = pyspark.__version__
            except (ImportError, AttributeError):
                platform_info["client_library_version"] = None
        else:
            platform_info["client_library_version"] = None

        if connection:
            try:
                spark = connection
                platform_info["platform_version"] = spark.version
            except Exception as e:
                self.logger.debug(f"Error collecting LakeSail platform info: {e}")
                platform_info["platform_version"] = None
        else:
            platform_info["platform_version"] = None

        return platform_info

    def get_target_dialect(self) -> str:
        return "spark"

    def _get_spark_conf(self) -> dict[str, Any]:
        conf = {
            "spark.app.name": self.app_name,
        }

        conf.update(
            spark_session_tuning_conf_entries(
                self.shuffle_partitions, self.adaptive_enabled, tuning_enabled=self.tuning_enabled
            )
        )

        if self.disable_cache:
            conf["spark.sql.inMemoryColumnarStorage.enabled"] = "false"

        conf.update(self.spark_config)

        return conf

    def _create_spark_session(self) -> Any:
        builder = SparkSession.builder.remote(self.endpoint)
        for key, value in self._get_spark_conf().items():
            builder = builder.config(key, value)
        return builder.getOrCreate()

    def check_server_database_exists(self, **connection_config) -> bool:
        owns_session = False
        spark = self._spark_session
        try:
            if spark is None:
                spark = self._create_spark_session()
                owns_session = True

            database = connection_config.get("database", self.database)
            databases = [db.name for db in spark.catalog.listDatabases()]
            return database.lower() in [db.lower() for db in databases]

        except Exception as e:
            self.logger.debug(f"Error checking database existence: {e}")
            return False
        finally:
            if owns_session and spark is not None:
                try:
                    spark.stop()
                except Exception:
                    pass

    def drop_database(self, **connection_config) -> None:
        database = connection_config.get("database", self.database)

        if not validate_spark_identifier(database):
            raise ValueError(f"Invalid database identifier: {database}")

        if not self.check_server_database_exists(database=database):
            self.log_verbose(f"Database {database} does not exist - nothing to drop")
            return

        owns_session = False
        spark = self._spark_session
        try:
            if spark is None:
                spark = self._create_spark_session()
                owns_session = True

            spark.sql(f"DROP DATABASE IF EXISTS {database} CASCADE")
            self.logger.info(f"Dropped database {database}")
        except Exception as e:
            raise RuntimeError(f"Failed to drop database {database}: {e}") from e
        finally:
            if owns_session and spark is not None:
                try:
                    spark.stop()
                except Exception:
                    pass

    def _auto_start_local_server(self) -> None:
        import atexit
        import sys
        import time

        host, port = parse_spark_connect_endpoint(self.endpoint)
        self.log_verbose("Sail server not running - auto-starting local pysail server...")
        proc = subprocess.Popen(
            [sys.executable, "-m", "pysail", "spark", "server", "--ip", host, "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        def _cleanup_server():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

        atexit.register(_cleanup_server)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stderr_output = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
                detail = f": {stderr_output}" if stderr_output else ""
                raise RuntimeError(
                    f"pysail server exited immediately (code {proc.returncode}){detail}. "
                    f"Start it manually: {sys.executable} -m pysail spark server"
                )
            time.sleep(0.5)
            if is_spark_connect_reachable(self.endpoint):
                self._managed_server_process = proc
                self._managed_server_atexit = _cleanup_server
                self.log_verbose("pysail server started and ready")
                return

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr_output = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
        detail = f"\nServer stderr: {stderr_output}" if stderr_output else ""
        raise RuntimeError(
            f"Auto-started pysail server did not become ready within 10 seconds.{detail}\n"
            f"Start it manually: {sys.executable} -m pysail spark server"
        )

    def _ensure_server_ready(self) -> None:
        if is_spark_connect_reachable(self.endpoint):
            return

        if self.sail_mode == "local":
            try:
                importlib.import_module("pysail")
            except ImportError:
                raise RuntimeError(
                    f"Cannot connect to LakeSail Sail at {self.endpoint} and 'pysail' is not installed. "
                    "Install it with: uv add pysail\n"
                    "Then start the server manually: python -m pysail spark server"
                ) from None
            self._auto_start_local_server()
        else:
            host, port = parse_spark_connect_endpoint(self.endpoint)
            raise RuntimeError(
                f"Cannot connect to LakeSail Sail server at {self.endpoint}. "
                f"Ensure the server is running and reachable at {host}:{port}."
            )

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("LakeSail Spark Connect session")

        self.log_very_verbose(f"LakeSail config: endpoint={self.endpoint}, database={self.database}")

        try:
            self._ensure_server_ready()
            spark = self._create_spark_session()
            self._spark_session = spark
            self.capture_effective_session_config(spark, "session_created")

            self.handle_existing_database(**connection_config)

            target_database = connection_config.get("database", self.database)
            if not validate_spark_identifier(target_database):
                raise ValueError(f"Invalid database identifier: {target_database}")

            if not self.database_was_reused:
                database_exists = self.check_server_database_exists(database=target_database)

                if not database_exists:
                    self.log_verbose(f"Creating database: {target_database}")
                    spark.sql(f"CREATE DATABASE IF NOT EXISTS {target_database}")
                    self.logger.info(f"Created database {target_database}")

            spark.sql(f"USE DATABASE {target_database}")

            self.logger.info(f"Connected to LakeSail Sail at {self.endpoint}")
            self.log_operation_complete("LakeSail Spark Connect session", details=f"Connected to {self.endpoint}")

            return spark

        except Exception as e:
            if self._spark_session is not None:
                try:
                    self._spark_session.stop()
                except Exception:
                    pass
                self._spark_session = None
            self.logger.error(f"Failed to connect to LakeSail Sail: {e}")
            raise

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()

        spark = connection

        try:
            schema_sql = self._create_schema_with_tuning(benchmark, source_dialect="spark")
            statements = spark_schema_statements(schema_sql, table_format=self.table_format, strip_constraints=True)
            run_spark_schema_creation_loop(
                spark,
                statements,
                logger=self.logger,
                on_pre_loop=lambda s: purge_orphaned_warehouse_directory(s, logger=self.logger),
            )
            self.logger.info("Schema created")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise

        return elapsed_seconds(start_time)

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self.record_harness_spark_conf(connection, ("spark.app.name", "spark.sql.inMemoryColumnarStorage.enabled"))
        if self.tuning_enabled:
            self.apply_olap_runtime_conf(connection, benchmark_type, "LakeSail Sail")
            drop_tuned_spark_session_config(self, LAKESAIL_SPARK_MAP_DROP_REASON)

    def _get_dialect_queries(
        self,
        benchmark: Any,
        benchmark_slug: str,
        connection: Any | None = None,
        *,
        strict_translation: bool = False,
    ) -> dict:
        if benchmark_slug == "vector_search" and hasattr(benchmark, "get_queries"):
            try:
                return benchmark.get_queries(
                    dialect="lakesail",
                    platform_version=self._get_runtime_platform_version(connection),
                )
            except TypeError:
                return benchmark.get_queries(dialect="lakesail")
        return super()._get_dialect_queries(
            benchmark,
            benchmark_slug,
            connection,
            strict_translation=strict_translation,
        )

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        return get_spark_query_plan(connection, query, logger=self.logger)

    def close_connection(self, connection: Any) -> None:
        try:
            if connection and hasattr(connection, "stop"):
                connection.stop()
                self._spark_session = None
        except Exception as e:
            self.logger.warning(f"Error closing LakeSail session: {e}")

        if self._managed_server_process is not None:
            try:
                self._managed_server_process.terminate()
                self._managed_server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._managed_server_process.kill()
            except Exception as e:
                self.logger.debug(f"Error stopping managed pysail server: {e}")
            finally:
                self._managed_server_process = None

            if hasattr(self, "_managed_server_atexit"):
                import atexit

                atexit.unregister(self._managed_server_atexit)
                del self._managed_server_atexit

    def test_connection(self) -> bool:
        try:
            spark = self._create_spark_session()

            try:
                spark.sql("SELECT 1").collect()
                return True
            finally:
                spark.stop()
        except Exception as e:
            self.logger.debug(f"Connection test failed: {e}")
            return False

    _supported_tuning_type_names = ("PARTITIONING", "SORTING")

    def generate_tuning_clause(self, table_tuning) -> str:
        if not table_tuning or not table_tuning.has_any_tuning():
            return ""

        clauses = []

        try:
            from benchbox.core.tuning.interface import TuningType

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                clauses.append(f"PARTITIONED BY ({', '.join(column_names)})")

        except ImportError:
            pass

        return " ".join(clauses)

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        from benchbox.platforms.base.tuning_utils import log_partition_tunings

        log_partition_tunings(table_tuning, self.logger, "LakeSail")

    def _get_existing_tables(self, connection: Any) -> list[str]:
        return list_spark_tables(connection)

    def analyze_table(self, connection: Any, table_name: str) -> None:
        analyze_spark_table(connection, table_name, logger=self.logger)


_build_lakesail_config = make_registered_platform_config_builder(
    "lakesail",
    __name__,
    "LakeSail Sail",
    "pyspark",
    [
        "endpoint",
        "app_name",
        "driver_memory",
        "sail_mode",
        "deployment_mode",
        "sail_workers",
        "shuffle_partitions",
        "adaptive_enabled",
        "table_format",
        "spark_config",
        "disable_cache",
    ],
)
