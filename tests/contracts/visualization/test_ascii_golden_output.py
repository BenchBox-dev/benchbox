from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.visualization.ascii.bar_chart import BarChart, BarData
from benchbox.core.visualization.ascii.base import ChartOptions, TerminalCapabilities
from benchbox.core.visualization.ascii.box_plot import BoxPlot, BoxPlotSeries
from benchbox.core.visualization.ascii.cdf_chart import CDFChart, CDFSeriesData
from benchbox.core.visualization.ascii.comparison_bar import ComparisonBar, ComparisonBarData
from benchbox.core.visualization.ascii.diverging_bar import DivergingBar, DivergingBarData
from benchbox.core.visualization.ascii.heatmap import Heatmap
from benchbox.core.visualization.ascii.histogram import Histogram, HistogramBar
from benchbox.core.visualization.ascii.line_chart import LineChart, LinePoint
from benchbox.core.visualization.ascii.normalized_speedup import NormalizedSpeedup, SpeedupData
from benchbox.core.visualization.ascii.percentile_ladder import PercentileData, PercentileLadder
from benchbox.core.visualization.ascii.rank_table import RankTable, RankTableData
from benchbox.core.visualization.ascii.scatter_plot import ScatterPlot, ScatterPoint
from benchbox.core.visualization.ascii.sparkline_table import (
    SparklineColumn,
    SparklineTable,
    SparklineTableData,
)
from benchbox.core.visualization.ascii.stacked_bar import StackedBar, StackedBarData, StackedBarSegment
from benchbox.core.visualization.ascii.summary_box import SummaryBox, SummaryStats

pytestmark = pytest.mark.medium


GOLDEN_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures" / "golden" / "ascii"

OPTS = ChartOptions(use_color=False, use_unicode=True, width=80)

_DETERMINISTIC_CAPS = TerminalCapabilities(width=80, unicode_support=True)


@pytest.fixture(autouse=True)
def _force_unicode_capabilities():
    with patch(
        "textcharts.base.detect_terminal_capabilities",
        return_value=_DETERMINISTIC_CAPS,
    ):
        yield


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden", default=False))


def _bar_chart() -> tuple[str, str]:
    data = [
        BarData(label="DuckDB", value=1234.5, is_best=True),
        BarData(label="SQLite", value=3456.7, is_worst=True),
        BarData(label="Polars", value=2100.0),
    ]
    chart = BarChart(data=data, title="Total Runtime", metric_label="ms", options=OPTS)
    return "bar_chart", chart.render()


def _histogram() -> tuple[str, str]:
    bars = [
        HistogramBar(label="Q1", value=120.5, is_best=True),
        HistogramBar(label="Q2", value=340.2),
        HistogramBar(label="Q3", value=89.1),
        HistogramBar(label="Q4", value=567.8, is_worst=True),
        HistogramBar(label="Q5", value=210.0),
        HistogramBar(label="Q6", value=150.3),
    ]
    chart = Histogram(data=bars, title="Query Latency", y_label="Execution Time (ms)", options=OPTS)
    return "histogram", chart.render()


def _heatmap() -> tuple[str, str]:
    matrix = [
        [120.0, 150.0, 200.0],
        [340.0, 280.0, 310.0],
        [89.0, 95.0, 110.0],
        [560.0, 480.0, 520.0],
    ]
    chart = Heatmap(
        matrix=matrix,
        row_labels=["Q1", "Q2", "Q3", "Q4"],
        col_labels=["DuckDB", "SQLite", "Polars"],
        title="Query Heatmap",
        value_label="ms",
        options=OPTS,
    )
    return "heatmap", chart.render()


def _box_plot() -> tuple[str, str]:
    series = [
        BoxPlotSeries(name="DuckDB", values=[80, 95, 110, 130, 150, 200, 250, 300, 500]),
        BoxPlotSeries(name="SQLite", values=[200, 250, 300, 350, 400, 450, 500, 600, 800]),
        BoxPlotSeries(name="Polars", values=[100, 120, 140, 160, 180, 200, 220, 280, 350]),
    ]
    chart = BoxPlot(series=series, title="Query Time Distribution", options=OPTS)
    return "box_plot", chart.render()


def _line_chart() -> tuple[str, str]:
    points = [
        LinePoint(series="DuckDB", x=1, y=120.0, label="Run 1"),
        LinePoint(series="DuckDB", x=2, y=115.0, label="Run 2"),
        LinePoint(series="DuckDB", x=3, y=110.0, label="Run 3"),
        LinePoint(series="SQLite", x=1, y=340.0, label="Run 1"),
        LinePoint(series="SQLite", x=2, y=320.0, label="Run 2"),
        LinePoint(series="SQLite", x=3, y=310.0, label="Run 3"),
    ]
    chart = LineChart(points=points, title="Performance Trend", options=OPTS)
    return "line_chart", chart.render()


def _scatter_plot() -> tuple[str, str]:
    points = [
        ScatterPoint(name="DuckDB", x=0.05, y=1234.5),
        ScatterPoint(name="SQLite", x=0.00, y=3456.7),
        ScatterPoint(name="Snowflake", x=2.50, y=890.0),
        ScatterPoint(name="Databricks", x=5.00, y=650.0),
    ]
    chart = ScatterPlot(
        points=points,
        title="Cost vs Performance",
        x_label="Cost (USD)",
        y_label="Performance",
        options=OPTS,
    )
    return "scatter_plot", chart.render()


def _comparison_bar() -> tuple[str, str]:
    data = [
        ComparisonBarData(
            label="Q1", baseline_value=120.0, comparison_value=95.0, baseline_name="v1.0", comparison_name="v1.1"
        ),
        ComparisonBarData(
            label="Q2", baseline_value=340.0, comparison_value=380.0, baseline_name="v1.0", comparison_name="v1.1"
        ),
        ComparisonBarData(
            label="Q3", baseline_value=89.0, comparison_value=72.0, baseline_name="v1.0", comparison_name="v1.1"
        ),
        ComparisonBarData(
            label="Q4", baseline_value=560.0, comparison_value=540.0, baseline_name="v1.0", comparison_name="v1.1"
        ),
    ]
    chart = ComparisonBar(data=data, title="Version Comparison", metric_label="Execution Time (ms)", options=OPTS)
    return "comparison_bar", chart.render()


def _diverging_bar() -> tuple[str, str]:
    data = [
        DivergingBarData(label="Q1", pct_change=-20.8),
        DivergingBarData(label="Q2", pct_change=11.8),
        DivergingBarData(label="Q3", pct_change=-19.1),
        DivergingBarData(label="Q4", pct_change=-3.6),
        DivergingBarData(label="Q5", pct_change=45.2),
    ]
    chart = DivergingBar(data=data, title="Regression Analysis", options=OPTS)
    return "diverging_bar", chart.render()


def _summary_box() -> tuple[str, str]:
    stats = SummaryStats(
        title="TPC-H on DuckDB (SF 1)",
        primary_value=156.3,
        secondary_value=142.0,
        total_value=3450.0,
        num_items=22,
        best_items=[("Q6", 12.5), ("Q1", 45.2), ("Q3", 67.8)],
        worst_items=[("Q21", 890.0), ("Q18", 670.0), ("Q9", 450.0)],
        primary_label="Geo Mean",
        secondary_label="Median",
        total_label="Total",
        count_label="Queries",
        environment={"OS": "macOS 15.3", "CPUs": "12 (arm64)", "Memory": "36 GB"},
        platform_config={"Driver": "DuckDB 1.2.0", "Tuning": "Tuned"},
    )
    chart = SummaryBox(stats=stats, options=OPTS)
    return "summary_box", chart.render()


def _percentile_ladder() -> tuple[str, str]:
    data = [
        PercentileData(name="DuckDB", p50=120.0, p90=350.0, p95=480.0, p99=890.0),
        PercentileData(name="SQLite", p50=280.0, p90=520.0, p95=650.0, p99=1200.0),
        PercentileData(name="Polars", p50=150.0, p90=310.0, p95=420.0, p99=700.0),
    ]
    chart = PercentileLadder(data=data, title="Tail Latency", metric_label="ms", options=OPTS)
    return "percentile_ladder", chart.render()


def _normalized_speedup() -> tuple[str, str]:
    data = [
        SpeedupData(name="DuckDB", ratio=1.0, is_baseline=True),
        SpeedupData(name="SQLite", ratio=0.36),
        SpeedupData(name="Polars", ratio=0.85),
        SpeedupData(name="Snowflake", ratio=1.42),
    ]
    chart = NormalizedSpeedup(data=data, title="Relative Speedup", options=OPTS)
    return "normalized_speedup", chart.render()


def _stacked_bar() -> tuple[str, str]:
    data = [
        StackedBarData(
            label="DuckDB",
            segments=[
                StackedBarSegment(phase_name="Generate", value=5.0),
                StackedBarSegment(phase_name="Load", value=12.0),
                StackedBarSegment(phase_name="Query", value=1234.5),
            ],
        ),
        StackedBarData(
            label="SQLite",
            segments=[
                StackedBarSegment(phase_name="Generate", value=5.0),
                StackedBarSegment(phase_name="Load", value=45.0),
                StackedBarSegment(phase_name="Query", value=3456.7),
            ],
        ),
    ]
    chart = StackedBar(data=data, title="Phase Breakdown", metric_label="ms", options=OPTS)
    return "stacked_bar", chart.render()


def _sparkline_table() -> tuple[str, str]:
    data = SparklineTableData(
        rows=["DuckDB", "SQLite", "Polars"],
        columns=[
            SparklineColumn(name="Total (ms)", values={"DuckDB": 1234.5, "SQLite": 3456.7, "Polars": 2100.0}),
            SparklineColumn(name="Geo Mean", values={"DuckDB": 156.3, "SQLite": 420.1, "Polars": 210.5}),
            SparklineColumn(name="P99 (ms)", values={"DuckDB": 890.0, "SQLite": 1200.0, "Polars": 700.0}),
        ],
    )
    chart = SparklineTable(data=data, title="Platform Overview", options=OPTS)
    return "sparkline_table", chart.render()


def _cdf_chart() -> tuple[str, str]:
    data = [
        CDFSeriesData(name="DuckDB", values=[80, 95, 110, 130, 150, 200, 250, 300, 500]),
        CDFSeriesData(name="SQLite", values=[200, 250, 300, 350, 400, 450, 500, 600, 800]),
    ]
    chart = CDFChart(data=data, title="Cumulative Distribution", options=OPTS)
    return "cdf_chart", chart.render()


def _rank_table() -> tuple[str, str]:
    platforms = ["DuckDB", "SQLite", "Polars"]
    queries = ["Q1", "Q2", "Q3", "Q4"]
    times = {
        ("DuckDB", "Q1"): 120.0,
        ("SQLite", "Q1"): 150.0,
        ("Polars", "Q1"): 135.0,
        ("DuckDB", "Q2"): 340.0,
        ("SQLite", "Q2"): 280.0,
        ("Polars", "Q2"): 310.0,
        ("DuckDB", "Q3"): 89.0,
        ("SQLite", "Q3"): 110.0,
        ("Polars", "Q3"): 95.0,
        ("DuckDB", "Q4"): 560.0,
        ("SQLite", "Q4"): 480.0,
        ("Polars", "Q4"): 520.0,
    }
    data = RankTableData(items=queries, groups=platforms, values=times)
    chart = RankTable(data=data, title="Platform Rankings", options=OPTS)
    return "rank_table", chart.render()


ALL_CHART_BUILDERS = [
    _bar_chart,
    _histogram,
    _heatmap,
    _box_plot,
    _line_chart,
    _scatter_plot,
    _comparison_bar,
    _diverging_bar,
    _summary_box,
    _percentile_ladder,
    _normalized_speedup,
    _stacked_bar,
    _sparkline_table,
    _cdf_chart,
    _rank_table,
]


def _normalize(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines) + "\n"


def _compare_or_fail(chart_name: str, normalized: str, golden_path: Path, update_golden: bool) -> None:
    if update_golden:
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(normalized, encoding="utf-8")
        pytest.skip(f"Updated golden file: {golden_path}")

    if not golden_path.exists():
        pytest.fail(
            f"Golden file missing: {golden_path}. If this chart is "
            f"new or the rendering intentionally changed, regenerate "
            f"with --update-golden and review the diff before "
            f"committing; do not let this test create it implicitly."
        )

    expected = golden_path.read_text(encoding="utf-8")
    assert normalized == expected, (
        f"Chart '{chart_name}' output differs from golden file {golden_path}.\nRun with --update-golden to regenerate."
    )


@pytest.mark.parametrize(
    "builder",
    ALL_CHART_BUILDERS,
    ids=[fn.__name__.lstrip("_") for fn in ALL_CHART_BUILDERS],
)
def test_golden_output(builder, update_golden: bool) -> None:
    chart_name, rendered = builder()
    normalized = _normalize(rendered)
    golden_path = GOLDEN_DIR / f"{chart_name}.txt"

    _compare_or_fail(chart_name, normalized, golden_path, update_golden)


def test_missing_golden_file_hard_fails_without_writing(tmp_path: Path) -> None:
    golden_path = tmp_path / "some_chart.txt"
    assert not golden_path.exists()

    with pytest.raises(pytest.fail.Exception):
        _compare_or_fail("some_chart", "irrelevant rendered content\n", golden_path, update_golden=False)

    assert not golden_path.exists(), "Missing-golden-file path must not create the golden file"
    assert list(tmp_path.iterdir()) == [], "Missing-golden-file path must not write anything to the golden dir"
