# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import argparse
import importlib
import json
import logging
import tempfile
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.core.errors import PlanCaptureError
from benchbox.platforms.base.config_utils import make_registered_platform_config_builder
from benchbox.platforms.base.mysql_wire import split_sql_statements
from benchbox.platforms.base.tuning import make_informational_constraint_applier
from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        TuningColumn,
        UnifiedTuningConfiguration,
    )

from benchbox.utils.cloud_storage import get_cloud_path_info, is_cloud_path
from benchbox.utils.file_format import detect_compression, detect_data_format
from benchbox.utils.iceberg_layout import relocate_iceberg_table, resolve_iceberg_metadata_file
from benchbox.utils.printing import emit

from ..utils.dependencies import check_platform_dependencies, get_dependency_error_message
from .base import DriverIsolationCapability, PlatformAdapter
from .base.data_loading import NO_BENCHMARK, DataSource, resolve_adapter_data_source, resolve_csv_dialect
from .base.runtime_metadata import build_default_normalized_result_metadata

try:
    import google.auth

    google_auth = google.auth
    from google.cloud import bigquery, storage
    from google.oauth2 import service_account
except ImportError:
    google_auth = None
    bigquery = None
    storage = None
    service_account = None

try:
    from google.api_core.exceptions import TooManyRequests
    from google.cloud.exceptions import NotFound
except ImportError:

    class NotFound(Exception):
        pass

    class TooManyRequests(Exception):
        pass


def _compact_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if value not in (None, "", {}, [], ())}


def _lazy_query_parser(module_name: str, class_name: str) -> Any:
    return getattr(importlib.import_module(module_name), class_name)()


_PROJECT_SCOPED_INFORMATION_SCHEMA_VIEWS = frozenset({"SCHEMATA"})


def _is_project_scoped_information_schema_view(name: str) -> bool:
    upper = (name or "").upper()
    if not upper.startswith("INFORMATION_SCHEMA."):
        return False
    return upper.rsplit(".", 1)[-1] in _PROJECT_SCOPED_INFORMATION_SCHEMA_VIEWS


class BigQueryAdapter(PlatformAdapter):
    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    supports_external_tables = True
    physical_identifier_case = "upper"
    _DELIMITED_FORMATS = frozenset({"tbl", "csv"})
    plan_capture_phase_eligible = False
    ledger_execute_verbs = ("query",)

    def __init__(self, **config):
        super().__init__(**config)

        available, missing = check_platform_dependencies("bigquery")
        if not available:
            error_msg = get_dependency_error_message("bigquery", missing)
            raise ImportError(error_msg)

        self._dialect = "bigquery"

        self.project_id = config.get("project_id")
        self.dataset_id = config.get("dataset_id") or "benchbox"
        self.location = config.get("location") or "US"
        self.credentials_path = config.get("credentials_path")

        staging_root = config.get("staging_root")
        self.staging_root = staging_root
        if staging_root:
            from benchbox.utils.cloud_storage import get_cloud_path_info

            path_info = get_cloud_path_info(staging_root)
            if path_info["provider"] in ("gs", "gcs"):
                self.storage_bucket = path_info["bucket"]
                self.storage_prefix = path_info["path"].strip("/") if path_info["path"] else "benchbox-data"
                self.logger.info(
                    f"Using staging location from config: gs://{self.storage_bucket}/{self.storage_prefix}"
                )
            else:
                raise ValueError(f"BigQuery requires GCS (gs://) staging location, got: {path_info['provider']}://")
        else:
            self.storage_bucket = config.get("storage_bucket")
            self.storage_prefix = config.get("storage_prefix") or "benchbox-data"

        self.job_priority = config.get("job_priority") or "INTERACTIVE"
        self.biglake_connection = config.get("biglake_connection")
        if config.get("query_cache") is not None:
            self.query_cache = config.get("query_cache")
        elif config.get("disable_result_cache") is not None:
            self.query_cache = not config.get("disable_result_cache")
        else:
            self.query_cache = False
        self.dry_run = config.get("dry_run") if config.get("dry_run") is not None else False
        self.maximum_bytes_billed = config.get("maximum_bytes_billed")

        self.clustering_fields = config.get("clustering_fields") or []
        self.partitioning_field = config.get("partitioning_field")

        if not self.project_id:
            from ..core.exceptions import ConfigurationError

            raise ConfigurationError(
                "BigQuery configuration requires project_id.\n"
                "Configure with one of:\n"
                "  1. CLI: benchbox setup --platform bigquery\n"
                "  2. Environment variable: BIGQUERY_PROJECT\n"
                "Also ensure Google Cloud credentials are configured:\n"
                "  - Set GOOGLE_APPLICATION_CREDENTIALS to service account JSON path\n"
                "  - Or run 'gcloud auth application-default login'"
            )

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[TuningColumn]) -> str | None:
        mode, method = self.resolve_sorted_ingestion_strategy()
        if mode == "off":
            return None

        raise ValueError(
            "BigQuery sorted ingestion is not yet executable through this CTAS hook because "
            "the BigQuery client path does not expose execute()/cursor(). Use CLUSTER BY today "
            "or implement BigQuery query-job execution for method "
            f"'{method}'."
        )

    @staticmethod
    def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
        bq_group = parser.add_argument_group("BigQuery Arguments")
        bq_group.add_argument("--project-id", type=str, help="BigQuery project ID")
        bq_group.add_argument("--dataset-id", type=str, help="BigQuery dataset ID")
        bq_group.add_argument("--location", type=str, default="US", help="BigQuery dataset location")
        bq_group.add_argument("--credentials-path", type=str, help="Path to Google Cloud credentials file")
        bq_group.add_argument("--storage-bucket", type=str, help="GCS bucket for data loading")

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.utils.database_naming import generate_database_name

        adapter_config = {}
        very_verbose = config.get("very_verbose", False)

        if not config.get("project_id"):
            try:
                _, project_id = google.auth.default()
                if project_id:
                    adapter_config["project_id"] = project_id
                    if very_verbose:
                        logging.info(f"Auto-detected BigQuery project ID: {project_id}")
            except google.auth.exceptions.DefaultCredentialsError:
                if very_verbose:
                    logging.warning("Could not auto-detect BigQuery project ID. Please provide --project-id.")

        if config.get("project_id"):
            adapter_config["project_id"] = config["project_id"]

        dataset_name = generate_database_name(
            benchmark_name=config["benchmark"],
            scale_factor=config["scale_factor"],
            platform="bigquery",
            tuning_config=config.get("tuning_config"),
        )
        adapter_config["dataset_id"] = dataset_name

        for key in [
            "location",
            "credentials_path",
            "storage_bucket",
            "storage_prefix",
            "staging_root",
            "job_priority",
            "biglake_connection",
            "query_cache",
            "disable_result_cache",
            "maximum_bytes_billed",
            "tuning_config",
            "tuning_enabled",
            "unified_tuning_configuration",
            "tuning_source",
            "tuning_source_file",
            "verbose_enabled",
            "very_verbose",
        ]:
            if key in config:
                adapter_config[key] = config[key]

        return cls(**adapter_config)

    @property
    def platform_name(self) -> str:
        return "BigQuery"

    def _detect_bigquery_client_version(self) -> str | None:
        try:
            import google.cloud.bigquery

            return getattr(google.cloud.bigquery, "__version__", None)
        except (ImportError, AttributeError):
            return None

    def _collect_bigquery_dataset_metadata(self, connection: Any) -> dict[str, Any]:
        try:
            dataset_ref = f"{self.project_id}.{self.dataset_id}"
            dataset = connection.get_dataset(dataset_ref)
            self.logger.debug(f"Successfully captured BigQuery dataset metadata for {dataset_ref}")
            return {
                "dataset_metadata_collection_status": "available",
                "dataset_location": dataset.location,
                "dataset_default_table_expiration_ms": dataset.default_table_expiration_ms,
                "dataset_default_partition_expiration_ms": dataset.default_partition_expiration_ms,
                "dataset_created": dataset.created.isoformat() if dataset.created else None,
                "dataset_modified": dataset.modified.isoformat() if dataset.modified else None,
            }
        except Exception as e:
            self.logger.debug(f"Could not fetch BigQuery dataset metadata: {e}")
            return {
                "dataset_metadata_collection_status": "unavailable",
                "dataset_metadata_error_class": type(e).__name__,
                "dataset_metadata_error_message": str(e),
            }

    @staticmethod
    def _determine_bigquery_pricing(
        reservation_info: dict[str, Any] | None,
        commitment_info: dict[str, Any] | None,
    ) -> tuple[str, str]:
        if not reservation_info:
            return "on-demand", "ON_DEMAND"

        edition = reservation_info.get("edition") or "STANDARD"
        if not commitment_info:
            return "flat-rate", edition

        commitment_plan_map = {
            "FLEX": "flex-slots",
            "MONTHLY": "monthly-commitment",
            "ANNUAL": "annual-commitment",
            "THREE_YEAR": "three-year-commitment",
        }
        pricing_model = commitment_plan_map.get(commitment_info.get("commitment_plan", ""), "flat-rate")
        return pricing_model, edition

    def _apply_bigquery_compute_configuration(
        self,
        platform_info: dict[str, Any],
        dataset_metadata: dict[str, Any] | None,
        reservation_info: dict[str, Any] | None,
        commitment_info: dict[str, Any] | None,
        assignment_info: dict[str, Any] | None,
    ) -> None:
        compute: dict[str, Any] = dict(dataset_metadata) if dataset_metadata else {}
        pricing_model, edition = self._determine_bigquery_pricing(reservation_info, commitment_info)
        self.logger.debug(f"Detected BigQuery pricing model: {pricing_model}, edition: {edition}")

        compute["pricing_model"] = pricing_model
        compute["edition"] = edition
        compute["slot_capacity"] = reservation_info.get("slot_capacity") if reservation_info else None
        compute["autoscale_max_slots"] = reservation_info.get("autoscale_max_slots") if reservation_info else None

        if reservation_info:
            compute["reservation_details"] = {
                "name": reservation_info.get("reservation_name"),
                "slot_capacity": reservation_info.get("slot_capacity"),
                "ignore_idle_slots": reservation_info.get("ignore_idle_slots"),
                "autoscale_max_slots": reservation_info.get("autoscale_max_slots"),
                "creation_time": reservation_info.get("creation_time"),
                "update_time": reservation_info.get("update_time"),
            }
        if commitment_info:
            compute["capacity_commitment"] = commitment_info
        if assignment_info:
            compute["assignment"] = assignment_info

        platform_info["compute_configuration"] = compute

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info: dict[str, Any] = {
            "platform_type": "bigquery",
            "platform_name": "BigQuery",
            "connection_mode": "remote",
            "cloud_provider": "GCP",
            "configuration": {
                "project_id": self.project_id,
                "dataset_id": self.dataset_id,
                "location": self.location,
                "storage_bucket": self.storage_bucket,
                "storage_prefix": self.storage_prefix,
                "staging_root": self.staging_root,
                "biglake_connection": self.biglake_connection,
                "job_timeout": getattr(self, "job_timeout", None),
                "job_priority": self.job_priority,
                "query_cache_enabled": self.query_cache,
                "maximum_bytes_billed": self.maximum_bytes_billed,
            },
            "client_library_version": self._detect_bigquery_client_version(),
            "platform_version": None,
        }

        if connection:
            try:
                dataset_metadata = self._collect_bigquery_dataset_metadata(connection)

                reservation_info = None
                try:
                    query = f"""
                        SELECT
                            reservation_name,
                            slot_capacity,
                            ignore_idle_slots,
                            edition,
                            autoscale_max_slots,
                            creation_time,
                            update_time
                        FROM `region-{self.location}`.INFORMATION_SCHEMA.RESERVATIONS
                        WHERE project_id = @project_id
                        LIMIT 1
                    """

                    from google.cloud.bigquery import ScalarQueryParameter

                    job_config = bigquery.QueryJobConfig(
                        query_parameters=[ScalarQueryParameter("project_id", "STRING", self.project_id)]
                    )

                    query_job = connection.query(query, job_config=job_config)
                    results = list(query_job.result())

                    if results:
                        row = results[0]
                        reservation_info = {
                            "reservation_name": row.reservation_name if hasattr(row, "reservation_name") else None,
                            "slot_capacity": int(row.slot_capacity)
                            if hasattr(row, "slot_capacity") and row.slot_capacity
                            else None,
                            "ignore_idle_slots": row.ignore_idle_slots if hasattr(row, "ignore_idle_slots") else None,
                            "edition": row.edition if hasattr(row, "edition") else None,
                            "autoscale_max_slots": int(row.autoscale_max_slots)
                            if hasattr(row, "autoscale_max_slots") and row.autoscale_max_slots
                            else None,
                            "creation_time": row.creation_time.isoformat()
                            if hasattr(row, "creation_time") and row.creation_time
                            else None,
                            "update_time": row.update_time.isoformat()
                            if hasattr(row, "update_time") and row.update_time
                            else None,
                        }
                        self.logger.debug(
                            f"Successfully captured BigQuery reservation info: {reservation_info['reservation_name']}"
                        )

                except Exception as e:
                    self.logger.debug(
                        f"Could not fetch BigQuery reservation info (insufficient permissions or not configured): {e}"
                    )

                commitment_info = None
                try:
                    commitment_query = f"""
                        SELECT
                            commitment_id,
                            slot_count,
                            commitment_plan,
                            state,
                            renewal_plan,
                            commitment_start_time,
                            commitment_end_time
                        FROM `region-{self.location}`.INFORMATION_SCHEMA.CAPACITY_COMMITMENTS
                        WHERE project_id = @project_id
                            AND state = 'ACTIVE'
                        ORDER BY commitment_start_time DESC
                        LIMIT 1
                    """

                    job_config = bigquery.QueryJobConfig(
                        query_parameters=[ScalarQueryParameter("project_id", "STRING", self.project_id)]
                    )

                    commitment_job = connection.query(commitment_query, job_config=job_config)
                    commitment_results = list(commitment_job.result())

                    if commitment_results:
                        row = commitment_results[0]
                        commitment_info = {
                            "commitment_id": row.commitment_id if hasattr(row, "commitment_id") else None,
                            "slot_count": int(row.slot_count)
                            if hasattr(row, "slot_count") and row.slot_count
                            else None,
                            "commitment_plan": row.commitment_plan if hasattr(row, "commitment_plan") else None,
                            "state": row.state if hasattr(row, "state") else None,
                            "renewal_plan": row.renewal_plan if hasattr(row, "renewal_plan") else None,
                            "commitment_start_time": row.commitment_start_time.isoformat()
                            if hasattr(row, "commitment_start_time") and row.commitment_start_time
                            else None,
                            "commitment_end_time": row.commitment_end_time.isoformat()
                            if hasattr(row, "commitment_end_time") and row.commitment_end_time
                            else None,
                        }
                        self.logger.debug(
                            f"Successfully captured capacity commitment: {commitment_info['commitment_plan']}"
                        )

                except Exception as e:
                    error_msg = str(e).lower()
                    if "permission" in error_msg or "access denied" in error_msg:
                        self.logger.warning(
                            f"Unable to query capacity commitments (insufficient permissions): {e}. "
                            "Grant bigquery.capacityCommitments.list permission for complete platform metadata."
                        )
                    else:
                        self.logger.debug(f"Could not fetch capacity commitment info: {e}")

                assignment_info = None
                try:
                    assignment_query = f"""
                        SELECT
                            assignment_id,
                            assignee_id,
                            assignee_type,
                            job_type,
                            reservation_name
                        FROM `region-{self.location}`.INFORMATION_SCHEMA.ASSIGNMENTS_BY_PROJECT
                        WHERE assignee_id = @project_id
                        LIMIT 1
                    """

                    job_config = bigquery.QueryJobConfig(
                        query_parameters=[ScalarQueryParameter("project_id", "STRING", self.project_id)]
                    )

                    assignment_job = connection.query(assignment_query, job_config=job_config)
                    assignment_results = list(assignment_job.result())

                    if assignment_results:
                        row = assignment_results[0]
                        assignment_info = {
                            "assignment_id": row.assignment_id if hasattr(row, "assignment_id") else None,
                            "assignee_type": row.assignee_type if hasattr(row, "assignee_type") else None,
                            "job_type": row.job_type if hasattr(row, "job_type") else None,
                            "reservation_name": row.reservation_name if hasattr(row, "reservation_name") else None,
                        }
                        self.logger.debug(
                            f"Successfully captured reservation assignment: {assignment_info['assignment_id']}"
                        )

                except Exception as e:
                    error_msg = str(e).lower()
                    if "permission" in error_msg or "access denied" in error_msg:
                        self.logger.warning(
                            f"Unable to query reservation assignments (insufficient permissions): {e}. "
                            "Grant bigquery.reservationAssignments.list permission for complete platform metadata."
                        )
                    else:
                        self.logger.debug(f"Could not fetch reservation assignment info: {e}")

                self._apply_bigquery_compute_configuration(
                    platform_info,
                    dataset_metadata,
                    reservation_info,
                    commitment_info,
                    assignment_info,
                )

            except Exception as e:
                self.logger.debug(f"Error collecting BigQuery platform info: {e}")

        return platform_info

    def get_normalized_result_metadata(
        self,
        *,
        connection: Any | None = None,
        platform_info: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        info = dict(platform_info) if isinstance(platform_info, Mapping) else self.get_platform_info(connection)
        metadata = build_default_normalized_result_metadata(self, connection=connection, platform_info=info)
        config = info.get("configuration") if isinstance(info.get("configuration"), Mapping) else {}
        compute = info.get("compute_configuration") if isinstance(info.get("compute_configuration"), Mapping) else {}

        metadata["platform_deployment"] = self._bigquery_deployment_metadata(config)
        metadata["platform_cloud"] = self._bigquery_cloud_metadata(config, compute)
        metadata["platform_compute"] = self._bigquery_compute_metadata(config, compute)
        metadata["platform_storage"] = self._bigquery_storage_metadata(config)
        return metadata

    @staticmethod
    def _bigquery_deployment_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        return _compact_metadata(
            {
                "deployment_type": "serverless",
                "connection_mode": "remote",
                "endpoint_class": "cloud_endpoint",
                "metadata_source": "requested",
                "collection_status": "partial",
                "dataset": config.get("dataset_id"),
            }
        )

    @staticmethod
    def _bigquery_cloud_metadata(config: Mapping[str, Any], compute: Mapping[str, Any]) -> dict[str, Any]:
        location = compute.get("dataset_location") or config.get("location")
        return _compact_metadata(
            {
                "provider": "gcp",
                "location": location,
                "project": config.get("project_id"),
                "source": "observed" if compute.get("dataset_location") else "requested",
                "collection_status": "partial" if config.get("project_id") or location else "unavailable",
            }
        )

    @staticmethod
    def _bigquery_compute_metadata(config: Mapping[str, Any], compute: Mapping[str, Any]) -> dict[str, Any]:
        reservation = (
            compute.get("reservation_details") if isinstance(compute.get("reservation_details"), Mapping) else {}
        )
        commitment = (
            compute.get("capacity_commitment") if isinstance(compute.get("capacity_commitment"), Mapping) else {}
        )
        assignment = compute.get("assignment") if isinstance(compute.get("assignment"), Mapping) else {}
        observed = bool(
            compute.get("dataset_location")
            or reservation
            or commitment
            or assignment
            or compute.get("dataset_metadata_collection_status") == "available"
        )
        dataset_metadata_unavailable = compute.get("dataset_metadata_collection_status") == "unavailable"
        collection_status = "available" if observed else "partial"
        if dataset_metadata_unavailable:
            collection_status = "partial"
        payload = {
            "serverless_slots": compute.get("slot_capacity"),
            "pricing_model": compute.get("pricing_model"),
            "edition": compute.get("edition"),
            "reservation": reservation.get("name"),
            "reservation_slot_capacity": reservation.get("slot_capacity"),
            "reservation_ignore_idle_slots": reservation.get("ignore_idle_slots"),
            "autoscale_max_slots": compute.get("autoscale_max_slots"),
            "capacity_commitment_id": commitment.get("commitment_id"),
            "capacity_commitment_plan": commitment.get("commitment_plan"),
            "capacity_commitment_slots": commitment.get("slot_count"),
            "reservation_assignment_id": assignment.get("assignment_id"),
            "reservation_assignment": assignment.get("reservation_name"),
            "reservation_assignment_job_type": assignment.get("job_type"),
            "job_priority": config.get("job_priority"),
            "cache_enabled": config.get("query_cache_enabled"),
            "maximum_bytes_billed": config.get("maximum_bytes_billed"),
            "dataset_metadata_collection_status": compute.get("dataset_metadata_collection_status"),
            "dataset_metadata_error_class": compute.get("dataset_metadata_error_class"),
            "dataset_metadata_error_message": compute.get("dataset_metadata_error_message"),
            "source": "observed" if observed else "requested",
            "collection_status": collection_status,
        }
        return _compact_metadata(payload)

    @staticmethod
    def _bigquery_storage_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        bucket = config.get("storage_bucket")
        staging_location = config.get("staging_root")
        biglake_connection = config.get("biglake_connection")
        has_storage_target = bool(bucket or staging_location or biglake_connection)
        prefix = config.get("storage_prefix") if bucket or staging_location else None
        if not staging_location and bucket:
            staging_location = f"gs://{bucket}/{prefix or ''}".rstrip("/")
        return _compact_metadata(
            {
                "staging_location": staging_location,
                "bucket": bucket,
                "prefix": prefix,
                "biglake_connection": biglake_connection,
                "source": "requested",
                "collection_status": "partial" if has_storage_target else "unavailable",
            }
        )

    def get_target_dialect(self) -> str:
        return "bigquery"

    def preprocess_operation_sql(self, query_id: str, operation: Any) -> str | None:
        import re

        overrides = getattr(operation, "platform_overrides", None) or {}
        if "bigquery" in overrides:
            base = overrides["bigquery"]
            if base is None:
                return None
        else:
            base = operation.write_sql
        rewritten = re.sub(
            r"\bCAST\(([^()]+?)\s+AS\s+VARCHAR\s*\)",
            r"CAST(\1 AS STRING)",
            base,
            flags=re.IGNORECASE,
        )
        rewritten = re.sub(r"\bINTERVAL\s+'(\d+)'\s+([A-Za-z]+)", r"INTERVAL \1 \2", rewritten)
        rewritten = self._numeric_decimal_literals(rewritten)
        statements = split_sql_statements(rewritten)
        parts = []
        changed = False
        for statement in statements:
            if re.match(r"(?i)^\s*(UPDATE|DELETE)\b", statement) and not re.search(r"(?i)\bWHERE\b", statement):
                statement = statement.rstrip() + "\nWHERE true"
                changed = True
            parts.append(statement)
        if changed:
            rewritten = ";\n".join(parts)
            if base.endswith("\n"):
                rewritten += "\n"
        bare_insert = re.search(r"(?i)(WHEN\s+NOT\s+MATCHED\b[^\n;]*?THEN\s+)INSERT(\s*)$", rewritten)
        if bare_insert:
            rewritten = rewritten[: bare_insert.start()] + bare_insert.group(1) + "INSERT ROW" + bare_insert.group(2)
        return rewritten

    @staticmethod
    def _numeric_decimal_literals(sql: str) -> str:
        import re

        parts = re.split(r"('(?:[^'\\]|\\.|'')*')", sql)
        for index in range(0, len(parts), 2):
            parts[index] = re.sub(r"(?<![\w.'])(\d+\.\d+)(?![\w.])", r"NUMERIC '\1'", parts[index])
        return "".join(parts)

    def _get_connection_params(self, **connection_config) -> dict[str, Any]:
        return {
            "project_id": connection_config.get("project_id", self.project_id),
            "location": connection_config.get("location", self.location),
            "credentials_path": connection_config.get("credentials_path", self.credentials_path),
        }

    def _load_credentials(self, credentials_path: str | None) -> Any:
        if credentials_path:
            return service_account.Credentials.from_service_account_file(credentials_path)
        credentials, _ = google_auth.default()
        return credentials

    def _create_admin_client(self, **connection_config) -> Any:
        params = self._get_connection_params(**connection_config)
        credentials = self._load_credentials(params["credentials_path"])
        return bigquery.Client(
            project=params["project_id"],
            location=params["location"],
            credentials=credentials,
        )

    def check_server_database_exists(self, **connection_config) -> bool:
        try:
            client = self._create_admin_client(**connection_config)
            dataset_id = connection_config.get("dataset", self.dataset_id)

            datasets = list(client.list_datasets())
            dataset_names = [d.dataset_id for d in datasets]

            return dataset_id in dataset_names

        except Exception:
            return False

    def drop_database(self, **connection_config) -> None:
        try:
            client = self._create_admin_client(**connection_config)
            dataset_id = connection_config.get("dataset", self.dataset_id)

            dataset_ref = client.dataset(dataset_id)

            client.delete_dataset(dataset_ref, delete_contents=True, not_found_ok=True)

        except Exception as e:
            raise RuntimeError(f"Failed to drop BigQuery dataset {dataset_id}: {e}") from e

    def _validate_database_compatibility(self, **connection_config):
        from benchbox.platforms.base.validation import DatabaseValidator

        validator = DatabaseValidator(adapter=self, connection_config=connection_config)
        result = validator.validate()

        if not result.is_valid:
            return result

        try:
            client = self._create_admin_client(**connection_config)
            dataset_ref = client.dataset(self.dataset_id, project=self.project_id)

            try:
                tables = list(client.list_tables(dataset_ref))
            except Exception as e:
                self.log_very_verbose(f"Could not list tables for empty table check: {e}")
                return result

            if not tables:
                return result

            empty_count = 0
            empty_tables = []

            for table_info in tables:
                try:
                    table = client.get_table(table_info.reference)
                    if table.num_rows == 0:
                        empty_count += 1
                        empty_tables.append(table.table_id)
                except Exception as e:
                    self.log_very_verbose(f"Failed to check table {table_info.table_id}: {e}")

            if empty_count > len(tables) / 2:
                self.log_verbose(
                    f"Found {empty_count}/{len(tables)} empty tables - database appears to have failed data load"
                )
                result.issues.append(
                    f"Empty tables detected: {empty_count}/{len(tables)} tables have no rows "
                    f"(indicates failed previous load)"
                )
                result.is_valid = False
                result.can_reuse = False

        except Exception as e:
            self.log_very_verbose(f"Empty table check failed: {e}")

        return result

    def _cleanup_empty_tables_if_needed(self, client) -> None:
        try:
            self.logger.debug(f"Starting empty table cleanup check for dataset {self.dataset_id}")
            dataset_ref = client.dataset(self.dataset_id, project=self.project_id)
            self.logger.debug(f"Listing tables in dataset {self.dataset_id}")
            tables = list(client.list_tables(dataset_ref))
            self.logger.debug(f"Found {len(tables)} tables in dataset")

            if not tables:
                self.logger.debug("No tables found - nothing to clean up")
                return

            empty_count = 0
            empty_tables = []

            self.logger.debug(f"Checking {len(tables)} tables for empty rows")
            for table_info in tables:
                try:
                    table = client.get_table(table_info.reference)
                    if table.num_rows == 0:
                        empty_count += 1
                        empty_tables.append(table.table_id)
                        self.logger.debug(f"Table {table.table_id} is empty (0 rows)")
                except Exception as e:
                    self.logger.warning(f"Failed to check table {table_info.table_id}: {e}")

            self.logger.debug(f"Empty table count: {empty_count}/{len(tables)}")

            if empty_count > len(tables) / 2:
                self.logger.warning(
                    f"Found {empty_count}/{len(tables)} empty tables - cleaning up failed previous load"
                )

                for table_id in empty_tables:
                    try:
                        table_ref = dataset_ref.table(table_id)
                        client.delete_table(table_ref, not_found_ok=True)
                        self.logger.info(f"Dropped empty table: {table_id}")
                    except Exception as e:
                        self.logger.warning(f"Failed to drop empty table {table_id}: {e}")

                self.database_was_reused = False
                self.logger.info(f"Dropped {len(empty_tables)} empty tables - forcing schema and data recreation")
            elif empty_count > 0:
                self.logger.info(
                    f"Found {empty_count} empty tables (out of {len(tables)} total) - not enough to trigger cleanup"
                )
            else:
                self.logger.debug("No empty tables found")

        except Exception as e:
            import traceback

            self.logger.error(f"Empty table cleanup check failed: {e}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("BigQuery connection")

        self.handle_existing_database(**connection_config)

        params = self._get_connection_params(**connection_config)
        self.log_very_verbose(
            f"BigQuery connection params: project={params.get('project_id')}, location={params.get('location')}"
        )

        try:
            credentials = self._load_credentials(params["credentials_path"])

            client = bigquery.Client(
                project=params["project_id"],
                location=params["location"],
                credentials=credentials,
            )

            query = "SELECT 1 as test"
            query_job = client.query(query)
            list(query_job.result())

            self.logger.info(f"Connected to BigQuery project: {params['project_id']}")

            self.logger.debug(
                f"Checking if cleanup needed: database_was_reused={getattr(self, 'database_was_reused', False)}"
            )
            if getattr(self, "database_was_reused", False):
                self.logger.info("Database was reused - checking for empty tables from failed previous loads")
                self._cleanup_empty_tables_if_needed(client)

            self.log_operation_complete("BigQuery connection", details=f"Connected to project {params['project_id']}")

            return client

        except Exception as e:
            self.logger.error(f"Failed to connect to BigQuery: {e}")
            raise

    def _get_existing_tables(self, connection: Any) -> list[str]:
        try:
            dataset_ref = connection.dataset(self.dataset_id, project=self.project_id)
            tables = list(connection.list_tables(dataset_ref))
            return [table.table_id.lower() for table in tables]
        except Exception as e:
            self.log_very_verbose(f"Failed to list tables: {e}")
            return []

    def _validate_data_integrity(
        self, benchmark, connection: Any, table_stats: dict[str, int]
    ) -> tuple[str, dict[str, Any]]:
        validation_details = {}

        try:
            accessible_tables = []
            inaccessible_tables = []

            for table_name in table_stats:
                try:
                    resolved_name, _ = self._resolve_target_table(connection, table_name)

                    query = f"SELECT 1 FROM `{self.project_id}.{self.dataset_id}.{resolved_name}` LIMIT 1"
                    query_job = connection.query(query)
                    list(query_job.result())

                    accessible_tables.append(table_name)
                except Exception as e:
                    self.log_very_verbose(f"Table {table_name} inaccessible: {e}")
                    inaccessible_tables.append(table_name)

            if inaccessible_tables:
                validation_details["inaccessible_tables"] = inaccessible_tables
                validation_details["constraints_enabled"] = False
                return "FAILED", validation_details
            else:
                validation_details["accessible_tables"] = accessible_tables
                validation_details["constraints_enabled"] = True
                return "PASSED", validation_details

        except Exception as e:
            validation_details["constraints_enabled"] = False
            validation_details["integrity_error"] = str(e)
            return "FAILED", validation_details

    def get_table_row_count(self, connection: Any, table: str) -> int:
        try:
            resolved_name, _ = self._resolve_target_table(connection, table)

            query = f"SELECT COUNT(*) FROM `{self.project_id}.{self.dataset_id}.{resolved_name}`"
            query_job = connection.query(query)
            result = list(query_job.result())
            return result[0][0] if result else 0
        except Exception as e:
            self.log_very_verbose(f"Could not get row count for {table}: {e}")
            return 0

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()

        try:
            dataset_ref = connection.dataset(self.dataset_id)

            try:
                connection.get_dataset(dataset_ref)
                self.logger.info(f"Dataset {self.dataset_id} already exists")
            except NotFound:
                dataset = bigquery.Dataset(dataset_ref)
                dataset.location = self.location
                dataset.description = (
                    f"BenchBox benchmark data for {benchmark._name if hasattr(benchmark, '_name') else 'benchmark'}"
                )

                dataset = connection.create_dataset(dataset)
                self.logger.info(f"Created dataset {self.dataset_id}")

            schema_sql = self._create_schema_with_tuning(benchmark, source_dialect="standard")

            from benchbox.platforms.cloud_shared import split_leading_sql_comments

            statements = [
                stmt.strip()
                for stmt in schema_sql.split(";")
                if stmt.strip() and split_leading_sql_comments(stmt)[1].strip()
            ]

            for statement in statements:
                bq_statement = self._convert_to_bigquery_table(statement)

                query_job = connection.query(bq_statement)
                query_job.result()

                self.logger.debug(f"Executed schema statement: {bq_statement[:100]}...")

            self.logger.info("Schema created")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise

        return elapsed_seconds(start_time)

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        logger = logging.getLogger(__name__)
        logger.debug(f"Starting data loading for benchmark: {benchmark.__class__.__name__}")
        logger.debug(f"Data directory: {data_dir}")

        if is_cloud_path(str(data_dir)):
            path_info = get_cloud_path_info(str(data_dir))
            logger.info(f"Loading data from cloud storage: {path_info['provider']} bucket '{path_info['bucket']}'")
            emit(f"  Loading data from {path_info['provider']} cloud storage")

        start_time = mono_time()
        table_stats = {}
        per_table_timings: dict[str, Any] = {}
        total_time = 0.0

        try:
            data_source = self._resolve_data_files(benchmark, data_dir)
            if not isinstance(data_source, DataSource):
                data_source = DataSource(source_type="legacy_test_mapping", tables=data_source)
            self._validate_compression_support(data_source.tables, benchmark)

            if self.storage_bucket:
                bucket = self._create_storage_bucket()
                table_stats, per_table_timings = self._load_tables_via_cloud_storage(
                    connection, data_source, bucket, benchmark
                )
            else:
                self.logger.warning("No Cloud Storage bucket configured, using direct loading")
                table_stats, per_table_timings = self._load_tables_direct(connection, data_source, benchmark)

            total_time = elapsed_seconds(start_time)
            total_rows = sum(table_stats.values())
            self.logger.info(f"✅ Loaded {total_rows:,} total rows in {total_time:.2f}s")

        except Exception as e:
            self.logger.error(f"Data loading failed: {e}")
            raise

        return table_stats, total_time, per_table_timings

    def validate_external_table_requirements(self) -> None:
        if not self.storage_bucket:
            raise ValueError(
                "BigQuery external mode requires a GCS bucket (set --platform-option storage_bucket=<bucket> "
                "or provide --platform-option staging_root=gs://bucket/path)."
            )

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        self.validate_external_table_requirements()

        start_time = mono_time()
        table_stats: dict[str, int] = {}
        data_source = self._resolve_data_files(benchmark, data_dir)
        if not isinstance(data_source, DataSource):
            data_source = DataSource(source_type="legacy_test_mapping", tables=data_source)
        bucket = self._create_storage_bucket()

        for table_name, file_paths in data_source.tables.items():
            table_name_upper = table_name.upper()
            source_format, uris = self._prepare_external_table_uris(bucket, table_name, file_paths)
            if not uris:
                raise ValueError(
                    f"BigQuery external mode requires Parquet files, Delta directories, or Iceberg "
                    f"directories for table '{table_name_upper}'. No supported sources were found."
                )

            uris_sql = ", ".join(f"'{uri}'" for uri in uris)
            connection_clause = (
                f"\n                WITH CONNECTION `{self.biglake_connection}`"
                if source_format in ("DELTA_LAKE", "ICEBERG")
                else ""
            )
            ddl = f"""
                CREATE OR REPLACE EXTERNAL TABLE `{self.project_id}.{self.dataset_id}.{table_name_upper}`{connection_clause}
                OPTIONS (
                  format = '{source_format}',
                  uris = [{uris_sql}]
                )
            """
            query_job = connection.query(ddl)
            query_job.result()

            table_stats[table_name_upper] = self._get_table_row_count(connection, table_name_upper)

        total_time = elapsed_seconds(start_time)
        return table_stats, total_time, None

    def _resolve_data_files(self, benchmark: Any, data_dir: Path) -> Any:
        return resolve_adapter_data_source(self, benchmark, data_dir)

    @staticmethod
    def _ensure_file_list(file_paths: Any) -> list[Any]:
        return file_paths if isinstance(file_paths, list) else [file_paths]

    def _filter_valid_files(self, file_paths: Any, *, allow_cloud: bool) -> list[Any]:
        valid_files: list[Any] = []
        for file_path in self._ensure_file_list(file_paths):
            if allow_cloud and is_cloud_path(str(file_path)):
                valid_files.append(file_path)
                continue
            path = Path(file_path)
            if path.exists() and (path.is_dir() or path.stat().st_size > 0):
                valid_files.append(path)
        return valid_files

    def _validate_compression_support(self, data_files: dict[str, Any], benchmark: Any) -> None:
        for file_paths in data_files.values():
            for file_path in self._ensure_file_list(file_paths):
                if detect_compression(file_path) != "zstd":
                    continue

                benchmark_name = getattr(benchmark, "name", "unknown")
                scale_factor = getattr(benchmark, "scale_factor", "unknown")
                data_dir = getattr(benchmark, "data_dir", getattr(benchmark, "output_dir", "<data_dir>"))
                raise ValueError(
                    f"\n❌ Incompatible data compression detected\n\n"
                    f"BigQuery does not support Zstd (.zst) compression for CSV file loading.\n"
                    f"Found Zstd file: {Path(file_path).name}\n\n"
                    f"To fix this, regenerate the data with gzip compression:\n\n"
                    f"  # Remove existing incompatible data\n"
                    f"  rm -rf {data_dir}\n\n"
                    f"  # Regenerate with gzip compression\n"
                    f"  benchbox run --platform bigquery --benchmark {benchmark_name} "
                    f"--scale {scale_factor} --compression gzip\n\n"
                    f"Or use uncompressed data (larger files, slower uploads):\n\n"
                    f"  benchbox run --platform bigquery --benchmark {benchmark_name} "
                    f"--scale {scale_factor} --compression none\n"
                )

    def _create_storage_bucket(self) -> Any:
        params = self._get_connection_params()
        credentials = self._load_credentials(params["credentials_path"])
        storage_client = storage.Client(project=self.project_id, credentials=credentials)
        return storage_client.bucket(self.storage_bucket)

    def _lookup_target_table(self, connection: Any, table_name: str) -> tuple[str, Any, Any]:
        table_name_upper = table_name.upper()
        dataset_ref = connection.dataset(self.dataset_id)
        try:
            table_obj = connection.get_table(dataset_ref.table(table_name_upper))
            return table_name_upper, dataset_ref.table(table_name_upper), table_obj
        except NotFound:
            try:
                table_obj = connection.get_table(dataset_ref.table(table_name))
                return table_name, dataset_ref.table(table_name), table_obj
            except NotFound:
                return table_name_upper, dataset_ref.table(table_name_upper), None

    def _resolve_target_table(self, connection: Any, table_name: str) -> tuple[str, Any]:
        resolved_name, table_ref, _ = self._lookup_target_table(connection, table_name)
        return resolved_name, table_ref

    def resolve_physical_table(self, logical_name: str, connection: Any = None) -> str:
        if connection is None:
            return super().resolve_physical_table(logical_name, connection)
        return self._lookup_target_table(connection, logical_name)[0]

    def resolve_physical_column(self, table_name: str, logical_column: str, connection: Any = None) -> str:
        if connection is not None:
            _, _, table_obj = self._lookup_target_table(connection, table_name)
            try:
                for field in table_obj.schema if table_obj is not None else ():
                    if field.name.lower() == logical_column.lower():
                        return field.name
            except TypeError:
                pass
        return logical_column.lower()

    def _get_table_row_count(self, connection: Any, table_name_upper: str) -> int:
        resolved_name, _ = self._resolve_target_table(connection, table_name_upper)
        query = f"SELECT COUNT(*) FROM `{self.project_id}.{self.dataset_id}.{resolved_name}`"
        query_job = connection.query(query)
        result = list(query_job.result())
        return result[0][0] if result else 0

    def _load_table_via_cloud_storage(
        self,
        connection: Any,
        bucket: Any,
        table_name: str,
        valid_files: list[Any],
        data_source: Any | None = None,
        benchmark: Any | None = None,
    ) -> int:
        if not valid_files:
            raise ValueError(f"No source files for BigQuery table {table_name}")
        if len(valid_files) > 10_000:
            raise ValueError(f"BigQuery table {table_name} exceeds the 10,000 source URI limit")
        resolved_name, table_ref = self._resolve_target_table(connection, table_name)

        job_configs = [
            self._build_load_job_config(
                file_path,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                allow_quoted_newlines=True,
                table_name=table_name,
                data_source=data_source,
                benchmark=benchmark,
            )
            for file_path in valid_files
        ]
        job_config = job_configs[0]
        if any(config.to_api_repr() != job_config.to_api_repr() for config in job_configs[1:]):
            raise ValueError(f"BigQuery source files for {table_name} have incompatible load settings")

        def upload_file(item: tuple[int, Any]) -> str:
            file_idx, file_path = item
            if is_cloud_path(str(file_path)):
                return str(file_path)
            path = Path(file_path)
            blob_name = f"{self.storage_prefix}/{table_name}_{file_idx}{''.join(path.suffixes)}"
            self.log_very_verbose(f"Uploading to Cloud Storage: {blob_name}")
            bucket.blob(blob_name).upload_from_filename(str(path))
            return f"gs://{self.storage_bucket}/{blob_name}"

        with ThreadPoolExecutor(max_workers=min(8, len(valid_files))) as uploads:
            uris = list(uploads.map(upload_file, enumerate(valid_files)))

        max_retries = 5
        load_job = None
        for attempt in range(max_retries):
            try:
                if load_job is None:
                    load_job = connection.load_table_from_uri(uris, table_ref, job_config=job_config)
                load_job.result()
                break
            except TooManyRequests as e:
                if attempt >= max_retries - 1:
                    raise
                sleep_seconds = 2.5 * (2**attempt)
                self.logger.warning(
                    "Hit BigQuery rate limit loading %s, retrying in %.1fs: %s",
                    table_name,
                    sleep_seconds,
                    e,
                )
                time.sleep(sleep_seconds)

        return self._get_table_row_count(connection, resolved_name)

    def _load_table_direct(
        self,
        connection: Any,
        table_name: str,
        valid_files: list[Path],
        data_source: Any | None = None,
        benchmark: Any | None = None,
    ) -> int:
        resolved_name, table_ref = self._resolve_target_table(connection, table_name)

        for file_idx, file_path in enumerate(valid_files):
            chunk_info = f" (chunk {file_idx + 1}/{len(valid_files)})" if len(valid_files) > 1 else ""
            self.log_very_verbose(f"Loading {table_name}{chunk_info} from {file_path.name}")
            write_disposition = (
                bigquery.WriteDisposition.WRITE_TRUNCATE if file_idx == 0 else bigquery.WriteDisposition.WRITE_APPEND
            )
            job_config = self._build_load_job_config(
                file_path,
                write_disposition=write_disposition,
                table_name=table_name,
                data_source=data_source,
                benchmark=benchmark,
            )

            if file_idx > 0:
                time.sleep(1.0)

            max_retries = 5
            load_job = None
            for attempt in range(max_retries):
                try:
                    if load_job is None:
                        with open(file_path, "rb") as source_file:
                            load_job = connection.load_table_from_file(source_file, table_ref, job_config=job_config)
                    load_job.result()
                    break
                except TooManyRequests as e:
                    if attempt >= max_retries - 1:
                        raise
                    sleep_seconds = 2.5 * (2**attempt)
                    self.logger.warning(
                        f"Hit BigQuery rate limit on {table_name} chunk {file_idx + 1}, retrying in {sleep_seconds:.1f}s: {e}"
                    )
                    time.sleep(sleep_seconds)

        return self._get_table_row_count(connection, resolved_name)

    def _build_load_job_config(
        self,
        file_path: Path | str,
        *,
        write_disposition: bigquery.WriteDisposition,
        allow_quoted_newlines: bool = False,
        table_name: str | None = None,
        data_source: Any | None = None,
        benchmark: Any | None = None,
    ) -> bigquery.LoadJobConfig:
        data_format = detect_data_format(file_path)
        if data_format == "parquet":
            return bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.PARQUET,
                autodetect=False,
                write_disposition=write_disposition,
            )

        if data_format not in self._DELIMITED_FORMATS:
            self.logger.warning(
                "Unrecognized data format %r for %s - falling back to CSV load job config",
                data_format,
                Path(file_path).name,
            )

        dialect_source = data_source or DataSource(source_type="bigquery_load_job", tables={})
        dialect = resolve_csv_dialect(
            dialect_source,
            table_name or Path(file_path).stem,
            Path(file_path),
            benchmark if benchmark is not None else NO_BENCHMARK,
        )
        config_kwargs: dict[str, Any] = {
            "source_format": bigquery.SourceFormat.CSV,
            "field_delimiter": dialect.delimiter,
            "skip_leading_rows": 1 if dialect.has_header else 0,
            "autodetect": False,
            "write_disposition": write_disposition,
        }
        if dialect.null_marker is not None:
            config_kwargs["null_marker"] = dialect.null_marker
        if allow_quoted_newlines:
            config_kwargs["allow_quoted_newlines"] = True
        return bigquery.LoadJobConfig(**config_kwargs)

    def _load_tables_via_cloud_storage(
        self,
        connection: Any,
        data_source: Any,
        bucket: Any,
        benchmark: Any | None = None,
    ) -> tuple[dict[str, int], dict[str, Any]]:
        logger = logging.getLogger(__name__)
        table_stats: dict[str, int] = {}
        per_table_timings: dict[str, Any] = {}

        if not isinstance(data_source, DataSource):
            data_source = DataSource(source_type="legacy_test_mapping", tables=data_source)

        for table_name, file_paths in data_source.tables.items():
            valid_files = self._filter_valid_files(file_paths, allow_cloud=True)
            if not valid_files:
                self.logger.warning(f"Skipping {table_name} - no valid data files")
                table_stats[table_name.upper()] = 0
                per_table_timings[table_name.upper()] = {"total_ms": 0}
                continue

            logger.debug(f"Loading {table_name} from {len(valid_files)} file(s)")
            try:
                self.log_verbose(f"Loading data for table: {table_name}")
                load_start = mono_time()
                row_count = self._load_table_via_cloud_storage(
                    connection, bucket, table_name, valid_files, data_source, benchmark
                )
                table_name_upper = table_name.upper()
                table_stats[table_name_upper] = row_count
                load_time = elapsed_seconds(load_start)
                per_table_timings[table_name_upper] = {"total_ms": load_time * 1000}
                chunk_info = f" from {len(valid_files)} file(s)" if len(valid_files) > 1 else ""
                self.logger.info(
                    f"✅ Loaded {row_count:,} rows into {table_name_upper}{chunk_info} in {load_time:.2f}s"
                )
            except Exception as e:
                self.logger.error(f"Failed to load {table_name}: {str(e)[:100]}...")
                table_stats[table_name.upper()] = 0
                per_table_timings[table_name.upper()] = {"total_ms": 0}

        return table_stats, per_table_timings

    def _load_tables_direct(
        self, connection: Any, data_source: Any, benchmark: Any | None = None
    ) -> tuple[dict[str, int], dict[str, Any]]:
        table_stats: dict[str, int] = {}
        per_table_timings: dict[str, Any] = {}

        if not isinstance(data_source, DataSource):
            data_source = DataSource(source_type="legacy_test_mapping", tables=data_source)

        for table_name, file_paths in data_source.tables.items():
            valid_files_any = self._filter_valid_files(file_paths, allow_cloud=False)
            valid_files = [file_path for file_path in valid_files_any if isinstance(file_path, Path)]
            if not valid_files:
                self.logger.warning(f"Skipping {table_name} - no valid data files")
                table_stats[table_name.upper()] = 0
                per_table_timings[table_name.upper()] = {"total_ms": 0}
                continue

            try:
                self.log_verbose(f"Direct loading data for table: {table_name}")
                load_start = mono_time()
                row_count = self._load_table_direct(connection, table_name, valid_files, data_source, benchmark)
                table_name_upper = table_name.upper()
                table_stats[table_name_upper] = row_count
                load_time = elapsed_seconds(load_start)
                per_table_timings[table_name_upper] = {"total_ms": load_time * 1000}
                chunk_info = f" from {len(valid_files)} file(s)" if len(valid_files) > 1 else ""
                self.logger.info(
                    f"✅ Loaded {row_count:,} rows into {table_name_upper}{chunk_info} in {load_time:.2f}s"
                )
            except Exception as e:
                self.logger.error(f"Failed to load {table_name}: {str(e)[:100]}...")
                table_stats[table_name.upper()] = 0
                per_table_timings[table_name.upper()] = {"total_ms": 0}

        return table_stats, per_table_timings

    def _prepare_external_table_uris(self, bucket: Any, table_name: str, file_paths: Any) -> tuple[str, list[str]]:
        valid_files = self._filter_valid_files(file_paths, allow_cloud=True)
        delta_uris = self._prepare_external_delta_uris(bucket, table_name, valid_files)
        if delta_uris:
            if not self.biglake_connection:
                raise ValueError(
                    "BigQuery Delta external mode requires --platform-option biglake_connection=<project.region.name>."
                )
            return "DELTA_LAKE", delta_uris
        iceberg_uris = self._prepare_external_iceberg_uris(bucket, table_name, valid_files)
        if iceberg_uris:
            if not self.biglake_connection:
                raise ValueError(
                    "BigQuery Iceberg external mode requires --platform-option biglake_connection=<project.region.name>."
                )
            return "ICEBERG", iceberg_uris
        return "PARQUET", self._prepare_external_parquet_uris(bucket, table_name, valid_files)

    def _prepare_external_parquet_uris(self, bucket: Any, table_name: str, file_paths: Any) -> list[str]:
        uris: list[str] = []
        valid_files = self._filter_valid_files(file_paths, allow_cloud=True)

        for file_path in valid_files:
            file_path_str = str(file_path)
            if is_cloud_path(file_path_str):
                if file_path_str.lower().endswith(".parquet"):
                    uris.append(file_path_str)
                continue

            path = Path(file_path)
            if path.suffix.lower() != ".parquet":
                continue

            blob_name = f"{self.storage_prefix}/{table_name.lower()}/{path.name}"
            blob = bucket.blob(blob_name)
            blob.upload_from_filename(str(path))
            uris.append(f"gs://{self.storage_bucket}/{blob_name}")

        return uris

    def _prepare_external_delta_uris(self, bucket: Any, table_name: str, file_paths: list[Path]) -> list[str]:
        uris: list[str] = []

        for file_path in file_paths:
            file_path_str = str(file_path)
            if is_cloud_path(file_path_str):
                if "/_delta_log" in file_path_str:
                    uris.append(file_path_str.split("/_delta_log", 1)[0] + "/")
                continue

            path = Path(file_path)
            if not path.is_dir() or not (path / "_delta_log").is_dir():
                continue

            table_prefix = f"{self.storage_prefix}/{table_name.lower()}/"
            for source_file in path.rglob("*"):
                if not source_file.is_file():
                    continue
                relative = source_file.relative_to(path)
                blob = bucket.blob(f"{table_prefix}{relative.as_posix()}")
                blob.upload_from_filename(str(source_file))
            uris.append(f"gs://{self.storage_bucket}/{table_prefix}")

        return uris

    def _prepare_external_iceberg_uris(self, bucket: Any, table_name: str, file_paths: list[Path]) -> list[str]:
        uris: list[str] = []
        local_dirs: list[Path] = []
        seen_iceberg_shape = False

        for file_path in file_paths:
            file_path_str = str(file_path)
            if is_cloud_path(file_path_str):
                if file_path_str.lower().endswith(".metadata.json"):
                    uris.append(file_path_str)
                elif "/metadata/" in file_path_str:
                    seen_iceberg_shape = True
                continue

            path = Path(file_path)
            if resolve_iceberg_metadata_file(path) is None:
                continue
            local_dirs.append(path)

        if seen_iceberg_shape and not uris and not local_dirs:
            raise ValueError(
                f"BigQuery Iceberg external mode found Iceberg-shaped cloud input for table '{table_name.lower()}' "
                "but no *.metadata.json file URI: pass the table's current gs://.../metadata/*.metadata.json URI."
            )

        if (uris or local_dirs) and not self.biglake_connection:
            raise ValueError(
                "BigQuery Iceberg external mode requires --platform-option biglake_connection=<project.region.name>."
            )

        for path in local_dirs:
            table_prefix = f"{self.storage_prefix}/{table_name.lower()}/"
            dest_uri = f"gs://{self.storage_bucket}/{table_prefix.rstrip('/')}"
            with tempfile.TemporaryDirectory(prefix="benchbox-iceberg-reloc-") as staging:
                relocated = relocate_iceberg_table(path, dest_uri, staging)
                for rel in relocated.data_files:
                    blob = bucket.blob(f"{table_prefix}{rel}")
                    blob.upload_from_filename(str(path / rel))
                for rel, staged in relocated.graph_files.items():
                    blob = bucket.blob(f"{table_prefix}{rel}")
                    blob.upload_from_filename(str(staged))
            uris.append(relocated.metadata_location)

        return uris

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:

        job_config = bigquery.QueryJobConfig(
            priority=getattr(
                bigquery.QueryPriority,
                self.job_priority,
                bigquery.QueryPriority.INTERACTIVE,
            ),
            use_query_cache=self.query_cache,
            dry_run=self.dry_run,
        )

        if self.maximum_bytes_billed:
            job_config.maximum_bytes_billed = self.maximum_bytes_billed

        if self.project_id and self.dataset_id:
            job_config.default_dataset = f"{self.project_id}.{self.dataset_id}"

        if benchmark_type.lower() in ["olap", "analytics", "tpch", "tpcds"]:
            job_config.use_legacy_sql = False
            job_config.flatten_results = False

        job_config.dry_run = self.dry_run
        connection._default_job_config = job_config
        self._track_configured_connection(connection)

    def _track_configured_connection(self, connection: Any) -> None:
        if not hasattr(self, "_configured_connections"):
            import weakref

            self._configured_connections = weakref.WeakSet()
        try:
            self._configured_connections.add(connection)
        except TypeError:
            pass

    def _reset_cached_dry_run_state(self, connection: Any = None) -> None:
        target_connections = set()
        if connection is not None:
            target_connections.add(connection)
        if hasattr(self, "_configured_connections"):
            for conn in list(self._configured_connections):
                target_connections.add(conn)

        for conn in target_connections:
            default_config = getattr(conn, "_default_job_config", None)
            if default_config is not None and hasattr(default_config, "dry_run"):
                default_config.dry_run = bool(getattr(self, "dry_run", False))

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
            translated_query = self._convert_to_bigquery_table(query)
            if "`" in translated_query:
                translated_query = self._normalize_table_names_case(translated_query)
                translated_query = self._qualify_table_names(translated_query, allow_fallback=("`" not in query))
            else:
                translated_query = self._qualify_table_names(translated_query)

            import re

            if re.sub(r"[^a-z0-9]", "", (benchmark_type or "").lower()).startswith("tpcdi"):
                translated_query = self._apply_tpcdi_bigquery_rewrites(translated_query)

            translated_query = self._safeguard_division_by_zero(translated_query)

            if "`" in translated_query:
                translated_query = self._normalize_table_names_case(translated_query)

            job_config = getattr(connection, "_default_job_config", bigquery.QueryJobConfig())
            if hasattr(job_config, "dry_run") and job_config.dry_run != self.dry_run:
                job_config.dry_run = self.dry_run

            query_job = connection.query(translated_query, job_config=job_config)
            result = list(query_job.result())

            execution_time = elapsed_seconds(start_time)
            actual_row_count = len(result) if result else 0

            job_stats = {
                "bytes_processed": query_job.total_bytes_processed,
                "bytes_billed": query_job.total_bytes_billed,
                "slot_ms": query_job.slot_millis,
                "creation_time": query_job.created.isoformat() if query_job.created else None,
                "start_time": query_job.started.isoformat() if query_job.started else None,
                "end_time": query_job.ended.isoformat() if query_job.ended else None,
            }

            validation_result = None
            if validate_row_count and benchmark_type:
                from benchbox.core.validation.query_validation import QueryValidator

                validator = QueryValidator()
                validation_result = validator.validate_query_result(
                    benchmark_type=benchmark_type,
                    query_id=query_id,
                    actual_row_count=actual_row_count,
                    scale_factor=scale_factor,
                    stream_id=stream_id,
                )

                if validation_result.warning_message:
                    self.log_verbose(f"Row count validation: {validation_result.warning_message}")
                elif not validation_result.is_valid:
                    self.log_verbose(f"Row count validation FAILED: {validation_result.error_message}")
                else:
                    self.log_very_verbose(
                        f"Row count validation PASSED: {actual_row_count} rows "
                        f"(expected: {validation_result.expected_row_count})"
                    )

            result_dict = self._build_query_result_with_validation(
                query_id=query_id,
                execution_time=execution_time,
                actual_row_count=actual_row_count,
                first_row=result[0] if result else None,
                validation_result=validation_result,
                materialized_rows=result,
            )

            result_dict["translated_query"] = translated_query if translated_query != query else None
            result_dict["job_statistics"] = job_stats
            result_dict["job_id"] = query_job.job_id
            result_dict["resource_usage"] = job_stats

            if self.capture_plans and result_dict.get("status") == "SUCCESS":
                query_plan, plan_capture_time_ms = self._capture_bq_plan(query_job, query_id)
                if query_plan:
                    result_dict["query_plan"] = query_plan
                    result_dict["plan_fingerprint"] = query_plan.plan_fingerprint
                    if getattr(self, "normalize_plan_literals", False):
                        result_dict["plan_fingerprint_normalized"] = query_plan.normalized_fingerprint
                if plan_capture_time_ms is not None:
                    result_dict["plan_capture_time_ms"] = plan_capture_time_ms

            return result_dict

        except PlanCaptureError:
            raise
        except Exception as e:
            execution_time = elapsed_seconds(start_time)

            return {
                "query_id": query_id,
                "status": "FAILED",
                "execution_time_seconds": execution_time,
                "rows_returned": 0,
                "error": str(e),
                "error_type": type(e).__name__,
            }

    @staticmethod
    def _inline_pk_columns(search_text: str) -> list[str]:
        import re

        if not re.search(r"PRIMARY\s+KEY", search_text, flags=re.IGNORECASE):
            return []
        _pk_keywords = {"PRIMARY", "CONSTRAINT", "FOREIGN", "UNIQUE", "CHECK", "KEY"}

        def _match_segment(segment: str) -> str | None:
            match = re.search(
                r"[`\"']?(\w+)[`\"']?\s+(?:[A-Z0-9_]+\s*(?:\([^()]*\))?\s*)+?"
                r"PRIMARY\s+KEY(?!\s*\()",
                segment,
                flags=re.IGNORECASE,
            )
            if match and match.group(1).upper() not in _pk_keywords:
                return match.group(1)
            return None

        names = []
        depth = 0
        current: list[str] = []
        for char in search_text:
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            if char == "," and depth == 0:
                found = _match_segment("".join(current))
                if found is not None:
                    names.append(found)
                current = []
            else:
                current.append(char)
        found = _match_segment("".join(current))
        if found is not None:
            names.append(found)
        return names

    @staticmethod
    def _normalize_bq_column_types(text: str) -> str:
        import re

        def _decimal_to_bignumeric(match: re.Match[str]) -> str:
            precision, scale = int(match.group(2)), int(match.group(3))
            if scale > 9 or precision > 38:
                return f"BIGNUMERIC({precision},{scale})"
            return match.group(0)

        text = re.sub(
            r"\b(DECIMAL|NUMERIC)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)",
            _decimal_to_bignumeric,
            text,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"\bVARCHAR\s*(\(\s*\d+\s*\))?",
            "STRING",
            text,
            flags=re.IGNORECASE,
        )

    def _qualify_table_target(self, raw_target: str) -> str:
        target_bare = raw_target.strip("`")
        if "." in target_bare:
            *qualifier, bare = target_bare.split(".")
            normalized = ".".join([*qualifier, bare.upper()])
        else:
            normalized = target_bare.upper()

        if f"{self.dataset_id}." not in normalized:
            return f"`{self.project_id}.{self.dataset_id}.{normalized}`"
        elif not normalized.startswith("`"):
            *qualifier, bare = normalized.split(".")
            return "`" + ".".join([*qualifier, bare.upper()]) + "`"
        return normalized

    def _qualify_ctas_target(self, work: str) -> str:
        import re

        ctas = re.match(
            r"^(\s*CREATE\s+(.+?)\s+)(`?[A-Za-z0-9_.]+`?)(\s+AS\b.*)",
            work,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not ctas:
            return work

        raw_prefix = ctas.group(1)
        modifiers = ctas.group(2).upper()
        raw_target = ctas.group(3)
        as_and_rest = ctas.group(4)

        if "VIEW" in modifiers or "TEMP" in modifiers or "TEMPORARY" in modifiers:
            if re.fullmatch(
                r"(?:VIEW|OR\s+REPLACE\s+VIEW|MATERIALIZED\s+VIEW|OR\s+REPLACE\s+MATERIALIZED\s+VIEW)",
                modifiers.strip(),
                flags=re.IGNORECASE,
            ):
                qualified_target = self._qualify_table_target(raw_target)
                return raw_prefix + qualified_target + as_and_rest
            return work

        leading_space = raw_prefix[: len(raw_prefix) - len(raw_prefix.lstrip())]
        if "IF NOT EXISTS" in modifiers:
            verb = f"{leading_space}CREATE TABLE IF NOT EXISTS "
        else:
            verb = f"{leading_space}CREATE OR REPLACE TABLE "

        qualified_target = self._qualify_table_target(raw_target)
        return verb + qualified_target + as_and_rest

    def _convert_to_bigquery_table(self, statement: str) -> str:
        import re

        from benchbox.platforms.cloud_shared import split_leading_sql_comments

        prefix, work = split_leading_sql_comments(statement)

        if not work.strip().upper().startswith("CREATE"):
            return statement

        pattern = re.compile(
            r"^\s*CREATE\s+(?:(OR\s+REPLACE)\s+)?TABLE\s+(?:(IF\s+NOT\s+EXISTS)\s+)?`?([a-zA-Z0-9_.]+)`?\s*(\(.*)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.match(work)
        if match:
            has_if_not_exists = bool(match.group(2))
            create_verb = "CREATE TABLE IF NOT EXISTS" if has_if_not_exists else "CREATE OR REPLACE TABLE"
            qualified_table = self._qualify_table_target(match.group(3))
            rest = self._normalize_bq_column_types(match.group(4))
            work = f"{create_verb} {qualified_table} {rest}"
        else:
            ctas_work = self._qualify_ctas_target(work)
            if ctas_work != work:
                work = ctas_work
            else:
                if (
                    re.search(r"\bCREATE\s+TABLE\b", work, flags=re.IGNORECASE)
                    and not re.search(r"\bOR\s+REPLACE\b", work, flags=re.IGNORECASE)
                    and not re.search(r"\bIF\s+NOT\s+EXISTS\b", work, flags=re.IGNORECASE)
                ):
                    work = re.sub(
                        r"\bCREATE\s+TABLE\b",
                        "CREATE OR REPLACE TABLE",
                        work,
                        count=1,
                        flags=re.IGNORECASE,
                    )
            work = self._normalize_bq_column_types(work)

        pk_cols = self._inline_pk_columns(rest if match else work)
        if match:
            rest = re.sub(r"\s+PRIMARY\s+KEY(?!\s*\()\b", "", rest, flags=re.IGNORECASE)
            has_table_pk = re.search(r"PRIMARY\s+KEY\s*\(", rest, flags=re.IGNORECASE) is not None
            if pk_cols and not has_table_pk:
                scan = re.sub(
                    r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\"|--[^\n]*|/\*.*?\*/",
                    lambda match: " " * len(match.group(0)),
                    rest,
                    flags=re.DOTALL,
                )
                depth = 0
                for i, ch in enumerate(scan):
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            rest = rest[:i] + f", PRIMARY KEY ({', '.join(pk_cols)}) NOT ENFORCED" + rest[i:]
                            break
            work = f"{create_verb} {qualified_table} {rest}"
        else:
            work = re.sub(r"\s+PRIMARY\s+KEY(?!\s*\()\b", "", work, flags=re.IGNORECASE)

        work = re.sub(
            r"PRIMARY\s+KEY\s*\(([^()]*)\)(?!\s*NOT\s+ENFORCED)",
            r"PRIMARY KEY (\1) NOT ENFORCED",
            work,
            flags=re.IGNORECASE,
        )

        references_pattern = re.compile(
            r"REFERENCES\s+(`?[a-zA-Z0-9_.]+`?)\s*\(([^()]*)\)(?!\s*NOT\s+ENFORCED)",
            flags=re.IGNORECASE,
        )
        masked_work = re.sub(
            r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\"|--[^\n]*|/\*.*?\*/",
            lambda match: " " * len(match.group(0)),
            work,
            flags=re.DOTALL,
        )
        pieces: list[str] = []
        cursor = 0
        for match in references_pattern.finditer(masked_work):
            original_match = work[match.start() : match.end()]
            inner = re.match(
                r"REFERENCES\s+(`?[a-zA-Z0-9_.]+`?)\s*\(([^()]*)\)(?!\s*NOT\s+ENFORCED)",
                original_match,
                flags=re.IGNORECASE,
            )
            if inner is None:
                continue
            pieces.append(work[cursor : match.start()])
            pieces.append(f"REFERENCES {self._qualify_table_target(inner.group(1))} ({inner.group(2)}) NOT ENFORCED")
            cursor = match.end()
        pieces.append(work[cursor:])
        work = "".join(pieces)

        if "PARTITION BY" not in work.upper() and self.partitioning_field:
            work += f" PARTITION BY DATE({self.partitioning_field})"

        if "CLUSTER BY" not in work.upper() and self.clustering_fields:
            clustering = ", ".join(self.clustering_fields)
            work += f" CLUSTER BY {clustering}"

        return prefix + work

    _FALLBACK_QUALIFY_TABLES = (
        "REGION",
        "NATION",
        "CUSTOMER",
        "SUPPLIER",
        "PART",
        "PARTSUPP",
        "ORDERS",
        "LINEITEM",
    )

    _QUALIFY_CLAUSE_KEYWORDS = frozenset(
        {
            "AS",
            "AND",
            "OR",
            "ON",
            "USING",
            "WHERE",
            "GROUP",
            "ORDER",
            "HAVING",
            "QUALIFY",
            "WINDOW",
            "LIMIT",
            "OFFSET",
            "JOIN",
            "INNER",
            "LEFT",
            "RIGHT",
            "FULL",
            "CROSS",
            "NATURAL",
            "SET",
            "WHEN",
            "THEN",
            "ELSE",
            "END",
            "UNION",
            "INTERSECT",
            "EXCEPT",
            "SELECT",
            "FROM",
            "VALUES",
            "TABLE",
        }
    )

    def _extract_unqualified_tables(self, query: str) -> list[str] | None:
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            return None
        try:
            trees = sqlglot.parse(query, read="bigquery")
        except Exception:
            return None
        if not trees or any(tree is None for tree in trees):
            return None
        cte_names = set()
        for tree in trees:
            for cte in tree.find_all(exp.CTE):
                name = (cte.alias_or_name or "").upper()
                if name:
                    cte_names.add(name)
        tables: list[str] = []
        for tree in trees:
            for cte in tree.find_all(exp.CTE):
                earlier_ctes = set()
                with_clause = cte.parent
                if isinstance(with_clause, exp.With):
                    for sibling in with_clause.expressions:
                        if sibling is cte:
                            break
                        earlier_ctes.add((sibling.alias_or_name or "").upper())
                for table in cte.this.find_all(exp.Table):
                    if table.db or table.catalog:
                        continue
                    name = (table.name or "").upper()
                    if name and name not in tables and name not in earlier_ctes:
                        tables.append(name)
            for table in tree.find_all(exp.Table):
                if table.db or table.catalog:
                    continue
                name = (table.name or "").upper()
                if not name or name in cte_names or name in tables:
                    continue
                tables.append(name)
        return tables

    def _qualify_single_statement(self, statement: str, allow_fallback: bool = True) -> str:
        import re

        table_names = self._extract_unqualified_tables(statement)
        if table_names is None:
            if not allow_fallback:
                return statement
            table_names = list(self._FALLBACK_QUALIFY_TABLES)
        if self._batch_temp_tables:
            table_names = [name for name in table_names if name not in self._batch_temp_tables]
        table_names = [name for name in table_names if not _is_project_scoped_information_schema_view(name)]

        literal_pattern = r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\"|--[^\n]*|/\*.*?\*/"

        def _mask(text: str) -> str:
            return re.sub(literal_pattern, lambda match: " " * len(match.group(0)), text, flags=re.DOTALL)

        masked = _mask(statement)

        for table_name in table_names:
            qualified_name = f"`{self.project_id}.{self.dataset_id}.{table_name}`"
            has_refs = (
                re.search(rf"(?<![\w.`])`?{re.escape(table_name)}`?\s*\.", masked, flags=re.IGNORECASE) is not None
            )

            pattern = (
                rf"(\bFROM\s+|\bJOIN\s+|\bINSERT\s+INTO\s+|\bUPDATE\s+"
                rf"|\bMERGE\s+INTO\s+|\bUSING\s+"
                rf"|\bTRUNCATE\s+(?:TABLE\s+)?|\bDROP\s+(?:TABLE\s+|VIEW\s+|MATERIALIZED\s+VIEW\s+)(?:IF\s+EXISTS\s+)?"
                rf"|\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?|\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                rf"|\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                rf"|\bFROM\s*\(\s*|\bJOIN\s*\(\s*|,\s*)"
                rf"(`{re.escape(table_name)}`|{re.escape(table_name)}\b)"
            )
            segments: list[str] = []
            last = 0
            for match in re.finditer(pattern, masked, flags=re.IGNORECASE):
                prefix = match.group(1)
                name_start, name_end = match.span(2)
                if prefix.strip() == "," and not self._in_from_clause(masked, match.end(1)):
                    continue
                segments.append(statement[last:name_start])
                replacement = qualified_name
                alias_forbidden = bool(re.search(r"(?i)\b(INSERT|CREATE|DROP|TRUNCATE|ALTER)\b", prefix))
                if has_refs and not alias_forbidden and not self._qualify_has_alias(masked[name_end:]):
                    replacement += f" AS {table_name.lower()}"
                segments.append(replacement)
                last = name_end
            segments.append(statement[last:])
            statement = "".join(segments)
            masked = _mask(statement)

        return statement

    def _qualify_table_names(self, query: str, allow_fallback: bool = True) -> str:
        if not query or not query.strip():
            return query

        statements = split_sql_statements(query)
        if not statements:
            return query

        import re

        temp_tables = {
            m.group(1).upper()
            for stmt in statements
            for m in re.finditer(
                r"(?i)\bCREATE\s+(?:OR\s+REPLACE\s+)?TEMP(?:ORARY)?\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?",
                stmt,
            )
        }
        previous_temp_tables = self._batch_temp_tables
        self._batch_temp_tables = temp_tables
        try:
            qualified_parts = [
                self._qualify_single_statement(stmt, allow_fallback=allow_fallback) for stmt in statements
            ]
        finally:
            self._batch_temp_tables = previous_temp_tables
        result = ";\n".join(qualified_parts)
        if query.rstrip().endswith(";"):
            result += ";"
        if query.endswith("\n"):
            result += "\n"
        return result

    _FROM_SCAN_TOKEN = None
    _batch_temp_tables: frozenset[str] | set[str] = frozenset()

    @staticmethod
    def _from_scan_token():
        import re

        if BigQueryAdapter._FROM_SCAN_TOKEN is None:
            BigQueryAdapter._FROM_SCAN_TOKEN = re.compile(
                r"(?P<lparen>\()|(?P<rparen>\))|(?P<kw>\bFROM\b|\bJOIN\b|\bWHERE\b|\bGROUP\s+BY\b"
                r"|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|\bWINDOW\b|\bQUALIFY\b|\bOVER\b|\bON\b"
                r"|\bUSING\b|\bINTO\b|\bSET\b|\bVALUES\b)|(?P<comma>,)|(?P<semi>;)",
                flags=re.IGNORECASE,
            )
        return BigQueryAdapter._FROM_SCAN_TOKEN

    @staticmethod
    def _from_scan_keyword(stack: list[list], depth: int, first: str) -> None:
        if first == "FROM":
            stack.append([depth, False])
        elif first == "JOIN":
            if stack and stack[-1][0] == depth:
                stack[-1][1] = False
        elif first in ("WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "WINDOW", "QUALIFY", "OVER"):
            while stack and stack[-1][0] >= depth:
                stack.pop()
        elif first == "ON":
            if stack and stack[-1][0] == depth:
                stack[-1][1] = True
        elif first == "USING":
            if stack and stack[-1][0] == depth:
                stack[-1][1] = True
            else:
                stack.append([depth, False])
        elif first in ("INTO", "SET", "VALUES"):
            while stack and stack[-1][0] >= depth:
                stack.pop()

    @staticmethod
    def _in_from_clause(masked: str, pos: int) -> bool:
        import re

        depth = 0
        stack: list[list] = []
        token = BigQueryAdapter._from_scan_token()
        for match in token.finditer(masked[:pos]):
            kind = match.lastgroup
            word = (match.group("kw") or "").upper().split()
            first = word[0] if word else ""
            if kind == "lparen":
                depth += 1
            elif kind == "rparen":
                depth = max(0, depth - 1)
                while stack and stack[-1][0] > depth:
                    stack.pop()
            elif kind == "semi":
                stack.clear()
            elif kind == "kw":
                BigQueryAdapter._from_scan_keyword(stack, depth, first)
            elif kind == "comma":
                if stack and stack[-1][0] == depth and not stack[-1][1]:
                    return True
                if stack and stack[-1][0] == depth and stack[-1][1]:
                    tail = masked[match.end() :]
                    if re.match(r"\s*[A-Za-z_][\w$]*", tail):
                        stack[-1][1] = False
                        return True
                continue
        return False

    @staticmethod
    def _qualify_has_alias(after: str) -> bool:
        import re

        scan = re.sub(r"/\*.*?\*/", " ", after, flags=re.DOTALL)
        as_match = re.match(r"\s+AS\s+(?:`([^`]+)`|([A-Za-z_]\w*))", scan, flags=re.IGNORECASE)
        if as_match:
            if as_match.group(1) is not None:
                return True
            return as_match.group(2).upper() not in BigQueryAdapter._QUALIFY_CLAUSE_KEYWORDS
        bare_match = re.match(r"\s+(?:`([^`]+)`|([A-Za-z_]\w*))", scan)
        if bare_match:
            if bare_match.group(1) is not None:
                return True
            return bare_match.group(2).upper() not in BigQueryAdapter._QUALIFY_CLAUSE_KEYWORDS
        return False

    def _normalize_table_names_case(self, query: str) -> str:
        import re

        pattern = r"`([A-Za-z_][A-Za-z0-9_]*)`"

        def uppercase_table(match: re.Match[str]) -> str:
            return f"`{match.group(1).upper()}`"

        return re.sub(pattern, uppercase_table, query)

    def _apply_tpcdi_bigquery_rewrites(self, query: str) -> str:
        from benchbox.platforms.cloud_shared import rewrite_tpcdi_for_bigquery

        return rewrite_tpcdi_for_bigquery(query)

    def _safeguard_division_by_zero(self, query: str) -> str:
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            return query

        try:
            tree = sqlglot.parse_one(query, read="bigquery")
        except Exception as e:
            self.log_very_verbose(f"Division safeguard skipped (unparseable BigQuery SQL): {e}")
            return query
        if not any(isinstance(node, exp.Div) for node in tree.walk()):
            return query

        def to_safe_divide(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Div):
                return exp.Anonymous(
                    this="SAFE_DIVIDE",
                    expressions=[node.this.copy(), node.expression.copy()],
                )
            return node

        return tree.transform(to_safe_divide).sql(dialect="bigquery", identify=True)

    def _get_platform_metadata(self, connection: Any) -> dict[str, Any]:
        metadata = {
            "platform": self.platform_name,
            "project_id": self.project_id,
            "dataset_id": self.dataset_id,
            "location": self.location,
            "result_cache_enabled": self.query_cache,
        }

        try:
            dataset_ref = connection.dataset(self.dataset_id)
            dataset = connection.get_dataset(dataset_ref)

            metadata["dataset_info"] = {
                "created": dataset.created.isoformat() if dataset.created else None,
                "modified": dataset.modified.isoformat() if dataset.modified else None,
                "location": dataset.location,
                "description": dataset.description,
            }

            tables = list(connection.list_tables(dataset))
            table_info = []

            for table in tables:
                table_ref = dataset.table(table.table_id)
                table_obj = connection.get_table(table_ref)

                table_info.append(
                    {
                        "table_id": table.table_id,
                        "num_rows": table_obj.num_rows,
                        "num_bytes": table_obj.num_bytes,
                        "created": table_obj.created.isoformat() if table_obj.created else None,
                        "modified": table_obj.modified.isoformat() if table_obj.modified else None,
                    }
                )

            metadata["tables"] = table_info

            try:
                project = connection.get_project(self.project_id)
                metadata["project_info"] = {
                    "display_name": project.display_name,
                    "project_number": project.project_number,
                }
            except Exception:
                pass

        except Exception as e:
            metadata["metadata_error"] = str(e)

        return metadata

    def get_query_plan(self, connection: Any, query: str) -> dict[str, Any] | None:
        if bigquery is None:
            return None
        try:
            translated_query = (
                self._normalize_table_names_case(query) if "`" in query else self._qualify_table_names(query)
            )
            job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False, use_legacy_sql=False)
            if self.project_id and self.dataset_id:
                job_config.default_dataset = f"{self.project_id}.{self.dataset_id}"
            if self.maximum_bytes_billed:
                job_config.maximum_bytes_billed = self.maximum_bytes_billed
            query_job = connection.query(translated_query, job_config=job_config)
            try:
                bytes_processed = int(query_job.total_bytes_processed or 0)
            except (TypeError, ValueError):
                return None
            from benchbox.core.cost.pricing import resolve_bigquery_price_per_tb

            resolution = resolve_bigquery_price_per_tb(self.location)
            estimated_cost = None
            if resolution.value is not None:
                estimated_cost = bytes_processed / (1024**4) * float(resolution.value)
            return {
                "bytes_processed": bytes_processed,
                "estimated_cost": estimated_cost,
                "pricing_fallback": resolution.fallback_used,
            }
        except Exception:
            return None

    def get_query_plan_parser(self):
        return _lazy_query_parser("benchbox.core.query_plans.parsers.bigquery", "BigQueryQueryPlanParser")

    def _capture_bq_plan(self, job: Any, query_id: str) -> tuple[Any, float]:
        if not self.capture_plans:
            return None, 0.0
        if self.plan_query_filter and query_id not in self.plan_query_filter:
            return None, 0.0

        start_time = time.perf_counter()
        try:
            stages = [self._bq_entry_to_dict(entry) for entry in (getattr(job, "query_plan", None) or [])]
            if not stages:
                capture_time_ms = (time.perf_counter() - start_time) * 1000
                self._record_plan_capture_failure(
                    query_id,
                    reason="explain_failed",
                    message="Completed job exposed no query_plan stages",
                )
                return None, capture_time_ms

            parser = self.get_query_plan_parser()
            plan = parser.parse_explain_output(query_id, json.dumps(stages))
        except PlanCaptureError:
            raise
        except Exception as exc:
            capture_time_ms = (time.perf_counter() - start_time) * 1000
            self._record_plan_capture_failure(query_id, reason="parse_error", message=str(exc))
            return None, capture_time_ms

        if plan is None:
            capture_time_ms = (time.perf_counter() - start_time) * 1000
            self._record_plan_capture_failure(query_id, reason="parse_error", message="Parser returned no plan")
            return None, capture_time_ms

        raw_output_policy, raw_output_max_bytes = self._resolve_raw_output_policy(query_id)
        plan.apply_raw_output_policy(raw_output_policy, raw_output_max_bytes)

        capture_time_ms = (time.perf_counter() - start_time) * 1000
        self.query_plans_captured += 1
        return plan, capture_time_ms

    @staticmethod
    def _bq_entry_to_dict(entry: Any) -> dict[str, Any]:
        if isinstance(entry, dict):
            return entry
        raw = getattr(entry, "_properties", None)
        if isinstance(raw, dict) and raw:
            return raw
        steps = [
            {
                "kind": getattr(step, "kind", None),
                "substeps": list(getattr(step, "substeps", None) or []),
            }
            for step in (getattr(entry, "steps", None) or [])
        ]
        return {
            "name": getattr(entry, "name", None),
            "id": getattr(entry, "entry_id", getattr(entry, "id", None)),
            "status": getattr(entry, "status", None),
            "inputStages": list(getattr(entry, "input_stages", None) or []),
            "recordsRead": getattr(entry, "records_read", None),
            "recordsWritten": getattr(entry, "records_written", None),
            "steps": steps,
        }

    def close_connection(self, connection: Any) -> None:
        if not connection:
            return

        try:
            if hasattr(connection, "close"):
                connection.close()
        except Exception as e:
            error_msg = str(e).lower()
            if any(keyword in error_msg for keyword in ["credential", "refresh", "anonymous", "auth", "token"]):
                self.log_very_verbose(f"Credential cleanup warning (non-fatal, suppressed): {e}")
            else:
                self.logger.warning(f"Error closing connection: {e}")

        try:
            if hasattr(connection, "_transport"):
                transport = connection._transport
                if hasattr(transport, "close"):
                    transport.close()
        except Exception:
            pass

    _supported_tuning_type_names = ("PARTITIONING", "CLUSTERING")

    def generate_tuning_clause(self, table_tuning) -> str:
        if not table_tuning or not table_tuning.has_any_tuning():
            return ""

        clauses = []

        try:
            from benchbox.core.tuning.interface import TuningType

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                partition_col = sorted_cols[0]

                col_type = partition_col.type.upper()
                if any(date_type in col_type for date_type in ["DATE", "TIMESTAMP", "DATETIME"]):
                    if "DATE" in col_type:
                        partition_clause = f"PARTITION BY {partition_col.name}"
                    else:
                        partition_clause = f"PARTITION BY DATE({partition_col.name})"
                elif "INT" in col_type:
                    partition_clause = (
                        f"PARTITION BY RANGE_BUCKET({partition_col.name}, GENERATE_ARRAY(0, 1000000, 10000))"
                    )
                else:
                    partition_clause = f"PARTITION BY DATE({partition_col.name})"

                clauses.append(partition_clause)

            cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
            if cluster_columns:
                sorted_cols = sorted(cluster_columns, key=lambda col: col.order)
                cluster_cols = sorted_cols[:4]
                column_names = [col.name for col in cluster_cols]

                cluster_clause = f"CLUSTER BY {', '.join(column_names)}"
                clauses.append(cluster_clause)

        except ImportError:
            pass

        return " ".join(clauses)

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        if not table_tuning or not table_tuning.has_any_tuning():
            return

        table_name = self.resolve_physical_table(table_tuning.table_name)
        self.logger.info(f"Applying BigQuery tunings for table: {table_name}")

        try:
            from benchbox.core.tuning.interface import TuningType

            dataset_ref = connection.dataset(self.dataset_id)
            table_ref = dataset_ref.table(table_name)

            try:
                table_obj = connection.get_table(table_ref)

                if table_obj.time_partitioning:
                    self.logger.info(f"Table {table_name} has partitioning: {table_obj.time_partitioning.type_}")

                if table_obj.clustering_fields:
                    self.logger.info(f"Table {table_name} has clustering: {table_obj.clustering_fields}")

                partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
                cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)

                needs_recreation = False

                if partition_columns and not table_obj.time_partitioning:
                    needs_recreation = True
                    self.logger.info(f"Table {table_name} needs recreation for partitioning")

                if cluster_columns:
                    sorted_cols = sorted(cluster_columns, key=lambda col: col.order)
                    desired_clustering = [
                        self.resolve_physical_column(table_name, col.name, connection) for col in sorted_cols[:4]
                    ]
                    current_clustering = table_obj.clustering_fields or []

                    if desired_clustering != current_clustering:
                        needs_recreation = True
                        self.logger.info(f"Table {table_name} needs recreation for clustering")

                if needs_recreation:
                    self.logger.warning(
                        f"Table {table_name} configuration differs from desired tuning. "
                        "Consider recreating the table with proper tuning configuration."
                    )

            except Exception as e:
                self.logger.warning(f"Could not verify table configuration for {table_name}: {e}")

            distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
            if distribution_columns:
                self.logger.warning(f"Distribution tuning not directly supported in BigQuery for table: {table_name}")

            sorting_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
            if sorting_columns:
                sorted_cols = sorted(sorting_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                self.logger.info(
                    f"Sorting in BigQuery achieved via clustering for table {table_name}: {', '.join(column_names)}"
                )

        except ImportError:
            self.logger.warning("Tuning interface not available - skipping tuning application")
        except Exception as e:
            raise ValueError(f"Failed to apply tunings to BigQuery table {table_name}: {e}") from e

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

        apply_standard_unified_tuning(self, unified_config, connection)

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        if not platform_config:
            return

        self.logger.info("BigQuery platform optimizations stored for query execution")

    apply_constraint_configuration = make_informational_constraint_applier(
        "Primary key constraints enabled for BigQuery (applied during table creation)",
        "Foreign key constraints enabled for BigQuery (applied during table creation)",
    )


def _apply_bigquery_config_fields(config: Any) -> None:
    staging_root = config.options.get("staging_root")
    if not staging_root:
        default_output = config.options.get("default_output_location")
        if isinstance(default_output, str) and default_output.startswith("gs://"):
            staging_root = default_output

    storage_bucket = config.options.get("storage_bucket")
    storage_prefix = config.options.get("storage_prefix")
    if staging_root:
        try:
            path_info = get_cloud_path_info(staging_root)
            if path_info and path_info.get("bucket"):
                storage_bucket = path_info["bucket"]
                storage_prefix = path_info.get("path", "")
        except (ValueError, TypeError):
            pass

    config.staging_root = staging_root
    config.storage_bucket = storage_bucket
    config.storage_prefix = storage_prefix


_build_bigquery_config = make_registered_platform_config_builder(
    "bigquery",
    __name__,
    "Google BigQuery",
    "google-cloud-bigquery",
    [
        "project_id",
        "dataset_id",
        "location",
        "credentials_path",
    ],
    postprocess=_apply_bigquery_config_fields,
)
