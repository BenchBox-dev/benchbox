from __future__ import annotations

import dataclasses
import json
from hashlib import sha256
from types import SimpleNamespace

import duckdb
import pytest

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox.core.equivalence import cross_surface
from benchbox.core.equivalence.builders import base, tpcds
from benchbox.core.equivalence.dataframe_surface import SurfaceDivergence
from benchbox.core.results.canonical_json import canonical_json_text
from benchbox.core.tpcds.dataframe_queries import TPCDS_DATAFRAME_QUERIES
from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS
from benchbox.core.tpcds.dataframe_queries.production_binding import (
    clear_binding_cache,
    power_parameter_seed,
    query_number,
)
from benchbox.tpcds import TPCDS

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


@pytest.fixture(scope="module", params=[(None, 0), (42, 1)], ids=["default", "seed42-stream1"])
def draw(request, tmp_path_factory):
    directory = tmp_path_factory.mktemp("shared-draw")
    seed, stream = request.param
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("BENCHBOX_CACHE_DIR", str(directory / "cache"))
        patch.setattr(TPCDS, "generate_data", lambda self: [])
        patch.setattr(base, "_load_duckdb_cell", lambda *a, **k: object())
        clear_binding_cache()
        data = tpcds.build_tpcds_duckdb(0.01, directory, seed=seed, stream_id=stream)
    yield data
    clear_binding_cache()


def test_all_103_statements_match_real_dsqgen_power_sql_and_share_captured_bindings(draw):
    assert set(draw.query_ids) == {str(number) for number in range(1, 100)} | {"14b", "23b", "24b", "39b"}
    assert len(draw.query_ids) == 103
    identity = draw.query_parameters
    assert identity["rngseed"] == power_parameter_seed(identity["seed"], identity["power_stream_id"])
    assert identity["dsqgen_stream_id"] == 0
    assert (
        identity["dsqgen_sha256"]
        == sha256(draw.benchmark._impl.query_manager.dsqgen.dsqgen_path.read_bytes()).hexdigest()
    )
    assert set(identity["bindings"]) == {str(number) for number in range(1, 100)}
    assert set(identity["sql_sha256"]) == set(draw.query_ids)
    for query_id in draw.query_ids:
        number = query_number(query_id)
        variant = "b" if query_id.endswith("b") else "a" if number in {14, 23, 24, 39} else None
        sql = draw.reference_sql(query_id)
        expected = draw.benchmark.get_query(number, dialect="duckdb", seed=identity["rngseed"], variant=variant)
        assert sql == expected, query_id
        parsed = sqlglot.parse(sql, read="duckdb")
        assert len(parsed) == 1 and isinstance(parsed[0], exp.Query), query_id
        binding = draw.dataframe_query.bindings[number]
        assert binding.parameters == ADAPTERS[number](dict(binding.logged)), query_id
        assert dataclasses.asdict(binding) == identity["bindings"][str(number)], query_id
        query = draw.dataframe_query(query_id)
        for family in ("expression", "pandas"):
            assert query.get_impl_for_family(family).parameter_binding is binding, (query_id, family)
    for number in (14, 23, 24, 39):
        assert draw.reference_sql(str(number)) != draw.reference_sql(f"{number}b")


@pytest.mark.parametrize(
    "defect",
    [
        "statement",
        "duplicate",
        "binding",
        "record",
        "sql-hash",
        "sql-text",
        "draw",
        "scale",
        "identity",
        "implementation",
        "query-id",
        "record-value",
        "unbound",
    ],
)
def test_incomplete_or_inconsistent_draw_is_rejected(draw, defect):
    data = dataclasses.replace(draw, query_parameters={**draw.query_parameters})
    metadata = data.query_parameters
    queries = {qid: draw.dataframe_query(qid) for qid in draw.query_ids}
    bindings = dict(draw.dataframe_query.bindings)

    def query(qid):
        return queries[qid]

    query.bindings = bindings
    data = dataclasses.replace(data, dataframe_query=query)
    if defect == "statement":
        data = dataclasses.replace(data, query_ids=draw.query_ids[:-1])
    elif defect == "duplicate":
        data = dataclasses.replace(data, query_ids=(*draw.query_ids[:-1], "1"))
    elif defect == "binding":
        del bindings[99]
    elif defect == "record":
        metadata["bindings"] = {k: v for k, v in metadata["bindings"].items() if k != "99"}
    elif defect == "sql-hash":
        metadata["sql_sha256"] = {k: v for k, v in metadata["sql_sha256"].items() if k != "39b"}
    elif defect == "sql-text":
        data = dataclasses.replace(
            data, reference_sql=lambda qid: "SELECT 999" if qid == "39b" else draw.reference_sql(qid)
        )
    elif defect == "draw":
        metadata["rngseed"] += 1
    elif defect == "scale":
        metadata["scale_factor"] = 1.0
    elif defect == "identity":
        del metadata["seed"]
    elif defect == "implementation":
        queries["39b"] = dataclasses.replace(queries["39b"], pandas_impl=None)
    elif defect == "query-id":
        queries["39b"] = queries["39"]
    elif defect == "record-value":
        metadata["bindings"] = {**metadata["bindings"], "99": {**metadata["bindings"]["99"], "seed": 7}}
    elif defect == "unbound":
        queries["39b"] = TPCDS_DATAFRAME_QUERIES.get_or_raise("Q39b")
    with pytest.raises(ValueError, match="TPC-DS"):
        tpcds.validate_tpcds_gate_data(data)


@pytest.mark.parametrize("defect", ["adapter", "registry", "binding", "implementation"])
def test_builder_refuses_incomplete_coverage_before_data_generation(draw, monkeypatch, tmp_path, defect):
    from benchbox.core.tpcds.dataframe_queries import production_binding

    def unexpected(*args, **kwargs):
        pytest.fail("Incomplete coverage reached data generation or loading")

    monkeypatch.setattr(TPCDS, "generate_data", unexpected)
    monkeypatch.setattr(base, "_load_duckdb_cell", unexpected)
    if defect == "adapter":
        monkeypatch.delitem(ADAPTERS, 99)
    elif defect == "registry":
        monkeypatch.setattr(
            TPCDS_DATAFRAME_QUERIES, "get_query_ids", lambda: [f"Q{qid}" for qid in draw.query_ids[:-1]]
        )
    elif defect == "binding":
        monkeypatch.setattr(
            production_binding,
            "bind_power_stream",
            lambda *a, **k: {n: b for n, b in draw.dataframe_query.bindings.items() if n != 99},
        )
    elif defect == "implementation":
        original = TPCDS_DATAFRAME_QUERIES.get_or_raise
        monkeypatch.setattr(
            TPCDS_DATAFRAME_QUERIES,
            "get_or_raise",
            lambda qid: dataclasses.replace(original(qid), pandas_impl=None) if qid == "Q39b" else original(qid),
        )
    with pytest.raises(ValueError, match="TPC-DS"):
        tpcds.build_tpcds_duckdb(
            0.01, tmp_path, seed=draw.query_parameters["seed"], stream_id=draw.query_parameters["power_stream_id"]
        )


def test_staged_preconditions_fail_before_production_loading(draw, monkeypatch):
    data = dataclasses.replace(draw, connection=duckdb.connect(), query_ids=draw.query_ids[:-1])
    gate = dataclasses.replace(cross_surface.get_gate("tpcds"), build=lambda *a: data)
    monkeypatch.setattr(
        cross_surface, "build_production_contexts", lambda *a, **k: pytest.fail("Loaded an incomplete draw")
    )
    with pytest.raises(ValueError, match="four b statements"):
        cross_surface.run_gate(gate)


def test_enforced_divergence_fails_and_full_draw_is_emitted(draw, monkeypatch, capsys):
    data = dataclasses.replace(draw, connection=duckdb.connect())
    gate = dataclasses.replace(cross_surface.get_gate("tpcds"), build=lambda *a: data, backends=("datafusion",))
    monkeypatch.setattr(cross_surface, "build_production_contexts", lambda *a, **k: {"datafusion": None})
    monkeypatch.setattr(
        cross_surface,
        "find_cross_surface_divergences",
        lambda *a, **k: [SurfaceDivergence(query_id="39b", cell="datafusion", detail="Value mismatch")],
    )
    assert cross_surface.run_gate(gate, repeats=3) == 1
    output = capsys.readouterr().out
    assert "STAGED REPORT ONLY" not in output
    assert "GATE FAILURE - unclassified cross-surface divergences: ['39b_datafusion']" in output
    emitted, _ = json.JSONDecoder().raw_decode(output[output.index("{") :])
    expected = canonical_json_text(draw.query_parameters)
    assert output.startswith(expected)
    assert emitted == json.loads(expected)


def test_cli_passes_explicit_draw_backends_and_repeats(monkeypatch):
    calls = []
    monkeypatch.setattr(cross_surface, "run_gate", lambda gate, **kw: calls.append((gate, kw)) or 0)
    assert (
        cross_surface.main(
            [
                "--benchmark",
                "tpcds",
                "--backend",
                "expression",
                "--backend",
                "pandas",
                "--backend",
                "datafusion",
                "--seed",
                "42",
                "--power-stream",
                "1",
                "--repeats",
                "3",
            ]
        )
        == 0
    )
    gate, options = calls[0]
    assert gate.backends == ("expression", "pandas", "datafusion")
    assert gate.build.func is tpcds.build_tpcds_duckdb
    assert gate.build.keywords == {"seed": 42, "stream_id": 1}
    assert options == {"update_baseline": False, "repeats": 3}
    assert "tpcds" in cross_surface.GATES


def test_other_benchmark_defaults_are_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(cross_surface, "run_gate", lambda gate, **kw: calls.append(gate) or 0)
    assert cross_surface.main([]) == 0
    assert calls == [cross_surface.get_gate("ssb")]
    assert calls[0] is cross_surface.get_gate("ssb")


@pytest.mark.parametrize(
    "options",
    [
        ["--benchmark", "ssb", "--seed", "42"],
        ["--benchmark", "ssb", "--power-stream", "0"],
        ["--benchmark", "tpcds", "--seed", "-1"],
        ["--benchmark", "tpcds", "--power-stream", "-1"],
        ["--backend", "pandas", "--backend", "pandas"],
        ["--backend", "missing"],
        ["--benchmark", "tpcds", "--stream", "1"],
        ["--update-baseline", "--backend", "expression"],
        ["--benchmark", "tpcds", "--update-baseline", "--seed", "42"],
        ["--benchmark", "tpcds", "--update-baseline", "--power-stream", "0"],
        ["--update-baseline", "--execution-engine", "streaming"],
    ],
)
def test_unsupported_cli_options_are_rejected_before_execution(monkeypatch, options):
    monkeypatch.setattr(cross_surface, "run_gate", lambda *a, **k: pytest.fail("Executed unsupported options"))
    with pytest.raises(SystemExit) as error:
        cross_surface.main(options)
    assert error.value.code == 2


def test_default_baseline_maintenance_still_reaches_its_existing_refusal_rules(monkeypatch):
    calls = []
    monkeypatch.setattr(cross_surface, "run_gate", lambda gate, **kw: calls.append((gate, kw)) or 1)
    assert cross_surface.main(["--benchmark", "ssb", "--update-baseline"]) == 1
    gate, options = calls[0]
    assert gate is cross_surface.get_gate("ssb")
    assert options == {"update_baseline": True, "repeats": 1}
