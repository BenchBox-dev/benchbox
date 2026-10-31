import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.registry import REGISTRY
from benchbox.sql_compat.rules.execution_filter.questdb_read_primitives import QUESTDB_READ_PRIMITIVES_SKIPS

pytestmark = [pytest.mark.unit, pytest.mark.fast]

EXPECTED_SKIPS = {
    "approx_top_k_lineitem",
    "json_aggregates",
    "fulltext_simple_search",
    "fulltext_boolean_search",
    "fulltext_phrase_search",
    "array_distinct",
    "struct_construction",
    "struct_access",
    "array_of_struct",
    "map_construction",
    "map_access",
    "map_keys_values",
    "list_filter",
}


def test_questdb_skips_exactly_the_unsupported_queries():
    benchmark = ReadPrimitivesBenchmark(scale_factor=0.01)

    assert set(benchmark.get_platform_skip_queries("QuestDB")) == EXPECTED_SKIPS


def test_questdb_skips_name_only_catalog_queries():
    assert set(ReadPrimitivesBenchmark(scale_factor=0.01).get_all_queries()) >= EXPECTED_SKIPS


@pytest.mark.parametrize("query_id", sorted(EXPECTED_SKIPS))
def test_every_questdb_skip_is_registered_with_an_engine_error_reason(query_id):
    reason = QUESTDB_READ_PRIMITIVES_SKIPS[query_id]
    decision = REGISTRY.resolve(
        CompatibilityContext(
            platform="questdb",
            platform_version=None,
            benchmark="read_primitives",
            query_id=query_id,
            phase=Phase.EXECUTION_FILTER,
            mode="sql",
            dialect="questdb",
        )
    )

    assert decision is not None
    assert decision.action == CompatAction.SKIP_QUERY
    assert decision.reason == reason
    assert "QuestDB 10.0.1" in reason


def test_other_platforms_are_unaffected():
    assert ReadPrimitivesBenchmark(scale_factor=0.01).get_platform_skip_queries("duckdb") == []
