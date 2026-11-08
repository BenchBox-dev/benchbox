from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

KIND_INFRASTRUCTURE = "infrastructure"
KIND_PRODUCT = "product"

SIGNATURE_TIMEOUT = "timeout"
SIGNATURE_MEMORY = "memory"
SIGNATURE_DOWNLOAD = "download_error"
SIGNATURE_SINGLESTORE_1712 = "singlestore_1712"

SIGNATURE_QUERY_FAILURE = "query_failure"
SIGNATURE_VALIDATION_FAILURE = "validation_failure"
SIGNATURE_CRASH = "crash"

INFRASTRUCTURE_SIGNATURES = (
    SIGNATURE_TIMEOUT,
    SIGNATURE_MEMORY,
    SIGNATURE_DOWNLOAD,
    SIGNATURE_SINGLESTORE_1712,
)
PRODUCT_SIGNATURES = (SIGNATURE_QUERY_FAILURE, SIGNATURE_VALIDATION_FAILURE, SIGNATURE_CRASH)

_NOT_A_FAILURE_TERMINAL_STATES = frozenset({"passed", "skipped", "unreachable"})
_VALIDATION_SUBMIT_STATES = frozenset({"schema_violation", "unvalidated", "bundle_load_error", "unofficial"})

_SINGLESTORE_1712_RE = re.compile(
    r"\bERROR\s+1712\b|\(\s*1712\s*,|\b(?:errno|error[ _]code)\s*[=:]?\s*1712\b",
    re.IGNORECASE,
)
_DOWNLOAD_ERROR_RE = re.compile(r"\bDownloadError\b|download failed after|failed to download", re.IGNORECASE)


@dataclass(frozen=True)
class FailureClass:
    kind: str
    signature: str

    @property
    def is_infrastructure(self) -> bool:
        return self.kind == KIND_INFRASTRUCTURE


@dataclass(frozen=True)
class FailureEvidence:
    status: str
    terminal_state: str
    submit_terminal_state: str
    failed_query_errors: tuple[str, ...] = ()
    failure_text: str = ""


def evidence_from_row(row: Mapping[str, object]) -> FailureEvidence:
    failed = row.get("failed_queries")
    errors: tuple[str, ...] = ()
    if isinstance(failed, Sequence) and not isinstance(failed, str):
        errors = tuple(str(entry.get("error") or "") if isinstance(entry, Mapping) else "" for entry in failed)
    return FailureEvidence(
        status=str(row.get("status") or ""),
        terminal_state=str(row.get("terminal_state") or ""),
        submit_terminal_state=str(row.get("submit_terminal_state") or ""),
        failed_query_errors=errors,
        failure_text=str(row.get("failure_tail") or ""),
    )


def classify_failure(evidence: FailureEvidence) -> FailureClass | None:
    if evidence.status == "passed" or evidence.terminal_state in _NOT_A_FAILURE_TERMINAL_STATES:
        return None
    if evidence.failed_query_errors:
        if all(_SINGLESTORE_1712_RE.search(error) for error in evidence.failed_query_errors):
            return FailureClass(KIND_INFRASTRUCTURE, SIGNATURE_SINGLESTORE_1712)
        return FailureClass(KIND_PRODUCT, SIGNATURE_QUERY_FAILURE)
    if evidence.submit_terminal_state in _VALIDATION_SUBMIT_STATES:
        return FailureClass(KIND_PRODUCT, SIGNATURE_VALIDATION_FAILURE)
    if evidence.terminal_state == "timeout":
        return FailureClass(KIND_INFRASTRUCTURE, SIGNATURE_TIMEOUT)
    if evidence.terminal_state == "killed":
        return FailureClass(KIND_INFRASTRUCTURE, SIGNATURE_MEMORY)
    if _SINGLESTORE_1712_RE.search(evidence.failure_text):
        return FailureClass(KIND_INFRASTRUCTURE, SIGNATURE_SINGLESTORE_1712)
    if _DOWNLOAD_ERROR_RE.search(evidence.failure_text):
        return FailureClass(KIND_INFRASTRUCTURE, SIGNATURE_DOWNLOAD)
    if evidence.submit_terminal_state == "query_failure":
        return FailureClass(KIND_PRODUCT, SIGNATURE_QUERY_FAILURE)
    return FailureClass(KIND_PRODUCT, SIGNATURE_CRASH)
