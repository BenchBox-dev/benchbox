# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .benchmark_specs import get_spec
from .schema import SchemaV2ValidationError, SchemaV2Validator


class CheckStatus(str, Enum):
    PASS = "PASS"
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"


class CheckCategory(str, Enum):
    STRUCTURAL = "STRUCTURAL"
    COMPLETENESS = "COMPLETENESS"
    BELIEVABILITY = "BELIEVABILITY"


@dataclass
class CheckResult:
    category: CheckCategory
    name: str
    status: CheckStatus
    message: str
    details: dict[str, Any] | None = None


@dataclass
class IntegrityReport:
    file: str
    benchmark_id: str
    platform: str
    scale_factor: float
    overall_status: CheckStatus
    checks: list[CheckResult] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def passed(self) -> bool:
        return self.overall_status == CheckStatus.PASS

    def has_warnings(self) -> bool:
        return any(c.status == CheckStatus.WARN for c in self.checks)


_VALID_PHASE_STATUSES = frozenset(
    {
        "SUCCESS",
        "COMPLETED",
        "PASSED",
        "FAILED",
        "SKIPPED",
        "NOT_RUN",
        "ERROR",
        "TIMEOUT",
    }
)

_TPC_METRICS_BENCHMARKS = frozenset({"tpch", "tpcds", "tpchavoc", "tpch_skew"})

_REQUIRED_KEYS = frozenset(
    {
        "version",
        "run",
        "benchmark",
        "platform",
        "config",
        "summary",
        "phases",
        "queries",
        "execution",
        "environment",
        "export",
    }
)

_NO_LOAD_BENCHMARKS = frozenset({"metadata_primitives"})

RESULT_COMPANION_SUFFIXES = (".plans.json", ".tuning.json", ".applied.json", ".override.json", ".manifest.json")

_STRUCTURAL = CheckCategory.STRUCTURAL
_COMPLETENESS = CheckCategory.COMPLETENESS
_BELIEVABILITY = CheckCategory.BELIEVABILITY
_PASS = CheckStatus.PASS
_INFO = CheckStatus.INFO
_WARN = CheckStatus.WARN
_FAIL = CheckStatus.FAIL


def _add_check(
    checks: list[CheckResult],
    category: CheckCategory,
    name: str,
    status: CheckStatus,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    checks.append(CheckResult(category, name, status, message, details))


def _pass(checks: list[CheckResult], category: CheckCategory, name: str, message: str) -> None:
    _add_check(checks, category, name, _PASS, message)


def _info(checks: list[CheckResult], category: CheckCategory, name: str, message: str) -> None:
    _add_check(checks, category, name, _INFO, message)


def _warn(
    checks: list[CheckResult], category: CheckCategory, name: str, message: str, details: dict[str, Any] | None = None
) -> None:
    _add_check(checks, category, name, _WARN, message, details)


def _fail(
    checks: list[CheckResult], category: CheckCategory, name: str, message: str, details: dict[str, Any] | None = None
) -> None:
    _add_check(checks, category, name, _FAIL, message, details)


def _unreadable_report(path: Path) -> IntegrityReport:
    return IntegrityReport(
        file=str(path),
        benchmark_id="unknown",
        platform="unknown",
        scale_factor=0.0,
        overall_status=_FAIL,
        checks=[
            CheckResult(_STRUCTURAL, "file_readable", _FAIL, f"Failed to read or parse {path.name}"),
        ],
        summary={"PASS": 0, "WARN": 0, "FAIL": 1},
    )


def _check_timing_range(
    raw: dict[str, Any],
    checks: list[CheckResult],
    name: str,
    field: str,
    pass_message: str,
) -> None:
    timing = raw.get("summary", {}).get("timing", {})
    value = timing.get(field)
    min_ms = timing.get("min_ms")
    max_ms = timing.get("max_ms")
    if value is not None and min_ms is not None and max_ms is not None and not (min_ms <= value <= max_ms):
        _fail(
            checks,
            _BELIEVABILITY,
            name,
            f"{field}={value} not in [{min_ms}, {max_ms}]",
            {field: value, "min_ms": min_ms, "max_ms": max_ms},
        )
        return
    _pass(checks, _BELIEVABILITY, name, pass_message)


class ResultIntegrityValidator:
    def __init__(self) -> None:
        self._schema_validator = SchemaV2Validator()

    def validate(self, raw: dict[str, Any], filepath: str = "") -> IntegrityReport:
        benchmark_id = raw.get("benchmark", {}).get("id", "unknown")
        platform = raw.get("platform", {}).get("name", "unknown")
        scale_factor = raw.get("benchmark", {}).get("scale_factor", 0.0)

        checks: list[CheckResult] = []

        schema_ok = self._check_schema(raw, checks)
        if schema_ok:
            self._check_required_keys(raw, checks)
            self._check_query_count_math(raw, checks)
            self._check_timing_non_negative(raw, checks)
            self._check_percentile_ordering(raw, checks)
            self._check_phase_status_values(raw, checks)
            self._check_query_entry_fields(raw, checks)
            self._check_query_ms_non_negative(raw, checks)

        spec = get_spec(benchmark_id)
        self._check_expected_query_ids(raw, spec, checks)
        self._check_measurement_queries_present(raw, checks)
        self._check_power_phase_completed(raw, checks)
        self._check_tables_object(raw, spec, checks)
        self._check_tpc_metrics(raw, benchmark_id, checks)

        self._check_avg_between_min_max(raw, checks)
        self._check_geomean_plausible(raw, checks)
        self._check_success_rate(raw, spec, checks)
        self._check_sf1_row_counts(raw, spec, scale_factor, checks)
        self._check_timing_outliers(raw, checks)
        self._check_no_duplicate_executions(raw, checks)
        self._check_load_time_nonzero(raw, benchmark_id, checks)

        status_counts = dict.fromkeys(CheckStatus, 0)
        for c in checks:
            status_counts[c.status] += 1

        overall = _FAIL if status_counts[_FAIL] > 0 else _WARN if status_counts[_WARN] > 0 else _PASS

        return IntegrityReport(
            file=filepath,
            benchmark_id=benchmark_id,
            platform=platform,
            scale_factor=scale_factor,
            overall_status=overall,
            checks=checks,
            summary={s.value: count for s, count in status_counts.items()},
        )

    def _check_schema(self, raw: dict[str, Any], checks: list[CheckResult]) -> bool:
        try:
            self._schema_validator.validate(raw)
        except SchemaV2ValidationError as e:
            _fail(checks, _STRUCTURAL, "schema_v2", f"Schema validation failed: {e}")
            return False
        _pass(checks, _STRUCTURAL, "schema_v2", "Schema v2 validation passed")
        return True

    def _check_required_keys(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        present = set(raw.keys())
        missing = _REQUIRED_KEYS - present
        if missing:
            _fail(
                checks,
                _STRUCTURAL,
                "required_keys",
                f"Missing required keys: {sorted(missing)}",
                {"missing": sorted(missing)},
            )
            return
        _pass(checks, _STRUCTURAL, "required_keys", f"All {len(_REQUIRED_KEYS)} required keys present")

    def _check_query_count_math(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        sq = raw.get("summary", {}).get("queries", {})
        total = sq.get("total", 0)
        passed = sq.get("passed", 0)
        failed = sq.get("failed", 0)
        skipped = sq.get("skipped", 0)
        if passed + failed + skipped != total:
            _fail(
                checks,
                _STRUCTURAL,
                "query_count_math",
                f"{passed} passed + {failed} failed + {skipped} skipped != {total} total",
                {"passed": passed, "failed": failed, "skipped": skipped, "total": total},
            )
        else:
            _pass(
                checks,
                _STRUCTURAL,
                "query_count_math",
                f"{total} = {passed} passed + {failed} failed + {skipped} skipped",
            )

    def _check_timing_non_negative(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        timing = raw.get("summary", {}).get("timing", {})
        negative = {k: v for k, v in timing.items() if isinstance(v, (int, float)) and v < 0}
        if negative:
            _fail(
                checks,
                _STRUCTURAL,
                "timing_non_negative",
                f"Negative timing values: {negative}",
                {"negative_fields": negative},
            )
        else:
            _pass(checks, _STRUCTURAL, "timing_non_negative", "All timing values non-negative")

    def _check_percentile_ordering(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        timing = raw.get("summary", {}).get("timing", {})
        p90 = timing.get("p90_ms")
        p95 = timing.get("p95_ms")
        p99 = timing.get("p99_ms")
        if p90 is not None and p95 is not None and p99 is not None:
            if not (p90 <= p95 <= p99):
                _fail(
                    checks,
                    _STRUCTURAL,
                    "percentile_ordering",
                    f"Percentiles out of order: p90={p90}, p95={p95}, p99={p99}",
                    {"p90_ms": p90, "p95_ms": p95, "p99_ms": p99},
                )
                return
        _pass(checks, _STRUCTURAL, "percentile_ordering", "Percentile ordering correct (p90 <= p95 <= p99)")

    def _check_phase_status_values(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        phases = raw.get("phases", {})
        invalid = {}
        for name, phase in phases.items():
            if isinstance(phase, dict):
                status = phase.get("status", "")
                if status and status not in _VALID_PHASE_STATUSES:
                    invalid[name] = status
        if invalid:
            _fail(
                checks,
                _STRUCTURAL,
                "phase_status_values",
                f"Invalid phase statuses: {invalid}",
                {"invalid_phases": invalid},
            )
        else:
            _pass(checks, _STRUCTURAL, "phase_status_values", f"All {len(phases)} phase statuses valid")

    def _check_query_entry_fields(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        queries = raw.get("queries", [])
        missing_fields = []
        for i, q in enumerate(queries):
            if not isinstance(q, dict):
                missing_fields.append({"index": i, "error": "not a dict"})
                continue
            for field_name in ("id", "ms", "status"):
                if field_name not in q:
                    missing_fields.append({"index": i, "missing": field_name})
        if missing_fields:
            _fail(
                checks,
                _STRUCTURAL,
                "query_entry_fields",
                f"{len(missing_fields)} query entries missing required fields",
                {"issues": missing_fields[:10]},
            )
        else:
            _pass(
                checks,
                _STRUCTURAL,
                "query_entry_fields",
                f"All {len(queries)} query entries have id, ms, status",
            )

    def _check_query_ms_non_negative(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        queries = raw.get("queries", [])
        negative = [
            {"id": q.get("id"), "ms": q.get("ms")}
            for q in queries
            if isinstance(q, dict) and isinstance(q.get("ms"), (int, float)) and q["ms"] < 0
        ]
        if negative:
            _fail(
                checks,
                _STRUCTURAL,
                "query_ms_non_negative",
                f"{len(negative)} queries with negative ms",
                {"negative_queries": negative[:10]},
            )
        else:
            _pass(checks, _STRUCTURAL, "query_ms_non_negative", "No queries with negative ms")

    def _check_expected_query_ids(self, raw: dict[str, Any], spec: Any | None, checks: list[CheckResult]) -> None:
        if spec is None:
            _pass(checks, _COMPLETENESS, "expected_query_ids", "Unknown benchmark - skipping query ID check")
            return

        queries = raw.get("queries", [])
        actual_ids = {q["id"] for q in queries if isinstance(q, dict) and "id" in q}

        if spec.unique_query_ids:
            expected = spec.unique_query_ids
            found = actual_ids & expected
            ratio = len(found) / len(expected) if expected else 1.0
            if ratio < 0.8:
                _fail(
                    checks,
                    _COMPLETENESS,
                    "expected_query_ids",
                    f"{len(found)}/{len(expected)} expected query IDs found ({ratio:.0%})",
                    {"missing": sorted(expected - actual_ids)},
                )
            elif ratio < 1.0:
                _warn(
                    checks,
                    _COMPLETENESS,
                    "expected_query_ids",
                    f"{len(found)}/{len(expected)} expected query IDs found ({ratio:.0%})",
                    {"missing": sorted(expected - actual_ids)},
                )
            else:
                _pass(
                    checks,
                    _COMPLETENESS,
                    "expected_query_ids",
                    f"{len(found)}/{len(expected)} expected query IDs found",
                )
        elif spec.min_unique_queries > 0:
            unique_count = len(actual_ids)
            if unique_count < spec.min_unique_queries:
                _fail(
                    checks,
                    _COMPLETENESS,
                    "expected_query_ids",
                    f"Only {unique_count} unique queries, expected >= {spec.min_unique_queries}",
                )
            else:
                _pass(
                    checks,
                    _COMPLETENESS,
                    "expected_query_ids",
                    f"{unique_count} unique queries (>= {spec.min_unique_queries} minimum)",
                )
        else:
            _pass(checks, _COMPLETENESS, "expected_query_ids", "Open-ended benchmark - no fixed query set")

    def _check_measurement_queries_present(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        queries = raw.get("queries", [])
        has_measurement = any(
            (isinstance(q, dict) and (q.get("run_type") == "measurement" or q.get("iter", 0) > 0)) for q in queries
        )
        if not has_measurement:
            _fail(
                checks,
                _COMPLETENESS,
                "measurement_queries_present",
                "No measurement queries found (no run_type='measurement' or iter > 0)",
            )
        else:
            _pass(checks, _COMPLETENESS, "measurement_queries_present", "Measurement queries present")

    def _check_power_phase_completed(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        phases = raw.get("phases", {})
        power = phases.get("power_test", {})
        if not isinstance(power, dict):
            _warn(checks, _COMPLETENESS, "power_phase_completed", "No power_test phase found")
            return
        status = power.get("status", "")
        if status in ("COMPLETED", "SUCCESS", "PASSED"):
            _pass(checks, _COMPLETENESS, "power_phase_completed", f"Power test phase status: {status}")
        else:
            _warn(checks, _COMPLETENESS, "power_phase_completed", f"Power test phase status: {status}")

    def _check_tables_object(self, raw: dict[str, Any], spec: Any | None, checks: list[CheckResult]) -> None:
        tables = raw.get("tables")
        if spec is not None and spec.requires_tables_object and tables is None:
            _warn(checks, _COMPLETENESS, "tables_object", "Tables object missing but required by spec")
        else:
            _pass(
                checks,
                _COMPLETENESS,
                "tables_object",
                "Tables object present" if tables is not None else "Tables not required",
            )

    def _check_tpc_metrics(self, raw: dict[str, Any], benchmark_id: str, checks: list[CheckResult]) -> None:
        from .benchmark_specs import LEGACY_ALIASES

        canonical = LEGACY_ALIASES.get(benchmark_id, benchmark_id)
        if canonical not in _TPC_METRICS_BENCHMARKS:
            _pass(checks, _COMPLETENESS, "tpc_metrics", "Not a TPC benchmark - skipped")
            return
        summary = raw.get("summary", {})
        if "tpc_metrics" in summary:
            _pass(checks, _COMPLETENESS, "tpc_metrics", "TPC metrics present in summary")
        else:
            _info(
                checks,
                _COMPLETENESS,
                "tpc_metrics",
                f"TPC metrics absent for {canonical} benchmark (unofficial run - no QphH/QthDS expected)",
            )

    def _check_avg_between_min_max(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        _check_timing_range(raw, checks, "avg_between_min_max", "avg_ms", "Average within [min, max] range")

    def _check_geomean_plausible(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        _check_timing_range(
            raw,
            checks,
            "geomean_plausible",
            "geometric_mean_ms",
            "Geometric mean within [min, max] range",
        )

    def _check_success_rate(self, raw: dict[str, Any], spec: Any | None, checks: list[CheckResult]) -> None:
        sq = raw.get("summary", {}).get("queries", {})
        total = sq.get("total", 0)
        passed = sq.get("passed", 0)
        skipped = sq.get("skipped", 0)
        if total == 0:
            _fail(checks, _BELIEVABILITY, "success_rate", "No queries recorded")
            return

        billable = total - skipped
        if billable == 0:
            _fail(
                checks,
                _BELIEVABILITY,
                "success_rate",
                "No queries executed; all queries skipped by compatibility rules",
            )
            return
        rate = passed / billable
        if spec is not None and spec.high_failure_expected:
            _pass(
                checks,
                _BELIEVABILITY,
                "success_rate",
                f"{rate:.1%} success rate (high failure expected for this benchmark)",
            )
            return

        floor = spec.min_success_rate if spec is not None else 0.8
        if rate < floor:
            _fail(
                checks,
                _BELIEVABILITY,
                "success_rate",
                f"{rate:.1%} success rate below {floor:.0%} floor",
                {"rate": rate, "floor": floor, "passed": passed, "total": billable, "skipped": skipped},
            )
        else:
            _pass(
                checks,
                _BELIEVABILITY,
                "success_rate",
                f"{rate:.1%} >= {floor:.0%} floor ({skipped} skipped)",
            )

    def _check_sf1_row_counts(
        self,
        raw: dict[str, Any],
        spec: Any | None,
        scale_factor: float,
        checks: list[CheckResult],
    ) -> None:
        if spec is None or spec.sf1_row_counts is None or scale_factor != 1.0:
            _pass(
                checks,
                _BELIEVABILITY,
                "sf1_row_counts",
                "Row count check not applicable (no spec, no SF1 counts, or SF != 1.0)",
            )
            return
        tables = raw.get("tables")
        if tables is None:
            _pass(checks, _BELIEVABILITY, "sf1_row_counts", "No tables metadata to check")
            return

        table_rows: dict[str, int] = {}
        if isinstance(tables, list):
            for t in tables:
                if isinstance(t, dict) and "name" in t:
                    table_rows[t["name"]] = t.get("rows", 0)
        elif isinstance(tables, dict):
            for name, info in tables.items():
                if isinstance(info, dict):
                    table_rows[name] = info.get("rows", 0)
                elif isinstance(info, int):
                    table_rows[name] = info

        tolerance = 0.01

        mismatches = {}
        missing_tables = []
        for table_name, expected_rows in spec.sf1_row_counts.items():
            actual = table_rows.get(table_name)
            if actual is None:
                missing_tables.append(table_name)
                continue
            if expected_rows == 0:
                continue
            pct_diff = abs(actual - expected_rows) / expected_rows
            if pct_diff > tolerance:
                mismatches[table_name] = {"expected": expected_rows, "actual": actual, "diff": f"{pct_diff:.1%}"}

        if mismatches:
            _fail(
                checks,
                _BELIEVABILITY,
                "sf1_row_counts",
                f"{len(mismatches)} tables outside ±1% of SF1 expected rows",
                {"mismatches": mismatches, "missing": missing_tables},
            )
        elif missing_tables:
            checked = len(spec.sf1_row_counts) - len(missing_tables)
            _fail(
                checks,
                _BELIEVABILITY,
                "sf1_row_counts",
                f"{len(missing_tables)} expected tables missing from tables metadata "
                f"({checked}/{len(spec.sf1_row_counts)} checked)",
                {"missing": missing_tables},
            )
        else:
            matched = len(spec.sf1_row_counts)
            _pass(checks, _BELIEVABILITY, "sf1_row_counts", f"{matched} tables within ±1% of SF1 expected")

    def _check_timing_outliers(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        queries = raw.get("queries", [])
        outliers = [
            q.get("id")
            for q in queries
            if isinstance(q, dict) and isinstance(q.get("ms"), (int, float)) and q["ms"] > 1_800_000
        ]
        if outliers:
            _warn(
                checks,
                _BELIEVABILITY,
                "timing_outliers",
                f"{len(outliers)} queries exceed 30 minutes",
                {"query_ids": outliers[:10]},
            )
        else:
            _pass(checks, _BELIEVABILITY, "timing_outliers", "No queries exceed 30-minute threshold")

    def _check_no_duplicate_executions(self, raw: dict[str, Any], checks: list[CheckResult]) -> None:
        queries = raw.get("queries", [])
        seen: set[tuple[str, int, int]] = set()
        dupes: list[tuple[str, int, int]] = []
        for q in queries:
            if not isinstance(q, dict):
                continue
            key = (str(q.get("id", "")), q.get("iter", 0), q.get("stream", 0))
            if key in seen:
                dupes.append(key)
            else:
                seen.add(key)
        if dupes:
            _warn(
                checks,
                _BELIEVABILITY,
                "no_duplicate_executions",
                f"{len(dupes)} duplicate (id, iter, stream) entries",
                {"duplicates": [{"id": d[0], "iter": d[1], "stream": d[2]} for d in dupes[:10]]},
            )
        else:
            _pass(checks, _BELIEVABILITY, "no_duplicate_executions", "No duplicate (id, iter, stream) entries")

    def _check_load_time_nonzero(self, raw: dict[str, Any], benchmark_id: str, checks: list[CheckResult]) -> None:
        from .benchmark_specs import LEGACY_ALIASES

        canonical = LEGACY_ALIASES.get(benchmark_id, benchmark_id)
        if canonical in _NO_LOAD_BENCHMARKS:
            _pass(checks, _BELIEVABILITY, "load_time_nonzero", "No data loading expected for this benchmark")
            return

        phases = raw.get("phases", {})
        loading = phases.get("data_loading", {})
        if not isinstance(loading, dict):
            _pass(checks, _BELIEVABILITY, "load_time_nonzero", "No data_loading phase found")
            return
        load_time = loading.get("duration_ms", loading.get("load_time_ms"))
        if load_time is not None and load_time == 0:
            _warn(checks, _BELIEVABILITY, "load_time_nonzero", "Load time is 0ms")
        else:
            _pass(
                checks,
                _BELIEVABILITY,
                "load_time_nonzero",
                "Load time is non-zero" if load_time else "No load time recorded",
            )


def _validate_path(path: Path, validator: ResultIntegrityValidator) -> IntegrityReport:
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _unreadable_report(path)
    return validator.validate(raw, filepath=str(path))


def validate_file(path: Path | str) -> IntegrityReport:
    return _validate_path(Path(path), ResultIntegrityValidator())


def validate_directory(
    path: Path | str,
    pattern: str = "*.json",
) -> list[IntegrityReport]:
    path = Path(path)
    validator = ResultIntegrityValidator()
    return [
        _validate_path(filepath, validator)
        for filepath in sorted(path.glob(pattern))
        if not filepath.name.endswith(RESULT_COMPANION_SUFFIXES)
    ]


def check_payload(check: CheckResult) -> dict[str, Any]:
    return {
        "category": check.category.value,
        "name": check.name,
        "status": check.status.value,
        "message": check.message,
        "details": check.details,
    }


def report_payload(report: IntegrityReport, *, verbose: bool = False) -> dict[str, Any]:
    return {
        "file": report.file,
        "benchmark_id": report.benchmark_id,
        "platform": report.platform,
        "scale_factor": report.scale_factor,
        "overall_status": report.overall_status.value,
        "summary": report.summary,
        "checks": [check_payload(c) for c in report.checks if verbose or c.status != CheckStatus.PASS],
    }


def directory_payload(reports: list[IntegrityReport]) -> dict[str, Any]:
    return {
        "total": len(reports),
        "pass": sum(1 for r in reports if r.overall_status == CheckStatus.PASS),
        "warn": sum(1 for r in reports if r.overall_status == CheckStatus.WARN),
        "fail": sum(1 for r in reports if r.overall_status == CheckStatus.FAIL),
        "files": [
            {
                "file": r.file,
                "benchmark_id": r.benchmark_id,
                "platform": r.platform,
                "overall_status": r.overall_status.value,
            }
            for r in reports
        ],
    }
