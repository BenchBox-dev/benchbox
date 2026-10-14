# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    pass

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.base import DriverIsolationCapability, PlatformAdapter
from benchbox.platforms.base.cloud_spark import (
    CloudSparkConfigMixin,
    CloudSparkStaging,
    SparkDDLGeneratorMixin,
    SparkTableFormat,
    SparkTuningMixin,
)
from benchbox.platforms.base.cloud_spark.config import CloudPlatform
from benchbox.platforms.base.config_utils import make_platform_config_builder
from benchbox.platforms.base.phase_tracking import _resolve_benchmark_table_names
from benchbox.platforms.onehouse.onehouse_client import (
    ClusterConfig,
    OnehouseClient,
    TableFormat,
)
from benchbox.utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)

try:
    import boto3

    BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None
    BOTO3_AVAILABLE = False

logger = logging.getLogger(__name__)


class QuantonAdapter(
    CloudSparkConfigMixin,
    SparkTuningMixin,
    SparkDDLGeneratorMixin,
    PlatformAdapter,
):
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

    cloud_platform: ClassVar[CloudPlatform] = CloudPlatform.QUANTON

    table_format: ClassVar[SparkTableFormat] = SparkTableFormat.ICEBERG

    def __init__(
        self,
        api_key: str | None = None,
        s3_staging_dir: str | None = None,
        region: str = "us-east-1",
        database: str | None = None,
        table_format: str = "iceberg",
        cluster_size: str = "small",
        timeout_minutes: int = 60,
        api_endpoint: str | None = None,
        record_key: str | None = None,
        precombine_field: str | None = None,
        hudi_table_type: str = "COPY_ON_WRITE",
        **kwargs: Any,
    ) -> None:
        resolved_api_key = api_key or os.environ.get("ONEHOUSE_API_KEY")
        if not resolved_api_key:
            raise ConfigurationError(
                "Onehouse API key required. Provide via api_key parameter or ONEHOUSE_API_KEY environment variable."
            )

        if not s3_staging_dir:
            raise ConfigurationError("s3_staging_dir is required (e.g., s3://bucket/path)")

        if not s3_staging_dir.startswith("s3://"):
            raise ConfigurationError(f"Invalid S3 path: {s3_staging_dir}. Must start with s3://")

        try:
            self._table_format_enum = TableFormat(table_format.lower())
        except ValueError:
            valid_formats = ", ".join(f.value for f in TableFormat)
            raise ConfigurationError(f"Invalid table_format: {table_format}. Must be one of: {valid_formats}") from None

        s3_parts = s3_staging_dir[5:].split("/", 1)
        self.s3_bucket = s3_parts[0]
        self.s3_prefix = s3_parts[1] if len(s3_parts) > 1 else ""

        self.api_key = resolved_api_key
        self.s3_staging_dir = s3_staging_dir.rstrip("/")
        self.region = region
        self.database = database or "benchbox"
        self.table_format_str = table_format.lower()
        self.cluster_size = cluster_size
        self.timeout_minutes = timeout_minutes
        self.api_endpoint = api_endpoint

        format_mapping = {
            "iceberg": SparkTableFormat.ICEBERG,
            "hudi": SparkTableFormat.HUDI,
            "delta": SparkTableFormat.DELTA,
        }
        self.__class__.table_format = format_mapping.get(self.table_format_str, SparkTableFormat.ICEBERG)

        self.record_key = record_key
        self.precombine_field = precombine_field
        self.hudi_table_type = hudi_table_type

        self._staging: CloudSparkStaging | None = None
        try:
            self._staging = CloudSparkStaging.from_uri(self.s3_staging_dir)
        except Exception as e:
            logger.warning(f"Failed to initialize S3 staging: {e}")

        cluster_config = ClusterConfig(
            cluster_size=cluster_size,
            auto_scaling=True,
        )
        self._client = OnehouseClient(
            api_key=self.api_key,
            region=self.region,
            api_endpoint=self.api_endpoint,
            cluster_config=cluster_config,
        )

        self._s3_client: Any = None

        self._query_count = 0
        self._total_job_duration_seconds = 0.0

        self._benchmark_type: str | None = None
        self._scale_factor: float = 1.0
        self._spark_config: dict[str, str] = {}

        super().__init__(**kwargs)

    def _get_s3_client(self) -> Any:
        if self._s3_client is None:
            if not BOTO3_AVAILABLE:
                deps_satisfied, missing = check_platform_dependencies("quanton")
                if not deps_satisfied:
                    raise ConfigurationError(get_dependency_error_message("quanton", missing))

            session = boto3.Session(region_name=self.region)
            self._s3_client = session.client("s3")
        return self._s3_client

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        return {
            "platform": "quanton",
            "display_name": "Onehouse Quanton",
            "vendor": "Onehouse",
            "type": "managed_spark",
            "region": self.region,
            "table_format": self.table_format_str,
            "cluster_size": self.cluster_size,
            "supports_sql": True,
            "supports_dataframe": True,
            "supports_hudi": True,
            "supports_iceberg": True,
            "supports_delta": True,
            "billing_model": "compute-hours",
        }

    def create_connection(self, **kwargs: Any) -> Any:
        try:
            if self._client.test_connection():
                logger.info("Connected to Onehouse Quanton API")
                return {
                    "status": "connected",
                    "region": self.region,
                    "table_format": self.table_format_str,
                }
            else:
                raise ConfigurationError("Failed to connect to Onehouse API")
        except Exception as e:
            raise ConfigurationError(f"Failed to connect to Onehouse Quanton: {e}") from e

    def test_connection(self) -> bool:
        try:
            return self._client.test_connection()
        except Exception:
            return False

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        database = self.database

        try:
            location = f"{self.s3_staging_dir}/databases/{database}"
            self._client.create_database(database, location=location)
            logger.info(f"Created database '{database}' at {location}")
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.info(f"Database '{database}' already exists")
            else:
                logger.warning(f"Failed to create database: {e}")
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
        file_format = self.requested_table_format or self.table_format_str
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

            ddl = self._generate_create_table_ddl(table, table_uri)
            if ddl:
                try:
                    self._execute_ddl(ddl)
                    logger.info(f"Created table {self.database}.{table}")
                except Exception as e:
                    logger.warning(f"Failed to create table {table}: {e}")

        return dict.fromkeys(tables, 0), elapsed_seconds(start_time), {"table_uris": table_uris}

    def _generate_create_table_ddl(self, table: str, location: str) -> str:
        if self.table_format_str == "iceberg":
            return f"""
                CREATE TABLE IF NOT EXISTS {self.database}.{table}
                USING ICEBERG
                LOCATION '{location}'
            """
        elif self.table_format_str == "hudi":
            properties = [f"'hoodie.table.name' = '{table}'"]

            if self.record_key:
                properties.append(f"'hoodie.datasource.write.recordkey.field' = '{self.record_key}'")
            else:
                logger.warning(
                    f"No record_key configured for Hudi table {table}. "
                    "Set --record-key or provide record_key in config."
                )
                properties.append(f"'hoodie.datasource.write.recordkey.field' = '{table}_key'")

            if self.precombine_field:
                properties.append(f"'hoodie.datasource.write.precombine.field' = '{self.precombine_field}'")

            properties.append(f"'hoodie.table.type' = '{self.hudi_table_type}'")

            props_str = ",\n                    ".join(properties)
            return f"""
                CREATE TABLE IF NOT EXISTS {self.database}.{table}
                USING HUDI
                TBLPROPERTIES (
                    {props_str}
                )
                LOCATION '{location}'
            """
        elif self.table_format_str == "delta":
            return f"""
                CREATE TABLE IF NOT EXISTS {self.database}.{table}
                USING DELTA
                LOCATION '{location}'
            """
        return ""

    def _execute_ddl(self, ddl: str) -> None:
        job_id = self._client.submit_sql_job(
            sql=ddl,
            database=self.database,
            table_format=self._table_format_enum,
            spark_config=self._spark_config,
        )
        self._client.wait_for_job(job_id, timeout_minutes=10)

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
            result_id = f"results-{uuid.uuid4().hex[:12]}"
            output_location = f"{self.s3_staging_dir}/results/{result_id}"
            job_id = self._client.submit_sql_job(
                sql=query,
                database=self.database,
                table_format=self._table_format_enum,
                output_location=output_location,
                spark_config=self._spark_config,
            )
            result = self._client.wait_for_job(job_id, timeout_minutes=self.timeout_minutes)
            elapsed = elapsed_seconds(start_time)
            self._query_count += 1
            if result.duration_seconds:
                self._total_job_duration_seconds += result.duration_seconds
            logger.debug(f"Query completed in {elapsed:.1f}s (job duration: {result.duration_seconds:.1f}s)")
            try:
                results = self._client.get_job_results(job_id)
            except Exception as e:
                logger.warning(f"Failed to retrieve results from API, trying S3: {e}")
                results = self._retrieve_results_from_s3(output_location)
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

    def _retrieve_results_from_s3(self, output_location: str) -> list[dict[str, Any]]:
        import json

        s3_client = self._get_s3_client()

        s3_parts = output_location[5:].split("/", 1)
        bucket = s3_parts[0]
        prefix = s3_parts[1] if len(s3_parts) > 1 else ""

        response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)

        results = []
        for obj in response.get("Contents", []):
            if obj["Key"].endswith(".json"):
                obj_response = s3_client.get_object(Bucket=bucket, Key=obj["Key"])
                content = obj_response["Body"].read().decode()
                for line in content.strip().split("\n"):
                    if line:
                        results.append(json.loads(line))

        return results

    def close(self) -> None:
        try:
            self._client.close()
        except Exception as e:
            logger.warning(f"Error closing Quanton client: {e}")

        logger.info(f"Quanton session closed. Executed {self._query_count} queries.")
        if self._total_job_duration_seconds > 0:
            logger.info(f"Total job duration: {self._total_job_duration_seconds:.1f}s")

    @staticmethod
    def add_cli_arguments(parser: Any) -> None:
        group = parser.add_argument_group("Onehouse Quanton Options")
        group.add_argument(
            "--onehouse-api-key",
            dest="api_key",
            help="Onehouse API key (or set ONEHOUSE_API_KEY env var)",
        )
        group.add_argument(
            "--s3-staging-dir",
            help="S3 path for data staging (e.g., s3://bucket/path)",
        )
        group.add_argument(
            "--onehouse-region",
            dest="region",
            default="us-east-1",
            help="AWS region for cluster deployment (default: us-east-1)",
        )
        group.add_argument(
            "--table-format",
            choices=["iceberg", "hudi", "delta"],
            default="iceberg",
            help="Table format: iceberg, hudi, or delta (default: iceberg)",
        )
        group.add_argument(
            "--cluster-size",
            choices=["small", "medium", "large", "xlarge"],
            default="small",
            help="Cluster size (default: small)",
        )
        group.add_argument(
            "--database",
            default="benchbox",
            help="Database name (default: benchbox)",
        )
        group.add_argument(
            "--record-key",
            dest="record_key",
            help="Hudi record key field (required for hudi table format)",
        )
        group.add_argument(
            "--precombine-field",
            dest="precombine_field",
            help="Hudi precombine field for ordering during updates",
        )
        group.add_argument(
            "--hudi-table-type",
            dest="hudi_table_type",
            choices=["COPY_ON_WRITE", "MERGE_ON_READ"],
            default="COPY_ON_WRITE",
            help="Hudi table type: COPY_ON_WRITE (faster reads) or MERGE_ON_READ (faster writes)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> QuantonAdapter:
        from benchbox.core.compute_resource import normalize_compute_options

        config = normalize_compute_options("quanton", config)
        params = {
            "api_key": config.get("api_key") or config.get("onehouse_api_key"),
            "s3_staging_dir": config.get("s3_staging_dir"),
            "region": config.get("region", "us-east-1"),
            "database": config.get("database", "benchbox"),
            "table_format": config.get("table_format", "iceberg"),
            "cluster_size": config.get("compute_size") or config.get("cluster_size", "small"),
            "timeout_minutes": config.get("timeout_minutes", 60),
            "api_endpoint": config.get("api_endpoint"),
            "record_key": config.get("record_key"),
            "precombine_field": config.get("precombine_field"),
            "hudi_table_type": config.get("hudi_table_type", "COPY_ON_WRITE"),
        }

        for key in [
            "force_recreate",
            "show_query_plans",
            "capture_plans",
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


_build_quanton_config = make_platform_config_builder(
    "quanton",
    __name__,
    "Onehouse Quanton",
    "requests",
    ["api_key", "s3_staging_dir", "region", "database", "table_format"],
)
