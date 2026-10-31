from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.coffeeshop.generator import CoffeeShopDataGenerator
from benchbox.core.coffeeshop.queries import CoffeeShopQueryManager
from benchbox.core.coffeeshop.schema import TABLES, get_all_create_table_sql
from benchbox.core.query_catalog_base import TranslatableQueryMixin
from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

_SPARK_FAMILY_DIALECTS = {"spark", "lakesail", "pyspark", "velox", "databricks"}

SPARK_TM1_SQL = """\
SELECT
    CASE
        WHEN HOUR(TO_TIMESTAMP(CONCAT('1970-01-01 ', ol.order_time))) BETWEEN 5 AND 10 THEN 'Morning'
        WHEN HOUR(TO_TIMESTAMP(CONCAT('1970-01-01 ', ol.order_time))) BETWEEN 11 AND 14 THEN 'Midday'
        WHEN HOUR(TO_TIMESTAMP(CONCAT('1970-01-01 ', ol.order_time))) BETWEEN 15 AND 17 THEN 'Afternoon'
        ELSE 'Evening'
    END AS day_part,
    COUNT(DISTINCT ol.order_id) AS orders,
    SUM(ol.total_price) AS revenue,
    AVG(ol.total_price) AS avg_line_value
FROM order_lines ol
JOIN dim_locations dl ON ol.location_record_id = dl.record_id
WHERE dl.region = '{region}'
  AND ol.order_date BETWEEN DATE '{start_date}' AND DATE '{end_date}'
GROUP BY day_part
ORDER BY day_part;"""

SPARK_SA4_SQL = """\
SELECT
    dl.region,
    COUNT(DISTINCT ol.order_id) AS orders,
    SUM(ol.total_price) AS revenue,
    CAST(SUM(ol.total_price) AS DOUBLE) / NULLIF(CAST(totals.grand_total AS DOUBLE), 0D) AS revenue_share
FROM order_lines ol
JOIN dim_locations dl ON ol.location_record_id = dl.record_id
CROSS JOIN (
    SELECT SUM(total_price) AS grand_total
    FROM order_lines
    WHERE order_date BETWEEN DATE '{start_date}' AND DATE '{end_date}'
) totals
WHERE ol.order_date BETWEEN DATE '{start_date}' AND DATE '{end_date}'
GROUP BY dl.region, totals.grand_total
ORDER BY revenue DESC;"""


class CoffeeShopBenchmark(GeneratorOutputDirMixin, TranslatableQueryMixin, BaseBenchmark):
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: str | Path | None = None,
        **config: Any,
    ):
        super().__init__(scale_factor, output_dir=output_dir, **config)

        self._name = "CoffeeShop Benchmark"
        self._version = "2.0"
        self._description = (
            "Reference-aligned CoffeeShop benchmark with Dim_Locations, Dim_Products, "
            "and exploded order_lines fact table."
        )

        self.query_manager: CoffeeShopQueryManager = CoffeeShopQueryManager()
        self.data_generator = CoffeeShopDataGenerator(
            scale_factor=scale_factor,
            output_dir=self.output_dir,
            **config,
        )

        self.tables: dict[str, str] = {}

    def generate_data(
        self,
        tables: list[str] | None = None,
        output_format: str = "csv",
    ) -> dict[str, str]:
        if output_format != "csv":
            raise ValueError(f"Unsupported output format: {output_format}")

        valid_tables = set(TABLES.keys())
        requested = set(tables) if tables else valid_tables

        unknown_tables = requested - valid_tables
        if unknown_tables:
            raise ValueError(f"Invalid table names: {sorted(unknown_tables)}")

        generated = self.data_generator.generate_data(sorted(requested))
        self.tables = generated
        return generated

    def get_query(self, query_id: int | str, *, params: dict[str, Any] | None = None) -> str:
        return self.query_manager.get_query(str(query_id), params)

    def get_queries(self, dialect: str | None = None) -> dict[str, str]:
        from benchbox.sql_compat.actions import CompatAction
        from benchbox.sql_compat.context import CompatibilityContext, Phase
        from benchbox.sql_compat.registry import REGISTRY
        from benchbox.sql_compat.rules.query_source.coffeeshop_variants import (
            CLICKHOUSE_SA4_SQL,
            CLICKHOUSE_TM1_SQL,
        )

        queries = self.query_manager.get_all_queries()
        if not dialect:
            return queries

        translated: dict[str, str] = {}
        for query_id, sql in queries.items():
            translated[query_id] = self.translate_query_text(sql, dialect)

        if dialect.lower() in _SPARK_FAMILY_DIALECTS and "TM1" in translated:
            params = dict(self.query_manager._queries["TM1"].get("defaults", {}))
            translated["TM1"] = SPARK_TM1_SQL.format(**params)
        if dialect.lower() in _SPARK_FAMILY_DIALECTS and "SA4" in translated:
            params = dict(self.query_manager._queries["SA4"].get("defaults", {}))
            translated["SA4"] = SPARK_SA4_SQL.format(**params)

        platform = next((name for name in ("clickhouse", "questdb") if name in dialect.lower()), None)
        if platform is None:
            return translated

        qm = self.query_manager
        legacy_variants: dict[str, str] = (
            {"SA4": CLICKHOUSE_SA4_SQL, "TM1": CLICKHOUSE_TM1_SQL} if platform == "clickhouse" else {}
        )
        for query_id in ("SA4", "TM1"):
            if query_id not in translated:
                continue
            ctx = CompatibilityContext(
                platform=platform,
                platform_version=None,
                benchmark="coffeeshop",
                query_id=query_id,
                phase=Phase.QUERY_SOURCE,
                mode="sql",
                dialect=dialect,
            )
            registry_decision = REGISTRY.resolve(ctx)
            if registry_decision is not None:
                if registry_decision.action is CompatAction.SELECT_VARIANT:
                    variant_sql = registry_decision.payload.variant_sql
                else:
                    continue
            elif query_id in legacy_variants:
                variant_sql = legacy_variants[query_id]
            else:
                continue
            entry = qm._queries[query_id]
            params = dict(entry.get("defaults", {}))
            translated[query_id] = variant_sql.format(**params)

        return translated

    def get_all_queries(self) -> dict[str, str]:
        return self.query_manager.get_all_queries()

    def execute_query(
        self,
        query_id: int | str,
        connection: Any,
        params: dict[str, Any] | None = None,
    ) -> Any:
        sql = self.get_query(query_id, params=params)
        if hasattr(connection, "execute"):
            cursor = connection.execute(sql)
            return cursor.fetchall()
        if hasattr(connection, "cursor"):
            cursor = connection.cursor()
            cursor.execute(sql)
            return cursor.fetchall()
        raise ValueError("Unsupported connection type")

    def get_schema(self, dialect: str = "standard") -> dict[str, dict]:
        return TABLES

    def get_table_loading_order(self, available_tables: list[str]) -> list[str]:

        from benchbox.core.coffeeshop.schema import get_table_loading_order as _schema_table_loading_order

        full_order = _schema_table_loading_order()
        available_set = set(available_tables)

        ordered_tables = [t for t in full_order if t in available_set]
        remaining_tables = [t for t in available_tables if t not in ordered_tables]
        ordered_tables.extend(remaining_tables)

        return ordered_tables

    def get_create_tables_sql(
        self,
        dialect: str = "standard",
        tuning_config: UnifiedTuningConfiguration | None = None,
    ) -> str:
        enable_primary_keys = tuning_config.primary_keys.enabled if tuning_config else True
        enable_foreign_keys = tuning_config.foreign_keys.enabled if tuning_config else True
        return get_all_create_table_sql(
            dialect=dialect,
            enable_primary_keys=enable_primary_keys,
            enable_foreign_keys=enable_foreign_keys,
        )

    def get_csv_loading_config(self, table_name: str) -> list[str]:

        return ["header=false", "auto_detect=true", "ignore_errors=true"]

    def load_data_to_database(self, connection: Any, tables: list[str] | None = None) -> None:
        if not self.tables:
            raise ValueError("No data generated. Call generate_data() first.")

        requested = set(tables) if tables else set(self.tables.keys())
        table_order = ["dim_locations", "dim_products", "order_lines"]
        ordered_tables = [table for table in table_order if table in requested]

        schema_sql = self.get_create_tables_sql()
        if hasattr(connection, "executescript"):
            connection.executescript(schema_sql)
        else:
            cursor = connection.cursor()
            for statement in schema_sql.split(";"):
                if statement.strip():
                    cursor.execute(statement)

        for table_name in ordered_tables:
            file_path = self.tables.get(table_name)
            if not file_path:
                continue

            table_schema = TABLES[table_name]
            columns = [col["name"] for col in table_schema["columns"]]
            placeholders = ",".join(["?" for _ in columns])
            insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders})"

            with open(file_path, encoding="utf-8") as handle:
                reader = csv.reader(handle)
                rows = [self._normalize_row(row) for row in reader]

            if hasattr(connection, "executemany"):
                connection.executemany(insert_sql, rows)
            else:
                cursor = connection.cursor()
                for row in rows:
                    cursor.execute(insert_sql, row)

        if hasattr(connection, "commit"):
            connection.commit()

    def _normalize_row(self, row: list[str]) -> list[Any]:
        return [value if value != "" else None for value in row]

    def run_benchmark(
        self,
        connection: Any,
        queries: list[str] | None = None,
        iterations: int = 1,
    ) -> dict[str, Any]:
        query_ids = queries or list(self.query_manager.get_all_queries().keys())
        results = {
            "benchmark": self._name,
            "scale_factor": self.scale_factor,
            "iterations": iterations,
            "query_results": [],
        }

        for query_id in query_ids:
            iteration_results: list[dict[str, Any]] = []
            min_time = float("inf")
            max_time = 0.0
            total_successful = 0.0
            success_count = 0
            total_rows = 0
            last_error: str | None = None

            for iteration in range(iterations):
                start = mono_time()
                try:
                    rows = self.execute_query(query_id, connection)
                    duration = elapsed_seconds(start)
                    row_count = len(rows) if rows else 0

                    iteration_results.append(
                        {
                            "iteration": iteration + 1,
                            "time": duration,
                            "rows": row_count,
                            "success": True,
                        }
                    )

                    min_time = min(min_time, duration)
                    max_time = max(max_time, duration)
                    total_successful += duration
                    success_count += 1
                    total_rows += row_count
                except Exception as exc:
                    iteration_results.append(
                        {
                            "iteration": iteration + 1,
                            "time": None,
                            "rows": 0,
                            "success": False,
                            "error": str(exc),
                        }
                    )
                    last_error = str(exc)

            avg_time = total_successful / success_count if success_count else None
            results["query_results"].append(
                {
                    "query_id": query_id,
                    "iterations": iteration_results,
                    "min_time": min_time if success_count else None,
                    "max_time": max_time if success_count else None,
                    "avg_time": avg_time,
                    "total_rows": total_rows,
                    "success": success_count == iterations,
                    "error": last_error,
                }
            )

        return results


__all__ = ["CoffeeShopBenchmark"]
