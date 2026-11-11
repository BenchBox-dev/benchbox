# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any

from ..utils.dependencies import (
    check_platform_dependencies,
    get_dependency_error_message,
)
from .base import DriverIsolationCapability
from .presto_trino_adapter_base import PrestoTrinoAdapterBase

try:
    import prestodb
    from prestodb.auth import BasicAuthentication as PrestoBasicAuthentication
except ImportError:
    prestodb = None
    PrestoBasicAuthentication = None

PRESTO_DIALECT = "presto"


class PrestoAdapter(PrestoTrinoAdapterBase):
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    supports_external_tables = True
    platform_key = "presto"
    platform_log_name = "Presto"
    local_start_hint = "Start your local coordinator before rerunning BenchBox,"
    unavailable_catalog_marker = "does not exist on the Presto server"
    default_username = "presto"
    table_format_choices = ("memory", "hive")
    ddl_memory_catalog_names = ("memory",)
    target_dialect = PRESTO_DIALECT
    bulk_native_compression = frozenset({".gz"})
    bulk_null_format_property = None
    bulk_field_delimiter_property = "textfile_field_delim"
    uses_client_source = True
    from_config_optional_fields = (*PrestoTrinoAdapterBase.from_config_optional_fields, "source")
    memory_error_markers = ("MEMORY_LIMIT_EXCEEDED", "exceeds max memory")
    info_platform_name = "PrestoDB"
    include_dialect_in_info = True
    include_session_properties_in_info = True
    include_client_source_in_info = True
    use_version_fallback = True
    default_display_name = "Presto"
    default_driver_package = "presto-python-client"
    platform_config_fields = PrestoTrinoAdapterBase.common_platform_config_fields
    supported_tuning_type_names = ("PARTITIONING",)
    qualify_analyze_table = True
    runtime_planning_column = "distributed_planning_time_ms"
    driver_module_attr = "prestodb"
    olap_session_settings = (
        "SET SESSION optimize_hash_generation = true",
        "SET SESSION join_reordering_strategy = 'AUTOMATIC'",
        "SET SESSION join_distribution_type = 'AUTOMATIC'",
    )

    def __init__(self, **config):
        super().__init__(**config)

        if not prestodb:
            available, missing = check_platform_dependencies("presto")
            if not available:
                error_msg = get_dependency_error_message("presto", missing)
                raise ImportError(error_msg)

        self._init_connection_config(config)

    @property
    def platform_name(self) -> str:
        return "Presto"

    def _connect_with_params(self, params: dict[str, Any]) -> Any:
        return prestodb.dbapi.connect(**params)

    def _catalog_listing_params(self) -> dict[str, Any]:
        params = self._bootstrap_connection_params()
        params["catalog"] = "system"
        params["schema"] = "runtime"
        return params

    def _get_connection_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "user": self.username,
            "catalog": self.catalog,
            "schema": self.schema,
            "http_scheme": self.http_scheme,
            "source": self.source,
        }

        if self.password and PrestoBasicAuthentication:
            params["auth"] = PrestoBasicAuthentication(self.username, self.password)

        if self.http_scheme == "https":
            requests_kwargs = {}
            if self.ssl_cert_path:
                requests_kwargs["verify"] = self.ssl_cert_path
            else:
                requests_kwargs["verify"] = self.verify_ssl
            if requests_kwargs:
                params["requests_kwargs"] = requests_kwargs

        if self.session_properties:
            params["session_properties"] = self.session_properties

        if self.query_timeout and self.query_timeout > 0:
            params["request_timeout"] = self.query_timeout

        return params

    def drop_database(self, **connection_config) -> None:
        schema = connection_config.get("schema", self.schema)
        catalog = connection_config.get("catalog", self.catalog)

        if not self._validate_identifier(catalog) or not self._validate_identifier(schema):
            raise ValueError(f"Invalid catalog or schema identifier: {catalog}.{schema}")

        if not self.check_server_database_exists(schema=schema, catalog=catalog):
            self.log_verbose(f"Schema {catalog}.{schema} does not exist - nothing to drop")
            return

        try:
            params = self._get_connection_params()
            params["catalog"] = catalog
            params["schema"] = schema

            conn = prestodb.dbapi.connect(**params)
            cursor = conn.cursor()

            try:
                cursor.execute("SHOW TABLES")
                tables = [row[0] for row in cursor.fetchall()]

                for table in tables:
                    if self._validate_identifier(table):
                        try:
                            cursor.execute(f"DROP TABLE IF EXISTS {catalog}.{schema}.{table}")
                            self.logger.info(f"Dropped table {table}")
                        except Exception as table_error:
                            self.logger.warning(f"Failed to drop table {table}: {table_error}")

                cursor.execute(f"DROP SCHEMA IF EXISTS {catalog}.{schema}")
                self.logger.info(f"Dropped schema {catalog}.{schema}")
            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            raise RuntimeError(f"Failed to drop Presto schema {catalog}.{schema}: {e}") from e

    def generate_tuning_clause(self, table_tuning) -> str:
        if not table_tuning or not table_tuning.has_any_tuning():
            return ""

        clauses = []

        try:
            from benchbox.core.tuning.interface import TuningType

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns and self.table_format == "hive":
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                clauses.append(f"PARTITIONED BY ({', '.join(column_names)})")

        except ImportError:
            pass

        if clauses:
            return " ".join(clauses)
        return ""

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        from benchbox.platforms.base.tuning_utils import log_partition_tunings

        log_partition_tunings(table_tuning, self.logger, "Presto")


try:
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

    PlatformHookRegistry.register_config_builder("presto", PrestoAdapter.build_platform_config)
except ImportError:
    pass

_build_presto_config = PrestoAdapter.build_platform_config
