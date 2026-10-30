from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import benchbox.core.joinorder.dataframe_queries as dataframe_queries
from benchbox._vendor.sqlglot import parse_one
from benchbox.core.joinorder.dataframe_queries import (
    _QUERY_MANAGER,
    _analyze_query,
    _JoinPlan,
    _plan_join_sequence,
    _step_keys,
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


def _shape_for(query_id: str):
    return _analyze_query(parse_one(_QUERY_MANAGER.get_query(query_id), read="duckdb"))


def _estimates_for(query_id: str) -> dict[str, float]:
    shape = _shape_for(query_id)
    return dataframe_queries._estimated_rows(shape.tables, shape.local_predicates)


def _join_order(query_id: str) -> list[str]:
    shape = _shape_for(query_id)
    return [shape.anchor, *[step.new_alias for step in shape.steps]]


@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_join_plan_is_left_deep_chain_over_all_tables(query_id: str) -> None:
    shape = _shape_for(query_id)

    assert len(shape.steps) == len(shape.tables) - 1
    joined = {shape.anchor}
    for step in shape.steps:
        assert step.existing_alias in joined
        assert step.new_alias not in joined
        joined.add(step.new_alias)
    assert joined == {alias for alias, _table in shape.tables}


@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_join_plan_is_pure_function_of_sql_not_data(query_id: str) -> None:
    steps_a = _shape_for(query_id).steps
    steps_b = _shape_for(query_id).steps
    assert steps_a == steps_b

    assert all(step.predicate_index >= 0 for step in steps_a)


ESTIMATED_PEAK_ROWS_BOUND = 5_000_000


@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_planned_intermediate_results_stay_below_bound(query_id: str) -> None:
    assert _shape_for(query_id).peak_rows <= ESTIMATED_PEAK_ROWS_BOUND


@pytest.mark.parametrize("query_id", QUERY_IDS)
def test_every_join_predicate_is_a_join_key_so_no_cross_product_is_filtered_afterwards(query_id: str) -> None:
    shape = _shape_for(query_id)
    used = [key.predicate_index for step in shape.steps for key in _step_keys(step)]

    assert sorted(used) == list(range(len(shape.join_predicates)))


@pytest.mark.parametrize(
    ("query_id", "expected_prefix"),
    [
        ("6a", ["k", "mk", "t", "ci"]),
        ("17a", ["cn", "mc", "t", "mk", "k", "ci"]),
        ("8c", ["cn", "mc", "t", "ci"]),
    ],
)
def test_selective_inputs_are_joined_before_the_large_fact_tables(query_id: str, expected_prefix: list[str]) -> None:
    assert _join_order(query_id)[: len(expected_prefix)] == expected_prefix


def test_pandas_frames_carry_only_referenced_columns(pandas_ctx: PandasContext) -> None:
    shape = _shape_for("6a")
    frame = dataframe_queries._prefixed_pandas_frame(
        pandas_ctx, "cast_info", "ci", shape.local_predicates["ci"], shape.referenced_columns["ci"]
    )

    assert sorted(frame.columns) == ["ci__movie_id", "ci__person_id"]


def test_pandas_executor_uses_exact_planned_join_sequence(
    monkeypatch: pytest.MonkeyPatch,
    pandas_ctx: PandasContext,
) -> None:
    expected = _shape_for("1a")
    calls: list[tuple[list[tuple[str, str]], _JoinPlan]] = []

    def spy_plan(
        observed_tables: list[tuple[str, str]],
        observed_join_predicates: list[Any],
        observed_estimates: dict[str, float],
    ) -> _JoinPlan:
        plan = _plan_join_sequence(observed_tables, observed_join_predicates, observed_estimates)
        calls.append((observed_tables, plan))
        return plan

    monkeypatch.setattr(dataframe_queries, "_plan_join_sequence", spy_plan)
    get_dataframe_queries().get_or_raise("1a").pandas_impl(pandas_ctx)

    assert len(calls) == 1
    tables, plan = calls[0]
    assert (tables, plan.anchor, plan.steps) == (expected.tables, expected.anchor, expected.steps)
