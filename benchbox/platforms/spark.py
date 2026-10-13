from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from benchbox.utils.clock import elapsed_seconds, mono_time

from ..utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)
from ._spark_helpers import (
    SPARK_AQE_KEYS,
    SparkLikeAdapterMixin,
    analyze_spark_table,
    get_spark_query_plan,
    list_spark_tables,
    optimize_spark_table_definition,
    run_spark_schema_creation_loop,
    spark_aqe_conf_entries,
    validate_spark_identifier,
)
from .base import DriverIsolationCapability, PlatformAdapter
from .base.config_utils import make_registered_platform_config_builder
from .base.spark_execution_mixin import SparkDataLoadMixin, SparkQueryExecutionMixin
from .base.spark_logging import suppress_window_exec_warning

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


_MAX_COMPATIBLE_JAVA_VERSION = 22

_logger = logging.getLogger(__name__)
_SPARK_AUTO_BROADCAST_THRESHOLD = "spark.sql.autoBroadcastJoinThreshold"
_SPARK_AQE_KEYS = SPARK_AQE_KEYS


def _get_java_version(java_home: str | None = None) -> int | None:
    java_bin = "java"
    if java_home:
        java_bin = os.path.join(java_home, "bin", "java")

    try:
        result = subprocess.run(
            [java_bin, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stderr + result.stdout
        match = re.search(r'"(\d+)[\.\+]', output)
        if match:
            return int(match.group(1))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _find_compatible_java_home() -> str | None:
    import platform as _platform

    if _platform.system() == "Darwin":
        for version in ("17", "21", "11"):
            try:
                result = subprocess.run(
                    ["/usr/libexec/java_home", "-v", version],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    candidate = result.stdout.strip()
                    ver = _get_java_version(candidate)
                    if ver and ver <= _MAX_COMPATIBLE_JAVA_VERSION:
                        return candidate
            except (OSError, subprocess.TimeoutExpired):
                continue
    else:
        for base in ("/usr/lib/jvm", "/usr/java"):
            base_path = Path(base)
            if not base_path.exists():
                continue
            for version in ("17", "21", "11"):
                for candidate_dir in sorted(base_path.glob(f"*-{version}*"), reverse=True):
                    if candidate_dir.is_dir():
                        ver = _get_java_version(str(candidate_dir))
                        if ver and ver <= _MAX_COMPATIBLE_JAVA_VERSION:
                            return str(candidate_dir)
    return None


def _ensure_compatible_java(java_home_override: str | None = None) -> str | None:
    if java_home_override:
        ver = _get_java_version(java_home_override)
        if ver and ver > _MAX_COMPATIBLE_JAVA_VERSION:
            raise RuntimeError(
                f"Specified java_home ({java_home_override}) uses Java {ver}, "
                f"but PySpark requires Java <= {_MAX_COMPATIBLE_JAVA_VERSION}. "
                f"Subject.getSubject() was removed in Java 23 (JEP 411)."
            )
        os.environ["JAVA_HOME"] = java_home_override
        _logger.info(f"Using user-specified JAVA_HOME: {java_home_override}")
        return java_home_override

    current_version = _get_java_version()
    if current_version is None or current_version <= _MAX_COMPATIBLE_JAVA_VERSION:
        return os.environ.get("JAVA_HOME")

    _logger.warning(
        f"System Java {current_version} is incompatible with PySpark "
        f"(requires <= {_MAX_COMPATIBLE_JAVA_VERSION}). "
        f"Searching for a compatible JDK..."
    )

    compatible_home = _find_compatible_java_home()
    if compatible_home:
        os.environ["JAVA_HOME"] = compatible_home
        compat_ver = _get_java_version(compatible_home)
        _logger.info(f"Auto-selected Java {compat_ver} at {compatible_home} for Spark compatibility")
        return compatible_home

    raise RuntimeError(
        f"System Java version {current_version} is incompatible with PySpark. "
        f"Hadoop's UserGroupInformation uses Subject.getSubject(), which was "
        f"removed in Java 23 (JEP 411). Install Java 17 or 21 and either:\n"
        f"  1. Set JAVA_HOME to point to it before running BenchBox\n"
        f"  2. Use --platform-option java_home=/path/to/jdk17\n"
        f"  3. Install via: brew install openjdk@17"
    )


class SparkAdapter(SparkLikeAdapterMixin, SparkDataLoadMixin, SparkQueryExecutionMixin, PlatformAdapter):
    physical_identifier_case = "lower"

    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

    def __init__(self, **config):
        super().__init__(**config)

        if not SparkSession:
            available, missing = check_platform_dependencies("spark")
            if not available:
                error_msg = get_dependency_error_message("spark", missing)
                raise ImportError(error_msg)

        self._dialect = "spark"

        self.master = config.get("master") or "local[*]"
        self.app_name = config.get("app_name") or "BenchBox"
        self.deploy_mode = config.get("deploy_mode")

        self.warehouse_dir = config.get("warehouse_dir")
        self.database = config.get("database") or "default"

        self.driver_memory = config.get("driver_memory") or "4g"
        self.executor_memory = config.get("executor_memory") or "4g"
        self.executor_cores = config.get("executor_cores") if config.get("executor_cores") is not None else 2
        self.num_executors = config.get("num_executors")

        self.shuffle_partitions = (
            config.get("shuffle_partitions") if config.get("shuffle_partitions") is not None else 200
        )
        self.broadcast_threshold = config.get("broadcast_threshold")
        self.adaptive_enabled = config.get("adaptive_enabled") if config.get("adaptive_enabled") is not None else True

        self.table_format = config.get("table_format") or "parquet"

        self.enable_hive = config.get("enable_hive") if config.get("enable_hive") is not None else False

        self.spark_config = config.get("spark_config") or {}

        self.java_home = config.get("java_home")

        self.staging_root = config.get("staging_root")

        self.disable_cache = config.get("disable_cache") if config.get("disable_cache") is not None else True

        self._spark_session = None

    @property
    def platform_name(self) -> str:
        return "Spark"

    @staticmethod
    def add_cli_arguments(parser) -> None:

        spark_group = parser.add_argument_group("Spark Arguments")
        spark_group.add_argument(
            "--master",
            type=str,
            default="local[*]",
            help="Spark master URL (local[*], spark://host:port, k8s://host:port, yarn)",
        )
        spark_group.add_argument("--app-name", type=str, default="BenchBox", help="Spark application name")
        spark_group.add_argument(
            "--deploy-mode", type=str, choices=["client", "cluster"], help="Spark deploy mode (client or cluster)"
        )
        spark_group.add_argument("--driver-memory", type=str, default="4g", help="Spark driver memory (e.g., 4g, 8g)")
        spark_group.add_argument(
            "--executor-memory", type=str, default="4g", help="Spark executor memory (e.g., 4g, 8g)"
        )
        spark_group.add_argument("--executor-cores", type=int, default=2, help="Number of cores per executor")
        spark_group.add_argument("--num-executors", type=int, help="Number of executors (for YARN/K8s)")
        spark_group.add_argument(
            "--shuffle-partitions",
            type=int,
            default=200,
            help="Number of shuffle partitions (spark.sql.shuffle.partitions)",
        )
        spark_group.add_argument(
            "--table-format",
            type=str,
            choices=["parquet", "orc", "delta", "iceberg"],
            default="parquet",
            help="Table format for creating benchmark tables",
        )
        spark_group.add_argument(
            "--enable-hive", action="store_true", default=False, help="Enable Hive metastore support"
        )
        spark_group.add_argument(
            "--adaptive-enabled",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Enable or disable Adaptive Query Execution (AQE)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.platforms.base.config_utils import build_adapter_config

        benchmark_name = str(config["benchmark"]).lower()
        adapter_config = build_adapter_config(
            config,
            platform="spark",
            fields=[
                "master",
                "app_name",
                "deploy_mode",
                "driver_memory",
                "executor_memory",
                "executor_cores",
                "num_executors",
                "warehouse_dir",
                "shuffle_partitions",
                "broadcast_threshold",
                "adaptive_enabled",
                "table_format",
                "enable_hive",
                "java_home",
                "spark_config",
                "staging_root",
                "disable_cache",
            ],
        )

        spark_config = adapter_config.get("spark_config") or {}
        broadcast_threshold = adapter_config.get("broadcast_threshold")
        if (
            benchmark_name == "joinorder"
            and broadcast_threshold is None
            and _SPARK_AUTO_BROADCAST_THRESHOLD not in spark_config
        ):
            adapter_config["broadcast_threshold"] = -1

        if not adapter_config.get("warehouse_dir"):
            from benchbox.utils.path_utils import get_benchmark_runs_databases_path

            if config.get("output_dir"):
                warehouse_path = get_benchmark_runs_databases_path(
                    config["benchmark"],
                    config["scale_factor"],
                    base_dir=Path(config["output_dir"]) / "databases",
                )
            else:
                warehouse_path = get_benchmark_runs_databases_path(config["benchmark"], config["scale_factor"])
            warehouse_path.mkdir(parents=True, exist_ok=True)
            adapter_config["warehouse_dir"] = str(warehouse_path)

        return cls(**adapter_config)

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "spark",
            "platform_name": "Apache Spark",
            "connection_mode": "local" if self.master.startswith("local") else "cluster",
            "master": self.master,
            "configuration": {
                "database": self.database,
                "table_format": self.table_format,
                "driver_memory": self.driver_memory,
                "executor_memory": self.executor_memory,
                "executor_cores": self.executor_cores,
                "shuffle_partitions": self.shuffle_partitions,
                "adaptive_enabled": self.adaptive_enabled,
                "hive_enabled": self.enable_hive,
            },
        }
        tuned_session = getattr(self, "_tuned_session_applied", None) or {}
        shuffle_live = tuned_session.get("spark.sql.shuffle.partitions")
        if shuffle_live is not None:
            try:
                platform_info["configuration"]["shuffle_partitions"] = int(shuffle_live)
            except (TypeError, ValueError):
                platform_info["configuration"]["shuffle_partitions"] = shuffle_live
        adaptive_live = tuned_session.get("spark.sql.adaptive.enabled")
        if adaptive_live is not None:
            platform_info["configuration"]["adaptive_enabled"] = str(adaptive_live).lower() == "true"

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

                conf = spark.sparkContext.getConf()
                platform_info["configuration"]["spark_master"] = conf.get("spark.master")
                platform_info["configuration"]["spark_app_id"] = spark.sparkContext.applicationId

                try:
                    sc = spark.sparkContext
                    executor_ids = sc._jsc.sc().getExecutorIds()
                    if executor_ids:
                        platform_info["configuration"]["num_executors"] = executor_ids.size()
                except Exception:
                    pass

            except Exception as e:
                self.logger.debug(f"Error collecting Spark platform info: {e}")
                if platform_info.get("platform_version") is None:
                    platform_info["platform_version"] = None
        else:
            platform_info["platform_version"] = None

        return platform_info

    def get_target_dialect(self) -> str:
        return "spark"

    def _get_spark_conf(self) -> dict[str, Any]:
        conf = {
            "spark.app.name": self.app_name,
            "spark.driver.memory": self.driver_memory,
            "spark.executor.memory": self.executor_memory,
            "spark.executor.cores": str(self.executor_cores),
            "spark.sql.shuffle.partitions": str(self.shuffle_partitions),
        }

        conf.update(spark_aqe_conf_entries(self.adaptive_enabled))

        if self.broadcast_threshold is not None:
            conf[_SPARK_AUTO_BROADCAST_THRESHOLD] = str(self.broadcast_threshold)

        if self.num_executors is not None:
            conf["spark.executor.instances"] = str(self.num_executors)

        if self.warehouse_dir:
            conf["spark.sql.warehouse.dir"] = self.warehouse_dir

        conf["spark.hadoop.io.compression.codecs"] = (
            "org.apache.hadoop.io.compress.DefaultCodec,"
            "org.apache.hadoop.io.compress.GzipCodec,"
            "org.apache.hadoop.io.compress.BZip2Codec,"
            "org.apache.hadoop.io.compress.DeflateCodec,"
            "org.apache.hadoop.io.compress.SnappyCodec,"
            "org.apache.hadoop.io.compress.Lz4Codec,"
            "org.apache.hadoop.io.compress.ZStandardCodec"
        )

        if self.disable_cache:
            conf["spark.sql.inMemoryColumnarStorage.enabled"] = "false"

        if self.table_format == "delta":
            conf["spark.sql.extensions"] = "io.delta.sql.DeltaSparkSessionExtension"
            conf["spark.sql.catalog.spark_catalog"] = "org.apache.spark.sql.delta.catalog.DeltaCatalog"

        if self.table_format == "iceberg":
            conf["spark.sql.extensions"] = "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
            conf["spark.sql.catalog.spark_catalog"] = "org.apache.iceberg.spark.SparkSessionCatalog"
            conf["spark.sql.catalog.spark_catalog.type"] = "hive"

        conf.update(self.spark_config)

        return conf

    def check_server_database_exists(self, **connection_config) -> bool:
        try:
            if self._spark_session is None:
                return False

            database = connection_config.get("database", self.database)
            databases = [db.name for db in self._spark_session.catalog.listDatabases()]
            return database.lower() in [db.lower() for db in databases]

        except Exception as e:
            self.logger.debug(f"Error checking database existence: {e}")
            return False

    def drop_database(self, **connection_config) -> None:
        database = connection_config.get("database", self.database)

        if not validate_spark_identifier(database):
            raise ValueError(f"Invalid database identifier: {database}")

        if not self.check_server_database_exists(database=database):
            self.log_verbose(f"Database {database} does not exist - nothing to drop")
            return

        try:
            self._spark_session.sql(f"DROP DATABASE IF EXISTS {database} CASCADE")
            self.logger.info(f"Dropped database {database}")

        except Exception as e:
            raise RuntimeError(f"Failed to drop Spark database {database}: {e}") from e

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("Spark session")

        java_home = _ensure_compatible_java(self.java_home)
        if java_home:
            self.log_verbose(f"Using JAVA_HOME: {java_home}")

        builder = SparkSession.builder.master(self.master)

        spark_conf = self._get_spark_conf()
        for key, value in spark_conf.items():
            builder = builder.config(key, value)

        if self.enable_hive:
            builder = builder.enableHiveSupport()

        self.log_very_verbose(f"Spark config: master={self.master}, database={self.database}")

        try:
            spark = builder.getOrCreate()
            self._spark_session = spark

            self._configure_runtime_logging(spark)

            self.handle_existing_database(**connection_config)

            target_database = connection_config.get("database", self.database)

            if not self.database_was_reused:
                database_exists = self.check_server_database_exists(database=target_database)

                if not database_exists:
                    self.log_verbose(f"Creating database: {target_database}")
                    spark.sql(f"CREATE DATABASE IF NOT EXISTS {target_database}")
                    self.logger.info(f"Created database {target_database}")

            spark.sql(f"USE {target_database}")

            self.logger.info(f"Connected to Spark with master {self.master}")

            self.log_operation_complete("Spark session", details=f"Connected to {self.master}")

            return spark

        except Exception as e:
            self.logger.error(f"Failed to create Spark session: {e}")
            raise

    def _configure_runtime_logging(self, spark: Any) -> None:
        spark_log_level = "WARN" if self.verbose else "ERROR"
        spark.sparkContext.setLogLevel(spark_log_level)
        suppress_window_exec_warning(spark)

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()

        spark = connection

        try:
            schema_sql = self._create_schema_with_tuning(benchmark, source_dialect="standard")
            statements = [stmt.strip() for stmt in schema_sql.split(";") if stmt.strip()]
            fmt = self.table_format
            v1_table = (fmt or "parquet").lower() in {"parquet", "orc"}
            run_spark_schema_creation_loop(
                spark,
                statements,
                lambda stmt: optimize_spark_table_definition(
                    stmt,
                    table_format=fmt,
                    strip_v1_constraints=v1_table,
                    upcast_smallint=v1_table,
                ),
                logger=self.logger,
                on_location_collision=self._remove_orphaned_table_location,
            )
            self.logger.info("Schema created")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise

        return elapsed_seconds(start_time)

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self.apply_olap_runtime_conf(connection, benchmark_type, "Spark")
        if self.tuning_enabled:
            from benchbox.platforms._spark_helpers import apply_tuned_spark_session_config

            apply_tuned_spark_session_config(self, connection)

    def _remove_orphaned_table_location(self, spark: Any, table_name: str) -> None:
        try:
            warehouse_dir = spark.conf.get("spark.sql.warehouse.dir", "spark-warehouse")
            warehouse_path = Path(warehouse_dir.removeprefix("file:")).resolve()
            leaf_name = table_name.split(".")[-1].strip("`")
            current_db = spark.catalog.currentDatabase()
            table_dir = (warehouse_path / f"{current_db}.db" / leaf_name).resolve()
            if not table_dir.is_relative_to(warehouse_path):
                self.log_verbose(f"Refusing to remove {table_dir}: outside warehouse {warehouse_path}")
                return
            if table_dir.exists():
                shutil.rmtree(table_dir)
                self.log_verbose(f"Removed orphaned table location: {table_dir}")
        except Exception as e:
            self.log_verbose(f"Could not remove orphaned location for {table_name}: {e}")

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        return get_spark_query_plan(connection, query, logger=self.logger)

    def close_connection(self, connection: Any) -> None:
        try:
            if connection and hasattr(connection, "stop"):
                connection.stop()
                self._spark_session = None
        except Exception as e:
            self.logger.warning(f"Error closing Spark session: {e}")

    def test_connection(self) -> bool:
        try:
            _ensure_compatible_java(self.java_home)

            builder = SparkSession.builder.master(self.master)
            spark_conf = self._get_spark_conf()
            for key, value in spark_conf.items():
                builder = builder.config(key, value)

            spark = builder.getOrCreate()
            self._configure_runtime_logging(spark)

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

            sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
            if sort_columns and self.table_format == "delta":
                sorted_cols = sorted(sort_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                clauses.append(f"CLUSTER BY ({', '.join(column_names)})")

        except ImportError:
            pass

        return " ".join(clauses)

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        if not table_tuning or not table_tuning.has_any_tuning():
            return

        table_name = self.resolve_physical_table(table_tuning.table_name, connection)
        self.logger.info(f"Applying Spark tunings for table: {table_name}")

        spark = connection

        try:
            from benchbox.core.tuning.interface import TuningType

            if self.table_format == "delta":
                sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
                if sort_columns:
                    sorted_cols = sorted(sort_columns, key=lambda col: col.order)
                    column_names = [col.name for col in sorted_cols]
                    zorder_cols = ", ".join(column_names)
                    try:
                        spark.sql(f"OPTIMIZE {table_name} ZORDER BY ({zorder_cols})")
                        self.logger.info(f"Applied Z-ORDER optimization for {table_name}: {zorder_cols}")
                    except Exception as e:
                        self.logger.warning(f"Failed to apply Z-ORDER for {table_name}: {e}")

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                self.logger.info(f"Partitioning for {table_name}: {', '.join(column_names)}")

        except ImportError:
            self.logger.warning("Tuning interface not available - skipping tuning application")

    def _get_existing_tables(self, connection: Any) -> list[str]:
        return list_spark_tables(connection)

    def analyze_table(self, connection: Any, table_name: str) -> None:
        analyze_spark_table(connection, table_name, logger=self.logger)


_build_spark_config = make_registered_platform_config_builder(
    "spark",
    __name__,
    "Apache Spark",
    "pyspark",
    [
        "master",
        "app_name",
        "deploy_mode",
        "driver_memory",
        "executor_memory",
        "executor_cores",
        "num_executors",
        "shuffle_partitions",
        "broadcast_threshold",
        "adaptive_enabled",
        "table_format",
        "enable_hive",
        "spark_config",
        "java_home",
        "staging_root",
    ],
)
