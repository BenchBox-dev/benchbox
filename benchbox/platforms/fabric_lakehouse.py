from __future__ import annotations

import logging
import re
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        ForeignKeyConfiguration,
        PlatformOptimizationConfiguration,
        PrimaryKeyConfiguration,
    )

from benchbox.core.exceptions import ConfigurationError, ReadOnlyPlatformError
from benchbox.core.results.timer_scope import TIMING_METHOD_QUERY_ONLY, RequestLedger, outside_query_timer
from benchbox.platforms.base import DriverIsolationCapability, PlatformAdapter
from benchbox.utils.dependencies import check_platform_dependencies, get_dependency_error_message

logger = logging.getLogger(__name__)

FABRIC_LAKEHOUSE_DIALECT = "tsql"
_SQL_COPT_SS_ACCESS_TOKEN = 1256

try:
    import pyodbc
except ImportError:
    pyodbc = None


class FabricLakehouseAdapter(PlatformAdapter):
    timing_method = TIMING_METHOD_QUERY_ONLY
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.NOT_FEASIBLE

    _READ_ONLY_TOKENS = {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "CREATE",
        "ALTER",
        "DROP",
        "TRUNCATE",
        "COPY",
    }

    def __init__(self, **config: Any) -> None:
        super().__init__(**config)

        available, missing = check_platform_dependencies("fabric")
        if not available:
            raise ImportError(get_dependency_error_message("fabric", missing))

        self._dialect = FABRIC_LAKEHOUSE_DIALECT

        self.server = config.get("server")
        self.workspace = config.get("workspace")
        self.lakehouse = config.get("lakehouse")
        self.database = config.get("database") or self.lakehouse

        self.port = config.get("port") if config.get("port") is not None else 1433
        self.schema = config.get("schema") or "dbo"

        self.auth_method = config.get("auth_method") or "default_credential"
        self.tenant_id = config.get("tenant_id")
        self.client_id = config.get("client_id")
        self.client_secret = config.get("client_secret")

        self.driver = config.get("driver") or "ODBC Driver 18 for SQL Server"
        self.connect_timeout = config.get("connect_timeout") if config.get("connect_timeout") is not None else 30
        self.query_timeout = config.get("query_timeout") if config.get("query_timeout") is not None else 0

        if not self.server and not self.workspace:
            raise ConfigurationError(
                "Fabric Lakehouse requires connection details. Provide either workspace or full SQL endpoint server."
            )

        if not self.database:
            raise ConfigurationError("Fabric Lakehouse requires database or lakehouse name.")

        if self.auth_method == "service_principal" and not all([self.client_id, self.client_secret, self.tenant_id]):
            missing_fields = []
            if not self.client_id:
                missing_fields.append("client_id")
            if not self.client_secret:
                missing_fields.append("client_secret")
            if not self.tenant_id:
                missing_fields.append("tenant_id")
            raise ConfigurationError(
                f"Fabric Lakehouse service principal authentication is incomplete. Missing: {', '.join(missing_fields)}"
            )

        if not self.server and self.workspace:
            self.server = f"{self.workspace}.sql.azuresynapse.net"

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(**config)

    @property
    def platform_name(self) -> str:
        return "Fabric Lakehouse"

    def get_target_dialect(self) -> str:
        return FABRIC_LAKEHOUSE_DIALECT

    @staticmethod
    def add_cli_arguments(parser) -> None:
        group = parser.add_argument_group("Fabric Lakehouse Arguments")
        group.add_argument("--server", type=str, help="Fabric Lakehouse SQL endpoint")
        group.add_argument("--workspace", type=str, help="Fabric workspace GUID or name")
        group.add_argument("--lakehouse", type=str, help="Fabric lakehouse name")
        group.add_argument("--database", type=str, help="Database name (defaults to lakehouse)")
        group.add_argument(
            "--auth-method",
            type=str,
            choices=["service_principal", "default_credential", "interactive"],
            default="default_credential",
            help="Authentication method (Entra ID only)",
        )

    def _get_connection_string(self) -> str:
        return (
            f"DRIVER={{{self.driver}}};"
            f"SERVER={self.server},{self.port};"
            f"DATABASE={self.database};"
            "Encrypt=yes;"
            "TrustServerCertificate=no;"
            f"Connection Timeout={self.connect_timeout};"
        )

    def _get_access_token(self) -> str:
        try:
            from azure.identity import (
                ClientSecretCredential,
                DefaultAzureCredential,
                InteractiveBrowserCredential,
            )
        except ImportError as err:
            raise ImportError("azure-identity is required for Fabric Lakehouse authentication") from err

        if self.auth_method == "service_principal":
            credential = ClientSecretCredential(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                client_secret=self.client_secret,
            )
        elif self.auth_method == "interactive":
            credential = InteractiveBrowserCredential(tenant_id=self.tenant_id)
        else:
            credential = DefaultAzureCredential()

        return credential.get_token("https://database.windows.net/.default").token

    @staticmethod
    def _create_token_struct(token: str) -> bytes:
        token_utf16 = token.encode("utf-16-le")
        return struct.pack("=i", len(token_utf16)) + token_utf16

    def create_connection(self, **connection_config) -> Any:
        if pyodbc is None:
            raise ImportError("pyodbc is required for Fabric Lakehouse platform")

        token = self._get_access_token()
        token_struct = self._create_token_struct(token)
        conn = pyodbc.connect(
            self._get_connection_string(),
            attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct},
            autocommit=True,
        )

        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        finally:
            cursor.close()

        return conn

    def _read_only_error(self, statement: str) -> ReadOnlyPlatformError:
        return ReadOnlyPlatformError(
            "Fabric Lakehouse SQL endpoint is read-only. "
            f"Cannot execute statement: {statement[:120]}\n"
            "Load data and apply DDL using fabric-spark, then run query phases on fabric-lakehouse."
        )

    @staticmethod
    def _strip_leading_comments(sql: str) -> str:
        text = sql.strip()
        while True:
            if text.startswith("--"):
                newline_idx = text.find("\n")
                if newline_idx == -1:
                    return ""
                text = text[newline_idx + 1 :].lstrip()
            elif text.startswith("/*"):
                end_idx = text.find("*/")
                if end_idx == -1:
                    return ""
                text = text[end_idx + 2 :].lstrip()
            else:
                break
        return text

    def _is_read_only_query(self, query: str) -> bool:
        text = self._strip_leading_comments(query)
        if not text:
            return True

        match = re.match(r"^([A-Za-z_]+)", text)
        if not match:
            return True

        token = match.group(1).upper()
        return token not in self._READ_ONLY_TOKENS

    def create_schema(self, benchmark: Any, connection: Any) -> float:
        raise self._read_only_error("CREATE SCHEMA/TABLE")

    def load_data(
        self,
        benchmark: Any,
        connection: Any,
        data_dir: Path,
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        raise self._read_only_error("LOAD DATA")

    def configure_for_benchmark(self, connection: Any, benchmark_type: str = "olap") -> None:
        del benchmark_type
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SET ANSI_NULLS ON")
                cursor.execute("SET ANSI_WARNINGS ON")
            finally:
                cursor.close()
        except Exception as exc:
            logger.debug("SET options not applied (endpoint may not support them): %s", exc)

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str | None = None,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        ledger = RequestLedger()
        start_time = mono_time()

        with outside_query_timer():
            read_only = self._is_read_only_query(query)
        if not read_only:
            error = self._read_only_error(query)
            return {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "FAILED",
                "execution_time_seconds": elapsed_seconds(start_time),
                "rows_returned": 0,
                "error": str(error),
                "error_type": type(error).__name__,
            }

        with outside_query_timer():
            cursor = connection.cursor()
        try:
            start_time = mono_time()
            ledger.add_request()
            cursor.execute(query)
            rows = cursor.fetchall() if getattr(cursor, "description", None) else []
            execution_time = elapsed_seconds(start_time)
            return {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "SUCCESS",
                "execution_time_seconds": execution_time,
                "rows_returned": len(rows),
                **self.timing_fields_for(connection, ledger, rows=rows, elapsed_seconds=execution_time),
            }
        except Exception as exc:
            return {
                "query_id": query_id,
                "stream_id": stream_id,
                "status": "FAILED",
                "execution_time_seconds": elapsed_seconds(start_time),
                "rows_returned": 0,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        finally:
            cursor.close()

    def get_existing_tables(self, connection: Any) -> list[str]:
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = ?
                ORDER BY TABLE_NAME
                """,
                (self.schema,),
            )
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_platform_info(self) -> dict[str, Any]:
        info = {
            "platform": "fabric-lakehouse",
            "display_name": "Microsoft Fabric Lakehouse SQL Endpoint",
            "vendor": "Microsoft",
            "type": "cloud_sql",
            "read_only": True,
            "workspace": self.workspace,
            "database": self.database,
            "server": self.server,
            "dialect": FABRIC_LAKEHOUSE_DIALECT,
            "supports_sql": True,
            "supports_dataframe": False,
        }

        try:
            connection = self.create_connection()
            cursor = connection.cursor()
            cursor.execute("SELECT @@VERSION")
            info["version"] = cursor.fetchone()[0]
            cursor.close()
            connection.close()
        except Exception as exc:
            info["version"] = f"Unknown ({exc})"

        return info

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        del platform_config, connection
        return None

    def apply_constraint_configuration(
        self,
        primary_key_config: PrimaryKeyConfiguration,
        foreign_key_config: ForeignKeyConfiguration,
        connection: Any,
    ) -> None:
        del primary_key_config, foreign_key_config, connection
        return None


__all__ = ["FABRIC_LAKEHOUSE_DIALECT", "FabricLakehouseAdapter"]
