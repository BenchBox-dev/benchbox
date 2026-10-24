from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        TuningColumn,
        UnifiedTuningConfiguration,
    )

from benchbox.core.execution_engine import ExecutionEngineReceipt, supported_execution_engines
from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE
from benchbox.core.upload_validation import UploadValidationEngine
from benchbox.platforms.base import DriverIsolationCapability, PlatformAdapter
from benchbox.platforms.base.runtime_metadata import build_default_normalized_result_metadata, with_canonical_compute
from benchbox.platforms.base.tuning import make_informational_constraint_applier
from benchbox.utils.datagen_manifest import MANIFEST_FILENAME
from benchbox.utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
    get_package_install_message,
)
from benchbox.utils.dialect_utils import ddl_column_names, schema_ddl_read_dialect, translate_ddl
from benchbox.utils.file_format import COMPRESSION_EXTENSIONS

try:
    from databricks import sql as databricks_sql
except ImportError:
    databricks_sql = None


def _select_databricks_warehouse(warehouses: list, very_verbose: bool, logger: logging.Logger):
    if not warehouses:
        return None

    running_wh = next((wh for wh in warehouses if str(wh.state) == "RUNNING"), None)
    if running_wh:
        if very_verbose:
            logger.info(f"Selected running warehouse: {running_wh.name}")
        return running_wh

    if very_verbose:
        logger.info("No running warehouses found. Looking for an available one to auto-start.")
    available_wh = next(
        (wh for wh in warehouses if str(wh.state) not in ["DELETING", "DELETED"]),
        None,
    )
    if available_wh and very_verbose:
        logger.info(f"Selected available warehouse to auto-start: {available_wh.name} (State: {available_wh.state})")
    return available_wh


DATABRICKS_OAUTH_ENV_VARS = ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_CONFIG_PROFILE")


def _validate_gateway(gateway: str) -> None:
    from benchbox.core.exceptions import ConfigurationError
    from benchbox.core.gateway import DEFAULT_GATEWAY, supported_gateways

    if gateway == DEFAULT_GATEWAY:
        return
    allowed = supported_gateways("databricks")
    if gateway not in allowed:
        raise ConfigurationError(
            f"Databricks does not support gateway '{gateway}'. Allowed: {', '.join([DEFAULT_GATEWAY, *sorted(allowed)])}"
        )
    oauth_vars = [name for name in DATABRICKS_OAUTH_ENV_VARS if os.environ.get(name)]
    if oauth_vars:
        raise ConfigurationError(
            f"Databricks gateway '{gateway}' supports personal access token authentication only, but "
            f"{', '.join(oauth_vars)} is set. Unset it and provide access_token (DATABRICKS_TOKEN) instead."
        )


def _compact_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if value not in (None, "", {}, [], ())}


def _normalize_table_format(config: Mapping[str, Any]) -> str:
    return str(config.get("table_format") or "delta").strip().lower()


_COMMIT_HASH_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")

WAREHOUSE_HTTP_PATH_PREFIX = "/sql/1.0/warehouses/"

_CURRENT_VERSION_KEYS = ("dbr_version", "dbsql_version", "u_build_hash", "r_build_hash")

_ENGINE_VERSION_SOURCE_CURRENT_VERSION = "current_version"
_ENGINE_VERSION_SOURCE_SQL_QUERY = "sql_query"


def _sanitize_spark_engine_version(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split()
    if len(parts) == 1:
        return parts[0]
    if _COMMIT_HASH_RE.match(parts[1]):
        return parts[0]
    return text


def _first_column(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        if len(row) == 1:
            try:
                return next(iter(row.values()))
            except Exception:
                return None
        for key in ("version", "spark_version", "current_version()", "current_version"):
            if key in row:
                try:
                    return row[key]
                except Exception:
                    continue
        try:
            return next(iter(row.values()))
        except Exception:
            return None
    if isinstance(row, (tuple, list)):
        if len(row) == 0:
            return None
        try:
            return row[0]
        except Exception:
            return None
    if isinstance(row, (str, bytes)):
        return row
    try:
        return row[0]
    except Exception:
        return row


def _unwrap_current_version_struct(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        if any(key in row for key in _CURRENT_VERSION_KEYS):
            return row
        if len(row) == 1:
            try:
                return next(iter(row.values()))
            except Exception:
                return None
        return row
    if isinstance(row, (tuple, list)):
        if len(row) == 0:
            return None
        try:
            if "dbsql_version" in row or "dbr_version" in row:
                return row
        except Exception:
            pass
        try:
            return row[0]
        except Exception:
            return None
    as_dict = getattr(row, "asDict", None)
    if callable(as_dict):
        try:
            result = as_dict()
            if isinstance(result, Mapping):
                return result
        except Exception:
            pass
    return row


def _clean_version_token(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _struct_fields_from_object(payload: Any) -> dict[str, Any] | None:
    values: dict[str, Any] = {}
    for key in _CURRENT_VERSION_KEYS:
        try:
            try:
                present = key in payload
            except Exception:
                present = False
            if present:
                try:
                    values[key] = payload[key]
                except Exception:
                    values[key] = getattr(payload, key, None)
            else:
                values[key] = getattr(payload, key, None)
        except Exception:
            values[key] = None
    if not any(isinstance(values.get(key), str) for key in _CURRENT_VERSION_KEYS):
        return None
    return values


def _parse_current_version_payload(payload: Any) -> dict[str, str | None] | None:
    if payload is None or isinstance(payload, str):
        return None
    if isinstance(payload, (tuple, list)):
        if len(payload) == 0:
            return None
        try:
            if "dbsql_version" in payload or "dbr_version" in payload:
                pass
            else:
                payload = payload[0]
                if payload is None or isinstance(payload, str):
                    return None
        except Exception:
            try:
                payload = payload[0]
            except Exception:
                return None
            if payload is None or isinstance(payload, str):
                return None
    if isinstance(payload, Mapping):
        data: dict[str, Any] = dict(payload)
    else:
        extracted = _struct_fields_from_object(payload)
        if extracted is None:
            return None
        data = extracted
    cleaned = {key: _clean_version_token(data.get(key)) for key in _CURRENT_VERSION_KEYS}
    if not cleaned.get("dbsql_version") and not cleaned.get("dbr_version"):
        if not cleaned.get("u_build_hash") and not cleaned.get("r_build_hash"):
            return None
    return cleaned


def _select_databricks_platform_version(parsed: Mapping[str, Any] | None) -> str | None:
    if not isinstance(parsed, Mapping):
        return None
    return _clean_version_token(parsed.get("dbsql_version")) or _clean_version_token(parsed.get("dbr_version"))


class DatabricksAdapter(PlatformAdapter):
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    supports_external_tables = True
    physical_identifier_case = "lower"
    post_load_connection_recording = False

    def __init__(self, **config):
        super().__init__(**config)

        available, missing = check_platform_dependencies("databricks")
        if not available:
            error_msg = get_dependency_error_message("databricks", missing)
            raise ImportError(error_msg)

        self._dialect = "databricks"

        self.server_hostname = config.get("server_hostname") or config.get("host")
        self.compute_resource = config.get("compute_resource") or config.get("warehouse_id")
        self.http_path = self._resolve_http_path(self.compute_resource, config.get("http_path"))
        self.access_token = config.get("access_token") or config.get("token")
        self.catalog = config.get("catalog") or "main"
        self.schema = config.get("schema") or "benchbox"
        self.uc_catalog = config.get("uc_catalog")
        self.uc_schema = config.get("uc_schema")
        self.uc_volume = config.get("uc_volume")
        self.staging_root = config.get("staging_root")
        self.gateway = str(config.get("gateway") or "native")
        _validate_gateway(self.gateway)
        self.region = config.get("region") or config.get("cloud_region") or config.get("workspace_region")

        self.enable_delta_optimization = (
            config.get("enable_delta_optimization") if config.get("enable_delta_optimization") is not None else True
        )
        self.delta_auto_optimize = (
            config.get("delta_auto_optimize") if config.get("delta_auto_optimize") is not None else True
        )
        self.delta_auto_compact = (
            config.get("delta_auto_compact") if config.get("delta_auto_compact") is not None else True
        )

        table_format = _normalize_table_format(config)
        if table_format not in ("delta", "hudi"):
            raise ValueError(f"Unsupported Databricks table_format '{table_format}'. Use 'delta' or 'hudi'.")
        self.table_format = table_format
        from benchbox.platforms.base.data_loading import validate_sql_identifier

        hudi_primary_key = config.get("hudi_primary_key")
        if hudi_primary_key is not None:
            hudi_primary_key = validate_sql_identifier(str(hudi_primary_key).strip(), "hudi_primary_key")
        self.hudi_primary_key = hudi_primary_key
        hudi_precombine_field = config.get("hudi_precombine_field")
        if hudi_precombine_field is not None:
            hudi_precombine_field = validate_sql_identifier(str(hudi_precombine_field).strip(), "hudi_precombine_field")
        self.hudi_precombine_field = hudi_precombine_field
        hudi_table_type = str(config.get("hudi_table_type") or "cow").strip().lower()
        if hudi_table_type not in ("cow", "mor"):
            raise ValueError(f"Unsupported hudi_table_type '{hudi_table_type}'. Use 'cow' or 'mor'.")
        self.hudi_table_type = hudi_table_type

        self.cluster_size = config.get("cluster_size")
        self.auto_terminate_minutes = (
            config.get("auto_terminate_minutes") if config.get("auto_terminate_minutes") is not None else 30
        )

        self.create_catalog = config.get("create_catalog") if config.get("create_catalog") is not None else False

        force_upload_val = config.get("force_upload")
        self.force_upload = bool(force_upload_val if force_upload_val is not None else False)

        disable_result_cache = config.get("disable_result_cache", True)
        self.disable_result_cache = True if disable_result_cache is None else bool(disable_result_cache)
        self._liquid_clustering_operations: list[dict[str, Any]] = []
        self._z_order_operations: list[dict[str, Any]] = []
        self._cache_disabled_sessions: Any = None
        self._cache_disable_failed = False
        self._cache_control_receipt: dict[str, Any] | None = None
        self._applied_layout_operations: list[dict[str, Any]] = []
        self._skipped_layout_operations: list[dict[str, Any]] = []

        if not self.server_hostname or not self.http_path or not self.access_token:
            missing = []
            if not self.server_hostname:
                missing.append("server_hostname")
            if not self.http_path:
                missing.append("http_path")
            if not self.access_token:
                missing.append("access_token")

            from benchbox.core.exceptions import ConfigurationError

            raise ConfigurationError(
                f"Databricks configuration is incomplete. Missing: {', '.join(missing)}\n"
                "Set them with any of:\n"
                "  1. benchbox setup --platform databricks\n"
                "  2. ~/.benchbox/credentials.yaml, under 'databricks:' "
                "(values may reference environment variables as ${VAR})\n"
                "  3. --platform-option compute_resource=<SQL warehouse ID or HTTP path> (sets http_path only)"
            )

    @property
    def platform_name(self) -> str:
        return "Databricks"

    def _reset_run_scoped_state(self) -> None:
        super()._reset_run_scoped_state()
        self._cache_disabled_sessions = None
        self._cache_disable_failed = False
        self._cache_control_receipt = None
        self._liquid_clustering_operations = []
        self._z_order_operations = []
        self._applied_layout_operations = []
        self._skipped_layout_operations = []

    def _resolve_databricks_clustering_strategy(self) -> str:
        effective_config = self.get_effective_tuning_configuration()
        platform_opts = getattr(effective_config, "platform_optimizations", None)
        if platform_opts is None:
            return "none"

        strategy = getattr(platform_opts, "databricks_clustering_strategy", None) or "none"
        liquid_enabled = bool(getattr(platform_opts, "liquid_clustering_enabled", False))
        liquid_columns = list(getattr(platform_opts, "liquid_clustering_columns", []))
        z_order_enabled = bool(getattr(platform_opts, "z_ordering_enabled", False))
        z_order_columns = list(getattr(platform_opts, "z_ordering_columns", []))

        if hasattr(platform_opts, "__post_init__"):
            platform_opts.__post_init__()

        liquid_requested = (
            strategy in {"liquid_clustering", "liquid_clustering_auto"} or liquid_enabled or liquid_columns
        )
        if strategy == "none" and (liquid_enabled or liquid_columns or z_order_enabled or z_order_columns):
            raise ValueError(
                "databricks_clustering_strategy='none' cannot be combined with Liquid Clustering or ZORDER fields"
            )
        if liquid_requested and (z_order_enabled or z_order_columns):
            raise ValueError(
                "Databricks Liquid Clustering cannot be combined with z_ordering_enabled or z_ordering_columns; "
                "use a Liquid template with ZORDER fields removed or keep the legacy Z-ORDER rendering."
            )
        if strategy == "liquid_clustering_auto":
            return "liquid_clustering_auto"
        if strategy == "liquid_clustering" or liquid_enabled or liquid_columns:
            return "liquid_clustering"
        if strategy == "none":
            table_tunings = getattr(effective_config, "table_tunings", {}) or {}
            if isinstance(table_tunings, Mapping) and any(
                getattr(tuning, "clustering", None) or getattr(tuning, "distribution", None)
                for tuning in table_tunings.values()
            ):
                return "z_order"
            return strategy
        if z_order_enabled or z_order_columns:
            return "z_order"
        return strategy if strategy == "z_order" else "none"

    def _record_layout_operation(
        self,
        *,
        mechanism: str,
        table: str,
        statement: str,
        status: str,
        phase: str,
        columns: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        operation = {
            "mechanism": mechanism,
            "table": table,
            "statement": statement,
            "phase": phase,
            "status": status,
        }
        if columns:
            operation["columns"] = list(columns)
        if error is not None:
            operation["error_class"] = type(error).__name__
            operation["error_message"] = str(error)
        if status == "applied":
            self._applied_layout_operations.append(operation)
        else:
            self._skipped_layout_operations.append(operation)

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[TuningColumn]) -> str | None:
        if getattr(self, "table_format", "delta") == "hudi":
            self.logger.info(f"Skipped sorted ingestion for Hudi table {table_name}")
            return None
        mode, method = self.resolve_sorted_ingestion_strategy()
        if mode == "off":
            return None

        sorted_column_names = ", ".join(column.name for column in sort_columns)
        if method == "ctas":
            return f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM {table_name} ORDER BY {sorted_column_names}"
        if method == "z_order":
            return f"OPTIMIZE {table_name} ZORDER BY ({sorted_column_names})"
        if method == "liquid_clustering":
            return f"ALTER TABLE {table_name} CLUSTER BY ({sorted_column_names})"

        raise ValueError(f"Sorted ingestion method '{method}' is not supported for Databricks.")

    @staticmethod
    def add_cli_arguments(parser) -> None:
        db_group = parser.add_argument_group("Databricks Arguments")
        db_group.add_argument("--server-hostname", type=str, help="Databricks server hostname")
        db_group.add_argument("--http-path", type=str, help="Databricks SQL Warehouse HTTP path")
        db_group.add_argument("--access-token", type=str, help="Databricks access token")
        db_group.add_argument("--catalog", type=str, default="workspace", help="Databricks catalog name")
        db_group.add_argument(
            "--schema", type=str, default=None, help="Databricks schema name (auto-generated if not specified)"
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.core.compute_resource import normalize_compute_options

        config = normalize_compute_options("databricks", config)
        from benchbox.utils.database_naming import generate_database_name

        adapter_config = {}
        very_verbose = config.get("very_verbose", False)

        def is_placeholder(value):
            if not value:
                return True
            str_val = str(value)
            return (
                "your-workspace" in str_val
                or "your-warehouse-id" in str_val
                or "${" in str_val
                or "example" in str_val.lower()
            )

        compute_resource = config.get("compute_resource")
        if not all(
            [
                config.get("server_hostname") and not is_placeholder(config.get("server_hostname")),
                compute_resource or (config.get("http_path") and not is_placeholder(config.get("http_path"))),
                config.get("access_token") and not is_placeholder(config.get("access_token")),
            ]
        ):
            auto_config = cls._auto_detect_databricks_config(very_verbose=very_verbose)
            if auto_config:
                adapter_config.update(auto_config)
                if compute_resource:
                    adapter_config.pop("http_path", None)

        for key in ["server_hostname", "http_path", "access_token"]:
            if config.get(key) and not is_placeholder(config.get(key)):
                adapter_config[key] = config[key]
        if compute_resource:
            adapter_config["compute_resource"] = compute_resource

        adapter_config["catalog"] = config.get("catalog", "workspace")

        provided_schema = config.get("schema")
        has_benchmark_context = "benchmark" in config and "scale_factor" in config

        if has_benchmark_context:
            is_default_schema = provided_schema in (None, "", "benchbox")

            if is_default_schema:
                schema_name = generate_database_name(
                    benchmark_name=config["benchmark"],
                    scale_factor=config["scale_factor"],
                    platform="databricks",
                    tuning_config=config.get("tuning_config"),
                )
                adapter_config["schema"] = schema_name
            else:
                adapter_config["schema"] = provided_schema
        else:
            adapter_config["schema"] = provided_schema or "benchbox"

        for key in [
            "tuning_config",
            "tuning_enabled",
            "unified_tuning_configuration",
            "tuning_source",
            "tuning_source_file",
            "verbose_enabled",
            "very_verbose",
            "uc_catalog",
            "uc_schema",
            "uc_volume",
            "staging_root",
            "region",
            "cloud_region",
            "workspace_region",
            "cluster_size",
            "auto_terminate_minutes",
            "enable_delta_optimization",
            "delta_auto_optimize",
            "delta_auto_compact",
            "table_format",
            "hudi_primary_key",
            "hudi_precombine_field",
            "hudi_table_type",
            "create_catalog",
            "disable_result_cache",
            "gateway",
        ]:
            if key in config:
                adapter_config[key] = config[key]

        return cls(**adapter_config)

    @staticmethod
    def _auto_detect_databricks_config(very_verbose: bool = False):
        logger = logging.getLogger("DatabricksAdapter")
        try:
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.service.sql import WarehousesAPI

            if very_verbose:
                logger.info("Attempting to auto-detect Databricks configuration from SDK...")

            workspace = WorkspaceClient()
            server_hostname = workspace.config.host.replace("https://", "")
            access_token = workspace.config.token

            if very_verbose:
                logger.info(f"Found Databricks host: {server_hostname}")

            warehouses = list(WarehousesAPI(workspace.api_client).list())
            if very_verbose:
                logger.info(f"Found {len(warehouses)} Databricks SQL Warehouses.")
                for wh in warehouses:
                    logger.info(f"  - Warehouse: {wh.name}, State: {wh.state}, ID: {wh.id}")

            selected_warehouse = _select_databricks_warehouse(warehouses, very_verbose, logger)

            http_path = None
            if selected_warehouse:
                http_path = f"/sql/1.0/warehouses/{selected_warehouse.id}"
                if very_verbose:
                    logger.info(f"Using HTTP path: {http_path}")
            elif very_verbose:
                logger.warning("No suitable warehouse found for auto-detection.")

            return {
                "server_hostname": server_hostname,
                "http_path": http_path,
                "access_token": access_token,
            }
        except Exception as e:
            if very_verbose:
                logger.error(f"Databricks auto-detection failed: {e}")
            return None

    def _resolve_databricks_engine_version(self, connection: Any) -> dict[str, Any]:
        fallback_hashes: dict[str, str] = {}
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT current_version()")
                parsed = _parse_current_version_payload(_unwrap_current_version_struct(cursor.fetchone()))
                if parsed is not None:
                    for key in ("u_build_hash", "r_build_hash"):
                        if parsed.get(key):
                            fallback_hashes[key] = str(parsed[key])
                    selected = _select_databricks_platform_version(parsed)
                    if selected:
                        resolved: dict[str, Any] = {
                            "platform_version": selected,
                            "engine_version": selected,
                            "engine_version_source": _ENGINE_VERSION_SOURCE_CURRENT_VERSION,
                        }
                        for key in _CURRENT_VERSION_KEYS:
                            if parsed.get(key):
                                resolved[key] = parsed[key]
                        return resolved
            finally:
                try:
                    cursor.close()
                except Exception:
                    pass
        except Exception as exc:
            self.logger.debug(f"current_version() probe failed, falling back to version(): {exc}")
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT version()")
                sanitized = _sanitize_spark_engine_version(_first_column(cursor.fetchone()))
                if sanitized:
                    resolved = {
                        "platform_version": sanitized,
                        "engine_version": sanitized,
                        "engine_version_source": _ENGINE_VERSION_SOURCE_SQL_QUERY,
                    }
                    resolved.update(fallback_hashes)
                    return resolved
                cursor.execute("SELECT spark_version() as version")
                sanitized = _sanitize_spark_engine_version(_first_column(cursor.fetchone()))
                if sanitized:
                    resolved = {
                        "platform_version": sanitized,
                        "engine_version": sanitized,
                        "engine_version_source": _ENGINE_VERSION_SOURCE_SQL_QUERY,
                    }
                    resolved.update(fallback_hashes)
                    return resolved
            finally:
                try:
                    cursor.close()
                except Exception:
                    pass
        except Exception as exc:
            self.logger.debug(f"Could not query Databricks runtime version: {exc}")
        degraded: dict[str, Any] = {
            "platform_version": None,
            "engine_version": None,
            "engine_version_source": None,
        }
        degraded.update(fallback_hashes)
        return degraded

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        clustering_strategy = self._resolve_databricks_clustering_strategy()
        effective_config = self.get_effective_tuning_configuration()
        platform_opts = getattr(effective_config, "platform_optimizations", None)
        requested_strategy = getattr(platform_opts, "databricks_clustering_strategy", None)

        platform_info = {
            "platform_type": "databricks",
            "platform_name": "Databricks",
            "connection_mode": "remote",
            "host": self.server_hostname,
            "configuration": {
                "server_hostname": self.server_hostname,
                "catalog": self.catalog,
                "schema": self.schema,
                "http_path": self.http_path,
                "warehouse_id": self._warehouse_id_from_http_path(self.http_path),
                "staging_root": self.staging_root,
                "uc_catalog": self.uc_catalog,
                "uc_schema": self.uc_schema,
                "uc_volume": self.uc_volume,
                "region": self.region,
                "enable_delta_optimization": self.enable_delta_optimization,
                "delta_auto_optimize": self.delta_auto_optimize,
                "delta_auto_compact": self.delta_auto_compact,
                "table_format": self.table_format,
                "hudi_primary_key": self.hudi_primary_key,
                "hudi_precombine_field": self.hudi_precombine_field,
                "hudi_table_type": self.hudi_table_type,
                "cluster_size": self.cluster_size,
                "auto_terminate_minutes": self.auto_terminate_minutes,
                "cluster_mode": getattr(self, "cluster_mode", None),
                "spark_version": getattr(self, "spark_version", None),
                "result_cache_enabled": not self.disable_result_cache,
                "requested_databricks_clustering_strategy": requested_strategy,
                "resolved_databricks_clustering_strategy": clustering_strategy,
                "databricks_clustering_strategy": clustering_strategy,
                "liquid_clustering_enabled": bool(getattr(platform_opts, "liquid_clustering_enabled", False)),
                "liquid_clustering_columns_config": list(getattr(platform_opts, "liquid_clustering_columns", [])),
                "liquid_clustering_operations": list(self._liquid_clustering_operations),
                "z_order_operations": list(self._z_order_operations),
                "applied_layout_operations": list(self._applied_layout_operations),
                "skipped_layout_operations": list(self._skipped_layout_operations),
            },
        }

        try:
            import databricks.sql

            platform_info["client_library_version"] = getattr(databricks.sql, "__version__", None)
        except (ImportError, AttributeError):
            platform_info["client_library_version"] = None

        if connection is not None:
            resolved_version = self._resolve_databricks_engine_version(connection)
            platform_info["platform_version"] = resolved_version.get("platform_version")
            platform_info["engine_version"] = resolved_version.get("engine_version")
            platform_info["engine_version_source"] = resolved_version.get("engine_version_source")
            for detail_key in _CURRENT_VERSION_KEYS:
                if resolved_version.get(detail_key) is not None:
                    platform_info[detail_key] = resolved_version[detail_key]
        else:
            platform_info["platform_version"] = None
            platform_info["engine_version"] = None
            platform_info["engine_version_source"] = None

        if self.gateway != "native":
            platform_info["gateway"] = {"name": self.gateway, "routed": True}

        warehouse_id = self._warehouse_id_from_http_path(self.http_path)
        try:
            from databricks.sdk import WorkspaceClient

            if warehouse_id:
                workspace = WorkspaceClient(host=f"https://{self.server_hostname}", token=self.access_token)

                warehouse = workspace.warehouses.get(warehouse_id)

                is_serverless = (
                    hasattr(warehouse, "warehouse_type")
                    and hasattr(warehouse, "enable_serverless_compute")
                    and warehouse.warehouse_type
                    and warehouse.warehouse_type.value == "PRO"
                    and warehouse.enable_serverless_compute is True
                )

                raw_warehouse_type = (
                    warehouse.warehouse_type.value
                    if hasattr(warehouse, "warehouse_type") and warehouse.warehouse_type
                    else None
                )
                warehouse_type_display = "SERVERLESS" if is_serverless else raw_warehouse_type

                channel_name = None
                warehouse_version = None
                if hasattr(warehouse, "channel") and warehouse.channel:
                    if hasattr(warehouse.channel, "name") and warehouse.channel.name:
                        channel_name = warehouse.channel.name.value
                    if hasattr(warehouse.channel, "dbsql_version"):
                        warehouse_version = warehouse.channel.dbsql_version

                    if channel_name is None:
                        self.logger.debug(f"Channel name extraction failed for warehouse {warehouse_id}")
                    if warehouse_version is None:
                        self.logger.debug(f"Warehouse version extraction failed for warehouse {warehouse_id}")

                platform_info["compute_configuration"] = {
                    "warehouse_id": warehouse.id,
                    "warehouse_name": warehouse.name if hasattr(warehouse, "name") else None,
                    "warehouse_size": warehouse.cluster_size if hasattr(warehouse, "cluster_size") else None,
                    "warehouse_type": warehouse_type_display,
                    "auto_stop_mins": warehouse.auto_stop_mins if hasattr(warehouse, "auto_stop_mins") else None,
                    "min_num_clusters": warehouse.min_num_clusters if hasattr(warehouse, "min_num_clusters") else None,
                    "max_num_clusters": warehouse.max_num_clusters if hasattr(warehouse, "max_num_clusters") else None,
                    "enable_photon": warehouse.enable_photon if hasattr(warehouse, "enable_photon") else None,
                    "enable_serverless_compute": warehouse.enable_serverless_compute
                    if hasattr(warehouse, "enable_serverless_compute")
                    else None,
                    "spot_instance_policy": warehouse.spot_instance_policy.value
                    if hasattr(warehouse, "spot_instance_policy") and warehouse.spot_instance_policy
                    else None,
                    "channel": channel_name,
                    "warehouse_version": warehouse_version,
                    "state": warehouse.state.value if hasattr(warehouse, "state") else None,
                    "warehouse_metadata_collection_status": "available",
                }

                self.logger.debug(f"Successfully captured Databricks warehouse metadata for {warehouse_id}")

        except ImportError as e:
            self.logger.debug("databricks-sdk not installed, skipping warehouse metadata collection")
            platform_info["compute_configuration"] = self._unavailable_warehouse_metadata(warehouse_id, e)
        except Exception as e:
            self.logger.debug(
                f"Could not fetch Databricks warehouse metadata (insufficient permissions or API error): {e}"
            )
            platform_info["compute_configuration"] = self._unavailable_warehouse_metadata(warehouse_id, e)

        if not self.region:
            detected_region = self._detect_databricks_region()
            if detected_region:
                self.region = detected_region
                configuration = platform_info.get("configuration")
                if isinstance(configuration, dict):
                    configuration["region"] = detected_region

        platform_info["configuration"]["execution_engine"] = asdict(
            self._photon_receipt(platform_info.get("compute_configuration", {}).get("enable_photon"))
        )
        return platform_info

    @staticmethod
    def _photon_receipt(photon_enabled: Any) -> ExecutionEngineReceipt:
        applied = "photon" if photon_enabled is True else "standard" if photon_enabled is False else None
        engines = supported_execution_engines("databricks")
        return ExecutionEngineReceipt(
            requested=DEFAULT_EXECUTION_ENGINE,
            applied=applied,
            applied_class=engines[applied].engine_class if applied in engines else None,
            applied_native={} if applied is None else {"enable_photon": photon_enabled},
            resolution="platform_default",
        )

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

        from benchbox.platforms.cloud_shared import sanitize_cache_control_receipt

        receipt = sanitize_cache_control_receipt(self._cache_control_receipt)
        config = dict(cast(Mapping[str, Any], config))
        config["result_cache_enabled"] = not receipt["cache_disabled"] if receipt and receipt["validated"] else None

        metadata["platform_deployment"] = self._databricks_deployment_metadata(config, compute)
        metadata["platform_cloud"] = self._databricks_cloud_metadata(config)
        databricks_compute = self._databricks_compute_metadata(config, compute)
        metadata["platform_compute"] = with_canonical_compute(
            metadata.get("platform_compute"), databricks_compute, size=databricks_compute.get("warehouse_size")
        )
        if receipt is not None:
            metadata["platform_compute"]["cache_control"] = receipt
        metadata["platform_storage"] = self._databricks_storage_metadata(config)
        return metadata

    @staticmethod
    def _warehouse_id_from_http_path(http_path: str | None) -> str | None:
        if not http_path:
            return None
        segments = [segment for segment in str(http_path).split("?", 1)[0].split("/") if segment]
        for kind in ("warehouses", "endpoints"):
            if kind in segments:
                index = segments.index(kind)
                return segments[index + 1] if index + 1 < len(segments) else None
        return None

    @classmethod
    def _resolve_http_path(cls, compute_resource: str | None, http_path: str | None) -> str | None:
        if not compute_resource:
            return http_path
        if not http_path:
            return f"{WAREHOUSE_HTTP_PATH_PREFIX}{compute_resource}"
        implied = cls._warehouse_id_from_http_path(http_path)
        if implied is not None and implied != compute_resource:
            raise ValueError(
                f"Conflicting Databricks compute settings: compute_resource='{compute_resource}' "
                f"names a different warehouse than http_path='{http_path}' (warehouse '{implied}'). "
                "Keep only one."
            )
        return http_path

    def _detect_databricks_region(self) -> str | None:
        try:
            from databricks.sdk import WorkspaceClient

            if not self.server_hostname or not self.access_token:
                return None
            workspace = WorkspaceClient(host=f"https://{self.server_hostname}", token=self.access_token)
            summary = workspace.metastores.summary()
            region = getattr(summary, "region", None)
            return region.strip() if isinstance(region, str) and region.strip() else None
        except Exception as e:
            self.logger.debug(f"Could not detect Databricks workspace region: {e}")
            return None

    @staticmethod
    def _is_serverless_warehouse(compute: Mapping[str, Any]) -> bool:
        warehouse_type = str(compute.get("warehouse_type") or "").lower()
        return bool(compute.get("enable_serverless_compute") or warehouse_type == "serverless")

    @staticmethod
    def _unavailable_warehouse_metadata(warehouse_id: str | None, exc: Exception) -> dict[str, Any]:
        return _compact_metadata(
            {
                "warehouse_id": warehouse_id,
                "warehouse_metadata_collection_status": "unavailable",
                "warehouse_metadata_error_class": type(exc).__name__,
                "warehouse_metadata_error_message": f"{type(exc).__name__}: warehouse metadata collection failed",
            }
        )

    @staticmethod
    def _databricks_cloud_provider(host: Any) -> str | None:
        host_value = str(host or "").lower()
        if "azuredatabricks.net" in host_value:
            return "azure"
        if "gcp.databricks.com" in host_value:
            return "gcp"
        if "databricks.com" in host_value:
            return "aws"
        return None

    @classmethod
    def _databricks_deployment_metadata(
        cls,
        config: Mapping[str, Any],
        compute: Mapping[str, Any],
    ) -> dict[str, Any]:
        observed = bool(compute)
        serverless = cls._is_serverless_warehouse(compute)
        return _compact_metadata(
            {
                "deployment_type": "serverless" if serverless else "managed_cloud",
                "connection_mode": "remote",
                "endpoint_class": "cloud_endpoint",
                "metadata_source": "observed" if observed else "requested",
                "collection_status": "available" if observed else "partial",
                "workspace_host": config.get("server_hostname"),
                "http_path": config.get("http_path"),
                "warehouse_id": compute.get("warehouse_id")
                or config.get("compute_resource")
                or config.get("warehouse_id"),
                "catalog": config.get("catalog"),
                "schema": config.get("schema"),
            }
        )

    @classmethod
    def _databricks_cloud_metadata(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        host = config.get("server_hostname")
        region = config.get("region") or config.get("cloud_region") or config.get("workspace_region")
        provider = cls._databricks_cloud_provider(host)
        has_cloud_metadata = bool(provider or region or host)
        return _compact_metadata(
            {
                "provider": provider,
                "region": region,
                "workspace": host,
                "region_collection_status": "available" if region else "unavailable",
                "source": "inferred" if provider else "requested" if has_cloud_metadata else "unavailable",
                "collection_status": "partial" if has_cloud_metadata else "unavailable",
            }
        )

    @classmethod
    def _databricks_compute_metadata(
        cls,
        config: Mapping[str, Any],
        compute: Mapping[str, Any],
    ) -> dict[str, Any]:
        observed = any(
            compute.get(key) is not None
            for key in (
                "warehouse_name",
                "warehouse_size",
                "warehouse_type",
                "enable_photon",
                "enable_serverless_compute",
                "warehouse_version",
                "state",
            )
        )
        warehouse_id = compute.get("warehouse_id") or config.get("compute_resource") or config.get("warehouse_id")
        serverless = cls._is_serverless_warehouse(compute)
        auto_stop_mins = (
            compute.get("auto_stop_mins")
            if compute.get("auto_stop_mins") is not None
            else config.get("auto_terminate_minutes")
        )
        return _compact_metadata(
            {
                "warehouse": compute.get("warehouse_name"),
                "warehouse_id": warehouse_id,
                "warehouse_size": compute.get("warehouse_size") or config.get("cluster_size"),
                "warehouse_type": compute.get("warehouse_type"),
                "serverless": serverless if observed else None,
                "photon_enabled": compute.get("enable_photon"),
                "min_cluster_count": compute.get("min_num_clusters"),
                "max_cluster_count": compute.get("max_num_clusters"),
                "auto_stop_mins": auto_stop_mins,
                "spot_instance_policy": compute.get("spot_instance_policy"),
                "channel": compute.get("channel"),
                "warehouse_version": compute.get("warehouse_version"),
                "state": compute.get("state"),
                "result_cache_enabled": config.get("result_cache_enabled"),
                "delta_auto_optimize": config.get("delta_auto_optimize"),
                "delta_auto_compact": config.get("delta_auto_compact"),
                "warehouse_metadata_collection_status": compute.get("warehouse_metadata_collection_status"),
                "warehouse_metadata_error_class": compute.get("warehouse_metadata_error_class"),
                "warehouse_metadata_error_message": compute.get("warehouse_metadata_error_message"),
                "source": "observed" if observed else "requested",
                "collection_status": "available" if observed else "partial",
            }
        )

    @staticmethod
    def _databricks_storage_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        staging_location = config.get("staging_root")
        if not staging_location and config.get("uc_catalog") and config.get("uc_schema") and config.get("uc_volume"):
            staging_location = f"dbfs:/Volumes/{config['uc_catalog']}/{config['uc_schema']}/{config['uc_volume']}"
        has_storage = bool(staging_location or config.get("catalog") or config.get("schema"))
        return _compact_metadata(
            {
                "table_format": _normalize_table_format(config),
                "staging_location": staging_location,
                "catalog": config.get("catalog"),
                "schema": config.get("schema"),
                "uc_catalog": config.get("uc_catalog"),
                "uc_schema": config.get("uc_schema"),
                "uc_volume": config.get("uc_volume"),
                "source": "requested" if has_storage else "unavailable",
                "collection_status": "partial" if has_storage else "unavailable",
            }
        )

    def get_target_dialect(self) -> str:
        return "databricks"

    def preprocess_operation_sql(self, query_id: str, operation: Any) -> str | None:
        import re

        overrides = getattr(operation, "platform_overrides", None) or {}
        if "databricks" in overrides:
            base = overrides["databricks"]
            if base is None:
                return None
        else:
            base = operation.write_sql
        base = re.sub(
            r"\bunnest\(\s*generate_series\(([^()]*)\)\s*\)",
            r"explode(sequence(\1))",
            base,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"\bCAST\(([^()]+?)\s+AS\s+VARCHAR\s*\)",
            r"CAST(\1 AS STRING)",
            base,
            flags=re.IGNORECASE,
        )

    def _get_connection_params(self, **connection_config) -> dict[str, Any]:
        return {
            "server_hostname": connection_config.get("server_hostname", self.server_hostname),
            "http_path": connection_config.get("http_path", self.http_path),
            "access_token": connection_config.get("access_token", self.access_token),
        }

    def _create_admin_connection(self, **connection_config) -> Any:
        params = self._get_connection_params(**connection_config)

        return databricks_sql.connect(**params, user_agent_entry="BenchBox/1.0")

    def check_server_database_exists(self, **connection_config) -> bool:
        try:
            connection = self._create_admin_connection(**connection_config)
            cursor = connection.cursor()

            catalog = connection_config.get("catalog", self.catalog)
            schema = connection_config.get("schema", self.schema)

            cursor.execute("SHOW CATALOGS")
            catalogs = [row[0] for row in cursor.fetchall()]

            if catalog not in catalogs:
                return False

            cursor.execute(f"SHOW SCHEMAS IN {catalog}")
            schemas = [row[0] for row in cursor.fetchall()]

            return schema in schemas

        except Exception:
            return False
        finally:
            if "connection" in locals():
                connection.close()

    def reset_database_in_place(self, **connection_config) -> bool:
        catalog = connection_config.get("catalog", self.catalog)
        schema = connection_config.get("schema", self.schema)
        connection = None
        try:
            connection = self._create_admin_connection(**connection_config)
            cursor = connection.cursor()
            cursor.execute(f"SHOW TABLES IN {catalog}.{schema}")
            tables = [row[1] for row in cursor.fetchall() if not (len(row) > 2 and row[2])]
            for table in tables:
                cursor.execute(f"TRUNCATE TABLE {catalog}.{schema}.`{table}`")
            self.log_verbose(f"Truncated {len(tables)} tables in {catalog}.{schema} for reload")
            self._schema_reset_in_place = True
            return True
        except Exception as e:
            self.log_verbose(f"In-place reset of {catalog}.{schema} failed: {e}")
            raise
        finally:
            if connection is not None:
                connection.close()

    def drop_database(self, **connection_config) -> None:
        try:
            connection = self._create_admin_connection(**connection_config)
            cursor = connection.cursor()

            catalog = connection_config.get("catalog", self.catalog)
            schema = connection_config.get("schema", self.schema)

            cursor.execute(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")

        except Exception as e:
            raise RuntimeError(f"Failed to drop Databricks schema {catalog}.{schema}: {e}") from e
        finally:
            if "connection" in locals():
                connection.close()

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("Databricks connection")

        self.handle_existing_database(**connection_config)

        try:
            params = self._get_connection_params(**connection_config)
            self.log_very_verbose(
                f"Databricks connection params: host={params.get('server_hostname')}, catalog={self.catalog}"
            )

            connection = self._create_admin_connection(**connection_config)

            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchall()
            self.log_very_verbose("Databricks connection test successful")

            if not (getattr(self, "create_catalog", False) and not getattr(self, "database_was_reused", False)):
                cursor.execute(f"USE CATALOG {self.catalog}")
                if not getattr(self, "database_was_reused", False) and not getattr(self, "_validating_database", False):
                    cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.{self.schema}")
                cursor.execute(f"USE SCHEMA {self.schema}")
            self.log_very_verbose(f"Set schema context to {self.catalog}.{self.schema}")

            try:
                self._ensure_session_cache_disabled(cursor, session=connection)
            except Exception:
                connection.close()
                raise
            finally:
                cursor.close()

            self.log_operation_complete(
                "Databricks connection",
                details=f"Connected to {params['server_hostname']}, catalog: {self.catalog}",
            )

            return connection

        except Exception as e:
            self.logger.error(f"Failed to connect to Databricks: {e}")
            raise

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        self.log_operation_start("Schema creation", f"benchmark: {benchmark.__class__.__name__}")

        enable_primary_keys, enable_foreign_keys = self._get_constraint_configuration()
        self._log_constraint_configuration(enable_primary_keys, enable_foreign_keys)
        self.log_verbose(
            f"Schema constraints - Primary keys: {enable_primary_keys}, Foreign keys: {enable_foreign_keys}"
        )

        try:
            cursor = connection.cursor()

            if self.create_catalog:
                cursor.execute(f"CREATE CATALOG IF NOT EXISTS {self.catalog}")
                cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.{self.schema}")
                self.log_verbose(f"Created catalog and schema: {self.catalog}.{self.schema}")
                self.create_catalog = False
            else:
                cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.{self.schema}")
                self.log_verbose(f"Created schema: {self.catalog}.{self.schema}")

            cursor.execute(f"USE CATALOG {self.catalog}")
            cursor.execute(f"USE SCHEMA {self.schema}")
            self.log_very_verbose(f"Set schema context to: {self.catalog}.{self.schema}")

            schema_sql = self._create_schema_with_tuning(benchmark, source_dialect=self.get_target_dialect())

            self.log_verbose(f"Received schema SQL from _create_schema_with_tuning: {len(schema_sql)} characters")
            self.log_very_verbose(f"Schema SQL (first 300 chars): {schema_sql[:300]}")

            if not schema_sql or not schema_sql.strip():
                self.logger.error(f"Schema SQL is empty! Benchmark class: {benchmark.__class__.__name__}")
                self.logger.error(f"Benchmark has get_schema_sql: {hasattr(benchmark, 'get_schema_sql')}")
                raise RuntimeError(f"No schema SQL generated for {benchmark.__class__.__name__}")

            statements = translate_ddl(
                schema_sql,
                self.get_target_dialect(),
                schema_ddl_read_dialect(schema_sql, self.get_target_dialect()),
                **self._schema_ddl_options(schema_sql),
            )

            self.log_verbose(f"Parsed {len(statements)} CREATE TABLE statements from schema SQL")
            if not statements:
                self.logger.error("No CREATE TABLE statements found after parsing schema SQL")
                self.logger.error(f"Raw schema SQL (first 500 chars): {schema_sql[:500]}")
                raise RuntimeError("Schema SQL produced no executable statements")

            tables_created, failed_tables = self._execute_schema_statements(statements, cursor)

            duration = elapsed_seconds(start_time)
            self.log_operation_complete("Schema creation", duration, f"{tables_created} Delta Lake tables created")

            return duration

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise
        finally:
            if "cursor" in locals():
                cursor.close()

    def _ensure_uc_volume_exists(self, uc_volume_path: str, connection: Any) -> None:
        volume_path = uc_volume_path.replace("dbfs:", "").rstrip("/")

        if not volume_path.startswith("/Volumes/"):
            raise ValueError(f"Invalid UC Volume path: {uc_volume_path}. Must start with dbfs:/Volumes/")

        path_parts = volume_path.split("/")

        if len(path_parts) < 5:
            raise ValueError(
                f"Invalid UC Volume path: {uc_volume_path}. "
                "Expected dbfs:/Volumes/catalog/schema/volume (optionally with a subpath)."
            )

        catalog = path_parts[2]
        schema = path_parts[3]
        volume = path_parts[4]

        self.log_verbose(f"Ensuring UC Volume exists: {catalog}.{schema}.{volume}")

        try:
            cursor = connection.cursor()

            try:
                create_schema_sql = f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}"
                cursor.execute(create_schema_sql)
                self.log_very_verbose(f"Schema ready: {catalog}.{schema}")
            except Exception as schema_error:
                error_msg = str(schema_error).lower()
                if "permission" in error_msg or "access denied" in error_msg or "unauthorized" in error_msg:
                    raise ValueError(
                        f"Permission denied creating schema: {catalog}.{schema}. "
                        f"Ensure you have CREATE SCHEMA permission on catalog {catalog}. "
                        f"Or create it manually: CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}"
                    ) from None
                raise

            create_volume_sql = f"CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.{volume}"
            cursor.execute(create_volume_sql)

            self.log_verbose(f"✅ UC Volume ready: {catalog}.{schema}.{volume}")
            cursor.close()

        except ValueError:
            raise
        except Exception as e:
            error_msg = str(e).lower()

            if "permission" in error_msg or "access denied" in error_msg or "unauthorized" in error_msg:
                raise ValueError(
                    f"Permission denied creating UC Volume: {catalog}.{schema}.{volume}. "
                    f"Ensure you have CREATE VOLUME permission on schema {catalog}.{schema}. "
                    f"Or create it manually: CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.{volume}"
                ) from e

            raise ValueError(
                f"Failed to create UC Volume {catalog}.{schema}.{volume}: {e}. "
                f"Try creating manually: CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.{volume}"
            ) from e

    def _upload_to_uc_volume(
        self,
        data_files: dict[str, Any],
        uc_volume_path: str,
        data_dir: Path,
        force_upload: bool = False,
    ) -> dict[str, str]:
        try:
            from databricks.sdk import WorkspaceClient
        except ImportError:
            raise ImportError(
                get_package_install_message("databricks-sdk", "databricks-sdk required for UC Volume uploads.")
            ) from None

        workspace = WorkspaceClient(
            host=f"https://{self.server_hostname}",
            token=self.access_token,
        )

        volume_path = uc_volume_path.replace("dbfs:", "")

        from benchbox.utils.cloud_storage import DatabricksPath

        if isinstance(data_dir, DatabricksPath):
            self.log_very_verbose(f"Using DatabricksPath local component: {data_dir._path}")

        manifest_path = self._resolve_uc_manifest_path(data_dir)

        reuse_result = self._try_reuse_uc_volume_data(uc_volume_path, manifest_path, force_upload)
        if reuse_result is not None:
            return reuse_result

        if manifest_path.exists():
            try:
                self._upload_manifest_to_uc_volume(manifest_path, uc_volume_path, workspace)
            except Exception as e:
                self.logger.warning(f"Failed to upload manifest to UC Volume: {e}")

        upload_root = self._resolve_local_upload_root(data_dir)
        uploaded_files: dict[str, Any] = {}
        for table_name, file_path in data_files.items():
            local_paths = self._collect_local_paths_for_table(table_name, file_path)
            if not local_paths:
                continue
            upload_entries = self._build_uc_upload_entries(local_paths, upload_root)
            result = self._upload_table_to_uc(
                table_name, local_paths, upload_entries, volume_path, uc_volume_path, workspace, upload_root
            )
            if result is not None:
                uploaded_files[table_name] = result

        if manifest_path.exists():
            try:
                self._upload_manifest_to_uc_volume(manifest_path, uc_volume_path, workspace)
            except Exception as e:
                self.logger.warning(f"Failed to upload manifest to UC Volume: {e}")

        return uploaded_files

    def _collect_local_paths_for_table(self, table_name: str, file_path: Any) -> list[Path]:
        local_paths: list[Path] = []
        for candidate in self._normalize_table_file_inputs(file_path):
            local_path = Path(candidate) if not isinstance(candidate, Path) else candidate
            if not local_path.is_absolute():
                local_path = local_path.resolve()
            if not local_path.exists():
                self.logger.error(f"File not found for table {table_name}: {local_path}")
                self.logger.error(f"  Checked path: {local_path.absolute()}")
                self.logger.error(f"  CWD: {Path.cwd()}")
                continue
            self.log_very_verbose(f"Found {local_path.name} ({local_path.stat().st_size:,} bytes) at {local_path}")
            local_paths.append(local_path)
        return local_paths

    def _upload_table_to_uc(
        self,
        table_name: str,
        local_paths: list[Path],
        upload_entries: list[tuple[Path, str]],
        volume_path: str,
        uc_volume_path: str,
        workspace: Any,
        upload_root: Path,
    ) -> Any:
        if len(local_paths) == 1:
            return self._upload_single_table_path(
                table_name, local_paths[0], upload_entries[0][1], volume_path, uc_volume_path, workspace, upload_root
            )
        return self._upload_multi_table_paths(table_name, upload_entries, volume_path, uc_volume_path, workspace)

    def _upload_single_table_path(
        self,
        table_name: str,
        local_path: Path,
        remote_path: str,
        volume_path: str,
        uc_volume_path: str,
        workspace: Any,
        upload_root: Path,
    ) -> Any:
        is_sharded, _pattern, chunk_files = self._detect_sharded_files(local_path, table_name)
        if is_sharded and chunk_files:
            sharded_entries = self._build_uc_upload_entries(chunk_files, upload_root)
            sharded_targets = [rp for _lp, rp in sharded_entries]
            self._upload_sharded_files(
                chunk_files, volume_path, uc_volume_path, workspace, remote_paths=sharded_targets
            )
            wildcard = self._detect_manifest_wildcard(sharded_targets)
            if wildcard:
                self.log_verbose(
                    f"Uploaded {len(chunk_files)} chunks for {table_name} (shard pattern {wildcard}; loading per-file)"
                )
            return [self._join_uri_path(f"dbfs:{volume_path}", rp) for rp in sharded_targets]
        return self._upload_single_file(local_path, volume_path, uc_volume_path, workspace, remote_path=remote_path)

    def _upload_multi_table_paths(
        self,
        table_name: str,
        upload_entries: list[tuple[Path, str]],
        volume_path: str,
        uc_volume_path: str,
        workspace: Any,
    ) -> Any:
        wildcard = self._detect_manifest_wildcard([rp for _lp, rp in upload_entries])
        uploaded_uris: list[str] = []
        for local_path, remote_path in upload_entries:
            uri = self._upload_single_file(local_path, volume_path, uc_volume_path, workspace, remote_path=remote_path)
            if uri is not None:
                uploaded_uris.append(uri)
        if wildcard:
            self.log_verbose(
                f"Uploaded {len(uploaded_uris)} files for {table_name} (shard pattern {wildcard}; loading per-file)"
            )
        return uploaded_uris or None

    def _resolve_uc_manifest_path(self, data_dir: Path) -> Path:
        try:
            from benchbox.utils.cloud_storage import DatabricksPath
        except Exception:
            DatabricksPath = None

        if DatabricksPath is not None and isinstance(data_dir, DatabricksPath):
            return data_dir._path / MANIFEST_FILENAME
        else:
            return Path(data_dir) / MANIFEST_FILENAME

    def _resolve_local_upload_root(self, data_dir: Path) -> Path:
        try:
            from benchbox.utils.cloud_storage import DatabricksPath
        except Exception:
            DatabricksPath = None

        if DatabricksPath is not None and isinstance(data_dir, DatabricksPath):
            return data_dir._path.resolve()
        return Path(data_dir).resolve()

    @staticmethod
    def _build_uc_upload_entries(local_paths: list[Path], upload_root: Path) -> list[tuple[Path, str]]:
        all_under_root = all(path.is_relative_to(upload_root) for path in local_paths)

        if all_under_root:
            root = upload_root
        else:
            root = Path(os.path.commonpath([str(path.parent) for path in local_paths]))

        entries = [(path, path.relative_to(root).as_posix()) for path in local_paths]

        seen_targets: set[str] = set()
        for _local_path, remote_path in entries:
            if remote_path in seen_targets:
                raise ValueError(f"UC Volume upload would overwrite duplicate remote path '{remote_path}'")
            seen_targets.add(remote_path)

        return entries

    @staticmethod
    def _join_uri_path(root: str, relative_path: str) -> str:
        cleaned = relative_path.lstrip("/")
        if not cleaned:
            return root.rstrip("/")
        return f"{root.rstrip('/')}/{cleaned}"

    @staticmethod
    def _split_remote_path(path_like: Any) -> tuple[str, PurePosixPath] | None:
        raw = str(path_like).rstrip("/")
        if raw.startswith("dbfs:/"):
            suffix = raw[len("dbfs:/") :].lstrip("/")
            return "dbfs:/", PurePosixPath("/") / suffix if suffix else PurePosixPath("/")

        parsed = urlparse(raw)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}", PurePosixPath(parsed.path or "/")

        return None

    def _common_remote_directory(self, file_paths: list[Any]) -> str | None:
        raw_split_paths = [self._split_remote_path(path_like) for path_like in file_paths]
        if not raw_split_paths or any(item is None for item in raw_split_paths):
            return None

        split_paths = cast(list[tuple[str, PurePosixPath]], raw_split_paths)
        anchors = {anchor for anchor, _path in split_paths}
        if len(anchors) != 1:
            return None

        anchor = split_paths[0][0]
        parent_parts = [path.parent.parts for _anchor, path in split_paths]

        common_parts: list[str] = []
        for segments in zip(*parent_parts):
            if all(segment == segments[0] for segment in segments):
                common_parts.append(segments[0])
            else:
                break

        if not common_parts:
            return anchor

        common_path = PurePosixPath(*common_parts).as_posix().lstrip("/")
        if anchor == "dbfs:/":
            return f"dbfs:/{common_path}" if common_path else "dbfs:/"
        return f"{anchor}/{common_path}" if common_path else anchor

    def _try_reuse_uc_volume_data(
        self, uc_volume_path: str, manifest_path: Path, force_upload: bool
    ) -> dict[str, Any] | None:
        if force_upload or not manifest_path.exists():
            return None

        validation_engine = UploadValidationEngine()
        verbose = getattr(self, "very_verbose", False)

        should_upload, validation_result = validation_engine.should_upload_data(
            remote_path=uc_volume_path,
            local_manifest_path=manifest_path,
            force_upload=force_upload,
            verbose=verbose,
        )

        if not should_upload:
            remote_manifest = validation_result.remote_manifest
            if remote_manifest:
                self.log_verbose("Reusing existing data from UC Volume (validation passed)")
                return self._get_remote_file_uris_from_manifest(uc_volume_path, remote_manifest)
            else:
                self.log_verbose("Pre-upload validation passed but remote manifest unavailable, proceeding with upload")

        return None

    def _detect_sharded_files(self, local_path: Path, table_name: str) -> tuple[bool, str, list[Path]]:
        filename = local_path.name
        parts = filename.split(".")

        is_sharded = False
        chunk_files: list[Path] = []
        pattern = ""

        compression_exts_nodot = {ext.lstrip(".") for ext in COMPRESSION_EXTENSIONS}

        if len(parts) >= 3:
            if len(parts) >= 4 and parts[-1] in compression_exts_nodot and parts[-2].isdigit():
                is_sharded = True
                base_parts = parts[:-2]
                compression = parts[-1]
                pattern = f"{'.'.join(base_parts)}.*.{compression}"
            elif parts[-1].isdigit():
                is_sharded = True
                base_parts = parts[:-1]
                pattern = f"{'.'.join(base_parts)}.*"

            if is_sharded:
                parent_dir = local_path.parent
                chunk_files = sorted([f for f in parent_dir.glob(pattern) if f.is_file()])
                if chunk_files:
                    self.log_verbose(f"Found {len(chunk_files)} chunk files for {table_name}: {pattern}")

        return is_sharded, pattern, chunk_files

    def _upload_file_content_to_uc(
        self, file_path: Path, target_path: str, uc_volume_path: str, workspace: Any
    ) -> None:
        from io import BytesIO

        expected_size = file_path.stat().st_size
        with open(file_path, "rb") as f:
            content = f.read()

        if len(content) == 0:
            self.logger.error(f"Read 0 bytes from {file_path} (expected {expected_size})")
            raise RuntimeError(f"Failed to read content from {file_path}")

        if len(content) != expected_size:
            self.logger.warning(f"Size mismatch for {file_path.name}: stat={expected_size}, read={len(content)}")

        workspace.files.upload(target_path, BytesIO(content), overwrite=True)
        self.log_very_verbose(f"Successfully uploaded {file_path.name} ({len(content):,} bytes)")

    def _upload_sharded_files(
        self,
        chunk_files: list[Path],
        volume_path: str,
        uc_volume_path: str,
        workspace: Any,
        remote_paths: list[str] | None = None,
    ) -> None:
        if remote_paths is not None and len(remote_paths) != len(chunk_files):
            raise ValueError("remote_paths length must match chunk_files length")

        for index, chunk_file in enumerate(chunk_files):
            if not chunk_file.exists():
                self.logger.error(f"Chunk file disappeared: {chunk_file}")
                continue

            chunk_size = chunk_file.stat().st_size
            if chunk_size == 0:
                self.logger.warning(f"Skipping empty chunk file: {chunk_file.name}")
                continue

            target_relative = remote_paths[index] if remote_paths is not None else chunk_file.name
            target_path = self._join_uri_path(volume_path, target_relative)
            self.log_very_verbose(f"Uploading {chunk_file.name} ({chunk_size:,} bytes) to {target_path}")

            try:
                self._upload_file_content_to_uc(chunk_file, target_path, uc_volume_path, workspace)
            except Exception as e:
                self.logger.error(f"Failed to upload {chunk_file.name} to UC Volume: {e}")
                raise RuntimeError(f"Failed to upload {chunk_file.name} to {uc_volume_path}: {e}") from e

    def _upload_single_file(
        self,
        local_path: Path,
        volume_path: str,
        uc_volume_path: str,
        workspace: Any,
        remote_path: str | None = None,
    ) -> str | None:
        single_file_size = local_path.stat().st_size
        if single_file_size == 0:
            self.logger.warning(f"Skipping empty file: {local_path.name}")
            return None

        target_relative = remote_path or local_path.name
        target_path = self._join_uri_path(volume_path, target_relative)
        self.log_verbose(f"Uploading {local_path.name} ({single_file_size:,} bytes) to {target_path}")

        try:
            self._upload_file_content_to_uc(local_path, target_path, uc_volume_path, workspace)
            return f"dbfs:{target_path}"
        except Exception as e:
            self.logger.error(f"Failed to upload {local_path.name} to UC Volume: {e}")
            raise RuntimeError(f"Failed to upload {local_path.name} to {uc_volume_path}: {e}") from e

    def _upload_manifest_to_uc_volume(self, manifest_path: Path, uc_volume_path: str, workspace: Any) -> None:
        try:
            target_path = uc_volume_path.replace("dbfs:", "")
            if not target_path.endswith("/" + MANIFEST_FILENAME):
                target_path = target_path.rstrip("/") + "/" + MANIFEST_FILENAME

            with open(manifest_path, "rb") as fh:
                content = fh.read()
            from io import BytesIO

            workspace.files.upload(target_path, BytesIO(content), overwrite=True)
            try:
                manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
                tables = manifest.get("tables") or {}
                self.logger.info(f"Uploaded manifest to {uc_volume_path} ({len(content)} bytes, {len(tables)} tables)")
            except Exception:
                self.logger.info(f"Uploaded manifest to {uc_volume_path}")
        except Exception as e:
            raise RuntimeError(f"Manifest upload failed: {e}") from e

    def _get_remote_file_uris_from_manifest(self, uc_volume_path: str, remote_manifest: dict) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        tables = remote_manifest.get("tables") or {}
        for table, entries in tables.items():
            if not entries:
                continue
            if len(entries) == 1:
                rel = entries[0].get("path")
                if rel:
                    mapping[table] = self._join_uri_path(uc_volume_path.rstrip("/"), str(rel))
                continue
            names = [str(e.get("path")) for e in entries if e.get("path")]
            if not names:
                continue
            mapping[table] = [self._join_uri_path(uc_volume_path.rstrip("/"), name) for name in names]
        return mapping

    @staticmethod
    def _is_manifest_shard_name(name: str) -> bool:
        parts = Path(name).name.split(".")
        compression_exts_nodot = {ext.lstrip(".") for ext in COMPRESSION_EXTENSIONS}
        if len(parts) >= 4 and parts[-1] in compression_exts_nodot and parts[-2].isdigit():
            return True
        if len(parts) >= 2 and parts[-1].isdigit():
            return True
        return DatabricksAdapter._underscore_chunk_base(Path(name).name) is not None

    @staticmethod
    def _underscore_chunk_base(filename: str) -> tuple[str, str] | None:
        import re

        stem = Path(filename).name
        suffixes = "".join(Path(filename).suffixes)
        stem_no_ext = stem[: -len(suffixes)] if suffixes else stem
        match = re.fullmatch(r"(.+)_(\d+)_(\d+)", stem_no_ext)
        if not match:
            return None
        return match.group(1), suffixes

    @staticmethod
    def _manifest_pattern_for_name(name: str) -> tuple[str, str]:
        parts = name.split(".")
        if len(parts) >= 3 and parts[-2].isdigit():
            return ".".join(parts[:-2]), "." + parts[-1]
        if len(parts) >= 2 and parts[-1].isdigit():
            return ".".join(parts[:-1]), ""
        underscore = DatabricksAdapter._underscore_chunk_base(Path(name).name)
        if underscore is not None:
            return underscore
        stem = Path(name).stem
        return stem, Path(name).suffix

    def _detect_manifest_wildcard(self, names: list[str]) -> str | None:
        if not names or not all(self._is_manifest_shard_name(name) for name in names):
            return None
        base0, ext0 = self._manifest_pattern_for_name(names[0])
        for name in names[1:]:
            base, ext = self._manifest_pattern_for_name(name)
            if base != base0 or ext != ext0:
                return None
        return f"{base0}.*{ext0}"

    @staticmethod
    def _normalize_table_file_inputs(file_paths: Any) -> list[Any]:
        if isinstance(file_paths, (str, Path)):
            return [file_paths]
        return list(file_paths)

    @staticmethod
    def _path_name(path_like: Any) -> str:
        import os

        path_str = str(path_like).rstrip("/")
        if "://" in path_str or path_str.startswith("dbfs:/"):
            return path_str.split("/")[-1]
        return os.path.basename(path_str)

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        if self.table_format == "hudi":
            raise ValueError(
                "Managed data loads are not supported for Databricks Hudi tables: "
                "COPY INTO targets Delta tables only. Create the schema with "
                "table_format='hudi' and load it through a Hudi-aware Spark job instead."
            )
        start_time = mono_time()
        self.log_operation_start("Data loading", f"benchmark: {benchmark.__class__.__name__}")
        self.log_very_verbose(f"Data directory: {data_dir}")

        table_stats = {}
        per_table_timings = {}
        cursor = connection.cursor()

        try:
            from benchbox.platforms.base.data_loading import DataSource

            data_source = self._resolve_databricks_data_files(benchmark, data_dir)
            if not isinstance(data_source, DataSource):
                data_source = DataSource(source_type="legacy_test_mapping", tables=data_source)
            stage_root = self._resolve_stage_root(data_dir)
            data_source.tables = self._maybe_upload_to_uc_volume(data_source.tables, stage_root, data_dir, connection)

            cursor.execute(f"USE CATALOG {self.catalog}")
            cursor.execute(f"USE SCHEMA {self.schema}")
            self.log_verbose(f"Set schema context for data loading: {self.catalog}.{self.schema}")

            cursor.execute(f"SHOW TABLES IN {self.catalog}.{self.schema}")
            existing_tables = {row[1].lower() for row in cursor.fetchall()}
            self.log_very_verbose(f"Found {len(existing_tables)} existing tables in {self.catalog}.{self.schema}")

            for table_name, file_path in data_source.tables.items():
                try:
                    load_start = mono_time()
                    row_count, copy_time, optimize_time = self._load_single_table(
                        cursor,
                        connection,
                        benchmark,
                        table_name,
                        file_path,
                        stage_root,
                        existing_tables,
                        data_source,
                    )
                    table_stats[table_name.lower()] = row_count
                    load_time = elapsed_seconds(load_start)

                    per_table_timings[table_name.upper()] = {
                        "copy_into_ms": copy_time * 1000,
                        "optimize_ms": optimize_time * 1000,
                        "total_ms": load_time * 1000,
                        "rows": row_count,
                    }

                    self.logger.info(f"✅ Loaded {row_count:,} rows into {table_name.upper()} in {load_time:.2f}s")

                except Exception as e:
                    self.logger.error(f"Failed to load {table_name}: {str(e)[:200]}")
                    table_stats[table_name.lower()] = 0
                    per_table_timings[table_name.upper()] = {
                        "copy_into_ms": 0,
                        "optimize_ms": 0,
                        "total_ms": 0,
                        "rows": 0,
                    }

            total_time = elapsed_seconds(start_time)
            total_rows = sum(table_stats.values())
            self.log_operation_complete(
                "Data loading", total_time, f"{total_rows:,} total rows, {len(table_stats)} tables"
            )

        finally:
            cursor.close()

        return table_stats, total_time, per_table_timings

    def _resolve_databricks_data_files(self, benchmark, data_dir: Path):
        from benchbox.platforms.base.data_loading import DataSource, DataSourceResolver

        resolver = DataSourceResolver(
            platform_name=self.platform_name,
            table_mode=self.table_mode,
            platform_config=self.platform_config,
            requested_format=self.requested_table_format,
        )
        data_source = resolver.resolve(benchmark, data_dir)
        if not data_source or not data_source.tables:
            raise ValueError("No data files found. Ensure benchmark.generate_data() was called first.")

        normalized: dict[str, Any] = {}
        for table_name, table_files in data_source.tables.items():
            candidates = self._normalize_table_file_inputs(table_files)
            normalized[table_name] = candidates[0] if len(candidates) == 1 else candidates
        return DataSource(
            source_type=data_source.source_type,
            tables=normalized,
            table_formats=dict(data_source.table_formats),
            table_metadata=dict(data_source.table_metadata),
        )

    @staticmethod
    def _is_cloud_uri(s: str) -> bool:
        return s.startswith(("s3://", "gs://", "abfss://", "dbfs:/"))

    def _resolve_stage_root(self, data_dir: Path) -> str:
        from benchbox.utils.cloud_storage import DatabricksPath

        stage_root = None

        if isinstance(data_dir, DatabricksPath) and hasattr(data_dir, "dbfs_target") and data_dir.dbfs_target:
            stage_root = data_dir.dbfs_target.rstrip("/")
            self.log_verbose(f"Using DatabricksPath dbfs_target: {stage_root}")
        elif isinstance(self.staging_root, str) and self._is_cloud_uri(self.staging_root):
            stage_root = self.staging_root.rstrip("/")
        else:
            if self.uc_catalog and self.uc_schema and self.uc_volume:
                stage_root = f"dbfs:/Volumes/{self.uc_catalog}/{self.uc_schema}/{self.uc_volume}".rstrip("/")
            else:
                data_dir_str = str(data_dir)
                if self._is_cloud_uri(data_dir_str):
                    stage_root = data_dir_str.rstrip("/")

        if not stage_root:
            raise ValueError(
                "Databricks data loading requires a cloud/UC Volume staging location. "
                "Add --output flag with cloud path `dbfs:/`; `s3://`, `gs://`, `abfss://`."
            )
        return stage_root

    def _maybe_upload_to_uc_volume(self, data_files: dict, stage_root: str, data_dir: Path, connection: Any) -> dict:
        from benchbox.utils.cloud_storage import DatabricksPath

        data_is_local = isinstance(data_dir, DatabricksPath) or not self._is_cloud_uri(str(data_dir))

        def _is_complete_uc_volume_path(p: str) -> bool:
            v = p.replace("dbfs:", "").rstrip("/")
            if not v.startswith("/Volumes/"):
                return False
            parts = v.split("/")
            return len(parts) >= 5

        if data_is_local and stage_root.startswith("dbfs:/Volumes/") and _is_complete_uc_volume_path(stage_root):
            self.log_verbose(f"Uploading local data to UC Volume: {stage_root}")
            self._ensure_uc_volume_exists(stage_root, connection)
            force_upload = getattr(self, "force_upload", False)
            original_files = dict(data_files)
            uploaded_files = self._upload_to_uc_volume(
                data_files,
                stage_root,
                data_dir,
                force_upload=force_upload,
            )
            data_files = uploaded_files if uploaded_files else original_files
            self.log_verbose("Upload to UC Volume completed")

        return data_files

    def _resolve_file_uri_and_delimiter(
        self,
        file_path,
        stage_root: str,
        *,
        table_name: str | None = None,
        data_source: Any | None = None,
        benchmark: Any | None = None,
    ) -> tuple[str, str, str]:
        if isinstance(file_path, list):
            if not file_path:
                raise ValueError("No data files were provided for Databricks COPY INTO")
            if len(file_path) == 1:
                file_path = file_path[0]
            else:
                common_dir = None
                wildcard = self._detect_manifest_wildcard([self._path_name(path_like) for path_like in file_path])
                if wildcard:
                    first_path = str(file_path[0])
                    if first_path.startswith("dbfs:/Volumes/"):
                        file_uri = f"{first_path.rsplit('/', 1)[0]}/{wildcard}"
                    else:
                        file_uri = f"{stage_root}/{wildcard}"
                    filename = wildcard
                else:
                    common_dir = self._common_remote_directory(file_path)
                    if common_dir is None:
                        raise ValueError(
                            "Databricks COPY INTO requires a single file, a remote directory, or a "
                            "shard-compatible file set per table."
                        )
                    file_uri = common_dir
                    filename = common_dir.rstrip("/").split("/")[-1]
                filename_for_format = (
                    self._path_name(file_path[0]) if common_dir is not None else filename.replace(".*", "")
                )
                dialect_path = Path(self._path_name(file_path[0]))
                delimiter = self._resolve_csv_delimiter(
                    data_source, table_name or dialect_path.stem, dialect_path, benchmark
                )
                return file_uri, filename, delimiter

        if isinstance(file_path, str) and file_path.startswith("dbfs:/Volumes/"):
            file_uri = file_path
            uri_path = file_path.replace("dbfs:", "")
            filename = uri_path.split("/")[-1]
        else:
            filename = self._path_name(file_path)
            file_uri = f"{stage_root}/{filename}"

        filename_for_format = filename.replace(".*", "")
        dialect_path = Path(filename_for_format)
        delimiter = self._resolve_csv_delimiter(data_source, table_name or dialect_path.stem, dialect_path, benchmark)
        return file_uri, filename, delimiter

    def _expand_copy_sources(self, file_path: Any, stage_root: str, file_uri: str) -> list[str]:
        entries = self._normalize_table_file_inputs(file_path)
        if len(entries) > 1 and "*" not in file_uri:
            sources = []
            for entry in entries:
                entry_str = str(entry)
                if entry_str.startswith("dbfs:/") or self._is_cloud_uri(entry_str):
                    sources.append(entry_str)
                else:
                    sources.append(f"{stage_root}/{self._path_name(entry)}")

            source_parents = {source.rsplit("/", 1)[0] for source in sources}
            is_partition_directory = any("=" in segment for segment in file_uri.rstrip("/").split("/"))
            if source_parents == {file_uri.rstrip("/")} and not is_partition_directory:
                return sources

        if "*" not in file_uri:
            return [file_uri]
        sources = []
        for entry in entries:
            entry_str = str(entry)
            if "*" in entry_str:
                continue
            if entry_str.startswith("dbfs:/Volumes/"):
                sources.append(entry_str)
            else:
                sources.append(f"{stage_root}/{self._path_name(entry)}")
        if not sources:
            raise ValueError(
                "Databricks COPY INTO does not accept glob patterns: "
                f"no expandable shard files for source '{file_uri}'. "
                "Upload paths must resolve to exact file URIs."
            )
        return sources

    def _resolve_csv_delimiter(self, data_source: Any, table_name: str, file_path: Path, benchmark: Any | None) -> str:
        from benchbox.platforms.base.data_loading import NO_BENCHMARK, DataSource, resolve_csv_dialect

        dialect_source = data_source or DataSource(source_type="databricks_copy_into", tables={})
        return resolve_csv_dialect(
            dialect_source, table_name, file_path, benchmark if benchmark is not None else NO_BENCHMARK
        ).delimiter

    def _resolve_copy_dialect(self, data_source: Any, table_name: str, file_path: Path, benchmark: Any | None):
        from benchbox.platforms.base.data_loading import NO_BENCHMARK, DataSource, resolve_csv_dialect

        dialect_source = data_source or DataSource(source_type="databricks_copy_into", tables={})
        return resolve_csv_dialect(
            dialect_source, table_name, file_path, benchmark if benchmark is not None else NO_BENCHMARK
        )

    def _resolve_csv_null_marker(
        self, data_source: Any, table_name: str, file_path: Path, benchmark: Any | None
    ) -> str | None:
        return self._resolve_copy_dialect(data_source, table_name, file_path, benchmark).null_marker

    def _get_column_list_for_table(self, benchmark, table_name: str, cursor: Any | None = None) -> str:
        table_name_upper = table_name.upper()
        columns = self._schema_columns_for_table(benchmark, table_name)
        if not columns and cursor is not None:
            columns = self._describe_table_columns(cursor, table_name_upper)
        if not columns:
            self.logger.warning(
                f"No column list resolved for {table_name_upper}; COPY INTO will map CSV fields by position"
            )
            return ""
        self.log_very_verbose(f"Using explicit column mapping for {table_name_upper}: {len(columns)} columns")
        return f" ({', '.join(columns)})"

    def _schema_columns_for_table(self, benchmark, table_name: str) -> list[str]:
        if not hasattr(benchmark, "get_schema"):
            return []
        try:
            schema = benchmark.get_schema()
        except Exception as e:
            self.logger.warning(f"Could not read benchmark schema for {table_name}: {e}")
            return []
        if not isinstance(schema, dict):
            return []
        table_schema = schema.get(table_name.lower()) or schema.get(table_name)
        if table_schema is None:
            return []
        raw_columns = (
            table_schema.get("columns") if isinstance(table_schema, dict) else getattr(table_schema, "columns", None)
        )
        names = []
        for col in raw_columns or []:
            name = col.get("name") if isinstance(col, dict) else getattr(col, "name", None)
            if name:
                names.append(str(name))
        return names

    def _describe_table_columns(self, cursor: Any, table_name_upper: str) -> list[str]:
        try:
            cursor.execute(f"DESCRIBE TABLE {table_name_upper}")
            rows = list(cursor.fetchall() or [])
        except Exception as e:
            self.logger.warning(f"DESCRIBE TABLE {table_name_upper} failed while resolving COPY columns: {e}")
            return []
        names = []
        for row in rows:
            col = str(row[0]).strip() if len(row) > 0 and row[0] is not None else ""
            if not col or col.startswith("#"):
                break
            names.append(col)
        return names

    def _target_cast_select(self, cursor: Any, table_name_upper: str) -> str:
        cursor.execute(f"DESCRIBE TABLE {table_name_upper}")
        items = []
        for row in cursor.fetchall():
            col = str(row[0]) if len(row) > 0 else ""
            dtype = str(row[1]) if len(row) > 1 else ""
            if not col or col.startswith("#") or not dtype or dtype.startswith("#"):
                continue
            items.append(f"CAST(`{col}` AS {dtype}) AS `{col}`")
        if not items:
            raise RuntimeError(f"DESCRIBE TABLE {table_name_upper} returned no columns for cast SELECT")
        return ", ".join(items)

    def _parquet_cast_select(self, cursor: Any, table_name_upper: str) -> str:
        return self._target_cast_select(cursor, table_name_upper)

    def _load_single_table(
        self,
        cursor,
        connection,
        benchmark,
        table_name: str,
        file_path,
        stage_root: str,
        existing_tables: set[str],
        data_source: Any | None = None,
    ) -> tuple[int, float, float]:
        table_name_upper = table_name.upper()

        if table_name.lower() not in existing_tables:
            self.logger.error(f"Table {table_name_upper} not found in schema {self.catalog}.{self.schema}")
            self.logger.error(f"Available tables: {sorted(existing_tables)}")
            raise RuntimeError(
                f"Table {table_name_upper} does not exist in {self.catalog}.{self.schema}. "
                f"Ensure schema creation completed successfully before loading data."
            )

        file_uri, filename, delimiter = self._resolve_file_uri_and_delimiter(
            file_path,
            stage_root,
            table_name=table_name,
            data_source=data_source,
            benchmark=benchmark,
        )
        if isinstance(file_path, list) and file_path:
            null_dialect_path = Path(self._path_name(file_path[0]))
        else:
            null_dialect_path = Path(filename.replace(".*", ""))
        null_marker = self._resolve_csv_null_marker(
            data_source, table_name or null_dialect_path.stem, null_dialect_path, benchmark
        )
        copy_dialect = self._resolve_copy_dialect(
            data_source, table_name or null_dialect_path.stem, null_dialect_path, benchmark
        )
        header_opt = "true" if copy_dialect.has_header else "false"
        format_options = f"'delimiter'='{delimiter}', 'header'='{header_opt}'"
        if null_marker:
            sentinel = null_marker.replace("'", "''")
            format_options += f", 'nullValue'='{sentinel}'"

        copy_sources = self._expand_copy_sources(file_path, stage_root, file_uri)
        if len(copy_sources) > 1:
            self.log_verbose(f"Loading {table_name_upper} from {len(copy_sources)} shard files")

        is_parquet = copy_sources[0].lower().split("?")[0].endswith(".parquet") if copy_sources else False
        use_cast_select = is_parquet or copy_dialect.has_header
        cast_select = ""
        column_list = ""
        if use_cast_select:
            self.log_very_verbose(f"Using cast SELECT load for {table_name_upper}")
            cast_select = self._target_cast_select(cursor, table_name_upper)
        else:
            column_list = self._get_column_list_for_table(benchmark, table_name, cursor)

        copy_options = " COPY_OPTIONS('force' = 'true')"
        copy_time = 0.0
        for source_uri in copy_sources:
            if is_parquet:
                copy_sql = (
                    f"COPY INTO {table_name_upper} FROM (SELECT {cast_select} FROM '{source_uri}') FILEFORMAT = PARQUET"
                    f"{copy_options}"
                )
            elif copy_dialect.has_header:
                copy_sql = (
                    f"COPY INTO {table_name_upper} FROM (SELECT {cast_select} FROM '{source_uri}') "
                    f"FILEFORMAT = CSV FORMAT_OPTIONS({format_options}){copy_options}"
                )
            else:
                copy_sql = (
                    f"COPY INTO {table_name_upper}{column_list} FROM '{source_uri}' "
                    f"FILEFORMAT = CSV FORMAT_OPTIONS({format_options}){copy_options}"
                )
            copy_start = mono_time()
            cursor.execute(copy_sql)
            copy_time += elapsed_seconds(copy_start)

        cursor.execute(f"SELECT COUNT(*) FROM {table_name_upper}")
        row_count = cursor.fetchone()[0]

        effective_tuning = self.get_effective_tuning_configuration()
        if effective_tuning is not None:
            self.apply_ctas_sort(table_name_upper, effective_tuning, connection)
            self.run_post_load_tunings(table_name_upper, effective_tuning, connection)

        optimize_time = 0.0
        already_optimized = table_name_upper.lower() in self._delta_optimized_after_load()
        self._delta_optimized_after_load().discard(table_name_upper.lower())
        if self.table_format == "hudi":
            if self.enable_delta_optimization:
                self._record_layout_operation(
                    mechanism="optimize",
                    table=table_name_upper,
                    statement=f"OPTIMIZE {table_name_upper}",
                    status="skipped",
                    phase="post_load",
                )
                self.logger.info(f"Skipped Delta-only OPTIMIZE for Hudi table {table_name_upper}")
        elif self.enable_delta_optimization and not already_optimized:
            optimize_start = mono_time()
            optimize_statement = f"OPTIMIZE {table_name_upper}"
            try:
                cursor.execute(optimize_statement)
                self._record_layout_operation(
                    mechanism="optimize",
                    table=table_name_upper,
                    statement=optimize_statement,
                    status="applied",
                    phase="post_load",
                )
            except Exception as e:
                self._record_layout_operation(
                    mechanism="optimize",
                    table=table_name_upper,
                    statement=optimize_statement,
                    status="skipped",
                    phase="post_load",
                    error=e,
                )
            optimize_time = elapsed_seconds(optimize_start)

        return row_count, copy_time, optimize_time

    def validate_external_table_requirements(self) -> None:
        has_explicit_staging = isinstance(self.staging_root, str) and self._is_cloud_uri(self.staging_root)
        has_uc_volume = bool(self.uc_catalog and self.uc_schema and self.uc_volume)
        if not has_explicit_staging and not has_uc_volume:
            raise ValueError(
                "Databricks external mode requires cloud staging. Configure --platform-option staging_root=<cloud-uri> "
                "(dbfs:/, s3://, gs://, or abfss://) or Unity Catalog volume options "
                "(uc_catalog, uc_schema, uc_volume)."
            )

    @staticmethod
    def _external_location_from_file_uri(file_uri: str) -> str:
        normalized_uri = file_uri.strip().rstrip("/")
        if "*" in normalized_uri:
            return normalized_uri.rsplit("/", 1)[0]

        suffix = Path(normalized_uri).suffix.lower()
        if suffix == ".parquet":
            return normalized_uri.rsplit("/", 1)[0]

        if suffix:
            raise ValueError(
                f"Databricks external mode requires Parquet sources, got '{file_uri}'. "
                "Provide Parquet input files for --table-mode external."
            )

        return normalized_uri

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        start_time = mono_time()
        table_stats: dict[str, int] = {}
        cursor = connection.cursor()

        try:
            from benchbox.platforms.base.data_loading import DataSource

            data_source = self._resolve_databricks_data_files(benchmark, data_dir)
            if not isinstance(data_source, DataSource):
                data_source = DataSource(source_type="legacy_test_mapping", tables=data_source)
            stage_root = self._resolve_stage_root(data_dir)
            data_source.tables = self._maybe_upload_to_uc_volume(data_source.tables, stage_root, data_dir, connection)

            cursor.execute(f"USE CATALOG {self.catalog}")
            cursor.execute(f"USE SCHEMA {self.schema}")
            cursor.execute(f"SHOW TABLES IN {self.catalog}.{self.schema}")
            existing_tables = {row[1].lower() for row in cursor.fetchall()}

            for table_name, file_path in data_source.tables.items():
                table_name_upper = table_name.upper()
                table_name_lower = table_name.lower()
                if table_name_lower not in existing_tables:
                    raise RuntimeError(
                        f"Table {table_name_upper} does not exist in {self.catalog}.{self.schema}. "
                        "Ensure schema creation completed before external registration."
                    )

                file_uri, _filename, _delimiter = self._resolve_file_uri_and_delimiter(file_path, stage_root)
                location = self._external_location_from_file_uri(file_uri)

                cursor.execute(f"DROP TABLE IF EXISTS {table_name_upper}")
                cursor.execute(f"CREATE TABLE {table_name_upper} USING PARQUET LOCATION '{location}'")
                cursor.execute(f"SELECT COUNT(*) FROM {table_name_upper}")
                result = cursor.fetchone()
                table_stats[table_name_lower] = int(result[0]) if result else 0

        finally:
            cursor.close()

        total_time = elapsed_seconds(start_time)
        return table_stats, total_time, None

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        from benchbox.core.exceptions import ConfigurationError
        from benchbox.platforms.cloud_shared import empty_cache_control_receipt

        configs = getattr(self, "spark_configs", {}) or {}
        if self.disable_result_cache and any(
            str(key).strip().lower() == "use_cached_result" and str(value).lower() != "false"
            for key, value in configs.items()
        ):
            receipt = empty_cache_control_receipt()
            receipt["errors"].append("Custom Spark configuration conflicts with required result cache disable")
            self._cache_disable_failed = True
            self._cache_control_receipt = receipt
            raise ConfigurationError(receipt["errors"][0], details=receipt)

        cursor = connection.cursor()

        try:
            self._ensure_session_cache_disabled(cursor, session=connection)

            if hasattr(self, "spark_configs") and self.spark_configs:
                for config_key, config_value in self.spark_configs.items():
                    if self.disable_result_cache and str(config_key).strip().lower() == "use_cached_result":
                        continue
                    try:
                        cursor.execute(f"SET {config_key} = {config_value}")
                        self.logger.debug(f"Set {config_key} = {config_value}")
                    except Exception as e:
                        self.logger.warning(f"Failed to set {config_key}: {e}")
            else:
                self.logger.debug("No custom Spark configurations to apply")

        finally:
            cursor.close()

    def _ensure_session_cache_disabled(self, cursor: Any, *, session: Any | None = None) -> None:
        import inspect
        import weakref

        from benchbox.core.exceptions import ConfigurationError
        from benchbox.core.tuning.applied_ledger import (
            PHASE_SESSION,
            RecordingConnection,
            _RecordingCursor,
            recording_connection,
        )
        from benchbox.platforms.cloud_shared import empty_cache_control_receipt, explicit_cache_enabled_receipt

        if not self.disable_result_cache:
            self._cache_control_receipt = explicit_cache_enabled_receipt("use_cached_result", "true")
            return

        while isinstance(cursor, _RecordingCursor):
            cursor = cursor._cur
        while isinstance(session, RecordingConnection):
            session = session.raw_connection
        if session is None:
            for attr in ("connection", "_connection"):
                if inspect.getattr_static(cursor, attr, None) is not None:
                    session = getattr(cursor, attr)
                    break
        key = session if session is not None else cursor
        if self._cache_disabled_sessions is None:
            self._cache_disabled_sessions = weakref.WeakSet()
        receipt = empty_cache_control_receipt()
        try:
            weakref.ref(key)
            hash(key)
            if key in self._cache_disabled_sessions:
                return
        except TypeError as exc:
            receipt["errors"].append("Unsupported session identity: cache initialization cannot be tracked")
            self._cache_disable_failed = True
            self._cache_control_receipt = receipt
            raise ConfigurationError(receipt["errors"][0], details=receipt) from exc

        capture = recording_connection(cursor, getattr(self, "_applied_tuning_ledger", None), PHASE_SESSION)
        try:
            capture.execute("SET use_cached_result = false")
            cursor.fetchall()
            cursor.execute("SET use_cached_result")
            row = cursor.fetchone()
            if row is None or len(row) != 2 or str(row[0]).lower() != "use_cached_result":
                raise ValueError("Unsupported cache readback: expected (use_cached_result, value)")
            value = str(row[1]).lower()
            receipt["settings"]["use_cached_result"] = value
            if value != "false":
                raise ValueError(f"Expected use_cached_result=false, observed {value}")
            receipt["validated"] = True
            receipt["cache_disabled"] = True
        except Exception as exc:
            receipt["errors"].append(str(exc))
            self._cache_disable_failed = True
            self._cache_control_receipt = receipt
            raise ConfigurationError("Databricks session cache control failed", details=receipt) from exc

        if not self._cache_disable_failed:
            self._cache_control_receipt = receipt
        self._cache_disabled_sessions.add(key)

    def _initialize_query_session(self, connection: Any) -> None:
        if hasattr(connection, "cursor"):
            cursor = connection.cursor()
            try:
                self._ensure_session_cache_disabled(cursor, session=connection)
            finally:
                cursor.close()
        else:
            self._ensure_session_cache_disabled(connection)

    def _make_direct_power_connection_adapter(self, connection: Any, benchmark_id: str, scale_factor: float) -> Any:
        self._initialize_query_session(connection)
        return super()._make_direct_power_connection_adapter(connection, benchmark_id, scale_factor)

    def _make_power_connection_adapter(self, connection: Any, benchmark_id: str, scale_factor: float) -> Any:
        self._initialize_query_session(connection)
        return super()._make_power_connection_adapter(connection, benchmark_id, scale_factor)

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        stream = super().new_stream_connection(connection, benchmark_type=benchmark_type)
        self._initialize_query_session(stream)
        return stream

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
        own_cursor = False
        if hasattr(connection, "cursor"):
            cursor = connection.cursor()
            own_cursor = True
        else:
            cursor = connection

        try:
            self._ensure_session_cache_disabled(cursor, session=connection if own_cursor else None)
        except Exception as exc:
            if own_cursor:
                cursor.close()
            return {
                "query_id": query_id,
                "status": "FAILED",
                "execution_time_seconds": 0.0,
                "rows_returned": 0,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

        start_time = mono_time()
        self.log_verbose(f"Executing query {query_id}")
        self.log_very_verbose(f"Query SQL (first 200 chars): {query[:200]}{'...' if len(query) > 200 else ''}")

        try:
            query = self._normalize_databricks_query(query)
            from benchbox.platforms.base.mysql_wire import split_sql_statements

            statements = split_sql_statements(query)
            result = []
            for statement in statements or [query]:
                cursor.execute(statement)
                result = cursor.fetchall()

            execution_time = elapsed_seconds(start_time)
            actual_row_count = len(result) if result else 0

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

            result_dict["translated_query"] = None

            result_dict["resource_usage"] = {
                "execution_time_seconds": execution_time,
            }

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
        finally:
            if own_cursor:
                cursor.close()

        self._merge_plan_capture_into_result(result_dict, connection, query, query_id)

        return result_dict

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        from benchbox.platforms.base.sql_execution import join_explain_rows

        cursor = connection.cursor()
        try:
            cursor.execute(f"EXPLAIN EXTENDED {query}")
            plan_rows = cursor.fetchall()
            return join_explain_rows(plan_rows)
        except Exception as e:
            self.logger.debug(f"Could not get Databricks query plan: {e}")
            return None
        finally:
            cursor.close()

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.spark import SparkQueryPlanParser

        return SparkQueryPlanParser()

    def _normalize_databricks_query(self, query: str) -> str:
        from benchbox.utils.dialect_utils import apply_rewrites

        try:
            return apply_rewrites(
                query,
                "databricks",
                [("deduplicate_output_aliases", {"nested": False}), ("safe_division", {})],
            )
        except Exception as e:
            self.log_very_verbose(f"Output alias and division rewrites skipped (unparseable Databricks SQL): {e}")
            return query

    def _schema_ddl_options(self, schema_sql: str) -> dict[str, Any]:
        options: dict[str, Any] = {
            "or_replace": True,
            "replace_if_not_exists": bool(getattr(self, "_schema_reset_in_place", False)),
        }
        if self.table_format == "hudi":
            options["default_layout"] = {"format": "hudi", "properties": {"type": self.hudi_table_type}}
            options["layouts"] = {
                table: {"format": "hudi", "properties": self._hudi_table_properties(columns)}
                for table, columns in ddl_column_names(
                    schema_sql, schema_ddl_read_dialect(schema_sql, "databricks")
                ).items()
            }
        else:
            properties = (
                {"delta.autoOptimize.optimizeWrite": "true", "delta.autoOptimize.autoCompact": "true"}
                if self.delta_auto_optimize
                else {}
            )
            options["default_layout"] = {"format": "delta", "properties": properties}
        return options

    def _hudi_table_properties(self, columns: list[str]) -> dict[str, str]:
        properties = {"type": self.hudi_table_type}
        for option, field in (
            ("primaryKey", self.hudi_primary_key),
            ("preCombineField", self.hudi_precombine_field),
        ):
            if field:
                match = next((name for name in columns if name.lower() == field.lower()), None)
                if match is not None:
                    properties[option] = match
        return properties

    def _get_platform_metadata(self, connection: Any) -> dict[str, Any]:
        clustering_strategy = self._resolve_databricks_clustering_strategy()
        effective_config = self.get_effective_tuning_configuration()
        platform_opts = getattr(effective_config, "platform_optimizations", None)
        requested_strategy = getattr(platform_opts, "databricks_clustering_strategy", None)
        metadata = {
            "platform": self.platform_name,
            "server_hostname": self.server_hostname,
            "catalog": self.catalog,
            "schema": self.schema,
            "result_cache_enabled": not self.disable_result_cache,
            "requested_databricks_clustering_strategy": requested_strategy,
            "resolved_databricks_clustering_strategy": clustering_strategy,
            "databricks_clustering_strategy": clustering_strategy,
            "liquid_clustering_enabled": bool(getattr(platform_opts, "liquid_clustering_enabled", False)),
            "liquid_clustering_columns_config": list(getattr(platform_opts, "liquid_clustering_columns", [])),
            "liquid_clustering_operations": list(self._liquid_clustering_operations),
            "z_order_operations": list(self._z_order_operations),
            "applied_layout_operations": list(self._applied_layout_operations),
            "skipped_layout_operations": list(self._skipped_layout_operations),
        }

        try:
            resolved_version = self._resolve_databricks_engine_version(connection)
        except Exception as exc:
            metadata["metadata_error"] = str(exc)
            return metadata
        for key in ("platform_version", "engine_version", "engine_version_source", *_CURRENT_VERSION_KEYS):
            if resolved_version.get(key) is not None:
                metadata[key] = resolved_version[key]
        if resolved_version.get("engine_version_source") == _ENGINE_VERSION_SOURCE_CURRENT_VERSION:
            try:
                version_cursor = connection.cursor()
                try:
                    version_cursor.execute("SELECT version()")
                    sanitized_spark = _sanitize_spark_engine_version(_first_column(version_cursor.fetchone()))
                    metadata["spark_version"] = sanitized_spark if sanitized_spark else "unknown"
                finally:
                    try:
                        version_cursor.close()
                    except Exception:
                        pass
            except Exception as exc:
                self.logger.debug(f"Could not query Databricks Spark version: {exc}")
                metadata.setdefault("spark_version", "unknown")
        else:
            sanitized = resolved_version.get("engine_version")
            metadata["spark_version"] = sanitized if sanitized else "unknown"

        cursor = connection.cursor()

        try:
            cursor.execute("SELECT current_catalog(), current_schema()")
            result = cursor.fetchone()
            if result:
                metadata["current_catalog"] = result[0]
                metadata["current_schema"] = result[1]

            cursor.execute("SHOW FUNCTIONS LIKE 'current_*'")
            functions = cursor.fetchall()
            metadata["available_functions"] = [f[0] for f in functions]

            cursor.execute("SET")
            configs = cursor.fetchall()
            spark_configs = {k: v for k, v in configs if k.startswith("spark.")}
            metadata["spark_configurations"] = spark_configs

        except Exception as e:
            metadata["metadata_error"] = str(e)
        finally:
            try:
                cursor.close()
            except Exception:
                pass

        return metadata

    def analyze_table(self, connection: Any, table_name: str) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(f"ANALYZE TABLE {table_name.upper()} COMPUTE STATISTICS")
            self.logger.info(f"Analyzed table {table_name.upper()}")
        except Exception as e:
            self.logger.warning(f"Failed to analyze table {table_name}: {e}")
        finally:
            cursor.close()

    def optimize_table(self, connection: Any, table_name: str) -> None:
        table_name_upper = table_name.upper()
        if self.table_format == "hudi":
            if self.enable_delta_optimization:
                self._record_layout_operation(
                    mechanism="optimize",
                    table=table_name_upper,
                    statement=f"OPTIMIZE {table_name_upper}",
                    status="skipped",
                    phase="post_load",
                )
                self.logger.info(f"Skipped Delta-only OPTIMIZE for Hudi table {table_name_upper}")
            return

        if not self.enable_delta_optimization:
            return

        cursor = connection.cursor()
        statement = f"OPTIMIZE {table_name_upper}"
        try:
            cursor.execute(statement)
            self._record_layout_operation(
                mechanism="optimize",
                table=table_name_upper,
                statement=statement,
                status="applied",
                phase="post_load",
            )
            self.logger.info(f"Optimized Delta table {table_name_upper}")
        except Exception as e:
            self._record_layout_operation(
                mechanism="optimize",
                table=table_name_upper,
                statement=statement,
                status="skipped",
                phase="post_load",
                error=e,
            )
            self.logger.warning(f"Failed to optimize table {table_name}: {e}")
        finally:
            cursor.close()

    def vacuum_table(self, connection: Any, table_name: str, hours: int = 168) -> None:
        if self.table_format == "hudi":
            if self.enable_delta_optimization:
                self._record_layout_operation(
                    mechanism="vacuum",
                    table=table_name.upper(),
                    statement=f"VACUUM {table_name.upper()}",
                    status="skipped",
                    phase="post_load",
                )
                self.logger.info(f"Skipped Delta-only VACUUM for Hudi table {table_name.upper()}")
            return

        if not self.enable_delta_optimization:
            return

        cursor = connection.cursor()
        try:
            cursor.execute(f"VACUUM {table_name.upper()} RETAIN {hours} HOURS")
            self.logger.info(f"Vacuumed Delta table {table_name.upper()}")
        except Exception as e:
            self.logger.warning(f"Failed to vacuum table {table_name}: {e}")
        finally:
            cursor.close()

    def _get_existing_tables(self, connection: Any) -> list[str]:
        try:
            cursor = connection.cursor()
            cursor.execute(f"SHOW TABLES IN {self.catalog}.{self.schema}")
            result = cursor.fetchall()
            cursor.close()

            return [row[1] for row in result if not row[2]]
        except Exception as e:
            self.logger.debug(f"Failed to get existing tables: {e}")
            return []

    def close_connection(self, connection: Any) -> None:
        try:
            if connection and hasattr(connection, "close"):
                connection.close()
        except Exception as e:
            self.logger.warning(f"Error closing connection: {e}")

    _supported_tuning_type_names = ("PARTITIONING", "CLUSTERING", "DISTRIBUTION")

    def generate_tuning_clause(self, table_tuning) -> str:
        if not table_tuning or not table_tuning.has_any_tuning():
            return ""

        clauses = []

        try:
            from benchbox.core.tuning.interface import TuningType

            if self.table_format == "hudi":
                return self._generate_hudi_tuning_clause(table_tuning, TuningType)

            clauses.append("USING DELTA")
            clustering_strategy = self._resolve_databricks_clustering_strategy()
            use_liquid = clustering_strategy in {"liquid_clustering", "liquid_clustering_auto"}

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                if use_liquid:
                    raise ValueError(
                        "Databricks Liquid Clustering is incompatible with PARTITIONED BY; "
                        "move partition columns to Liquid clustering intent or use databricks_z_order."
                    )
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]

                partition_clause = f"PARTITIONED BY ({', '.join(column_names)})"
                clauses.append(partition_clause)

            cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
            if clustering_strategy == "liquid_clustering_auto":
                clauses.append("CLUSTER BY AUTO")
            elif cluster_columns:
                sorted_cols = sorted(cluster_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]

                cluster_clause = f"CLUSTER BY ({', '.join(column_names)})"
                clauses.append(cluster_clause)

        except ImportError:
            clauses.append("USING DELTA")

        return " ".join(clauses)

    def _generate_hudi_tuning_clause(self, table_tuning, TuningType) -> str:
        clauses = ["USING HUDI"]
        properties = [f"'type' = '{self.hudi_table_type}'"]
        if self.hudi_primary_key:
            properties.append(f"'primaryKey' = '{self.hudi_primary_key}'")
        if self.hudi_precombine_field:
            properties.append(f"'preCombineField' = '{self.hudi_precombine_field}'")
        clauses.append("TBLPROPERTIES (" + ", ".join(properties) + ")")

        partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
        if partition_columns:
            sorted_cols = sorted(partition_columns, key=lambda col: col.order)
            column_names = [col.name for col in sorted_cols]
            clauses.append(f"PARTITIONED BY ({', '.join(column_names)})")

        return " ".join(clauses)

    def _delta_optimized_after_load(self) -> set[str]:
        optimized = getattr(self, "_delta_optimized_tables", None)
        if optimized is None:
            optimized = set()
            self._delta_optimized_tables = optimized
        return optimized

    def apply_post_load_tunings(self, table_name: str, effective_config: Any, connection: Any) -> bool:
        table_tuning = self.table_tuning_for(effective_config, table_name)
        if table_tuning is None or not table_tuning.has_any_tuning() or not self.enable_delta_optimization:
            return False
        physical_table = self.resolve_physical_table(table_name)
        cursor = connection.cursor()
        try:
            cursor.execute(f"DESCRIBE EXTENDED {physical_table}")
            if not any("DELTA" in str(row).upper() for row in cursor.fetchall()):
                return False
            self._delta_optimized_after_load().add(physical_table.lower())
            if not self._apply_delta_optimize(cursor, physical_table, phase="post_load"):
                self.note_post_load_maintenance_failure()
            return True
        finally:
            cursor.close()

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        if not table_tuning or not table_tuning.has_any_tuning():
            return

        table_name = self.resolve_physical_table(table_tuning.table_name)
        self.logger.info(f"Applying Databricks tunings for table: {table_name}")

        cursor = connection.cursor()
        try:
            from benchbox.core.tuning.interface import TuningType

            cursor.execute(f"DESCRIBE EXTENDED {table_name}")
            table_info = cursor.fetchall()

            is_delta_table = any("DELTA" in str(row).upper() for row in table_info)
            if not is_delta_table:
                self.logger.warning(
                    f"Table {table_name} is not a Delta table - some optimizations may not be available"
                )

            effective_config = self.get_effective_tuning_configuration()
            platform_opts = getattr(effective_config, "platform_optimizations", None)
            clustering_strategy = self._resolve_databricks_clustering_strategy()
            liquid_enabled = bool(getattr(platform_opts, "liquid_clustering_enabled", False))
            liquid_columns = [
                self.resolve_physical_column(table_name, column)
                for column in getattr(platform_opts, "liquid_clustering_columns", [])
            ]

            cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
            distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
            sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)

            zorder_columns = [
                self.resolve_physical_column(table_name, column)
                for column in self._build_zorder_columns(cluster_columns, distribution_columns)
            ]
            use_liquid = clustering_strategy in {"liquid_clustering", "liquid_clustering_auto"} or liquid_enabled
            if self.table_format == "hudi":
                self._record_hudi_tuning_skips(
                    table_name,
                    zorder_columns,
                    use_liquid,
                    liquid_columns,
                    sort_columns,
                )
                self._log_partitioning_and_sorting(
                    table_name,
                    partition_columns,
                    sort_columns,
                    use_liquid,
                )
                return
            if use_liquid and partition_columns:
                raise ValueError(
                    "Databricks Liquid Clustering is incompatible with per-table partitioning; "
                    "move partition columns to clustering intent or use databricks_z_order."
                )
            if use_liquid and distribution_columns:
                raise ValueError(
                    "Databricks Liquid Clustering has no user-managed distribution key; "
                    "fold distribution candidates into clustering intent or use databricks_z_order."
                )
            self._apply_clustering_strategy(
                cursor,
                table_name,
                is_delta_table,
                clustering_strategy,
                use_liquid,
                liquid_columns,
                zorder_columns,
                sort_columns,
            )
            self._log_partitioning_and_sorting(
                table_name,
                partition_columns,
                sort_columns,
                use_liquid,
            )
        except ImportError:
            self.logger.warning("Tuning interface not available - skipping tuning application")
        except Exception as e:
            raise ValueError(f"Failed to apply tunings to Databricks table {table_name}: {e}") from e
        finally:
            cursor.close()

    @staticmethod
    def _build_zorder_columns(cluster_columns, distribution_columns) -> list[str]:
        cols: list[str] = []
        if cluster_columns:
            cols.extend(col.name for col in sorted(cluster_columns, key=lambda c: c.order))
        if distribution_columns:
            for col in sorted(distribution_columns, key=lambda c: c.order):
                if col.name not in cols:
                    cols.append(col.name)
        return cols

    def _record_hudi_tuning_skips(
        self,
        table_name: str,
        zorder_columns: list[str],
        use_liquid: bool,
        liquid_columns: list[str],
        sort_columns,
    ) -> None:
        if zorder_columns and not use_liquid:
            self._record_layout_operation(
                mechanism="z_order",
                table=table_name,
                statement=f"OPTIMIZE {table_name} ZORDER BY ({', '.join(zorder_columns)})",
                status="skipped",
                phase="ddl",
                columns=zorder_columns,
            )
            self.logger.info(f"Skipped Delta-only Z-ORDER for Hudi table {table_name}")
        if use_liquid:
            effective = list(liquid_columns) or list(zorder_columns)
            if not effective and sort_columns:
                effective = [
                    self.resolve_physical_column(table_name, col.name)
                    for col in sorted(sort_columns, key=lambda c: c.order)
                ]
            if effective:
                clause = f"ALTER TABLE {table_name} CLUSTER BY ({', '.join(effective)})"
                self._record_layout_operation(
                    mechanism="liquid_clustering",
                    table=table_name,
                    statement=clause,
                    status="skipped",
                    phase="ddl",
                    columns=effective,
                )
                self.logger.info(f"Skipped Delta-only Liquid Clustering for Hudi table {table_name}")
        if self.enable_delta_optimization:
            self._record_layout_operation(
                mechanism="optimize",
                table=table_name,
                statement=f"OPTIMIZE {table_name}",
                status="skipped",
                phase="ddl",
            )
            self.logger.info(f"Skipped Delta-only OPTIMIZE for Hudi table {table_name}")
            self._record_layout_operation(
                mechanism="analyze",
                table=table_name,
                statement=f"ANALYZE TABLE {table_name} COMPUTE STATISTICS",
                status="skipped",
                phase="ddl",
            )
            self.logger.info(f"Skipped ANALYZE for Hudi table {table_name}")

    def _apply_clustering_strategy(
        self,
        cursor: Any,
        table_name: str,
        is_delta_table: bool,
        clustering_strategy: str,
        use_liquid: bool,
        liquid_columns: list[str],
        zorder_columns: list[str],
        sort_columns,
    ) -> None:
        if clustering_strategy == "liquid_clustering_auto":
            self._apply_liquid_auto_clustering(cursor, table_name, is_delta_table)
        elif use_liquid:
            self._apply_liquid_clustering(
                cursor, table_name, is_delta_table, liquid_columns, zorder_columns, sort_columns
            )
        elif zorder_columns and is_delta_table:
            self._apply_zorder_optimization(cursor, table_name, zorder_columns)

    def _apply_liquid_auto_clustering(self, cursor: Any, table_name: str, is_delta_table: bool) -> None:
        clause = f"ALTER TABLE {table_name} CLUSTER BY AUTO"
        if not is_delta_table:
            self._record_layout_operation(
                mechanism="liquid_clustering_auto",
                table=table_name,
                statement=clause,
                status="skipped",
                phase="ddl",
            )
            self.logger.info(f"Liquid AUTO selected for {table_name} but table is not Delta")
            return

        try:
            cursor.execute(clause)
            self._liquid_clustering_operations.append(
                {"table": table_name, "columns": [], "statement": clause, "mode": "auto"}
            )
            self._record_layout_operation(
                mechanism="liquid_clustering_auto",
                table=table_name,
                statement=clause,
                status="applied",
                phase="ddl",
            )
            self.logger.info(f"Applied automatic Liquid Clustering to {table_name}")
        except Exception as e:
            self._record_layout_operation(
                mechanism="liquid_clustering_auto",
                table=table_name,
                statement=clause,
                status="skipped",
                phase="ddl",
                error=e,
            )
            self.logger.warning(f"Failed to apply automatic Liquid Clustering to {table_name}: {e}")

    def _apply_liquid_clustering(
        self,
        cursor: Any,
        table_name: str,
        is_delta_table: bool,
        liquid_columns: list[str],
        zorder_columns: list[str],
        sort_columns,
    ) -> None:
        if not liquid_columns:
            liquid_columns = list(zorder_columns)
        if not liquid_columns and sort_columns:
            liquid_columns = [
                self.resolve_physical_column(table_name, col.name)
                for col in sorted(sort_columns, key=lambda c: c.order)
            ]
        if liquid_columns and is_delta_table:
            clause = f"ALTER TABLE {table_name} CLUSTER BY ({', '.join(liquid_columns)})"
            try:
                cursor.execute(clause)
                self._liquid_clustering_operations.append(
                    {"table": table_name, "columns": list(liquid_columns), "statement": clause, "mode": "manual"}
                )
                self._record_layout_operation(
                    mechanism="liquid_clustering",
                    table=table_name,
                    statement=clause,
                    status="applied",
                    phase="ddl",
                    columns=liquid_columns,
                )
                self.logger.info(f"Applied Liquid Clustering to {table_name}: {', '.join(liquid_columns)}")
            except Exception as e:
                self._record_layout_operation(
                    mechanism="liquid_clustering",
                    table=table_name,
                    statement=clause,
                    status="skipped",
                    phase="ddl",
                    columns=liquid_columns,
                    error=e,
                )
                self.logger.warning(f"Failed to apply Liquid Clustering to {table_name}: {e}")
        elif is_delta_table:
            self._record_layout_operation(
                mechanism="liquid_clustering",
                table=table_name,
                statement=f"ALTER TABLE {table_name} CLUSTER BY (...)",
                status="skipped",
                phase="ddl",
            )
            self.logger.info(f"Liquid Clustering selected for {table_name} but no clustering columns were available")

    def _apply_zorder_optimization(self, cursor: Any, table_name: str, zorder_columns: list[str]) -> None:
        if self.table_format == "hudi":
            self._record_layout_operation(
                mechanism="z_order",
                table=table_name,
                statement=f"OPTIMIZE {table_name} ZORDER BY ({', '.join(zorder_columns)})",
                status="skipped",
                phase="ddl",
                columns=zorder_columns,
            )
            self.logger.info(f"Skipped Delta-only Z-ORDER for Hudi table {table_name}")
            return
        clause = f"OPTIMIZE {table_name} ZORDER BY ({', '.join(zorder_columns)})"
        try:
            cursor.execute(clause)
            self._z_order_operations.append({"table": table_name, "columns": list(zorder_columns), "statement": clause})
            self._record_layout_operation(
                mechanism="z_order",
                table=table_name,
                statement=clause,
                status="applied",
                phase="ddl",
                columns=zorder_columns,
            )
            self.logger.info(f"Applied Z-ORDER optimization to {table_name}: {', '.join(zorder_columns)}")
        except Exception as e:
            self._record_layout_operation(
                mechanism="z_order",
                table=table_name,
                statement=clause,
                status="skipped",
                phase="ddl",
                columns=zorder_columns,
                error=e,
            )
            self.logger.warning(f"Failed to apply Z-ORDER optimization to {table_name}: {e}")

    def _log_partitioning_and_sorting(self, table_name: str, partition_columns, sort_columns, use_liquid: bool) -> None:
        if partition_columns:
            names = [col.name for col in sorted(partition_columns, key=lambda c: c.order)]
            self.logger.info(
                f"Partitioning strategy for {table_name}: {', '.join(names)} (defined at CREATE TABLE time)"
            )
        if sort_columns:
            names = [col.name for col in sorted(sort_columns, key=lambda c: c.order)]
            if getattr(self, "table_format", "delta") == "hudi":
                self.logger.info(
                    f"Sorting not applied for Hudi table {table_name}: {', '.join(names)} "
                    "(Z-ORDER/Liquid clustering are Delta-only)"
                )
                return
            mechanism = "Liquid Clustering" if use_liquid else "Z-ORDER clustering"
            self.logger.info(
                f"Sorting in Databricks achieved via {mechanism} for table {table_name}: {', '.join(names)}"
            )

    def _apply_delta_optimize(self, cursor: Any, table_name: str, *, phase: str) -> bool:
        if self.table_format == "hudi":
            self._record_layout_operation(
                mechanism="optimize",
                table=table_name,
                statement=f"OPTIMIZE {table_name}",
                status="skipped",
                phase=phase,
            )
            self.logger.info(f"Skipped Delta-only OPTIMIZE for Hudi table {table_name}")
            self._record_layout_operation(
                mechanism="analyze",
                table=table_name,
                statement=f"ANALYZE TABLE {table_name} COMPUTE STATISTICS",
                status="skipped",
                phase=phase,
            )
            self.logger.info(f"Skipped ANALYZE for Hudi table {table_name}")
            return True
        optimize_statement = f"OPTIMIZE {table_name}"
        try:
            cursor.execute(optimize_statement)
            self._record_layout_operation(
                mechanism="optimize",
                table=table_name,
                statement=optimize_statement,
                status="applied",
                phase=phase,
            )
            self.logger.info(f"Optimized Delta table {table_name}")
        except Exception as e:
            self._record_layout_operation(
                mechanism="optimize",
                table=table_name,
                statement=optimize_statement,
                status="skipped",
                phase=phase,
                error=e,
            )
            self.logger.warning(f"Failed to optimize Delta table {table_name}: {e}")
            return False

        analyze_statement = f"ANALYZE TABLE {table_name} COMPUTE STATISTICS"
        try:
            cursor.execute(analyze_statement)
            self._record_layout_operation(
                mechanism="analyze",
                table=table_name,
                statement=analyze_statement,
                status="applied",
                phase=phase,
            )
            self.logger.info(f"Updated statistics for {table_name}")
        except Exception as e:
            self._record_layout_operation(
                mechanism="analyze",
                table=table_name,
                statement=analyze_statement,
                status="skipped",
                phase=phase,
                error=e,
            )
            self.logger.warning(f"Failed to analyze Delta table {table_name}: {e}")
            return False
        return True

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

        apply_standard_unified_tuning(self, unified_config, connection)

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        if not platform_config:
            return

        self.logger.info("Databricks platform optimizations stored for Spark session and Delta Lake management")

    apply_constraint_configuration = make_informational_constraint_applier(
        "Primary key constraints enabled for Databricks (informational only, applied during table creation)",
        "Foreign key constraints enabled for Databricks (informational only, applied during table creation)",
    )
