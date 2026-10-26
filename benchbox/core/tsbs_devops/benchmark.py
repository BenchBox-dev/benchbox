# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.query_catalog_base import TranslatableQueryMixin
from benchbox.core.tsbs_devops.generator import TSBSDevOpsDataGenerator
from benchbox.core.tsbs_devops.queries import TSBSDevOpsQueryManager
from benchbox.core.tsbs_devops.schema import (
    TSBS_DEVOPS_SCHEMA,
    get_create_tables_sql,
)
from benchbox.utils.cloud_storage import normalize_output_dir
from benchbox.utils.compression_mixin import extract_compression_kwargs
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path

if TYPE_CHECKING:
    from benchbox.core.connection import DatabaseConnection


class TSBSDevOpsBenchmark(GeneratorOutputDirMixin, TranslatableQueryMixin, BaseBenchmark):
    _source_dialect = "duckdb"
    _native_source_passthrough = True

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Union[str, Path] | None = None,
        num_hosts: int | None = None,
        duration_days: int | None = None,
        interval_seconds: int = 10,
        start_time: datetime | None = None,
        seed: int | None = None,
        verbose: int | bool = 0,
        quiet: bool = False,
        force_regenerate: bool = False,
        **kwargs: Any,
    ) -> None:
        resolved_output_dir = (
            normalize_output_dir(output_dir)
            if output_dir
            else get_benchmark_runs_datagen_path("tsbs_devops", scale_factor)
        )
        super().__init__(
            scale_factor=scale_factor,
            output_dir=resolved_output_dir,
            verbose=verbose,
            quiet=quiet,
            force_regenerate=force_regenerate,
            **kwargs,
        )
        self.seed = seed
        self.csv_has_header: bool = True

        self._name = "TSBS DevOps"
        self._version = "1.0"
        self._description = "Time Series Benchmark Suite for DevOps monitoring workloads"

        self.logger = logging.getLogger("benchbox.core.tsbs_devops.benchmark")

        compression_kwargs = extract_compression_kwargs(kwargs)
        self.data_generator = TSBSDevOpsDataGenerator(
            scale_factor=scale_factor,
            output_dir=self.output_dir,
            num_hosts=num_hosts,
            duration_days=duration_days,
            interval_seconds=interval_seconds,
            start_time=start_time,
            seed=seed,
            verbose=verbose,
            quiet=quiet,
            force_regenerate=force_regenerate,
            **compression_kwargs,
        )

        self.num_hosts = self.data_generator.num_hosts
        self.duration_days = self.data_generator.duration_days
        self.interval_seconds = self.data_generator.interval_seconds
        self.start_time = self.data_generator.start_time

        self.query_manager = TSBSDevOpsQueryManager(
            num_hosts=self.num_hosts,
            start_time=self.start_time,
            duration_days=self.duration_days,
            seed=seed,
        )

        self.tables: dict[str, Path] = {}

    def generate_data(self) -> list[Union[str, Path]]:
        self.log_verbose(f"Generating TSBS DevOps data (SF={self.scale_factor})")

        self.tables = self.data_generator.generate()

        if self.verbose_enabled:
            stats = self.data_generator.get_generation_stats()
            self.logger.info(f"Generated {stats['total_rows']} total rows")

        return list(self.tables.values())

    def get_queries(self, dialect: str | None = None) -> dict[str, str]:
        return {qid: self.translate_for_dialect(sql, dialect) for qid, sql in self.query_manager.get_queries().items()}

    def get_query(
        self,
        query_id: Union[int, str],
        *,
        params: dict[str, Any] | None = None,
        **kwargs,
    ) -> str:
        query_key = str(query_id)
        return self.translate_for_dialect(self.query_manager.get_query(query_key, params), kwargs.get("dialect"))

    def get_schema(self) -> dict[str, dict[str, Any]]:
        return TSBS_DEVOPS_SCHEMA

    def get_create_tables_sql(
        self,
        dialect: str = "standard",
        include_constraints: bool = True,
        time_partitioning: bool = False,
        tuning_config: Any = None,
    ) -> str:
        return get_create_tables_sql(
            dialect=dialect,
            include_constraints=include_constraints,
            time_partitioning=time_partitioning,
        )

    def get_benchmark_info(self) -> dict[str, Any]:
        return {
            "name": "TSBS DevOps",
            "description": "Time Series Benchmark Suite for DevOps monitoring workloads",
            "reference": "https://github.com/timescale/tsbs",
            "version": "1.0",
            "scale_factor": self.scale_factor,
            "num_hosts": self.num_hosts,
            "duration_days": self.duration_days,
            "interval_seconds": self.interval_seconds,
            "num_queries": self.query_manager.get_query_count(),
            "query_categories": self.query_manager.get_categories(),
            "tables": ["tags", "cpu", "mem", "disk", "net"],
            "metrics": {
                "cpu": "CPU usage metrics (user, system, idle, iowait, etc.)",
                "mem": "Memory metrics (used, free, cached, buffered)",
                "disk": "Disk I/O metrics (reads, writes, IOPS, latency)",
                "net": "Network metrics (bytes, packets, errors)",
            },
        }

    def get_query_info(self, query_id: str) -> dict[str, Any]:
        return self.query_manager.get_query_info(query_id)

    def get_queries_by_category(self, category: str) -> list[str]:
        return self.query_manager.get_queries_by_category(category)

    def get_generation_stats(self) -> dict:
        return self.data_generator.get_generation_stats()

    def _load_data(self, connection: DatabaseConnection) -> None:
        logger = logging.getLogger(__name__)

        if not self.tables:
            raise ValueError("No data has been generated. Call generate_data() first.")

        logger.info("Loading TSBS DevOps data into database...")

        try:
            schema_sql = self.get_create_tables_sql(dialect="standard", include_constraints=False)
            if ";" in schema_sql:
                statements = [stmt.strip() for stmt in schema_sql.split(";") if stmt.strip()]
                for statement in statements:
                    connection.execute(statement)
            else:
                connection.execute(schema_sql)
            connection.commit()
            logger.info("Created TSBS DevOps database schema")
        except Exception as e:
            logger.error(f"Failed to create database schema: {e}")
            raise

        table_order = ["tags", "cpu", "mem", "disk", "net"]
        total_rows = 0
        loaded_tables = 0

        for table_name in table_order:
            if table_name not in self.tables:
                logger.warning(f"Skipping {table_name} - no data file found")
                continue

            data_file = Path(self.tables[table_name])
            if not data_file.exists():
                logger.warning(f"Skipping {table_name} - data file does not exist: {data_file}")
                continue

            try:
                logger.info(f"Loading data for {table_name}...")
                rows_loaded = self._load_table_data(connection, table_name, data_file)

                total_rows += rows_loaded
                loaded_tables += 1
                logger.info(f"Loaded {rows_loaded:,} rows into {table_name}")

            except Exception as e:
                logger.error(f"Failed to load data for {table_name}: {e}")
                raise

        try:
            connection.commit()
            logger.info(f"Successfully loaded {total_rows:,} total rows across {loaded_tables} tables")
        except Exception as e:
            logger.error(f"Failed to commit data loading transaction: {e}")
            raise

    def _load_table_data(self, connection: DatabaseConnection, table_name: str, data_file: Path) -> int:
        table_schema = TSBS_DEVOPS_SCHEMA[table_name]
        column_names = list(table_schema["columns"].keys())
        num_columns = len(column_names)

        placeholders = ", ".join(["?" for _ in range(num_columns)])
        insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders})"

        rows_loaded = 0

        with open(data_file, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)

            for row in reader:
                if len(row) != num_columns:
                    continue

                connection.execute(insert_sql, row)
                rows_loaded += 1

        return rows_loaded


from benchbox.core.hooks.benchmark_hooks import (
    BenchmarkHookRegistry,
    BenchmarkOptionSpec,
    parse_datetime,
    parse_int,
)

BenchmarkHookRegistry.register_option_specs(
    "tsbs_devops",
    BenchmarkOptionSpec(
        name="num_hosts",
        parser=parse_int,
        help="Number of simulated hosts",
        aliases=("num-hosts",),
    ),
    BenchmarkOptionSpec(
        name="duration_days",
        parser=parse_int,
        help="Duration in days for data generation",
        aliases=("duration-days",),
    ),
    BenchmarkOptionSpec(
        name="interval_seconds",
        parser=parse_int,
        default=10,
        help="Measurement interval in seconds",
        aliases=("interval-seconds",),
    ),
    BenchmarkOptionSpec(
        name="start_time",
        parser=parse_datetime,
        help="Start time for data generation (ISO format)",
        aliases=("start-time",),
    ),
    BenchmarkOptionSpec(
        name="seed",
        parser=parse_int,
        help="Random seed for reproducibility",
    ),
    BenchmarkOptionSpec(
        name="force_regenerate",
        parser=lambda v: v.strip().lower() in ("true", "1", "yes"),
        help="Force data regeneration",
        aliases=("force-regenerate",),
    ),
    benchmark_class=TSBSDevOpsBenchmark,
)
