# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from decimal import Decimal

import pytest

from benchbox.core.tpchavoc.validation import ResultValidator, ValidationError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.tpchavoc,
    pytest.mark.medium,
]


def test_numeric_equal_handles_float_vs_decimal_without_crashing():
    validator = ResultValidator(tolerance=1e-10)
    assert validator._numeric_values_equal(25.0, Decimal("25.0"))
    assert validator._numeric_values_equal(Decimal("25.0"), 25.0)


def test_numeric_equal_float_vs_decimal_within_tolerance():
    validator = ResultValidator(tolerance=1e-6)
    assert validator._numeric_values_equal(25.537587, Decimal("25.537587"))


def test_numeric_equal_float_vs_decimal_reports_real_mismatch():
    validator = ResultValidator(tolerance=1e-10)
    assert not validator._numeric_values_equal(25.537587116854997, Decimal("25.53"))


def test_aggregation_results_report_clean_mismatch_for_decimal_truncation():
    validator = ResultValidator(tolerance=1e-10)
    original = [("A", "F", 25.537587116854997)]
    variant = [("A", "F", Decimal("25.53"))]
    with pytest.raises(ValidationError, match="value mismatch"):
        validator.validate_aggregation_results(original, variant, query_id=1, variant_id=4, aggregation_columns=[2])


def test_numeric_equal_treats_nan_and_none_as_missing_only_when_opted_in():
    strict = ResultValidator(tolerance=1e-10)
    widened = ResultValidator(tolerance=1e-10, treat_nan_as_null=True)
    nan = float("nan")

    assert not strict._numeric_values_equal(nan, nan)
    assert strict._numeric_values_equal(None, None)
    assert not strict._numeric_values_equal(nan, None)
    assert not strict._numeric_values_equal(nan, 0)
    assert not strict._numeric_values_equal(0, nan)
    assert not strict._numeric_values_equal(None, 5.0)

    assert widened._numeric_values_equal(nan, nan)
    assert widened._numeric_values_equal(None, None)
    assert widened._numeric_values_equal(nan, None)
    assert not widened._numeric_values_equal(nan, 0)
    assert not widened._numeric_values_equal(0, nan)
    assert not widened._numeric_values_equal(None, 5.0)
