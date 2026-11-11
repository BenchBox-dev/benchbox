# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def runner():
    return CliRunner()


class TestTuningGroup:
    def test_tuning_help(self, runner):

        result = runner.invoke(cli, ["tuning", "--help"])
        assert result.exit_code == 0
        assert "init" in result.output
        assert "validate" in result.output
        assert "defaults" in result.output
        assert "platforms" in result.output

    def test_tuning_init_help(self, runner):

        result = runner.invoke(cli, ["tuning", "init", "--help"])
        assert result.exit_code == 0
        assert "--platform" in result.output
        assert "--mode" in result.output
        assert "--profile" in result.output
        assert "--output" in result.output
        assert "--smart-defaults" in result.output

    def test_tuning_validate_help(self, runner):

        result = runner.invoke(cli, ["tuning", "validate", "--help"])
        assert result.exit_code == 0
        assert "--platform" in result.output
        assert "CONFIG_FILE" in result.output

    def test_tuning_defaults_help(self, runner):

        result = runner.invoke(cli, ["tuning", "defaults", "--help"])
        assert result.exit_code == 0
        assert "--platform" in result.output

    def test_tuning_platforms_help(self, runner):

        result = runner.invoke(cli, ["tuning", "platforms", "--help"])
        assert result.exit_code == 0


class TestTuningInit:
    def test_init_requires_platform(self, runner):

        result = runner.invoke(cli, ["tuning", "init"])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()

    def test_init_auto_mode_sql(self, runner):

        result = runner.invoke(cli, ["tuning", "init", "--platform", "duckdb", "--help"])
        assert result.exit_code == 0

    def test_init_auto_mode_dataframe(self, runner):

        result = runner.invoke(cli, ["tuning", "init", "--platform", "polars", "--help"])
        assert result.exit_code == 0

    def test_init_invalid_dataframe_platform(self, runner):

        result = runner.invoke(cli, ["tuning", "init", "--platform", "duckdb", "--mode", "dataframe"])
        assert result.exit_code != 0
        assert "does not support DataFrame mode" in result.output


class TestTuningDefaults:
    def test_defaults_requires_platform(self, runner):

        result = runner.invoke(cli, ["tuning", "defaults"])
        assert result.exit_code != 0

    def test_defaults_polars(self, runner):

        result = runner.invoke(cli, ["tuning", "defaults", "--platform", "polars"])
        assert result.exit_code == 0
        assert "Smart Defaults" in result.output
        assert "polars" in result.output.lower()
        assert "System Profile" in result.output


class TestTuningPlatforms:
    def test_platforms_lists_sql_and_dataframe(self, runner):

        result = runner.invoke(cli, ["tuning", "platforms"])
        assert result.exit_code == 0
        assert "SQL Platforms" in result.output
        assert "DataFrame Platforms" in result.output
        assert "duckdb" in result.output.lower()
        assert "snowflake" in result.output.lower()
        assert "polars" in result.output.lower()
        assert "pandas" in result.output.lower()


class TestRetiredShimsNotRegistered:
    def test_create_sample_tuning_is_not_registered(self):
        assert cli.commands.get("create-sample-tuning") is None

    def test_df_tuning_is_not_registered(self):
        assert cli.commands.get("df-tuning") is None

    def test_tuning_is_not_hidden(self):
        cmd = cli.commands.get("tuning")
        assert cmd is not None, "tuning should be registered"
        assert not cmd.hidden, "tuning should not be hidden"


class TestCommandCategorization:
    def test_tuning_in_configuration_category(self, runner):

        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "tuning" in result.output
        assert "create-sample-tuning" not in result.output
        assert "df-tuning" not in result.output


