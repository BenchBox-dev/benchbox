# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.comparison.suite import UnifiedBenchmarkSuite, run_unified_comparison
from benchbox.core.comparison.types import (
    PlatformType,
    UnifiedBenchmarkConfig,
    UnifiedPlatformResult,
    UnifiedQueryResult,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _qr(query_id: str, platform: str, mean_ms: float, status: str = "SUCCESS") -> UnifiedQueryResult:
    return UnifiedQueryResult(
        query_id=query_id,
        platform=platform,
        platform_type=PlatformType.SQL,
        mean_time_ms=mean_ms,
        status=status,
    )


def _pr(platform: str, geomean: float, total: float = 0.0, success_rate: float = 100.0) -> UnifiedPlatformResult:
    return UnifiedPlatformResult(
        platform=platform,
        platform_type=PlatformType.SQL,
        geometric_mean_ms=geomean,
        total_time_ms=total,
        success_rate=success_rate,
    )


def _pr_with_queries(platform: str, query_ids_ms: dict) -> UnifiedPlatformResult:
    qrs = [_qr(qid, platform, ms) for qid, ms in query_ids_ms.items()]
    suite = UnifiedBenchmarkSuite()
    return suite._build_platform_result(platform, PlatformType.SQL, qrs)


class TestUnifiedBenchmarkSuiteInit:
    def test_default_config_created(self):
        suite = UnifiedBenchmarkSuite()
        assert suite.config is not None
        assert suite.config.platform_type == PlatformType.AUTO
        assert suite.config.benchmark == "tpch"

    def test_custom_config_stored(self):
        config = UnifiedBenchmarkConfig(platform_type=PlatformType.SQL, scale_factor=1.0)
        suite = UnifiedBenchmarkSuite(config=config)
        assert suite.config is config
        assert suite.config.scale_factor == 1.0


class TestGetAvailablePlatforms:
    def test_returns_sql_platforms(self):
        suite = UnifiedBenchmarkSuite()
        with (
            patch("benchbox.core.comparison.suite.UnifiedBenchmarkSuite.get_available_platforms") as mock_method,
        ):
            mock_method.return_value = ["duckdb", "sqlite"]
            result = suite.get_available_platforms(PlatformType.SQL)
        with (
            patch("benchbox.platforms.list_available_platforms", return_value=["duckdb", "sqlite"]),
            patch("benchbox.platforms.list_available_dataframe_platforms", return_value={}),
        ):
            result = suite.get_available_platforms(PlatformType.SQL)
            assert "duckdb" in result

    def test_returns_dataframe_platforms(self):
        suite = UnifiedBenchmarkSuite()
        with (
            patch("benchbox.platforms.list_available_platforms", return_value=[]),
            patch(
                "benchbox.platforms.list_available_dataframe_platforms",
                return_value={"polars-df": True, "pandas-df": False},
            ),
        ):
            result = suite.get_available_platforms(PlatformType.DATAFRAME)
            assert "polars-df" in result
            assert "pandas-df" not in result

    def test_returns_all_platforms_when_none_type(self):
        suite = UnifiedBenchmarkSuite()
        with (
            patch("benchbox.platforms.list_available_platforms", return_value=["duckdb"]),
            patch(
                "benchbox.platforms.list_available_dataframe_platforms",
                return_value={"polars-df": True},
            ),
        ):
            result = suite.get_available_platforms(None)
            assert "duckdb" in result
            assert "polars-df" in result


class TestRunComparison:
    def test_raises_on_empty_platforms(self):
        suite = UnifiedBenchmarkSuite()
        with pytest.raises(ValueError, match="At least one"):
            suite.run_comparison([])

    def test_routes_to_sql_comparison(self):
        config = UnifiedBenchmarkConfig(platform_type=PlatformType.SQL)
        suite = UnifiedBenchmarkSuite(config=config)
        with patch.object(suite, "_run_sql_comparison", return_value=[]) as mock_sql:
            suite.run_comparison(["duckdb"])
            mock_sql.assert_called_once_with(["duckdb"], None)

    def test_routes_to_dataframe_comparison(self):
        config = UnifiedBenchmarkConfig(platform_type=PlatformType.DATAFRAME)
        suite = UnifiedBenchmarkSuite(config=config)
        with patch.object(suite, "_run_dataframe_comparison", return_value=[]) as mock_df:
            suite.run_comparison(["polars-df"])
            mock_df.assert_called_once_with(["polars-df"], None)

    def test_auto_detects_sql(self):
        config = UnifiedBenchmarkConfig(platform_type=PlatformType.AUTO)
        suite = UnifiedBenchmarkSuite(config=config)
        with patch.object(suite, "_run_sql_comparison", return_value=[]) as mock_sql:
            suite.run_comparison(["duckdb", "sqlite"])
            mock_sql.assert_called_once()

    def test_auto_detects_dataframe(self):
        config = UnifiedBenchmarkConfig(platform_type=PlatformType.AUTO)
        suite = UnifiedBenchmarkSuite(config=config)
        with patch.object(suite, "_run_dataframe_comparison", return_value=[]) as mock_df:
            suite.run_comparison(["polars-df"])
            mock_df.assert_called_once()

    def test_raises_on_mixed_platform_types(self):
        config = UnifiedBenchmarkConfig(platform_type=PlatformType.AUTO)
        suite = UnifiedBenchmarkSuite(config=config)
        with pytest.raises(ValueError, match="Mixed platform types"):
            suite.run_comparison(["duckdb", "polars-df"])


class TestRunSqlComparison:
    def test_collects_results_for_each_platform(self):
        suite = UnifiedBenchmarkSuite()
        mock_result = _pr("duckdb", geomean=100.0)
        with patch.object(suite, "_benchmark_sql_platform", return_value=mock_result):
            results = suite._run_sql_comparison(["duckdb"])
        assert len(results) == 1
        assert results[0].platform == "duckdb"

    def test_captures_exception_as_error_result(self):
        suite = UnifiedBenchmarkSuite()
        with patch.object(suite, "_benchmark_sql_platform", side_effect=RuntimeError("boom")):
            results = suite._run_sql_comparison(["bad-platform"])
        assert len(results) == 1
        assert results[0].query_results[0].status == "ERROR"
        assert "boom" in results[0].query_results[0].error_message


class TestBuildPlatformResult:
    def test_calculates_geometric_mean(self):
        suite = UnifiedBenchmarkSuite()
        qrs = [_qr("Q1", "duckdb", 100.0), _qr("Q2", "duckdb", 400.0)]
        result = suite._build_platform_result("duckdb", PlatformType.SQL, qrs)
        expected_geomean = math.exp((math.log(100.0) + math.log(400.0)) / 2)
        assert abs(result.geometric_mean_ms - expected_geomean) < 0.01

    def test_calculates_total_time(self):
        suite = UnifiedBenchmarkSuite()
        qrs = [_qr("Q1", "duckdb", 150.0), _qr("Q2", "duckdb", 250.0)]
        result = suite._build_platform_result("duckdb", PlatformType.SQL, qrs)
        assert result.total_time_ms == 400.0

    def test_excludes_error_queries_from_geomean(self):
        suite = UnifiedBenchmarkSuite()
        qrs = [_qr("Q1", "duckdb", 100.0), _qr("Q2", "duckdb", 0.0, status="ERROR")]
        result = suite._build_platform_result("duckdb", PlatformType.SQL, qrs)
        assert result.success_rate == 50.0

    def test_empty_query_results(self):
        suite = UnifiedBenchmarkSuite()
        result = suite._build_platform_result("duckdb", PlatformType.SQL, [])
        assert result.geometric_mean_ms == 0.0
        assert result.success_rate == 0.0

    def test_zero_mean_times_excluded_from_geomean(self):
        suite = UnifiedBenchmarkSuite()
        qrs = [_qr("Q1", "duckdb", 0.0)]
        result = suite._build_platform_result("duckdb", PlatformType.SQL, qrs)
        assert result.geometric_mean_ms == 0.0


class TestGetSummary:
    def test_raises_on_empty_results(self):
        suite = UnifiedBenchmarkSuite()
        with pytest.raises(ValueError, match="No results"):
            suite.get_summary([])

    def test_identifies_fastest_slowest(self):
        suite = UnifiedBenchmarkSuite()
        results = [_pr("duckdb", geomean=100.0), _pr("sqlite", geomean=500.0)]
        summary = suite.get_summary(results)
        assert summary.fastest_platform == "duckdb"
        assert summary.slowest_platform == "sqlite"
        assert abs(summary.speedup_ratio - 5.0) < 0.01

    def test_handles_no_geomeans(self):
        suite = UnifiedBenchmarkSuite()
        results = [_pr("duckdb", geomean=0.0), _pr("sqlite", geomean=0.0)]
        summary = suite.get_summary(results)
        assert summary.speedup_ratio is None
        assert summary.fastest_platform is None
        assert summary.slowest_platform is None
        assert summary.is_comparable is False
        assert summary.total_queries == 0

    def test_finds_query_winners(self):
        suite = UnifiedBenchmarkSuite()
        qr1 = _qr("Q1", "duckdb", 100.0)
        qr2 = _qr("Q1", "sqlite", 300.0)
        pr1 = UnifiedPlatformResult("duckdb", PlatformType.SQL, [qr1], geometric_mean_ms=100.0)
        pr2 = UnifiedPlatformResult("sqlite", PlatformType.SQL, [qr2], geometric_mean_ms=300.0)
        summary = suite.get_summary([pr1, pr2])
        assert summary.query_winners.get("Q1") == "duckdb"

    def test_total_queries_count(self):
        suite = UnifiedBenchmarkSuite()
        qrs1 = [_qr(f"Q{i}", "duckdb", float(i * 10)) for i in range(1, 4)]
        qrs2 = [_qr(f"Q{i}", "sqlite", float(i * 20)) for i in range(1, 4)]
        pr1 = UnifiedPlatformResult("duckdb", PlatformType.SQL, qrs1, geometric_mean_ms=20.0)
        pr2 = UnifiedPlatformResult("sqlite", PlatformType.SQL, qrs2, geometric_mean_ms=40.0)
        summary = suite.get_summary([pr1, pr2])
        assert summary.total_queries == 3


class TestExportResults:
    def _make_results(self):
        qrs = [_qr("Q1", "duckdb", 100.0)]
        return [UnifiedPlatformResult("duckdb", PlatformType.SQL, qrs, geometric_mean_ms=100.0)]

    def test_exports_json_format(self, tmp_path):
        suite = UnifiedBenchmarkSuite()
        results = self._make_results()
        out = suite.export_results(results, tmp_path / "results.json", format="json")
        assert out.exists()
        data = json.loads(out.read_text())
        assert "results" in data
        assert data["benchmark_suite"] == "unified_comparison"

    def test_exports_markdown_format(self, tmp_path):
        suite = UnifiedBenchmarkSuite()
        results = self._make_results()
        out = suite.export_results(results, tmp_path / "report.md", format="markdown")
        assert out.exists()
        content = out.read_text()
        assert "# Platform Comparison Report" in content

    def test_exports_text_format(self, tmp_path):
        suite = UnifiedBenchmarkSuite()
        results = self._make_results()
        out = suite.export_results(results, tmp_path / "report.txt", format="text")
        assert out.exists()
        content = out.read_text()
        assert "PLATFORM COMPARISON RESULTS" in content

    def test_raises_on_unsupported_format(self, tmp_path):
        suite = UnifiedBenchmarkSuite()
        results = self._make_results()
        with pytest.raises(ValueError, match="Unsupported export format"):
            suite.export_results(results, tmp_path / "out.xyz", format="xyz")

    def test_creates_parent_directories(self, tmp_path):
        suite = UnifiedBenchmarkSuite()
        results = self._make_results()
        nested = tmp_path / "a" / "b" / "c" / "out.json"
        suite.export_results(results, nested, format="json")
        assert nested.exists()


class TestGenerateMarkdownReport:
    def test_includes_platform_table(self):
        suite = UnifiedBenchmarkSuite()
        qrs = [_qr("Q1", "duckdb", 50.0), _qr("Q1", "sqlite", 200.0)]
        pr1 = UnifiedPlatformResult("duckdb", PlatformType.SQL, [qrs[0]], geometric_mean_ms=50.0)
        pr2 = UnifiedPlatformResult("sqlite", PlatformType.SQL, [qrs[1]], geometric_mean_ms=200.0)
        report = suite._generate_markdown_report([pr1, pr2])
        assert "Platform" in report
        assert "duckdb" in report
        assert "sqlite" in report

    def test_includes_query_winners_section(self):
        suite = UnifiedBenchmarkSuite()
        qrs = [_qr("Q1", "duckdb", 50.0)]
        pr = UnifiedPlatformResult("duckdb", PlatformType.SQL, qrs, geometric_mean_ms=50.0)
        report = suite._generate_markdown_report([pr])
        assert "Query Winners" in report


class TestGenerateTextReport:
    def test_includes_headers(self):
        suite = UnifiedBenchmarkSuite()
        qrs = [_qr("Q1", "duckdb", 100.0)]
        pr = UnifiedPlatformResult("duckdb", PlatformType.SQL, qrs, geometric_mean_ms=100.0)
        report = suite._generate_text_report([pr])
        assert "PLATFORM COMPARISON RESULTS" in report
        assert "duckdb" in report
        assert "Fastest" in report


class TestRunDataframeComparison:
    def test_delegates_to_dataframe_suite(self):
        suite = UnifiedBenchmarkSuite()

        fake_qr = SimpleNamespace(
            query_id="Q1",
            platform="polars-df",
            iterations=3,
            execution_times_ms=[100.0, 110.0, 90.0],
            mean_time_ms=100.0,
            std_time_ms=10.0,
            min_time_ms=90.0,
            max_time_ms=110.0,
            memory_peak_mb=0.0,
            rows_returned=100,
            status="SUCCESS",
            error_message=None,
        )
        fake_result = SimpleNamespace(
            platform="polars-df",
            query_results=[fake_qr],
            total_time_ms=100.0,
            geometric_mean_ms=100.0,
            success_rate=100.0,
        )

        mock_df_suite = MagicMock()
        mock_df_suite.run_comparison.return_value = [fake_result]

        with patch(
            "benchbox.core.dataframe.benchmark_suite.DataFrameBenchmarkSuite",
            return_value=mock_df_suite,
        ):
            results = suite._run_dataframe_comparison(["polars-df"], data_dir="/tmp/data")

        assert len(results) == 1
        assert results[0].platform == "polars-df"
        assert results[0].platform_type == PlatformType.DATAFRAME

    def test_auto_constructs_data_dir_when_none(self):
        suite = UnifiedBenchmarkSuite(config=UnifiedBenchmarkConfig(scale_factor=0.01))
        fake_result = SimpleNamespace(
            platform="polars-df",
            query_results=[],
            total_time_ms=0.0,
            geometric_mean_ms=0.0,
            success_rate=0.0,
        )
        mock_df_suite = MagicMock()
        mock_df_suite.run_comparison.return_value = [fake_result]

        with patch("benchbox.core.dataframe.benchmark_suite.DataFrameBenchmarkSuite", return_value=mock_df_suite):
            suite._run_dataframe_comparison(["polars-df"], data_dir=None)

        call_kwargs = mock_df_suite.run_comparison.call_args[1]
        assert "sf001" in str(call_kwargs.get("data_dir", ""))


class TestBenchmarkSqlPlatform:
    def test_delegates_to_the_canonical_run_path(self):
        config = UnifiedBenchmarkConfig(query_ids=["Q1"], benchmark="tpch", scale_factor=0.01)
        suite = UnifiedBenchmarkSuite(config=config)

        run_results = SimpleNamespace(
            query_results=[
                {
                    "query_id": "Q1",
                    "status": "SUCCESS",
                    "execution_time_seconds": 0.05,
                    "rows_returned": 4,
                    "run_type": "measurement",
                }
            ]
        )
        with patch.object(suite, "_execute_platform_run", return_value=run_results) as run:
            result = suite._benchmark_sql_platform("duckdb")

        run.assert_called_once_with("duckdb", None)
        assert result.platform == "duckdb"
        assert result.success_rate == 100.0
        assert [q.query_id for q in result.query_results] == ["Q1"]
        assert result.query_results[0].mean_time_ms == pytest.approx(50.0)

    def test_run_failure_propagates_so_the_caller_records_a_failure(self):
        suite = UnifiedBenchmarkSuite(config=UnifiedBenchmarkConfig(query_ids=["Q1"]))
        with (
            patch.object(suite, "_execute_platform_run", side_effect=RuntimeError("no driver")),
            pytest.raises(RuntimeError, match="no driver"),
        ):
            suite._benchmark_sql_platform("snowflake")

    def test_warmup_iterations_are_excluded(self):
        suite = UnifiedBenchmarkSuite(config=UnifiedBenchmarkConfig())
        run_results = SimpleNamespace(
            query_results=[
                {"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 9.0, "run_type": "warmup"},
                {"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.1, "run_type": "measurement"},
                {"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.3, "run_type": "measurement"},
            ]
        )
        results = suite._to_unified_query_results(run_results, "duckdb")
        assert len(results) == 1
        assert results[0].iterations == 2
        assert results[0].mean_time_ms == pytest.approx(200.0)

    def test_a_query_whose_iterations_all_failed_stays_in_the_denominator(self):
        suite = UnifiedBenchmarkSuite(config=UnifiedBenchmarkConfig())
        run_results = SimpleNamespace(
            query_results=[
                {"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.1, "run_type": "measurement"},
                {"query_id": "Q2", "status": "ERROR", "error": "syntax", "run_type": "measurement"},
            ]
        )
        results = suite._to_unified_query_results(run_results, "duckdb")
        assert [(q.query_id, q.status) for q in results] == [("Q1", "SUCCESS"), ("Q2", "ERROR")]
        built = suite._build_platform_result("duckdb", PlatformType.SQL, results)
        assert built.success_rate == pytest.approx(50.0)

    def test_query_ids_are_normalized_for_the_run_path(self):
        suite = UnifiedBenchmarkSuite(config=UnifiedBenchmarkConfig(query_ids=["Q1", "6", "q22"]))
        assert suite._normalized_query_ids() == ["1", "6", "22"]

    def test_no_query_ids_means_every_query(self):
        assert UnifiedBenchmarkSuite(config=UnifiedBenchmarkConfig())._normalized_query_ids() is None


class TestGenerateMarkdownReportBranches:
    def test_no_query_winners_when_all_errors(self):
        suite = UnifiedBenchmarkSuite()
        qrs = [_qr("Q1", "duckdb", 0.0, status="ERROR")]
        pr = UnifiedPlatformResult("duckdb", PlatformType.SQL, qrs, geometric_mean_ms=0.0)
        report = suite._generate_markdown_report([pr])
        assert "Query Winners" not in report


class TestGetSummaryBranches:
    def test_query_with_only_errors_has_no_winner(self):
        suite = UnifiedBenchmarkSuite()
        qrs = [_qr("Q1", "duckdb", 0.0, status="ERROR")]
        pr = UnifiedPlatformResult("duckdb", PlatformType.SQL, qrs, geometric_mean_ms=0.0)
        summary = suite.get_summary([pr])
        assert "Q1" not in summary.query_winners


class TestRunUnifiedComparison:
    def test_delegates_to_suite(self):
        mock_results = [_pr("duckdb", geomean=100.0)]
        with patch.object(UnifiedBenchmarkSuite, "run_comparison", return_value=mock_results) as mock_run:
            results = run_unified_comparison(["duckdb"], platform_type=PlatformType.SQL)
            mock_run.assert_called_once_with(platforms=["duckdb"], data_dir=None)
        assert results is mock_results


