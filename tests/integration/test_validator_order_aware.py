# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.validation import ResultValidator, ValidationError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.tpchavoc,
    pytest.mark.medium,
]


def _validator() -> ResultValidator:
    return ResultValidator(tolerance=1e-10)


def test_order_aware_reversed_order_is_a_mismatch():
    validator = _validator()
    reference = [(1, 100, "a"), (1, 90, "b"), (2, 50, "c"), (3, 10, "d")]
    order_by = [0, 1]
    assert validator.validate_results_exact(reference, list(reference), 3, 1, order_aware=True, order_by=order_by)
    with pytest.raises(ValidationError) as exc_info:
        validator.validate_results_exact(
            reference, list(reversed(reference)), 3, 1, order_aware=True, order_by=order_by, tie_aware=True
        )
    assert "ORDER BY" in str(exc_info.value)


def test_order_aware_passes_under_legacy_full_row_sort():
    validator = _validator()
    reference = [(1, 100, "a"), (1, 90, "b"), (2, 50, "c"), (3, 10, "d")]
    assert validator.validate_results_exact(reference, list(reversed(reference)), 3, 1)


def test_order_aware_tie_group_reshuffle_is_not_flagged():
    validator = _validator()
    reference = [(1, "a"), (2, "b"), (2, "c")]
    reshuffled = [(1, "a"), (2, "c"), (2, "b")]
    order_by = [0]
    assert validator.validate_results_exact(reference, reshuffled, 3, 1, order_aware=True, order_by=order_by)


def test_order_aware_truncated_limit_boundary_swap_is_not_flagged():
    validator = _validator()
    reference = [(9, "x"), (5, "a"), (5, "b")]
    variant = [(9, "x"), (5, "a"), (5, "c")]
    order_by = [0]
    assert validator.validate_results_exact(
        reference,
        variant,
        3,
        1,
        order_aware=True,
        order_by=order_by,
        tie_aware=True,
        final_key_tied_beyond_limit=True,
    )


def test_order_aware_complete_final_tie_group_value_bug_is_caught():
    validator = _validator()
    reference = [(9, "x"), (5, "a"), (5, "b")]
    bug = [(9, "x"), (5, "a"), (5, "c")]
    order_by = [0]
    with pytest.raises(ValidationError):
        validator.validate_results_exact(reference, bug, 3, 1, order_aware=True, order_by=order_by, tie_aware=True)


def test_order_aware_multi_row_final_tie_group_accepts_only_with_probe():
    validator = _validator()
    reference = [(9, "x"), (5, "a"), (5, "b")]
    variant = [(9, "x"), (5, "a"), (5, "c")]
    order_by = [0]
    assert validator.validate_results_exact(
        reference,
        variant,
        3,
        1,
        order_aware=True,
        order_by=order_by,
        tie_aware=True,
        final_key_tied_beyond_limit=True,
    )


def test_order_aware_value_bug_in_non_tie_row_is_caught():
    validator = _validator()
    reference = [(1, 100, "a"), (2, 90, "b"), (3, 50, "c")]
    bug = [(1, 100, "a"), (2, 90, "WRONG"), (3, 50, "c")]
    order_by = [0]
    with pytest.raises(ValidationError):
        validator.validate_results_exact(reference, bug, 3, 1, order_aware=True, order_by=order_by, tie_aware=True)


def test_order_aware_single_row_final_group_value_bug_is_caught():
    validator = _validator()
    reference = [(1, "ok"), (2, "good")]
    bug = [(1, "ok"), (2, "bad")]
    order_by = [0]
    with pytest.raises(ValidationError):
        validator.validate_results_exact(reference, bug, 3, 1, order_aware=True, order_by=order_by, tie_aware=True)


def test_order_aware_one_visible_row_boundary_tie_accepted_only_with_probe():
    validator = _validator()
    reference = [(10, "x"), (5, "a")]
    variant = [(10, "x"), (5, "b")]
    order_by = [0]
    assert validator.validate_results_exact(
        reference,
        variant,
        3,
        1,
        order_aware=True,
        order_by=order_by,
        tie_aware=True,
        final_key_tied_beyond_limit=True,
    )
    with pytest.raises(ValidationError):
        validator.validate_results_exact(reference, variant, 3, 1, order_aware=True, order_by=order_by, tie_aware=True)


def test_order_aware_dropped_column_is_a_clean_column_count_mismatch():
    validator = _validator()
    reference = [(1, 100, "a"), (2, 90, "b")]
    dropped = [(100, "a"), (90, "b")]
    order_by = [0]
    with pytest.raises(ValidationError) as exc_info:
        validator.validate_results_exact(reference, dropped, 3, 1, order_aware=True, order_by=order_by)
    assert "Column count mismatch" in str(exc_info.value)


def test_order_aware_falls_back_when_no_order_key():
    validator = _validator()
    reference = [(2, "b"), (1, "a")]
    same_multiset_reordered = [(1, "a"), (2, "b")]
    assert validator.validate_results_exact(reference, same_multiset_reordered, 3, 1, order_aware=True, order_by=None)
    assert validator.validate_results_exact(reference, same_multiset_reordered, 3, 1, order_aware=True, order_by=[])


def test_order_aware_out_of_range_key_falls_back_without_indexerror():
    validator = _validator()
    reference = [(1, "a"), (2, "b")]
    assert validator.validate_results_exact(reference, list(reference), 3, 1, order_aware=True, order_by=[5])
    assert validator.validate_results_exact(reference, list(reversed(reference)), 3, 1, order_aware=True, order_by=[5])


def test_none_mixed_column_equal_passes_without_crashing():
    validator = _validator()
    rows = [(1, None), (2, 5), (3, None)]
    assert validator.validate_results_exact(rows, list(rows), 3, 1)


def test_none_mixed_column_difference_is_a_clean_mismatch_not_an_error():
    validator = _validator()
    reference = [(1, None), (2, 5)]
    variant = [(1, 7), (2, 5)]
    with pytest.raises(ValidationError) as exc_info:
        validator.validate_results_exact(reference, variant, 3, 1)
    message = str(exc_info.value)
    assert "mismatch" in message.lower()
    assert "error:" not in message


def test_mixed_type_column_does_not_raise_typeerror():
    validator = _validator()
    rows = [(1, "x"), (2, 7)]
    assert validator.validate_results_exact(rows, list(reversed(rows)), 3, 1)
