from __future__ import annotations

import re
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
NO_REFERENCE_VALIDATION_STATUS = "NO_REFERENCE"
NO_REFERENCE_REASON = "no row-count reference at this scale factor"
NOT_VALIDATED_VALIDATION_STATUS = "NOT_VALIDATED"
DOCUMENTED_ROW_COUNT_SKIP_REASONS: tuple[str, ...] = (
    DATA_REFRESHED_REASON,
    WITHIN_TOLERANCE_REASON,
    WITHIN_RANGE_REASON,
    PARAMETER_SENSITIVE_REASON,
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


def bundle_failed_query_count(data: dict[str, Any]) -> int:
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


def has_failed_query_validation(query_rows: Any) -> bool:
    if not isinstance(query_rows, list):
        return False
    return any(
        isinstance(query, dict)
        and isinstance(evidence := query.get("row_count_validation"), dict)
        and str(evidence.get("status") or "").strip().upper() == "FAILED"
        for query in query_rows
    )


def unvalidated_measurement_query_ids(query_rows: Any) -> list[str]:
    if not isinstance(query_rows, list):
        return []
    return [
        str(query.get("query_id") or query.get("id"))
        for query in query_rows
        if isinstance(query, dict)
        and str(query.get("run_type") or "measurement").strip().lower() == "measurement"
        and isinstance(evidence := query.get("row_count_validation"), dict)
        and str(evidence.get("status") or "").strip().upper() == "UNVALIDATED"
    ]


def unchecked_row_count_evidence(query_rows: Any) -> bool:
    if not isinstance(query_rows, list):
        return False
    statuses = [
        str(evidence.get("status") or "").strip().upper()
        for query in query_rows
        if isinstance(query, dict)
        and str(query.get("run_type") or "measurement").strip().lower() == "measurement"
        and isinstance(evidence := query.get("row_count_validation"), dict)
    ]
    return bool(statuses) and "PASSED" not in statuses and "SKIPPED" in statuses


def _measurement_row_count_statuses(query_rows: Any) -> list[str]:
    if not isinstance(query_rows, list):
        return []
    statuses: list[str] = []
    for query in query_rows:
        if not isinstance(query, dict) or str(query.get("run_type") or "measurement").strip().lower() != "measurement":
            continue
        evidence = query.get("row_count_validation")
        statuses.append(str(evidence.get("status") or "").strip().upper() if isinstance(evidence, dict) else "")
    return statuses


def has_row_count_evidence(query_rows: Any) -> bool:
    statuses = _measurement_row_count_statuses(query_rows)
    return "PASSED" in statuses and "UNVALIDATED" not in statuses


def unevidenced_validation_status(query_rows: Any) -> str | None:
    if unvalidated_measurement_query_ids(query_rows):
        return NO_REFERENCE_VALIDATION_STATUS
    if unchecked_row_count_evidence(query_rows):
        return NOT_VALIDATED_VALIDATION_STATUS
    if _measurement_row_count_statuses(query_rows) and not has_row_count_evidence(query_rows):
        return NO_REFERENCE_VALIDATION_STATUS
    return None


def _query_row_failed(query: Any) -> bool:
    if not isinstance(query, dict):
        return False
    run_type = str(query.get("run_type") or "measurement").strip().lower()
    if run_type != "measurement":
        return False
    status = query.get("status")
    return status is not None and str(status).upper() not in {"SUCCESS", "SKIPPED"}
