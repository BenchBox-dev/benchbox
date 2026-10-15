# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import math
import re
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import (
        ForeignKeyConfiguration,
        PlatformOptimizationConfiguration,
        PrimaryKeyConfiguration,
        TableTuning,
        TuningColumn,
        UnifiedTuningConfiguration,
    )

from .base import DriverIsolationCapability, PlatformAdapter
from .base.connection_wrappers import _make_stream_cursor

try:
    import sqlite3
except ImportError:
    sqlite3 = None

_SQLITE_VERSION_ERRORS: tuple[type[Exception], ...]
if sqlite3 is not None:
    _SQLITE_VERSION_ERRORS = (sqlite3.Error, AttributeError, IndexError, TypeError)
else:
    _SQLITE_VERSION_ERRORS = (AttributeError, IndexError, TypeError)

_TPCH_TABLES: frozenset[str] = frozenset(
    {"customer", "lineitem", "nation", "orders", "part", "partsupp", "region", "supplier"}
)

_TPCH_HELPER_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_bb_tpch_lineitem_order_supp_dates "
    "ON lineitem (l_orderkey, l_suppkey, l_receiptdate, l_commitdate)",
    "CREATE INDEX IF NOT EXISTS idx_bb_tpch_orders_status_order ON orders (o_orderstatus, o_orderkey)",
    "CREATE INDEX IF NOT EXISTS idx_bb_tpch_supplier_nation_supp ON supplier (s_nationkey, s_suppkey)",
    "CREATE INDEX IF NOT EXISTS idx_bb_tpch_nation_name_key ON nation (n_name, n_nationkey)",
    "CREATE INDEX IF NOT EXISTS idx_bb_tpch_customer_nation_cust ON customer (c_nationkey, c_custkey)",
    "CREATE INDEX IF NOT EXISTS idx_bb_tpch_orders_date_order_customer ON orders (o_orderdate, o_orderkey, o_custkey)",
    "CREATE INDEX IF NOT EXISTS idx_bb_tpch_orders_customer_date ON orders (o_custkey, o_orderdate, o_orderkey)",
    "CREATE INDEX IF NOT EXISTS idx_bb_tpch_lineitem_part_supp_dates_qty "
    "ON lineitem (l_partkey, l_suppkey, l_shipdate, l_quantity)",
    "CREATE INDEX IF NOT EXISTS idx_bb_tpch_partsupp_part_supp_cost "
    "ON partsupp (ps_partkey, ps_suppkey, ps_supplycost)",
)

_JOINORDER_TABLES: frozenset[str] = frozenset(
    {
        "aka_name",
        "aka_title",
        "cast_info",
        "char_name",
        "comp_cast_type",
        "company_name",
        "company_type",
        "complete_cast",
        "info_type",
        "keyword",
        "kind_type",
        "link_type",
        "movie_companies",
        "movie_info",
        "movie_info_idx",
        "movie_keyword",
        "movie_link",
        "name",
        "person_info",
        "role_type",
        "title",
    }
)

_JOINORDER_HELPER_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_bb_job_company_name_country_id ON company_name (country_code, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_company_type_kind_id ON company_type (kind, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_info_type_info_id ON info_type (info, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_keyword_keyword_id ON keyword (keyword, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_kind_type_kind_id ON kind_type (kind, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_link_type_link_id ON link_type (link, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_role_type_role_id ON role_type (role, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_comp_cast_type_kind_id ON comp_cast_type (kind, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_title_year_kind_id ON title (production_year, kind_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_title_kind_year_id ON title (kind_id, production_year, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_name_name_id ON name (name, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_name_pcode_gender_id ON name (name_pcode_cf, gender, id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_aka_name_person_id ON aka_name (person_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_aka_title_movie_kind ON aka_title (movie_id, kind_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_cast_info_person_movie ON cast_info (person_id, movie_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_cast_info_movie_person ON cast_info (movie_id, person_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_cast_info_role_movie ON cast_info (role_id, movie_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_cast_info_person_role_movie ON cast_info (person_role_id, movie_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_complete_cast_movie_subject_status "
    "ON complete_cast (movie_id, subject_id, status_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_movie_companies_type_movie_company "
    "ON movie_companies (company_type_id, movie_id, company_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_movie_companies_company_movie_type "
    "ON movie_companies (company_id, movie_id, company_type_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_movie_companies_movie_type_company "
    "ON movie_companies (movie_id, company_type_id, company_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_movie_info_type_movie ON movie_info (info_type_id, movie_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_movie_info_movie_type ON movie_info (movie_id, info_type_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_movie_info_idx_type_movie ON movie_info_idx (info_type_id, movie_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_movie_info_idx_movie_type ON movie_info_idx (movie_id, info_type_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_movie_keyword_keyword_movie ON movie_keyword (keyword_id, movie_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_movie_keyword_movie_keyword ON movie_keyword (movie_id, keyword_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_movie_link_link_movie_linked "
    "ON movie_link (link_type_id, movie_id, linked_movie_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_movie_link_linked_link_movie "
    "ON movie_link (linked_movie_id, link_type_id, movie_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_person_info_person_type ON person_info (person_id, info_type_id)",
    "CREATE INDEX IF NOT EXISTS idx_bb_job_person_info_type_person ON person_info (info_type_id, person_id)",
)


class _SQLiteStdDev:
    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

    def step(self, value: Any) -> None:
        if value is None:
            return
        value = float(value)
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)

    def finalize(self) -> float | None:
        if self.count < 2:
            return None
        return math.sqrt(self.m2 / (self.count - 1))


class _SQLitePercentileCont:
    def __init__(self) -> None:
        self.percentile: float | None = None
        self.values: list[float] = []

    def step(self, percentile: Any, value: Any) -> None:
        if percentile is None or value is None:
            return
        current_percentile = float(percentile)
        if not 0.0 <= current_percentile <= 1.0:
            raise ValueError("percentile must be between 0 and 1")
        if self.percentile is None:
            self.percentile = current_percentile
        elif self.percentile != current_percentile:
            raise ValueError("percentile must be constant within an aggregate")
        self.values.append(float(value))

    def finalize(self) -> float | None:
        if not self.values:
            return None
        self.values.sort()
        percentile = self.percentile or 0.0
        position = (len(self.values) - 1) * percentile
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return self.values[lower]
        weight = position - lower
        return self.values[lower] + (self.values[upper] - self.values[lower]) * weight


def _sqlite_regexp_replace(value: Any, pattern: Any, replacement: Any) -> str | None:
    if value is None or pattern is None or replacement is None:
        return None
    return re.sub(str(pattern), str(replacement), str(value))


def _register_sqlite_compatibility_functions(connection: Any) -> None:
    connection.create_function("REGEXP_REPLACE", 3, _sqlite_regexp_replace)
    connection.create_aggregate("STDDEV", 1, _SQLiteStdDev)
    connection.create_aggregate("STDDEV_SAMP", 1, _SQLiteStdDev)
    connection.create_aggregate("PERCENTILE_CONT", 2, _SQLitePercentileCont)


class SQLiteAdapter(PlatformAdapter):
    driver_isolation_capability = DriverIsolationCapability.NOT_APPLICABLE
    plan_capture_phase_eligible = True

    @property
    def platform_name(self) -> str:
        return "SQLite"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        if not hasattr(parser, "add_argument"):
            return
        try:
            parser.add_argument(
                "--sqlite-database",
                dest="database_path",
                help="SQLite database path (auto-generated when --benchmark/--scale provided)",
            )
            parser.add_argument(
                "--sqlite-timeout",
                dest="timeout",
                type=float,
                default=30.0,
                help="SQLite connection timeout in seconds",
            )
            parser.add_argument(
                "--sqlite-check-same-thread",
                dest="check_same_thread",
                action="store_true",
                help="Enable SQLite check_same_thread flag",
            )
        except Exception:
            pass

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from pathlib import Path

        from benchbox.utils.database_naming import generate_database_filename

        adapter_config = {}

        if config.get("database_path"):
            adapter_config["database_path"] = config["database_path"]
        elif config.get("connection_string"):
            adapter_config["database_path"] = config["connection_string"]
        elif config.get("benchmark") and config.get("scale_factor") is not None:
            from benchbox.utils.path_utils import get_benchmark_runs_databases_path

            if config.get("output_dir"):
                data_dir = get_benchmark_runs_databases_path(
                    config["benchmark"],
                    config["scale_factor"],
                    base_dir=Path(config["output_dir"]) / "databases",
                )
            else:
                data_dir = get_benchmark_runs_databases_path(config["benchmark"], config["scale_factor"])

            db_filename = generate_database_filename(
                benchmark_name=config["benchmark"],
                scale_factor=config["scale_factor"],
                platform="sqlite",
                tuning_config=config.get("tuning_config"),
            )
            adapter_config["database_path"] = str(data_dir / db_filename)
            data_dir.mkdir(parents=True, exist_ok=True)
        else:
            from ..core.exceptions import ConfigurationError

            raise ConfigurationError(
                "SQLite requires database path configuration.\n"
                "Either pass --platform-option database_path=<path> or provide --benchmark and --scale."
            )

        adapter_config["timeout"] = config.get("timeout", 30.0)
        adapter_config["check_same_thread"] = config.get("check_same_thread", False)

        _opts = config.get("options") or {}
        adapter_config["force_recreate"] = bool(
            config.get("force_recreate") or _opts.get("force_recreate") or config.get("force")
        )

        from benchbox.platforms.base.config_utils import PLAN_FORWARD_KEYS

        for key in PLAN_FORWARD_KEYS:
            if key in config and config[key] is not None:
                adapter_config[key] = config[key]
        for key in [
            "tuning_config",
            "tuning_enabled",
            "unified_tuning_configuration",
            "tuning_source",
            "tuning_source_file",
            "verbose_enabled",
            "very_verbose",
        ]:
            if key in config:
                adapter_config[key] = config[key]

        return cls(**adapter_config)

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        platform_info = {
            "platform_type": "sqlite",
            "platform_name": "SQLite",
            "connection_mode": "memory" if self.database_path == ":memory:" else "file",
            "configuration": {
                "database_path": self.database_path,
                "timeout": self.timeout,
                "check_same_thread": self.check_same_thread,
            },
        }

        try:
            import sqlite3

            platform_info["client_library_version"] = "builtin"
            platform_info["platform_version"] = sqlite3.sqlite_version
        except (ImportError, AttributeError):
            platform_info["client_library_version"] = None
            platform_info["platform_version"] = None

        if connection is not None:
            try:
                engine_version = connection.execute("SELECT sqlite_version()").fetchone()[0]
                if isinstance(engine_version, str) and engine_version:
                    platform_info["engine_version"] = engine_version
                    platform_info["engine_version_source"] = "sql_query"
            except _SQLITE_VERSION_ERRORS:
                pass

        return platform_info

    def get_target_dialect(self) -> str:
        return "sqlite"

    def __init__(self, **config):
        super().__init__(**config)
        if sqlite3 is None:
            raise ImportError("SQLite not available (should be included with Python)")

        self.database_path = config.get("database_path", ":memory:")
        self.timeout = config.get("timeout", 30.0)
        self.check_same_thread = config.get("check_same_thread", False)

    def get_database_path(self, **connection_config) -> str | None:
        db_path = connection_config.get("database_path")
        if db_path is not None:
            return db_path
        return self.database_path

    def _open_connection(self, db_path: str | None) -> Any:
        conn = sqlite3.connect(
            db_path,
            timeout=self.timeout,
            check_same_thread=self.check_same_thread,
            cached_statements=0,
        )

        _register_sqlite_compatibility_functions(conn)

        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = 10000")
        conn.execute("PRAGMA temp_store = MEMORY")
        return conn

    @staticmethod
    def _main_database_file(connection: Any) -> str:
        for _seq, name, file in connection.execute("PRAGMA database_list").fetchall():
            if name == "main":
                return file or ""
        return ""

    _IN_MEMORY_REQUIRES_FILE_MESSAGE = (
        "SQLite throughput requires a file-backed database: an in-memory database cannot be "
        "shared by independent stream connections. Use a database_path on disk."
    )

    def ensure_stream_sessions_supported(self, connection: Any = None) -> None:
        if connection is not None:
            in_memory = not self._main_database_file(connection)
        else:
            database_path = str(self.get_database_path() or "")
            in_memory = database_path in {"", ":memory:"} or database_path.startswith("file::memory:")
        if in_memory:
            raise RuntimeError(self._IN_MEMORY_REQUIRES_FILE_MESSAGE)

    def new_stream_connection(self, connection: Any, *, benchmark_type: str | None = None) -> Any:
        database_file = self._main_database_file(connection)
        if not database_file:
            return _make_stream_cursor(connection)
        stream_connection = self._open_connection(database_file)
        try:
            if benchmark_type is not None:
                self.configure_for_benchmark(stream_connection, benchmark_type)
        except Exception:
            stream_connection.close()
            raise
        return stream_connection

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("SQLite connection")

        self.handle_existing_database(**connection_config)

        db_path = self.get_database_path(**connection_config)
        self.log_very_verbose(f"SQLite database path: {db_path}")

        sqlite3.register_adapter(Decimal, float)

        conn = self._open_connection(db_path)

        optimizations_applied = [
            "foreign_keys=ON",
            "journal_mode=WAL",
            "synchronous=NORMAL",
            "cache_size=10000",
            "temp_store=MEMORY",
        ]

        self.log_operation_complete("SQLite connection", details=f"Applied: {', '.join(optimizations_applied)}")

        return conn

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        self.log_operation_start("Schema creation", f"benchmark: {benchmark.__class__.__name__}")

        schema_sql = self._create_schema_with_tuning(benchmark, source_dialect="standard")

        self.log_very_verbose(f"Executing schema creation script ({len(schema_sql)} characters)")

        connection.executescript(schema_sql)
        connection.commit()

        duration = elapsed_seconds(start_time)
        self.log_operation_complete("Schema creation", duration, "Schema and tables created")
        return duration

    def apply_table_tunings(self, table_tuning: TableTuning, connection: Any) -> None:
        pass

    def generate_tuning_clause(self, table_tuning: TableTuning) -> str:
        return ""

    def apply_unified_tuning(self, unified_config: UnifiedTuningConfiguration, connection: Any) -> None:
        pass

    def apply_platform_optimizations(self, platform_config: PlatformOptimizationConfiguration, connection: Any) -> None:
        pass

    def apply_constraint_configuration(
        self,
        primary_key_config: PrimaryKeyConfiguration,
        foreign_key_config: ForeignKeyConfiguration,
        connection: Any,
    ) -> None:
        if foreign_key_config and hasattr(foreign_key_config, "enabled"):
            if foreign_key_config.enabled:
                connection.execute("PRAGMA foreign_keys = ON")
            else:
                connection.execute("PRAGMA foreign_keys = OFF")

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        from benchbox.platforms.base.data_loading import DataLoader

        loader = DataLoader(
            adapter=self,
            benchmark=benchmark,
            connection=connection,
            data_dir=data_dir,
            tuning_config=self.unified_tuning_configuration if self.tuning_enabled else None,
        )
        table_stats, loading_time = loader.load()
        helper_index_time = self._apply_benchmark_helper_indexes(benchmark, connection)
        return table_stats, loading_time + helper_index_time, None

    def _apply_benchmark_helper_indexes(self, benchmark: Any, connection: Any) -> float:
        if _is_tpch_benchmark(benchmark):
            return self._apply_helper_index_set(
                benchmark_name="TPC-H",
                required_tables=_TPCH_TABLES,
                statements=_TPCH_HELPER_INDEXES,
                connection=connection,
                analyze=False,
            )
        if _is_joinorder_benchmark(benchmark):
            return self._apply_helper_index_set(
                benchmark_name="JoinOrder",
                required_tables=_JOINORDER_TABLES,
                statements=_JOINORDER_HELPER_INDEXES,
                connection=connection,
                analyze=True,
            )

        return 0.0

    def _apply_helper_index_set(
        self,
        *,
        benchmark_name: str,
        required_tables: frozenset[str],
        statements: tuple[str, ...],
        connection: Any,
        analyze: bool,
    ) -> float:
        existing_tables = set(self._get_existing_tables(connection))
        if not required_tables.issubset(existing_tables):
            missing = ", ".join(sorted(required_tables - existing_tables))
            self.log_very_verbose(f"Skipping SQLite {benchmark_name} helper indexes; missing tables: {missing}")
            return 0.0

        start_time = mono_time()
        created = 0
        for statement in statements:
            try:
                connection.execute(statement)
                created += 1
            except Exception as exc:
                self.logger.warning("SQLite helper index creation failed: %s", exc)

        if created and analyze:
            try:
                connection.execute("ANALYZE")
            except Exception as exc:
                self.logger.warning("SQLite ANALYZE after helper indexes failed: %s", exc)

        if created:
            connection.commit()
            self.log_verbose(f"Applied {created} SQLite {benchmark_name} helper index(es)")

        return elapsed_seconds(start_time)

    def _build_ctas_sort_sql(self, table_name: str, sort_columns: list[TuningColumn]) -> str | None:
        return None

    def _get_existing_tables(self, connection) -> list[str]:
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            result = cursor.fetchall()
            return [row[0].lower() for row in result]
        except Exception as e:
            self.logger.debug(f"Failed to get existing tables: {e}")
            return []

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        if benchmark_type == "olap":
            connection.execute("PRAGMA query_only = false")
            connection.execute("PRAGMA read_uncommitted = true")
        elif benchmark_type == "oltp":
            connection.execute("PRAGMA synchronous = FULL")

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        if callable(getattr(connection, "cursor", None)):
            cursor = connection.cursor()
            _owns_cursor = True
        elif hasattr(connection, "connection") and callable(getattr(connection.connection, "cursor", None)):
            cursor = connection.connection.cursor()
            _owns_cursor = True
        else:
            cursor = connection
            _owns_cursor = False
        try:
            cursor.execute(f"EXPLAIN QUERY PLAN {query}")
            rows = cursor.fetchall()
            return _format_sqlite_query_plan(rows) if rows else None
        except Exception as e:
            self.logger.debug(f"Failed to get SQLite query plan: {e}")
            return None
        finally:
            if _owns_cursor and cursor is not None:
                cursor.close()

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.sqlite import SQLiteQueryPlanParser

        return SQLiteQueryPlanParser()

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
        start_time = mono_time()
        self.log_verbose(f"Executing query {query_id}")
        self.log_very_verbose(f"Query SQL (first 200 chars): {query[:200]}{'...' if len(query) > 200 else ''}")

        cursor = None
        _owns_cursor = False
        try:
            _owns_cursor = callable(getattr(connection, "cursor", None))
            cursor = connection.cursor() if _owns_cursor else connection
            cursor.execute(query)
            results = cursor.fetchall()

            execution_time = elapsed_seconds(start_time)
            actual_row_count = len(results)

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

            result = self._build_query_result_with_validation(
                query_id=query_id,
                execution_time=execution_time,
                actual_row_count=actual_row_count,
                first_row=results[0] if results else None,
                validation_result=validation_result,
                materialized_rows=results,
            )
            result["results"] = results

        except Exception as e:
            return self._build_query_failure_result(query_id, start_time, e)
        finally:
            if _owns_cursor and cursor is not None:
                cursor.close()

        if not self.capture_plans:
            self.display_query_plan_if_enabled(connection, query, query_id)

        self._merge_plan_capture_into_result(result, connection, query, query_id)

        return result

    def run_power_test(self, benchmark, **kwargs) -> dict[str, Any]:
        raise NotImplementedError("Power test not implemented for SQLite adapter")

    def run_throughput_test(self, benchmark, **kwargs) -> dict[str, Any]:
        raise NotImplementedError("Throughput test not implemented for SQLite adapter")

    def run_maintenance_test(self, benchmark, **kwargs) -> dict[str, Any]:
        raise NotImplementedError("Maintenance test not implemented for SQLite adapter")


def _format_sqlite_query_plan(rows: list) -> str | None:
    if not rows:
        return None
    node_text: dict[int, str] = {}
    children: dict[int, list[int]] = {}
    for row in rows:
        node_id, parent_id, _unused, detail = int(row[0]), int(row[1]), row[2], str(row[3])
        node_text[node_id] = detail
        children.setdefault(parent_id, []).append(node_id)

    roots = children.get(0, [])
    if not roots:
        return None

    lines = ["QUERY PLAN"]

    def _emit(node_id: int, prefix: str, is_last: bool) -> None:
        marker = "`--" if is_last else "|--"
        lines.append(f"{prefix}{marker}{node_text[node_id]}")
        child_ids = children.get(node_id, [])
        ext = "   " if is_last else "|  "
        for i, cid in enumerate(child_ids):
            _emit(cid, prefix + ext, i == len(child_ids) - 1)

    for i, rid in enumerate(roots):
        _emit(rid, "", i == len(roots) - 1)

    return "\n".join(lines)


def _is_tpch_benchmark(benchmark: Any) -> bool:
    benchmark_id = str(getattr(benchmark, "benchmark_id", "") or getattr(benchmark, "id", "")).lower()
    if benchmark_id in {"tpch", "tpc-h"}:
        return True
    class_name = benchmark.__class__.__name__.lower()
    display_name = str(getattr(benchmark, "_name", "") or getattr(benchmark, "name", "")).lower()
    return "tpch" in class_name or "tpc-h" in display_name


def _is_joinorder_benchmark(benchmark: Any) -> bool:
    benchmark_id = str(getattr(benchmark, "benchmark_id", "") or getattr(benchmark, "id", "")).lower()
    if benchmark_id in {"joinorder", "join-order"}:
        return True
    class_name = benchmark.__class__.__name__.lower()
    display_name = str(getattr(benchmark, "_name", "") or getattr(benchmark, "name", "")).lower()
    return "joinorder" in class_name or "join order" in display_name
