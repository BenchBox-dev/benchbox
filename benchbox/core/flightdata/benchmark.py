# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.flightdata.downloader import LAST_AVAILABLE_YEAR, FlightDataDownloader
from benchbox.core.flightdata.queries import FlightDataQueryManager
from benchbox.core.flightdata.schema import FLIGHT_SCHEMA, get_create_tables_sql
from benchbox.core.query_catalog_base import TranslatableQueryMixin
from benchbox.utils.compression_mixin import extract_compression_kwargs
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path

if TYPE_CHECKING:
    from benchbox.core.connection import DatabaseConnection
    from benchbox.core.dataframe.query import QueryRegistry


class FlightDataBenchmark(GeneratorOutputDirMixin, TranslatableQueryMixin, BaseBenchmark):
    _source_dialect = "duckdb"
    _native_source_passthrough = True
    OUTPUT_DIR_GENERATOR_ATTRS = ("downloader",)

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Union[str, Path] | None = None,
        end_year: int = LAST_AVAILABLE_YEAR,
        seed: int | None = None,
        verbose: int | bool = 0,
        quiet: bool = False,
        force_regenerate: bool = False,
        allow_synthetic_fallback: bool = False,
        **kwargs: Any,
    ) -> None:
        resolved_output_dir = (
            Path(output_dir) if output_dir else get_benchmark_runs_datagen_path("flightdata", scale_factor)
        )
        super().__init__(
            scale_factor=scale_factor,
            output_dir=resolved_output_dir,
            verbose=verbose,
            quiet=quiet,
            force_regenerate=force_regenerate,
            **kwargs,
        )

        self.end_year = end_year
        self.seed = seed
        self.force_regenerate = force_regenerate

        self._name = "Flight Data OLAP"
        self._version = "1.0"
        self._description = "US BTS On-Time Performance data for aviation analytics"

        self.logger = logging.getLogger("benchbox.core.flightdata.benchmark")

        compression_kwargs = extract_compression_kwargs(kwargs)
        self.downloader = FlightDataDownloader(
            scale_factor=scale_factor,
            output_dir=self.output_dir,
            seed=seed,
            verbose=verbose,
            quiet=quiet,
            force_redownload=force_regenerate,
            allow_synthetic_fallback=allow_synthetic_fallback,
            **compression_kwargs,
        )

        months_seq = self.downloader.months
        if months_seq:
            oldest_year, oldest_month = months_seq[-1]
            newest_year, newest_month = months_seq[0]
            self._query_start_date = f"{oldest_year}-{oldest_month:02d}-01"
            if newest_month == 12:
                exclusive_end = date(newest_year + 1, 1, 1)
            else:
                exclusive_end = date(newest_year, newest_month + 1, 1)
            self._query_end_date = exclusive_end.isoformat()
        else:
            self._query_start_date = f"{end_year - 1}-01-01"
            self._query_end_date = f"{end_year + 1}-01-01"

        self.query_manager = FlightDataQueryManager(
            start_date=self._query_start_date,
            end_date=self._query_end_date,
        )

        self.tables: dict[str, Path | list[Path]] = {}
        self.csv_has_header: bool = True
        self.csv_null_marker: str = ""

    def generate_data(self) -> list[Union[str, Path]]:
        self.log_verbose(f"Generating Flight Data (SF={self.scale_factor}, {self.downloader.num_months} months)")
        self.tables = self.downloader.download()
        stats = self.downloader.get_download_stats()
        self.log_verbose(
            f"Flight data ready: {stats['total_flights']:,} flights, "
            f"{stats['months_downloaded']} downloaded, {stats['months_synthetic']} synthetic"
        )
        return self._flatten_table_paths(self.tables)

    def ensure_auxiliary_data_files(self) -> None:
        repaired = self.downloader.repair_reusable_layout()
        if repaired:
            self.tables = repaired
        else:
            self.downloader.backfill_csv_dialect_metadata()

    def manifest_matches_datagen_identity(self, manifest: dict[str, Any]) -> bool:
        return self.downloader.manifest_matches_source_identity(manifest)

    @staticmethod
    def _flatten_table_paths(tables: dict[str, Path | list[Path]]) -> list[Path]:
        paths: list[Path] = []
        for table_path in tables.values():
            if isinstance(table_path, list):
                paths.extend(table_path)
            else:
                paths.append(table_path)
        return paths

    def get_csv_loading_config(self, table_name: str) -> list[str]:
        return ["delim=','", "header=true", "auto_detect=true", "ignore_errors=true"]

    def get_queries(self, dialect: str | None = None) -> dict[str, str]:
        return self.query_manager.get_queries(dialect=dialect)

    def get_query(
        self,
        query_id: Union[int, str],
        *,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        return self.query_manager.get_query(str(query_id), params)

    def get_schema(self) -> dict[str, dict[str, Any]]:
        return FLIGHT_SCHEMA

    def get_create_tables_sql(
        self,
        dialect: str = "standard",
        include_constraints: bool = True,
        tuning_config: Any = None,
    ) -> str:
        return get_create_tables_sql(dialect=dialect, include_constraints=include_constraints)

    def get_benchmark_info(self) -> dict[str, Any]:
        stats = self.downloader.get_download_stats()
        return {
            "name": "Flight Data OLAP",
            "description": "US BTS On-Time Performance data for aviation analytics",
            "reference": "https://www.transtats.bts.gov/ontime/",
            "version": "1.0",
            "scale_factor": self.scale_factor,
            "end_year": self.end_year,
            "num_months": stats["num_months"],
            "query_start_date": self._query_start_date,
            "query_end_date": self._query_end_date,
            "num_queries": self.query_manager.get_query_count(),
            "query_categories": self.query_manager.get_categories(),
            "tables": ["flights", "airlines", "airports"],
            "data_type": "real_or_synthetic",
        }

    def get_query_info(self, query_id: str) -> dict[str, Any]:
        return self.query_manager.get_query_info(query_id)

    def get_queries_by_category(self, category: str) -> list[str]:
        return self.query_manager.get_queries_by_category(category)

    def get_download_stats(self) -> dict[str, Any]:
        return self.downloader.get_download_stats()

    def get_dataframe_queries(self) -> QueryRegistry:
        from benchbox.core.flightdata.dataframe_queries import (
            get_dataframe_queries,
            set_parameter_overrides,
        )

        set_parameter_overrides(None)

        start_date, end_date = self._get_date_range()
        set_parameter_overrides({"start_date": start_date, "end_date": end_date})

        return get_dataframe_queries()

    def _get_date_range(self) -> tuple[date, date]:
        query_start = getattr(self, "_query_start_date", None)
        query_end = getattr(self, "_query_end_date", None)
        if isinstance(query_start, str) and isinstance(query_end, str):
            return date.fromisoformat(query_start), date.fromisoformat(query_end)

        self.logger.warning("FlightData: query date range not initialized; falling back to default date range (2018).")
        return date(2018, 1, 1), date(2019, 1, 1)

    def _load_data(self, connection: DatabaseConnection) -> None:
        if not self.tables:
            raise ValueError("No data has been generated. Call generate_data() first.")

        self.logger.info("Loading flight data into database...")

        schema_sql = self.get_create_tables_sql()
        if ";" in schema_sql:
            for stmt in schema_sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    connection.execute(stmt)
        else:
            connection.execute(schema_sql)
        connection.commit()

        self.logger.info("Created flight data schema")

        table_order = ["airlines", "airports", "flights"]
        total_rows = 0

        for table_name in table_order:
            if table_name not in self.tables:
                self.logger.warning(f"Skipping {table_name} - no data file")
                continue

            table_paths = self.tables[table_name]
            data_files = table_paths if isinstance(table_paths, list) else [table_paths]
            table_rows = 0
            for data_file_raw in data_files:
                data_file = Path(data_file_raw)
                if not data_file.exists():
                    self.logger.warning(f"Skipping {table_name} - file not found: {data_file}")
                    continue

                table_rows += self._load_table_data(connection, table_name, data_file)
            total_rows += table_rows
            self.logger.info(f"Loaded {table_rows:,} rows into {table_name}")

        connection.commit()
        self.logger.info(f"Loaded {total_rows:,} total rows across {len(table_order)} tables")

    def _load_table_data(self, connection: DatabaseConnection, table_name: str, data_file: Path) -> int:
        schema = FLIGHT_SCHEMA[table_name]
        num_columns = len(schema["columns"])
        placeholders = ", ".join(["?" for _ in range(num_columns)])
        insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders})"

        rows_loaded = 0
        rows_skipped = 0
        with open(data_file, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) != num_columns:
                    rows_skipped += 1
                    continue
                connection.execute(insert_sql, row)
                rows_loaded += 1

        if rows_skipped:
            self.logger.warning(
                "Skipped %d malformed rows in %s (expected %d columns)",
                rows_skipped,
                table_name,
                num_columns,
            )
        return rows_loaded


from benchbox.core.hooks.benchmark_hooks import (
    BenchmarkHookRegistry,
    BenchmarkOptionSpec,
    parse_bool,
    parse_int,
)

BenchmarkHookRegistry.register_option_specs(
    "flightdata",
    BenchmarkOptionSpec(
        name="end_year",
        parser=parse_int,
        default=LAST_AVAILABLE_YEAR,
        help=f"Last year of flight data to include (default: {LAST_AVAILABLE_YEAR})",
        aliases=("end-year",),
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
    BenchmarkOptionSpec(
        name="allow_synthetic_fallback",
        parser=parse_bool,
        default=False,
        help="Allow synthetic months when a BTS download fails at SF >= 0.1",
        aliases=("allow-synthetic-fallback",),
    ),
    benchmark_class=FlightDataBenchmark,
)
