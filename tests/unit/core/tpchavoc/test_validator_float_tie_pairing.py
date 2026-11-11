from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.validation import ResultValidator, ValidationError, calculate_checksum

pytestmark = [pytest.mark.unit, pytest.mark.medium]

TOTAL = -0.7541713805310839
ULP_NOISE = 2e-16


@pytest.mark.parametrize("noise", [ULP_NOISE, -ULP_NOISE])
def test_rows_tied_up_to_float_noise_pair_by_their_other_columns(noise):
    original = [(TOTAL, 0, "detail"), (TOTAL, 1, "subtotal")]
    variant = [(TOTAL + noise, 0, "detail"), (TOTAL, 1, "subtotal")]

    assert ResultValidator().validate_results_exact(original, variant, query_id=36, variant_id=1)


@pytest.mark.parametrize("noise", [ULP_NOISE, -ULP_NOISE])
def test_the_same_holds_for_the_multiset_comparison(noise):
    original = [(TOTAL, 0), (TOTAL, 1)]
    variant = [(TOTAL + noise, 0), (TOTAL, 1)]

    assert ResultValidator()._multisets_equal(original, variant)


def test_a_real_difference_still_mismatches():
    original = [(TOTAL, 0), (TOTAL, 1)]
    variant = [(TOTAL + 1e-6, 0), (TOTAL, 1)]

    with pytest.raises(ValidationError, match="Value mismatch"):
        ResultValidator().validate_results_exact(original, variant, query_id=36, variant_id=1)


def test_rows_with_no_float_cells_order_as_before():
    validator = ResultValidator()
    rows = [("b", 2), ("a", 3), ("a", 1)]

    assert sorted(rows, key=validator._pairing_sort_key) == sorted(rows)


def test_the_value_digest_still_sorts_on_the_exact_float():
    low, high = TOTAL, TOTAL + ULP_NOISE
    rows = [(high, 1), (low, 0)]

    assert calculate_checksum(rows) == calculate_checksum([(low, 0), (high, 1)])


def test_pairing_respects_configured_tolerance():
    original = [(100.0, 0), (100.0, 1)]
    variant = [(100.00005, 0), (100.0, 1)]

    assert ResultValidator(tolerance=1e-3).validate_results_exact(original, variant, query_id=1, variant_id=1)


def test_pairing_handles_values_straddling_decimal_rounding_boundary():
    val1 = 1.0000000049999999
    val2 = 1.0000000050000001
    original = [(val1, 0), (val1, 1)]
    variant = [(val2, 0), (val1, 1)]

    assert ResultValidator().validate_results_exact(original, variant, query_id=1, variant_id=1)


