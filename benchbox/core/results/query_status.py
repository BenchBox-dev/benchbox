from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

DOCUMENTED_PLATFORM_QUERY_EXCLUSIONS: dict[tuple[str, str], dict[str, frozenset[str]]] = {
    ("tpcds_obt", "clickhouse-local"): {
        "driver_versions": frozenset({"4.1.6"}),
        "query_ids": frozenset({"41", "44"}),
    },
}


DATA_REFRESHED_REASON = "data refreshed"
WITHIN_TOLERANCE_REASON = "within tolerance"
WITHIN_RANGE_REASON = "within range"
PARAMETER_SENSITIVE_REASON = "parameter sensitive"
REFERENCE_DATA_DIFFERS_REASON = "reference data differs"
NO_REFERENCE_VALIDATION_STATUS = "NO_REFERENCE"
NO_REFERENCE_REASON = "no row-count reference at this scale factor"
NOT_VALIDATED_VALIDATION_STATUS = "NOT_VALIDATED"
PASSED_VALIDATION_STATUS = "PASSED"
FAILED_VALIDATION_STATUS = "FAILED"
PARTIAL_VALIDATION_STATUS = "PARTIAL"
DOCUMENTED_ROW_COUNT_SKIP_REASONS: tuple[str, ...] = (
    DATA_REFRESHED_REASON,
    WITHIN_TOLERANCE_REASON,
    WITHIN_RANGE_REASON,
    PARAMETER_SENSITIVE_REASON,
    REFERENCE_DATA_DIFFERS_REASON,
)


def within_tolerance_warning(actual: int, expected: int) -> str:
    return f"{WITHIN_TOLERANCE_REASON}: row count {actual} against reference count {expected} is not an exact match"


def within_range_warning(actual: int) -> str:
    return (
        f"{WITHIN_RANGE_REASON}: row count {actual} passed the accepted range check; no single reference count applies"
    )


def row_count_skip_is_documented(evidence: Any) -> bool:
    warning = evidence.get("warning") if isinstance(evidence, dict) else None
    return isinstance(warning, str) and warning.startswith(
        tuple(f"{reason}:" for reason in DOCUMENTED_ROW_COUNT_SKIP_REASONS)
    )


def _driver_versions(data: dict[str, Any]) -> set[str]:
    versions: set[str] = set()
    execution = data.get("execution")
    platform = data.get("platform")
    config = platform.get("config") if isinstance(platform, dict) else None
    for section in (execution, config):
        if isinstance(section, dict) and section.get("driver_version_actual"):
            versions.add(str(section["driver_version_actual"]).strip())
    return versions


def _summary_counts_match(data: dict[str, Any], failed_rows: int) -> bool:
    summary = data.get("summary")
    queries = summary.get("queries") if isinstance(summary, dict) else None
    if not isinstance(queries, dict):
        return False
    failed = int_or_none(queries.get("failed"))
    passed = int_or_none(queries.get("passed"))
    total = int_or_none(queries.get("total"))
    skipped = int_or_none(queries.get("skipped")) or 0
    if failed is None or passed is None or total is None:
        return False
    return failed == failed_rows and total == passed + failed + skipped


def documented_exclusions_cover_failures(data: dict[str, Any]) -> bool:
    benchmark = data.get("benchmark")
    platform = data.get("platform")
    if not isinstance(benchmark, dict) or not isinstance(platform, dict):
        return False
    platform_id = re.sub(r"[\s_]+", "-", str(platform.get("name") or "").strip().lower())
    rule = DOCUMENTED_PLATFORM_QUERY_EXCLUSIONS.get((str(benchmark.get("id") or "").strip().lower(), platform_id))
    queries = data.get("queries")
    if rule is None or not isinstance(queries, list):
        return False
    versions = _driver_versions(data)
    if not versions or not versions <= rule["driver_versions"]:
        return False
    failed_rows = [row for row in queries if _query_row_failed(row)]
    failed_ids = {str(row.get("id")) for row in failed_rows}
    if not failed_ids or not failed_ids <= rule["query_ids"]:
        return False
    return _summary_counts_match(data, len(failed_rows))


def bundle_failed_query_count(data: Mapping[str, Any]) -> int:
    summary = data.get("summary")
    if isinstance(summary, dict):
        queries = summary.get("queries")
        if isinstance(queries, dict):
            failed = int_or_none(queries.get("failed"))
            if failed is not None and failed > 0:
                return failed

            total = int_or_none(queries.get("total"))
            passed = int_or_none(queries.get("passed"))
            skipped = int_or_none(queries.get("skipped")) or 0
            if total is not None and passed is not None and total > passed + skipped:
                return max(total - passed - skipped, 0)

    query_rows = data.get("queries")
    if isinstance(query_rows, list):
        return sum(1 for query in query_rows if _query_row_failed(query))
    return 0


def int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _row_statuses(query_rows: Any) -> list[tuple[bool, str, Mapping[str, Any]]]:
    if not isinstance(query_rows, list):
        return []
    rows: list[tuple[bool, str, Mapping[str, Any]]] = []
    for query in query_rows:
        evidence = query.get("row_count_validation") if isinstance(query, Mapping) else None
        if isinstance(evidence, Mapping):
            measurement = str(query.get("run_type") or "measurement").strip().lower() == "measurement"
            rows.append((measurement, str(evidence.get("status") or "").strip().upper(), evidence))
    return rows


def row_counts_checked(query_rows: Any) -> bool:
    statuses = [status for measurement, status, _ in _row_statuses(query_rows) if measurement]
    return "PASSED" in statuses and "UNVALIDATED" not in statuses


def has_failed_query_validation(query_rows: Any) -> bool:
    return any(status == "FAILED" for _, status, _ in _row_statuses(query_rows))


def _noun(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else plural or f"{singular}s"


def row_evidence_roll_up(query_rows: Any, *, reference_exists: bool, failed_queries: int = 0) -> tuple[str, str]:
    rows = _row_statuses(query_rows)
    failed = sum(1 for _, status, _ in rows if status == "FAILED")
    if failed:
        return FAILED_VALIDATION_STATUS, f"{failed} failed row-count {_noun(failed, 'check')}"
    if failed_queries > 0:
        return (
            PARTIAL_VALIDATION_STATUS,
            f"{failed_queries} failed measurement {_noun(failed_queries, 'query', 'queries')}",
        )
    unvalidated = sum(1 for _, status, _ in rows if status == "UNVALIDATED")
    undocumented = sum(
        1
        for _, status, evidence in rows
        if status not in {"PASSED", "UNVALIDATED"}
        and not (
            status == "SKIPPED"
            and (
                row_count_skip_is_documented(evidence)
                or (not reference_exists and str(evidence.get("warning") or "").startswith(NO_REFERENCE_REASON))
            )
        )
    )
    if (
        not unvalidated
        and not undocumented
        and any(measurement and status == "PASSED" for measurement, status, _ in rows)
    ):
        return PASSED_VALIDATION_STATUS, "row counts validated"
    if undocumented:
        return (
            NOT_VALIDATED_VALIDATION_STATUS,
            f"{undocumented} row {_noun(undocumented, 'count')} skipped without a documented reason",
        )
    if not reference_exists:
        return NO_REFERENCE_VALIDATION_STATUS, NO_REFERENCE_REASON
    if unvalidated:
        return NOT_VALIDATED_VALIDATION_STATUS, f"{unvalidated} unvalidated row {_noun(unvalidated, 'count')}"
    if any(
        status == "SKIPPED" and str(evidence.get("warning") or "").startswith(f"{REFERENCE_DATA_DIFFERS_REASON}:")
        for _, status, evidence in rows
    ):
        return NO_REFERENCE_VALIDATION_STATUS, f"{NO_REFERENCE_REASON}; {REFERENCE_DATA_DIFFERS_REASON}"
    return NOT_VALIDATED_VALIDATION_STATUS, "no row count was checked"


def _query_row_failed(query: Any) -> bool:
    if not isinstance(query, dict):
        return False
    run_type = str(query.get("run_type") or "measurement").strip().lower()
    if run_type != "measurement":
        return False
    status = query.get("status")
    return status is not None and str(status).upper() not in {"SUCCESS", "SKIPPED"}
