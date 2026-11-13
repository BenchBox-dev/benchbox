# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json
import sys
import sys as _sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli
from benchbox.cli.exit_codes import ExitCode
from tests.fixtures.result_dict_fixtures import make_v2_result_dict

__import__("benchbox.cli.commands.compare")
_compare_module = _sys.modules["benchbox.cli.commands.compare"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCompareCommand:
    @staticmethod
    def _set_loaded_result_pair(mock_load):
        mock_baseline = MagicMock(benchmark_name="TPC-H", platform="DuckDB", scale_factor=0.01)
        mock_current = MagicMock(benchmark_name="TPC-H", platform="DuckDB", scale_factor=0.01)
        mock_load.side_effect = [(mock_baseline, {}), (mock_current, {})]

    @staticmethod
    def _write_empty_compare_inputs(tmpdir: str) -> tuple[Path, Path]:
        baseline_path = Path(tmpdir) / "baseline.json"
        current_path = Path(tmpdir) / "current.json"
        baseline_path.write_text("{}")
        current_path.write_text("{}")
        return baseline_path, current_path

    def test_compare_command_exists(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "compare" in result.output

    def test_compare_help(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["compare", "--help"])

        assert result.exit_code == 0
        assert "Compare benchmark results" in result.output
        assert "--fail-on-regression" in result.output
        assert "--format" in result.output
        assert "--output" in result.output
        assert "--show-all-queries" in result.output

    def test_compare_requires_two_files(self):
        runner = CliRunner()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"test": "data"}, f)
            file_path = f.name

        try:
            result = runner.invoke(cli, ["compare", file_path])
            assert result.exit_code == 1
            assert "At least 2 result files required" in result.output
        finally:
            Path(file_path).unlink()

    @patch.object(_compare_module, "load_result_file")
    @patch.object(_compare_module, "ResultExporter")
    def test_compare_two_files_success(self, mock_exporter_class, mock_load):

        runner = CliRunner()
        self._set_loaded_result_pair(mock_load)

        mock_exporter = MagicMock()
        mock_exporter.compare_results.return_value = {
            "baseline_file": "baseline.json",
            "current_file": "current.json",
            "performance_changes": {},
            "query_comparisons": [],
            "summary": {
                "total_queries_compared": 22,
                "improved_queries": 5,
                "regressed_queries": 3,
                "unchanged_queries": 14,
                "overall_assessment": "mixed",
            },
        }
        mock_exporter_class.return_value = mock_exporter

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path, current_path = self._write_empty_compare_inputs(tmpdir)
            result = runner.invoke(cli, ["compare", str(baseline_path), str(current_path)])

            assert result.exit_code == 0
            assert "BENCHMARK COMPARISON REPORT" in result.output

    @patch.object(_compare_module, "load_result_file")
    @patch.object(_compare_module, "ResultExporter")
    def test_compare_with_regression_detection(self, mock_exporter_class, mock_load):

        runner = CliRunner()
        self._set_loaded_result_pair(mock_load)

        mock_exporter = MagicMock()
        mock_exporter.compare_results.return_value = {
            "baseline_file": "baseline.json",
            "current_file": "current.json",
            "performance_changes": {
                "total_execution_time": {
                    "baseline": 10.0,
                    "current": 15.0,
                    "change_percent": 50.0,
                    "improved": False,
                }
            },
            "query_comparisons": [
                {
                    "query_id": "q1",
                    "baseline_time_ms": 100,
                    "current_time_ms": 200,
                    "change_percent": 100.0,
                    "improved": False,
                }
            ],
            "summary": {},
        }
        mock_exporter_class.return_value = mock_exporter

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path, current_path = self._write_empty_compare_inputs(tmpdir)
            result = runner.invoke(
                cli, ["compare", str(baseline_path), str(current_path), "--fail-on-regression", "10%"]
            )

            assert result.exit_code == ExitCode.REGRESSION
            assert "regression detected" in result.output.lower()

    def test_compare_json_output_format(self):

        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path = Path(tmpdir) / "baseline.json"
            current_path = Path(tmpdir) / "current.json"

            baseline_data = make_v2_result_dict(version="2.0", query_time_ms=100, total_ms=100)
            current_data = make_v2_result_dict(version="2.0", query_time_ms=100, total_ms=100)

            baseline_path.write_text(json.dumps(baseline_data))
            current_path.write_text(json.dumps(current_data))

            result = runner.invoke(cli, ["compare", str(baseline_path), str(current_path), "--format", "json"])

            assert result.exit_code in [0, 1]
            if result.exit_code == 0:
                try:
                    json.loads(result.stdout)
                except json.JSONDecodeError:
                    pytest.fail("Output is not valid JSON")

    def test_compare_invalid_threshold_format(self):

        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path, current_path = self._write_empty_compare_inputs(tmpdir)
            result = runner.invoke(
                cli, ["compare", str(baseline_path), str(current_path), "--fail-on-regression", "invalid"]
            )

            assert result.exit_code == 1
            assert "Invalid threshold format" in result.output

    def test_compare_help_includes_plan_options(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["compare", "--help"])

        assert result.exit_code == 0
        assert "--include-plans" in result.output
        assert "--plan-threshold" in result.output

    @patch.object(_compare_module, "load_result_file")
    @patch.object(_compare_module, "ResultExporter")
    @patch.object(_compare_module, "_compare_plans")
    def test_compare_with_include_plans_flag(self, mock_compare_plans, mock_exporter_class, mock_load):

        runner = CliRunner()
        self._set_loaded_result_pair(mock_load)

        mock_exporter = MagicMock()
        mock_exporter.compare_results.return_value = {
            "baseline_file": "baseline.json",
            "current_file": "current.json",
            "performance_changes": {},
            "query_comparisons": [],
            "summary": {
                "total_queries_compared": 22,
                "improved_queries": 5,
                "regressed_queries": 3,
                "unchanged_queries": 14,
                "overall_assessment": "mixed",
            },
        }
        mock_exporter_class.return_value = mock_exporter

        mock_compare_plans.return_value = {
            "plans_compared": 22,
            "plans_unchanged": 20,
            "plans_changed": 2,
            "regressions_detected": 0,
            "threshold_applied": 0.0,
            "query_plans": [],
            "regressions": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path, current_path = self._write_empty_compare_inputs(tmpdir)
            result = runner.invoke(cli, ["compare", str(baseline_path), str(current_path), "--include-plans"])

            assert result.exit_code == 0
            mock_compare_plans.assert_called_once()
            assert "QUERY PLAN ANALYSIS" in result.output

    @patch.object(_compare_module, "load_result_file")
    @patch.object(_compare_module, "ResultExporter")
    @patch.object(_compare_module, "_compare_plans")
    def test_compare_include_plans_no_plans_available(self, mock_compare_plans, mock_exporter_class, mock_load):

        runner = CliRunner()
        self._set_loaded_result_pair(mock_load)

        mock_exporter = MagicMock()
        mock_exporter.compare_results.return_value = {
            "baseline_file": "baseline.json",
            "current_file": "current.json",
            "performance_changes": {},
            "query_comparisons": [],
            "summary": {},
        }
        mock_exporter_class.return_value = mock_exporter

        mock_compare_plans.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path, current_path = self._write_empty_compare_inputs(tmpdir)
            result = runner.invoke(cli, ["compare", str(baseline_path), str(current_path), "--include-plans"])

            assert result.exit_code == 0
            assert "No query plans found" in result.output


class TestResultFileDiscovery:
    def test_result_file_metadata_class(self):

        from benchbox.cli.commands.compare import ResultFileMetadata

        meta = ResultFileMetadata(
            path=Path("/test/result.json"),
            benchmark="TPC-H Benchmark",
            benchmark_id="tpc_h_benchmark",
            platform="DuckDB",
            scale=0.01,
            timestamp="2025-12-15T10:30:00",
            execution_id="abc123",
        )

        assert meta.benchmark == "TPC-H Benchmark"
        assert meta.platform == "DuckDB"
        assert meta.scale == 0.01
        assert "2025-12-15" in meta.formatted_timestamp
        assert "result.json" in meta.short_path

    def test_result_file_metadata_formatted_timestamp_handles_invalid(self):

        from benchbox.cli.commands.compare import ResultFileMetadata

        meta = ResultFileMetadata(
            path=Path("/test/result.json"),
            benchmark="TPC-H",
            benchmark_id="tpch",
            platform="DuckDB",
            scale=1.0,
            timestamp="invalid-timestamp",
            execution_id="xyz",
        )

        ts = meta.formatted_timestamp
        assert ts == "invalid-timestam"

    def test_result_file_metadata_empty_timestamp(self):

        from benchbox.cli.commands.compare import ResultFileMetadata

        meta = ResultFileMetadata(
            path=Path("/test/result.json"),
            benchmark="TPC-H",
            benchmark_id="tpch",
            platform="DuckDB",
            scale=1.0,
            timestamp="",
            execution_id="xyz",
        )

        assert meta.formatted_timestamp == "Unknown"

    def test_discover_result_files_with_valid_files(self):

        from benchbox.cli.commands.compare import _discover_result_files_with_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            valid_result = make_v2_result_dict(
                version="2.0",
                benchmark_id="tpc_h",
                benchmark_name="TPC-H Benchmark",
                platform="DuckDB",
                execution_id="abc123",
                timestamp="2025-12-15T10:30:00",
                query_time_ms=500,
                total_queries=1,
                passed_queries=1,
                failed_queries=0,
                total_ms=500,
                queries=[{"id": "1", "ms": 500.0, "rows": 4}],
            )
            (tmppath / "valid_result.json").write_text(json.dumps(valid_result))

            invalid_result = {"benchmark": {"id": "test"}}
            (tmppath / "invalid_result.json").write_text(json.dumps(invalid_result))

            (tmppath / "not_json.txt").write_text("not json content")

            results = _discover_result_files_with_metadata(search_dirs=[tmppath])

            assert len(results) == 1
            assert results[0].benchmark == "TPC-H Benchmark"
            assert results[0].platform == "DuckDB"
            assert results[0].scale == 0.01

    def test_discover_result_files_excludes_manifests(self):

        from benchbox.cli.commands.compare import _discover_result_files_with_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            manifest = {
                "schema_version": "1.0",
                "type": "datagen_manifest",
                "files": [],
            }
            (tmppath / "_datagen_manifest.json").write_text(json.dumps(manifest))

            results = _discover_result_files_with_metadata(search_dirs=[tmppath])

            assert len(results) == 0

    def test_discover_result_files_sorted_by_timestamp(self):
        from benchbox.cli.commands.compare import _discover_result_files_with_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            for i, ts in enumerate(["2025-12-13", "2025-12-15", "2025-12-14"]):
                result = make_v2_result_dict(
                    version="2.0",
                    platform="DuckDB",
                    execution_id=f"run{i}",
                    timestamp=f"{ts}T10:00:00",
                    scale_factor=1.0,
                    query_time_ms=500,
                    total_queries=1,
                    passed_queries=1,
                    failed_queries=0,
                    total_ms=500,
                    queries=[{"id": "1", "ms": 500.0, "rows": 4}],
                )
                (tmppath / f"result_{i}.json").write_text(json.dumps(result))

            results = _discover_result_files_with_metadata(search_dirs=[tmppath])

            assert len(results) == 3
            assert "2025-12-15" in results[0].timestamp
            assert "2025-12-14" in results[1].timestamp
            assert "2025-12-13" in results[2].timestamp

    def test_discover_result_files_handles_empty_directory(self):

        from benchbox.cli.commands.compare import _discover_result_files_with_metadata

        with tempfile.TemporaryDirectory() as tmpdir:
            results = _discover_result_files_with_metadata(search_dirs=[Path(tmpdir)])
            assert len(results) == 0

    def test_discover_result_files_handles_nonexistent_directory(self):

        from benchbox.cli.commands.compare import _discover_result_files_with_metadata

        results = _discover_result_files_with_metadata(search_dirs=[Path("/nonexistent/path/that/does/not/exist")])
        assert len(results) == 0


class TestComparePlansDeprecation:
    def test_compare_plans_hidden_from_help(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "compare-plans" not in result.output

    def test_compare_plans_still_functional(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["compare-plans", "--help"])

        assert "Compare query plans" in result.output or "DEPRECATED" in result.output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
