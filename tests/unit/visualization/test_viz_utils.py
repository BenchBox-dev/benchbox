from __future__ import annotations

import pytest

from benchbox.core.visualization.utils import (
    build_chart_subtitle,
    extract_chart_subtitle,
    natural_query_sort_key,
    slugify,
)
from tests.fixtures.result_dict_fixtures import make_normalized_result

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestBuildChartSubtitle:
    def test_empty_returns_none(self):
        assert build_chart_subtitle() is None

    def test_benchmark_only(self):
        assert build_chart_subtitle(benchmark="tpch") == "TPCH"

    def test_scale_factor_formatted(self):
        subtitle = build_chart_subtitle(scale_factor=0.01)
        assert subtitle == "SF=sf001"

    def test_full_subtitle(self):
        subtitle = build_chart_subtitle(
            benchmark="tpch", scale_factor=1, platform_version="DuckDB 1.4.3", tuning="tuned"
        )
        assert subtitle == "TPCH | SF=sf1 | DuckDB 1.4.3 | tuned"


class TestExtractChartSubtitle:
    def test_empty_results_returns_none(self):
        assert extract_chart_subtitle([]) is None

    def test_basic_fields_extracted(self):
        r = make_normalized_result(platform="DuckDB", platform_version="1.4.3")
        subtitle = extract_chart_subtitle([r])
        assert "TPCH" in subtitle
        assert "SF=" in subtitle

    def test_version_appended_to_platform_name(self):

        r = make_normalized_result(platform="DuckDB", platform_version="1.4.3")
        subtitle = extract_chart_subtitle([r])
        assert "DuckDB 1.4.3" in subtitle

    def test_no_duplicate_when_version_already_in_label(self):

        r = make_normalized_result(platform="DuckDB 1.4.3", platform_version="1.4.3")
        subtitle = extract_chart_subtitle([r])
        assert "DuckDB 1.4.3" in subtitle
        assert "DuckDB 1.4.3 1.4.3" not in subtitle

    def test_no_platform_version_when_no_raw_version(self):

        r = make_normalized_result(platform="DuckDB")
        subtitle = extract_chart_subtitle([r])
        assert "DuckDB" not in subtitle

    def test_uses_first_result_for_subtitle(self):

        r1 = make_normalized_result(platform="DuckDB", platform_version="1.0.0", scale_factor=1.0)
        r2 = make_normalized_result(platform="Polars", platform_version="0.19.0", benchmark="tpcds", scale_factor=10.0)
        subtitle = extract_chart_subtitle([r1, r2])
        assert "TPCH" in subtitle
        assert "DuckDB" in subtitle


class TestSlugify:
    def test_basic_text(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_characters(self):
        assert slugify("foo@bar#baz") == "foo-bar-baz"

    def test_multiple_spaces(self):
        assert slugify("foo   bar") == "foo-bar"

    def test_leading_trailing_spaces(self):
        assert slugify("  hello  ") == "hello"

    def test_preserves_hyphens(self):
        assert slugify("hello-world") == "hello-world"

    def test_preserves_underscores(self):
        assert slugify("hello_world") == "hello_world"

    def test_collapses_multiple_hyphens(self):
        assert slugify("foo--bar---baz") == "foo-bar-baz"

    def test_empty_string_returns_untitled(self):
        assert slugify("") == "untitled"

    def test_only_special_chars_returns_untitled(self):
        assert slugify("@#$%") == "untitled"

    def test_mixed_case(self):
        assert slugify("HelloWorld") == "helloworld"

    def test_numbers_preserved(self):
        assert slugify("test123") == "test123"
        assert slugify("123test") == "123test"

    def test_unicode_characters(self):

        assert slugify("café") == "café"

    def test_platform_names(self):
        assert slugify("DuckDB") == "duckdb"

        assert slugify("Snowflake (Enterprise)") == "snowflake-enterprise"
        assert slugify("BigQuery") == "bigquery"


class TestNaturalQuerySortKey:
    def test_prefixed_ids_sort_by_number(self):
        assert sorted(["Q10", "Q1", "Q2"], key=natural_query_sort_key) == ["Q1", "Q2", "Q10"]

    def test_bare_numeric_ids_sort_by_number(self):
        assert sorted(["14", "2", "9"], key=natural_query_sort_key) == ["2", "9", "14"]

    def test_suffix_text_is_tie_breaker(self):
        assert sorted(["Q14b", "Q14", "Q14a"], key=natural_query_sort_key) == ["Q14", "Q14a", "Q14b"]

    def test_nonnumeric_ids_sort_after_numeric_ids(self):
        assert sorted(["setup", "Q2", "Q1"], key=natural_query_sort_key) == ["Q1", "Q2", "setup"]

    def test_result_plotter_wrapper_delegates_to_shared_key(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        assert ResultPlotter._natural_sort_key("Q10") == natural_query_sort_key("Q10")


