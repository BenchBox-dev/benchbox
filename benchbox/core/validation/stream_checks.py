from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache

from benchbox.core.expected_results.models import ValidationMode, ValidationResult
from benchbox.core.expected_results.row_count_references import (
    INTEGRITY_BENCHMARKS,
    QUERY_IDS,
    RowCountReference,
    has_reference,
    load_row_count_reference,
)
from benchbox.core.expected_results.structural_bounds import StructuralBound, structural_bound
from benchbox.core.expected_results.tpch_results import TPCH_LOOSE_QUERY_IDS, TPCH_RANGE_ROW_COUNT_BOUNDS
from benchbox.core.results.query_status import (
    DATA_REFRESHED_REASON,
    NO_REFERENCE_REASON,
    PARAMETER_SENSITIVE_REASON,
    REFERENCE_DATA_DIFFERS_REASON,
)

CHECK_EXACT = "exact"
CHECK_INVARIANT = "invariant"
CHECK_RANGE = "range"
CHECK_NEVER_EMPTY = "never_empty"
CHECK_STRUCTURAL = "structural"
ROW_CHECK_KINDS = (CHECK_EXACT, CHECK_INVARIANT, CHECK_RANGE, CHECK_NEVER_EMPTY, CHECK_STRUCTURAL)
SETTLING_PROCEDURE = "docs/agent/maintainer-bundles.md (Settling a disputed stream check)"
TPCH_RANGE_SCALE = 1.0
LOOSE_TOLERANCE = 0.5


@dataclass(frozen=True)
class QueryGeneration:
    seed: int | None = None
    streams_index: int | None = None


UNESTABLISHED_DATA_STAMP = ""

_generation: ContextVar[QueryGeneration | None] = ContextVar("query_generation", default=None)
_run_data_stamp: str | None = None


def current_query_generation() -> QueryGeneration | None:
    return _generation.get()


@contextmanager
def query_generation_context(generation: QueryGeneration | None) -> Iterator[None]:
    token = _generation.set(generation)
    try:
        yield
    finally:
        _generation.reset(token)


def current_run_data_stamp() -> str | None:
    return _run_data_stamp


@contextmanager
def run_data_stamp_context(stamp: str | None) -> Iterator[None]:
    global _run_data_stamp
    previous = _run_data_stamp
    _run_data_stamp = stamp
    try:
        yield
    finally:
        _run_data_stamp = previous


def canonical_benchmark(benchmark_type: str) -> str | None:
    name = benchmark_type.strip().lower().replace("-", "")
    return name if name in INTEGRITY_BENCHMARKS else None


def normalize_statement_id(benchmark: str, query_id: str | int) -> str:
    text = str(query_id).strip()
    if benchmark == "tpcds":
        match = re.fullmatch(r"[Qq]?(\d+)([a-dA-D]?)", text)
        return f"{int(match.group(1))}{match.group(2).lower()}" if match else text
    match = re.search(r"(\d+)", text)
    return str(int(match.group(1))) if match else text


@lru_cache(maxsize=16)
def _reference(benchmark: str, scale_factor: float) -> RowCountReference | None:
    if not has_reference(benchmark, scale_factor):
        return None
    return load_row_count_reference(benchmark, scale_factor, require_current=False)


def clear_reference_cache() -> None:
    _reference.cache_clear()


def _short(stamp: str | None) -> str:
    return (stamp or "unknown")[:12]


def _result(
    statement: str,
    actual: int,
    generation: QueryGeneration,
    *,
    valid: bool,
    check: str | None = None,
    bound: str | None = None,
    expected: int | None = None,
    skip: str | None = None,
    unvalidated: bool = False,
    error: str | None = None,
    reference: RowCountReference | None = None,
) -> ValidationResult:
    result = ValidationResult(
        is_valid=valid,
        query_id=statement,
        expected_row_count=expected,
        actual_row_count=actual,
        validation_mode=ValidationMode.SKIP if skip or unvalidated else ValidationMode.EXACT,
        error_message=error,
        warning_message=skip,
        unvalidated=unvalidated,
    )
    result.check = check
    result.bound = bound
    result.stream_checked = True
    result.seed = generation.seed
    result.streams_index = generation.streams_index
    if reference is not None and valid and not skip and not unvalidated:
        result.reference_kind = reference.kind
        result.reference_label = reference.label
    return result


def _failure(
    statement: str,
    benchmark: str,
    actual: int,
    generation: QueryGeneration,
    check: str,
    bound: str,
    detail: str,
    reference: RowCountReference | None,
    expected: int | None = None,
) -> ValidationResult:
    if reference is not None and reference.integrity is not None and check in (CHECK_INVARIANT, CHECK_NEVER_EMPTY):
        basis = f"sample of {reference.integrity.draws} draws"
    elif check == CHECK_STRUCTURAL:
        basis = "template-derived bound"
    else:
        basis = "recorded reference"
    stamp = _short(reference.data_stamp if reference is not None else None)
    message = (
        f"Stream check FAILED: {benchmark} query {statement} returned {actual} rows; {check} check requires {bound} "
        f"({detail}; {basis}; data stamp {stamp}). Settle a disputed check as in {SETTLING_PROCEDURE}."
    )
    return _result(
        statement,
        actual,
        generation,
        valid=False,
        check=check,
        bound=bound,
        expected=expected,
        error=message,
        reference=reference,
    )


def _structural(
    benchmark: str, statement: str, actual: int, generation: QueryGeneration, reference: RowCountReference | None
) -> tuple[StructuralBound, ValidationResult | None]:
    bound = structural_bound(benchmark, statement)
    if bound.one_row and actual != 1:
        return bound, _failure(
            statement, benchmark, actual, generation, CHECK_STRUCTURAL, "exactly 1", bound.describe(), reference
        )
    if bound.limit is not None and actual > bound.limit:
        return bound, _failure(
            statement,
            benchmark,
            actual,
            generation,
            CHECK_STRUCTURAL,
            f"at most {bound.limit}",
            bound.describe(),
            reference,
        )
    return bound, None


def _structural_bound_text(bound: StructuralBound) -> str:
    return "exactly 1" if bound.one_row else f"at most {bound.limit}"


def _skip(
    statement: str,
    actual: int,
    generation: QueryGeneration,
    bound: StructuralBound,
    reason: str,
    text: str,
) -> ValidationResult:
    return _result(
        statement,
        actual,
        generation,
        valid=True,
        check=CHECK_STRUCTURAL if bound else None,
        bound=_structural_bound_text(bound) if bound else None,
        skip=f"{reason}: {text} Actual rows returned: {actual}",
    )


def _range_bounds(
    benchmark: str, statement: str, scale_factor: float, reference: RowCountReference
) -> tuple[int, int] | None:
    if benchmark != "tpch" or scale_factor != TPCH_RANGE_SCALE:
        return None
    if statement in TPCH_RANGE_ROW_COUNT_BOUNDS:
        return TPCH_RANGE_ROW_COUNT_BOUNDS[statement]
    if statement in TPCH_LOOSE_QUERY_IDS:
        default = reference.exact_stream(None, None)
        if default is not None:
            count = default.row_counts[statement]
            return int(count * (1 - LOOSE_TOLERANCE)), int(count * (1 + LOOSE_TOLERANCE))
    return None


def decide_row_count(
    benchmark_type: str,
    query_id: str | int,
    actual_row_count: int,
    scale_factor: float | None,
    *,
    generation: QueryGeneration | None = None,
    data_refreshed: bool = False,
    run_data_stamp: str | None = None,
    run_query_generation_hash: str | None = None,
) -> ValidationResult:
    benchmark = canonical_benchmark(benchmark_type)
    if benchmark is None:
        raise ValueError(f"stream checks cover TPC-H and TPC-DS, not {benchmark_type!r}")
    scale = float(scale_factor or 1.0)
    statement = normalize_statement_id(benchmark, query_id)
    if statement not in QUERY_IDS[benchmark]:
        return ValidationResult(
            is_valid=False,
            query_id=str(query_id),
            expected_row_count=None,
            actual_row_count=actual_row_count,
            validation_mode=ValidationMode.EXACT,
            error_message=(
                f"Row-count lookup failed: query '{query_id}' (normalized '{statement}') is not a {benchmark} "
                f"statement. Check the query id and variant key. The query is not treated as validated. "
                f"Actual rows returned: {actual_row_count}"
            ),
        )
    generation = generation or QueryGeneration()
    reference = _reference(benchmark, scale)

    bound, failure = _structural(benchmark, statement, actual_row_count, generation, reference)
    if failure is not None:
        return failure
    if data_refreshed:
        return _skip(
            statement,
            actual_row_count,
            generation,
            bound,
            DATA_REFRESHED_REASON,
            f"query {query_id!r} ran after refresh functions changed the {benchmark} data, so reference counts do not apply.",
        )
    if reference is None:
        result = _result(
            statement,
            actual_row_count,
            generation,
            valid=True,
            check=CHECK_STRUCTURAL if bound else None,
            bound=_structural_bound_text(bound) if bound else None,
            unvalidated=True,
            skip=(
                f"Unvalidated: {NO_REFERENCE_REASON} (benchmark '{benchmark}', scale factor {scale:g}, "
                f"query '{query_id}'). The row count was not checked. Actual rows returned: {actual_row_count}"
            ),
        )
        return result

    from benchbox.utils.query_generation import current_query_generation_hash

    inapplicable = reference.inapplicable_reason(
        run_data_stamp if run_data_stamp is not None else current_run_data_stamp(),
        run_query_generation_hash or current_query_generation_hash(benchmark),
    )
    if inapplicable is not None:
        return _skip(
            statement,
            actual_row_count,
            generation,
            bound,
            REFERENCE_DATA_DIFFERS_REASON,
            inapplicable.removeprefix(f"{REFERENCE_DATA_DIFFERS_REASON}: ") + ".",
        )
    return _decide_against_reference(benchmark, statement, actual_row_count, scale, generation, reference, bound)


def _decide_against_reference(
    benchmark: str,
    statement: str,
    actual: int,
    scale: float,
    generation: QueryGeneration,
    reference: RowCountReference,
    bound: StructuralBound,
) -> ValidationResult:
    entry = reference.exact_stream(generation.seed, generation.streams_index)
    if entry is not None:
        expected = entry.row_counts[statement]
        if actual != expected:
            return _failure(
                statement,
                benchmark,
                actual,
                generation,
                CHECK_EXACT,
                f"exactly {expected}",
                f"reference stream {entry.query_parameters}",
                reference,
                expected,
            )
        return _result(
            statement,
            actual,
            generation,
            valid=True,
            check=CHECK_EXACT,
            bound=f"exactly {expected}",
            expected=expected,
            reference=reference,
        )
    integrity = reference.integrity
    if integrity is not None and statement in integrity.invariant_counts:
        expected = integrity.invariant_counts[statement]
        if actual != expected:
            return _failure(
                statement,
                benchmark,
                actual,
                generation,
                CHECK_INVARIANT,
                f"exactly {expected}",
                "count never varied across the sample",
                reference,
                expected,
            )
        return _result(
            statement,
            actual,
            generation,
            valid=True,
            check=CHECK_INVARIANT,
            bound=f"exactly {expected}",
            expected=expected,
            reference=reference,
        )
    bounds = _range_bounds(benchmark, statement, scale, reference)
    if bounds is not None:
        low, high = bounds
        text = f"{low} to {high}"
        if not low <= actual <= high:
            return _failure(
                statement,
                benchmark,
                actual,
                generation,
                CHECK_RANGE,
                text,
                "range from the parameter domain",
                reference,
            )
        return _result(statement, actual, generation, valid=True, check=CHECK_RANGE, bound=text, reference=reference)
    if integrity is not None and statement in integrity.never_empty:
        if actual < 1:
            return _failure(
                statement,
                benchmark,
                actual,
                generation,
                CHECK_NEVER_EMPTY,
                "at least 1 row",
                "never returned zero rows across the sample",
                reference,
            )
        return _result(
            statement,
            actual,
            generation,
            valid=True,
            check=CHECK_NEVER_EMPTY,
            bound="at least 1 row",
            reference=reference,
        )
    return _skip(
        statement,
        actual,
        generation,
        bound,
        PARAMETER_SENSITIVE_REASON,
        f"query {statement!r} row count at scale factor {scale:g} depends on the substitution parameters; "
        "no exact, invariant, range or must-return-rows check applies.",
    )


def stream_checks_record(benchmark_id: str, scale_factor: float, query_rows: list[dict] | None) -> dict | None:
    benchmark = canonical_benchmark(benchmark_id)
    if benchmark is None or not isinstance(query_rows, list):
        return None
    counts: dict[str, int] = {}
    for row in query_rows:
        evidence = row.get("row_count_validation") if isinstance(row, dict) else None
        check = evidence.get("check") if isinstance(evidence, dict) else None
        if check in ROW_CHECK_KINDS:
            counts[check] = counts.get(check, 0) + 1
    if not counts:
        return None
    reference = _reference(benchmark, float(scale_factor))
    references = []
    if reference is not None and reference.file_sha256 and reference.integrity is not None:
        references.append(
            {
                "benchmark": benchmark,
                "scale_factor": reference.scale_factor,
                "file": f"{benchmark}_row_counts_sf{reference.scale_factor:g}.json",
                "sha256": reference.file_sha256,
                "data_stamp": reference.data_stamp,
                "query_generation_hash": reference.query_generation_hash,
                "sample_draws": reference.integrity.draws,
            }
        )
    return {"references": references, "checks": {kind: counts[kind] for kind in ROW_CHECK_KINDS if kind in counts}}
