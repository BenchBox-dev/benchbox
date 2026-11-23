from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from benchbox.core.results import corpus_rule
from benchbox.core.results.loader import (
    ResultLoadError,
    UnsupportedSchemaError,
    load_result_file,
)
from benchbox.core.results.status import (
    result_failed_query_count,
    result_non_clean_reason,
    result_unvalidated_reason,
)
from benchbox.core.results.subscale_policy import normalize_tpc_benchmark_id, result_gateway_routed


class SubmitTerminalState(str, Enum):
    submittable = "submittable"
    unofficial = "unofficial"
    query_failure = "query_failure"
    schema_violation = "schema_violation"
    bundle_load_error = "bundle_load_error"
    unvalidated = "unvalidated"
    missing_manifest = "missing_manifest"


def result_public_refusal(result: Any) -> str | None:
    return corpus_rule.public_refusal(
        getattr(result, "benchmark_id", None) or normalize_tpc_benchmark_id(getattr(result, "benchmark_name", None)),
        getattr(result, "scale_factor", None),
        gateway_routed=result_gateway_routed(result),
    )


PUBLIC_REFUSAL_REASONS = {
    corpus_rule.HELD_GATEWAY_ROUTED: "unofficial_nonstandard",
    corpus_rule.CLASS_SUBSCALE: "unofficial_subscale",
}


def result_public_refusal_reason(result: Any) -> str | None:
    refusal = result_public_refusal(result)
    return None if refusal is None else PUBLIC_REFUSAL_REASONS[refusal]


def result_refused_at_submission(result: Any) -> bool:
    return result_public_refusal(result) is not None


def classify_loaded_result(result: Any) -> SubmitTerminalState:
    if result_refused_at_submission(result):
        return SubmitTerminalState.unofficial

    non_clean_reason = result_non_clean_reason(result)
    if non_clean_reason:
        if result_failed_query_count(result):
            return SubmitTerminalState.query_failure
        if result_unvalidated_reason(result):
            return SubmitTerminalState.unvalidated
        return SubmitTerminalState.schema_violation
    return SubmitTerminalState.submittable


def classify_result_path(result_json: Path | str | None) -> SubmitTerminalState:
    if result_json is None:
        return SubmitTerminalState.missing_manifest
    path = Path(result_json).expanduser()
    if not path.exists():
        return SubmitTerminalState.missing_manifest
    try:
        result, _raw = load_result_file(path)
    except FileNotFoundError:
        return SubmitTerminalState.missing_manifest
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, ResultLoadError, UnsupportedSchemaError):
        return SubmitTerminalState.bundle_load_error
    return classify_loaded_result(result)
