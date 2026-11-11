from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from benchbox.core.results.timer_scope import TIMING_METHOD_QUERY_ONLY, outside_query_timer
from benchbox.core.transactional.staging_keys import leading_key_columns
from benchbox.core.tuning.generators.starrocks import DEFAULT_BUCKET_COUNT
from benchbox.platforms.base.data_loading import (
    DataSource,
    FileFormatRegistry,
    resolve_csv_dialect,
)
from benchbox.platforms.base.sql_execution import execute_sql_query
from benchbox.platforms.starrocks.stream_load import (
    StarRocksParquetHandler,
    StarRocksStreamLoadHandler,
    StreamLoadTarget,
    treats_empty_as_null,
)
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.dialect_utils import apply_rewrite, create_table_shape, is_create_table, translate_ddl

logger = logging.getLogger(__name__)


def _alias_derived_tables(query: str) -> str:
    return apply_rewrite("add_derived_table_aliases", query, "starrocks", prefix="_sq", quoted=True)


class StarRocksWorkloadMixin:
    timing_method = TIMING_METHOD_QUERY_ONLY

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()

        enable_primary_keys, enable_foreign_keys = self._get_constraint_configuration()
        self._log_constraint_configuration(enable_primary_keys, enable_foreign_keys)

        try:
            effective_config = self.get_effective_tuning_configuration()
            schema_sql = benchmark.get_create_tables_sql(
                dialect="duckdb",
                tuning_config=effective_config,
            )

            table_tunings = None
            if self.tuning_enabled and effective_config is not None:
                table_tunings = effective_config.table_tunings

            schema_sql_clean = re.sub(r"--[^\n]*\n?", "", schema_sql)
            statements = [stmt.strip() for stmt in schema_sql_clean.split(";") if stmt.strip()]

            cursor = connection.cursor()
            try:
                try:
                    cursor.execute('ADMIN SET FRONTEND CONFIG("tablet_create_timeout_second"="600")')
                except Exception:
                    pass

                for statement in statements:
                    statement = self._translate_schema_statement(statement, table_tunings)
                    cursor.execute(statement)
                    self.logger.debug(f"Executed schema statement: {statement[:100]}...")
            finally:
                cursor.close()

            self.logger.info(f"Schema created (PKs: {enable_primary_keys}, FKs: {enable_foreign_keys})")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise

        return elapsed_seconds(start_time)

    def _translate_schema_statement(self, statement: str, table_tunings: dict[str, Any] | None = None) -> str:
        if not is_create_table(statement):
            return statement

        source_dialect = "starrocks" if "`" in statement else "standard"
        table_name, columns, key = create_table_shape(statement, source_dialect)
        if not columns:
            return "; ".join(
                translate_ddl(statement, "starrocks", source_dialect=source_dialect, **self._ddl_options())
            )

        table_tuning, tuning_clauses = self._resolve_table_tuning(table_name, table_tunings)
        layout = self._starrocks_layout(columns, key, table_tuning)
        if staging_key := leading_key_columns(table_name, columns):
            layout |= {"key_model": "primary", "key_columns": staging_key, "distribute_by": [staging_key[0]]}
        undefined = sorted(
            {name.lower() for field in ("distribute_by", "partition_by", "sort_by") for name in layout.get(field, [])}
            - set(columns)
        )
        if undefined:
            raise ValueError(f"Tuned columns {undefined} are not defined by table {table_name!r}")
        if table_tunings is not None and layout["key_model"] == "primary":
            self._record_starrocks_tuning_to_ledger(table_name, [f"PRIMARY KEY ({', '.join(key)})"])
        if tuning_clauses is not None:
            self._record_starrocks_tuning_to_ledger(
                table_name,
                [
                    clause
                    for clause in (tuning_clauses.partition_by, tuning_clauses.distribute_by, tuning_clauses.order_by)
                    if clause
                ],
            )
        return "; ".join(
            translate_ddl(
                statement, "starrocks", source_dialect=source_dialect, default_layout=layout, **self._ddl_options()
            )
        )

    @staticmethod
    def _ddl_options() -> dict[str, Any]:
        return {"strip_foreign_keys": True, "type_map": "starrocks"}

    @staticmethod
    def _starrocks_layout(columns: list[str], key: list[str], table_tuning: Any) -> dict[str, Any]:
        first = columns[0]
        primary = bool(key) and set(key) == set(columns[: len(set(key))])
        layout: dict[str, Any] = {
            "key_model": "primary" if primary else "duplicate",
            "key_columns": [] if primary else [first],
            "distribution": "hash",
            "distribute_by": [first],
            "buckets": DEFAULT_BUCKET_COUNT,
        }
        if table_tuning is not None:
            from benchbox.core.tuning.interface import TuningType

            def tuned(tuning_type: Any) -> list[str]:
                return [c.name for c in sorted(table_tuning.get_columns_by_type(tuning_type), key=lambda c: c.order)]

            distribution = tuned(TuningType.DISTRIBUTION)
            if distribution:
                layout["distribute_by"] = distribution[:1]
            layout["partition_by"] = tuned(TuningType.PARTITIONING)
            layout["sort_by"] = tuned(TuningType.SORTING)
        return layout

    def _resolve_table_tuning(self, table_name: str | None, table_tunings: dict[str, Any] | None):
        if not table_tunings or table_name is None:
            return None, None

        table_tuning = None
        for configured_name, configured_tuning in table_tunings.items():
            if str(configured_name).upper() == table_name.upper():
                table_tuning = configured_tuning
                break

        if table_tuning is None or not table_tuning.has_any_tuning():
            return None, None

        from benchbox.core.tuning.ddl_generator import get_ddl_generator

        clauses = get_ddl_generator("starrocks").generate_tuning_clauses(table_tuning)
        if clauses.is_empty():
            return None, None
        return table_tuning, clauses

    def _record_starrocks_tuning_to_ledger(self, table_name: str | None, clauses: list[str]) -> None:
        ledger = getattr(self, "_applied_tuning_ledger", None)
        if ledger is None or not clauses:
            return
        try:
            from benchbox.core.tuning.applied_ledger import PHASE_DDL

            for clause in clauses:
                ledger.record(clause, PHASE_DDL, mechanism="starrocks_ddl_generator", table=table_name)
        except Exception as exc:
            self.logger.debug("StarRocks applied-ledger record degraded: %s", exc)

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        from benchbox.platforms.base.data_loading import DataLoader

        target = StreamLoadTarget(self.host, self.http_port, self.database, self.username, self.password)

        def starrocks_handler_factory(file_path, adapter, benchmark_instance, table_name=None, data_source=None):
            base_ext = FileFormatRegistry.get_base_data_extension(file_path)

            if base_ext in (".tbl", ".dat", ".csv"):
                dialect_source = data_source or DataSource(source_type="starrocks_handler", tables={})
                dialect = resolve_csv_dialect(
                    dialect_source, table_name or file_path.stem, file_path, benchmark_instance
                )
                null_empty = treats_empty_as_null(dialect.delimiter, dialect.null_marker)
                return StarRocksStreamLoadHandler(
                    dialect.delimiter,
                    target,
                    null_empty_strings=null_empty,
                    has_header=dialect.has_header,
                )
            elif base_ext == ".parquet":
                return StarRocksParquetHandler(target)
            return None

        cursor = connection.cursor()
        try:
            cursor.execute("SET query_timeout = 86400")
            cursor.execute("SET SESSION net_read_timeout = 86400")
            cursor.execute("SET SESSION net_write_timeout = 86400")
            cursor.execute("SET SESSION max_allowed_packet = 67108864")
        except Exception:
            pass
        finally:
            cursor.close()

        loader = DataLoader(
            adapter=self,
            benchmark=benchmark,
            connection=connection,
            data_dir=data_dir,
            handler_factory=starrocks_handler_factory,
            tuning_config=self.unified_tuning_configuration if self.tuning_enabled else None,
        )
        table_stats, loading_time = loader.load()
        return table_stats, loading_time, None

    def _get_existing_tables(self, connection) -> list[str]:
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SHOW TABLES")
                tables = [row[0].lower() for row in cursor.fetchall()]
                return tables
            finally:
                cursor.close()
        except Exception as e:
            self.logger.debug(f"Failed to get existing tables: {e}")
            return []

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
        with outside_query_timer():
            query = _alias_derived_tables(query)
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
            timing_fields_for=self.timing_fields_for,
        )
        result.setdefault("translated_query", None)
        return result


__all__ = ["StarRocksWorkloadMixin"]
