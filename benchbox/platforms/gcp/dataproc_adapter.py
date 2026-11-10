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
    SparkTuningMixin,
)
from benchbox.platforms.base.cloud_spark.config import CloudPlatform
from benchbox.platforms.base.phase_tracking import _resolve_benchmark_table_names
from benchbox.platforms.gcp._gcs_path import parse_gcs_staging_dir
from benchbox.utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)

try:
    from google.cloud import dataproc_v1, storage

    GOOGLE_CLOUD_AVAILABLE = True
except ImportError:
    dataproc_v1 = None
    storage = None
    GOOGLE_CLOUD_AVAILABLE = False

logger = logging.getLogger(__name__)


class DataprocJobState:
    STATE_UNSPECIFIED = "STATE_UNSPECIFIED"
    PENDING = "PENDING"
    SETUP_DONE = "SETUP_DONE"
    RUNNING = "RUNNING"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCEL_STARTED = "CANCEL_STARTED"
    CANCELLED = "CANCELLED"
    DONE = "DONE"
    ERROR = "ERROR"


class DataprocAdapter(CloudSparkConfigMixin, SparkTuningMixin, PlatformAdapter):
    timing_method = TIMING_METHOD_QUERY_ONLY
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

    cloud_platform = CloudPlatform.DATAPROC

    def __init__(
        self,
        project_id: str | None = None,
        region: str = "us-central1",
        cluster_name: str | None = None,
        gcs_staging_dir: str | None = None,
        database: str | None = None,
        master_machine_type: str = "n2-standard-4",
        worker_machine_type: str = "n2-standard-4",
        num_workers: int = 2,
        use_preemptible_workers: bool = False,
        num_preemptible_workers: int = 0,
        image_version: str = "2.1-debian11",
        timeout_minutes: int = 60,
        create_ephemeral_cluster: bool = False,
        table_format: str | None = None,
        **kwargs: Any,
    ) -> None:
        if not GOOGLE_CLOUD_AVAILABLE:
            deps_satisfied, missing = check_platform_dependencies("dataproc")
            if not deps_satisfied:
                raise ConfigurationError(get_dependency_error_message("dataproc", missing))

        if not project_id:
            raise ConfigurationError("project_id is required for Dataproc adapter")

        parsed = parse_gcs_staging_dir(gcs_staging_dir)
        self.gcs_bucket = parsed.bucket
        self.gcs_prefix = parsed.prefix

        self.project_id = project_id
        self.region = region
        self.cluster_name = cluster_name or f"benchbox-{uuid.uuid4().hex[:8]}"
        self.gcs_staging_dir = parsed.uri
        self.database = database or "benchbox"
        self.master_machine_type = master_machine_type
        self.worker_machine_type = worker_machine_type
        self.num_workers = num_workers
        self.use_preemptible_workers = use_preemptible_workers
        self.num_preemptible_workers = num_preemptible_workers
        self.image_version = image_version
        self.timeout_minutes = timeout_minutes
        self.create_ephemeral_cluster = create_ephemeral_cluster
        self.table_format = table_format or "parquet"

        self._staging: CloudSparkStaging | None = None
        try:
            self._staging = CloudSparkStaging.from_uri(self.gcs_staging_dir)
        except Exception as e:
            logger.warning(f"Failed to initialize GCS staging: {e}")

        self._cluster_client: Any = None
        self._job_client: Any = None
        self._storage_client: Any = None

        self._query_count = 0
        self._total_job_time_seconds = 0.0
        self._cluster_created_by_us = False

        self._benchmark_type: str | None = None
        self._scale_factor: float = 1.0
        self._spark_config: dict[str, str] = {}

        super().__init__(**kwargs)

    def _get_cluster_client(self) -> Any:
        if self._cluster_client is None:
            self._cluster_client = dataproc_v1.ClusterControllerClient(
                client_options={"api_endpoint": f"{self.region}-dataproc.googleapis.com:443"}
            )
        return self._cluster_client

    def _get_job_client(self) -> Any:
        if self._job_client is None:
            self._job_client = dataproc_v1.JobControllerClient(
                client_options={"api_endpoint": f"{self.region}-dataproc.googleapis.com:443"}
            )
        return self._job_client

    def _get_storage_client(self) -> Any:
        if self._storage_client is None:
            self._storage_client = storage.Client(project=self.project_id)
        return self._storage_client

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        return {
            "platform": "dataproc",
            "display_name": "Google Cloud Dataproc",
            "vendor": "Google Cloud",
            "type": "managed_spark",
            "project_id": self.project_id,
            "region": self.region,
            "cluster_name": self.cluster_name,
            "master_machine_type": self.master_machine_type,
            "worker_machine_type": self.worker_machine_type,
            "num_workers": self.num_workers,
            "image_version": self.image_version,
            "ephemeral_cluster": self.create_ephemeral_cluster,
            "supports_sql": True,
            "supports_dataframe": True,
            "billing_model": "per-second VM pricing",
        }

    def create_connection(self, **kwargs: Any) -> Any:
        client = self._get_cluster_client()

        try:
            cluster = client.get_cluster(
                project_id=self.project_id,
                region=self.region,
                cluster_name=self.cluster_name,
            )
            logger.info(f"Connected to Dataproc cluster: {self.cluster_name}")
            return {
                "status": "connected",
                "cluster_name": self.cluster_name,
                "cluster_state": cluster.status.state.name,
                "worker_count": cluster.config.worker_config.num_instances,
            }
        except Exception as e:
            if "NotFound" in str(e) or "404" in str(e):
                if self.create_ephemeral_cluster:
                    logger.info(f"Cluster {self.cluster_name} not found, will create on first job")
                    return {
                        "status": "pending",
                        "cluster_name": self.cluster_name,
                        "message": "Cluster will be created on first job submission",
                    }
                raise ConfigurationError(
                    f"Cluster {self.cluster_name} not found. Create it first or set create_ephemeral_cluster=True"
                ) from None
            raise ConfigurationError(f"Failed to connect to Dataproc: {e}") from e

    def _create_cluster(self) -> None:
        client = self._get_cluster_client()

        cluster_config = {
            "project_id": self.project_id,
            "cluster_name": self.cluster_name,
            "config": {
                "master_config": {
                    "num_instances": 1,
                    "machine_type_uri": self.master_machine_type,
                },
                "worker_config": {
                    "num_instances": self.num_workers,
                    "machine_type_uri": self.worker_machine_type,
                },
                "software_config": {
                    "image_version": self.image_version,
                    "properties": self._spark_config,
                },
                "gce_cluster_config": {
                    "zone_uri": "",
                },
            },
        }

        if self.use_preemptible_workers and self.num_preemptible_workers > 0:
            cluster_config["config"]["secondary_worker_config"] = {
                "num_instances": self.num_preemptible_workers,
                "machine_type_uri": self.worker_machine_type,
                "is_preemptible": True,
            }

        logger.info(f"Creating Dataproc cluster: {self.cluster_name}")
        operation = client.create_cluster(
            project_id=self.project_id,
            region=self.region,
            cluster=cluster_config,
        )

        result = operation.result()
        self._cluster_created_by_us = True
        logger.info(f"Cluster created: {result.cluster_name}")

    def _ensure_cluster_exists(self) -> None:
        client = self._get_cluster_client()

        try:
            cluster = client.get_cluster(
                project_id=self.project_id,
                region=self.region,
                cluster_name=self.cluster_name,
            )
            if cluster.status.state.name == "RUNNING":
                return
            if cluster.status.state.name in ("CREATING", "STARTING"):
                logger.info(f"Waiting for cluster {self.cluster_name} to be ready...")
                while True:
                    time.sleep(10)
                    cluster = client.get_cluster(
                        project_id=self.project_id,
                        region=self.region,
                        cluster_name=self.cluster_name,
                    )
                    if cluster.status.state.name == "RUNNING":
                        return
                    if cluster.status.state.name in ("ERROR", "DELETING"):
                        raise ConfigurationError(f"Cluster is in {cluster.status.state.name} state")
        except Exception as e:
            if "NotFound" in str(e) or "404" in str(e):
                if self.create_ephemeral_cluster:
                    self._create_cluster()
                    return
                raise
            raise

    def _delete_cluster(self) -> None:
        if not self._cluster_created_by_us:
            return

        client = self._get_cluster_client()
        logger.info(f"Deleting ephemeral cluster: {self.cluster_name}")

        try:
            operation = client.delete_cluster(
                project_id=self.project_id,
                region=self.region,
                cluster_name=self.cluster_name,
            )
            operation.result()
            logger.info(f"Cluster deleted: {self.cluster_name}")
        except Exception as e:
            logger.warning(f"Failed to delete cluster: {e}")

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        database = self.database

        create_db_query = f"CREATE DATABASE IF NOT EXISTS {database}"

        self._ensure_cluster_exists()
        self._submit_spark_sql_job(create_db_query, wait_for_completion=True)
        logger.info(f"Database '{database}' created or already exists")
        return elapsed_seconds(start_time)

    def _submit_spark_sql_job(
        self,
        query: str,
        wait_for_completion: bool = True,
    ) -> tuple[str, str]:
        client = self._get_job_client()

        job_id = f"benchbox-{uuid.uuid4().hex[:12]}"
        results_path = f"{self.gcs_staging_dir}/results/{job_id}"

        job_script = f'''
from pyspark.sql import SparkSession

spark = SparkSession.builder \\
    .appName("BenchBox Query") \\
    .enableHiveSupport() \\
    .getOrCreate()

spark.sql("USE {self.database}")

result = spark.sql("""{query}""")
result.write.mode("overwrite").json("{results_path}")

spark.stop()
'''

        script_path = f"{self.gcs_staging_dir}/scripts/{job_id}.py"
        self._upload_to_gcs(script_path, job_script)

        job = {
            "placement": {"cluster_name": self.cluster_name},
            "pyspark_job": {
                "main_python_file_uri": script_path,
                "properties": self._spark_config,
            },
            "reference": {"job_id": job_id},
        }

        logger.debug(f"Submitting job {job_id}")
        operation = client.submit_job_as_operation(
            project_id=self.project_id,
            region=self.region,
            job=job,
        )

        if wait_for_completion:
            result = operation.result(timeout=self.timeout_minutes * 60)
            return job_id, result.status.state.name
        else:
            return job_id, "PENDING"

    def _upload_to_gcs(self, gcs_path: str, content: str) -> None:
        client = self._get_storage_client()

        if gcs_path.startswith("gs://"):
            gcs_path = gcs_path[5:]
        bucket_name, blob_name = gcs_path.split("/", 1)

        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(content)

    def _retrieve_results(self, job_id: str) -> list[dict[str, Any]]:
        client = self._get_storage_client()
        results_prefix = f"{self.gcs_prefix}/results/{job_id}/"

        bucket = client.bucket(self.gcs_bucket)
        blobs = bucket.list_blobs(prefix=results_prefix)

        results = []
        for blob in blobs:
            if blob.name.endswith(".json"):
                content = blob.download_as_string()
                for line in content.decode().strip().split("\n"):
                    if line:
                        results.append(json.loads(line))

        return results

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
            logger.info("Tables already exist in GCS staging, skipping upload")
            table_uris = {table: self._staging.get_table_uri(table) for table in tables}
            return dict.fromkeys(tables, 0), elapsed_seconds(start_time), {"table_uris": table_uris}

        if self._staging:
            logger.info(f"Uploading {len(tables)} tables to GCS staging")
            self._staging.upload_tables(
                tables=tables,
                source_dir=source_path,
                file_format=file_format,
            )

        self._ensure_cluster_exists()

        table_uris = {}
        for table in tables:
            table_uri = f"{self.gcs_staging_dir}/tables/{table}"
            table_uris[table] = table_uri

            create_table_query = f"""
                CREATE EXTERNAL TABLE IF NOT EXISTS {self.database}.{table}
                USING {self.table_format.upper()}
                LOCATION '{table_uri}'
            """
            self._submit_spark_sql_job(create_table_query, wait_for_completion=True)
            logger.info(f"Created table {self.database}.{table}")

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
        ledger = RequestLedger()
        start_time = mono_time()
        try:
            with outside_query_timer():
                self._ensure_cluster_exists()
            start_time = mono_time()
            job_id, state = self._submit_spark_sql_job(query, wait_for_completion=True)
            ledger.add_request(REQUEST_KIND_QUERY, job_id)
            if state != DataprocJobState.DONE:
                elapsed = elapsed_seconds(start_time)
                self._query_count += 1
                self._total_job_time_seconds += elapsed
                raise RuntimeError(f"Dataproc job failed with state: {state}")
            results = self._retrieve_results(job_id)
            elapsed = elapsed_seconds(start_time)
            with outside_query_timer():
                self._query_count += 1
                self._total_job_time_seconds += elapsed
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
        if self._cluster_created_by_us and self.create_ephemeral_cluster:
            self._delete_cluster()

        logger.info(f"Dataproc session closed. Executed {self._query_count} queries.")
        if self._total_job_time_seconds > 0:
            logger.info(f"Total job time: {self._total_job_time_seconds:.1f}s")

    @staticmethod
    def add_cli_arguments(parser: Any) -> None:
        group = parser.add_argument_group("Dataproc Options")
        group.add_argument(
            "--project-id",
            help="GCP project ID",
        )
        group.add_argument(
            "--region",
            default="us-central1",
            help="GCP region (default: us-central1)",
        )
        group.add_argument(
            "--cluster-name",
            help="Dataproc cluster name",
        )
        group.add_argument(
            "--gcs-staging-dir",
            help="GCS path for data staging (e.g., gs://bucket/path)",
        )
        group.add_argument(
            "--database",
            default="benchbox",
            help="Hive database name (default: benchbox)",
        )
        group.add_argument(
            "--master-machine-type",
            default="n2-standard-4",
            help="Master VM type (default: n2-standard-4)",
        )
        group.add_argument(
            "--worker-machine-type",
            default="n2-standard-4",
            help="Worker VM type (default: n2-standard-4)",
        )
        group.add_argument(
            "--num-workers",
            type=int,
            default=2,
            help="Number of workers (default: 2)",
        )
        group.add_argument(
            "--use-preemptible",
            action="store_true",
            help="Use preemptible workers",
        )
        group.add_argument(
            "--ephemeral-cluster",
            action="store_true",
            help="Create ephemeral cluster per job",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> DataprocAdapter:
        from benchbox.core.compute_resource import normalize_compute_options
        from benchbox.platforms.base.config_utils import forward_plan_options

        config = normalize_compute_options("dataproc", config)
        params = {
            "project_id": config.get("project_id"),
            "region": config.get("region", "us-central1"),
            "cluster_name": config.get("compute_resource") or config.get("cluster_name"),
            "gcs_staging_dir": config.get("gcs_staging_dir"),
            "database": config.get("database", "benchbox"),
            "master_machine_type": config.get("master_machine_type", "n2-standard-4"),
            "worker_machine_type": config.get("worker_machine_type", "n2-standard-4"),
            "num_workers": config.get("num_workers", 2),
            "use_preemptible_workers": config.get("use_preemptible", False),
            "create_ephemeral_cluster": config.get("ephemeral_cluster", False),
            "table_format": config.get("table_format"),
        }

        if not params["cluster_name"]:
            benchmark = config.get("benchmark", "benchmark")
            scale = config.get("scale_factor", 1)
            params["cluster_name"] = f"benchbox-{benchmark}-sf{scale}-{uuid.uuid4().hex[:6]}"

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
