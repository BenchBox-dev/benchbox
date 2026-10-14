# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest

from benchbox.core.equivalence.cross_surface import (
    ORDER_UNORDERED,
    ORDER_UNVERIFIABLE,
    ORDER_VERIFIED,
    ClassifiedDivergence,
    CrossSurfaceGate,
    _apply_baseline_update,
    _bump_trailing_limit,
    _derived_order_plan,
    _derived_order_violation,
    _final_key_tied_beyond_limit,
    _order_report_lines,
    _report,
    count_executed_cells,
    find_cross_surface_divergences,
)
from benchbox.core.equivalence.dataframe_surface import SurfaceDivergence
from benchbox.core.tpchavoc.validation import ResultValidator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_production_contexts_reject_unsupported_execution_engine(tmp_path):
    import benchbox.core.equivalence.cross_surface as cross_surface
    from benchbox.core.execution_engine import UnsupportedExecutionEngineError

    with pytest.raises(UnsupportedExecutionEngineError, match="execution engine 'gpu'"):
        cross_surface.build_production_contexts(object(), tmp_path, backends=("expression",), execution_engine="gpu")


def test_run_gate_rejects_unsupported_execution_engine_before_build():
    import benchbox.core.equivalence.cross_surface as cross_surface
    from benchbox.core.execution_engine import UnsupportedExecutionEngineError

    gate = CrossSurfaceGate(name="fake", build=lambda scale, path: pytest.fail("built gate data"))

    with pytest.raises(UnsupportedExecutionEngineError, match="execution engine 'gpu'"):
        cross_surface.run_gate(gate, execution_engine="gpu")


def test_production_contexts_reject_engine_without_expression_backend(tmp_path):
    import benchbox.core.equivalence.cross_surface as cross_surface

    with pytest.raises(ValueError, match="requires the expression"):
        cross_surface.build_production_contexts(object(), tmp_path, backends=("pandas",), execution_engine="streaming")


def test_production_contexts_execute_polars_collect_with_requested_engine(monkeypatch, tmp_path):
    pl = pytest.importorskip("polars")

    import benchbox.core.equivalence.cross_surface as cross_surface
    import benchbox.platforms.dataframe.polars_df as polars_df

    engines = []
    collect_frame = polars_df.collect_frame

    def spy_collect_frame(frame, engine):
        engines.append(engine)
        return collect_frame(frame, engine)

    def load_and_collect(adapter, benchmark, data_dir, *, scale_factor):
        return adapter.materialize(pl.DataFrame({"value": [1]}).lazy())

    monkeypatch.setattr(polars_df, "collect_frame", spy_collect_frame)
    monkeypatch.setattr(polars_df.PolarsDataFrameAdapter, "load_benchmark_into_context", load_and_collect)

    contexts = cross_surface.build_production_contexts(
        object(), tmp_path, backends=("expression",), execution_engine="streaming"
    )

    assert contexts["expression"].to_dict(as_series=False) == {"value": [1]}
    assert engines == ["streaming"]


def test_cli_runs_polars_with_requested_engine(monkeypatch, tmp_path):
    from types import SimpleNamespace

    pl = pytest.importorskip("polars")

    import benchbox.core.equivalence.cross_surface as cross_surface
    import benchbox.platforms.dataframe.polars_df as polars_df

    engines = []
    connection = SimpleNamespace(close=lambda: None)
    data = SimpleNamespace(
        connection=connection,
        benchmark=object(),
        data_dir=tmp_path,
        query_ids=("Q1",),
        reference_sql={},
        dataframe_query={},
    )
    gate = CrossSurfaceGate(
        name="fake",
        build=lambda scale, path: data,
        backends=("expression",),
    )

    def load_and_collect(adapter, benchmark, data_dir, *, scale_factor):
        return adapter.materialize(pl.DataFrame({"value": [1]}).lazy())

    collect_frame = polars_df.collect_frame

    def spy_collect_frame(frame, engine):
        engines.append(engine)
        return collect_frame(frame, engine)

    monkeypatch.setattr(cross_surface, "get_gate", lambda _: gate)
    monkeypatch.setattr(polars_df, "collect_frame", spy_collect_frame)
    monkeypatch.setattr(polars_df.PolarsDataFrameAdapter, "load_benchmark_into_context", load_and_collect)
    monkeypatch.setattr(cross_surface, "find_cross_surface_divergences", lambda *a, **k: [])
    monkeypatch.setattr(cross_surface, "count_executed_cells", lambda *a, **k: {"expression": 1})
    monkeypatch.setattr(cross_surface, "_report", lambda *a, **k: 0)

    assert cross_surface.main(["--execution-engine", "streaming"]) == 0
    assert engines == ["streaming"]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--execution-engine", "bogus"], "does not support execution engine 'bogus'"),
        (["--execution-engine", "streaming", "--backend", "pandas"], "requires the expression"),
    ],
)
def test_cli_reports_invalid_execution_engine_as_a_usage_error(monkeypatch, capsys, argv, message):
    import benchbox.core.equivalence.cross_surface as cross_surface

    monkeypatch.setattr(cross_surface, "run_gate", lambda *a, **k: pytest.fail("ran the gate"))

    with pytest.raises(SystemExit) as excinfo:
        cross_surface.main(argv)

    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "usage:" in stderr
    assert message in stderr
    assert "Traceback" not in stderr


def test_dtype_cell_collects_with_the_requested_engine(monkeypatch, tmp_path):
    pl = pytest.importorskip("polars")

    import benchbox.core.equivalence.cross_surface as cross_surface
    import benchbox.platforms.dataframe.polars_df as polars_df

    engines = []
    collect_frame = polars_df.collect_frame

    def spy_collect_frame(frame, engine):
        engines.append(engine)
        return collect_frame(frame, engine)

    def load_frame(adapter, benchmark, data_dir, *, scale_factor):
        ctx = adapter.create_context()
        ctx.register_table("t", pl.LazyFrame({"value": [1]}))
        return ctx

    monkeypatch.setattr(polars_df, "collect_frame", spy_collect_frame)
    monkeypatch.setattr(polars_df.PolarsDataFrameAdapter, "load_benchmark_into_context", load_frame)
    contexts = cross_surface.build_production_contexts(
        object(), tmp_path, backends=("expression",), execution_engine="streaming"
    )

    class _Query:
        def get_impl_for_family(self, family):
            return lambda ctx: ctx.get_table("t")

    class _Connection:
        def execute(self, sql):
            import pyarrow as pa

            return type("R", (), {"fetch_arrow_table": lambda self: pa.table({"value": [1]})})()

    divergences, compared = cross_surface.find_cross_surface_dtype_divergences(
        _Connection(),
        query_ids=["Q1"],
        reference_sql=lambda query_id: "SELECT 1",
        dataframe_query=lambda query_id: _Query(),
        contexts=contexts,
    )

    assert divergences == []
    assert compared == {"expression": 1}
    assert engines == ["streaming"]


def test_run_gate_rejects_baseline_update_with_explicit_engine():
    import benchbox.core.equivalence.cross_surface as cross_surface

    gate = CrossSurfaceGate(name="fake", build=lambda scale, path: pytest.fail("built gate data"))

    with pytest.raises(ValueError, match="Baseline updates cannot be combined"):
        cross_surface.run_gate(gate, update_baseline=True, execution_engine="streaming")


def test_production_contexts_keep_version_default_when_engine_is_unset(monkeypatch, tmp_path):
    pl = pytest.importorskip("polars")

    import benchbox.core.equivalence.cross_surface as cross_surface
    import benchbox.platforms.dataframe.polars_df as polars_df

    expected_engine = polars_df.PolarsDataFrameAdapter().collect_engine
    engines = []
    collect_frame = polars_df.collect_frame

    def spy_collect_frame(frame, engine):
        engines.append(engine)
        return collect_frame(frame, engine)

    def load_and_collect(adapter, benchmark, data_dir, *, scale_factor):
        return adapter.materialize(pl.DataFrame({"value": [1]}).lazy())

    monkeypatch.setattr(polars_df, "collect_frame", spy_collect_frame)
    monkeypatch.setattr(polars_df.PolarsDataFrameAdapter, "load_benchmark_into_context", load_and_collect)

    contexts = cross_surface.build_production_contexts(object(), tmp_path, backends=("expression",))

    assert contexts["expression"].to_dict(as_series=False) == {"value": [1]}
    assert engines == [expected_engine]


def test_cross_surface_builders_are_decomposed_and_share_loader():
    import benchbox.core.equivalence.cross_surface as cross_surface

    builder_names = {
        "build_ssb_duckdb",
        "build_amplab_duckdb",
        "build_clickbench_duckdb",
        "build_coffeeshop_duckdb",
        "build_joinorder_synthetic_duckdb",
        "build_h2odb_duckdb",
        "build_read_primitives_duckdb",
    }

    cross_surface_source = inspect.getsource(cross_surface)
    for name in builder_names:
        builder = getattr(cross_surface, name)
        assert builder.__module__.startswith("benchbox.core.equivalence.builders.")
        assert f"def {name}" not in cross_surface_source

    for name in ("build_clickbench_duckdb", "build_coffeeshop_duckdb", "build_joinorder_synthetic_duckdb"):
        builder_source = inspect.getsource(getattr(cross_surface, name))
        assert "_load_duckdb_cell(" in builder_source
        assert "DuckDBAdapter" not in builder_source


class _FakeFrame:
    def __init__(self, rows):
        self._rows = rows

    def rows(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows_by_sql):
        self._rows_by_sql = rows_by_sql

    def execute(self, sql):
        self._result = self._rows_by_sql[sql]
        return self

    def fetchall(self):
        return self._result


class _FakeQuery:
    def __init__(self, impls):
        self._impls = impls

    def get_impl_for_family(self, family):
        return self._impls.get(family)


def _make_inputs(reference_rows, impls_by_backend, sql="SELECT * FROM t"):
    connection = _FakeConnection({sql: reference_rows})

    def reference_sql(_qid):
        return sql

    def dataframe_query(_qid):
        return _FakeQuery({b: (lambda ctx, rows=rows: _FakeFrame(rows)) for b, rows in impls_by_backend.items()})

    contexts = {"expression": object(), "pandas": object()}
    return connection, reference_sql, dataframe_query, contexts


def test_matching_surfaces_yield_no_divergences():
    rows = [(1, 2.0), (3, 4.0)]
    connection, reference_sql, dataframe_query, contexts = _make_inputs(rows, {"expression": rows, "pandas": rows})
    divergences = find_cross_surface_divergences(
        connection,
        query_ids=["Q1.1"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts=contexts,
        validator=ResultValidator(),
    )
    assert divergences == []


def test_divergent_backend_is_reported_with_backend_cell():
    ref = [(1, 2.0)]
    connection, reference_sql, dataframe_query, contexts = _make_inputs(
        ref, {"expression": ref, "pandas": [(1, 999.0)]}
    )
    divergences = find_cross_surface_divergences(
        connection,
        query_ids=["Q1.1"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts=contexts,
        validator=ResultValidator(),
    )
    assert [(d.query_id, d.cell, d.key) for d in divergences] == [("Q1.1", "pandas", "Q1.1_pandas")]


def test_strict_default_reports_dataframe_nan_against_sql_null():
    ref = [(None,)]
    connection, reference_sql, dataframe_query, contexts = _make_inputs(ref, {"pandas": [(float("nan"),)]})
    divergences = find_cross_surface_divergences(
        connection,
        query_ids=["Qnan"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts=contexts,
        validator=ResultValidator(),
        backends=("pandas",),
    )
    assert [(d.query_id, d.cell, d.key) for d in divergences] == [("Qnan", "pandas", "Qnan_pandas")]
    assert "Value mismatch" in divergences[0].detail


def test_strict_default_reports_trailing_whitespace_divergence():
    ref = [("foo",)]
    connection, reference_sql, dataframe_query, contexts = _make_inputs(ref, {"expression": [("foo ",)]})
    divergences = find_cross_surface_divergences(
        connection,
        query_ids=["Qspace"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts=contexts,
        validator=ResultValidator(),
        backends=("expression",),
    )
    assert [(d.query_id, d.cell, d.key) for d in divergences] == [("Qspace", "expression", "Qspace_expression")]
    assert "Value mismatch" in divergences[0].detail


def test_explicit_value_widening_flags_accept_documented_decode_cases():
    ref = [(None, "foo")]
    connection, reference_sql, dataframe_query, contexts = _make_inputs(ref, {"pandas": [(float("nan"), "foo ")]})
    divergences = find_cross_surface_divergences(
        connection,
        query_ids=["Qtolerated"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts=contexts,
        validator=ResultValidator(treat_nan_as_null=True, strip_strings=True),
        backends=("pandas",),
    )
    assert divergences == []


def test_tie_aware_only_applies_to_truncated_top_n_queries():
    ref = [(1, 5), (2, 3), (3, 3)]
    swapped = [(1, 5), (2, 3), (99, 3)]

    connection, reference_sql, dataframe_query, contexts = _make_inputs(
        ref, {"expression": swapped}, sql="SELECT a, c FROM t ORDER BY c DESC"
    )
    strict = find_cross_surface_divergences(
        connection,
        query_ids=["Q1"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts=contexts,
        validator=ResultValidator(),
        backends=("expression",),
    )
    assert [d.key for d in strict] == ["Q1_expression"], "non-LIMIT swap must be reported, not masked"

    connection, reference_sql, dataframe_query, contexts = _make_inputs(
        ref, {"expression": swapped}, sql="SELECT a, c FROM t ORDER BY c DESC LIMIT 3"
    )
    connection._rows_by_sql["SELECT a, c FROM t ORDER BY c DESC LIMIT 4"] = [(1, 5), (2, 3), (3, 3), (4, 3)]
    relaxed = find_cross_surface_divergences(
        connection,
        query_ids=["Q1"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts=contexts,
        validator=ResultValidator(),
        backends=("expression",),
    )
    assert relaxed == [], "boundary-tie swap under a LIMIT query should be tolerated"


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("SELECT a FROM t ORDER BY a DESC LIMIT 2", "SELECT a FROM t ORDER BY a DESC LIMIT 3"),
        ("SELECT a FROM t ORDER BY a LIMIT 10 OFFSET 5", "SELECT a FROM t ORDER BY a LIMIT 11 OFFSET 5"),
        ("select a from t order by a limit 1;", "select a from t order by a limit 2;"),
        ("SELECT a FROM t ORDER BY a LIMIT 7 -- top seven", "SELECT a FROM t ORDER BY a LIMIT 8"),
        ("SELECT a FROM t ORDER BY a", None),
    ],
)
def test_bump_trailing_limit_raises_the_cutoff_by_one(sql, expected):
    assert _bump_trailing_limit(sql) == expected


def test_final_key_tied_beyond_limit_true_when_key_recurs_past_cutoff():
    sql = "SELECT a, b FROM t ORDER BY a DESC LIMIT 2"
    connection = _FakeConnection({"SELECT a, b FROM t ORDER BY a DESC LIMIT 3": [(10, "x"), (5, "a"), (5, "b")]})
    reference = [(10, "x"), (5, "a")]
    assert _final_key_tied_beyond_limit(connection, sql, [0], reference) is True


def test_final_key_tied_beyond_limit_false_when_no_row_past_cutoff():
    sql = "SELECT a, b FROM t ORDER BY a DESC LIMIT 2"
    connection = _FakeConnection({"SELECT a, b FROM t ORDER BY a DESC LIMIT 3": [(10, "x"), (5, "good")]})
    reference = [(10, "x"), (5, "good")]
    assert _final_key_tied_beyond_limit(connection, sql, [0], reference) is False


def test_final_key_tied_beyond_limit_false_when_next_row_is_a_distinct_key():
    sql = "SELECT a, b FROM t ORDER BY a DESC LIMIT 2"
    connection = _FakeConnection({"SELECT a, b FROM t ORDER BY a DESC LIMIT 3": [(10, "x"), (5, "a"), (3, "c")]})
    reference = [(10, "x"), (5, "a")]
    assert _final_key_tied_beyond_limit(connection, sql, [0], reference) is False


def test_final_key_tied_beyond_limit_checks_multi_row_final_group():
    sql = "SELECT a, b FROM t ORDER BY a DESC LIMIT 3"
    connection = _FakeConnection(
        {"SELECT a, b FROM t ORDER BY a DESC LIMIT 4": [(10, "x"), (5, "a"), (5, "b"), (5, "c")]}
    )
    reference = [(10, "x"), (5, "a"), (5, "b")]
    assert _final_key_tied_beyond_limit(connection, sql, [0], reference) is True


def test_multi_row_complete_final_tie_value_bug_is_caught_via_probe():
    sql = "SELECT a, b FROM t ORDER BY a DESC LIMIT 3"
    reference = [(10, "x"), (5, "a"), (5, "b")]
    candidate = [(10, "x"), (5, "a"), (5, "bad")]
    connection = _FakeConnection(
        {
            sql: reference,
            "SELECT a, b FROM t ORDER BY a DESC LIMIT 4": reference,
        }
    )

    def reference_sql(_qid):
        return sql

    def dataframe_query(_qid):
        return _FakeQuery({"expression": lambda ctx: _FakeFrame(candidate)})

    divergences = find_cross_surface_divergences(
        connection,
        query_ids=["Q1"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts={"expression": object()},
        validator=ResultValidator(),
        backends=("expression",),
    )
    assert [d.key for d in divergences] == ["Q1_expression"], "complete final tie divergence must be reported"


def test_one_visible_row_boundary_tie_is_not_a_divergence_via_probe():
    sql = "SELECT a, b FROM t ORDER BY a DESC LIMIT 2"
    reference = [(10, "x"), (5, "a")]
    candidate = [(10, "x"), (5, "b")]
    connection = _FakeConnection(
        {
            sql: reference,
            "SELECT a, b FROM t ORDER BY a DESC LIMIT 3": [(10, "x"), (5, "a"), (5, "b")],
        }
    )

    def reference_sql(_qid):
        return sql

    def dataframe_query(_qid):
        return _FakeQuery({"expression": lambda ctx: _FakeFrame(candidate)})

    divergences = find_cross_surface_divergences(
        connection,
        query_ids=["Q1"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts={"expression": object()},
        validator=ResultValidator(),
        backends=("expression",),
    )
    assert divergences == [], "a probe-confirmed one-visible-row boundary tie must not be flagged"


def test_one_visible_row_final_value_bug_is_caught_when_not_tied_past_limit():
    sql = "SELECT a, b FROM t ORDER BY a DESC LIMIT 2"
    reference = [(10, "x"), (5, "good")]
    candidate = [(10, "x"), (5, "bad")]
    connection = _FakeConnection(
        {
            sql: reference,
            "SELECT a, b FROM t ORDER BY a DESC LIMIT 3": [(10, "x"), (5, "good")],
        }
    )

    def reference_sql(_qid):
        return sql

    def dataframe_query(_qid):
        return _FakeQuery({"expression": lambda ctx: _FakeFrame(candidate)})

    divergences = find_cross_surface_divergences(
        connection,
        query_ids=["Q1"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts={"expression": object()},
        validator=ResultValidator(),
        backends=("expression",),
    )
    assert [d.key for d in divergences] == ["Q1_expression"], "a deterministic final-row bug must still be caught"


def test_missing_backend_impl_is_skipped_not_a_divergence():
    ref = [(1, 2.0)]
    connection, reference_sql, dataframe_query, contexts = _make_inputs(ref, {"expression": ref})
    divergences = find_cross_surface_divergences(
        connection,
        query_ids=["Q1.1"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts=contexts,
        validator=ResultValidator(),
        backends=("expression", "pandas"),
    )
    assert divergences == []


def test_count_executed_cells_detects_a_fully_absent_backend():
    def dataframe_query(qid):
        return _FakeQuery({"expression": lambda ctx: _FakeFrame([])})

    coverage = count_executed_cells(["Q1.1", "Q1.2"], dataframe_query, ("expression", "pandas"))
    assert coverage == {"expression": 2, "pandas": 0}
    assert [b for b, n in coverage.items() if n == 0] == ["pandas"]


def test_count_executed_cells_full_coverage():
    def dataframe_query(qid):
        return _FakeQuery({"expression": lambda ctx: _FakeFrame([]), "pandas": lambda ctx: _FakeFrame([])})

    coverage = count_executed_cells(["Q1.1", "Q1.2", "Q1.3"], dataframe_query, ("expression", "pandas"))
    assert coverage == {"expression": 3, "pandas": 3}


def test_reference_failure_records_one_cell_and_skips_candidates():
    def reference_sql(_qid):
        return "SELECT bad"

    class _BoomConnection:
        def execute(self, sql):
            raise RuntimeError("no such table")

    def dataframe_query(_qid):  # pragma: no cover
        raise AssertionError("candidates must not run without a reference")

    divergences = find_cross_surface_divergences(
        _BoomConnection(),
        query_ids=["Q1.1"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts={"expression": object(), "pandas": object()},
        validator=ResultValidator(),
    )
    assert len(divergences) == 1
    assert divergences[0].cell == "reference"
    assert divergences[0].key == "Q1.1_reference"
    assert "no such table" in divergences[0].detail


def test_reference_row_counts_are_populated_when_requested():
    rows = [(1, 2.0), (3, 4.0)]
    connection, reference_sql, dataframe_query, contexts = _make_inputs(rows, {"expression": rows, "pandas": rows})
    counts: dict = {}
    find_cross_surface_divergences(
        connection,
        query_ids=["Q1.1"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts=contexts,
        validator=ResultValidator(),
        reference_row_counts=counts,
    )
    assert counts == {"Q1.1": 2}


def test_report_fails_on_unclassified_vacuous_query():
    coverage = {"expression": 1, "pandas": 1}
    exit_code = _report(
        [],
        total=2,
        coverage=coverage,
        known={},
        benchmark="fake",
        reference_row_counts={"Q1": 0},
        legitimately_empty={},
    )
    assert exit_code == 1, "an unclassified vacuous (0-row) query must FAIL the gate"


def test_report_passes_when_vacuous_query_is_classified():
    coverage = {"expression": 1, "pandas": 1}
    exit_code = _report(
        [],
        total=2,
        coverage=coverage,
        known={},
        benchmark="fake",
        reference_row_counts={"Q1": 0},
        legitimately_empty={"Q1": "genuinely empty at the bounded cell - rationale"},
    )
    assert exit_code == 0, "a classified legitimately-empty query must pass"


def test_report_excludes_vacuous_cells_from_discriminating_count(capsys):
    coverage = {"expression": 2, "pandas": 2}
    _report(
        [],
        total=4,
        coverage=coverage,
        known={},
        benchmark="fake",
        reference_row_counts={"Q1": 5, "Q2": 0},
        legitimately_empty={"Q2": "classified"},
    )
    out = capsys.readouterr().out
    assert "compared 2 of 4 query-backend cells" in out
    assert "2 vacuous empty-vs-empty" in out


def test_report_fails_on_stale_legitimately_empty_entry(capsys):
    exit_code = _report(
        [],
        total=2,
        coverage={"expression": 1, "pandas": 1},
        known={},
        benchmark="fake",
        reference_row_counts={"Q1": 3},
        legitimately_empty={"Q1": "was empty before"},
    )
    assert exit_code == 1
    assert "legitimately_empty entries whose reference now returns rows: ['Q1']" in capsys.readouterr().out


def test_report_counts_all_null_rows_apart_from_zero_rows(capsys):
    exit_code = _report(
        [],
        total=4,
        coverage={"expression": 2, "pandas": 2},
        known={},
        benchmark="fake",
        reference_row_counts={"Q1": 0, "Q2": 0},
        legitimately_empty={"Q1": "zero rows", "Q2": "all-NULL aggregate"},
        all_null_references={"Q2"},
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "compared 0 of 4 query-backend cells" in out
    assert "vacuous queries: 1 zero-row, 1 single all-NULL row" in out
    assert "Q2 [all-NULL row]: all-NULL aggregate" in out
    assert "Q1: zero rows" in out


def test_all_null_reference_is_recorded_as_vacuous_and_all_null():
    rows = [(None, None)]
    connection, reference_sql, dataframe_query, contexts = _make_inputs(rows, {"expression": rows, "pandas": rows})
    counts: dict = {}
    all_null: set = set()
    find_cross_surface_divergences(
        connection,
        query_ids=["Q1"],
        reference_sql=reference_sql,
        dataframe_query=dataframe_query,
        contexts=contexts,
        validator=ResultValidator(),
        reference_row_counts=counts,
        all_null_references=all_null,
    )
    assert counts == {"Q1": 0}
    assert all_null == {"Q1"}


def test_report_with_real_divergence_still_fails_for_nonempty_query():
    coverage = {"expression": 1, "pandas": 1}
    exit_code = _report(
        [SurfaceDivergence("Q1", "pandas", "value mismatch")],
        total=2,
        coverage=coverage,
        known={},
        benchmark="fake",
        reference_row_counts={"Q1": 5},
        legitimately_empty={},
    )
    assert exit_code == 1


def test_known_divergence_baseline_fails_when_entry_is_resolved(capsys):
    coverage = {"expression": 1, "pandas": 1}

    exit_code = _report(
        [],
        total=2,
        coverage=coverage,
        known={"Q1_pandas": "documented test baseline"},
        benchmark="fake",
        reference_row_counts={"Q1": 5},
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "GATE FAILURE - previously-known divergences now equivalent: ['Q1_pandas']" in out
    assert "remove the stale baseline entry" in out


def test_known_divergence_baseline_allows_nondeterministic_absence(capsys):
    coverage = {"expression": 1, "pandas": 1}

    exit_code = _report(
        [],
        total=2,
        coverage=coverage,
        known={
            "Q1_pandas": ClassifiedDivergence(
                reason="arbitrary top-N selection",
                accepts=lambda divergence: "Value mismatch" in str(divergence.detail),
                requires_live_divergence=False,
            )
        },
        benchmark="fake",
        reference_row_counts={"Q1": 5},
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "previously-known divergences now equivalent" not in out
    assert "SQL and DataFrame surfaces are equivalent (modulo classified exceptions)." in out


def test_known_divergence_baseline_accepts_live_entry(capsys):
    coverage = {"expression": 1, "pandas": 1}

    exit_code = _report(
        [SurfaceDivergence("Q1", "pandas", "accepted mismatch")],
        total=2,
        coverage=coverage,
        known={"Q1_pandas": "documented test baseline"},
        benchmark="fake",
        reference_row_counts={"Q1": 5},
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "[documented test baseline]" in out
    assert "SQL and DataFrame surfaces are equivalent (modulo classified exceptions)." in out


def test_known_divergence_baseline_does_not_hide_new_unclassified_divergence(capsys):
    coverage = {"expression": 1, "pandas": 1}

    exit_code = _report(
        [SurfaceDivergence("Q2", "pandas", "new mismatch")],
        total=2,
        coverage=coverage,
        known={},
        benchmark="fake",
        reference_row_counts={"Q2": 5},
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "GATE FAILURE - unclassified cross-surface divergences: ['Q2_pandas']" in out


def test_review_by_past_due_warns_but_does_not_fail_the_gate(capsys):
    coverage = {"expression": 1, "pandas": 1}

    exit_code = _report(
        [SurfaceDivergence("Q1", "pandas", "accepted mismatch")],
        total=2,
        coverage=coverage,
        known={
            "Q1_pandas": ClassifiedDivergence(
                reason="synthetic long-lived waiver",
                accepts=lambda divergence: "accepted" in str(divergence.detail),
                review_by=date(2020, 1, 1),
            )
        },
        benchmark="fake",
        reference_row_counts={"Q1": 5},
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "WAIVER REVIEW DUE - Q1_pandas: review_by 2020-01-01 has passed - synthetic long-lived waiver" in out


def test_review_by_future_date_does_not_warn(capsys):
    coverage = {"expression": 1, "pandas": 1}

    exit_code = _report(
        [SurfaceDivergence("Q1", "pandas", "accepted mismatch")],
        total=2,
        coverage=coverage,
        known={
            "Q1_pandas": ClassifiedDivergence(
                reason="synthetic waiver, not due",
                accepts=lambda divergence: "accepted" in str(divergence.detail),
                review_by=date(2099, 1, 1),
            )
        },
        benchmark="fake",
        reference_row_counts={"Q1": 5},
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "WAIVER REVIEW DUE" not in out


def test_review_by_absent_by_default_produces_no_warning(capsys):
    coverage = {"expression": 1, "pandas": 1}

    exit_code = _report(
        [SurfaceDivergence("Q1", "pandas", "accepted mismatch")],
        total=2,
        coverage=coverage,
        known={"Q1_pandas": "documented test baseline"},
        benchmark="fake",
        reference_row_counts={"Q1": 5},
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "WAIVER REVIEW DUE" not in out


def _make_gate(name: str, known_divergences: dict) -> CrossSurfaceGate:
    return CrossSurfaceGate(name=name, build=lambda scale, tmp: None, known_divergences=known_divergences)


def test_update_baseline_prunes_resolved_entry_when_nothing_else_is_wrong(monkeypatch, capsys):
    gate = _make_gate("fake", {"Q1_pandas": "documented test baseline"})
    calls = []
    monkeypatch.setattr(
        "benchbox.core.equivalence.known_divergences_baseline.update_baseline_file",
        lambda path, resolved, gate_name: calls.append((sorted(resolved), gate_name)) or list(resolved),
    )

    exit_code = _apply_baseline_update(
        gate,
        divergences=[],
        total=2,
        coverage={"expression": 1, "pandas": 1},
        reference_row_counts={"Q1": 5},
        vacuous_cells=0,
    )

    assert exit_code == 0
    assert calls == [(["Q1_pandas"], "fake")]
    assert "removed resolved known-divergence baseline entries" in capsys.readouterr().out


def test_update_baseline_refuses_to_write_on_an_unrelated_regression(monkeypatch, capsys):
    gate = _make_gate("fake", {"Q1_pandas": "documented test baseline"})
    calls = []
    monkeypatch.setattr(
        "benchbox.core.equivalence.known_divergences_baseline.update_baseline_file",
        lambda path, resolved, gate_name: calls.append((sorted(resolved), gate_name)) or list(resolved),
    )

    exit_code = _apply_baseline_update(
        gate,
        divergences=[SurfaceDivergence("Q2", "pandas", "new mismatch")],
        total=4,
        coverage={"expression": 2, "pandas": 2},
        reference_row_counts={"Q1": 5, "Q2": 5},
        vacuous_cells=0,
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "GATE FAILURE - unclassified cross-surface divergences: ['Q2_pandas']" in out
    assert "refusing to update baseline" in out
    assert calls == []


def test_update_baseline_with_nothing_resolved_still_enforces_other_failures(monkeypatch, capsys):
    gate = _make_gate("fake", {})
    monkeypatch.setattr(
        "benchbox.core.equivalence.known_divergences_baseline.update_baseline_file",
        lambda path, resolved, gate_name: pytest.fail("update_baseline_file must not be called"),
    )

    exit_code = _apply_baseline_update(
        gate,
        divergences=[SurfaceDivergence("Q2", "pandas", "new mismatch")],
        total=2,
        coverage={"expression": 1, "pandas": 1},
        reference_row_counts={"Q2": 5},
        vacuous_cells=0,
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "GATE FAILURE - unclassified cross-surface divergences: ['Q2_pandas']" in out
    assert "refusing to update baseline" in out


def test_update_baseline_with_nothing_resolved_and_run_clean_is_a_noop(monkeypatch, capsys):
    gate = _make_gate("fake", {})
    monkeypatch.setattr(
        "benchbox.core.equivalence.known_divergences_baseline.update_baseline_file",
        lambda path, resolved, gate_name: pytest.fail("update_baseline_file must not be called"),
    )

    exit_code = _apply_baseline_update(
        gate,
        divergences=[],
        total=2,
        coverage={"expression": 1, "pandas": 1},
        reference_row_counts={"Q1": 5},
        vacuous_cells=0,
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no resolved known-divergence entries; baseline unchanged." in out


def test_update_baseline_code_only_entry_cannot_be_pruned_and_fails(monkeypatch, capsys):
    gate = _make_gate(
        "fake",
        {
            "Q9_expression": ClassifiedDivergence(
                reason="code-only entry", accepts=lambda d: False, requires_live_divergence=True
            )
        },
    )
    monkeypatch.setattr(
        "benchbox.core.equivalence.known_divergences_baseline.update_baseline_file",
        lambda path, resolved, gate_name: [],
    )

    exit_code = _apply_baseline_update(
        gate,
        divergences=[],
        total=2,
        coverage={"expression": 1, "pandas": 1},
        reference_row_counts={"Q1": 5},
        vacuous_cells=0,
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "['Q9_expression'] are code-only (ClassifiedDivergence) and cannot be auto-pruned" in out
    assert "remove them manually from cross_surface.py in a reviewed change" in out


def test_update_baseline_prunes_yaml_entries_and_still_fails_on_remaining_code_only_entry(monkeypatch, capsys):
    gate = _make_gate(
        "fake",
        {
            "Q1_pandas": "documented test baseline",
            "Q9_expression": ClassifiedDivergence(
                reason="code-only entry", accepts=lambda d: False, requires_live_divergence=True
            ),
        },
    )
    monkeypatch.setattr(
        "benchbox.core.equivalence.known_divergences_baseline.update_baseline_file",
        lambda path, resolved, gate_name: sorted(set(resolved) & {"Q1_pandas"}),
    )

    exit_code = _apply_baseline_update(
        gate,
        divergences=[],
        total=4,
        coverage={"expression": 2, "pandas": 2},
        reference_row_counts={"Q1": 5, "Q9": 5},
        vacuous_cells=0,
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "removed resolved known-divergence baseline entries: ['Q1_pandas']" in out
    assert "['Q9_expression'] are code-only (ClassifiedDivergence) and cannot be auto-pruned" in out


def test_clickbench_and_joinorder_are_enforced_gates():
    from benchbox.core.equivalence.cross_surface import GATES, STAGED_GATES, get_gate

    assert "clickbench" in GATES
    assert "joinorder_synthetic" in GATES
    assert "clickbench" not in STAGED_GATES
    assert get_gate("clickbench").name == "clickbench"
    assert set(GATES["clickbench"].known_divergences) == {"Q18_expression", "Q18_pandas"}
    assert GATES["joinorder_synthetic"].known_divergences == {}
    assert "ssb" in GATES


def test_flightdata_is_promoted_to_enforced_gates():
    from benchbox.core.equivalence.cross_surface import GATES, STAGED_GATES, get_gate

    assert "flightdata" in GATES
    assert "flightdata" not in STAGED_GATES
    assert get_gate("flightdata").name == "flightdata"
    assert GATES["flightdata"].known_divergences == {}
    assert GATES["flightdata"].scale_factor == 0.01


def test_datavault_is_promoted_to_enforced_gates():
    from benchbox.core.equivalence.cross_surface import GATES, STAGED_GATES, get_gate

    assert "datavault" in GATES
    assert "datavault" not in STAGED_GATES
    assert get_gate("datavault").name == "datavault"
    assert GATES["datavault"].known_divergences == {}
    assert GATES["datavault"].scale_factor == 0.01


def test_datavault_sql_dataframe_id_mapping_is_mechanical_not_guessed():
    from benchbox.core.datavault.benchmark import DataVaultBenchmark
    from benchbox.core.datavault.dataframe_queries import DATAVAULT_DATAFRAME_QUERIES

    benchmark = DataVaultBenchmark(scale_factor=0.01)
    sql_ids = sorted((str(q) for q in benchmark.get_queries().keys()), key=int)
    df_ids = sorted(
        (str(q) for q in DATAVAULT_DATAFRAME_QUERIES.get_query_ids()),
        key=lambda q: int(q.lstrip("Q")),
    )
    assert sql_ids == [str(n) for n in range(1, 23)]
    assert df_ids == [f"Q{n}" for n in range(1, 23)]


def test_read_primitives_gate_opts_into_documented_nan_null_decode_tolerance():
    from benchbox.core.equivalence.cross_surface import GATES

    gate = GATES["read_primitives"]
    validator = gate.build_validator()

    assert gate.treat_nan_as_null is True
    assert validator.treat_nan_as_null is True
    assert validator.strip_strings is False
    assert GATES["ssb"].build_validator().treat_nan_as_null is False


def test_h2odb_is_an_enforced_gate_with_classified_percentile_exception():
    from benchbox.core.equivalence.cross_surface import GATES, STAGED_GATES, get_gate

    assert "h2odb" in GATES
    assert "h2odb" not in STAGED_GATES
    assert get_gate("h2odb").name == "h2odb"
    assert set(GATES["h2odb"].known_divergences) == {"Q9_expression", "Q9_pandas"}
    assert GATES["h2odb"].scale_factor == 0.01


def test_h2odb_q9_baseline_tolerates_only_the_sub_cent_residue_not_real_bugs():
    from benchbox.core.equivalence.cross_surface import GATES

    known = GATES["h2odb"].known_divergences
    coverage = {"expression": 1, "pandas": 1}

    residue = [
        SurfaceDivergence(
            "Q9", "expression", "Q9.0: Value mismatch at row 4, column 2. Original: 77.87, Variant: 77.874"
        ),
        SurfaceDivergence("Q9", "pandas", "Q9.0: Value mismatch at row 4, column 2. Original: 77.87, Variant: 77.874"),
    ]
    assert (
        _report(residue, total=2, coverage=coverage, known=known, benchmark="h2odb", reference_row_counts={"Q9": 6})
        == 0
    )

    real_bug = [
        SurfaceDivergence("Q9", "pandas", "Q9.0: Value mismatch at row 0, column 1. Original: 50.0, Variant: 60.0"),
    ]
    assert (
        _report(real_bug, total=2, coverage=coverage, known=known, benchmark="h2odb", reference_row_counts={"Q9": 6})
        == 1
    )

    wrong_column = [
        SurfaceDivergence("Q9", "pandas", "Q9.0: Value mismatch at row 4, column 1. Original: 77.87, Variant: 77.874"),
    ]
    assert (
        _report(
            wrong_column, total=2, coverage=coverage, known=known, benchmark="h2odb", reference_row_counts={"Q9": 6}
        )
        == 1
    )


def test_h2odb_q9_residue_predicate_rejects_structural_and_large_diffs():
    from benchbox.core.equivalence.cross_surface import _h2odb_q9_decimal_residue

    def d(detail: str) -> SurfaceDivergence:
        return SurfaceDivergence("Q9", "pandas", detail)

    assert _h2odb_q9_decimal_residue(d("Q9.0: Value mismatch at row 4, column 2. Original: 77.87, Variant: 77.874"))

    assert not _h2odb_q9_decimal_residue(d("Q9.0: Value mismatch at row 0, column 1. Original: 50.0, Variant: 60.0"))
    assert not _h2odb_q9_decimal_residue(d("Q9.0: Row count mismatch. Original: 6, Variant: 5"))
    assert not _h2odb_q9_decimal_residue(d("Q9.0: Column count mismatch at row 0. Original: 3, Variant: 2"))
    assert not _h2odb_q9_decimal_residue(d("error: boom"))

    assert not _h2odb_q9_decimal_residue(d("Q9.0: Value mismatch at row 4, column 1. Original: 77.87, Variant: 77.874"))
    assert not _h2odb_q9_decimal_residue(d("Q9.0: Value mismatch at row 4, column 0. Original: 4.00, Variant: 4.004"))

    assert not _h2odb_q9_decimal_residue(d("Q9.0: Value mismatch at row 4, column 2. Original: 77.87, Variant: 77.90"))

    assert not _h2odb_q9_decimal_residue(
        d("Q9.0: Value mismatch at row 4, column 2. Original: 77.874, Variant: 77.877")
    )

    assert not _h2odb_q9_decimal_residue(d("Q9.5: Value mismatch at row 5, column 1. Original: 77.91, Variant: 77.915"))


def test_order_by_result_key_maps_alias_name_expr_and_ordinal():
    from benchbox.core.equivalence.cross_surface import _order_by_result_key as resolve

    assert resolve("SELECT a, b FROM t ORDER BY a, b") == [0, 1]
    assert resolve("SELECT sum(x) AS revenue, y FROM t GROUP BY y ORDER BY revenue DESC") == [0]
    assert resolve("SELECT ol.d, dl.r FROM o ol JOIN d dl ON 1=1 ORDER BY ol.d, dl.r") == [0, 1]
    assert resolve("SELECT k, COUNT(*) AS c FROM t GROUP BY k ORDER BY COUNT(*) DESC") == [1]
    assert resolve("SELECT a, b FROM t ORDER BY 2 DESC") == [1]


def test_order_by_result_key_refuses_unmappable_keys():
    from benchbox.core.equivalence.cross_surface import _order_by_result_key as resolve

    assert resolve("SELECT a FROM t") is None
    assert resolve("SELECT * FROM t ORDER BY x") is None
    assert resolve("SELECT t.* FROM t ORDER BY x") is None
    assert resolve("SELECT a FROM t ORDER BY b") is None
    assert resolve("SELECT a, b FROM t ORDER BY 5") is None
    assert resolve("this is not sql ;;;") is None


def test_read_primitives_known_divergences_are_all_classified():
    from benchbox.core.equivalence.cross_surface import GATES

    known = GATES["read_primitives"].known_divergences
    assert len(known) == 17
    assert all(isinstance(entry, ClassifiedDivergence) for entry in known.values())
    assert set(known) == {
        "approx_count_distinct_simple_expression",
        "approx_count_distinct_simple_pandas",
        "approx_count_distinct_groupby_expression",
        "approx_count_distinct_groupby_pandas",
        "approx_quantile_groupby_expression",
        "approx_quantile_groupby_pandas",
        "statistical_percentiles_expression",
        "statistical_percentiles_pandas",
        "optimizer_common_subexpression_expression",
        "optimizer_common_subexpression_pandas",
        "min_by_complex_expression",
        "min_by_complex_pandas",
        "json_aggregates_expression",
        "json_aggregates_pandas",
        "map_access_expression",
        "map_construction_expression",
        "map_keys_values_expression",
    }
    assert GATES["read_primitives"].dtype_skip_keys == frozenset(
        {
            "approx_quantile_groupby_expression",
            "json_aggregates_expression",
            "map_access_expression",
            "map_construction_expression",
            "map_keys_values_expression",
        }
    )
    assert GATES["read_primitives"].dtype_skip_keys != frozenset(known)


def test_read_primitives_sketch_residue_predicate_accepts_bounded_rejects_wide():
    from benchbox.core.equivalence.cross_surface import _read_primitives_sketch_residue

    def d(query: str, col: int, orig: str, variant: str) -> SurfaceDivergence:
        return SurfaceDivergence(
            f"Q{query}",
            "expression",
            f"Q{query}.0: Value mismatch at row 0, column {col}. Original: {orig}, Variant: {variant}",
        )

    assert _read_primitives_sketch_residue(d("approx_count_distinct_simple", 0, "5210", "4969"))
    assert _read_primitives_sketch_residue(d("approx_count_distinct_groupby", 2, "36069", "32458"))
    assert _read_primitives_sketch_residue(d("approx_count_distinct_groupby", 3, "36069", "32458"))
    assert _read_primitives_sketch_residue(d("approx_quantile_groupby", 1, "26", "25.0"))

    assert _read_primitives_sketch_residue(d("approx_count_distinct_simple", 0, "1000", "850.001"))

    assert not _read_primitives_sketch_residue(d("approx_count_distinct_simple", 0, "5210", "521"))
    assert not _read_primitives_sketch_residue(d("approx_count_distinct_groupby", 2, "36069", "3600"))
    assert not _read_primitives_sketch_residue(d("approx_count_distinct_simple", 0, "1000", "849"))

    assert not _read_primitives_sketch_residue(d("approx_count_distinct_groupby", 0, "R", "N"))
    assert not _read_primitives_sketch_residue(d("approx_quantile_groupby", 0, "MAIL", "AIR"))

    assert not _read_primitives_sketch_residue(
        SurfaceDivergence("Qother", "expression", "Qother.0: Row count mismatch. Original: 6, Variant: 5")
    )
    assert not _read_primitives_sketch_residue(
        SurfaceDivergence("Qapprox_quantile_groupby", "expression", "error: boom")
    )


def test_read_primitives_percentile_decimal_residue_predicate():
    from benchbox.core.equivalence.cross_surface import _read_primitives_percentile_decimal_residue as predicate

    def d(col: int, orig: str, variant: str) -> SurfaceDivergence:
        return SurfaceDivergence(
            "Qstatistical_percentiles",
            "expression",
            f"Qstatistical_percentiles.0: Value mismatch at row 1, column {col}. Original: {orig}, Variant: {variant}",
        )

    assert predicate(d(6, "74356.28", "74356.28399999996"))

    assert not predicate(d(6, "74356.28", "74357.30"))
    assert not predicate(d(4, "25.00", "25.004"))
    assert not predicate(d(6, "74356.284", "74356.288"))
    assert not predicate(
        SurfaceDivergence(
            "Qstatistical_percentiles",
            "expression",
            "Qstatistical_percentiles.0: Row count mismatch. Original: 6, Variant: 5",
        )
    )
    assert not predicate(SurfaceDivergence("Qstatistical_percentiles", "expression", "error: boom"))


def test_read_primitives_round_decimal_residue_predicate():
    from benchbox.core.equivalence.cross_surface import _read_primitives_round_decimal_residue as predicate

    def d(col: int, orig: str, variant: str) -> SurfaceDivergence:
        return SurfaceDivergence(
            "Qoptimizer_common_subexpression",
            "expression",
            f"Qoptimizer_common_subexpression.0: Value mismatch at row 0, column {col}. Original: {orig}, Variant: {variant}",
        )

    assert predicate(d(6, "4721058.05", "4721058.04"))

    assert not predicate(d(6, "4721058.05", "4721057.90"))
    assert not predicate(d(0, "100", "99"))
    assert not predicate(d(6, "4721058.051", "4721058.041"))
    assert not predicate(
        SurfaceDivergence(
            "Qoptimizer_common_subexpression",
            "expression",
            "Qoptimizer_common_subexpression.0: Column count mismatch at row 0. Original: 7, Variant: 6",
        )
    )
    assert not predicate(SurfaceDivergence("Qoptimizer_common_subexpression", "expression", "error: boom"))


def test_read_primitives_argmin_tie_predicate():
    from benchbox.core.equivalence.cross_surface import _read_primitives_argmin_tie as predicate

    def d(col: int, orig: str, variant: str) -> SurfaceDivergence:
        return SurfaceDivergence(
            "Qmin_by_complex",
            "expression",
            f"Qmin_by_complex.0: Value mismatch at row 0, column {col}. Original: {orig}, Variant: {variant}",
        )

    assert predicate(d(1, "seashell orange firebrick peach rose", "gainsboro snow indian frosted navy"))
    assert predicate(d(2, "SMALL BRUSHED STEEL", "LARGE POLISHED TIN"))

    assert not predicate(d(0, "Brand#11", "Brand#25"))
    assert not predicate(d(3, "901.00", "850.00"))
    assert not predicate(
        SurfaceDivergence(
            "Qmin_by_complex", "expression", "Qmin_by_complex.0: Row count mismatch. Original: 25, Variant: 24"
        )
    )
    assert not predicate(SurfaceDivergence("Qmin_by_complex", "expression", "error: boom"))


def test_read_primitives_json_agg_predicate_accepts_structural_match_only():
    from benchbox.core.equivalence.cross_surface import _read_primitives_json_agg_accepts as predicate

    def order_mismatch(orig_key: str, variant_key: str) -> SurfaceDivergence:
        return SurfaceDivergence(
            "Qjson_aggregates",
            "expression",
            "Qjson_aggregates.0: ORDER BY key mismatch at position 0. "
            f"Original key: ('{orig_key}',), Variant key: ('{variant_key}',) "
            "(order-key columns [0]) - the returned order differs (e.g. a reversed ORDER BY).",
        )

    def value_mismatch(col: int, orig: str, variant: str) -> SurfaceDivergence:
        return SurfaceDivergence(
            "Qjson_aggregates",
            "pandas",
            f"Qjson_aggregates.0: Value mismatch at row 0, column {col}. Original: {orig}, Variant: {variant}",
        )

    assert predicate(order_mismatch("Brand#11", "Brand#51"))

    assert not predicate(order_mismatch("Brand#11", "NotABrand"))
    assert not predicate(order_mismatch("NotABrand", "Brand#11"))

    assert predicate(value_mismatch(1, '["a","b","c"]', "['a', 'b', 'c']"))
    assert predicate(value_mismatch(1, '["a","b","c"]', "['c', 'b', 'a']"))

    assert predicate(value_mismatch(2, '{"1": 901.00, "2": 902.50}', "{'1': 901.0, '2': 902.5}"))

    assert not predicate(value_mismatch(1, '["a","b","c"]', "['a', 'b']"))
    assert not predicate(value_mismatch(1, '["a","b","c"]', "['a', 'b', 'ZZZ']"))
    assert not predicate(value_mismatch(2, '{"1": 901.00}', "{'1': 850.0}"))
    assert not predicate(value_mismatch(0, "Brand#11", "Brand#12"))
    assert not predicate(value_mismatch(3, "5", "4"))
    assert not predicate(value_mismatch(1, "not json", "also not json"))
    assert not predicate(SurfaceDivergence("Qjson_aggregates", "expression", "error: boom"))


def test_read_primitives_polars_map_gap_predicate():
    from benchbox.core.equivalence.cross_surface import _read_primitives_polars_map_gap as predicate

    assert predicate(
        SurfaceDivergence(
            "Qmap_access", "expression", "error: map_from_entries not supported on Polars (no native Map dtype)"
        )
    )
    assert predicate(
        SurfaceDivergence(
            "Qmap_keys_values", "expression", "error: Map operations not supported on Polars (no native Map dtype)"
        )
    )

    assert not predicate(SurfaceDivergence("Qmap_access", "expression", "error: unexpected KeyError: 'foo'"))
    assert not predicate(
        SurfaceDivergence(
            "Qmap_access", "expression", "Qmap_access.0: Value mismatch at row 0, column 1. Original: 1.0, Variant: 2.0"
        )
    )


def _read_primitives_live_baseline_divergences() -> list[SurfaceDivergence]:
    sketch_detail = {
        "approx_count_distinct_simple": (0, "5210", "4969"),
        "approx_count_distinct_groupby": (2, "36069", "32458"),
        "approx_quantile_groupby": (1, "26", "25.0"),
    }
    divergences = []
    for base, (col, orig, variant) in sketch_detail.items():
        for cell in ("expression", "pandas"):
            divergences.append(
                SurfaceDivergence(
                    base,
                    cell,
                    f"Q{base}.0: Value mismatch at row 0, column {col}. Original: {orig}, Variant: {variant}",
                )
            )
    for cell in ("expression", "pandas"):
        divergences.append(
            SurfaceDivergence(
                "statistical_percentiles",
                cell,
                "Qstatistical_percentiles.0: Value mismatch at row 1, column 6. "
                "Original: 74356.28, Variant: 74356.28399999996",
            )
        )
        divergences.append(
            SurfaceDivergence(
                "optimizer_common_subexpression",
                cell,
                "Qoptimizer_common_subexpression.0: Value mismatch at row 0, column 6. "
                "Original: 4721058.05, Variant: 4721058.04",
            )
        )
        divergences.append(
            SurfaceDivergence(
                "min_by_complex",
                cell,
                "Qmin_by_complex.0: Value mismatch at row 0, column 1. "
                "Original: seashell orange firebrick peach rose, Variant: gainsboro snow indian frosted navy",
            )
        )
    divergences.append(
        SurfaceDivergence(
            "json_aggregates",
            "expression",
            "Qjson_aggregates.0: ORDER BY key mismatch at position 0. Original key: ('Brand#11',), "
            "Variant key: ('Brand#51',) (order-key columns [0]) - the returned order differs "
            "(e.g. a reversed ORDER BY).",
        )
    )
    divergences.append(
        SurfaceDivergence(
            "json_aggregates",
            "pandas",
            "Qjson_aggregates.0: Value mismatch at row 0, column 1. Original: [\"a\",\"b\"], Variant: ['a', 'b']",
        )
    )
    for base in ("map_access", "map_construction", "map_keys_values"):
        divergences.append(
            SurfaceDivergence(
                base, "expression", "error: map_from_entries not supported on Polars (no native Map dtype)"
            )
        )
    return divergences


def test_read_primitives_baseline_report_accepts_documented_cells_rejects_regressions(capsys):
    from benchbox.core.equivalence.cross_surface import GATES

    known = GATES["read_primitives"].known_divergences
    documented = _read_primitives_live_baseline_divergences()
    assert {f"{d.query_id}_{d.cell}" for d in documented} == set(known), (
        "test fixture must cover every converted read_primitives waiver key exactly once"
    )
    coverage = {"expression": len(documented), "pandas": len(documented)}
    assert (
        _report(
            documented,
            total=len(documented),
            coverage=coverage,
            known=known,
            benchmark="read_primitives",
            reference_row_counts={},
        )
        == 0
    )
    capsys.readouterr()

    regression = [d for d in documented if f"{d.query_id}_{d.cell}" != "approx_count_distinct_simple_expression"]
    regression.append(
        SurfaceDivergence(
            "approx_count_distinct_simple",
            "expression",
            "Qapprox_count_distinct_simple.0: Value mismatch at row 0, column 0. Original: 5210, Variant: 100",
        )
    )
    assert (
        _report(
            regression,
            total=len(regression),
            coverage=coverage,
            known=known,
            benchmark="read_primitives",
            reference_row_counts={},
        )
        == 1
    )


def _sd(query_id: str, detail: str) -> SurfaceDivergence:
    return SurfaceDivergence(query_id, "pandas", detail)


def test_validator_reports_all_mismatched_columns_in_detail():
    validator = ResultValidator()
    detail = validator._first_positional_mismatch(
        [("brandX", "nameA", "typeA", 10.0)],
        [("brandX", "nameB", "typeA", 99.0)],
        query_id=1,
        variant_id=1,
    )
    assert detail is not None
    assert "column 1" in detail
    assert "; also columns [3]" in detail
    inline = detail.split("; also columns")[0]
    assert "nameA" in inline and "nameB" in inline
    assert "99.0" not in detail
    assert detail.endswith("[3]")


def test_mismatched_columns_helper_parses_suffix():
    from benchbox.core.equivalence.cross_surface import _VALUE_MISMATCH_RE, _mismatched_columns

    single = _VALUE_MISMATCH_RE.search("Q1.1: Value mismatch at row 0, column 2. Original: 1.00, Variant: 1.003")
    assert _mismatched_columns(single) == {2}
    multi = _VALUE_MISMATCH_RE.search(
        "Q1.1: Value mismatch at row 0, column 1. Original: a, Variant: b; also columns [3, 5]"
    )
    assert _mismatched_columns(multi) == {1, 3, 5}


def test_argmin_tie_accepts_single_waived_column_but_not_a_second_wrong_column():
    from benchbox.core.equivalence.cross_surface import _read_primitives_argmin_tie

    accepted = _sd(
        "min_by_complex_pandas",
        "Q1.1: Value mismatch at row 0, column 1. Original: nameA, Variant: nameB",
    )
    assert _read_primitives_argmin_tie(accepted) is True

    both_wrong = _sd(
        "min_by_complex_pandas",
        "Q1.1: Value mismatch at row 0, column 1. Original: nameA, Variant: nameB; also columns [3]",
    )
    assert _read_primitives_argmin_tie(both_wrong) is False


def test_h2odb_q9_residue_requires_p90_to_be_the_only_mismatched_column():
    from benchbox.core.equivalence.cross_surface import _h2odb_q9_decimal_residue

    accepted = _sd("Q9_pandas", "Q9.1: Value mismatch at row 0, column 2. Original: 1.00, Variant: 1.003")
    assert _h2odb_q9_decimal_residue(accepted) is True

    also_zero = _sd(
        "Q9_pandas", "Q9.1: Value mismatch at row 0, column 2. Original: 1.00, Variant: 1.003; also columns [0]"
    )
    assert _h2odb_q9_decimal_residue(also_zero) is False
    zero_first = _sd(
        "Q9_pandas", "Q9.1: Value mismatch at row 0, column 0. Original: gA, Variant: gB; also columns [2]"
    )
    assert _h2odb_q9_decimal_residue(zero_first) is False


_HIERARCHY_SQL = (
    "SELECT total, category, level, rnk FROM t "
    "ORDER BY level DESC, CASE WHEN level = 0 THEN category END, rnk LIMIT 100"
)
_HIERARCHY_COLUMNS = [("total", "DECIMAL(17,2)"), ("category", "VARCHAR"), ("level", "INTEGER"), ("rnk", "BIGINT")]


def test_derived_order_accepts_rows_in_case_key_order():
    rows = [(90.0, None, 2, 1), (60.0, "Music", 1, 1), (30.0, "Books", 1, 2), (20.0, "Books", 0, 1), (5.0, None, 0, 1)]
    assert _derived_order_violation(_HIERARCHY_SQL, _HIERARCHY_COLUMNS, rows) is None


def test_derived_order_rejects_subtotals_sorted_by_category():
    rows = [(90.0, None, 2, 1), (30.0, "Books", 1, 2), (60.0, "Music", 1, 1), (20.0, "Books", 0, 1)]
    violation = _derived_order_violation(_HIERARCHY_SQL, _HIERARCHY_COLUMNS, rows)
    assert violation is not None
    assert violation.startswith("returned row 1 breaks the ORDER BY")


def test_derived_order_allows_any_order_within_ties():
    sql = "SELECT a, b FROM t ORDER BY a - b"
    columns = [("a", "DOUBLE"), ("b", "DOUBLE")]
    assert _derived_order_violation(sql, columns, [(3.0, 2.0), (2.0, 1.0), (5.0, 1.0)]) is None
    assert _derived_order_violation(sql, columns, [(5.0, 1.0), (3.0, 2.0)]) is not None


def test_derived_order_is_not_checked_when_a_key_is_not_in_the_result():
    sql = "SELECT a FROM t ORDER BY hidden"
    assert _derived_order_violation(sql, [("a", "INTEGER")], [(2,), (1,)]) is None
    assert _plan_kind(sql, [("a", "INTEGER")]) == ORDER_UNVERIFIABLE
    qualified = "SELECT b AS a FROM t ORDER BY t.a + 0"
    assert _derived_order_violation(qualified, [("a", "INTEGER")], [(2,), (1,)]) is None
    assert _plan_kind(qualified, [("a", "INTEGER")]) == ORDER_UNVERIFIABLE


def test_derived_order_fails_closed_when_the_sort_keys_cannot_be_evaluated():
    sql = "SELECT a FROM t ORDER BY a / 2"
    violation = _derived_order_violation(sql, [("a", "VARCHAR")], [("x",), ("y",)])
    assert violation is not None
    assert "could not evaluate the sort keys" in violation


def test_report_lists_vacuity_without_failing_for_an_unclassified_draw(capsys):
    arguments = {
        "total": 4,
        "coverage": {"expression": 2, "pandas": 2},
        "known": {},
        "benchmark": "fake",
        "reference_row_counts": {"Q1": 0, "Q2": 4},
        "legitimately_empty": {"Q2": "empty on the default draw"},
        "enforce_vacuity": False,
    }
    assert _report([], **arguments) == 0
    out = capsys.readouterr().out
    assert "lists it without failing: ['Q1']" in out
    assert "GATE FAILURE" not in out
    assert _report([SurfaceDivergence("Q2", "pandas", "value mismatch")], **arguments) == 1


def _plan_kind(sql, columns):
    return _derived_order_plan(sql, lambda: columns).status.kind


def test_order_by_all_is_checked_over_every_output_column():
    columns = [("a", "INTEGER"), ("b", "INTEGER")]
    ascending = "SELECT a, b FROM t ORDER BY ALL"
    assert _plan_kind(ascending, columns) == ORDER_VERIFIED
    assert _derived_order_violation(ascending, columns, [(1, 2), (1, 3), (3, 4)]) is None
    assert _derived_order_violation(ascending, columns, [(1, 3), (1, 2), (3, 4)]) is not None
    descending = "SELECT a, b FROM t ORDER BY ALL DESC"
    assert _derived_order_violation(descending, columns, [(3, 4), (1, 3), (1, 2)]) is None
    assert _derived_order_violation(descending, columns, [(1, 2), (3, 4)]) is not None


def test_order_by_all_honors_null_placement():
    columns = [("a", "INTEGER")]
    sql = "SELECT a FROM t ORDER BY ALL NULLS FIRST"
    assert _derived_order_violation(sql, columns, [(None,), (1,), (2,)]) is None
    assert _derived_order_violation(sql, columns, [(1,), (2,), (None,)]) is not None


def test_qualified_sort_columns_resolve_to_the_matching_output_column():
    columns = [("a", "INTEGER"), ("b", "INTEGER")]
    sql = "SELECT t.a, t.b FROM t ORDER BY t.b DESC"
    assert _plan_kind(sql, columns) == ORDER_VERIFIED
    assert _derived_order_violation(sql, columns, [(2, 9), (1, 5)]) is None
    assert _derived_order_violation(sql, columns, [(1, 5), (2, 9)]) is not None
    assert _derived_order_violation("SELECT a, b FROM t ORDER BY t.b", columns, [(2, 9), (1, 5)]) is not None


def test_qualified_sort_column_uses_the_qualifier_to_pick_among_same_named_outputs():
    columns = [("a", "INTEGER"), ("a", "INTEGER")]
    sql = "SELECT x.a, y.a FROM x JOIN y ON x.k = y.k ORDER BY y.a"
    assert _plan_kind(sql, columns) == ORDER_VERIFIED
    assert _derived_order_violation(sql, columns, [(9, 1), (1, 2)]) is None
    assert _derived_order_violation(sql, columns, [(1, 2), (9, 1)]) is not None


def test_qualified_sort_column_that_is_not_an_output_stays_unverifiable():
    columns = [("a", "INTEGER")]
    assert _plan_kind("SELECT y AS a FROM t ORDER BY t.a", columns) == ORDER_UNVERIFIABLE
    assert _plan_kind("SELECT u.a FROM t JOIN u ON t.k = u.k ORDER BY t.a", columns) == ORDER_UNVERIFIABLE
    assert _plan_kind("SELECT * FROM t ORDER BY t.a", columns) == ORDER_UNVERIFIABLE


def test_qualified_sort_column_needs_the_lone_source_to_match():
    columns = [("b", "INTEGER")]
    assert _plan_kind("SELECT b FROM t ORDER BY t.b", columns) == ORDER_VERIFIED
    assert _plan_kind("SELECT b FROM t AS x ORDER BY x.b", columns) == ORDER_VERIFIED
    assert _plan_kind("SELECT b FROM t ORDER BY u.b", columns) == ORDER_UNVERIFIABLE
    assert _plan_kind("SELECT b FROM t, u ORDER BY t.b", columns) == ORDER_UNVERIFIABLE


def test_join_using_qualified_order_by_stays_unverifiable():
    columns = [("a", "INTEGER")]
    sql = "SELECT a FROM t LEFT JOIN u USING(a) ORDER BY u.a + 0"
    assert _plan_kind(sql, columns) == ORDER_UNVERIFIABLE
    assert _derived_order_violation(sql, columns, [(2,), (1,)]) is None


def test_collated_output_columns_stay_unverifiable():
    assert _plan_kind("SELECT a COLLATE NOCASE AS x FROM t ORDER BY ALL", [("x", "VARCHAR")]) == ORDER_UNVERIFIABLE
    assert _plan_kind("SELECT a COLLATE NOCASE AS x FROM t ORDER BY x", [("x", "VARCHAR")]) == ORDER_UNVERIFIABLE
    assert _plan_kind("SELECT a FROM t ORDER BY ALL", [("a", "VARCHAR")]) == ORDER_UNVERIFIABLE
    assert _plan_kind("SELECT a FROM t ORDER BY ALL", [("a", "INTEGER")]) == ORDER_VERIFIED


def test_duplicate_output_names_are_checked_by_position_and_refused_by_name():
    columns = [("a", "INTEGER"), ("a", "INTEGER")]
    by_position = "SELECT x.a, y.a FROM x, y ORDER BY 2"
    assert _plan_kind(by_position, columns) == ORDER_VERIFIED
    assert _derived_order_violation(by_position, columns, [(5, 1), (4, 2)]) is None
    assert _derived_order_violation(by_position, columns, [(5, 2), (4, 1)]) is not None
    assert _plan_kind("SELECT x.a, y.a FROM x, y ORDER BY a", columns) == ORDER_UNVERIFIABLE


def test_decimal_sort_keys_keep_exact_precision():
    columns = [("amount", "DECIMAL(38,2)")]
    sql = "SELECT amount FROM t ORDER BY amount"
    low, high = Decimal("123456789012345678.01"), Decimal("123456789012345678.02")
    assert float(low) == float(high)
    assert _derived_order_violation(sql, columns, [(low,), (high,)]) is None
    assert _derived_order_violation(sql, columns, [(high,), (low,)]) is not None
    expression = "SELECT amount FROM t ORDER BY amount + 0"
    assert _derived_order_violation(expression, columns, [(high,), (low,)]) is not None


def test_a_mis_ordered_derived_key_fails_the_gate_cell():
    sql = "SELECT a, b FROM t ORDER BY a - b"
    reference = [(5, 1), (3, 2)]
    connection = _FakeConnection(
        {
            sql: reference,
            f"DESCRIBE {sql}": [("a", "INTEGER", "YES", None, None, None), ("b", "BIGINT", "YES", None, None, None)],
        }
    )
    statuses = {}

    def divergences_for(candidate):
        return find_cross_surface_divergences(
            connection,
            query_ids=["Q1"],
            reference_sql=lambda _qid: sql,
            dataframe_query=lambda _qid: _FakeQuery({"expression": lambda ctx: _FakeFrame(candidate)}),
            contexts={"expression": object()},
            validator=ResultValidator(),
            backends=("expression",),
            order_statuses=statuses,
        )

    assert divergences_for([(3, 2), (5, 1)]) == []
    assert statuses[("Q1", "expression")].kind == ORDER_VERIFIED
    failing = divergences_for([(5, 1), (3, 2)])
    assert [d.key for d in failing] == ["Q1_expression"]
    assert "breaks the ORDER BY" in failing[0].detail


def test_cells_are_recorded_as_verified_unordered_or_unverifiable():
    def statuses_for(sql, describe):
        rows = [(1, 2)]
        connection = _FakeConnection({sql: rows, f"DESCRIBE {sql}": describe})
        statuses = {}
        find_cross_surface_divergences(
            connection,
            query_ids=["Q1"],
            reference_sql=lambda _qid: sql,
            dataframe_query=lambda _qid: _FakeQuery({"expression": lambda ctx: _FakeFrame(rows)}),
            contexts={"expression": object()},
            validator=ResultValidator(),
            backends=("expression",),
            order_statuses=statuses,
        )
        return statuses[("Q1", "expression")]

    describe = [("a", "INTEGER", "YES", None, None, None), ("b", "INTEGER", "YES", None, None, None)]
    assert statuses_for("SELECT a, b FROM t ORDER BY a", describe).kind == ORDER_VERIFIED
    assert statuses_for("SELECT a, b FROM t", describe).kind == ORDER_UNORDERED
    assert statuses_for("SELECT a, b FROM t ORDER BY hidden", describe).kind == ORDER_UNVERIFIABLE


def test_order_report_counts_verified_and_unordered_cells():
    from benchbox.core.equivalence.cross_surface import OrderStatus

    statuses = {
        ("Q1", "pandas"): OrderStatus(ORDER_VERIFIED),
        ("Q1", "expression"): OrderStatus(ORDER_VERIFIED),
        ("Q2", "pandas"): OrderStatus(ORDER_UNORDERED, "no ORDER BY"),
        ("Q3", "pandas"): OrderStatus(ORDER_UNVERIFIABLE, "ORDER BY column hidden is not a unique output column"),
    }
    lines = _order_report_lines(statuses)
    assert lines[0] == (
        "  returned order: 2 of 4 cells verified, 2 compared unordered (1 without ORDER BY, 1 with an ORDER BY "
        "that cannot be checked)"
    )
    assert lines[1:] == ["    unverifiable ORDER BY, query Q3: ORDER BY column hidden is not a unique output column"]
    assert _order_report_lines({}) == []


def test_report_prints_the_order_summary(capsys):
    from benchbox.core.equivalence.cross_surface import OrderStatus

    assert (
        _report(
            [],
            1,
            {"expression": 1},
            {},
            benchmark="fake",
            order_statuses={("Q1", "expression"): OrderStatus(ORDER_VERIFIED)},
        )
        == 0
    )
    assert "returned order: 1 of 1 cells verified, 0 compared unordered" in capsys.readouterr().out
