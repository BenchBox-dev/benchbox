from __future__ import annotations

from decimal import Decimal

import pytest

from benchbox.core.validation.cross_platform import (
    STRICT,
    ComparisonReport,
    Tolerance,
    compare_query_results,
    register_query_tolerance,
    tolerance_for,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _compare(ref: list[tuple], cmp: list[tuple], tolerance: Tolerance = STRICT) -> ComparisonReport:
    return compare_query_results(
        query_id="Q-test",
        reference_platform="duckdb",
        comparison_platform="snowflake",
        reference_rows=ref,
        comparison_rows=cmp,
        tolerance=tolerance,
    )


def test_strict_exact_match() -> None:
    report = _compare([(1, "a"), (2, "b")], [(1, "a"), (2, "b")])
    assert report.matched
    assert report.divergences == []
    assert report.row_count_reference == 2


def test_strict_detects_cell_divergence() -> None:
    report = _compare([(1, "a")], [(1, "b")])
    assert not report.matched
    assert len(report.divergences) == 1
    d = report.divergences[0]
    assert d.row_index == 0 and d.column_index == 1
    assert d.reference_value == "a" and d.comparison_value == "b"


def test_row_count_mismatch_short_circuits() -> None:
    report = _compare([(1,), (2,)], [(1,)])
    assert not report.matched
    assert len(report.divergences) == 1
    assert report.divergences[0].row_index == -1
    assert report.row_count_reference == 2
    assert report.row_count_comparison == 1


def test_numeric_epsilon_allows_small_drift() -> None:
    tol = Tolerance(
        epsilon=1e-6,
        rationale="TPC-H §2.6.4 floating-point aggregate epsilon",
    )
    report = _compare([(1.0000001,)], [(1.0000002,)], tolerance=tol)
    assert report.matched


def test_numeric_epsilon_rejects_large_drift() -> None:
    tol = Tolerance(
        epsilon=1e-6,
        rationale="TPC-H §2.6.4 floating-point aggregate epsilon",
    )
    report = _compare([(1.0,)], [(1.1,)], tolerance=tol)
    assert not report.matched


def test_decimal_and_float_compare_cross_type() -> None:
    tol = Tolerance(epsilon=1e-9, rationale="TPC-H §2.6.4")
    report = _compare([(Decimal("1.5"),)], [(1.5,)], tolerance=tol)
    assert report.matched


def test_ordering_not_required_sorts_before_diff() -> None:
    tol = Tolerance(
        ordering_required=False,
        rationale="TPC-H §2.6.3 unordered result set",
    )
    report = _compare([(1,), (2,), (3,)], [(3,), (1,), (2,)], tolerance=tol)
    assert report.matched


def test_ordering_required_default_flags_reordering() -> None:
    report = _compare([(1,), (2,)], [(2,), (1,)])
    assert not report.matched


def test_null_equals_null() -> None:
    report = _compare([(None, "a")], [(None, "a")])
    assert report.matched


def test_nan_treated_as_null() -> None:
    report = _compare([(float("nan"),)], [(None,)])
    assert report.matched


def test_string_trailing_whitespace_stripped() -> None:

    report = _compare([("foo   ",)], [("foo",)])
    assert report.matched


def test_loose_tolerance_without_rationale_rejected() -> None:
    with pytest.raises(ValueError, match="spec-anchored rationale"):
        Tolerance(epsilon=0.01)
    with pytest.raises(ValueError, match="spec-anchored rationale"):
        Tolerance(ordering_required=False)


def test_loose_tolerance_with_whitespace_rationale_rejected() -> None:
    with pytest.raises(ValueError):
        Tolerance(epsilon=0.01, rationale="   ")


def test_strict_is_default_and_has_empty_rationale() -> None:
    assert STRICT.epsilon == 0.0
    assert STRICT.ordering_required is True
    assert STRICT.rationale == ""


def test_tolerance_registry_round_trip() -> None:
    tol = Tolerance(epsilon=1e-6, rationale="TPC-H §2.6.4")
    register_query_tolerance("tpch", "Q1", tol)
    assert tolerance_for("tpch", "Q1") is tol
    assert tolerance_for("tpch", "Q99") is STRICT
    assert tolerance_for("tpcds", "Q1") is STRICT


def test_column_count_mismatch_recorded_per_row() -> None:
    report = _compare([(1, 2)], [(1,)])
    assert not report.matched
    assert report.divergences[0].column_index == -1


def test_summary_ok_format() -> None:
    report = _compare([(1,)], [(1,)])
    summary = report.summary()
    assert "OK" in summary and "Q-test" in summary


def test_summary_diverged_caps_at_five_samples() -> None:
    ref = [(i,) for i in range(10)]
    cmp = [(i + 1000,) for i in range(10)]
    report = _compare(ref, cmp)
    summary = report.summary()
    assert "DIVERGED" in summary
    assert "5 more divergences" in summary


def test_both_empty_result_sets_match() -> None:
    report = _compare([], [])
    assert report.matched
    assert report.row_count_reference == 0
    assert report.row_count_comparison == 0
    assert report.divergences == []


def test_all_null_rows_match() -> None:
    report = _compare([(None, None, None)], [(None, None, None)])
    assert report.matched


def test_empty_vs_nonempty_mismatch() -> None:
    report = _compare([], [(1, 2)])
    assert not report.matched
    assert report.divergences[0].row_index == -1


