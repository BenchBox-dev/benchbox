# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    pass

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.base import DriverIsolationCapability, PlatformAdapter
from benchbox.platforms.base.cloud_spark import (
    CloudSparkConfigMixin,
    CloudSparkStaging,
    SparkExternalTableMixin,
    SparkTuningMixin,
)
from benchbox.platforms.base.cloud_spark.config import CloudPlatform
from benchbox.platforms.base.config_utils import make_platform_config_builder
from benchbox.platforms.base.phase_tracking import _resolve_benchmark_table_names
from benchbox.utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)

try:
    import boto3
    from botocore.exceptions import ClientError, ParamValidationError

    BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None
    ClientError = Exception
    ParamValidationError = Exception
    BOTO3_AVAILABLE = False

logger = logging.getLogger(__name__)


class AthenaSparkSessionState:
    CREATING = "CREATING"
    CREATED = "CREATED"
    IDLE = "IDLE"
    BUSY = "BUSY"
    TERMINATING = "TERMINATING"
    TERMINATED = "TERMINATED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"

    READY_STATES = {IDLE, CREATED}

    TERMINAL_STATES = {TERMINATED, FAILED}


class AthenaSparkCalculationState:
    CREATING = "CREATING"
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCELING = "CANCELING"
    CANCELED = "CANCELED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    TERMINAL_STATES = {COMPLETED, FAILED, CANCELED}

    SUCCESS_STATES = {COMPLETED}


class AthenaSparkAdapter(CloudSparkConfigMixin, SparkTuningMixin, SparkExternalTableMixin, PlatformAdapter):
    plan_capture_phase_eligible = True
    external_staging_instruction = "Set the s3_staging_dir platform option to s3://bucket/path."

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

    cloud_platform = CloudPlatform.EMR

    def __init__(
        self,
        workgroup: str | None = None,
        s3_staging_dir: str | None = None,
        region: str = "us-east-1",
        database: str | None = None,
        runtime_version: str | None = None,
        engine_version: str | None = None,
        session_idle_timeout_minutes: int = 15,
        coordinator_dpu_size: int = 1,
        max_concurrent_dpus: int = 20,
        default_executor_dpu_size: int = 1,
        timeout_minutes: int = 60,
        notebook_version: str | None = None,
        table_format: str | None = None,
        **kwargs: Any,
    ) -> None:
        if not BOTO3_AVAILABLE:
            deps_satisfied, missing = check_platform_dependencies("athena-spark")
            if not deps_satisfied:
                raise ConfigurationError(get_dependency_error_message("athena-spark", missing))

        if engine_version is not None:
            from benchbox.core.compute_resource import warn_deprecated_compute_alias

            warn_deprecated_compute_alias("athena-spark", "engine_version", "runtime_version")
            if runtime_version is not None and runtime_version != engine_version:
                raise ConfigurationError(
                    f"Conflicting Athena Spark runtime versions: runtime_version='{runtime_version}' "
                    f"disagrees with engine_version='{engine_version}'. Keep only runtime_version."
                )
            runtime_version = engine_version
        if not workgroup:
            raise ConfigurationError("workgroup is required for Athena Spark. Must be a Spark-enabled workgroup.")

        if not s3_staging_dir:
            raise ConfigurationError("s3_staging_dir is required (e.g., s3://bucket/path)")

        if not s3_staging_dir.startswith("s3://"):
            raise ConfigurationError(f"Invalid S3 path: {s3_staging_dir}. Must start with s3://")

        s3_parts = s3_staging_dir[5:].split("/", 1)
        self.s3_bucket = s3_parts[0]
        self.s3_prefix = s3_parts[1] if len(s3_parts) > 1 else ""

        self.workgroup = workgroup
        self.s3_staging_dir = s3_staging_dir.rstrip("/")
        self.region = region
        self.database = database or "benchbox"
        self.runtime_version = runtime_version
        self.session_idle_timeout_minutes = session_idle_timeout_minutes
        self.coordinator_dpu_size = coordinator_dpu_size
        self.max_concurrent_dpus = max_concurrent_dpus
        self.default_executor_dpu_size = default_executor_dpu_size
        self.timeout_minutes = timeout_minutes
        self.notebook_version = notebook_version
        self.table_format = table_format or "parquet"

        self._staging: CloudSparkStaging | None = None
        try:
            self._staging = CloudSparkStaging.from_uri(self.s3_staging_dir)
        except Exception as e:
            logger.warning(f"Failed to initialize S3 staging: {e}")

        self._athena_client: Any = None
        self._glue_client: Any = None
        self._s3_client: Any = None

        self._session_id: str | None = None

        self._query_count = 0
        self._total_execution_time_seconds = 0.0
        self._total_dpu_hours = 0.0

        self._benchmark_type: str | None = None
        self._scale_factor: float = 1.0
        self._spark_config: dict[str, str] = {}

        super().__init__(**kwargs)

    def _get_athena_client(self) -> Any:
        if self._athena_client is None:
            self._athena_client = boto3.client("athena", region_name=self.region)
        return self._athena_client

    def _get_glue_client(self) -> Any:
        if self._glue_client is None:
            self._glue_client = boto3.client("glue", region_name=self.region)
        return self._glue_client

    def _get_s3_client(self) -> Any:
        if self._s3_client is None:
            self._s3_client = boto3.client("s3", region_name=self.region)
        return self._s3_client

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        return {
            "platform": "athena-spark",
            "display_name": "Amazon Athena for Apache Spark",
            "vendor": "AWS",
            "type": "interactive_spark",
            "region": self.region,
            "workgroup": self.workgroup,
            "runtime_version": self.runtime_version,
            "runtime_version_source": "requested" if self.runtime_version else None,
            "supports_sql": True,
            "supports_dataframe": True,
            "billing_model": "DPU-hour",
            "session_id": self._session_id,
        }

    def create_connection(self, **kwargs: Any) -> Any:
        client = self._get_athena_client()

        try:
            if self._session_id:
                session_status = self._get_session_status()
                if session_status in AthenaSparkSessionState.READY_STATES:
                    logger.info(f"Using existing session: {self._session_id}")
                    return {
                        "status": "connected",
                        "session_id": self._session_id,
                        "session_state": session_status,
                    }

            logger.info(f"Starting Athena Spark session in workgroup: {self.workgroup}")

            request: dict[str, Any] = {
                "WorkGroup": self.workgroup,
                "EngineConfiguration": {
                    "CoordinatorDpuSize": self.coordinator_dpu_size,
                    "MaxConcurrentDpus": self.max_concurrent_dpus,
                    "DefaultExecutorDpuSize": self.default_executor_dpu_size,
                },
                "SessionIdleTimeoutInMinutes": self.session_idle_timeout_minutes,
            }
            if self.notebook_version:
                request["NotebookVersion"] = self.notebook_version

            response = client.start_session(**request)

            self._session_id = response["SessionId"]
            session_state = response["State"]

            self._wait_for_session_ready()

            logger.info(f"Athena Spark session started: {self._session_id}")
            return {
                "status": "connected",
                "session_id": self._session_id,
                "session_state": session_state,
                "workgroup": self.workgroup,
            }

        except ParamValidationError as e:
            raise ConfigurationError(
                f"Invalid Athena Spark session request for workgroup '{self.workgroup}': {e}"
            ) from e
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message = e.response.get("Error", {}).get("Message", str(e))

            if error_code == "InvalidRequestException":
                raise ConfigurationError(
                    f"Invalid Athena Spark configuration: {error_message}. "
                    f"Ensure workgroup '{self.workgroup}' is Spark-enabled."
                ) from e
            raise ConfigurationError(f"Failed to start Athena Spark session: {error_message}") from e

    def _get_session_status(self) -> str:
        if not self._session_id:
            return AthenaSparkSessionState.TERMINATED

        client = self._get_athena_client()

        try:
            response = client.get_session_status(SessionId=self._session_id)
            return response["Status"]["State"]
        except Exception:
            return AthenaSparkSessionState.TERMINATED

    def _wait_for_session_ready(self, timeout_seconds: int = 300) -> None:
        client = self._get_athena_client()
        start_time = mono_time()

        while elapsed_seconds(start_time) < timeout_seconds:
            response = client.get_session_status(SessionId=self._session_id)
            state = response["Status"]["State"]

            if state in AthenaSparkSessionState.READY_STATES:
                return

            if state in AthenaSparkSessionState.TERMINAL_STATES:
                reason = response["Status"].get("StateChangeReason", "Unknown")
                raise ConfigurationError(f"Session failed: {state} - {reason}")

            time.sleep(2)

        raise ConfigurationError(f"Session startup timed out after {timeout_seconds}s")

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        database = self.database
        glue_client = self._get_glue_client()

        try:
            glue_client.get_database(Name=database)
            logger.info(f"Database '{database}' already exists")
        except glue_client.exceptions.EntityNotFoundException:
            location_uri = f"{self.s3_staging_dir}/databases/{database}/"
            glue_client.create_database(
                DatabaseInput={
                    "Name": database,
                    "Description": "BenchBox benchmark database",
                    "LocationUri": location_uri,
                }
            )
            logger.info(f"Created database '{database}'")
        return elapsed_seconds(start_time)

    def _submit_calculation(
        self,
        code: str,
        code_type: str = "SQL",
        wait_for_completion: bool = True,
    ) -> tuple[str, str]:
        if not self._session_id:
            raise ConfigurationError("No active session. Call create_connection() first.")

        client = self._get_athena_client()

        if code_type == "SQL":
            code_literal = json.dumps(code)
            execution_code = f"""
spark.sql("USE {self.database}")
result = spark.sql({code_literal})
result.show(100, truncate=False)
"""
        else:
            execution_code = code

        response = client.start_calculation_execution(
            SessionId=self._session_id,
            CodeBlock=execution_code,
        )

        calculation_id = response["CalculationExecutionId"]
        state = response["State"]

        if wait_for_completion:
            state = self._wait_for_calculation_complete(calculation_id)

        return calculation_id, state

    def _wait_for_calculation_complete(
        self,
        calculation_id: str,
        timeout_seconds: int | None = None,
    ) -> str:
        client = self._get_athena_client()
        timeout = timeout_seconds or self.timeout_minutes * 60
        start_time = mono_time()

        while elapsed_seconds(start_time) < timeout:
            response = client.get_calculation_execution_status(CalculationExecutionId=calculation_id)
            state = response["Status"]["State"]

            if state in AthenaSparkCalculationState.TERMINAL_STATES:
                return state

            time.sleep(2)

        raise RuntimeError(f"Calculation timed out after {timeout}s")

    def _get_calculation_result(self, calculation_id: str) -> list[dict[str, Any]]:
        client = self._get_athena_client()

        try:
            response = client.get_calculation_execution(CalculationExecutionId=calculation_id)

            result = response.get("Result", {})
            result_s3_uri = result.get("ResultS3Uri")

            if result_s3_uri:
                return self._fetch_results_from_s3(result_s3_uri)

            stdout = result.get("StdOutS3Uri")
            if stdout:
                return self._fetch_results_from_s3(stdout)

            return []

        except Exception as e:
            logger.warning(f"Could not get calculation results: {e}")
            return []

    def _fetch_results_from_s3(self, s3_uri: str) -> list[dict[str, Any]]:
        s3_client = self._get_s3_client()

        if s3_uri.startswith("s3://"):
            s3_uri = s3_uri[5:]
        bucket, key = s3_uri.split("/", 1)

        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read().decode("utf-8")

            results = []
            for line in content.strip().split("\n"):
                if line:
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        results.append({"output": line})
            return results

        except Exception as e:
            logger.debug(f"Could not fetch results from {s3_uri}: {e}")
            return []

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

        if self._staging and self._staging.tables_exist(tables):
            logger.info("Tables already exist in S3 staging, skipping upload")
            table_uris = {table: self._staging.get_table_uri(table) for table in tables}
            return dict.fromkeys(tables, 0), elapsed_seconds(start_time), {"table_uris": table_uris}

        if self._staging:
            logger.info(f"Uploading {len(tables)} tables to S3 staging")
            self._staging.upload_tables(
                tables=tables,
                source_dir=source_path,
                file_format=file_format,
            )

        table_uris = {}
        for table in tables:
            table_uri = f"{self.s3_staging_dir}/tables/{table}"
            table_uris[table] = table_uri

            create_table_sql = f"""
                CREATE EXTERNAL TABLE IF NOT EXISTS {self.database}.{table}
                USING {self.table_format.upper()}
                LOCATION '{table_uri}'
            """
            self._submit_calculation(create_table_sql, code_type="SQL", wait_for_completion=True)
            logger.info(f"Created table {self.database}.{table}")

        return dict.fromkeys(tables, 0), elapsed_seconds(start_time), {"table_uris": table_uris}

    def _register_external_table(self, table_name: str, location: str, file_format: str) -> None:
        self._validate_external_identifier(table_name, "table name")
        self._validate_external_identifier(self.database, "database name")
        safe_location = self._escape_external_location(location)
        create_table_sql = f"""
            CREATE OR REPLACE TABLE {self.database}.{table_name}
            USING {file_format.upper()}
            LOCATION '{safe_location}'
        """
        calculation_id, state = self._submit_calculation(create_table_sql, code_type="SQL", wait_for_completion=True)
        if state not in AthenaSparkCalculationState.SUCCESS_STATES:
            raise RuntimeError(
                f"Athena Spark external table registration failed for "
                f"'{self.database}.{table_name}' with state: {state} (calculation {calculation_id})"
            )
        logger.info(f"Registered external table {self.database}.{table_name}")

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
            calculation_id, state = self._submit_calculation(query, code_type="SQL", wait_for_completion=True)
            elapsed = elapsed_seconds(start_time)
            self._query_count += 1
            self._total_execution_time_seconds += elapsed
            if state not in AthenaSparkCalculationState.SUCCESS_STATES:
                raise RuntimeError(f"Athena Spark calculation failed with state: {state}")
            results = self._get_calculation_result(calculation_id)
            return {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "SUCCESS",
                "execution_time_seconds": elapsed,
                "rows_returned": len(results),
                "results": results,
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
        if self._session_id:
            try:
                client = self._get_athena_client()
                client.terminate_session(SessionId=self._session_id)
                logger.info(f"Terminated session: {self._session_id}")
            except Exception as e:
                logger.warning(f"Failed to terminate session: {e}")
            finally:
                self._session_id = None

        logger.info(f"Athena Spark session closed. Executed {self._query_count} calculations.")
        if self._total_execution_time_seconds > 0:
            logger.info(f"Total execution time: {self._total_execution_time_seconds:.1f}s")

    @staticmethod
    def add_cli_arguments(parser: Any) -> None:
        group = parser.add_argument_group("Athena Spark Options")
        group.add_argument(
            "--workgroup",
            help="Spark-enabled Athena workgroup name",
        )
        group.add_argument(
            "--s3-staging-dir",
            help="S3 path for data staging (e.g., s3://bucket/path)",
        )
        group.add_argument(
            "--region",
            default="us-east-1",
            help="AWS region (default: us-east-1)",
        )
        group.add_argument(
            "--database",
            default="benchbox",
            help="Glue Data Catalog database (default: benchbox)",
        )
        group.add_argument(
            "--coordinator-dpu-size",
            type=int,
            default=1,
            help="Coordinator DPU size (default: 1)",
        )
        group.add_argument(
            "--max-concurrent-dpus",
            type=int,
            default=20,
            help="Maximum concurrent DPUs (default: 20)",
        )
        group.add_argument(
            "--session-idle-timeout",
            type=int,
            default=15,
            help="Session idle timeout in minutes (default: 15)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> AthenaSparkAdapter:
        from benchbox.core.compute_resource import normalize_compute_options

        normalized = normalize_compute_options("athena-spark", config)
        config = {key: value for key, value in normalized.items() if value is not None}
        params = {
            "workgroup": config.get("compute_resource") or config.get("workgroup"),
            "s3_staging_dir": config.get("s3_staging_dir"),
            "region": config.get("region", "us-east-1"),
            "database": config.get("database", "benchbox"),
            "runtime_version": config.get("runtime_version"),
            "engine_version": config.get("engine_version"),
            "session_idle_timeout_minutes": config.get("session_idle_timeout_minutes", 15),
            "coordinator_dpu_size": config.get("coordinator_dpu_size", 1),
            "max_concurrent_dpus": config.get("max_concurrent_dpus", 20),
            "default_executor_dpu_size": config.get("default_executor_dpu_size", 1),
            "timeout_minutes": config.get("timeout_minutes", 60),
            "table_format": config.get("table_format"),
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

        return cls(**params)

    def get_target_dialect(self) -> str:
        return "spark"


_build_athena_spark_config = make_platform_config_builder(
    "athena-spark",
    __name__,
    "Amazon Athena for Apache Spark",
    "boto3",
    [
        "compute_resource",
        "s3_staging_dir",
        "region",
        "database",
        "runtime_version",
        "engine_version",
        "session_idle_timeout_minutes",
        "coordinator_dpu_size",
        "max_concurrent_dpus",
        "default_executor_dpu_size",
        "timeout_minutes",
    ],
)
