from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path
from typing import Any

from benchbox.core.benchmark_mixins import CursorValidationQueryExecutionMixin
from benchbox.platforms.base.data_loading import resolve_adapter_data_source
from benchbox.platforms.base.external_table_mixin import HiveExternalTableMixin
from benchbox.platforms.presto_trino_utils import (
    load_file_batches,
    normalize_existing_files,
    show_tables_lower,
    validate_catalog_exists,
)
from benchbox.utils.clock import elapsed_seconds, mono_time

from .base import PlatformAdapter


class PrestoTrinoAdapterBase(CursorValidationQueryExecutionMixin, HiveExternalTableMixin, PlatformAdapter):
    platform_key = ""
    platform_log_name = ""
    local_start_hint = ""
    unavailable_catalog_marker = ""
    default_username = ""
    default_table_format = "memory"
    table_format_choices = ("memory",)
    target_dialect = ""
    default_service_port = 8080
    uses_client_source = False
    from_config_optional_fields = (
        "http_scheme",
        "verify_ssl",
        "ssl_cert_path",
        "session_properties",
        "query_timeout",
        "disable_result_cache",
        "table_format",
        "staging_root",
        "source_catalog",
    )
    extra_config_defaults: dict[str, Any] = {}
    olap_session_settings: tuple[str, ...] = ()
    memory_error_markers = ("MEMORY_LIMIT_EXCEEDED",)
    persistent_catalog_hint = "hive, iceberg, or delta catalogs"
    info_platform_name = ""
    include_dialect_in_info = False
    include_session_properties_in_info = False
    include_client_source_in_info = False
    set_engine_version_info = False
    use_version_fallback = False
    default_display_name = ""
    default_driver_package = ""
    platform_config_fields: tuple[str, ...] = ()
    common_platform_config_fields = tuple(
        "host port catalog username password http_scheme verify_ssl ssl_cert_path "  # noqa: SIM905
        "session_properties query_timeout table_format staging_root schema".split()
    )
    supported_tuning_type_names: tuple[str, ...] = ()
    qualify_analyze_table = False
    driver_module_attr = ""

    def _init_connection_config(self, config: dict[str, Any]) -> None:
        self._dialect = self.target_dialect
        self.host = config.get("host") or "localhost"
        self.port = config.get("port") if config.get("port") is not None else 8080
        self.catalog = config.get("catalog")
        self.schema = config.get("schema") or "default"
        self.username = config.get("username") or config.get("user") or self.default_username
        self.password = config.get("password")
        self.http_scheme = config.get("http_scheme") or ("https" if self.password else "http")
        self.verify_ssl = config.get("verify_ssl") if config.get("verify_ssl") is not None else True
        self.ssl_cert_path = config.get("ssl_cert_path")
        self.session_properties = config.get("session_properties") or {}
        self._catalog_was_auto_selected = False
        self.query_timeout = config.get("query_timeout") if config.get("query_timeout") is not None else 0
        self.disable_result_cache = config.get("disable_result_cache", True)
        self.table_format = config.get("table_format") or self.default_table_format
        self.staging_root = config.get("staging_root")
        self.source_catalog = config.get("source_catalog")
        if self.uses_client_source:
            self.source = config.get("source") or "BenchBox"
        for attr, default in self.extra_config_defaults.items():
            setattr(self, attr, config.get(attr, default))

    @classmethod
    def add_cli_arguments(cls, parser) -> None:
        group = parser.add_argument_group(f"{cls.platform_log_name} Arguments")
        group.add_argument(
            "--host", type=str, default="localhost", help=f"{cls.platform_log_name} coordinator hostname"
        )
        group.add_argument("--port", type=int, default=8080, help=f"{cls.platform_log_name} coordinator port")
        group.add_argument("--catalog", type=str, help="Catalog for queries (e.g., hive, iceberg)")
        group.add_argument("--schema", type=str, default="default", help="Default schema within the catalog")
        group.add_argument("--username", type=str, default=cls.default_username, help="Username for authentication")
        group.add_argument("--password", type=str, help="Password for basic authentication")
        group.add_argument(
            "--http-scheme", type=str, choices=["http", "https"], help="HTTP scheme (auto-detected based on password)"
        )
        group.add_argument(
            "--table-format",
            type=str,
            choices=list(cls.table_format_choices),
            default=cls.default_table_format,
            help="Table format for creating benchmark tables",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.platforms.base.config_utils import build_adapter_config

        return cls(
            **build_adapter_config(
                config,
                platform=cls.platform_key,
                generated_key="schema",
                fields=("host", "port", "catalog", "username", "password", *cls.from_config_optional_fields),
            )
        )

    def get_target_dialect(self) -> str:
        return self.target_dialect

    @classmethod
    def build_platform_config(cls, platform: str, options: dict[str, Any], overrides: dict[str, Any], info: Any) -> Any:
        from benchbox.platforms.base.config_utils import build_platform_config

        return build_platform_config(
            platform_type=cls.platform_key,
            credential_key=cls.platform_key,
            default_display_name=cls.default_display_name,
            default_driver_package=cls.default_driver_package,
            platform_fields=list(cls.platform_config_fields),
            options=options,
            overrides=overrides,
            info=info,
        )

    def _client_library_version(self) -> str | None:
        module = getattr(sys.modules.get(self.__module__), self.driver_module_attr, None)
        return getattr(module, "__version__", None) if module else None

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        configuration: dict[str, Any] = {
            "catalog": self.catalog,
            "schema": self.schema,
            "http_scheme": self.http_scheme,
            "table_format": self.table_format,
            "result_cache_disabled": self.disable_result_cache,
        }
        if self.include_session_properties_in_info:
            configuration["session_properties"] = dict(self.session_properties) if self.session_properties else {}
        if self.include_client_source_in_info:
            configuration["client_source"] = self.source

        platform_info = {
            "platform_type": self.platform_key,
            "platform_name": self.info_platform_name or self.platform_log_name,
            "connection_mode": "remote",
            "host": self.host,
            "port": self.port,
            "configuration": configuration,
            "client_library_version": self._client_library_version(),
        }
        if self.include_dialect_in_info:
            platform_info["dialect"] = self.target_dialect

        if not connection:
            platform_info["platform_version"] = None
            return platform_info

        cursor = None
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT node_version FROM system.runtime.nodes WHERE coordinator = true LIMIT 1")
                result = cursor.fetchone()
                platform_info["platform_version"] = result[0] if result else None
            except Exception as primary_error:
                self.logger.debug(f"Primary {self.platform_log_name} version query failed: {primary_error}")
                platform_info["platform_version"] = None

            if self.use_version_fallback and platform_info.get("platform_version") is None:
                try:
                    cursor.execute("SELECT version()")
                    result = cursor.fetchone()
                    platform_info["platform_version"] = result[0] if result else None
                except Exception as fallback_error:
                    self.logger.debug(f"Fallback {self.platform_log_name} version query failed: {fallback_error}")

            if self.set_engine_version_info:
                platform_info["engine_version"] = platform_info.get("platform_version")
                platform_info["engine_version_source"] = "sql_query"

            try:
                cursor.execute("SELECT count(*) FROM system.runtime.nodes")
                result = cursor.fetchone()
                if result:
                    configuration["node_count"] = result[0]
            except Exception as node_error:
                self.logger.debug(f"Error collecting {self.platform_log_name} node count: {node_error}")

            try:
                cursor.execute("SHOW CATALOGS")
                configuration["available_catalogs"] = [row[0] for row in cursor.fetchall()]
            except Exception as catalog_error:
                self.logger.debug(f"Error collecting {self.platform_log_name} catalogs: {catalog_error}")

        except Exception as e:
            self.logger.debug(f"Error collecting {self.platform_log_name} platform info: {e}")
            if platform_info.get("platform_version") is None:
                platform_info["platform_version"] = None
        finally:
            if cursor:
                cursor.close()

        return platform_info

    def _connect_with_params(self, params: dict[str, Any]) -> Any:
        raise NotImplementedError

    def _catalog_listing_params(self) -> dict[str, Any]:
        params = self._bootstrap_connection_params()
        params["schema"] = "information_schema"
        return params

    def _bootstrap_connection_params(self) -> dict[str, Any]:
        params = self._get_connection_params()
        params.pop("session_properties", None)
        return params

    def _get_available_catalogs(self) -> list[str]:
        try:
            conn = self._connect_with_params(self._catalog_listing_params())
            cursor = conn.cursor()
            try:
                cursor.execute("SHOW CATALOGS")
                return [row[0] for row in cursor.fetchall()]
            finally:
                cursor.close()
                conn.close()
        except Exception as e:
            self.logger.debug(f"Error getting available catalogs: {e}")
            return []

    def _auto_select_catalog(self) -> str | None:
        available = self._get_available_catalogs()
        if not available:
            return None

        usable_catalogs = [catalog for catalog in available if catalog not in {"jmx", "system"}]
        if not usable_catalogs:
            return None

        for catalog in ["hive", "iceberg", "delta", "memory", "tpch"]:
            if catalog in usable_catalogs:
                return catalog
        return usable_catalogs[0]

    def _validate_catalog_exists(self, catalog: str | None) -> str:
        validated_catalog, auto_selected = validate_catalog_exists(
            catalog,
            platform_name=self.platform_log_name,
            auto_select_catalog=self._auto_select_catalog,
            get_available_catalogs=self._get_available_catalogs,
            logger=self.logger,
        )
        if auto_selected:
            self._catalog_was_auto_selected = True
        return validated_catalog

    def check_server_database_exists(self, **connection_config) -> bool:
        try:
            catalog = connection_config.get("catalog", self.catalog)
            schema = connection_config.get("schema", self.schema)
            validated_catalog = self._validate_catalog_exists(catalog)

            if not self.catalog or self._catalog_was_auto_selected:
                self.catalog = validated_catalog
            catalog = validated_catalog

            if not self._validate_identifier(catalog) or not self._validate_identifier(schema):
                self.logger.warning(f"Invalid catalog or schema identifier: {catalog}.{schema}")
                return False

            params = self._bootstrap_connection_params()
            params["catalog"] = catalog
            params["schema"] = "information_schema"

            conn = self._connect_with_params(params)
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"SELECT schema_name FROM information_schema.schemata "
                    f"WHERE catalog_name = '{catalog}' AND schema_name = '{schema}'"
                )
                return cursor.fetchone() is not None
            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            if (
                "ConfigurationError" in type(e).__name__
                or "server is unreachable" in str(e)
                or self.unavailable_catalog_marker in str(e)
            ):
                raise
            self.logger.debug(f"Error checking schema existence: {e}")
            return False

    def _validate_identifier(self, identifier: str) -> bool:
        if not identifier:
            return False
        return bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_-]*$", identifier)) and len(identifier) <= 128

    def _save_auto_selected_catalog(self) -> None:
        try:
            from benchbox.security.credentials import CredentialManager

            cred_manager = CredentialManager()
            existing = cred_manager.get_platform_credentials(self.platform_key) or {}
            existing["catalog"] = self.catalog
            existing["host"] = self.host
            existing["port"] = self.port

            cred_manager.save_platform_credentials(self.platform_key, existing)
            self.logger.info(
                f"Saved '{self.catalog}' as default catalog for future {self.platform_key} runs. "
                f"Override with --platform-option catalog=<name> or edit ~/.benchbox/credentials.yaml"
            )
        except Exception as e:
            self.logger.debug(f"Could not save credentials: {e}")

    def _is_local_host(self, host: str | None) -> bool:
        if not host:
            return False
        normalized = host.strip().lower()
        if normalized in {"localhost", "127.0.0.1", "::1"}:
            return True
        try:
            return ipaddress.ip_address(normalized).is_loopback
        except ValueError:
            return normalized.endswith(".local")

    def _error_indicates_connection_refused(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in [
                "connection refused",
                "failed to establish a new connection",
                "max retries exceeded",
                "errno 61",
                "errno 111",
            ]
        )

    def _build_friendly_connection_error(self, exc: Exception) -> str | None:
        if not self._is_local_host(self.host) or not self._error_indicates_connection_refused(exc):
            return None

        host = self.host or "localhost"
        port = self.port or 8080
        return (
            f"{self.platform_log_name} is not running on {host}:{port}. {self.local_start_hint}"
            " or configure host and port for a running cluster in the platform "
            "configuration before retrying."
        )

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start(f"{self.platform_log_name} connection")
        self.handle_existing_database(**connection_config)

        params = self._get_connection_params()
        for key in ["host", "port", "catalog", "schema"]:
            if key in connection_config:
                params[key] = connection_config[key]

        target_schema = params.get("schema", self.schema)
        target_catalog = params.get("catalog", self.catalog)

        if not self.database_was_reused and not self.check_server_database_exists(
            schema=target_schema, catalog=target_catalog
        ):
            if target_catalog is None and self.catalog:
                target_catalog = self.catalog
                params["catalog"] = target_catalog
            self.log_verbose(f"Creating schema: {target_catalog}.{target_schema}")
            temp_params = params.copy()
            temp_params.pop("session_properties", None)
            temp_params["schema"] = "information_schema"
            temp_conn = self._connect_with_params(temp_params)
            temp_cursor = temp_conn.cursor()
            try:
                temp_cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {target_catalog}.{target_schema}")
                self.logger.info(f"Created schema {target_catalog}.{target_schema}")
            finally:
                temp_cursor.close()
                temp_conn.close()
        elif target_catalog is None and self.catalog:
            target_catalog = self.catalog
            params["catalog"] = target_catalog

        self.log_very_verbose(
            f"{self.platform_log_name} connection params: host={params.get('host')}, catalog={target_catalog}"
        )

        try:
            connection = self._connect_with_params(params)
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()

            self.logger.info(f"Connected to {self.platform_log_name} coordinator at {params['host']}:{params['port']}")
            if self._catalog_was_auto_selected:
                self._save_auto_selected_catalog()
            self.log_operation_complete(
                f"{self.platform_log_name} connection", details=f"Connected to {params['host']}:{params['port']}"
            )
            return connection

        except Exception as e:
            friendly_message = self._build_friendly_connection_error(e)
            if friendly_message:
                self.logger.error(friendly_message)
                raise RuntimeError(friendly_message) from e
            self.logger.error(f"Failed to connect to {self.platform_log_name}: {e}")
            raise

    ddl_memory_table_formats: tuple[str, ...] = ("memory",)
    ddl_memory_catalog_names: tuple[str, ...] = ()
    ddl_format_property_formats: tuple[str, ...] = ("hive",)
    ddl_no_not_null_formats: tuple[str, ...] = ("hive",)

    def _ddl_options(self) -> dict[str, Any]:
        memory = (
            self.table_format in self.ddl_memory_table_formats or (self.catalog or "") in self.ddl_memory_catalog_names
        )
        with_format = not memory and self.table_format in self.ddl_format_property_formats
        return {
            "strip_primary_keys": True,
            "strip_not_null": memory or self.table_format in self.ddl_no_not_null_formats,
            "table_case": "lower",
            "default_layout": {"format": "PARQUET"} if with_format else None,
        }

    def _schema_ddl(self, schema_sql: str) -> list[str]:
        from benchbox.utils.dialect_utils import translate_ddl

        options = self._ddl_options()
        statements: list[str] = []
        for statement in (part.strip() for part in schema_sql.split(";")):
            if statement:
                statements.extend(translate_ddl(statement, self.get_target_dialect(), **options))
        return statements

    def create_schema(self, benchmark, connection: Any) -> float:
        from benchbox.platforms.presto_trino_utils import execute_schema_statements

        start_time = mono_time()
        schema_sql = self._create_schema_with_tuning(benchmark, source_dialect=self.get_target_dialect())
        execute_schema_statements(
            statements=self._schema_ddl(schema_sql),
            connection=connection,
            logger=self.logger,
            extract_table_name=self._extract_table_name,
            log_notice=self.log_notice,
        )
        return elapsed_seconds(start_time)

    def _resolve_data_files(self, benchmark: Any, data_dir: Path) -> dict[str, Any]:
        return resolve_adapter_data_source(self, benchmark, data_dir).tables

    def _load_table_data(
        self,
        cursor: Any,
        table_name: str,
        file_paths: Any,
        target_catalog: str,
        target_schema: str,
        benchmark: Any | None = None,
        data_source: Any | None = None,
    ) -> tuple[int, int] | None:
        from benchbox.platforms.base.data_loading import resolve_csv_dialect

        valid_files = normalize_existing_files(file_paths)
        table_name_lower = table_name.lower()

        if not valid_files:
            self.logger.warning(f"Skipping {table_name} - no valid data files")
            return None

        if not self._validate_identifier(table_name_lower):
            self.logger.warning(f"Skipping {table_name} - invalid table identifier")
            return None

        chunk_info = f" from {len(valid_files)} file(s)" if len(valid_files) > 1 else ""
        self.log_verbose(f"Loading data for table: {table_name}{chunk_info}")

        dialect = None
        if data_source is not None and benchmark is not None:
            dialect = resolve_csv_dialect(data_source, table_name, valid_files[0], benchmark).delimiter

        qualified_table = f"{target_catalog}.{target_schema}.{table_name_lower}"
        rows_loaded = 0
        for file_path in valid_files:
            rows_loaded += load_file_batches(cursor, file_path, qualified_table, delimiter=dialect)

        return rows_loaded, len(valid_files)

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        start_time = mono_time()
        table_stats = {}
        cursor = connection.cursor()
        target_catalog = self.catalog
        target_schema = self.schema

        if not self._validate_identifier(target_catalog) or not self._validate_identifier(target_schema):
            raise ValueError(f"Invalid catalog or schema for load: {target_catalog}.{target_schema}")

        try:
            data_source = resolve_adapter_data_source(self, benchmark, data_dir)
            for table_name, file_paths in data_source.tables.items():
                try:
                    load_start = mono_time()
                    table_result = self._load_table_data(
                        cursor,
                        table_name,
                        file_paths,
                        target_catalog,
                        target_schema,
                        benchmark=benchmark,
                        data_source=data_source,
                    )
                    table_name_lower = table_name.lower()
                    if table_result is None:
                        table_stats[table_name_lower] = 0
                        continue

                    total_rows_loaded, file_count = table_result
                    table_stats[table_name_lower] = total_rows_loaded
                    chunk_info = f" from {file_count} file(s)" if file_count > 1 else ""
                    self.logger.info(
                        f"✅ Loaded {total_rows_loaded:,} rows into {table_name_lower}{chunk_info} in "
                        f"{elapsed_seconds(load_start):.2f}s"
                    )

                except Exception as e:
                    error_str = str(e)
                    self.logger.error(f"Failed to load {table_name}: {error_str[:100]}...")
                    self._log_memory_limit_guidance(error_str)
                    table_stats[table_name.lower()] = 0

            total_time = elapsed_seconds(start_time)
            total_rows = sum(table_stats.values())
            self.logger.info(f"✅ Loaded {total_rows:,} total rows in {total_time:.2f}s")

        except Exception as e:
            self.logger.error(f"Data loading failed: {e}")
            raise
        finally:
            cursor.close()

        return table_stats, total_time, None

    def _log_memory_limit_guidance(self, error_str: str) -> None:
        error_text = error_str.lower()
        if not any(marker.lower() in error_text for marker in self.memory_error_markers) or hasattr(
            self, "_memory_error_logged"
        ):
            return

        self._memory_error_logged = True
        name = self.platform_log_name
        self.logger.error(
            "\n"
            f"{name.upper()} MEMORY LIMIT EXCEEDED\n"
            f"The {name} server has insufficient memory for this data load.\n"
            f"Options: increase {name} memory, use a smaller scale factor "
            f"(`benchbox run --platform {self.platform_key} --scale 0.1 ...`), or use "
            f"{self.persistent_catalog_hint} instead of the memory catalog."
        )

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        cursor = connection.cursor()
        try:
            if benchmark_type.lower() not in {"olap", "analytics", "tpch", "tpcds"}:
                return
            for setting in self.olap_session_settings:
                try:
                    cursor.execute(setting)
                    self.logger.debug(f"Applied setting: {setting}")
                except Exception as e:
                    self.logger.warning(f"Failed to apply setting {setting}: {e}")
        finally:
            cursor.close()

    def supports_tuning_type(self, tuning_type: Any) -> bool:
        try:
            from benchbox.core.tuning.interface import TuningType

            return tuning_type in {getattr(TuningType, name) for name in self.supported_tuning_type_names}
        except ImportError:
            return False

    def analyze_table(self, connection: Any, table_name: str) -> None:
        if self.table_format == "memory":
            self.logger.debug(f"ANALYZE not supported for memory catalog - skipping {table_name}")
            return

        table_name_lower = table_name.lower()
        if self.qualify_analyze_table:
            if not self._validate_identifier(table_name_lower):
                self.logger.warning(f"Skipping analyze for invalid table identifier: {table_name}")
                return
            target_table = f"{self.catalog}.{self.schema}.{table_name_lower}"
        else:
            target_table = table_name_lower

        cursor = connection.cursor()
        try:
            cursor.execute(f"ANALYZE {target_table}")
        finally:
            cursor.close()

    def _extract_table_name(self, statement: str) -> str | None:
        from benchbox.core.sql_utils import extract_table_name

        return extract_table_name(statement)

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        from benchbox.platforms.base.sql_execution import get_query_plan_from_cursor

        return get_query_plan_from_cursor(connection, query, explain_prefix="EXPLAIN (FORMAT JSON)", logger=self.logger)

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.presto_trino import PrestoTrinoQueryPlanParser

        return PrestoTrinoQueryPlanParser(platform_name=self.platform_key or "presto_trino")

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
            connection=connection,
            query=query,
            query_id=query_id,
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
            params = self._bootstrap_connection_params()
            params["schema"] = "information_schema"
            conn = self._connect_with_params(params)
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

    def apply_unified_tuning(self, unified_config: Any, connection: Any) -> None:
        from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

        apply_standard_unified_tuning(self, unified_config, connection)

    def apply_platform_optimizations(self, platform_config: Any, connection: Any) -> None:
        if platform_config:
            self.logger.info(f"{self.platform_log_name} platform optimizations applied via session properties")

    def apply_constraint_configuration(self, primary_key_config: Any, foreign_key_config: Any, connection: Any) -> None:
        if primary_key_config and primary_key_config.enabled:
            self.logger.info(
                f"Primary key constraints enabled for {self.platform_log_name} (informational only, not enforced)"
            )

        if foreign_key_config and foreign_key_config.enabled:
            self.logger.info(
                f"Foreign key constraints enabled for {self.platform_log_name} (informational only, not enforced)"
            )

    _get_existing_tables = staticmethod(show_tables_lower)
