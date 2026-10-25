import json
import sys
import sys as _sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli

__import__("benchbox.cli.commands.shell")
_shell_module = _sys.modules["benchbox.cli.commands.shell"]
__import__("benchbox.cli.commands.datagen")
_datagen_module = _sys.modules["benchbox.cli.commands.datagen"]
__import__("benchbox.cli.commands.run_official")
_run_official_module = _sys.modules["benchbox.cli.commands.run_official"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDatagenCommand:
    def test_datagen_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "datagen" in result.output

    def test_datagen_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["datagen", "--help"])

        assert result.exit_code == 0
        assert "Generate benchmark data" in result.output
        assert "--benchmark" in result.output
        assert "--scale" in result.output
        assert "--output" in result.output
        assert "--seed" in result.output

    def test_datagen_requires_benchmark_and_scale(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["datagen"])

        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()


class TestAggregateCommand:
    def test_aggregate_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "aggregate" in result.output

    def test_aggregate_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["aggregate", "--help"])

        assert result.exit_code == 0
        assert "Aggregate multiple benchmark results" in result.output
        assert "--input-dir" in result.output
        assert "--output-file" in result.output
        assert "--benchmark" in result.output
        assert "--platform" in result.output

    def test_aggregate_requires_input_and_output(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["aggregate"])

        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()

    def test_aggregate_with_empty_directory(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "trends.csv"

            result = runner.invoke(cli, ["aggregate", "--input-dir", tmpdir, "--output-file", str(output_file)])

            assert result.exit_code == 1
            assert "No JSON result files found" in result.output

    def test_aggregate_success(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            result_file = Path(tmpdir) / "result1.json"
            result_data = {
                "schema_version": "1.0",
                "benchmark": {"id": "tpch", "name": "TPC-H"},
                "execution": {
                    "timestamp": "2024-01-01T00:00:00",
                    "platform": "DuckDB",
                    "duration_ms": 1000,
                },
                "configuration": {"scale_factor": 0.01},
                "results": {
                    "queries": {
                        "details": [
                            {
                                "id": 1,
                                "status": "SUCCESS",
                                "timing": {"execution_ms": 100},
                            }
                        ]
                    }
                },
            }
            result_file.write_text(json.dumps(result_data))

            output_file = Path(tmpdir) / "trends.csv"

            result = runner.invoke(cli, ["aggregate", "--input-dir", tmpdir, "--output-file", str(output_file)])

            assert result.exit_code == 0
            assert "Aggregation complete" in result.output
            assert output_file.exists()


class TestShellCommand:
    def test_shell_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "shell" in result.output

    def test_shell_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["shell", "--help"])

        assert result.exit_code == 0
        assert "interactive" in result.output.lower() and "sql" in result.output.lower()
        assert "--platform" in result.output
        assert "--database" in result.output
        assert "--benchmark" in result.output
        assert "--scale" in result.output
        assert "--list" in result.output
        assert "--last" in result.output
        assert "--output" in result.output

    def test_shell_no_databases_found(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(cli, ["shell", "--output", tmpdir])

            assert result.exit_code == 1
            assert "No databases found" in result.output

    def test_shell_list_flag(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "datagen" / "tpch_sf1"
            db_path.mkdir(parents=True)
            db_file = db_path / "tpch_sf1_none_none.duckdb"
            db_file.touch()

            result = runner.invoke(cli, ["shell", "--output", tmpdir, "--list"])

            assert result.exit_code == 0
            assert "tpch" in result.output.lower() or "Available" in result.output

    def test_shell_direct_database_path(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.duckdb"
            db_path.touch()

            with patch.object(_shell_module, "_launch_duckdb_shell") as mock_launch:
                runner.invoke(cli, ["shell", "--database", str(db_path)])

                mock_launch.assert_called_once()

    def test_shell_platform_autodetect(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.duckdb"
            db_path.touch()

            with patch.object(_shell_module, "_launch_duckdb_shell") as mock_launch:
                runner.invoke(cli, ["shell", "--database", str(db_path)])

                mock_launch.assert_called_once()

    def test_shell_sqlite_autodetect(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite"
            db_path.touch()

            with patch.object(_shell_module, "_launch_sqlite_shell") as mock_launch:
                runner.invoke(cli, ["shell", "--database", str(db_path)])

                mock_launch.assert_called_once()

    def test_shell_unsupported_platform_explicit(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite"
            db_path.touch()

            result = runner.invoke(cli, ["shell", "--platform", "unsupported", "--database", str(db_path)])

            assert result.exit_code == 1
            assert "not supported" in result.output.lower()

    def test_shell_benchmark_filter(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            datagen_dir = Path(tmpdir) / "datagen"
            tpch_dir = datagen_dir / "tpch_sf1"
            tpcds_dir = datagen_dir / "tpcds_sf1"
            tpch_dir.mkdir(parents=True)
            tpcds_dir.mkdir(parents=True)

            (tpch_dir / "tpch_sf1_none_none.duckdb").touch()
            (tpcds_dir / "tpcds_sf1_none_none.duckdb").touch()

            result = runner.invoke(cli, ["shell", "--output", tmpdir, "--benchmark", "tpch", "--list"])

            assert result.exit_code == 0

    def test_shell_scale_filter(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            datagen_dir = Path(tmpdir) / "datagen"
            dir1 = datagen_dir / "tpch_sf1"
            dir2 = datagen_dir / "tpch_sf10"
            dir1.mkdir(parents=True)
            dir2.mkdir(parents=True)

            (dir1 / "tpch_sf1_none_none.duckdb").touch()
            (dir2 / "tpch_sf10_none_none.duckdb").touch()

            result = runner.invoke(cli, ["shell", "--output", tmpdir, "--scale", "1.0", "--list"])

            assert result.exit_code == 0

    def test_shell_discovers_databases_in_multiple_locations(self):
        from benchbox.cli.config import DirectoryManager

        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            dir_mgr = DirectoryManager(base_dir=tmpdir)

            datagen_path = dir_mgr.get_datagen_path("tpch", 1.0)
            datagen_path.mkdir(parents=True, exist_ok=True)
            (datagen_path / "tpch_sf1_notuning.duckdb").touch()

            dir_mgr.databases_dir.mkdir(parents=True, exist_ok=True)
            (dir_mgr.databases_dir / "tpcds_sf10_notuning.duckdb").touch()

            result = runner.invoke(cli, ["shell", "--output", tmpdir, "--list"])

            assert result.exit_code == 0
            assert "tpch" in result.output.lower() or "Available" in result.output
            assert "tpcds" in result.output.lower() or "Available" in result.output


class TestMetricsCommand:
    def test_metrics_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "metrics" in result.output

    def test_metrics_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["metrics", "--help"])

        assert result.exit_code == 0
        assert "Calculate benchmark performance metrics" in result.output
        assert "qphh" in result.output

    def test_metrics_qphh_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["metrics", "qphh", "--help"])

        assert result.exit_code == 0
        assert "QphH" in result.output
        assert "--power-results" in result.output
        assert "--throughput-results" in result.output
        assert "--scale-factor" in result.output

    def test_metrics_qphh_requires_both_results(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["metrics", "qphh"])

        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()

    def test_metrics_qphh_success(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            power_file = Path(tmpdir) / "power.json"
            throughput_file = Path(tmpdir) / "throughput.json"

            power_data = {
                "environment": {"scale_factor": 1.0},
                "summary": {
                    "tpc_metrics": {"power_at_size": 36.0},
                    "timing": {"total_ms": 100000},
                },
            }
            throughput_data = {
                "environment": {"scale_factor": 1.0, "num_streams": 2},
                "run": {"streams": 2},
                "summary": {
                    "tpc_metrics": {"throughput_at_size": 36.0},
                    "timing": {"total_ms": 200000},
                },
            }

            power_file.write_text(json.dumps(power_data))
            throughput_file.write_text(json.dumps(throughput_data))

            result = runner.invoke(
                cli,
                ["metrics", "qphh", "--power-results", str(power_file), "--throughput-results", str(throughput_file)],
            )

            assert result.exit_code == 0
            assert "Power@Size" in result.output
            assert "Throughput@Size" in result.output
            assert "QphH@Size:" not in result.output

    def test_metrics_qphh_reports_unavailable_reasons_for_tpch_without_refresh(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            power_file = Path(tmpdir) / "power.json"
            throughput_file = Path(tmpdir) / "throughput.json"
            power_file.write_text(
                json.dumps(
                    {
                        "benchmark": {"id": "tpch", "name": "TPC-H"},
                        "environment": {"scale_factor": 1.0},
                        "summary": {"tpc_metrics": {"power_at_size": 36.0}},
                    }
                )
            )
            throughput_file.write_text(
                json.dumps(
                    {
                        "benchmark": {"id": "tpch", "name": "TPC-H"},
                        "environment": {"scale_factor": 1.0},
                        "run": {"streams": 2},
                        "summary": {"tpc_metrics": {"throughput_at_size": 36.0}},
                        "phases": {"throughput_test": {"status": "COMPLETED", "duration_ms": 1000}},
                    }
                )
            )

            result = runner.invoke(
                cli,
                ["metrics", "qphh", "--power-results", str(power_file), "--throughput-results", str(throughput_file)],
            )

            assert result.exit_code == 0
            assert "Power@Size:" in result.output
            assert "Throughput@Size:" in result.output
            assert "QphH@Size:" not in result.output
            assert "QphH@Size is not reported" in result.output
            assert "Power phase must contain successful RF1 and RF2 evidence" in result.output

    @pytest.mark.parametrize(
        ("power_summary", "throughput_summary", "expected"),
        [
            ({"tpc_metrics": {"suppressed": True, "reason": "compliance_class=unofficial_subscale"}}, {}, "suppressed"),
            ({}, {"queries": {"total": 44, "passed": 39, "failed": 5}}, "failed queries"),
        ],
    )
    def test_metrics_qphh_refuses_suppressed_or_failed_results(self, power_summary, throughput_summary, expected):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            power_file = Path(tmpdir) / "power.json"
            throughput_file = Path(tmpdir) / "throughput.json"
            power_file.write_text(
                json.dumps(
                    {
                        "environment": {"scale_factor": 1.0},
                        "summary": {"tpc_metrics": {"power_at_size": 36.0}, **power_summary},
                    }
                )
            )
            throughput_file.write_text(
                json.dumps(
                    {
                        "environment": {"scale_factor": 1.0},
                        "run": {"streams": 2},
                        "summary": {"tpc_metrics": {"throughput_at_size": 36.0}, **throughput_summary},
                    }
                )
            )

            result = runner.invoke(
                cli,
                ["metrics", "qphh", "--power-results", str(power_file), "--throughput-results", str(throughput_file)],
            )

            assert result.exit_code != 0
            assert expected in result.output
            assert "Throughput@Size:" not in result.output


class TestCalculateQphhCommand:
    def test_calculate_qphh_hidden_from_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "calculate-qphh" not in result.output

    def test_calculate_qphh_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["calculate-qphh", "--help"])

        assert result.exit_code == 0
        assert "Power@Size" in result.output
        assert "--power-results" in result.output
        assert "--throughput-results" in result.output
        assert "--scale-factor" in result.output

    def test_calculate_qphh_requires_both_results(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["calculate-qphh"])

        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()

    def test_calculate_qphh_shows_deprecation_warning(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            power_file = Path(tmpdir) / "power.json"
            throughput_file = Path(tmpdir) / "throughput.json"

            power_data = {
                "environment": {"scale_factor": 1.0},
                "summary": {
                    "tpc_metrics": {"power_at_size": 36.0},
                    "timing": {"total_ms": 100000},
                },
            }
            throughput_data = {
                "environment": {"scale_factor": 1.0, "num_streams": 2},
                "run": {"streams": 2},
                "summary": {
                    "tpc_metrics": {"throughput_at_size": 36.0},
                    "timing": {"total_ms": 200000},
                },
            }

            power_file.write_text(json.dumps(power_data))
            throughput_file.write_text(json.dumps(throughput_data))

            result = runner.invoke(
                cli,
                ["calculate-qphh", "--power-results", str(power_file), "--throughput-results", str(throughput_file)],
            )

            assert result.exit_code == 0
            assert "deprecated" in result.output.lower() or "DeprecationWarning" in result.output
            assert "Power@Size" in result.output
            assert "QphH@Size:" not in result.output


class TestRunOfficialFlag:
    def test_run_official_flag_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])

        assert result.exit_code == 0
        assert "--official" in result.output
        help_text = " ".join(result.output.split())
        assert "TPC official mode" in help_text
        assert "do not comply with the TPC Specification" in help_text
        assert "TPC-compliant" not in help_text

    def test_run_official_invalid_scale_factor(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["run", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.5", "--phases", "power", "--official"],
        )

        assert result.exit_code == 1
        assert "not a TPC-specified scale factor" in result.output

    def test_run_official_warns_on_missing_seed(self):
        runner = CliRunner()

        result = runner.invoke(
            cli,
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "1",
                "--phases",
                "power",
                "--official",
                "--dry-run",
                "/tmp/test",
            ],
        )

        assert "No --seed specified" in result.output or "seed" in result.output.lower()


class TestRunOfficialCommand:
    def test_run_official_hidden_from_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "run-official" not in result.output

    def test_run_official_still_functional(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["run-official", "--help"])

        assert "TPC-specified" in result.output or "DEPRECATED" in result.output

    def test_run_official_help_mentions_quiet(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["run-official", "--help"])
        assert result.exit_code == 0
        assert "--quiet" in result.output

    def test_run_official_shows_deprecation_warning(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["run-official", "tpch", "--platform", "duckdb", "--scale", "0.5", "--phases", "power"]
        )

        assert "deprecated" in result.output.lower() or "DeprecationWarning" in result.output

    def test_run_official_invalid_scale_factor(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["run-official", "tpch", "--platform", "duckdb", "--scale", "0.5", "--phases", "power"]
        )

        assert result.exit_code == 1
        assert "not a TPC-specified scale factor" in result.output


class TestDatagenParseRunArgs:
    def test_benchmark_and_scale(self):
        from benchbox.cli.commands.datagen import _parse_run_args

        result = _parse_run_args(["--benchmark", "tpch", "--scale", "0.1"])
        assert result["benchmark"] == "tpch"
        assert result["scale"] == 0.1

    def test_seed_parsed_as_int(self):
        from benchbox.cli.commands.datagen import _parse_run_args

        result = _parse_run_args(["--seed", "42"])
        assert result["seed"] == 42

    def test_verbose_is_boolean_true(self):
        from benchbox.cli.commands.datagen import _parse_run_args

        result = _parse_run_args(["--verbose"])
        assert result["verbose"] is True

    def test_all_three_together(self):
        from benchbox.cli.commands.datagen import _parse_run_args

        result = _parse_run_args(["--benchmark", "tpch", "--scale", "0.1", "--verbose"])
        assert result["benchmark"] == "tpch"
        assert result["scale"] == 0.1
        assert result["verbose"] is True


class TestDatagenCommandBranches:
    def test_non_parquet_format_note_in_console(self):
        from unittest.mock import MagicMock, patch as _patch

        from benchbox.cli.commands.datagen import _parse_run_args

        runner = CliRunner()
        with (
            _patch.object(_datagen_module, "console") as mock_console,
            _patch.object(_datagen_module, "run"),
        ):
            calls = []
            mock_console.print.side_effect = lambda *a, **k: calls.append(str(a))
            runner.invoke(
                cli,
                ["datagen", "--benchmark", "tpch", "--scale", "0.01", "--format", "csv"],
            )
        all_output = " ".join(calls)
        assert "csv" in all_output.lower() or "format" in all_output.lower()

    def test_seed_forwarded_in_run_args(self):
        from benchbox.cli.commands.datagen import _parse_run_args

        result = _parse_run_args(["--benchmark", "tpch", "--scale", "0.1", "--seed", "99"])
        assert result["seed"] == 99


class TestRunOfficialCommandBranches:
    def test_throughput_without_streams_exits_1(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["run-official", "tpch", "--platform", "duckdb", "--scale", "1", "--phases", "throughput"],
        )
        assert result.exit_code == 1
        assert "streams" in result.output.lower()

    def test_valid_scale_with_mock_run_shows_deprecation(self):
        runner = CliRunner()
        with patch.object(_run_official_module, "run") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(
                cli,
                ["run-official", "tpch", "--platform", "duckdb", "--scale", "1", "--phases", "power", "--seed", "42"],
                catch_exceptions=False,
            )
        assert "deprecated" in result.output.lower() or "DeprecationWarning" in result.output
        assert "validate_results" not in mock_run.call_args.kwargs
        assert mock_run.call_args.kwargs["validation"] is None

    def test_tpc_allowed_scale_factors_constant(self):
        from benchbox.cli.commands.run_official import TPC_ALLOWED_SCALE_FACTORS

        for sf in (1, 10, 100, 1000, 10000):
            assert sf in TPC_ALLOWED_SCALE_FACTORS

    def test_no_seed_shows_warning(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["run-official", "tpch", "--platform", "duckdb", "--scale", "5", "--phases", "power"],
        )
        assert "seed" in result.output.lower() or "not a TPC-specified scale factor" in result.output

    def test_validate_results_forwarded(self):
        from benchbox.cli.composite_params import ValidationConfig

        runner = CliRunner()
        with patch.object(_run_official_module, "run") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(
                cli,
                [
                    "run-official",
                    "tpch",
                    "--platform",
                    "duckdb",
                    "--scale",
                    "1",
                    "--phases",
                    "power",
                    "--seed",
                    "42",
                    "--validate-results",
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert "validate_results" not in mock_run.call_args.kwargs
        validation = mock_run.call_args.kwargs["validation"]
        assert isinstance(validation, ValidationConfig)
        assert validation.mode == "exact"
        assert validation.preflight is True
        assert validation.postgen is True
        assert validation.postload is True
        assert validation.check_platforms is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
