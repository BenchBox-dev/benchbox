from __future__ import annotations

import os
from dataclasses import asdict
from types import SimpleNamespace

import pytest

dd = pytest.importorskip("dask.dataframe")
pd = pytest.importorskip("pandas")
pytest.importorskip("distributed")

import dask

from benchbox.core.dataframe.query import DataFrameQuery
from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter
from tests.unit.platforms.dataframe.observed_engine_contract import (
    assert_observation_ignores_version,
    assert_observation_tracks_engine,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture
def restore_environ(monkeypatch):
    monkeypatch.setattr(os, "environ", os.environ.copy())


@pytest.fixture
def lazy():
    return dd.from_pandas(pd.DataFrame({"a": [1, 2, 3, 1], "b": [4, 5, 6, 7]}), npartitions=2)


def _observe(engine: str, lazy) -> str:
    adapter = DaskDataFrameAdapter(execution_engine=engine, n_workers=1, threads_per_worker=1)
    try:
        return adapter.observe_execution_engine(lazy)
    finally:
        adapter.close()


def test_forcing_each_engine_changes_the_observed_value(lazy, restore_environ):
    assert_observation_tracks_engine(
        lambda engine: _observe(engine, lazy), {"threaded": "threaded", "distributed": "distributed"}
    )


def test_observation_does_not_depend_on_the_reported_library_version(lazy, monkeypatch):
    adapter = DaskDataFrameAdapter(execution_engine="threaded")

    def observe_under(version: str) -> str:
        monkeypatch.setattr(dask, "__version__", version)
        return adapter.observe_execution_engine(lazy)

    assert_observation_ignores_version(observe_under)


def test_observation_follows_the_scheduler_in_effect(lazy):
    adapter = DaskDataFrameAdapter(execution_engine="threaded")
    with dask.config.set(scheduler="synchronous"):
        assert adapter.observe_execution_engine(lazy) == "synchronous"


def test_run_records_the_observed_scheduler_with_its_source(lazy):
    adapter = DaskDataFrameAdapter(execution_engine="threaded")
    ctx = adapter.create_context()
    ctx.register_table("t", lazy)
    query = DataFrameQuery(
        query_id="Q1",
        query_name="Q1",
        description="Q1",
        pandas_impl=lambda ctx: ctx.get_table("t").groupby("a").agg({"b": "sum"}).reset_index(),
    )
    row = adapter.execute_query(ctx, query)
    platform_info = SimpleNamespace(config={"execution_engine": asdict(adapter._engine_receipt)})
    adapter._finalize_engine_observation(platform_info)

    receipt = platform_info.config["execution_engine"]
    assert row["execution_engine"] == "threaded"
    assert (receipt["observed"], receipt["observed_source"], receipt["observed_reason"]) == (
        "threaded",
        "runtime_state",
        None,
    )
