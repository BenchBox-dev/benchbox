from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.core.results.timer_scope import (
    REQUEST_KIND_QUERY,
    TIMING_METHOD_QUERY_ONLY,
    RequestLedger,
    outside_query_timer,
)
from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    pass

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.base import DriverIsolationCapability, PlatformAdapter
from benchbox.platforms.base.cloud_spark import SparkTuningMixin
from benchbox.utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)
from benchbox.utils.file_format import is_parquet_format

try:
    from snowflake.snowpark import Session
    from snowflake.snowpark.exceptions import SnowparkSQLException

    SNOWPARK_AVAILABLE = True
except ImportError:
    Session = None
    SnowparkSQLException = Exception
    SNOWPARK_AVAILABLE = False

logger = logging.getLogger(__name__)


class SnowparkConnectAdapter(SparkTuningMixin, PlatformAdapter):
    plan_capture_phase_eligible = True
    timing_method = TIMING_METHOD_QUERY_ONLY

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY

    def __init__(
        self,
        account: str | None = None,
        user: str | None = None,
        password: str | None = None,
        warehouse: str = "COMPUTE_WH",
        database: str = "BENCHBOX",
        schema: str = "PUBLIC",
        role: str | None = None,
        authenticator: str | None = None,
        private_key_path: str | None = None,
        private_key_passphrase: str | None = None,
        warehouse_size: str = "MEDIUM",
        session_parameters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not SNOWPARK_AVAILABLE:
            deps_satisfied, missing = check_platform_dependencies("snowpark-connect")
            if not deps_satisfied:
                raise ConfigurationError(get_dependency_error_message("snowpark-connect", missing))

        if not account:
            raise ConfigurationError(
                "account is required for Snowpark Connect. "
                "Provide your Snowflake account identifier (e.g., 'xy12345.us-east-1')."
            )

        if not user:
            raise ConfigurationError("user is required for Snowpark Connect.")

        if not password and not private_key_path:
            raise ConfigurationError("Either password or private_key_path is required for authentication.")

        self.account = account
        self.user = user
        self.password = password
        self.warehouse = warehouse
        self.database = database
        self.schema = schema
        self.role = role
        self.authenticator = authenticator
        self.private_key_path = private_key_path
        self.private_key_passphrase = private_key_passphrase
        self.warehouse_size = warehouse_size
        self.session_parameters = session_parameters or {}

        self._session: Any = None

        self._query_count = 0
        self._total_execution_time_seconds = 0.0

        self._benchmark_type: str | None = None
        self._scale_factor: float = 1.0

        super().__init__(**kwargs)

    def _build_connection_parameters(self) -> dict[str, Any]:
        params = {
            "account": self.account,
            "user": self.user,
            "warehouse": self.warehouse,
            "database": self.database,
            "schema": self.schema,
        }

        if self.password:
            params["password"] = self.password

        if self.role:
            params["role"] = self.role

        if self.authenticator:
            params["authenticator"] = self.authenticator

        if self.private_key_path:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives import serialization

            with open(self.private_key_path, "rb") as key_file:
                private_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=(self.private_key_passphrase.encode() if self.private_key_passphrase else None),
                    backend=default_backend(),
                )
            params["private_key"] = private_key

        return params

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        return {
            "platform": "snowpark-connect",
            "display_name": "Snowpark Connect for Spark",
            "vendor": "Snowflake",
            "type": "pyspark_compatible",
            "account": self.account,
            "warehouse": self.warehouse,
            "database": self.database,
            "supports_sql": True,
            "supports_dataframe": True,
            "billing_model": "Snowflake credits",
            "limitations": [
                "RDD APIs not supported",
                "DataFrame.hint() is no-op",
                "DataFrame.repartition() is no-op",
            ],
        }

    def create_connection(self, **kwargs: Any) -> Any:
        self.fail_closed_on_force_recreate(
            platform_label="Snowpark Connect",
            database=kwargs.get("database", self.database),
            manual_hint="Drop the database manually (for example `DROP DATABASE <name>` in Snowflake)",
        )
        try:
            if self._session is not None:
                try:
                    self._session.sql("SELECT 1").collect()
                    logger.info("Using existing Snowpark session")
                    return self._session
                except Exception:
                    self._session = None

            logger.info(f"Creating Snowpark session for {self.account}")
            connection_params = self._build_connection_parameters()

            if self.session_parameters:
                connection_params["session_parameters"] = self.session_parameters

            self._session = Session.builder.configs(connection_params).create()

            result = self._session.sql("SELECT CURRENT_VERSION()").collect()
            version = result[0][0] if result else "unknown"

            logger.info(f"Connected to Snowflake {version} via Snowpark")
            return self._session

        except SnowparkSQLException as e:
            raise ConfigurationError(f"Failed to connect to Snowflake: {e}") from e
        except Exception as e:
            raise ConfigurationError(f"Snowpark session creation failed: {e}") from e

    def create_schema(self, benchmark, connection: Any) -> float:
        session = connection or self._session
        if session is None:
            raise ConfigurationError("No active session. Call create_connection() first.")

        self._session = session
        start_time = mono_time()

        session.sql(f"CREATE DATABASE IF NOT EXISTS {self.database}").collect()
        logger.info(f"Database '{self.database}' created or already exists")

        session.sql(f"USE DATABASE {self.database}").collect()

        session.sql(f"CREATE SCHEMA IF NOT EXISTS {self.schema}").collect()
        session.sql(f"USE SCHEMA {self.schema}").collect()
        logger.info(f"Schema '{self.schema}' created or already exists")
        return elapsed_seconds(start_time)

    def load_data(
        self,
        benchmark,
        connection: Any,
        data_dir: Path,
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        session = connection or self._session
        if session is None:
            raise ConfigurationError("No active session. Call create_connection() first.")

        self._session = session
        start_time = mono_time()
        source_path = Path(data_dir)
        if not source_path.exists():
            raise ConfigurationError(f"Source directory not found: {data_dir}")

        table_stats: dict[str, int] = {}
        per_table_timings: dict[str, Any] = {}

        for table in self._resolve_table_names(benchmark):
            table_start = mono_time()
            table_files = list(source_path.glob(f"{table}.*")) + list(source_path.glob(f"{table}/*.parquet"))

            if not table_files:
                logger.warning(f"No data files found for table {table}")
                continue

            if all(is_parquet_format(file_path) for file_path in table_files):
                for file_path in table_files:
                    stage_name = f"@~/{table}"
                    session.sql(f"PUT file://{file_path} {stage_name} AUTO_COMPRESS=FALSE").collect()

                df = session.read.parquet(f"@~/{table}/")
                df.write.mode("overwrite").save_as_table(table)

                count_result = session.sql(f"SELECT COUNT(*) FROM {table}").collect()
                row_count = count_result[0][0] if count_result else 0
                table_stats[table] = row_count
                logger.info(f"Loaded {row_count:,} rows into {table}")

            else:
                try:
                    session.sql(f"REMOVE @~/{table}/").collect()
                except Exception as e:
                    if "does not exist or not authorized" not in str(e):
                        raise
                for file_path in table_files:
                    stage_name = f"@~/{table}"
                    session.sql(f"PUT file://{file_path} {stage_name} OVERWRITE = TRUE").collect()

                try:
                    session.sql(f"TRUNCATE TABLE {table}").collect()
                except Exception as e:
                    if "does not exist or not authorized" not in str(e):
                        raise

                session.sql(
                    f"COPY INTO {table} FROM @~/{table}/ FILE_FORMAT = (TYPE = CSV FIELD_DELIMITER = '|') FORCE = TRUE"
                ).collect()

                count_result = session.sql(f"SELECT COUNT(*) FROM {table}").collect()
                row_count = count_result[0][0] if count_result else 0
                table_stats[table] = row_count
                logger.info(f"Loaded {row_count:,} rows into {table}")

            per_table_timings[table] = {"total_ms": elapsed_seconds(table_start) * 1000}

        return table_stats, elapsed_seconds(start_time), per_table_timings

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
        session = connection or self._session
        if session is None:
            raise ConfigurationError("No active session. Call create_connection() first.")

        self._session = session
        ledger = RequestLedger()
        start_time = mono_time()

        try:
            dataframe = session.sql(query)
            ledger.add_request(REQUEST_KIND_QUERY)
            result = dataframe.collect()
            ledger.add_request(REQUEST_KIND_QUERY)
            elapsed = elapsed_seconds(start_time)

            with outside_query_timer():
                self._query_count += 1
                self._total_execution_time_seconds += elapsed

                rows = self._rows_to_dicts(result)
                timing_fields = self.timing_fields_for(session, ledger, rows=result, elapsed_seconds=elapsed)
            result_dict = self._build_query_result_with_validation(
                query_id=query_id,
                execution_time=elapsed,
                actual_row_count=len(rows),
                first_row=rows[0] if rows else None,
                materialized_rows=result,
                timing_fields=timing_fields,
            )
            result_dict["stream_id"] = stream_id
            result_dict["results"] = rows
            result_dict["benchmark_type"] = benchmark_type
            result_dict["scale_factor"] = scale_factor
            result_dict["validation_enabled"] = validate_row_count
            return result_dict

        except SnowparkSQLException as e:
            elapsed = elapsed_seconds(start_time)
            return {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "FAILED",
                "execution_time_seconds": elapsed,
                "rows_returned": 0,
                "error": str(e),
                "error_type": type(e).__name__,
            }

    @staticmethod
    def _rows_to_dicts(result: Any) -> list[dict[str, Any]]:
        if not result:
            return []
        if hasattr(result[0], "asDict"):
            return [row.asDict() for row in result]
        return [dict(enumerate(row)) for row in result]

    @staticmethod
    def _resolve_table_names(benchmark: Any) -> list[str]:
        available: list[str] | None = None
        if hasattr(benchmark, "get_available_tables") and callable(benchmark.get_available_tables):
            available = list(benchmark.get_available_tables())
        elif hasattr(benchmark, "get_table_names") and callable(benchmark.get_table_names):
            available = list(benchmark.get_table_names())
        elif hasattr(benchmark, "get_schema") and callable(benchmark.get_schema):
            schema = benchmark.get_schema()
            if isinstance(schema, dict):
                available = list(schema)
        else:
            tables = getattr(benchmark, "tables", None)
            if isinstance(tables, dict):
                available = list(tables.keys())

        if available is not None:
            if hasattr(benchmark, "get_table_loading_order") and callable(benchmark.get_table_loading_order):
                return list(benchmark.get_table_loading_order(available))
            return available

        raise ConfigurationError("Benchmark does not expose table names for Snowpark data loading")

    def execute_dataframe(
        self,
        df_operation: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        if self._session is None:
            raise ConfigurationError("No active session. Call create_connection() first.")

        start_time = mono_time()

        try:
            result = df_operation.collect()
            elapsed = elapsed_seconds(start_time)

            self._query_count += 1
            self._total_execution_time_seconds += elapsed

            if result:
                return [row.asDict() if hasattr(row, "asDict") else dict(row) for row in result]

            return []

        except SnowparkSQLException as e:
            raise RuntimeError(f"DataFrame execution failed: {e}") from e

    def get_dataframe(self, table_name: str) -> Any:
        if self._session is None:
            raise ConfigurationError("No active session. Call create_connection() first.")

        return self._session.table(table_name)

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
                logger.info("Snowpark session closed")
            except Exception as e:
                logger.warning(f"Error closing session: {e}")
            finally:
                self._session = None

        logger.info(f"Executed {self._query_count} queries.")
        if self._total_execution_time_seconds > 0:
            logger.info(f"Total execution time: {self._total_execution_time_seconds:.1f}s")

    @staticmethod
    def add_cli_arguments(parser: Any) -> None:
        group = parser.add_argument_group("Snowpark Connect Options")
        group.add_argument(
            "--account",
            help="Snowflake account identifier (e.g., xy12345.us-east-1)",
        )
        group.add_argument(
            "--user",
            help="Snowflake username",
        )
        group.add_argument(
            "--password",
            help="Snowflake password",
        )
        group.add_argument(
            "--warehouse",
            default="COMPUTE_WH",
            help="Virtual warehouse name (default: COMPUTE_WH)",
        )
        group.add_argument(
            "--database",
            default="BENCHBOX",
            help="Database name (default: BENCHBOX)",
        )
        group.add_argument(
            "--schema",
            default="PUBLIC",
            help="Schema name (default: PUBLIC)",
        )
        group.add_argument(
            "--role",
            help="Role to use for the session",
        )
        group.add_argument(
            "--warehouse-size",
            default="MEDIUM",
            help="Warehouse size (default: MEDIUM)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> SnowparkConnectAdapter:
        from benchbox.core.compute_resource import normalize_compute_options
        from benchbox.platforms.base.config_utils import forward_plan_options

        config = normalize_compute_options("snowpark-connect", config)
        params = {
            "account": config.get("account"),
            "user": config.get("user"),
            "password": config.get("password"),
            "warehouse": config.get("compute_resource") or config.get("warehouse") or "COMPUTE_WH",
            "database": config.get("database", "BENCHBOX"),
            "schema": config.get("schema", "PUBLIC"),
            "role": config.get("role"),
            "authenticator": config.get("authenticator"),
            "private_key_path": config.get("private_key_path"),
            "private_key_passphrase": config.get("private_key_passphrase"),
            "warehouse_size": config.get("compute_size") or config.get("warehouse_size") or "MEDIUM",
        }

        for key in [
            "tuning_config",
            "tuning_enabled",
            "unified_tuning_configuration",
            "tuning_source",
            "tuning_source_file",
        ]:
            if key in config:
                params[key] = config[key]

        forward_plan_options(config, params)

        if "force_recreate" in config:
            params["force_recreate"] = config["force_recreate"]
        elif config.get("force", False):
            params["force_recreate"] = True

        return cls(**params)

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self._benchmark_type = benchmark_type.lower()
        logger.info(f"Configuring Snowpark Connect for {benchmark_type} benchmark")

        if self._session:
            self._session.sql("ALTER SESSION SET USE_CACHED_RESULT = FALSE").collect()
            logger.debug("Disabled result cache for benchmarking")

    def apply_platform_optimizations(self, config: Any) -> list[str]:
        logger.debug("Snowpark tuning surface is empty; no session settings applied")
        ledger = getattr(self, "_applied_tuning_ledger", None)
        if ledger is not None and config is not None:
            requested = self._requested_platform_optimization_names(config)
            for name in requested:
                ledger.record_dropped(
                    f"platform_optimization:{name}",
                    "Snowpark does not map platform optimizations to Snowflake session settings",
                )
        return list[str]()

    @staticmethod
    def _requested_platform_optimization_names(config: Any) -> list[str]:
        names: list[str] = []
        for key in (
            "z_ordering_enabled",
            "liquid_clustering_enabled",
            "auto_optimize_enabled",
            "auto_compact_enabled",
            "bloom_filters_enabled",
            "materialized_views_enabled",
        ):
            if getattr(config, key, False):
                names.append(key)
        strategy = getattr(config, "databricks_clustering_strategy", "none")
        if strategy and strategy != "none":
            names.append(f"databricks_clustering_strategy:{strategy}")
        for key in ("z_ordering_columns", "liquid_clustering_columns", "bloom_filter_columns"):
            columns = getattr(config, key, None)
            if columns:
                names.append(f"{key}:{len(columns)}")
        return names

    def get_target_dialect(self) -> str:
        return "snowflake"
