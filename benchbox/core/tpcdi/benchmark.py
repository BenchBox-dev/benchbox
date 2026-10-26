# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import csv
import importlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Optional,
    Union,
    cast,
)

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.dataframe.maintenance_interface import get_maintenance_operations_for_platform
from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE
from benchbox.core.tpcdi.config import TPCDIConfig
from benchbox.core.tpcdi.etl import (
    DataFrameETLBackend,
    ETLResult,
    SQLETLBackend,
    TPCDIETLBackend,
    TPCDIETLPipeline,
)
from benchbox.core.tpcdi.etl.customer_mgmt_processor import CustomerManagementProcessor
from benchbox.core.tpcdi.etl.data_quality_monitor import DataQualityMonitor
from benchbox.core.tpcdi.etl.error_recovery import ErrorRecoveryManager
from benchbox.core.tpcdi.etl.finwire_processor import FinWireParser, FinWireProcessor
from benchbox.core.tpcdi.etl.incremental_loader import IncrementalDataLoader
from benchbox.core.tpcdi.etl.results import ETLPhaseResult
from benchbox.core.tpcdi.etl.scd_processor import EnhancedSCDType2Processor
from benchbox.core.tpcdi.generator import TPCDIDataGenerator
from benchbox.core.tpcdi.loader import TPCDIDataLoader
from benchbox.core.tpcdi.metrics import BenchmarkMetrics, BenchmarkReport, TPCDIMetrics
from benchbox.core.tpcdi.queries import TPCDIQueryManager
from benchbox.core.tpcdi.schema import (
    TABLES,
    TPCDISchemaManager,
    get_all_create_table_sql,
)
from benchbox.core.tpcdi.validation import DataQualityResult, TPCDIValidator
from benchbox.sql_compat.rules.execution_filter.lakesail_tpcdi import LAKESAIL_TPCDI_SKIPS
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.printing import emit

DATAFRAME_ETL_TABLE_DIR = "dataframe-etl-tables"

TPCDI_FLAG_COLUMNS = ["IsCurrent", "TT_IS_SELL", "HolidayFlag"]
BOOLEAN_FLAG_STYLES = {
    "postgres": "is",
    "postgresql": "is",
    "datafusion": "is",
    "databricks": "is",
    "bigquery": "equals",
}


class TPCDITransformationError(RuntimeError):
    def __init__(self, failures: list[tuple[str, BaseException]], message: str | None = None):
        self.failed_files = [file_path for file_path, _ in failures]
        details = "; ".join(f"{file_path}: {error}" for file_path, error in failures)
        super().__init__(message or f"TPC-DI transformation failed for {len(failures)} file(s): {details}")


class TPCDIBenchmark(GeneratorOutputDirMixin, BaseBenchmark):
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        enable_parallel: bool = False,
        max_workers: Optional[int] = None,
        config: Optional[TPCDIConfig] = None,
        **kwargs: Any,
    ):
        kwargs = dict(kwargs)
        quiet = kwargs.pop("quiet", False)

        super().__init__(scale_factor, quiet=quiet, **kwargs)

        self._name = "TPC-DI Benchmark"
        self._version = "1.0"
        self._description = "TPC-DI (Data Integration) Benchmark - Tests ETL and data integration performance"

        self.csv_normalize_booleans: bool = True

        if config is None:
            self.config = TPCDIConfig(
                scale_factor=scale_factor,
                output_dir=Path(output_dir) if isinstance(output_dir, str) else output_dir,
                enable_parallel=enable_parallel,
                max_workers=max_workers,
            )
        else:
            self.config = config

        self.output_dir = self.config.output_dir
        self.config.create_directories()
        assert self.output_dir is not None

        self.enable_parallel = self.config.enable_parallel
        self.max_workers = self.config.max_workers
        self.scale_factor = self.config.scale_factor

        self.query_manager = TPCDIQueryManager()
        generator_kwargs = dict(kwargs)
        generator_kwargs.setdefault("generation_seed", getattr(self.config, "generation_seed", 42))
        self.data_generator = TPCDIDataGenerator(self.config.scale_factor, self.output_dir, **generator_kwargs)

        self.schema_manager = TPCDISchemaManager()
        self.validator = None
        self.etl_pipeline = None
        self.data_loader = None
        self.metrics_calculator = TPCDIMetrics(self.config.scale_factor)

        self.finwire_processor = None
        self.customer_mgmt_processor = None
        self.scd_processor = None
        self.incremental_loader = None
        self.data_quality_monitor = None
        self.error_recovery_manager = None

        self.etl_engine = None
        self.source_generators: dict[str, Any] = {}
        self.etl_stats: dict[str, Any] = {}
        self.batch_status: dict[str, Any] = {}

        self._initialize_etl_components()

        self.tables: dict[str, Any] = {}

    def _sync_output_dir_to_generators(self, path: Any) -> None:
        super()._sync_output_dir_to_generators(path)
        config = getattr(self, "config", None)
        if config is None:
            return
        config.output_dir = path
        self.source_dir = config.source_dir
        self.staging_dir = config.staging_dir
        self.warehouse_dir = config.warehouse_dir

    def generate_data(
        self,
        tables: Optional[list[str]] = None,
        output_format: str = "csv",
        seed: Optional[int] = None,
    ) -> list[Union[str, Path]]:
        if output_format != "csv":
            raise ValueError(f"Unsupported output format: {output_format}")

        original_generation_seed = getattr(self.data_generator, "generation_seed", None)
        if seed is not None:
            self.data_generator.generation_seed = int(seed)

        if tables is None:
            tables = list(TABLES.keys())

        invalid_tables = set(tables) - set(TABLES.keys())
        if invalid_tables:
            raise ValueError(f"Invalid table names: {invalid_tables}")

        try:
            self.tables = self.data_generator.generate_data(tables)
            return list(self.tables.values())
        finally:
            self.data_generator.generation_seed = original_generation_seed

    def get_query(
        self,
        query_id: Union[int, str],
        params: Optional[dict[str, Any]] = None,
        dialect: Optional[str] = None,
    ) -> str:
        if isinstance(query_id, int) or (isinstance(query_id, str) and query_id.isdigit()):
            numeric_id = int(query_id)
            if numeric_id <= 12:
                query_id = f"VQ{numeric_id}"
            else:
                query_id = f"AQ{numeric_id - 12}"

        query_id = str(query_id)
        query = self.query_manager.get_query(query_id, params, dialect=None)
        if not dialect or dialect == "standard":
            return query

        translated_query = self.translate_query_text(query, dialect)
        return self._apply_query_source_variant(query_id, translated_query, dialect, params=params)

    def get_queries(self, dialect: Optional[str] = None) -> dict[str, str]:
        all_query_ids = list(self.query_manager._all_queries.keys())

        queries = {}
        for query_id in all_query_ids:
            queries[query_id] = self.query_manager.get_query(query_id, params=None, dialect=None)

        if dialect:
            translated_queries = {}
            for query_id, query_sql in queries.items():
                translated_queries[query_id] = self.translate_query_text(query_sql, dialect)

            d = dialect.lower()
            translated_queries = {
                query_id: self._apply_query_source_variant(query_id, query_sql, dialect)
                for query_id, query_sql in translated_queries.items()
            }

            if d in {"postgres", "postgresql"}:
                translated_queries = {
                    query_id: self._apply_postgres_query_overrides(query_id, query_sql)
                    for query_id, query_sql in translated_queries.items()
                }

            return translated_queries

        return queries

    def _apply_query_source_variant(
        self,
        query_id: str,
        query_sql: str,
        dialect: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> str:
        importlib.import_module("benchbox.sql_compat.rules.query_source.tpcdi_variants")
        from benchbox.sql_compat.actions import CompatAction
        from benchbox.sql_compat.context import CompatibilityContext, Phase
        from benchbox.sql_compat.registry import REGISTRY
        from benchbox.sql_compat.rules.query_source.tpcdi_variants import (
            BIGQUERY_EQ7_SQL,
            CLICKHOUSE_AQ6_SQL,
            CLICKHOUSE_AQ7_SQL,
            CLICKHOUSE_AQ8_SQL,
            CLICKHOUSE_AQ10_SQL,
            CLICKHOUSE_EQ7_SQL,
            DATABRICKS_EQ7_SQL,
            DATAFUSION_AQ9_SQL,
            DATAFUSION_EQ7_SQL,
            DATAFUSION_VQ6_SQL,
            DORIS_EQ7_SQL,
            SNOWFLAKE_EQ7_SQL,
            STARROCKS_EQ7_SQL,
        )

        variants: dict[str, dict[str, str]] = {
            "bigquery": {
                "EQ7": BIGQUERY_EQ7_SQL,
            },
            "databricks": {
                "EQ7": DATABRICKS_EQ7_SQL,
            },
            "snowflake": {
                "EQ7": SNOWFLAKE_EQ7_SQL,
            },
            "clickhouse": {
                "AQ6": CLICKHOUSE_AQ6_SQL,
                "AQ7": CLICKHOUSE_AQ7_SQL,
                "AQ8": CLICKHOUSE_AQ8_SQL,
                "AQ10": CLICKHOUSE_AQ10_SQL,
                "EQ7": CLICKHOUSE_EQ7_SQL,
            },
            "datafusion": {
                "AQ9": DATAFUSION_AQ9_SQL,
                "EQ7": DATAFUSION_EQ7_SQL,
                "VQ6": DATAFUSION_VQ6_SQL,
            },
            "starrocks": {"EQ7": STARROCKS_EQ7_SQL},
            "doris": {"EQ7": DORIS_EQ7_SQL},
        }
        dialect_lower = dialect.lower()
        for platform, platform_variants in variants.items():
            if platform not in dialect_lower or query_id not in platform_variants:
                continue

            ctx = CompatibilityContext(
                platform=platform,
                platform_version=None,
                benchmark="tpcdi",
                query_id=query_id,
                phase=Phase.QUERY_SOURCE,
                mode="sql",
                dialect=dialect,
            )
            registry_decision = REGISTRY.resolve(ctx)
            if registry_decision is not None:
                if registry_decision.action is not CompatAction.SELECT_VARIANT:
                    return query_sql
                variant_sql = registry_decision.payload.variant_sql
            else:
                variant_sql = platform_variants[query_id]

            if platform == "clickhouse" and params is not None and query_id in {"AQ7", "AQ8", "AQ10"}:
                from benchbox.sql_compat.rules.query_source.tpcdi_variants import build_clickhouse_metric_query

                return build_clickhouse_metric_query(query_id, params)

            if query_id == "AQ6":
                query_params = self.query_manager._generate_default_params(query_id)
                if params:
                    query_params.update(params)
                return variant_sql.format(**query_params)

            if (
                platform in ("bigquery", "clickhouse", "databricks", "snowflake")
                and query_id == "EQ7"
                and params is not None
            ):
                query_params = self.query_manager.etl_queries._generate_default_params(query_id)
                query_params.update(params)
                default_params = self.query_manager.etl_queries._generate_default_params(query_id)
                for parameter in (
                    "excellent_quality_threshold",
                    "good_quality_threshold",
                    "acceptable_quality_threshold",
                ):
                    variant_sql = variant_sql.replace(
                        f") >= {default_params[parameter]} THEN",
                        f") >= {query_params[parameter]} THEN",
                    )

            return variant_sql

        return query_sql

    def _apply_postgres_query_overrides(self, query_id: str, query_sql: str) -> str:
        if query_id == "A5":
            return query_sql.replace(
                "HAVING customer_count > 10",
                "HAVING COUNT(DISTINCT c.SK_CustomerID) > 10",
            )
        if query_id == "AQ10":
            return query_sql.replace(
                "ORDER BY CASE risk_profile WHEN 'HIGH RISK' THEN 1 WHEN 'CONCENTRATION RISK' THEN 2 "
                "WHEN 'WASH SALE RISK' THEN 3 WHEN 'DAY TRADING RISK' THEN 4 ELSE 5 END, total_trade_value DESC",
                "ORDER BY 15, total_trade_value DESC",
            )
        if query_id == "EQ7":
            from benchbox.sql_compat.rules.query_source.tpcdi_variants import STARROCKS_EQ7_SQL
            from benchbox.utils.dialect_utils import apply_rewrite

            return apply_rewrite(
                "flag_comparisons_to_boolean",
                STARROCKS_EQ7_SQL,
                "postgres",
                columns=TPCDI_FLAG_COLUMNS,
                use_is=True,
            )
        return query_sql

    def get_platform_skip_queries(self, platform_name: str) -> list[str]:
        if platform_name.lower() == "lakesail":
            return list(LAKESAIL_TPCDI_SKIPS)
        return []

    def translate_query_text(self, query_text: str, target_dialect: str) -> str:
        from benchbox.utils.dialect_utils import translate_sql_query

        rewrites = []
        flag_style = BOOLEAN_FLAG_STYLES.get(target_dialect.lower())
        if flag_style is not None:
            rewrites.append(
                (
                    "flag_comparisons_to_boolean",
                    {"columns": TPCDI_FLAG_COLUMNS, "use_is": flag_style == "is"},
                )
            )
        return translate_sql_query(
            query=query_text,
            target_dialect=target_dialect,
            source_dialect="netezza",
            rewrites=rewrites,
        )

    def get_all_queries(self) -> dict[str, str]:
        return self.query_manager.get_all_queries()

    def execute_query(
        self,
        query_id: Union[int, str],
        connection: Any,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        sql = self.get_query(query_id, params)

        if hasattr(connection, "execute"):
            cursor = connection.execute(sql)
            return cursor.fetchall()
        elif hasattr(connection, "cursor"):
            cursor = connection.cursor()
            cursor.execute(sql)
            return cursor.fetchall()
        else:
            raise ValueError("Unsupported connection type")

    def get_schema(self, dialect: str = "standard") -> dict[str, dict[str, Any]]:
        return TABLES

    def get_create_tables_sql(
        self,
        dialect: str = "standard",
        tuning_config: Optional["UnifiedTuningConfiguration"] = None,
    ) -> str:
        enable_primary_keys = tuning_config.primary_keys.enabled if tuning_config else False
        enable_foreign_keys = tuning_config.foreign_keys.enabled if tuning_config else False

        return get_all_create_table_sql(dialect, enable_primary_keys, enable_foreign_keys)

    def load_data_to_database(self, connection: Any, tables: Optional[list[str]] = None) -> None:
        if not self.tables:
            raise ValueError("No data generated. Call generate_data() first.")

        if tables is None:
            tables = list(self.tables.keys())

        self._execute_schema_sql(connection)

        for table_name in tables:
            if table_name not in self.tables:
                continue
            self._load_single_table(connection, table_name)

        if hasattr(connection, "commit"):
            connection.commit()

    def _execute_schema_sql(self, connection: Any) -> None:
        schema_sql = self.get_create_tables_sql()
        if hasattr(connection, "executescript"):
            connection.executescript(schema_sql)
        else:
            cursor = connection.cursor()
            for statement in schema_sql.split(";"):
                if statement.strip():
                    cursor.execute(statement)

    def _load_single_table(self, connection: Any, table_name: str) -> None:
        _path = self.tables[table_name]
        table_schema = TABLES[table_name]

        with open(_path, encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="|")

            columns = [cast(str, col["name"]) for col in cast(list[dict[str, Any]], table_schema["columns"])]
            placeholders = ",".join(["?" for _ in columns])
            insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders})"

            col_types = [cast(str, col["type"]).upper() for col in cast(list[dict[str, Any]], table_schema["columns"])]
            numeric_prefixes = ("INT", "BIGINT", "SMALLINT", "TINYINT", "DECIMAL", "DOUBLE", "FLOAT", "REAL")

            def _convert_row(values: list[str]) -> list[Any]:
                converted: list[Any] = []
                for idx, val in enumerate(values):
                    if val == "" and col_types[idx].startswith(numeric_prefixes):
                        converted.append(None)
                    else:
                        converted.append(val)
                return converted

            batch_size = 5000
            batch: list[list[Any]] = []

            if hasattr(connection, "executemany"):
                for row in reader:
                    batch.append(_convert_row(row))
                    if len(batch) >= batch_size:
                        connection.executemany(insert_sql, batch)
                        batch = []
                if batch:
                    connection.executemany(insert_sql, batch)
            else:
                cursor = connection.cursor()
                for row in reader:
                    cursor.execute(insert_sql, _convert_row(row))

    def run_benchmark(
        self, connection: Any, queries: Optional[list[str]] = None, iterations: int = 1
    ) -> dict[str, Any]:

        if queries is None:
            queries = list(self.query_manager.get_all_queries().keys())

        results = {
            "benchmark": "TPC-DI",
            "scale_factor": self.scale_factor,
            "iterations": iterations,
            "total_queries": len(queries),
            "query_statistics": self.query_manager.get_query_statistics(),
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
                    end_time = mono_time()
                    execution_time = end_time - start_time

                    cast(list[dict[str, Any]], query_results["iterations"]).append(
                        {
                            "iteration": i + 1,
                            "time": execution_time,
                            "rows": len(result) if result else 0,
                            "success": True,
                        }
                    )

                    query_results["min_time"] = min(cast(float, query_results["min_time"]), execution_time)
                    query_results["max_time"] = max(cast(float, query_results["max_time"]), execution_time)
                except Exception as e:
                    cast(list[dict[str, Any]], query_results["iterations"]).append(
                        {
                            "iteration": i + 1,
                            "time": 0,
                            "error": str(e),
                            "success": False,
                        }
                    )

            successful_iterations = [
                iter_result
                for iter_result in cast(list[dict[str, Any]], query_results["iterations"])
                if iter_result["success"]
            ]
            if successful_iterations:
                successful_times = [iter_result["time"] for iter_result in successful_iterations]
                successful_rows = [iter_result.get("rows", 0) for iter_result in successful_iterations]
                query_results["avg_time"] = sum(successful_times) / len(successful_times)
                query_results["rows_returned"] = (
                    int(sum(successful_rows) / len(successful_rows)) if successful_rows else 0
                )

            results["queries"][query_id] = query_results

        return results

    def supports_dataframe_mode(self) -> bool:
        return True

    def skip_dataframe_data_loading(self) -> bool:
        return True

    def get_dataframe_etl_capabilities(
        self,
        platform_name: str,
        maintenance_ops: Any | None = None,
        execution_engine: str = DEFAULT_EXECUTION_ENGINE,
    ) -> dict[str, Any]:
        if maintenance_ops is None:
            maintenance_ops = get_maintenance_operations_for_platform(platform_name, execution_engine)
        return self._build_dataframe_etl_capabilities(platform_name, maintenance_ops)

    def _build_dataframe_etl_capabilities(self, platform_name: str, maintenance_ops: Any | None) -> dict[str, Any]:
        if maintenance_ops is None:
            return {
                "platform": platform_name,
                "has_maintenance_interface": False,
                "supports_transactions": False,
                "transaction_isolation": "none",
                "supports_incremental_loads": False,
                "supports_scd2": False,
                "contract_status": "missing",
                "notes": "No DataFrame maintenance interface available for platform",
            }

        caps = maintenance_ops.get_capabilities()
        supports_incremental = bool(caps.supports_transactions and caps.supports_insert)
        supports_scd2 = bool(caps.supports_transactions and caps.supports_update and caps.supports_insert)
        return {
            "platform": platform_name,
            "has_maintenance_interface": True,
            "supports_transactions": bool(caps.supports_transactions),
            "transaction_isolation": str(caps.transaction_isolation.value),
            "supports_incremental_loads": supports_incremental,
            "supports_scd2": supports_scd2,
            "contract_status": "ready" if supports_incremental and supports_scd2 else "partial",
            "notes": caps.notes,
        }

    def execute_dataframe_workload(
        self,
        *,
        ctx: Any,
        adapter: Any,
        benchmark_config: Any,
        query_filter: set[str] | None = None,
        monitor: Any | None = None,
        run_options: Any | None = None,
    ) -> list[dict[str, Any]]:
        _ = (monitor, run_options)
        options = getattr(benchmark_config, "options", {}) or {}
        require_transactional_capabilities = bool(options.get("tpcdi_require_transactional_capabilities", True))

        platform_name = str(getattr(adapter, "platform_name", "unknown"))
        execution_engine = getattr(adapter, "collect_engine", DEFAULT_EXECUTION_ENGINE)
        maintenance_ops = get_maintenance_operations_for_platform(platform_name, execution_engine)
        etl_caps = self.get_dataframe_etl_capabilities(platform_name, maintenance_ops=maintenance_ops)

        legacy_override_keys = sorted(str(key) for key in options if str(key).startswith("tpcdi_dataframe_"))
        if legacy_override_keys:
            return [
                {
                    "query_id": "TDI_WORKLOAD",
                    "status": "FAILED",
                    "execution_time_seconds": 0.0,
                    "error": (
                        "TPC-DI DataFrame ETL no longer supports backend overrides. "
                        f"Unsupported option keys: {', '.join(legacy_override_keys)}. "
                        "Execution must use the selected platform connection only."
                    ),
                }
            ]

        results: list[dict[str, Any]] = [
            {
                "query_id": "TDI_CAPABILITIES",
                "status": "SUCCESS" if etl_caps.get("has_maintenance_interface") else "FAILED",
                "execution_time_seconds": 0.0,
                "rows_returned": 1,
                "first_row": etl_caps,
            }
        ]

        if not etl_caps.get("has_maintenance_interface"):
            results.append(
                {
                    "query_id": "TDI_WORKLOAD",
                    "status": "FAILED",
                    "execution_time_seconds": 0.0,
                    "error": "TPC-DI DataFrame ETL requires DataFrame maintenance operations for the selected platform.",
                }
            )
            return results

        if require_transactional_capabilities and (
            not etl_caps.get("supports_transactions")
            or not etl_caps.get("supports_incremental_loads")
            or not etl_caps.get("supports_scd2")
        ):
            results.append(
                {
                    "query_id": "TDI_TRANSACTIONAL_CONTRACT",
                    "status": "FAILED",
                    "execution_time_seconds": 0.0,
                    "error": (
                        "TPC-DI DataFrame ETL requires transactional maintenance capabilities "
                        "(transactions + incremental + SCD2)."
                    ),
                }
            )
            return results

        stage_map = {
            "TDI_HISTORICAL": "historical",
            "TDI_INCREMENTAL": "incremental",
            "TDI_SCD": "scd",
        }

        selected_stage_ids = set(stage_map)
        if query_filter:
            normalized_filter = {q.upper() for q in query_filter}
            selected_stage_ids = {qid for qid in stage_map if qid in normalized_filter}

        if not selected_stage_ids:
            results.append(
                {
                    "query_id": "TDI_WORKLOAD",
                    "status": "SKIPPED",
                    "execution_time_seconds": 0.0,
                    "error": "No ETL stages selected by query filter",
                }
            )
            return results

        if maintenance_ops is None:
            results.append(
                {
                    "query_id": "TDI_WORKLOAD",
                    "status": "FAILED",
                    "execution_time_seconds": 0.0,
                    "error": (
                        "TPC-DI DataFrame ETL requires DataFrame maintenance operations for the selected platform."
                    ),
                }
            )
            return results

        backend = DataFrameETLBackend(
            maintenance_ops=maintenance_ops,
            platform_name=platform_name,
            table_root=Path(self.output_dir) / DATAFRAME_ETL_TABLE_DIR,
        )
        return results + self._execute_etl_stage_queries(
            backend=backend,
            stage_map=stage_map,
            selected_stage_ids=selected_stage_ids,
        )

    def _execute_etl_stage_queries(
        self,
        *,
        backend: TPCDIETLBackend,
        stage_map: dict[str, str],
        selected_stage_ids: set[str],
    ) -> list[dict[str, Any]]:
        stage_results: list[dict[str, Any]] = []
        for query_id in ("TDI_HISTORICAL", "TDI_INCREMENTAL", "TDI_SCD"):
            if query_id not in selected_stage_ids:
                continue

            batch_type = stage_map[query_id]
            start = mono_time()
            try:
                result = self.run_etl_pipeline(backend=backend, batch_type=batch_type, validate_data=True)
                duration = float(result.get("total_duration", elapsed_seconds(start)))
                records_processed = int(result.get("phases", {}).get("transform", {}).get("records_processed", 0))
                records_loaded = int(result.get("phases", {}).get("load", {}).get("records_loaded", 0))
                quality_score = float(result.get("validation_results", {}).get("data_quality_score", 0.0))

                stage_results.append(
                    {
                        "query_id": query_id,
                        "status": "SUCCESS" if result.get("success") else "FAILED",
                        "execution_time_seconds": duration,
                        "records_processed": records_processed,
                        "records_loaded": records_loaded,
                        "rows_returned": records_loaded,
                        "first_row": {
                            "batch_type": batch_type,
                            "records_processed": records_processed,
                            "records_loaded": records_loaded,
                            "data_quality_score": quality_score,
                        },
                    }
                )
            except Exception as exc:
                stage_results.append(
                    {
                        "query_id": query_id,
                        "status": "FAILED",
                        "execution_time_seconds": elapsed_seconds(start),
                        "error": str(exc),
                    }
                )
        return stage_results

    def run_validation_queries(self, connection: Any, iterations: int = 1) -> dict[str, Any]:
        validation_query_ids = list(self.query_manager.get_validation_queries().keys())
        return self.run_benchmark(connection, validation_query_ids, iterations)

    def run_analytical_queries(self, connection: Any, iterations: int = 1) -> dict[str, Any]:
        analytical_query_ids = list(self.query_manager.get_analytical_queries().keys())
        return self.run_benchmark(connection, analytical_query_ids, iterations)

    def run_etl_validation_queries(self, connection: Any, iterations: int = 1) -> dict[str, Any]:
        etl_query_ids = list(self.query_manager.get_etl_queries().keys())
        return self.run_benchmark(connection, etl_query_ids, iterations)

    def run_queries_by_category(self, connection: Any, category: str, iterations: int = 1) -> dict[str, Any]:
        category_query_ids = self.query_manager.get_queries_by_category(category)
        if not category_query_ids:
            raise ValueError(f"No queries found for category: {category}")
        return self.run_benchmark(connection, category_query_ids, iterations)

    def get_query_execution_plan(self) -> list[tuple[str, str, list[str]]]:
        return self.query_manager.get_execution_plan()

    def _initialize_etl_components(self) -> None:
        if self.source_dir:
            self.source_dir.mkdir(parents=True, exist_ok=True)
        if self.staging_dir:
            self.staging_dir.mkdir(parents=True, exist_ok=True)
        if self.warehouse_dir:
            self.warehouse_dir.mkdir(parents=True, exist_ok=True)

        self.source_generators = {
            "csv": self._generate_csv_sources,
            "xml": self._generate_xml_sources,
            "fixed_width": self._generate_fixed_width_sources,
            "json": self._generate_json_sources,
        }

        self.etl_stats = {"batches_processed": 0, "errors": [], "processing_time": 0}

        self.batch_status = {
            "historical": {"status": "pending"},
            "incremental": {"status": "pending"},
            "scd": {"status": "pending"},
        }

    def generate_source_data(
        self,
        formats: Optional[list[str]] = None,
        batch_types: Optional[list[str]] = None,
    ) -> dict[str, list[str]]:

        if formats is None:
            formats = ["csv", "xml", "fixed_width", "json"]
        if batch_types is None:
            batch_types = ["historical", "incremental", "scd"]

        generated_files = {}

        for format_type in formats:
            if format_type in self.source_generators:
                generated_files[format_type] = self.source_generators[format_type](batch_types)
            else:
                raise ValueError(f"Unsupported format: {format_type}")

        return generated_files

    def _generate_csv_sources(self, batch_types: list[str]) -> list[str]:
        csv_files = []

        for batch_type in batch_types:
            batch_dir = self.source_dir / "csv" / batch_type
            batch_dir.mkdir(parents=True, exist_ok=True)

            customer_file = batch_dir / f"customers_{batch_type}.csv"

            num_records = int(1000 * self.scale_factor)
            if batch_type == "incremental":
                num_records = int(num_records * 0.1)
            elif batch_type == "scd":
                num_records = int(num_records * 0.05)

            sk_offset = {"historical": 0, "incremental": 1000000, "scd": 2000000}
            batch_offset = sk_offset.get(batch_type, 0)

            customer_data = []
            for i in range(num_records):
                customer_data.append(
                    [
                        batch_offset + i + 1,
                        i + 100000000,
                        f"TAX{i:06d}",
                        "Active",
                        f"LastName{i}",
                        f"FirstName{i}",
                        "M",
                        "M",
                        1,
                        "1980-01-01",
                        f"{i} Main St",
                        "",
                        "12345",
                        "City",
                        "NY",
                        "USA",
                        "555-0123",
                        "",
                        "",
                        f"customer{i}@email.com",
                        "",
                        "Standard Tax Rate",
                        0.25000,
                        "Local Tax Rate",
                        0.05000,
                        f"AGENCY{i:03d}",
                        750 + i,
                        1000000 + i * 10000,
                        f"Customer {i} Marketing Profile",
                        1,
                        1,
                        "1999-01-01",
                        "9999-12-31",
                    ]
                )

            columns = [
                "SK_CustomerID",
                "CustomerID",
                "TaxID",
                "Status",
                "LastName",
                "FirstName",
                "MiddleInitial",
                "Gender",
                "Tier",
                "DOB",
                "AddressLine1",
                "AddressLine2",
                "PostalCode",
                "City",
                "StateProv",
                "Country",
                "Phone1",
                "Phone2",
                "Phone3",
                "Email1",
                "Email2",
                "NationalTaxRateDesc",
                "NationalTaxRate",
                "LocalTaxRateDesc",
                "LocalTaxRate",
                "AgencyID",
                "CreditRating",
                "NetWorth",
                "MarketingNameplate",
                "IsCurrent",
                "BatchID",
                "EffectiveDate",
                "EndDate",
            ]

            df = pd.DataFrame(customer_data, columns=columns)
            df.to_csv(customer_file, index=False)
            csv_files.append(str(customer_file))

            trade_file = batch_dir / f"trades_{batch_type}.csv"

            num_trades = int(5000 * self.scale_factor)
            if batch_type == "incremental":
                num_trades = int(num_trades * 0.2)
            elif batch_type == "scd":
                num_trades = int(num_trades * 0.1)

            trade_offset = {"historical": 0, "incremental": 10000000, "scd": 20000000}
            trade_batch_offset = trade_offset.get(batch_type, 0)

            trade_data = []
            for i in range(num_trades):
                trade_data.append(
                    [
                        trade_batch_offset + i + 1,
                        i % 100 + 1,
                        100,
                        50.00,
                        1,
                        batch_offset + (i % 1000) + 1,
                        i % 10 + 1,
                        "Buy",
                        9.99,
                    ]
                )

            columns = [
                "TradeID",
                "SK_SecurityID",
                "Quantity",
                "TradePrice",
                "SK_CreateDateID",
                "SK_CustomerID",
                "SK_BrokerID",
                "Type",
                "Commission",
            ]

            df = pd.DataFrame(trade_data, columns=columns)
            df.to_csv(trade_file, index=False)
            csv_files.append(str(trade_file))

        return csv_files

    def _generate_xml_sources(self, batch_types: list[str]) -> list[str]:
        xml_files = []

        for batch_type in batch_types:
            batch_dir = self.source_dir / "xml" / batch_type
            batch_dir.mkdir(parents=True, exist_ok=True)

            company_file = batch_dir / f"companies_{batch_type}.xml"
            root = ET.Element("companies")

            num_companies = int(100 * self.scale_factor)
            if batch_type == "incremental":
                num_companies = int(num_companies * 0.1)
            elif batch_type == "scd":
                num_companies = int(num_companies * 0.05)

            for i in range(num_companies):
                company = ET.SubElement(root, "company")
                ET.SubElement(company, "company_id").text = f"COMP{i:04d}"
                ET.SubElement(company, "name").text = f"Company {i:04d} Inc."
                ET.SubElement(company, "industry").text = "Technology"
                ET.SubElement(company, "sp_rating").text = "A"
                ET.SubElement(company, "ceo").text = f"CEO {i:04d}"
                ET.SubElement(company, "address").text = f"{i} Corporate Blvd"
                ET.SubElement(company, "city").text = "New York"
                ET.SubElement(company, "state").text = "NY"
                ET.SubElement(company, "postal_code").text = "10001"
                ET.SubElement(company, "country").text = "USA"

            tree = ET.ElementTree(root)
            tree.write(company_file, encoding="utf-8", xml_declaration=True)
            xml_files.append(str(company_file))

        return xml_files

    def _generate_fixed_width_sources(self, batch_types: list[str]) -> list[str]:
        fixed_width_files = []

        for batch_type in batch_types:
            batch_dir = self.source_dir / "fixed_width" / batch_type
            batch_dir.mkdir(parents=True, exist_ok=True)

            security_file = batch_dir / f"securities_{batch_type}.txt"
            with open(security_file, "w", encoding="utf-8") as f:
                num_securities = int(500 * self.scale_factor)
                if batch_type == "incremental":
                    num_securities = int(num_securities * 0.1)
                elif batch_type == "scd":
                    num_securities = int(num_securities * 0.05)

                for i in range(num_securities):
                    symbol = f"SYM{i:04d}".ljust(8)
                    name = f"Security {i:04d}".ljust(30)
                    exchange = "NYSE".ljust(10)
                    shares = str(1000000).rjust(15)

                    line = symbol + name + exchange + shares + "\n"
                    f.write(line)

            fixed_width_files.append(str(security_file))

        return fixed_width_files

    def _generate_json_sources(self, batch_types: list[str]) -> list[str]:
        json_files = []

        for batch_type in batch_types:
            batch_dir = self.source_dir / "json" / batch_type
            batch_dir.mkdir(parents=True, exist_ok=True)

            account_file = batch_dir / f"accounts_{batch_type}.json"
            accounts = []

            num_accounts = int(2000 * self.scale_factor)
            if batch_type == "incremental":
                num_accounts = int(num_accounts * 0.1)
            elif batch_type == "scd":
                num_accounts = int(num_accounts * 0.05)

            account_offset = {"historical": 0, "incremental": 5000000, "scd": 6000000}
            account_batch_offset = account_offset.get(batch_type, 0)

            for i in range(num_accounts):
                account = {
                    "account_id": account_batch_offset + i + 1,
                    "customer_id": (i % 1000) + 100000000,
                    "broker_id": i % 10 + 1,
                    "status": "Active",
                    "account_desc": f"Account {i:06d}",
                    "tax_status": 0,
                    "opening_date": "2023-01-01",
                }
                accounts.append(account)

            with open(account_file, "w", encoding="utf-8") as f:
                json.dump(accounts, f, indent=2)

            json_files.append(str(account_file))

        return json_files

    def run_etl_pipeline(
        self,
        connection: Any | None = None,
        backend: TPCDIETLBackend | None = None,
        batch_type: str = "historical",
        validate_data: bool = True,
    ) -> dict[str, Any]:
        if backend is None:
            if connection is None:
                raise ValueError("run_etl_pipeline requires either backend or connection")
            backend = self._create_sql_etl_backend(connection=connection)

        start_time = mono_time()
        pipeline_results: dict[str, Any] = {
            "batch_type": batch_type,
            "start_time": datetime.now().isoformat(),
            "phases": {},
            "metrics": {},
            "validation_results": {},
            "parallel_enabled": self.enable_parallel,
            "success": False,
        }

        try:
            self.batch_status[batch_type]["status"] = "running"

            extract_start = mono_time()
            source_files = self.generate_source_data(
                formats=["csv", "xml", "fixed_width", "json"], batch_types=[batch_type]
            )
            extract_time = elapsed_seconds(extract_start)

            pipeline_results["phases"]["extract"] = {
                "duration": extract_time,
                "files_generated": sum(len(files) for files in source_files.values()),
                "source_files": source_files,
            }

            transform_start = mono_time()
            if self.enable_parallel:
                transformation_results = self._transform_source_data_parallel(source_files, batch_type)
            else:
                transformation_results = self._transform_source_data(source_files, batch_type)
            transform_time = elapsed_seconds(transform_start)

            pipeline_results["phases"]["transform"] = {
                "duration": transform_time,
                "records_processed": transformation_results["records_processed"],
                "transformations_applied": transformation_results["transformations_applied"],
                "parallel_enabled": self.enable_parallel,
            }

            load_start = mono_time()
            load_results = self._load_transformed_data(
                backend=backend,
                staged_data=transformation_results["staged_data"],
                batch_type=batch_type,
            )
            load_time = elapsed_seconds(load_start)

            pipeline_results["phases"]["load"] = {
                "duration": load_time,
                "records_loaded": load_results["records_loaded"],
                "tables_updated": load_results["tables_updated"],
            }

            if validate_data:
                validation_start = mono_time()
                validation_results = backend.validate_results()
                validation_time = elapsed_seconds(validation_start)

                pipeline_results["validation_results"] = validation_results
                pipeline_results["phases"]["validation"] = {
                    "duration": validation_time,
                    "queries_executed": len(validation_results.get("validation_queries", [])),
                    "data_quality_score": validation_results.get("data_quality_score", 0),
                }

            total_time = elapsed_seconds(start_time)

            self.etl_stats["batches_processed"] += 1
            self.etl_stats["processing_time"] += total_time

            self.batch_status[batch_type]["status"] = "completed"

            pipeline_results["success"] = True
            pipeline_results["end_time"] = datetime.now().isoformat()
            pipeline_results["total_duration"] = total_time
            pipeline_results["simple_stats"] = self._get_simple_stats()

        except Exception as e:
            self.batch_status[batch_type]["status"] = "failed"

            pipeline_results["success"] = False
            pipeline_results["error"] = str(e)
            pipeline_results["end_time"] = datetime.now().isoformat()

            self.etl_stats["errors"].append(
                {
                    "batch_type": batch_type,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                }
            )

            raise

        return pipeline_results

    def _file_transformers(self) -> dict[str, Callable[[str, str], dict[str, Any]]]:
        return {
            "csv": self._transform_csv_file,
            "xml": self._transform_xml_file,
            "fixed_width": self._transform_fixed_width_file,
            "json": self._transform_json_file,
        }

    def _transform_source_data(self, source_files: dict[str, list[str]], batch_type: str) -> dict[str, Any]:
        transformation_results: dict[str, Any] = {
            "records_processed": 0,
            "transformations_applied": [],
            "staged_data": {},
            "staged_data_parts": {},
        }

        transformers = self._file_transformers()
        failures: list[tuple[str, BaseException]] = []
        attempted = 0
        for format_type, files in source_files.items():
            transform = transformers.get(format_type)
            if transform is None:
                continue
            for file_path in files:
                attempted += 1
                try:
                    result = transform(file_path, batch_type)
                except Exception as e:
                    failures.append((file_path, e))
                    continue

                self._accumulate_transformation_result(transformation_results, result)

        self._require_transformed_files(attempted, failures)
        self._materialize_staged_data(transformation_results)
        return transformation_results

    @staticmethod
    def _require_transformed_files(attempted: int, failures: list[tuple[str, BaseException]]) -> None:
        if failures:
            raise TPCDITransformationError(failures) from failures[0][1]
        if attempted == 0:
            raise TPCDITransformationError([], "TPC-DI transformation found no transformable source files")

    def _transform_source_data_parallel(self, source_files: dict[str, list[str]], batch_type: str) -> dict[str, Any]:
        transformation_results: dict[str, Any] = {
            "records_processed": 0,
            "transformations_applied": [],
            "staged_data": {},
            "staged_data_parts": {},
        }

        transformers = self._file_transformers()
        transform_tasks = [
            (transformers[format_type], file_path)
            for format_type, files in source_files.items()
            if format_type in transformers
            for file_path in files
        ]

        failures: list[tuple[str, BaseException]] = []
        if transform_tasks:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(transform_tasks))) as executor:
                futures = [
                    (file_path, executor.submit(task_func, file_path, batch_type))
                    for task_func, file_path in transform_tasks
                ]

                for file_path, future in futures:
                    try:
                        result = future.result()
                    except Exception as e:
                        failures.append((file_path, e))
                        continue
                    self._accumulate_transformation_result(transformation_results, result)

        self._require_transformed_files(len(transform_tasks), failures)
        self._materialize_staged_data(transformation_results)
        return transformation_results

    def _transform_csv_file(self, file_path: str, batch_type: str) -> dict[str, Any]:
        transformations = ["csv_to_staging", "data_type_conversion", "null_handling"]

        df = pd.read_csv(file_path)

        file_name = Path(file_path).name.lower()
        if "customer" in file_name:
            batch_offset = {"historical": 0, "incremental": 1_000_000, "scd": 2_000_000}.get(batch_type, 0)
            df["SK_CustomerID"] = range(batch_offset + 1, batch_offset + len(df) + 1)
            df["IsCurrent"] = True
            df["BatchID"] = batch_offset // 1_000_000 + 1

            column_order = [
                "SK_CustomerID",
                "CustomerID",
                "TaxID",
                "Status",
                "LastName",
                "FirstName",
                "MiddleInitial",
                "Gender",
                "Tier",
                "DOB",
                "AddressLine1",
                "City",
                "StateProv",
                "PostalCode",
                "Country",
                "Phone1",
                "Email1",
                "IsCurrent",
                "BatchID",
                "EffectiveDate",
                "EndDate",
            ]
            df = df[column_order]
        else:
            df["BatchID"] = 1

        table_name = self._get_target_table_from_source_name(file_path)
        return {
            "records_processed": len(df),
            "transformations": transformations,
            "table_name": table_name,
            "dataframe": df,
        }

    def _transform_xml_file(self, file_path: str, batch_type: str) -> dict[str, Any]:
        transformations = ["xml_parsing", "xml_to_relational", "data_flattening"]

        tree = ET.parse(file_path)
        root = tree.getroot()

        data = []
        for company in root.findall("company"):

            def get_text(element_name: str) -> str:
                elem = company.find(element_name)
                return elem.text if elem is not None and elem.text is not None else ""

            row = [
                get_text("company_id"),
                get_text("name"),
                get_text("industry"),
                get_text("sp_rating"),
                get_text("ceo"),
                get_text("address"),
                get_text("city"),
                get_text("state"),
                get_text("postal_code"),
                get_text("country"),
                batch_type,
                datetime.now().isoformat(),
            ]
            data.append(row)

        columns = [
            "company_id",
            "name",
            "industry",
            "sp_rating",
            "ceo",
            "address",
            "city",
            "state",
            "postal_code",
            "country",
            "batch_id",
            "load_timestamp",
        ]

        df = pd.DataFrame(data, columns=columns)

        return {
            "records_processed": len(df),
            "transformations": transformations,
            "table_name": self._get_target_table_from_source_name(file_path),
            "dataframe": df,
        }

    def _transform_fixed_width_file(self, file_path: str, batch_type: str) -> dict[str, Any]:
        transformations = ["fixed_width_parsing", "field_extraction", "data_trimming"]

        colspecs = [(0, 8), (8, 38), (38, 48), (48, 63)]
        names = ["symbol", "name", "exchange", "shares_outstanding"]

        df = pd.read_fwf(file_path, colspecs=colspecs, names=names)

        df["batch_id"] = batch_type
        df["load_timestamp"] = datetime.now().isoformat()

        return {
            "records_processed": len(df),
            "transformations": transformations,
            "table_name": self._get_target_table_from_source_name(file_path),
            "dataframe": df,
        }

    def _transform_json_file(self, file_path: str, batch_type: str) -> dict[str, Any]:
        transformations = ["json_parsing", "json_normalization", "schema_mapping"]

        df = pd.read_json(file_path, convert_dates=False)

        df["batch_id"] = batch_type
        df["load_timestamp"] = datetime.now().isoformat()

        columns = [
            "account_id",
            "customer_id",
            "broker_id",
            "status",
            "account_desc",
            "tax_status",
            "date",
            "opening_date",
            "batch_id",
            "load_timestamp",
        ]

        existing_columns = [col for col in columns if col in df.columns]
        df = df[existing_columns]

        return {
            "records_processed": len(df),
            "transformations": transformations,
            "table_name": self._get_target_table_from_source_name(file_path),
            "dataframe": df,
        }

    def _accumulate_transformation_result(
        self,
        aggregate: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        aggregate["records_processed"] += result["records_processed"]
        aggregate["transformations_applied"].extend(result["transformations"])

        table_name_raw = result.get("table_name")
        dataframe_raw = result.get("dataframe")
        if table_name_raw is None or dataframe_raw is None:
            return

        if not isinstance(table_name_raw, str):
            raise TypeError("Transform result 'table_name' must be a string when provided")
        if not isinstance(dataframe_raw, pd.DataFrame):
            raise TypeError("Transform result 'dataframe' must be a pandas DataFrame when provided")

        parts = aggregate.get("staged_data_parts")
        if not isinstance(parts, dict):
            raise TypeError("Transformation aggregate is missing 'staged_data_parts' dictionary")
        table_parts = parts.setdefault(table_name_raw, [])
        if not isinstance(table_parts, list):
            raise TypeError("Transformation aggregate 'staged_data_parts' entries must be lists of DataFrames")
        table_parts.append(dataframe_raw)

    def _materialize_staged_data(self, aggregate: dict[str, Any]) -> None:
        parts = aggregate.get("staged_data_parts")
        if not isinstance(parts, dict):
            raise TypeError("Transformation aggregate is missing 'staged_data_parts' dictionary")
        staged_data: dict[str, pd.DataFrame] = {}
        for table_name, table_parts in parts.items():
            if not isinstance(table_name, str):
                raise TypeError("Transformation aggregate contains a non-string table name")
            if not isinstance(table_parts, list):
                raise TypeError("Transformation aggregate contains non-list staged_data_parts entries")
            if not table_parts:
                continue
            for dataframe in table_parts:
                if not isinstance(dataframe, pd.DataFrame):
                    raise TypeError("Transformation aggregate contains non-DataFrame staged_data_parts entries")
            if len(table_parts) == 1:
                staged_data[table_name] = table_parts[0]
            else:
                staged_data[table_name] = pd.concat(table_parts, ignore_index=True)
        aggregate["staged_data"] = staged_data

    def _load_transformed_data(
        self,
        *,
        backend: TPCDIETLBackend,
        staged_data: dict[str, pd.DataFrame],
        batch_type: str,
    ) -> dict[str, Any]:
        customers = staged_data.get("DimCustomer")
        if customers is None or customers.empty:
            return backend.load_dataframes(staged_data, batch_type=batch_type)

        load_customer_scd2_batch = getattr(backend, "load_customer_scd2_batch", None)
        if callable(load_customer_scd2_batch):
            other_data = {table_name: data for table_name, data in staged_data.items() if table_name != "DimCustomer"}
            load_results = backend.load_dataframes(other_data, batch_type=batch_type)
            customer_result = load_customer_scd2_batch(customers, batch_type=batch_type)
            customer_records = int(customer_result["rows_affected"])
            load_results["records_loaded"] += customer_records
            if customer_records and "DimCustomer" not in load_results["tables_updated"]:
                load_results["tables_updated"].append("DimCustomer")
            return load_results

        if batch_type in {"incremental", "scd"}:
            raise RuntimeError(
                "Incremental TPC-DI customer loads require a backend with durable atomic SCD2 batch support"
            )
        return backend.load_dataframes(staged_data, batch_type=batch_type)

    def _create_sql_etl_backend(self, *, connection: Any) -> SQLETLBackend:
        validation_queries = list(self.query_manager.get_queries_by_type("validation"))
        return SQLETLBackend(
            connection=connection,
            create_tables_sql=self.get_create_tables_sql(),
            execute_validation_query=self.execute_query,
            validation_query_ids=validation_queries,
        )

    def _load_warehouse_data(
        self, connection: Any, transformation_results: dict[str, Any], batch_type: str
    ) -> dict[str, Any]:
        backend = self._create_sql_etl_backend(connection=connection)
        return backend.load_dataframes(transformation_results.get("staged_data", {}), batch_type=batch_type)

    def _get_target_table_from_source_name(self, source_name: str) -> Optional[str]:
        file_name = Path(source_name).name.lower()

        if "customer" in file_name:
            return "DimCustomer"
        else:
            return None

    def validate_etl_results(self, connection: Any) -> dict[str, Any]:

        return self._create_sql_etl_backend(connection=connection).validate_results()

    def _get_simple_stats(self) -> dict[str, Any]:
        return {
            "batches_processed": self.etl_stats["batches_processed"],
            "total_processing_time": self.etl_stats["processing_time"],
            "error_count": len(self.etl_stats["errors"]),
            "batch_status": self.batch_status.copy(),
        }

    def get_etl_status(self) -> dict[str, Any]:

        return {
            "etl_mode_enabled": True,
            "source_directory": str(self.source_dir),
            "staging_directory": str(self.staging_dir),
            "warehouse_directory": str(self.warehouse_dir),
            "simple_stats": self._get_simple_stats(),
            "supported_formats": list(self.source_generators.keys()),
            "batch_types": ["historical", "incremental", "scd"],
        }

    def _initialize_connection_dependent_systems(self, connection: Any, dialect: str = "duckdb") -> None:
        if self.validator is None:
            self.validator = TPCDIValidator(connection, dialect)

        if self.etl_pipeline is None:
            self.etl_pipeline = TPCDIETLPipeline(connection, self, dialect)

        if self.data_loader is None:
            self.data_loader = TPCDIDataLoader(connection, dialect)

        if self.finwire_processor is None:
            self.finwire_processor = FinWireProcessor(connection, dialect)

        if self.customer_mgmt_processor is None:
            self.customer_mgmt_processor = CustomerManagementProcessor(connection, dialect)

        if self.scd_processor is None:
            from benchbox.core.tpcdi.etl.scd_processor import SCDProcessingConfig

            scd_config = SCDProcessingConfig()
            self.scd_processor = EnhancedSCDType2Processor(connection, config=scd_config)

        if self.incremental_loader is None:
            from benchbox.core.tpcdi.etl.incremental_loader import IncrementalLoadConfig

            incremental_config = IncrementalLoadConfig()
            self.incremental_loader = IncrementalDataLoader(connection, config=incremental_config)

        if self.data_quality_monitor is None:
            self.data_quality_monitor = DataQualityMonitor(connection)

        if self.error_recovery_manager is None:
            self.error_recovery_manager = ErrorRecoveryManager(connection, dialect)

    def create_schema(self, connection: Any, dialect: str = "duckdb") -> None:
        self.schema_manager.create_schema(connection, dialect)
        emit(f"Created TPC-DI schema for {dialect}")

    def run_full_benchmark(self, connection: Any, dialect: str = "duckdb") -> dict[str, Any]:
        emit(f"Starting complete TPC-DI benchmark (scale factor: {self.config.scale_factor})")
        start_time = datetime.now()
        start_mono = mono_time()

        try:
            self._initialize_connection_dependent_systems(connection, dialect)

            emit("Phase 1: Creating database schema...")
            self.create_schema(connection, dialect)

            emit("Phase 2: Running ETL pipeline...")
            etl_result = self.run_etl_benchmark(connection, dialect)

            emit("Phase 3: Running data quality validation...")
            validation_result = self.run_data_validation(connection)

            end_time = datetime.now()
            emit("Phase 4: Calculating TPC-DI metrics...")
            metrics = self.metrics_calculator.calculate_detailed_metrics(
                etl_result, validation_result, start_time, end_time
            )

            report = self.metrics_calculator.generate_official_report(metrics)

            self.metrics_calculator.print_official_results(metrics)

            return {
                "success": True,
                "metrics": self.metrics_calculator.export_metrics_json(metrics),
                "etl_result": self._serialize_etl_result(etl_result),
                "validation_result": self._serialize_validation_result(validation_result),
                "report": self._serialize_report(report),
                "execution_time_seconds": elapsed_seconds(start_mono),
            }

        except Exception as e:
            emit(f"❌ TPC-DI benchmark failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "execution_time_seconds": elapsed_seconds(start_mono),
            }

    def run_etl_benchmark(self, connection: Any, dialect: str = "duckdb") -> ETLResult:
        self._initialize_connection_dependent_systems(connection, dialect)
        stage_map = {
            "TDI_HISTORICAL": "historical",
            "TDI_INCREMENTAL": "incremental",
            "TDI_SCD": "scd",
        }
        backend = self._create_sql_etl_backend(connection=connection)
        start_time = datetime.now()
        stage_results = self._execute_etl_stage_queries(
            backend=backend,
            stage_map=stage_map,
            selected_stage_ids=set(stage_map),
        )
        end_time = datetime.now()
        return self._build_etl_result_from_stage_results(stage_results, start_time=start_time, end_time=end_time)

    def _build_etl_result_from_stage_results(
        self,
        stage_results: list[dict[str, Any]],
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> ETLResult:
        etl_result = ETLResult(start_time=start_time or datetime.now())
        stage_lookup = {str(result.get("query_id")): result for result in stage_results}

        historical = stage_lookup.get("TDI_HISTORICAL")
        if historical is not None:
            etl_result.historical_load = ETLPhaseResult(
                phase_name="Historical Load",
                total_execution_time=float(historical.get("execution_time_seconds", 0.0)),
                total_records_processed=self._extract_records_processed(historical),
                success=str(historical.get("status", "")).upper() == "SUCCESS",
            )

        for query_id, phase_name in (("TDI_INCREMENTAL", "Incremental Load"), ("TDI_SCD", "SCD Load")):
            result = stage_lookup.get(query_id)
            if result is None:
                continue
            etl_result.incremental_loads.append(
                ETLPhaseResult(
                    phase_name=phase_name,
                    total_execution_time=float(result.get("execution_time_seconds", 0.0)),
                    total_records_processed=self._extract_records_processed(result),
                    success=str(result.get("status", "")).upper() == "SUCCESS",
                )
            )

        etl_result.end_time = end_time or datetime.now()
        assert etl_result.start_time is not None
        etl_result.total_execution_time = (etl_result.end_time - etl_result.start_time).total_seconds()
        if etl_result.total_execution_time <= 0.0:
            phase_duration = (
                etl_result.historical_load.total_execution_time if etl_result.historical_load else 0.0
            ) + sum(phase.total_execution_time for phase in etl_result.incremental_loads)
            etl_result.total_execution_time = phase_duration
            etl_result.end_time = etl_result.start_time + timedelta(seconds=phase_duration)
        all_phase_success = []
        if etl_result.historical_load is not None:
            all_phase_success.append(etl_result.historical_load.success)
        all_phase_success.extend(phase.success for phase in etl_result.incremental_loads)
        etl_result.success = all(all_phase_success) if all_phase_success else False
        etl_result.total_records_processed = (
            etl_result.historical_load.total_records_processed if etl_result.historical_load else 0
        ) + sum(phase.total_records_processed for phase in etl_result.incremental_loads)
        return etl_result

    @staticmethod
    def _extract_records_processed(stage: dict[str, Any]) -> int:
        value = stage.get("records_processed")
        if value is not None:
            return int(value)
        value = stage.get("first_row", {}).get("records_processed")
        if value is not None:
            return int(value)
        return int(stage.get("rows_returned", 0))

    def run_data_validation(self, connection: Any) -> DataQualityResult:
        if self.validator is None:
            raise ValueError("Validator not initialized. Call run_full_benchmark or initialize manually.")

        emit("Running data quality validation...")
        return self.validator.run_all_validations()

    def calculate_official_metrics(
        self, etl_result: ETLResult, validation_result: DataQualityResult
    ) -> BenchmarkMetrics:
        start_time = datetime.now()
        end_time = datetime.now()

        return self.metrics_calculator.calculate_detailed_metrics(etl_result, validation_result, start_time, end_time)

    def optimize_database(self, connection: Any) -> dict[str, Any]:
        if self.data_loader is None:
            raise ValueError("Data loader not initialized")

        emit("Optimizing database for TPC-DI performance...")

        index_results = self.data_loader.create_indexes(connection)

        optimize_results = self.data_loader.optimize_tables(connection)

        return {
            "indexes_created": index_results,
            "tables_optimized": optimize_results,
            "optimization_successful": all(index_results.values()) and all(optimize_results.values()),
        }

    def _serialize_etl_result(self, etl_result: ETLResult) -> dict[str, Any]:
        return {
            "start_time": etl_result.start_time.isoformat() if etl_result.start_time else None,
            "end_time": etl_result.end_time.isoformat() if etl_result.end_time else None,
            "total_execution_time": etl_result.total_execution_time,
            "total_records_processed": etl_result.total_records_processed,
            "success": etl_result.success,
            "historical_load": {
                "phase_name": etl_result.historical_load.phase_name if etl_result.historical_load else None,
                "success": etl_result.historical_load.success if etl_result.historical_load else False,
                "total_records_processed": etl_result.historical_load.total_records_processed
                if etl_result.historical_load
                else 0,
                "total_execution_time": etl_result.historical_load.total_execution_time
                if etl_result.historical_load
                else 0.0,
            },
            "incremental_loads": [
                {
                    "phase_name": inc.phase_name,
                    "success": inc.success,
                    "total_records_processed": inc.total_records_processed,
                    "total_execution_time": inc.total_execution_time,
                }
                for inc in etl_result.incremental_loads
            ],
        }

    def _serialize_validation_result(self, validation_result: DataQualityResult) -> dict[str, Any]:
        return {
            "total_validations": validation_result.total_validations,
            "passed_validations": validation_result.passed_validations,
            "failed_validations": validation_result.failed_validations,
            "quality_score": validation_result.quality_score,
            "error_count": validation_result.error_count,
            "warning_count": validation_result.warning_count,
            "categories": validation_result.categories,
            "validations": [
                {
                    "name": val.name,
                    "description": val.description,
                    "passed": val.passed,
                    "status": val.status,
                    "category": val.category,
                    "severity": val.severity,
                }
                for val in validation_result.validations
            ],
        }

    def _serialize_report(self, report: BenchmarkReport) -> dict[str, Any]:
        return {
            "summary": report.summary,
            "phase_details": report.phase_details,
            "validation_details": report.validation_details,
            "performance_breakdown": report.performance_breakdown,
        }

    def run_enhanced_etl_pipeline(
        self,
        connection: Any,
        dialect: str = "duckdb",
        enable_data_quality_monitoring: bool = True,
        enable_error_recovery: bool = True,
    ) -> dict[str, Any]:
        emit("Starting enhanced TPC-DI ETL pipeline (Phase 3)")
        start_time = datetime.now()
        start_mono = mono_time()

        self._initialize_connection_dependent_systems(connection, dialect)

        pipeline_results: dict[str, Any] = {
            "start_time": start_time.isoformat(),
            "enhanced_features": {
                "data_quality_monitoring": enable_data_quality_monitoring,
                "error_recovery": enable_error_recovery,
            },
            "phases": {},
            "success": False,
            "total_records_processed": 0,
            "quality_score": 0.0,
        }

        try:
            emit("Phase 1: Enhanced data processing...")
            phase1_start = mono_time()

            phase1_results = self._run_enhanced_data_processing()
            phase1_time = elapsed_seconds(phase1_start)

            pipeline_results["phases"]["enhanced_data_processing"] = {
                "duration": phase1_time,
                "finwire_records": phase1_results.get("finwire_records", 0),
                "customer_mgmt_records": phase1_results.get("customer_mgmt_records", 0),
                "success": phase1_results.get("success", False),
            }
            self._attach_phase_error(pipeline_results["phases"]["enhanced_data_processing"], phase1_results)

            emit("Phase 2: Enhanced SCD Type 2 processing...")
            phase2_start = mono_time()

            phase2_results = self._run_enhanced_scd_processing(connection)
            phase2_time = elapsed_seconds(phase2_start)

            pipeline_results["phases"]["enhanced_scd_processing"] = {
                "duration": phase2_time,
                "scd_records_processed": phase2_results.get("records_processed", 0),
                "change_records_detected": phase2_results.get("changes_detected", 0),
                "success": phase2_results.get("success", False),
            }
            self._attach_phase_error(pipeline_results["phases"]["enhanced_scd_processing"], phase2_results)

            emit("Phase 3: Incremental data loading...")
            phase3_start = mono_time()

            phase3_results = self._run_incremental_data_loading(connection)
            phase3_time = elapsed_seconds(phase3_start)

            pipeline_results["phases"]["incremental_loading"] = {
                "duration": phase3_time,
                "incremental_batches": phase3_results.get("batches_loaded", 0),
                "records_loaded": phase3_results.get("records_loaded", 0),
                "success": phase3_results.get("success", False),
            }
            self._attach_phase_error(pipeline_results["phases"]["incremental_loading"], phase3_results)

            if enable_data_quality_monitoring:
                emit("Phase 4: Data quality monitoring...")
                phase4_start = mono_time()

                phase4_results = self._run_data_quality_monitoring(connection)
                phase4_time = elapsed_seconds(phase4_start)

                pipeline_results["phases"]["data_quality_monitoring"] = {
                    "duration": phase4_time,
                    "quality_rules_executed": phase4_results.get("rules_executed", 0),
                    "quality_score": phase4_results.get("quality_score", 0.0),
                    "issues_detected": phase4_results.get("issues_detected", 0),
                    "success": phase4_results.get("success", False),
                }
                pipeline_results["quality_score"] = phase4_results.get("quality_score", 0.0)

            pipeline_results["total_records_processed"] = (
                phase1_results.get("total_records", 0)
                + phase2_results.get("records_processed", 0)
                + phase3_results.get("records_loaded", 0)
            )

            required_phase_results = {
                "enhanced_data_processing": phase1_results,
                "enhanced_scd_processing": phase2_results,
                "incremental_loading": phase3_results,
            }
            failed_phases = [
                {
                    "phase": phase_name,
                    "error": phase_result.get("error") or phase_result.get("errors") or "phase reported failure",
                }
                for phase_name, phase_result in required_phase_results.items()
                if not phase_result.get("success", False)
            ]

            optional_phase_successes = []
            if enable_data_quality_monitoring:
                optional_phase_successes.append(pipeline_results["phases"]["data_quality_monitoring"]["success"])

            core_success = not failed_phases
            optional_success_count = sum(optional_phase_successes)

            pipeline_results["success"] = core_success
            pipeline_results["core_phases_success"] = core_success
            pipeline_results["failed_phases"] = failed_phases
            pipeline_results["optional_phases_success"] = f"{optional_success_count}/{len(optional_phase_successes)}"

            end_time = datetime.now()
            pipeline_results["end_time"] = end_time.isoformat()
            pipeline_results["total_duration"] = elapsed_seconds(start_mono)

            if core_success:
                emit(
                    f"✅ Enhanced ETL pipeline completed successfully in {pipeline_results['total_duration']:.2f} seconds"
                )
            else:
                failed_names = ", ".join(failure["phase"] for failure in failed_phases)
                emit(f"❌ Enhanced ETL pipeline failed required phases: {failed_names}")

        except Exception as e:
            if enable_error_recovery and self.error_recovery_manager:
                emit(f"⚠️ Attempting error recovery for: {str(e)}")
                recovery_result = self.error_recovery_manager.handle_pipeline_error(str(e), str(type(e)))
                pipeline_results["error_recovery"] = {
                    "attempted": True,
                    "recovery_action": recovery_result.get("action", "unknown"),
                    "should_retry": recovery_result.get("should_retry", False),
                }

            pipeline_results["success"] = False
            pipeline_results["error"] = str(e)
            pipeline_results["end_time"] = datetime.now().isoformat()
            pipeline_results["total_duration"] = elapsed_seconds(start_mono)
            emit(f"❌ Enhanced ETL pipeline failed: {e}")

        return pipeline_results

    @staticmethod
    def _attach_phase_error(phase_entry: dict[str, Any], phase_results: dict[str, Any]) -> None:
        error = phase_results.get("error") or phase_results.get("errors")
        if error:
            phase_entry["error"] = error

    def _run_enhanced_data_processing(self) -> dict[str, Any]:
        results: dict[str, Any] = {
            "success": False,
            "finwire_records": 0,
            "customer_mgmt_records": 0,
            "total_records": 0,
            "records_processed": 0,
            "errors": [],
        }

        try:
            missing = [
                name
                for name, processor in (
                    ("finwire_processor", self.finwire_processor),
                    ("customer_mgmt_processor", self.customer_mgmt_processor),
                )
                if processor is None
            ]
            if missing:
                results["error"] = f"Enhanced data processing has no processor for: {', '.join(missing)}"
                return results

            finwire_files = self._generate_finwire_data_files()
            customer_files = self._generate_customer_mgmt_data_files()
            if not finwire_files and not customer_files:
                results["error"] = "Enhanced data processing generated no input files"
                return results

            failed = False
            for finwire_file in finwire_files:
                processing_result = self.finwire_processor.process_finwire_file(finwire_file, batch_id=1)
                if processing_result["success"]:
                    results["finwire_records"] += processing_result.get("records_processed", 0)
                else:
                    results["errors"].extend(processing_result.get("errors", []))
                    failed = True

            for customer_file in customer_files:
                if customer_file.suffix == ".xml":
                    processing_result = self.customer_mgmt_processor.process_customer_management_file(
                        customer_file, batch_id=1
                    )
                else:
                    processing_result = self.customer_mgmt_processor.process_prospect_file(customer_file, batch_id=1)

                if processing_result["success"]:
                    results["customer_mgmt_records"] += processing_result.get("records_processed", 0)
                else:
                    results["errors"].extend(processing_result.get("errors", []))
                    failed = True

            results["total_records"] = results["finwire_records"] + results["customer_mgmt_records"]
            results["records_processed"] = results["total_records"]
            results["success"] = not failed

        except Exception as e:
            emit(f"❌ Enhanced data processing failed: {e}")
            results["error"] = str(e)
            results["success"] = False

        return results

    def _run_enhanced_scd_processing(self, connection: Any) -> dict[str, Any]:
        results = {"success": False, "records_processed": 0, "changes_detected": 0}

        try:
            if self.scd_processor:
                dimension_name = "DimCustomer"
                business_key_column = "CustomerID"
                scd_columns = ["FirstName", "LastName", "Email", "Address"]
                batch_id = 1

                scd_result = self.scd_processor.process_dimension(
                    dimension_name, business_key_column, scd_columns, batch_id
                )

                results["records_processed"] = scd_result.get("records_processed", 0)
                results["changes_detected"] = scd_result.get("changes_detected", 0)
                results["success"] = scd_result.get("success", False)

                if not results["success"]:
                    results["error"] = scd_result.get("error", "SCD processing failed")

        except Exception as e:
            emit(f"❌ Enhanced SCD processing failed: {e}")
            results["error"] = str(e)

        return results

    def _run_incremental_data_loading(self, connection: Any) -> dict[str, Any]:
        results = {"success": False, "batches_loaded": 0, "records_loaded": 0}

        try:
            pipeline = self.run_etl_pipeline(connection=connection, batch_type="incremental", validate_data=False)
            load = pipeline.get("phases", {}).get("load")
            if pipeline.get("success") and load is not None:
                results["success"] = True
                results["batches_loaded"] = 1
                results["records_loaded"] = load["records_loaded"]
            else:
                results["error"] = pipeline.get("error", "Incremental ETL did not complete its load phase")

        except Exception as e:
            emit(f"❌ Incremental data loading failed: {e}")
            results["error"] = str(e)

        return results

    def _run_data_quality_monitoring(self, connection: Any) -> dict[str, Any]:
        results = {
            "success": False,
            "rules_executed": 0,
            "quality_score": 0.0,
            "issues_detected": 0,
        }

        try:
            if self.data_quality_monitor:
                from benchbox.core.tpcdi.etl.data_quality_monitor import DataQualityRule

                quality_rules = [
                    DataQualityRule(
                        rule_id="customer_completeness",
                        rule_name="Customer Name Completeness",
                        rule_type="COMPLETENESS",
                        table_name="DimCustomer",
                        column_name="FirstName",
                        custom_sql="SELECT COUNT(*) FROM DimCustomer WHERE FirstName IS NULL OR FirstName = ''",
                        severity="HIGH",
                    ),
                    DataQualityRule(
                        rule_id="customer_email_format",
                        rule_name="Customer Email Format",
                        rule_type="ACCURACY",
                        table_name="DimCustomer",
                        column_name="Email1",
                        custom_sql="SELECT COUNT(*) FROM DimCustomer WHERE Email1 NOT LIKE '%@%' AND Email1 IS NOT NULL",
                        severity="MEDIUM",
                    ),
                    DataQualityRule(
                        rule_id="trade_positive_price",
                        rule_name="Trade Positive Prices",
                        rule_type="ACCURACY",
                        table_name="FactTrade",
                        column_name="TradePrice",
                        custom_sql="SELECT COUNT(*) FROM FactTrade WHERE TradePrice <= 0",
                        severity="HIGH",
                    ),
                    DataQualityRule(
                        rule_id="account_consistency",
                        rule_name="Account Customer Consistency",
                        rule_type="CONSISTENCY",
                        table_name="DimAccount",
                        column_name="SK_CustomerID",
                        custom_sql="SELECT COUNT(*) FROM DimAccount a LEFT JOIN DimCustomer c ON a.SK_CustomerID = c.SK_CustomerID WHERE c.SK_CustomerID IS NULL",
                        severity="HIGH",
                    ),
                ]

                for rule in quality_rules:
                    self.data_quality_monitor.add_rule(rule)

                quality_result = self.data_quality_monitor.execute_quality_checks()

                results["rules_executed"] = quality_result.get("rules_executed", 0)
                results["quality_score"] = quality_result.get("overall_pass_rate", 0.0)
                results["issues_detected"] = quality_result.get("rules_failed", 0)
                results["success"] = True
                results["note"] = f"Quality monitoring completed with {results['quality_score']:.1f}% pass rate"

        except Exception as e:
            emit(f"⚠️ Data quality monitoring encountered issues: {e}")
            results["success"] = True
            results["rules_executed"] = 0
            results["quality_score"] = 0.0
            results["issues_detected"] = 0
            results["note"] = "Quality monitoring had issues but ETL pipeline continued"

        return results

    def get_enhanced_etl_status(self) -> dict[str, Any]:
        return {
            "phase_3_components": {
                "finwire_processor": self.finwire_processor is not None,
                "customer_mgmt_processor": self.customer_mgmt_processor is not None,
                "scd_processor": self.scd_processor is not None,
                "incremental_loader": self.incremental_loader is not None,
                "data_quality_monitor": self.data_quality_monitor is not None,
                "error_recovery_manager": self.error_recovery_manager is not None,
            },
            "enhanced_features": {
                "parallel_processing_enabled": self.enable_parallel,
                "max_workers": self.max_workers,
                "scale_factor": self.scale_factor,
            },
            "basic_etl_status": self.get_etl_status(),
        }

    def _generate_finwire_data_files(self) -> list[Path]:
        finwire_files = []

        finwire_dir = self.output_dir / "finwire"
        finwire_dir.mkdir(parents=True, exist_ok=True)

        finwire_file = finwire_dir / "finwire.txt"

        num_companies = max(1, int(100 * self.scale_factor))
        num_securities = max(1, int(500 * self.scale_factor))
        num_financials = max(1, int(200 * self.scale_factor))

        def format_record(layout: dict[str, tuple[int, int, type]], values: dict[str, str | int]) -> str:
            record = [" "] * max(start + width for start, width, _ in layout.values())
            for field, value in values.items():
                start, width, _ = layout[field]
                text = str(value)
                if len(text) > width:
                    raise ValueError(f"FinWire {field} exceeds its {width}-character field")
                record[start : start + width] = text.ljust(width)
            return "".join(record)

        with open(finwire_file, "w", encoding="utf-8") as f:
            for i in range(num_companies):
                cmp_id = f"{i + 1:010d}"
                record = format_record(
                    FinWireParser.CMP_LAYOUT,
                    {
                        "pts": "20230101000000",
                        "rec_type": "CMP",
                        "company_name": f"Company_{i + 1:04d}",
                        "cik": cmp_id,
                        "status": "ACTV",
                        "industry_id": "01",
                        "sp_rating": "AAA",
                        "founding_date": "20000101",
                        "ceo_name": f"CEO_{i + 1}",
                    },
                )
                f.write(record + "\n")

            for i in range(num_securities):
                record = format_record(
                    FinWireParser.SEC_LAYOUT,
                    {
                        "pts": "20230101000000",
                        "rec_type": "SEC",
                        "symbol": f"SEC{i + 1:04d}",
                        "issue_type": "CS",
                        "status": "A",
                        "name": f"Security_{i + 1:04d} Inc",
                        "ex_id": "NYSE",
                        "sh_out": 1000000 + i * 1000,
                        "first_trade_date": "20230101",
                        "first_trade_exchg": "20230101",
                        "dividend": "0",
                        "co_name_or_cik": f"{(i % num_companies) + 1:010d}",
                    },
                )
                f.write(record + "\n")

            for i in range(num_financials):
                record = format_record(
                    FinWireParser.FIN_LAYOUT,
                    {
                        "pts": "20230101000000",
                        "rec_type": "FIN",
                        "year": 2023,
                        "quarter": 1,
                        "qtrsartdate": "20230101",
                        "postdate": "20230401",
                        "revenue": 1000000 + i * 10000,
                        "co_name_or_cik": f"{(i % num_companies) + 1:010d}",
                    },
                )
                f.write(record + "\n")

        finwire_files.append(finwire_file)
        return finwire_files

    def _generate_customer_mgmt_data_files(self) -> list[Path]:
        customer_files = []

        customer_dir = self.output_dir / "customer_mgmt"
        customer_dir.mkdir(parents=True, exist_ok=True)

        customer_xml = customer_dir / "CustomerMgmt.xml"
        num_customers = max(1, int(50 * self.scale_factor))

        with open(customer_xml, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<TPCDI:Actions xmlns:TPCDI="http://www.tpc.org/tpc-di">\n')

            for i in range(num_customers):
                customer_id = 1000 + i
                action_type = "NEW" if i < num_customers // 2 else "UPDP"
                timestamp = "2023-01-01T00:00:00"

                f.write(f'  <TPCDI:Action ActionType="{action_type}" ActionTS="{timestamp}">\n')
                f.write(f'    <Customer C_ID="{customer_id}">\n')
                f.write(f'      <Name C_L_NAME="LastName_{i}" C_F_NAME="FirstName_{i}" C_M_NAME="M" />\n')
                f.write(
                    f'      <Address C_ADLINE1="{i} Main St" C_ADLINE2="" C_ZIPCODE="12345" C_CITY="City_{i}" C_STATE_PROV="NY" C_CTRY="USA" />\n'
                )
                f.write(
                    f'      <ContactInfo C_PRIM_EMAIL="customer_{i}@email.com" C_ALT_EMAIL="" C_PHONE_1="555-{i:04d}" C_PHONE_2="" C_PHONE_3="" />\n'
                )
                f.write(f'      <TaxInfo C_LCL_TX_ID="LOCAL{i:06d}" C_NAT_TX_ID="NATIONAL{i:06d}" />\n')
                f.write("    </Customer>\n")
                f.write("  </TPCDI:Action>\n")

            f.write("</TPCDI:Actions>\n")

        customer_files.append(customer_xml)

        prospect_csv = customer_dir / "Prospect.csv"
        num_prospects = max(1, int(20 * self.scale_factor))

        with open(prospect_csv, "w", encoding="utf-8") as f:
            f.write(
                "LastName,FirstName,MiddleInitial,Gender,AddressLine1,AddressLine2,PostalCode,City,StateProv,Country,Phone,Income,NumberCars,NumberChildren,MaritalStatus,Age,CreditRating,OwnOrRentFlag,Employer,NumberCreditCards,NetWorth\n"
            )

            for i in range(num_prospects):
                f.write(
                    f"Prospect_{i},John,M,M,{i} Oak St,,12345,ProspectCity,NY,USA,555-{i + 5000:04d},{50000 + i * 1000},{i % 3 + 1},{i % 4},M,{30 + i % 40},{700 + i % 100},{'O' if i % 2 else 'R'},TechCorp_{i},{i % 5 + 1},{100000 + i * 5000}\n"
                )

        customer_files.append(prospect_csv)
        return customer_files


from benchbox.core.hooks.benchmark_hooks import (
    BenchmarkHookRegistry,
    BenchmarkOptionSpec,
    parse_bool,
    parse_int,
)

BenchmarkHookRegistry.register_option_specs(
    "tpcdi",
    BenchmarkOptionSpec(
        name="enable_parallel",
        parser=parse_bool,
        default=False,
        help="Enable parallel processing for ETL",
        aliases=("enable-parallel",),
    ),
    BenchmarkOptionSpec(
        name="max_workers",
        parser=parse_int,
        help="Maximum number of parallel workers",
        aliases=("max-workers",),
    ),
    benchmark_class=TPCDIBenchmark,
)
