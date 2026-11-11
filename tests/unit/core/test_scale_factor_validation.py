from __future__ import annotations

import pytest

from benchbox.core.benchmark_registry import (
    BENCHMARK_METADATA,
    TPC_OFFICIAL_SCALE_OPTIONS,
    validate_scale_factor,
)
from benchbox.core.errors import ScaleFactorNotSupportedError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_joinorder_accepts_canonical_unit_scale() -> None:

    validate_scale_factor("joinorder", 1.0)
    validate_scale_factor("joinorder", 1)


def test_joinorder_rejects_non_unit_scale() -> None:
    with pytest.raises(ScaleFactorNotSupportedError) as excinfo:
        validate_scale_factor("joinorder", 0.5)
    assert excinfo.value.benchmark_id == "joinorder"
    assert excinfo.value.scale_factor == 0.5
    assert excinfo.value.scale_options == [1.0]

    assert isinstance(excinfo.value, ValueError)


@pytest.mark.parametrize("bad_sf", [0.0, 0.01, 0.5, 1.5, 2.0, 10.0, -1.0])
def test_joinorder_rejects_arbitrary_scales(bad_sf: float) -> None:
    with pytest.raises(ScaleFactorNotSupportedError):
        validate_scale_factor("joinorder", bad_sf)


@pytest.mark.parametrize("sf", [0.01, 0.1, 1.0, 10.0])
def test_tpch_accepts_declared_scale_options(sf: float) -> None:
    validate_scale_factor("tpch", sf)


@pytest.mark.parametrize("sf", TPC_OFFICIAL_SCALE_OPTIONS)
def test_tpch_accepts_tpc_official_scale_options(sf: float) -> None:
    validate_scale_factor("tpch", sf)


def test_tpch_rejects_undeclared_scale() -> None:
    with pytest.raises(ScaleFactorNotSupportedError):
        validate_scale_factor("tpch", 0.5)


@pytest.mark.parametrize("sf", [0.01, 0.1, 1.0, 10.0, 3.5])
def test_clickbench_accepts_synthetic_scales(sf: float) -> None:
    validate_scale_factor("clickbench", sf)


@pytest.mark.parametrize("sf", [float("nan"), float("inf"), float("-inf")])
def test_clickbench_rejects_non_finite_scales(sf: float) -> None:
    with pytest.raises(ValueError, match="finite scale_factor"):
        validate_scale_factor("clickbench", sf)


def test_tpcds_delegates_to_compliance_classifier() -> None:

    validate_scale_factor("tpcds", 1.0)


@pytest.mark.parametrize(
    "benchmark_id",
    [bid for bid, meta in BENCHMARK_METADATA.items() if "scale_options" in meta and bid != "tpcds"],
)
def test_every_benchmark_accepts_first_declared_scale(benchmark_id: str) -> None:
    sf = float(BENCHMARK_METADATA[benchmark_id]["scale_options"][0])
    validate_scale_factor(benchmark_id, sf)


def test_unknown_benchmark_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="Unknown benchmark 'does-not-exist'"):
        validate_scale_factor("does-not-exist", 999.0)


def test_float_tolerance_accepts_imperceptible_drift() -> None:

    validate_scale_factor("joinorder", 1.0 + 1e-12)


def test_float_tolerance_rejects_perceptible_drift() -> None:
    with pytest.raises(ScaleFactorNotSupportedError):
        validate_scale_factor("joinorder", 1.0001)
