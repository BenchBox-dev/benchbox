from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from benchbox.core.results import corpus_rule
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import build_result_payload


def facts_for_result(result: BenchmarkResults) -> corpus_rule.Facts:
    return corpus_rule.facts_from_result(build_result_payload(result))


def outcome_for_result(result: Any) -> corpus_rule.Outcome:
    if not isinstance(result, BenchmarkResults):
        return outcome_from_stored_facts(None)
    return corpus_rule.derive_outcome(facts_for_result(result))


def facts_to_json(facts: corpus_rule.Facts) -> str:
    return json.dumps(
        {
            "benchmark": facts.benchmark,
            "scale_factor": facts.scale_factor,
            "phases": None if facts.phases is None else sorted(facts.phases),
            "streams": facts.streams,
            "gateway_routed": facts.gateway_routed,
            "known_defect": facts.known_defect,
            "validation_failed": facts.validation_failed,
            "public_validation_refused": facts.public_validation_refused,
            "row_counts_checked": facts.row_counts_checked,
        },
        sort_keys=True,
    )


def facts_from_json(text: str | None) -> corpus_rule.Facts | None:
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, Mapping) or not isinstance(payload.get("phases"), list):
        return None
    return corpus_rule.Facts(
        benchmark=payload.get("benchmark"),
        scale_factor=payload.get("scale_factor"),
        phases=frozenset(str(phase) for phase in payload["phases"]),
        streams=payload.get("streams"),
        gateway_routed=bool(payload.get("gateway_routed")),
        known_defect=bool(payload.get("known_defect")),
        validation_failed=bool(payload.get("validation_failed")),
        public_validation_refused=bool(payload.get("public_validation_refused")),
        row_counts_checked=bool(payload.get("row_counts_checked", True)),
    )


def outcome_from_stored_facts(text: str | None) -> corpus_rule.Outcome:
    facts = facts_from_json(text)
    if facts is None:
        facts = corpus_rule.Facts(benchmark=None, scale_factor=None, phases=None)
    return corpus_rule.derive_outcome(facts)


POWER_SCORE_SOURCES = frozenset(
    {
        corpus_rule.SCORE_QPH_AND_POWER,
        corpus_rule.SCORE_FROM_TIMINGS,
        corpus_rule.SCORE_POWER_QUERY_TIMINGS_ONLY,
        corpus_rule.SCORE_POWER_FROM_TIMINGS,
    }
)
THROUGHPUT_SCORE_SOURCES = frozenset(
    {
        corpus_rule.SCORE_QPH_AND_POWER,
        corpus_rule.SCORE_FROM_TIMINGS,
        corpus_rule.SCORE_THROUGHPUT_FROM_TIMINGS,
    }
)
QPH_SCORE_SOURCES = frozenset({corpus_rule.SCORE_QPH_AND_POWER})
SPEC_METRIC_SCORE_SOURCES = {
    "power_at_size": POWER_SCORE_SOURCES,
    "throughput_at_size": THROUGHPUT_SCORE_SOURCES,
    "qph_at_size": QPH_SCORE_SOURCES,
}


def supports_spec_metric(outcome: corpus_rule.Outcome, metric: str) -> bool:
    if outcome.held_reason is not None:
        return False
    return outcome.score_source in SPEC_METRIC_SCORE_SOURCES.get(metric, ())


def admitted_for_ranking(outcome: corpus_rule.Outcome, include_unofficial: bool, metric: str) -> bool:
    if metric in SPEC_METRIC_SCORE_SOURCES and not supports_spec_metric(outcome, metric):
        return False
    if include_unofficial:
        return outcome.maintainer_admission and outcome.held_reason is None and outcome.score_source is not None
    return outcome.ranked and outcome.public_admission


def facts_describe_result(facts: corpus_rule.Facts, result: BenchmarkResults) -> bool:
    benchmark = corpus_rule.normalize_benchmark_id(getattr(result, "benchmark_id", None))
    scale = getattr(result, "scale_factor", None)
    return (
        benchmark is not None
        and benchmark == corpus_rule.normalize_benchmark_id(facts.benchmark)
        and isinstance(scale, int | float)
        and facts.scale_factor is not None
        and abs(float(scale) - facts.scale_factor) < 1e-9
    )


def outcome_words(outcome: corpus_rule.Outcome) -> tuple[str, str]:
    if outcome.class_name == corpus_rule.CLASS_UNDETERMINED:
        missing = ", ".join(outcome.missing_facts) or corpus_rule.MISSING_PHASES
        return (
            corpus_rule.CLASS_UNDETERMINED,
            f"Undetermined: facts missing ({missing}); re-import the result file to derive its class",
        )
    if outcome.held_reason == corpus_rule.HELD_NO_TIMED_PHASE:
        return outcome.class_name, "Not a result: no timed phase ran"
    return outcome.class_name, "; ".join(outcome.labels)
