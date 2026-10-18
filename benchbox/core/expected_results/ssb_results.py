from __future__ import annotations

import logging
import math

from benchbox.core.expected_results.models import BenchmarkExpectedResults, ExpectedQueryResult, ValidationMode

logger = logging.getLogger(__name__)

SSB_EXPECTED_ROW_COUNTS: dict[float, dict[str, int]] = {
    0.01: {
        "1.1": 1,
        "1.2": 1,
        "1.3": 1,
        "2.1": 86,
        "2.2": 19,
        "2.3": 2,
        "3.1": 120,
        "3.2": 0,
        "3.3": 0,
        "3.4": 0,
        "4.1": 35,
        "4.2": 18,
        "4.3": 8,
    },
    0.1: {
        "1.1": 1,
        "1.2": 1,
        "1.3": 1,
        "2.1": 280,
        "2.2": 56,
        "2.3": 7,
        "3.1": 150,
        "3.2": 398,
        "3.3": 20,
        "3.4": 2,
        "4.1": 35,
        "4.2": 100,
        "4.3": 242,
    },
    1.0: {
        "1.1": 1,
        "1.2": 1,
        "1.3": 1,
        "2.1": 280,
        "2.2": 56,
        "2.3": 7,
        "3.1": 150,
        "3.2": 600,
        "3.3": 24,
        "3.4": 2,
        "4.1": 35,
        "4.2": 100,
        "4.3": 1854,
    },
    10.0: {
        "1.1": 1,
        "1.2": 1,
        "1.3": 1,
        "2.1": 280,
        "2.2": 56,
        "2.3": 7,
        "3.1": 150,
        "3.2": 600,
        "3.3": 24,
        "3.4": 4,
        "4.1": 35,
        "4.2": 100,
        "4.3": 3992,
    },
}


def ssb_expected_row_counts(scale_factor: float) -> dict[str, int] | None:
    for recorded_scale, counts in SSB_EXPECTED_ROW_COUNTS.items():
        if math.isclose(recorded_scale, scale_factor, rel_tol=1e-9):
            return counts
    return None


def normalize_ssb_query_id(query_id: object) -> str:
    return str(query_id).strip().upper().removeprefix("Q")


def get_ssb_expected_results(scale_factor: float = 1.0) -> BenchmarkExpectedResults | None:
    counts = ssb_expected_row_counts(scale_factor)
    if counts is None:
        logger.info(
            f"SSB expected results only available for scale factors "
            f"{sorted(SSB_EXPECTED_ROW_COUNTS)}. Validation will be skipped for SF={scale_factor}."
        )
        return None

    query_results = {
        query_id: ExpectedQueryResult(
            query_id=query_id,
            scale_factor=scale_factor,
            expected_row_count=row_count,
            validation_mode=ValidationMode.EXACT,
            notes=f"Expected result from SSB recorded row counts for SF={scale_factor}",
        )
        for query_id, row_count in counts.items()
    }

    return BenchmarkExpectedResults(
        benchmark_name="ssb",
        scale_factor=scale_factor,
        query_results=query_results,
        metadata={
            "source": "SSB recorded row counts",
            "total_queries": len(query_results),
        },
    )


from benchbox.core.expected_results.registry import register_benchmark_provider

register_benchmark_provider("ssb", get_ssb_expected_results)
