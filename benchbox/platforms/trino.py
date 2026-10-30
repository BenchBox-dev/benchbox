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
    import trino
    from trino.auth import BasicAuthentication
except ImportError:
    trino = None
    BasicAuthentication = None


class TrinoAdapter(PrestoTrinoAdapterBase):
    physical_identifier_case = "lower"

    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    supports_external_tables = True
    platform_key = "trino"
    platform_log_name = "Trino"
    local_start_hint = "Start your local coordinator (for example `brew services start trino` or `trino-server run`)"
    unavailable_catalog_marker = "does not exist on the Trino server"
    default_username = "trino"
    table_format_choices = ("memory", "hive", "iceberg", "delta")
    ddl_format_property_formats = ("hive", "iceberg")
    target_dialect = "trino"
    from_config_optional_fields = (*PrestoTrinoAdapterBase.from_config_optional_fields, "timezone", "encoding")
    extra_config_defaults = {"timezone": None, "encoding": None}
    info_platform_name = "Trino"
    set_engine_version_info = True
    default_display_name = "Trino"
    default_driver_package = "trino"
    platform_config_fields = (*PrestoTrinoAdapterBase.common_platform_config_fields, "timezone")
    supported_tuning_type_names = ("PARTITIONING", "SORTING")
    driver_module_attr = "trino"
    olap_session_settings = (
        "SET SESSION optimizer_hash_generation_enabled = true",
        "SET SESSION join_reordering_strategy = 'AUTOMATIC'",
        "SET SESSION join_distribution_type = 'AUTOMATIC'",
    )

    def __init__(self, **config):
        super().__init__(**config)

        if not trino:
            available, missing = check_platform_dependencies("trino")
            if not available:
                error_msg = get_dependency_error_message("trino", missing)
                raise ImportError(error_msg)

        self._init_connection_config(config)

    @property
    def platform_name(self) -> str:
        return "Trino"

    def _connect_with_params(self, params: dict[str, Any]) -> Any:
        return trino.dbapi.connect(**params)

    def _catalog_listing_params(self) -> dict[str, Any]:
        params = self._bootstrap_connection_params()
        params.pop("catalog", None)
        params.pop("schema", None)
        return params

    def _get_connection_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "user": self.username,
            "catalog": self.catalog,
            "schema": self.schema,
            "http_scheme": self.http_scheme,
        }

        if self.password and BasicAuthentication:
            params["auth"] = BasicAuthentication(self.username, self.password)

        if self.http_scheme == "https":
            if self.ssl_cert_path:
                params["verify"] = self.ssl_cert_path
            else:
                params["verify"] = self.verify_ssl

        if self.timezone:
            params["timezone"] = self.timezone

        if self.encoding:
            params["encoding"] = self.encoding

        if self.session_properties:
            params["session_properties"] = self.session_properties

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
            params["schema"] = "information_schema"

            conn = trino.dbapi.connect(**params)
            cursor = conn.cursor()

            try:
                cursor.execute(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE")
                self.logger.info(f"Dropped schema {catalog}.{schema}")
            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            raise RuntimeError(f"Failed to drop Trino schema {catalog}.{schema}: {e}") from e

    def generate_tuning_clause(self, table_tuning) -> str:
        if not table_tuning or not table_tuning.has_any_tuning():
            return ""

        clauses = []

        try:
            from benchbox.core.tuning.interface import TuningType

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns and self.table_format in ("hive", "iceberg"):
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]

                if self.table_format == "hive":
                    clauses.append(f"PARTITIONED BY ({', '.join(column_names)})")
                elif self.table_format == "iceberg":
                    partition_spec = ", ".join([f"'{col}'" for col in column_names])
                    clauses.append(f"partitioning = ARRAY[{partition_spec}]")

            sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
            if sort_columns and self.table_format == "iceberg":
                sorted_cols = sorted(sort_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                sort_spec = ", ".join([f"'{col}'" for col in column_names])
                clauses.append(f"sorted_by = ARRAY[{sort_spec}]")

        except ImportError:
            pass

        if clauses and self.table_format == "iceberg":
            return f"WITH ({', '.join(clauses)})"
        elif clauses:
            return " ".join(clauses)
        return ""

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        if not table_tuning or not table_tuning.has_any_tuning():
            return

        table_name = self.resolve_physical_table(table_tuning.table_name, connection)
        self.logger.info(f"Applying Trino tunings for table: {table_name}")

        try:
            from benchbox.core.tuning.interface import TuningType

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                self.logger.info(f"Partitioning for {table_name}: {', '.join(column_names)}")

            sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
            if sort_columns:
                sorted_cols = sorted(sort_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                self.logger.info(f"Sorting for {table_name}: {', '.join(column_names)}")

        except ImportError:
            self.logger.warning("Tuning interface not available - skipping tuning application")


try:
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

    PlatformHookRegistry.register_config_builder("trino", TrinoAdapter.build_platform_config)
except ImportError:
    pass

_build_trino_config = TrinoAdapter.build_platform_config
