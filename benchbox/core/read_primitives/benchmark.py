# Copyright 2026 Joe Harris / BenchBox Project

# This implementation is derived from TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.benchmark_mixins import DataGenerationMixin
from benchbox.core.query_catalog_base import QuerySkippedError, TranslatableQueryMixin

if TYPE_CHECKING:
    from benchbox.core.dataframe.query import QueryRegistry
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

from benchbox.core.connection import DatabaseConnection
from benchbox.core.contracts import run_categorized_query_benchmark
from benchbox.core.read_primitives.generator import ReadPrimitivesDataGenerator
from benchbox.core.read_primitives.queries import ReadPrimitivesQueryManager
from benchbox.core.read_primitives.schema import TABLES, get_all_create_table_sql
from benchbox.core.utils.tuning import extract_constraint_flags
from benchbox.utils.file_format import get_delimiter_for_file, is_tpc_format
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path


class ReadPrimitivesBenchmark(GeneratorOutputDirMixin, TranslatableQueryMixin, DataGenerationMixin, BaseBenchmark):
    _source_dialect = "netezza"
    _native_source_passthrough = False

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        **config: Any,
    ):
        config = dict(config)
        quiet = config.pop("quiet", False)

        super().__init__(scale_factor, quiet=quiet, **config)

        self._name = "Read Primitives Benchmark"
        self._version = "1.0"
        self._description = (
            "Read Primitives benchmark - Testing fundamental database read operations using TPC-H schema"
        )

        if output_dir is None:
            output_dir = get_benchmark_runs_datagen_path("tpch", scale_factor)

        self.output_dir = output_dir

        self.query_manager: ReadPrimitivesQueryManager = ReadPrimitivesQueryManager()
        self.data_generator = ReadPrimitivesDataGenerator(scale_factor, self.output_dir, **config)

        self.tables = {}

    DATA_SOURCE_BENCHMARK = "tpch"

    def supports_dataframe_mode(self) -> bool:
        return True

    def _get_table_schema(self) -> dict[str, dict]:
        return TABLES

    def get_query(self, query_id: Union[int, str], *, params: Optional[dict[str, Any]] = None) -> str:
        if params is not None:
            raise ValueError("Read Primitives queries are static and don't accept parameters")
        return self.query_manager.get_query(str(query_id))

    def _get_query_safe(self, query_id: str) -> str:
        try:
            return self.get_query(query_id)
        except ValueError:
            return f"-- Unknown query: {query_id}\nSELECT 'unknown_query' AS result;"

    def get_queries(self, dialect: Optional[str] = None) -> dict[str, str]:
        base_queries = self.query_manager.get_all_queries()

        if dialect:
            translated_queries = {}
            for query_id in base_queries.keys():
                is_variant = self.query_manager.has_variant(query_id, dialect)
                try:
                    query_sql = self.query_manager.get_query(query_id, dialect=dialect)
                except QuerySkippedError:
                    continue
                if is_variant:
                    translated_queries[query_id] = query_sql
                else:
                    translated_queries[query_id] = self.translate_query_text(query_sql, dialect)
            return translated_queries

        return base_queries

    def get_all_queries(self) -> dict[str, str]:
        return self.query_manager.get_all_queries()

    def get_queries_by_category(self, category: str) -> dict[str, str]:
        return self.query_manager.get_queries_by_category(category)

    def get_query_categories(self) -> list[str]:
        return self.query_manager.get_query_categories()

    def execute_query(
        self,
        query_id: Union[int, str],
        connection: Any,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        sql = self.get_query(query_id)

        if hasattr(connection, "execute"):
            cursor = connection.execute(sql)
            return cursor.fetchall()
        elif hasattr(connection, "cursor"):
            cursor = connection.cursor()
            cursor.execute(sql)
            return cursor.fetchall()
        else:
            raise ValueError("Unsupported connection type")

    def get_schema(self, dialect: str = "standard") -> dict[str, dict]:
        return TABLES

    def get_create_tables_sql(
        self,
        dialect: str = "standard",
        tuning_config: Optional["UnifiedTuningConfiguration"] = None,
    ) -> str:
        try:
            enable_primary_keys, enable_foreign_keys = extract_constraint_flags(tuning_config)
        except AttributeError as e:
            self.logger.error(
                f"Failed to extract constraint settings from tuning_config: {e}. "
                f"tuning_config type: {type(tuning_config)}"
            )
            raise RuntimeError(
                f"Invalid tuning_config object (missing primary_keys or foreign_keys attributes): {e}"
            ) from e

        return get_all_create_table_sql(
            dialect=dialect,
            enable_primary_keys=enable_primary_keys,
            enable_foreign_keys=enable_foreign_keys,
        )

    def run_benchmark(
        self,
        connection: Any,
        queries: Optional[list[str]] = None,
        iterations: int = 1,
        categories: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        import time

        if categories:
            queries = []
            for category in categories:
                category_queries = self.get_queries_by_category(category)
                queries.extend(category_queries.keys())
        elif queries is None:
            queries = list(self.query_manager.get_all_queries().keys())

        try:
            from benchbox.core.read_primitives.variant_contracts import summarize_variant_comparability

            comparability = summarize_variant_comparability()
        except Exception:
            comparability = {"comparable": None, "issue_count": None}

        results: dict[str, Any] = {
            "benchmark": "Read Primitives",
            "scale_factor": self.scale_factor,
            "iterations": iterations,
            "categories": categories,
            "variant_comparability": comparability,
            "queries": {},
        }

        for query_id in queries:
            try:
                category = self.query_manager.get_query_category(query_id)
            except ValueError:
                category = "unknown"

            query_comparability = (comparability.get("per_query") or {}).get(str(query_id), {})
            query_results = {
                "query_id": query_id,
                "category": category,
                "comparability": {
                    "variant_dialects": query_comparability.get("variant_dialects", []),
                    "issue_count": query_comparability.get("issue_count", 0),
                },
                "iterations": [],
                "avg_time": 0,
                "min_time": float("inf"),
                "max_time": 0,
                "sql_text": self._get_query_safe(query_id),
            }

            for i in range(iterations):
                start_time = time.perf_counter()
                try:
                    result = self.execute_query(query_id, connection)
                    end_time = time.perf_counter()
                    execution_time = end_time - start_time

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

    def run_category_benchmark(self, connection: Any, category: str, iterations: int = 1) -> dict[str, Any]:
        return run_categorized_query_benchmark(self, connection, category, iterations)

    def get_benchmark_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "name": self._name,
            "version": self._version,
            "description": self._description,
            "scale_factor": self.scale_factor,
            "total_queries": len(self.query_manager.get_all_queries()),
            "categories": self.get_query_categories(),
            "tables": list(TABLES.keys()),
            "schema": "TPC-H",
        }
        try:
            from benchbox.core.read_primitives.variant_contracts import summarize_variant_comparability

            info["variant_comparability"] = summarize_variant_comparability()
        except Exception:
            info["variant_comparability"] = {"comparable": None, "issue_count": None}
        return info

    def _check_compatible_tpch_database(self, connection: DatabaseConnection) -> bool:
        import logging

        logger = logging.getLogger(__name__)

        try:
            required_tables = [
                "region",
                "nation",
                "customer",
                "supplier",
                "part",
                "partsupp",
                "orders",
                "lineitem",
            ]

            for table_name in required_tables:
                try:
                    result = connection.execute(f"SELECT COUNT(*) FROM {table_name} LIMIT 1")
                    if not result:
                        logger.debug(f"Table {table_name} does not exist or is empty")
                        return False
                except Exception:
                    logger.debug(f"Table {table_name} does not exist")
                    return False

            try:
                result = connection.execute("SELECT COUNT(*) FROM lineitem")
                lineitem_count = result[0][0] if result else 0

                expected_min = int(6000000 * self.scale_factor * 0.8)
                expected_max = int(6000000 * self.scale_factor * 1.2)

                if not (expected_min <= lineitem_count <= expected_max):
                    logger.debug(
                        f"Lineitem row count {lineitem_count:,} not compatible with scale factor {self.scale_factor} (expected {expected_min:,}-{expected_max:,})"
                    )
                    return False

                logger.info(
                    f"Found compatible TPC-H database with {lineitem_count:,} lineitem rows (scale factor {self.scale_factor})"
                )
                return True

            except Exception as e:
                logger.debug(f"Could not validate row counts: {e}")
                return False

        except Exception as e:
            logger.debug(f"Database compatibility check failed: {e}")
            return False

    def _load_data(self, connection: DatabaseConnection) -> None:
        import logging

        logger = logging.getLogger(__name__)

        if self._check_compatible_tpch_database(connection):
            logger.info("Reusing existing compatible TPC-H database for Read Primitives benchmark")
            return

        if not self.tables:
            raise ValueError("No data has been generated. Call generate_data() first.")

        logger.info("Loading Read Primitives data into database...")

        try:
            schema_sql = self.get_create_tables_sql()
            if ";" in schema_sql:
                statements = [stmt.strip() for stmt in schema_sql.split(";") if stmt.strip()]
                for statement in statements:
                    connection.execute(statement)
            else:
                connection.execute(schema_sql)
            connection.commit()
            logger.info("✅ Created Read Primitives database schema")
        except Exception as e:
            logger.error(f"Failed to create database schema: {e}")
            raise

        total_rows = 0
        loaded_tables = 0

        table_order = [
            "region",
            "nation",
            "customer",
            "supplier",
            "part",
            "partsupp",
            "orders",
            "lineitem",
        ]

        for table_name in table_order:
            if table_name not in self.tables:
                logger.warning(f"Skipping {table_name} - no data file found")
                continue

            data_file = Path(self.tables[table_name])
            tbl_file = data_file.with_suffix(".tbl")
            if tbl_file.exists():
                data_file = tbl_file
            elif not data_file.exists():
                logger.warning(f"Skipping {table_name} - data file does not exist: {data_file}")
                continue

            try:
                logger.info(f"Loading data for {table_name.upper()}...")
                rows_loaded = self._load_table_data(connection, table_name, data_file)

                total_rows += rows_loaded
                loaded_tables += 1
                logger.info(f"✅ Loaded {rows_loaded:,} rows into {table_name.upper()}")

            except Exception as e:
                logger.error(f"Failed to load data for {table_name}: {e}")
                raise

        try:
            connection.commit()
            logger.info(f"✅ Successfully loaded {total_rows:,} total rows across {loaded_tables} tables")
        except Exception as e:
            logger.error(f"Failed to commit data loading transaction: {e}")
            raise

    def _load_table_data(self, connection: DatabaseConnection, table_name: str, data_file: Path) -> int:
        import csv

        table_schema = TABLES[table_name]
        num_columns = len(table_schema["columns"])

        placeholders = ", ".join(["?" for _ in range(num_columns)])
        insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders})"

        rows_loaded = 0

        delimiter = get_delimiter_for_file(data_file)

        with open(data_file, newline="", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=delimiter)

            for row in reader:
                if is_tpc_format(data_file) and row and row[-1] == "":
                    row = row[:-1]

                if len(row) != num_columns:
                    continue

                connection.execute(insert_sql, row)
                rows_loaded += 1

        return rows_loaded

    def _get_default_benchmark_type(self) -> str:
        return "mixed"

    def _validate_database_configuration_compatibility(self, other_config: dict) -> bool:
        benchmark_type = other_config.get("benchmark_type", "").lower()
        if benchmark_type not in ["tpch", "tpc-h", "primitives", "read_primitives"]:
            return False

        other_scale = other_config.get("scale_factor")
        if other_scale != self.scale_factor:
            return False

        return True

    def get_dataframe_queries(self) -> "QueryRegistry":
        from benchbox.core.read_primitives.dataframe_queries import get_dataframe_queries

        return get_dataframe_queries()

    def get_dataframe_skip_queries(self) -> list[str]:
        from benchbox.core.read_primitives.dataframe_queries import get_skip_for_dataframe

        return get_skip_for_dataframe()

    def get_expression_family_skip_queries(self) -> list[str]:
        from benchbox.core.read_primitives.dataframe_queries import get_skip_for_expression_family

        return get_skip_for_expression_family()

    def get_platform_skip_queries(self, platform_name: str) -> list[str]:
        name = platform_name.lower()
        if name == "lakesail":
            return [
                "empty_build_join",
                "approx_top_k_lineitem",
                "window_moving_frame",
                "json_extract_nested",
                "json_aggregates",
                "fulltext_simple_search",
                "fulltext_boolean_search",
                "fulltext_phrase_search",
                "approx_quantiles_array",
                "optimizer_scalar_subquery_flattening",
                "groupby_all_simple",
                "groupby_all_complex",
                "orderby_all_simple",
                "orderby_all_desc",
                "list_transform",
                "list_filter",
                "list_reduce",
                "asof_join_basic",
                "pivot_basic",
            ]
        if name == "questdb":
            from benchbox.sql_compat.rules.execution_filter.questdb_read_primitives import (
                QUESTDB_READ_PRIMITIVES_SKIPS,
            )

            return list(QUESTDB_READ_PRIMITIVES_SKIPS)
        return []

    def get_df_platform_skip_queries(self, platform_name: str) -> list[str]:
        name = platform_name.lower()
        if name == "datafusion":
            from benchbox.core.read_primitives.dataframe_queries import get_skip_for_datafusion

            return get_skip_for_datafusion()
        if name == "polars":
            from benchbox.core.read_primitives.dataframe_queries import get_skip_for_polars

            return get_skip_for_polars()
        if name == "pyspark":
            from benchbox.core.read_primitives.dataframe_queries import get_skip_for_pyspark

            return get_skip_for_pyspark()
        return []
