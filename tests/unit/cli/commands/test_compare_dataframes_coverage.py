from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

cdf = importlib.import_module("benchbox.cli.commands.compare_dataframes")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
]


class _FakeConfig:
    def __init__(self, scale_factor, query_ids, warmup_iterations, benchmark_iterations):
        self.scale_factor = scale_factor
        self.query_ids = query_ids
        self.warmup_iterations = warmup_iterations
        self.benchmark_iterations = benchmark_iterations


class _FakeResult:
    def __init__(self, platform: str):
        self.platform = platform
        self.geometric_mean_ms = 10.0
        self.total_time_ms = 20.0
        self.success_rate = 100.0

    def to_dict(self):
        return {"platform": self.platform}


class _FakeSummary:
    fastest_platform = "polars-df"
    slowest_platform = "pandas-df"
    query_winners = {"Q1": "polars-df"}

    def to_dict(self):
        return {"fastest": self.fastest_platform}


class _FakeSuite:
    def __init__(self, config):
        self.config = config

    def get_available_platforms(self):
        return ["polars-df", "pandas-df"]

    def run_comparison(self, platforms, data_dir):
        assert data_dir.exists()
        return [_FakeResult(p) for p in platforms]

    def get_summary(self, _results):
        return _FakeSummary()

    def _generate_markdown_report(self, _results):
        return "# Report\n"


class _FakePlotter:
    def __init__(self, _results, theme="light"):
        self.theme = theme

    def generate_charts(self, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        return ["speedup"]


class _FakeSQLQueryResult:
    def __init__(self, query_id="Q1", status="SUCCESS"):
        self.query_id = query_id
        self.status = status
        self.sql_time_ms = 10.0
        self.df_time_ms = 5.0
        self.speedup = 2.0


class _FakeSQLSummary:
    sql_platform = "duckdb"
    df_platform = "polars-df"
    df_faster_count = 1
    df_wins_percentage = 100.0
    sql_faster_count = 0
    average_speedup = 2.0
    query_results = [_FakeSQLQueryResult()]

    def to_dict(self):
        return {"wins": 1}


class _FakeSQLBenchmark:
    def __init__(self, config):
        self.config = config

    def run_comparison(self, sql_platform, df_platform, data_dir):
        assert sql_platform and df_platform and data_dir.exists()
        return _FakeSQLSummary()

    def generate_report(self, _summary):
        return "# SQL vs DF\n"


class _FakeSQLPlotter:
    def __init__(self, _summary, theme="light"):
        self.theme = theme

    def generate_charts(self, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        return ["speedup"]


class _Cap:
    def __init__(self, family, category, lazy=False, streaming=False, gpu=False, distributed=False):
        self.family = family
        self.category = SimpleNamespace(value=category)
        self.supports_lazy = lazy
        self.supports_streaming = streaming
        self.supports_gpu = gpu
        self.supports_distributed = distributed


def _install_fake_suite_module(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(
        BenchmarkConfig=_FakeConfig,
        DataFrameBenchmarkSuite=_FakeSuite,
        DataFrameComparisonPlotter=_FakePlotter,
        SQLVsDataFrameBenchmark=_FakeSQLBenchmark,
        SQLVsDataFramePlotter=_FakeSQLPlotter,
        PLATFORM_CAPABILITIES={
            "polars-df": _Cap("expression", "cpu", lazy=True, streaming=True),
            "pandas-df": _Cap("pandas", "cpu"),
        },
    )
    monkeypatch.setitem(sys.modules, "benchbox.core.dataframe.benchmark_suite", fake)


def test_list_platforms_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_suite_module(monkeypatch)
    monkeypatch.setattr(
        "benchbox.platforms.list_available_dataframe_platforms", lambda: {"polars-df": True, "pandas-df": False}
    )

    result = CliRunner().invoke(cdf.compare_dataframes, ["--list-platforms"])
    assert result.exit_code == 0
    assert "DataFrame Platforms" in result.output


def test_requires_platform_or_vs_sql(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_suite_module(monkeypatch)
    result = CliRunner().invoke(cdf.compare_dataframes, ["--data-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "Specify at least one platform" in result.output


def test_missing_data_dir_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_suite_module(monkeypatch)
    result = CliRunner().invoke(cdf.compare_dataframes, ["-p", "polars-df", "--data-dir", "nope-dir"])
    assert result.exit_code != 0


def test_run_platform_comparison_json_and_charts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_suite_module(monkeypatch)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    result = CliRunner().invoke(
        cdf.compare_dataframes,
        [
            "-p",
            "polars-df",
            "-p",
            "pandas-df",
            "--data-dir",
            str(data_dir),
            "--format",
            "json",
            "-o",
            str(tmp_path),
            "--generate-charts",
        ],
    )
    assert result.exit_code == 0
    assert (tmp_path / "comparison.json").exists()
    assert (tmp_path / "charts").exists()


def test_run_sql_vs_dataframe_markdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_suite_module(monkeypatch)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    result = CliRunner().invoke(
        cdf.compare_dataframes,
        [
            "-p",
            "polars-df",
            "--vs-sql",
            "duckdb",
            "--data-dir",
            str(data_dir),
            "--format",
            "markdown",
            "-o",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert (tmp_path / "sql_vs_dataframe.md").exists()


def test_helper_printers() -> None:
    cdf._print_text_summary([_FakeResult("polars-df")], _FakeSummary())
    cdf._print_sql_vs_df_summary(_FakeSQLSummary())


