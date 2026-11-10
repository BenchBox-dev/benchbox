# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.core.results.timer_scope import TIMING_METHOD_QUERY_ONLY, RequestLedger, outside_query_timer
from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
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
    from azure.storage.filedatalake import DataLakeServiceClient

    AZURE_DATALAKE_AVAILABLE = True
except ImportError:
    DataLakeServiceClient = None
    AZURE_DATALAKE_AVAILABLE = False

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    REQUESTS_AVAILABLE = False

logger = logging.getLogger(__name__)


class LivySessionState:
    NOT_STARTED = "not_started"
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    SHUTTING_DOWN = "shutting_down"
    ERROR = "error"
    DEAD = "dead"
    KILLED = "killed"
    SUCCESS = "success"


class FabricSparkAdapter(LivyStatementMixin, SparkTuningMixin, PlatformAdapter):
    timing_method = TIMING_METHOD_QUERY_ONLY
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

    FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
    ONELAKE_DFS_BASE = "https://onelake.dfs.fabric.microsoft.com"

    def __init__(
        self,
        workspace_id: str | None = None,
        lakehouse_id: str | None = None,
        tenant_id: str | None = None,
        livy_endpoint: str | None = None,
        onelake_path: str | None = None,
        spark_pool_name: str | None = None,
        timeout_minutes: int = 60,
        spark_config: dict[str, str] | None = None,
        table_format: str | None = None,
        adaptive_enabled: bool = True,
        **kwargs: Any,
    ) -> None:
        if not AZURE_IDENTITY_AVAILABLE:
            deps_satisfied, missing = check_platform_dependencies("fabric-spark")
            if not deps_satisfied:
                raise ConfigurationError(get_dependency_error_message("fabric-spark", missing))

        if not workspace_id:
            raise ConfigurationError("workspace_id is required (Fabric workspace GUID)")

        if not lakehouse_id:
            raise ConfigurationError("lakehouse_id is required (Fabric Lakehouse GUID)")

        self.workspace_id = workspace_id
        self.lakehouse_id = lakehouse_id
        self.tenant_id = tenant_id
        self.spark_pool_name = spark_pool_name
        self.timeout_minutes = timeout_minutes
        self.table_format = table_format or "delta"
        self.user_spark_config = spark_config or {}
        self.adaptive_enabled = adaptive_enabled

        self.livy_endpoint = livy_endpoint or self._derive_livy_endpoint()

        self.onelake_path = onelake_path or self._derive_onelake_path()

        self._staging: CloudSparkStaging | None = None
        try:
            staging_uri = (
                f"abfss://{self.workspace_id}@onelake.dfs.fabric.microsoft.com/{self.lakehouse_id}/Files/benchbox"
            )
            self._staging = CloudSparkStaging.from_uri(staging_uri)
        except Exception as e:
            logger.warning("Failed to initialize OneLake staging: %s", e)

        self._token_provider = AzureTokenProvider(
            scope="https://api.fabric.microsoft.com/.default",
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
        return f"https://api.fabric.microsoft.com/v1/workspaces/{self.workspace_id}/lakehouses/{self.lakehouse_id}/livyApi/versions/2023-12-01/sessions"

    def _derive_onelake_path(self) -> str:
        return f"abfss://{self.workspace_id}@onelake.dfs.fabric.microsoft.com/{self.lakehouse_id}"

    def _get_access_token(self) -> str:
        return self._token_provider.access_token()

    def _get_headers(self) -> dict[str, str]:
        return self._token_provider.auth_headers()

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        return {
            "platform": "fabric-spark",
            "display_name": "Microsoft Fabric Spark",
            "vendor": "Microsoft",
            "type": "managed_spark",
            "workspace_id": self.workspace_id,
            "lakehouse_id": self.lakehouse_id,
            "spark_pool": self.spark_pool_name,
            "supports_sql": True,
            "supports_dataframe": True,
            "billing_model": "Capacity Units (CU)",
            "storage": "OneLake",
        }

    def _create_session(self) -> int:
        if not REQUESTS_AVAILABLE:
            raise ConfigurationError("requests package is required for Fabric Spark")

        session_config: dict[str, Any] = {
            "kind": "spark",
            "conf": spark_aqe_conf_entries(self.adaptive_enabled),
        }

        if self.table_format == "iceberg":
            session_config["conf"]["spark.sql.extensions"] = (
                "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
            )
            session_config["conf"]["spark.sql.catalog.spark_catalog"] = "org.apache.iceberg.spark.SparkSessionCatalog"
            session_config["conf"]["spark.sql.catalog.spark_catalog.type"] = "hive"

        if self._spark_config:
            session_config["conf"].update(self._spark_config)

        session_config["conf"].update(self.user_spark_config)

        if self.spark_pool_name:
            session_config["name"] = f"benchbox-{self.spark_pool_name}"

        logger.info("Creating Livy session in workspace %s", self.workspace_id)

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

        self._wait_for_session_state(session_id, [LivySessionState.IDLE])
        self._session_created_by_us = True

        logger.info("Livy session created: %s", session_id)
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
            if state in [LivySessionState.ERROR, LivySessionState.DEAD, LivySessionState.KILLED]:
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
                    if session["state"] == LivySessionState.IDLE:
                        return self._session_id
                    if session["state"] == LivySessionState.BUSY:
                        self._wait_for_session_state(self._session_id, [LivySessionState.IDLE])
                        return self._session_id
                    logger.warning("Session %s is in state %s, closing", self._session_id, session.get("state"))
                    try:
                        requests.delete(session_url, headers=self._get_headers(), timeout=30)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning("Session %s is invalid: %s", self._session_id, e)
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

            workspace_url = f"{self.FABRIC_API_BASE}/workspaces/{self.workspace_id}"
            response = requests.get(
                workspace_url,
                headers=self._get_headers(),
                timeout=30,
            )

            if response.status_code == 200:
                workspace = response.json()
                logger.info("Connected to Fabric workspace: %s", workspace.get("displayName", self.workspace_id))
                return {
                    "status": "connected",
                    "workspace_id": self.workspace_id,
                    "workspace_name": workspace.get("displayName"),
                    "lakehouse_id": self.lakehouse_id,
                }
            elif response.status_code == 401:
                raise ConfigurationError(
                    "Authentication failed. Ensure Azure credentials are configured "
                    "(az login, service principal, or managed identity)"
                )
            elif response.status_code == 403:
                raise ConfigurationError(
                    f"Access denied to workspace {self.workspace_id}. Check workspace permissions."
                )
            elif response.status_code == 404:
                raise ConfigurationError(
                    f"Workspace {self.workspace_id} not found. Verify the workspace ID is correct."
                )
            else:
                raise ConfigurationError(f"Failed to access workspace: {response.status_code} - {response.text}")
        except requests.exceptions.RequestException as e:
            raise ConfigurationError(f"Failed to connect to Fabric: {e}") from e

    def create_schema(self, benchmark: Any, connection: Any) -> float:
        start_time = mono_time()
        database = getattr(benchmark, "name", None) or "default"

        logger.info("Using Lakehouse schema: %s", database)

        if database != "default":
            self._execute_statement(
                f"CREATE DATABASE IF NOT EXISTS {database}",
                kind="sql",
            )

        return elapsed_seconds(start_time)

    def load_data(
        self,
        benchmark: Any,
        connection: Any,
        data_dir: Path,
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        start_time = mono_time()

        data_files = getattr(benchmark, "tables", {}) or {}
        tables = list(data_files.keys()) if isinstance(data_files, Mapping) else []
        explicit_data_files = data_files if self._has_explicit_data_files(data_files) else None
        source_path = Path(data_dir)

        if not source_path.exists():
            raise ConfigurationError(f"Source directory not found: {data_dir}")

        if self._staging is None:
            raise ConfigurationError(
                "OneLake staging is unavailable; data upload cannot proceed. "
                "Check Azure credentials and network connectivity."
            )

        if self._staging.tables_exist(tables):
            logger.info("Tables already exist in OneLake, skipping upload")
        else:
            logger.info("Uploading %d tables to OneLake", len(tables))
            if explicit_data_files is not None:
                self._staging.upload_data_files(explicit_data_files)
            else:
                self._staging.upload_tables(
                    tables=tables,
                    source_dir=source_path,
                    file_format="parquet",
                )

        per_table_timings: dict[str, Any] = {}
        for table in tables:
            tbl_start = mono_time()
            table_uri = f"{self.onelake_path}/Files/benchbox/tables/{table}"
            create_sql = f"""
                CREATE TABLE IF NOT EXISTS {table}
                USING {self.table_format.upper()}
                LOCATION '{table_uri}'
            """
            try:
                self._execute_statement(create_sql, kind="sql")
                per_table_timings[table] = {"total_ms": elapsed_seconds(tbl_start) * 1000}
                logger.debug("Created %s table: %s", self.table_format, table)
            except Exception as e:
                raise RuntimeError(f"Failed to create Fabric Spark table {table}: {e}") from e

        return {}, elapsed_seconds(start_time), per_table_timings or None

    @staticmethod
    def _has_explicit_data_files(data_files: Any) -> bool:
        if not isinstance(data_files, Mapping) or not data_files:
            return False

        for table_files in data_files.values():
            if isinstance(table_files, (str, Path)):
                continue
            if not isinstance(table_files, Sequence) or not table_files:
                return False
            if any(not isinstance(path_like, (str, Path)) for path_like in table_files):
                return False

        return True

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str | None = None,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        ledger = RequestLedger()
        start_time = mono_time()

        try:
            result, execution_time = self._execute_statement_measured(query, ledger, kind="sql")
            data = result.get("data", {})
            values = data.get("values", []) if isinstance(data, dict) else []
            with outside_query_timer():
                timing_fields = self.timing_fields_for(connection, ledger, rows=values, elapsed_seconds=execution_time)
            return {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "SUCCESS",
                "execution_time_seconds": execution_time,
                "rows_returned": len(values),
                **timing_fields,
            }
        except Exception as exc:
            return {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "FAILED",
                "execution_time_seconds": elapsed_seconds(start_time),
                "rows_returned": 0,
                "error": str(exc),
                "error_type": type(exc).__name__,
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
                logger.info("Closed Livy session: %s", self._session_id)
            except Exception as e:
                logger.warning("Failed to close session: %s", e)
            finally:
                self._session_id = None

    def get_target_dialect(self) -> str:
        return "spark"

    def configure_for_benchmark(
        self,
        connection: Any,
        benchmark_type: str,
        scale_factor: float | None = None,
        **options: Any,
    ) -> None:
        self._benchmark_type = benchmark_type.lower()
        if scale_factor is not None:
            self._scale_factor = scale_factor

        if self._benchmark_type == "tpch":
            config = SparkConfigOptimizer.for_tpch(
                scale_factor=self._scale_factor,
                platform=CloudPlatform.FABRIC,
                adaptive_enabled=self.adaptive_enabled,
            )
        elif self._benchmark_type == "tpcds":
            config = SparkConfigOptimizer.for_tpcds(
                scale_factor=self._scale_factor,
                platform=CloudPlatform.FABRIC,
                adaptive_enabled=self.adaptive_enabled,
            )
        elif self._benchmark_type == "ssb":
            config = SparkConfigOptimizer.for_ssb(
                scale_factor=self._scale_factor,
                platform=CloudPlatform.FABRIC,
                adaptive_enabled=self.adaptive_enabled,
            )
        else:
            config = SparkConfigOptimizer.for_tpch(
                scale_factor=self._scale_factor,
                platform=CloudPlatform.FABRIC,
                adaptive_enabled=self.adaptive_enabled,
            )

        self._spark_config = config.to_dict()
        logger.info("Configured for %s at SF=%s", benchmark_type, self._scale_factor)

    def apply_platform_tuning(
        self,
        config: PlatformOptimizationConfiguration,
    ) -> None:
        if hasattr(config, "spark_config") and config.spark_config:
            self._spark_config.update(config.spark_config)

    def apply_unified_tuning(
        self,
        unified_config: UnifiedTuningConfiguration,
        connection: Any,
    ) -> None:
        if hasattr(unified_config, "platform_optimization"):
            self.apply_platform_tuning(unified_config.platform_optimization)

    @classmethod
    def add_cli_arguments(cls, parser: Any) -> None:
        group = parser.add_argument_group("Fabric Spark Arguments")
        group.add_argument(
            "--workspace-id",
            help="Fabric workspace GUID",
            dest="workspace_id",
        )
        group.add_argument(
            "--lakehouse-id",
            help="Fabric Lakehouse GUID",
            dest="lakehouse_id",
        )
        group.add_argument(
            "--tenant-id",
            help="Azure tenant ID",
            dest="tenant_id",
        )
        group.add_argument(
            "--spark-pool",
            help="Spark pool name",
            dest="spark_pool_name",
        )
        group.add_argument(
            "--timeout",
            type=int,
            default=60,
            help="Statement timeout in minutes (default: 60)",
            dest="timeout_minutes",
        )
        group.add_argument(
            "--adaptive-enabled",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Enable or disable Adaptive Query Execution (AQE)",
            dest="adaptive_enabled",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> FabricSparkAdapter:
        from benchbox.core.compute_resource import normalize_compute_options
        from benchbox.platforms.base.config_utils import forward_plan_options

        config = normalize_compute_options("fabric-spark", config)
        params: dict[str, Any] = {
            "workspace_id": config.get("workspace_id"),
            "lakehouse_id": config.get("lakehouse_id"),
            "tenant_id": config.get("tenant_id"),
            "livy_endpoint": config.get("livy_endpoint"),
            "onelake_path": config.get("onelake_path"),
            "spark_pool_name": config.get("compute_resource") or config.get("spark_pool_name"),
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
