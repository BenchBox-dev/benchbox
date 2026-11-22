from __future__ import annotations

import time
from dataclasses import asdict
from types import SimpleNamespace

import pytest

pl = pytest.importorskip("polars")

from benchbox.core.dataframe.query import DataFrameQuery
from benchbox.platforms.dataframe import polars_df
from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter, classify_physical_plan
from tests.unit.platforms.dataframe.observed_engine_contract import (
    assert_observation_ignores_version,
    assert_observation_tracks_engine,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture
def scan(tmp_path):
    path = tmp_path / "t.parquet"
    pl.DataFrame({"a": [1, 2, 3, 1], "b": [4, 5, 6, 7]}).write_parquet(path)
    return pl.scan_parquet(path)


def _aggregate(scan):
    return scan.group_by("a").agg(pl.col("b").sum())


def test_forcing_each_engine_changes_the_observed_value(scan):
    lazy = _aggregate(scan)
    assert_observation_tracks_engine(
        lambda engine: PolarsDataFrameAdapter(execution_engine=engine).observe_execution_engine(lazy),
        {"in-memory": "in-memory", "streaming": "streaming"},
    )


def test_observation_does_not_depend_on_the_reported_library_version(scan, monkeypatch):
    lazy = _aggregate(scan)
    adapter = PolarsDataFrameAdapter(execution_engine="streaming")

    def observe_under(version: str) -> str:
        monkeypatch.setattr(pl, "__version__", version)
        return adapter.observe_execution_engine(lazy)

    assert_observation_ignores_version(observe_under)


def test_default_engine_observation_follows_the_plan_the_library_builds(scan):
    lazy = _aggregate(scan)
    observed = PolarsDataFrameAdapter().observe_execution_engine(lazy)
    assert observed == classify_physical_plan(lazy.show_graph(engine="auto", raw_output=True, plan_stage="physical"))


def test_engine_affinity_setting_is_followed_by_the_default_engine(scan):
    lazy = _aggregate(scan)
    seen = {}
    for affinity in ("in-memory", "streaming"):
        with pl.Config(engine_affinity=affinity):
            seen[affinity] = PolarsDataFrameAdapter().observe_execution_engine(lazy)
    assert seen == {"in-memory": "in-memory", "streaming": "streaming"}


def test_streaming_plan_with_in_memory_fallback_nodes_is_mixed(scan):
    lazy = scan.with_columns(pl.col("b").map_batches(lambda series: series))
    assert PolarsDataFrameAdapter(execution_engine="streaming").observe_execution_engine(lazy) == "mixed"


def _run_queries(adapter, scan, queries):
    ctx = adapter.create_context()
    ctx.register_table("t", scan)
    rows = [
        adapter.execute_query(
            ctx, DataFrameQuery(query_id=name, query_name=name, description=name, expression_impl=impl)
        )
        for name, impl in queries
    ]
    platform_info = SimpleNamespace(config={"execution_engine": asdict(adapter._engine_receipt)})
    adapter._finalize_engine_observation(platform_info)
    return rows, platform_info.config["execution_engine"]


def test_run_records_the_observed_engine_per_query_and_for_the_run(scan):
    adapter = PolarsDataFrameAdapter(execution_engine="streaming")
    rows, receipt = _run_queries(adapter, scan, [("Q1", lambda ctx: _aggregate(ctx.get_table("t")))])
    assert rows[0]["execution_engine"] == "streaming"
    assert (receipt["observed"], receipt["observed_source"], receipt["observed_reason"]) == (
        "streaming",
        "explain",
        None,
    )


def test_run_whose_queries_differ_is_mixed(scan):
    adapter = PolarsDataFrameAdapter(execution_engine="streaming")
    _, receipt = _run_queries(
        adapter,
        scan,
        [
            ("Q1", lambda ctx: _aggregate(ctx.get_table("t"))),
            ("Q2", lambda ctx: ctx.get_table("t").with_columns(pl.col("b").map_batches(lambda series: series))),
        ],
    )
    assert (receipt["observed"], receipt["observed_source"]) == ("mixed", "explain")


def test_run_without_a_completed_query_is_not_captured_with_a_reason(scan):
    _, receipt = _run_queries(PolarsDataFrameAdapter(execution_engine="streaming"), scan, [])
    assert (receipt["observed"], receipt["observed_source"]) == ("not_captured", "none")
    assert receipt["observed_reason"] == "no query completed"


def test_library_without_a_physical_plan_api_is_not_captured_with_that_reason(scan, monkeypatch):
    monkeypatch.setattr(polars_df, "physical_plan_unavailable_reason", lambda: "no physical plan")
    adapter = PolarsDataFrameAdapter(execution_engine="streaming")
    rows, receipt = _run_queries(adapter, scan, [("Q1", lambda ctx: _aggregate(ctx.get_table("t")))])
    assert "execution_engine" not in rows[0]
    assert (receipt["observed"], receipt["observed_reason"]) == ("not_captured", "no physical plan")


def test_failed_observation_is_not_silently_replaced_by_an_engine(scan, monkeypatch):
    def broken(self, lazy):
        raise RuntimeError("no graph")

    monkeypatch.setattr(PolarsDataFrameAdapter, "observe_execution_engine", broken)
    adapter = PolarsDataFrameAdapter(execution_engine="streaming")
    rows, receipt = _run_queries(adapter, scan, [("Q1", lambda ctx: _aggregate(ctx.get_table("t")))])
    assert rows[0]["status"] == "SUCCESS"
    assert receipt["observed"] == "not_captured"
    assert "1 of 1" in receipt["observed_reason"]


@pytest.mark.parametrize(
    "graph",
    [
        "",
        "digraph other {\n}",
        'digraph polars {\nlabel=<streaming engine node>\n4294967297 [label="x"];\n}',
        "graph of an unrelated future format",
        "graph polars_query_v2",
    ],
)
def test_unrecognized_physical_plan_is_not_classified(graph):
    assert classify_physical_plan(graph) is None


def test_unrecognized_physical_plan_is_not_captured_with_a_reason(scan, monkeypatch):
    monkeypatch.setattr(pl.LazyFrame, "show_graph", lambda self, **kwargs: "digraph future {\n}")
    adapter = PolarsDataFrameAdapter(execution_engine="streaming")
    rows, receipt = _run_queries(adapter, scan, [("Q1", lambda ctx: _aggregate(ctx.get_table("t")))])
    assert "execution_engine" not in rows[0]
    assert (receipt["observed"], receipt["observed_source"]) == ("not_captured", "none")
    assert receipt["observed_reason"]


def test_observation_time_is_excluded_from_the_query_timer(scan, monkeypatch):
    from benchbox.core.results.fetch_mode import ConversionClock

    def slow(self, lazy):
        time.sleep(0.05)
        return "streaming"

    monkeypatch.setattr(PolarsDataFrameAdapter, "observe_execution_engine", slow)
    adapter = PolarsDataFrameAdapter(execution_engine="streaming")
    clock = ConversionClock().start()
    try:
        _run_queries(adapter, scan, [("Q1", lambda ctx: _aggregate(ctx.get_table("t")))])
    finally:
        excluded = clock.finish()
    assert excluded >= 0.05
