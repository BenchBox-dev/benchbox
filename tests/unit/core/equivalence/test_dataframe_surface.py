# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal

import pytest

from benchbox.core.equivalence.dataframe_surface import (
    SurfaceDivergence,
    _normalize_value,
    find_surface_divergences,
    materialize_rows,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Mismatch(Exception):
    pass


def _equal_check(reference, candidate):

    def check(actual_reference):
        assert actual_reference == reference
        if candidate != reference:
            raise _Mismatch(f"rows differ: {candidate} != {reference}")

    return check


def test_all_cells_equivalent_yields_no_divergences() -> None:
    divergences = find_surface_divergences(
        [1, 2],
        reference_rows=lambda q: [(q,)],
        candidate_cells=lambda q: [
            ("expression", _equal_check([(q,)], [(q,)])),
            ("pandas", _equal_check([(q,)], [(q,)])),
        ],
        validation_error=_Mismatch,
    )
    assert divergences == []


def test_mismatch_uses_validator_message_verbatim() -> None:
    divergences = find_surface_divergences(
        [7],
        reference_rows=lambda q: [(1,)],
        candidate_cells=lambda q: [("pandas", _equal_check([(1,)], [(2,)]))],
        validation_error=_Mismatch,
    )
    assert len(divergences) == 1
    only = divergences[0]
    assert only.query_id == 7
    assert only.cell == "pandas"
    assert only.key == "7_pandas"
    assert only.detail == "rows differ: [(2,)] != [(1,)]"


def test_execution_error_is_prefixed_and_isolated_per_cell() -> None:
    def boom(_reference):
        raise RuntimeError("kaboom")

    divergences = find_surface_divergences(
        [3],
        reference_rows=lambda q: [(0,)],
        candidate_cells=lambda q: [
            ("expression", boom),
            ("pandas", _equal_check([(0,)], [(0,)])),
        ],
        validation_error=_Mismatch,
    )
    assert [(d.cell, d.detail) for d in divergences] == [("expression", "error: kaboom")]


def test_reference_failure_records_one_divergence_and_skips_cells() -> None:
    calls: list[int] = []

    def reference_rows(q):
        raise ValueError("no data")

    def candidate_cells(q):
        calls.append(q)
        return [("expression", _equal_check([], []))]

    divergences = find_surface_divergences(
        [5],
        reference_rows=reference_rows,
        candidate_cells=candidate_cells,
        validation_error=_Mismatch,
        reference_failure_cell="v0:canonical",
    )
    assert calls == []
    assert len(divergences) == 1
    assert divergences[0].cell == "v0:canonical"
    assert divergences[0].detail == "reference query failed: no data"


def test_surface_divergence_key_format() -> None:
    assert SurfaceDivergence(3, "v3:expression", "x").key == "3_v3:expression"
    assert SurfaceDivergence(9, "", "x").key == "9"


class _FakeEagerFrame:
    def __init__(self, rows):
        self._rows = rows

    def rows(self):
        return self._rows


class _FakeLazyFrame:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return _FakeEagerFrame(self._rows)


class _Wrapper:
    def __init__(self, native):
        self.native = native


def test_materialize_rows_collects_and_normalizes() -> None:
    frame = _FakeLazyFrame([(Decimal("1.50"), datetime(2020, 1, 1, 0, 0, 0)), (None, float("nan"))])
    rows = materialize_rows(_Wrapper(frame))

    assert rows[0] == (1.5, date(2020, 1, 1))
    assert rows[1][0] is None
    assert math.isnan(rows[1][1])


def test_materialize_rows_rejects_unknown_result() -> None:
    with pytest.raises(TypeError):
        materialize_rows(object())


def test_normalize_value_scalar_mappings() -> None:
    assert _normalize_value(None) is None
    assert _normalize_value(Decimal("2.25")) == 2.25
    assert _normalize_value(datetime(2021, 6, 1, 0, 0, 0)) == date(2021, 6, 1)
    assert _normalize_value(datetime(2021, 6, 1, 12, 30, 0)) == datetime(2021, 6, 1, 12, 30, 0)
    assert math.isnan(_normalize_value(float("nan")))
    assert _normalize_value("abc") == "abc"
    assert _normalize_value(5) == 5


