# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCheckDepsCommand:
    def test_check_deps_command_exists(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "check-deps" in result.output

    def test_check_deps_help(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps", "--help"])

        assert result.exit_code == 0
        assert "Check dependency status" in result.output
        assert "--platform" in result.output
        assert "--verbose" in result.output
        assert "--matrix" in result.output

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.utils.dependencies.list_available_dependency_groups")
    def test_check_deps_overview(self, mock_list_groups, mock_check_deps):
        mock_groups = {
            "clickhouse": MagicMock(description="ClickHouse driver", packages=["clickhouse-driver"]),
            "databricks": MagicMock(description="Databricks connector", packages=["databricks-sql-connector"]),
            "cloud": MagicMock(),
            "all": MagicMock(),
        }
        mock_list_groups.return_value = mock_groups

        def mock_check(platform, packages):
            if platform == "clickhouse":
                return True, []
            elif platform == "databricks":
                return False, ["databricks-sql-connector"]
            return False, packages

        mock_check_deps.side_effect = mock_check

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps"])

        assert result.exit_code == 0
        assert "BenchBox Dependency Status" in result.output
        assert "✅" in result.output
        assert "❌" in result.output
        assert "Installation Guide" in result.output

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.utils.dependencies.list_available_dependency_groups")
    def test_check_deps_specific_platform_available(self, mock_list_groups, mock_check_deps):

        mock_groups = {
            "databricks": MagicMock(
                description="Databricks connector",
                packages=["databricks-sql-connector"],
                install_command='uv pip install "benchbox[databricks]"',
                use_cases=["Lakehouse analytics"],
            )
        }
        mock_list_groups.return_value = mock_groups
        mock_check_deps.return_value = (True, [])

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps", "--platform", "databricks"])

        assert result.exit_code == 0
        assert "✅ databricks dependencies are installed" in result.output

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.utils.dependencies.list_available_dependency_groups")
    @patch("benchbox.utils.dependencies.get_dependency_error_message")
    def test_check_deps_specific_platform_missing(self, mock_error_msg, mock_list_groups, mock_check_deps):

        mock_groups = {
            "databricks": MagicMock(
                install_command='uv pip install "benchbox[databricks]"',
            )
        }
        mock_list_groups.return_value = mock_groups
        mock_check_deps.return_value = (False, ["databricks-sql-connector"])

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps", "--platform", "databricks"])

        assert result.exit_code == 0
        assert "❌ databricks missing dependencies" in result.output
        assert "databricks-sql-connector" in result.output

    @patch("benchbox.utils.dependencies.list_available_dependency_groups")
    def test_check_deps_unknown_platform(self, mock_list_groups):

        mock_groups = {
            "databricks": MagicMock(),
            "bigquery": MagicMock(),
        }
        mock_list_groups.return_value = mock_groups

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps", "--platform", "nonexistent"])

        assert result.exit_code == 0
        assert "Unknown platform 'nonexistent'" in result.output
        assert "Available platforms:" in result.output

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.utils.dependencies.list_available_dependency_groups")
    def test_check_deps_verbose_specific_platform(self, mock_list_groups, mock_check_deps):

        mock_groups = {
            "databricks": MagicMock(
                description="Databricks SQL connector",
                packages=["databricks-sql-connector"],
                use_cases=["Lakehouse analytics", "Delta Lake"],
            )
        }
        mock_list_groups.return_value = mock_groups
        mock_check_deps.return_value = (True, [])

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps", "--platform", "databricks", "--verbose"])

        assert result.exit_code == 0
        assert "Description:" in result.output
        assert "Use cases:" in result.output
        assert "Required packages:" in result.output

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.utils.dependencies.list_available_dependency_groups")
    @patch("benchbox.utils.dependencies.get_installation_recommendations")
    def test_check_deps_verbose_overview(self, mock_recommendations, mock_list_groups, mock_check_deps):

        mock_groups = {
            "databricks": MagicMock(description="Databricks", packages=["databricks-sql-connector"]),
        }
        mock_list_groups.return_value = mock_groups
        mock_check_deps.return_value = (True, [])
        mock_recommendations.return_value = ['uv pip install "benchbox[cloud]"']

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps", "--verbose"])

        assert result.exit_code == 0
        assert "Installation Recommendations:" in result.output
        mock_recommendations.assert_called_once()

    def test_check_deps_case_insensitive_platform(self):

        with patch("benchbox.utils.dependencies.list_available_dependency_groups") as mock_list_groups:
            with patch("benchbox.utils.dependencies.check_platform_dependencies") as mock_check_deps:
                mock_groups = {
                    "databricks": MagicMock(
                        install_command='uv pip install "benchbox[databricks]"',
                    )
                }
                mock_list_groups.return_value = mock_groups
                mock_check_deps.return_value = (True, [])

                runner = CliRunner()
                result = runner.invoke(cli, ["check-deps", "--platform", "DATABRICKS"])

                assert result.exit_code == 0
                assert "✅ DATABRICKS dependencies are installed" in result.output

    @patch("benchbox.utils.dependencies.get_installation_scenarios")
    @patch("benchbox.utils.dependencies.get_installation_matrix_rows")
    def test_check_deps_matrix_flag(self, mock_matrix_rows, mock_scenarios):

        mock_matrix_rows.return_value = [
            (
                "Scenario A",
                "Databricks",
                "databricks",
                'uv pip install "benchbox[databricks]"',
                'python -m pip install "benchbox[databricks]"',
                'pipx install "benchbox[databricks]"',
            )
        ]
        mock_scenarios.return_value = []

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps", "--matrix"])

        assert result.exit_code == 0
        assert "BenchBox Installation Matrix" in result.output
        assert "Scenario A" in result.output
        mock_matrix_rows.assert_called_once()

    @patch("benchbox.utils.dependencies.get_installation_scenarios")
    @patch("benchbox.utils.dependencies.get_installation_matrix_rows")
    def test_check_deps_matrix_flag_with_tip(self, mock_matrix_rows, mock_scenarios):

        mock_matrix_rows.return_value = [
            (
                "Scenario B",
                "ClickHouse",
                "clickhouse",
                'uv pip install "benchbox[clickhouse]"',
                'python -m pip install "benchbox[clickhouse]"',
                'pipx install "benchbox[clickhouse]"',
            )
        ]
        mock_scenarios.return_value = [MagicMock(dependency_groups=["cloud", "clickhouse"])]

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps", "--matrix"])

        assert result.exit_code == 0
        assert "Combine extras with multiple --extra flags" in result.output


class TestCheckDepsIntegration:
    def test_check_deps_real_execution(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps"])

        assert result.exit_code == 0
        assert "BenchBox Dependency Status" in result.output

    def test_check_deps_real_platform_check(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps", "--platform", "databricks"])

        assert result.exit_code == 0
        assert (
            "✅ databricks dependencies are installed" in result.output
            or "❌ databricks missing dependencies" in result.output
        )

    def test_check_deps_verbose_real(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["check-deps", "--verbose"])

        assert result.exit_code == 0
        assert "Installation Recommendations:" in result.output
        assert "Installation Guide" in result.output


