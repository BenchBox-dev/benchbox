from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.visualization.ascii.bar_chart import BarChart, BarData
from benchbox.core.visualization.ascii.base import (
    ChartOptions,
    ColorMode,
)
from benchbox.core.visualization.ascii.box_plot import BoxPlot, BoxPlotSeries
from benchbox.core.visualization.ascii.cdf_chart import CDFChart, CDFSeriesData
from benchbox.core.visualization.ascii.heatmap import Heatmap
from benchbox.core.visualization.ascii.histogram import Histogram, HistogramBar
from benchbox.core.visualization.ascii.line_chart import LineChart
from benchbox.core.visualization.ascii.normalized_speedup import (
    NormalizedSpeedup,
    SpeedupData,
)
from benchbox.core.visualization.ascii.percentile_ladder import (
    PercentileData,
    PercentileLadder,
)
from benchbox.core.visualization.ascii.rank_table import RankTable, RankTableData
from benchbox.core.visualization.ascii.scatter_plot import ScatterPlot
from benchbox.core.visualization.ascii.sparkline_table import (
    SparklineColumn,
    SparklineTable,
    SparklineTableData,
)
from benchbox.core.visualization.ascii.stacked_bar import StackedBar, StackedBarData, StackedBarSegment
from tests.fixtures.result_dict_fixtures import make_normalized_result

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestComparisonTemplateIntegration:
    def test_exporters_render_comparison_bar(self):
        from benchbox.core.visualization.ascii.comparison_bar import ComparisonBarData
        from benchbox.core.visualization.exporters import render_ascii_chart

        data = [ComparisonBarData(label="Q1", baseline_value=100, comparison_value=75)]
        result = render_ascii_chart("comparison_bar", data, title="Test")
        assert "Q1" in result

    def test_exporters_render_diverging_bar(self):
        from benchbox.core.visualization.ascii.diverging_bar import DivergingBarData
        from benchbox.core.visualization.exporters import render_ascii_chart

        data = [DivergingBarData(label="Q1", pct_change=-25.0)]
        result = render_ascii_chart("diverging_bar", data, title="Test")
        assert "Q1" in result

    def test_exporters_render_summary_box(self):
        from benchbox.core.visualization.ascii.summary_box import SummaryStats
        from benchbox.core.visualization.exporters import render_ascii_chart

        stats = SummaryStats(
            title="Test Summary",
            primary_value=100,
            primary_label="Geo Mean",
            secondary_label="Median",
            total_label="Total",
            count_label="Queries",
        )
        result = render_ascii_chart("summary_box", stats)
        assert "Test Summary" in result

    def test_exporters_render_summary_box_from_dict(self):
        from benchbox.core.visualization.exporters import render_ascii_chart

        data = {
            "title": "Dict Summary",
            "primary_value": 100,
            "num_items": 5,
            "primary_label": "Geo Mean",
            "secondary_label": "Median",
            "total_label": "Total",
            "count_label": "Queries",
        }
        result = render_ascii_chart("summary_box", data)
        assert "Dict Summary" in result

    def test_comparison_template_exists(self):
        from benchbox.core.visualization.templates import get_template

        template = get_template("comparison")
        assert template.name == "comparison"
        assert "comparison_bar" in template.chart_types
        assert "diverging_bar" in template.chart_types
        assert "summary_box" in template.chart_types

    def test_new_classes_importable_from_module(self):
        from benchbox.core.visualization.ascii import ComparisonBar, DivergingBar, SummaryBox

        assert ComparisonBar is not None
        assert DivergingBar is not None
        assert SummaryBox is not None


class TestSeriesNamingSymmetry:
    def test_disambiguate_modes_both_get_suffix(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        results = [
            make_normalized_result(platform="DataFusion", execution_mode="sql"),
            make_normalized_result(platform="DataFusion", execution_mode="dataframe"),
        ]
        plotter = ResultPlotter(results)

        platforms = [r.platform for r in plotter.results]
        assert "DataFusion (sql)" in platforms
        assert "DataFusion (df)" in platforms

    def test_single_result_no_suffix(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        results = [make_normalized_result(platform="DuckDB", execution_mode="sql")]
        plotter = ResultPlotter(results)

        assert plotter.results[0].platform == "DuckDB"

    def test_same_mode_no_suffix(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        results = [
            make_normalized_result(platform="DuckDB", execution_mode="sql"),
            make_normalized_result(platform="Polars", execution_mode="sql"),
        ]
        plotter = ResultPlotter(results)

        platforms = [r.platform for r in plotter.results]
        assert "DuckDB" in platforms
        assert "Polars" in platforms

    def test_mode_abbreviation_dataframe_to_df(self):
        from benchbox.core.labels import _MODE_ABBREV

        assert _MODE_ABBREV["dataframe"] == "df"
        assert _MODE_ABBREV.get("sql", "sql") == "sql"


class TestDisambiguateVersions:
    def test_different_versions_appended_to_label(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        results = [
            make_normalized_result(platform="DuckDB", platform_version="1.0.0"),
            make_normalized_result(platform="DuckDB", platform_version="1.4.3"),
        ]
        plotter = ResultPlotter(results)

        platforms = {r.platform for r in plotter.results}
        assert "DuckDB v1.0.0" in platforms
        assert "DuckDB v1.4.3" in platforms

    def test_same_version_labels_unchanged(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        results = [
            make_normalized_result(platform="DuckDB", platform_version="1.4.3"),
            make_normalized_result(platform="DuckDB", platform_version="1.4.3"),
        ]
        plotter = ResultPlotter(results)

        for r in plotter.results:
            assert r.platform == "DuckDB"

    def test_different_platforms_unaffected(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        results = [
            make_normalized_result(platform="DuckDB", platform_version="1.0.0"),
            make_normalized_result(platform="Polars", platform_version="0.19.0"),
        ]
        plotter = ResultPlotter(results)

        platforms = [r.platform for r in plotter.results]
        assert "DuckDB" in platforms
        assert "Polars" in platforms

    def test_no_version_in_raw_skipped(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        results = [
            make_normalized_result(platform="DuckDB"),
            make_normalized_result(platform="DuckDB"),
        ]
        plotter = ResultPlotter(results)

        for r in plotter.results:
            assert r.platform == "DuckDB"


class TestSortResultsByVersion:
    def test_versions_sorted_ascending(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        results = [
            make_normalized_result(platform="DuckDB 1.3.2"),
            make_normalized_result(platform="DuckDB 1.0.0"),
            make_normalized_result(platform="DuckDB 1.2.2"),
            make_normalized_result(platform="DuckDB 1.1.3"),
        ]
        plotter = ResultPlotter.__new__(ResultPlotter)
        plotter.results = results
        plotter._sort_results_by_version()

        ordered = [r.platform for r in plotter.results]
        assert ordered == ["DuckDB 1.0.0", "DuckDB 1.1.3", "DuckDB 1.2.2", "DuckDB 1.3.2"]

    def test_prerelease_sorts_after_stable(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        results = [
            make_normalized_result(platform="DuckDB 1.5.0-dev7404"),
            make_normalized_result(platform="DuckDB 1.4.4"),
        ]
        plotter = ResultPlotter.__new__(ResultPlotter)
        plotter.results = results
        plotter._sort_results_by_version()

        ordered = [r.platform for r in plotter.results]
        assert ordered == ["DuckDB 1.4.4", "DuckDB 1.5.0-dev7404"]

    def test_six_versions_full_order(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        insert_order = [
            "DuckDB 1.1.3",
            "DuckDB 1.0.0",
            "DuckDB 1.2.2",
            "DuckDB 1.3.2",
            "DuckDB 1.4.4",
            "DuckDB 1.5.0-dev7404",
        ]
        expected = [
            "DuckDB 1.0.0",
            "DuckDB 1.1.3",
            "DuckDB 1.2.2",
            "DuckDB 1.3.2",
            "DuckDB 1.4.4",
            "DuckDB 1.5.0-dev7404",
        ]
        plotter = ResultPlotter.__new__(ResultPlotter)
        plotter.results = [make_normalized_result(platform=p) for p in insert_order]
        plotter._sort_results_by_version()

        assert [r.platform for r in plotter.results] == expected

    def test_non_versioned_labels_sort_last(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        results = [
            make_normalized_result(platform="Polars"),
            make_normalized_result(platform="DuckDB 1.0.0"),
        ]
        plotter = ResultPlotter.__new__(ResultPlotter)
        plotter.results = results
        plotter._sort_results_by_version()

        assert plotter.results[0].platform == "DuckDB 1.0.0"
        assert plotter.results[1].platform == "Polars"

    def test_single_result_unchanged(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        results = [make_normalized_result(platform="DuckDB 1.0.0")]
        plotter = ResultPlotter.__new__(ResultPlotter)
        plotter.results = results
        plotter._sort_results_by_version()

        assert plotter.results[0].platform == "DuckDB 1.0.0"


class TestNewChartsModuleImports:
    def test_import_all_new_charts(self):
        from benchbox.core.visualization.ascii import (
            CDFChart,
            NormalizedSpeedup,
            RankTable,
            SparklineTable,
            StackedBar,
        )

        assert CDFChart is not None
        assert NormalizedSpeedup is not None
        assert RankTable is not None
        assert SparklineTable is not None
        assert StackedBar is not None


class TestHeatmapQueryOrdering:
    def test_query_ids_sorted_numerically(self):
        from types import SimpleNamespace

        from benchbox.core.visualization.ascii_runtime import _build_query_matrix

        queries = [
            SimpleNamespace(query_id="14", execution_time_ms=100),
            SimpleNamespace(query_id="2", execution_time_ms=200),
            SimpleNamespace(query_id="9", execution_time_ms=150),
        ]
        result = SimpleNamespace(platform="DuckDB", queries=queries)
        _, query_ids, _ = _build_query_matrix([result])
        assert query_ids == ["2", "9", "14"]

    def test_query_ids_with_prefix_sorted(self):
        from types import SimpleNamespace

        from benchbox.core.visualization.ascii_runtime import _build_query_matrix

        queries = [
            SimpleNamespace(query_id="Q10", execution_time_ms=10),
            SimpleNamespace(query_id="Q2", execution_time_ms=20),
            SimpleNamespace(query_id="Q1", execution_time_ms=30),
        ]
        result = SimpleNamespace(platform="DuckDB", queries=queries)
        _, query_ids, _ = _build_query_matrix([result])
        assert query_ids == ["Q1", "Q2", "Q10"]


class TestPowerBarRenderer:
    def test_renders_with_valid_power_data(self):
        from benchbox.core.visualization.ascii.base import ChartOptions
        from benchbox.core.visualization.ascii_runtime import render_ascii_chart_from_results

        results = [
            make_normalized_result(platform="DuckDB 1.0.0", benchmark="tpcds", scale_factor=10, power_at_size=385807.0),
            make_normalized_result(platform="DuckDB 1.5.0", benchmark="tpcds", scale_factor=10, power_at_size=669328.0),
        ]
        opts = ChartOptions(use_color=False)
        output = render_ascii_chart_from_results(results, "power_bar", opts, {})
        assert output is not None
        assert "DuckDB 1.0.0" in output
        assert "DuckDB 1.5.0" in output
        assert "Power@Size" in output

    def test_returns_none_when_no_power_data(self):
        from benchbox.core.visualization.ascii.base import ChartOptions
        from benchbox.core.visualization.ascii_runtime import render_ascii_chart_from_results

        results = [
            make_normalized_result(platform="DuckDB", benchmark="tpcds", scale_factor=10),
            make_normalized_result(platform="Polars", benchmark="tpcds", scale_factor=10),
        ]
        opts = ChartOptions(use_color=False)
        output = render_ascii_chart_from_results(results, "power_bar", opts, {})
        assert output is None

    def test_best_is_highest_value(self):
        from benchbox.core.visualization.ascii.bar_chart import BarData
        from benchbox.core.visualization.ascii.base import ChartOptions
        from benchbox.core.visualization.ascii_runtime import _render_power_bar

        captured: list[BarData] = []

        class _CapturingBarChart:
            def __init__(self, data, **kwargs):
                captured.extend(data)

            def render(self):
                return ""

        import benchbox.core.visualization.ascii_runtime as runtime

        original = runtime.BarChart
        runtime.BarChart = _CapturingBarChart
        try:
            results = [
                make_normalized_result(platform="Slow", benchmark="tpcds", scale_factor=10, power_at_size=100.0),
                make_normalized_result(platform="Fast", benchmark="tpcds", scale_factor=10, power_at_size=900.0),
                make_normalized_result(platform="Mid", benchmark="tpcds", scale_factor=10, power_at_size=500.0),
            ]
            _render_power_bar(results, ChartOptions(use_color=False), {})
        finally:
            runtime.BarChart = original

        best = [d for d in captured if d.is_best]
        worst = [d for d in captured if d.is_worst]
        assert len(best) == 1 and best[0].label == "Fast"
        assert len(worst) == 1 and worst[0].label == "Slow"

    def test_single_result_no_worst(self):
        from benchbox.core.visualization.ascii.bar_chart import BarData
        from benchbox.core.visualization.ascii.base import ChartOptions
        from benchbox.core.visualization.ascii_runtime import _render_power_bar

        captured: list[BarData] = []

        class _CapturingBarChart:
            def __init__(self, data, **kwargs):
                captured.extend(data)

            def render(self):
                return ""

        import benchbox.core.visualization.ascii_runtime as runtime

        original = runtime.BarChart
        runtime.BarChart = _CapturingBarChart
        try:
            _render_power_bar(
                [make_normalized_result(platform="Only", benchmark="tpcds", scale_factor=10, power_at_size=500.0)],
                ChartOptions(use_color=False),
                {},
            )
        finally:
            runtime.BarChart = original

        assert captured[0].is_best is True
        assert not any(d.is_worst for d in captured)

    def test_mixed_results_only_power_data_rendered(self):
        from benchbox.core.visualization.ascii.base import ChartOptions
        from benchbox.core.visualization.ascii_runtime import render_ascii_chart_from_results

        results = [
            make_normalized_result(platform="HasPower", benchmark="tpcds", scale_factor=10, power_at_size=500.0),
            make_normalized_result(platform="NoPower", benchmark="tpcds", scale_factor=10),
        ]
        opts = ChartOptions(use_color=False)
        output = render_ascii_chart_from_results(results, "power_bar", opts, {})
        assert output is not None
        assert "HasPower" in output
        assert "NoPower" not in output


class TestNormalizeDictPowerAtSize:
    @staticmethod
    def _make_payload(power_at_size=None):
        payload = {
            "benchmark": {"name": "tpcds", "scale_factor": 10},
            "execution": {"platform": "duckdb"},
            "results": {"timing": {"total_ms": 5000}},
        }
        if power_at_size is not None:
            payload["summary"] = {"tpc_metrics": {"power_at_size": power_at_size}}
        return payload

    def test_extracts_power_at_size_from_tpc_metrics(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        result = ResultPlotter._normalize_dict(self._make_payload(power_at_size=385807.0), source_path=None)
        assert result.power_at_size == pytest.approx(385807.0)

    def test_power_at_size_none_when_absent(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        result = ResultPlotter._normalize_dict(self._make_payload(), source_path=None)
        assert result.power_at_size is None

    def test_power_at_size_coerced_to_float(self):
        from benchbox.core.visualization.result_plotter import ResultPlotter

        result = ResultPlotter._normalize_dict(self._make_payload(power_at_size=385807), source_path=None)
        assert isinstance(result.power_at_size, float)


class TestSuggestChartTypesPowerBar:
    @staticmethod
    def _make_plotter(power_values: list[float | None]):
        from benchbox.core.visualization.result_plotter import NormalizedResult, ResultPlotter

        results = [
            NormalizedResult(
                benchmark="tpcds",
                platform=f"platform_{i}",
                scale_factor=10,
                execution_id=None,
                timestamp=None,
                total_time_ms=1000.0,
                avg_time_ms=None,
                success_rate=None,
                cost_total=None,
                power_at_size=v,
            )
            for i, v in enumerate(power_values)
        ]
        plotter = ResultPlotter.__new__(ResultPlotter)
        plotter.results = results
        return plotter

    def test_power_bar_suggested_when_power_data_present(self):
        plotter = self._make_plotter([385807.0, 669328.0])
        assert "power_bar" in plotter._suggest_chart_types()

    def test_power_bar_not_suggested_without_power_data(self):
        plotter = self._make_plotter([None, None])
        assert "power_bar" not in plotter._suggest_chart_types()

    def test_power_bar_suggested_when_at_least_one_has_power(self):
        plotter = self._make_plotter([None, 500.0])
        assert "power_bar" in plotter._suggest_chart_types()


class TestExporterChartTypeDispatch:
    def test_exporters_render_percentile_ladder(self):
        from benchbox.core.visualization.exporters import render_ascii_chart

        data = [PercentileData("DuckDB", 12, 45, 78, 120)]
        result = render_ascii_chart("percentile_ladder", data, title="Test")
        assert "DuckDB" in result

    def test_percentile_ladder_importable_from_module(self):
        from benchbox.core.visualization.ascii import PercentileLadder

        assert callable(PercentileLadder)

    def test_exporters_render_normalized_speedup(self):
        from benchbox.core.visualization.exporters import render_ascii_chart

        data = [SpeedupData("Test", 2.0, False)]
        result = render_ascii_chart("normalized_speedup", data, title="Test")
        assert "Test" in result

    def test_exporters_render_stacked_phase(self):
        from benchbox.core.visualization.exporters import render_ascii_chart

        data = [StackedBarData("Test", [StackedBarSegment("Power", 5000)])]
        result = render_ascii_chart("stacked_phase", data, title="Test")
        assert "Test" in result

    def test_exporters_render_sparkline_table(self):
        from benchbox.core.visualization.exporters import render_ascii_chart

        cols = [SparklineColumn("Test", {"A": 1}, False)]
        data = SparklineTableData(rows=["A"], columns=cols)
        result = render_ascii_chart("sparkline_table", data, title="Test")
        assert "A" in result

    def test_exporters_render_cdf_chart(self):
        from benchbox.core.visualization.exporters import render_ascii_chart

        data = [CDFSeriesData("Test", [10, 20, 30])]
        result = render_ascii_chart("cdf_chart", data, title="Test")
        assert "Test" in result

    def test_exporters_render_rank_table(self):
        from benchbox.core.visualization.exporters import render_ascii_chart

        data = RankTableData(
            items=["Q1"],
            groups=["A", "B"],
            values={("A", "Q1"): 10, ("B", "Q1"): 20},
        )
        result = render_ascii_chart("rank_table", data, title="Test")
        assert "A" in result


class TestSubtitleIntegration:
    def test_subtitle_renders_scale_factor(self):
        from benchbox.core.visualization.utils import build_chart_subtitle

        data = [BarData(label="Q1", value=100)]
        opts = ChartOptions(use_color=False)
        subtitle = build_chart_subtitle(scale_factor=1)
        chart = BarChart(data=data, options=opts, subtitle=subtitle)
        result = chart.render()
        assert "SF=sf1" in result

    def test_subtitle_in_all_chart_types(self):
        from benchbox.core.visualization.utils import build_chart_subtitle

        subtitle = build_chart_subtitle(scale_factor=0.01)
        opts = ChartOptions(use_color=False)

        hist_data = [HistogramBar(label="Q1", value=100)]
        h = Histogram(data=hist_data, y_label="Execution Time (ms)", options=opts, subtitle=subtitle)
        assert "SF=sf001" in h.render()

        bp = BoxPlot(
            series=[BoxPlotSeries(name="X", values=[1, 2, 3])],
            options=opts,
            subtitle=subtitle,
        )
        assert "SF=sf001" in bp.render()

        hm = Heatmap(
            matrix=[[1, 2], [3, 4]],
            row_labels=["Q1", "Q2"],
            col_labels=["A", "B"],
            value_label="ms",
            options=opts,
            subtitle=subtitle,
        )
        assert "SF=sf001" in hm.render()


class TestPercentileLadderSubtitleIntegration:
    def test_subtitle_renders(self):
        from benchbox.core.visualization.utils import build_chart_subtitle

        data = [PercentileData("Test", 10, 20, 30, 40)]
        opts = ChartOptions(use_color=False)
        subtitle = build_chart_subtitle(scale_factor=1)
        chart = PercentileLadder(data=data, metric_label="ms", options=opts, subtitle=subtitle)
        result = chart.render()
        assert "SF=sf1" in result


class TestShimImports:
    def test_bar_chart_importable(self):
        from benchbox.core.visualization.ascii.bar_chart import BarChart, BarData

        assert callable(BarChart)
        assert callable(BarData)

    def test_all_chart_types_importable(self):
        from benchbox.core.visualization.ascii import (
            BarChart,
            BoxPlot,
            CDFChart,
            ComparisonBar,
            DivergingBar,
            Heatmap,
            Histogram,
            LineChart,
            NormalizedSpeedup,
            PercentileLadder,
            RankTable,
            ScatterPlot,
            SparklineTable,
            StackedBar,
            SummaryBox,
        )

        assert all(
            cls is not None
            for cls in [
                BarChart,
                BoxPlot,
                CDFChart,
                ComparisonBar,
                DivergingBar,
                Heatmap,
                LineChart,
                NormalizedSpeedup,
                PercentileLadder,
                Histogram,
                RankTable,
                ScatterPlot,
                SparklineTable,
                StackedBar,
                SummaryBox,
            ]
        )

    def test_options_importable(self):
        from benchbox.core.visualization.ascii.base import ChartOptions, ColorMode

        assert ChartOptions is not None
        assert ColorMode is not None


