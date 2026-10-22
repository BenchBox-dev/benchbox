from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from benchbox.core.benchmark_mixins import CursorValidationQueryExecutionMixin
from benchbox.core.deployment import deployment_class, select_adapter_deployment
from benchbox.platforms.base.config_utils import make_registered_platform_config_builder
from benchbox.platforms.base.runtime_metadata import build_default_normalized_result_metadata, with_canonical_compute
from benchbox.platforms.base.tuning import make_informational_constraint_applier
from benchbox.platforms.presto_trino_utils import normalize_existing_files, show_tables_lower
from benchbox.utils.clock import elapsed_seconds, mono_time

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        UnifiedTuningConfiguration,
    )

from ..core.exceptions import ConfigurationError
from ..utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)
from .base import DriverIsolationCapability, PlatformAdapter
from .base.data_loading import FileFormatRegistry

try:
    from firebolt.client.auth import ClientCredentials
    from firebolt.client.auth.firebolt_core import FireboltCore
    from firebolt.db import connect as firebolt_connect

    FIREBOLT_AVAILABLE = True
except ImportError:
    FIREBOLT_AVAILABLE = False
    firebolt_connect = None
    ClientCredentials = None
    FireboltCore = None


def _resolve_firebolt_deployment_mode(
    config: dict, url: str | None, client_id: str | None, client_secret: str | None
) -> str:
    selected = select_adapter_deployment("firebolt", config, None)
    if selected is not None:
        return selected

    if url and not (client_id or client_secret):
        return "core"
    if client_id and client_secret:
        if url:
            raise ValueError(
                "Firebolt configuration is ambiguous: both Core URL and Cloud credentials provided. "
                "Specify --platform firebolt:core or firebolt:cloud explicitly."
            )
        return "cloud"

    return "core"


def _compact_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if value not in (None, "", {}, [], ())}


def _endpoint_class_from_url(url: Any) -> str:
    url_value = str(url or "").strip()
    if not url_value:
        return "unknown"
    parsed = urlparse(url_value if "://" in url_value else f"//{url_value}")
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return "localhost_port"
    return "remote_host" if host else "unknown"


def _host_from_url(url: Any) -> str | None:
    url_value = str(url or "").strip()
    if not url_value:
        return None
    parsed = urlparse(url_value if "://" in url_value else f"//{url_value}")
    return parsed.hostname or url_value.split("/", 1)[0].split(":", 1)[0]


def _validate_firebolt_mode_config(adapter: FireboltAdapter) -> None:
    if adapter.deployment_mode == "core":
        if not adapter.url:
            adapter.url = "http://localhost:3473"
    else:
        missing = [
            name
            for name, val in [
                ("client_id", adapter.client_id),
                ("client_secret", adapter.client_secret),
                ("account_name", adapter.account_name),
                ("engine_name", adapter.engine_name),
            ]
            if not val
        ]
        if missing:
            raise ConfigurationError(
                f"Firebolt Cloud configuration is incomplete. Missing: {', '.join(missing)}\n"
                "Configure with:\n"
                "  1. Environment variables: FIREBOLT_CLIENT_ID, FIREBOLT_CLIENT_SECRET, "
                "FIREBOLT_ACCOUNT_NAME, FIREBOLT_ENGINE_NAME\n"
                "  2. CLI options: --platform-option client_id=<id> --platform-option client_secret=<secret>\n"
                "\n"
                "Create service account credentials in the Firebolt console under Settings > Service Accounts."
            )


class FireboltAdapter(CursorValidationQueryExecutionMixin, PlatformAdapter):
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY

    def __init__(self, **config):
        super().__init__(**config)

        if not FIREBOLT_AVAILABLE:
            available, missing = check_platform_dependencies("firebolt")
            if not available:
                error_msg = get_dependency_error_message("firebolt", missing)
                raise ImportError(error_msg)

        self._dialect = "postgres"

        self.url = config.get("url") or config.get("engine_url")
        self.client_id = (
            config.get("client_id") or os.environ.get("FIREBOLT_CLIENT_ID") or os.environ.get("SERVICE_ACCOUNT_ID")
        )
        self.client_secret = (
            config.get("client_secret")
            or os.environ.get("FIREBOLT_CLIENT_SECRET")
            or os.environ.get("SERVICE_ACCOUNT_SECRET")
        )

        self.deployment_mode = _resolve_firebolt_deployment_mode(config, self.url, self.client_id, self.client_secret)
        self.deployment_selected = self.deployment_mode
        self.deployment_selected_class = deployment_class("firebolt", self.deployment_mode)

        self.database = config.get("database") or os.environ.get("FIREBOLT_DATABASE") or "benchbox"

        self.account_name = config.get("account_name") or os.environ.get("FIREBOLT_ACCOUNT_NAME")
        self.engine_name = (
            config.get("compute_resource") or config.get("engine_name") or os.environ.get("FIREBOLT_ENGINE_NAME")
        )
        self.api_endpoint = (
            config.get("api_endpoint") or os.environ.get("FIREBOLT_API_ENDPOINT") or "api.app.firebolt.io"
        )
        self.region = config.get("region") or config.get("cloud_region") or os.environ.get("FIREBOLT_REGION")
        self.cloud_provider = config.get("cloud_provider") or os.environ.get("FIREBOLT_CLOUD_PROVIDER")
        self.engine_type = config.get("engine_type")
        self.engine_size = config.get("compute_size") or config.get("engine_size")

        _validate_firebolt_mode_config(self)

        self.s3_staging_url = config.get("s3_staging_url") or os.environ.get("FIREBOLT_S3_STAGING_URL") or None
        self.s3_region = config.get("s3_region") or os.environ.get("FIREBOLT_S3_REGION") or None

        if self.s3_staging_url:
            if not self.s3_staging_url.startswith("s3://"):
                raise ConfigurationError(
                    f"Invalid S3 staging URL: '{self.s3_staging_url}'. "
                    "Must start with 's3://' (e.g., s3://my-bucket/benchbox-staging/)"
                )
            if not self.s3_staging_url.endswith("/"):
                self.s3_staging_url += "/"

        self.disable_result_cache = self._coerce_bool(config.get("disable_result_cache"), True)
        self.strict_validation = self._coerce_bool(config.get("strict_validation"), False)

    @property
    def platform_name(self) -> str:
        return f"Firebolt ({self.deployment_mode.title()})"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        firebolt_group = parser.add_argument_group("Firebolt Arguments")

        firebolt_group.add_argument(
            "--deployment-mode",
            type=str,
            choices=["core", "cloud"],
            help="Firebolt deployment mode (auto-detected if not specified)",
        )

        firebolt_group.add_argument(
            "--url",
            type=str,
            default="http://localhost:3473",
            help="Firebolt Core endpoint URL (default: http://localhost:3473)",
        )

        firebolt_group.add_argument(
            "--client-id",
            type=str,
            help="Firebolt Cloud OAuth client ID",
        )
        firebolt_group.add_argument(
            "--client-secret",
            type=str,
            help="Firebolt Cloud OAuth client secret",
        )
        firebolt_group.add_argument(
            "--account-name",
            type=str,
            help="Firebolt Cloud account name",
        )
        firebolt_group.add_argument(
            "--engine-name",
            type=str,
            help="Firebolt Cloud engine name",
        )
        firebolt_group.add_argument(
            "--api-endpoint",
            type=str,
            default="api.app.firebolt.io",
            help="Firebolt Cloud API endpoint",
        )

        firebolt_group.add_argument(
            "--database",
            type=str,
            default="benchbox",
            help="Database name (default: benchbox)",
        )

        firebolt_group.add_argument(
            "--firebolt-s3-staging-url",
            type=str,
            help="S3 URL for staging data files during cloud loading (e.g., s3://bucket/benchbox-staging/)",
        )
        firebolt_group.add_argument(
            "--firebolt-s3-region",
            type=str,
            help="AWS region for the S3 staging bucket (e.g., us-east-1)",
        )

        firebolt_group.add_argument(
            "--disable-result-cache",
            action="store_true",
            default=True,
            help="Disable result cache for accurate benchmarking (default: True)",
        )
        firebolt_group.add_argument(
            "--strict-validation",
            action="store_true",
            default=False,
            help="Enable strict validation mode (fail on warnings)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.platforms.base.config_utils import build_adapter_config

        adapter_config = build_adapter_config(
            config,
            platform="firebolt",
            fields=[
                "url",
                "client_id",
                "client_secret",
                "account_name",
                "engine_name",
                "compute_resource",
                "api_endpoint",
                "region",
                "cloud_region",
                "cloud_provider",
                "engine_type",
                "engine_size",
                "compute_size",
                "deployment_mode",
                "s3_staging_url",
                "s3_region",
                "disable_result_cache",
                "strict_validation",
            ],
            include_none=False,
        )

        if config.get("deployment_mode"):
            mode = config["deployment_mode"]
            if mode == "core" and "url" not in adapter_config:
                adapter_config["url"] = "http://localhost:3473"

        return cls(**adapter_config)

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "firebolt",
            "platform_name": self.platform_name,
            "connection_mode": self.deployment_mode,
            "configuration": {
                "database": self.database,
                "deployment_mode": self.deployment_mode,
                "result_cache_enabled": not self.disable_result_cache,
                "s3_staging_url": self.s3_staging_url,
                "s3_region": self.s3_region,
            },
        }

        if self.deployment_mode == "core":
            platform_info["url"] = self.url
            platform_info["configuration"]["url"] = self.url
        else:
            platform_info["account_name"] = self.account_name
            platform_info["engine_name"] = self.engine_name
            platform_info["api_endpoint"] = self.api_endpoint
            platform_info["configuration"].update(
                {
                    "account_name": self.account_name,
                    "engine_name": self.engine_name,
                    "api_endpoint": self.api_endpoint,
                    "region": self.region,
                    "cloud_provider": self.cloud_provider,
                    "engine_type": self.engine_type,
                    "engine_size": self.engine_size,
                    "auth_method": "client_credentials",
                }
            )

        try:
            import firebolt

            platform_info["client_library_version"] = firebolt.__version__
        except (ImportError, AttributeError):
            platform_info["client_library_version"] = None

        if connection:
            cursor = None
            try:
                cursor = connection.cursor()
                cursor.execute("SELECT version()")
                result = cursor.fetchone()
                platform_info["platform_version"] = result[0] if result else None
                platform_info["engine_version"] = platform_info["platform_version"]
                platform_info["engine_version_source"] = "sql_query"
                if self.deployment_mode == "cloud":
                    self._collect_firebolt_cloud_engine_metadata(cursor, platform_info)
            except Exception as e:
                self.logger.debug(f"Error collecting Firebolt platform info: {e}")
                platform_info["platform_version"] = None
            finally:
                if cursor:
                    cursor.close()
        else:
            platform_info["platform_version"] = None

        return platform_info

    def _collect_firebolt_cloud_engine_metadata(self, cursor: Any, platform_info: dict[str, Any]) -> None:
        try:
            engine_name = str(self.engine_name or "").replace("'", "''")
            cursor.execute(
                "SELECT engine_name, engine_type, status "
                "FROM information_schema.engines "
                f"WHERE engine_name = '{engine_name}'"
            )
            engine_info = cursor.fetchone()
            if not engine_info or len(engine_info) < 2:
                platform_info["compute_configuration"] = {
                    "engine_metadata_collection_status": "unavailable",
                }
                return

            platform_info["compute_configuration"] = _compact_metadata(
                {
                    "engine_name": engine_info[0] if len(engine_info) > 0 else self.engine_name,
                    "engine_type": engine_info[1] if len(engine_info) > 1 else None,
                    "engine_status": engine_info[2] if len(engine_info) > 2 else None,
                    "engine_metadata_collection_status": "available",
                }
            )
        except Exception as e:
            self.logger.debug(f"Could not fetch Firebolt engine details: {e}")
            platform_info["compute_configuration"] = {
                "engine_metadata_collection_status": "unavailable",
                "engine_metadata_error_class": type(e).__name__,
                "engine_metadata_error_message": str(e),
            }

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

        exec_env = self._firebolt_execution_environment(info, config)
        if isinstance(metadata.get("execution_environment"), Mapping):
            client_link = metadata["execution_environment"].get("client_link")
            if client_link:
                exec_env["client_link"] = client_link
        metadata["execution_environment"] = exec_env
        metadata["platform_deployment"] = self._firebolt_deployment_metadata(info, config)
        metadata["platform_cloud"] = self._firebolt_cloud_metadata(config)
        metadata["platform_compute"] = with_canonical_compute(
            metadata.get("platform_compute"), self._firebolt_compute_metadata(config, compute)
        )
        metadata["platform_storage"] = self._firebolt_storage_metadata(config)
        return metadata

    @staticmethod
    def _firebolt_execution_environment(info: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        mode = str(config.get("deployment_mode") or info.get("connection_mode") or "core")
        if mode == "cloud":
            runtime = {
                "runtime_type": "managed_cloud",
                "collection_status": "partial",
                "source": "requested",
                "engine_host": config.get("api_endpoint"),
            }
        else:
            url = config.get("url") or info.get("url")
            runtime = {
                "runtime_type": "remote_server",
                "collection_status": "partial",
                "source": "requested",
                "engine_host": _host_from_url(url),
            }
        return _compact_metadata({"platform_runtime": runtime})

    def _firebolt_deployment_metadata(self, info: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        mode = str(config.get("deployment_mode") or info.get("connection_mode") or "core")
        is_cloud = mode == "cloud"
        url = config.get("url") or info.get("url")
        endpoint_class = "cloud_endpoint" if is_cloud else _endpoint_class_from_url(url)
        return _compact_metadata(
            {
                "deployment_type": "managed_cloud" if is_cloud else "self_hosted",
                "connection_mode": mode,
                "endpoint_class": endpoint_class,
                "metadata_source": "requested",
                "collection_status": "partial",
                "selected": self.deployment_selected,
                "selected_class": self.deployment_selected_class,
                "account": config.get("account_name") if is_cloud else None,
                "engine": config.get("engine_name") if is_cloud else None,
                "database": config.get("database"),
                "api_endpoint": config.get("api_endpoint") if is_cloud else None,
                "url": url if not is_cloud else None,
                "auth_method": config.get("auth_method") if is_cloud else "firebolt_core",
            }
        )

    @staticmethod
    def _firebolt_cloud_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        is_cloud = config.get("deployment_mode") == "cloud"
        provider = config.get("cloud_provider")
        provider = str(provider).lower() if provider else None
        region = config.get("region") or config.get("cloud_region")
        has_cloud_metadata = bool(
            is_cloud and (provider or region or config.get("account_name") or config.get("api_endpoint"))
        )
        return _compact_metadata(
            {
                "provider": provider,
                "region": region,
                "account": config.get("account_name") if is_cloud else None,
                "service_endpoint": config.get("api_endpoint") if is_cloud else None,
                "region_collection_status": "available" if region else "unavailable",
                "source": "requested" if has_cloud_metadata else "unavailable",
                "collection_status": "partial" if has_cloud_metadata else "unavailable",
            }
        )

    @staticmethod
    def _firebolt_compute_metadata(config: Mapping[str, Any], compute: Mapping[str, Any]) -> dict[str, Any]:
        is_cloud = config.get("deployment_mode") == "cloud"
        observed = compute.get("engine_metadata_collection_status") == "available"
        has_compute_metadata = bool(
            is_cloud
            and (
                observed
                or config.get("compute_resource")
                or config.get("engine_name")
                or config.get("engine_type")
                or config.get("compute_size")
                or config.get("engine_size")
                or config.get("result_cache_enabled") is not None
                or compute.get("engine_metadata_collection_status")
            )
        )
        return _compact_metadata(
            {
                "service_model": "managed" if is_cloud else "core",
                "engine": compute.get("engine_name") or config.get("compute_resource") or config.get("engine_name"),
                "engine_type": compute.get("engine_type") or config.get("engine_type"),
                "engine_size": config.get("compute_size") or config.get("engine_size"),
                "engine_status": compute.get("engine_status"),
                "result_cache_enabled": config.get("result_cache_enabled") if is_cloud else None,
                "engine_metadata_collection_status": compute.get("engine_metadata_collection_status"),
                "engine_metadata_error_class": compute.get("engine_metadata_error_class"),
                "engine_metadata_error_message": compute.get("engine_metadata_error_message"),
                "source": "observed" if observed else "requested" if has_compute_metadata else "unavailable",
                "collection_status": "available" if observed else "partial" if has_compute_metadata else "unavailable",
            }
        )

    @classmethod
    def _firebolt_storage_metadata(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        staging_url = config.get("s3_staging_url")
        staging = cls._firebolt_s3_staging_metadata(staging_url)
        if config.get("s3_region"):
            staging["region"] = config.get("s3_region")
        has_staging = bool(staging_url)
        return _compact_metadata(
            {
                "table_format": "firebolt_engine_table",
                "staging_location": staging_url,
                "staging_url_type": "s3" if has_staging else None,
                "staging_url_type_status": "available" if has_staging else "unavailable",
                "bucket": staging.get("bucket"),
                "prefix": staging.get("prefix"),
                "region": staging.get("region"),
                "source": "requested" if has_staging else "inferred",
                "collection_status": "partial",
            }
        )

    @staticmethod
    def _firebolt_s3_staging_metadata(staging_url: Any) -> dict[str, Any]:
        url = str(staging_url or "")
        if not url.startswith("s3://"):
            return {}
        from benchbox.utils.cloud_urls import parse_s3_url

        bucket, prefix = parse_s3_url(url)
        return {"bucket": bucket, "prefix": prefix}

    def get_target_dialect(self) -> str:
        return "postgres"

    def _get_connection_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "database": self.database,
        }

        if self.deployment_mode == "core":
            if not FireboltCore:
                raise ImportError("firebolt-sdk is required for Firebolt Core mode")
            params["auth"] = FireboltCore()
            params["url"] = self.url
        else:
            if not ClientCredentials:
                raise ImportError("firebolt-sdk is required for Firebolt Cloud mode")

            params["auth"] = ClientCredentials(self.client_id, self.client_secret)
            params["account_name"] = self.account_name
            params["engine_name"] = self.engine_name
            params["api_endpoint"] = self.api_endpoint

        return params

    def check_server_database_exists(self, **connection_config) -> bool:
        database = connection_config.get("database", self.database)

        if self.deployment_mode == "core":
            try:
                params = self._get_connection_params()
                if database:
                    params["database"] = database
                conn = firebolt_connect(**params)
                cursor = conn.cursor()
                try:
                    cursor.execute("SHOW TABLES")
                    existing_tables = cursor.fetchall() or []
                    has_tables = len(existing_tables) > 0

                    if has_tables:
                        self.log_verbose(
                            f"Firebolt Core database '{database}' exists with {len(existing_tables)} table(s)"
                        )
                    else:
                        self.log_very_verbose(f"Firebolt Core database '{database}' exists but is empty")

                    return has_tables
                finally:
                    cursor.close()
                    conn.close()
            except Exception as e:
                self.logger.debug(f"Core mode database existence check failed: {e}")
                return False

        cursor = None
        conn = None
        try:
            params = self._get_connection_params()
            check_params = params.copy()
            check_params["database"] = "information_schema"

            conn = firebolt_connect(**check_params)
            cursor = conn.cursor()

            database = connection_config.get("database", self.database)
            database_literal = database.replace("'", "''")

            cursor.execute(
                f"SELECT database_name FROM information_schema.databases WHERE database_name = '{database_literal}'"
            )
            result = cursor.fetchone()

            return result is not None

        except Exception as e:
            self.logger.debug(f"Error checking database existence: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def drop_database(self, **connection_config) -> None:
        database = connection_config.get("database", self.database)

        if self.deployment_mode == "core":
            self.fail_closed_on_force_recreate(
                platform_label="Firebolt (Core)",
                database=database,
                manual_hint=(
                    "Core creates databases implicitly and offers no DROP DATABASE: drop the "
                    "benchmark tables manually, or remove the Core data directory and restart Core"
                ),
            )
            self.log_verbose(f"Firebolt Core: database {database} will be recreated implicitly")
            return

        if not self.check_server_database_exists(database=database):
            self.log_verbose(f"Database {database} does not exist - nothing to drop")
            return

        try:
            params = self._get_connection_params()
            params["database"] = "information_schema"

            conn = firebolt_connect(**params)
            cursor = conn.cursor()

            try:
                cursor.execute(f"DROP DATABASE IF EXISTS {self._quote_identifier(database)}")
                self.logger.info(f"Dropped database {database}")
            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            raise RuntimeError(f"Failed to drop Firebolt database {database}: {e}") from e

    def create_connection(self, **connection_config) -> Any:
        mode_str = "Core" if self.deployment_mode == "core" else "Cloud"
        self.log_operation_start(f"Firebolt {mode_str} connection")

        self.handle_existing_database(**connection_config)

        params = self._get_connection_params()

        if "database" in connection_config:
            params["database"] = connection_config["database"]

        self.log_very_verbose(
            f"Firebolt connection params: mode={self.deployment_mode}, database={params.get('database')}"
        )

        try:
            connection = firebolt_connect(**params)

            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()

            if self.disable_result_cache and self.deployment_mode == "cloud":
                self._disable_result_cache(connection)

            endpoint = self.url if self.deployment_mode == "core" else f"{self.account_name}/{self.engine_name}"
            self.logger.info(f"Connected to Firebolt {mode_str} at {endpoint}")

            self.log_operation_complete(
                f"Firebolt {mode_str} connection",
                details=f"Connected to {endpoint}",
            )

            return connection

        except Exception as e:
            self.logger.error(f"Failed to connect to Firebolt: {e}")
            raise

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()

        cursor = connection.cursor()

        try:
            schema_sql = self._create_schema_with_tuning(benchmark, source_dialect=self.get_target_dialect())

            for statement in self._translate_schema_ddl(schema_sql):
                try:
                    cursor.execute(statement)
                    self.logger.debug(f"Executed schema statement: {statement[:100]}...")
                except Exception as e:
                    if "already exists" in str(e).lower():
                        table_name = self._extract_table_name(statement)
                        if table_name:
                            cursor.execute(f"DROP TABLE IF EXISTS {self._quote_identifier(table_name)}")
                            cursor.execute(statement)
                    else:
                        raise

            self.logger.info("Schema created")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise
        finally:
            cursor.close()

        return elapsed_seconds(start_time)

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        use_s3_staging = self.deployment_mode == "cloud" and self.s3_staging_url is not None

        if use_s3_staging:
            return self._load_data_via_s3(benchmark, connection, data_dir)
        return self._load_data_via_insert(benchmark, connection, data_dir)

    _normalize_existing_files = staticmethod(normalize_existing_files)

    def _resolve_data_files(self, benchmark, data_dir: Path) -> Any:
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
        return DataSource(
            source_type=data_source.source_type,
            tables={table: [Path(p) for p in paths] for table, paths in data_source.tables.items()},
            table_formats=dict(data_source.table_formats),
            table_metadata=dict(data_source.table_metadata),
        )

    def _load_data_via_insert(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        start_time = mono_time()
        table_stats = {}

        cursor = connection.cursor()
        placeholder = self._get_parameter_placeholder(cursor)

        try:
            data_source = self._resolve_data_files(benchmark, data_dir)
            from benchbox.platforms.base.data_loading import DataSource, resolve_csv_dialect

            if not isinstance(data_source, DataSource):
                data_source = DataSource(source_type="legacy_test_mapping", tables=data_source)

            for table_name, file_paths in data_source.tables.items():
                valid_files = self._normalize_existing_files(file_paths)

                if not valid_files:
                    self.logger.warning(f"Skipping {table_name} - no valid data files")
                    table_stats[table_name.lower()] = 0
                    continue

                chunk_info = f" from {len(valid_files)} file(s)" if len(valid_files) > 1 else ""
                self.log_verbose(f"Loading data for table: {table_name}{chunk_info}")

                try:
                    load_start = mono_time()
                    table_name_lower = table_name.lower()
                    table_name_quoted = self._quote_identifier(table_name_lower)
                    total_rows_loaded = 0

                    for file_path in valid_files:
                        file_path = Path(file_path)

                        dialect = resolve_csv_dialect(data_source, table_name, file_path, benchmark)
                        delimiter = dialect.delimiter

                        compression_handler = FileFormatRegistry.get_compression_handler(file_path)

                        with compression_handler.open(file_path) as f:
                            batch_size = 500
                            batch_rows: list[tuple[Any, ...]] = []
                            insert_sql: str | None = None
                            column_count: int | None = None

                            for raw_line in f:
                                line = raw_line.rstrip("\n")
                                if dialect.null_marker is not None and line and line.endswith(delimiter):
                                    line = line[:-1]

                                if not line:
                                    continue

                                values = line.split(delimiter)

                                if column_count is None:
                                    column_count = len(values)
                                    insert_sql = (
                                        f"INSERT INTO {table_name_quoted} VALUES "
                                        f"({', '.join([placeholder for _ in range(column_count)])})"
                                    )
                                elif len(values) != column_count:
                                    raise ValueError(
                                        f"Inconsistent column count in {file_path}: "
                                        f"expected {column_count}, got {len(values)}"
                                    )

                                if dialect.null_marker is None:
                                    converted_values = tuple(values)
                                else:
                                    converted_values = tuple(
                                        None if v == dialect.null_marker or v.lower() == "null" else v for v in values
                                    )
                                batch_rows.append(converted_values)

                                if len(batch_rows) >= batch_size:
                                    self._execute_batch_insert(cursor, insert_sql, batch_rows)
                                    total_rows_loaded += len(batch_rows)
                                    batch_rows = []

                            if batch_rows:
                                self._execute_batch_insert(cursor, insert_sql, batch_rows)
                                total_rows_loaded += len(batch_rows)

                    table_stats[table_name_lower] = total_rows_loaded

                    load_time = elapsed_seconds(load_start)
                    self.logger.info(
                        f"Loaded {total_rows_loaded:,} rows into {table_name_lower}{chunk_info} in {load_time:.2f}s"
                    )

                except Exception as e:
                    error_message = str(e) or repr(e) or type(e).__name__
                    self.logger.error(f"Failed to load {table_name}: {error_message}")
                    table_stats[table_name.lower()] = 0

            total_time = elapsed_seconds(start_time)
            total_rows = sum(table_stats.values())
            self.logger.info(f"Loaded {total_rows:,} total rows in {total_time:.2f}s")

        except Exception as e:
            self.logger.error(f"Data loading failed: {e}")
            raise
        finally:
            cursor.close()

        return table_stats, total_time, None

    def _load_data_via_s3(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "boto3 is required for S3 staging data loading.\n"
                "Install with: uv add boto3\n"
                "Alternatively, remove --firebolt-s3-staging-url to use INSERT batching."
            ) from None

        start_time = mono_time()
        table_stats = {}

        aws_key_id = os.environ.get("AWS_ACCESS_KEY_ID", "")
        aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")

        s3_client_kwargs: dict[str, Any] = {}
        if self.s3_region:
            s3_client_kwargs["region_name"] = self.s3_region
        s3_client = boto3.client("s3", **s3_client_kwargs)

        s3_bucket, s3_prefix = self._parse_s3_url(self.s3_staging_url)

        cursor = connection.cursor()

        try:
            data_source = self._resolve_data_files(benchmark, data_dir)
            from benchbox.platforms.base.data_loading import DataSource, resolve_csv_dialect

            if not isinstance(data_source, DataSource):
                data_source = DataSource(source_type="legacy_test_mapping", tables=data_source)

            for table_name, file_paths in data_source.tables.items():
                if not isinstance(file_paths, list):
                    file_paths = [file_paths]

                valid_files = []
                for file_path in file_paths:
                    file_path = Path(file_path)
                    if file_path.exists() and file_path.stat().st_size > 0:
                        valid_files.append(file_path)

                if not valid_files:
                    self.logger.warning(f"Skipping {table_name} - no valid data files")
                    table_stats[table_name.lower()] = 0
                    continue

                chunk_info = f" from {len(valid_files)} file(s)" if len(valid_files) > 1 else ""
                self.log_verbose(f"Loading data for table (S3 staging): {table_name}{chunk_info}")

                try:
                    load_start = mono_time()
                    table_name_lower = table_name.lower()
                    table_name_quoted = self._quote_identifier(table_name_lower)
                    total_rows_loaded = 0

                    for file_path in valid_files:
                        file_path = Path(file_path)

                        dialect = resolve_csv_dialect(data_source, table_name, file_path, benchmark)
                        delimiter = dialect.delimiter

                        s3_key = f"{s3_prefix}{table_name_lower}/{file_path.name}"
                        s3_file_url = f"s3://{s3_bucket}/{s3_key}"

                        upload_start = mono_time()
                        self.log_verbose(f"Uploading {file_path.name} to {s3_file_url}")
                        s3_client.upload_file(str(file_path), s3_bucket, s3_key)
                        upload_time = elapsed_seconds(upload_start)
                        self.log_verbose(f"Uploaded {file_path.name} in {upload_time:.2f}s")

                        s3_type = "CSV" if delimiter == "," else "TSV"

                        safe_key_id = aws_key_id.replace("'", "''")
                        safe_secret = aws_secret_key.replace("'", "''")

                        ingest_sql = (
                            f"INSERT INTO {table_name_quoted} "
                            f"SELECT * FROM s3("
                            f"'{s3_file_url}', "
                            f"'{safe_key_id}', "
                            f"'{safe_secret}', "
                            f"'{s3_type}'"
                            f")"
                        )

                        ingest_start = mono_time()
                        cursor.execute(ingest_sql)
                        ingest_time = elapsed_seconds(ingest_start)

                        rows_loaded = getattr(cursor, "rowcount", -1)
                        if rows_loaded is None or rows_loaded < 0:
                            cursor.execute(f"SELECT COUNT(*) FROM {table_name_quoted}")
                            count_result = cursor.fetchone()
                            rows_loaded = count_result[0] if count_result else 0

                        total_rows_loaded += rows_loaded
                        self.log_verbose(
                            f"Ingested {rows_loaded:,} rows from {file_path.name} via S3 in {ingest_time:.2f}s"
                        )

                    table_stats[table_name_lower] = total_rows_loaded

                    load_time = elapsed_seconds(load_start)
                    self.logger.info(
                        f"Loaded {total_rows_loaded:,} rows into {table_name_lower}{chunk_info} "
                        f"via S3 staging in {load_time:.2f}s"
                    )

                except Exception as e:
                    error_message = str(e) or repr(e) or type(e).__name__
                    self.logger.error(f"Failed to load {table_name} via S3: {error_message}")
                    table_stats[table_name.lower()] = 0

            total_time = elapsed_seconds(start_time)
            total_rows = sum(table_stats.values())
            self.logger.info(f"Loaded {total_rows:,} total rows via S3 staging in {total_time:.2f}s")

        except Exception as e:
            self.logger.error(f"S3 staging data loading failed: {e}")
            raise
        finally:
            cursor.close()

        loading_metadata = {
            "loading_method": "s3_staging",
            "s3_staging_url": self.s3_staging_url,
            "s3_region": self.s3_region,
        }

        return table_stats, total_time, loading_metadata

    @staticmethod
    def _parse_s3_url(s3_url: str) -> tuple[str, str]:
        from benchbox.utils.cloud_urls import parse_s3_url

        return parse_s3_url(s3_url)

    def _execute_batch_insert(self, cursor: Any, insert_sql: str, rows: list[tuple[Any, ...]]) -> None:
        if not rows:
            return

        if hasattr(cursor, "executemany"):
            cursor.executemany(insert_sql, rows)
        else:
            for row in rows:
                cursor.execute(insert_sql, row)

    def _get_parameter_placeholder(self, cursor: Any) -> str:
        paramstyle = getattr(cursor, "paramstyle", None) or getattr(cursor, "paramstyle_name", None)
        if paramstyle in {"format", "pyformat"}:
            return "%s"
        return "?"

    def _coerce_bool(self, value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip().lower() not in {"false", "0", "no", "off"}
        return bool(value)

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self.log_verbose(f"Configuring Firebolt for {benchmark_type} benchmark")

        if benchmark_type.lower() in ["olap", "analytics", "tpch", "tpcds"]:
            self.log_verbose("Firebolt vectorized engine optimized for analytical workloads")

    def _extract_table_name(self, statement: str) -> str | None:
        from benchbox.core.sql_utils import extract_table_name

        return extract_table_name(statement)

    def _translate_schema_ddl(self, schema_sql: str) -> list[str]:
        from benchbox.utils.dialect_utils import translate_ddl

        return translate_ddl(
            schema_sql,
            self.get_target_dialect(),
            strip_primary_keys=True,
            strip_foreign_keys=True,
            type_map={"VARCHAR": "TEXT", "CHAR": "TEXT", "DECIMAL": "NUMERIC"},
            drop_type_parameters=["TEXT"],
            table_case="lower",
        )

    def _quote_identifier(self, name: str) -> str:
        if not isinstance(name, str) or not name:
            raise ValueError("Identifier must be a non-empty string")
        return '"' + name.replace('"', '""') + '"'

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        from benchbox.platforms.base.sql_execution import get_query_plan_from_cursor

        return get_query_plan_from_cursor(connection, query)

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.firebolt import FireboltQueryPlanParser

        return FireboltQueryPlanParser()

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
        return self.execute_query_with_plan_capture(
            super().execute_query,
            connection,
            query,
            query_id,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )

    def close_connection(self, connection: Any) -> None:
        try:
            if connection and hasattr(connection, "close"):
                connection.close()
        except Exception as e:
            self.logger.warning(f"Error closing connection: {e}")

    def test_connection(self) -> bool:
        try:
            params = self._get_connection_params()
            conn = firebolt_connect(**params)
            cursor = conn.cursor()

            try:
                cursor.execute("SELECT 1")
                cursor.fetchone()
                return True
            finally:
                cursor.close()
                conn.close()
        except Exception as e:
            self.logger.debug(f"Connection test failed: {e}")
            return False

    _supported_tuning_type_names = ("PARTITIONING", "DISTRIBUTION")

    def generate_tuning_clause(self, table_tuning) -> str:
        if not table_tuning or not table_tuning.has_any_tuning():
            return ""

        clauses = []

        try:
            from benchbox.core.tuning.interface import TuningType

            distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
            if distribution_columns:
                sorted_cols = sorted(distribution_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                clauses.append(f"PRIMARY INDEX ({', '.join(column_names)})")

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                clauses.append(f"PARTITION BY {', '.join(column_names)}")

        except ImportError:
            pass

        return " ".join(clauses) if clauses else ""

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        from benchbox.platforms.base.tuning_utils import log_partition_tunings

        log_partition_tunings(table_tuning, self.logger, "Firebolt")

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

        apply_standard_unified_tuning(self, unified_config, connection)

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        if not platform_config:
            return

        self.logger.info("Firebolt platform optimizations noted (engine pre-optimized for analytics)")

    apply_constraint_configuration = make_informational_constraint_applier(
        "Primary key constraints enabled for Firebolt (informational only, not enforced)",
        "Foreign key constraints enabled for Firebolt (informational only, not enforced)",
    )

    _get_existing_tables = staticmethod(show_tables_lower)

    def analyze_table(self, connection: Any, table_name: str) -> None:
        self.logger.debug(f"Firebolt collects statistics automatically - skipping explicit ANALYZE for {table_name}")

    def _disable_result_cache(self, connection: Any) -> None:
        if self.deployment_mode != "cloud":
            self.log_very_verbose("Result cache control only applicable to Cloud mode")
            return

        cursor = connection.cursor()
        try:
            cursor.execute("SET enable_result_cache = false")
            self.log_verbose("Disabled Firebolt result cache for accurate benchmarking")

            if self.strict_validation:
                self.validate_session_cache_control(connection)

        except Exception as e:
            msg = f"Failed to disable Firebolt result cache: {e}"
            if self.strict_validation:
                raise ConfigurationError(msg) from e
            self.logger.warning(msg)
        finally:
            cursor.close()

    def validate_session_cache_control(self, connection: Any) -> bool:
        if self.deployment_mode != "cloud":
            return True

        cursor = connection.cursor()
        try:
            cursor.execute("SHOW enable_result_cache")
            result = cursor.fetchone()

            if result:
                cache_enabled = str(result[0]).lower() in ("true", "1", "on")
                if cache_enabled:
                    msg = "Result cache is still enabled - benchmark results may be cached"
                    if self.strict_validation:
                        raise ConfigurationError(msg)
                    self.logger.warning(msg)
                    return False

            self.log_very_verbose("Validated: result cache is disabled")
            return True

        except Exception as e:
            if "SHOW" in str(e).upper():
                self.logger.debug(f"Could not validate cache settings (SHOW not supported): {e}")
                return True
            if self.strict_validation:
                raise ConfigurationError(f"Cache validation failed: {e}") from e
            self.logger.warning(f"Could not validate cache settings: {e}")
            return False
        finally:
            cursor.close()

    def _create_admin_connection(self) -> Any:
        params = self._get_connection_params()

        if self.deployment_mode == "cloud":
            params["database"] = "information_schema"

        try:
            conn = firebolt_connect(**params)
            self.log_very_verbose(f"Created Firebolt admin connection (mode={self.deployment_mode})")
            return conn
        except Exception as e:
            self.logger.error(f"Failed to create admin connection: {e}")
            raise

    def _get_platform_metadata(self, connection: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "mode": self.deployment_mode,
            "database": self.database,
        }

        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version()")
            result = cursor.fetchone()
            if result:
                metadata["version"] = result[0]

            if self.deployment_mode == "cloud":
                metadata["account_name"] = self.account_name
                metadata["engine_name"] = self.engine_name
                metadata["api_endpoint"] = self.api_endpoint

                try:
                    cursor.execute(
                        "SELECT engine_name, engine_type, status "
                        "FROM information_schema.engines "
                        f"WHERE engine_name = '{self.engine_name}'"
                    )
                    engine_info = cursor.fetchone()
                    if engine_info:
                        metadata["engine_type"] = engine_info[1]
                        metadata["engine_status"] = engine_info[2]
                except Exception as e:
                    self.logger.debug(f"Could not fetch engine details: {e}")
            else:
                metadata["url"] = self.url

        except Exception as e:
            self.logger.debug(f"Error collecting platform metadata: {e}")
        finally:
            cursor.close()

        return metadata


_build_firebolt_config = make_registered_platform_config_builder(
    "firebolt",
    __name__,
    "Firebolt",
    "firebolt-sdk",
    [
        "url",
        "client_id",
        "client_secret",
        "account_name",
        "engine_name",
        "api_endpoint",
        "database",
        "region",
        "cloud_region",
        "cloud_provider",
        "engine_type",
        "engine_size",
        "compute_size",
        "deployment_mode",
        "s3_staging_url",
        "s3_region",
        "disable_result_cache",
        "strict_validation",
    ],
)
