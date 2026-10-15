# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.core.benchmark_mixins import CursorValidationQueryExecutionMixin
from benchbox.core.sql_utils import normalize_table_name_in_sql
from benchbox.platforms.base.tuning import make_informational_constraint_applier
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.iceberg_layout import is_iceberg_directory, relocate_iceberg_table

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        TuningColumn,
        UnifiedTuningConfiguration,
    )

from ..core.exceptions import ConfigurationError
from ..utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
    get_dependency_group_packages,
)
from ..utils.file_format import detect_compression, is_parquet_format
from .base import DriverIsolationCapability, PlatformAdapter
from .base.config_utils import make_registered_platform_config_builder
from .base.data_loading import (
    NO_BENCHMARK,
    DataSource,
    FileFormatRegistry,
    resolve_adapter_data_source,
    resolve_csv_dialect,
    run_staged_table_loads,
)
from .base.runtime_metadata import build_default_normalized_result_metadata

try:
    import redshift_connector
except ImportError:
    try:
        import psycopg

        redshift_connector = None
    except ImportError:
        psycopg = None
        redshift_connector = None

try:
    import boto3
    from botocore.config import Config as BotocoreConfig
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    boto3 = None


def _compact_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if value not in (None, "", {}, [], ())}


def redshift_deployment_type(hostname: str | None) -> str:
    host = hostname or ""
    if ".redshift-serverless.amazonaws.com" in host:
        return "serverless"
    if ".redshift.amazonaws.com" in host:
        return "provisioned"
    return "unknown"


_RESOURCE_KEY_BY_DEPLOYMENT = {"serverless": "workgroup_name", "provisioned": "cluster_identifier"}


def redshift_resource_key(config: Mapping[str, Any]) -> str | None:
    return _RESOURCE_KEY_BY_DEPLOYMENT.get(redshift_deployment_type(config.get("host")))


class RedshiftAdapter(CursorValidationQueryExecutionMixin, PlatformAdapter):
    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    supports_external_tables = True
    plan_capture_phase_eligible = True
    supports_refresh_functions = True
    physical_identifier_case = "lower"

    def __init__(self, **config):
        super().__init__(**config)

        dependency_packages = get_dependency_group_packages("redshift")

        if not redshift_connector and not psycopg:
            available, missing = check_platform_dependencies("redshift")
            if not available:
                error_msg = get_dependency_error_message("redshift", missing)
                raise ImportError(error_msg)
        else:
            shared_packages = [pkg for pkg in dependency_packages if pkg != "redshift-connector"]
            if shared_packages:
                available_shared, missing_shared = check_platform_dependencies("redshift", shared_packages)
                if not available_shared:
                    error_msg = get_dependency_error_message("redshift", missing_shared)
                    raise ImportError(error_msg)

        self._dialect = "redshift"

        self.host = config.get("host")
        self.port = config.get("port") if config.get("port") is not None else 5439
        self.database = config.get("database") or "dev"
        self.username = config.get("username")
        self.password = config.get("password")
        from benchbox.core.compute_resource import bind_resource_key

        compute = bind_resource_key("redshift", config, redshift_resource_key(config))
        self.cluster_identifier = compute.get("cluster_identifier")
        self.compute_resource = compute.get("compute_resource")

        self.admin_database = config.get("admin_database") or "dev"

        self.schema = config.get("schema") or "public"

        self.connect_timeout = config.get("connect_timeout") if config.get("connect_timeout") is not None else 10
        self.statement_timeout = config.get("statement_timeout") if config.get("statement_timeout") is not None else 0
        self.sslmode = config.get("sslmode") or "require"

        self.wlm_query_slot_count = (
            config.get("wlm_query_slot_count") if config.get("wlm_query_slot_count") is not None else 1
        )
        self.wlm_query_queue_name = config.get("wlm_query_queue_name")

        self.ssl_enabled = config.get("ssl_enabled") if config.get("ssl_enabled") is not None else True
        self.ssl_insecure = config.get("ssl_insecure") if config.get("ssl_insecure") is not None else False
        self.sslrootcert = config.get("sslrootcert")

        staging_root = config.get("staging_root")
        self.staging_root = staging_root
        if staging_root:
            from benchbox.utils.cloud_storage import get_cloud_path_info

            path_info = get_cloud_path_info(staging_root)
            if path_info["provider"] == "s3":
                self.s3_bucket = path_info["bucket"]
                self.s3_prefix = path_info["path"].strip("/") if path_info["path"] else "benchbox-data"
                self.logger.info(f"Using staging location from config: s3://{self.s3_bucket}/{self.s3_prefix}")
            else:
                raise ValueError(f"Redshift requires S3 (s3://) staging location, got: {path_info['provider']}://")
        else:
            self.s3_bucket = config.get("s3_bucket")
            self.s3_prefix = config.get("s3_prefix") or "benchbox-data"

        self.iam_role = config.get("iam_role")
        self.aws_access_key_id = config.get("aws_access_key_id")
        self.aws_secret_access_key = config.get("aws_secret_access_key")
        self.aws_session_token = config.get("aws_session_token")
        self.aws_region = config.get("aws_region") or "us-east-1"

        self.workload_management_config = config.get("wlm_config")
        compupdate_raw = config.get("compupdate") or "PRESET"
        self.compupdate = compupdate_raw.upper()
        valid_compupdate_values = {"ON", "OFF", "PRESET"}
        if self.compupdate not in valid_compupdate_values:
            raise ValueError(
                f"Invalid COMPUPDATE value: '{compupdate_raw}'. "
                f"Must be one of: {', '.join(sorted(valid_compupdate_values))}"
            )

        self.auto_vacuum = config.get("auto_vacuum") if config.get("auto_vacuum") is not None else True
        self.auto_analyze = config.get("auto_analyze") if config.get("auto_analyze") is not None else True

        self.disable_result_cache = config.get("disable_result_cache", True)

        self._cache_control_receipt: dict[str, Any] | None = None

        self.strict_validation = config.get("strict_validation", True)

        self.deployment_type = self._detect_deployment_type(self.host or "")
        self.workgroup_name = compute.get("workgroup_name")
        if self.workgroup_name is None and self.deployment_type == "serverless" and self.cluster_identifier is None:
            self.workgroup_name = self._extract_identifier_from_hostname(self.host or "", self.deployment_type)

        if not all([self.host, self.username, self.password]):
            missing = []
            if not self.host:
                missing.append("host (or REDSHIFT_HOST)")
            if not self.username:
                missing.append("username (or REDSHIFT_USER)")
            if not self.password:
                missing.append("password (or REDSHIFT_PASSWORD)")

            raise ConfigurationError(
                f"Redshift configuration is incomplete. Missing: {', '.join(missing)}\n"
                "Configure with one of:\n"
                "  1. CLI: benchbox setup --platform redshift\n"
                "  2. Environment variables: REDSHIFT_HOST, REDSHIFT_USER, REDSHIFT_PASSWORD"
            )

    @property
    def platform_name(self) -> str:
        return "Redshift"

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[TuningColumn]) -> str | list[str] | None:
        mode, method = self.resolve_sorted_ingestion_strategy()
        if mode == "off":
            return None

        if method == "vacuum_sort":
            return f"VACUUM SORT ONLY {self.schema}.{table_name}"

        if method == "ctas":
            return None

        raise ValueError(f"Sorted ingestion method '{method}' is not supported for Redshift.")

    def _ctas_sort_unsupported_reason(self, method: str) -> str | None:
        if method != "ctas":
            return None
        return (
            "Redshift CTAS sort is disabled: CTAS plus DROP and RENAME loses DISTSTYLE, DISTKEY, column "
            "encodings and constraints, and ORDER BY on a CTAS does not declare a SORTKEY"
        )

    @staticmethod
    def add_cli_arguments(parser) -> None:

        rs_group = parser.add_argument_group("Redshift Arguments")
        rs_group.add_argument("--host", type=str, help="Redshift cluster endpoint hostname")
        rs_group.add_argument("--port", type=int, default=5439, help="Redshift cluster port")
        rs_group.add_argument("--database", type=str, default="dev", help="Database name")
        rs_group.add_argument("--username", type=str, help="Database user with required privileges")
        rs_group.add_argument("--password", type=str, help="Password for the database user")
        rs_group.add_argument("--iam-role", type=str, help="IAM role ARN for COPY operations")
        rs_group.add_argument("--s3-bucket", type=str, help="S3 bucket for data staging")
        rs_group.add_argument("--s3-prefix", type=str, default="benchbox-data", help="Prefix within the staging bucket")

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.platforms.base.config_utils import build_adapter_config

        return cls(
            **build_adapter_config(
                config,
                platform="redshift",
                resource_key=redshift_resource_key(config),
                fields=[
                    "host",
                    "port",
                    "username",
                    "password",
                    "schema",
                    "iam_role",
                    "s3_bucket",
                    "s3_prefix",
                    "staging_root",
                    "aws_access_key_id",
                    "aws_secret_access_key",
                    "aws_session_token",
                    "aws_region",
                    "cluster_identifier",
                    "compute_resource",
                    "workgroup_name",
                    "admin_database",
                    "connect_timeout",
                    "statement_timeout",
                    "sslmode",
                    "ssl_enabled",
                    "ssl_insecure",
                    "sslrootcert",
                    "wlm_query_slot_count",
                    "wlm_query_queue_name",
                    "wlm_config",
                    "compupdate",
                    "auto_vacuum",
                    "auto_analyze",
                    "disable_result_cache",
                    "strict_validation",
                ],
            )
        )

    def _detect_client_library_version(self) -> str | None:
        if redshift_connector:
            return getattr(redshift_connector, "__version__", None)
        try:
            import psycopg

            return getattr(psycopg, "__version__", None)
        except ImportError:
            return None

    def _apply_serverless_metadata(
        self, platform_info: dict[str, Any], metadata: dict[str, Any], identifier: str | None
    ) -> None:
        config = platform_info["configuration"]
        config["workgroup_name"] = metadata.get("workgroup_name", identifier)
        config["namespace_name"] = metadata.get("namespace_name")
        config["base_capacity_rpu"] = metadata.get("base_capacity_rpu")
        config["max_capacity_rpu"] = metadata.get("max_capacity_rpu")
        config["current_rpu_capacity"] = metadata.get("current_rpu_capacity")
        config["status"] = metadata.get("status")
        config["enhanced_vpc_routing"] = metadata.get("enhanced_vpc_routing")
        config["encrypted"] = metadata.get("encrypted")

    def _apply_provisioned_metadata(
        self, platform_info: dict[str, Any], metadata: dict[str, Any], identifier: str | None
    ) -> None:
        config = platform_info["configuration"]
        config["cluster_identifier"] = metadata.get("cluster_identifier", identifier)
        config["node_type"] = metadata.get("node_type")
        config["number_of_nodes"] = metadata.get("number_of_nodes")
        config["total_storage_capacity_mb"] = metadata.get("total_storage_capacity_mb")
        config["cluster_status"] = metadata.get("cluster_status")
        config["cluster_version"] = metadata.get("cluster_version")
        config["enhanced_vpc_routing"] = metadata.get("enhanced_vpc_routing")
        config["encrypted"] = metadata.get("encrypted")

        platform_info["compute_configuration"] = {
            "node_type": metadata.get("node_type"),
            "cluster_version": metadata.get("cluster_version"),
            "num_compute_nodes": metadata.get("number_of_nodes"),
        }

    def _collect_deployment_metadata(
        self, cursor: Any, deployment_type: str, identifier: str | None, region: str | None
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if deployment_type == "serverless":
            api_fn = self._get_serverless_metadata_api
            sql_fn = self._get_serverless_metadata_sql
            log_label = "Serverless"
        elif deployment_type == "provisioned":
            api_fn = self._get_provisioned_metadata_api
            sql_fn = self._get_provisioned_metadata_sql
            log_label = "Provisioned"
        else:
            return metadata

        if identifier and region:
            api_metadata = api_fn(identifier, region)
            if api_metadata:
                metadata.update(api_metadata)
                self.logger.debug(f"Using {log_label} metadata from AWS API")

        if not metadata:
            sql_metadata = sql_fn(cursor)
            if sql_metadata:
                metadata.update(sql_metadata)
                self.logger.debug(f"Using {log_label} metadata from SQL queries")

        return metadata

    def _collect_wlm_configuration(self, cursor: Any, platform_info: dict[str, Any]) -> None:
        try:
            cursor.execute("""
                SELECT
                    service_class,
                    num_query_tasks,
                    query_working_mem,
                    max_execution_time,
                    user_group_wild_card,
                    query_group_wild_card
                FROM stv_wlm_service_class_config
                WHERE service_class >= 6
                ORDER BY service_class
                LIMIT 5
            """)
            wlm_results = cursor.fetchall()
        except Exception as e:
            self.logger.debug(f"Could not query Redshift WLM configuration: {e}")
            return

        if not wlm_results:
            return

        platform_info.setdefault("compute_configuration", {})
        platform_info["compute_configuration"]["wlm_queues"] = [
            {
                "service_class": row[0] if len(row) > 0 else None,
                "num_query_tasks": row[1] if len(row) > 1 else None,
                "query_working_mem_mb": row[2] if len(row) > 2 else None,
                "max_execution_time_ms": row[3] if len(row) > 3 else None,
            }
            for row in wlm_results
        ]
        self.logger.debug("Successfully captured Redshift WLM configuration")

    def _enrich_platform_info_from_connection(
        self,
        connection: Any,
        platform_info: dict[str, Any],
        deployment_type: str,
        identifier: str | None,
        region: str | None,
    ) -> None:
        cursor = None
        try:
            cursor = connection.cursor()

            cursor.execute("SELECT version()")
            result = cursor.fetchone()
            platform_info["platform_version"] = result[0] if result else None
            platform_info["engine_version"] = platform_info["platform_version"]
            platform_info["engine_version_source"] = "sql_query"

            metadata = self._collect_deployment_metadata(cursor, deployment_type, identifier, region)
            if metadata and deployment_type == "serverless":
                self._apply_serverless_metadata(platform_info, metadata, identifier)
            elif metadata and deployment_type == "provisioned":
                self._apply_provisioned_metadata(platform_info, metadata, identifier)

            self._collect_wlm_configuration(cursor, platform_info)
        except Exception as e:
            self.logger.debug(f"Error collecting Redshift platform info: {e}")
            if platform_info.get("platform_version") is None:
                platform_info["platform_version"] = None
        finally:
            if cursor:
                cursor.close()

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        deployment_type = self.deployment_type
        region = self._extract_region_from_hostname(self.host, deployment_type) or self.aws_region
        identifier = self._extract_identifier_from_hostname(self.host, deployment_type)

        platform_info: dict[str, Any] = {
            "platform_type": "redshift",
            "platform_name": "Redshift",
            "connection_mode": "remote",
            "cloud_provider": "AWS",
            "host": self.host,
            "port": self.port,
            "configuration": {
                "database": self.database,
                "schema": self.schema,
                "region": region,
                "s3_bucket": self.s3_bucket,
                "s3_prefix": self.s3_prefix,
                "staging_root": self.staging_root,
                "iam_role": self.iam_role,
                "iam_role_configured": bool(self.iam_role),
                "cluster_identifier": self.cluster_identifier
                or (identifier if deployment_type != "serverless" else None),
                "workgroup_name": self.workgroup_name,
                "compupdate": getattr(self, "compupdate", None),
                "result_cache_enabled": not self.disable_result_cache,
                "wlm_query_slot_count": self.wlm_query_slot_count,
                "wlm_query_queue_name": self.wlm_query_queue_name,
                "deployment_type": deployment_type,
            },
            "client_library_version": self._detect_client_library_version(),
        }

        if connection:
            self._enrich_platform_info_from_connection(connection, platform_info, deployment_type, identifier, region)
        else:
            platform_info["platform_version"] = None

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

        metadata["platform_deployment"] = self._redshift_deployment_metadata(config)
        metadata["platform_cloud"] = self._redshift_cloud_metadata(config)
        metadata["platform_compute"] = self._redshift_compute_metadata(
            config, compute, cache_control=getattr(self, "_cache_control_receipt", None)
        )
        metadata["platform_storage"] = self._redshift_storage_metadata(config)
        return metadata

    @staticmethod
    def _redshift_service_model(config: Mapping[str, Any]) -> str:
        deployment_type = str(config.get("deployment_type") or "unknown").lower()
        if deployment_type in {"serverless", "provisioned"}:
            return deployment_type
        return "unknown"

    @classmethod
    def _redshift_deployment_metadata(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        service_model = cls._redshift_service_model(config)
        observed = any(
            config.get(key) is not None
            for key in (
                "workgroup_name",
                "namespace_name",
                "base_capacity_rpu",
                "current_rpu_capacity",
                "node_type",
                "number_of_nodes",
                "total_storage_capacity_mb",
            )
        )
        return _compact_metadata(
            {
                "deployment_type": "serverless" if service_model == "serverless" else "managed_cloud",
                "service_model": service_model,
                "connection_mode": "remote",
                "endpoint_class": "cloud_endpoint",
                "metadata_source": "observed" if observed else "inferred",
                "collection_status": "available" if observed else "partial",
                "database": config.get("database"),
                "schema": config.get("schema"),
                "workgroup": config.get("workgroup_name"),
                "namespace": config.get("namespace_name"),
                "cluster_identifier": config.get("cluster_identifier"),
            }
        )

    @staticmethod
    def _redshift_cloud_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        region = config.get("region")
        return _compact_metadata(
            {
                "provider": "aws",
                "region": region,
                "source": "inferred" if region else "unavailable",
                "collection_status": "partial" if region else "unavailable",
            }
        )

    @classmethod
    def _redshift_compute_metadata(
        cls,
        config: Mapping[str, Any],
        compute: Mapping[str, Any],
        cache_control: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        service_model = cls._redshift_service_model(config)
        rpu = config.get("base_capacity_rpu") or config.get("current_rpu_capacity")
        node_type = config.get("node_type") or compute.get("node_type")
        node_count = config.get("number_of_nodes") or compute.get("num_compute_nodes")
        observed = any(
            value is not None
            for value in (
                rpu,
                config.get("max_capacity_rpu"),
                config.get("current_rpu_capacity"),
                node_type,
                node_count,
                config.get("total_storage_capacity_mb"),
                compute.get("wlm_queues"),
            )
        )
        collection_status = "available" if observed else "partial"
        if service_model == "unknown" and not observed:
            collection_status = "unavailable"
        from benchbox.core.compute_resource import resolve_resource_kind

        canonical_resource = (
            config.get("compute_resource") or config.get("workgroup_name") or config.get("cluster_identifier")
        )
        return _compact_metadata(
            {
                "service_model": service_model,
                "resource": canonical_resource,
                "resource_kind": resolve_resource_kind("redshift", config),
                "workgroup": config.get("workgroup_name"),
                "namespace": config.get("namespace_name"),
                "cluster_id": config.get("cluster_identifier"),
                "cluster_name": config.get("cluster_identifier"),
                "cluster_status": config.get("cluster_status") or config.get("status"),
                "cluster_version": config.get("cluster_version") or compute.get("cluster_version"),
                "rpu": rpu,
                "base_capacity_rpu": config.get("base_capacity_rpu"),
                "current_rpu_capacity": config.get("current_rpu_capacity"),
                "max_capacity_rpu": config.get("max_capacity_rpu"),
                "node_type": node_type,
                "node_count": node_count,
                "enhanced_vpc_routing": config.get("enhanced_vpc_routing"),
                "encrypted": config.get("encrypted"),
                "result_cache_enabled": config.get("result_cache_enabled"),
                "cache_control": dict(cache_control) if isinstance(cache_control, Mapping) else None,
                "compupdate": config.get("compupdate"),
                "wlm_query_slot_count": config.get("wlm_query_slot_count"),
                "wlm_query_queue_name": config.get("wlm_query_queue_name"),
                "wlm_queues": compute.get("wlm_queues"),
                "source": "observed" if observed else "inferred",
                "collection_status": collection_status,
            }
        )

    @staticmethod
    def _redshift_storage_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        bucket = config.get("s3_bucket")
        prefix = config.get("s3_prefix") if bucket or config.get("staging_root") else None
        staging_location = config.get("staging_root")
        if not staging_location and bucket:
            staging_location = f"s3://{bucket}/{prefix or ''}".rstrip("/")
        storage_capacity_mb = config.get("total_storage_capacity_mb")
        iam_role_configured = config.get("iam_role_configured")
        has_storage_metadata = bool(staging_location or bucket or storage_capacity_mb or iam_role_configured)
        return _compact_metadata(
            {
                "staging_location": staging_location,
                "bucket": bucket,
                "prefix": prefix,
                "storage_capacity_mb": storage_capacity_mb,
                "iam_role_configured": iam_role_configured,
                "source": "requested",
                "collection_status": "partial" if has_storage_metadata else "unavailable",
            }
        )

    def get_target_dialect(self) -> str:
        return "redshift"

    def _detect_deployment_type(self, hostname: str) -> str:
        return redshift_deployment_type(hostname)

    def _extract_region_from_hostname(self, hostname: str, deployment_type: str) -> str | None:
        if not hostname:
            return None

        parts = hostname.split(".")
        if deployment_type == "serverless":
            return parts[2] if len(parts) > 2 else None
        elif deployment_type == "provisioned":
            return parts[1] if len(parts) > 1 else None
        return None

    def _extract_identifier_from_hostname(self, hostname: str, deployment_type: str) -> str | None:
        if not hostname:
            return None

        parts = hostname.split(".")
        if deployment_type == "serverless" or deployment_type == "provisioned":
            return parts[0] if len(parts) > 0 else None
        return None

    def _get_serverless_metadata_sql(self, cursor: Any) -> dict[str, Any]:
        metadata = {}

        try:
            cursor.execute("""
                SELECT compute_capacity
                FROM sys_serverless_usage
                ORDER BY start_time DESC
                LIMIT 1
            """)
            result = cursor.fetchone()

            if result:
                metadata["current_rpu_capacity"] = result[0] if result[0] is not None else None
                self.logger.debug("Detected Redshift Serverless via sys_serverless_usage table")
        except Exception as e:
            self.logger.debug(f"sys_serverless_usage query failed (not serverless or no permissions): {e}")

        return metadata

    def _get_provisioned_metadata_sql(self, cursor: Any) -> dict[str, Any]:
        metadata = {}

        try:
            cursor.execute("""
                SELECT node_type, cluster_version, COUNT(*) as num_nodes
                FROM stv_cluster_configuration
                GROUP BY node_type, cluster_version
            """)
            result = cursor.fetchone()

            if result:
                metadata["node_type"] = result[0] if len(result) > 0 else None
                metadata["cluster_version"] = result[1] if len(result) > 1 else None
                metadata["number_of_nodes"] = result[2] if len(result) > 2 else None
                self.logger.debug(
                    f"Detected Redshift Provisioned: {metadata['node_type']} x{metadata['number_of_nodes']}"
                )
        except Exception as e:
            self.logger.debug(f"stv_cluster_configuration query failed (not provisioned or no permissions): {e}")

        return metadata

    def _get_serverless_metadata_api(self, workgroup_name: str, region: str) -> dict[str, Any]:
        if not boto3:
            self.logger.debug("boto3 not available - skipping Serverless API metadata")
            return {}

        metadata = {}

        try:
            client = boto3.client("redshift-serverless", region_name=region)

            response = client.get_workgroup(workgroupName=workgroup_name)
            workgroup = response.get("workgroup", {})

            metadata["workgroup_name"] = workgroup.get("workgroupName")
            metadata["base_capacity_rpu"] = workgroup.get("baseCapacity")
            metadata["max_capacity_rpu"] = workgroup.get("maxCapacity")
            metadata["namespace_name"] = workgroup.get("namespaceName")
            metadata["enhanced_vpc_routing"] = workgroup.get("enhancedVpcRouting", False)
            metadata["status"] = workgroup.get("status")

            if metadata.get("namespace_name"):
                try:
                    namespace_response = client.get_namespace(namespaceName=metadata["namespace_name"])
                    namespace = namespace_response.get("namespace", {})
                    metadata["kms_key_id"] = namespace.get("kmsKeyId")
                    metadata["encrypted"] = True
                except Exception as e:
                    self.logger.debug(f"Could not fetch namespace details: {e}")

            self.logger.debug(f"Retrieved Serverless metadata via API: {metadata['base_capacity_rpu']} RPUs")

        except NoCredentialsError:
            self.logger.debug("No AWS credentials found - skipping Serverless API metadata")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            self.logger.debug(f"AWS API error querying Serverless metadata: {error_code} - {e}")
        except Exception as e:
            self.logger.debug(f"Failed to query Serverless API metadata: {e}")

        return metadata

    def _get_provisioned_metadata_api(self, cluster_identifier: str, region: str) -> dict[str, Any]:
        if not boto3:
            self.logger.debug("boto3 not available - skipping Provisioned API metadata")
            return {}

        metadata = {}

        try:
            client = boto3.client("redshift", region_name=region)

            response = client.describe_clusters(ClusterIdentifier=cluster_identifier)
            clusters = response.get("Clusters", [])

            if clusters:
                cluster = clusters[0]

                metadata["cluster_identifier"] = cluster.get("ClusterIdentifier")
                metadata["node_type"] = cluster.get("NodeType")
                metadata["number_of_nodes"] = cluster.get("NumberOfNodes")
                metadata["cluster_status"] = cluster.get("ClusterStatus")
                metadata["encrypted"] = cluster.get("Encrypted", False)
                metadata["kms_key_id"] = cluster.get("KmsKeyId")
                metadata["enhanced_vpc_routing"] = cluster.get("EnhancedVpcRouting", False)

                total_storage_mb = cluster.get("TotalStorageCapacityInMegaBytes")
                if total_storage_mb:
                    metadata["total_storage_capacity_mb"] = total_storage_mb

                self.logger.debug(
                    f"Retrieved Provisioned metadata via API: {metadata['node_type']} x{metadata['number_of_nodes']}"
                )

        except NoCredentialsError:
            self.logger.debug("No AWS credentials found - skipping Provisioned API metadata")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            self.logger.debug(f"AWS API error querying Provisioned metadata: {error_code} - {e}")
        except Exception as e:
            self.logger.debug(f"Failed to query Provisioned API metadata: {e}")

        return metadata

    def _get_connection_params(self, **connection_config) -> dict[str, Any]:
        return {
            "host": connection_config.get("host", self.host),
            "port": connection_config.get("port", self.port),
            "database": connection_config.get("database", self.database),
            "user": connection_config.get("username", self.username),
            "password": connection_config.get("password", self.password),
            "sslmode": connection_config.get("sslmode", self.sslmode),
        }

    def _connect_with_driver(
        self,
        *,
        application_name: str,
        connect_timeout: int | None = None,
        tcp_keepalive: bool = False,
        tcp_keepalive_idle: int | None = None,
        tcp_keepalive_interval: int | None = None,
        tcp_keepalive_count: int | None = None,
        **connection_config,
    ) -> Any:
        params = self._get_connection_params(**connection_config)

        if redshift_connector:
            connect_kwargs: dict[str, Any] = {
                "host": params["host"],
                "port": params["port"],
                "database": params["database"],
                "user": params["user"],
                "password": params["password"],
                "ssl": self.ssl_enabled,
                "application_name": application_name,
            }

            if connect_timeout is not None:
                connect_kwargs["timeout"] = connect_timeout

            if self.ssl_enabled and params["sslmode"] in {"verify-ca", "verify-full"}:
                connect_kwargs["sslmode"] = params["sslmode"]

            if self.ssl_insecure:
                connect_kwargs["ssl_insecure"] = True

            if tcp_keepalive:
                connect_kwargs["tcp_keepalive"] = True
                if tcp_keepalive_idle is not None:
                    connect_kwargs["tcp_keepalive_idle"] = tcp_keepalive_idle
                if tcp_keepalive_interval is not None:
                    connect_kwargs["tcp_keepalive_interval"] = tcp_keepalive_interval
                if tcp_keepalive_count is not None:
                    connect_kwargs["tcp_keepalive_count"] = tcp_keepalive_count

            return redshift_connector.connect(**connect_kwargs)

        connect_kwargs = {
            "host": params["host"],
            "port": params["port"],
            "database": params["database"],
            "user": params["user"],
            "password": params["password"],
            "sslmode": params["sslmode"],
            "application_name": application_name,
        }

        if connect_timeout is not None:
            connect_kwargs["connect_timeout"] = connect_timeout
        if self.sslrootcert:
            connect_kwargs["sslrootcert"] = self.sslrootcert

        return psycopg.connect(**connect_kwargs)

    def _resolve_connect_timeout(self) -> int:
        if hasattr(self, "_cached_connect_timeout"):
            return self._cached_connect_timeout

        timeout = self._compute_connect_timeout()
        self._cached_connect_timeout = timeout
        return timeout

    def _compute_connect_timeout(self) -> int:
        if not boto3 or not self.host:
            return self.connect_timeout

        deployment_type = self.deployment_type
        region = self._extract_region_from_hostname(self.host, deployment_type)
        identifier = self._extract_identifier_from_hostname(self.host, deployment_type)

        if not region or not identifier:
            return self.connect_timeout

        if deployment_type == "provisioned":
            try:
                client = boto3.client("redshift", region_name=region)
                response = client.describe_clusters(ClusterIdentifier=identifier)
                clusters = response.get("Clusters", [])
                if clusters:
                    status = clusters[0].get("ClusterStatus", "unknown").lower()
                    if status == "available":
                        return self.connect_timeout
                    elif status in ("resuming", "rebooting"):
                        self.log_verbose(f"Provisioned cluster is {status} - using extended timeout (120s)")
                        return max(self.connect_timeout, 120)
                    elif status == "paused":
                        self.logger.warning(
                            f"Redshift cluster '{identifier}' is paused. "
                            "Resume it via the AWS console or: "
                            f"aws redshift resume-cluster --cluster-identifier {identifier}"
                        )
                        return max(self.connect_timeout, 120)
            except Exception as e:
                self.logger.debug(f"Could not check provisioned cluster status: {e}")

        elif deployment_type == "serverless":
            self.logger.debug("Serverless cluster: using extended timeout (60s) to cover potential cold-start")
            return max(self.connect_timeout, 60)

        return self.connect_timeout

    def _long_running_timeout(self, floor: int = 300) -> int:
        return max(self._resolve_connect_timeout(), floor)

    def _create_admin_connection(self, **connection_config) -> Any:
        connect_timeout = connection_config.pop("connect_timeout", self._resolve_connect_timeout())
        admin_config = connection_config.copy()
        admin_config["database"] = self.admin_database

        return self._connect_with_driver(
            application_name="BenchBox-Admin",
            connect_timeout=connect_timeout,
            **admin_config,
        )

    def _create_direct_connection(self, **connection_config) -> Any:
        connect_timeout = connection_config.pop("connect_timeout", self._resolve_connect_timeout())
        connection = self._connect_with_driver(
            application_name="BenchBox-Validation",
            connect_timeout=connect_timeout,
            **connection_config,
        )

        if self.wlm_query_queue_name:
            cursor = connection.cursor()
            try:
                queue_name_escaped = self.wlm_query_queue_name.replace("'", "''")
                cursor.execute(f"SET query_group TO '{queue_name_escaped}'")
            finally:
                cursor.close()

        return connection

    def check_server_database_exists(self, **connection_config) -> bool:
        try:
            connection = self._create_admin_connection()
            cursor = connection.cursor()

            database = connection_config.get("database", self.database)

            cursor.execute("SELECT datname FROM pg_database WHERE datname = %s", (database,))
            result = cursor.fetchone()

            return result is not None

        except Exception:
            return False
        finally:
            if "connection" in locals() and connection:
                connection.close()

    def drop_database(self, **connection_config) -> None:
        database = connection_config.get("database", self.database)

        if not self.check_server_database_exists(database=database):
            self.log_verbose(f"Database {database} does not exist - nothing to drop")
            return

        try:
            connection = self._create_admin_connection(connect_timeout=self._long_running_timeout())
            connection.autocommit = True

            cursor = connection.cursor()
            self.log_verbose(f"Terminating existing connections to {database}...")
            cursor.execute(
                """
                SELECT pg_terminate_backend(procpid)
                FROM pg_stat_activity
                WHERE datname = %s AND procpid <> pg_backend_pid()
                """,
                (database,),
            )

            try:
                cursor.execute(f'DROP DATABASE "{database}"')
            except Exception as drop_error:
                sqlstate = None
                first_arg = drop_error.args[0] if drop_error.args else None
                if isinstance(first_arg, dict):
                    sqlstate = first_arg.get("C")
                if sqlstate is None:
                    sqlstate = getattr(drop_error, "pgcode", None)
                if sqlstate is not None:
                    if sqlstate != "55006":
                        raise
                else:
                    error_msg = str(drop_error).lower()
                    if "active connection" not in error_msg and "being accessed" not in error_msg:
                        raise
                self.log_verbose("Database still has active connections after terminate, retrying...")
                cursor.close()
                try:
                    connection.close()
                except Exception:
                    pass
                connection = self._create_admin_connection(connect_timeout=self._long_running_timeout())
                connection.autocommit = True
                cursor = connection.cursor()
                cursor.execute(
                    """
                    SELECT pg_terminate_backend(procpid)
                    FROM pg_stat_activity
                    WHERE datname = %s AND procpid <> pg_backend_pid()
                    """,
                    (database,),
                )
                cursor.execute(f'DROP DATABASE "{database}"')

        except Exception as e:
            raise RuntimeError(f"Failed to drop Redshift database {database}: {e}") from e
        finally:
            if "cursor" in locals():
                try:
                    cursor.close()
                except Exception:
                    pass
            if "connection" in locals() and connection:
                try:
                    connection.close()
                except Exception:
                    pass

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("Redshift connection")

        self.handle_existing_database(**connection_config)

        params = self._get_connection_params(**connection_config)
        target_database = params.get("database")

        if not self.database_was_reused:
            database_exists = self.check_server_database_exists(database=target_database)

            if not database_exists:
                self.log_verbose(f"Creating database: {target_database}")

                try:
                    admin_conn = self._create_admin_connection()
                    admin_conn.autocommit = True
                    admin_cursor = admin_conn.cursor()

                    try:
                        admin_cursor.execute(f'CREATE DATABASE "{target_database}"')
                        self.logger.info(f"Created database {target_database}")
                    finally:
                        admin_cursor.close()
                        admin_conn.close()
                except Exception as e:
                    self.logger.error(f"Failed to create database {target_database}: {e}")
                    raise

        self.log_very_verbose(f"Redshift connection params: host={params.get('host')}, database={target_database}")

        try:
            connect_timeout = connection_config.get("connect_timeout", self._resolve_connect_timeout())
            connect_config = {k: v for k, v in connection_config.items() if k != "connect_timeout"}
            connection = self._connect_with_driver(
                application_name="BenchBox",
                connect_timeout=connect_timeout,
                tcp_keepalive=True,
                tcp_keepalive_idle=600,
                tcp_keepalive_interval=30,
                tcp_keepalive_count=3,
                **connect_config,
            )
            connection.autocommit = True

            cursor = connection.cursor()
            if self.wlm_query_slot_count > 1:
                cursor.execute(f"SET wlm_query_slot_count = {int(self.wlm_query_slot_count)}")
            if self.statement_timeout > 0:
                cursor.execute(f"SET statement_timeout = {int(self.statement_timeout)}")

            cursor.execute(f'SET search_path TO "{self.schema}"')

            cursor.execute("SELECT version()")
            cursor.fetchone()
            cursor.close()

            self.logger.info(f"Connected to Redshift cluster at {params['host']}:{params['port']}")

            self.log_operation_complete(
                "Redshift connection", details=f"Connected to {params['host']}:{params['port']}"
            )

            return connection

        except Exception as e:
            self.logger.error(f"Failed to connect to Redshift: {e}")
            raise

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()

        cursor = connection.cursor()

        try:
            if self.schema and self.schema.lower() != "public":
                self.log_verbose(f"Creating schema: {self.schema}")
                cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
                self.logger.info(f"Created schema {self.schema}")

            self.log_very_verbose(f"Setting search_path to: {self.schema}")
            cursor.execute(f'SET search_path TO "{self.schema}"')

            schema_sql = self._create_schema_with_tuning(benchmark, source_dialect="standard")

            statements = [stmt.strip() for stmt in schema_sql.split(";") if stmt.strip()]

            for statement in statements:
                statement = self._normalize_table_name_in_sql(statement)
                is_create_table = statement.upper().startswith("CREATE TABLE")

                if is_create_table:
                    table_name = self._extract_table_name(statement)
                    if table_name:
                        table_name_lower = table_name.strip('"').lower()
                        drop_statement = f"DROP TABLE IF EXISTS {table_name_lower}"
                        self.log_notice(f"Executing destructive schema reset: {drop_statement}")
                        cursor.execute(drop_statement)

                statement = self._optimize_table_definition(statement)
                cursor.execute(statement)
                self.logger.info(f"Executed schema statement: {statement[:100]}...")

            self.logger.info("Schema created")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise
        finally:
            cursor.close()

        return elapsed_seconds(start_time)

    def _resolve_data_files(self, benchmark, data_dir: Path) -> Any:
        return resolve_adapter_data_source(self, benchmark, data_dir)

    def _create_s3_client(self):
        try:
            if bool(self.aws_access_key_id) != bool(self.aws_secret_access_key):
                raise ValueError(
                    "Explicit S3 upload credentials require both aws_access_key_id and aws_secret_access_key."
                )

            session = boto3.Session(
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                aws_session_token=self.aws_session_token,
                region_name=self.aws_region,
            )
            if session.get_credentials() is None:
                raise ValueError(
                    "AWS credentials not found. Configure credentials via:\n"
                    "  1. Explicit S3 upload credentials in config\n"
                    "  2. AWS CLI (aws configure)\n"
                    "  3. Environment variables (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)"
                )

            return session.client(
                "s3",
                config=BotocoreConfig(request_checksum_calculation="when_required"),
            )
        except NoCredentialsError as e:
            raise ValueError(
                "AWS credentials not found. Configure credentials via:\n"
                "  1. IAM role (aws_access_key_id/aws_secret_access_key in config)\n"
                "  2. AWS CLI (aws configure)\n"
                "  3. Environment variables (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)"
            ) from e
        except ClientError as e:
            raise ValueError(f"Failed to create AWS S3 client: {e}") from e

    def _upload_file_to_s3(self, s3_client, file_path: Path, table_name: str, file_idx: int) -> str:
        file_stem = file_path.stem
        original_suffix = file_path.suffix
        if "." in file_stem:
            parts = file_path.name.split(".", 1)
            full_suffix = "." + parts[1] if len(parts) > 1 else original_suffix
        else:
            full_suffix = original_suffix

        s3_key = f"{self.s3_prefix}/{table_name}_{file_idx}{full_suffix}"

        try:
            s3_client.upload_file(str(file_path), self.s3_bucket, s3_key)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "NoSuchBucket":
                raise ValueError(
                    f"S3 bucket '{self.s3_bucket}' does not exist. Create the bucket or update your configuration."
                ) from e
            elif error_code == "AccessDenied":
                raise ValueError(
                    f"Access denied to S3 bucket '{self.s3_bucket}'. Check IAM permissions for s3:PutObject."
                ) from e
            else:
                raise ValueError(
                    f"Failed to upload {file_path.name} to s3://{self.s3_bucket}/{s3_key}: {error_code} - {e}"
                ) from e

        return f"s3://{self.s3_bucket}/{s3_key}"

    def _build_s3_copy_source(self, s3_client, s3_uris: list[str], table_name: str) -> tuple[str, str]:
        if len(s3_uris) > 1:
            manifest = {"entries": [{"url": uri, "mandatory": True} for uri in s3_uris]}
            manifest_key = f"{self.s3_prefix}/{table_name}_manifest.json"

            try:
                s3_client.put_object(
                    Bucket=self.s3_bucket,
                    Key=manifest_key,
                    Body=json.dumps(manifest),
                )
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "Unknown")
                raise ValueError(
                    f"Failed to upload manifest file to s3://{self.s3_bucket}/{manifest_key}: {error_code} - {e}"
                ) from e

            return f"s3://{self.s3_bucket}/{manifest_key}", "manifest"
        else:
            return s3_uris[0], ""

    @staticmethod
    def _sanitize_copy_credential(value: str, name: str) -> str:
        if "'" in value:
            raise ValueError(
                f"Credential '{name}' contains a single-quote character, which is not allowed in COPY SQL."
            )
        return value

    def _get_copy_credentials_clause(self) -> str:
        if self.iam_role:
            return f"IAM_ROLE '{self._sanitize_copy_credential(self.iam_role, 'iam_role')}'"
        elif self.aws_access_key_id and self.aws_secret_access_key:
            key_id = self._sanitize_copy_credential(self.aws_access_key_id, "aws_access_key_id")
            secret = self._sanitize_copy_credential(self.aws_secret_access_key, "aws_secret_access_key")
            if self.aws_session_token:
                token = self._sanitize_copy_credential(self.aws_session_token, "aws_session_token")
                return f"ACCESS_KEY_ID '{key_id}' SECRET_ACCESS_KEY '{secret}' SESSION_TOKEN '{token}'"
            return f"ACCESS_KEY_ID '{key_id}' SECRET_ACCESS_KEY '{secret}'"
        else:
            self.log_verbose("No explicit credentials configured for COPY; using cluster default IAM role")
            return ""

    def _load_table_via_s3(
        self,
        cursor,
        s3_client,
        table_name: str,
        valid_files: list[Path],
        connection: Any,
        data_source: Any | None = None,
        benchmark: Any | None = None,
    ) -> int:
        table_name_lower = table_name.lower()
        parquet_modes = {is_parquet_format(file_path) for file_path in valid_files}
        if len(parquet_modes) > 1:
            raise ValueError(
                f"Redshift COPY requires a uniform source format per table; received mixed parquet and delimited "
                f"files for '{table_name_lower}'."
            )
        is_parquet_copy = parquet_modes == {True}

        s3_uris = []
        for file_idx, file_path in enumerate(valid_files):
            s3_uris.append(self._upload_file_to_s3(s3_client, file_path, table_name, file_idx))

        copy_from_path, manifest_option = self._build_s3_copy_source(s3_client, s3_uris, table_name)

        compressions = {detect_compression(f) for f in valid_files}
        if "zstd" in compressions:
            compression_option = "ZSTD"
        elif "gzip" in compressions:
            compression_option = "GZIP"
        else:
            compression_option = ""

        credentials_clause = self._get_copy_credentials_clause()

        qualified_table = f"{self.schema}.{table_name_lower}"
        if is_parquet_copy:
            copy_sql = f"""
                COPY {qualified_table}
                FROM '{copy_from_path}'
                {credentials_clause}
                {manifest_option}
                FORMAT AS PARQUET
            """
        else:
            dialect_source = data_source or DataSource(source_type="redshift_s3", tables={})
            dialect = resolve_csv_dialect(
                dialect_source, table_name, valid_files[0], benchmark if benchmark is not None else NO_BENCHMARK
            )
            delimiter = dialect.delimiter
            copy_sql = f"""
                COPY {qualified_table}
                FROM '{copy_from_path}'
                {credentials_clause}
                {manifest_option}
                {compression_option}
                DELIMITER '{delimiter}'
                IGNOREHEADER 0
                COMPUPDATE {self.compupdate}
            """

        cursor.execute(copy_sql)

        effective_tuning = self.get_effective_tuning_configuration()
        if effective_tuning is not None:
            self.apply_ctas_sort(table_name_lower, effective_tuning, connection)
            self.run_post_load_tunings(table_name_lower, effective_tuning, connection)

        cursor.execute(f"SELECT COUNT(*) FROM {qualified_table}")
        row_count = cursor.fetchone()[0]

        if self.auto_analyze:
            cursor.execute(f"ANALYZE {qualified_table}")

        return row_count

    def _load_table_via_insert(
        self,
        cursor,
        table_name: str,
        valid_files: list[Path],
        connection: Any,
        data_source: Any | None = None,
        benchmark: Any | None = None,
    ) -> int:
        table_name_lower = table_name.lower()
        total_rows_loaded = 0

        for file_idx, file_path in enumerate(valid_files):
            chunk_info = f" (chunk {file_idx + 1}/{len(valid_files)})" if len(valid_files) > 1 else ""
            self.log_very_verbose(f"Loading {table_name}{chunk_info} from {file_path.name}")

            dialect_source = data_source or DataSource(source_type="redshift_insert", tables={})
            dialect = resolve_csv_dialect(
                dialect_source, table_name, file_path, benchmark if benchmark is not None else NO_BENCHMARK
            )
            delimiter = dialect.delimiter
            compression_handler = FileFormatRegistry.get_compression_handler(file_path)

            with compression_handler.open(file_path) as f:
                rows_loaded = 0
                batch_size = 1000
                batch_data = []

                for line in f:
                    line = line.strip()
                    if dialect.null_marker is not None and line and line.endswith(delimiter):
                        line = line[:-1]

                    values = line.split(delimiter)
                    if dialect.null_marker is None:
                        escaped_values = ["'" + str(v).replace("'", "''") + "'" for v in values]
                    else:
                        escaped_values = [
                            "NULL" if v == dialect.null_marker else "'" + str(v).replace("'", "''") + "'"
                            for v in values
                        ]
                    batch_data.append(f"({', '.join(escaped_values)})")

                    if len(batch_data) >= batch_size:
                        insert_sql = f"INSERT INTO {table_name_lower} VALUES " + ", ".join(batch_data)
                        cursor.execute(insert_sql)
                        rows_loaded += len(batch_data)
                        total_rows_loaded += len(batch_data)
                        batch_data = []

                if batch_data:
                    insert_sql = f"INSERT INTO {table_name_lower} VALUES " + ", ".join(batch_data)
                    cursor.execute(insert_sql)
                    rows_loaded += len(batch_data)
                    total_rows_loaded += len(batch_data)

            self.log_very_verbose(f"Loaded {rows_loaded:,} rows from {file_path.name}")

        effective_tuning = self.get_effective_tuning_configuration()
        if effective_tuning is not None:
            self.apply_ctas_sort(table_name_lower, effective_tuning, connection)
            self.run_post_load_tunings(table_name_lower, effective_tuning, connection)

        return total_rows_loaded

    def _filter_valid_files(self, file_paths) -> list[Path]:
        if not isinstance(file_paths, list):
            file_paths = [file_paths]
        valid_files = []
        for file_path in file_paths:
            file_path = Path(file_path)
            if file_path.exists() and (file_path.is_dir() or file_path.stat().st_size > 0):
                valid_files.append(file_path)
        return valid_files

    def validate_external_table_requirements(self) -> None:
        if not self.s3_bucket:
            raise ValueError(
                "Redshift external mode requires S3 staging (set --platform-option s3_bucket=<bucket> "
                "or provide --platform-option staging_root=s3://bucket/path)."
            )
        if not self.iam_role:
            raise ValueError(
                "Redshift external mode requires IAM role credentials for Spectrum "
                "(set --platform-option iam_role=<arn>)."
            )

    @staticmethod
    def _map_external_column_type(column_type: str) -> str:
        normalized = str(column_type).strip().upper()
        if not normalized:
            return "VARCHAR(65535)"
        if normalized.startswith(("DECIMAL", "NUMERIC", "VARCHAR", "CHAR", "TIMESTAMP")):
            return normalized
        if "BIGINT" in normalized:
            return "BIGINT"
        if "SMALLINT" in normalized:
            return "SMALLINT"
        if "INT" in normalized:
            return "INTEGER"
        if "DOUBLE" in normalized:
            return "DOUBLE PRECISION"
        if "REAL" in normalized or "FLOAT" in normalized:
            return "REAL"
        if "DATE" in normalized:
            return "DATE"
        if "BOOLEAN" in normalized or normalized == "BOOL":
            return "BOOLEAN"
        return "VARCHAR(65535)"

    def _build_external_column_list(self, benchmark: Any, table_name: str) -> list[tuple[str, str]]:
        if not hasattr(benchmark, "get_schema"):
            raise ValueError(
                f"Benchmark schema metadata unavailable for '{table_name}'. "
                "Redshift external mode requires benchmark.get_schema()."
            )

        schema = benchmark.get_schema() or {}
        table_schema = schema.get(table_name) or schema.get(table_name.lower()) or schema.get(table_name.upper())
        if not table_schema:
            raise ValueError(f"No schema definition found for table '{table_name}'.")

        columns = table_schema.get("columns", [])
        if not columns:
            raise ValueError(f"No columns found in schema definition for table '{table_name}'.")

        column_list = []
        for column in columns:
            column_name = str(column.get("name", "")).strip().lower()
            if not column_name:
                continue
            mapped_type = self._map_external_column_type(str(column.get("type", "")))
            column_list.append((column_name, mapped_type))

        if not column_list:
            raise ValueError(f"No valid column definitions were generated for table '{table_name}'.")

        return column_list

    def _build_external_column_definitions(self, benchmark: Any, table_name: str) -> str:
        return ", ".join(
            f"{name} {column_type}" for name, column_type in self._build_external_column_list(benchmark, table_name)
        )

    @staticmethod
    def _map_external_column_type_to_glue(spectrum_type: str) -> str:
        upper = spectrum_type.strip().upper()
        if upper.startswith("NUMERIC"):
            return "DECIMAL" + upper[len("NUMERIC") :]
        if upper.startswith("TIMESTAMP"):
            return "TIMESTAMP"
        aliases = {"INTEGER": "INT", "DOUBLE PRECISION": "DOUBLE", "REAL": "FLOAT"}
        return aliases.get(upper, spectrum_type)

    def _create_glue_client(self):
        if not boto3:
            raise ValueError("boto3 is required for Glue catalog registration (Redshift Iceberg external mode).")
        if bool(self.aws_access_key_id) != bool(self.aws_secret_access_key):
            raise ValueError("Explicit Glue credentials require both aws_access_key_id and aws_secret_access_key.")
        session = boto3.Session(
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            aws_session_token=self.aws_session_token,
            region_name=self.aws_region,
        )
        if session.get_credentials() is None:
            raise ValueError(
                "AWS credentials not found. Configure credentials via explicit config, AWS CLI, or environment."
            )
        return session.client("glue")

    def _register_iceberg_table_in_glue(
        self,
        glue_client: Any,
        database: str,
        table_name: str,
        location: str,
        columns: list[tuple[str, str]],
        metadata_location: str | None = None,
    ) -> None:
        try:
            glue_client.delete_table(DatabaseName=database, Name=table_name)
        except Exception as e:
            response = getattr(e, "response", None)
            error_code = response.get("Error", {}).get("Code", "") if isinstance(response, dict) else ""
            if error_code != "EntityNotFoundException":
                raise
        parameters = {"table_type": "iceberg", "classification": "iceberg"}
        if metadata_location:
            parameters["metadata_location"] = metadata_location
        glue_client.create_table(
            DatabaseName=database,
            TableInput={
                "Name": table_name,
                "TableType": "EXTERNAL_TABLE",
                "Parameters": parameters,
                "StorageDescriptor": {
                    "Location": location,
                    "InputFormat": "org.apache.iceberg.mr.hive.HiveIcebergInputFormat",
                    "OutputFormat": "org.apache.iceberg.mr.hive.HiveIcebergOutputFormat",
                    "SerdeInfo": {"SerializationLibrary": "org.apache.iceberg.mr.hive.HiveIcebergSerDe"},
                    "Columns": [
                        {"Name": name, "Type": self._map_external_column_type_to_glue(column_type)}
                        for name, column_type in columns
                    ],
                },
            },
        )

    def _upload_external_parquet_files_to_s3(self, s3_client: Any, table_name: str, parquet_files: list[Path]) -> str:
        table_name_lower = table_name.lower()
        s3_prefix = f"{self.s3_prefix}/{self.database.lower()}_external/{table_name_lower}"
        for file_path in parquet_files:
            s3_key = f"{s3_prefix}/{file_path.name}"
            s3_client.upload_file(str(file_path), self.s3_bucket, s3_key)
        return f"s3://{self.s3_bucket}/{s3_prefix}/"

    def _external_s3_table_uri(self, table_name: str) -> tuple[str, str]:
        s3_prefix = f"{self.s3_prefix}/{self.database.lower()}_external/{table_name.lower()}"
        return self.s3_bucket, s3_prefix

    def _upload_local_file_to_s3(self, s3_client: Any, local_path: Path, bucket: str, key: str) -> None:
        s3_client.upload_file(str(local_path), bucket, key)

    def _upload_external_directory_to_s3(self, s3_client: Any, table_name: str, directory: Path) -> str:
        s3_bucket, s3_prefix = self._external_s3_table_uri(table_name)
        for file_path in directory.rglob("*"):
            if not file_path.is_file():
                continue
            relative = file_path.relative_to(directory).as_posix()
            self._upload_local_file_to_s3(s3_client, file_path, s3_bucket, f"{s3_prefix}/{relative}")
        return f"s3://{s3_bucket}/{s3_prefix}/"

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        self.validate_external_table_requirements()
        assert self.iam_role is not None

        start_time = mono_time()
        table_stats: dict[str, int] = {}
        if self.schema.endswith("_external"):
            self.logger.warning(
                f"Schema '{self.schema}' already ends with '_external'. "
                "The derived external schema will be '%s_external'.",
                self.schema,
            )
        external_schema = f"{self.schema}_external"
        escaped_iam_role = self.iam_role.replace("'", "''")
        escaped_db = self.database.lower().replace("'", "''")
        cursor = connection.cursor()

        try:
            data_source = self._resolve_data_files(benchmark, data_dir)
            if not isinstance(data_source, DataSource):
                data_source = DataSource(source_type="legacy_test_mapping", tables=data_source)
            s3_client = self._create_s3_client()
            glue_client: Any = None

            cursor.execute(
                f"""
                CREATE EXTERNAL SCHEMA IF NOT EXISTS {external_schema}
                FROM DATA CATALOG
                DATABASE '{escaped_db}_external'
                IAM_ROLE '{escaped_iam_role}'
                CREATE EXTERNAL DATABASE IF NOT EXISTS
                """
            )

            for table_name, file_paths in data_source.tables.items():
                table_name_lower = table_name.lower()
                valid_files = self._filter_valid_files(file_paths)
                delta_dirs = [path for path in valid_files if path.is_dir() and (path / "_delta_log").is_dir()]
                iceberg_dirs = [path for path in valid_files if is_iceberg_directory(path)]
                parquet_files = [path for path in valid_files if path.suffix.lower() == ".parquet"]
                if delta_dirs:
                    location = self._upload_external_directory_to_s3(s3_client, table_name_lower, delta_dirs[0])
                    source_format = "delta"
                elif iceberg_dirs:
                    location = ""
                    source_format = "iceberg"
                elif parquet_files:
                    location = self._upload_external_parquet_files_to_s3(s3_client, table_name_lower, parquet_files)
                    source_format = "parquet"
                else:
                    raise ValueError(
                        f"Redshift external mode requires Parquet files, Delta directories, or Iceberg "
                        f"directories for table '{table_name_lower}'. No supported sources were found."
                    )

                if source_format == "iceberg":
                    if glue_client is None:
                        glue_client = self._create_glue_client()
                    glue_database = f"{self.database.lower()}_external"
                    s3_bucket, s3_prefix = self._external_s3_table_uri(table_name_lower)
                    dest_uri = f"s3://{s3_bucket}/{s3_prefix}"
                    with tempfile.TemporaryDirectory(prefix="benchbox-iceberg-reloc-") as staging:
                        relocated = relocate_iceberg_table(iceberg_dirs[0], dest_uri, staging)
                        for rel in relocated.data_files:
                            self._upload_local_file_to_s3(
                                s3_client, iceberg_dirs[0] / rel, s3_bucket, f"{s3_prefix}/{rel}"
                            )
                        for rel, staged in relocated.graph_files.items():
                            self._upload_local_file_to_s3(s3_client, staged, s3_bucket, f"{s3_prefix}/{rel}")
                    column_list = self._build_external_column_list(benchmark, table_name_lower)
                    self.log_notice(
                        f"Registering Redshift Iceberg table in Glue catalog: {glue_database}.{table_name_lower}"
                    )
                    self._register_iceberg_table_in_glue(
                        glue_client,
                        glue_database,
                        table_name_lower,
                        dest_uri + "/",
                        column_list,
                        relocated.metadata_location,
                    )
                    cursor.execute(f"SELECT COUNT(*) FROM {external_schema}.{table_name_lower}")
                    result = cursor.fetchone()
                    table_stats[table_name_lower] = int(result[0]) if result else 0
                    continue

                column_defs = self._build_external_column_definitions(benchmark, table_name_lower)

                self.log_notice(
                    f"Dropping existing Redshift external table before recreate: {external_schema}.{table_name_lower}"
                )
                cursor.execute(f"DROP TABLE IF EXISTS {external_schema}.{table_name_lower}")
                if source_format == "delta":
                    table_properties = "\n                        TABLE PROPERTIES ('table_type'='DELTA')"
                else:
                    table_properties = ""
                escaped_location = location.replace("'", "''")
                cursor.execute(
                    f"""
                    CREATE EXTERNAL TABLE {external_schema}.{table_name_lower}
                    ({column_defs})
                    STORED AS PARQUET
                    LOCATION '{escaped_location}'{table_properties}
                    """
                )
                cursor.execute(f"SELECT COUNT(*) FROM {external_schema}.{table_name_lower}")
                result = cursor.fetchone()
                table_stats[table_name_lower] = int(result[0]) if result else 0

        finally:
            cursor.close()

        total_time = elapsed_seconds(start_time)
        return table_stats, total_time, None

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        phase_start = mono_time()
        cursor = connection.cursor()

        try:
            data_source = self._resolve_data_files(benchmark, data_dir)
            if not isinstance(data_source, DataSource):
                data_source = DataSource(source_type="legacy_test_mapping", tables=data_source)

            if self.s3_bucket and boto3:
                s3_client = self._create_s3_client()

                def load_via_s3(table_name: str, valid_files: list[Path]) -> int:
                    return self._load_table_via_s3(
                        cursor, s3_client, table_name, valid_files, connection, data_source, benchmark
                    )

                table_stats, total_time, _ = run_staged_table_loads(
                    self,
                    tables=data_source.tables,
                    stat_key=str.lower,
                    filter_files=self._filter_valid_files,
                    load_one=load_via_s3,
                    record_timings=False,
                    fail_fast=False,
                    phase_start=phase_start,
                )

            else:
                self.logger.warning("No S3 bucket configured, using direct INSERT loading")

                def load_via_insert(table_name: str, valid_files: list[Path]) -> int:
                    return self._load_table_via_insert(
                        cursor, table_name, valid_files, connection, data_source, benchmark
                    )

                table_stats, total_time, _ = run_staged_table_loads(
                    self,
                    tables=data_source.tables,
                    stat_key=str.lower,
                    filter_files=self._filter_valid_files,
                    load_one=load_via_insert,
                    record_timings=False,
                    fail_fast=False,
                    describe_start=lambda table_name, chunk_info: f"Direct loading data for table: {table_name}",
                    phase_start=phase_start,
                )

        except Exception as e:
            self.logger.error(f"Data loading failed: {e}")
            raise
        finally:
            cursor.close()

        return table_stats, total_time, None

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:

        cursor = connection.cursor()

        try:
            cache_setting = "OFF" if self.disable_result_cache else "ON"
            optimization_settings = [
                f"SET enable_result_cache_for_session TO {cache_setting}",
                "SET query_group TO 'benchbox'",
                "SET statement_timeout TO '1800000'",
            ]

            if benchmark_type.lower() in ["olap", "analytics", "tpch", "tpcds"]:
                optimization_settings.extend(
                    [
                        "SET enable_case_sensitive_identifier TO OFF",
                        "SET datestyle TO 'ISO, MDY'",
                        "SET extra_float_digits TO 0",
                    ]
                )

            critical_failures = []
            for setting in optimization_settings:
                try:
                    cursor.execute(setting)
                    self.logger.debug(f"Applied setting: {setting}")
                except Exception as e:
                    if "enable_result_cache_for_session" in setting:
                        critical_failures.append(setting)
                    self.logger.warning(f"Failed to apply setting {setting}: {e}")

            if self.disable_result_cache or critical_failures:
                self.logger.debug("Validating cache control settings...")
                validation_result = self.validate_session_cache_control(connection)
                from benchbox.platforms.cloud_shared import sanitize_cache_control_receipt

                self._cache_control_receipt = sanitize_cache_control_receipt(validation_result)

                if not validation_result["validated"]:
                    self.logger.warning(f"Cache control validation failed: {validation_result.get('errors', [])}")
                else:
                    self.logger.info(
                        f"Cache control validated successfully: cache_disabled={validation_result['cache_disabled']}"
                    )
            else:
                from benchbox.platforms.cloud_shared import (
                    explicit_cache_enabled_receipt,
                    sanitize_cache_control_receipt,
                )

                self._cache_control_receipt = sanitize_cache_control_receipt(
                    explicit_cache_enabled_receipt("enable_result_cache_for_session", "ON")
                )

            if self.auto_vacuum or self.auto_analyze:
                self._run_vacuum_analyze_isolated(connection)

        finally:
            cursor.close()

    def _run_vacuum_analyze_isolated(self, main_connection: Any) -> None:
        main_cursor = main_connection.cursor()
        try:
            main_cursor.execute(f"""
                SELECT schemaname, tablename
                FROM pg_tables
                WHERE schemaname = '{self.schema}'
            """)
            tables = main_cursor.fetchall()
        finally:
            main_cursor.close()

        if not tables:
            return

        maint_conn = None
        try:
            maint_conn = self._connect_with_driver(
                application_name="BenchBox-Maintenance",
                connect_timeout=self._long_running_timeout(),
            )
            maint_conn.autocommit = True
            maint_cursor = maint_conn.cursor()
            maint_cursor.execute(f'SET search_path TO "{self.schema}"')

            for _schema, table in tables:
                ops = []
                if self.auto_vacuum:
                    ops.append(("VACUUM", f'VACUUM "{table}"'))
                if self.auto_analyze:
                    ops.append(("ANALYZE", f'ANALYZE "{table}"'))

                for op_name, sql in ops:
                    try:
                        maint_cursor.execute(sql)
                    except Exception as e:
                        self.logger.warning(f"{op_name} failed for {table}: {e}")
                        self.logger.warning(
                            f"Maintenance connection lost during {op_name} - skipping remaining VACUUM/ANALYZE"
                        )
                        return

        except Exception as e:
            self.logger.warning(f"Could not create maintenance connection for VACUUM/ANALYZE: {e}")
        finally:
            if maint_conn is not None:
                try:
                    maint_conn.close()
                except Exception:
                    pass

    def validate_session_cache_control(self, connection: Any) -> dict[str, Any]:
        from benchbox.platforms.cloud_shared import validate_session_cache_control

        return validate_session_cache_control(
            connection=connection,
            query="SELECT current_setting('enable_result_cache_for_session') as value",
            setting_key="enable_result_cache_for_session",
            disabled_value="off",
            enabled_value="on",
            normalize="lower",
            platform_name="Redshift",
            disable_result_cache=self.disable_result_cache,
            strict_validation=self.strict_validation,
            adapter_logger=self.logger,
        )

    def _build_query_stats(
        self,
        execution_time: float,
        *,
        connection: Any = None,
        query_id: str | None = None,
    ) -> dict[str, Any]:
        stats = {"execution_time_seconds": execution_time}
        if connection is None:
            return stats
        try:
            redshift_stats = self._get_query_statistics(connection, query_id or "")
            redshift_stats["execution_time_seconds"] = execution_time
            return redshift_stats
        except Exception:
            return stats

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
        result = super().execute_query(
            connection,
            query,
            query_id,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )
        if result.get("status") == "FAILED":
            return result

        result["translated_query"] = None
        if not self.capture_plans:
            self.display_query_plan_if_enabled(connection, query, query_id)
        self._merge_plan_capture_into_result(result, connection, query, query_id)
        return result

    def _extract_table_name(self, statement: str) -> str | None:
        from benchbox.core.sql_utils import extract_table_name

        return extract_table_name(statement)

    def _normalize_table_name_in_sql(self, sql: str) -> str:
        return normalize_table_name_in_sql(sql)

    def _optimize_table_definition(self, statement: str) -> str:
        if not statement.upper().startswith("CREATE TABLE"):
            return statement

        if "DISTSTYLE" not in statement.upper():
            statement += " DISTSTYLE AUTO"

        if "SORTKEY" not in statement.upper():
            statement += " SORTKEY AUTO"

        return statement

    def _get_platform_metadata(self, connection: Any) -> dict[str, Any]:
        metadata = {
            "platform": self.platform_name,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "result_cache_enabled": not self.disable_result_cache,
        }

        cursor = connection.cursor()

        try:
            cursor.execute("SELECT version()")
            result = cursor.fetchone()
            metadata["redshift_version"] = result[0] if result else "unknown"

            try:
                if self.deployment_type == "serverless":
                    cursor.execute("""
                        SELECT compute_capacity
                        FROM sys_serverless_usage
                        ORDER BY start_time DESC
                        LIMIT 1
                    """)
                    result = cursor.fetchone()
                    if result:
                        metadata["cluster_info"] = {
                            "deployment_type": "serverless",
                            "compute_capacity_rpu": result[0],
                        }
                else:
                    cursor.execute("""
                        SELECT
                            node_type,
                            num_nodes,
                            cluster_version,
                            publicly_accessible
                        FROM stv_cluster_configuration
                        LIMIT 1
                    """)
                    result = cursor.fetchone()
                    if result:
                        metadata["cluster_info"] = {
                            "deployment_type": "provisioned",
                            "node_type": result[0],
                            "num_nodes": result[1],
                            "cluster_version": result[2],
                            "publicly_accessible": result[3],
                        }
            except Exception as e:
                self.logger.debug(f"Could not query cluster information: {e}")

            cursor.execute("""
                SELECT
                    current_user,
                    current_database(),
                    current_schema(),
                    inet_client_addr(),
                    inet_client_port()
            """)
            result = cursor.fetchone()
            if result:
                metadata["session_info"] = {
                    "current_user": result[0],
                    "current_database": result[1],
                    "current_schema": result[2],
                    "client_addr": result[3],
                    "client_port": result[4],
                }

            cursor.execute(f"""
                SELECT
                    schemaname,
                    tablename,
                    tableowner,
                    tablespace,
                    hasindexes,
                    hasrules,
                    hastriggers
                FROM pg_tables
                WHERE schemaname = '{self.schema}'
            """)
            tables = cursor.fetchall()
            metadata["tables"] = [
                {
                    "schema": row[0],
                    "table": row[1],
                    "owner": row[2],
                    "tablespace": row[3],
                    "has_indexes": row[4],
                    "has_rules": row[5],
                    "has_triggers": row[6],
                }
                for row in tables
            ]

        except Exception as e:
            metadata["metadata_error"] = str(e)
        finally:
            cursor.close()

        return metadata

    def _get_existing_tables(self, connection: Any) -> list[str]:
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s
                AND table_type = 'BASE TABLE'
                """,
                (self.schema,),
            )
            return [row[0].lower() for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _get_query_statistics(self, connection: Any, query_id: str) -> dict[str, Any]:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT pg_last_query_id()")
            result = cursor.fetchone()
            if not result or result[0] == -1:
                return {}

            redshift_query_id = result[0]

            if self.deployment_type == "serverless":
                cursor.execute(
                    """
                    SELECT
                        query_id,
                        DATEDIFF('microseconds', start_time, end_time) as duration_microsecs,
                        DATEDIFF('microseconds', start_time, end_time) as cpu_time_microsecs,
                        0 as bytes_scanned,
                        0 as bytes_returned,
                        1 as slots,
                        1 as wlm_slots,
                        status
                    FROM sys_query_history
                    WHERE query_id = %s
                    ORDER BY start_time DESC
                    LIMIT 1
                """,
                    (redshift_query_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT
                        query,
                        DATEDIFF('microseconds', starttime, endtime) as duration_microsecs,
                        DATEDIFF('microseconds', starttime, endtime) as cpu_time_microsecs,
                        0 as bytes_scanned,
                        0 as bytes_returned,
                        1 as slots,
                        1 as wlm_slots,
                        aborted
                    FROM stl_query
                    WHERE query = %s
                    ORDER BY starttime DESC
                    LIMIT 1
                """,
                    (redshift_query_id,),
                )
            result = cursor.fetchone()

            if result:
                raw_aborted = result[7]
                if isinstance(raw_aborted, str):
                    aborted = raw_aborted not in ("success", "running")
                else:
                    aborted = bool(raw_aborted) if raw_aborted is not None else False
                return {
                    "query_id": str(result[0]),
                    "duration_microsecs": result[1] or 0,
                    "cpu_time_microsecs": result[2] or 0,
                    "bytes_scanned": result[3] or 0,
                    "bytes_returned": result[4] or 0,
                    "slots": result[5] or 1,
                    "wlm_slots": result[6] or 1,
                    "aborted": aborted,
                }
            else:
                return {}
        except Exception:
            return {}
        finally:
            cursor.close()

    def analyze_table(self, connection: Any, table_name: str) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(f"ANALYZE {table_name.lower()}")
        finally:
            cursor.close()

    def gather_statistics(self, connection: Any, table_names: list[str]) -> tuple[str, int]:
        if self.auto_analyze:
            return "auto-on-load", 0
        return super().gather_statistics(connection, table_names)

    def vacuum_table(self, connection: Any, table_name: str) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(f"VACUUM {table_name.lower()}")
        except Exception as e:
            self.logger.warning(f"Failed to vacuum table {table_name}: {e}")
        finally:
            cursor.close()

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        cursor = connection.cursor()
        try:
            cursor.execute(f"EXPLAIN {query}")
            plan_rows = cursor.fetchall()
            return "\n".join([row[0] for row in plan_rows]) if plan_rows else None
        except Exception as e:
            self.logger.debug(f"Could not get query plan: {e}")
            return None
        finally:
            cursor.close()

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.redshift import RedshiftQueryPlanParser

        return RedshiftQueryPlanParser()

    def close_connection(self, connection: Any) -> None:
        try:
            if connection and hasattr(connection, "close"):
                connection.close()
        except Exception as e:
            self.logger.warning(f"Error closing connection: {e}")

    _supported_tuning_type_names = ("DISTRIBUTION", "SORTING", "PARTITIONING")

    def generate_tuning_clause(self, table_tuning) -> str:
        if not table_tuning or not table_tuning.has_any_tuning():
            return ""

        clauses = []

        try:
            from benchbox.core.tuning.interface import TuningType

            distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
            if distribution_columns:
                sorted_cols = sorted(distribution_columns, key=lambda col: col.order)
                dist_col = sorted_cols[0]

                clauses.append("DISTSTYLE KEY")
                clauses.append(f"DISTKEY ({dist_col.name})")
            else:
                clauses.append("DISTSTYLE EVEN")

            sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
            if sort_columns:
                sorted_cols = sorted(sort_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]

                sortkey_clause = f"SORTKEY ({', '.join(column_names)})"
                clauses.append(sortkey_clause)

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                pass

        except ImportError:
            pass

        return " ".join(clauses)

    def apply_post_load_tunings(self, table_name: str, effective_config: Any, connection: Any) -> bool:
        table_tuning = self.table_tuning_for(effective_config, table_name)
        if table_tuning is None or not table_tuning.has_any_tuning():
            return False
        if self.auto_analyze:
            return False
        physical_table = self.resolve_physical_table(table_name, connection)
        cursor = connection.cursor()
        try:
            cursor.execute(f"ANALYZE {physical_table}")
            self.logger.info(f"Analyzed table statistics for {physical_table}")
        except Exception as e:
            self.logger.warning(f"Failed to analyze {physical_table}: {e}")
            self.note_post_load_maintenance_failure()
        finally:
            cursor.close()
        return True

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        if not table_tuning or not table_tuning.has_any_tuning():
            return

        table_name = self.resolve_physical_table(table_tuning.table_name, connection)
        self.logger.info(f"Applying Redshift tunings for table: {table_name}")

        cursor = connection.cursor()
        try:
            from benchbox.core.tuning.interface import TuningType

            cursor.execute(f"""
                SELECT
                    "schema",
                    "table",
                    diststyle,
                    distkey,
                    sortkey1,
                    sortkey2,
                    sortkey3,
                    sortkey4
                FROM pg_table_def
                WHERE schemaname = 'public'
                AND tablename = '{table_name}'
            """)
            result = cursor.fetchone()

            if result:
                current_diststyle = result[2]
                current_distkey = result[3]
                current_sortkeys = [sk for sk in result[4:8] if sk]

                self.logger.info(f"Current configuration for {table_name}:")
                self.logger.info(f"  Distribution style: {current_diststyle}")
                self.logger.info(f"  Distribution key: {current_distkey}")
                self.logger.info(f"  Sort keys: {current_sortkeys}")

                distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
                sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)

                needs_recreation = False

                if distribution_columns:
                    sorted_cols = sorted(distribution_columns, key=lambda col: col.order)
                    desired_distkey = self.resolve_physical_column(table_name, sorted_cols[0].name, connection)
                    if current_distkey != desired_distkey or current_diststyle != "KEY":
                        needs_recreation = True
                        self.logger.info(
                            f"Distribution key mismatch: current='{current_distkey}', desired='{desired_distkey}'"
                        )

                if sort_columns:
                    sorted_cols = sorted(sort_columns, key=lambda col: col.order)
                    desired_sortkeys = [
                        self.resolve_physical_column(table_name, col.name, connection) for col in sorted_cols
                    ]
                    if current_sortkeys != desired_sortkeys:
                        needs_recreation = True
                        self.logger.info(f"Sort keys mismatch: current={current_sortkeys}, desired={desired_sortkeys}")

                if needs_recreation:
                    self.logger.warning(
                        f"Table {table_name} configuration differs from desired tuning. "
                        "Redshift requires table recreation to change distribution/sort keys."
                    )
            else:
                self.logger.warning(f"Could not find table configuration for {table_name}")

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                self.logger.info(
                    f"Partitioning strategy for {table_name}: {', '.join(column_names)} (handled via table design patterns)"
                )

            cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
            if cluster_columns:
                sorted_cols = sorted(cluster_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                self.logger.info(
                    f"Clustering strategy for {table_name} achieved via sort keys: {', '.join(column_names)}"
                )

        except ImportError:
            self.logger.warning("Tuning interface not available - skipping tuning application")
        except Exception as e:
            raise ValueError(f"Failed to apply tunings to Redshift table {table_name}: {e}") from e
        finally:
            cursor.close()

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

        apply_standard_unified_tuning(self, unified_config, connection)

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        if not platform_config:
            return

        self.logger.info("Redshift platform optimizations stored for session and workload management")

    apply_constraint_configuration = make_informational_constraint_applier(
        "Primary key constraints enabled for Redshift (informational only, applied during table creation)",
        "Foreign key constraints enabled for Redshift (informational only, applied during table creation)",
    )


_build_redshift_config = make_registered_platform_config_builder(
    "redshift",
    __name__,
    "Amazon Redshift",
    "redshift-connector",
    [
        "host",
        "port",
        "username",
        "password",
        "schema",
        "s3_bucket",
        "s3_prefix",
        "staging_root",
        "iam_role",
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
        "aws_region",
        "cluster_identifier",
        "admin_database",
        "connect_timeout",
        "statement_timeout",
        "sslmode",
        "disable_result_cache",
        "strict_validation",
    ],
    base_options={"admin_database": "dev"},
    resource_key=redshift_resource_key,
)
