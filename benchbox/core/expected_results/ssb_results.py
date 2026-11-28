from __future__ import annotations

import logging

from benchbox.core.expected_results.models import BenchmarkExpectedResults
from benchbox.core.expected_results.row_count_references import (
    QUERY_IDS,
    expected_query_results,
    has_reference,
    load_row_count_reference,
    reference_scales,
)

logger = logging.getLogger(__name__)

SSB_QUERY_IDS: frozenset[str] = frozenset(QUERY_IDS["ssb"])


def ssb_expected_row_counts(scale_factor: float) -> dict[str, int] | None:
    if not has_reference("ssb", scale_factor):
        return None
    return dict(load_row_count_reference("ssb", scale_factor).streams[0].row_counts)


def normalize_ssb_query_id(query_id: object) -> str:
    return str(query_id).strip().upper().removeprefix("Q")


def get_ssb_expected_results(scale_factor: float = 1.0) -> BenchmarkExpectedResults | None:
    if not has_reference("ssb", scale_factor):
        logger.info(
            f"SSB expected results only available for scale factors "
            f"{list(reference_scales('ssb'))}. Validation will be skipped for SF={scale_factor}."
        )
        return None

    reference = load_row_count_reference("ssb", scale_factor)
    query_results = expected_query_results(reference, 0)
    return BenchmarkExpectedResults(
        benchmark_name="ssb",
        scale_factor=scale_factor,
        query_results=query_results,
        known_query_ids=SSB_QUERY_IDS,
        metadata={
            "source": f"SSB row-count reference, {reference.label}",
            "reference_kind": reference.kind,
            "total_queries": len(query_results),
            "provenance": dict(reference.provenance),
        },
    )


from benchbox.core.expected_results.registry import register_benchmark_provider

register_benchmark_provider("ssb", get_ssb_expected_results)
