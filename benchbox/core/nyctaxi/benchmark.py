# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import csv
import importlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.nyctaxi.downloader import (
    FHVDataDownloader,
    GreenTaxiDataDownloader,
    HVFHVDataDownloader,
    NYCTaxiDataDownloader,
)
from benchbox.core.nyctaxi.queries import NYCTaxiQueryManager
from benchbox.core.nyctaxi.schema import (
    NYC_TAXI_SCHEMA,
    TaxiType,
    get_create_tables_sql,
)
from benchbox.core.query_catalog_base import TranslatableQueryMixin
from benchbox.utils.compression_mixin import extract_compression_kwargs
from benchbox.utils.datagen_manifest import DataGenerationManifest, resolve_compression_metadata
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path

if TYPE_CHECKING:
    from benchbox.core.connection import DatabaseConnection


_NYCTAXI_DOW_RE = re.compile(r"EXTRACT\s*\(\s*DOW\s+FROM\s+([^()]+)\)", re.IGNORECASE)
_NYCTAXI_EPOCH_DIFF_RE = re.compile(
    r"EXTRACT\s*\(\s*EPOCH\s+FROM\s+\(\s*([^()]+?)\s*-\s*([^()]+?)\s*\)\s*\)",
    re.IGNORECASE,
)
_NYCTAXI_SF_EPOCH_RE = re.compile(
    r"DATE_PART\s*\(\s*EPOCH\s*,\s*\(\s*([^()]+?)\s*-\s*([^()]+?)\s*\)\s*\)",
    re.IGNORECASE,
)

_NYCTAXI_TRANSLATED_DIALECTS = ("bigquery", "snowflake", "databricks", "spark")

_NYCTAXI_SUPPORTED_DIALECTS = (
    "postgres",
    "postgresql",
    "duckdb",
    *_NYCTAXI_TRANSLATED_DIALECTS,
    "clickhouse",
    "starrocks",
)


class NYCTaxiBenchmark(GeneratorOutputDirMixin, TranslatableQueryMixin, BaseBenchmark):
    AVAILABLE_YEARS = list(range(2019, 2026))

    OUTPUT_DIR_GENERATOR_ATTRS = ("downloader", "green_downloader", "hvfhv_downloader", "fhv_downloader")

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Union[str, Path] | None = None,
        year: int = 2019,
        months: list[int] | None = None,
        seed: int | None = None,
        verbose: int | bool = 0,
        quiet: bool = False,
        force_regenerate: bool = False,
        taxi_types: list[TaxiType] | None = None,
        **kwargs: Any,
    ) -> None:
        if year not in self.AVAILABLE_YEARS:
            raise ValueError(f"year must be in {self.AVAILABLE_YEARS[0]}-{self.AVAILABLE_YEARS[-1]}, got {year}")

        resolved_output_dir = (
            Path(output_dir) if output_dir else get_benchmark_runs_datagen_path("nyctaxi", scale_factor)
        )
        super().__init__(
            scale_factor=scale_factor,
            output_dir=resolved_output_dir,
            verbose=verbose,
            quiet=quiet,
            force_regenerate=force_regenerate,
            **kwargs,
        )

        self.year = year
        self.months = months
        self.seed = seed
        self.force_regenerate = force_regenerate
        self.taxi_types: list[TaxiType] = taxi_types if taxi_types is not None else [TaxiType.YELLOW]

        self.csv_has_header: bool = True

        self._name = "NYC Taxi OLAP"
        self._version = "1.0"
        self._description = "NYC Taxi & Limousine Commission trip data for OLAP analytics"

        self.logger = logging.getLogger("benchbox.core.nyctaxi.benchmark")
        compression_kwargs = extract_compression_kwargs(kwargs)

        self.downloader = NYCTaxiDataDownloader(
            scale_factor=scale_factor,
            output_dir=self.output_dir,
            year=year,
            months=months,
            seed=seed,
            verbose=verbose,
            quiet=quiet,
            force_redownload=force_regenerate,
            **compression_kwargs,
        )

        self.green_downloader: GreenTaxiDataDownloader | None = None
        self.hvfhv_downloader: HVFHVDataDownloader | None = None
        self.fhv_downloader: FHVDataDownloader | None = None

        if TaxiType.GREEN in self.taxi_types:
            self.green_downloader = GreenTaxiDataDownloader(
                scale_factor=scale_factor,
                output_dir=self.output_dir,
                year=year,
                months=months,
                seed=seed,
                verbose=verbose,
                quiet=quiet,
                force_redownload=force_regenerate,
                **compression_kwargs,
            )

        if TaxiType.HVFHV in self.taxi_types:
            self.hvfhv_downloader = HVFHVDataDownloader(
                scale_factor=scale_factor,
                output_dir=self.output_dir,
                year=year,
                months=months,
                seed=seed,
                verbose=verbose,
                quiet=quiet,
                force_redownload=force_regenerate,
                **compression_kwargs,
            )

        if TaxiType.FHV in self.taxi_types:
            self.fhv_downloader = FHVDataDownloader(
                scale_factor=scale_factor,
                output_dir=self.output_dir,
                year=year,
                months=months,
                seed=seed,
                verbose=verbose,
                quiet=quiet,
                force_redownload=force_regenerate,
                **compression_kwargs,
            )

        if months:
            start_month = min(months)
            end_month = max(months)
        else:
            start_month = 1
            end_month = 12

        import calendar

        start_date = datetime(year, start_month, 1)
        _, last_day = calendar.monthrange(year, end_month)
        end_date = datetime(year, end_month, last_day)

        self.query_manager = NYCTaxiQueryManager(
            start_date=start_date,
            end_date=end_date,
            seed=seed,
            include_green_queries=TaxiType.GREEN in self.taxi_types,
            include_hvfhv_queries=TaxiType.HVFHV in self.taxi_types,
            include_fhv_queries=TaxiType.FHV in self.taxi_types,
            include_cross_type_queries=(TaxiType.GREEN in self.taxi_types and TaxiType.HVFHV in self.taxi_types),
        )

        self.tables: dict[str, Path] = {}

    def generate_data(self) -> list[Union[str, Path]]:
        self.log_verbose(
            f"Generating NYC Taxi data (SF={self.scale_factor}, types={[t.value for t in self.taxi_types]})"
        )

        self.tables = self.downloader.download()

        if self.verbose_enabled:
            stats = self.downloader.get_download_stats()
            self.logger.info(f"Data ready: year={stats['year']}, sample_rate={stats['sample_rate']:.4f}")

        if self.green_downloader is not None:
            self.log_verbose("Downloading Green Taxi data...")
            green_path = self.green_downloader.download()
            self.tables["green_trips"] = green_path

        if self.hvfhv_downloader is not None:
            self.log_verbose("Downloading HVFHV data...")
            hvfhv_path = self.hvfhv_downloader.download()
            self.tables["hvfhv_trips"] = hvfhv_path

        if self.fhv_downloader is not None:
            self.log_verbose("Downloading FHV data...")
            fhv_path = self.fhv_downloader.download()
            self.tables["fhv_trips"] = fhv_path

        self._write_manifest()
        return list(self.tables.values())

    def _write_manifest(self) -> None:
        if not self.tables:
            return

        yellow_stats = self.downloader.get_download_stats()
        row_counts = dict(yellow_stats.get("row_counts", {}))

        if self.green_downloader is not None:
            row_counts.update(self.green_downloader.get_download_stats().get("row_counts", {}))

        if self.hvfhv_downloader is not None:
            row_counts.update(self.hvfhv_downloader.get_download_stats().get("row_counts", {}))

        if self.fhv_downloader is not None:
            row_counts.update(self.fhv_downloader.get_download_stats().get("row_counts", {}))

        manifest = DataGenerationManifest(
            output_dir=self.output_dir,
            benchmark="nyctaxi",
            scale_factor=self.scale_factor,
            compression=resolve_compression_metadata(self.downloader),
            parallel=1,
            seed=self.seed,
            extra_metadata={
                "source_provenance": {
                    "trips": self.downloader.source_provenance(),
                    **(
                        {"green_trips": self.green_downloader.source_provenance()}
                        if self.green_downloader is not None
                        else {}
                    ),
                    **(
                        {"hvfhv_trips": self.hvfhv_downloader.source_provenance()}
                        if self.hvfhv_downloader is not None
                        else {}
                    ),
                    **(
                        {"fhv_trips": self.fhv_downloader.source_provenance()}
                        if self.fhv_downloader is not None
                        else {}
                    ),
                }
            },
        )

        for table, path in self.tables.items():
            manifest.add_entry(
                table,
                path,
                row_count=row_counts.get(table, 0),
                metadata={
                    "csv_delimiter": ",",
                    "csv_has_header": True,
                    "csv_null_marker": "",
                    "csv_normalize_booleans": False,
                },
            )

        manifest.write()

    def supported_dialects(self) -> list[str]:
        return list(_NYCTAXI_SUPPORTED_DIALECTS)

    def translate_query_text(self, query_text: str, target_dialect: str) -> str:
        d = target_dialect.lower()
        if "bigquery" in d:
            query_text = _NYCTAXI_DOW_RE.sub(r"(EXTRACT(DAYOFWEEK FROM \1) - 1)", query_text)
            query_text = _NYCTAXI_EPOCH_DIFF_RE.sub(
                r"(UNIX_SECONDS(TIMESTAMP(\1)) - UNIX_SECONDS(TIMESTAMP(\2)))", query_text
            )
        elif "databricks" in d or "spark" in d:
            query_text = _NYCTAXI_DOW_RE.sub(r"(DAYOFWEEK(\1) - 1)", query_text)
            query_text = _NYCTAXI_EPOCH_DIFF_RE.sub(r"(UNIX_TIMESTAMP(\1) - UNIX_TIMESTAMP(\2))", query_text)
        query_text = super().translate_query_text(query_text, target_dialect)
        if "snowflake" in d:
            query_text = _NYCTAXI_SF_EPOCH_RE.sub(r"(DATEDIFF(second, \2, \1))", query_text)
        return query_text

    def get_queries(self, dialect: str | None = None) -> dict[str, str]:
        importlib.import_module("benchbox.sql_compat.rules.query_source.nyctaxi_variants")
        from benchbox.sql_compat.actions import CompatAction
        from benchbox.sql_compat.context import CompatibilityContext, Phase
        from benchbox.sql_compat.registry import REGISTRY
        from benchbox.sql_compat.rules.query_source.nyctaxi_variants import (
            CLICKHOUSE_FHV_BASE_VOLUME_SQL,
            CLICKHOUSE_RUSH_HOUR_SQL,
            CLICKHOUSE_TRIP_DURATION_SQL,
            CLICKHOUSE_TRIPS_BY_DOW_SQL,
            CLICKHOUSE_WEEKDAY_WEEKEND_SQL,
            STARROCKS_FHV_BASE_VOLUME_SQL,
            STARROCKS_RUSH_HOUR_SQL,
            STARROCKS_TRIP_DURATION_SQL,
            STARROCKS_TRIPS_BY_DOW_SQL,
            STARROCKS_WEEKDAY_WEEKEND_SQL,
        )

        queries = self.query_manager.get_queries()
        if not dialect:
            return queries

        d = dialect.lower()
        if any(platform in d for platform in _NYCTAXI_TRANSLATED_DIALECTS):
            queries = {qid: self.translate_query_text(sql, dialect) for qid, sql in queries.items()}
        qm = self.query_manager
        _variants: dict[str, dict[str, str]] = {
            "starrocks": {
                "trips-by-day-of-week": STARROCKS_TRIPS_BY_DOW_SQL,
                "weekday-weekend-comparison": STARROCKS_WEEKDAY_WEEKEND_SQL,
                "rush-hour-analysis": STARROCKS_RUSH_HOUR_SQL,
                "trip-duration-analysis": STARROCKS_TRIP_DURATION_SQL,
                "fhv-base-volume": STARROCKS_FHV_BASE_VOLUME_SQL,
            },
            "clickhouse": {
                "trips-by-day-of-week": CLICKHOUSE_TRIPS_BY_DOW_SQL,
                "weekday-weekend-comparison": CLICKHOUSE_WEEKDAY_WEEKEND_SQL,
                "rush-hour-analysis": CLICKHOUSE_RUSH_HOUR_SQL,
                "trip-duration-analysis": CLICKHOUSE_TRIP_DURATION_SQL,
                "fhv-base-volume": CLICKHOUSE_FHV_BASE_VOLUME_SQL,
            },
        }
        for platform, platform_variants in _variants.items():
            if platform not in d:
                continue
            for query_id, legacy_sql in platform_variants.items():
                if query_id not in queries:
                    continue
                ctx = CompatibilityContext(
                    platform=platform,
                    platform_version=None,
                    benchmark="nyctaxi",
                    query_id=query_id,
                    phase=Phase.QUERY_SOURCE,
                    mode="sql",
                    dialect=dialect,
                )
                registry_decision = REGISTRY.resolve(ctx)
                if registry_decision is not None:
                    if registry_decision.action is CompatAction.SELECT_VARIANT:
                        variant_template = registry_decision.payload.variant_sql
                    else:
                        continue
                else:
                    variant_template = legacy_sql
                query_def = qm._active_queries[query_id]
                params = qm._generate_params(query_def)
                queries[query_id] = variant_template.format(**params)
        return queries

    def get_query(
        self,
        query_id: Union[int, str],
        *,
        params: dict[str, Any] | None = None,
        **kwargs,
    ) -> str:
        query_key = str(query_id)
        return self.query_manager.get_query(query_key, params)

    def get_schema(self) -> dict[str, dict[str, Any]]:
        active_tables = self._get_active_tables()
        return {k: v for k, v in NYC_TAXI_SCHEMA.items() if k in active_tables}

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
            taxi_types=self.taxi_types,
        )

    def get_benchmark_info(self) -> dict[str, Any]:
        return {
            "name": "NYC Taxi OLAP",
            "description": "NYC Taxi & Limousine Commission trip data for OLAP analytics",
            "reference": "https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page",
            "version": "1.0",
            "scale_factor": self.scale_factor,
            "year": self.year,
            "months": self.months or list(range(1, 13)),
            "num_queries": self.query_manager.get_query_count(),
            "query_categories": self.query_manager.get_categories(),
            "tables": self._get_active_tables(),
            "taxi_types": [t.value for t in self.taxi_types],
            "data_type": "real",
            "dimensions": {
                "temporal": "pickup/dropoff timestamps",
                "geographic": "TLC taxi zones",
                "financial": "fares, tips, surcharges",
            },
        }

    def _get_active_tables(self) -> list[str]:
        tables = ["taxi_zones", "trips"]
        if TaxiType.GREEN in self.taxi_types:
            tables.append("green_trips")
        if TaxiType.HVFHV in self.taxi_types:
            tables.append("hvfhv_trips")
        if TaxiType.FHV in self.taxi_types:
            tables.append("fhv_trips")
        return tables

    def get_query_info(self, query_id: str) -> dict[str, Any]:
        return self.query_manager.get_query_info(query_id)

    def get_queries_by_category(self, category: str) -> list[str]:
        return self.query_manager.get_queries_by_category(category)

    def get_download_stats(self) -> dict:
        return self.downloader.get_download_stats()

    def _load_data(self, connection: DatabaseConnection) -> None:
        logger = logging.getLogger(__name__)

        if not self.tables:
            raise ValueError("No data has been generated. Call generate_data() first.")

        logger.info("Loading NYC Taxi data into database...")

        try:
            schema_sql = self.get_create_tables_sql()
            if ";" in schema_sql:
                statements = [stmt.strip() for stmt in schema_sql.split(";") if stmt.strip()]
                for statement in statements:
                    connection.execute(statement)
            else:
                connection.execute(schema_sql)
            connection.commit()
            logger.info("Created NYC Taxi database schema")
        except Exception as e:
            logger.error(f"Failed to create database schema: {e}")
            raise

        total_rows = 0
        loaded_tables = 0

        table_order = ["taxi_zones", "trips"]
        if TaxiType.GREEN in self.taxi_types:
            table_order.append("green_trips")
        if TaxiType.HVFHV in self.taxi_types:
            table_order.append("hvfhv_trips")
        if TaxiType.FHV in self.taxi_types:
            table_order.append("fhv_trips")

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
        if table_name not in NYC_TAXI_SCHEMA:
            raise ValueError(f"Unknown table: {table_name}")
        table_schema = NYC_TAXI_SCHEMA[table_name]
        column_names = list(table_schema["columns"].keys())
        num_columns = len(column_names)

        placeholders = ", ".join(["?" for _ in range(num_columns)])
        insert_sql = f"INSERT INTO {table_name} VALUES ({placeholders})"

        rows_loaded = 0

        with open(data_file, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)

            rows_skipped = 0
            for row in reader:
                if len(row) != num_columns:
                    rows_skipped += 1
                    continue

                connection.execute(insert_sql, row)
                rows_loaded += 1

        if rows_skipped:
            logging.getLogger(__name__).warning(
                "Skipped %d malformed rows in %s (expected %d columns)",
                rows_skipped,
                table_name,
                num_columns,
            )
        return rows_loaded


from benchbox.core.hooks.benchmark_hooks import (
    BenchmarkHookRegistry,
    BenchmarkOptionSpec,
    parse_enum_list,
    parse_int,
    parse_int_list,
)

BenchmarkHookRegistry.register_option_specs(
    "nyctaxi",
    BenchmarkOptionSpec(
        name="taxi_types",
        parser=parse_enum_list(TaxiType),
        help="Taxi data types to load (yellow,green,hvfhv,fhv)",
        aliases=("taxi-types",),
    ),
    BenchmarkOptionSpec(
        name="year",
        parser=parse_int,
        default=2019,
        help="Year of TLC data (2019-2025)",
    ),
    BenchmarkOptionSpec(
        name="months",
        parser=parse_int_list,
        help="Months to include, comma-separated (1-12)",
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
    benchmark_class=NYCTaxiBenchmark,
)
