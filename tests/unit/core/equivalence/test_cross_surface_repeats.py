from __future__ import annotations

from itertools import count
from pathlib import Path
from typing import Any

import duckdb
import pytest

from benchbox.core.equivalence import cross_surface
from benchbox.core.equivalence.cross_surface import (
    CrossSurfaceData,
    CrossSurfaceGate,
    find_flaky_cells,
    main,
    run_gate,
)
from benchbox.core.equivalence.dataframe_surface import SurfaceDivergence

pytestmark = [pytest.mark.unit, pytest.mark.medium]


class _Frame:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def rows(self) -> list[tuple[Any, ...]]:
        return self._rows


class _Query:
    def __init__(self, query_id: str, wrong_on: set[int]) -> None:
        self._query_id = query_id
        self._wrong_on = wrong_on
        self._calls = count(1)

    def get_impl_for_family(self, backend: str):
        if backend != "expression":
            return None

        def impl(_ctx: Any) -> _Frame:
            call = next(self._calls)
            if self._query_id == "Q2" and call in self._wrong_on:
                return _Frame([(999,)])
            return _Frame([(2,)] if self._query_id == "Q2" else [(1,)])

        return impl


def _gate(name: str, wrong_on: set[int]) -> CrossSurfaceGate:
    def build(_scale: float, _tmp: Path) -> CrossSurfaceData:
        queries = {"Q1": _Query("Q1", set()), "Q2": _Query("Q2", wrong_on)}
        return CrossSurfaceData(
            connection=duckdb.connect(":memory:"),
            query_ids=["Q1", "Q2"],
            reference_sql=lambda qid: {"Q1": "SELECT 1", "Q2": "SELECT 2"}[qid],
            dataframe_query=lambda qid: queries[qid],
            benchmark=None,
            data_dir=Path("/unused"),
        )

    return CrossSurfaceGate(name=name, build=build, backends=("expression",))


@pytest.fixture(autouse=True)
def _stub_production_contexts(monkeypatch):
    monkeypatch.setattr(cross_surface, "build_production_contexts", lambda *a, **k: {"expression": None})


def _enforce(monkeypatch, gate: CrossSurfaceGate) -> None:
    monkeypatch.setitem(cross_surface.GATES, gate.name, gate)


def test_find_flaky_cells_reports_a_cell_that_diverges_in_only_some_runs():
    diverged = SurfaceDivergence(query_id="Q2", cell="expression", detail="Value mismatch")
    stable = SurfaceDivergence(query_id="Q9", cell="pandas", detail="Row count mismatch")

    flaky = find_flaky_cells([[diverged, stable], [stable], [stable, diverged]])

    assert flaky == [("Q2", "expression", ["Value mismatch", None, "Value mismatch"])]


def test_find_flaky_cells_ignores_a_cell_that_diverges_the_same_way_every_run():
    stable = SurfaceDivergence(query_id="Q9", cell="pandas", detail="Row count mismatch")

    assert find_flaky_cells([[stable], [stable]]) == []


def test_find_flaky_cells_reports_a_cell_whose_detail_changes():
    first = SurfaceDivergence(query_id="Q2", cell="expression", detail="Value mismatch at row 1")
    second = SurfaceDivergence(query_id="Q2", cell="expression", detail="Value mismatch at row 4")

    assert [(q, c) for q, c, _ in find_flaky_cells([[first], [second]])] == [("Q2", "expression")]


def test_one_run_ignores_a_cell_that_would_flip(monkeypatch, capsys):
    gate = _gate("coin_gate", wrong_on={2})
    _enforce(monkeypatch, gate)

    assert run_gate(gate) == 0
    assert "flaky" not in capsys.readouterr().out


def test_an_enforced_gate_fails_on_a_flaky_cell(monkeypatch, capsys):
    gate = _gate("coin_gate", wrong_on={2})
    _enforce(monkeypatch, gate)

    assert run_gate(gate, repeats=3) == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "[flaky" in out
    assert "Q2_expression: diverged in 1 of 3 runs" in out
    assert "FAIL (enforced gate)" in out


def test_a_staged_gate_reports_a_flaky_cell_but_keeps_its_exit_code(monkeypatch, capsys):
    gate = _gate("staged_coin_gate", wrong_on={2})
    monkeypatch.setitem(cross_surface.STAGED_GATES, gate.name, gate)

    assert run_gate(gate, repeats=3) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "report only (staged gate)" in out


def test_a_cell_that_always_matches_is_not_flaky(monkeypatch, capsys):
    gate = _gate("steady_gate", wrong_on=set())
    _enforce(monkeypatch, gate)

    assert run_gate(gate, repeats=3) == 0
    assert "flaky" not in capsys.readouterr().out


def test_update_baseline_refuses_to_write_from_a_flaky_run(monkeypatch, capsys):
    gate = _gate("coin_gate", wrong_on={2})
    _enforce(monkeypatch, gate)
    wrote = []
    monkeypatch.setattr(cross_surface, "_apply_baseline_update", lambda *a, **k: wrote.append(1) or 0)

    assert run_gate(gate, update_baseline=True, repeats=2) == 1
    assert wrote == []


def test_the_command_line_rejects_fewer_than_one_repeat():
    with pytest.raises(SystemExit):
        main(["--benchmark", "ssb", "--repeats", "0"])


