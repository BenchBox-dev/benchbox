from __future__ import annotations

import math
import random

import pytest

from benchbox.core.results.models import QueryExecution
from benchbox.core.results.schema import QUERY_MS_EXPORT_SIGNIFICANT_DIGITS, build_result_payload
from benchbox.core.results.subscale_policy import derive_power_at_size
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

POWER_REPRODUCTION_TOLERANCE = 1e-4


def _power_from_raw(durations_ms: list[float], scale_factor: float) -> float:
    seconds = [ms / 1000.0 for ms in durations_ms]
    geometric_mean = math.exp(sum(math.log(value) for value in seconds) / len(seconds))
    return (scale_factor * 3600) / geometric_mean


@pytest.mark.parametrize("seed", range(20))
@pytest.mark.parametrize("scale", [0.001, 0.01])
def test_exported_query_timings_reproduce_power_within_tolerance(seed: int, scale: float) -> None:
    rng = random.Random(seed)
    durations = [rng.uniform(0.02, 9.0) * 10 ** rng.randint(-1, 3) for _ in range(22)]
    result = make_benchmark_results(
        benchmark_id="tpch",
        scale_factor=scale,
        query_results=[
            QueryExecution(query_id=str(index + 1), execution_time_ms=ms, rows_returned=1, status="SUCCESS")
            for index, ms in enumerate(durations)
        ],
    )

    queries = build_result_payload(result)["queries"]
    derived = derive_power_at_size(queries, scale, 22)

    assert derived is not None
    assert abs(derived / _power_from_raw(durations, scale) - 1.0) <= POWER_REPRODUCTION_TOLERANCE


def test_one_decimal_rounding_would_have_exceeded_tolerance() -> None:
    durations = [0.84, 1.26, 2.34, 1.16, 3.37, 0.95] * 3 + [1.44, 2.51, 1.07, 3.9]
    rounded = [round(ms, 1) for ms in durations]

    scale = 0.01
    drift = abs(_power_from_raw(rounded, scale) / _power_from_raw(durations, scale) - 1.0)

    assert drift > POWER_REPRODUCTION_TOLERANCE
    assert QUERY_MS_EXPORT_SIGNIFICANT_DIGITS >= 7


