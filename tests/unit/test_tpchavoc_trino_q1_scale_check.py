from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from _project.scripts import tpchavoc_trino_q1_scale_check as scale_check
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


def test_integer_count_off_by_one_is_never_equivalent_at_large_scale():
    canonical = [["A", "F", 100.0, 150_000_000]]

    assert max_relative_difference(canonical, [["A", "F", 100.0, 149_999_999]]) == math.inf
    assert max_relative_difference(canonical, [["A", "F", 100.0, 150_000_000]]) == 0.0


@pytest.mark.parametrize("bad", [float("nan"), "NaN", "Infinity", float("inf")])
def test_non_finite_cell_is_never_equivalent(bad):
    assert max_relative_difference([["A", 1.0]], [["A", bad]]) == math.inf
    assert max_relative_difference([["A", bad]], [["A", 1.0]]) == math.inf
    assert max_relative_difference([["A", bad]], [["A", bad]]) == math.inf


def test_tree_state_is_unknown_when_git_fails(monkeypatch):
    def failing_git(command, **kwargs):
        return subprocess.CompletedProcess(command, 128, stdout="", stderr="fatal: not a git repository")

    monkeypatch.setattr(scale_check.subprocess, "run", failing_git)

    assert scale_check.repository_dirty() is None
    assert scale_check.repository_revision() == "unknown"


def test_git_provenance_is_read_from_the_repository_that_holds_the_script(monkeypatch):
    seen = []

    def recording_git(command, **kwargs):
        seen.append(kwargs["cwd"])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(scale_check.subprocess, "run", recording_git)

    assert scale_check.repository_dirty() is False
    assert seen == [scale_check.REPOSITORY_ROOT]
    assert (scale_check.REPOSITORY_ROOT / "_project" / "scripts" / "tpchavoc_trino_q1_scale_check.py").is_file()
