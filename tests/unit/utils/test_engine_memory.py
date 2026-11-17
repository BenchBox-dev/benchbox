from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.utils import engine_memory as em

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture
def heavy(monkeypatch: pytest.MonkeyPatch):
    def configure(budget_gib: float | None) -> None:
        monkeypatch.setenv("BENCHBOX_HEAVY_RUN_ID", "run-1")
        if budget_gib is None:
            monkeypatch.delenv("BENCHBOX_HEAVY_MEMORY_BUDGET_GIB", raising=False)
        else:
            monkeypatch.setenv("BENCHBOX_HEAVY_MEMORY_BUDGET_GIB", str(budget_gib))

    return configure


@pytest.mark.parametrize(
    ("text", "default_unit", "expected"),
    [
        ("3g", "b", 3.0),
        ("3GiB", "b", 3.0),
        ("2 gigabytes", "b", 2.0),
        ("1536m", "b", 1.5),
        ("1536", "m", 1.5),
        ("2e3 MB", "b", 2000 / 1024),
        ("1t", "b", 1024.0),
    ],
)
def test_sizes_parse_to_gib(text: str, default_unit: str, expected: float) -> None:
    assert em.parse_size_gib(text, default_unit=default_unit) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "lots", "-1g", "3 widgets"])
def test_unreadable_sizes_fail_clearly(text: str) -> None:
    with pytest.raises(em.EngineMemoryBudgetError, match="cannot read"):
        em.parse_size_gib(text)


@pytest.mark.parametrize("budget", [x / 4 for x in range(20, 161)])
def test_a_derived_cap_plus_its_allowance_never_exceeds_the_admitted_budget(budget: float) -> None:
    for allowance in (em.SPARK_NON_HEAP_ALLOWANCE_GIB, em.DUCKDB_NON_BUFFER_ALLOWANCE_GIB):
        try:
            cap = em.derived_cap_gib(budget, allowance, engine="Engine")
        except em.EngineMemoryBudgetError:
            assert budget - allowance < em.MIN_ENGINE_CAP_GIB
        else:
            assert cap >= em.MIN_ENGINE_CAP_GIB
            assert cap + allowance <= budget


def test_a_ceiling_lowers_the_cap_but_never_raises_it_past_the_headroom() -> None:
    assert em.derived_cap_gib(64, 4.5, engine="Engine", ceiling_gib=12) == 12
    assert em.derived_cap_gib(8, 4.5, engine="Engine", ceiling_gib=12) == 3


@pytest.mark.parametrize(("requested", "fits"), [(3.5, True), (3.51, False)])
def test_an_explicit_cap_is_accepted_exactly_up_to_budget_minus_allowance(requested: float, fits: bool) -> None:
    if fits:
        em.check_explicit_cap(requested, 8.0, 4.5, engine="Spark", option="driver_memory")
    else:
        with pytest.raises(em.EngineMemoryBudgetError, match="at most 3.5 GiB"):
            em.check_explicit_cap(requested, 8.0, 4.5, engine="Spark", option="driver_memory")


def test_there_is_no_budget_outside_a_heavy_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHBOX_HEAVY_RUN_ID", raising=False)
    monkeypatch.setenv("BENCHBOX_HEAVY_MEMORY_BUDGET_GIB", "8")
    assert em.admitted_budget_gib() is None


def duckdb_limit(**config: object) -> str | None:
    with patch("benchbox.platforms.duckdb.duckdb"):
        return DuckDBAdapter(**config).memory_limit


def test_duckdb_default_limit_is_unchanged_outside_a_heavy_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHBOX_HEAVY_RUN_ID", raising=False)
    assert duckdb_limit() == "4GB"


@pytest.mark.parametrize(("budget", "expected"), [(64, "4GB"), (8, "4GB"), (7.5, "4GB"), (7, "3GB"), (5, "1GB")])
def test_duckdb_default_limit_is_derived_from_the_admitted_budget(heavy, budget: float, expected: str) -> None:
    heavy(budget)
    assert duckdb_limit() == expected
    assert float(expected.removesuffix("GB")) + em.DUCKDB_NON_BUFFER_ALLOWANCE_GIB <= budget


def test_duckdb_default_fails_clearly_when_the_budget_cannot_hold_the_allowance(heavy) -> None:
    heavy(4)
    with pytest.raises(em.EngineMemoryBudgetError, match="DuckDB needs about 3.5 GiB"):
        duckdb_limit()


def test_duckdb_explicit_limit_that_fits_is_kept_verbatim(heavy) -> None:
    heavy(8)
    assert duckdb_limit(memory_limit="4 GiB") == "4 GiB"
    assert duckdb_limit(memory_limit="2GB") == "2GB"


def test_duckdb_explicit_limit_that_cannot_fit_is_rejected_not_lowered(heavy) -> None:
    heavy(8)
    with pytest.raises(em.EngineMemoryBudgetError, match="Lower memory_limit"):
        duckdb_limit(memory_limit="6GB")


def test_duckdb_explicit_unbounded_limit_stays_unbounded(heavy) -> None:
    heavy(8)
    assert duckdb_limit(memory_limit=None) is None
