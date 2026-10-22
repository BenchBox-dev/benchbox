from __future__ import annotations

import datetime
import random
from decimal import Decimal

import pytest

from benchbox.core.tpchavoc.validation import ResultValidator, ValidationError

pytestmark = [pytest.mark.unit, pytest.mark.medium]

SHARED_NAN = float("nan")
HUGE = 10**400

SCALARS = [
    0,
    1,
    -1,
    2,
    True,
    False,
    2**53 + 1,
    HUGE,
    -HUGE,
    0.0,
    -0.0,
    1.0,
    1.0 + 2e-16,
    1.0 + 1e-12,
    1.0 + 1e-6,
    2.5,
    1e308,
    float("inf"),
    float("-inf"),
    SHARED_NAN,
    Decimal("1"),
    Decimal("1.0"),
    Decimal("1.000001"),
    Decimal("2.5"),
    Decimal("NaN"),
    Decimal("Infinity"),
    None,
    "a",
    "a ",
    " a",
    "",
    "b",
    b"a",
    b"b",
    datetime.date(2020, 1, 1),
    datetime.date(2020, 1, 2),
    datetime.datetime(2020, 1, 1),
    (1, 2),
    (1.0, 2),
    (1, SHARED_NAN),
    (None, (1, "a")),
    [1, 2],
    {"k": 1},
    {"k": 1.0},
    {"k": SHARED_NAN},
]

FRESH_NAN_FACTORIES = [lambda: float("nan"), lambda: (1, float("nan")), lambda: Decimal("NaN")]


def _cell(rng: random.Random):
    roll = rng.random()
    if roll < 0.05:
        return rng.choice(FRESH_NAN_FACTORIES)()
    return rng.choice(SCALARS)


def _make_case(rng: random.Random):
    width = rng.randint(1, 3)
    rows = [tuple(_cell(rng) for _ in range(width)) for _ in range(rng.randint(0, 6))]
    variant = [tuple(row) for row in rows]
    mode = rng.random()
    if mode < 0.35:
        rng.shuffle(variant)
    elif mode < 0.7 and variant:
        i = rng.randrange(len(variant))
        j = rng.randrange(width)
        row = list(variant[i])
        row[j] = _cell(rng)
        variant[i] = tuple(row)
        if rng.random() < 0.5:
            rng.shuffle(variant)
    elif mode < 0.85 and len(variant) > 1:
        variant.reverse()
    return rows, variant


def _outcome(call):
    try:
        return ("ok", call())
    except ValidationError:
        return ("rejected", None)
    except Exception as exc:
        return ("error", type(exc).__name__)


def _slow_outcome(monkeypatch, validator, rows, variant, **kwargs):
    with monkeypatch.context() as patch:
        patch.setattr(ResultValidator, "_exact_fast_path_accepts", staticmethod(lambda *a, **k: False))
        return _outcome(lambda: validator.validate_results_exact(rows, variant, 1, 1, **kwargs))


@pytest.mark.parametrize("seed", range(8))
def test_fast_path_acceptance_implies_slow_path_acceptance(seed, monkeypatch):
    rng = random.Random(seed)
    accepted = 0
    for _ in range(1500):
        rows, variant = _make_case(rng)
        ordered = rng.random() < 0.5
        validator = ResultValidator(
            treat_nan_as_null=rng.random() < 0.2,
            strip_strings=rng.random() < 0.3,
        )
        kwargs = {"order_aware": ordered, "order_by": [0] if ordered else None, "tie_aware": rng.random() < 0.3}

        fast = ResultValidator._exact_fast_path_accepts(rows, variant, ordered=ordered)
        slow = _slow_outcome(monkeypatch, validator, rows, variant, **kwargs)
        if fast:
            accepted += 1
            assert slow == ("ok", True), (rows, variant, kwargs)
        assert _outcome(lambda: validator.validate_results_exact(rows, variant, 1, 1, **kwargs)) == slow, (
            rows,
            variant,
            kwargs,
        )
    assert accepted > 100


@pytest.mark.parametrize(
    "rows",
    [
        [(SHARED_NAN,)],
        [(1, (2, SHARED_NAN))],
        [({"k": SHARED_NAN},)],
        [(Decimal("NaN"),)],
        [(HUGE,)],
        [(-HUGE,)],
    ],
)
def test_fast_path_declines_rows_the_slow_path_rejects_or_cannot_handle(rows):
    assert ResultValidator._exact_fast_path_accepts(rows, list(rows), ordered=False) is False
    assert ResultValidator._exact_fast_path_accepts(rows, list(rows), ordered=True) is False


def test_identical_nan_object_is_rejected_like_the_slow_path():
    rows = [(SHARED_NAN,)]
    with pytest.raises(ValidationError):
        ResultValidator().validate_results_exact(rows, list(rows), 1, 1)


@pytest.mark.parametrize(
    ("original", "variant"),
    [
        ([(1.0,)], [(1.0 + 1e-6,)]),
        ([(Decimal("1"),)], [(Decimal("1.000001"),)]),
        ([(1.0, "a")], [(1.0000001, "a")]),
    ],
)
def test_fast_path_never_accepts_values_that_are_only_close(original, variant):
    assert ResultValidator._exact_fast_path_accepts(original, variant, ordered=False) is False
    with pytest.raises(ValidationError):
        ResultValidator().validate_results_exact(original, variant, 1, 1)


def test_fast_path_accepts_equal_rows_in_any_order_when_order_is_irrelevant():
    original = [(1, "a"), (2, "b"), (3, None)]
    variant = [(3, None), (1, "a"), (2, "b")]
    assert ResultValidator._exact_fast_path_accepts(original, variant, ordered=False) is True
    assert ResultValidator._exact_fast_path_accepts(original, variant, ordered=True) is False


def test_fast_path_falls_back_when_rows_are_not_orderable():
    original = [(1,), (None,)]
    variant = [(None,), (1,)]
    assert ResultValidator._exact_fast_path_accepts(original, variant, ordered=False) is False
    assert ResultValidator().validate_results_exact(original, variant, 1, 1) is True
