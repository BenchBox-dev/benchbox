# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import patch

import pytest

from benchbox.cli.benchmarks import BenchmarkManager
from benchbox.core.schemas import BenchmarkConfig

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestBenchmarkFiltering:
    def test_filter_by_category(self):

        manager = BenchmarkManager()

        tpc_benchmarks = manager._filter_benchmarks(category="TPC")
        assert "tpch" in tpc_benchmarks
        assert "tpcds" in tpc_benchmarks
        assert "tpcdi" in tpc_benchmarks
        assert "ssb" not in tpc_benchmarks
        assert "clickbench" not in tpc_benchmarks

    def test_filter_by_search_term_name(self):

        manager = BenchmarkManager()

        tpc_results = manager._filter_benchmarks(search_term="TPC")
        assert "tpch" in tpc_results
        assert "tpcds" in tpc_results
        assert "tpcdi" in tpc_results

    def test_filter_by_search_term_description(self):

        manager = BenchmarkManager()

        decision_results = manager._filter_benchmarks(search_term="decision")
        assert "tpch" in decision_results
        assert "tpcds" in decision_results

    def test_filter_by_search_term_case_insensitive(self):

        manager = BenchmarkManager()

        lower_results = manager._filter_benchmarks(search_term="tpc")
        upper_results = manager._filter_benchmarks(search_term="TPC")
        mixed_results = manager._filter_benchmarks(search_term="TpC")

        assert lower_results == upper_results == mixed_results

    def test_filter_combined_category_and_search(self):

        manager = BenchmarkManager()

        results = manager._filter_benchmarks(category="Industry", search_term="click")
        assert "clickbench" in results
        assert "tpch" not in results
        assert "ssb" not in results

    def test_filter_no_results(self):

        manager = BenchmarkManager()

        results = manager._filter_benchmarks(search_term="nonexistent")
        assert len(results) == 0

    def test_filter_no_filters_returns_all(self):

        manager = BenchmarkManager()

        results = manager._filter_benchmarks()
        assert len(results) == len(manager._get_public_benchmarks())
        assert "joinorder_synthetic" in manager.benchmarks
        assert "joinorder_synthetic" not in results

    def test_filter_academic_excludes_internal_benchmarks(self):
        manager = BenchmarkManager()

        results = manager._filter_benchmarks(category="Academic")

        assert "joinorder" in results
        assert "ssb" in results
        assert "joinorder_synthetic" not in results


class TestBenchmarkDisplay:
    def test_list_available_benchmarks_prints_tree(self):
        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.console") as mock_console:
            manager.list_available_benchmarks()

        assert mock_console.print.called

    def test_display_all_benchmarks_structure(self):

        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.console"):
            displayed = manager._display_all_benchmarks()

        assert len(displayed) == len(manager._get_public_benchmarks())
        assert "joinorder_synthetic" not in displayed

        for bench_id, bench_info in displayed.items():
            assert "display_name" in bench_info
            assert "category" in bench_info
            assert "num_queries" in bench_info
            assert "complexity" in bench_info
            assert "estimated_time_range" in bench_info

    def test_display_with_category_filter(self):

        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.console"):
            displayed = manager._display_all_benchmarks(filter_category="TPC")

        assert "tpch" in displayed
        assert "tpcds" in displayed
        assert "clickbench" not in displayed

    def test_display_with_search_filter(self):

        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.console"):
            displayed = manager._display_all_benchmarks(search_term="Decision")

        assert len(displayed) > 0

    def test_display_empty_results(self):

        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.console") as mock_console:
            displayed = manager._display_all_benchmarks(search_term="xyz123nonexistent")

        assert len(displayed) == 0

        assert any("No benchmarks match" in str(call) for call in mock_console.print.call_args_list)


class TestBenchmarkPreview:
    def test_show_benchmark_preview(self):

        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.console") as mock_console:
            manager._show_benchmark_preview("tpch", manager.benchmarks["tpch"])

        assert mock_console.print.called
        assert len(mock_console.print.call_args_list) > 0

    def test_preview_includes_key_info(self):

        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.console") as mock_console:
            manager._show_benchmark_preview("tpcds", manager.benchmarks["tpcds"])

        assert mock_console.print.called
        from rich.panel import Panel

        panel_arg = mock_console.print.call_args[0][0]
        assert isinstance(panel_arg, Panel)

    def test_show_sample_queries_renders_loaded_queries(self):
        manager = BenchmarkManager()
        seen_scale = None

        class StubBenchmark:
            def __init__(self, scale_factor):
                nonlocal seen_scale
                seen_scale = scale_factor
                self.scale_factor = scale_factor
                self.queries = {
                    "Q1": "SELECT 1",
                    "Q2": "SELECT 2",
                }

        with (
            patch("benchbox.core.benchmark_loader.get_core_benchmark_class", return_value=StubBenchmark),
            patch("benchbox.cli.benchmarks.console") as mock_console,
        ):
            manager._show_sample_queries("joinorder", limit=1)

        printed = [str(call.args[0]) for call in mock_console.print.call_args_list if call.args]
        assert any("Sample Queries" in item for item in printed)
        assert any("Query Q1:" in item for item in printed)
        assert seen_scale == manager.benchmarks["joinorder"]["default_scale"]

    def test_show_sample_queries_without_queries_warns(self):
        manager = BenchmarkManager()

        class StubBenchmark:
            def __init__(self, scale_factor):
                self.scale_factor = scale_factor
                self.queries = {}

        with (
            patch("benchbox.core.benchmark_loader.get_core_benchmark_class", return_value=StubBenchmark),
            patch("benchbox.cli.benchmarks.console") as mock_console,
        ):
            manager._show_sample_queries("tpch")

        assert any("No queries available for preview" in str(call) for call in mock_console.print.call_args_list)

    def test_show_sample_queries_load_failure_warns(self):
        manager = BenchmarkManager()

        with (
            patch("benchbox.core.benchmark_loader.get_core_benchmark_class", side_effect=RuntimeError("boom")),
            patch("benchbox.cli.benchmarks.console") as mock_console,
        ):
            manager._show_sample_queries("tpch")

        assert any("Could not load sample queries" in str(call) for call in mock_console.print.call_args_list)


class TestBenchmarkSelectionIntegration:
    @patch("benchbox.cli.benchmarks.Prompt")
    def test_select_benchmark_direct_numeric(self, mock_prompt):

        manager = BenchmarkManager()

        mock_prompt.ask.return_value = "1"

        with patch.object(manager, "_configure_benchmark") as mock_config:
            mock_config.return_value = BenchmarkConfig(
                name="tpch",
                display_name="TPC-H",
                scale_factor=0.01,
                queries=None,
                concurrency=1,
                options={},
            )

            result = manager.select_benchmark()

        assert isinstance(result, BenchmarkConfig)
        assert result.name in manager.benchmarks

    @patch("benchbox.cli.benchmarks.Prompt")
    def test_select_benchmark_two_phase_tpc_category(self, mock_prompt):

        manager = BenchmarkManager()

        mock_prompt.ask.side_effect = ["1", "1"]

        with patch.object(manager, "_configure_benchmark") as mock_config:
            mock_config.return_value = BenchmarkConfig(
                name="tpch",
                display_name="TPC-H",
                scale_factor=0.01,
                queries=None,
                concurrency=1,
                options={},
            )

            result = manager.select_benchmark()

        assert isinstance(result, BenchmarkConfig)
        assert result.name == "tpch"

    @patch("benchbox.cli.benchmarks.Prompt")
    def test_select_benchmark_two_phase_primitives_category(self, mock_prompt):
        manager = BenchmarkManager()

        mock_prompt.ask.side_effect = ["2", "1"]

        with patch.object(manager, "_configure_benchmark") as mock_config:
            mock_config.return_value = BenchmarkConfig(
                name="read_primitives",
                display_name="Read Primitives",
                scale_factor=0.01,
                queries=None,
                concurrency=1,
                options={},
            )

            result = manager.select_benchmark()

        assert isinstance(result, BenchmarkConfig)
        assert result.name == "read_primitives"

    @patch("benchbox.cli.benchmarks.Prompt")
    def test_select_benchmark_two_phase_industry_category(self, mock_prompt):
        manager = BenchmarkManager()

        mock_prompt.ask.side_effect = ["3", "1"]

        with patch.object(manager, "_configure_benchmark") as mock_config:
            mock_config.return_value = BenchmarkConfig(
                name="clickbench",
                display_name="ClickBench",
                scale_factor=1.0,
                queries=None,
                concurrency=1,
                options={},
            )

            result = manager.select_benchmark()

        assert isinstance(result, BenchmarkConfig)
        assert result.name == "clickbench"

    @patch("benchbox.cli.benchmarks.Prompt")
    def test_select_benchmark_two_phase_experimental_category(self, mock_prompt):
        manager = BenchmarkManager()

        mock_prompt.ask.side_effect = ["7", "1"]

        with patch.object(manager, "_configure_benchmark") as mock_config:
            mock_config.return_value = BenchmarkConfig(
                name="tpch_skew",
                display_name="TPC-H Skew",
                scale_factor=0.01,
                queries=None,
                concurrency=1,
                options={},
            )

            result = manager.select_benchmark()

        assert isinstance(result, BenchmarkConfig)
        assert result.name == "tpch_skew"


class TestBenchmarkSelectionEdgeCases:
    def test_filter_with_empty_string(self):

        manager = BenchmarkManager()

        results = manager._filter_benchmarks(search_term="")
        assert len(results) == len(manager._get_public_benchmarks())

    def test_filter_with_whitespace(self):

        manager = BenchmarkManager()

        results = manager._filter_benchmarks(search_term="   ")
        assert isinstance(results, dict)

    def test_filter_with_special_characters(self):

        manager = BenchmarkManager()

        results = manager._filter_benchmarks(search_term="[]*?")
        assert isinstance(results, dict)

    def test_display_single_benchmark_after_filter(self):

        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.console"):
            displayed = manager._display_all_benchmarks(search_term="clickbench")

        assert "clickbench" in displayed


class TestBackwardsCompatibility:
    def test_old_display_benchmark_categories_still_works(self):
        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.console"):
            manager._display_benchmark_categories()

    def test_old_select_category_still_works(self):

        manager = BenchmarkManager()

        categories = ["TPC Standard", "Analytical"]

        with patch("benchbox.cli.benchmarks.Prompt") as mock_prompt:
            mock_prompt.ask.return_value = "1"

            result = manager._select_category(categories)

        assert result in categories

    def test_old_select_specific_benchmark_still_works(self):

        manager = BenchmarkManager()

        available = {"tpch": manager.benchmarks["tpch"], "tpcds": manager.benchmarks["tpcds"]}

        with patch("benchbox.cli.benchmarks.Prompt") as mock_prompt:
            mock_prompt.ask.return_value = "1"

            result = manager._select_specific_benchmark(available)

        assert result in available


class TestTwoPhaseSelectionFlow:
    def test_category_order_is_popularity_based(self):

        manager = BenchmarkManager()

        expected_order = [
            "TPC",
            "Primitives",
            "Industry",
            "Academic",
            "Time Series",
            "Real World",
            "AI/ML",
            "Experimental",
        ]
        assert expected_order == manager.CATEGORY_ORDER

    def test_benchmark_order_defined_for_all_categories(self):

        manager = BenchmarkManager()

        actual_categories = {info["category"] for info in manager.benchmarks.values()}

        for category in actual_categories:
            assert category in manager.BENCHMARK_ORDER, f"Missing BENCHMARK_ORDER for {category}"

    def test_tpc_benchmark_order(self):
        manager = BenchmarkManager()

        tpc_order = manager.BENCHMARK_ORDER["TPC"]
        assert tpc_order == ["tpch", "tpcds", "tpcdi"]

    def test_primitives_benchmark_order(self):

        manager = BenchmarkManager()

        primitives_order = manager.BENCHMARK_ORDER["Primitives"]
        assert primitives_order == [
            "read_primitives",
            "write_primitives",
            "transaction_primitives",
            "metadata_primitives",
            "ai_primitives",
        ]

    def test_prompt_category_selection_returns_category(self):

        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.Prompt") as mock_prompt:
            mock_prompt.ask.return_value = "1"

            result = manager._prompt_category_selection()

        assert result == "TPC"

    def test_prompt_category_selection_academic(self):
        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.Prompt") as mock_prompt:
            mock_prompt.ask.return_value = "4"

            result = manager._prompt_category_selection()

        assert result == "Academic"

    def test_prompt_benchmark_in_category_returns_tuple(self):
        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.Prompt") as mock_prompt:
            mock_prompt.ask.return_value = "1"

            bench_id, bench_info = manager._prompt_benchmark_in_category("TPC")

        assert bench_id == "tpch"
        assert bench_info["display_name"] == "TPC-H"

    def test_prompt_benchmark_in_category_second_choice(self):

        manager = BenchmarkManager()

        with patch("benchbox.cli.benchmarks.Prompt") as mock_prompt:
            mock_prompt.ask.return_value = "2"

            bench_id, bench_info = manager._prompt_benchmark_in_category("TPC")

        assert bench_id == "tpcds"
        assert bench_info["display_name"] == "TPC-DS"

    def test_benchmarks_sorted_by_popularity_then_alpha(self):

        manager = BenchmarkManager()

        category = "Industry"
        category_benchmarks = {
            bench_id: info for bench_id, info in manager.benchmarks.items() if info["category"] == category
        }

        popularity_order = manager.BENCHMARK_ORDER.get(category, [])

        def sort_key(item):
            bench_id, info = item
            try:
                position = popularity_order.index(bench_id)
            except ValueError:
                position = 999
            return (position, info["display_name"])

        sorted_benchmarks = sorted(category_benchmarks.items(), key=sort_key)

        assert sorted_benchmarks[0][0] == "clickbench"
        assert sorted_benchmarks[1][0] == "h2odb"
        assert sorted_benchmarks[2][0] == "coffeeshop"


