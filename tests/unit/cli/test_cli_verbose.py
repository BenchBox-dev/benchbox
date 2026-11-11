# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.main import run, setup_verbose_logging

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestVerboseLogging:
    def test_setup_verbose_logging_enabled(self):

        logger, settings = setup_verbose_logging(verbose=True)

        assert logger is not None
        assert settings.verbose_enabled is True
        assert settings.very_verbose is True

        root_logger = logging.getLogger()
        assert root_logger.level <= logging.DEBUG

    def test_setup_verbose_logging_disabled(self):

        logger, settings = setup_verbose_logging(verbose=False)

        assert logger is None
        assert settings.verbose_enabled is False
        assert settings.level == 0

    def test_verbose_logging_third_party_suppression(self):

        setup_verbose_logging(verbose=True)

        urllib3_logger = logging.getLogger("urllib3")
        requests_logger = logging.getLogger("requests")
        sqlalchemy_logger = logging.getLogger("sqlalchemy")

        assert urllib3_logger.level >= logging.WARNING
        assert requests_logger.level >= logging.WARNING
        assert sqlalchemy_logger.level >= logging.INFO

    def test_verbose_logging_function_exists(self):

        assert callable(setup_verbose_logging)

        logger_verbose, verbose_settings = setup_verbose_logging(verbose=True)
        logger_normal, normal_settings = setup_verbose_logging(verbose=False)

        assert logger_verbose is not None
        assert logger_normal is None
        assert verbose_settings.verbose_enabled is True
        assert normal_settings.verbose_enabled is False


class TestCLIVerboseMode:
    def setup_method(self):
        self.runner = CliRunner()
        self.temp_dir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        import shutil

        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("benchbox.cli.main.ConfigManager")
    @patch("benchbox.cli.orchestrator.BenchmarkOrchestrator")
    def test_verbose_flag_short_form(self, mock_orchestrator, mock_config_manager):

        mock_config = Mock()
        mock_config.config_path = "test_config.yaml"
        mock_config.validate_config.return_value = True
        mock_config_manager.return_value = mock_config

        mock_orch = Mock()
        mock_orch.run_guided_benchmark.return_value = None
        mock_orchestrator.return_value = mock_orch

        result = self.runner.invoke(run, ["-v", "--non-interactive"])

        assert result.exit_code in [0, 1]

    @patch("benchbox.cli.main.ConfigManager")
    @patch("benchbox.cli.orchestrator.BenchmarkOrchestrator")
    def test_verbose_flag_long_form(self, mock_orchestrator, mock_config_manager):

        mock_config = Mock()
        mock_config.config_path = "test_config.yaml"
        mock_config.validate_config.return_value = True
        mock_config_manager.return_value = mock_config

        mock_orch = Mock()
        mock_orch.run_guided_benchmark.return_value = None
        mock_orchestrator.return_value = mock_orch

        result = self.runner.invoke(run, ["--verbose", "--non-interactive"])

        assert result.exit_code in [0, 1]

    def test_verbose_without_flag_help_works(self):

        result = self.runner.invoke(run, ["--help"])

        assert result.exit_code == 0
        assert "--verbose" in result.output
        assert "-v" in result.output

    def test_quiet_conflicts_with_verbose(self):
        result = self.runner.invoke(run, ["-v", "--quiet", "--non-interactive"])

        assert result.exit_code == 2
        assert "--quiet cannot be used with -v/-vv" in result.output


class TestVerboseModeIntegration:
    def test_verbose_logging_infrastructure_works(self):

        logger, settings = setup_verbose_logging(verbose=True)

        assert logger is not None
        assert settings.verbose_enabled is True

    def test_verbose_logging_with_different_loggers(self):

        setup_verbose_logging(verbose=True)

        loggers = [
            "benchbox.cli.main",
            "benchbox.cli.orchestrator",
            "benchbox.platforms.duckdb",
            "benchbox.base",
        ]

        for logger_name in loggers:
            logger = logging.getLogger(logger_name)
            assert logger is not None
            assert hasattr(logger, "debug")
            assert hasattr(logger, "info")


class TestVerboseModeExamples:
    def setup_method(self):
        self.runner = CliRunner()

    @patch("benchbox.cli.main.ConfigManager")
    @patch("benchbox.cli.orchestrator.BenchmarkOrchestrator")
    def test_verbose_with_database_and_benchmark_flags(self, mock_orchestrator, mock_config_manager):

        mock_config = Mock()
        mock_config.config_path = "test_config.yaml"
        mock_config.validate_config.return_value = True
        mock_config_manager.return_value = mock_config

        mock_orch = Mock()
        mock_orch.run_guided_benchmark.return_value = None
        mock_orchestrator.return_value = mock_orch

        result = self.runner.invoke(
            run,
            [
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--verbose",
                "--non-interactive",
            ],
        )

        assert result.exit_code in [0, 1]

    @patch("benchbox.cli.main.ConfigManager")
    @patch("benchbox.cli.orchestrator.BenchmarkOrchestrator")
    def test_verbose_short_flag_with_options(self, mock_orchestrator, mock_config_manager):

        mock_config = Mock()
        mock_config.config_path = "test_config.yaml"
        mock_config.validate_config.return_value = True
        mock_config_manager.return_value = mock_config

        mock_orch = Mock()
        mock_orch.run_guided_benchmark.return_value = None
        mock_orchestrator.return_value = mock_orch

        result = self.runner.invoke(
            run,
            [
                "-v",
                "--platform",
                "sqlite",
                "--benchmark",
                "ssb",
                "--scale",
                "0.01",
                "--non-interactive",
            ],
        )

        assert result.exit_code in [0, 1]


class TestVerboseLoggingCoverage:
    def test_verbose_logging_infrastructure_complete(self):

        assert callable(setup_verbose_logging)

        verbose_logger, verbose_settings = setup_verbose_logging(verbose=True)
        normal_logger, normal_settings = setup_verbose_logging(verbose=False)

        assert verbose_logger is not None
        assert verbose_settings.verbose_enabled is True
        assert normal_logger is None
        assert normal_settings.verbose_enabled is False

    def test_verbose_mode_help_text_includes_examples(self):

        runner = CliRunner()
        result = runner.invoke(run, ["--help"])

        assert "--verbose" in result.output
        assert "-v" in result.output
        assert "Verbose output" in result.output or "verbose" in result.output.lower()

    def test_verbose_logging_with_different_levels(self):

        logger_verbose, verbose_settings = setup_verbose_logging(verbose=True)
        assert logger_verbose is not None
        assert verbose_settings.verbose_enabled is True

        logger_normal, normal_settings = setup_verbose_logging(verbose=False)
        assert logger_normal is None
        assert normal_settings.verbose_enabled is False

        assert callable(logger_verbose.debug)
        assert callable(logger_verbose.info)
        assert callable(logger_verbose.warning)
        assert callable(logger_verbose.error)

    def test_setup_verbose_logging_return_values(self):

        verbose_logger, verbose_settings = setup_verbose_logging(verbose=True)
        normal_logger, normal_settings = setup_verbose_logging(verbose=False)

        assert verbose_logger is not None
        assert verbose_settings.verbose_enabled is True
        assert normal_logger is None
        assert normal_settings.verbose_enabled is False

        assert hasattr(verbose_logger, "debug")
        assert hasattr(verbose_logger, "info")
        assert hasattr(verbose_logger, "warning")
        assert hasattr(verbose_logger, "error")

    def test_verbose_logging_repeated_calls(self):

        logger1, settings1 = setup_verbose_logging(verbose=True)
        logger2, settings2 = setup_verbose_logging(verbose=False)
        logger3, settings3 = setup_verbose_logging(verbose=True)

        assert logger1 is not None
        assert settings1.verbose_enabled is True
        assert logger2 is None
        assert settings2.verbose_enabled is False
        assert logger3 is not None
        assert settings3.verbose_enabled is True


