from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from benchbox.core.tpcds_obt.queries import CONVERTIBLE_QUERY_IDS
from tests.integration._tpcds_obt_utils import (
    QueryTimeout,
    build_engines,
    choose_seed,
    compare_parts,
    run_statements,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]

SCALE_FACTOR = float(os.environ.get("BENCHBOX_OBT_EQUIVALENCE_SF", "0.01"))
QUERY_TIMEOUT_SECONDS = float(os.environ.get("BENCHBOX_OBT_EQUIVALENCE_TIMEOUT", "60"))

GRAIN = "the flattened sale-line grain changes cardinality for a dimension-only scope or a per-entity aggregate"
CORRELATION = "a correlated subquery, self-join or cross-join binds to different rows on the flattened grain"
CURRENT_POINTER = "customer-current address, demographics or household role is not carried by the OBT"
RETURN_KEY = "a return-side store, call-center, catalog-page or web-page key is not carried by the OBT"
MISSING_PART = "the converted statement covers fewer parts than the base query"
LABEL = "the OBT column name leaks into a result label"
ZERO_DIVISOR = "a zero divisor yields NaN where the base query yields NULL"
EMPTY = "the base query returns no rows for every candidate parameter seed at this scale, so equality is vacuous"

DIVERGENT_AT_SF_0_01: dict[int, str] = {
    1: GRAIN,
    2: CORRELATION,
    5: RETURN_KEY,
    6: CURRENT_POINTER,
    9: GRAIN,
    14: MISSING_PART,
    15: CURRENT_POINTER,
    17: CORRELATION,
    18: CURRENT_POINTER,
    25: CORRELATION,
    29: CORRELATION,
    30: GRAIN,
    34: CORRELATION,
    35: CURRENT_POINTER,
    45: CURRENT_POINTER,
    59: CORRELATION,
    69: CURRENT_POINTER,
    73: CORRELATION,
    76: LABEL,
    77: RETURN_KEY,
    79: CORRELATION,
    81: GRAIN,
    90: ZERO_DIVISOR,
    91: CURRENT_POINTER,
}
UNVERIFIABLE_AT_SF_0_01: dict[int, str] = {
    4: EMPTY,
    8: EMPTY,
    10: EMPTY,
    23: EMPTY,
    24: EMPTY,
    31: EMPTY,
    41: EMPTY,
    54: EMPTY,
    58: EMPTY,
    65: EMPTY,
    85: EMPTY,
}

DIVERGENT_AT_SF_1: dict[int, str] = {
    1: GRAIN,
    2: CORRELATION,
    5: RETURN_KEY,
    6: CURRENT_POINTER,
    9: GRAIN,
    10: CURRENT_POINTER,
    14: MISSING_PART,
    15: CURRENT_POINTER,
    17: CORRELATION,
    18: CURRENT_POINTER,
    19: CURRENT_POINTER,
    25: CORRELATION,
    29: CORRELATION,
    30: GRAIN,
    32: CORRELATION,
    34: CORRELATION,
    35: CURRENT_POINTER,
    41: GRAIN,
    45: CURRENT_POINTER,
    59: CORRELATION,
    65: GRAIN,
    69: CURRENT_POINTER,
    71: CORRELATION,
    73: CORRELATION,
    76: LABEL,
    77: RETURN_KEY,
    79: CORRELATION,
    81: GRAIN,
    91: CURRENT_POINTER,
    92: CORRELATION,
}
UNVERIFIABLE_AT_SF_1: dict[int, str] = {
    8: EMPTY,
    23: EMPTY,
    24: EMPTY,
    54: EMPTY,
}

EXPECTED_DIVERGENT: dict[float, dict[int, str]] = {0.01: DIVERGENT_AT_SF_0_01, 1.0: DIVERGENT_AT_SF_1}
EXPECTED_UNVERIFIABLE: dict[float, dict[int, str]] = {0.01: UNVERIFIABLE_AT_SF_0_01, 1.0: UNVERIFIABLE_AT_SF_1}


@dataclass(frozen=True)
class Outcome:
    status: str
    detail: str


def _evaluate(engines, query_id: int) -> Outcome:
    obt, base, benchmark, manager = engines
    seed, base_parts = choose_seed(base, benchmark, query_id, SCALE_FACTOR)
    try:
        obt_parts = run_statements(
            obt, manager.get_query(query_id, scale_factor=SCALE_FACTOR, seed=seed), QUERY_TIMEOUT_SECONDS
        )
    except QueryTimeout as error:
        return Outcome("divergent", f"OBT statement did not finish: {error}")
    comparison = compare_parts(obt_parts, base_parts)
    if not comparison.equal:
        return Outcome("divergent", f"seed {seed}: {comparison.detail}")
    if not base_parts or not all(base_parts):
        return Outcome("unverifiable", f"seed {seed}: base rows per part {comparison.base_rows}")
    return Outcome("equal", f"seed {seed}: {comparison.base_rows} rows per part")


@pytest.fixture(scope="module")
def engines(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("tpcds_obt_equivalence")
    obt, base, benchmark, manager, adapter = build_engines(root, SCALE_FACTOR)
    yield obt, base, benchmark, manager
    obt.close()
    adapter.close_connection(base)


def _expected(table: dict[float, dict[int, str]]) -> dict[int, str]:
    assert SCALE_FACTOR in table, (
        f"no expected-divergence list is recorded for scale factor {SCALE_FACTOR}; "
        f"recorded: {sorted(EXPECTED_DIVERGENT)}"
    )
    return table[SCALE_FACTOR]


def test_expected_lists_are_well_formed() -> None:
    divergent = _expected(EXPECTED_DIVERGENT)
    unverifiable = _expected(EXPECTED_UNVERIFIABLE)
    assert not divergent.keys() & unverifiable.keys()
    assert (divergent.keys() | unverifiable.keys()) <= set(CONVERTIBLE_QUERY_IDS)


@pytest.mark.parametrize("query_id", CONVERTIBLE_QUERY_IDS)
def test_an_obt_query_returns_the_tpcds_result_unless_listed(engines, query_id: int) -> None:
    divergent = _expected(EXPECTED_DIVERGENT)
    unverifiable = _expected(EXPECTED_UNVERIFIABLE)
    outcome = _evaluate(engines, query_id)

    listed = "divergent" if query_id in divergent else "unverifiable" if query_id in unverifiable else "equal"
    assert outcome.status == listed, (
        f"Q{query_id}: the expected-divergence lists record it as {listed} but it is {outcome.status} "
        f"on SF{SCALE_FACTOR:g} ({outcome.detail}); update the lists only when the change is understood"
    )
