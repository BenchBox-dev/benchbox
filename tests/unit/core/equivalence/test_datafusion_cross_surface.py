from __future__ import annotations

import subprocess
import sys
from datetime import date
from decimal import Decimal
from itertools import count
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture
def native_cell(tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("datafusion")
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    monkeypatch.setenv("BENCHBOX_CACHE_DIR", str(tmp_path / "cache"))
    table = pa.table(
        {
            "id": [3, 1, 2],
            "amount": pa.array([Decimal("5.00"), None, Decimal("0.00")], type=pa.decimal128(15, 2)),
            "name": [None, "a", "b"],
        }
    )
    path = tmp_path / "t.parquet"
    pq.write_table(table, path)
    connection = duckdb.connect()
    connection.register("t", table)
    benchmark = SimpleNamespace(name="gate_fixture", display_name="Gate fixture", scale_factor=0.01, tables={"t": path})
    yield connection, benchmark, tmp_path
    connection.close()


def _query(implementation):
    from benchbox.core.dataframe.query import DataFrameQuery

    return DataFrameQuery(query_id="q1", query_name="Gate fixture", description="", expression_impl=implementation)


def _ordered(ctx):
    return ctx.get_table("t").select("id", "amount", "name").sort("id")


def test_arrow_rows_preserve_duplicate_columns_types_and_order():
    pa = pytest.importorskip("pyarrow")
    from benchbox.core.equivalence.dataframe_surface import materialize_rows

    table = pa.Table.from_arrays(
        [
            pa.chunked_array([[2], [1]]),
            pa.array([Decimal("3.50"), None], type=pa.decimal128(15, 2)),
            pa.array([date(2001, 1, 2), date(2001, 1, 1)]),
        ],
        names=["value", "value", "day"],
    )
    assert materialize_rows(table) == [(2, 3.5, date(2001, 1, 2)), (1, None, date(2001, 1, 1))]
    assert materialize_rows(table.slice(0, 0)) == []


def test_cross_surface_import_does_not_load_datafusion_sdk():
    code = "import sys; import benchbox.core.equivalence.cross_surface; assert 'datafusion' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("collected", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_native_datafusion_matches_duckdb_through_production_loader(native_cell, collected, empty):
    from benchbox.core.equivalence.cross_surface import build_production_contexts, find_cross_surface_divergences
    from benchbox.core.tpchavoc.validation import ResultValidator

    connection, benchmark, data_dir = native_cell
    contexts = build_production_contexts(benchmark, data_dir, backends=("datafusion",), scale_factor=0.01)
    assert contexts["datafusion"].platform == "DataFusion"

    def implementation(ctx):
        frame = _ordered(ctx)
        if empty:
            frame = frame.filter(ctx.col("id") < ctx.lit(0))
        return frame.collect() if collected else frame

    sql = "SELECT id, amount, name FROM t" + (" WHERE id < 0" if empty else "") + " ORDER BY id"
    divergences = find_cross_surface_divergences(
        connection,
        query_ids=["q1"],
        reference_sql=lambda _: sql,
        dataframe_query=lambda _: _query(implementation),
        contexts=contexts,
        backends=("datafusion",),
        validator=ResultValidator(),
    )
    assert divergences == []


@pytest.mark.parametrize("mutation", ["value", "order", "null", "error"])
def test_native_datafusion_mutations_fail_the_gate(native_cell, monkeypatch, mutation):
    from benchbox.core.equivalence import cross_surface
    from benchbox.core.equivalence.builders.base import CrossSurfaceData

    connection, benchmark, data_dir = native_cell

    def implementation(ctx):
        frame = _ordered(ctx)
        if mutation == "value":
            return frame.with_columns((ctx.col("amount") + ctx.lit(1)).alias("amount"))
        if mutation == "order":
            return frame.sort("id", descending=True)
        if mutation == "null":
            return frame.with_columns(ctx.col("amount").fill_null(0).alias("amount"))
        return frame.select("missing_column")

    data = CrossSurfaceData(
        connection=connection,
        query_ids=["q1"],
        reference_sql=lambda _: "SELECT id, amount, name FROM t ORDER BY id",
        dataframe_query=lambda _: _query(implementation),
        benchmark=benchmark,
        data_dir=data_dir,
    )
    gate = cross_surface.CrossSurfaceGate(name="datafusion_fixture", build=lambda *_: data, backends=("datafusion",))
    monkeypatch.setitem(cross_surface.GATES, gate.name, gate)
    assert cross_surface.run_gate(gate) == 1


def test_datafusion_coverage_counts_expression_implementations():
    from benchbox.core.dataframe.query import DataFrameQuery
    from benchbox.core.equivalence.cross_surface import count_executed_cells

    queries = {
        "present": _query(_ordered),
        "absent": DataFrameQuery(query_id="absent", query_name="Absent", description="", pandas_impl=lambda _: None),
    }
    assert count_executed_cells(queries, queries.__getitem__, ("datafusion", "pandas")) == {
        "datafusion": 1,
        "pandas": 1,
    }
    assert count_executed_cells(["absent"], queries.__getitem__, ("datafusion",)) == {"datafusion": 0}


def test_missing_datafusion_implementation_fails_the_gate(native_cell, monkeypatch):
    from benchbox.core.dataframe.query import DataFrameQuery
    from benchbox.core.equivalence import cross_surface
    from benchbox.core.equivalence.builders.base import CrossSurfaceData

    connection, benchmark, data_dir = native_cell
    data = CrossSurfaceData(
        connection=connection,
        query_ids=["q1"],
        reference_sql=lambda _: "SELECT id, amount, name FROM t ORDER BY id",
        dataframe_query=lambda _: DataFrameQuery(
            query_id="q1", query_name="Absent", description="", pandas_impl=lambda _: None
        ),
        benchmark=benchmark,
        data_dir=data_dir,
    )
    gate = cross_surface.CrossSurfaceGate(name="datafusion_fixture", build=lambda *_: data, backends=("datafusion",))
    monkeypatch.setitem(cross_surface.GATES, gate.name, gate)
    assert cross_surface.run_gate(gate) == 1


def test_native_datafusion_repeats_catch_changed_results(native_cell, monkeypatch, capsys):
    from benchbox.core.equivalence import cross_surface
    from benchbox.core.equivalence.builders.base import CrossSurfaceData

    connection, benchmark, data_dir = native_cell
    calls = count(1)

    def implementation(ctx):
        frame = _ordered(ctx)
        if next(calls) == 2:
            frame = frame.with_columns((ctx.col("amount") + ctx.lit(1)).alias("amount"))
        return frame

    data = CrossSurfaceData(
        connection=connection,
        query_ids=["q1"],
        reference_sql=lambda _: "SELECT id, amount, name FROM t ORDER BY id",
        dataframe_query=lambda _: _query(implementation),
        benchmark=benchmark,
        data_dir=data_dir,
    )
    gate = cross_surface.CrossSurfaceGate(name="datafusion_fixture", build=lambda *_: data, backends=("datafusion",))
    monkeypatch.setitem(cross_surface.GATES, gate.name, gate)
    assert cross_surface.run_gate(gate, repeats=3) == 1
    assert next(calls) == 4
    output = " ".join(capsys.readouterr().out.split())
    assert "q1_datafusion: diverged in 1 of 3 runs" in output
    assert "FAIL (enforced gate)" in output


