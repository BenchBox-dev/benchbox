from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import benchbox.core.joinorder.dataframe_queries as dataframe_queries
from benchbox._vendor.sqlglot import parse_one
from benchbox.core.joinorder.dataframe_queries import (
    _QUERY_MANAGER,
    _flatten_and,
    _is_join_equality,
    _JoinStep,
    _plan_join_sequence,
    _sql_tables,
    get_dataframe_queries,
)
from benchbox.core.joinorder.queries import CANONICAL_JOINORDER_QUERIES

pytestmark = [pytest.mark.unit, pytest.mark.fast]

QUERY_IDS = list(CANONICAL_JOINORDER_QUERIES)
REPO_ROOT = Path(__file__).resolve().parents[4]
TINY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "joinorder_canonical_tiny"


class PandasContext:
    platform = "pandas"

    def __init__(self, tables: dict[str, pd.DataFrame]) -> None:
        self._tables = tables

    def get_table(self, name: str) -> pd.DataFrame:
        return self._tables[name]


@pytest.fixture(scope="module")
def pandas_ctx() -> PandasContext:
    return PandasContext({path.stem: pd.read_parquet(path) for path in TINY_FIXTURE.glob("*.parquet")})


def _plan_for(query_id: str):
    tree = parse_one(_QUERY_MANAGER.get_query(query_id), read="duckdb")
    tables = _sql_tables(tree)
    predicates = _flatten_and(tree.args["where"].this)
    join_predicates = [predicate for predicate in predicates if _is_join_equality(predicate)]
    return tables, join_predicates, _plan_join_sequence(tables, join_predicates)


@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_join_plan_is_left_deep_chain_over_all_tables(query_id: str) -> None:
    tables, _join_predicates, steps = _plan_for(query_id)

    assert len(steps) == len(tables) - 1
    anchor = tables[0][0]
    assert not steps or steps[0].existing_alias == anchor
    joined = {anchor}
    for step in steps:
        assert step.existing_alias in joined
        assert step.new_alias not in joined
        joined.add(step.new_alias)
    assert joined == {alias for alias, _table in tables}


@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_join_plan_is_pure_function_of_sql_not_data(query_id: str) -> None:

    _t1, _j1, steps_a = _plan_for(query_id)
    _t2, _j2, steps_b = _plan_for(query_id)
    assert steps_a == steps_b

    assert all(step.predicate_index >= 0 for step in steps_a)


def test_pandas_executor_uses_exact_planned_join_sequence(
    monkeypatch: pytest.MonkeyPatch,
    pandas_ctx: PandasContext,
) -> None:

    tables, _join_predicates, expected_steps = _plan_for("1a")
    calls: list[tuple[list[tuple[str, str]], list[_JoinStep]]] = []

    def spy_plan(
        observed_tables: list[tuple[str, str]],
        observed_join_predicates: list[Any],
    ) -> list[_JoinStep]:
        steps = _plan_join_sequence(observed_tables, observed_join_predicates)
        calls.append((observed_tables, steps))
        return steps

    monkeypatch.setattr(dataframe_queries, "_plan_join_sequence", spy_plan)
    get_dataframe_queries().get_or_raise("1a").pandas_impl(pandas_ctx)

    assert calls == [(tables, expected_steps)]
    assert [step.predicate_index for step in expected_steps[:3]] == [0, 1, 2]
