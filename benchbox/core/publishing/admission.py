# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from benchbox.core.results.submit_classification import result_public_refusal_reason
from benchbox.core.results.subscale_policy import normalize_tpc_benchmark_id
from benchbox.core.results.tpcdi_audit import LEGACY_TPCDI_REFUSAL, legacy_tpcdi_note


@dataclass(frozen=True)
class PublishAdmissionDecision:
    allowed: bool
    reason: str | None = None
    code: str | None = None


def publish_admission(result: Any, label: str) -> PublishAdmissionDecision:
    if legacy_tpcdi_note(result) is not None:
        return PublishAdmissionDecision(allowed=False, code="legacy_tpcdi", reason=LEGACY_TPCDI_REFUSAL)
    refusal = result_public_refusal_reason(result)
    if refusal is not None and (
        refusal not in {"unofficial_nonstandard", "unofficial_subscale"} or label.lower() != "unofficial-research"
    ):
        return PublishAdmissionDecision(
            allowed=False,
            code="unofficial_compliance"
            if refusal in {"unofficial_nonstandard", "unofficial_subscale"}
            else "non_clean",
            reason=refusal,
        )
    return PublishAdmissionDecision(allowed=True)


TPC_BENCHMARK_DISPLAY_NAMES = {"tpch": "TPC-H", "tpcds": "TPC-DS", "tpcdi": "TPC-DI"}


def unofficial_result_subject(result: Any) -> str:
    display_name = TPC_BENCHMARK_DISPLAY_NAMES.get(normalize_tpc_benchmark_id(getattr(result, "benchmark_name", None)))
    return f"an unofficial {display_name or 'TPC'} configuration"


def is_tpcds_result(result: Any) -> bool:
    return normalize_tpc_benchmark_id(getattr(result, "benchmark_name", None)) == "tpcds"
