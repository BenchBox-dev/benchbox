from __future__ import annotations

from typing import Any

import pytest

from benchbox.core.results.platform_info import (
    PlatformInfoInput,
    build_platform_info,
    format_platform_display_name,
    merge_platform_info,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class MockAdapter:
    def __init__(
        self,
        platform_name: str = "MockPlatform",
        family: str | None = None,
        platform_info: dict[str, Any] | None = None,
    ):
        self.platform_name = platform_name
        if family:
            self.family = family
        self._platform_info = platform_info

    def get_platform_info(self) -> dict[str, Any]:
        if self._platform_info is not None:
            return self._platform_info
        return {
            "platform": self.platform_name,
            "version": "1.0.0",
            "connection_mode": "in-memory",
            "configuration": {"threads": 4, "memory": "8GB"},
        }


class TestBuildPlatformInfo:
    def test_build_basic_info(self) -> None:
        adapter = MockAdapter(platform_name="DuckDB")
        result = build_platform_info(adapter)

        assert result.name == "DuckDB"
        assert result.platform_version == "1.0.0"
        assert result.client_library_version == "unknown"
        assert result.execution_mode == "sql"
        assert result.connection_mode == "in-memory"

    def test_build_with_dataframe_mode(self) -> None:
        adapter = MockAdapter(platform_name="Polars", family="expression")
        result = build_platform_info(adapter, execution_mode="dataframe")

        assert result.name == "Polars"
        assert result.execution_mode == "dataframe"
        assert result.family == "expression"

    def test_build_extracts_config(self) -> None:
        adapter = MockAdapter(
            platform_name="DuckDB",
            platform_info={
                "platform": "DuckDB",
                "version": "1.0.0",
                "configuration": {"threads": 4, "memory": "8GB"},
                "host": "localhost",
            },
        )
        result = build_platform_info(adapter)

        assert "threads" in result.config
        assert result.config["threads"] == 4
        assert result.config["memory"] == "8GB"
        assert result.config["host"] == "localhost"

    def test_build_from_adapter_without_platform_info(self) -> None:

        class MinimalAdapter:
            platform_name = "MinimalDB"

        adapter = MinimalAdapter()
        result = build_platform_info(adapter)

        assert result.name == "MinimalDB"
        assert result.platform_version == "unknown"
        assert result.config == {}

    def test_build_extracts_version_from_multiple_locations(self) -> None:

        adapter = MockAdapter(
            platform_name="DB1",
            platform_info={"platform": "DB1", "platform_version": "2.0.0"},
        )
        result = build_platform_info(adapter)
        assert result.platform_version == "2.0.0"

        adapter = MockAdapter(
            platform_name="DB2",
            platform_info={"platform": "DB2", "client_library_version": "3.0.0"},
        )
        result = build_platform_info(adapter)
        assert result.client_library_version == "3.0.0"

    def test_build_handles_get_platform_info_exception(self) -> None:

        class FailingAdapter:
            platform_name = "FailDB"

            def get_platform_info(self) -> dict[str, Any]:
                raise RuntimeError("Connection error")

        adapter = FailingAdapter()
        result = build_platform_info(adapter)

        assert result.name == "FailDB"

    def test_build_excludes_redundant_keys_from_config(self) -> None:
        adapter = MockAdapter(
            platform_name="TestDB",
            platform_info={
                "platform": "TestDB",
                "platform_name": "TestDB",
                "name": "TestDB",
                "version": "1.0.0",
                "platform_version": "1.0.0",
                "connection_mode": "remote",
                "custom_setting": "value",
            },
        )
        result = build_platform_info(adapter)

        assert "platform" not in result.config
        assert "platform_name" not in result.config
        assert "name" not in result.config
        assert "version" not in result.config
        assert "platform_version" not in result.config
        assert "connection_mode" not in result.config

        assert result.config["custom_setting"] == "value"


class TestPlatformInfoInput:
    def test_create_basic(self) -> None:
        info = PlatformInfoInput(name="DuckDB")

        assert info.name == "DuckDB"
        assert info.platform_version is None
        assert info.client_library_version is None
        assert info.execution_mode == "sql"
        assert info.connection_mode is None
        assert info.family is None
        assert info.config == {}

    def test_create_with_all_fields(self) -> None:
        info = PlatformInfoInput(
            name="Polars",
            platform_version="1.0.0",
            client_library_version="2.0.0",
            execution_mode="dataframe",
            connection_mode="in-memory",
            family="expression",
            config={"threads": 4},
        )

        assert info.name == "Polars"
        assert info.platform_version == "1.0.0"
        assert info.client_library_version == "2.0.0"
        assert info.execution_mode == "dataframe"
        assert info.connection_mode == "in-memory"
        assert info.family == "expression"
        assert info.config == {"threads": 4}


class TestFormatPlatformDisplayName:
    def test_format_sql_mode(self) -> None:
        result = format_platform_display_name("DuckDB", "sql")
        assert result == "DuckDB"

    def test_format_dataframe_mode(self) -> None:
        result = format_platform_display_name("Polars", "dataframe")
        assert result == "Polars"

    def test_format_default_mode(self) -> None:
        result = format_platform_display_name("DuckDB")
        assert result == "DuckDB"


class TestMergePlatformInfo:
    def test_merge_with_additional_config(self) -> None:
        base = PlatformInfoInput(
            name="DuckDB",
            platform_version="1.0.0",
            config={"threads": 4},
        )
        additional = {"memory": "8GB", "cache_size": "1GB"}

        result = merge_platform_info(base, additional)

        assert result.name == "DuckDB"
        assert result.platform_version == "1.0.0"
        assert result.config["threads"] == 4
        assert result.config["memory"] == "8GB"
        assert result.config["cache_size"] == "1GB"

    def test_merge_overwrites_existing_keys(self) -> None:
        base = PlatformInfoInput(
            name="DuckDB",
            config={"threads": 4, "memory": "4GB"},
        )
        additional = {"memory": "8GB"}

        result = merge_platform_info(base, additional)

        assert result.config["threads"] == 4
        assert result.config["memory"] == "8GB"

    def test_merge_with_none(self) -> None:
        base = PlatformInfoInput(
            name="DuckDB",
            config={"threads": 4},
        )

        result = merge_platform_info(base, None)

        assert result is base

    def test_merge_with_empty_dict(self) -> None:
        base = PlatformInfoInput(
            name="DuckDB",
            config={"threads": 4},
        )

        result = merge_platform_info(base, {})

        assert result.name == "DuckDB"
        assert result.config == {"threads": 4}


