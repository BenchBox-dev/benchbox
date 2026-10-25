from __future__ import annotations

import os
import re
from dataclasses import dataclass

import pytest

from benchbox.core.tpcds_obt.queries import CONVERTIBLE_QUERY_IDS
from tests.integration._tpcds_obt_utils import (
    QueryTimeout,
    build_engines,
    choose_seed,
    compare_parts,
    parts_are_vacuous,
    run_statements,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]

SCALE_FACTOR = float(os.environ.get("BENCHBOX_OBT_EQUIVALENCE_SF", "0.01"))
QUERY_TIMEOUT_SECONDS = float(os.environ.get("BENCHBOX_OBT_EQUIVALENCE_TIMEOUT", "60"))

EMPTY = "the base query returns no rows for every candidate parameter seed at this scale, so equality is vacuous"
ZERO_ROW = (
    "the base query returns one row of only NULL or 0 aggregates for every candidate parameter seed at this scale, "
    "so equality is vacuous"
)
ONE_WAREHOUSE = (
    "the base query returns one row (0, NULL, NULL) for every candidate parameter seed because SF0.01 has one "
    "warehouse and the query needs orders shipped from two, so equality is vacuous"
)

DIVERGENT_AT_SF_0_01: dict[int, str] = {}
UNVERIFIABLE_AT_SF_0_01: dict[int, str] = {
    4: EMPTY,
    10: EMPTY,
    13: ZERO_ROW,
    16: ONE_WAREHOUSE,
    23: EMPTY,
    24: EMPTY,
    31: EMPTY,
    32: ZERO_ROW,
    38: ZERO_ROW,
    41: EMPTY,
    58: EMPTY,
    61: ZERO_ROW,
    65: EMPTY,
    85: EMPTY,
    88: ZERO_ROW,
    90: ZERO_ROW,
    94: ONE_WAREHOUSE,
    95: ONE_WAREHOUSE,
    96: ZERO_ROW,
}

DIVERGENT_AT_SF_1: dict[int, str] = {}
UNVERIFIABLE_AT_SF_1: dict[int, str] = {
    23: EMPTY,
    24: EMPTY,
    61: ZERO_ROW,
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
    if parts_are_vacuous(base_parts):
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


@pytest.mark.skipif(SCALE_FACTOR < 1, reason="Q24 returns rows only from scale factor 1")
def test_q24_matches_tpcds_with_parameters_that_return_rows(engines) -> None:
    obt, base, benchmark, manager = engines
    market, colors = 8, ("yellow", "snow")
    obt_sql = manager.get_query(24, scale_factor=SCALE_FACTOR)
    obt_sql = re.sub(r"store_s_market_id = \d+", f"store_s_market_id = {market}", obt_sql)
    obt_statements = [statement for statement in obt_sql.split(";") if statement.strip()]
    obt_parts = []
    base_parts = []
    for statement, variant, color in zip(obt_statements, ("a", "b"), colors, strict=True):
        statement = re.sub(r"item_i_color = '[^']*'", f"item_i_color = '{color}'", statement)
        base_sql = benchmark.get_query(24, scale_factor=SCALE_FACTOR, variant=variant, dialect="duckdb")
        base_sql = re.sub(r'"s_market_id" = \d+', f'"s_market_id" = {market}', base_sql)
        base_sql = re.sub(r'"i_color" = \'[^\']*\'', f"\"i_color\" = '{color}'", base_sql)
        obt_parts.extend(run_statements(obt, statement, QUERY_TIMEOUT_SECONDS))
        base_parts.extend(run_statements(base, base_sql, QUERY_TIMEOUT_SECONDS))
    assert not parts_are_vacuous(base_parts), (
        "the pinned Q24 parameters no longer return informative rows on the base query"
    )
    comparison = compare_parts(obt_parts, base_parts)
    assert comparison.equal, comparison.detail
