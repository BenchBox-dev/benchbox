from __future__ import annotations

import pytest

from benchbox.core.benchmark_registry import (
    BENCHMARK_METADATA,
    get_benchmark_default_scale,
    get_scale_factor_rule,
    nonstandard_scale_note,
    validate_scale_factor,
)
from benchbox.core.errors import ScaleFactorNotSupportedError, ScaleFactorOutOfRangeError
from benchbox.core.tpcds.compliance import TpcdsComplianceClass, validate_tpcds_scale

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

FIXED = ("joinorder", "metadata_primitives")


@pytest.mark.parametrize(
    ("benchmark_id", "sf"),
    [
        ("tpch", 0.5),
        ("tpch", 2),
        ("tpch", 100000),
        ("ssb", 0.02),
        ("ssb", 5),
        ("ssb", 100),
        ("h2odb", 10),
        ("read_primitives", 1),
        ("tpcdi", 3),
        ("clickbench", 3),
    ],
)
def test_in_range_values_are_accepted(benchmark_id: str, sf: float) -> None:
    validate_scale_factor(benchmark_id, sf)


@pytest.mark.parametrize(
    ("benchmark_id", "sf"),
    [
        ("ssb", 1.5),
        ("clickbench", 1.5),
        ("tpcds", 1.5),
        ("tpcds_obt", 2.5),
        ("tpch", 200000),
        ("tpch", 0.001),
        ("tpch", 0),
        ("tpch", -1),
    ],
)
def test_out_of_range_values_name_the_range(benchmark_id: str, sf: float) -> None:
    with pytest.raises(ScaleFactorOutOfRangeError, match="from .* to 100000") as excinfo:
        validate_scale_factor(benchmark_id, sf)
    assert isinstance(excinfo.value, ValueError)
    assert excinfo.value.scale_options == list(get_scale_factor_rule(benchmark_id).standard)


@pytest.mark.parametrize("benchmark_id", [bid for bid in BENCHMARK_METADATA if bid not in FIXED])
@pytest.mark.parametrize("sf", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_rejected(benchmark_id: str, sf: float) -> None:
    with pytest.raises(ValueError):
        validate_scale_factor(benchmark_id, sf)


@pytest.mark.parametrize("benchmark_id", FIXED)
@pytest.mark.parametrize("sf", [float("nan"), float("inf"), 0.5, 2, 10, 0.01])
def test_fixed_benchmarks_reject_everything_but_one(benchmark_id: str, sf: float) -> None:
    with pytest.raises(ScaleFactorNotSupportedError):
        validate_scale_factor(benchmark_id, sf)


@pytest.mark.parametrize("benchmark_id", FIXED)
def test_fixed_benchmarks_accept_one(benchmark_id: str) -> None:
    assert validate_scale_factor(benchmark_id, 1) is True
    assert validate_scale_factor(benchmark_id, 1.0) is True


def test_joinorder_float_tolerance() -> None:
    validate_scale_factor("joinorder", 1.0 + 1e-12)
    with pytest.raises(ScaleFactorNotSupportedError):
        validate_scale_factor("joinorder", 1.0001)


def test_joinorder_error_carries_listed_values() -> None:
    with pytest.raises(ScaleFactorNotSupportedError) as excinfo:
        validate_scale_factor("joinorder", 0.5)
    assert excinfo.value.scale_options == [1.0]


def test_listed_and_unlisted_values_are_distinguished() -> None:
    assert validate_scale_factor("tpch", 2) is False
    assert validate_scale_factor("tpch", 10) is True


def test_tpcds_official_points_are_standard() -> None:
    assert validate_scale_factor("tpcds", 300) is True
    assert validate_scale_factor("tpcds", 1000) is True
    assert validate_scale_factor("tpcds", 2) is False
    assert validate_scale_factor("tpcds", 30) is False


def test_tpcds_obt_does_not_inherit_official_points() -> None:
    assert validate_scale_factor("tpcds_obt", 100) is False
    assert validate_scale_factor("tpcds_obt", 10) is True


def test_note_only_for_nonstandard_values() -> None:
    note = nonstandard_scale_note("tpch", 2)
    assert note is not None
    assert "tpch" in note
    assert "1" in note and "10" in note
    assert nonstandard_scale_note("tpch", 1) is None
    assert nonstandard_scale_note("tpcds", 300) is None


def test_unknown_benchmark_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="Unknown benchmark 'does-not-exist'"):
        validate_scale_factor("does-not-exist", 999.0)


@pytest.mark.parametrize("benchmark_id", ["tpcds", "tpcds_obt", "coffeeshop", "joinorder_synthetic"])
@pytest.mark.parametrize("sf", [0.001, 0.005])
def test_values_below_the_floor_are_rejected(benchmark_id: str, sf: float) -> None:
    with pytest.raises(ValueError, match="0.01"):
        validate_scale_factor(benchmark_id, sf)


@pytest.mark.parametrize("benchmark_id", ["tpcds", "tpcds_obt", "coffeeshop", "joinorder_synthetic"])
def test_floor_value_is_accepted(benchmark_id: str) -> None:
    validate_scale_factor(benchmark_id, 0.01)


def test_tpcds_scale_validator_names_the_floor() -> None:
    with pytest.raises(ValueError, match="0.01"):
        validate_tpcds_scale(0.001)
    assert validate_tpcds_scale(0.01) is TpcdsComplianceClass.UNOFFICIAL_SUBSCALE


def test_coffeeshop_default_scale_is_the_floor() -> None:
    assert get_benchmark_default_scale("coffeeshop") == 0.01


def test_no_registry_entry_goes_below_the_floor() -> None:
    for benchmark_id, meta in BENCHMARK_METADATA.items():
        values = [*meta["scale_options"], meta["default_scale"], meta.get("min_scale", 0.01)]
        assert min(values) >= 0.01, benchmark_id
