from __future__ import annotations

import importlib

import pytest

from benchbox.core.dataframe.query import DataFrameQuery, QueryRegistry

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize(
    ("module_path", "registry_name", "get_name", "list_name"),
    [
        (
            "benchbox.core.clickbench.dataframe_queries.registry",
            "CLICKBENCH_DATAFRAME_QUERIES",
            "get_clickbench_query",
            "list_clickbench_queries",
        ),
        (
            "benchbox.core.nyctaxi.dataframe_queries.registry",
            "NYCTAXI_DATAFRAME_QUERIES",
            "get_nyctaxi_query",
            "list_nyctaxi_queries",
        ),
        (
            "benchbox.core.ssb.dataframe_queries.registry",
            "SSB_DATAFRAME_QUERIES",
            "get_ssb_query",
            "list_ssb_queries",
        ),
        (
            "benchbox.core.tpcds.dataframe_queries.registry",
            "TPCDS_DATAFRAME_QUERIES",
            "get_tpcds_query",
            "list_tpcds_queries",
        ),
    ],
)
def test_registry_wrapper_helpers_follow_rebound_singleton(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    registry_name: str,
    get_name: str,
    list_name: str,
) -> None:

    module = importlib.import_module(module_path)
    replacement = QueryRegistry("replacement")
    query = DataFrameQuery(
        query_id="QX",
        query_name="Replacement Registry Query",
        description="Verifies wrapper helpers use the rebound registry singleton.",
        pandas_impl=lambda ctx: None,
    )

    monkeypatch.setattr(module, registry_name, replacement)

    module.register_query(query)

    assert getattr(module, get_name)("QX") is query
    assert any(candidate is query for candidate in getattr(module, list_name)())


