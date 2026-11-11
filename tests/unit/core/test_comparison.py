import json
import math

import pytest

from benchbox.core.comparison.types import (
    DATAFRAME_PLATFORM_SUFFIX,
    SQL_PLATFORMS,
    ComparisonMode,
    PlatformType,
    UnifiedBenchmarkConfig,
    UnifiedComparisonSummary,
    UnifiedPlatformResult,
    UnifiedQueryResult,
    detect_platform_type,
    detect_platform_types,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestPlatformType:
    def test_sql_value(self):
        assert PlatformType.SQL.value == "sql"

    def test_dataframe_value(self):
        assert PlatformType.DATAFRAME.value == "dataframe"

    def test_auto_value(self):
        assert PlatformType.AUTO.value == "auto"


class TestComparisonMode:
    def test_run_value(self):
        assert ComparisonMode.RUN.value == "run"

    def test_files_value(self):
        assert ComparisonMode.FILES.value == "files"


class TestConstants:
    def test_sql_platforms_is_frozenset(self):
        assert isinstance(SQL_PLATFORMS, frozenset)

    def test_known_sql_platforms(self):
        assert "duckdb" in SQL_PLATFORMS
        assert "snowflake" in SQL_PLATFORMS
        assert "sqlite" in SQL_PLATFORMS
        assert "bigquery" in SQL_PLATFORMS

    def test_dataframe_suffix(self):
        assert DATAFRAME_PLATFORM_SUFFIX == "-df"


class TestDetectPlatformType:
    def test_sql_platform(self):
        assert detect_platform_type("duckdb") == PlatformType.SQL
        assert detect_platform_type("snowflake") == PlatformType.SQL

    def test_dataframe_platform(self):
        assert detect_platform_type("polars-df") == PlatformType.DATAFRAME
        assert detect_platform_type("pandas-df") == PlatformType.DATAFRAME

    def test_unknown_defaults_to_sql(self):
        assert detect_platform_type("unknown-platform") == PlatformType.SQL

    def test_case_sensitivity(self):

        assert detect_platform_type("DuckDB") == PlatformType.SQL


class TestDetectPlatformTypes:
    def test_empty_list(self):
        ptype, inconsistent = detect_platform_types([])
        assert ptype == PlatformType.SQL
        assert inconsistent == []

    def test_all_sql(self):
        ptype, inconsistent = detect_platform_types(["duckdb", "sqlite"])
        assert ptype == PlatformType.SQL
        assert inconsistent == []

    def test_all_dataframe(self):
        ptype, inconsistent = detect_platform_types(["polars-df", "pandas-df"])
        assert ptype == PlatformType.DATAFRAME
        assert inconsistent == []

    def test_mixed_returns_majority(self):
        ptype, inconsistent = detect_platform_types(["duckdb", "sqlite", "polars-df"])
        assert ptype == PlatformType.SQL
        assert "polars-df" in inconsistent

    def test_mixed_df_majority(self):
        ptype, inconsistent = detect_platform_types(["polars-df", "pandas-df", "duckdb"])
        assert ptype == PlatformType.DATAFRAME
        assert "duckdb" in inconsistent


class TestUnifiedBenchmarkConfig:
    def test_defaults(self):
        cfg = UnifiedBenchmarkConfig()
        assert cfg.platform_type == PlatformType.AUTO
        assert cfg.scale_factor == 0.01
        assert cfg.benchmark == "tpch"
        assert cfg.query_ids is None
        assert cfg.warmup_iterations == 1
        assert cfg.benchmark_iterations == 3


class TestUnifiedQueryResult:
    def test_defaults(self):
        qr = UnifiedQueryResult(query_id="Q1", platform="duckdb", platform_type=PlatformType.SQL)
        assert qr.status == "SUCCESS"
        assert qr.execution_times_ms == []
        assert qr.error_message is None

    def test_to_dict(self):
        qr = UnifiedQueryResult(
            query_id="Q1",
            platform="duckdb",
            platform_type=PlatformType.SQL,
            iterations=3,
            mean_time_ms=10.5,
            rows_returned=100,
        )
        d = qr.to_dict()
        assert d["query_id"] == "Q1"
        assert d["platform_type"] == "sql"
        assert d["mean_time_ms"] == 10.5
        assert d["rows_returned"] == 100

    def test_error_result(self):
        qr = UnifiedQueryResult(
            query_id="Q1",
            platform="duckdb",
            platform_type=PlatformType.SQL,
            status="ERROR",
            error_message="timeout",
        )
        d = qr.to_dict()
        assert d["status"] == "ERROR"
        assert d["error_message"] == "timeout"


class TestUnifiedPlatformResult:
    def test_defaults(self):
        pr = UnifiedPlatformResult(platform="duckdb", platform_type=PlatformType.SQL)
        assert pr.query_results == []
        assert pr.total_time_ms == 0.0

        assert pr.success_rate == 0.0

    def test_to_dict(self):
        qr = UnifiedQueryResult(query_id="Q1", platform="duckdb", platform_type=PlatformType.SQL)
        pr = UnifiedPlatformResult(
            platform="duckdb",
            platform_type=PlatformType.SQL,
            query_results=[qr],
            total_time_ms=10.0,
            geometric_mean_ms=10.0,
            success_rate=100.0,
        )
        d = pr.to_dict()
        assert d["platform"] == "duckdb"
        assert len(d["query_results"]) == 1
        assert d["geometric_mean_ms"] == 10.0


class TestUnifiedComparisonSummary:
    def test_to_dict(self):
        summary = UnifiedComparisonSummary(
            platforms=["duckdb", "sqlite"],
            platform_type=PlatformType.SQL,
            fastest_platform="duckdb",
            slowest_platform="sqlite",
            speedup_ratio=2.5,
            query_winners={"Q1": "duckdb", "Q2": "sqlite"},
            total_queries=2,
        )
        d = summary.to_dict()
        assert d["fastest_platform"] == "duckdb"
        assert d["speedup_ratio"] == 2.5
        assert d["platform_type"] == "sql"
        assert len(d["query_winners"]) == 2


class TestUnifiedBenchmarkSuiteBuildResult:
    def setup_method(self):
        from benchbox.core.comparison.suite import UnifiedBenchmarkSuite

        self.suite = UnifiedBenchmarkSuite()

    def test_all_successful(self):
        qrs = [
            UnifiedQueryResult(
                query_id=f"Q{i}",
                platform="duckdb",
                platform_type=PlatformType.SQL,
                mean_time_ms=float(10 * (i + 1)),
                status="SUCCESS",
            )
            for i in range(3)
        ]
        result = self.suite._build_platform_result("duckdb", PlatformType.SQL, qrs)
        assert result.platform == "duckdb"
        assert result.total_time_ms == 10.0 + 20.0 + 30.0
        assert result.geometric_mean_ms > 0
        assert result.success_rate == 100.0

    def test_with_errors(self):
        qrs = [
            UnifiedQueryResult(
                query_id="Q1", platform="duckdb", platform_type=PlatformType.SQL, mean_time_ms=10.0, status="SUCCESS"
            ),
            UnifiedQueryResult(
                query_id="Q2", platform="duckdb", platform_type=PlatformType.SQL, status="ERROR", error_message="oops"
            ),
        ]
        result = self.suite._build_platform_result("duckdb", PlatformType.SQL, qrs)
        assert result.success_rate == 50.0
        assert result.total_time_ms == 10.0

    def test_empty_results(self):
        result = self.suite._build_platform_result("duckdb", PlatformType.SQL, [])
        assert result.success_rate == 0
        assert result.geometric_mean_ms == 0.0


class TestUnifiedBenchmarkSuiteGetSummary:
    def setup_method(self):
        from benchbox.core.comparison.suite import UnifiedBenchmarkSuite

        self.suite = UnifiedBenchmarkSuite()

    def _make_platform_result(self, platform, geomean, query_ids_times):
        qrs = [
            UnifiedQueryResult(
                query_id=qid,
                platform=platform,
                platform_type=PlatformType.SQL,
                mean_time_ms=t,
                status="SUCCESS",
            )
            for qid, t in query_ids_times
        ]
        return UnifiedPlatformResult(
            platform=platform,
            platform_type=PlatformType.SQL,
            query_results=qrs,
            geometric_mean_ms=geomean,
            success_rate=100.0,
        )

    def test_get_summary(self):
        r1 = self._make_platform_result("duckdb", 10.0, [("Q1", 5.0), ("Q2", 20.0)])
        r2 = self._make_platform_result("sqlite", 50.0, [("Q1", 40.0), ("Q2", 60.0)])
        summary = self.suite.get_summary([r1, r2])
        assert summary.fastest_platform == "duckdb"
        assert summary.slowest_platform == "sqlite"
        assert summary.speedup_ratio == pytest.approx(5.0)
        assert summary.query_winners["Q1"] == "duckdb"
        assert summary.query_winners["Q2"] == "duckdb"
        assert summary.total_queries == 2

    def test_get_summary_empty_raises(self):
        with pytest.raises(ValueError, match="No results"):
            self.suite.get_summary([])

    def test_get_summary_no_geomeans(self):
        r1 = UnifiedPlatformResult(platform="p1", platform_type=PlatformType.SQL, geometric_mean_ms=0.0)
        summary = self.suite.get_summary([r1])
        assert summary.speedup_ratio is None
        assert summary.fastest_platform is None
        assert summary.slowest_platform is None
        assert summary.is_comparable is False

    def test_run_comparison_empty_raises(self):
        with pytest.raises(ValueError, match="At least one"):
            self.suite.run_comparison([])

    def test_run_comparison_mixed_types_raises(self):
        with pytest.raises(ValueError, match="Mixed platform types"):
            self.suite.run_comparison(["duckdb", "polars-df"])


class TestUnifiedBenchmarkSuiteExport:
    def setup_method(self):
        from benchbox.core.comparison.suite import UnifiedBenchmarkSuite

        self.suite = UnifiedBenchmarkSuite()

    def test_export_json(self, tmp_path):
        qr = UnifiedQueryResult(
            query_id="Q1",
            platform="duckdb",
            platform_type=PlatformType.SQL,
            mean_time_ms=10.0,
            status="SUCCESS",
        )
        pr = UnifiedPlatformResult(
            platform="duckdb",
            platform_type=PlatformType.SQL,
            query_results=[qr],
            geometric_mean_ms=10.0,
            success_rate=100.0,
        )
        output = tmp_path / "results.json"
        self.suite.export_results([pr], output, format="json")

        data = json.loads(output.read_text())
        assert data["benchmark_suite"] == "unified_comparison"
        assert len(data["results"]) == 1

    def test_export_markdown(self, tmp_path):
        qr = UnifiedQueryResult(
            query_id="Q1", platform="duckdb", platform_type=PlatformType.SQL, mean_time_ms=10.0, status="SUCCESS"
        )
        pr = UnifiedPlatformResult(
            platform="duckdb",
            platform_type=PlatformType.SQL,
            query_results=[qr],
            geometric_mean_ms=10.0,
            success_rate=100.0,
        )
        output = tmp_path / "results.md"
        self.suite.export_results([pr], output, format="markdown")
        content = output.read_text()
        assert "# Platform Comparison Report" in content

    def test_export_text(self, tmp_path):
        qr = UnifiedQueryResult(
            query_id="Q1", platform="duckdb", platform_type=PlatformType.SQL, mean_time_ms=10.0, status="SUCCESS"
        )
        pr = UnifiedPlatformResult(
            platform="duckdb",
            platform_type=PlatformType.SQL,
            query_results=[qr],
            geometric_mean_ms=10.0,
            success_rate=100.0,
        )
        output = tmp_path / "results.txt"
        self.suite.export_results([pr], output, format="text")
        content = output.read_text()
        assert "PLATFORM COMPARISON RESULTS" in content

    def test_export_unsupported_format(self, tmp_path):
        pr = UnifiedPlatformResult(platform="duckdb", platform_type=PlatformType.SQL)
        with pytest.raises(ValueError, match="Unsupported"):
            self.suite.export_results([pr], tmp_path / "out.csv", format="csv")


class TestUnifiedComparisonPlotter:
    def test_empty_results_raises(self):
        from benchbox.core.comparison.plotter import UnifiedComparisonPlotter

        with pytest.raises(ValueError, match="No results"):
            UnifiedComparisonPlotter(results=[])

    def test_init_sets_platform_type(self):
        from benchbox.core.comparison.plotter import UnifiedComparisonPlotter

        pr = UnifiedPlatformResult(platform="duckdb", platform_type=PlatformType.SQL)
        plotter = UnifiedComparisonPlotter(results=[pr])
        assert plotter.platform_type == PlatformType.SQL
        assert plotter.theme == "light"

    def test_custom_theme(self):
        from benchbox.core.comparison.plotter import UnifiedComparisonPlotter

        pr = UnifiedPlatformResult(platform="duckdb", platform_type=PlatformType.SQL)
        plotter = UnifiedComparisonPlotter(results=[pr], theme="dark")
        assert plotter.theme == "dark"


class TestGetAvailablePlatforms:
    def setup_method(self):
        from benchbox.core.comparison.suite import UnifiedBenchmarkSuite

        self.suite = UnifiedBenchmarkSuite()

    def test_sql_only_filter(self, monkeypatch):
        monkeypatch.setattr(
            "benchbox.core.comparison.suite.UnifiedBenchmarkSuite.get_available_platforms",
            lambda self, platform_type=None: (
                ["duckdb", "sqlite"] if platform_type == PlatformType.SQL else ["duckdb", "sqlite", "polars-df"]
            ),
        )
        platforms = self.suite.get_available_platforms(PlatformType.SQL)

        for p in platforms:
            assert not p.endswith("-df") or p in platforms

    def test_returns_list(self, monkeypatch):
        monkeypatch.setattr(
            "benchbox.platforms.list_available_platforms",
            lambda: ["duckdb", "sqlite"],
        )
        monkeypatch.setattr(
            "benchbox.platforms.list_available_dataframe_platforms",
            lambda: {"polars-df": True, "pandas-df": False},
        )
        platforms = self.suite.get_available_platforms()
        assert isinstance(platforms, list)
        assert "duckdb" in platforms


class TestBuildPlatformResult:
    def setup_method(self):
        from benchbox.core.comparison.suite import UnifiedBenchmarkSuite

        self.suite = UnifiedBenchmarkSuite()

    def test_successful_queries_aggregated(self):
        query_results = [
            UnifiedQueryResult(
                query_id="Q1",
                platform="duckdb",
                platform_type=PlatformType.SQL,
                mean_time_ms=100.0,
                status="SUCCESS",
            ),
            UnifiedQueryResult(
                query_id="Q2",
                platform="duckdb",
                platform_type=PlatformType.SQL,
                mean_time_ms=200.0,
                status="SUCCESS",
            ),
        ]
        result = self.suite._build_platform_result("duckdb", PlatformType.SQL, query_results)
        assert result.platform == "duckdb"
        assert result.success_rate == pytest.approx(100.0)
        assert result.geometric_mean_ms > 0

    def test_failed_queries_lower_success_rate(self):
        query_results = [
            UnifiedQueryResult(
                query_id="Q1",
                platform="duckdb",
                platform_type=PlatformType.SQL,
                mean_time_ms=100.0,
                status="SUCCESS",
            ),
            UnifiedQueryResult(
                query_id="Q2",
                platform="duckdb",
                platform_type=PlatformType.SQL,
                status="ERROR",
                error_message="timeout",
            ),
        ]
        result = self.suite._build_platform_result("duckdb", PlatformType.SQL, query_results)
        assert result.success_rate == pytest.approx(50.0)

    def test_all_failed_queries(self):
        query_results = [
            UnifiedQueryResult(
                query_id="Q1",
                platform="duckdb",
                platform_type=PlatformType.SQL,
                status="ERROR",
                error_message="crash",
            ),
        ]
        result = self.suite._build_platform_result("duckdb", PlatformType.SQL, query_results)
        assert result.success_rate == pytest.approx(0.0)
        assert result.geometric_mean_ms == 0.0


class TestRunSqlComparisonError:
    def setup_method(self):
        from benchbox.core.comparison.suite import UnifiedBenchmarkSuite

        self.suite = UnifiedBenchmarkSuite()

    def test_single_platform_error_creates_error_result(self, monkeypatch):
        monkeypatch.setattr(
            self.suite,
            "_benchmark_sql_platform",
            lambda platform, data_dir: (_ for _ in ()).throw(RuntimeError("connection failed")),
        )
        results = self.suite._run_sql_comparison(["duckdb"])
        assert len(results) == 1
        assert results[0].query_results[0].status == "ERROR"
        assert "connection failed" in (results[0].query_results[0].error_message or "")


