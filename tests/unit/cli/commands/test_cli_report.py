from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli
from benchbox.core.results.database import ResultDatabase
from tests.fixtures.result_dict_fixtures import make_benchmark_results, make_v2_result_dict

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def create_test_result(
    execution_id: str = "test-exec-001",
    platform: str = "DuckDB",
    benchmark: str = "TPC-H",
    scale_factor: float = 1.0,
    timestamp: datetime | None = None,
    geometric_mean_ms: float = 100.0,
):
    return make_benchmark_results(
        benchmark_name=benchmark,
        platform=platform,
        scale_factor=scale_factor,
        execution_id=execution_id,
        timestamp=timestamp,
        duration_seconds=10.0,
        total_queries=22,
        successful_queries=22,
        validation_status="PASSED",
        geometric_mean_execution_time=geometric_mean_ms / 1000.0,
        platform_info={"platform_version": "1.0.0"},
    )


class TestReportStats:
    def test_stats_empty_database(self, tmp_path):
        runner = CliRunner()
        db_path = tmp_path / "test.db"

        result = runner.invoke(cli, ["report", "stats", "--db-path", str(db_path)])

        assert result.exit_code == 0
        assert "Total Results" in result.output
        assert "0" in result.output

    def test_stats_with_data(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = ResultDatabase(db_path)
        db.store_result(create_test_result(execution_id="stat-1"))
        db.store_result(create_test_result(execution_id="stat-2", platform="Snowflake"))

        runner = CliRunner()
        result = runner.invoke(cli, ["report", "stats", "--db-path", str(db_path)])

        assert result.exit_code == 0
        assert "2" in result.output


class TestReportList:
    def test_list_empty_database(self, tmp_path):
        runner = CliRunner()
        db_path = tmp_path / "test.db"

        result = runner.invoke(cli, ["report", "list", "--db-path", str(db_path)])

        assert result.exit_code == 0
        assert "No results found" in result.output

    def test_list_with_results(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = ResultDatabase(db_path)
        db.store_result(create_test_result(execution_id="list-1", platform="DuckDB"))

        runner = CliRunner()
        result = runner.invoke(cli, ["report", "list", "--db-path", str(db_path)])

        assert result.exit_code == 0
        assert "DuckDB" in result.output
        assert "TPC-H" in result.output

    def test_list_with_platform_filter(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = ResultDatabase(db_path)
        db.store_result(create_test_result(execution_id="f1", platform="DuckDB"))
        db.store_result(create_test_result(execution_id="f2", platform="Snowflake"))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["report", "list", "--platform", "DuckDB", "--db-path", str(db_path)],
        )

        assert result.exit_code == 0
        assert "DuckDB" in result.output
        assert "snowflake" not in result.output.casefold()
        assert "f2" not in result.output


class TestReportRankings:
    def test_rankings_no_data(self, tmp_path):
        runner = CliRunner()
        db_path = tmp_path / "test.db"

        result = runner.invoke(
            cli,
            [
                "report",
                "rankings",
                "--benchmark",
                "TPC-H",
                "--scale-factor",
                "1",
                "--db-path",
                str(db_path),
            ],
        )

        assert result.exit_code == 0
        assert "No results found" in result.output

    def test_rankings_with_data(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = ResultDatabase(db_path)
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="r1",
                platform="DuckDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                geometric_mean_ms=100.0,
                timestamp=now,
            )
        )
        db.store_result(
            create_test_result(
                execution_id="r2",
                platform="Snowflake",
                benchmark="TPC-H",
                scale_factor=1.0,
                geometric_mean_ms=200.0,
                timestamp=now,
            )
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "report",
                "rankings",
                "--benchmark",
                "TPC-H",
                "--scale-factor",
                "1",
                "--db-path",
                str(db_path),
            ],
        )

        assert result.exit_code == 0
        assert "Platform Rankings" in result.output
        assert "DuckDB" in result.output
        assert "Snowflake" in result.output


class TestReportRankingsAfterImport:
    def test_rankings_list_runs_imported_from_exported_bundles(self, tmp_path):
        from benchbox.core.results.exporter import ResultExporter

        bundles_dir = tmp_path / "bundles"
        now = datetime.now(timezone.utc)
        exporter = ResultExporter(output_dir=bundles_dir)
        for execution_id, platform, geometric_mean_ms in (
            ("bundle-duckdb", "DuckDB", 100.0),
            ("bundle-snowflake", "Snowflake", 200.0),
        ):
            exporter.export_result(
                make_benchmark_results(
                    benchmark_name="TPC-H",
                    platform=platform,
                    scale_factor=1.0,
                    execution_id=execution_id,
                    timestamp=now,
                    duration_seconds=10.0,
                    total_queries=2,
                    successful_queries=2,
                    validation_status="PASSED",
                    query_results=[
                        {
                            "query_id": str(query_number),
                            "execution_time_ms": geometric_mean_ms,
                            "status": "SUCCESS",
                            "iteration": 1,
                            "stream_id": 0,
                        }
                        for query_number in (1, 2)
                    ],
                ),
                formats=["json"],
            )
        assert len(list(bundles_dir.glob("*.json"))) == 2

        db_path = tmp_path / "imported.db"
        runner = CliRunner()
        imported = runner.invoke(cli, ["report", "import", str(bundles_dir), "--db-path", str(db_path)])
        assert imported.exit_code == 0, imported.output
        assert "Imported: 2" in imported.output

        ranked = runner.invoke(
            cli,
            ["report", "rankings", "--benchmark", "TPC-H", "--scale-factor", "1", "--db-path", str(db_path)],
        )

        assert ranked.exit_code == 0, ranked.output
        assert "No results found" not in ranked.output
        assert "DuckDB" in ranked.output
        assert "Snowflake" in ranked.output

        listed = runner.invoke(cli, ["report", "list", "--db-path", str(db_path)])
        assert listed.exit_code == 0, listed.output
        assert "PASSED" in listed.output


class TestReportTrends:
    def test_trends_no_data(self, tmp_path):
        runner = CliRunner()
        db_path = tmp_path / "test.db"

        result = runner.invoke(
            cli,
            [
                "report",
                "trends",
                "--platform",
                "DuckDB",
                "--benchmark",
                "TPC-H",
                "--scale-factor",
                "1",
                "--db-path",
                str(db_path),
            ],
        )

        assert result.exit_code == 0
        assert "No trend data found" in result.output


class TestReportRegressions:
    def test_regressions_no_data(self, tmp_path):
        runner = CliRunner()
        db_path = tmp_path / "test.db"

        result = runner.invoke(
            cli,
            ["report", "regressions", "--db-path", str(db_path)],
        )

        assert result.exit_code == 0
        assert "No performance regressions detected" in result.output


class TestReportImport:
    def test_import_directory(self, tmp_path):
        db_path = tmp_path / "test.db"
        results_dir = tmp_path / "results"
        results_dir.mkdir()

        result_data = make_v2_result_dict(
            version="2.0",
            benchmark_id="tpc_h",
            benchmark_name="TPC-H",
            platform="DuckDB",
            platform_version="1.0.0",
            execution_id="import-test",
            timestamp="2025-01-01T10:00:00",
            scale_factor=1.0,
        )

        with open(results_dir / "result.json", "w", encoding="utf-8") as f:
            json.dump(result_data, f)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["report", "import", str(results_dir), "--db-path", str(db_path)],
        )

        assert result.exit_code == 0
        assert "Imported: 1" in result.output

    def test_import_nonexistent_directory(self, tmp_path):
        runner = CliRunner()
        db_path = tmp_path / "test.db"

        result = runner.invoke(
            cli,
            ["report", "import", "/nonexistent/path", "--db-path", str(db_path)],
        )

        assert result.exit_code != 0


class TestReportHelp:
    def test_report_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["report", "--help"])

        assert result.exit_code == 0
        assert "Historical result analysis" in result.output
        assert "rankings" in result.output
        assert "trends" in result.output
        assert "regressions" in result.output
        assert "import" in result.output
        assert "stats" in result.output

    def test_rankings_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["report", "rankings", "--help"])

        assert result.exit_code == 0
        assert "--benchmark" in result.output
        assert "--scale-factor" in result.output
        assert "--metric" in result.output
