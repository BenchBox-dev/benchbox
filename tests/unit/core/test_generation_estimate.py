from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core import generation_estimate
from benchbox.core.generation_estimate import (
    DATA_GENERATION,
    QUERY_GENERATION,
    announce_generation_estimate,
    estimate_generation,
)
from benchbox.core.runner import runner
from benchbox.utils.run_deadline import RunDeadline, active_run_deadline

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

HOST = "darwin-arm64 (Apple M4, 10 cores, 16 GiB)"


@pytest.fixture
def lines(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    captured: list[str] = []
    monkeypatch.setattr(generation_estimate, "emit", captured.append)
    return captured


def measured_range(kind: str, benchmark: str, key: str) -> tuple[float, float]:
    points = generation_estimate._measurements()[kind][benchmark]
    values = [point[key] for point in points]
    return min(values), max(values)


def test_estimate_text_is_the_owner_text_for_a_measured_scale(lines: list[str]) -> None:
    announce_generation_estimate(DATA_GENERATION, "tpch", 1)

    assert lines == [
        f"Rough estimate: generation will take about 12 seconds (measured on {HOST} at scale 0.01, 0.1, 1, 10)."
    ]


def test_estimate_for_a_measured_scale_lies_within_the_measured_range() -> None:
    for benchmark in ("tpch", "tpcds"):
        low, high = measured_range(DATA_GENERATION, benchmark, "wall_seconds")
        for scale in (0.01, 0.05, 0.1, 0.5, 1, 5, 10):
            estimate = estimate_generation(DATA_GENERATION, benchmark, scale)
            assert estimate is not None
            assert low <= estimate.seconds <= high
            assert not estimate.extrapolated


def test_estimate_at_a_measured_scale_equals_the_measurement() -> None:
    estimate = estimate_generation(DATA_GENERATION, "tpcds", 10)

    assert estimate is not None
    assert estimate.seconds == pytest.approx(183.41)


def test_unmeasured_larger_scale_is_labelled_extrapolated(lines: list[str]) -> None:
    announce_generation_estimate(DATA_GENERATION, "tpcds", 100)

    assert lines == [
        "Rough estimate: generation will take about 31 minutes "
        f"(measured on {HOST} at scale 0.01, 0.1, 1, 10; "
        "extrapolated for scale 100, above the largest measured scale 10)."
    ]
    estimate = estimate_generation(DATA_GENERATION, "tpcds", 100)
    assert estimate is not None and estimate.extrapolated
    assert estimate.seconds > measured_range(DATA_GENERATION, "tpcds", "wall_seconds")[1]


def test_query_generation_estimate_uses_the_dsqgen_measurements(lines: list[str]) -> None:
    announce_generation_estimate(QUERY_GENERATION, "tpcds", 1000)
    announce_generation_estimate(QUERY_GENERATION, "tpcds", 10000)

    assert lines[0] == (
        f"Rough estimate: generation will take about 1 second (measured on {HOST} at scale 1, 100, 1000, 3000, 5000)."
    )
    assert "extrapolated for scale 10000, above the largest measured scale 5000" in lines[1]


def test_no_estimate_for_an_unmeasured_benchmark(lines: list[str]) -> None:
    assert announce_generation_estimate(DATA_GENERATION, "clickbench", 1) is None
    assert announce_generation_estimate(QUERY_GENERATION, "tpch", 1) is None
    assert lines == []


def test_warns_when_the_run_limit_is_shorter_than_the_estimate(lines: list[str]) -> None:
    with active_run_deadline(RunDeadline(60, started=False)):
        announce_generation_estimate(DATA_GENERATION, "tpcds", 10)

    assert lines[1] == "The run limit of 60 seconds is shorter than the estimated generation time of about 3 minutes."


def test_no_warning_when_no_limit_is_set(lines: list[str]) -> None:
    announce_generation_estimate(DATA_GENERATION, "tpcds", 10)

    assert len(lines) == 1


def test_no_warning_when_the_limit_is_larger_than_the_estimate(lines: list[str]) -> None:
    with active_run_deadline(RunDeadline(3600, started=False)):
        announce_generation_estimate(DATA_GENERATION, "tpcds", 10)

    assert len(lines) == 1


def test_data_generation_prints_the_estimate_before_generating(
    lines: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    monkeypatch.setattr(generation_estimate, "emit", lambda message: order.append("estimate"))
    monkeypatch.setattr(runner, "emit", lambda message: None)
    benchmark = SimpleNamespace(tables=None, output_dir=None, generate_data=lambda: order.append("generate"))
    config = SimpleNamespace(name="tpch", scale_factor=0.1, options={})

    assert runner._ensure_data_generated(benchmark, config) == (True, False)

    assert order == ["estimate", "generate"]
