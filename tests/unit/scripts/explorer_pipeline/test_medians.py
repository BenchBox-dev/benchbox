from __future__ import annotations

import copy

import pytest

from _project.scripts.explorer_pipeline.medians import median_result
from benchbox.core.results import corpus_score
from tests.unit.scripts.explorer_pipeline.conftest import scored_minimal_bundle

pytestmark = [pytest.mark.unit, pytest.mark.fast]

BASE_SCORE = 1000.0


def _run(iterations: int, final_factor: float, earlier_factor: float = 100.0) -> dict:
    data = scored_minimal_bundle(BASE_SCORE)
    template = data["queries"]
    queries = []
    for iteration in range(1, iterations + 1):
        factor = final_factor if iteration == iterations else earlier_factor
        for query in template:
            row = copy.deepcopy(query)
            row.update(iter=iteration, run_type="measurement", ms=query["ms"] * factor)
            queries.append(row)
    data["queries"] = queries
    return data


def test_runs_with_different_iteration_counts_are_scored_from_each_runs_final_iteration() -> None:
    runs = [_run(1, 1.0), _run(3, 2.0), _run(1, 3.0)]

    merged = median_result(runs)

    assert corpus_score.power_at_size(merged) == pytest.approx(BASE_SCORE / 2.0)


def test_the_median_does_not_depend_on_the_order_of_the_runs() -> None:
    runs = [_run(3, 2.0), _run(1, 3.0), _run(1, 1.0)]

    assert corpus_score.power_at_size(median_result(runs)) == pytest.approx(BASE_SCORE / 2.0)
    assert corpus_score.power_at_size(median_result(runs[::-1])) == pytest.approx(BASE_SCORE / 2.0)
