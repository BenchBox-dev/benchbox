# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from benchbox.core.execution_engine import ExecutionEngineReceipt

from benchbox.core.deprecation_notices import warn_deprecated_alias
from benchbox.utils.clock import elapsed_seconds, mono_time

try:
    import polars as pl
except ImportError:
    pl = None

from benchbox.platforms.base import DriverIsolationCapability, PlatformAdapter
from benchbox.platforms.base.no_constraint_mixin import NoConstraintEnforcementMixin
from benchbox.platforms.polars_compat import reader_rechunk_effective, reader_rechunk_option

logger = logging.getLogger(__name__)


class PolarsDataFrameContext:
    def __init__(self, adapter: PolarsAdapter):
        self._adapter = adapter
        self._tables: dict[str, pl.LazyFrame] = {}

    def register_table(self, name: str, df: pl.LazyFrame | pl.DataFrame) -> None:
        lf = df.lazy() if isinstance(df, pl.DataFrame) else df
        self._tables[name] = lf

    def unregister_table(self, name: str) -> None:
        if name in self._tables:
            del self._tables[name]

    def get_table(self, name: str) -> pl.LazyFrame | None:
        return self._tables.get(name)

    def get_tables(self) -> list[str]:
        return list(self._tables.keys())


class PolarsAdapter(NoConstraintEnforcementMixin, PlatformAdapter):
    plan_capture_phase_eligible = True

    driver_isolation_capability = DriverIsolationCapability.NOT_APPLICABLE
    supports_external_tables = True

    @property
    def platform_name(self) -> str:
        return "Polars"

    def get_target_dialect(self) -> str:
        return "dataframe"

    @staticmethod
    def add_cli_arguments(parser) -> None:
        polars_group = parser.add_argument_group("Polars Arguments")
        polars_group.add_argument(
            "--polars-execution-mode",
            type=str,
            choices=["lazy", "eager"],
            default="lazy",
            help="Execution mode: lazy (recommended) or eager",
        )
        polars_group.add_argument(
            "--polars-n-rows",
            type=int,
            default=None,
            help="Limit number of rows to read (for testing)",
        )
        polars_group.add_argument(
            "--polars-working-dir",
            type=str,
            help="Working directory for Polars data files",
        )
        polars_group.add_argument(
            "--polars-rechunk",
            action="store_true",
            default=True,
            help="Rechunk data for better memory layout (default: True)",
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        from pathlib import Path

        from benchbox.utils.database_naming import generate_database_filename
        from benchbox.utils.scale_factor import format_benchmark_name

        adapter_config = {}

        if config.get("working_dir"):
            adapter_config["working_dir"] = config["working_dir"]
        else:
            from benchbox.utils.path_utils import get_benchmark_runs_datagen_path

            if config.get("output_dir"):
                data_dir = Path(config["output_dir"]) / format_benchmark_name(
                    config["benchmark"], config["scale_factor"]
                )
            else:
                data_dir = get_benchmark_runs_datagen_path(config["benchmark"], config["scale_factor"])

            db_filename = generate_database_filename(
                benchmark_name=config["benchmark"],
                scale_factor=config["scale_factor"],
                platform="polars",
                tuning_config=config.get("tuning_config"),
            )

            working_dir = data_dir / db_filename
            adapter_config["working_dir"] = str(working_dir)
            working_dir.mkdir(parents=True, exist_ok=True)

        adapter_config["execution_mode"] = config.get("execution_mode", "lazy")

        adapter_config["n_rows"] = config.get("n_rows")

        adapter_config["rechunk"] = config.get("rechunk", True)

        adapter_config["execution_engine"] = config.get("execution_engine") or "default"

        adapter_config["force_recreate"] = config.get("force", False)

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
        if "streaming" in config:
            adapter_config["streaming"] = config["streaming"]

        return cls(**adapter_config)

    def resolve_execution_engine(self, requested: str) -> ExecutionEngineReceipt:
        from benchbox.core.execution_engine import platform_default_receipt

        return platform_default_receipt("polars", requested)

    def __init__(self, **config):
        super().__init__(**config)
        if pl is None:
            raise ImportError("Polars not installed. Install with: pip install polars")

        self.working_dir = Path(config.get("working_dir", "./polars_working"))
        self.execution_mode = config.get("execution_mode", "lazy")
        self.n_rows = config.get("n_rows")
        self.rechunk = config.get("rechunk", True)
        self._engine_receipt = self.resolve_execution_engine(config.get("execution_engine") or "default")
        if config.get("streaming", False):
            warn_deprecated_alias(
                "polars", "streaming", "no value ('streaming' has no effect on the SQL Polars adapter)"
            )

        self._table_schemas: dict[str, dict] = {}

        self.working_dir.mkdir(parents=True, exist_ok=True)

    def get_platform_info(self, connection: Any = None) -> dict[str, Any]:
        from dataclasses import asdict

        platform_info = {
            "platform_type": "polars",
            "platform_name": "Polars",
            "connection_mode": "in-memory",
            "configuration": {
                "working_dir": str(self.working_dir),
                "execution_mode": self.execution_mode,
                "execution_engine": asdict(self._engine_receipt),
                "n_rows_limit": self.n_rows,
                "rechunk": self.rechunk,
                "rechunk_effective": reader_rechunk_effective(self.rechunk),
                "result_cache_enabled": False,
            },
        }

        try:
            platform_info["client_library_version"] = pl.__version__
            platform_info["platform_version"] = pl.__version__
        except AttributeError:
            platform_info["client_library_version"] = None
            platform_info["platform_version"] = None

        return platform_info

    def create_connection(self, **connection_config) -> Any:
        self.log_operation_start("Polars connection")

        self.handle_existing_database(**connection_config)

        config_applied = []

        pl.enable_string_cache()
        config_applied.append("string_cache=enabled")

        n_threads = os.cpu_count() or 4
        config_applied.append(f"threads={n_threads}")

        config_applied.append(f"execution_mode={self.execution_mode}")

        self.log_very_verbose(f"Polars configuration: {', '.join(config_applied)}")

        ctx = PolarsDataFrameContext(self)

        self.log_operation_complete("Polars connection", details=f"Applied: {', '.join(config_applied)}")

        return ctx

    def create_schema(self, benchmark, connection: Any) -> float:
        start_time = mono_time()
        self.log_operation_start("Schema creation", f"benchmark: {benchmark.__class__.__name__}")

        enable_primary_keys, enable_foreign_keys = self._get_constraint_configuration()
        self._log_constraint_configuration(enable_primary_keys, enable_foreign_keys)

        if enable_primary_keys or enable_foreign_keys:
            self.log_verbose(
                "Polars does not enforce PRIMARY KEY or FOREIGN KEY constraints - "
                "schema will be created without constraints"
            )

        self._table_schemas = self._get_benchmark_schema(benchmark)

        duration = elapsed_seconds(start_time)
        self.log_operation_complete(
            "Schema creation",
            duration,
            f"Schema validated for {len(self._table_schemas)} tables",
        )
        return duration

    def _get_benchmark_schema(self, benchmark) -> dict[str, dict]:
        schemas = {}

        try:
            benchmark_schema = benchmark.get_schema()
        except (AttributeError, TypeError):
            self.log_verbose(
                f"Benchmark {benchmark.__class__.__name__} does not provide get_schema() method, "
                "will rely on schema inference during data loading"
            )
            return {}

        if not benchmark_schema:
            self.log_verbose("Benchmark returned empty schema, will rely on schema inference")
            return {}

        for table_name_key, table_def in benchmark_schema.items():
            table_name = table_name_key.lower()

            columns = []
            if isinstance(table_def, dict) and "columns" in table_def:
                for col in table_def["columns"]:
                    if isinstance(col, dict) and "name" in col:
                        col_type = col.get("type", "VARCHAR")
                        if not isinstance(col_type, str):
                            col_type = "VARCHAR"

                        columns.append(
                            {
                                "name": col["name"],
                                "type": col_type,
                            }
                        )

            if columns:
                schemas[table_name] = {"columns": columns}
                self.log_very_verbose(f"Extracted schema for {table_name}: {len(columns)} columns")

        return schemas

    def load_data(
        self, benchmark, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        from benchbox.platforms.base.data_loading import DataSourceResolver

        start_time = mono_time()
        self.log_operation_start("Data loading", f"mode: {self.execution_mode}")

        resolver = DataSourceResolver(
            platform_name=self.platform_name,
            table_mode=self.table_mode,
            platform_config=self.platform_config,
            requested_format=self.requested_table_format,
        )
        data_source = resolver.resolve(benchmark, data_dir)

        if not data_source or not data_source.tables:
            raise ValueError(f"No data files found in {data_dir}")

        table_stats = {}
        per_table_timings = {}

        for table_name, file_paths in data_source.tables.items():
            table_start = mono_time()

            valid_files = self._normalize_and_validate_file_paths(file_paths)

            if not valid_files:
                self.log_verbose(f"Skipping {table_name} - no valid data files")
                continue

            table_name_lower = table_name.lower()

            format_hint = data_source.table_formats.get(table_name) or data_source.table_formats.get(table_name_lower)
            row_count = self._load_table(connection, table_name_lower, valid_files, data_dir, format_hint=format_hint)

            table_duration = elapsed_seconds(table_start)
            table_stats[table_name_lower] = row_count
            per_table_timings[table_name_lower] = {"total_ms": table_duration * 1000}

            self.log_verbose(f"Loaded table {table_name_lower}: {row_count:,} rows in {table_duration:.2f}s")

        total_duration = elapsed_seconds(start_time)
        total_rows = sum(table_stats.values())

        self.log_operation_complete(
            "Data loading",
            total_duration,
            f"{total_rows:,} rows across {len(table_stats)} tables",
        )

        return table_stats, total_duration, per_table_timings

    def create_external_tables(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        return self.load_data(benchmark, connection, data_dir)

    def _detect_file_format(self, file_paths: list[Path]) -> tuple[str, str]:
        from benchbox.platforms.base.utils import detect_file_format

        format_info = detect_file_format(file_paths)
        format_type = "parquet" if format_info.format_type == "parquet" else "csv"
        return format_type, format_info.delimiter

    def _load_table(
        self,
        connection: PolarsDataFrameContext,
        table_name: str,
        file_paths: list[Path],
        data_dir: Path,
        format_hint: str | None = None,
    ) -> int:
        if format_hint == "parquet":
            format_type, delimiter = "parquet", ","
        elif format_hint == "tbl":
            format_type, delimiter = "csv", "|"
        elif format_hint == "csv":
            format_type, delimiter = "csv", ","
        else:
            format_type, delimiter = self._detect_file_format(file_paths)

        schema_info = self._table_schemas.get(table_name, {})
        columns = schema_info.get("columns", [])
        column_names = [col["name"] for col in columns] if columns else None

        self.log_very_verbose(f"Loading {table_name} from {len(file_paths)} file(s), format: {format_type}")

        if format_type == "parquet":
            lf = self._load_parquet(file_paths)
        else:
            lf = self._load_csv(file_paths, delimiter, column_names)

        connection.register_table(table_name, lf)

        row_count = lf.select(pl.len()).collect().item()

        return row_count

    def _load_parquet(self, file_paths: list[Path]) -> pl.LazyFrame:
        if len(file_paths) == 1:
            return pl.scan_parquet(file_paths[0], **reader_rechunk_option("scan_parquet", self.rechunk))

        parent_dir = file_paths[0].parent
        if all(f.parent == parent_dir for f in file_paths):
            pattern = str(parent_dir / "*.parquet")
            return pl.scan_parquet(pattern, **reader_rechunk_option("scan_parquet", self.rechunk))

        lfs = [pl.scan_parquet(f, **reader_rechunk_option("scan_parquet", self.rechunk)) for f in file_paths]
        return cast(pl.LazyFrame, pl.concat(lfs))

    def _load_csv(
        self,
        file_paths: list[Path],
        delimiter: str,
        column_names: list[str] | None,
    ) -> pl.LazyFrame:
        from benchbox.utils.file_format import (
            TRAILING_DUMMY_COLUMN,
            has_trailing_delimiter,
        )

        has_trailing = (
            bool(column_names) and bool(file_paths) and has_trailing_delimiter(file_paths[0], delimiter, column_names)
        )

        scan_kwargs: dict[str, Any] = {
            "separator": delimiter,
            "has_header": False,
            **reader_rechunk_option("scan_csv", self.rechunk),
            "ignore_errors": True,
        }

        if self.n_rows:
            scan_kwargs["n_rows"] = self.n_rows

        if column_names:
            if has_trailing:
                extended_names = column_names + [TRAILING_DUMMY_COLUMN]
                scan_kwargs["new_columns"] = extended_names
            else:
                scan_kwargs["new_columns"] = column_names

        if len(file_paths) == 1:
            lf = pl.scan_csv(file_paths[0], **scan_kwargs)
        else:
            parent_dir = file_paths[0].parent
            if all(f.parent == parent_dir for f in file_paths):
                ext = file_paths[0].suffix
                if ext.isdigit():
                    base_name = file_paths[0].stem.rsplit(".", 1)[0]
                    pattern = str(parent_dir / f"{base_name}*")
                else:
                    pattern = str(parent_dir / f"*{ext}")
                lf = pl.scan_csv(pattern, **scan_kwargs)
            else:
                lfs = [pl.scan_csv(f, **scan_kwargs) for f in file_paths]
                lf = pl.concat(lfs)

        if has_trailing and column_names:
            lf = lf.drop(TRAILING_DUMMY_COLUMN)

        return cast(pl.LazyFrame, lf)

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self.log_verbose(f"Polars configured for {benchmark_type} benchmark")

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
        raise NotImplementedError(
            "Polars SQL mode is not supported. Polars' SQL implementation has fundamental limitations "
            "(no implicit joins, limited subquery support) that make it incompatible with TPC benchmarks. "
            "Use 'polars-df' platform for DataFrame API execution, or use a SQL-native platform like "
            "'duckdb' or 'postgresql' for SQL benchmarks."
        )

    def check_database_exists(self, **connection_config) -> bool:
        working_dir = Path(connection_config.get("working_dir", self.working_dir))

        if not working_dir.exists():
            return False

        return any(working_dir.glob("*.parquet")) or any(working_dir.glob("*.csv"))

    def drop_database(self, **connection_config) -> None:
        import shutil

        working_dir = Path(connection_config.get("working_dir", self.working_dir))

        if working_dir.exists():
            self.logger.warning(f"Removing Polars working directory: {working_dir}")
            shutil.rmtree(working_dir)
            self.logger.warning("Polars working directory removed")

    def validate_platform_capabilities(self, benchmark_type: str):
        from benchbox.core.validation import ValidationResult

        errors = []
        warnings = []

        if pl is None:
            errors.append("Polars library not available - install with 'pip install polars'")
        else:
            try:
                version = pl.__version__
                self.log_very_verbose(f"Polars version: {version}")

                version_parts = version.split(".")
                if len(version_parts) >= 2:
                    major = int(version_parts[0])
                    minor = int(version_parts[1])
                    if major == 0 and minor < 20:
                        warnings.append(
                            f"Polars version {version} is older - consider upgrading for better performance"
                        )
            except (ValueError, AttributeError):
                warnings.append("Could not determine Polars version")

        warnings.append(
            "Polars SQL mode is not available. Use 'polars-df' platform for DataFrame API execution, "
            "or use a SQL-native platform like 'duckdb' for SQL benchmarks."
        )

        platform_info = {
            "platform": self.platform_name,
            "benchmark_type": benchmark_type,
            "dry_run_mode": self.dry_run_mode,
            "polars_available": pl is not None,
            "working_dir": str(self.working_dir),
            "execution_mode": self.execution_mode,
            "sql_mode": False,
        }

        if pl is not None:
            platform_info["polars_version"] = pl.__version__

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            details=platform_info,
        )

    def _get_existing_tables(self, connection) -> list[str]:
        try:
            if isinstance(connection, PolarsDataFrameContext):
                return [name.lower() for name in connection.get_tables()]
            return []
        except Exception as e:
            self.log_verbose(f"Error getting existing tables: {e}")
            return []

    def _validate_data_integrity(
        self, benchmark, connection, table_stats: dict[str, int]
    ) -> tuple[str, dict[str, Any]]:
        validation_details = {}

        try:
            accessible_tables = []
            inaccessible_tables = []

            for table_name in table_stats:
                try:
                    table = connection.get_table(table_name)
                    if table is not None:
                        accessible_tables.append(table_name)
                    else:
                        inaccessible_tables.append(table_name)
                except Exception as e:
                    self.log_verbose(f"Table {table_name} inaccessible: {e}")
                    inaccessible_tables.append(table_name)

            if inaccessible_tables:
                validation_details["inaccessible_tables"] = inaccessible_tables
                validation_details["constraints_enabled"] = False
                return "FAILED", validation_details
            else:
                validation_details["accessible_tables"] = accessible_tables
                validation_details["constraints_enabled"] = True
                return "PASSED", validation_details

        except Exception as e:
            validation_details["error"] = str(e)
            return "FAILED", validation_details
