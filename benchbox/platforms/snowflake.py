from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        TuningColumn,
        UnifiedTuningConfiguration,
    )

from ..core.exceptions import ConfigurationError
from ..utils.cloud_storage import get_cloud_path_info, snowflake_stage_mode_error
from ..utils.dependencies import check_platform_dependencies, get_dependency_error_message
from .base import DriverIsolationCapability, PlatformAdapter
from .base.config_utils import make_registered_platform_config_builder
from .base.data_loading import (
    NO_BENCHMARK,
    DataSource,
    resolve_adapter_data_source,
    resolve_csv_dialect,
    run_staged_table_loads,
)
from .base.runtime_metadata import build_default_normalized_result_metadata, with_canonical_compute
from .base.tuning import make_informational_constraint_applier
from .presto_trino_utils import normalize_existing_files

try:
    import snowflake.connector
    from snowflake.connector import DictCursor
except ImportError:
    snowflake = None
    DictCursor = None


def _compact_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if value not in (None, "", {}, [], ())}


class SnowflakeAdapter(PlatformAdapter):
    plan_capture_phase_eligible = True
    supports_refresh_functions = True

    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    supports_external_tables = True
    physical_identifier_case = "upper"

    def __init__(self, **config):
        super().__init__(**config)

        available, missing = check_platform_dependencies("snowflake")
        if not available:
            error_msg = get_dependency_error_message("snowflake", missing)
            raise ImportError(error_msg)

        self._dialect = "snowflake"

        self.account = config.get("account")
        self.warehouse = config.get("compute_resource") or config.get("warehouse") or "COMPUTE_WH"
        self.database = config.get("database") or "BENCHBOX"
        self.schema = config.get("schema") or "PUBLIC"
        self.username = config.get("username")
        self.password = config.get("password")
        self.role = config.get("role")
        self.edition = config.get("edition")

        self.authenticator = config.get("authenticator") or "snowflake"
        self.private_key_path = config.get("private_key_path")
        self.private_key_passphrase = config.get("private_key_passphrase")

        self.warehouse_size = config.get("compute_size") or config.get("warehouse_size") or "MEDIUM"
        self.auto_suspend = config.get("auto_suspend") if config.get("auto_suspend") is not None else 300
        self.auto_resume = config.get("auto_resume") if config.get("auto_resume") is not None else True
        self.multi_cluster_warehouse = (
            config.get("multi_cluster_warehouse") if config.get("multi_cluster_warehouse") is not None else False
        )

        self.query_tag = config.get("query_tag") or "BenchBox"
        self.timezone = config.get("timezone") or "UTC"

        self.disable_result_cache = config.get("disable_result_cache", True)

        self._cache_control_receipt: dict[str, Any] | None = None

        self.strict_validation = config.get("strict_validation", True)

        self.suppress_nondeterministic_errors = config.get("suppress_nondeterministic_errors", False)

        self.modify_warehouse_settings = config.get("modify_warehouse_settings", False)

        self.file_format = config.get("file_format") or "CSV"
        self.compression = config.get("compression") or "AUTO"

        self.staging_root = config.get("staging_root")
        self.table_mode = str(config.get("table_mode") or "native").lower()
        self.iceberg_external_volume = config.get("iceberg_external_volume")
        self.iceberg_catalog = config.get("iceberg_catalog") or "SNOWFLAKE"
        self.delta_table_format = config.get("delta_table_format") or "DELTA"
        if self.staging_root:
            stage_error = snowflake_stage_mode_error(self.staging_root, table_mode=self.table_mode)
            if stage_error:
                raise ValueError(stage_error)
            path_info = get_cloud_path_info(self.staging_root)
            self.logger.info(
                f"staging_root configured for Snowflake external mode ({path_info['provider']}://{path_info['bucket']})"
            )

        if not all([self.account, self.username, self.password, self.warehouse, self.database]):
            missing = []
            if not self.account:
                missing.append("account (or SNOWFLAKE_ACCOUNT)")
            if not self.username:
                missing.append("username (or SNOWFLAKE_USER)")
            if not self.password:
                missing.append("password (or SNOWFLAKE_PASSWORD)")
            if not self.warehouse:
                missing.append("warehouse (or SNOWFLAKE_WAREHOUSE)")
            if not self.database:
                missing.append("database (or SNOWFLAKE_DATABASE)")

            raise ConfigurationError(
                f"Snowflake configuration is incomplete. Missing: {', '.join(missing)}\n"
                "Configure with one of:\n"
                "  1. CLI: benchbox setup --platform snowflake\n"
                "  2. Environment variables: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, etc."
            )

    @property
    def platform_name(self) -> str:
        return "Snowflake"

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[TuningColumn]) -> str | None:
        mode, method = self.resolve_sorted_ingestion_strategy()
        if mode == "off":
            return None

        if method != "ctas":
            raise ValueError(
                f"Sorted ingestion method '{method}' is not supported for Snowflake; use method='ctas' or 'auto'."
            )

        order_by = ", ".join(column.name for column in sort_columns)
        return f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM {table_name} ORDER BY {order_by}"

    @staticmethod
    def add_cli_arguments(parser) -> None:

        sf_group = parser.add_argument_group("Snowflake Arguments")
        sf_group.add_argument("--account", type=str, help="Snowflake account identifier")
        sf_group.add_argument("--warehouse", type=str, default="COMPUTE_WH", help="Warehouse name")
        sf_group.add_argument(
            "--platform", type=str, default=None, help="Database name (auto-generated if not specified)"
        )
        sf_group.add_argument("--schema", type=str, default="PUBLIC", help="Schema name")
        sf_group.add_argument("--username", type=str, help="User name")
        sf_group.add_argument("--password", type=str, help="User password")
        sf_group.add_argument("--role", type=str, help="Role to assume for the session")
        sf_group.add_argument("--authenticator", type=str, default="snowflake", help="Authentication method")
        sf_group.add_argument("--private-key-path", type=str, help="Path to private key for key pair auth")

        sf_group.add_argument(
            "--modify-warehouse-settings",
            action="store_true",
            default=False,
            help="Modify warehouse settings (size, auto-suspend, scaling). PERSISTENT changes - use with caution.",
        )
        sf_group.add_argument(
            "--suppress-nondeterministic-errors",
            action="store_true",
            default=False,
            help="Suppress errors on nondeterministic MERGE/UPDATE operations",
        )
        sf_group.add_argument(
            "--no-disable-result-cache",
            action="store_false",
            dest="disable_result_cache",
            help="Enable result cache (disabled by default for accurate benchmarking)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.platforms.base.config_utils import build_adapter_config

        adapter_config = build_adapter_config(
            config,
            platform="snowflake",
            fields=[
                "account",
                "warehouse",
                "compute_resource",
                "schema",
                "username",
                "password",
                "role",
                "edition",
                "authenticator",
                "private_key_path",
                "private_key_passphrase",
                "warehouse_size",
                "auto_suspend",
                "compute_size",
                "auto_resume",
                "multi_cluster_warehouse",
                "query_tag",
                "timezone",
                "file_format",
                "compression",
                "staging_root",
                "iceberg_external_volume",
                "iceberg_catalog",
                "delta_table_format",
                "disable_result_cache",
                "strict_validation",
                "suppress_nondeterministic_errors",
                "modify_warehouse_settings",
                "force_recreate",
            ],
        )
        if "force_recreate" not in adapter_config and config.get("force", False):
            adapter_config["force_recreate"] = True
        return cls(**adapter_config)

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "snowflake",
            "platform_name": "Snowflake",
            "connection_mode": "remote",
            "configuration": {
                "account": self.account,
                "warehouse": self.warehouse,
                "database": self.database,
                "schema": self.schema,
                "role": self.role,
                "edition": getattr(self, "edition", None),
                "warehouse_size": getattr(self, "warehouse_size", None),
                "auto_suspend": self.auto_suspend,
                "auto_resume": self.auto_resume,
                "multi_cluster_warehouse": self.multi_cluster_warehouse,
                "result_cache_enabled": not self.disable_result_cache,
                "file_format": self.file_format,
                "compression": self.compression,
                "staging_root": self.staging_root,
            },
        }

        try:
            import snowflake.connector

            platform_info["client_library_version"] = snowflake.connector.__version__
        except (ImportError, AttributeError):
            platform_info["client_library_version"] = None

        if connection:
            cursor = None
            try:
                cursor = connection.cursor()

                result = cursor.execute("SELECT current_version()").fetchone()
                platform_info["platform_version"] = result[0] if result else None
                platform_info["engine_version"] = platform_info["platform_version"]
                platform_info["engine_version_source"] = "sql_query"

                try:
                    result = cursor.execute("SELECT CURRENT_REGION()").fetchone()
                    if result and result[0]:
                        region = str(result[0])
                        platform_info["cloud_region"] = region
                        provider = region.split("_", 1)[0].upper()
                        if provider in ("AWS", "AZURE", "GCP"):
                            platform_info["cloud_provider"] = provider
                except Exception as e:
                    self.logger.debug(f"Could not query Snowflake region: {e}")

                if self.warehouse:
                    try:
                        warehouse_escaped = self.warehouse.replace("'", "''")
                        result = cursor.execute(f"SHOW WAREHOUSES LIKE '{warehouse_escaped}'").fetchone()

                        if result:
                            platform_info["compute_configuration"] = {
                                "warehouse_name": result[0] if len(result) > 0 else None,
                                "warehouse_state": result[1] if len(result) > 1 else None,
                                "warehouse_type": result[2] if len(result) > 2 else None,
                                "warehouse_size": result[3] if len(result) > 3 else None,
                                "min_cluster_count": result[4] if len(result) > 4 else None,
                                "max_cluster_count": result[5] if len(result) > 5 else None,
                                "auto_suspend": result[11] if len(result) > 11 else None,
                                "auto_resume": result[12] if len(result) > 12 else None,
                                "enable_query_acceleration": result[21] if len(result) > 21 else None,
                                "query_acceleration_max_scale_factor": result[22] if len(result) > 22 else None,
                                "scaling_policy": result[27] if len(result) > 27 else None,
                            }

                            self.logger.debug(
                                f"Successfully captured Snowflake warehouse metadata for {self.warehouse}"
                            )
                    except Exception as e:
                        platform_info["compute_configuration"] = self._unavailable_warehouse_metadata(e)
                        self.logger.debug(
                            f"Could not fetch Snowflake warehouse metadata (insufficient permissions or warehouse not found): {e}"
                        )

                try:
                    result = cursor.execute("SELECT current_account_name(), current_organization_name()").fetchone()
                    if result:
                        platform_info["configuration"]["account_name"] = result[0]
                        platform_info["configuration"]["organization_name"] = result[1]
                except Exception as e:
                    self.logger.debug(f"Could not query Snowflake account metadata: {e}")

            except Exception as e:
                self.logger.debug(f"Error collecting Snowflake platform info: {e}")
                if platform_info.get("platform_version") is None:
                    platform_info["platform_version"] = None
            finally:
                if cursor:
                    cursor.close()
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

        metadata["platform_deployment"] = self._snowflake_deployment_metadata(config)
        metadata["platform_cloud"] = self._snowflake_cloud_metadata(info, config)
        metadata["platform_compute"] = with_canonical_compute(
            metadata.get("platform_compute"),
            self._snowflake_compute_metadata(
                config, compute, cache_control=getattr(self, "_cache_control_receipt", None)
            ),
        )
        metadata["platform_storage"] = self._snowflake_storage_metadata(config)
        return metadata

    @staticmethod
    def _unavailable_warehouse_metadata(exc: Exception) -> dict[str, Any]:
        return {
            "metadata_collection_status": "unavailable",
            "collection_error_class": type(exc).__name__,
            "collection_error_message": f"{type(exc).__name__}: warehouse metadata collection failed",
        }

    @staticmethod
    def _snowflake_deployment_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        return _compact_metadata(
            {
                "deployment_type": "managed_cloud",
                "connection_mode": "remote",
                "endpoint_class": "cloud_endpoint",
                "metadata_source": "observed" if config.get("account_name") else "requested",
                "collection_status": "partial",
                "role": config.get("role"),
                "database": config.get("database"),
                "schema": config.get("schema"),
            }
        )

    @staticmethod
    def _snowflake_cloud_metadata(info: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        provider = info.get("cloud_provider")
        region = info.get("cloud_region")
        account = config.get("account_name") or config.get("account")
        payload = {
            "provider": str(provider).lower() if provider else None,
            "region": region,
            "account": account,
            "organization": config.get("organization_name"),
            "source": "observed" if provider or region or config.get("account_name") else "requested",
            "collection_status": "partial" if account or provider or region else "unavailable",
        }
        return _compact_metadata(payload)

    @staticmethod
    def _snowflake_compute_metadata(
        config: Mapping[str, Any],
        compute: Mapping[str, Any],
        cache_control: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        observed = any(
            compute.get(key) is not None
            for key in (
                "warehouse_state",
                "warehouse_type",
                "min_cluster_count",
                "max_cluster_count",
                "scaling_policy",
            )
        )
        collection_error = compute.get("collection_error_class")
        collection_status = "available" if observed else "partial"
        if collection_error and not (
            config.get("compute_resource")
            or config.get("warehouse")
            or config.get("compute_size")
            or config.get("warehouse_size")
        ):
            collection_status = "unavailable"

        payload = {
            "warehouse": compute.get("warehouse_name") or config.get("compute_resource") or config.get("warehouse"),
            "warehouse_size": compute.get("warehouse_size")
            or config.get("compute_size")
            or config.get("warehouse_size"),
            "warehouse_size_source": "observed"
            if compute.get("warehouse_size")
            else "requested"
            if config.get("compute_size") or config.get("warehouse_size")
            else None,
            "warehouse_type": compute.get("warehouse_type"),
            "warehouse_state": compute.get("warehouse_state"),
            "min_cluster_count": compute.get("min_cluster_count"),
            "max_cluster_count": compute.get("max_cluster_count"),
            "auto_suspend": compute.get("auto_suspend")
            if compute.get("auto_suspend") is not None
            else config.get("auto_suspend"),
            "auto_resume": compute.get("auto_resume")
            if compute.get("auto_resume") is not None
            else config.get("auto_resume"),
            "multi_cluster_warehouse": config.get("multi_cluster_warehouse"),
            "query_acceleration_enabled": compute.get("enable_query_acceleration"),
            "query_acceleration_max_scale_factor": compute.get("query_acceleration_max_scale_factor"),
            "scaling_policy": compute.get("scaling_policy"),
            "result_cache_enabled": config.get("result_cache_enabled"),
            "cache_control": dict(cache_control) if isinstance(cache_control, Mapping) else None,
            "source": "observed" if observed else "requested",
            "collection_status": collection_status,
            "collection_error_class": collection_error,
            "collection_error_message": compute.get("collection_error_message"),
        }
        return _compact_metadata(payload)

    @staticmethod
    def _snowflake_storage_metadata(config: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "table_format": config.get("file_format"),
            "staging_location": config.get("staging_root"),
            "compression": config.get("compression"),
            "source": "requested",
            "collection_status": "partial"
            if config.get("file_format") or config.get("staging_root") or config.get("compression")
            else "unavailable",
        }
        return _compact_metadata(payload)

    def get_target_dialect(self) -> str:
        return "snowflake"

    def _connect_params(self, connection_config: dict[str, Any], *, include_database: bool) -> dict[str, Any]:
        conn_params: dict[str, Any] = {
            "account": connection_config.get("account", self.account),
            "user": connection_config.get("username", self.username),
            "password": connection_config.get("password", self.password),
            "warehouse": connection_config.get("warehouse", self.warehouse),
            "application": "BenchBox",
            "timezone": self.timezone,
            "autocommit": True,
        }
        if connection_config.get("role", self.role):
            conn_params["role"] = connection_config.get("role", self.role)
        if self.authenticator != "snowflake":
            conn_params["authenticator"] = self.authenticator
        if self.private_key_path:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.serialization import (
                load_pem_private_key,
            )

            with open(self.private_key_path, "rb") as key_file:
                private_key = load_pem_private_key(
                    key_file.read(),
                    password=self.private_key_passphrase.encode() if self.private_key_passphrase else None,
                )
            pkb = private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            conn_params["private_key"] = pkb
            del conn_params["password"]
        if include_database:
            conn_params["database"] = connection_config.get("database", self.database)
            conn_params["schema"] = connection_config.get("schema", self.schema)
        return conn_params

    def _create_admin_connection(self, **connection_config) -> Any:
        return snowflake.connector.connect(
            **self._connect_params(connection_config, include_database=False),
            client_session_keep_alive=True,
            login_timeout=30,
            network_timeout=60,
        )

    def check_server_database_exists(self, **connection_config) -> bool:
        try:
            connection = self._create_admin_connection(**connection_config)
            cursor = connection.cursor()

            database = connection_config.get("database", self.database)
            schema = connection_config.get("schema", self.schema)

            cursor.execute("SHOW DATABASES")
            databases = [row[1] for row in cursor.fetchall()]

            if database.upper() in [db.upper() for db in databases]:
                return True

            try:
                cursor.execute(f'USE DATABASE "{database}"')
                schema_escaped = schema.replace("'", "''")
                cursor.execute(f"SHOW SCHEMAS LIKE '{schema_escaped}'")
                schemas = cursor.fetchall()

                if schemas:
                    cursor.execute(f'USE SCHEMA "{schema}"')
                    cursor.execute("SHOW TABLES")
                    tables = cursor.fetchall()

                    if tables:
                        return True
            except Exception:
                pass

            return False

        except Exception as exc:
            self.log_verbose(f"Snowflake database existence check failed: {type(exc).__name__}")
            return False
        finally:
            if "connection" in locals() and connection:
                connection.close()

    def drop_database(self, **connection_config) -> None:
        database = connection_config.get("database", self.database)
        try:
            connection = self._create_admin_connection(**connection_config)
            cursor = connection.cursor()

            cursor.execute(f'DROP DATABASE IF EXISTS "{database}"')

        except Exception as e:
            raise RuntimeError(f"Failed to drop Snowflake database {database}: {e}") from e
        finally:
            if "connection" in locals() and connection:
                connection.close()

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("Snowflake connection")

        self.log_verbose("Creating Snowflake connection")

        self.handle_existing_database(**connection_config)

        self.log_very_verbose(
            f"Snowflake connection params: account={connection_config.get('account', self.account)}, database={connection_config.get('database', self.database)}"
        )

        try:
            self.log_verbose(f"Connecting to Snowflake account: {connection_config.get('account', self.account)}")
            conn_params = self._connect_params(connection_config, include_database=True)

            if conn_params.get("role"):
                self.log_very_verbose(f"Using role: {conn_params['role']}")

            if self.authenticator != "snowflake":
                self.log_very_verbose(f"Using authenticator: {self.authenticator}")

            if self.private_key_path:
                self.log_verbose("Using key pair authentication")

            connection = snowflake.connector.connect(**conn_params)

            self.log_verbose("Testing Snowflake connection")
            cursor = connection.cursor()
            cursor.execute("SELECT CURRENT_VERSION()")
            cursor.fetchall()
            cursor.close()

            self.log_verbose(f"Connected to Snowflake account: {self.account}")
            self.log_verbose(f"Using warehouse: {self.warehouse}, database: {self.database}, schema: {self.schema}")

            self.log_operation_complete(
                "Snowflake connection", details=f"Connected to account {self.account}, database: {self.database}"
            )

            return connection

        except Exception as e:
            self.logger.error(f"Failed to connect to Snowflake: {e}")
            raise

    def _should_skip_schema_creation(self, benchmark, connection: Any) -> bool:
        if self.force_recreate:
            self.log_verbose("Force recreate enabled - schema creation required")
            return False
        try:
            cursor = connection.cursor()

            expected_tables = self._get_expected_tables(benchmark)
            if not expected_tables:
                return False

            for table_name in expected_tables:
                table_upper = table_name.upper()

                cursor.execute(f"SHOW TABLES LIKE '{table_upper}'")
                row = cursor.fetchone()
                if not row:
                    self.log_verbose(f"Table {table_upper} missing - schema creation required")
                    return False

                catalog_name = row[1] if len(row) > 1 and row[1] else table_upper

                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table_upper}")
                except Exception:
                    cursor.execute(f'SELECT COUNT(*) FROM "{catalog_name}"')
                row_count = cursor.fetchone()[0]
                if row_count == 0:
                    self.log_verbose(f"Table {table_upper} empty - schema creation required")
                    return False

            self.log_verbose(f"All {len(expected_tables)} tables exist with data - skipping schema creation")
            return True

        except Exception as e:
            self.log_very_verbose(f"Schema check failed: {e} - proceeding with creation")
            return False

    def create_schema(self, benchmark, connection: Any) -> float:
        self.log_operation_start("Snowflake schema creation")
        start_time = mono_time()

        self.log_verbose(f"Creating schema for benchmark: {benchmark.__class__.__name__}")
        self.log_very_verbose(f"Target database: {self.database}, schema: {self.schema}")

        cursor = connection.cursor()

        try:
            self.log_verbose(f"Creating/using database: {self.database}")
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {self.database}")
            cursor.execute(f"USE DATABASE {self.database}")

            self.log_verbose(f"Creating/using schema: {self.schema}")
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cursor.execute(f"USE SCHEMA {self.schema}")

            if self._should_skip_schema_creation(benchmark, connection):
                elapsed_time = elapsed_seconds(start_time)
                self.log_operation_complete(
                    "Snowflake schema creation", details=f"Skipped (existing data is valid) in {elapsed_time:.2f}s"
                )
                return elapsed_time

            self.log_very_verbose(f"Setting query tag: {self.query_tag}_schema_creation")
            cursor.execute(f"ALTER SESSION SET QUERY_TAG = '{self.query_tag}_schema_creation'")

            self.log_very_verbose("Retrieving schema SQL from benchmark")
            schema_sql = self._create_schema_with_tuning(benchmark, source_dialect="standard")

            from benchbox.platforms.cloud_shared import split_leading_sql_comments

            statements = [
                stmt.strip()
                for stmt in schema_sql.split(";")
                if stmt.strip() and split_leading_sql_comments(stmt)[1].strip()
            ]

            self.log_verbose(f"Executing {len(statements)} schema statements")

            for i, statement in enumerate(statements, 1):
                optimized_statement = self._optimize_table_definition(statement)
                self.log_very_verbose(f"Executing statement {i}/{len(statements)}: {optimized_statement[:100]}...")
                cursor.execute(optimized_statement)

            self.log_verbose("Schema created successfully")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise
        finally:
            cursor.close()

        elapsed_time = elapsed_seconds(start_time)
        self.log_operation_complete("Snowflake schema creation", details=f"Completed in {elapsed_time:.2f}s")
        return elapsed_time

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        self.log_operation_start("Snowflake data loading")

        self.log_verbose(f"Starting data loading for benchmark: {benchmark.__class__.__name__}")
        self.log_very_verbose(f"Data directory: {data_dir}")

        phase_start = mono_time()

        cursor = connection.cursor()

        try:
            self.log_very_verbose(f"Setting query tag: {self.query_tag}_data_loading")
            cursor.execute(f"ALTER SESSION SET QUERY_TAG = '{self.query_tag}_data_loading'")

            self._create_load_file_formats(cursor)
            data_source = self._resolve_data_files(benchmark, data_dir)

            def load_one(table_name: str, valid_files: list[Path]) -> int:
                return self._load_table_from_stage(
                    cursor, table_name, table_name.upper(), valid_files, data_source, benchmark
                )

            def on_table_loaded(table_name: str, table_name_upper: str, row_count: int) -> None:
                del row_count
                effective_tuning = self.get_effective_tuning_configuration()
                if effective_tuning is not None:
                    self.apply_ctas_sort(table_name_upper, effective_tuning, connection)
                    self.run_post_load_tunings(table_name_upper, effective_tuning, connection)

            table_stats, total_time, per_table_timings = run_staged_table_loads(
                self,
                tables=data_source.tables,
                stat_key=str.upper,
                filter_files=self._normalize_existing_files,
                load_one=load_one,
                on_table_loaded=on_table_loaded,
                record_timings=True,
                fail_fast=True,
                success_log=self.log_verbose,
                summary_log=self.log_verbose,
                phase_start=phase_start,
            )
            assert per_table_timings is not None
            self.log_operation_complete(
                "Snowflake data loading", details=f"Loaded {sum(table_stats.values()):,} rows in {total_time:.2f}s"
            )

        except Exception as e:
            self.logger.error(f"Data loading failed: {e}")
            raise
        finally:
            cursor.close()

        return table_stats, total_time, per_table_timings

    def validate_external_table_requirements(self) -> None:
        if not self.staging_root:
            raise ValueError(
                "Snowflake external mode requires --platform-option staging_root=<cloud-uri> "
                "(for example s3://bucket/path, gs://bucket/path, or azure://container/path)."
            )
        stage_error = snowflake_stage_mode_error(self.staging_root, table_mode="external")
        if stage_error:
            raise ValueError(stage_error)

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        self.validate_external_table_requirements()
        assert self.staging_root is not None

        self.log_operation_start("Snowflake external table registration")
        start_time = mono_time()
        table_stats: dict[str, int] = {}
        cursor = connection.cursor()

        stage_name = f"{self.schema}.BENCHBOX_EXTERNAL_STAGE"
        stage_root = self.staging_root.rstrip("/").replace("'", "''")

        try:
            data_source = self._resolve_data_files(benchmark, data_dir)

            cursor.execute(f"CREATE STAGE IF NOT EXISTS {stage_name} URL='{stage_root}'")

            for table_name, file_paths in data_source.tables.items():
                table_name_upper = table_name.upper()
                table_path = f"{self.database.lower()}/{table_name.lower()}/"
                source_format = self._detect_external_table_format(file_paths)
                if source_format == "iceberg":
                    if not self.iceberg_external_volume:
                        raise ValueError(
                            "Snowflake Iceberg external mode requires --platform-option iceberg_external_volume=<name>."
                        )
                    stage_path = get_cloud_path_info(self.staging_root).get("path", "").strip("/")
                    base_location = f"{stage_path}/{table_path}".strip("/") if stage_path else table_path.strip("/")
                    cursor.execute(f"""
                        CREATE OR REPLACE ICEBERG TABLE {table_name_upper}
                        EXTERNAL_VOLUME = '{self.iceberg_external_volume.replace("'", "''")}'
                        CATALOG = '{self.iceberg_catalog.replace("'", "''")}'
                        BASE_LOCATION = '{base_location.replace("'", "''")}'
                    """)
                else:
                    table_clause = (
                        f"TABLE_FORMAT={self.delta_table_format}"
                        if source_format == "delta"
                        else "FILE_FORMAT=(TYPE=PARQUET)"
                    )
                    cursor.execute(f"""
                        CREATE OR REPLACE EXTERNAL TABLE {table_name_upper}
                        WITH LOCATION=@{stage_name}/{table_path}
                        {table_clause}
                        AUTO_REFRESH=FALSE
                    """)
                    cursor.execute(f"ALTER EXTERNAL TABLE {table_name_upper} REFRESH")
                cursor.execute(f"SELECT COUNT(*) FROM {table_name_upper}")
                result = cursor.fetchone()
                table_stats[table_name_upper] = int(result[0]) if result else 0

        finally:
            cursor.close()

        total_time = elapsed_seconds(start_time)
        self.log_operation_complete("Snowflake external table registration", details=f"{len(table_stats)} tables")
        return table_stats, total_time, None

    @staticmethod
    def _detect_external_table_format(file_paths: Any) -> str:
        paths = file_paths if isinstance(file_paths, list) else [file_paths]
        for raw_path in paths:
            path = Path(raw_path)
            if not path.is_dir():
                continue
            if (path / "_delta_log").is_dir():
                return "delta"
            if (path / "metadata").is_dir():
                return "iceberg"
        return "parquet"

    def _create_load_file_formats(self, cursor: Any) -> None:
        self.log_verbose("Creating file formats for data loading")
        cursor.execute(f"""
            CREATE OR REPLACE FILE FORMAT {self.schema}.BENCHBOX_CSV_FORMAT
            TYPE = 'CSV'
            FIELD_DELIMITER = ','
            RECORD_DELIMITER = '\\n'
            SKIP_HEADER = 0
            ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE
            REPLACE_INVALID_CHARACTERS = TRUE
            EMPTY_FIELD_AS_NULL = TRUE
            FIELD_OPTIONALLY_ENCLOSED_BY = '"'
            COMPRESSION = '{self.compression}'
        """)
        cursor.execute(f"""
            CREATE OR REPLACE FILE FORMAT {self.schema}.BENCHBOX_TBL_FORMAT
            TYPE = 'CSV'
            FIELD_DELIMITER = '|'
            RECORD_DELIMITER = '\\n'
            SKIP_HEADER = 0
            ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE
            REPLACE_INVALID_CHARACTERS = TRUE
            EMPTY_FIELD_AS_NULL = TRUE
            FIELD_OPTIONALLY_ENCLOSED_BY = '"'
            COMPRESSION = '{self.compression}'
        """)

    def _resolve_data_files(self, benchmark: Any, data_dir: Path) -> DataSource:
        return resolve_adapter_data_source(self, benchmark, data_dir)

    _normalize_existing_files = staticmethod(normalize_existing_files)

    def _get_file_format_for_table(
        self,
        table_name: str,
        first_file: Path,
        data_source: DataSource,
        benchmark: Any,
    ) -> str:
        dialect = resolve_csv_dialect(data_source, table_name, first_file, benchmark)
        if dialect.delimiter == "|":
            self.log_very_verbose(f"Using TBL file format for {table_name}")
            return f"{self.schema}.BENCHBOX_TBL_FORMAT"
        self.log_very_verbose(f"Using CSV file format for {table_name}")
        return f"{self.schema}.BENCHBOX_CSV_FORMAT"

    def _ensure_preserve_file_format(
        self,
        cursor: Any,
        table_name: str,
        first_file: Path,
        data_source: DataSource,
        benchmark: Any,
    ) -> str | None:
        dialect = resolve_csv_dialect(data_source, table_name, first_file, benchmark)
        if not dialect.null_marker and not dialect.has_header:
            return None
        marker = (dialect.null_marker or "").replace("'", "''")
        key = f"{dialect.delimiter}\x1f{marker}\x1f{int(dialect.has_header)}\x1f{self.compression}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12].upper()
        format_name = f"{self.schema}.BENCHBOX_DYN_{digest}"
        delimiter = dialect.delimiter.replace("'", "''")
        skip_header = 1 if dialect.has_header else 0
        empty_field_as_null = "FALSE" if marker else "TRUE"
        null_if = f"NULL_IF = ('{marker}')" if marker else ""
        self.log_very_verbose(f"Ensuring per-dialect file format {format_name} for {table_name}")
        cursor.execute(f"""
            CREATE FILE FORMAT IF NOT EXISTS {format_name}
            TYPE = 'CSV'
            FIELD_DELIMITER = '{delimiter}'
            RECORD_DELIMITER = '\\n'
            SKIP_HEADER = {skip_header}
            ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE
            REPLACE_INVALID_CHARACTERS = TRUE
            EMPTY_FIELD_AS_NULL = {empty_field_as_null}
            {null_if}
            FIELD_OPTIONALLY_ENCLOSED_BY = '"'
            COMPRESSION = '{self.compression}'
        """)
        return format_name

    def _parse_copy_results(self, copy_results: list[Any]) -> None:
        for row in copy_results:
            if len(row) <= 3:
                continue

            status = str(row[1]) if len(row) > 1 else "UNKNOWN"
            try:
                loaded = int(row[3])
            except (ValueError, TypeError) as e:
                self.logger.warning(f"Could not parse rows_loaded from COPY result: {e}")
                continue

            if status == "LOADED":
                continue

            file_name = str(row[0]) if len(row) > 0 else "unknown"
            error_msg = str(row[5]) if len(row) > 5 and row[5] else "No error message provided"
            self.logger.warning(f"File {file_name} status: {status}, loaded {loaded} rows. Error: {error_msg}")

    def _load_table_from_stage(
        self,
        cursor: Any,
        table_name: str,
        table_name_upper: str,
        valid_files: list[Path],
        data_source: DataSource | None = None,
        benchmark: Any = None,
    ) -> int:
        stage_name = f"@%{table_name_upper}"
        target_table = table_name_upper
        self.log_very_verbose(f"Using stage: {stage_name}")

        try:
            cursor.execute(f"REMOVE {stage_name}")
        except Exception as e:
            if "does not exist or not authorized" not in str(e):
                raise

        for file_idx, file_path in enumerate(valid_files):
            chunk_msg = f" (chunk {file_idx + 1}/{len(valid_files)})" if len(valid_files) > 1 else ""
            self.log_very_verbose(f"Uploading file{chunk_msg} with PUT: {file_path.name}")
            try:
                cursor.execute(f"PUT file://{file_path.absolute()} {stage_name} OVERWRITE = TRUE")
            except Exception as e:
                if "does not exist or not authorized" in str(e):
                    stage_name = f'@%"{table_name}"'
                    target_table = f'"{table_name}"'
                    cursor.execute(f"PUT file://{file_path.absolute()} {stage_name} OVERWRITE = TRUE")
                else:
                    raise

        ds = data_source or DataSource(source_type="snowflake_stage", tables={})
        bm = benchmark if benchmark is not None else NO_BENCHMARK
        is_parquet = str(valid_files[0]).lower().endswith(".parquet")
        if is_parquet:
            file_format_clause = "FILE_FORMAT = (TYPE = 'PARQUET') MATCH_BY_COLUMN_NAME = 'CASE_INSENSITIVE'"
        else:
            file_format = self._ensure_preserve_file_format(cursor, table_name, valid_files[0], ds, bm)
            if file_format is None:
                file_format = self._get_file_format_for_table(table_name, valid_files[0], ds, bm)
            file_format_clause = f"FILE_FORMAT = (FORMAT_NAME = '{file_format}')"

        try:
            self.log_very_verbose(f"Truncating {target_table} before COPY INTO")
            cursor.execute(f"TRUNCATE TABLE {target_table}")
        except Exception as e:
            if "does not exist or not authorized" not in str(e):
                raise
        copy_command = f"""
            COPY INTO {target_table}
            FROM {stage_name}
            {file_format_clause}
            ON_ERROR = 'CONTINUE'
            PURGE = TRUE
            FORCE = TRUE
        """
        self.log_very_verbose(f"Executing COPY INTO for {target_table}")
        try:
            cursor.execute(copy_command)
        except Exception as e:
            if target_table != f'"{table_name}"' and "does not exist or not authorized" in str(e):
                target_table = f'"{table_name}"'
                copy_command = f"""
                    COPY INTO {target_table}
                    FROM {stage_name}
                    {file_format_clause}
                    ON_ERROR = 'CONTINUE'
                    PURGE = TRUE
                    FORCE = TRUE
                """
                cursor.execute(copy_command)
            else:
                raise
        self._parse_copy_results(cursor.fetchall())

        try:
            cursor.execute(f"SELECT COUNT(*) FROM {target_table}")
        except Exception:
            cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        return cursor.fetchone()[0]

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:

        cursor = connection.cursor()

        try:
            cursor.execute(f"ALTER SESSION SET QUERY_TAG = '{self.query_tag}_optimization'")

            session_settings = [
                f"ALTER SESSION SET TIMEZONE = '{self.timezone}'",
                "ALTER SESSION SET AUTOCOMMIT = TRUE",
            ]

            if self.suppress_nondeterministic_errors:
                session_settings.extend(
                    [
                        "ALTER SESSION SET ERROR_ON_NONDETERMINISTIC_MERGE = FALSE",
                        "ALTER SESSION SET ERROR_ON_NONDETERMINISTIC_UPDATE = FALSE",
                    ]
                )

            if benchmark_type.lower() in ["olap", "analytics", "tpch", "tpcds"]:
                cache_setting = "FALSE" if self.disable_result_cache else "TRUE"
                session_settings.extend(
                    [
                        "ALTER SESSION SET QUERY_ACCELERATION_MAX_SCALE_FACTOR = 8",
                        f"ALTER SESSION SET USE_CACHED_RESULT = {cache_setting}",
                        "ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 1800",
                    ]
                )

                if self.modify_warehouse_settings:
                    if self.multi_cluster_warehouse:
                        cursor.execute(f"""
                            ALTER WAREHOUSE {self.warehouse} SET
                            MIN_CLUSTER_COUNT = 1
                            MAX_CLUSTER_COUNT = 3
                            AUTO_SUSPEND = {self.auto_suspend}
                            AUTO_RESUME = {self.auto_resume}
                            SCALING_POLICY = 'STANDARD'
                        """)
                    else:
                        cursor.execute(f"""
                            ALTER WAREHOUSE {self.warehouse} SET
                            WAREHOUSE_SIZE = '{self.warehouse_size}'
                            AUTO_SUSPEND = {self.auto_suspend}
                            AUTO_RESUME = {self.auto_resume}
                        """)
                else:
                    self.logger.info("Warehouse modifications skipped (modify_warehouse_settings=False)")

            critical_failures = []
            for setting in session_settings:
                try:
                    cursor.execute(setting)
                    self.logger.debug(f"Applied setting: {setting}")
                except Exception as e:
                    if "USE_CACHED_RESULT" in setting:
                        critical_failures.append(setting)
                    self.logger.warning(f"Failed to apply setting {setting}: {e}")

            cursor.execute(f"USE WAREHOUSE {self.warehouse}")

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
                    explicit_cache_enabled_receipt("USE_CACHED_RESULT", "TRUE")
                )

        finally:
            cursor.close()

    def validate_session_cache_control(self, connection: Any) -> dict[str, Any]:
        from benchbox.platforms.cloud_shared import validate_session_cache_control

        return validate_session_cache_control(
            connection=connection,
            query="SHOW PARAMETERS LIKE 'USE_CACHED_RESULT' IN SESSION",
            setting_key="USE_CACHED_RESULT",
            disabled_value="FALSE",
            enabled_value="TRUE",
            normalize="upper",
            platform_name="Snowflake",
            disable_result_cache=self.disable_result_cache,
            strict_validation=self.strict_validation,
            adapter_logger=self.logger,
            value_column_index=1,
        )

    @staticmethod
    def _first_statement_keyword(statement: str) -> str:
        import re

        cleaned = re.sub(r"^(?:\s*--(?:[^\n]*)\n|\s*/\*.*?\*/)+", "", statement, flags=re.DOTALL)
        m = re.match(r"^\s*([A-Za-z]+)", cleaned)
        return m.group(1).upper() if m else ""

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
        self.log_operation_start("Snowflake query execution", query_id)
        self.log_very_verbose(f"Executing query {query_id}: {query[:100]}...")

        start_time = mono_time()

        own_cursor = False
        if hasattr(connection, "cursor"):
            cursor = connection.cursor()
            own_cursor = True
            stats_connection = connection
        else:
            cursor = connection
            stats_connection = getattr(connection, "connection", connection)

        try:
            self.log_very_verbose(f"Setting query tag: {self.query_tag}_{query_id}")
            cursor.execute(f"ALTER SESSION SET QUERY_TAG = '{self.query_tag}_{query_id}'")

            query = self._safeguard_snowflake_division(query)
            self.log_verbose(f"Executing query {query_id} on Snowflake")
            from benchbox.platforms.base.mysql_wire import split_sql_statements

            statements = split_sql_statements(query)
            statements_to_run = statements or [query]
            result = []

            keywords = [self._first_statement_keyword(s) for s in statements_to_run]
            has_tx_control = any(kw in {"BEGIN", "START", "COMMIT", "ROLLBACK"} for kw in keywords)
            has_ddl = any(
                kw in {"CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME", "COMMENT", "GRANT", "REVOKE", "USE"}
                for kw in keywords
            )
            use_explicit_tx = len(statements_to_run) > 1 and not has_tx_control and not has_ddl

            if use_explicit_tx:
                cursor.execute("BEGIN")
                try:
                    for statement in statements_to_run:
                        cursor.execute(statement)
                        result = cursor.fetchall()
                    cursor.execute("COMMIT")
                except Exception:
                    try:
                        cursor.execute("ROLLBACK")
                    except Exception as rollback_err:
                        self.log_very_verbose(f"Rollback failed: {rollback_err}")
                    raise
            else:
                for statement in statements_to_run:
                    cursor.execute(statement)
                    result = cursor.fetchall()

            execution_time = elapsed_seconds(start_time)
            actual_row_count = len(result) if result else 0

            query_stats = self._get_query_statistics(stats_connection, query_id)

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
            result_dict["query_statistics"] = query_stats
            if isinstance(query_stats, dict):
                query_stats = {**query_stats, "execution_time_seconds": execution_time}
            result_dict["resource_usage"] = query_stats

            if result_dict["status"] == "FAILED":
                self.log_operation_complete("Snowflake query execution", execution_time, "FAILED: validation error")
            else:
                self.log_verbose(f"Query {query_id} completed: {actual_row_count} rows in {execution_time:.3f}s")
                self.log_operation_complete(
                    "Snowflake query execution", execution_time, f"returned {actual_row_count} rows"
                )

        except Exception as e:
            execution_time = elapsed_seconds(start_time)
            self.log_verbose(f"Query {query_id} failed after {execution_time:.3f}s: {e}")
            self.log_operation_complete("Snowflake query execution", execution_time, f"FAILED: {e}")

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
        own_cursor = False
        if hasattr(connection, "cursor"):
            cursor = connection.cursor()
            own_cursor = True
        else:
            cursor = connection
        try:
            cursor.execute(f"EXPLAIN USING JSON {query}")
            row = cursor.fetchone()
            if not row or row[0] is None:
                return None
            return str(row[0])
        except Exception as e:
            self.logger.debug(f"Could not get query plan: {e}")
            return None
        finally:
            if own_cursor:
                cursor.close()

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.snowflake import SnowflakeQueryPlanParser

        return SnowflakeQueryPlanParser()

    def _optimize_table_definition(self, statement: str) -> str:
        from benchbox.platforms.cloud_shared import split_leading_sql_comments

        prefix, body = split_leading_sql_comments(statement)
        if not body.upper().startswith("CREATE TABLE"):
            return statement

        if "CREATE TABLE" in body and "OR REPLACE" not in body.upper():
            if "IF NOT EXISTS" not in body.upper():
                body = body.replace("CREATE TABLE", "CREATE OR REPLACE TABLE", 1)
        statement = prefix + body

        import re

        parts = re.split(r"('(?:[^']|'')*')", statement)
        for index in range(0, len(parts), 2):
            parts[index] = re.sub(r'"([^"]+)"', lambda m: m.group(0).upper(), parts[index])
        statement = "".join(parts)

        return statement

    def _safeguard_snowflake_division(self, query: str) -> str:
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            return query

        try:
            tree = sqlglot.parse_one(query, read="snowflake")
        except Exception as e:
            self.log_very_verbose(f"Division safeguard skipped (unparseable Snowflake SQL): {e}")
            return query
        if not any(isinstance(node, exp.Div) for node in tree.walk()):
            return query

        def to_nullif_divisor(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Div):
                return exp.Div(
                    this=node.this.copy(),
                    expression=exp.Anonymous(
                        this="NULLIF",
                        expressions=[node.expression.copy(), exp.Literal.number(0)],
                    ),
                )
            return node

        return tree.transform(to_nullif_divisor).sql(dialect="snowflake")

    def _get_existing_tables(self, connection: Any) -> list[str]:
        try:
            cursor = connection.cursor()
            cursor.execute("SHOW TABLES")
            tables = [row[1].lower() for row in cursor.fetchall()]
            return tables
        except Exception:
            return []

    def _validate_data_integrity(
        self, benchmark, connection: Any, table_stats: dict[str, int]
    ) -> tuple[str, dict[str, Any]]:
        validation_details = {}

        try:
            accessible_tables = []
            inaccessible_tables = []

            actual_names = {name.lower(): name for name in self._get_existing_tables(connection)}

            cursor = connection.cursor()
            for table_name in table_stats:
                stored = actual_names.get(table_name.lower())
                candidates = [f'"{stored}"'] if stored else []
                candidates.append(table_name)
                probed = False
                for candidate in candidates:
                    try:
                        cursor.execute(f"SELECT 1 FROM {candidate} LIMIT 1")
                        cursor.fetchone()
                        accessible_tables.append(table_name)
                        probed = True
                        break
                    except Exception:
                        continue
                if not probed:
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

    def _get_query_statistics(
        self, connection: Any, query_id: str, max_retries: int = 0, initial_delay: float = 0.5
    ) -> dict[str, Any]:
        import time as time_module

        cursor = connection.cursor()
        for attempt in range(max_retries + 1):
            try:
                cursor.execute(f"""
                    SELECT
                        QUERY_ID,
                        TOTAL_ELAPSED_TIME,
                        EXECUTION_TIME,
                        COMPILATION_TIME,
                        BYTES_SCANNED,
                        ROWS_PRODUCED,
                        CREDITS_USED_CLOUD_SERVICES,
                        WAREHOUSE_SIZE,
                        CLUSTER_NUMBER
                    FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(
                        END_TIME_RANGE_START => DATEADD('MINUTE', -5, CURRENT_TIMESTAMP()),
                        END_TIME_RANGE_END => CURRENT_TIMESTAMP()
                    ))
                    WHERE QUERY_TAG = '{self.query_tag}_{query_id}'
                    ORDER BY START_TIME DESC
                    LIMIT 1
                """)

                result = cursor.fetchone()

                if result:
                    cursor.close()
                    return {
                        "snowflake_query_id": result[0],
                        "total_elapsed_time_ms": result[1],
                        "execution_time_ms": result[2],
                        "compilation_time_ms": result[3],
                        "bytes_scanned": result[4],
                        "rows_produced": result[5],
                        "credits_used_cloud_services": result[6],
                        "warehouse_size": result[7],
                        "cluster_number": result[8],
                        "retrieval_attempts": attempt + 1,
                    }
                else:
                    if attempt < max_retries:
                        delay = initial_delay * (2**attempt)
                        self.logger.debug(
                            f"Query statistics not yet available for {query_id}, "
                            f"retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                        )
                        time_module.sleep(delay)
                    else:
                        cursor.close()
                        return {
                            "note": f"Query statistics not available after {max_retries + 1} attempts. "
                            "Statistics may appear in query history later.",
                            "retrieval_attempts": max_retries + 1,
                        }

            except Exception as exc:
                cursor.close()
                return {
                    "statistics_error": f"{type(exc).__name__}: query statistics retrieval failed",
                    "retrieval_attempts": attempt + 1,
                }

        cursor.close()
        return {"note": "Query statistics not yet available", "retrieval_attempts": max_retries + 1}

    def _get_platform_metadata(self, connection: Any) -> dict[str, Any]:
        metadata = {
            "platform": self.platform_name,
            "account": self.account,
            "warehouse": self.warehouse,
            "database": self.database,
            "schema": self.schema,
            "result_cache_enabled": not self.disable_result_cache,
        }

        cursor = connection.cursor()

        try:
            cursor.execute("SELECT CURRENT_VERSION()")
            result = cursor.fetchone()
            metadata["snowflake_version"] = result[0] if result else "unknown"

            cursor.execute("""
                SELECT
                    CURRENT_USER(),
                    CURRENT_ROLE(),
                    CURRENT_WAREHOUSE(),
                    CURRENT_DATABASE(),
                    CURRENT_SCHEMA(),
                    CURRENT_REGION(),
                    CURRENT_ACCOUNT()
            """)
            result = cursor.fetchone()
            if result:
                metadata["session_info"] = {
                    "current_user": result[0],
                    "current_role": result[1],
                    "current_warehouse": result[2],
                    "current_database": result[3],
                    "current_schema": result[4],
                    "current_region": result[5],
                    "current_account": result[6],
                }

            cursor.execute(f"""
                SHOW WAREHOUSES LIKE '{self.warehouse}'
            """)
            wh_result = cursor.fetchall()
            if wh_result:
                wh_info = wh_result[0]
                metadata["warehouse_info"] = {
                    "name": wh_info[0],
                    "state": wh_info[1],
                    "type": wh_info[2],
                    "size": wh_info[3],
                    "min_cluster_count": wh_info[4],
                    "max_cluster_count": wh_info[5],
                    "started_clusters": wh_info[6],
                    "running": wh_info[7],
                    "queued": wh_info[8],
                    "auto_suspend": wh_info[12],
                    "auto_resume": wh_info[13],
                    "available": wh_info[14],
                    "provisioning": wh_info[15],
                    "quiescing": wh_info[16],
                    "other": wh_info[17],
                    "created_on": wh_info[18],
                    "resumed_on": wh_info[19],
                    "updated_on": wh_info[20],
                    "owner": wh_info[21],
                    "comment": wh_info[22],
                    "scaling_policy": wh_info[25] if len(wh_info) > 25 else None,
                }

            from benchbox.platforms.snowflake_introspection import normalize_snowflake_schema

            cursor.execute(
                """
                SELECT
                    TABLE_NAME,
                    ROW_COUNT,
                    BYTES,
                    RETENTION_TIME,
                    CREATED,
                    LAST_ALTERED,
                    CLUSTERING_KEY
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = %s
                AND TABLE_TYPE = 'BASE TABLE'
                """,
                (normalize_snowflake_schema(self.schema),),
            )
            tables = cursor.fetchall()
            metadata["tables"] = [
                {
                    "table_name": row[0],
                    "row_count": row[1],
                    "bytes": row[2],
                    "retention_time": row[3],
                    "created": row[4].isoformat() if row[4] else None,
                    "last_altered": row[5].isoformat() if row[5] else None,
                    "clustering_key": row[6],
                }
                for row in tables
            ]

        except Exception as e:
            metadata["metadata_error"] = str(e)
        finally:
            cursor.close()

        return metadata

    def analyze_table(self, connection: Any, table_name: str) -> None:
        cursor = connection.cursor()
        try:
            physical_table = self.resolve_physical_table(table_name, connection)
            cursor.execute(f"ALTER TABLE {physical_table} RECLUSTER")
            self.logger.info(f"Triggered reclustering for table {physical_table}")
        finally:
            cursor.close()

    def get_tuning_introspector(self):
        from benchbox.platforms.base import tuning_trust

        return tuning_trust.snowflake_tuning_introspector(self.schema)

    def close_connection(self, connection: Any) -> None:
        try:
            if connection and hasattr(connection, "close"):
                connection.close()
        except Exception as e:
            self.logger.warning(f"Error closing connection: {e}")

    _supported_tuning_type_names = ("CLUSTERING", "PARTITIONING")

    def generate_tuning_clause(self, table_tuning) -> str:
        if not table_tuning or not table_tuning.has_any_tuning():
            return ""

        clauses = []

        try:
            from benchbox.core.tuning.interface import TuningType

            cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
            if cluster_columns:
                sorted_cols = sorted(cluster_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]

                cluster_clause = f"CLUSTER BY ({', '.join(column_names)})"
                clauses.append(cluster_clause)

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns and not cluster_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]

                cluster_clause = f"CLUSTER BY ({', '.join(column_names)})"
                clauses.append(cluster_clause)

        except ImportError:
            pass

        return " ".join(clauses)

    def _reset_run_scoped_state(self) -> None:
        super()._reset_run_scoped_state()
        self._resume_recluster_tables = set()

    def _pending_resume_recluster(self) -> set[str]:
        pending = getattr(self, "_resume_recluster_tables", None)
        if pending is None:
            pending = set()
            self._resume_recluster_tables = pending
        return pending

    def apply_post_load_tunings(self, table_name: str, effective_config: Any, connection: Any) -> bool:
        physical_table = self.resolve_physical_table(table_name, connection)
        pending = self._pending_resume_recluster()
        if physical_table not in pending:
            return False
        pending.discard(physical_table)
        cursor = connection.cursor()
        try:
            cursor.execute(f"ALTER TABLE {physical_table} RESUME RECLUSTER")
            self.logger.info(f"Enabled automatic clustering for {physical_table}")
        except Exception as e:
            self.logger.warning(f"Could not enable automatic clustering for {physical_table}: {e}")
            self.note_post_load_maintenance_failure()
        finally:
            cursor.close()
        return True

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        if not table_tuning or not table_tuning.has_any_tuning():
            return

        table_name = self.resolve_physical_table(table_tuning.table_name, connection)
        self.logger.info(f"Applying Snowflake tunings for table: {table_name}")

        cursor = connection.cursor()
        try:
            from benchbox.core.tuning.interface import TuningType
            from benchbox.platforms.snowflake_introspection import normalize_snowflake_schema, parse_clustering_key

            cluster_columns = table_tuning.get_columns_by_type(TuningType.CLUSTERING)
            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)

            clustering_columns = []
            if cluster_columns:
                sorted_cols = sorted(cluster_columns, key=lambda col: col.order)
                clustering_columns = [col.name for col in sorted_cols]
            elif partition_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                clustering_columns = [col.name for col in sorted_cols]

            if clustering_columns:
                cursor.execute(
                    """
                        SELECT CLUSTERING_KEY, AUTO_CLUSTERING_ON
                        FROM INFORMATION_SCHEMA.TABLES
                        WHERE TABLE_SCHEMA = %s
                        AND TABLE_NAME = %s
                    """,
                    (normalize_snowflake_schema(self.schema), table_name),
                )
                result = cursor.fetchone()
                current_clustering = result[0] if result and result[0] else None
                automatic_clustering_value = result[1] if result and len(result) > 1 else None
                automatic_clustering_on = automatic_clustering_value is True or str(
                    automatic_clustering_value
                ).strip().upper() in {
                    "ON",
                    "TRUE",
                    "1",
                }

                desired_clustering = f"({', '.join(clustering_columns)})"
                cluster_sql = f"ALTER TABLE {table_name} CLUSTER BY ({', '.join(clustering_columns)})"

                clustering_matches = parse_clustering_key(current_clustering) == parse_clustering_key(
                    desired_clustering
                )
                if not clustering_matches:
                    try:
                        cursor.execute(cluster_sql)
                        self.logger.info(f"Applied clustering key to {table_name}: {', '.join(clustering_columns)}")

                        if len(clustering_columns) <= 4:
                            self._pending_resume_recluster().add(table_name)

                    except Exception as e:
                        self.logger.warning(f"Failed to apply clustering key to {table_name}: {e}")
                else:
                    self.logger.info(f"Table {table_name} already has desired clustering key: {current_clustering}")
                    ledger = getattr(self, "_applied_tuning_ledger", None)
                    if ledger is not None:
                        from benchbox.core.tuning.applied_ledger import SATISFIED_BY_PREEXISTING_STATE

                        ledger.record_satisfied(
                            cluster_sql,
                            SATISFIED_BY_PREEXISTING_STATE,
                            "already present in Snowflake catalog; ALTER skipped",
                        )

                    if len(clustering_columns) <= 4 and not automatic_clustering_on:
                        self._pending_resume_recluster().add(table_name)

            sort_columns = table_tuning.get_columns_by_type(TuningType.SORTING)
            if sort_columns and not clustering_columns:
                sorted_cols = sorted(sort_columns, key=lambda col: col.order)
                column_names = [col.name for col in sorted_cols]
                self.logger.info(
                    f"Sorting in Snowflake achieved via clustering for table {table_name}: {', '.join(column_names)}"
                )

            distribution_columns = table_tuning.get_columns_by_type(TuningType.DISTRIBUTION)
            if distribution_columns:
                self.logger.warning(
                    f"Distribution tuning not applicable for Snowflake's shared-nothing architecture on table: {table_name}"
                )

        except ImportError:
            self.logger.warning("Tuning interface not available - skipping tuning application")
        except Exception as e:
            raise ValueError(f"Failed to apply tunings to Snowflake table {table_name}: {e}") from e
        finally:
            cursor.close()

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

        apply_standard_unified_tuning(self, unified_config, connection)

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        if not platform_config:
            return

        self.logger.info("Snowflake platform optimizations stored for warehouse and session management")

    apply_constraint_configuration = make_informational_constraint_applier(
        "Primary key constraints enabled for Snowflake (informational only, applied during table creation)",
        "Foreign key constraints enabled for Snowflake (informational only, applied during table creation)",
    )


_build_snowflake_config = make_registered_platform_config_builder(
    "snowflake",
    __name__,
    "Snowflake",
    "snowflake-connector-python",
    [
        "account",
        "warehouse",
        "schema",
        "username",
        "password",
        "role",
        "edition",
        "authenticator",
        "private_key_path",
        "private_key_passphrase",
    ],
)
