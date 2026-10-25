from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from benchbox.core.results.result_factory import total_ms_or_zero
from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.platforms.base.models import (
        DataGenerationPhase,
        DataLoadingPhase,
        SchemaCreationPhase,
        ValidationPhase,
    )

try:
    from benchbox.platforms.base.models import (
        DataGenerationPhase,
        DataLoadingPhase,
        SchemaCreationPhase,
        TableCreationStats,
        TableGenerationStats,
        TableLoadingStats,
        ValidationPhase,
    )
except ImportError:  # pragma: no cover
    DataGenerationPhase = None
    DataLoadingPhase = None
    SchemaCreationPhase = None
    TableCreationStats = None
    TableGenerationStats = None
    TableLoadingStats = None
    ValidationPhase = None


def _extract_table_names(raw_table_names: Any) -> list[str]:
    if raw_table_names is None:
        return []
    if isinstance(raw_table_names, dict):
        return [str(name) for name in raw_table_names.keys()]
    if isinstance(raw_table_names, str):
        return [raw_table_names]
    if isinstance(raw_table_names, (list, tuple, set)):
        return [str(name) for name in raw_table_names]
    if hasattr(raw_table_names, "keys") and callable(raw_table_names.keys):
        try:
            return [str(name) for name in raw_table_names.keys()]
        except Exception:
            return []
    return []


def _resolve_benchmark_table_names(benchmark: Any) -> list[str]:
    if hasattr(benchmark, "get_table_names") and callable(benchmark.get_table_names):
        try:
            names = _extract_table_names(benchmark.get_table_names())
            if names:
                return names
        except Exception:
            pass
    if hasattr(benchmark, "tables"):
        names = _extract_table_names(benchmark.tables)
        if names:
            return names
    return []


class PhaseTrackingMixin:
    logger: logging.Logger

    def _create_enhanced_data_generation_phase(self, benchmark) -> DataGenerationPhase | None:
        if not hasattr(benchmark, "tables") and not hasattr(getattr(benchmark, "_impl", None), "tables"):
            return None

        start_time = mono_time()
        tables_dict = benchmark.tables if hasattr(benchmark, "tables") else getattr(benchmark._impl, "tables", {})

        if not tables_dict or not hasattr(tables_dict, "items"):
            return None

        per_table_stats = {}
        total_rows = 0
        total_bytes = 0
        tables_generated = 0

        try:
            table_items = tables_dict.items()
            if not hasattr(table_items, "__iter__"):
                return None
            try:
                iter(table_items)
            except (TypeError, AttributeError):
                return None
        except (AttributeError, TypeError):
            return None

        try:
            for table_name, table_data in table_items:
                table_start = mono_time()
                try:
                    if hasattr(table_data, "__iter__") and not isinstance(table_data, str):
                        rows = list(table_data)
                        row_count = len(rows)

                        if rows:
                            avg_row_size = len(str(rows[0])) if rows else 50
                            estimated_bytes = row_count * avg_row_size
                        else:
                            estimated_bytes = 0

                        per_table_stats[table_name] = TableGenerationStats(
                            generation_time_ms=int(elapsed_seconds(table_start) * 1000),
                            status="SUCCESS",
                            rows_generated=row_count,
                            data_size_bytes=estimated_bytes,
                            file_path=f"{table_name}.tbl",
                        )

                        total_rows += row_count
                        total_bytes += estimated_bytes
                        tables_generated += 1

                except Exception as e:
                    per_table_stats[table_name] = TableGenerationStats(
                        generation_time_ms=int(elapsed_seconds(table_start) * 1000),
                        status="FAILED",
                        rows_generated=0,
                        data_size_bytes=0,
                        file_path=f"{table_name}.tbl",
                        error_type="GENERATION_ERROR",
                        error_message=str(e),
                        error_timestamp=datetime.now().isoformat(),
                    )
        except (TypeError, AttributeError):
            return None

        overall_status = "SUCCESS"
        if any(stats.status == "FAILED" for stats in per_table_stats.values()):
            overall_status = "PARTIAL_FAILURE" if tables_generated > 0 else "FAILED"

        return DataGenerationPhase(
            duration_ms=int(elapsed_seconds(start_time) * 1000),
            status=overall_status,
            tables_generated=tables_generated,
            total_rows_generated=total_rows,
            total_data_size_bytes=total_bytes,
            per_table_stats=per_table_stats,
        )

    def _create_enhanced_schema_creation_phase(
        self, benchmark, connection: Any, schema_creation_time: float
    ) -> SchemaCreationPhase:
        duration_ms = int(schema_creation_time * 1000)
        table_names = _resolve_benchmark_table_names(benchmark)
        table_count = len(table_names)

        per_table_creation = {}
        if table_count > 0:
            estimated_time_per_table = max(1, duration_ms // table_count)
            for table_name in table_names:
                per_table_creation[table_name] = TableCreationStats(
                    creation_time_ms=estimated_time_per_table,
                    status="SUCCESS",
                    constraints_applied=1,
                    indexes_created=1,
                )

        return SchemaCreationPhase(
            duration_ms=duration_ms,
            status="SUCCESS",
            tables_created=table_count,
            constraints_applied=table_count,
            indexes_created=table_count,
            per_table_creation=per_table_creation,
        )

    def _create_enhanced_data_loading_phase(
        self, table_stats: dict[str, int], loading_time: float, per_table_timings: dict[str, Any] | None = None
    ) -> DataLoadingPhase:
        duration_ms = int(loading_time * 1000)

        per_table_loading = {}
        total_rows = sum(table_stats.values())

        timings = per_table_timings if isinstance(per_table_timings, dict) else {}
        if timings:
            for table_name, row_count in table_stats.items():
                actual_time_ms = total_ms_or_zero(timings.get(table_name, {}))
                per_table_loading[table_name] = TableLoadingStats(
                    rows=row_count, load_time_ms=int(actual_time_ms), status="SUCCESS"
                )
        else:
            time_per_row = duration_ms / max(1, total_rows)
            for table_name, row_count in table_stats.items():
                proportional_time = int(row_count * time_per_row)
                per_table_loading[table_name] = TableLoadingStats(
                    rows=row_count, load_time_ms=proportional_time, status="SUCCESS"
                )

        return DataLoadingPhase(
            duration_ms=duration_ms,
            status="SUCCESS",
            total_rows_loaded=total_rows,
            tables_loaded=len(table_stats),
            per_table_stats=per_table_loading,
        )

    def _create_enhanced_validation_phase(self, benchmark=None, connection=None, table_stats=None) -> ValidationPhase:
        start_time = mono_time()

        validation_details = {
            "row_count_matches": True,
            "schema_valid": True,
            "constraints_enabled": True,
        }

        row_count_status = "PASSED"
        schema_status = "PASSED"
        integrity_status = "PASSED"

        if benchmark and connection and table_stats is not None:
            row_count_status, row_validation_details = self._validate_table_row_counts(benchmark, table_stats)
            validation_details.update(row_validation_details)

            schema_status, schema_validation_details = self._validate_schema_integrity(benchmark, connection)
            validation_details.update(schema_validation_details)

            integrity_status, integrity_validation_details = self._validate_data_integrity(
                benchmark, connection, table_stats
            )
            validation_details.update(integrity_validation_details)

        duration_ms = int(elapsed_seconds(start_time) * 1000)

        return ValidationPhase(
            duration_ms=max(50, duration_ms),
            row_count_validation=row_count_status,
            schema_validation=schema_status,
            data_integrity_checks=integrity_status,
            validation_details=validation_details,
        )

    def _validate_table_row_counts(self, benchmark, table_stats: dict[str, int]) -> tuple[str, dict[str, Any]]:
        validation_details = {}

        expected_row_counts = self._get_expected_row_counts(benchmark)

        failed_tables = []
        empty_tables = []

        for table_name, actual_rows in table_stats.items():
            if actual_rows == 0:
                empty_tables.append(table_name)
                continue

            if (
                expected_row_counts
                and hasattr(expected_row_counts, "__contains__")
                and table_name in expected_row_counts
            ):
                min_expected = expected_row_counts[table_name]
                if actual_rows < min_expected:
                    failed_tables.append(
                        {
                            "table": table_name,
                            "actual": actual_rows,
                            "expected_minimum": min_expected,
                        }
                    )

        if empty_tables:
            status = "FAILED"
            validation_details["empty_tables"] = empty_tables
            validation_details["row_count_matches"] = False
        elif failed_tables:
            status = "PARTIAL"
            validation_details["insufficient_data_tables"] = failed_tables
            validation_details["row_count_matches"] = False
        else:
            status = "PASSED"
            validation_details["row_count_matches"] = True

        validation_details["total_tables_validated"] = len(table_stats)
        validation_details["tables_with_data"] = len([t for t in table_stats.values() if t > 0])

        return status, validation_details

    def _validate_schema_integrity(self, benchmark, connection) -> tuple[str, dict[str, Any]]:
        validation_details = {}

        try:
            expected_tables = self._get_expected_tables(benchmark)

            existing_tables = self._get_existing_tables(connection)

            missing_tables = []
            if expected_tables:
                missing_tables = [table for table in expected_tables if table not in existing_tables]

            if missing_tables:
                validation_details["missing_tables"] = missing_tables
                validation_details["schema_valid"] = False
                return "FAILED", validation_details
            else:
                validation_details["schema_valid"] = True
                validation_details["verified_tables"] = list(existing_tables)
                return "PASSED", validation_details

        except Exception as e:
            validation_details["schema_valid"] = False
            validation_details["validation_error"] = str(e)
            return "FAILED", validation_details

    def _validate_data_integrity(
        self, benchmark, connection, table_stats: dict[str, int]
    ) -> tuple[str, dict[str, Any]]:
        validation_details = {}

        try:
            accessible_tables = []
            inaccessible_tables = []

            for table_name in table_stats:
                try:
                    cursor = connection.cursor()
                    try:
                        cursor.execute(f"SELECT 1 FROM {table_name} LIMIT 1")
                        accessible_tables.append(table_name)
                    finally:
                        cursor.close()
                except Exception:
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
            validation_details["constraints_enabled"] = False
            validation_details["integrity_error"] = str(e)
            return "FAILED", validation_details

    def _get_expected_row_counts(self, benchmark) -> dict[str, int] | None:
        if hasattr(benchmark, "expected_row_counts"):
            return benchmark.expected_row_counts
        return None

    def get_table_row_count(self, connection: Any, table: str) -> int:
        try:
            cursor = connection.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception:
            return 0

    def _get_expected_tables(self, benchmark) -> list[str] | None:
        try:
            if hasattr(benchmark, "get_schema") and callable(benchmark.get_schema):
                schema = benchmark.get_schema()
                if isinstance(schema, list) and schema and isinstance(schema[0], dict) and "name" in schema[0]:
                    return [str(t["name"]).lower() for t in schema]
                if isinstance(schema, list) and schema and not isinstance(schema[0], dict):
                    return [str(t).lower() for t in schema]
                if isinstance(schema, dict):
                    return [str(t).lower() for t in schema]
        except Exception:
            pass
        try:
            if hasattr(benchmark, "get_available_tables") and callable(benchmark.get_available_tables):
                return [str(t).lower() for t in benchmark.get_available_tables()]
            if hasattr(benchmark, "get_table_names") and callable(benchmark.get_table_names):
                return [str(t).lower() for t in benchmark.get_table_names()]
        except Exception:
            pass
        if hasattr(benchmark, "tables") and benchmark.tables and hasattr(benchmark.tables, "keys"):
            try:
                return [str(t).lower() for t in benchmark.tables.keys()]
            except (TypeError, AttributeError):
                pass
        return None

    def _get_existing_tables(self, connection) -> list[str]:
        query = """
            SELECT table_name FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
            AND table_schema NOT IN ('information_schema', 'pg_catalog',
                                     'mysql', 'performance_schema', 'sys')
        """
        try:
            cursor = connection.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            cursor.close()
            return [row[0].lower() for row in rows]
        except Exception:
            pass
        try:
            result = connection.execute(query).fetchall()
            return [row[0].lower() for row in result]
        except Exception:
            return []
