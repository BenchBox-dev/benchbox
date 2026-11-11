# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

import pytest

pytest.importorskip("polars", reason="Polars not installed")
pytest.importorskip("pandas", reason="Pandas not installed")
pytest.importorskip("duckdb", reason="DuckDB not installed")

import polars as pl

from benchbox.core.equivalence.cross_surface import (
    EQUIVALENCE_SCALE,
    GATES,
    build_production_contexts,
    find_cross_surface_divergences,
)
from benchbox.core.equivalence.dataframe_surface import fetch_reference_rows, materialize_rows
from benchbox.core.tpchavoc.validation import ResultValidator

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.duckdb,
    pytest.mark.timeout(600),
]


_TARGETS: dict[str, str] = {
    "ssb": "Q3.1",
    "amplab": "4",
    "coffeeshop": "SA1",
    "clickbench": "Q8",
    "joinorder_synthetic": "4a",
    "h2odb": "Q6",
    "read_primitives": "orderby_bigint",
    "flightdata": "ontime-by-carrier",
    "datavault": "Q16",
    "nyctaxi": "borough-summary",
    "tsbs_devops": "double-groupby-1-hr",
    "tpch_skew": "9",
    "tpch": "9",
    "tpcds": "7",
}


def _mutate_rows(rows: list[tuple[Any, ...]], kind: str) -> list[tuple[Any, ...]]:
    if kind == "flip_comparator":
        return rows + [rows[-1]]
    if kind == "drop_group_key":
        return [row[1:] for row in rows]
    if kind == "reverse_sort":
        return list(reversed(rows))
    if kind == "drop_join":
        first = list(rows[0])
        column = _perturbable_column(first)
        first[column] = _perturb_value(first[column])
        return [tuple(first)] + list(rows[1:])
    raise ValueError(f"unknown mutation kind: {kind}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _perturbable_column(row: list[Any]) -> int:
    for index in range(len(row) - 1, -1, -1):
        if _is_number(row[index]):
            return index
    for index in range(len(row) - 1, -1, -1):
        if isinstance(row[index], str):
            return index
    raise ValueError(f"drop_join mutation needs a numeric or string column to perturb; row={row!r}")


def _perturb_value(value: Any) -> Any:
    if _is_number(value):
        return -value - 1
    return f"{value}_ZZZ_MUTATION"


_EXPECT_CAUGHT: dict[tuple[str, str], bool] = {
    ("joinorder_synthetic", "reverse_sort"): False,
}


def _expect_caught(benchmark: str, kind: str) -> bool:
    return _EXPECT_CAUGHT.get((benchmark, kind), True)


def _mutated_dataframe_query(
    dataframe_query: Callable[[Any], Any],
    target: str,
    kind: str,
) -> Callable[[Any], Any]:

    def wrap(impl: Callable[[Any], Any] | None) -> Callable[[Any], Any] | None:
        if impl is None:
            return None

        def wrapped(ctx: Any) -> Any:
            rows = materialize_rows(impl(ctx))
            if not rows:
                return pl.DataFrame()
            mutated = _mutate_rows(rows, kind)
            schema = [f"c{i}" for i in range(len(mutated[0]))]
            return pl.DataFrame(mutated, schema=schema, orient="row")

        return wrapped

    def dispatch(query_id: Any) -> Any:
        query = dataframe_query(query_id)
        if str(query_id) != target:
            return query
        return replace(
            query,
            pandas_impl=wrap(query.pandas_impl),
            expression_impl=wrap(query.expression_impl),
        )

    return dispatch


@dataclass
class _GateCell:
    gate_name: str
    data: Any
    contexts: dict[str, Any]
    reference_row_count: int


def _build_gate_cell(gate_name: str, tmp_path: Any) -> _GateCell:
    gate = GATES[gate_name]
    data = gate.build(gate.scale_factor, tmp_path)
    contexts = build_production_contexts(
        data.benchmark, data.data_dir, backends=gate.backends, scale_factor=gate.scale_factor
    )
    reference_row_count = len(fetch_reference_rows(data.connection, data.reference_sql(_TARGETS[gate_name])))
    return _GateCell(gate_name=gate_name, data=data, contexts=contexts, reference_row_count=reference_row_count)


def _run_target_gate(cell: _GateCell, *, mutation: str | None = None) -> list[Any]:
    gate = GATES[cell.gate_name]
    data = cell.data
    target = _TARGETS[cell.gate_name]
    dataframe_query = data.dataframe_query
    if mutation is not None:
        dataframe_query = _mutated_dataframe_query(data.dataframe_query, target, mutation)
    return find_cross_surface_divergences(
        data.connection,
        query_ids=[target],
        reference_sql=data.reference_sql,
        dataframe_query=dataframe_query,
        contexts=cell.contexts,
        validator=ResultValidator(tolerance=gate.tolerance),
        backends=gate.backends,
    )


_MUTATIONS = ("flip_comparator", "drop_group_key", "reverse_sort", "drop_join")


@pytest.fixture(scope="module")
def gate_cell(request: Any, tmp_path_factory: Any) -> Any:
    gate_name = request.param
    tmp_path = tmp_path_factory.mktemp(f"mutcell_{gate_name}")
    cell = _build_gate_cell(gate_name, tmp_path)
    try:
        yield cell
    finally:
        cell.data.connection.close()


def test_targets_cover_every_enforced_gate() -> None:
    assert set(_TARGETS) == set(GATES), (
        "mutation targets out of sync with enforced GATES: "
        f"missing={set(GATES) - set(_TARGETS)} extra={set(_TARGETS) - set(GATES)}"
    )


@pytest.mark.parametrize("gate_cell", sorted(_TARGETS), indirect=True)
def test_unmutated_target_is_green(gate_cell: _GateCell) -> None:
    gate_name = gate_cell.gate_name
    target = _TARGETS[gate_name]
    divergences = _run_target_gate(gate_cell, mutation=None)
    assert not divergences, f"{gate_name} target {target} is not green before mutation: {divergences}"
    minimum = 1 if gate_name == "joinorder_synthetic" else 2
    assert gate_cell.reference_row_count >= minimum, (
        f"{gate_name} target {target} returned {gate_cell.reference_row_count} reference row(s) "
        f"at SF={GATES[gate_name].scale_factor}; a vacuous/too-small target would make 'not caught' results a "
        f"BS3 artifact - pick a discriminating target."
    )


@pytest.mark.parametrize("gate_cell", sorted(_TARGETS), indirect=True)
@pytest.mark.parametrize("kind", _MUTATIONS)
def test_mutation_sensitivity(kind: str, gate_cell: _GateCell) -> None:
    gate_name = gate_cell.gate_name
    gate = GATES[gate_name]
    target = _TARGETS[gate_name]
    divergences = _run_target_gate(gate_cell, mutation=kind)
    caught = bool(divergences)
    expected = _expect_caught(gate_name, kind)

    if expected:
        expected_keys = {f"{target}_{backend}" for backend in gate.backends}
        found_keys = {d.key for d in divergences}
        missing = expected_keys - found_keys
        assert not missing, (
            f"SENSITIVITY GAP: {gate_name} {target} {kind} was NOT caught by the gate "
            f"on every gated backend (expected RED on {sorted(expected_keys)}; "
            f"missing {sorted(missing)}, got {sorted(found_keys)}). A target query that "
            f"lost an implementation or that only one backend catches must not pass as "
            f"'sensitive' - the comparator missed a seeded bug it should detect."
        )
        errored = [d for d in divergences if d.detail.startswith("error:")]
        assert not errored, (
            f"{gate_name} {target} {kind} was caught only via a harness execution error, "
            f"not a result mismatch: {[(d.key, d.detail) for d in errored]}"
        )
    else:
        assert not caught, (
            f"{gate_name} {target} {kind} was unexpectedly CAUGHT "
            f"({[d.key for d in divergences]}). A documented blind spot is now closed - "
            f"update _EXPECT_CAUGHT and the findings doc."
        )


@pytest.mark.parametrize("gate_cell", ["ssb"], indirect=True)
def test_reversed_sort_is_the_bs2_probe(gate_cell: _GateCell) -> None:
    assert gate_cell.reference_row_count >= 2, (
        f"ssb {_TARGETS['ssb']} returned only {gate_cell.reference_row_count} row(s); reverse-sort probe is vacuous."
    )
    divergences = _run_target_gate(gate_cell, mutation="reverse_sort")
    assert divergences, (
        "ssb Q3.1 reversed-sort was NOT caught - the order-aware comparator (w2) "
        "should make a reversed ORDER BY visible on this multi-row gate."
    )
    errored = [d for d in divergences if d.detail.startswith("error:")]
    assert not errored, (
        f"ssb Q3.1 reversed-sort was flagged only via a harness execution error, "
        f"not an ORDER BY mismatch: {[(d.key, d.detail) for d in errored]}"
    )


def test_gate_output_is_byte_stable_across_two_in_process_runs(tmp_path: Any) -> None:
    benchmark = "ssb"
    gate = GATES[benchmark]

    def run_once(sub: Any) -> list[tuple[str, str]]:
        data = gate.build(EQUIVALENCE_SCALE, sub)
        connection = data.connection
        try:
            contexts = build_production_contexts(data.benchmark, data.data_dir, backends=gate.backends)
            divergences = find_cross_surface_divergences(
                connection,
                query_ids=data.query_ids,
                reference_sql=data.reference_sql,
                dataframe_query=data.dataframe_query,
                contexts=contexts,
                validator=ResultValidator(tolerance=gate.tolerance),
                backends=gate.backends,
            )
        finally:
            connection.close()
        return sorted((d.key, d.detail) for d in divergences)

    first = run_once(tmp_path / "run1")
    second = run_once(tmp_path / "run2")
    assert first == second, f"ssb gate output not byte-stable across runs: {first!r} != {second!r}"


