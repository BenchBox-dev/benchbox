# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from benchbox.core.results.status import result_non_clean_reason
from benchbox.core.results.subscale_policy import result_compliance_class
from benchbox.validation.bundle import CLI_REFUSED_COMPLIANCE_CLASSES


@dataclass(frozen=True)
class PublishAdmissionDecision:
    allowed: bool
    reason: str | None = None
    code: str | None = None


def publish_admission(result: Any, label: str) -> PublishAdmissionDecision:
    compliance_class = result_compliance_class(result)
    if compliance_class in CLI_REFUSED_COMPLIANCE_CLASSES and label.lower() != "unofficial-research":
        return PublishAdmissionDecision(
            allowed=False,
            code="unofficial_compliance",
            reason=f"compliance_class={compliance_class}",
        )
    non_clean = result_non_clean_reason(result)
    if non_clean:
        return PublishAdmissionDecision(
            allowed=False,
            code="non_clean",
            reason=non_clean,
        )
    return PublishAdmissionDecision(allowed=True)
