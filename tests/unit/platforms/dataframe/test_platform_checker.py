# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import pytest

from benchbox.platforms.dataframe.platform_checker import (
    DATAFRAME_PLATFORMS,
    DataFrameFamily,
    DataFramePlatformChecker,
    PlatformInfo,
    PlatformStatus,
    format_platform_status_table,
    get_installation_suggestion,
    get_platform_error_message,
    require_platform,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestDataFrameFamily:
    def test_pandas_family_value(self):

        assert DataFrameFamily.PANDAS.value == "pandas"

    def test_expression_family_value(self):

        assert DataFrameFamily.EXPRESSION.value == "expression"


class TestPlatformInfo:
    def test_platform_info_creation(self):

        info = PlatformInfo(
            name="TestPlatform",
            family=DataFrameFamily.PANDAS,
            import_name="test_module",
            version_attr="__version__",
            extra_name="test-extra",
            description="A test platform",
        )

        assert info.name == "TestPlatform"
        assert info.family == DataFrameFamily.PANDAS
        assert info.import_name == "test_module"
        assert info.extra_name == "test-extra"

    def test_platform_info_with_versions(self):

        info = PlatformInfo(
            name="TestPlatform",
            family=DataFrameFamily.EXPRESSION,
            import_name="test",
            version_attr="__version__",
            extra_name="test",
            description="Test",
            min_version="1.0.0",
            max_version="2.0.0",
        )

        assert info.min_version == "1.0.0"
        assert info.max_version == "2.0.0"


class TestDataFramePlatforms:
    def test_pandas_registered(self):

        assert "pandas" in DATAFRAME_PLATFORMS
        info = DATAFRAME_PLATFORMS["pandas"]
        assert info.family == DataFrameFamily.PANDAS

    def test_polars_registered(self):

        assert "polars" in DATAFRAME_PLATFORMS
        info = DATAFRAME_PLATFORMS["polars"]
        assert info.family == DataFrameFamily.EXPRESSION

    def test_dask_registered(self):

        assert "dask" in DATAFRAME_PLATFORMS
        info = DATAFRAME_PLATFORMS["dask"]
        assert info.family == DataFrameFamily.PANDAS

    def test_pyspark_registered(self):

        assert "pyspark" in DATAFRAME_PLATFORMS
        info = DATAFRAME_PLATFORMS["pyspark"]
        assert info.family == DataFrameFamily.EXPRESSION

    def test_datafusion_registered(self):

        assert "datafusion" in DATAFRAME_PLATFORMS
        info = DATAFRAME_PLATFORMS["datafusion"]
        assert info.family == DataFrameFamily.EXPRESSION

    def test_all_platforms_have_required_fields(self):
        for name, info in DATAFRAME_PLATFORMS.items():
            assert info.name, f"{name} missing name"
            assert info.family, f"{name} missing family"
            assert info.import_name, f"{name} missing import_name"
            assert info.version_attr, f"{name} missing version_attr"
            assert info.description, f"{name} missing description"


class TestDataFramePlatformChecker:
    def test_polars_is_available(self):
        assert DataFramePlatformChecker.is_available("polars")

    def test_pandas_is_available(self):
        assert DataFramePlatformChecker.is_available("pandas")

    def test_unknown_platform_not_available(self):

        assert not DataFramePlatformChecker.is_available("nonexistent_platform")

    def test_get_version_polars(self):

        version = DataFramePlatformChecker.get_version("polars")
        assert version is not None
        assert "." in version

    def test_get_version_unknown_platform(self):

        version = DataFramePlatformChecker.get_version("nonexistent")
        assert version is None

    def test_check_platform_polars(self):

        status = DataFramePlatformChecker.check_platform("polars")

        assert isinstance(status, PlatformStatus)
        assert status.available is True
        assert status.version is not None
        assert status.info.name == "Polars"
        assert status.error is None

    def test_check_platform_unknown(self):

        status = DataFramePlatformChecker.check_platform("nonexistent")

        assert status.available is False
        assert "Unknown DataFrame platform" in status.error

    def test_get_available_platforms(self):

        available = DataFramePlatformChecker.get_available_platforms()

        assert isinstance(available, list)
        assert "polars" in available

    def test_get_available_by_family_expression(self):

        available = DataFramePlatformChecker.get_available_by_family(DataFrameFamily.EXPRESSION)

        assert isinstance(available, list)
        assert "polars" in available

    def test_get_all_platforms(self):

        platforms = DataFramePlatformChecker.get_all_platforms()

        assert isinstance(platforms, dict)
        assert "pandas" in platforms
        assert "polars" in platforms
        assert all(isinstance(v, PlatformInfo) for v in platforms.values())

    def test_check_all_platforms(self):

        statuses = DataFramePlatformChecker.check_all_platforms()

        assert isinstance(statuses, dict)
        assert all(isinstance(v, PlatformStatus) for v in statuses.values())
        assert statuses["polars"].available is True


class TestInstallationSuggestion:
    def test_suggestion_for_pandas(self):

        suggestion = get_installation_suggestion("pandas")

        assert "benchbox[pandas]" in suggestion or "--extra pandas" in suggestion
        assert "uv add" in suggestion

    def test_suggestion_for_polars_core(self):
        suggestion = get_installation_suggestion("polars")

        assert "core dependency" in suggestion

    def test_suggestion_for_unknown_platform(self):

        suggestion = get_installation_suggestion("nonexistent")

        assert "Unknown" in suggestion


class TestPlatformErrorMessage:
    def test_error_for_available_platform(self):

        message = get_platform_error_message("polars")

        assert "available" in message.lower()

    def test_error_for_unknown_platform(self):

        message = get_platform_error_message("nonexistent")

        assert "Unknown" in message


class TestRequirePlatform:
    def test_require_polars_succeeds(self):
        module = require_platform("polars")

        assert module is not None
        assert hasattr(module, "__version__")

    def test_require_unknown_raises(self):

        with pytest.raises(ImportError, match="Unknown"):
            require_platform("nonexistent_platform")


class TestFormatPlatformStatusTable:
    def test_table_format(self):

        table = format_platform_status_table()

        assert "DataFrame Platform Status" in table
        assert "Platform" in table
        assert "Family" in table
        assert "Available" in table
        assert "Version" in table

    def test_table_shows_polars(self):

        table = format_platform_status_table()

        assert "Polars" in table
        assert "expression" in table

    def test_table_shows_count(self):

        table = format_platform_status_table()

        assert "Available:" in table
        assert "platforms" in table


class TestMinimumVersionEnforcement:
    def test_below_minimum_reports_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            DataFramePlatformChecker,
            "get_version",
            staticmethod(lambda platform: "53.0.0"),
        )
        status = DataFramePlatformChecker.check_platform("datafusion")

        assert status.available is False
        assert status.error is not None
        assert "54.0.0" in status.error

    def test_at_minimum_reports_available(self) -> None:
        status = DataFramePlatformChecker.check_platform("datafusion")

        assert status.available is True
        assert status.error is None


