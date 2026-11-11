# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

from benchbox.cli.database import (
    CLOUD_CATEGORIES,
    LOCAL_CATEGORIES,
    DatabaseManager,
    ExecutionStyleFilter,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestExecutionStyleFilter:
    def test_default_values(self):

        style_filter = ExecutionStyleFilter()
        assert style_filter.execution_mode == "all"
        assert style_filter.location == "all"

    def test_sql_local_filter(self):

        style_filter = ExecutionStyleFilter(execution_mode="sql", location="local")
        assert style_filter.execution_mode == "sql"
        assert style_filter.location == "local"

    def test_dataframe_cloud_filter(self):

        style_filter = ExecutionStyleFilter(execution_mode="dataframe", location="cloud")
        assert style_filter.execution_mode == "dataframe"
        assert style_filter.location == "cloud"


class TestCategoryConstants:
    def test_local_categories_exist(self):

        assert "analytical" in LOCAL_CATEGORIES
        assert "embedded" in LOCAL_CATEGORIES
        assert "distributed" in LOCAL_CATEGORIES
        assert "dataframe" in LOCAL_CATEGORIES

    def test_cloud_categories_exist(self):

        assert "cloud" in CLOUD_CATEGORIES

    def test_categories_are_disjoint(self):

        assert LOCAL_CATEGORIES.isdisjoint(CLOUD_CATEGORIES)


class TestFilterPlatforms:
    @pytest.fixture
    def db_manager(self):
        with patch("benchbox.cli.database.get_platform_manager"):
            return DatabaseManager()

    def test_filter_all_returns_all(self, db_manager):

        platforms = ["duckdb", "polars", "bigquery", "snowflake"]
        style_filter = ExecutionStyleFilter(execution_mode="all", location="all")

        result = db_manager.filter_platforms(platforms, style_filter)

        assert result == platforms

    def test_filter_sql_only(self, db_manager):

        platforms = ["duckdb", "polars", "pandas", "bigquery"]
        style_filter = ExecutionStyleFilter(execution_mode="sql", location="all")

        result = db_manager.filter_platforms(platforms, style_filter)

        assert "duckdb" in result
        assert "polars" not in result
        assert "bigquery" in result
        assert "pandas" not in result

    def test_filter_dataframe_only(self, db_manager):

        platforms = ["duckdb", "polars", "pandas", "pyspark"]
        style_filter = ExecutionStyleFilter(execution_mode="dataframe", location="all")

        result = db_manager.filter_platforms(platforms, style_filter)

        assert "polars" in result
        assert "pandas" in result
        assert "pyspark" in result
        assert "duckdb" not in result

    def test_filter_local_only(self, db_manager):

        platforms = ["duckdb", "sqlite", "bigquery", "snowflake"]
        style_filter = ExecutionStyleFilter(execution_mode="all", location="local")

        result = db_manager.filter_platforms(platforms, style_filter)

        assert "duckdb" in result
        assert "sqlite" in result
        assert "bigquery" not in result
        assert "snowflake" not in result

    def test_filter_cloud_only(self, db_manager):

        platforms = ["duckdb", "sqlite", "bigquery", "snowflake", "databricks"]
        style_filter = ExecutionStyleFilter(execution_mode="all", location="cloud")

        result = db_manager.filter_platforms(platforms, style_filter)

        assert "bigquery" in result
        assert "snowflake" in result
        assert "databricks" in result
        assert "duckdb" not in result
        assert "sqlite" not in result

    def test_filter_sql_local(self, db_manager):

        platforms = ["duckdb", "polars", "pandas", "bigquery", "databricks"]
        style_filter = ExecutionStyleFilter(execution_mode="sql", location="local")

        result = db_manager.filter_platforms(platforms, style_filter)

        assert "duckdb" in result
        assert "polars" not in result
        assert "pandas" not in result
        assert "bigquery" not in result
        assert "databricks" not in result

    def test_filter_dataframe_cloud(self, db_manager):

        platforms = ["duckdb", "polars", "pandas", "databricks", "glue"]
        style_filter = ExecutionStyleFilter(execution_mode="dataframe", location="cloud")

        result = db_manager.filter_platforms(platforms, style_filter)

        assert "databricks" in result
        assert "glue" in result
        assert "duckdb" not in result
        assert "polars" not in result
        assert "pandas" not in result

    def test_filter_empty_platforms_list(self, db_manager):

        style_filter = ExecutionStyleFilter(execution_mode="sql", location="local")

        result = db_manager.filter_platforms([], style_filter)

        assert result == []

    def test_filter_unknown_platform(self, db_manager):
        platforms = ["unknown_platform", "duckdb"]
        style_filter = ExecutionStyleFilter(execution_mode="sql", location="local")

        result = db_manager.filter_platforms(platforms, style_filter)

        assert "unknown_platform" not in result
        assert "duckdb" in result


class TestPromptExecutionStyle:
    @pytest.fixture
    def db_manager(self):
        with patch("benchbox.cli.database.get_platform_manager"):
            return DatabaseManager()

    @patch("benchbox.cli.database.Prompt.ask")
    @patch("benchbox.cli.database.console")
    def test_prompt_sql_local(self, mock_console, mock_prompt, db_manager):

        mock_prompt.side_effect = ["1", "1"]

        result = db_manager.prompt_execution_style()

        assert result.execution_mode == "sql"
        assert result.location == "local"

    @patch("benchbox.cli.database.Prompt.ask")
    @patch("benchbox.cli.database.console")
    def test_prompt_dataframe_cloud(self, mock_console, mock_prompt, db_manager):

        mock_prompt.side_effect = ["2", "2"]

        result = db_manager.prompt_execution_style()

        assert result.execution_mode == "dataframe"
        assert result.location == "cloud"

    @patch("benchbox.cli.database.Prompt.ask")
    @patch("benchbox.cli.database.console")
    def test_prompt_all_all(self, mock_console, mock_prompt, db_manager):
        mock_prompt.side_effect = ["3", "3"]

        result = db_manager.prompt_execution_style()

        assert result.execution_mode == "all"
        assert result.location == "all"


class TestSelectDatabaseWithFilter:
    @pytest.fixture
    def db_manager(self):
        with patch("benchbox.cli.database.get_platform_manager") as mock_pm:
            mock_manager = MagicMock()
            mock_manager.get_enabled_platforms.return_value = [
                "duckdb",
                "polars",
                "bigquery",
                "pandas",
            ]

            def make_platform_info(display_name, category, description="Test platform"):
                info = MagicMock()
                info.display_name = display_name
                info.category = category
                info.description = description
                return info

            mock_manager.detect_platforms.return_value = {
                "duckdb": make_platform_info("DuckDB", "analytical", "Columnar OLAP"),
                "polars": make_platform_info("Polars", "analytical", "DataFrame engine"),
                "bigquery": make_platform_info("BigQuery", "cloud", "Cloud warehouse"),
                "pandas": make_platform_info("Pandas", "dataframe", "DataFrame library"),
            }
            mock_pm.return_value = mock_manager
            return DatabaseManager()

    @patch("benchbox.cli.database.Prompt.ask")
    @patch("benchbox.cli.database.console")
    @patch("benchbox.cli.database.PlatformRegistry.get_platform_capabilities")
    def test_select_with_sql_filter(self, mock_caps, mock_console, mock_prompt, db_manager):

        mock_caps.return_value = MagicMock(supports_sql=True, supports_dataframe=False, default_mode="sql")
        mock_prompt.return_value = "1"

        style_filter = ExecutionStyleFilter(execution_mode="sql", location="all")
        config = db_manager.select_database(style_filter=style_filter)

        assert config.execution_mode == "sql"

    @patch("benchbox.cli.database.Prompt.ask")
    @patch("benchbox.cli.database.console")
    @patch("benchbox.cli.database.PlatformRegistry.get_platform_capabilities")
    def test_select_with_dataframe_filter(self, mock_caps, mock_console, mock_prompt, db_manager):

        mock_caps.return_value = MagicMock(supports_sql=True, supports_dataframe=True, default_mode="dataframe")
        mock_prompt.return_value = "1"

        style_filter = ExecutionStyleFilter(execution_mode="dataframe", location="all")
        config = db_manager.select_database(style_filter=style_filter)

        assert config.execution_mode == "dataframe"

    @patch("benchbox.cli.database.Prompt.ask")
    @patch("benchbox.cli.database.console")
    @patch("benchbox.cli.database.PlatformRegistry.get_platform_capabilities")
    def test_select_filters_platform_list(self, mock_caps, mock_console, mock_prompt, db_manager):

        mock_caps.return_value = MagicMock(supports_sql=True, supports_dataframe=False, default_mode="sql")
        mock_prompt.return_value = "1"

        style_filter = ExecutionStyleFilter(execution_mode="sql", location="local")
        db_manager.select_database(style_filter=style_filter)

        assert mock_prompt.called

    @patch("benchbox.cli.database.Prompt.ask")
    @patch("benchbox.cli.database.console")
    @patch("benchbox.cli.database.PlatformRegistry.get_platform_capabilities")
    def test_select_with_no_filter(self, mock_caps, mock_console, mock_prompt, db_manager):

        mock_caps.return_value = MagicMock(supports_sql=True, supports_dataframe=False, default_mode="sql")
        mock_prompt.return_value = "1"

        config = db_manager.select_database(style_filter=None)

        assert isinstance(config.type, str)


