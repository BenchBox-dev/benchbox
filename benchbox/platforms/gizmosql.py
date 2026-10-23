from __future__ import annotations

import importlib
import importlib.metadata
import json
import logging
import os
import re
import threading
import weakref
from collections.abc import Mapping
from itertools import chain
from pathlib import Path
from typing import Any

from benchbox.core.execution_engine import ExecutionEngineReceipt, platform_default_receipt
from benchbox.core.results.platform_options import sanitize_platform_options
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.dialect_utils import split_sql_statements

from .base import DriverIsolationCapability, PlatformAdapter
from .base.config_utils import build_adapter_config, make_platform_config_builder
from .base.ddl_helpers import strip_tpcds_foreign_keys
from .base.sql_execution import execute_sql_query

logger = logging.getLogger(__name__)

GIZMOSQL_DIALECT = "duckdb"
GIZMOSQL_DRIVER_PACKAGE = "adbc-driver-gizmosql"
GIZMOSQL_DEFAULT_PORT = 31337
GIZMOSQL_MIN_SERVER_VERSION = (1, 37)
GIZMOSQL_SUPPORTED_BACKEND = "duckdb"
GIZMOSQL_SUPPORTED_EDITIONS = ("core", "enterprise")
GIZMOSQL_TIMING_BASIS = "client_observed_flight_sql"
REDACTED = "****"

_AUTHORIZATION_HEADER_OPTION = "adbc.flight.sql.authorization_header"
_TLS_ROOT_CERTS_OPTION = "adbc.flight.sql.client_option.tls_root_certs"
_VERSION_PATTERN = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")
_SETTING_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SETTING_LITERAL_PATTERN = re.compile(r"^(?:-?\d+(?:\.\d+)?|true|false)$", re.IGNORECASE)
_LEADING_KEYWORD_PATTERN = re.compile(r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*([A-Za-z]+)", re.DOTALL)
_CREATE_TABLE_PATTERN = re.compile(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\"[^\"]+\"|[^\s(]+)", re.IGNORECASE)
_CREATE_TABLE_PREFIX_PATTERN = re.compile(r"^(\s*CREATE\s+TABLE\s+)(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE)
_MUTATING_KEYWORDS = frozenset(
    [
        "ALTER",
        "ANALYZE",
        "ATTACH",
        "CHECKPOINT",
        "COMMENT",
        "COPY",
        "CREATE",
        "DELETE",
        "DETACH",
        "DROP",
        "EXPORT",
        "IMPORT",
        "INSERT",
        "MERGE",
        "REPLACE",
        "TRUNCATE",
        "UPDATE",
        "VACUUM",
    ]
)
_OBSERVED_SERVER_SETTINGS = ("access_mode", "memory_limit", "threads")
_CONNECTION_FIELDS = (
    "host",
    "port",
    "username",
    "password",
    "auth_token",
    "catalog",
    "schema",
    "tls",
    "tls_skip_verify",
    "tls_ca_cert",
    "allow_insecure_connection",
    "autocommit",
    "query_timeout",
    "ingest_batch_bytes",
    "session_settings",
)
_DRIVER_FORWARD_KEYS = (
    "driver_package",
    "driver_version",
    "driver_version_requested",
    "driver_version_resolved",
    "driver_version_actual",
    "driver_runtime_strategy",
    "driver_runtime_path",
    "driver_runtime_python_executable",
    "driver_auto_install",
    "driver_auto_install_used",
)


class GizmoSQLError(RuntimeError):
    pass


class GizmoSQLConnectionError(GizmoSQLError):
    pass


class GizmoSQLUnsupportedServerError(GizmoSQLError):
    pass


class GizmoSQLReadOnlyError(GizmoSQLError):
    pass


class GizmoSQLSettingScopeError(GizmoSQLError):
    pass


class GizmoSQLLoadError(GizmoSQLError):
    pass


class GizmoSQLQueryTimeout(GizmoSQLError):
    pass


def load_gizmosql_driver() -> Any:
    try:
        return importlib.import_module("adbc_driver_gizmosql.dbapi")
    except ImportError as exc:
        raise ImportError(
            "The GizmoSQL ADBC driver is not installed. Install it with: uv add 'benchbox[gizmosql]'"
        ) from exc


def parse_server_version(raw: Any) -> tuple[int, ...] | None:
    match = _VERSION_PATTERN.search(str(raw or ""))
    if match is None:
        return None
    return tuple(int(part) for part in match.groups(default="0"))


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def parse_session_settings(raw: Any) -> dict[str, str]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, Mapping):
        pairs = [(str(name), str(value)) for name, value in raw.items()]
    else:
        pairs = []
        for item in re.split(r"[;,]", str(raw)):
            if not item.strip():
                continue
            name, separator, value = item.partition("=")
            if not separator:
                raise ValueError(f"session_settings entry '{item.strip()}' must look like name=value")
            pairs.append((name, value))
    settings: dict[str, str] = {}
    for name, value in pairs:
        name = name.strip().lower()
        if not _SETTING_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Invalid DuckDB setting name in session_settings: '{name}'")
        settings[name] = value.strip()
    return settings


def setting_literal(value: str) -> str:
    if _SETTING_LITERAL_PATTERN.fullmatch(value):
        return value
    return "'" + value.replace("'", "''") + "'"


def extract_explain_json(rows: Any) -> str | None:
    for row in rows or []:
        if row is None or len(row) < 2:
            continue
        value = row[1]
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        if isinstance(value, str):
            try:
                json.loads(value)
            except ValueError:
                continue
            return value
    return None


def _leading_keyword(sql: str) -> str | None:
    match = _LEADING_KEYWORD_PATTERN.match(sql)
    return match.group(1).upper() if match else None


class GizmoSQLCursor:
    def __init__(self, raw: Any, connection: GizmoSQLConnection) -> None:
        self._raw = raw
        self._connection = connection
        self._timer: threading.Timer | None = None
        self._timed_out = threading.Event()

    def _arm(self) -> None:
        self._disarm()
        self._timed_out.clear()
        timeout = self._connection.query_timeout
        if timeout:
            self._timer = threading.Timer(timeout, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _disarm(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()

    def _fire(self) -> None:
        self._timed_out.set()
        try:
            self._raw.adbc_cancel()
        except Exception as exc:
            logger.debug("GizmoSQL cancel after timeout failed: %s", type(exc).__name__)

    def _guarded(self, call: Any, *args: Any) -> Any:
        try:
            return call(*args)
        except Exception:
            self._disarm()
            if self._timed_out.is_set():
                raise GizmoSQLQueryTimeout(
                    f"Query exceeded the {self._connection.query_timeout}s query_timeout and was cancelled"
                ) from None
            raise

    def execute(self, operation: str, parameters: Any = None) -> GizmoSQLCursor:
        self._connection.guard(operation)
        self._arm()
        args = (operation,) if parameters is None else (operation, parameters)
        self._guarded(self._raw.execute, *args)
        if getattr(self._raw, "description", None) is None:
            self._disarm()
        return self

    def fetchall(self) -> list[Any]:
        rows = self._guarded(self._raw.fetchall)
        self._disarm()
        return rows

    def fetchone(self) -> Any:
        return self._guarded(self._raw.fetchone)

    def fetchmany(self, size: int | None = None) -> list[Any]:
        return self._guarded(self._raw.fetchmany) if size is None else self._guarded(self._raw.fetchmany, size)

    def fetch_arrow_table(self) -> Any:
        table = self._guarded(self._raw.fetch_arrow_table)
        self._disarm()
        return table

    @property
    def description(self) -> Any:
        return self._raw.description

    def close(self) -> None:
        self._disarm()
        self._raw.close()

    def __enter__(self) -> GizmoSQLCursor:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


class GizmoSQLConnection:
    def __init__(self, raw: Any, *, autocommit: bool = True, read_only: bool = False, query_timeout: int = 0) -> None:
        self.raw = raw
        self.autocommit = autocommit
        self.read_only = read_only
        self.query_timeout = query_timeout
        self._cursors: weakref.WeakSet[GizmoSQLCursor] = weakref.WeakSet()
        self._last_result: GizmoSQLCursor | None = None

    def guard(self, sql: str) -> None:
        if self.read_only and _leading_keyword(sql) in _MUTATING_KEYWORDS:
            raise GizmoSQLReadOnlyError(
                "The GizmoSQL server is read-only; this statement would modify it. "
                "Run a query-only phase against preloaded data, or point BenchBox at a writable server."
            )

    def cursor(self) -> GizmoSQLCursor:
        cursor = GizmoSQLCursor(self.raw.cursor(), self)
        self._cursors.add(cursor)
        return cursor

    def execute(self, sql: str, parameters: Any = None) -> GizmoSQLCursor:
        previous, self._last_result = self._last_result, None
        if previous is not None:
            previous.close()
        cursor = self.cursor()
        try:
            cursor.execute(sql, parameters)
        except Exception:
            cursor.close()
            raise
        self._last_result = cursor
        return cursor

    def commit(self) -> None:
        if not self.autocommit:
            self.raw.commit()

    def rollback(self) -> None:
        if not self.autocommit:
            self.raw.rollback()

    def close(self) -> None:
        for cursor in list(self._cursors):
            try:
                cursor.close()
            except Exception as exc:
                logger.debug("GizmoSQL cursor close failed: %s", type(exc).__name__)
        self._last_result = None
        self.raw.close()


class GizmoSQLDryRunCursor:
    description = None

    def __init__(self, adapter: GizmoSQLAdapter) -> None:
        self._adapter = adapter

    def execute(self, operation: str, parameters: Any = None) -> GizmoSQLDryRunCursor:
        del parameters
        self._adapter.capture_sql(operation, "query")
        return self

    def fetchall(self) -> list[Any]:
        return []

    def fetchone(self) -> None:
        return None

    def close(self) -> None:
        return None


class GizmoSQLDryRunConnection:
    def __init__(self, adapter: GizmoSQLAdapter) -> None:
        self._adapter = adapter

    def cursor(self) -> GizmoSQLDryRunCursor:
        return GizmoSQLDryRunCursor(self._adapter)

    def execute(self, sql: str, parameters: Any = None) -> GizmoSQLDryRunCursor:
        return self.cursor().execute(sql, parameters)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class GizmoSQLAdapter(PlatformAdapter):
    driver_isolation_capability = DriverIsolationCapability.FEASIBLE_CLIENT_ONLY
    plan_capture_phase_eligible = True
    physical_identifier_case = "lower"
    _supported_tuning_type_names = ()

    @property
    def platform_name(self) -> str:
        return "GizmoSQL"

    def get_target_dialect(self) -> str:
        return GIZMOSQL_DIALECT

    def resolve_execution_engine(self, requested: str) -> ExecutionEngineReceipt:
        return platform_default_receipt("gizmosql", requested)

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument"):
            return
        group = parser.add_argument_group("GizmoSQL Arguments")
        group.add_argument("--gizmosql-host", dest="host", default=None, help="GizmoSQL server hostname")
        group.add_argument(
            "--gizmosql-port", dest="port", type=int, default=None, help="GizmoSQL Flight SQL port (default: 31337)"
        )
        group.add_argument("--gizmosql-username", dest="username", default=None, help="GizmoSQL username")
        group.add_argument("--gizmosql-catalog", dest="catalog", default=None, help="Attached DuckDB catalog to use")
        group.add_argument("--gizmosql-schema", dest="schema", default=None, help="Schema holding the benchmark tables")

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> GizmoSQLAdapter:
        adapter_config = build_adapter_config(
            config, platform="gizmosql", generated_key=None, fields=_CONNECTION_FIELDS, include_none=False
        )
        for key, variable in (
            ("username", "GIZMOSQL_USERNAME"),
            ("password", "GIZMOSQL_PASSWORD"),
            ("auth_token", "GIZMOSQL_AUTH_TOKEN"),
        ):
            if not adapter_config.get(key) and os.environ.get(variable):
                adapter_config[key] = os.environ[variable]
        if not adapter_config.get("schema") and config.get("benchmark") and config.get("scale_factor") is not None:
            from benchbox.utils.scale_factor import format_benchmark_name

            benchmark_name = format_benchmark_name(config["benchmark"], config["scale_factor"])
            adapter_config["schema"] = f"benchbox_{benchmark_name}".lower().replace("-", "_")
        if "force" in config:
            adapter_config["force_recreate"] = config["force"]
        elif "force_recreate" in config:
            adapter_config["force_recreate"] = config["force_recreate"]
        for key in (*_DRIVER_FORWARD_KEYS, "dry_run", "verbose_enabled", "very_verbose"):
            if key in config:
                adapter_config[key] = config[key]
        return cls(**adapter_config)

    def __init__(self, **config: Any) -> None:
        super().__init__(**config)
        self.host = config.get("host") or "localhost"
        self.port = int(config.get("port") or GIZMOSQL_DEFAULT_PORT)
        self.username = config.get("username")
        self.password = config.get("password")
        self.auth_token = config.get("auth_token")
        self.catalog = config.get("catalog")
        self.schema = config.get("schema")
        self.tls = _as_bool(config.get("tls"), True)
        self.tls_skip_verify = _as_bool(config.get("tls_skip_verify"), False)
        self.tls_ca_cert = config.get("tls_ca_cert")
        self.allow_insecure_connection = _as_bool(config.get("allow_insecure_connection"), False)
        self.autocommit = _as_bool(config.get("autocommit"), True)
        self.query_timeout = int(config.get("query_timeout") or 0)
        self.ingest_batch_bytes = int(config.get("ingest_batch_bytes") or _default_ingest_batch_bytes())
        self.session_settings = parse_session_settings(config.get("session_settings"))
        self.skip_database_management = True
        self.server_info: dict[str, Any] | None = None
        self.server_read_only = False
        self.effective_session_settings: dict[str, str] = {}
        self._decision_connection: Any = None
        self._validate_configuration()
        self._driver = None if self.dry_run else load_gizmosql_driver()

    def _validate_configuration(self) -> None:
        from benchbox.platforms.gizmosql_loading import MAX_INGEST_BATCH_BYTES, MIN_INGEST_BATCH_BYTES

        if self.password and self.auth_token:
            raise ValueError("Set either password or auth_token for GizmoSQL, not both")
        if (not self.tls or self.tls_skip_verify) and not self.allow_insecure_connection:
            raise ValueError(
                "GizmoSQL connections verify TLS by default. Plaintext (tls=false) or tls_skip_verify=true is "
                "for local development only and requires allow_insecure_connection=true."
            )
        if self.query_timeout < 0:
            raise ValueError("query_timeout must be zero (disabled) or a positive number of seconds")
        if not MIN_INGEST_BATCH_BYTES <= self.ingest_batch_bytes <= MAX_INGEST_BATCH_BYTES:
            raise ValueError(
                f"ingest_batch_bytes must be between {MIN_INGEST_BATCH_BYTES} and {MAX_INGEST_BATCH_BYTES}: "
                "the GizmoSQL Flight endpoint rejects messages above 16 MiB"
            )

    @property
    def uri(self) -> str:
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"grpc+{'tls' if self.tls else 'tcp'}://{host}:{self.port}"

    def _redact(self, text: str) -> str:
        redacted = text
        for secret in (self.password, self.auth_token):
            if secret:
                redacted = redacted.replace(str(secret), REDACTED)
        return str(sanitize_platform_options({"message": redacted})["message"])

    def _connect(self, *, autocommit: bool) -> GizmoSQLConnection:
        driver = self._driver or load_gizmosql_driver()
        db_kwargs: dict[str, str] = {}
        if self.auth_token:
            db_kwargs[_AUTHORIZATION_HEADER_OPTION] = f"Bearer {self.auth_token}"
        if self.tls_ca_cert:
            db_kwargs[_TLS_ROOT_CERTS_OPTION] = Path(str(self.tls_ca_cert)).expanduser().read_text(encoding="utf-8")
        if not self.tls or self.tls_skip_verify:
            self.logger.warning(
                "GizmoSQL connection to %s:%s is not fully secured (%s); use this for local development only",
                self.host,
                self.port,
                "plaintext" if not self.tls else "TLS verification disabled",
            )
        try:
            raw = driver.connect(
                self.uri,
                username=self.username,
                password=self.password,
                tls_skip_verify=self.tls_skip_verify,
                db_kwargs=db_kwargs,
                autocommit=autocommit,
            )
        except Exception as exc:
            raise GizmoSQLConnectionError(
                f"Could not connect to GizmoSQL at {self.host}:{self.port}: "
                f"{type(exc).__name__}: {self._redact(str(exc))}"
            ) from None
        return GizmoSQLConnection(raw, autocommit=autocommit, query_timeout=self.query_timeout)

    def _first_row(self, connection: Any, sql: str, parameters: Any = None) -> Any:
        cursor = connection.cursor()
        try:
            cursor.execute(sql, parameters) if parameters is not None else cursor.execute(sql)
            return cursor.fetchone()
        finally:
            cursor.close()

    def _all_rows(self, connection: Any, sql: str, parameters: Any = None) -> list[Any]:
        cursor = connection.cursor()
        try:
            cursor.execute(sql, parameters) if parameters is not None else cursor.execute(sql)
            return list(cursor.fetchall())
        finally:
            cursor.close()

    def inspect_server(self, connection: Any) -> dict[str, Any]:
        try:
            row = self._first_row(connection, "SELECT GIZMOSQL_VERSION(), GIZMOSQL_EDITION()")
        except Exception as exc:
            raise GizmoSQLUnsupportedServerError(
                "The endpoint did not answer GIZMOSQL_VERSION()/GIZMOSQL_EDITION(); it is not a supported GizmoSQL "
                f"server ({type(exc).__name__}: {self._redact(str(exc))[:200]})"
            ) from None
        server_version, edition = str(row[0]), str(row[1])
        parsed = parse_server_version(server_version)
        if parsed is None or parsed[:2] < GIZMOSQL_MIN_SERVER_VERSION:
            floor = ".".join(str(part) for part in GIZMOSQL_MIN_SERVER_VERSION)
            raise GizmoSQLUnsupportedServerError(
                f"GizmoSQL server version '{server_version}' is not supported; BenchBox needs {floor} or newer"
            )
        if edition.lower() not in GIZMOSQL_SUPPORTED_EDITIONS:
            raise GizmoSQLUnsupportedServerError(f"Unsupported GizmoSQL edition '{edition}'")
        backend, backend_version = self._detect_backend(connection)
        if backend != GIZMOSQL_SUPPORTED_BACKEND:
            raise GizmoSQLUnsupportedServerError(
                f"GizmoSQL backend '{backend or 'unknown'}' is not supported; BenchBox supports the "
                f"'{GIZMOSQL_SUPPORTED_BACKEND}' backend only (start the server with --backend duckdb)"
            )
        duckdb_row = self._first_row(connection, "SELECT version()")
        duckdb_version = str(duckdb_row[0]).lstrip("v") if duckdb_row else backend_version
        settings = {
            str(name): str(value)
            for name, value in self._all_rows(
                connection,
                "SELECT name, value FROM duckdb_settings() WHERE name IN ("
                + ", ".join("?" for _ in _OBSERVED_SERVER_SETTINGS)
                + ")",
                list(_OBSERVED_SERVER_SETTINGS),
            )
        }
        catalog_row = self._first_row(
            connection, "SELECT readonly FROM duckdb_databases() WHERE database_name = current_database()"
        )
        read_only = settings.get("access_mode") == "read_only" or bool(catalog_row and catalog_row[0])
        if edition.lower() == "enterprise":
            self.logger.warning("GizmoSQL Enterprise detected; BenchBox validates the Core feature set only")
        return {
            "gizmosql_version": server_version.lstrip("v"),
            "edition": edition,
            "backend": backend,
            "duckdb_version": duckdb_version,
            "read_only": read_only,
            "server_settings": settings,
        }

    def _detect_backend(self, connection: Any) -> tuple[str | None, str | None]:
        info = getattr(getattr(connection, "raw", None), "adbc_get_info", None)
        vendor_version = ""
        if callable(info):
            try:
                vendor_version = str(info().get("vendor_version") or "")
            except Exception as exc:
                self.logger.debug("adbc_get_info failed: %s", type(exc).__name__)
        parts = vendor_version.split()
        if not parts:
            return None, None
        return parts[0].lower(), (parts[1].lstrip("v") if len(parts) > 1 else None)

    def require_writable(self, operation: str) -> None:
        if self.server_read_only:
            raise GizmoSQLReadOnlyError(
                f"Cannot {operation}: the GizmoSQL server is read-only. Query-only phases work against preloaded "
                "data; schema creation, loading, statistics and maintenance need a writable server."
            )

    def _namespace_target(self) -> str | None:
        if self.catalog and self.schema:
            return f"{quote_identifier(self.catalog)}.{quote_identifier(self.schema)}"
        if self.catalog:
            return quote_identifier(self.catalog)
        if self.schema:
            return quote_identifier(self.schema)
        return None

    def _prepare_namespace(self, connection: Any) -> None:
        if self.catalog and not self._first_row(
            connection, "SELECT 1 FROM duckdb_databases() WHERE database_name = ?", [self.catalog]
        ):
            raise GizmoSQLError(
                f"Catalog '{self.catalog}' is not attached on the GizmoSQL server; attach it on the server first"
            )
        if self.schema:
            catalog_filter = "?" if self.catalog else "current_database()"
            parameters = [self.schema, self.catalog] if self.catalog else [self.schema]
            exists = self._first_row(
                connection,
                f"SELECT 1 FROM information_schema.schemata WHERE schema_name = ? AND catalog_name = {catalog_filter}",
                parameters,
            )
            if not exists:
                if self.server_read_only:
                    raise GizmoSQLReadOnlyError(
                        f"Schema '{self.schema}' does not exist on the read-only GizmoSQL server. Set the schema "
                        "option to the schema that holds the preloaded benchmark tables (for example main)."
                    )
                target = self._namespace_target()
                self._run_statement(connection, f"CREATE SCHEMA IF NOT EXISTS {target}")
                connection.commit()
        self._configure_session(connection)

    def _configure_session(self, connection: Any) -> None:
        target = self._namespace_target()
        if target:
            self._run_statement(connection, f"USE {target}")
        if not self.session_settings:
            return
        names = list(self.session_settings)
        scopes = {
            str(name): str(scope)
            for name, scope in self._all_rows(
                connection,
                "SELECT name, scope FROM duckdb_settings() WHERE name IN (" + ", ".join("?" for _ in names) + ")",
                names,
            )
        }
        for name in names:
            if name not in scopes:
                raise GizmoSQLSettingScopeError(f"'{name}' is not a DuckDB setting on this GizmoSQL server")
            if scopes[name].upper() != "LOCAL":
                raise GizmoSQLSettingScopeError(
                    f"'{name}' is a {scopes[name].lower()}-scoped DuckDB setting; applying it would change the shared "
                    "GizmoSQL server for every session. Only session-scoped settings are allowed in session_settings."
                )
        for name, value in self.session_settings.items():
            self._run_statement(connection, f"SET {name} = {setting_literal(value)}")
        self.effective_session_settings = {
            str(name): str(value)
            for name, value in self._all_rows(
                connection,
                "SELECT name, value FROM duckdb_settings() WHERE name IN (" + ", ".join("?" for _ in names) + ")",
                names,
            )
        }

    def _run_statement(self, connection: Any, sql: str) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
        finally:
            cursor.close()

    def create_connection(self, **connection_config: Any) -> Any:
        self.log_operation_start("GizmoSQL connection")
        if self.dry_run_mode:
            return GizmoSQLDryRunConnection(self)
        connection = self._connect(autocommit=self.autocommit)
        try:
            self.server_info = self.inspect_server(connection)
            self.server_read_only = bool(self.server_info["read_only"])
            connection.read_only = self.server_read_only
            if self.server_read_only and self.force_recreate:
                raise GizmoSQLReadOnlyError("force_recreate needs a writable GizmoSQL server; this one is read-only")
            self._prepare_namespace(connection)
            self._decide_database_reuse(connection, **connection_config)
        except Exception:
            connection.close()
            raise
        self.log_operation_complete(
            "GizmoSQL connection",
            details=f"GizmoSQL {self.server_info['gizmosql_version']} ({self.server_info['edition']}), "
            f"DuckDB {self.server_info['duckdb_version']}{', read-only' if self.server_read_only else ''}",
        )
        return connection

    def _expected_tables(self) -> list[str]:
        benchmark = getattr(self, "benchmark", None)
        if benchmark is None:
            return []
        return self._get_expected_tables(benchmark) or []

    def _decide_database_reuse(self, connection: Any, **connection_config: Any) -> None:
        if getattr(self, "_existing_db_decided", False):
            return
        self._existing_db_decided = True
        self._decision_connection = connection
        try:
            self.handle_existing_database(**connection_config)
        finally:
            self._decision_connection = None

    def check_benchmark_tables_exist(self, **connection_config: Any) -> bool | None:
        connection = self._decision_connection
        expected = self._expected_tables()
        if connection is None or not expected:
            return False
        present = {
            str(row[0]).lower()
            for row in self._all_rows(
                connection,
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog = current_database() AND table_schema = current_schema()",
            )
        }
        populated = all(
            name in present
            and (self._first_row(connection, f"SELECT count(*) FROM {quote_identifier(name)}") or [0])[0]
            for name in expected
        )
        if self.server_read_only:
            if not populated:
                raise GizmoSQLReadOnlyError(
                    "The read-only GizmoSQL server does not hold populated tables for this benchmark in the selected "
                    "catalog/schema. Preload the data, or set the schema option to where it lives."
                )
            return True
        reuse = bool(populated) and not self.force_recreate
        if reuse:
            self.log_verbose("All benchmark tables are populated on the GizmoSQL server; reusing them")
        return reuse

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        del connection
        if self.dry_run_mode:
            return GizmoSQLDryRunConnection(self)
        stream = self._connect(autocommit=self.autocommit)
        try:
            stream.read_only = self.server_read_only
            self._configure_session(stream)
            if benchmark_type is not None:
                self.configure_for_benchmark(stream, benchmark_type)
            return stream
        except Exception:
            stream.close()
            raise

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self.log_very_verbose(f"GizmoSQL session ready for {benchmark_type}; no server-wide settings are changed")

    def _commit(self, connection: Any) -> None:
        if hasattr(connection, "commit"):
            connection.commit()

    def create_schema(self, benchmark: Any, connection: Any) -> float:
        start_time = mono_time()
        self.log_operation_start("Schema creation", f"benchmark: {benchmark.__class__.__name__}")
        if not self.dry_run_mode:
            self.require_writable("create the benchmark schema")
        enable_primary_keys, enable_foreign_keys = self._get_constraint_configuration()
        self._log_constraint_configuration(enable_primary_keys, enable_foreign_keys)
        schema_sql = strip_tpcds_foreign_keys(
            self, benchmark, self._create_schema_with_tuning(benchmark, source_dialect="standard")
        )
        created = 0
        for statement in split_sql_statements(schema_sql, GIZMOSQL_DIALECT):
            match = _CREATE_TABLE_PATTERN.match(statement)
            table = match.group(1).strip('"') if match else None
            if self.dry_run_mode:
                self.capture_sql(statement, "create_table", table)
                continue
            try:
                if table and self.force_recreate:
                    connection.execute(f"DROP TABLE IF EXISTS {quote_identifier(table)}")
                elif table:
                    statement = _CREATE_TABLE_PREFIX_PATTERN.sub(r"\1IF NOT EXISTS ", statement, count=1)
                connection.execute(statement)
                created += 1
            except GizmoSQLError:
                raise
            except Exception as exc:
                raise GizmoSQLError(
                    f"Failed to create table {table or 'unknown'}: {type(exc).__name__}: {self._redact(str(exc))}"
                ) from None
        self._commit(connection)
        duration = elapsed_seconds(start_time)
        self.log_operation_complete("Schema creation", duration, f"{created} tables ready")
        return duration

    def apply_platform_optimizations(self, platform_config: Any, connection: Any) -> None:
        if platform_config:
            self.log_verbose("GizmoSQL applies no platform optimizations; the server owns DuckDB tuning")

    def apply_constraint_configuration(self, primary_key_config: Any, foreign_key_config: Any, connection: Any) -> None:
        self.log_verbose("GizmoSQL constraints are applied by the generated CREATE TABLE statements")

    def apply_unified_tuning(self, unified_config: Any, connection: Any) -> None:
        self.log_verbose("GizmoSQL does not apply table tunings; constraints come from the CREATE TABLE statements")

    def _table_files(self, paths: Any) -> list[Path]:
        from benchbox.platforms.base.data_loading import normalize_table_paths

        files: list[Path] = []
        for path in normalize_table_paths(paths):
            if path.is_dir():
                found: list[Path] = []
                for pattern in ("*.tbl*", "*.dat*", "*.csv*", "*.parquet*"):
                    found.extend(path.glob(pattern))
                files.extend(sorted(found))
            elif path.is_file():
                files.append(path)
        return files

    def _table_arrow_schema(self, connection: Any, table: str) -> Any:
        cursor = connection.cursor()
        try:
            cursor.execute(f"SELECT * FROM {quote_identifier(table)} LIMIT 0")
            return cursor.fetch_arrow_table().schema
        finally:
            cursor.close()

    def load_table_files(self, loader: Any, table: str, files: list[Path], benchmark: Any, data_source: Any) -> int:
        from benchbox.platforms.base.data_loading import resolve_csv_dialect
        from benchbox.platforms.gizmosql_loading import file_batches, stream_reader
        from benchbox.utils.file_format import is_parquet_format

        quoted = quote_identifier(table)
        cursor = loader.cursor()
        try:
            existing = int(self._first_row(loader, f"SELECT count(*) FROM {quoted}")[0])
            if existing:
                self.log_verbose(f"Table {table} already holds {existing} rows on the GizmoSQL server; keeping them")
                loader.rollback()
                return existing
            schema = self._table_arrow_schema(loader, table)
            dialect = (
                None if is_parquet_format(files[0]) else resolve_csv_dialect(data_source, table, files[0], benchmark)
            )
            reader = stream_reader(
                schema,
                chain.from_iterable(file_batches(path, schema, dialect, self.ingest_batch_bytes) for path in files),
            )
            ingested = cursor.adbc_ingest(table, reader, mode="append")
            row = self._first_row(loader, f"SELECT count(*) FROM {quoted}")
            loaded = int(row[0])
            if ingested is not None and ingested >= 0 and ingested != loaded:
                raise GizmoSQLLoadError(f"ingested {ingested} rows but the table holds {loaded}")
            loader.commit()
            return loaded
        except Exception as exc:
            try:
                loader.rollback()
            except Exception as rollback_exc:
                self.logger.debug("GizmoSQL load rollback failed: %s", type(rollback_exc).__name__)
            raise GizmoSQLLoadError(
                f"Failed to load table {table}; its previous contents were kept: "
                f"{type(exc).__name__}: {self._redact(str(exc))}"
            ) from None
        finally:
            cursor.close()

    def load_data(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        from benchbox.platforms.base.data_loading import resolve_adapter_data_source, run_staged_table_loads

        if not self.dry_run_mode:
            self.require_writable("load benchmark data")
        data_source = resolve_adapter_data_source(self, benchmark, data_dir)
        names = list(data_source.tables)
        order = (
            benchmark.get_table_loading_order(names) if hasattr(benchmark, "get_table_loading_order") else sorted(names)
        )
        tables = {name: data_source.tables[name] for name in order if name in data_source.tables}

        if self.dry_run_mode:
            for name in tables:
                self.capture_sql(f"-- ADBC ingest into {name} from {data_dir}", "load_data", name)
            return {name.lower(): 1000 for name in tables}, 0.0, None

        loader = self._connect(autocommit=False)
        loader.query_timeout = 0
        try:
            self._configure_session(loader)
            return run_staged_table_loads(
                self,
                tables=tables,
                stat_key=str.lower,
                filter_files=self._table_files,
                load_one=lambda table, files: self.load_table_files(
                    loader, table.lower(), files, benchmark, data_source
                ),
                record_timings=True,
                fail_fast=True,
            )
        finally:
            loader.close()
            self._commit(connection)

    def gather_statistics(self, connection: Any, table_names: list[str]) -> tuple[str, int]:
        self.require_writable("gather table statistics")
        for table in table_names:
            connection.execute(f"ANALYZE {quote_identifier(table.lower())}")
        return "explicit", len(table_names)

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
        if self.dry_run_mode:
            self.capture_sql(query, "query", None)
            return {
                "query_id": query_id,
                "status": "DRY_RUN",
                "execution_time_seconds": 0.0,
                "rows_returned": 0,
                "first_row": None,
                "error": None,
                "dry_run": True,
            }
        return self.execute_query_with_plan_capture(
            self._execute_sql,
            connection=connection,
            query=query,
            query_id=query_id,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )

    def _execute_sql(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        result = execute_sql_query(
            connection,
            query,
            query_id,
            log_verbose=self.log_verbose,
            build_query_result_with_validation=self._build_query_result_with_validation,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )
        if result.get("error"):
            result["error"] = self._redact(str(result["error"]))
        return result

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        from benchbox.platforms.base.result_capture import is_dml_query

        if self.dry_run_mode:
            return None
        analyze = self.analyze_plans and not is_dml_query(query)
        options = "ANALYZE, FORMAT JSON" if analyze else "FORMAT JSON"
        cursor = connection.cursor()
        try:
            cursor.execute(f"EXPLAIN ({options}) {query}")
            return extract_explain_json(cursor.fetchall())
        except Exception as exc:
            self.logger.warning("Could not get GizmoSQL query plan: %s", self._redact(str(exc)))
            return None
        finally:
            cursor.close()

    def get_query_plan_parser(self) -> Any:
        from benchbox.core.query_plans.parsers.duckdb import DuckDBQueryPlanParser

        return DuckDBQueryPlanParser()

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        info = self.server_info
        if info is None and connection is not None and not self.dry_run_mode:
            try:
                info = self.inspect_server(connection)
            except Exception as exc:
                self.logger.debug("GizmoSQL server inspection failed: %s", type(exc).__name__)
        platform_info = super().get_platform_info(connection)
        requested_configuration = {
            "host": self.host,
            "port": self.port,
            "catalog": self.catalog,
            "schema": self.schema,
            "tls": self.tls,
            "tls_skip_verify": self.tls_skip_verify,
            "tls_ca_cert": self.tls_ca_cert,
            "allow_insecure_connection": self.allow_insecure_connection,
            "autocommit": self.autocommit,
            "query_timeout": self.query_timeout,
            "ingest_batch_bytes": self.ingest_batch_bytes,
            "session_settings": self.session_settings or None,
            "username": self.username,
            "password": self.password,
            "auth_token": self.auth_token,
        }
        configuration = sanitize_platform_options(
            {key: value for key, value in requested_configuration.items() if value is not None},
            redact_usernames=True,
        )
        platform_info.update(
            {
                "platform_type": "gizmosql",
                "platform_name": "GizmoSQL",
                "connection_mode": "server",
                "host": self.host,
                "port": self.port,
                "configuration": configuration,
                "client_library_version": _driver_version(),
                "timing_basis": GIZMOSQL_TIMING_BASIS,
            }
        )
        if info:
            platform_info.update(
                {
                    "platform_version": info["gizmosql_version"],
                    "engine_version": info["duckdb_version"],
                    "engine_version_source": "sql_query",
                    "edition": info["edition"],
                    "backend": info["backend"],
                    "read_only": info["read_only"],
                }
            )
        return platform_info

    def _get_platform_metadata(self, connection: Any) -> dict[str, Any]:
        info = self.server_info or {}
        return {
            "platform": self.platform_name,
            "gizmosql_version": info.get("gizmosql_version"),
            "edition": info.get("edition"),
            "backend": info.get("backend"),
            "duckdb_version": info.get("duckdb_version"),
            "read_only": info.get("read_only"),
            "server_settings_observed": dict(info.get("server_settings", {})),
            "session_settings_requested": dict(self.session_settings),
            "session_settings_effective": dict(self.effective_session_settings),
            "timing_basis": GIZMOSQL_TIMING_BASIS,
            "result_cache_enabled": False,
        }

    def validate_platform_capabilities(self, benchmark_type: str) -> Any:
        errors: list[str] = []
        warnings: list[str] = []
        try:
            load_gizmosql_driver()
        except ImportError as exc:
            errors.append(str(exc))
        if not self.tls or self.tls_skip_verify:
            warnings.append("TLS verification is relaxed for this GizmoSQL connection (development only)")
        try:
            from benchbox.core.validation import ValidationResult
        except ImportError:
            return None
        return ValidationResult(
            is_valid=not errors,
            errors=errors,
            warnings=warnings,
            details={
                "platform": self.platform_name,
                "benchmark_type": benchmark_type,
                "dry_run_mode": self.dry_run_mode,
                "host": self.host,
                "port": self.port,
            },
        )

    def validate_connection_health(self, connection: Any) -> Any:
        errors: list[str] = []
        details: dict[str, Any] = {}
        try:
            row = self._first_row(connection, "SELECT 1")
            if row is None or row[0] != 1:
                errors.append("Basic query execution test failed")
            else:
                details["basic_query_test"] = "passed"
        except Exception as exc:
            errors.append(f"Connection health check failed: {self._redact(str(exc))}")
        try:
            from benchbox.core.validation import ValidationResult
        except ImportError:
            return None
        return ValidationResult(
            is_valid=not errors,
            errors=errors,
            warnings=[],
            details={"platform": self.platform_name, "connection_type": type(connection).__name__, **details},
        )


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _default_ingest_batch_bytes() -> int:
    from benchbox.platforms.gizmosql_loading import DEFAULT_INGEST_BATCH_BYTES

    return DEFAULT_INGEST_BATCH_BYTES


def _driver_version() -> str | None:
    try:
        return importlib.metadata.version(GIZMOSQL_DRIVER_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return None


_build_gizmosql_config = make_platform_config_builder(
    "gizmosql",
    __name__,
    "GizmoSQL",
    GIZMOSQL_DRIVER_PACKAGE,
    _CONNECTION_FIELDS,
    field_defaults={"host": "localhost", "port": GIZMOSQL_DEFAULT_PORT},
    consume_explicit_options=True,
)
