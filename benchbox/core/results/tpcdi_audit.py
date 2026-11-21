from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from benchbox.core.results.status import validation_status_is_non_clean

TPCDI_BENCHMARK_ID = "tpcdi"
TPCDI_AUDIT_BLOCK_KEY = "tpcdi_audit"
UNAUDITED_VALIDATION_STATUS = "NOT_VALIDATED"
LEGACY_TPCDI_NOTE = "Legacy TPC-DI result from a withdrawn workload; not validated."
LEGACY_METADATA_KEY = "legacy_result_note"
LEGACY_TPCDI_REFUSAL = f"{LEGACY_TPCDI_NOTE} It cannot be compared, published or submitted."


def _normalize_id(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def is_tpcdi_id(value: Any) -> bool:
    return _normalize_id(value) == TPCDI_BENCHMARK_ID


def has_tpcdi_audit_block(validation_details: Any) -> bool:
    return isinstance(validation_details, Mapping) and bool(validation_details.get(TPCDI_AUDIT_BLOCK_KEY))


def audited_validation_status(benchmark_ids: Iterable[Any], validation_status: Any, validation_details: Any) -> Any:
    if not any(is_tpcdi_id(candidate) for candidate in benchmark_ids):
        return validation_status
    if has_tpcdi_audit_block(validation_details) or validation_status_is_non_clean(validation_status):
        return validation_status
    return UNAUDITED_VALIDATION_STATUS


def enforce_tpcdi_audit_rule(result: Any) -> None:
    state = vars(result)
    if not {"benchmark_name", "validation_status", "validation_details"} <= state.keys():
        return
    metadata = state.get("execution_metadata")
    status = audited_validation_status(
        (
            state["benchmark_name"],
            state.get("_benchmark_id_override"),
            metadata.get("benchmark_id") if isinstance(metadata, Mapping) else None,
        ),
        state["validation_status"],
        state["validation_details"],
    )
    if status is not state["validation_status"]:
        object.__setattr__(result, "validation_status", status)


def mark_legacy_tpcdi_result(result: Any) -> None:
    if not is_tpcdi_id(getattr(result, "benchmark_id", None)) and not is_tpcdi_id(
        getattr(result, "benchmark_name", None)
    ):
        return
    if has_tpcdi_audit_block(getattr(result, "validation_details", None)):
        return
    metadata = dict(getattr(result, "execution_metadata", None) or {})
    metadata[LEGACY_METADATA_KEY] = LEGACY_TPCDI_NOTE
    result.execution_metadata = metadata


def legacy_tpcdi_note(result: Any) -> str | None:
    metadata = getattr(result, "execution_metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    note = metadata.get(LEGACY_METADATA_KEY)
    return str(note) if note else None


def legacy_tpcdi_bundle_note(data: Any) -> str | None:
    if not isinstance(data, Mapping):
        return None
    benchmark = data.get("benchmark")
    if not isinstance(benchmark, Mapping) or not is_tpcdi_id(benchmark.get("id")):
        return None
    if data.get(TPCDI_AUDIT_BLOCK_KEY):
        return None
    return LEGACY_TPCDI_NOTE
