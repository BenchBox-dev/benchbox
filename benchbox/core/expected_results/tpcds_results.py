# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path

from benchbox.core.expected_results.loader import load_tpcds_expected_results
from benchbox.core.expected_results.models import (
    BenchmarkExpectedResults,
    ExpectedQueryResult,
    ValidationMode,
)

logger = logging.getLogger(__name__)

_SUBSCALE_REFERENCE_PATH = Path(__file__).with_name("reference_digests") / "tpcds_subscale_row_counts.json"


def _load_subscale_reference() -> dict:
    try:
        payload = json.loads(_SUBSCALE_REFERENCE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("benchmark") != "tpcds":
            return {}
        return payload
    except (OSError, ValueError) as exc:
        logger.warning(f"TPC-DS subscale row-count reference is unreadable: {_SUBSCALE_REFERENCE_PATH}: {exc}")
        return {}


def _materialize_subscale_reference() -> tuple[dict[float, dict[str, int]], frozenset[str], dict, int]:
    raw = _load_subscale_reference()
    try:
        counts = {
            float(scale): {str(query_id): int(row_count) for query_id, row_count in scale_counts.items()}
            for scale, scale_counts in raw.get("scales", {}).items()
        }
        sensitive = frozenset(str(query_id) for query_id in raw.get("parameter_sensitive_query_ids", []))
        provenance = dict(raw.get("provenance", {}))
        rngseed = int(raw.get("reference", {}).get("dsqgen_rngseed", 1001))
        return counts, sensitive, provenance, rngseed
    except (TypeError, ValueError, AttributeError) as exc:
        logger.warning(f"TPC-DS subscale row-count reference is malformed: {exc}")
        return {}, frozenset(), {}, 1001


(
    TPCDS_SUBSCALE_ROW_COUNTS,
    TPCDS_PARAMETER_SENSITIVE_QUERY_IDS,
    TPCDS_SUBSCALE_PROVENANCE,
    TPCDS_REFERENCE_RNGSEED,
) = _materialize_subscale_reference()


def tpcds_subscale_expected_row_counts(scale_factor: float) -> dict[str, int] | None:
    for recorded_scale, counts in TPCDS_SUBSCALE_ROW_COUNTS.items():
        if math.isclose(recorded_scale, scale_factor, rel_tol=1e-9):
            return counts
    return None


def tpcds_subscale_reference_seed(seed: int | None) -> bool:
    return seed is None or seed == 1


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


def get_tpcds_expected_results(scale_factor: float = 1.0) -> BenchmarkExpectedResults | None:
    if scale_factor != 1.0:
        subscale_counts = tpcds_subscale_expected_row_counts(scale_factor)
        if subscale_counts is not None:
            return BenchmarkExpectedResults(
                benchmark_name="tpcds",
                scale_factor=scale_factor,
                query_results={
                    query_id: ExpectedQueryResult(
                        query_id=query_id,
                        scale_factor=scale_factor,
                        expected_row_count=row_count,
                        validation_mode=ValidationMode.EXACT,
                        notes=(
                            f"Expected result from TPC-DS subscale recorded row counts for SF={scale_factor} "
                            f"at dsqgen RNGSEED {TPCDS_REFERENCE_RNGSEED} (stream 0)."
                        ),
                    )
                    for query_id, row_count in subscale_counts.items()
                },
                metadata={
                    "source": "TPC-DS subscale recorded row counts",
                    "total_queries": len(subscale_counts),
                    "reference_rngseed": TPCDS_REFERENCE_RNGSEED,
                    "provenance": TPCDS_SUBSCALE_PROVENANCE,
                },
            )
        logger.info(
            f"TPC-DS expected results only available for SF=1.0 and recorded subscale factors "
            f"{sorted(TPCDS_SUBSCALE_ROW_COUNTS)}. "
            f"Validation will be skipped for SF={scale_factor}. "
            f"Most TPC-DS queries are scale-dependent."
        )
        return None

    row_counts = load_tpcds_expected_results(scale_factor=1.0)

    query_results = {}

    scale_independent_queries = {}

    mode_notes = (
        "Cached TPC-DS answer-file expectation. The effective validation mode "
        "is resolved per run at validation time (default SKIP, safe because "
        "TPC-DS queries are parameterized with random substitution values "
        "controlled by RNGSEED while the answer files represent ONE specific "
        "parameterization). To enable EXACT validation, set "
        "BENCHBOX_QUERY_VALIDATION_MODE=exact or call "
        "set_query_validation_mode(ValidationMode.EXACT)."
    )

    for query_id, row_count in row_counts.items():
        is_scale_independent = scale_independent_queries.get(query_id, False)

        query_results[query_id] = ExpectedQueryResult(
            query_id=query_id,
            scale_factor=scale_factor,
            expected_row_count=row_count,
            validation_mode=ValidationMode.SKIP,
            scale_independent=is_scale_independent,
            notes=f"Expected result from TPC-DS answer file for SF={scale_factor}. {mode_notes}",
        )

    return BenchmarkExpectedResults(
        benchmark_name="tpcds",
        scale_factor=scale_factor,
        query_results=query_results,
        metadata={
            "source": "TPC-DS answer files",
            "answer_files_scale_factor": 1.0,
            "total_queries": len(query_results),
        },
    )


from benchbox.core.expected_results.registry import register_benchmark_provider

register_benchmark_provider("tpcds", get_tpcds_expected_results)
