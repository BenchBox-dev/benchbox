# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.utils.dependencies import (
    DEPENDENCY_GROUPS,
    PLATFORM_TO_EXTRA,
    DependencyInfo,
    InstallationScenario,
    check_platform_dependencies,
    get_dependency_decision_tree,
    get_dependency_error_message,
    get_dependency_group_packages,
    get_install_command,
    get_installation_matrix_rows,
    get_installation_recommendations,
    get_installation_scenarios,
    is_development_install,
    list_available_dependency_groups,
    validate_dependency_group,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDependencyInfo:
    def test_dependency_info_creation(self):
        info = DependencyInfo(
            name="test",
            description="Test platform",
            packages=["test-package"],
            install_command='uv pip install "benchbox[test]"',
            use_cases=["Testing"],
            platforms=["Test Platform"],
        )

        assert info.name == "test"
        assert info.description == "Test platform"
        assert info.packages == ["test-package"]
        assert info.install_command == 'uv pip install "benchbox[test]"'
        assert info.use_cases == ["Testing"]
        assert info.platforms == ["Test Platform"]

    def test_extra_name_defaults_to_name_when_command_has_no_extra_flag(self):
        info = DependencyInfo(
            name="test",
            description="Test platform",
            packages=["test-package"],
            install_command="uv add benchbox",
            use_cases=["Testing"],
            platforms=["Test Platform"],
        )

        assert info.extra_name == "test"

    def test_extra_name_follows_install_command_for_retained_aliases(self):
        info = DependencyInfo(
            name="databricks-connect",
            description="Legacy alias",
            packages=["databricks-sql-connector"],
            install_command="uv add benchbox --extra cloud-spark-databricks",
            use_cases=["Testing"],
            platforms=["Databricks"],
        )

        assert info.extra_name == "cloud-spark-databricks"
        message = info.get_install_message()
        assert "cloud-spark-databricks" in message
        assert "databricks-connect" not in message

    def test_databricks_connect_group_installs_the_real_extra(self):
        info = DEPENDENCY_GROUPS["databricks-connect"]

        assert info.extra_name == "cloud-spark-databricks"
        assert "databricks-connect" not in info.get_install_message()


class TestInstallationScenario:
    def test_command_generation_with_extras(self):
        scenario = InstallationScenario(
            name="Test",
            description="Desc",
            platforms=["Example"],
            dependency_groups=["cloud", "clickhouse"],
        )

        assert scenario.extras_label == "cloud,clickhouse"
        assert scenario.uv_command == "uv add benchbox --extra cloud --extra clickhouse"
        assert scenario.uv_pip_command == 'uv pip install "benchbox[cloud,clickhouse]"'
        assert scenario.pip_command == 'python -m pip install "benchbox[cloud,clickhouse]"'
        assert scenario.pipx_command == 'pipx install "benchbox[cloud,clickhouse]"'

    def test_command_generation_core(self):
        scenario = InstallationScenario(
            name="Core",
            description="",
            platforms=["DuckDB"],
            dependency_groups=[],
        )

        assert scenario.extras_label == "core"
        assert scenario.uv_command == "uv add benchbox"
        assert scenario.uv_pip_command == "uv pip install benchbox"
        assert scenario.pip_command == "python -m pip install benchbox"
        assert scenario.pipx_command == "pipx install benchbox"


class TestDependencyGroups:
    def test_all_expected_groups_exist(self):
        expected_groups = {
            "clickhouse",
            "databricks",
            "databricks-df",
            "databricks-connect",
            "cloud-spark",
            "bigquery",
            "redshift",
            "snowflake",
            "trino",
            "presto",
            "postgresql",
            "synapse",
            "fabric",
            "fabric-spark",
            "synapse-spark",
            "athena",
            "athena-spark",
            "glue",
            "emr-serverless",
            "dataproc",
            "dataproc-serverless",
            "snowpark-connect",
            "spark",
            "firebolt",
            "influxdb",
            "cloudstorage",
            "cloud",
            "all",
        }

        for group in expected_groups:
            assert group in DEPENDENCY_GROUPS, f"Missing expected group: {group}"

    def test_group_structure(self):
        for name, info in DEPENDENCY_GROUPS.items():
            assert isinstance(info, DependencyInfo)
            assert info.name == name
            assert isinstance(info.description, str)
            assert isinstance(info.packages, list)
            assert len(info.packages) > 0
            assert isinstance(info.install_command, str)
            assert "benchbox" in info.install_command
            assert "--extra" in info.install_command or info.install_command == "uv add benchbox"
            assert isinstance(info.use_cases, list)
            assert isinstance(info.platforms, list)

    def test_cloud_group_excludes_clickhouse(self):
        cloud_packages = DEPENDENCY_GROUPS["cloud"].packages
        assert "clickhouse-driver" not in cloud_packages

    def test_all_group_includes_everything(self):
        all_packages = set(DEPENDENCY_GROUPS["all"].packages)

        for name, info in DEPENDENCY_GROUPS.items():
            if name not in ["cloud", "all"]:
                for package in info.packages:
                    assert package in all_packages

    def test_cloudpathlib_in_cloud_platforms(self):
        cloud_platforms = ["databricks", "bigquery", "redshift", "snowflake"]

        for platform in cloud_platforms:
            packages = DEPENDENCY_GROUPS[platform].packages
            assert "cloudpathlib" in packages


class TestDependencyHelperFunctions:
    def test_get_dependency_group_packages_returns_copy(self):
        packages = get_dependency_group_packages("databricks")
        assert "databricks-sql-connector" in packages

        packages.append("sentinel")
        refreshed = get_dependency_group_packages("databricks")

        assert "sentinel" not in refreshed
        assert "cloudpathlib" in refreshed

    def test_get_dependency_group_packages_unknown(self):
        assert get_dependency_group_packages("unknown") == []


class TestInstallationScenarioRegistry:
    def test_scenarios_cover_expected_cases(self):
        scenarios = get_installation_scenarios()
        names = {scenario.name for scenario in scenarios}

        assert "Local development (core)" in names
        assert "Cloud storage helpers" in names
        assert "Cloud platforms bundle" in names
        assert "Full platform coverage" in names

    def test_matrix_rows_align_with_scenarios(self):
        scenarios = {scenario.name: scenario for scenario in get_installation_scenarios()}
        matrix = get_installation_matrix_rows()

        assert len(matrix) == len(scenarios)

        for name, _platforms, extras, uv_command, pip_command, pipx_command in matrix:
            scenario = scenarios[name]
            assert extras == scenario.extras_label
            assert uv_command == scenario.uv_command
            assert pip_command == scenario.pip_command
            assert pipx_command == scenario.pipx_command


class TestCheckPlatformDependencies:
    @pytest.mark.parametrize("error_type", [None, ImportError, OSError])
    def test_probe_restores_import_cwd(self, tmp_path, error_type):
        original_cwd = Path.cwd()

        def import_with_native_side_effect(module_name):
            assert module_name == "chdb"
            os.chdir(tmp_path)
            if error_type is not None:
                raise error_type("native library unavailable")
            return MagicMock()

        try:
            with patch("builtins.__import__", side_effect=import_with_native_side_effect):
                if error_type is OSError:
                    with pytest.raises(OSError, match="native library unavailable"):
                        check_platform_dependencies("clickhouse-local", ["chdb"])
                else:
                    result = check_platform_dependencies("clickhouse-local", ["chdb"])
                    assert result == (error_type is None, [] if error_type is None else ["chdb"])
            assert Path.cwd() == original_cwd
        finally:
            os.chdir(original_cwd)

    def test_check_available_packages(self):
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            available, missing = check_platform_dependencies("test", ["os", "sys"])

            assert available is True
            assert missing == []

    def test_check_missing_packages(self):
        with patch("builtins.__import__") as mock_import:
            mock_import.side_effect = ImportError("Module not found")

            available, missing = check_platform_dependencies("test", ["nonexistent", "alsomissing"])

            assert available is False
            assert set(missing) == {"nonexistent", "alsomissing"}

    def test_check_mixed_packages(self):

        def mock_import(module_name):
            if module_name.replace("-", "_") in ["os", "sys"]:
                return MagicMock()
            raise ImportError("Module not found")

        with patch("builtins.__import__", side_effect=mock_import):
            available, missing = check_platform_dependencies("test", ["os", "nonexistent", "sys"])

            assert available is False
            assert missing == ["nonexistent"]

    def test_package_name_normalization(self):
        with patch("builtins.__import__") as mock_import:
            mock_import.return_value = MagicMock()

            available, missing = check_platform_dependencies("test", ["package-with-hyphens"])

            mock_import.assert_called_with("package_with_hyphens")
            assert available is True

    def test_default_group_lookup(self):
        with patch("builtins.__import__") as mock_import:
            mock_import.side_effect = ImportError("Module not found")

            available, missing = check_platform_dependencies("cloudstorage")

            assert available is False
            assert missing == ["cloudpathlib"]


class TestGetDependencyErrorMessage:
    def test_known_platform_error_message(self):
        missing = ["databricks-sql-connector"]
        message = get_dependency_error_message("databricks", missing)

        assert "Missing dependencies for databricks platform" in message
        assert "Extra: benchbox[databricks]" in message
        assert "databricks-sql-connector" in message
        assert "pip install 'benchbox[databricks]'" in message
        assert "uv add benchbox --extra databricks" in message
        assert "Need more guidance? Run: benchbox check-deps --platform databricks" in message
        assert "Databricks-specific" in message.lower() or "Unity Catalog" in message

    def test_unknown_platform_error_message(self):
        missing = ["unknown-package"]
        message = get_dependency_error_message("unknown", missing)

        assert "Missing required dependencies for unknown" in message
        assert "unknown-package" in message
        assert "uv pip install unknown-package" in message

    def test_multiple_missing_packages(self):
        missing = ["google-cloud-bigquery", "google-cloud-storage"]
        message = get_dependency_error_message("bigquery", missing)

        assert "google-cloud-bigquery" in message
        assert "google-cloud-storage" in message


class TestGetInstallationRecommendations:
    def test_cloud_use_case_recommendations(self):
        recommendations = get_installation_recommendations("cloud analytics")

        assert any("cloud" in rec.lower() for rec in recommendations)
        assert len(recommendations) > 0

    def test_databricks_use_case_recommendations(self):
        recommendations = get_installation_recommendations("databricks lakehouse")

        assert any("databricks" in rec.lower() for rec in recommendations)

    def test_generic_recommendations(self):
        recommendations = get_installation_recommendations()

        assert len(recommendations) > 0
        assert any("cloud" in rec for rec in recommendations)
        assert any("all" in rec for rec in recommendations)

    def test_multiple_platform_recommendations(self):
        recommendations = get_installation_recommendations()

        platforms = [
            "cloud",
            "all",
            "cloudstorage",
            "databricks",
            "bigquery",
            "redshift",
            "snowflake",
            "clickhouse",
        ]
        for platform in platforms:
            assert any(platform in rec for rec in recommendations)


class TestListAvailableDependencyGroups:
    def test_returns_copy_of_groups(self):
        groups1 = list_available_dependency_groups()
        groups2 = list_available_dependency_groups()

        assert groups1 is not groups2
        assert groups1 == groups2

    def test_all_groups_included(self):
        groups = list_available_dependency_groups()

        expected = set(DEPENDENCY_GROUPS.keys())
        actual = set(groups.keys())

        assert actual == expected


class TestValidateDependencyGroup:
    def test_valid_group_names(self):
        valid_groups = [
            "clickhouse",
            "databricks",
            "bigquery",
            "redshift",
            "snowflake",
            "cloudstorage",
            "cloud",
            "all",
        ]

        for group in valid_groups:
            assert validate_dependency_group(group) is True

    def test_case_insensitive_validation(self):
        assert validate_dependency_group("DATABRICKS") is True
        assert validate_dependency_group("BigQuery") is True
        assert validate_dependency_group("clickHouse") is True

    def test_invalid_group_names(self):
        invalid_groups = ["nonexistent", "mysql", "postgres", ""]

        for group in invalid_groups:
            assert validate_dependency_group(group) is False


class TestGetDependencyDecisionTree:
    def test_decision_tree_content(self):
        tree = get_dependency_decision_tree()

        assert "Installation Guide" in tree
        assert "Quick Start" in tree
        assert "Cloud Storage Paths" in tree
        assert "Cloud Platform Specific" in tree
        assert "Scenarios:" in tree

        assert "uv add benchbox --extra cloud" in tree
        assert "uv add benchbox --extra all" in tree
        assert "uv add benchbox --extra databricks" in tree
        assert "uv add benchbox --extra cloudstorage" in tree
        assert "Alternative:" in tree or "pip-compatible" in tree

    def test_decision_tree_formatting(self):
        tree = get_dependency_decision_tree()

        assert "\n" in tree
        assert "└──" in tree or "├──" in tree or "•" in tree

    def test_decision_tree_includes_all_platforms(self):
        tree = get_dependency_decision_tree()

        platforms = ["Databricks", "BigQuery", "Redshift", "Snowflake", "ClickHouse"]
        for platform in platforms:
            assert platform in tree


class TestDependencyIntegration:
    def test_real_platform_dependency_check(self):
        available, missing = check_platform_dependencies("test", ["os"])
        assert available is True
        assert missing == []

    def test_error_message_integration(self):
        for platform_name in DEPENDENCY_GROUPS:
            if platform_name in ["all", "cloud"]:
                continue

            dep_info = DEPENDENCY_GROUPS[platform_name]
            message = get_dependency_error_message(platform_name, dep_info.packages)

            assert platform_name in message.lower()
            assert "install" in message.lower()
            assert f"benchbox[{platform_name}]" in message

    def test_complete_workflow(self):
        groups = list_available_dependency_groups()
        assert len(groups) > 0

        assert validate_dependency_group("databricks") is True

        recommendations = get_installation_recommendations("cloud")
        assert len(recommendations) > 0

        tree = get_dependency_decision_tree()
        assert len(tree) > 100


class TestInstallCommandDetection:
    def test_is_development_install_returns_bool(self):
        result = is_development_install()
        assert isinstance(result, bool)

    def test_is_development_install_cached(self):
        result1 = is_development_install()
        result2 = is_development_install()
        assert result1 == result2

    def test_get_install_command_development(self):
        with (
            patch("benchbox.utils.dependencies.is_development_install", return_value=True),
            patch("benchbox.utils.dependencies.is_uv_tool_environment", return_value=False),
        ):
            cmd = get_install_command("athena")
            assert cmd == "uv sync --extra athena"

    def test_get_install_command_development_uv_tool(self):
        fake_python = "/tmp/uv/tools/benchbox/bin/python3"
        with (
            patch("benchbox.utils.dependencies.is_development_install", return_value=True),
            patch("benchbox.utils.dependencies.is_uv_tool_environment", return_value=True),
            patch("benchbox.utils.dependencies.sys.executable", fake_python),
        ):
            cmd = get_install_command("athena")
            assert cmd == f'uv pip install --python "{fake_python}" "benchbox[athena]"'

    def test_get_install_command_package(self):
        with patch("benchbox.utils.dependencies.is_development_install", return_value=False):
            cmd = get_install_command("athena")
            assert cmd == 'uv pip install "benchbox[athena]"'

    def test_get_install_command_various_extras(self):
        extras = ["cloud", "databricks", "snowflake", "bigquery", "redshift"]

        with (
            patch("benchbox.utils.dependencies.is_development_install", return_value=True),
            patch("benchbox.utils.dependencies.is_uv_tool_environment", return_value=False),
        ):
            for extra in extras:
                cmd = get_install_command(extra)
                assert f"--extra {extra}" in cmd

        with patch("benchbox.utils.dependencies.is_development_install", return_value=False):
            for extra in extras:
                cmd = get_install_command(extra)
                assert f"benchbox[{extra}]" in cmd

    def test_get_install_command_databricks_connect_uses_replacement_extra(self):
        assert PLATFORM_TO_EXTRA["databricks-connect"] == "cloud-spark-databricks"

        with patch("benchbox.utils.dependencies.is_development_install", return_value=False):
            cmd = get_install_command("databricks-connect")

        assert cmd == 'uv pip install "benchbox[cloud-spark-databricks]"'

    @pytest.mark.parametrize("platform", ["timescaledb", "pg-duckdb", "pg-mooncake", "paradedb", "citus", "cedardb"])
    def test_get_install_command_psycopg_platforms_use_the_postgresql_extra(self, platform: str):
        import tomllib

        pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
        extras = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["optional-dependencies"]
        assert "postgresql" in extras
        assert platform not in extras

        with (
            patch("benchbox.utils.dependencies.is_development_install", return_value=True),
            patch("benchbox.utils.dependencies.is_uv_tool_environment", return_value=False),
        ):
            assert get_install_command(platform) == "uv sync --extra postgresql"

        with patch("benchbox.utils.dependencies.is_development_install", return_value=False):
            assert get_install_command(platform) == 'uv pip install "benchbox[postgresql]"'


