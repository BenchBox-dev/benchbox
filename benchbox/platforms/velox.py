from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from benchbox.core.deployment import deployment_class, select_adapter_deployment
from benchbox.core.execution_engine import ExecutionEngineReceipt, ObservedSource
from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE
from benchbox.utils.clock import elapsed_seconds, mono_time

from ..utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)
from ._spark_helpers import (
    SPARK_CBO_KEYS,
    SparkLikeAdapterMixin,
    analyze_spark_table,
    is_spark_connect_reachable,
    list_spark_tables,
    optimize_spark_table_definition,
    parse_spark_connect_endpoint,
    purge_orphaned_warehouse_directory,
    run_spark_schema_creation_loop,
    spark_aqe_conf_entries,
    validate_spark_identifier,
)
from .base import DriverIsolationCapability, PlatformAdapter
from .base.config_utils import make_registered_platform_config_builder
from .base.spark_execution_mixin import SparkDataLoadMixin, SparkQueryExecutionMixin

try:
    from pyspark.sql import SparkSession
except ImportError:
    SparkSession = None

_GLUTEN_PLUGIN_CLASS = "org.apache.gluten.GlutenPlugin"
_COLUMNAR_SHUFFLE_MANAGER = "org.apache.spark.shuffle.sort.ColumnarShuffleManager"

SUPPORTED_VELOX_DEPLOYMENTS = frozenset({"local", "remote"})

_SUPPORTED_TABLE_FORMATS = frozenset({"parquet", "orc", "delta", "iceberg", "hudi"})

_CONNECTOR_JAR_FORMATS = frozenset({"delta", "iceberg", "hudi"})

_TABLE_FORMAT_SPARK_CONF: dict[str, dict[str, str]] = {
    "delta": {
        "spark.sql.extensions": "io.delta.sql.DeltaSparkSessionExtension",
        "spark.sql.catalog.spark_catalog": "org.apache.spark.sql.delta.catalog.DeltaCatalog",
    },
    "iceberg": {
        "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        "spark.sql.catalog.spark_catalog": "org.apache.iceberg.spark.SparkSessionCatalog",
        "spark.sql.catalog.spark_catalog.type": "hive",
    },
    "hudi": {
        "spark.sql.extensions": "org.apache.spark.sql.hudi.HoodieSparkSessionExtension",
        "spark.sql.catalog.spark_catalog": "org.apache.spark.sql.hudi.catalog.HoodieCatalog",
    },
}


_WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _is_local_jar_path(jar: str) -> bool:
    if "://" in jar:
        return False
    return bool(_WINDOWS_DRIVE_PATH.match(jar)) or ":" not in jar


class VeloxAdapter(SparkLikeAdapterMixin, SparkDataLoadMixin, SparkQueryExecutionMixin, PlatformAdapter):
    plan_capture_phase_eligible = True
    default_service_port = 50051

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

    _requires_csv_extension: bool = False

    _df_caching_supported: bool = False
    _catalog_clear_cache_supported: bool = False

    @classmethod
    def _validate_deployment(cls, deployment: object) -> str:
        normalized = str(deployment).strip().lower()
        if normalized not in SUPPORTED_VELOX_DEPLOYMENTS:
            supported = ", ".join(sorted(SUPPORTED_VELOX_DEPLOYMENTS))
            hint = ""
            if normalized == "docker":
                hint = (
                    " Docker is packaging infrastructure for local development, not a deployment mode;"
                    " use deployment='local' inside the container."
                )
            raise ValueError(f"Unsupported Velox deployment '{normalized}'. Supported deployments: {supported}.{hint}")
        return normalized

    def __init__(self, **config):
        super().__init__(**config)

        if not SparkSession:
            available, missing = check_platform_dependencies("velox")
            if not available:
                error_msg = get_dependency_error_message("velox", missing)
                raise ImportError(error_msg)

        self._dialect = "spark"

        for spelling in ("deployment", "deployment_mode"):
            raw = config.get(spelling)
            if isinstance(raw, str) and raw.strip().lower() == "docker":
                self._validate_deployment(raw)
        self.deployment = self._validate_deployment(select_adapter_deployment("velox", config, "local"))
        self.deployment_selected = self.deployment
        self.deployment_selected_class = deployment_class("velox", self.deployment)

        if self.deployment == "local":
            self._df_caching_supported = True
            self._catalog_clear_cache_supported = True

        self.endpoint = config.get("endpoint") or "sc://localhost:50051"

        self.gluten_jar_path = config.get("gluten_jar_path") or ""

        self.gluten_version = config.get("gluten_version") or "1.6.0"

        self.offheap_size = config.get("offheap_size") or "8g"

        self.app_name = config.get("app_name") or "BenchBox-Velox"
        self.database = config.get("database") or "default"
        self.driver_memory = config.get("driver_memory") or "4g"
        self.shuffle_partitions = (
            config.get("shuffle_partitions") if config.get("shuffle_partitions") is not None else 200
        )
        self.adaptive_enabled = config.get("adaptive_enabled") if config.get("adaptive_enabled") is not None else True
        table_format = (config.get("table_format") or "parquet").lower()
        if table_format not in _SUPPORTED_TABLE_FORMATS:
            raise ValueError(
                f"Unsupported Velox table_format '{table_format}'. "
                f"Supported formats: {sorted(_SUPPORTED_TABLE_FORMATS)}."
            )
        self.table_format = table_format
        self.lakehouse_jars = self._parse_jar_list(config.get("lakehouse_jars"))
        self.spark_config = config.get("spark_config") or {}
        self.disable_cache = config.get("disable_cache") if config.get("disable_cache") is not None else True

        self._spark_session = None

    @property
    def platform_name(self) -> str:
        return "Velox"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        return None

    @staticmethod
    def _parse_jar_list(value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return [str(entry).strip() for entry in value if str(entry).strip()]

    def _validate_connector_jars(self) -> list[str]:
        if self.table_format not in _CONNECTOR_JAR_FORMATS:
            return []
        if not self.lakehouse_jars:
            if self.deployment == "local":
                raise ValueError(
                    f"table_format '{self.table_format}' requires connector jars in local mode: the Gluten "
                    "bundle does not ship Delta/Iceberg/Hudi SQL extensions. Supply them via "
                    "--platform-option lakehouse_jars=<jar1,jar2> (local paths, remote URIs, or Maven "
                    "coordinates). See docs/platforms/velox.md."
                )
            self.logger.warning(
                f"table_format '{self.table_format}' needs connector jars on the Spark-Connect server; "
                "lakehouse_jars is unset, so ensure the server classpath provides them."
            )
            return []
        missing = [jar for jar in self.lakehouse_jars if _is_local_jar_path(jar) and not Path(jar).exists()]
        if missing and self.deployment == "local":
            raise ValueError(
                f"lakehouse_jars not found: {', '.join(missing)}. "
                "Supply existing local paths, remote URIs, or Maven coordinates."
            )
        return self.lakehouse_jars

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.platforms.base.config_utils import build_adapter_config

        nested = config.get("options")
        merged = {**nested, **config} if isinstance(nested, dict) else config
        return cls(
            **build_adapter_config(
                merged,
                platform="velox",
                fields=[
                    "deployment",
                    "deployment_mode",
                    "_explicit_platform_options",
                    "endpoint",
                    "gluten_jar_path",
                    "gluten_version",
                    "offheap_size",
                    "app_name",
                    "driver_memory",
                    "shuffle_partitions",
                    "adaptive_enabled",
                    "table_format",
                    "lakehouse_jars",
                    "spark_config",
                    "disable_cache",
                ],
            )
        )

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info: dict[str, Any] = {
            "platform_type": "velox",
            "platform_name": "Apache Gluten + Velox",
            "deployment": self.deployment,
            "gluten_version": self.gluten_version,
            "offheap_size": self.offheap_size,
            "configuration": {
                "database": self.database,
                "table_format": self.table_format,
                "driver_memory": self.driver_memory,
                "shuffle_partitions": self.shuffle_partitions,
                "adaptive_enabled": self.adaptive_enabled,
            },
        }

        if self.deployment == "local":
            jar_name = Path(self.gluten_jar_path).name if self.gluten_jar_path else "<not set>"
            platform_info["gluten_jar"] = jar_name
        else:
            platform_info["endpoint"] = self.endpoint

        if SparkSession:
            try:
                import pyspark

                platform_info["client_library_version"] = getattr(pyspark, "__version__", None)
            except ImportError:
                platform_info["client_library_version"] = None
        else:
            platform_info["client_library_version"] = None

        if connection:
            spark = connection
            try:
                platform_info["platform_version"] = spark.version
            except Exception as e:
                self.logger.debug(f"Could not read Spark version: {e}")
                platform_info["platform_version"] = None

            try:
                explain_df = spark.sql("EXPLAIN SELECT count(*) FROM range(10)")
                plan_text = "\n".join(str(row[0]) for row in explain_df.collect())
                platform_info["velox_active"] = "VeloxColumnar" in plan_text
                platform_info["velox_probe_plan"] = plan_text[:500]
            except Exception as e:
                self.logger.debug(f"Velox active probe failed: {e}")
                platform_info["velox_active"] = None
                platform_info["velox_probe_plan"] = None
        else:
            platform_info["platform_version"] = None
            platform_info["velox_active"] = None

        velox_active = platform_info["velox_active"]
        observed = "velox" if velox_active is True else "standard" if velox_active is False else "not_captured"
        observed_source: ObservedSource = "explain" if velox_active is not None else "none"
        platform_info["configuration"]["execution_engine"] = asdict(
            ExecutionEngineReceipt(
                requested=DEFAULT_EXECUTION_ENGINE,
                applied=None,
                applied_class=None,
                resolution="platform_default",
                observed=observed,
                observed_source=observed_source,
            )
        )
        return platform_info

    def get_target_dialect(self) -> str:
        return "spark"

    def _get_spark_conf(self) -> dict[str, str]:
        conf: dict[str, str] = {
            "spark.app.name": self.app_name,
            "spark.sql.shuffle.partitions": str(self.shuffle_partitions),
        }

        conf.update(spark_aqe_conf_entries(self.adaptive_enabled))

        if self.disable_cache:
            conf["spark.sql.inMemoryColumnarStorage.enabled"] = "false"

        conf.update(_TABLE_FORMAT_SPARK_CONF.get(self.table_format, {}))

        if self.deployment == "local":
            conf["spark.plugins"] = _GLUTEN_PLUGIN_CLASS
            conf["spark.memory.offHeap.enabled"] = "true"
            conf["spark.memory.offHeap.size"] = self.offheap_size
            conf["spark.shuffle.manager"] = _COLUMNAR_SHUFFLE_MANAGER
            classpath_jars = []
            if self.gluten_jar_path:
                classpath_jars.append(self.gluten_jar_path)
            classpath_jars.extend(self._validate_connector_jars())
            if classpath_jars:
                joined = ",".join(classpath_jars)
                conf["spark.jars"] = joined
                conf["spark.driver.extraClassPath"] = joined
                conf["spark.executor.extraClassPath"] = joined

        conf.update(self.spark_config)

        if self.deployment == "local":
            sm = conf.get("spark.shuffle.manager", "")
            if sm and sm != _COLUMNAR_SHUFFLE_MANAGER:
                raise ValueError(
                    f"spark.shuffle.manager overridden to '{sm}' via spark_config, but "
                    "Velox requires ColumnarShuffleManager for shuffle acceleration. "
                    "Remove the override or set deployment='remote' to connect to a "
                    "pre-configured server."
                )

        return conf

    def _create_spark_session(self) -> Any:
        if self.deployment == "local":
            if not self.gluten_jar_path:
                raise ValueError(
                    "gluten_jar_path is required for local deployment mode. "
                    "Supply the absolute path to the Gluten Velox bundle jar via "
                    "--platform-option gluten_jar_path=<absolute-path> "
                    "(alias: --platform-option jar=<absolute-path>) or via the "
                    "gluten_jar_path config key. "
                    "See docs/platforms/velox_jar_setup.md for obtaining the jar. "
                    "On macOS/Windows use the Docker workflow in docker/velox/ instead."
                )
            if not Path(self.gluten_jar_path).exists():
                raise ValueError(
                    f"Gluten jar not found: {self.gluten_jar_path}\n"
                    "Download the correct bundle for your Spark version and host arch. "
                    "See docs/platforms/velox_jar_setup.md."
                )
            builder = SparkSession.builder.master("local[*]")
        else:
            self._ensure_server_ready()
            builder = SparkSession.builder.remote(self.endpoint)

        spark_conf = self._get_spark_conf()
        for key, value in spark_conf.items():
            builder = builder.config(key, value)

        return builder.getOrCreate()

    def _ensure_server_ready(self) -> None:
        if is_spark_connect_reachable(self.endpoint):
            return
        host, port = parse_spark_connect_endpoint(self.endpoint)
        raise RuntimeError(
            f"Cannot connect to Spark-Connect server at {self.endpoint}. "
            "Ensure a Gluten-enabled server is running and reachable.\n"
            "To start one via Docker: cd docker/velox && docker compose up -d velox-connect\n"
            f"Then retry after the server is listening on {host}:{port}."
        )

    def handle_existing_database(self, **connection_config) -> None:
        self.fail_closed_on_force_recreate(
            platform_label="Velox",
            database=connection_config.get("database", self.database),
            manual_hint=(
                "Drop the database manually (for example `DROP DATABASE <name> CASCADE` "
                "in Spark SQL against the warehouse, or remove its warehouse directory)"
            ),
        )
        super().handle_existing_database(**connection_config)

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("Velox SparkSession")
        self.log_very_verbose(f"Velox config: deployment={self.deployment}, database={self.database}")

        try:
            spark = self._create_spark_session()
            self._spark_session = spark

            self.handle_existing_database(**connection_config)

            target_database = connection_config.get("database", self.database)
            if not validate_spark_identifier(target_database):
                raise ValueError(f"Invalid database identifier: {target_database}")

            if not self.database_was_reused:
                existing = [db.name for db in spark.catalog.listDatabases()]
                if target_database.lower() not in [d.lower() for d in existing]:
                    self.log_verbose(f"Creating database: {target_database}")
                    spark.sql(f"CREATE DATABASE IF NOT EXISTS {target_database}")
                    self.logger.info(f"Created database {target_database}")

            spark.sql(f"USE {target_database}")
            self.logger.info(f"Connected to Velox ({self.deployment} mode), database={target_database}")
            self.log_operation_complete("Velox SparkSession", details=f"deployment={self.deployment}")
            return spark

        except Exception as e:
            if self._spark_session is not None:
                try:
                    self._spark_session.stop()
                except Exception:
                    pass
                self._spark_session = None
            self.logger.error(f"Failed to create Velox SparkSession: {e}")
            raise

    def close_connection(self, connection: Any) -> None:
        try:
            if connection and hasattr(connection, "stop"):
                connection.stop()
                self._spark_session = None
        except Exception as e:
            self.logger.warning(f"Error closing Velox session: {e}")

    def test_connection(self) -> bool:
        try:
            spark = self._create_spark_session()
            try:
                spark.sql("SELECT 1").collect()
                return True
            finally:
                spark.stop()
        except Exception as e:
            self.logger.debug(f"Velox connection test failed: {e}")
            return False

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        spark = connection
        try:
            if benchmark_type.lower() in ["olap", "analytics", "tpch", "tpcds"]:
                for cbo_key in SPARK_CBO_KEYS:
                    spark.conf.set(cbo_key, self.spark_config.get(cbo_key, "true"))
                self.logger.debug("Applied OLAP optimisations for Velox")
        except Exception as e:
            self.logger.warning(f"Failed to apply benchmark configuration: {e}")

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        spark = connection

        try:
            schema_sql = self._create_schema_with_tuning(benchmark, source_dialect="standard")
            statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
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
                on_pre_loop=lambda s: purge_orphaned_warehouse_directory(s, logger=self.logger),
            )
            self.logger.info("Velox schema created")
        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise

        return elapsed_seconds(start_time)

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        import re

        spark = connection
        try:
            result_df = spark.sql(f"EXPLAIN EXTENDED {query}")
            plan_rows = result_df.collect()
            if not plan_rows:
                self.logger.warning("Could not get query plan via EXPLAIN: empty plan rows")
                return None
            plan_text = "\n".join(str(row[0]) for row in plan_rows)

            has_velox = "VeloxColumnar" in plan_text
            has_fallback = bool(re.search(r"(?<!Velox)ColumnarToRow|RowToColumnar", plan_text))

            annotation_lines = []
            if has_velox:
                annotation_lines.append("# Velox native execution: YES (VeloxColumnar nodes present)")
            else:
                annotation_lines.append("# Velox native execution: NOT DETECTED (no VeloxColumnar nodes)")
            if has_fallback:
                annotation_lines.append(
                    "# JVM fallback: DETECTED (ColumnarToRow/RowToColumnar conversion nodes present)"
                )

            if annotation_lines:
                plan_text = "\n".join(annotation_lines) + "\n\n" + plan_text

            return plan_text
        except Exception as e:
            self.logger.warning("Could not get query plan via EXPLAIN: %s", e)
            return None

    def supports_tuning_type(self, tuning_type) -> bool:
        try:
            from benchbox.core.tuning.interface import TuningType

            return tuning_type in {TuningType.PARTITIONING}
        except ImportError:
            return False

    def generate_tuning_clause(self, table_tuning) -> str:
        if not table_tuning or not table_tuning.has_any_tuning():
            return ""
        clauses = []
        try:
            from benchbox.core.tuning.interface import TuningType

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                clauses.append(f"PARTITIONED BY ({', '.join(col.name for col in sorted_cols)})")
        except ImportError:
            pass
        return " ".join(clauses)

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        from benchbox.platforms.base.tuning_utils import log_partition_tunings

        log_partition_tunings(table_tuning, self.logger, "Velox")

    def _get_existing_tables(self, connection: Any) -> list[str]:
        return list_spark_tables(connection)

    def analyze_table(self, connection: Any, table_name: str) -> None:
        analyze_spark_table(connection, table_name, logger=self.logger)


_build_velox_config = make_registered_platform_config_builder(
    "velox",
    __name__,
    "Apache Gluten + Velox",
    "pyspark",
    [
        "deployment",
        "deployment_mode",
        "endpoint",
        "gluten_jar_path",
        "gluten_version",
        "offheap_size",
        "app_name",
        "driver_memory",
        "shuffle_partitions",
        "adaptive_enabled",
        "table_format",
        "spark_config",
        "disable_cache",
    ],
)
