from __future__ import annotations

import pytest

from benchbox._vendor import sqlglot
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.platforms.datafusion_tpch_rewrites import (
    DATAFUSION_TPCH_REWRITE_NAMES,
    normalize_tpch_query_id,
    rewrite_datafusion_tpch_query,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _tpch(query_id: int) -> str:
    return TPCHBenchmark(scale_factor=0.01).get_query(query_id, dialect="datafusion")


def test_registry_rules_name_the_same_fork_rewrites():
    import benchbox.sql_compat.rules.query_adapter.datafusion_query_rewrites
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    registered = {
        key[3]: entry.decision.payload.transformer_id
        for key, entry in REGISTRY.all_rules()
        if key[:3] == (Phase.QUERY_ADAPTER, "datafusion", "tpch")
    }
    assert registered == DATAFUSION_TPCH_REWRITE_NAMES


def test_q11_threshold_subquery_is_decorrelated_into_a_join():
    rewritten = rewrite_datafusion_tpch_query(_tpch(11), "11")
    assert "(SELECT" not in rewritten.upper().split("HAVING")[-1]
    assert sqlglot.parse_one(rewritten, read="datafusion").find(sqlglot.exp.Join) is not None


def test_q16_not_in_becomes_not_exists():
    rewritten = rewrite_datafusion_tpch_query(_tpch(16), "16")
    assert "NOT EXISTS" in rewritten.upper()
    assert " NOT ps_suppkey IN" not in rewritten


def test_q18_in_having_becomes_exists():
    rewritten = rewrite_datafusion_tpch_query(_tpch(18), "18")
    assert "EXISTS" in rewritten.upper()
    assert "o_orderkey IN (SELECT" not in rewritten


def test_q20_nested_correlation_is_removed():
    rewritten = rewrite_datafusion_tpch_query(_tpch(20), "20")
    assert rewritten != _tpch(20)
    assert sqlglot.parse_one(rewritten, read="datafusion")


@pytest.mark.parametrize("query_id", ["Q11", "q11", " 11 ", 11])
def test_query_id_forms_select_the_same_rewrite(query_id):
    assert rewrite_datafusion_tpch_query(_tpch(11), query_id) == rewrite_datafusion_tpch_query(_tpch(11), "11")


@pytest.mark.parametrize("query_id", [1, 5, 22, None, "99"])
def test_unaffected_queries_pass_through(query_id):
    sql = "SELECT * FROM lineitem WHERE l_orderkey = 5"
    assert rewrite_datafusion_tpch_query(sql, query_id) == sql


@pytest.mark.parametrize(("raw", "expected"), [("Q11", "11"), ("q16", "16"), (" 18 ", "18"), (20, "20"), (None, None)])
def test_normalize_tpch_query_id(raw, expected):
    assert normalize_tpch_query_id(raw) == expected
