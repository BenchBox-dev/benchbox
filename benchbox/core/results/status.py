from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from benchbox.core.results.query_status import bundle_failed_query_count, has_failed_query_validation, int_or_none

NON_CLEAN_VALIDATION_STATUSES: frozenset[str] = frozenset(
    {"failed", "interrupted", "partial", "error", "not_run", "not_validated", "uncertain", "unknown"}
)
NON_CLEAN_TRANSLATION_STATUSES: frozenset[str] = frozenset({"fallback", "failed"})
CLI_FAILURE_VALIDATION_STATUSES: frozenset[str] = frozenset({"failed", "interrupted", "partial", "error"})
UNVALIDATED_VALIDATION_STATUSES: frozenset[str] = NON_CLEAN_VALIDATION_STATUSES - CLI_FAILURE_VALIDATION_STATUSES


def normalize_validation_status(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("status")
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def validation_status_is_non_clean(value: Any) -> bool:
    status = normalize_validation_status(value)
    return status in NON_CLEAN_VALIDATION_STATUSES


def normalize_translation_status(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("status")
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def translation_status_is_non_clean(value: Any) -> bool:
    status = normalize_translation_status(value)
    return status in NON_CLEAN_TRANSLATION_STATUSES


def result_failed_query_count(result: Any) -> int:
    failed = int_or_none(getattr(result, "failed_queries", None))
    if failed is not None:
        return max(failed, 0)

    total = int_or_none(getattr(result, "total_queries", None))
    successful = int_or_none(getattr(result, "successful_queries", None))
    if total is not None and successful is not None and total > successful:
        return max(total - successful, 0)
    return 0


def _result_reason(result: Any, failing_validation_statuses: frozenset[str]) -> str | None:
    failed = result_failed_query_count(result)
    if failed:
        noun = "query" if failed == 1 else "queries"
        return f"{failed} failed {noun}"

    if has_failed_query_validation(getattr(result, "query_results", None)):
        return "query validation failed"

    status = normalize_validation_status(getattr(result, "validation_status", None))
    if status in failing_validation_statuses:
        return f"validation_status={status}"

    translation_status = _result_translation_status(result)
    if translation_status in NON_CLEAN_TRANSLATION_STATUSES:
        return f"translation_status={translation_status}"
    return None


def result_non_clean_reason(result: Any) -> str | None:
    return _result_reason(result, NON_CLEAN_VALIDATION_STATUSES)


def result_is_clean_pass(result: Any) -> bool:
    return result_non_clean_reason(result) is None


def result_cli_failure_reason(result: Any) -> str | None:
    return _result_reason(result, CLI_FAILURE_VALIDATION_STATUSES)


def result_unvalidated_reason(result: Any) -> str | None:
    if result_failed_query_count(result) or has_failed_query_validation(getattr(result, "query_results", None)):
        return None

    status = normalize_validation_status(getattr(result, "validation_status", None))
    if status in UNVALIDATED_VALIDATION_STATUSES:
        return f"validation_status={status}"
    return None


def bundle_non_clean_reason(data: dict[str, Any]) -> str | None:
    failed = bundle_failed_query_count(data)
    if failed:
        noun = "query" if failed == 1 else "queries"
        return f"{failed} failed {noun}"

    if has_failed_query_validation(data.get("queries")):
        return "query validation failed"

    status = _bundle_validation_status(data)
    if status in NON_CLEAN_VALIDATION_STATUSES:
        return f"validation_status={status}"

    translation_status = _bundle_translation_status(data)
    if translation_status in NON_CLEAN_TRANSLATION_STATUSES:
        return f"translation_status={translation_status}"
    return None


def bundle_is_clean_pass(data: dict[str, Any]) -> bool:
    return bundle_non_clean_reason(data) is None


def _result_translation_status(result: Any) -> str | None:
    execution_metadata = getattr(result, "execution_metadata", None)
    if not isinstance(execution_metadata, Mapping):
        return None
    return normalize_translation_status(execution_metadata.get("translation"))


def _bundle_validation_status(data: dict[str, Any]) -> str | None:
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return None
    return normalize_validation_status(summary.get("validation"))


def _bundle_translation_status(data: dict[str, Any]) -> str | None:
    execution = data.get("execution")
    if not isinstance(execution, dict):
        return None
    return normalize_translation_status(execution.get("translation"))
