# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli
from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager

_run_module = sys.modules["benchbox.cli.commands.run"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _run_obj():
    return {"config": ConfigManager()}


class TestQuietVerboseConflict:
    def test_quiet_and_verbose_together_exits_with_code_2(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--quiet", "--verbose", "--platform", "duckdb", "--benchmark", "tpch"],
            obj={},
        )

        assert result.exit_code == 2
        assert "cannot be used with" in result.output

    def test_quiet_and_double_verbose_together_exits_with_code_2(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--quiet", "-vv", "--platform", "duckdb", "--benchmark", "tpch"],
            obj={},
        )

        assert result.exit_code == 2
        assert "cannot be used with" in result.output


class TestInvalidPhaseValidation:
    def test_invalid_phase_name_rejected(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--phases", "bogus_phase"],
            obj={},
        )

        assert result.exit_code == 1
        assert "Invalid phases" in result.output
        assert "bogus_phase" in result.output

    def test_mixed_valid_and_invalid_phases_rejected(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--phases", "power,foobar"],
            obj={},
        )

        assert result.exit_code == 1
        assert "Invalid phases" in result.output
        assert "foobar" in result.output

    def test_valid_phases_shows_valid_list_on_error(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--platform", "duckdb", "--benchmark", "tpch", "--phases", "invalid"],
            obj={},
        )

        assert result.exit_code == 1
        assert "Valid phases" in result.output
        for phase in ("generate", "load", "warmup", "power", "throughput", "maintenance"):
            assert phase in result.output


class TestOfficialModeValidation:
    def test_official_mode_rejects_non_compliant_scale_factor(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--official", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01"],
            obj={},
        )

        assert result.exit_code == 1
        assert "not TPC-compliant" in result.output
        assert "0.01" in result.output

    def test_official_mode_warns_without_seed(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--official",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "1",
                "--phases",
                "generate",
            ],
            obj={},
        )

        assert "Warning" in result.output or "seed" in result.output.lower()

    def test_official_mode_shows_compliance_banner(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--official",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "1",
                "--seed",
                "42",
                "--phases",
                "generate",
            ],
            obj={},
        )

        assert "TPC-Compliant" in result.output or "TPC-allowed" in result.output
        assert "Seed: 42" in result.output


class TestTableModeAndTuningIncompatibility:
    def test_external_table_mode_with_tuned_rejected(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--table-mode",
                "external",
                "--tuning",
                "tuned",
            ],
            obj={},
        )

        assert result.exit_code == 1
        assert "incompatible" in result.output.lower()

    def test_external_table_mode_with_notuning_allowed(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--table-mode",
                "external",
                "--tuning",
                "notuning",
                "--phases",
                "generate",
            ],
            obj={},
        )

        assert "incompatible" not in result.output.lower()


class TestDryRunValidation:
    def test_dry_run_requires_platform_and_benchmark(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--dry-run", "/tmp/drytest"],
            obj=_run_obj(),
        )

        assert result.exit_code == 1
        assert "requires --platform and --benchmark" in result.output

    def test_dry_run_data_only_requires_benchmark(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--dry-run", "/tmp/drytest", "--phases", "generate"],
            obj=_run_obj(),
        )

        assert result.exit_code == 1
        assert "requires --benchmark" in result.output

    def test_dry_run_rejects_fractional_scale_above_one(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--dry-run",
                "/tmp/drytest",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "1.5",
            ],
            obj=_run_obj(),
        )

        assert result.exit_code == 1
        assert "must be whole integers" in result.output
        assert "1.5" in result.output

    def test_dry_run_accepts_integer_scale_factor(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--dry-run",
                "/tmp/drytest",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "1",
                "--phases",
                "power",
            ],
            obj=_run_obj(),
        )

        assert "must be whole integers" not in result.output

    def test_dry_run_accepts_fractional_scale_below_one(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--dry-run",
                "/tmp/drytest",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--phases",
                "power",
            ],
            obj=_run_obj(),
        )

        assert "must be whole integers" not in result.output

    def test_dry_run_rejects_invalid_query_subset_without_saving_preview(self, tmp_path):
        output_dir = tmp_path / "invalid-query-preview"
        result = CliRunner().invoke(
            run,
            [
                "--platform",
                "datafusion",
                "--benchmark",
                "tpchavoc",
                "--scale",
                "0.01",
                "--queries",
                "definitely-invalid",
                "--phases",
                "power",
                "--dry-run",
                str(output_dir),
                "--non-interactive",
            ],
            obj=_run_obj(),
        )

        assert result.exit_code == 1
        assert "Dry-run query extraction failed" in result.output
        assert "Invalid query" in result.output
        assert "definitely-invalid" in result.output
        assert "Traceback" not in result.output
        assert list(output_dir.glob("**/*")) == []

    def test_dry_run_joinorder_omitted_scale_uses_benchmark_default(self, tmp_path):
        runner = CliRunner()

        with patch("benchbox.cli.dryrun.DryRunExecutor") as dry_run_executor:
            dry_run_executor.return_value.execute_dry_run.return_value = MagicMock()
            dry_run_executor.return_value.save_dry_run_results.return_value = {}

            result = runner.invoke(
                run,
                [
                    "--dry-run",
                    str(tmp_path / "drytest"),
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "joinorder",
                    "--phases",
                    "generate",
                    "--non-interactive",
                ],
                obj=_run_obj(),
            )

        assert result.exit_code == 0, result.output
        benchmark_config = dry_run_executor.return_value.execute_dry_run.call_args.args[0]
        assert benchmark_config.name == "joinorder"
        assert benchmark_config.scale_factor == 1.0

    def test_dry_run_joinorder_explicit_unsupported_scale_still_rejected(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--dry-run",
                str(tmp_path / "drytest"),
                "--platform",
                "duckdb",
                "--benchmark",
                "joinorder",
                "--phases",
                "generate",
                "--scale",
                "0.01",
                "--non-interactive",
            ],
            obj=_run_obj(),
        )

        assert result.exit_code == 1
        assert "joinorder accepts scale_factor in [1.0]; got 0.01" in result.output

    def test_dry_run_unknown_benchmark_rejected(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--dry-run",
                "/tmp/drytest",
                "--platform",
                "duckdb",
                "--benchmark",
                "nonexistent_bench",
                "--scale",
                "0.01",
            ],
            obj=_run_obj(),
        )

        assert result.exit_code == 1
        assert "Unknown benchmark" in result.output or "nonexistent_bench" in result.output


class TestHelpTopicRendering:
    def test_run_help_shows_core_options(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])

        assert result.exit_code == 0
        assert "--platform" in result.output
        assert "--benchmark" in result.output
        assert "--scale" in result.output
        assert "--phases" in result.output
        assert "--dry-run" in result.output
        assert "--queries" in result.output
        assert "--tuning" in result.output

    def test_run_help_topic_all_shows_advanced_options(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help-topic", "all"])

        assert result.exit_code == 0
        assert "--capture-plans" in result.output
        assert "--mode" in result.output
        assert "--seed" in result.output
        assert "--validation" in result.output
        assert "--strict-translation" in result.output
        assert "--presort" in result.output

    def test_run_help_topic_examples_shows_usage_examples(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help-topic", "examples"])

        assert result.exit_code == 0
        assert "benchbox run" in result.output
        assert "duckdb" in result.output.lower() or "tpch" in result.output.lower()


class TestDryRunDataOnlyMode:
    def test_dry_run_data_only_skips_platform(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--dry-run",
                "/tmp/drytest",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--phases",
                "generate",
                "--scale",
                "0.01",
            ],
            obj=_run_obj(),
        )

        assert "Note:" in result.output or "ignored" in result.output.lower()


class TestDuplicatePhaseDedup:
    def test_duplicate_phases_do_not_cause_error(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--phases",
                "power,power",
            ],
            obj={},
        )

        assert "Invalid phases" not in result.output


class TestPlatformOptionsWithoutPlatform:
    def test_platform_option_without_platform_fails(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            [
                "--benchmark",
                "tpch",
                "--platform-option",
                "threads=4",
            ],
            obj={},
        )

        assert result.exit_code == 1
        assert "Platform options require a --platform selection" in result.output


class TestNonInteractiveModeValidation:
    def test_non_interactive_missing_benchmark_fails(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--non-interactive", "--platform", "duckdb", "--phases", "power"],
            obj={},
        )

        assert result.exit_code == 2
        assert "Non-interactive mode requires all parameters" in result.output
        assert "--benchmark" in result.output

    def test_non_interactive_missing_platform_for_query_phases(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--non-interactive", "--benchmark", "tpch", "--phases", "power"],
            obj={},
        )

        assert result.exit_code == 2
        assert "Non-interactive mode requires all parameters" in result.output
        assert "--platform" in result.output

    def test_non_interactive_data_only_does_not_require_platform(self):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["--non-interactive", "--benchmark", "tpch", "--phases", "generate"],
            obj={},
        )

        assert "Missing: --platform" not in result.output


class TestClientLinkLocalityOptions:
    def test_client_link_options_in_help(self):
        runner = CliRunner()
        result = runner.invoke(run, ["--help"])
        assert result.exit_code == 0
        assert "--client-region" in result.output
        assert "Attested client cloud region" in result.output
        assert "--client-cloud" in result.output
        assert "Attested client cloud provider" in result.output
        assert "--no-link-probe" in result.output
        assert "Disable post-benchmark statement overhead" in result.output

    def test_client_link_options_forwarded_to_benchmark_config(self):
        runner = CliRunner()
        captured_config = None

        def mock_execute(orchestrator, benchmark_config, *args, **kwargs):
            nonlocal captured_config
            captured_config = benchmark_config
            mock_result = MagicMock()
            mock_result.validation_status = "SUCCESS"
            return mock_result

        with (
            patch.object(_run_module, "_execute_orchestrated_run", side_effect=mock_execute),
            patch.object(_run_module, "_direct_handle_result"),
        ):
            result = runner.invoke(
                run,
                [
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--scale",
                    "0.01",
                    "--phases",
                    "power",
                    "--non-interactive",
                    "--client-region",
                    "us-east-1",
                    "--client-cloud",
                    "aws",
                    "--no-link-probe",
                ],
                obj=_run_obj(),
            )

        assert result.exit_code == 0, result.output
        assert captured_config is not None
        assert captured_config.client_region == "us-east-1"
        assert captured_config.client_cloud == "aws"
        assert captured_config.link_probe is False
        assert captured_config.options.get("client_region") == "us-east-1"
        assert captured_config.options.get("client_cloud") == "aws"
        assert captured_config.options.get("link_probe") is False

    def test_client_link_options_default_probe_true(self):
        runner = CliRunner()
        captured_config = None

        def mock_execute(orchestrator, benchmark_config, *args, **kwargs):
            nonlocal captured_config
            captured_config = benchmark_config
            mock_result = MagicMock()
            mock_result.validation_status = "SUCCESS"
            return mock_result

        with (
            patch.object(_run_module, "_execute_orchestrated_run", side_effect=mock_execute),
            patch.object(_run_module, "_direct_handle_result"),
        ):
            result = runner.invoke(
                run,
                [
                    "--platform",
                    "duckdb",
                    "--benchmark",
                    "tpch",
                    "--scale",
                    "0.01",
                    "--phases",
                    "power",
                    "--non-interactive",
                ],
                obj=_run_obj(),
            )

        assert result.exit_code == 0, result.output
        assert captured_config is not None
        assert captured_config.client_region is None
        assert captured_config.client_cloud is None
        assert captured_config.link_probe is True
        assert captured_config.options.get("link_probe") is True
