from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.dataframe.query import QueryRegistry
from benchbox.core.dataframe.query_resolution import get_dataframe_queries_for_benchmark, registry_dataframe_queries
from benchbox.core.schemas import BenchmarkConfig
from benchbox.platforms.dataframe.benchmark_mixin import no_dataframe_queries_message

pytestmark = [pytest.mark.unit, pytest.mark.fast]


PREVIOUSLY_UNREACHABLE = {
    "amplab": 8,
    "coffeeshop": 11,
    "h2odb": 10,
    "nyctaxi": 25,
    "ssb": 13,
    "tpch_skew": 22,
    "tsbs_devops": 18,
}


@pytest.mark.parametrize(("benchmark_id", "expected_count"), sorted(PREVIOUSLY_UNREACHABLE.items()))
def test_resolution_reaches_every_shipped_registry(benchmark_id: str, expected_count: int) -> None:
    config = BenchmarkConfig(name=benchmark_id, display_name=benchmark_id, scale_factor=0.01)

    queries = get_dataframe_queries_for_benchmark(config, None, stream_id=0)

    assert len(queries) == expected_count
    assert all(getattr(query, "query_id", None) for query in queries)


def test_resolution_detects_a_nonstandard_registry_name() -> None:
    queries = registry_dataframe_queries("tpcds_obt")

    ids = [query.query_id for query in queries]
    assert len(ids) == 17
    assert all(query_id.startswith("obt_") for query_id in ids)


def test_the_registry_helper_is_quiet_about_a_benchmark_that_ships_none() -> None:
    assert registry_dataframe_queries("no_such_benchmark") == []


def test_nested_module_import_error_is_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.core.dataframe import query_resolution

    error = ModuleNotFoundError("No module named 'broken_dependency'", name="broken_dependency")

    def broken_import(_target: str) -> None:
        raise error

    monkeypatch.setattr(query_resolution.importlib, "import_module", broken_import)

    with pytest.raises(ModuleNotFoundError, match="broken_dependency"):
        registry_dataframe_queries("broken_benchmark")


def test_multiple_registries_are_rejected_as_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.core.dataframe import query_resolution

    module = SimpleNamespace(first=QueryRegistry("first"), second=QueryRegistry("second"))
    monkeypatch.setattr(query_resolution.importlib, "import_module", lambda _target: module)

    with pytest.raises(RuntimeError, match="Multiple DataFrame query registries"):
        registry_dataframe_queries("ambiguous")


def test_registry_aliases_do_not_create_false_ambiguity(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.core.dataframe import query_resolution

    registry = QueryRegistry("aliased")
    module = SimpleNamespace(primary=registry, compatibility_alias=registry)
    monkeypatch.setattr(query_resolution.importlib, "import_module", lambda _target: module)

    assert registry_dataframe_queries("aliased") == []


def test_the_message_distinguishes_a_narrow_filter_from_a_missing_source() -> None:
    filtered = no_dataframe_queries_message("tpch", {"99"})
    missing = no_dataframe_queries_message("tpcds_obt", None)

    assert "--queries" in filtered and "'99'" in filtered
    assert "--queries" not in missing
    assert "no DataFrame query source" in missing
    assert "SQL mode" in missing
