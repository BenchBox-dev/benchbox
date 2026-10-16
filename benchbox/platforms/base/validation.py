from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from benchbox.platforms.base.models import DatabaseValidationResult


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    database_empty: bool = False

    def add_error(self, error: str) -> None:
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)


class BaseValidator(ABC):
    def __init__(self, adapter: Any, connection_config: dict[str, Any]):
        self.adapter = adapter
        self.connection_config = connection_config

    @abstractmethod
    def validate(self) -> ValidationResult:
        pass


class ConnectionValidator(BaseValidator):
    @contextmanager
    def create_temporary_connection(self):
        self.adapter.get_database_path(**self.connection_config)

        if hasattr(self.adapter, "_create_direct_connection"):
            connection = self.adapter._create_direct_connection(**self.connection_config)
        else:
            self.adapter._validating_database = True
            try:
                connection = self.adapter.create_connection(**self.connection_config)
            finally:
                self.adapter._validating_database = False

        try:
            yield connection
        finally:
            self.adapter.close_connection(connection)

    def validate(self) -> ValidationResult:
        result = ValidationResult(is_valid=True)
        try:
            with self.create_temporary_connection():
                pass
        except Exception as e:
            result.add_error(f"Failed to establish connection: {str(e)}")
        return result


class TuningValidator(BaseValidator):
    def validate(self) -> ValidationResult:
        result = ValidationResult(is_valid=True)

        tuning_result = self.adapter._validate_database_tunings(**self.connection_config)
        for warning in tuning_result.warnings:
            result.add_warning(warning)
        if not tuning_result.is_valid:
            for error in tuning_result.errors[:3]:
                result.add_error(f"Tuning: {error}")
            if len(tuning_result.errors) > 3:
                result.add_error(f"Tuning: ... and {len(tuning_result.errors) - 3} more tuning errors")

        return result


class SchemaValidator(BaseValidator):
    def validate(self, connection: Any) -> ValidationResult:
        result = ValidationResult(is_valid=True)

        try:
            expected_tables = self._get_expected_tables()
            if not expected_tables:
                result.add_warning("Benchmark schema has no tables defined")
                return result

            existing_tables = self._get_existing_tables(connection)

            missing_tables = expected_tables - existing_tables
            extra_tables = existing_tables - expected_tables

            if missing_tables:
                result.add_error(f"Missing tables: {', '.join(sorted(missing_tables))}")
                result.database_empty = missing_tables == expected_tables

            if extra_tables:
                extra_tables = self._filter_system_tables(extra_tables)
                if extra_tables:
                    result.add_warning(f"Extra tables found: {', '.join(sorted(extra_tables))}")

        except ValueError as e:
            result.add_warning(f"Schema validation skipped: {str(e)}")
        except Exception as e:
            result.add_warning(f"Table validation failed: {str(e)}")

        return result

    def _get_expected_tables(self) -> set[str]:
        benchmark_instance = getattr(self.adapter, "benchmark_instance", None)
        if not benchmark_instance:
            raise ValueError("benchmark_instance not set on adapter - cannot validate schema")

        if not hasattr(benchmark_instance, "get_schema"):
            raise ValueError(f"benchmark_instance {type(benchmark_instance).__name__} has no get_schema method")

        schema = benchmark_instance.get_schema()
        if not isinstance(schema, dict):
            raise ValueError(f"benchmark schema is not a dict: {type(schema)}")

        tables = {t.lower() for t in schema}
        return tables

    def _get_existing_tables(self, connection: Any) -> set[str]:
        existing_table_list = self.adapter._get_existing_tables(connection)
        return {t.lower() for t in existing_table_list}

    def _filter_system_tables(self, tables: set[str]) -> set[str]:
        system_tables = {
            "benchbox_tuning_metadata",
            "sqlite_sequence",
        }
        return tables - system_tables


class RowCountStrategy(ABC):
    def __init__(self, scale_factor: float):
        self.scale_factor = scale_factor

    @abstractmethod
    def get_sample_tables(self, available_tables: set[str]) -> list[str]:
        pass

    @abstractmethod
    def get_expected_range(self, table: str) -> tuple[float, float] | None:
        pass


class TPCHRowCountStrategy(RowCountStrategy):
    def get_sample_tables(self, available_tables: set[str]) -> list[str]:
        return list(available_tables)

    def get_expected_range(self, table: str) -> tuple[float, float] | None:
        ranges = {
            "lineitem": (6000000 * self.scale_factor * 0.8, 6000000 * self.scale_factor * 1.2),
            "orders": (1500000 * self.scale_factor * 0.8, 1500000 * self.scale_factor * 1.2),
            "customer": (150000 * self.scale_factor * 0.8, 150000 * self.scale_factor * 1.2),
        }
        return ranges.get(table)


_TPCDS_SPEC_ROWS: dict[str, tuple[int, int, int]] = {
    "call_center": (6, 24, 30),
    "catalog_page": (11_718, 12_000, 20_400),
    "catalog_returns": (144_067, 1_439_749, 14_404_374),
    "catalog_sales": (1_441_548, 14_401_261, 143_997_065),
    "customer": (100_000, 500_000, 2_000_000),
    "customer_address": (50_000, 250_000, 1_000_000),
    "customer_demographics": (1_920_800, 1_920_800, 1_920_800),
    "date_dim": (73_049, 73_049, 73_049),
    "household_demographics": (7_200, 7_200, 7_200),
    "income_band": (20, 20, 20),
    "inventory": (11_745_000, 133_110_000, 399_330_000),
    "item": (18_000, 102_000, 204_000),
    "promotion": (300, 500, 1_000),
    "reason": (75, 75, 75),
    "ship_mode": (20, 20, 20),
    "store": (12, 102, 402),
    "store_returns": (287_514, 2_875_432, 28_795_080),
    "store_sales": (2_880_404, 28_800_991, 287_997_024),
    "time_dim": (86_400, 86_400, 86_400),
    "warehouse": (5, 10, 15),
    "web_page": (60, 200, 2_040),
    "web_returns": (71_763, 719_217, 7_197_670),
    "web_sales": (719_384, 7_197_566, 72_001_237),
    "web_site": (30, 42, 24),
}
_TPCDS_SPEC_SCALES = (1, 10, 100)
_TPCDS_LINEAR_TABLES = frozenset(
    {"store_sales", "catalog_sales", "web_sales", "store_returns", "catalog_returns", "web_returns"}
)
_TPCDS_FRACTIONAL_LINEAR_TABLES = _TPCDS_LINEAR_TABLES | {"customer"}


class TPCDSRowCountStrategy(RowCountStrategy):
    def get_sample_tables(self, available_tables: set[str]) -> list[str]:
        return list(available_tables)

    def get_expected_range(self, table: str) -> tuple[float, float] | None:
        name = table.lower()
        rows = _TPCDS_SPEC_ROWS.get(name)
        if rows is None:
            return None
        scale = float(self.scale_factor)
        if scale.is_integer() and int(scale) in _TPCDS_SPEC_SCALES:
            expected = float(rows[_TPCDS_SPEC_SCALES.index(int(scale))])
        elif name in _TPCDS_LINEAR_TABLES or (scale < 1 and name in _TPCDS_FRACTIONAL_LINEAR_TABLES):
            expected = rows[0] * scale
        else:
            return None
        return (expected * 0.8, expected * 1.2)


class SSBRowCountStrategy(RowCountStrategy):
    def get_sample_tables(self, available_tables: set[str]) -> list[str]:
        return list(available_tables)

    def get_expected_range(self, table: str) -> tuple[float, float] | None:
        ranges = {
            "lineorder": (6000000 * self.scale_factor * 0.8, 6000000 * self.scale_factor * 1.2),
            "customer": (30000 * self.scale_factor * 0.8, 30000 * self.scale_factor * 1.2),
        }
        return ranges.get(table)


class JoinOrderRowCountStrategy(RowCountStrategy):
    def __init__(self, scale_factor: float, benchmark_instance: Any):
        super().__init__(scale_factor)
        self.benchmark_instance = benchmark_instance

    def get_sample_tables(self, available_tables: set[str]) -> list[str]:
        return list(available_tables)

    def get_expected_range(self, table: str) -> tuple[float, float] | None:
        row_count_getter = getattr(self.benchmark_instance, "get_table_row_count", None)
        if row_count_getter is None:
            return None

        expected_rows = int(row_count_getter(table))
        if expected_rows <= 0:
            return None
        return (float(expected_rows), float(expected_rows))


class GenericRowCountStrategy(RowCountStrategy):
    def get_sample_tables(self, available_tables: set[str]) -> list[str]:
        return list(available_tables)

    def get_expected_range(self, table: str) -> tuple[float, float] | None:
        return None


class RowCountValidator(BaseValidator):
    def validate(self, connection: Any, expected_tables: set[str]) -> ValidationResult:
        result = ValidationResult(is_valid=True)

        getattr(self.adapter, "scale_factor", None)
        if not (hasattr(self.adapter, "scale_factor") and self.adapter.scale_factor):
            return result

        if not expected_tables:
            return result

        try:
            strategy = self._get_strategy()

            sample_tables = strategy.get_sample_tables(expected_tables)

            for table in sample_tables:
                self._validate_table_row_count(connection, table, strategy, result)

        except ValueError as e:
            result.add_warning(f"Row count validation skipped: {str(e)}")
        except Exception as e:
            result.add_warning(f"Row count validation failed: {str(e)}")

        return result

    def _get_strategy(self) -> RowCountStrategy:
        benchmark_instance = getattr(self.adapter, "benchmark_instance", None)
        if not benchmark_instance:
            raise ValueError("benchmark_instance not set on adapter - cannot determine row count strategy")

        scale = getattr(self.adapter, "scale_factor", 1.0)
        benchmark_name = getattr(
            benchmark_instance,
            "__class__",
            type(benchmark_instance),
        ).__name__.lower()

        if "tpch" in benchmark_name:
            return TPCHRowCountStrategy(scale)
        elif "tpcds" in benchmark_name:
            return TPCDSRowCountStrategy(scale)
        elif "ssb" in benchmark_name:
            return SSBRowCountStrategy(scale)
        elif benchmark_name == "joinorderbenchmark":
            return JoinOrderRowCountStrategy(scale, benchmark_instance)
        else:
            return GenericRowCountStrategy(scale)

    def _validate_table_row_count(
        self, connection: Any, table: str, strategy: RowCountStrategy, result: ValidationResult
    ) -> None:
        try:
            actual_rows = self.adapter.get_table_row_count(connection, table)

            expected_range = strategy.get_expected_range(table)

            if expected_range:
                min_rows, max_rows = expected_range
                if not (min_rows <= actual_rows <= max_rows):
                    result.add_error(
                        f"Table {table}: expected ~{int(min_rows):,}-{int(max_rows):,} rows, found {actual_rows:,}"
                    )
            elif actual_rows == 0:
                result.add_error(f"Table {table} is empty")

        except Exception as e:
            result.add_warning(f"Could not validate row count for table {table}: {str(e)}")


class DatabaseValidator:
    def __init__(self, adapter: Any, connection_config: dict[str, Any]):
        self.adapter = adapter
        self.connection_config = connection_config

        self.connection_validator = ConnectionValidator(adapter, connection_config)
        self.tuning_validator = TuningValidator(adapter, connection_config)
        self.schema_validator = SchemaValidator(adapter, connection_config)
        self.row_count_validator = RowCountValidator(adapter, connection_config)

    def validate(self) -> DatabaseValidationResult:
        self.adapter.log_operation_start(
            "Database compatibility validation", "Checking schema, data, and tuning compatibility"
        )

        benchmark_instance = getattr(self.adapter, "benchmark_instance", None)
        if benchmark_instance:
            self.adapter.log_very_verbose(f"benchmark_instance available: {type(benchmark_instance).__name__}")
        else:
            self.adapter.log_very_verbose("benchmark_instance NOT SET - validation may fail")

        issues = []
        warnings = []
        tuning_valid = None
        tables_valid = None
        row_counts_valid = None
        database_empty = False

        try:
            with self.connection_validator.create_temporary_connection() as connection:
                tuning_result = self.tuning_validator.validate()
                tuning_valid = tuning_result.is_valid
                issues.extend(tuning_result.errors)
                warnings.extend(tuning_result.warnings)

                schema_result = self.schema_validator.validate(connection)
                tables_valid = schema_result.is_valid
                database_empty = schema_result.database_empty
                issues.extend(schema_result.errors)
                warnings.extend(schema_result.warnings)

                if tables_valid:
                    try:
                        expected_tables = self.schema_validator._get_expected_tables()
                    except ValueError:
                        expected_tables = set()
                    row_count_result = self.row_count_validator.validate(connection, expected_tables)
                    row_counts_valid = row_count_result.is_valid
                    issues.extend(row_count_result.errors)
                    warnings.extend(row_count_result.warnings)

        except Exception as e:
            issues.append(f"Database validation failed: {str(e)}")
            return DatabaseValidationResult(is_valid=False, can_reuse=False, issues=issues, warnings=warnings)

        is_valid, can_reuse = self._determine_validity(tuning_valid, tables_valid, row_counts_valid)

        status = "valid" if is_valid else ("reusable" if can_reuse else "invalid")
        self.adapter.log_operation_complete(
            "Database compatibility validation",
            details=f"Status: {status}, issues: {len(issues)}, warnings: {len(warnings)}",
        )

        return DatabaseValidationResult(
            is_valid=is_valid,
            can_reuse=can_reuse,
            issues=issues,
            warnings=warnings,
            tuning_valid=tuning_valid,
            tables_valid=tables_valid,
            row_counts_valid=row_counts_valid,
            database_empty=database_empty,
        )

    def _determine_validity(
        self, tuning_valid: bool | None, tables_valid: bool | None, row_counts_valid: bool | None
    ) -> tuple[bool, bool]:
        has_benchmark = hasattr(self.adapter, "benchmark_instance") and self.adapter.benchmark_instance
        if tables_valid is None and not has_benchmark:
            return False, False

        is_valid = (
            (tuning_valid is None or tuning_valid)
            and (tables_valid is None or tables_valid)
            and (row_counts_valid is None or row_counts_valid)
        )

        can_reuse = bool(is_valid or (tables_valid and (tuning_valid is None or tuning_valid)))

        return is_valid, can_reuse


__all__ = [
    "ValidationResult",
    "BaseValidator",
    "ConnectionValidator",
    "TuningValidator",
    "SchemaValidator",
    "RowCountValidator",
    "RowCountStrategy",
    "TPCHRowCountStrategy",
    "TPCDSRowCountStrategy",
    "SSBRowCountStrategy",
    "GenericRowCountStrategy",
    "DatabaseValidator",
]
