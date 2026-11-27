from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchbox.core.execution_engine import NOT_CAPTURED
from benchbox.core.results import comparison_identity, corpus_rule
from benchbox.core.results.schema import NONSTANDARD_COMPLIANCE_CLASS, RECORDED_COMPLIANCE_CLASS_BY_RULE_CLASS

LANE_PHASES = {
    corpus_rule.LANE_COMBINED: "combined",
    corpus_rule.LANE_POWER_ONLY: "power",
    corpus_rule.LANE_SUBSCALE: "power",
    corpus_rule.LANE_THROUGHPUT: "throughput",
}


class UndeterminedOutcomeError(ValueError):
    pass


def lane_phase(lane: str, data: Mapping[str, Any]) -> str:
    fixed = LANE_PHASES.get(lane)
    if fixed is not None:
        return fixed
    ran = corpus_rule.phases_ran(data) or frozenset()
    only_throughput = corpus_rule.PHASE_THROUGHPUT in ran and corpus_rule.PHASE_POWER not in ran
    return LANE_PHASES[corpus_rule.LANE_THROUGHPUT if only_throughput else corpus_rule.LANE_POWER_ONLY]


def derive_outcome(
    data: Mapping[str, Any], bundle_path: Path, *, validation_status: str | None, failed_query_count: int
) -> corpus_rule.Outcome:
    facts = corpus_rule.facts_from_result(
        data,
        known_defects=corpus_rule.sidecar_known_defects(bundle_path),
        validation_status=validation_status,
        failed_queries=failed_query_count,
    )
    outcome = corpus_rule.derive_outcome(facts)
    if outcome.class_name == corpus_rule.CLASS_UNDETERMINED:
        raise UndeterminedOutcomeError(
            f"{bundle_path}: the corpus rule cannot place this result; missing facts: {', '.join(outcome.missing_facts)}"
        )
    return outcome


def unranked_reason(outcome: corpus_rule.Outcome) -> str | None:
    if outcome.lanes:
        return None
    return outcome.held_reason or f"not_ranked_{outcome.class_name}"


def compliance_class(outcome: corpus_rule.Outcome) -> str | None:
    if outcome.held_reason == corpus_rule.HELD_GATEWAY_ROUTED:
        return NONSTANDARD_COMPLIANCE_CLASS
    return RECORDED_COMPLIANCE_CLASS_BY_RULE_CLASS.get(outcome.class_name)


def observed_engine(recorded: str | None) -> str | None:
    return None if not recorded or recorded == NOT_CAPTURED else recorded


def corpus_columns(
    outcome: corpus_rule.Outcome,
    data: Mapping[str, Any],
    *,
    variant_id: str,
    recorded_observed_engine: str | None,
) -> dict[str, Any]:
    identity = comparison_identity.comparison_identity(data, variant_id)
    version = identity.comparison_version
    return {
        "compliance_class": compliance_class(outcome),
        "corpus_class": outcome.class_name,
        "corpus_lanes": list(outcome.lanes),
        "corpus_labels": list(outcome.labels),
        "comparison_identity": identity.label,
        "comparison_version": version.text,
        "version_is_release": version.is_release,
        "observed_engine": observed_engine(recorded_observed_engine),
        "row_count_evidence_label": (
            corpus_rule.ROW_COUNTS_NOT_CHECKED_LABEL
            if corpus_rule.ROW_COUNTS_NOT_CHECKED_LABEL in outcome.labels
            else None
        ),
        "iteration_label": (
            corpus_rule.SINGLE_ITERATION_LABEL if corpus_rule.SINGLE_ITERATION_LABEL in outcome.labels else None
        ),
    }
