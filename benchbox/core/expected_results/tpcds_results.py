# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os

from benchbox.core.expected_results.models import (
    BenchmarkExpectedResults,
    ValidationMode,
)
from benchbox.core.expected_results.row_count_references import (
    QUERY_IDS,
    expected_query_results,
    has_reference,
    load_row_count_reference,
    reference_scales,
    stream_query_results,
)

logger = logging.getLogger(__name__)

TPCDS_REFERENCE_RNGSEED_BASE = 1001


def tpcds_reference_seed(seed: int | None, stream_id: int | None = 0) -> bool:
    from benchbox.core.tpcds.power_test import power_parameter_seed

    stream = stream_id or 0
    return power_parameter_seed(seed, stream) == TPCDS_REFERENCE_RNGSEED_BASE + stream


def set_query_validation_mode(mode: ValidationMode | None) -> None:
    from benchbox.core.validation.query_validation import set_validation_mode_context

    set_validation_mode_context(mode)
    if mode is not None:
        logger.info(f"Query validation mode override set to: {mode.value}")


def set_config_validation_mode(mode_str: str | None) -> None:
    set_query_validation_mode(parse_validation_mode(mode_str))


def parse_validation_mode(mode_str: str | None) -> ValidationMode | None:
    if mode_str is None:
        return None
    normalized = mode_str.lower()
    if normalized == "disabled":
        return ValidationMode.SKIP
    try:
        return ValidationMode(normalized)
    except ValueError:
        logger.warning(
            f"Invalid config validation mode: {mode_str}. "
            "Valid values: exact, loose, range, skip, disabled. Using default: skip"
        )
        return ValidationMode.SKIP


def get_query_validation_mode() -> ValidationMode:
    env_mode = os.environ.get("BENCHBOX_QUERY_VALIDATION_MODE", "").lower()
    if env_mode:
        if env_mode == "disabled":
            return ValidationMode.SKIP
        try:
            mode = ValidationMode(env_mode)
            logger.info(f"Using query validation mode from environment: {mode.value}")
            return mode
        except ValueError:
            logger.warning(
                f"Invalid BENCHBOX_QUERY_VALIDATION_MODE: {env_mode}. "
                f"Valid values: exact, loose, range, skip, disabled. Using default: skip"
            )

    return ValidationMode.SKIP


_TPCDS_MULTI_PART_QUERY_IDS = frozenset({"14a", "14b", "23a", "23b", "24a", "24b", "39a", "39b"})


def tpcds_known_query_ids() -> frozenset[str]:
    known = set(QUERY_IDS["tpcds"])
    known.update(str(number) for number in range(1, 100))
    known.update(_TPCDS_MULTI_PART_QUERY_IDS)
    return frozenset(known)


def get_tpcds_expected_results(scale_factor: float = 1.0) -> BenchmarkExpectedResults | None:
    if not has_reference("tpcds", scale_factor):
        logger.info(
            f"TPC-DS expected results are only available for the reference scale factors "
            f"{', '.join(f'{sf:g}' for sf in reference_scales('tpcds'))}. "
            f"Validation will be skipped for SF={scale_factor}."
        )
        return None

    reference = load_row_count_reference("tpcds", scale_factor)
    query_results = expected_query_results(reference, 0)
    return BenchmarkExpectedResults(
        benchmark_name="tpcds",
        scale_factor=scale_factor,
        query_results=query_results,
        known_query_ids=tpcds_known_query_ids(),
        metadata={
            "source": f"BenchBox TPC-DS row-count reference, {reference.label}",
            "reference_kind": reference.kind,
            "total_queries": len(query_results),
            "reference_rngseed": reference.streams[0].seed,
            "provenance": dict(reference.provenance),
        },
        stream_results=stream_query_results(reference),
    )


from benchbox.core.expected_results.registry import register_benchmark_provider

register_benchmark_provider("tpcds", get_tpcds_expected_results)
