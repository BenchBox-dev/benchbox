# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import csv
import inspect
import logging
from pathlib import Path
from typing import Any

from benchbox.core.benchmark_mixins import duckdb_insert_from_csv
from benchbox.core.connection import DatabaseConnection
from benchbox.utils.clock import elapsed_seconds, mono_time

logger = logging.getLogger(__name__)


class SimpleBenchmarkMixin:
    _benchmark_label: str
    _table_load_order: list[str]
    _REQUIRED_ATTRIBUTES = ("_benchmark_label", "_table_load_order")
    _REQUIRED_METHODS = ("get_query", "execute_query", "get_create_tables_sql", "_get_table_schema")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__name__.endswith(("Mixin", "Base")):
            return
        missing_attributes = [
            attribute_name
            for attribute_name in SimpleBenchmarkMixin._REQUIRED_ATTRIBUTES
            if not hasattr(cls, attribute_name)
        ]
        missing_methods = [
            method_name
            for method_name in SimpleBenchmarkMixin._REQUIRED_METHODS
            if not callable(member := inspect.getattr_static(cls, method_name, None))
            or getattr(member, "__isabstractmethod__", False)
        ]
        if missing_attributes or missing_methods:
            missing = ", ".join([*missing_attributes, *missing_methods])
            raise TypeError(
                f"{cls.__name__} uses SimpleBenchmarkMixin but is missing required contract members: {missing}"
            )

    def run_benchmark(self, connection: Any, queries: list[str] | None = None, iterations: int = 1) -> dict[str, Any]:
        if queries is None:
            queries = list(self.query_manager.get_all_queries().keys())

        results: dict[str, Any] = {
            "benchmark": self._benchmark_label,
            "scale_factor": self.scale_factor,
            "iterations": iterations,
            "queries": {},
        }

        for query_id in queries:
            query_results: dict[str, Any] = {
                "query_id": query_id,
                "iterations": [],
                "avg_time": 0,
                "min_time": float("inf"),
                "max_time": 0,
                "sql_text": self.get_query(query_id),
            }

            for i in range(iterations):
                start_time = mono_time()
                try:
                    result = self.execute_query(query_id, connection)
                    execution_time = elapsed_seconds(start_time)

                    query_results["iterations"].append(
                        {
                            "iteration": i + 1,
                            "time": execution_time,
                            "rows": len(result) if result else 0,
                            "success": True,
                        }
                    )

                    query_results["min_time"] = min(query_results["min_time"], execution_time)
                    query_results["max_time"] = max(query_results["max_time"], execution_time)

                except Exception as e:
                    query_results["iterations"].append(
                        {
                            "iteration": i + 1,
                            "time": 0,
                            "error": str(e),
                            "success": False,
                        }
                    )

            iterations_list: list[dict[str, Any]] = query_results["iterations"]
            successful_iterations = [iter_result for iter_result in iterations_list if iter_result["success"]]
            if successful_iterations:
                successful_times = [iter_result["time"] for iter_result in successful_iterations]
                successful_rows = [iter_result.get("rows", 0) for iter_result in successful_iterations]
                query_results["avg_time"] = sum(successful_times) / len(successful_times)
                query_results["rows_returned"] = (
                    int(sum(successful_rows) / len(successful_rows)) if successful_rows else 0
                )

            results["queries"][query_id] = query_results

        return results

    def _load_data(self, connection: DatabaseConnection) -> None:
        label = self._benchmark_label

        if not self.tables:
            raise ValueError("No data has been generated. Call generate_data() first.")

        logger.info("Loading %s data into database...", label)

        try:
            schema_sql = self.get_create_tables_sql()
            if ";" in schema_sql:
                statements = [stmt.strip() for stmt in schema_sql.split(";") if stmt.strip()]
                for statement in statements:
                    connection.execute(statement)
            else:
                connection.execute(schema_sql)
            connection.commit()
            logger.info("Created %s database schema", label)
        except Exception as e:
            logger.error("Failed to create database schema: %s", e)
            raise

        total_rows = 0
        loaded_tables = 0

        for table_name in self._table_load_order:
            if table_name not in self.tables:
                logger.warning("Skipping %s - no data file found", table_name)
                continue

            data_file = Path(self.tables[table_name])
            if not data_file.exists():
                logger.warning("Skipping %s - data file does not exist: %s", table_name, data_file)
                continue

            try:
                logger.info("Loading data for %s...", table_name.upper())
                rows_loaded = self._load_table_data(connection, table_name, data_file)

                total_rows += rows_loaded
                loaded_tables += 1
                logger.info("Loaded %s rows into %s", f"{rows_loaded:,}", table_name.upper())

            except Exception as e:
                logger.error("Failed to load data for %s: %s", table_name, e)
                raise

        try:
            connection.commit()
            logger.info("Successfully loaded %s total rows across %d tables", f"{total_rows:,}", loaded_tables)
        except Exception as e:
            logger.error("Failed to commit data loading transaction: %s", e)
            raise

    def _load_table_data(self, connection: DatabaseConnection, table_name: str, data_file: Path) -> int:
        table_schema = self._get_table_schema()[table_name]
        num_columns = len(table_schema["columns"])

        copied = duckdb_insert_from_csv(connection, table_name, data_file, num_columns)
        if copied is not None:
            return copied

        placeholders = ", ".join(["?" for _ in range(num_columns)])
        insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders})"

        rows_loaded = 0

        with open(data_file, newline="", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="|")

            for row in reader:
                if len(row) != num_columns:
                    continue

                connection.execute(insert_sql, row)
                rows_loaded += 1

        return rows_loaded
