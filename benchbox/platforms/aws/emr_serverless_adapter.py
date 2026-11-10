# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
import time
import uuid
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
from benchbox.platforms.base.cloud_spark import (
    CloudSparkConfigMixin,
    CloudSparkStaging,
    SparkExternalTableMixin,
    SparkTuningMixin,
)
from benchbox.platforms.base.cloud_spark.config import CloudPlatform
from benchbox.platforms.base.phase_tracking import _resolve_benchmark_table_names
from benchbox.utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)

try:
    import boto3
    from botocore.exceptions import ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None
    ClientError = Exception
    BOTO3_AVAILABLE = False

logger = logging.getLogger(__name__)


class EMRServerlessJobState:
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


class EMRServerlessAdapter(CloudSparkConfigMixin, SparkTuningMixin, SparkExternalTableMixin, PlatformAdapter):
    plan_capture_phase_eligible = True
    timing_method = TIMING_METHOD_QUERY_ONLY

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

    cloud_platform = CloudPlatform.EMR_SERVERLESS

    def __init__(
        self,
        application_id: str | None = None,
        s3_staging_dir: str | None = None,
        execution_role_arn: str | None = None,
        region: str = "us-east-1",
        database: str | None = None,
        release_label: str = "emr-7.0.0",
        spark_submit_parameters: str | None = None,
        timeout_minutes: int = 60,
        create_application: bool = False,
        application_name: str | None = None,
        initial_capacity: dict[str, Any] | None = None,
        table_format: str | None = None,
        **kwargs: Any,
    ) -> None:
        if not BOTO3_AVAILABLE:
            deps_satisfied, missing = check_platform_dependencies("emr-serverless")
            if not deps_satisfied:
                raise ConfigurationError(get_dependency_error_message("emr-serverless", missing))

        if not s3_staging_dir:
            raise ConfigurationError("s3_staging_dir is required (e.g., s3://bucket/path)")

        if not s3_staging_dir.startswith("s3://"):
            raise ConfigurationError(f"Invalid S3 path: {s3_staging_dir}. Must start with s3://")

        if not execution_role_arn:
            raise ConfigurationError("execution_role_arn is required for EMR Serverless")

        if not application_id and not create_application:
            raise ConfigurationError("Either application_id must be provided or create_application=True")

        s3_parts = s3_staging_dir[5:].split("/", 1)
        self.s3_bucket = s3_parts[0]
        self.s3_prefix = s3_parts[1] if len(s3_parts) > 1 else ""

        self.application_id = application_id
        self.s3_staging_dir = s3_staging_dir.rstrip("/")
        self.execution_role_arn = execution_role_arn
        self.region = region
        self.database = database or "benchbox"
        self.release_label = release_label
        self.spark_submit_parameters = spark_submit_parameters
        self.timeout_minutes = timeout_minutes
        self.create_application = create_application
        self.application_name = application_name or f"benchbox-{uuid.uuid4().hex[:8]}"
        self.initial_capacity = initial_capacity
        self.table_format = table_format or "parquet"

        self._staging: CloudSparkStaging | None = None
        try:
            self._staging = CloudSparkStaging.from_uri(self.s3_staging_dir)
        except Exception as e:
            logger.warning(f"Failed to initialize S3 staging: {e}")

        self._emr_serverless_client: Any = None
        self._glue_client: Any = None
        self._s3_client: Any = None

        self._query_count = 0
        self._total_vcpu_hours = 0.0
        self._total_memory_gb_hours = 0.0
        self._application_created_by_us = False

        self._benchmark_type: str | None = None
        self._scale_factor: float = 1.0
        self._spark_config: dict[str, str] = {}

        super().__init__(**kwargs)

    def _get_emr_serverless_client(self) -> Any:
        if self._emr_serverless_client is None:
            session = boto3.Session(region_name=self.region)
            self._emr_serverless_client = session.client("emr-serverless")
        return self._emr_serverless_client

    def _get_glue_client(self) -> Any:
        if self._glue_client is None:
            session = boto3.Session(region_name=self.region)
            self._glue_client = session.client("glue")
        return self._glue_client

    def _get_s3_client(self) -> Any:
        if self._s3_client is None:
            session = boto3.Session(region_name=self.region)
            self._s3_client = session.client("s3")
        return self._s3_client

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        return {
            "platform": "emr-serverless",
            "display_name": "Amazon EMR Serverless",
            "vendor": "AWS",
            "type": "managed_spark",
            "application_id": self.application_id,
            "region": self.region,
            "release_label": self.release_label,
            "execution_role": self.execution_role_arn,
            "supports_sql": True,
            "supports_dataframe": True,
            "billing_model": "vCPU-hour + memory GB-hour",
        }

    def _create_emr_application(self) -> str:
        client = self._get_emr_serverless_client()

        create_params: dict[str, Any] = {
            "name": self.application_name,
            "releaseLabel": self.release_label,
            "type": "SPARK",
            "autoStartConfiguration": {"enabled": True},
            "autoStopConfiguration": {"enabled": True, "idleTimeoutMinutes": 15},
        }

        if self.initial_capacity:
            create_params["initialCapacity"] = self.initial_capacity

        logger.info(f"Creating EMR Serverless application: {self.application_name}")
        response = client.create_application(**create_params)
        application_id = response["applicationId"]

        self._wait_for_application_state(application_id, ["CREATED", "STARTED"])
        self._application_created_by_us = True

        logger.info(f"Application created: {application_id}")
        return application_id

    def _wait_for_application_state(
        self,
        application_id: str,
        target_states: list[str],
        timeout_seconds: int = 300,
    ) -> str:
        client = self._get_emr_serverless_client()
        start_time = mono_time()

        while elapsed_seconds(start_time) < timeout_seconds:
            response = client.get_application(applicationId=application_id)
            state = response["application"]["state"]

            if state in target_states:
                return state
            if state in ["TERMINATED", "STOPPED"]:
                raise ConfigurationError(f"Application is in {state} state")

            time.sleep(5)

        raise ConfigurationError(f"Timeout waiting for application {application_id}")

    def _ensure_application_started(self) -> None:
        if not self.application_id:
            if self.create_application:
                self.application_id = self._create_emr_application()
            else:
                raise ConfigurationError("No application_id and create_application=False")

        client = self._get_emr_serverless_client()

        response = client.get_application(applicationId=self.application_id)
        state = response["application"]["state"]

        if state == "STARTED":
            return
        if state == "CREATED":
            logger.info(f"Starting application {self.application_id}")
            client.start_application(applicationId=self.application_id)
            self._wait_for_application_state(self.application_id, ["STARTED"])
        elif state in ["STARTING"]:
            self._wait_for_application_state(self.application_id, ["STARTED"])
        else:
            raise ConfigurationError(f"Application is in unexpected state: {state}")

    def create_connection(self, **kwargs: Any) -> Any:
        client = self._get_emr_serverless_client()

        try:
            if self.application_id:
                response = client.get_application(applicationId=self.application_id)
                app = response["application"]
                logger.info(f"Connected to EMR Serverless application: {self.application_id}")
                return {
                    "status": "connected",
                    "application_id": self.application_id,
                    "application_state": app["state"],
                    "release_label": app.get("releaseLabel"),
                }
            elif self.create_application:
                return {
                    "status": "pending",
                    "message": "Application will be created on first job submission",
                }
            else:
                raise ConfigurationError("No application_id provided")
        except ClientError as e:
            raise ConfigurationError(f"Failed to connect to EMR Serverless: {e}") from e

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        database = self.database
        client = self._get_glue_client()

        try:
            client.get_database(Name=database)
            logger.info(f"Database '{database}' already exists")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "EntityNotFoundException":
                logger.info(f"Creating database '{database}'")
                client.create_database(
                    DatabaseInput={
                        "Name": database,
                        "Description": "BenchBox benchmark database",
                    }
                )
            else:
                raise
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

        glue_client = self._get_glue_client()

        table_uris = {}
        for table in tables:
            table_uri = f"{self.s3_staging_dir}/tables/{table}"
            table_uris[table] = table_uri

            try:
                glue_client.get_table(DatabaseName=self.database, Name=table)
                logger.debug(f"Table {table} already exists in Glue catalog")
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "EntityNotFoundException":
                    if file_format == "parquet":
                        glue_client.create_table(
                            DatabaseName=self.database,
                            TableInput={
                                "Name": table,
                                "StorageDescriptor": {
                                    "Location": table_uri,
                                    "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                                    "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                                    "SerdeInfo": {
                                        "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
                                    },
                                },
                                "TableType": "EXTERNAL_TABLE",
                            },
                        )
                    else:
                        create_sql = (
                            f"CREATE EXTERNAL TABLE IF NOT EXISTS {self.database}.{table} "
                            f"USING {file_format.upper()} LOCATION '{table_uri}'"
                        )
                        self._submit_job_run(create_sql)
                    logger.info(f"Created table {self.database}.{table}")
                else:
                    raise

        return dict.fromkeys(tables, 0), elapsed_seconds(start_time), {"table_uris": table_uris}

    def _register_external_table(self, table_name: str, location: str, file_format: str) -> None:
        self._validate_external_identifier(table_name, "table name")
        self._validate_external_identifier(self.database, "database name")
        safe_location = self._escape_external_location(location)
        create_sql = (
            f"CREATE OR REPLACE TABLE {self.database}.{table_name} "
            f"USING {file_format.upper()} LOCATION '{safe_location}'"
        )
        self._ensure_application_started()
        job_run_id = self._submit_job_run(create_sql)
        self._wait_for_job_run(job_run_id)
        logger.info(f"Registered external table {self.database}.{table_name}")

    def _submit_job_run(self, query: str) -> str:
        client = self._get_emr_serverless_client()

        job_run_id = f"benchbox-{uuid.uuid4().hex[:12]}"
        results_path = f"{self.s3_staging_dir}/results/{job_run_id}"

        query_literal = json.dumps(query)
        job_script = f'''
from pyspark.sql import SparkSession

spark = SparkSession.builder \\
    .appName("BenchBox Query") \\
    .config("spark.sql.catalog.glue_catalog", "org.apache.iceberg.spark.SparkCatalog") \\
    .config("spark.sql.catalog.glue_catalog.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog") \\
    .enableHiveSupport() \\
    .getOrCreate()

spark.sql("USE {self.database}")

result = spark.sql({query_literal})
result.write.mode("overwrite").json("{results_path}")

spark.stop()
'''

        script_key = f"{self.s3_prefix}/scripts/{job_run_id}.py"
        s3_client = self._get_s3_client()
        s3_client.put_object(
            Bucket=self.s3_bucket,
            Key=script_key,
            Body=job_script.encode(),
        )
        script_uri = f"s3://{self.s3_bucket}/{script_key}"

        spark_params = self.spark_submit_parameters or ""
        for key, value in self._spark_config.items():
            spark_params += f" --conf {key}={value}"

        job_config: dict[str, Any] = {
            "applicationId": self.application_id,
            "executionRoleArn": self.execution_role_arn,
            "jobDriver": {
                "sparkSubmit": {
                    "entryPoint": script_uri,
                    "sparkSubmitParameters": spark_params.strip(),
                }
            },
            "configurationOverrides": {
                "monitoringConfiguration": {"s3MonitoringConfiguration": {"logUri": f"{self.s3_staging_dir}/logs/"}}
            },
        }

        response = client.start_job_run(**job_config)
        return response["jobRunId"]

    def _wait_for_job_run(self, job_run_id: str) -> tuple[str, dict[str, Any]]:
        client = self._get_emr_serverless_client()
        timeout_seconds = self.timeout_minutes * 60
        start_time = mono_time()

        while elapsed_seconds(start_time) < timeout_seconds:
            response = client.get_job_run(
                applicationId=self.application_id,
                jobRunId=job_run_id,
            )
            job_run = response["jobRun"]
            state = job_run["state"]

            if state == EMRServerlessJobState.SUCCESS:
                resource_usage = job_run.get("totalResourceUtilization", {})
                return state, resource_usage
            if state in [EMRServerlessJobState.FAILED, EMRServerlessJobState.CANCELLED]:
                error_msg = job_run.get("stateDetails", "Unknown error")
                raise RuntimeError(f"Job failed with state {state}: {error_msg}")

            time.sleep(5)

        raise RuntimeError(f"Job run {job_run_id} timed out after {self.timeout_minutes} minutes")

    def _retrieve_results(self, job_run_id: str) -> list[dict[str, Any]]:
        s3_client = self._get_s3_client()
        results_prefix = f"{self.s3_prefix}/results/{job_run_id}/"

        response = s3_client.list_objects_v2(
            Bucket=self.s3_bucket,
            Prefix=results_prefix,
        )

        results = []
        for obj in response.get("Contents", []):
            if obj["Key"].endswith(".json"):
                obj_response = s3_client.get_object(Bucket=self.s3_bucket, Key=obj["Key"])
                content = obj_response["Body"].read().decode()
                for line in content.strip().split("\n"):
                    if line:
                        results.append(json.loads(line))

        return results

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
        ledger = RequestLedger()
        start_time = mono_time()
        try:
            with outside_query_timer():
                self._ensure_application_started()
            start_time = mono_time()
            job_run_id = self._submit_job_run(query)
            ledger.add_request(REQUEST_KIND_QUERY, job_run_id)
            state, resource_usage = self._wait_for_job_run(job_run_id)
            results = self._retrieve_results(job_run_id)
            elapsed = elapsed_seconds(start_time)

            with outside_query_timer():
                self._query_count += 1
                vcpu_hours = resource_usage.get("vCPUHour", 0)
                memory_gb_hours = resource_usage.get("memoryGBHour", 0)
                self._total_vcpu_hours += vcpu_hours
                self._total_memory_gb_hours += memory_gb_hours
                timing_fields = self.timing_fields_for(connection, ledger, rows=results, elapsed_seconds=elapsed)
            return {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "SUCCESS",
                "execution_time_seconds": elapsed,
                "rows_returned": len(results),
                "results": results,
                "error": None,
                **timing_fields,
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
        if self._application_created_by_us and self.application_id:
            try:
                client = self._get_emr_serverless_client()
                client.stop_application(applicationId=self.application_id)
                logger.info(f"Stopped application: {self.application_id}")
            except Exception as e:
                logger.warning(f"Failed to stop application: {e}")

        logger.info(f"EMR Serverless session closed. Executed {self._query_count} queries.")
        if self._total_vcpu_hours > 0 or self._total_memory_gb_hours > 0:
            estimated_cost = (self._total_vcpu_hours * 0.052624) + (self._total_memory_gb_hours * 0.0057785)
            logger.info(
                f"Total resources: {self._total_vcpu_hours:.4f} vCPU-hours, "
                f"{self._total_memory_gb_hours:.4f} memory-GB-hours "
                f"(estimated cost: ${estimated_cost:.4f})"
            )

    @staticmethod
    def add_cli_arguments(parser: Any) -> None:
        group = parser.add_argument_group("EMR Serverless Options")
        group.add_argument(
            "--application-id",
            help="EMR Serverless application ID",
        )
        group.add_argument(
            "--s3-staging-dir",
            help="S3 path for data staging (e.g., s3://bucket/path)",
        )
        group.add_argument(
            "--execution-role-arn",
            help="IAM role ARN for job execution",
        )
        group.add_argument(
            "--region",
            default="us-east-1",
            help="AWS region (default: us-east-1)",
        )
        group.add_argument(
            "--database",
            default="benchbox",
            help="Glue database name (default: benchbox)",
        )
        group.add_argument(
            "--release-label",
            default="emr-7.0.0",
            help="EMR release label (default: emr-7.0.0)",
        )
        group.add_argument(
            "--create-application",
            action="store_true",
            help="Create new application if not provided",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> EMRServerlessAdapter:
        from benchbox.core.compute_resource import normalize_compute_options
        from benchbox.platforms.base.config_utils import forward_plan_options

        config = normalize_compute_options("emr-serverless", config)
        params = {
            "application_id": config.get("compute_resource") or config.get("application_id"),
            "s3_staging_dir": config.get("s3_staging_dir"),
            "execution_role_arn": config.get("execution_role_arn"),
            "region": config.get("region", "us-east-1"),
            "database": config.get("database", "benchbox"),
            "release_label": config.get("release_label", "emr-7.0.0"),
            "create_application": config.get("create_application", False),
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

        forward_plan_options(config, params)

        return cls(**params)

    def get_target_dialect(self) -> str:
        return "spark"
