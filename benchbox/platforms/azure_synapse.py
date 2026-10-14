# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        PlatformOptimizationConfiguration,
        TuningColumn,
        UnifiedTuningConfiguration,
    )

from ..utils.dependencies import check_platform_dependencies, get_dependency_error_message
from .base import DriverIsolationCapability, PlatformAdapter
from .base.config_utils import make_registered_platform_config_builder
from .base.data_loading import NO_BENCHMARK, DataSource, resolve_adapter_data_source, resolve_csv_dialect
from .base.tuning import make_informational_constraint_applier
from .presto_trino_utils import normalize_existing_files

SYNAPSE_DIALECT = "tsql"

try:
    import pyodbc
except ImportError:
    pyodbc = None


class AzureSynapseAdapter(PlatformAdapter):
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE
    supports_external_tables = True

    def __init__(self, **config):
        super().__init__(**config)

        available, missing = check_platform_dependencies("synapse")
        if not available:
            error_msg = get_dependency_error_message("synapse", missing)
            raise ImportError(error_msg)

        self._dialect = SYNAPSE_DIALECT

        self.server = config.get("server")
        self.database = config.get("database") or "benchbox"
        self.username = config.get("username")
        self.password = config.get("password")
        self.port = config.get("port") if config.get("port") is not None else 1433

        self.auth_method = config.get("auth_method") or "sql"
        self.tenant_id = config.get("tenant_id")
        self.client_id = config.get("client_id")
        self.client_secret = config.get("client_secret")

        self.driver = config.get("driver") or "ODBC Driver 18 for SQL Server"

        self.schema = config.get("schema") or "dbo"

        self.connect_timeout = config.get("connect_timeout") if config.get("connect_timeout") is not None else 30
        self.query_timeout = config.get("query_timeout") if config.get("query_timeout") is not None else 0
        self.encrypt = config.get("encrypt") if config.get("encrypt") is not None else True

        staging_root = config.get("staging_root")
        if staging_root:
            from benchbox.utils.cloud_storage import cloud_provider_family, get_cloud_path_info

            path_info = get_cloud_path_info(staging_root)
            if cloud_provider_family(staging_root) == "azure":
                self.storage_account = (
                    path_info.get("account")
                    or self._extract_storage_account(staging_root)
                    or config.get("storage_account")
                )
                if not self.storage_account:
                    raise ValueError(
                        "Azure Synapse staging_root must include a storage account "
                        "or provide the storage_account option"
                    )
                self.container = path_info["bucket"]
                self.storage_path = path_info["path"].strip("/") if path_info["path"] else "benchbox-data"
                self.logger.info(
                    f"Using staging location from config: {path_info['provider']}://{self.container}/{self.storage_path}"
                )
            else:
                raise ValueError(
                    "Azure Synapse requires Azure storage (az://, azure://, abfs://, abfss://), "
                    f"got: {path_info['provider']}://"
                )
        else:
            self.storage_account = config.get("storage_account")
            self.container = config.get("container")
            self.storage_path = config.get("storage_path") or "benchbox-data"

        self.storage_sas_token = config.get("storage_sas_token")
        self.storage_account_key = config.get("storage_account_key")
        self.storage_credential = config.get("storage_credential")

        self.resource_class = config.get("resource_class") or "staticrc20"
        self.distribution_default = config.get("distribution_default") or "ROUND_ROBIN"

        self.disable_result_cache = config.get("disable_result_cache", True)

        self.strict_validation = config.get("strict_validation", True)

        if self.auth_method == "sql" and not all([self.server, self.username, self.password]):
            missing = []
            if not self.server:
                missing.append("server")
            if not self.username:
                missing.append("username")
            if not self.password:
                missing.append("password")

            from benchbox.core.exceptions import ConfigurationError

            raise ConfigurationError(
                f"Azure Synapse SQL authentication is incomplete. Missing: {', '.join(missing)}\n"
                "Set them with either of:\n"
                "  1. ~/.benchbox/credentials.yaml, under 'synapse:' "
                "(values may reference environment variables as ${VAR})\n"
                "  2. --platform-option server=<server>.sql.azuresynapse.net "
                "--platform-option username=<user> --platform-option password=<password>\n"
                "\n"
                "For Azure AD authentication, use --platform-option auth_method=aad_msi"
            )
        elif self.auth_method in ("aad_password", "aad_msi") and not self.server:
            from benchbox.core.exceptions import ConfigurationError

            raise ConfigurationError(
                "Azure Synapse AAD authentication requires a server endpoint.\n"
                "Set it with either of:\n"
                "  1. ~/.benchbox/credentials.yaml, under 'synapse:' "
                "(values may reference environment variables as ${VAR})\n"
                "  2. --platform-option server=<workspace>.sql.azuresynapse.net"
            )

    physical_identifier_case = "lower"

    @property
    def platform_name(self) -> str:
        return "Azure Synapse"

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[TuningColumn]) -> str | None:
        mode, method = self.resolve_sorted_ingestion_strategy()
        if mode == "off":
            return None

        if method != "ctas":
            raise ValueError(f"Sorted ingestion method '{method}' is not supported for Azure Synapse.")

        physical_table = self.resolve_physical_table(table_name)
        order_by = ", ".join(self.resolve_physical_column(table_name, column.name) for column in sort_columns)
        temp_table = f"{physical_table}__ctas_sort"
        return (
            f"CREATE TABLE [{self.schema}].[{temp_table}] WITH (DISTRIBUTION = ROUND_ROBIN, HEAP) AS "
            f"SELECT * FROM [{self.schema}].[{physical_table}] ORDER BY {order_by}"
        )

    def get_target_dialect(self) -> str:
        return SYNAPSE_DIALECT

    @staticmethod
    def add_cli_arguments(parser) -> None:
        synapse_group = parser.add_argument_group("Azure Synapse Arguments")
        synapse_group.add_argument("--server", type=str, help="Azure Synapse server endpoint")
        synapse_group.add_argument("--database", type=str, default="benchbox", help="Database name")
        synapse_group.add_argument("--username", type=str, help="Database username")
        synapse_group.add_argument("--password", type=str, help="Database password")
        synapse_group.add_argument(
            "--auth-method",
            type=str,
            choices=["sql", "aad_password", "aad_msi"],
            default="sql",
            help="Authentication method",
        )
        synapse_group.add_argument("--storage-account", type=str, help="Azure storage account for data staging")
        synapse_group.add_argument("--container", type=str, help="Azure blob container name")
        synapse_group.add_argument("--storage-sas-token", type=str, help="SAS token for storage access")
        synapse_group.add_argument("--resource-class", type=str, default="staticrc20", help="Workload resource class")

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from benchbox.platforms.base.config_utils import build_adapter_config

        return cls(
            **build_adapter_config(
                config,
                platform="synapse",
                fields=[
                    "server",
                    "port",
                    "username",
                    "password",
                    "schema",
                    "auth_method",
                    "tenant_id",
                    "client_id",
                    "client_secret",
                    "driver",
                    "connect_timeout",
                    "query_timeout",
                    "encrypt",
                    "storage_account",
                    "container",
                    "storage_path",
                    "staging_root",
                    "storage_sas_token",
                    "storage_account_key",
                    "storage_credential",
                    "resource_class",
                    "distribution_default",
                    "disable_result_cache",
                    "strict_validation",
                ],
            )
        )

    def _extract_storage_account(self, url: str) -> str | None:
        match = re.search(r"@([^.]+)\.", url)
        return match.group(1) if match else None

    def _get_connection_string(self, database: str | None = None) -> str:
        db = database or self.database

        if self.auth_method == "sql":
            conn_str = (
                f"DRIVER={{{self.driver}}};"
                f"SERVER={self.server},{self.port};"
                f"DATABASE={db};"
                f"UID={self.username};"
                f"PWD={self.password};"
                f"Encrypt={'yes' if self.encrypt else 'no'};"
                f"TrustServerCertificate=no;"
                f"Connection Timeout={self.connect_timeout};"
            )
        elif self.auth_method == "aad_password":
            conn_str = (
                f"DRIVER={{{self.driver}}};"
                f"SERVER={self.server},{self.port};"
                f"DATABASE={db};"
                f"UID={self.username};"
                f"PWD={self.password};"
                f"Authentication=ActiveDirectoryPassword;"
                f"Encrypt={'yes' if self.encrypt else 'no'};"
                f"TrustServerCertificate=no;"
                f"Connection Timeout={self.connect_timeout};"
            )
        elif self.auth_method == "aad_msi":
            conn_str = (
                f"DRIVER={{{self.driver}}};"
                f"SERVER={self.server},{self.port};"
                f"DATABASE={db};"
                f"Authentication=ActiveDirectoryMsi;"
                f"Encrypt={'yes' if self.encrypt else 'no'};"
                f"TrustServerCertificate=no;"
                f"Connection Timeout={self.connect_timeout};"
            )
        else:
            raise ValueError(f"Unsupported auth method: {self.auth_method}")

        return conn_str

    def _get_connection_params(self, **connection_config) -> dict[str, Any]:
        return {
            "server": connection_config.get("server", self.server),
            "port": connection_config.get("port", self.port),
            "database": connection_config.get("database", self.database),
            "username": connection_config.get("username", self.username),
            "password": connection_config.get("password", self.password),
        }

    def _create_admin_connection(self, **connection_config) -> Any:
        conn_str = self._get_connection_string(database="master")
        connection = pyodbc.connect(conn_str, autocommit=True)
        return connection

    def check_server_database_exists(self, **connection_config) -> bool:
        try:
            connection = self._create_admin_connection()
            cursor = connection.cursor()

            database = connection_config.get("database", self.database)

            cursor.execute("SELECT name FROM sys.databases WHERE name = ?", (database,))
            result = cursor.fetchone()

            cursor.close()
            connection.close()

            return result is not None

        except Exception:
            return False

    def drop_database(self, **connection_config) -> None:
        database = connection_config.get("database", self.database)

        if not self.check_server_database_exists(database=database):
            self.log_verbose(f"Database {database} does not exist - nothing to drop")
            return

        try:
            connection = self._create_admin_connection()
            cursor = connection.cursor()

            cursor.execute(
                f"""
                DECLARE @kill varchar(8000) = '';
                SELECT @kill = @kill + 'KILL ' + CONVERT(varchar(5), session_id) + ';'
                FROM sys.dm_exec_sessions
                WHERE database_id = DB_ID('{database}')
                AND session_id <> @@SPID;
                EXEC(@kill);
            """
            )

            cursor.execute(f"DROP DATABASE [{database}]")

            cursor.close()
            connection.close()

        except Exception as e:
            raise RuntimeError(f"Failed to drop Azure Synapse database {database}: {e}") from e

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("Azure Synapse connection")

        self.handle_existing_database(**connection_config)

        params = self._get_connection_params(**connection_config)
        target_database = params.get("database")

        if not self.database_was_reused:
            database_exists = self.check_server_database_exists(database=target_database)

            if not database_exists:
                self.log_verbose(f"Creating database: {target_database}")

                try:
                    admin_conn = self._create_admin_connection()
                    cursor = admin_conn.cursor()

                    cursor.execute(f"CREATE DATABASE [{target_database}]")
                    self.logger.info(f"Created database {target_database}")

                    cursor.close()
                    admin_conn.close()
                except Exception as e:
                    self.logger.error(f"Failed to create database {target_database}: {e}")
                    raise

        try:
            conn_str = self._get_connection_string(database=target_database)
            connection = pyodbc.connect(conn_str, autocommit=True)

            cursor = connection.cursor()
            cursor.execute("SELECT @@VERSION")
            cursor.fetchone()
            cursor.close()

            self.logger.info(f"Connected to Azure Synapse at {self.server}")

            self.log_operation_complete("Azure Synapse connection", details=f"Connected to {self.server}")

            return connection

        except Exception as e:
            self.logger.error(f"Failed to connect to Azure Synapse: {e}")
            raise

    def create_schema(self, benchmark, connection: Any) -> float:
        self.log_operation_start("Azure Synapse schema creation")
        start_time = mono_time()

        cursor = connection.cursor()

        try:
            if self.schema and self.schema.lower() != "dbo":
                self.log_verbose(f"Creating schema: {self.schema}")
                cursor.execute(
                    f"""
                    IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = '{self.schema}')
                    BEGIN
                        EXEC('CREATE SCHEMA [{self.schema}]')
                    END
                """
                )

            schema_sql = self._create_schema_with_tuning(benchmark, source_dialect="standard")

            statements = [stmt.strip() for stmt in schema_sql.split(";") if stmt.strip()]

            for statement in statements:
                if not statement.upper().startswith("CREATE TABLE"):
                    continue

                table_name = self._extract_table_name(statement)
                if not table_name:
                    continue

                self.log_notice(f"Dropping table if it exists: {self.schema}.{table_name}")
                cursor.execute(
                    f"""
                    IF OBJECT_ID('{self.schema}.{table_name}', 'U') IS NOT NULL
                        DROP TABLE [{self.schema}].[{table_name}]
                """
                )

                optimized_statement = self._optimize_table_definition(statement)
                cursor.execute(optimized_statement)
                self.logger.info(f"Created table: {table_name}")

            self.logger.info("Schema created")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise
        finally:
            cursor.close()

        elapsed_time = elapsed_seconds(start_time)
        self.log_operation_complete("Azure Synapse schema creation", details=f"Completed in {elapsed_time:.2f}s")
        return elapsed_time

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        self.log_operation_start("Azure Synapse data loading")
        start_time = mono_time()
        table_stats = {}

        cursor = connection.cursor()

        try:
            data_source = self._resolve_data_files(benchmark, data_dir)

            if self.storage_account and self.container:
                table_stats = self._load_data_via_blob(cursor, data_source, data_dir, benchmark)
            else:
                self.logger.warning("No Azure storage configured, using BULK INSERT")
                table_stats = self._load_data_direct(cursor, data_source, data_dir, benchmark)

            total_time = elapsed_seconds(start_time)
            total_rows = sum(table_stats.values())
            self.logger.info(f"Loaded {total_rows:,} total rows in {total_time:.2f}s")
            self.log_operation_complete(
                "Azure Synapse data loading", details=f"Loaded {total_rows:,} rows in {total_time:.2f}s"
            )

        except Exception as e:
            self.logger.error(f"Data loading failed: {e}")
            raise
        finally:
            cursor.close()

        return table_stats, elapsed_seconds(start_time), None

    def _load_data_via_blob(
        self, cursor: Any, data_source: DataSource, data_dir: Path, benchmark: Any = None
    ) -> dict[str, int]:
        table_stats = {}
        effective_tuning = self.get_effective_tuning_configuration()
        bm = benchmark if benchmark is not None else NO_BENCHMARK

        self._setup_external_data_source(cursor)

        for table_name, file_paths in data_source.tables.items():
            if not isinstance(file_paths, list):
                file_paths = [file_paths]

            valid_files = [Path(f) for f in file_paths if Path(f).exists() and Path(f).stat().st_size > 0]

            if not valid_files:
                self.logger.warning(f"Skipping {table_name} - no valid data files")
                table_stats[table_name] = 0
                continue

            try:
                load_start = mono_time()

                blob_paths = self._upload_to_blob(table_name, valid_files)

                first_file = valid_files[0]
                field_terminator = resolve_csv_dialect(data_source, table_name, first_file, bm).delimiter

                qualified_table = f"[{self.schema}].[{table_name}]"
                for blob_path in blob_paths:
                    if self.storage_sas_token:
                        credential_clause = f", CREDENTIAL = (IDENTITY = 'Shared Access Signature', SECRET = '{self.storage_sas_token}')"
                    elif self.storage_credential:
                        credential_clause = f", CREDENTIAL = '{self.storage_credential}'"
                    else:
                        credential_clause = ""

                    copy_sql = f"""
                        COPY INTO {qualified_table}
                        FROM '{blob_path}'
                        WITH (
                            FILE_TYPE = 'CSV',
                            FIELDTERMINATOR = '{field_terminator}',
                            ROWTERMINATOR = '0x0A',
                            FIRSTROW = 1,
                            ENCODING = 'UTF8'
                            {credential_clause}
                        )
                    """

                    cursor.execute(copy_sql)

                cursor.execute(f"SELECT COUNT(*) FROM {qualified_table}")
                row_count = cursor.fetchone()[0]
                table_stats[table_name] = row_count

                if effective_tuning is not None:
                    ctas_connection = getattr(cursor, "connection", cursor)
                    self.apply_ctas_sort(table_name, effective_tuning, ctas_connection)
                    self.run_post_load_tunings(table_name, effective_tuning, ctas_connection)

                load_time = elapsed_seconds(load_start)
                self.logger.info(f"Loaded {row_count:,} rows into {table_name} in {load_time:.2f}s")

            except Exception as e:
                self.logger.error(f"Failed to load {table_name}: {str(e)[:100]}...")
                table_stats[table_name] = 0

        return table_stats

    def _load_data_direct(
        self, cursor: Any, data_source: DataSource, data_dir: Path, benchmark: Any = None
    ) -> dict[str, int]:
        table_stats = {}
        effective_tuning = self.get_effective_tuning_configuration()
        bm = benchmark if benchmark is not None else NO_BENCHMARK

        for table_name, file_paths in data_source.tables.items():
            if not isinstance(file_paths, list):
                file_paths = [file_paths]

            valid_files = [Path(f) for f in file_paths if Path(f).exists() and Path(f).stat().st_size > 0]

            if not valid_files:
                self.logger.warning(f"Skipping {table_name} - no valid data files")
                table_stats[table_name] = 0
                continue

            try:
                load_start = mono_time()
                total_rows = 0

                qualified_table = f"[{self.schema}].[{table_name}]"

                for file_path in valid_files:
                    delimiter = resolve_csv_dialect(data_source, table_name, file_path, bm).delimiter

                    with open(file_path, encoding="utf-8") as f:
                        batch_size = 1000
                        batch_data = []

                        for line in f:
                            line = line.strip()
                            if line and line.endswith(delimiter):
                                line = line[:-1]

                            values = line.split(delimiter)
                            escaped_values = ["'" + str(v).replace("'", "''") + "'" for v in values]
                            batch_data.append(f"({', '.join(escaped_values)})")

                            if len(batch_data) >= batch_size:
                                insert_sql = f"INSERT INTO {qualified_table} VALUES " + ", ".join(batch_data)
                                cursor.execute(insert_sql)
                                total_rows += len(batch_data)
                                batch_data = []

                        if batch_data:
                            insert_sql = f"INSERT INTO {qualified_table} VALUES " + ", ".join(batch_data)
                            cursor.execute(insert_sql)
                            total_rows += len(batch_data)

                table_stats[table_name] = total_rows

                if effective_tuning is not None:
                    ctas_connection = getattr(cursor, "connection", cursor)
                    self.apply_ctas_sort(table_name, effective_tuning, ctas_connection)
                    self.run_post_load_tunings(table_name, effective_tuning, ctas_connection)

                load_time = elapsed_seconds(load_start)
                self.logger.info(f"Loaded {total_rows:,} rows into {table_name} in {load_time:.2f}s")

            except Exception as e:
                self.logger.error(f"Failed to load {table_name}: {str(e)[:100]}...")
                table_stats[table_name] = 0

        return table_stats

    def validate_external_table_requirements(self) -> None:
        if not self.storage_account or not self.container:
            raise ValueError(
                "Azure Synapse external mode requires blob storage configuration "
                "(set --platform-option staging_root=abfss://container@account.dfs.core.windows.net/path "
                "or provide storage_account + container options)."
            )

    @staticmethod
    def _map_external_column_type(column_type: str) -> str:
        normalized = str(column_type).strip().upper()
        if not normalized:
            return "VARCHAR(8000)"
        if normalized.startswith(("DECIMAL", "NUMERIC", "VARCHAR", "CHAR")):
            return normalized
        if "BIGINT" in normalized:
            return "BIGINT"
        if "SMALLINT" in normalized:
            return "SMALLINT"
        if "INT" in normalized:
            return "INT"
        if "DOUBLE" in normalized or "FLOAT" in normalized:
            return "FLOAT"
        if "REAL" in normalized:
            return "REAL"
        if "DATE" in normalized:
            return "DATE"
        if "TIMESTAMP" in normalized or "DATETIME" in normalized:
            return "DATETIME2"
        if "BOOLEAN" in normalized or normalized == "BOOL":
            return "BIT"
        return "VARCHAR(8000)"

    def _build_external_column_definitions(self, benchmark: Any, table_name: str) -> str:
        if not hasattr(benchmark, "get_schema"):
            raise ValueError(
                f"Benchmark schema metadata unavailable for '{table_name}'. "
                "Azure Synapse external mode requires benchmark.get_schema()."
            )

        schema = benchmark.get_schema() or {}
        table_schema = schema.get(table_name) or schema.get(table_name.lower()) or schema.get(table_name.upper())
        if not table_schema:
            raise ValueError(f"No schema definition found for table '{table_name}'.")

        columns = table_schema.get("columns", [])
        if not columns:
            raise ValueError(f"No columns found in schema definition for table '{table_name}'.")

        column_defs = []
        for column in columns:
            column_name = str(column.get("name", "")).strip()
            if not column_name:
                continue
            mapped_type = self._map_external_column_type(str(column.get("type", "")))
            column_defs.append(f"[{column_name}] {mapped_type}")

        if not column_defs:
            raise ValueError(f"No valid column definitions were generated for table '{table_name}'.")

        return ", ".join(column_defs)

    _normalize_existing_files = staticmethod(normalize_existing_files)

    def _resolve_data_files(self, benchmark: Any, data_dir: Path) -> DataSource:
        return resolve_adapter_data_source(self, benchmark, data_dir)

    def _upload_external_parquet_to_blob(self, table_name: str, files: list[Path]) -> str:
        try:
            from azure.storage.blob import BlobServiceClient

            if self.storage_sas_token:
                account_url = f"https://{self.storage_account}.blob.core.windows.net"
                blob_service = BlobServiceClient(account_url=account_url, credential=self.storage_sas_token)
            elif self.storage_account_key:
                connection_string = (
                    f"DefaultEndpointsProtocol=https;"
                    f"AccountName={self.storage_account};"
                    f"AccountKey={self.storage_account_key};"
                    f"EndpointSuffix=core.windows.net"
                )
                blob_service = BlobServiceClient.from_connection_string(connection_string)
            else:
                from azure.identity import DefaultAzureCredential

                account_url = f"https://{self.storage_account}.blob.core.windows.net"
                blob_service = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())

            container_client = blob_service.get_container_client(self.container)
            table_name_lower = table_name.lower()
            relative_prefix = f"{self.storage_path}/{self.database.lower()}_external/{table_name_lower}".strip("/")

            for file_path in files:
                blob_name = f"{relative_prefix}/{file_path.name}"
                with open(file_path, "rb") as data:
                    container_client.upload_blob(name=blob_name, data=data, overwrite=True)

            return f"/{relative_prefix}/"

        except ImportError:
            raise ImportError(
                "Azure Storage SDK required for external mode uploads. "
                "Install with: pip install azure-storage-blob azure-identity"
            ) from None

    def _resolve_external_credential_name(self, cursor: Any) -> str | None:
        if self.storage_credential:
            return self.storage_credential

        if self.storage_sas_token:
            credential_name = "BENCHBOX_EXTERNAL_SAS_CRED"
            escaped_sas = self.storage_sas_token.replace("'", "''")
            cursor.execute(
                f"""
                IF NOT EXISTS (SELECT 1 FROM sys.database_scoped_credentials WHERE name = '{credential_name}')
                BEGIN
                    CREATE DATABASE SCOPED CREDENTIAL [{credential_name}]
                    WITH IDENTITY = 'SHARED ACCESS SIGNATURE', SECRET = '{escaped_sas}'
                END
                """
            )
            return credential_name

        if self.storage_account_key:
            credential_name = "BENCHBOX_EXTERNAL_KEY_CRED"
            escaped_key = self.storage_account_key.replace("'", "''")
            cursor.execute(
                f"""
                IF NOT EXISTS (SELECT 1 FROM sys.database_scoped_credentials WHERE name = '{credential_name}')
                BEGIN
                    CREATE DATABASE SCOPED CREDENTIAL [{credential_name}]
                    WITH IDENTITY = '{self.storage_account}', SECRET = '{escaped_key}'
                END
                """
            )
            return credential_name

        return None

    def _setup_external_table_primitives(self, cursor: Any) -> tuple[str, str]:
        self._setup_external_data_source(cursor)

        data_source_name = "BENCHBOX_EXTERNAL_SOURCE"
        file_format_name = "BENCHBOX_PARQUET_FORMAT"
        credential_name = self._resolve_external_credential_name(cursor)
        location = f"abfss://{self.container}@{self.storage_account}.dfs.core.windows.net"

        credential_clause = f", CREDENTIAL = [{credential_name}]" if credential_name else ""
        cursor.execute(
            f"""
            IF NOT EXISTS (SELECT 1 FROM sys.external_data_sources WHERE name = '{data_source_name}')
            BEGIN
                CREATE EXTERNAL DATA SOURCE [{data_source_name}]
                WITH (
                    TYPE = HADOOP,
                    LOCATION = '{location}'
                    {credential_clause}
                )
            END
            """
        )

        cursor.execute(
            f"""
            IF NOT EXISTS (SELECT 1 FROM sys.external_file_formats WHERE name = '{file_format_name}')
            BEGIN
                CREATE EXTERNAL FILE FORMAT [{file_format_name}] WITH (FORMAT_TYPE = PARQUET)
            END
            """
        )

        return data_source_name, file_format_name

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        self.validate_external_table_requirements()

        start_time = mono_time()
        table_stats: dict[str, int] = {}
        cursor = connection.cursor()

        try:
            data_files = self._resolve_data_files(benchmark, data_dir)
            data_source_name, file_format_name = self._setup_external_table_primitives(cursor)

            for table_name, file_paths in data_files.items():
                table_name_lower = table_name.lower()
                parquet_files = [
                    path for path in self._normalize_existing_files(file_paths) if path.suffix.lower() == ".parquet"
                ]
                if not parquet_files:
                    raise ValueError(
                        f"Azure Synapse external mode requires Parquet files for table '{table_name_lower}'. "
                        "No Parquet sources were found."
                    )

                location = self._upload_external_parquet_to_blob(table_name_lower, parquet_files)
                columns = self._build_external_column_definitions(benchmark, table_name_lower)
                qualified_table = f"[{self.schema}].[{table_name_lower}]"

                cursor.execute(f"DROP EXTERNAL TABLE IF EXISTS {qualified_table}")
                cursor.execute(
                    f"""
                    CREATE EXTERNAL TABLE {qualified_table}
                    ({columns})
                    WITH (
                        LOCATION = '{location}',
                        DATA_SOURCE = [{data_source_name}],
                        FILE_FORMAT = [{file_format_name}]
                    )
                    """
                )
                cursor.execute(f"SELECT COUNT(*) FROM {qualified_table}")
                result = cursor.fetchone()
                table_stats[table_name_lower] = int(result[0]) if result else 0

        finally:
            cursor.close()

        total_time = elapsed_seconds(start_time)
        return table_stats, total_time, None

    def _setup_external_data_source(self, cursor: Any) -> None:
        import secrets

        master_key_password = secrets.token_urlsafe(32)
        escaped_password = master_key_password.replace("'", "''")
        try:
            cursor.execute(
                f"""
                IF NOT EXISTS (SELECT * FROM sys.symmetric_keys WHERE name = '##MS_DatabaseMasterKey##')
                BEGIN
                    CREATE MASTER KEY ENCRYPTION BY PASSWORD = '{escaped_password}'
                END
            """
            )
        except Exception as e:
            self.logger.debug(f"Master key setup: {e}")

    def _upload_to_blob(self, table_name: str, files: list[Path]) -> list[str]:
        blob_paths = []

        try:
            from azure.storage.blob import BlobServiceClient

            if self.storage_sas_token:
                account_url = f"https://{self.storage_account}.blob.core.windows.net"
                blob_service = BlobServiceClient(account_url=account_url, credential=self.storage_sas_token)
            elif self.storage_account_key:
                connection_string = (
                    f"DefaultEndpointsProtocol=https;"
                    f"AccountName={self.storage_account};"
                    f"AccountKey={self.storage_account_key};"
                    f"EndpointSuffix=core.windows.net"
                )
                blob_service = BlobServiceClient.from_connection_string(connection_string)
            else:
                from azure.identity import DefaultAzureCredential

                account_url = f"https://{self.storage_account}.blob.core.windows.net"
                blob_service = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())

            container_client = blob_service.get_container_client(self.container)

            for idx, file_path in enumerate(files):
                blob_name = f"{self.storage_path}/{table_name}_{idx}{file_path.suffix}"

                with open(file_path, "rb") as data:
                    container_client.upload_blob(name=blob_name, data=data, overwrite=True)

                blob_url = f"https://{self.storage_account}.blob.core.windows.net/{self.container}/{blob_name}"
                blob_paths.append(blob_url)

                self.log_very_verbose(f"Uploaded {file_path.name} to {blob_url}")

        except ImportError:
            raise ImportError(
                "Azure Storage SDK required for blob uploads. Install with: pip install azure-storage-blob azure-identity"
            ) from None

        return blob_paths

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        cursor = connection.cursor()

        try:
            if self.disable_result_cache:
                cursor.execute("SET RESULT_SET_CACHING OFF")
                self.logger.debug("Disabled result set caching")

            if self.resource_class:
                try:
                    cursor.execute(f"EXEC sp_addrolemember '{self.resource_class}', '{self.username}'")
                except Exception:
                    pass

            if benchmark_type.lower() in ["olap", "analytics", "tpch", "tpcds"]:
                cursor.execute("SET ANSI_NULLS ON")
                cursor.execute("SET QUOTED_IDENTIFIER ON")

        finally:
            cursor.close()

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
        self.log_operation_start("Azure Synapse query execution", query_id)
        start_time = mono_time()

        cursor = connection.cursor()

        try:
            cursor.execute(query)
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

            result_dict = self._build_query_result_with_validation(
                query_id=query_id,
                execution_time=execution_time,
                actual_row_count=actual_row_count,
                first_row=result[0] if result else None,
                validation_result=validation_result,
                materialized_rows=result,
            )

            self.log_verbose(f"Query {query_id} completed: {actual_row_count} rows in {execution_time:.3f}s")
            self.log_operation_complete("Azure Synapse query execution", query_id, f"returned {actual_row_count} rows")

        except Exception as e:
            execution_time = elapsed_seconds(start_time)
            self.log_verbose(f"Query {query_id} failed after {execution_time:.3f}s: {e}")

            return {
                "query_id": query_id,
                "status": "FAILED",
                "execution_time_seconds": execution_time,
                "rows_returned": 0,
                "error": str(e),
                "error_type": type(e).__name__,
            }
        finally:
            cursor.close()

        self._merge_plan_capture_into_result(result_dict, connection, query, query_id)

        return result_dict

    def _extract_table_name(self, statement: str) -> str | None:
        match = re.search(r"CREATE\s+TABLE\s+\[?([^\s\[\](]+)\]?", statement, re.IGNORECASE)
        return match.group(1).strip() if match else None

    def _optimize_table_definition(self, statement: str) -> str:
        if not statement.upper().startswith("CREATE TABLE"):
            return statement

        table_name = self._extract_table_name(statement)
        if table_name and f"[{self.schema}]" not in statement:
            statement = re.sub(
                rf"CREATE\s+TABLE\s+\[?{re.escape(table_name)}\]?",
                f"CREATE TABLE [{self.schema}].[{table_name}]",
                statement,
                flags=re.IGNORECASE,
            )

        if "WITH" not in statement.upper() or "DISTRIBUTION" not in statement.upper():
            if statement.rstrip().endswith(")"):
                statement = statement.rstrip()[:-1] + f") WITH (DISTRIBUTION = {self.distribution_default})"
            else:
                statement += f" WITH (DISTRIBUTION = {self.distribution_default})"

        return statement

    def _get_existing_tables(self, connection: Any) -> list[str]:
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = '{self.schema}'
                AND TABLE_TYPE = 'BASE TABLE'
            """
            )
            return [row[0].lower() for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "azure_synapse",
            "platform_name": "Azure Synapse",
            "connection_mode": "remote",
            "cloud_provider": "Azure",
            "server": self.server,
            "configuration": {
                "database": self.database,
                "schema": self.schema,
                "resource_class": self.resource_class,
                "distribution_default": self.distribution_default,
                "result_cache_enabled": not self.disable_result_cache,
            },
            "dialect": SYNAPSE_DIALECT,
        }

        if connection:
            cursor = None
            try:
                cursor = connection.cursor()

                cursor.execute("SELECT @@VERSION")
                result = cursor.fetchone()
                platform_info["platform_version"] = result[0] if result else None

                try:
                    cursor.execute(
                        f"""
                        SELECT
                            SUM(reserved_page_count) * 8.0 / 1024 AS size_mb
                        FROM sys.dm_pdw_nodes_db_partition_stats
                        WHERE DB_NAME(database_id) = '{self.database}'
                    """
                    )
                    result = cursor.fetchone()
                    if result:
                        platform_info["database_size_mb"] = result[0]
                except Exception:
                    pass

            except Exception as e:
                self.logger.debug(f"Error collecting platform info: {e}")
            finally:
                if cursor:
                    cursor.close()

        return platform_info

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        from benchbox.platforms.base.sql_execution import get_query_plan_from_cursor

        return get_query_plan_from_cursor(connection, query)

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.azure_synapse import AzureSynapseQueryPlanParser

        return AzureSynapseQueryPlanParser()

    def analyze_table(self, connection: Any, table_name: str) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(f"UPDATE STATISTICS [{self.schema}].[{table_name}]")
            self.logger.info(f"Updated statistics for {table_name}")
        finally:
            cursor.close()

    def close_connection(self, connection: Any) -> None:
        try:
            if connection and hasattr(connection, "close"):
                connection.close()
        except Exception as e:
            self.logger.warning(f"Error closing connection: {e}")

    def test_connection(self) -> bool:
        try:
            conn_str = self._get_connection_string()
            connection = pyodbc.connect(conn_str, autocommit=True)
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            connection.close()
            return True
        except Exception:
            return False

    _supported_tuning_type_names = ("DISTRIBUTION", "PARTITIONING", "INDEXING")

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
                dist_name = self.resolve_physical_column(table_tuning.table_name, dist_col.name)
                clauses.append(f"DISTRIBUTION = HASH([{dist_name}])")
            else:
                clauses.append(f"DISTRIBUTION = {self.distribution_default}")

            partition_columns = table_tuning.get_columns_by_type(TuningType.PARTITIONING)
            if partition_columns:
                sorted_cols = sorted(partition_columns, key=lambda col: col.order)
                part_col = sorted_cols[0]
                part_name = self.resolve_physical_column(table_tuning.table_name, part_col.name)
                clauses.append(f"PARTITION ([{part_name}] RANGE RIGHT FOR VALUES ())")

            clauses.append("CLUSTERED COLUMNSTORE INDEX")

        except ImportError:
            pass

        return f"WITH ({', '.join(clauses)})" if clauses else ""

    def apply_table_tunings(self, table_tuning, connection: Any) -> None:
        if not table_tuning or not table_tuning.has_any_tuning():
            return

        table_name = table_tuning.table_name
        self.logger.info(f"Azure Synapse tunings for {table_name} applied during table creation")

        try:
            self.analyze_table(connection, self.resolve_physical_table(table_name, connection))
        except Exception as e:
            self.logger.warning(f"Failed to update statistics for {table_name}: {e}")

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

        apply_standard_unified_tuning(self, unified_config, connection)

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        if not platform_config:
            return

        self.logger.info("Azure Synapse platform optimizations applied")

    apply_constraint_configuration = make_informational_constraint_applier(
        "Primary key constraints enabled for Azure Synapse (informational only)",
        "Foreign key constraints enabled for Azure Synapse (informational only)",
    )


_build_synapse_config = make_registered_platform_config_builder(
    "synapse",
    __name__,
    "Azure Synapse",
    "pyodbc",
    [
        "server",
        "port",
        "username",
        "password",
        "schema",
        "auth_method",
        "storage_account",
        "container",
        "staging_root",
        "storage_sas_token",
    ],
)
