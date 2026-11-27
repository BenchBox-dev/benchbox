from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    remote_manifest: dict[str, Any] | None = None


@dataclass
class ValidationSummary:
    total_validations: int
    passed_validations: int
    failed_validations: int
    warnings_count: int


@dataclass
class BenchmarkExpectations:
    expected_table_count: int = 0
    critical_tables: list[str] = field(default_factory=list)
    dimension_tables: list[str] = field(default_factory=list)
    fact_tables: list[str] = field(default_factory=list)
    validation_thresholds: dict[str, Any] = field(default_factory=dict)


class DataValidationEngine:
    BENCHMARK_EXPECTATIONS = {
        "tpcds": BenchmarkExpectations(
            expected_table_count=25,
            critical_tables=[
                "call_center",
                "catalog_page",
                "catalog_returns",
                "catalog_sales",
                "customer",
                "customer_address",
                "customer_demographics",
                "date_dim",
                "household_demographics",
                "income_band",
                "inventory",
                "item",
                "promotion",
                "reason",
                "ship_mode",
                "store",
                "store_returns",
                "store_sales",
                "time_dim",
                "warehouse",
                "web_page",
                "web_returns",
                "web_sales",
                "web_site",
                "dbgen_version",
            ],
            dimension_tables=[
                "call_center",
                "catalog_page",
                "customer",
                "customer_address",
                "customer_demographics",
                "date_dim",
                "household_demographics",
                "income_band",
                "item",
                "promotion",
                "reason",
                "ship_mode",
                "store",
                "time_dim",
                "warehouse",
                "web_page",
                "web_site",
            ],
            fact_tables=[
                "catalog_returns",
                "catalog_sales",
                "inventory",
                "store_returns",
                "store_sales",
                "web_returns",
                "web_sales",
            ],
            validation_thresholds={
                "min_file_size_bytes": 10,
                "min_row_count": 1,
                "critical_table_coverage": 1.0,
            },
        ),
        "tpch": BenchmarkExpectations(
            expected_table_count=8,
            critical_tables=[
                "customer",
                "lineitem",
                "nation",
                "orders",
                "part",
                "partsupp",
                "region",
                "supplier",
            ],
            dimension_tables=["customer", "nation", "part", "region", "supplier"],
            fact_tables=["lineitem", "orders", "partsupp"],
            validation_thresholds={
                "min_file_size_bytes": 10,
                "min_row_count": 1,
                "critical_table_coverage": 1.0,
            },
        ),
    }

    def __init__(self):
        pass

    def validate_preflight_conditions(
        self, benchmark_type: str, scale_factor: float, output_dir: Path
    ) -> ValidationResult:
        errors = []
        warnings = []

        if benchmark_type not in self.BENCHMARK_EXPECTATIONS:
            errors.append(f"Unsupported benchmark type: {benchmark_type}")

        if scale_factor <= 0:
            errors.append("Scale factor must be positive")

        if not output_dir.exists():
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"Cannot create output directory {output_dir}: {e}")
        elif not output_dir.is_dir():
            errors.append(f"Output path exists but is not a directory: {output_dir}")

        try:
            stat = output_dir.stat()
            if hasattr(stat, "st_size"):
                pass
        except Exception as e:
            warnings.append(f"Could not verify output directory status: {e}")

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            details={
                "benchmark_type": benchmark_type,
                "scale_factor": scale_factor,
                "output_dir": str(output_dir),
            },
        )

    def validate_generated_data(self, manifest_path: Path) -> ValidationResult:
        errors = []
        warnings = []

        if not manifest_path.exists():
            return ValidationResult(
                is_valid=False,
                errors=[f"Manifest file not found: {manifest_path}"],
                warnings=warnings,
            )

        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except json.JSONDecodeError as e:
            return ValidationResult(
                is_valid=False,
                errors=[f"Failed to parse manifest JSON: {e}"],
                warnings=warnings,
            )
        except Exception as e:
            return ValidationResult(
                is_valid=False,
                errors=[f"Failed to read manifest file: {e}"],
                warnings=warnings,
            )

        if not any((manifest.get("tables") or {}).values()):
            errors.append("Manifest lists no tables; data generation produced no usable output")

        manifest = {
            **manifest,
            "tables": {
                table: next(iter(entry["formats"].values()), []) if isinstance(entry, dict) else entry
                for table, entry in (manifest.get("tables") or {}).items()
            },
        }
        benchmark_type = manifest.get("benchmark", "").lower()
        expectations = self.BENCHMARK_EXPECTATIONS.get(benchmark_type)

        if not expectations:
            warnings.append(f"No validation expectations defined for benchmark: {benchmark_type}")
        else:
            excluded = {str(table) for table in manifest.get("excluded_tables") or ()}
            if excluded:
                kept = [table for table in expectations.critical_tables if table not in excluded]
                expectations = replace(
                    expectations,
                    expected_table_count=expectations.expected_table_count
                    - (len(expectations.critical_tables) - len(kept)),
                    critical_tables=kept,
                )
            table_errors, table_warnings = self._validate_table_count(manifest, expectations)
            errors.extend(table_errors)
            warnings.extend(table_warnings)

            critical_errors, critical_warnings = self._validate_critical_tables(manifest, expectations)
            errors.extend(critical_errors)
            warnings.extend(critical_warnings)

            file_errors, file_warnings = self._validate_file_sizes(manifest, expectations, manifest_path.parent)
            errors.extend(file_errors)
            warnings.extend(file_warnings)

            row_errors, row_warnings = self._validate_row_counts(manifest, expectations)
            errors.extend(row_errors)
            warnings.extend(row_warnings)

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            details={
                "manifest_path": str(manifest_path),
                "benchmark_type": benchmark_type,
                "table_count": len(manifest.get("tables", {})),
            },
        )

    def _validate_table_count(
        self, manifest: dict[str, Any], expectations: BenchmarkExpectations
    ) -> tuple[list[str], list[str]]:
        errors = []
        warnings = []

        tables = manifest.get("tables", {})
        actual_count = len(tables)
        expected_count = expectations.expected_table_count

        if actual_count != expected_count:
            missing_count = expected_count - actual_count
            if missing_count > 0:
                errors.append(f"Expected {expected_count} tables, found {actual_count} (missing {missing_count})")
            else:
                warnings.append(f"Expected {expected_count} tables, found {actual_count} (extra tables)")

        return errors, warnings

    def _validate_critical_tables(
        self, manifest: dict[str, Any], expectations: BenchmarkExpectations
    ) -> tuple[list[str], list[str]]:
        errors = []
        warnings = []

        tables = set(manifest.get("tables", {}).keys())
        critical_tables = set(expectations.critical_tables)
        missing_critical = critical_tables - tables

        if missing_critical:
            errors.append(f"Missing critical tables: {', '.join(sorted(missing_critical))}")

        return errors, warnings

    def _validate_file_sizes(
        self,
        manifest: dict[str, Any],
        expectations: BenchmarkExpectations,
        data_dir: Path,
    ) -> tuple[list[str], list[str]]:
        errors = []
        warnings = []

        min_size = expectations.validation_thresholds.get("min_file_size_bytes", 10)
        tables = manifest.get("tables", {})

        for table_name, file_list in tables.items():
            for file_entry in file_list:
                file_path = data_dir / file_entry["path"]
                expected_size = file_entry["size_bytes"]

                if not file_path.exists():
                    errors.append(f"Data file missing: {file_entry['path']}")
                    continue

                actual_size = file_path.stat().st_size

                if actual_size < min_size:
                    warnings.append(
                        f"Table {table_name} file size ({actual_size} bytes) below minimum size threshold ({min_size} bytes)"
                    )

                size_diff = abs(actual_size - expected_size)
                tolerance = max(1024, expected_size * 0.05)

                if size_diff > tolerance:
                    warnings.append(
                        f"Table {table_name} file size mismatch: expected {expected_size}, actual {actual_size}"
                    )

        return errors, warnings

    def _validate_row_counts(
        self, manifest: dict[str, Any], expectations: BenchmarkExpectations
    ) -> tuple[list[str], list[str]]:
        errors = []
        warnings = []

        min_rows = expectations.validation_thresholds.get("min_row_count", 1)
        tables = manifest.get("tables", {})

        for table_name, file_list in tables.items():
            total_rows = sum(file_entry.get("row_count", 0) for file_entry in file_list)

            if total_rows == 0:
                warnings.append(f"Table {table_name} has 0 rows - may indicate generation issue")
            elif total_rows < min_rows and table_name in expectations.critical_tables:
                warnings.append(f"Critical table {table_name} has only {total_rows} rows")

        return errors, warnings


class DatabaseValidationEngine:
    def __init__(self):
        pass

    def validate_loaded_data(self, connection: Any, benchmark_type: str, scale_factor: float) -> ValidationResult:
        errors = []
        warnings = []

        try:
            expectations = DataValidationEngine.BENCHMARK_EXPECTATIONS.get(benchmark_type)
            if not expectations:
                warnings.append(f"No validation expectations for benchmark: {benchmark_type}")
                expected_tables = []
            else:
                expected_tables = expectations.critical_tables

            actual_tables = self._get_table_list(connection)

            missing_tables = set(expected_tables) - set(actual_tables)
            if missing_tables:
                errors.append(f"Missing tables in database: {', '.join(sorted(missing_tables))}")

            for table in expected_tables:
                if table in actual_tables:
                    try:
                        row_count = self._get_table_row_count(connection, table)
                        if row_count == 0:
                            warnings.append(f"Table {table} has 0 rows")
                        elif row_count < 0:
                            warnings.append(f"Could not count rows in table {table}")
                    except Exception as e:
                        warnings.append(f"Error counting rows in table {table}: {e}")

        except Exception as e:
            errors.append(f"Database validation failed: {str(e)}")

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            details={
                "benchmark_type": benchmark_type,
                "scale_factor": scale_factor,
                "connection_type": type(connection).__name__,
            },
        )

    def _get_table_list(self, connection: Any) -> list[str]:
        cursor = connection.cursor()

        try:
            cursor.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema IN ('main', 'public', CURRENT_SCHEMA())
            """)
            return [row[0].lower() for row in cursor.fetchall()]
        except Exception:
            pass

        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            return [row[0].lower() for row in cursor.fetchall()]
        except Exception:
            pass

        try:
            cursor.execute("SHOW TABLES")
            return [row[0].lower() for row in cursor.fetchall()]
        except Exception:
            pass

        return []

    def _get_table_row_count(self, connection: Any, table_name: str) -> int:
        cursor = connection.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        result = cursor.fetchone()
        return result[0] if result else -1
