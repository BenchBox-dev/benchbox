from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        ForeignKeyConfiguration,
        PlatformOptimizationConfiguration,
        PrimaryKeyConfiguration,
        UnifiedTuningConfiguration,
    )

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms._spark_helpers import adaptive_enabled_from_config, spark_aqe_conf_entries
from benchbox.platforms.azure._credentials import AzureTokenProvider
from benchbox.platforms.azure._livy_mixin import LivyStatementMixin
from benchbox.platforms.base import DriverIsolationCapability, PlatformAdapter
from benchbox.platforms.base.cloud_spark import (
    CloudSparkStaging,
    SparkConfigOptimizer,
    SparkTuningMixin,
)
from benchbox.platforms.base.cloud_spark.config import CloudPlatform
from benchbox.platforms.base.phase_tracking import _resolve_benchmark_table_names
from benchbox.utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)

try:
    from azure.identity import DefaultAzureCredential

    AZURE_IDENTITY_AVAILABLE = True
except ImportError:
    DefaultAzureCredential = None
    AZURE_IDENTITY_AVAILABLE = False

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    REQUESTS_AVAILABLE = False

logger = logging.getLogger(__name__)


class SynapseLivySessionState:
    NOT_STARTED = "not_started"
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    SHUTTING_DOWN = "shutting_down"
    ERROR = "error"
    DEAD = "dead"
    KILLED = "killed"
    SUCCESS = "success"


class SynapseSparkAdapter(LivyStatementMixin, SparkTuningMixin, PlatformAdapter):
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

    def __init__(
        self,
        workspace_name: str | None = None,
        spark_pool_name: str | None = None,
        storage_account: str | None = None,
        storage_container: str | None = None,
        storage_path: str | None = None,
        tenant_id: str | None = None,
        livy_endpoint: str | None = None,
        timeout_minutes: int = 60,
        spark_config: dict[str, str] | None = None,
        table_format: str | None = None,
        adaptive_enabled: bool = True,
        **kwargs: Any,
    ) -> None:
        if not AZURE_IDENTITY_AVAILABLE:
            deps_satisfied, missing = check_platform_dependencies("synapse-spark")
            if not deps_satisfied:
                raise ConfigurationError(get_dependency_error_message("synapse-spark", missing))

        if not workspace_name:
            raise ConfigurationError("workspace_name is required (Synapse workspace name)")

        if not spark_pool_name:
            raise ConfigurationError("spark_pool_name is required (Synapse Spark pool name)")

        if not storage_account:
            raise ConfigurationError("storage_account is required (ADLS Gen2 storage account name)")

        if not storage_container:
            raise ConfigurationError("storage_container is required (ADLS Gen2 container name)")

        self.workspace_name = workspace_name
        self.spark_pool_name = spark_pool_name
        self.storage_account = storage_account
        self.storage_container = storage_container
        self.storage_path = storage_path or "benchbox"
        self.tenant_id = tenant_id
        self.timeout_minutes = timeout_minutes
        self.table_format = table_format or "parquet"
        self.database = kwargs.get("database", "default")
        self.user_spark_config = spark_config or {}
        self.adaptive_enabled = adaptive_enabled

        self.livy_endpoint = livy_endpoint or self._derive_livy_endpoint()

        self.adls_uri = (
            f"abfss://{self.storage_container}@{self.storage_account}.dfs.core.windows.net/{self.storage_path}"
        )

        self._staging: CloudSparkStaging | None = None
        try:
            self._staging = CloudSparkStaging.from_uri(self.adls_uri)
        except Exception as e:
            logger.warning(f"Failed to initialize ADLS staging: {e}")

        self._token_provider = AzureTokenProvider(
            scope="https://dev.azuresynapse.net/.default",
            credential_class=DefaultAzureCredential,
            tenant_id=self.tenant_id,
        )

        self._session_id: int | None = None
        self._session_created_by_us = False

        self._query_count = 0
        self._total_statement_time_seconds = 0.0

        self._benchmark_type: str | None = None
        self._scale_factor: float = 1.0
        self._spark_config: dict[str, str] = {}

        super().__init__(**kwargs)

    def _derive_livy_endpoint(self) -> str:
        return f"https://{self.workspace_name}.dev.azuresynapse.net/livyApi/versions/2019-11-01-preview/sparkPools/{self.spark_pool_name}/sessions"

    def _get_access_token(self) -> str:
        return self._token_provider.access_token()

    def _get_headers(self) -> dict[str, str]:
        return self._token_provider.auth_headers()

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        return {
            "platform": "synapse-spark",
            "display_name": "Azure Synapse Analytics Spark",
            "vendor": "Microsoft",
            "type": "managed_spark",
            "workspace_name": self.workspace_name,
            "spark_pool": self.spark_pool_name,
            "storage_account": self.storage_account,
            "supports_sql": True,
            "supports_dataframe": True,
            "billing_model": "vCore-hours",
            "storage": "ADLS Gen2",
        }

    def _create_session(self) -> int:
        if not REQUESTS_AVAILABLE:
            raise ConfigurationError("requests package is required for Synapse Spark")

        session_config: dict[str, Any] = {
            "kind": "spark",
            "name": f"benchbox-{self.spark_pool_name}",
            "conf": spark_aqe_conf_entries(self.adaptive_enabled),
        }

        if self.table_format == "delta":
            session_config["conf"]["spark.sql.extensions"] = "io.delta.sql.DeltaSparkSessionExtension"
            session_config["conf"]["spark.sql.catalog.spark_catalog"] = (
                "org.apache.spark.sql.delta.catalog.DeltaCatalog"
            )
        elif self.table_format == "iceberg":
            session_config["conf"]["spark.sql.extensions"] = (
                "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
            )
            session_config["conf"]["spark.sql.catalog.spark_catalog"] = "org.apache.iceberg.spark.SparkSessionCatalog"
            session_config["conf"]["spark.sql.catalog.spark_catalog.type"] = "hive"

        if self._spark_config:
            session_config["conf"].update(self._spark_config)

        session_config["conf"].update(self.user_spark_config)

        logger.info(f"Creating Livy session in Synapse workspace {self.workspace_name}")

        response = requests.post(
            self.livy_endpoint,
            headers=self._get_headers(),
            json=session_config,
            timeout=60,
        )

        if response.status_code not in (200, 201):
            raise ConfigurationError(f"Failed to create Livy session: {response.status_code} - {response.text}")

        session = response.json()
        session_id = session["id"]

        self._wait_for_session_state(session_id, [SynapseLivySessionState.IDLE])
        self._session_created_by_us = True

        logger.info(f"Livy session created: {session_id}")
        return session_id

    def _wait_for_session_state(
        self,
        session_id: int,
        target_states: list[str],
        timeout_seconds: int = 600,
    ) -> str:
        start_time = mono_time()
        session_url = f"{self.livy_endpoint}/{session_id}"

        while elapsed_seconds(start_time) < timeout_seconds:
            response = requests.get(
                session_url,
                headers=self._get_headers(),
                timeout=30,
            )

            if response.status_code != 200:
                raise ConfigurationError(f"Failed to get session status: {response.status_code}")

            session = response.json()
            state = session["state"]

            if state in target_states:
                return state
            if state in [SynapseLivySessionState.ERROR, SynapseLivySessionState.DEAD, SynapseLivySessionState.KILLED]:
                raise ConfigurationError(f"Session is in {state} state")

            time.sleep(5)

        raise ConfigurationError(f"Timeout waiting for session {session_id}")

    def _ensure_session(self) -> int:
        if self._session_id is not None:
            session_url = f"{self.livy_endpoint}/{self._session_id}"
            try:
                response = requests.get(
                    session_url,
                    headers=self._get_headers(),
                    timeout=30,
                )
                if response.status_code == 200:
                    session = response.json()
                    if session["state"] == SynapseLivySessionState.IDLE:
                        return self._session_id
                    if session["state"] == SynapseLivySessionState.BUSY:
                        self._wait_for_session_state(self._session_id, [SynapseLivySessionState.IDLE])
                        return self._session_id
                    logger.warning("Session %s is in state %s, closing", self._session_id, session.get("state"))
                    try:
                        requests.delete(session_url, headers=self._get_headers(), timeout=30)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Session {self._session_id} is invalid: {e}")
                try:
                    requests.delete(session_url, headers=self._get_headers(), timeout=30)
                except Exception:
                    pass
            self._session_id = None

        self._session_id = self._create_session()
        return self._session_id

    def create_connection(self, **kwargs: Any) -> Any:
        try:
            self._get_access_token()

            pool_url = f"https://{self.workspace_name}.dev.azuresynapse.net/sparkPools/{self.spark_pool_name}"
            response = requests.get(
                pool_url,
                headers=self._get_headers(),
                timeout=30,
            )

            if response.status_code == 200:
                pool_info = response.json()
                logger.info(f"Connected to Synapse workspace: {self.workspace_name}")
                return {
                    "status": "connected",
                    "workspace_name": self.workspace_name,
                    "spark_pool": self.spark_pool_name,
                    "spark_version": pool_info.get("sparkVersion"),
                    "node_size": pool_info.get("nodeSize"),
                    "node_count": pool_info.get("nodeCount"),
                }
            elif response.status_code == 401:
                raise ConfigurationError(
                    "Authentication failed. Ensure Azure credentials are configured "
                    "(az login, service principal, or managed identity)"
                )
            elif response.status_code == 403:
                raise ConfigurationError(
                    f"Access denied to Synapse workspace {self.workspace_name}. Check workspace permissions."
                )
            elif response.status_code == 404:
                raise ConfigurationError(
                    f"Spark pool {self.spark_pool_name} not found in workspace {self.workspace_name}. "
                    "Verify the pool name is correct."
                )
            else:
                raise ConfigurationError(f"Failed to access Synapse: {response.status_code} - {response.text}")
        except requests.exceptions.RequestException as e:
            raise ConfigurationError(f"Failed to connect to Synapse: {e}") from e

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        database = self.database

        logger.info(f"Using Synapse Spark schema: {database}")

        if database != "default":
            self._execute_statement(
                f"CREATE DATABASE IF NOT EXISTS {database}",
                kind="sql",
            )
        return elapsed_seconds(start_time)

    def load_data(
        self,
        benchmark,
        connection: Any,
        data_dir: Path,
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        start_time = mono_time()
        source_path = Path(data_dir)
        tables = _resolve_benchmark_table_names(benchmark)
        file_format = self.requested_table_format or self.table_format
        if not source_path.exists():
            raise ConfigurationError(f"Source directory not found: {data_dir}")

        if self._staging:
            if self._staging.tables_exist(tables):
                logger.info("Tables already exist in ADLS, skipping upload")
            else:
                logger.info(f"Uploading {len(tables)} tables to ADLS Gen2")
                self._staging.upload_tables(
                    tables=tables,
                    source_dir=source_path,
                    file_format=file_format,
                )

        table_uris = {}
        for table in tables:
            table_uri = f"{self.adls_uri}/tables/{table}"
            table_uris[table] = table_uri

            create_sql = f"""
                CREATE TABLE IF NOT EXISTS {table}
                USING {self.table_format.upper()}
                LOCATION '{table_uri}'
            """
            try:
                self._execute_statement(create_sql, kind="sql")
                logger.debug(f"Created table: {table}")
            except Exception as e:
                raise RuntimeError(f"Failed to create Synapse Spark table {table}: {e}") from e

        return dict.fromkeys(tables, 0), elapsed_seconds(start_time), {"table_uris": table_uris}

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
        start_time = mono_time()

        try:
            result = self._execute_statement(query, kind="sql")
            execution_time = elapsed_seconds(start_time)
            data = result.get("data", {})
            schema = data.get("schema", {})
            rows = data.get("values", [])
            return {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "SUCCESS",
                "execution_time_seconds": execution_time,
                "rows_returned": len(rows),
                "columns": [f.get("name") for f in schema.get("fields", [])],
                "results": rows,
                "error": None,
            }
        except Exception as e:
            return {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "FAILED",
                "execution_time_seconds": elapsed_seconds(start_time),
                "rows_returned": 0,
                "error": str(e),
                "error_type": type(e).__name__,
            }

    def close(self) -> None:
        if self._session_id is not None and self._session_created_by_us:
            try:
                session_url = f"{self.livy_endpoint}/{self._session_id}"
                requests.delete(
                    session_url,
                    headers=self._get_headers(),
                    timeout=30,
                )
                logger.info(f"Closed Livy session: {self._session_id}")
            except Exception as e:
                logger.warning(f"Failed to close session: {e}")
            finally:
                self._session_id = None

    def get_target_dialect(self) -> str:
        return "spark"

    def configure_for_benchmark(
        self,
        benchmark: str,
        scale_factor: float | None = None,
        **options: Any,
    ) -> None:
        self._benchmark_type = benchmark.lower()
        self._scale_factor = scale_factor or 1.0

        if self._benchmark_type == "tpch":
            config = SparkConfigOptimizer.for_tpch(
                scale_factor=self._scale_factor,
                platform=CloudPlatform.SYNAPSE,
                adaptive_enabled=self.adaptive_enabled,
            )
        elif self._benchmark_type == "tpcds":
            config = SparkConfigOptimizer.for_tpcds(
                scale_factor=self._scale_factor,
                platform=CloudPlatform.SYNAPSE,
                adaptive_enabled=self.adaptive_enabled,
            )
        elif self._benchmark_type == "ssb":
            config = SparkConfigOptimizer.for_ssb(
                scale_factor=self._scale_factor,
                platform=CloudPlatform.SYNAPSE,
                adaptive_enabled=self.adaptive_enabled,
            )
        else:
            config = SparkConfigOptimizer.for_tpch(
                scale_factor=self._scale_factor,
                platform=CloudPlatform.SYNAPSE,
                adaptive_enabled=self.adaptive_enabled,
            )

        self._spark_config = config.to_dict()
        logger.info(f"Configured for {benchmark} at SF={self._scale_factor}")

    def apply_platform_tuning(
        self,
        config: PlatformOptimizationConfiguration,
    ) -> None:
        if hasattr(config, "spark_config") and config.spark_config:
            self._spark_config.update(config.spark_config)

    def apply_constraint_configuration(
        self,
        primary_keys: list[PrimaryKeyConfiguration] | None = None,
        foreign_keys: list[ForeignKeyConfiguration] | None = None,
    ) -> None:
        if primary_keys:
            logger.debug(f"Ignoring {len(primary_keys)} primary key constraints (Spark no-op)")
        if foreign_keys:
            logger.debug(f"Ignoring {len(foreign_keys)} foreign key constraints (Spark no-op)")

    def apply_unified_tuning(
        self,
        unified_config: UnifiedTuningConfiguration,
        connection: Any,
    ) -> None:
        if hasattr(unified_config, "platform_optimization"):
            self.apply_platform_tuning(unified_config.platform_optimization)

    @classmethod
    def add_cli_arguments(cls, parser: Any) -> None:
        parser.add_argument(
            "--workspace-name",
            help="Synapse workspace name",
            dest="workspace_name",
        )
        parser.add_argument(
            "--spark-pool",
            help="Spark pool name",
            dest="spark_pool_name",
            required=True,
        )
        parser.add_argument(
            "--storage-account",
            help="ADLS Gen2 storage account name",
            dest="storage_account",
        )
        parser.add_argument(
            "--storage-container",
            help="ADLS Gen2 container name",
            dest="storage_container",
        )
        parser.add_argument(
            "--storage-path",
            help="Path within container for staging (default: benchbox)",
            dest="storage_path",
            default="benchbox",
        )
        parser.add_argument(
            "--tenant-id",
            help="Azure tenant ID",
            dest="tenant_id",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=60,
            help="Statement timeout in minutes (default: 60)",
            dest="timeout_minutes",
        )
        parser.add_argument(
            "--adaptive-enabled",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Enable or disable Adaptive Query Execution (AQE)",
            dest="adaptive_enabled",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> SynapseSparkAdapter:
        from benchbox.core.compute_resource import normalize_compute_options
        from benchbox.platforms.base.config_utils import forward_plan_options

        config = normalize_compute_options("synapse-spark", config)
        params: dict[str, Any] = {
            "workspace_name": config.get("workspace_name"),
            "spark_pool_name": config.get("compute_resource") or config.get("spark_pool_name"),
            "storage_account": config.get("storage_account"),
            "storage_container": config.get("storage_container"),
            "storage_path": config.get("storage_path"),
            "tenant_id": config.get("tenant_id"),
            "livy_endpoint": config.get("livy_endpoint"),
            "timeout_minutes": config.get("timeout_minutes", 60),
            "spark_config": config.get("spark_config"),
            "table_format": config.get("table_format"),
            "adaptive_enabled": adaptive_enabled_from_config(config),
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

        return cls(**params)
