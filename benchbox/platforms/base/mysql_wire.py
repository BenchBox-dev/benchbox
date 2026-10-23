from __future__ import annotations

import sys
from typing import Any, Callable

from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.sql_identifier import is_valid_sql_identifier


def _skip_comment(sql: str, i: int, n: int) -> int | None:
    if sql[i] == "-" and i + 1 < n and sql[i + 1] == "-":
        i += 2
        while i < n and sql[i] != "\n":
            i += 1
        return i
    if sql[i] == "/" and i + 1 < n and sql[i + 1] == "*":
        i += 2
        while i < n and not (sql[i] == "*" and i + 1 < n and sql[i + 1] == "/"):
            i += 1
        return min(i + 2, n)
    return None


def _skip_quoted(sql: str, i: int, n: int, quote: str) -> int:
    i += 1
    while i < n:
        if sql[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if sql[i] == quote:
            if quote in ("'", '"') and i + 1 < n and sql[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    return n


def split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        char = sql[i]
        comment_end = _skip_comment(sql, i, n)
        if comment_end is not None:
            current.append(sql[i:comment_end])
            i = comment_end
            continue
        if char in ("'", '"', "`"):
            quote_end = _skip_quoted(sql, i, n, char)
            current.append(sql[i:quote_end])
            i = quote_end
            continue
        if char == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)
    return statements


class MySqlWireConnectionWrapper:
    optimize_contains_create = False
    reject_create_or_replace = False

    def __init__(self, conn: Any, ddl_optimizer: Callable[[str], str] | None = None) -> None:
        self._conn = conn
        self._ddl_optimizer = ddl_optimizer

    def _check_or_replace(self, stmt: str) -> None:
        if self.reject_create_or_replace and stmt.upper().startswith("CREATE OR REPLACE TABLE"):
            raise NotImplementedError(
                "CREATE OR REPLACE TABLE is not supported on this platform. "
                "Use DROP TABLE IF EXISTS followed by CREATE TABLE."
            )

    def _optimize_if_create(self, stmt: str) -> str:
        upper = stmt.upper()
        is_create = "CREATE TABLE" in upper if self.optimize_contains_create else upper.startswith("CREATE TABLE")
        if self._ddl_optimizer is not None and is_create:
            return self._ddl_optimizer(stmt)
        return stmt

    def execute(self, sql: str, params: Any = None) -> Any:
        if not sql or not sql.strip():
            return self._conn.cursor()

        statements = split_sql_statements(sql)
        if len(statements) <= 1:
            stmt = self._optimize_if_create(sql.strip())
            self._check_or_replace(stmt)
            cursor = self._conn.cursor()
            cursor.execute(stmt, params)
            return cursor

        last_cursor: Any = None
        for stmt in statements:
            self._check_or_replace(stmt)
            stmt = self._optimize_if_create(stmt)
            cursor = self._conn.cursor()
            cursor.execute(stmt)
            last_cursor = cursor
        return last_cursor if last_cursor is not None else self._conn.cursor()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


class MySqlWireLifecycleMixin:
    database_identifier_max_length = 128
    connection_operation_name = "MySQL-wire connection"
    database_exists_rethrow_error_code: int | None = None
    connection_runtime_error_code: int | None = None
    current_database_sql = "SELECT DATABASE()"
    empty_load_details: Any = None
    reset_table_rows_on_load_error = False

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
        from benchbox.platforms.base.sql_execution import execute_sql_query as default_execute_sql_query

        module = sys.modules.get(type(self).__module__)
        execute = getattr(module, "execute_sql_query", default_execute_sql_query)

        return execute(
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

    def _validate_identifier(self, identifier: str) -> bool:
        return is_valid_sql_identifier(identifier, max_length=self.database_identifier_max_length)

    def _connect_database(self, **connection_config: Any) -> Any:
        raise NotImplementedError

    def _wrap_database_connection(self, conn: Any) -> Any:
        return conn

    def check_server_database_exists(
        self,
        schema: str | None = None,
        catalog: str | None = None,
        database: str | None = None,
        **_kwargs: Any,
    ) -> bool:
        db_name = database or self.database
        try:
            conn = self._admin_connect()
            try:
                cursor = conn.cursor()
                cursor.execute("SHOW DATABASES")
                databases = [row[0] for row in cursor.fetchall()]
                cursor.close()
                return db_name in databases
            finally:
                conn.close()
        except Exception as exc:
            if (
                self.database_exists_rethrow_error_code is not None
                and exc.args
                and exc.args[0] == self.database_exists_rethrow_error_code
            ):
                raise
            self.logger.debug(f"Failed to check database existence: {exc}")
            return False

    def drop_database(
        self,
        schema: str | None = None,
        catalog: str | None = None,
        database: str | None = None,
        **_kwargs: Any,
    ) -> None:
        db_name = database or self.database
        if not self._validate_identifier(db_name):
            raise ValueError(f"Invalid database identifier: {db_name}")

        conn = self._admin_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
            cursor.close()
            self.logger.info(f"Dropped database: {db_name}")
        except Exception as exc:
            self.logger.warning(f"Failed to drop database {db_name}: {exc}")
            raise
        finally:
            conn.close()

    def _create_database(self) -> None:
        if not self._validate_identifier(self.database):
            raise ValueError(f"Invalid database identifier: {self.database}")

        conn = self._admin_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{self.database}`")
            cursor.close()
            self.logger.info(f"Created database: {self.database}")
        except Exception as exc:
            self.logger.error(f"Failed to create database: {exc}")
            raise
        finally:
            conn.close()

    def create_connection(self, **connection_config: Any) -> Any:
        self.log_operation_start(self.connection_operation_name)
        try:
            self.handle_existing_database(**connection_config)
            if not self.check_server_database_exists():
                self._create_database()

            conn = self._connect_database(**connection_config)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()

            self.log_operation_complete(
                self.connection_operation_name,
                details=f"Connected to {self.host}:{self.port}/{self.database}",
            )
            return self._wrap_database_connection(conn)
        except Exception as exc:
            if (
                self.connection_runtime_error_code is not None
                and exc.args
                and exc.args[0] == self.connection_runtime_error_code
            ):
                raise RuntimeError(
                    f"Cannot connect to {self.platform_name} at {self.host}:{self.port} - "
                    "verify the server is running and connection settings are correct"
                ) from exc
            raise

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        del connection
        conn = self._connect_database()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            finally:
                cursor.close()
            if benchmark_type is not None:
                self.configure_for_benchmark(conn, benchmark_type)
            return self._wrap_database_connection(conn)
        except Exception:
            try:
                conn.close()
            except Exception as close_error:
                self.logger.debug("Failed to close stream connection after setup error: %r", close_error)
            raise

    def _transform_schema_statement(self, stmt: str, benchmark: Any) -> str:
        return stmt

    def _schema_statements(self, benchmark: Any) -> list[str]:
        schema_sql = self._create_schema_with_tuning(benchmark, source_dialect="standard")
        return [
            self._transform_schema_statement(stmt, benchmark)
            for stmt in [s.strip() for s in schema_sql.split(";") if s.strip()]
        ]

    def create_schema(self, benchmark: Any, connection: Any) -> float:
        start_time = mono_time()
        self.log_operation_start("Schema creation", f"benchmark: {benchmark.__class__.__name__}")
        statements = self._schema_statements(benchmark)
        self.log_very_verbose(f"Executing {len(statements)} schema creation statements")

        cursor = connection.cursor()
        critical_failures = []
        try:
            for stmt in statements:
                try:
                    cursor.execute(stmt)
                except Exception as exc:
                    if stmt.strip().upper().startswith(("CREATE TABLE", "CREATE REFERENCE TABLE")):
                        critical_failures.append((stmt[:80], str(exc)))
                    self.logger.warning(f"Schema statement failed: {exc}")
            connection.commit()
        finally:
            cursor.close()

        if critical_failures:
            summary = "; ".join(f"{stmt}: {err}" for stmt, err in critical_failures)
            raise RuntimeError(f"{len(critical_failures)} critical CREATE TABLE statement(s) failed: {summary}")

        duration = elapsed_seconds(start_time)
        self.log_operation_complete("Schema creation", duration, "Schema and tables created")
        return duration

    def _load_table_name(self, table_name: str) -> str:
        return table_name

    def _handle_invalid_load_table(self, table_name: str, table_stats: dict[str, int]) -> bool:
        self.logger.warning(f"Skipping table with invalid identifier: {table_name}")
        table_stats[table_name] = 0
        return False

    def _resolve_data_files(self, table_path: Any) -> list[Any]:
        from benchbox.platforms.base.data_loading import normalize_table_paths

        return [path for path in normalize_table_paths(table_path) if path.exists()]

    def _load_resolved_data_file(
        self,
        benchmark: Any,
        connection: Any,
        table_name: str,
        source_table_name: str,
        data_file: Any,
        data_source: Any,
    ) -> int:
        raise NotImplementedError

    def load_data(
        self, benchmark: Any, connection: Any, data_dir: Any
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        from benchbox.platforms.base.data_loading import DataSourceResolver

        start_time = mono_time()
        table_stats: dict[str, int] = {}
        per_table_timings: dict[str, Any] = {}
        self.log_operation_start("Data loading", f"source: {data_dir}")

        module = __import__(self.__class__.__module__, fromlist=["DataSourceResolver"])
        resolver_cls = getattr(module, "DataSourceResolver", DataSourceResolver)
        resolver = resolver_cls(
            platform_name=self.platform_name,
            table_mode=self.table_mode,
            platform_config=self.platform_config,
            requested_format=self.requested_table_format,
        )
        data_source = resolver.resolve(benchmark, data_dir)
        if not data_source or not data_source.tables:
            self.logger.warning("No data files found. Ensure benchmark.generate_data() was called first.")
            loading_time = elapsed_seconds(start_time)
            self.log_operation_complete("Data loading", loading_time, "Loaded 0 total rows")
            details = (
                self.empty_load_details.copy() if isinstance(self.empty_load_details, dict) else self.empty_load_details
            )
            return {}, loading_time, details

        for table_name, table_path in data_source.tables.items():
            target_table = self._load_table_name(table_name)
            if not self._validate_identifier(target_table) and not self._handle_invalid_load_table(
                target_table, table_stats
            ):
                per_table_timings[target_table] = {"rows": 0, "duration_seconds": 0.0, "total_ms": 0}
                continue
            data_files = self._resolve_data_files(table_path)
            if not data_files:
                self.logger.warning(f"Data file not found for {target_table}")
                table_stats[target_table] = 0
                per_table_timings[target_table] = {"rows": 0, "duration_seconds": 0.0, "total_ms": 0}
                continue

            table_start = mono_time()
            table_rows = 0
            for data_file in data_files:
                try:
                    table_rows += self._load_resolved_data_file(
                        benchmark, connection, target_table, table_name, data_file, data_source
                    )
                except Exception as exc:
                    self.logger.error(f"Failed to load {target_table}: {exc}")
                    if self.reset_table_rows_on_load_error:
                        table_rows = 0
                        break
            table_stats[target_table] = table_rows
            table_time = elapsed_seconds(table_start)
            per_table_timings[target_table] = {
                "rows": table_rows,
                "duration_seconds": table_time,
                "total_ms": table_time * 1000,
            }
            self.log_verbose(f"Loaded {table_rows:,} rows into {target_table}")

        loading_time = elapsed_seconds(start_time)
        self.log_operation_complete("Data loading", loading_time, f"Loaded {sum(table_stats.values()):,} total rows")
        return table_stats, loading_time, per_table_timings

    def _explain_query_prefix(self, explain_options: dict[str, Any] | None = None) -> str:
        return "EXPLAIN"

    def _format_query_plan_rows(self, rows: Any) -> str:
        return "\n".join(str(row) for row in rows)

    def get_query_plan(
        self,
        connection: Any,
        query: str,
        explain_options: dict[str, Any] | None = None,
    ) -> str | None:
        cursor = connection.cursor()
        try:
            cursor.execute(f"{self._explain_query_prefix(explain_options)} {query}")
            return self._format_query_plan_rows(cursor.fetchall())
        except Exception as exc:
            self.logger.warning(f"Failed to get query plan: {exc}")
            return None
        finally:
            cursor.close()

    def analyze_table(self, connection: Any, table_name: str) -> None:
        if not self._validate_identifier(table_name):
            self.logger.warning(f"Invalid table identifier: {table_name}")
            return
        cursor = connection.cursor()
        try:
            cursor.execute(f"ANALYZE TABLE `{table_name}`")
        except Exception as exc:
            self.logger.warning(f"ANALYZE failed for {table_name}: {exc}")
        finally:
            cursor.close()

    def close_connection(self, connection: Any) -> None:
        if connection:
            try:
                connection.close()
            except Exception as exc:
                self.logger.debug(f"Error closing connection: {exc}")

    def test_connection(self) -> bool:
        try:
            conn = self._admin_connect()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            conn.close()
            return True
        except Exception as exc:
            self.logger.debug(f"Connection test failed: {exc}")
            return False

    def _get_existing_tables(self, connection: Any) -> list[str]:
        try:
            cursor = connection.cursor()
            cursor.execute("SHOW TABLES")
            rows = cursor.fetchall()
            cursor.close()
            return [row[0].lower() for row in rows]
        except Exception as exc:
            self.logger.debug(f"Failed to get existing tables: {exc}")
            return []

    def _populate_connection_health_details(
        self, cursor: Any, warnings: list[str], connection_info: dict[str, Any]
    ) -> None:
        return None

    def validate_connection_health(self, connection: Any):
        errors: list[str] = []
        warnings: list[str] = []
        connection_info: dict[str, Any] = {}
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1 as test_value")
            result = cursor.fetchone()
            if result[0] != 1:
                errors.append("Basic query execution test failed")
            else:
                connection_info["basic_query_test"] = "passed"
            self._populate_connection_health_details(cursor, warnings, connection_info)
            try:
                cursor.execute(self.current_database_sql)
                db_result = cursor.fetchone()
                if db_result:
                    connection_info["current_database"] = db_result[0]
            except Exception:
                warnings.append("Could not query current database")
            cursor.close()
        except Exception as exc:
            errors.append(f"Connection health check failed: {str(exc)}")

        try:
            from benchbox.core.validation import ValidationResult

            return ValidationResult(
                is_valid=len(errors) == 0,
                errors=errors,
                warnings=warnings,
                details={
                    "platform": self.platform_name,
                    "connection_type": type(connection).__name__,
                    **connection_info,
                },
            )
        except ImportError:
            return None


class NoOpTableTuningMixin:
    def apply_table_tunings(self, table_tuning: Any, connection: Any) -> None:
        return None

    def generate_tuning_clause(self, table_tuning: Any | None) -> str:
        return ""

    def apply_unified_tuning(self, unified_config: Any, connection: Any) -> None:
        return None

    def apply_platform_optimizations(self, platform_config: Any, connection: Any) -> None:
        return None

    def apply_constraint_configuration(self, primary_key_config: Any, foreign_key_config: Any, connection: Any) -> None:
        return None


def merged_platform_options(options: dict[str, Any], overrides: dict[str, Any], platform: str) -> dict[str, Any]:
    from benchbox.security.credentials import CredentialManager

    saved_creds = CredentialManager().get_platform_credentials(platform) or {}
    merged: dict[str, Any] = {}
    merged.update(options)
    merged.update(saved_creds)
    merged.update(overrides.get("_explicit_platform_options", {}))
    merged.update(overrides)
    return merged


def build_database_config(
    *,
    platform: str,
    options: dict[str, Any],
    overrides: dict[str, Any],
    info: Any,
    default_name: str,
    default_driver_package: str,
    fields: dict[str, Any],
) -> Any:
    from benchbox.core.schemas import DatabaseConfig

    merged = merged_platform_options(options, overrides, platform)
    config_dict = {
        "type": platform,
        "name": info.display_name if info else default_name,
        "options": merged or {},
        "driver_package": info.driver_package if info else default_driver_package,
        "driver_version": overrides.get("driver_version") or options.get("driver_version"),
        "driver_auto_install": bool(overrides.get("driver_auto_install", options.get("driver_auto_install", False))),
        "benchmark": overrides.get("benchmark"),
        "scale_factor": overrides.get("scale_factor"),
        "tuning_config": overrides.get("tuning_config"),
    }
    config_dict.update({key: value(merged) if callable(value) else value for key, value in fields.items()})
    return DatabaseConfig(**config_dict)
