from unittest.mock import patch

import pytest

from benchbox.platforms.base import PlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDependencyValidation:
    def test_check_import_success(self):

        result = PlatformAdapter._check_import("os")
        assert result is True

    def test_check_import_failure(self):

        result = PlatformAdapter._check_import("definitely_nonexistent_module_xyz")
        assert result is False

    @patch("benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin._check_import")
    def test_validate_platform_dependencies(self, mock_check_import):

        mock_check_import.return_value = True

        with patch.object(PlatformAdapter, "_check_databricks_dependencies", return_value=True):
            result = PlatformAdapter.validate_platform_dependencies()

        expected_deps = [
            "duckdb",
            "databricks",
            "clickhouse",
            "cloudpathlib",
            "snowflake",
            "psutil",
        ]

        for dep in expected_deps:
            assert dep in result
            assert result[dep] is True

        assert mock_check_import.call_count >= 5

    @patch("benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin._check_import")
    def test_validate_platform_dependencies_mixed(self, mock_check_import):

        def mock_import_side_effect(module_name):
            available_modules = ["duckdb", "psutil"]
            return module_name in available_modules

        mock_check_import.side_effect = mock_import_side_effect

        with patch.object(PlatformAdapter, "_check_databricks_dependencies", return_value=False):
            result = PlatformAdapter.validate_platform_dependencies()

        assert result["duckdb"] is True
        assert result["psutil"] is True
        assert result["databricks"] is False
        assert result["clickhouse"] is False
        assert result["cloudpathlib"] is False
        assert result["snowflake"] is False

    @patch("benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin._check_import")
    def test_check_databricks_dependencies(self, mock_check_import):

        mock_check_import.return_value = True
        result = PlatformAdapter._check_databricks_dependencies()
        assert result is True

        from unittest.mock import call

        expected_calls = [call("databricks.sql"), call("databricks.sdk")]
        mock_check_import.assert_has_calls(expected_calls, any_order=True)

    @patch("benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin._check_import")
    def test_check_databricks_dependencies_partial(self, mock_check_import):

        def mock_side_effect(module_name):
            return module_name == "databricks.sql"

        mock_check_import.side_effect = mock_side_effect
        result = PlatformAdapter._check_databricks_dependencies()
        assert result is False

    def test_get_install_command(self):

        assert PlatformAdapter._get_install_command("duckdb") == "uv add duckdb"
        assert PlatformAdapter._get_install_command("databricks") == "uv add databricks-sql-connector databricks-sdk"
        assert PlatformAdapter._get_install_command("clickhouse") == "uv add clickhouse-driver"
        assert PlatformAdapter._get_install_command("cloudpathlib") == "uv add cloudpathlib"
        assert PlatformAdapter._get_install_command("snowflake") == "uv add snowflake-connector-python"
        assert PlatformAdapter._get_install_command("psutil") == "uv add psutil"
        assert PlatformAdapter._get_install_command("unknown_dependency") is None

    @patch("benchbox.platforms.base.connection_lifecycle.quiet_console")
    @patch("benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.validate_platform_dependencies")
    @patch("sys.exit")
    def test_require_dependencies_all_available(self, mock_exit, mock_validate, mock_console):

        mock_validate.return_value = {"duckdb": True, "cloudpathlib": True}

        result = PlatformAdapter.require_dependencies(["duckdb", "cloudpathlib"])

        mock_exit.assert_not_called()
        assert result == {"duckdb": True, "cloudpathlib": True}

        assert any(
            "✅ All required dependencies are available" in str(call) for call in mock_console.print.call_args_list
        )

    @patch("benchbox.platforms.base.connection_lifecycle.quiet_console")
    @patch("benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.validate_platform_dependencies")
    @patch("benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin._get_install_command")
    @patch("sys.exit")
    def test_require_dependencies_missing_with_exit(self, mock_exit, mock_get_install, mock_validate, mock_console):

        mock_validate.return_value = {"duckdb": False, "cloudpathlib": True}
        mock_get_install.return_value = "uv add duckdb"

        PlatformAdapter.require_dependencies(["duckdb", "cloudpathlib"], exit_on_missing=True)

        mock_exit.assert_called_once_with(1)

        console_calls = [str(call) for call in mock_console.print.call_args_list]
        assert any("❌ Missing required dependencies:" in call for call in console_calls)
        assert any("duckdb" in call for call in console_calls)

    @patch("benchbox.platforms.base.connection_lifecycle.quiet_console")
    @patch("benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.validate_platform_dependencies")
    @patch("sys.exit")
    def test_require_dependencies_missing_no_exit(self, mock_exit, mock_validate, mock_console):

        mock_validate.return_value = {"duckdb": False, "cloudpathlib": True}

        result = PlatformAdapter.require_dependencies(["duckdb", "cloudpathlib"], exit_on_missing=False)

        mock_exit.assert_not_called()
        assert result == {"duckdb": False, "cloudpathlib": True}

        console_calls = [str(call) for call in mock_console.print.call_args_list]
        assert any("❌ Missing required dependencies:" in call for call in console_calls)

    def test_require_dependencies_empty_list(self):

        with patch(
            "benchbox.platforms.base.connection_lifecycle.ConnectionLifecycleMixin.validate_platform_dependencies"
        ) as mock_validate:
            mock_validate.return_value = {}

            with patch("benchbox.platforms.base.connection_lifecycle.quiet_console") as mock_console:
                result = PlatformAdapter.require_dependencies([])

            assert any(
                "✅ All required dependencies are available" in str(call) for call in mock_console.print.call_args_list
            )
            assert result == {}


class TestDependencyIntegration:
    def test_real_os_module_check(self):

        result = PlatformAdapter._check_import("os")
        assert result is True

    def test_real_nonexistent_module_check(self):

        result = PlatformAdapter._check_import("this_module_definitely_does_not_exist_12345")
        assert result is False

    def test_validate_dependencies_returns_dict(self):

        result = PlatformAdapter.validate_platform_dependencies()

        assert isinstance(result, dict)
        assert len(result) > 0

        for dep_name, available in result.items():
            assert isinstance(dep_name, str)
            assert isinstance(available, bool)


