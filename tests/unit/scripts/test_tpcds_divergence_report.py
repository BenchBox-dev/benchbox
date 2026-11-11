from __future__ import annotations

import pytest
from tpcds_divergence_report import (
    DECIMAL_FLOAT,
    ERROR,
    FLAKY,
    INT_VS_FLOAT,
    NULL_ORDER,
    NULL_VALUE,
    NULL_VS_NAN,
    ORDER,
    PARAMETER_DRIFT,
    ROW_COUNT_LOGIC,
    UNBOUND,
    UNCLASSIFIED,
    Cell,
    _RowCapture,
    _run_cell,
    classify_cell,
    label_detail,
    render_markdown,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

ORDER_KEY_NULL = (
    "Q71.0: ORDER BY key mismatch at position 0. Original key: (None, 7008009), "
    "Variant key: (3023.76, 7008009) (order-key columns [4, 0]) - the returned order differs"
)
ORDER_KEY_NOISE = (
    "Q36.0: ORDER BY key mismatch at position 4. Original key: (-0.7541713805310839, 0), "
    "Variant key: (-0.7541713805310837, 0) (order-key columns [2, 1]) - the returned order differs"
)
ORDER_KEY_INT_FLOAT = (
    "Q30.0: ORDER BY key mismatch at position 5. Original key: ('AAAAAAAAAJLEBAAA', 31, 8, None), "
    "Variant key: ('AAAAAAAAAJLEBAAA', 31.0, 8.0, nan) (order-key columns [0, 5, 6, 7]) - the returned order differs"
)
ORDER_KEY_REORDERED = (
    "Q93.0: ORDER BY key mismatch at position 0. Original key: (0.0, 976), "
    "Variant key: (0.0, 52.0) (order-key columns [1, 0]) - the returned order differs"
)
ORDER_KEY_OTHER = (
    "Q4.0: ORDER BY key mismatch at position 0. Original key: ('a', 1), "
    "Variant key: ('b', 1) (order-key columns [0, 1]) - the returned order differs"
)


@pytest.mark.parametrize(
    ("detail", "cause"),
    [
        ("Q39.0: Column count mismatch at row 0. Original: 10, Variant: 8", ROW_COUNT_LOGIC),
        ("Q10.0: Row count mismatch. Original: 2, Variant: 6", ROW_COUNT_LOGIC),
        (ORDER_KEY_NULL, NULL_ORDER),
        (ORDER_KEY_NOISE, DECIMAL_FLOAT),
        (ORDER_KEY_OTHER, UNCLASSIFIED),
        ("Q5.0: Value mismatch at row 0, column 3. Original: None, Variant: 0.0, Tolerance: 1e-10", NULL_VALUE),
        ("Q66.0: Value mismatch at row 0, column 8. Original: 8288141.53, Variant: 238632.13", ROW_COUNT_LOGIC),
        ("Q9.0: Value mismatch at row 0, column 1. Original: 100.0000000001, Variant: 100.0000000002", DECIMAL_FLOAT),
        ("Q44.0: Value mismatch at row 0, column 1. Original: n 1, Variant: n 2", UNCLASSIFIED),
        ("something else entirely", UNCLASSIFIED),
    ],
)
def test_detail_text_gets_the_cause_it_shows(detail, cause):
    assert label_detail(detail)[0] == cause


def test_a_null_against_nan_is_its_own_label_not_null_order():
    detail = "Q27.0: Value mismatch at row 0, column 3. Original: None, Variant: nan; also columns [4, 6]"

    assert label_detail(detail)[0] == NULL_VS_NAN


def test_a_null_against_a_number_is_a_null_value_not_null_order():
    detail = "Q5.0: Value mismatch at row 0, column 3. Original: None, Variant: 0.0"

    assert label_detail(detail)[0] == NULL_VALUE


def test_null_order_needs_the_same_rows_when_the_rows_are_known():
    same = ([(None, 1), (5.0, 2)], [(5.0, 2), (None, 1)])
    different = ([(None, 1), (5.0, 2)], [(5.0, 2), (7.0, 1)])

    assert label_detail(ORDER_KEY_NULL, same)[0] == NULL_ORDER
    assert label_detail(ORDER_KEY_NULL, different)[0] == NULL_VALUE


def test_an_integer_against_the_same_float_is_a_dtype_difference():
    detail = "Q30.0: Value mismatch at row 4, column 5. Original: 31, Variant: 31.0"
    assert label_detail(detail)[0] == INT_VS_FLOAT


@pytest.mark.parametrize("prefix", [("31", "31.0"), ("1.12345678499", "1.12345678501")])
def test_equal_numeric_key_cells_do_not_hide_a_later_null_nan_difference(prefix):
    left, right = prefix
    detail = (
        f"Q1.0: ORDER BY key mismatch at position 0. Original key: ({left}, None), "
        f"Variant key: ({right}, nan) (order-key columns [0, 1]) - the returned order differs"
    )

    assert label_detail(detail)[0] == NULL_VS_NAN
    assert label_detail(ORDER_KEY_INT_FLOAT)[0] == NULL_VS_NAN


@pytest.mark.parametrize("prefix", [("31", "32"), ("nan", "nan")])
def test_a_real_earlier_key_difference_is_not_skipped(prefix):
    left, right = prefix
    detail = (
        f"Q1.0: ORDER BY key mismatch at position 0. Original key: ({left}, None), "
        f"Variant key: ({right}, nan) (order-key columns [0, 1]) - the returned order differs"
    )

    assert label_detail(detail)[0] == UNCLASSIFIED


@pytest.mark.parametrize(
    ("left", "right", "cause"),
    [
        (1.123456781, 1.123456784, ROW_COUNT_LOGIC),
        (1.12345678499, 1.12345678501, ORDER),
    ],
)
def test_captured_rows_use_validator_tolerance_when_classifying_order(left, right, cause):
    reference = [(0.0, 976, left), (0.0, 52, 7.0)]
    candidate = [(0.0, 52.0, 7.0), (0.0, 976.0, right)]

    assert label_detail(ORDER_KEY_REORDERED, (reference, candidate))[0] == cause


@pytest.mark.parametrize("value", [float("nan"), 0.0])
def test_captured_rows_preserve_strict_null_value_differences(value):
    reference = [(0.0, 976, None), (0.0, 52, 7.0)]
    candidate = [(0.0, 52.0, 7.0), (0.0, 976.0, value)]

    assert label_detail(ORDER_KEY_REORDERED, (reference, candidate))[0] == ROW_COUNT_LOGIC


def test_captured_rows_preserve_row_multiplicity():
    reference = [(0.0, 976), (0.0, 976), (0.0, 52)]
    candidate = [(0.0, 52.0), (0.0, 52.0), (0.0, 976.0)]

    assert label_detail(ORDER_KEY_REORDERED, (reference, candidate))[0] == ROW_COUNT_LOGIC


def test_float_noise_between_two_floats_is_still_decimal_float():
    detail = "Q9.0: Value mismatch at row 0, column 1. Original: 100.0, Variant: 100.0000000001"

    assert label_detail(detail)[0] == DECIMAL_FLOAT


def test_the_same_rows_in_a_different_order_are_labelled_order():
    reference = [(0.0, 976), (0.0, 52), (3.5, 7)]
    candidate = [(0.0, 52.0), (3.5, 7.0), (0.0, 976.0)]

    assert label_detail(ORDER_KEY_REORDERED, (reference, candidate))[0] == ORDER


def test_different_rows_are_not_labelled_order():
    detail = (
        "Q92.0: ORDER BY key mismatch at position 0. Original key: (27418.73,), "
        "Variant key: (21503.71,) (order-key columns [0]) - the returned order differs"
    )

    cause = label_detail(detail, ([(27418.73,)], [(21503.71,)]))[0]

    assert cause == ROW_COUNT_LOGIC


def test_an_order_key_mismatch_without_rows_cannot_be_called_order():
    assert label_detail(ORDER_KEY_REORDERED)[0] == UNCLASSIFIED


def test_an_ordering_difference_is_evidence_on_its_own_without_an_adapter():
    rows = ([(0.0, 976), (0.0, 52)], [(0.0, 52), (0.0, 976)])

    assert classify_cell(ORDER_KEY_REORDERED, adapted=False, rows=rows)["cause"] == ORDER
    assert classify_cell(ORDER_KEY_INT_FLOAT, adapted=False)["cause"] == NULL_VS_NAN
    assert classify_cell("Q5.0: Value mismatch at row 0, column 3. Original: None, Variant: 0.0", adapted=False)[
        "cause"
    ] == (NULL_VALUE)


def test_a_null_in_the_first_differing_key_cell_is_found_even_after_equal_cells():
    detail = (
        "Q1.0: ORDER BY key mismatch at position 2. Original key: ('x', 5, 'a, b'), "
        "Variant key: ('x', 5, None) (order-key columns [0, 1, 2]) - the returned order differs"
    )

    assert label_detail(detail)[0] == NULL_ORDER


def test_a_query_without_an_adapter_is_unbound_unless_the_text_shows_null_placement():
    assert classify_cell("Q10.0: Row count mismatch. Original: 2, Variant: 6", adapted=False)["cause"] == UNBOUND
    record = classify_cell("Q10.0: Row count mismatch. Original: 2, Variant: 6", adapted=False)
    assert record["secondary"] == ROW_COUNT_LOGIC
    assert classify_cell(ORDER_KEY_NULL, adapted=False)["cause"] == NULL_ORDER


def test_a_query_with_an_adapter_keeps_the_label_the_text_shows():
    assert classify_cell("Q39.0: Column count mismatch at row 0. Original: 10, Variant: 8", adapted=True)["cause"] == (
        ROW_COUNT_LOGIC
    )


def test_a_cell_the_adapter_fixed_is_parameter_drift():
    assert classify_cell("", adapted=True, drift_fixed=True)["cause"] == PARAMETER_DRIFT


def test_runs_that_disagree_make_a_cell_flaky_whatever_the_text_says():
    record = classify_cell(ORDER_KEY_NOISE, adapted=True, outcomes=["", ORDER_KEY_NOISE, ""])

    assert record["cause"] == FLAKY
    assert classify_cell(ORDER_KEY_NOISE, adapted=True, outcomes=[ORDER_KEY_NOISE] * 3)["cause"] == DECIMAL_FLOAT


def test_an_error_is_reported_as_an_error():
    assert classify_cell("", adapted=True, error="ValueError: boom")["cause"] == ERROR


def test_the_report_lists_each_cell_with_its_full_evidence():
    cell = Cell(
        scale=0.1,
        backend="pandas",
        query="11",
        status="divergent",
        cause=ROW_COUNT_LOGIC,
        why="88 rows against 0",
        evidence="Q11.0: Row count mismatch. Original: 88, Variant: 0 | pipe",
    )

    report = render_markdown([cell])

    assert "## Scale factor 0.1" in report
    assert "| Q11 | pandas | divergent | row count/logic | 88 rows against 0 |" in report
    assert "Original: 88, Variant: 0 \\| pipe" in report


def test_a_value_mismatch_followed_by_extra_columns_is_still_read():
    detail = "Q66.0: Value mismatch at row 0, column 8. Original: 8288141.53, Variant: 238632.13; also columns [9, 10]"

    assert label_detail(detail)[0] == ROW_COUNT_LOGIC
    near = "Q9.0: Value mismatch at row 0, column 1. Original: 5.0000000001, Variant: 5.0000000002, Tolerance: 1e-10; also columns [2]"
    assert label_detail(near)[0] == DECIMAL_FLOAT


def test_a_scale_with_no_divergences_is_still_in_the_report():
    report = render_markdown([], [0.03])

    assert "## Scale factor 0.03" in report
    assert "no divergent cells" in report


class _Divergence:
    def __init__(self, detail):
        self.detail = detail


class _Harness:
    def __init__(self, detail):
        self._detail = detail

    def find_cross_surface_divergences(self, *_args, **_kwargs):
        return [_Divergence(self._detail)] if self._detail else []


class _Data:
    connection = None
    reference_sql = None


class _Gate:
    def build_validator(self):
        return None


@pytest.mark.parametrize("detail", ["error: boom", "reference query failed: no such table"])
def test_failures_the_harness_captured_are_errors_not_divergences(detail):
    status, text = _run_cell(_Harness(detail), _Gate(), _Data(), None, "1", "pandas", None)

    assert (status, text) == ("error", detail)


def test_a_real_divergence_is_still_divergent():
    status, _ = _run_cell(_Harness(ORDER_KEY_NULL), _Gate(), _Data(), None, "71", "pandas", None)

    assert status == "divergent"


class _PanicException(BaseException):
    pass


_PanicException.__name__ = "PanicException"


class _Raises:
    def __init__(self, exc):
        self._exc = exc

    def find_cross_surface_divergences(self, *_args, **_kwargs):
        raise self._exc


def test_a_polars_panic_is_an_error_for_that_cell_not_a_crash():
    panic = _PanicException("called `Result::unwrap()` on an `Err` value: Invalid argument (os error 22)")

    status, text = _run_cell(_Raises(panic), _Gate(), _Data(), None, "1", "pandas", None)

    assert status == "error"
    assert "PanicException" in text
    assert "os error 22" in text


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(1), GeneratorExit()])
def test_other_base_exceptions_still_stop_the_run(exc):
    with pytest.raises(type(exc)):
        _run_cell(_Raises(exc), _Gate(), _Data(), None, "1", "pandas", None)


class _Fetching:
    def __init__(self):
        self.fetch_reference_rows = lambda *_a, **_k: [(1,), (2,)]
        self.materialize_rows = lambda *_a, **_k: [(2.0,), (1.0,)]

    def find_cross_surface_divergences(self, *_args, **_kwargs):
        reference = self.fetch_reference_rows()
        self.fetch_reference_rows()
        self.materialize_rows()
        return [_Divergence(ORDER_KEY_REORDERED)] if reference else []


def test_the_rows_of_a_comparison_are_captured_and_the_harness_is_left_as_it_was():
    xs = _Fetching()
    fetch, materialize = xs.fetch_reference_rows, xs.materialize_rows
    capture = _RowCapture()

    _run_cell(xs, _Gate(), _Data(), None, "93", "pandas", None, capture)

    assert capture.rows == ([(1,), (2,)], [(2.0,), (1.0,)])
    assert (xs.fetch_reference_rows, xs.materialize_rows) == (fetch, materialize)
    assert label_detail(ORDER_KEY_REORDERED, capture.rows)[0] == ORDER


@pytest.mark.parametrize(
    ("left", "right", "cause"),
    [
        (1.123456781, 1.123456784, ROW_COUNT_LOGIC),
        (1.12345678499, 1.12345678501, ORDER),
    ],
)
def test_captured_row_tolerance_reaches_the_rendered_report(left, right, cause):
    xs = _Fetching()
    xs.fetch_reference_rows = lambda *_a, **_k: [(0.0, 976, left), (0.0, 52, 7.0)]
    xs.materialize_rows = lambda *_a, **_k: [(0.0, 52.0, 7.0), (0.0, 976.0, right)]
    capture = _RowCapture()

    status, detail = _run_cell(xs, _Gate(), _Data(), None, "93", "pandas", None, capture)
    record = classify_cell(detail, adapted=True, rows=capture.rows)
    cell = Cell(scale=0.1, backend="pandas", query="93", status=status, evidence=detail, **record)

    report = render_markdown([cell])

    assert record["cause"] == cause
    assert f"| Q93 | pandas | divergent | {cause} |" in report
    assert detail in report


