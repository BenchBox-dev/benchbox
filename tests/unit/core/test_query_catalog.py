# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.benchmark_registry import get_all_benchmarks
from benchbox.core.query_catalog import (
    REFERENCE_DIALECT,
    get_dataframe_query,
    get_dataframe_render,
    get_sql_render,
    list_query_ids,
    native_query_key,
    query_description,
    query_display_name,
    query_groups,
    query_source_path,
    supports_dialect_translation,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]

_ALL_BENCHMARK_IDS = sorted(get_all_benchmarks())


class TestListQueryIds:
    def test_unknown_benchmark_is_empty(self):
        assert list_query_ids("does-not-exist") == []

    @pytest.mark.parametrize("benchmark_id", _ALL_BENCHMARK_IDS)
    def test_every_registered_benchmark_has_queries(self, benchmark_id):
        ids = list_query_ids(benchmark_id)
        assert ids, f"{benchmark_id} exposed no query ids"
        assert len(ids) == len(set(ids)), f"{benchmark_id} has duplicate query ids"

    def test_order_is_stable(self):
        assert list_query_ids("tpch") == list_query_ids("tpch")

    def test_tpch_ids_are_the_22_queries(self):
        assert list_query_ids("tpch") == [str(n) for n in range(1, 23)]


class TestGetSqlRender:
    def test_unknown_benchmark_is_none(self):
        assert get_sql_render("does-not-exist", "1") is None

    def test_unknown_query_is_none(self):
        assert get_sql_render("tpch", "9999") is None

    def test_tpch_q1_translates_to_reference_dialect(self):
        render = get_sql_render("tpch", "1")
        assert render is not None
        assert render.dialect == REFERENCE_DIALECT
        assert not render.is_template
        assert "l_returnflag" in render.sql
        assert "SELECT" in render.sql

    def test_dialect_none_reports_default_and_skips_translation(self):
        render = get_sql_render("tpch", "1", dialect=None)
        assert render is not None
        assert render.dialect == "default"

    def test_benchmark_without_translation_is_labelled_default(self):
        render = get_sql_render("ai_primitives", list_query_ids("ai_primitives")[0])
        assert render is not None
        assert render.dialect == "default"

    def test_duckdb_source_benchmark_renders_reference_dialect(self):
        render = get_sql_render("datavault", "1")
        assert render is not None
        assert render.dialect == REFERENCE_DIALECT

    def test_clickbench_q_prefixed_key(self):
        render = get_sql_render("clickbench", "Q1")
        assert render is not None
        assert "hits" in render.sql.lower()

    def test_query_id_resolves_with_or_without_q_prefix(self):
        with_prefix = get_sql_render("ssb", "Q1.1", dialect="snowflake")
        without_prefix = get_sql_render("ssb", "1.1", dialect="snowflake")
        assert with_prefix is not None and without_prefix is not None
        assert with_prefix.sql == without_prefix.sql
        assert without_prefix.dialect == "snowflake"

    def test_vector_search_translates_for_datafusion(self):
        render = get_sql_render("vector_search", "Q1", dialect="datafusion")
        assert render is not None
        assert render.dialect == "datafusion"

    def test_vector_search_supported_dialect_returns_dialect(self):
        render = get_sql_render("vector_search", "Q1", dialect="postgresql")
        assert render is not None
        assert render.dialect == "postgresql"

    @pytest.mark.parametrize("benchmark_id", _ALL_BENCHMARK_IDS)
    def test_first_query_of_every_benchmark_renders(self, benchmark_id):
        first = list_query_ids(benchmark_id)[0]
        render = get_sql_render(benchmark_id, first)
        assert render is not None, f"{benchmark_id}:{first} did not render"
        assert render.sql.strip()
        assert not render.is_template, f"{benchmark_id}:{first} rendered as a raw template"


class TestGetDataframeRender:
    def test_tpch_q1_expression_source(self):
        render = get_dataframe_render("tpch", "1")
        assert render is not None
        assert render.family == "expression"
        assert "def q1_expression_impl" in render.source
        assert render.query_name == "Pricing Summary Report"

    def test_pandas_family_selectable(self):
        render = get_dataframe_render("tpch", "1", family="pandas")
        assert render is not None
        assert render.family == "pandas"
        assert "pandas" in render.source.lower() or "def q1_pandas_impl" in render.source

    def test_benchmark_without_dataframe_impl_is_none(self):
        assert get_dataframe_render("tpcdi", list_query_ids("tpcdi")[0]) is None

    def test_id_normalization_matches_q_prefixed_registry(self):
        assert get_dataframe_query("tpch", "6") is not None
        assert get_dataframe_query("tpch", "Q6") is not None


class TestQueryDisplayName:
    def test_tpch_uses_dataframe_query_name(self):
        assert query_display_name("tpch", "1") == "Pricing Summary Report"

    def test_get_query_info_name_wins(self):
        assert query_display_name("flightdata", "ontime-by-carrier") == "On-Time Rate by Carrier"

    def test_missing_name_is_none(self):
        assert query_display_name("does-not-exist", "1") is None

    def test_description_available_for_named_benchmarks(self):
        assert query_description("tpch", "1")
        assert query_description("flightdata", "ontime-by-carrier")


class TestQueryGroups:
    def test_benchmark_without_scheme_is_none(self):
        assert query_groups("tpch") is None
        assert query_groups("does-not-exist") is None

    def test_clickbench_dict_categories(self):
        groups = query_groups("clickbench")
        assert groups is not None
        flat = [q for members in groups.values() for q in members]
        assert sorted(flat) == sorted(list_query_ids("clickbench"))
        assert len(flat) == len(set(flat)), "a query landed in two groups"

    def test_list_categories_via_get_queries_by_category(self):
        groups = query_groups("read_primitives")
        assert groups is not None
        flat = {q for members in groups.values() for q in members}
        assert flat == set(list_query_ids("read_primitives"))

    def test_query_info_category_field(self):
        groups = query_groups("nyctaxi")
        assert groups is not None
        assert "temporal" in groups


class TestNativeQueryKey:
    def test_str_keyed_benchmark_returns_str(self):
        assert native_query_key("tpch", "6") == "6"
        assert isinstance(native_query_key("tpch", "6"), str)

    def test_int_keyed_benchmark_returns_int(self):
        assert native_query_key("datavault", "1") == 1
        assert isinstance(native_query_key("datavault", "1"), int)

    def test_q_prefixed_registry_still_resolves(self):
        assert native_query_key("clickbench", "Q1") == "Q1"


class TestDeterminism:
    @pytest.mark.parametrize("benchmark_id", ["nyctaxi", "tsbs_devops", "tpcds_obt"])
    def test_repeated_renders_are_identical(self, benchmark_id):
        import benchbox.core.query_catalog as qc

        def snapshot():
            return {q: get_sql_render(benchmark_id, q).sql for q in list_query_ids(benchmark_id)}

        first = snapshot()
        qc._benchmark_instance.cache_clear()
        qc._query_dict.cache_clear()
        qc._raw_query_keys.cache_clear()
        assert snapshot() == first


class TestSupportsDialectTranslation:
    def test_translating_and_non_translating_benchmarks(self):
        assert supports_dialect_translation("tpch") is True
        assert supports_dialect_translation("clickbench") is True
        assert supports_dialect_translation("flightdata") is True
        assert supports_dialect_translation("ai_primitives") is False
        assert supports_dialect_translation("transaction_primitives") is False

    def test_duckdb_source_benchmarks_advertise_every_dialect(self):
        for benchmark_id in ("datavault", "tsbs_devops", "flightdata"):
            for dialect in ("duckdb", "snowflake", "bigquery", "databricks", "datafusion", "mysql"):
                assert supports_dialect_translation(benchmark_id, dialect) is True

    def test_dialect_specific_support(self):
        assert supports_dialect_translation("vector_search", "postgresql") is True
        assert supports_dialect_translation("vector_search", "datafusion") is True
        assert supports_dialect_translation("ai_primitives", "postgresql") is False
        assert supports_dialect_translation("tpch", "duckdb") is True

    def test_nyctaxi_advertises_every_rendered_dialect(self):
        assert supports_dialect_translation("nyctaxi", "snowflake") is True
        assert supports_dialect_translation("nyctaxi", "bigquery") is True
        assert supports_dialect_translation("nyctaxi", "duckdb") is True
        assert supports_dialect_translation("nyctaxi", "postgres") is True
        assert supports_dialect_translation("nyctaxi", "clickhouse") is True
        assert supports_dialect_translation("nyctaxi", "mysql") is True
        assert supports_dialect_translation("nyctaxi", "datafusion") is True
        query_id = list_query_ids("nyctaxi")[0]
        assert get_sql_render("nyctaxi", query_id, dialect="mysql").dialect == "mysql"
        assert get_sql_render("nyctaxi", query_id, dialect="snowflake").dialect == "snowflake"


class TestQuerySourcePath:
    def test_tpch_points_at_template_file(self):
        assert query_source_path("tpch", "1") == "benchbox/_binaries/tpc-h/templates/queries/1.sql"

    def test_tpcds_points_at_template_file(self):
        assert query_source_path("tpcds", "1") == "_sources/tpc-ds/query_templates/query1.tpl"

    def test_generic_benchmark_points_at_queries_module(self):
        assert query_source_path("clickbench", "Q1") == "benchbox/core/clickbench/queries.py"

    def test_tpcdi_query_submodule_resolution(self):
        assert query_source_path("tpcdi", "AQ1") == "benchbox/core/tpcdi/query_analytics.py"
        assert query_source_path("tpcdi", "EQ1") == "benchbox/core/tpcdi/query_etl.py"
        assert query_source_path("tpcdi", "VQ1") == "benchbox/core/tpcdi/query_validation.py"

    def test_tpchavoc_variant_set_resolution(self):
        assert query_source_path("tpchavoc", "1_v1") == "benchbox/core/tpchavoc/variant_sets/q01.py"

    @pytest.mark.parametrize("benchmark_id", _ALL_BENCHMARK_IDS)
    def test_path_when_present_exists_on_disk(self, benchmark_id):
        from benchbox.core.query_catalog import _REPO_ROOT

        rel = query_source_path(benchmark_id, list_query_ids(benchmark_id)[0])
        if rel is not None:
            assert (_REPO_ROOT / rel).is_file()
