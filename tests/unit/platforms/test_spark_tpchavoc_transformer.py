# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox._vendor import sqlglot
from benchbox.core.tpchavoc.dialect_compat import (
    DUAL_VARIANT_IDS,
    GROUP_BY_EMPTY_VARIANT_IDS,
    SCALAR_GROUP_BY_VARIANT_IDS,
    SPARK_CHAR_CASE_VARIANT_IDS,
    SPARK_WINDOW_FILTER_VARIANT_IDS,
    rewrite_spark_variant,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

SCALAR_IDS = sorted(SCALAR_GROUP_BY_VARIANT_IDS)
EMPTY_IDS = sorted(GROUP_BY_EMPTY_VARIANT_IDS)
DUAL_IDS = sorted(DUAL_VARIANT_IDS)
WINDOW_FILTER_IDS = sorted(SPARK_WINDOW_FILTER_VARIANT_IDS)
CHAR_CASE_IDS = sorted(SPARK_CHAR_CASE_VARIANT_IDS)


def _variant_sql(variant_id: str) -> str:
    from benchbox.core.tpchavoc.variant_sets import VARIANT_REGISTRY

    query, _, variant = variant_id.partition("_v")
    generator = VARIANT_REGISTRY[int(query)][int(variant)]
    return generator.generate("base_query")


def _assert_spark_parseable(sql: str, variant_id: str) -> None:
    try:
        sqlglot.parse_one(sql, read="spark")
    except Exception as exc:
        pytest.fail(f"{variant_id}: transformed SQL does not parse as spark: {exc}")


@pytest.mark.parametrize("variant_id", SCALAR_IDS)
def test_scalar_group_by_variants_wrapped_in_first(variant_id: str):
    rewritten = rewrite_spark_variant(variant_id, _variant_sql(variant_id))
    expected_first_count = 2 if variant_id in {"1_v1", "8_v1", "12_v1"} else 1
    assert rewritten.upper().count("FIRST(") == expected_first_count
    _assert_spark_parseable(rewritten, variant_id)


@pytest.mark.parametrize("variant_id", EMPTY_IDS)
def test_group_by_empty_variants_drop_empty_grouping(variant_id: str):
    original = _variant_sql(variant_id)
    assert "group by" in original.lower()
    rewritten = rewrite_spark_variant(variant_id, original)
    assert "group by" not in rewritten.lower()
    assert "having" in rewritten.lower()
    _assert_spark_parseable(rewritten, variant_id)


@pytest.mark.parametrize("variant_id", DUAL_IDS)
def test_dual_variants_gain_column_alias(variant_id: str):
    rewritten = rewrite_spark_variant(variant_id, _variant_sql(variant_id))
    assert "dual_col" in rewritten.lower()
    assert "union all" in rewritten.lower()
    _assert_spark_parseable(rewritten, variant_id)


@pytest.mark.parametrize("variant_id", WINDOW_FILTER_IDS)
def test_window_filter_variants_move_condition_into_aggregate(variant_id: str):
    original = _variant_sql(variant_id)
    assert "filter (where" in original.lower()
    rewritten = rewrite_spark_variant(variant_id, original)
    assert "filter" not in rewritten.lower()
    assert "MIN(IF(" in rewritten.upper()
    assert "OVER (PARTITION BY" in rewritten.upper()
    _assert_spark_parseable(rewritten, variant_id)


@pytest.mark.parametrize("variant_id", CHAR_CASE_IDS)
def test_char_case_variants_trim_both_comparison_sides(variant_id: str):
    original = _variant_sql(variant_id)
    rewritten = rewrite_spark_variant(variant_id, original)
    if variant_id == "2_v10":
        assert "RTRIM(r_name) = RTRIM(CASE WHEN" in rewritten
        assert "r_name = CASE" not in rewritten
    else:
        assert "= rtrim(case when" in rewritten.lower()
        assert "rtrim('" in rewritten.lower()
    _assert_spark_parseable(rewritten, variant_id)


@pytest.mark.parametrize("variant_id", ["2_v1", "5_v5", "22_v10", "Q99", "11"])
def test_unlisted_query_ids_pass_through_unchanged(variant_id: str):
    original = _variant_sql("2_v1")
    assert rewrite_spark_variant(variant_id, original) == original


def test_none_query_id_passes_through_unchanged():
    original = _variant_sql("1_v1")
    assert rewrite_spark_variant(None, original) == original


def test_query_id_normalization_accepts_prefixed_forms():
    original = _variant_sql("1_v1")
    for form in ("Q1_V1", "q1_v1", "1-V1", " 1_v1 ", "Q01_V1", "01_v1"):
        assert "FIRST(" in rewrite_spark_variant(form, original).upper(), form


@pytest.mark.parametrize("variant_id", SCALAR_IDS + EMPTY_IDS + DUAL_IDS + WINDOW_FILTER_IDS + CHAR_CASE_IDS)
def test_rewrites_are_idempotent(variant_id: str):
    first_pass = rewrite_spark_variant(variant_id, _variant_sql(variant_id))
    second_pass = rewrite_spark_variant(variant_id, first_pass)
    assert second_pass == first_pass
    assert "FIRST(FIRST(" not in second_pass.upper()


def test_rewrite_rule_ids_cover_transformer_ids():
    assert {
        "1_v1",
        "3_v1",
        "7_v1",
        "8_v1",
        "9_v1",
        "10_v1",
        "12_v1",
        "16_v1",
    } == SCALAR_GROUP_BY_VARIANT_IDS
    assert {"6_v2", "14_v2"} == GROUP_BY_EMPTY_VARIANT_IDS
    assert {"17_v4"} == DUAL_VARIANT_IDS
    assert {"2_v7"} == SPARK_WINDOW_FILTER_VARIANT_IDS
    assert {"2_v10", "5_v10", "8_v10", "11_v10"} == SPARK_CHAR_CASE_VARIANT_IDS


def test_spark_tpchavoc_rules_registered():
    import benchbox.sql_compat.rules.query_adapter.spark_tpchavoc_rewrites
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.registry import REGISTRY

    rules = [
        (key, entry)
        for key, entry in REGISTRY.all_rules()
        if key[0] is Phase.QUERY_ADAPTER and key[1] == "spark" and key[2] == "tpchavoc"
    ]
    assert len(rules) == 16, f"Expected 16 spark/tpchavoc rules, got {len(rules)}"
    rule_ids = {entry.rule_id for _, entry in rules}
    assert "query_adapter.spark.tpchavoc.q01_scalar_group_by_first" in rule_ids
    assert "query_adapter.spark.tpchavoc.q06_group_by_empty_drop" in rule_ids
    assert "query_adapter.spark.tpchavoc.q17_dual_column_alias" in rule_ids


@pytest.mark.parametrize("variant_id", SCALAR_IDS + EMPTY_IDS + DUAL_IDS + WINDOW_FILTER_IDS + CHAR_CASE_IDS)
def test_spark_tpchavoc_rule_resolution(variant_id: str):
    import benchbox.sql_compat.rules.query_adapter.spark_tpchavoc_rewrites
    from benchbox.sql_compat.actions import CompatAction
    from benchbox.sql_compat.context import CompatibilityContext, Phase
    from benchbox.sql_compat.decision import RewriteQueryPayload
    from benchbox.sql_compat.registry import REGISTRY

    ctx = CompatibilityContext(
        platform="spark",
        platform_version=None,
        benchmark="tpchavoc",
        query_id=variant_id,
        phase=Phase.QUERY_ADAPTER,
        mode="sql",
        dialect="spark",
    )
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, f"No rule for spark/tpchavoc/{variant_id}"
    assert decision.action is CompatAction.REWRITE_QUERY
    assert isinstance(decision.payload, RewriteQueryPayload)
    assert decision.payload.transformer_id in {
        "wrap_grouped_scalar_subqueries",
        "name_literal_subquery_columns",
        "translate_sql_query",
        "filtered_aggregates_to_conditional",
        "trim_char_case_comparisons",
    }


def test_spark_tpchavoc_unlisted_variant_has_no_rule():
    import benchbox.sql_compat.rules.query_adapter.spark_tpchavoc_rewrites
    from benchbox.sql_compat.context import CompatibilityContext, Phase
    from benchbox.sql_compat.registry import REGISTRY

    ctx = CompatibilityContext(
        platform="spark",
        platform_version=None,
        benchmark="tpchavoc",
        query_id="2_v1",
        phase=Phase.QUERY_ADAPTER,
        mode="sql",
        dialect="spark",
    )
    assert REGISTRY.resolve(ctx) is None


def _delegated_query(adapter, original, query_id, benchmark_type):
    from unittest.mock import patch

    with patch.object(
        type(adapter),
        "_execute_query_spark",
        return_value={"status": "ok"},
    ) as delegated:
        adapter.execute_query(
            object(),
            original,
            query_id,
            benchmark_type=benchmark_type,
        )
    sent_query = delegated.call_args.kwargs["query"]
    return sent_query


def test_spark_adapter_applies_transformer_for_tpchavoc():
    from benchbox.platforms.spark import SparkAdapter

    original = _variant_sql("1_v1")
    sent_query = _delegated_query(SparkAdapter(), original, "1_v1", "tpchavoc")
    assert "FIRST(" in sent_query.upper()


def test_execute_query_applies_transformer_without_benchmark_type():
    from benchbox.platforms.spark import SparkAdapter

    original = _variant_sql("1_v1")
    sent_query = _delegated_query(SparkAdapter(), original, "1_v1", None)
    assert "FIRST(" in sent_query.upper()


def test_spark_adapter_skips_transformer_for_other_benchmarks():
    from benchbox.platforms.spark import SparkAdapter

    original = _variant_sql("1_v1")
    sent_query = _delegated_query(SparkAdapter(), original, "1", "tpch")
    assert sent_query == original


def test_spark_adapter_skips_transformer_for_unknown_ids_without_benchmark():
    from benchbox.platforms.spark import SparkAdapter

    original = _variant_sql("2_v1")
    sent_query = _delegated_query(SparkAdapter(), original, "2_v1", None)
    assert sent_query == original


@pytest.mark.parametrize(
    "adapter_cls_name", ["benchbox.platforms.velox.VeloxAdapter", "benchbox.platforms.lakesail.LakeSailAdapter"]
)
def test_spark_family_adapters_share_mixin_execute_query(adapter_cls_name):
    import importlib

    from benchbox.platforms.base.spark_execution_mixin import SparkQueryExecutionMixin

    module_name, _, cls_name = adapter_cls_name.rpartition(".")
    adapter_cls = getattr(importlib.import_module(module_name), cls_name)
    assert adapter_cls.execute_query is SparkQueryExecutionMixin.execute_query
