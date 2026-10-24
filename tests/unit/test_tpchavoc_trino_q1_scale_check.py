from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from _project.scripts.tpchavoc_trino_q1_scale_check import RELATIVE_TOLERANCE, max_relative_difference

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CANONICAL = [["A", "F", 100.0, 5.0], ["N", "O", 200.0, 7.0]]


def test_summation_order_noise_stays_within_tolerance():
    variant = [["A", "F", 100.0 * (1 + 1e-12), 5.0], ["N", "O", 200.0, 7.0 * (1 - 1e-12)]]

    assert max_relative_difference(CANONICAL, variant) <= RELATIVE_TOLERANCE


@pytest.mark.parametrize(
    "variant",
    [
        [["A", "F", 100.0, 5.0]],
        [["A", "F", 100.0, 5.0], ["N", "O", 200.0, 7.0], ["R", "F", 1.0, 1.0]],
        [["A", "F", 100.0], ["N", "O", 200.0, 7.0]],
        [["A", "X", 100.0, 5.0], ["N", "O", 200.0, 7.0]],
    ],
    ids=["missing-group", "extra-group", "missing-column", "changed-key"],
)
def test_structural_mismatch_is_never_equivalent(variant):
    assert max_relative_difference(CANONICAL, variant) == math.inf


def test_value_error_beyond_tolerance_is_detected():
    variant = [["A", "F", 100.0 * (1 + 1e-6), 5.0], ["N", "O", 200.0, 7.0]]

    assert max_relative_difference(CANONICAL, variant) > RELATIVE_TOLERANCE
