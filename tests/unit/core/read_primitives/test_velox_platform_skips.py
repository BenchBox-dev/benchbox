import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.registry import REGISTRY
from benchbox.sql_compat.rules.execution_filter.velox_read_primitives import VELOX_READ_PRIMITIVES_SKIPS

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_velox_skips_only_approx_top_k():
    assert ReadPrimitivesBenchmark(scale_factor=0.01).get_platform_skip_queries("Velox") == ["approx_top_k_lineitem"]


def test_velox_skip_is_registered_with_the_engine_error():
    reason = VELOX_READ_PRIMITIVES_SKIPS["approx_top_k_lineitem"]
    decision = REGISTRY.resolve(
        CompatibilityContext(
            platform="velox",
            platform_version=None,
            benchmark="read_primitives",
            query_id="approx_top_k_lineitem",
            phase=Phase.EXECUTION_FILTER,
            mode="sql",
            dialect="spark",
        )
    )

    assert decision is not None
    assert decision.action == CompatAction.SKIP_QUERY
    assert decision.reason == reason


def test_velox_rollup_uses_output_aliases_in_order_by():
    sql = ReadPrimitivesBenchmark(scale_factor=0.01).get_queries(dialect="spark")["olap_rollup_analysis"]
    assert "ORDER BY region" in sql
    assert "r.r_name NULLS" not in sql
