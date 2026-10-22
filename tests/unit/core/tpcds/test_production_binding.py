# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) DS (TPC-DS) - Copyright (c) Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from benchbox.core.dataframe.query_resolution import get_dataframe_queries_for_benchmark, get_tpcds_dataframe_queries
from benchbox.core.tpcds.dataframe_queries import TPCDS_DATAFRAME_QUERIES
from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS, adapter_query_ids
from benchbox.core.tpcds.dataframe_queries.parameters import get_parameters, parameter_overrides
from benchbox.core.tpcds.dataframe_queries.production_binding import (
    bind_queries,
    clear_binding_cache,
    power_parameter_seed,
    query_binding,
    query_number,
)
from benchbox.core.tpcds.power_test import TPCDSPowerTest

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

SCALE = 0.01
RUNS = [(None, 0), (None, 1), (42, 0), (42, 1)]
EVERY_ADAPTED_QUERY_RUNS = [(42, 0)]
UNADAPTED = min(set(range(1, 100)) - set(ADAPTERS), default=None)
needs_unadapted = pytest.mark.skipif(UNADAPTED is None, reason="every query has an adapter")


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_binding_cache()
    yield
    clear_binding_cache()


def _config(seed=None, queries=None, scale_factor=SCALE):
    options = {} if seed is None else {"seed": seed}
    return SimpleNamespace(name="tpcds", scale_factor=scale_factor, options=options, queries=queries)


class _Cursor:
    def fetchall(self):
        return []


class _RecordingConnection:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql):
        self.sql.append(sql)
        return _Cursor()

    def close(self):
        pass


class _StubDSQGen:
    def __init__(self, path, fail=False):
        self.dsqgen_path = path
        self.calls = []
        self.fail = fail

    def generate_parameter_log(self, query_id, **kwargs):
        self.calls.append((query_id, kwargs))
        if self.fail:
            from benchbox.core.tpcds.c_tools import TPCDSError

            raise TPCDSError("dsqgen failed")
        values = {
            44: {"STORE.01": "4", "NULLCOLSS.01": "ss_hdemo_sk"},
            93: {"REASON.01": f"reason for seed {kwargs['seed']}"},
        }[query_id]
        return SimpleNamespace(substitutions=values)


@pytest.fixture
def stub_dsqgen(tmp_path, monkeypatch):
    binary = tmp_path / "dsqgen"
    binary.write_bytes(b"stub")
    stub = _StubDSQGen(binary)
    import benchbox.core.tpcds.c_tools as c_tools

    monkeypatch.setattr(c_tools, "DSQGenBinary", lambda: stub)
    return stub


@pytest.fixture(scope="module")
def tpcds_benchmark(tmp_path_factory):
    from benchbox.core.tpcds.c_tools import TPCDSError
    from benchbox.tpcds import TPCDS

    try:
        return TPCDS(scale_factor=SCALE, output_dir=tmp_path_factory.mktemp("tpcds"))
    except (TPCDSError, FileNotFoundError, RuntimeError) as exc:
        pytest.skip(f"dsqgen binary or templates unavailable: {exc}")


class TestPowerParameterSeed:
    @pytest.mark.parametrize("seed,stream_id", RUNS)
    def test_the_sql_power_test_renders_each_stream_with_this_seed(self, seed, stream_id):
        calls = []

        class Benchmark:
            def get_query(self, query_id, **kwargs):
                calls.append(kwargs)
                return "select 1"

        TPCDSPowerTest(
            benchmark=Benchmark(),
            connection_factory=_RecordingConnection,
            scale_factor=SCALE,
            seed=seed,
            stream_id=stream_id,
            query_subset=[93],
        ).run()
        assert calls, "the power test rendered no query"
        assert {call["seed"] for call in calls} == {power_parameter_seed(seed, stream_id)}
        assert {call["scale_factor"] for call in calls} == {SCALE}

    def test_no_seed_and_seed_zero_both_mean_seed_one(self):
        assert power_parameter_seed(None, 0) == power_parameter_seed(0, 0) == power_parameter_seed(1, 0) == 1001
        assert power_parameter_seed(42, 3) == 1045


class TestSQLAndDataFrameBindTheSameValues:
    @pytest.mark.parametrize("seed,stream_id", EVERY_ADAPTED_QUERY_RUNS)
    def test_every_adapted_query(self, tpcds_benchmark, seed, stream_id):
        benchmark = tpcds_benchmark
        dsqgen = benchmark._impl.query_manager.dsqgen
        rendered: dict[int, str] = {}
        original_generate = dsqgen.generate

        def recording_generate(query_id, **kwargs):
            sql = original_generate(query_id, **kwargs)
            if str(query_id).isdigit():
                rendered[int(query_id)] = sql
            assert kwargs["seed"] == power_parameter_seed(seed, stream_id)
            return sql

        dsqgen.generate = recording_generate
        try:
            TPCDSPowerTest(
                benchmark=benchmark,
                connection_factory=_RecordingConnection,
                scale_factor=SCALE,
                seed=seed,
                stream_id=stream_id,
                dialect="duckdb",
                query_subset=list(adapter_query_ids()),
            ).run()
        finally:
            dsqgen.generate = original_generate
        assert set(adapter_query_ids()) <= set(rendered)

        queries = get_tpcds_dataframe_queries(_config(seed), benchmark, stream_id)
        bindings = {query_number(q.query_id): query_binding(q) for q in queries}
        assert {n for n, b in bindings.items() if b is not None} == set(adapter_query_ids())
        assert all(bindings[n] is None for n in bindings if n not in ADAPTERS)

        def same_sql(number):
            binding = bindings[number]
            again = dsqgen.generate_with_parameters(number, dict(binding.logged), seed=7, scale_factor=SCALE)
            return number, again == rendered[number], dict(binding.parameters) == ADAPTERS[number](binding.logged)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(same_sql, adapter_query_ids()))
        differing = [n for n, sql_ok, _ in results if not sql_ok]
        unadapted = [n for n, _, params_ok in results if not params_ok]
        assert not differing, f"seed={seed} stream={stream_id}: bound values differ from the SQL for {differing}"
        assert not unadapted, f"seed={seed} stream={stream_id}: bound parameters are not the adapter's for {unadapted}"
        for number, binding in bindings.items():
            if binding is not None:
                assert binding.seed == power_parameter_seed(seed, stream_id)
                assert binding.stream_id == 0
                assert binding.scale_factor == SCALE


class TestResolution:
    @needs_unadapted
    def test_bound_queries_carry_the_binding_and_unadapted_ones_are_returned_as_is(self, stub_dsqgen):
        config = _config(seed=42, queries=["Q44", "Q93", f"Q{UNADAPTED}"])
        queries = {q.query_id: q for q in get_tpcds_dataframe_queries(config, None, stream_id=1)}

        assert queries[f"Q{UNADAPTED}"] is TPCDS_DATAFRAME_QUERIES.get(f"Q{UNADAPTED}")
        assert query_binding(queries[f"Q{UNADAPTED}"]) is None
        q93 = query_binding(queries["Q93"])
        assert q93.parameters == {"reason": f"reason for seed {power_parameter_seed(42, 1)}"}
        assert query_binding(queries["Q44"]).parameters == {"store_sk": 4, "null_col": "ss_hdemo_sk"}
        assert query_binding(TPCDS_DATAFRAME_QUERIES.get("Q93")) is None
        assert sorted(call[0] for call in stub_dsqgen.calls) == [44, 93]
        assert {call[1]["stream_id"] for call in stub_dsqgen.calls} == {0}
        assert {call[1]["scale_factor"] for call in stub_dsqgen.calls} == {SCALE}

    def test_each_stream_is_bound_once_per_process(self, stub_dsqgen):
        config = _config(seed=42, queries=["Q93"])
        get_tpcds_dataframe_queries(config, None, stream_id=0)
        get_tpcds_dataframe_queries(config, None, stream_id=0)
        assert len(stub_dsqgen.calls) == 1
        get_tpcds_dataframe_queries(config, None, stream_id=1)
        assert [call[1]["seed"] for call in stub_dsqgen.calls] == [1042, 1043]

    def test_inspection_callers_can_skip_binding(self, stub_dsqgen):
        config = _config(seed=42, queries=["Q93"])
        queries = get_dataframe_queries_for_benchmark(config, None, stream_id=0, bind_parameters=False)
        assert all(query_binding(q) is None for q in queries)
        assert not stub_dsqgen.calls

    def test_a_failed_binding_fails_the_run_instead_of_using_the_defaults(self, stub_dsqgen):
        from benchbox.core.tpcds.c_tools import TPCDSError

        stub_dsqgen.fail = True
        with pytest.raises(TPCDSError):
            get_tpcds_dataframe_queries(_config(seed=42, queries=["Q93"]), None, stream_id=0)

    def test_tpch_resolution_is_unchanged(self, stub_dsqgen):
        from benchbox.core.dataframe.query_resolution import get_tpch_dataframe_queries

        config = SimpleNamespace(name="tpch", scale_factor=SCALE, options={"seed": 42}, queries=None)
        assert get_dataframe_queries_for_benchmark(config, None, stream_id=1) == get_tpch_dataframe_queries(1)
        assert not stub_dsqgen.calls


class TestBoundImplementation:
    def test_the_binding_is_installed_only_while_the_query_runs(self, stub_dsqgen):
        from benchbox.core.dataframe.query import DataFrameQuery

        seen = []
        query = DataFrameQuery(
            query_id="Q93",
            query_name="probe",
            description="records the parameters it runs on",
            expression_impl=lambda ctx: seen.append(get_parameters(93).get("reason")),
        )
        from benchbox.core.tpcds.dataframe_queries.production_binding import bind_power_stream

        (bound,) = bind_queries([query], bind_power_stream([93], scale_factor=SCALE, seed=None, stream_id=2))
        default = get_parameters(93).get("reason")
        bound.expression_impl(None)
        assert seen == [f"reason for seed {power_parameter_seed(None, 2)}"]
        assert get_parameters(93).get("reason") == default


class TestRecordedParameterSet:
    @needs_unadapted
    def test_the_dataframe_result_records_which_queries_were_bound_and_which_ran_on_defaults(self):
        from benchbox.core.results.builder import RunConfigInput
        from benchbox.platforms.dataframe.benchmark_mixin import _describe_query_parameters

        described = _describe_query_parameters(_config(seed=42, queries=["Q93", f"Q{UNADAPTED}", "3"]))
        assert described == (
            "dsqgen -LOG values (-RNGSEED 1042 + stream_id, dsqgen stream 0) for 2 of 3 queries: Q3 Q93; "
            f"default_parameters.yaml for 1: Q{UNADAPTED}"
        )
        assert RunConfigInput(query_parameters=described).to_dict()["query_parameters"] == described

    def test_a_full_run_lists_every_query_by_how_it_was_parameterised(self):
        from benchbox.platforms.dataframe.benchmark_mixin import _describe_query_parameters

        described = _describe_query_parameters(_config())
        assert "-RNGSEED 1001 + stream_id" in described
        assert f"for {len(ADAPTERS)} of 99 queries" in described
        unadapted = sorted(set(range(1, 100)) - set(ADAPTERS))
        assert described.endswith(
            f"default_parameters.yaml for {len(unadapted)}: " + (" ".join(f"Q{n}" for n in unadapted) or "none")
        )

    def test_other_benchmarks_record_nothing(self):
        from benchbox.platforms.dataframe.benchmark_mixin import _describe_query_parameters

        assert _describe_query_parameters(SimpleNamespace(name="tpcdi", options={"seed": 42}, queries=None)) is None


@pytest.mark.parametrize("seed,stream_id", RUNS)
def test_second_statement_variants_bind_the_sql_power_test_draw(tpcds_benchmark, seed, stream_id):
    variants = ["14b", "23b", "24b", "39b"]
    resolved = {
        query.query_id.lower().removeprefix("q"): query
        for query in get_tpcds_dataframe_queries(_config(seed, queries=variants), tpcds_benchmark, stream_id)
    }
    dsqgen = tpcds_benchmark._impl.query_manager.dsqgen
    for variant in variants:
        query = resolved[variant]
        binding = query_binding(query)
        assert binding is not None
        assert binding.seed == power_parameter_seed(seed, stream_id)
        assert binding.stream_id == 0
        assert binding.scale_factor == SCALE
        expected = dsqgen.generate(variant, seed=power_parameter_seed(seed, stream_id), scale_factor=SCALE)
        assert dsqgen.generate_with_parameters(variant, dict(binding.logged), seed=7, scale_factor=SCALE) == expected
        registered = TPCDS_DATAFRAME_QUERIES.get_or_raise(f"Q{variant}")
        assert query.expression_impl.__wrapped__ is registered.expression_impl
        assert query.pandas_impl.__wrapped__ is registered.pandas_impl


@pytest.mark.parametrize("family", ["expression", "pandas"])
def test_concurrent_bound_queries_keep_their_own_parameters(stub_dsqgen, family):
    from benchbox.core.dataframe.query import DataFrameQuery
    from benchbox.core.tpcds.dataframe_queries.production_binding import bind_power_stream

    barrier = Barrier(3)
    default = get_parameters(93).get("reason")

    def read_parameters(ctx):
        barrier.wait(timeout=10)
        reason = get_parameters(93).get("reason")
        barrier.wait(timeout=10)
        return reason

    query = DataFrameQuery(
        query_id="Q93",
        query_name="probe",
        description="parameter isolation",
        expression_impl=read_parameters,
        pandas_impl=read_parameters,
    )
    bound = [
        bind_queries([query], bind_power_stream([93], scale_factor=SCALE, seed=seed, stream_id=stream))[0]
        for seed, stream in [(101, 0), (202, 1)]
    ]
    implementations = [getattr(item, f"{family}_impl") for item in [*bound, query]]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda implementation: implementation(None), implementations))
    assert results == [
        f"reason for seed {power_parameter_seed(101, 0)}",
        f"reason for seed {power_parameter_seed(202, 1)}",
        default,
    ]
    assert get_parameters(93).get("reason") == default


def test_nested_parameter_bindings_restore_the_outer_scope_after_failure():
    default = get_parameters(93).get("reason")
    with parameter_overrides({93: {"reason": "outer"}}):
        with pytest.raises(ValueError, match="query failed"):
            with parameter_overrides({93: {"reason": "inner"}, 44: {"store_sk": 777}}):
                assert get_parameters(93).get("reason") == "inner"
                assert get_parameters(44).get("store_sk") == 777
                raise ValueError("query failed")
        assert get_parameters(93).get("reason") == "outer"
        with parameter_overrides({44: {"store_sk": 888}}):
            assert get_parameters(93).get("reason") == "outer"
            assert get_parameters(44).get("store_sk") == 888
    assert get_parameters(93).get("reason") == default
