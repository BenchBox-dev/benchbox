# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from benchbox.platforms.clickhouse.deployment_mode import (
    CLICKHOUSE_CANONICAL_PLATFORM_NAMES,
    CLICKHOUSE_DEFAULT_CANONICAL_PLATFORM,
    CLICKHOUSE_LEGACY_SELECTOR_MAP,
    clickhouse_legacy_selector_warning,
    is_clickhouse_platform,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestMigrationContract:
    def test_canonical_platform_names_are_complete(self) -> None:
        assert "clickhouse-local" in CLICKHOUSE_CANONICAL_PLATFORM_NAMES
        assert "clickhouse-server" in CLICKHOUSE_CANONICAL_PLATFORM_NAMES
        assert "clickhouse-cloud" in CLICKHOUSE_CANONICAL_PLATFORM_NAMES

    def test_bare_clickhouse_is_not_canonical(self) -> None:
        assert "clickhouse" not in CLICKHOUSE_CANONICAL_PLATFORM_NAMES

    def test_legacy_selector_map_covers_colon_syntax(self) -> None:
        assert CLICKHOUSE_LEGACY_SELECTOR_MAP["clickhouse:local"] == "clickhouse-local"
        assert CLICKHOUSE_LEGACY_SELECTOR_MAP["clickhouse:server"] == "clickhouse-server"
        assert CLICKHOUSE_LEGACY_SELECTOR_MAP["clickhouse:cloud"] == "clickhouse-cloud"

    def test_default_canonical_platform_is_local(self) -> None:
        assert CLICKHOUSE_DEFAULT_CANONICAL_PLATFORM == "clickhouse-local"

    def test_legacy_warning_message_mentions_target(self) -> None:
        msg = clickhouse_legacy_selector_warning("clickhouse", "clickhouse-local")
        assert "clickhouse-local" in msg
        assert "clickhouse" in msg
        assert "deprecated" in msg.lower()


class TestIsClickhousePlatform:
    @pytest.mark.parametrize(
        "platform",
        [
            "clickhouse",
            "clickhouse-local",
            "clickhouse-server",
            "clickhouse-cloud",
            "chdb",
            "CLICKHOUSE",
            "Clickhouse-Local",
        ],
    )
    def test_returns_true_for_all_clickhouse_family_names(self, platform: str) -> None:
        assert is_clickhouse_platform(platform) is True

    @pytest.mark.parametrize(
        "platform",
        ["duckdb", "postgresql", "snowflake", "databricks", "polars"],
    )
    def test_returns_false_for_non_clickhouse_platforms(self, platform: str) -> None:
        assert is_clickhouse_platform(platform) is False


class TestClickHouseLocalAdapter:
    def test_import_succeeds(self) -> None:
        from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter

        assert ClickHouseLocalAdapter is not None

    def test_forces_local_deployment_mode(self) -> None:
        from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter

        with (
            patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.platforms.clickhouse.setup.ClickHouseLocalClient"),
            patch("importlib.util.find_spec", return_value=object()),
        ):
            adapter = ClickHouseLocalAdapter()
            assert adapter.deployment_mode == "local"

    def test_platform_name_is_clickhouse_local(self) -> None:
        from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter

        with (
            patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.platforms.clickhouse.setup.ClickHouseLocalClient"),
            patch("importlib.util.find_spec", return_value=object()),
        ):
            adapter = ClickHouseLocalAdapter()
            assert adapter.platform_name == "ClickHouse Local"

    def test_from_config_forces_local_mode(self) -> None:
        from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter

        with (
            patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.platforms.clickhouse.setup.ClickHouseLocalClient"),
            patch("importlib.util.find_spec", return_value=object()),
        ):
            adapter = ClickHouseLocalAdapter.from_config({"deployment_mode": "server"})
            assert adapter.deployment_mode == "local"


class TestClickHouseServerAdapter:
    def test_import_succeeds(self) -> None:
        from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

        assert ClickHouseServerAdapter is not None

    def test_forces_server_deployment_mode(self) -> None:
        from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

        with (
            patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"),
        ):
            adapter = ClickHouseServerAdapter()
            assert adapter.deployment_mode == "server"

    def test_platform_name_is_clickhouse_server(self) -> None:
        from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

        with (
            patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"),
        ):
            adapter = ClickHouseServerAdapter()
            assert adapter.platform_name == "ClickHouse Server"

    def test_from_config_forces_server_mode(self) -> None:
        from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

        with (
            patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"),
        ):
            adapter = ClickHouseServerAdapter.from_config({"deployment_mode": "local"})
            assert adapter.deployment_mode == "server"

    def test_from_config_preserves_database_config_options(self) -> None:
        from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

        with (
            patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"),
        ):
            adapter = ClickHouseServerAdapter.from_config(
                {
                    "options": {
                        "host": "localhost",
                        "port": 19000,
                        "username": "default",
                        "password": "benchbox",
                    }
                }
            )

        assert adapter.host == "localhost"
        assert adapter.port == 19000
        assert adapter.username == "default"
        assert adapter.password == "benchbox"

    def test_from_config_derives_database_per_benchmark_and_scale(self) -> None:
        from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

        def build(benchmark: str, scale: float) -> str:
            with (
                patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
                patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"),
            ):
                return ClickHouseServerAdapter.from_config({"benchmark": benchmark, "scale_factor": scale}).database

        names = {build("tpch", 0.01), build("tpch", 1.0), build("tpcds", 0.01), build("tpcds_obt", 0.01)}

        assert len(names) == 4
        assert build("tpcds", 0.01) == "benchbox_tpcds_sf001"
        assert all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names)

    def test_from_config_keeps_explicit_database(self) -> None:
        from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

        with (
            patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"),
        ):
            adapter = ClickHouseServerAdapter.from_config(
                {"benchmark": "tpch", "scale_factor": 0.01, "options": {"database": "mine"}}
            )

        assert adapter.database == "mine"

    def test_second_benchmark_does_not_find_first_benchmarks_database(self) -> None:
        from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

        server_databases: set[str] = set()

        class _FakeAdmin:
            def execute(self, sql: str):
                return [(name,) for name in sorted({"default", "system", *server_databases})]

            def disconnect(self) -> None:
                return None

        def build(benchmark: str) -> ClickHouseServerAdapter:
            with (
                patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])),
                patch("benchbox.platforms.clickhouse.setup.ClickHouseClient"),
            ):
                adapter = ClickHouseServerAdapter.from_config({"benchmark": benchmark, "scale_factor": 0.01})
            adapter._create_admin_client = lambda **_: _FakeAdmin()
            return adapter

        tpch = build("tpch")
        assert tpch.check_server_database_exists() is False
        server_databases.add(tpch.database)
        assert tpch.check_server_database_exists() is True

        tpcds = build("tpcds")
        assert tpcds.check_server_database_exists() is False


class TestAdapterFactoryMigration:
    def test_clickhouse_local_resolves_without_warning(self) -> None:
        from benchbox.platforms.adapter_factory import _resolve_clickhouse_legacy

        platform, warning = _resolve_clickhouse_legacy("clickhouse-local")
        assert platform == "clickhouse-local"
        assert warning is None

    def test_clickhouse_server_resolves_without_warning(self) -> None:
        from benchbox.platforms.adapter_factory import _resolve_clickhouse_legacy

        platform, warning = _resolve_clickhouse_legacy("clickhouse-server")
        assert platform == "clickhouse-server"
        assert warning is None

    def test_clickhouse_cloud_resolves_without_warning(self) -> None:
        from benchbox.platforms.adapter_factory import _resolve_clickhouse_legacy

        platform, warning = _resolve_clickhouse_legacy("clickhouse-cloud")
        assert platform == "clickhouse-cloud"
        assert warning is None

    def test_bare_clickhouse_is_removed_and_raises_with_replacements(self) -> None:
        from benchbox.platforms.adapter_factory import _resolve_clickhouse_legacy

        with pytest.raises(ValueError) as exc_info:
            _resolve_clickhouse_legacy("clickhouse")
        msg = str(exc_info.value)
        assert "clickhouse-local" in msg
        assert "clickhouse-server" in msg
        assert "removed" in msg.lower()

    def test_clickhouse_colon_local_maps_to_clickhouse_local(self) -> None:
        from benchbox.platforms.adapter_factory import _resolve_clickhouse_legacy

        platform, warning = _resolve_clickhouse_legacy("clickhouse:local")
        assert platform == "clickhouse-local"
        assert warning is not None
        assert "clickhouse:local" in warning

    def test_clickhouse_colon_server_maps_to_clickhouse_server(self) -> None:
        from benchbox.platforms.adapter_factory import _resolve_clickhouse_legacy

        platform, warning = _resolve_clickhouse_legacy("clickhouse:server")
        assert platform == "clickhouse-server"
        assert warning is not None
        assert "clickhouse:server" in warning

    def test_clickhouse_colon_cloud_maps_to_clickhouse_cloud(self) -> None:
        from benchbox.platforms.adapter_factory import _resolve_clickhouse_legacy

        platform, warning = _resolve_clickhouse_legacy("clickhouse:cloud")
        assert platform == "clickhouse-cloud"
        assert warning is not None

    def test_non_clickhouse_platforms_pass_through_unchanged(self) -> None:
        from benchbox.platforms.adapter_factory import _resolve_clickhouse_legacy

        for name in ("duckdb", "snowflake", "polars-df", "databricks:serverless"):
            platform, warning = _resolve_clickhouse_legacy(name)
            assert platform == name
            assert warning is None

    def test_get_adapter_rejects_bare_clickhouse_with_helpful_error(self) -> None:
        from benchbox.platforms.adapter_factory import get_adapter

        with pytest.raises(ValueError) as exc_info:
            get_adapter("clickhouse")
        msg = str(exc_info.value)
        assert "clickhouse-local" in msg
        assert "clickhouse-server" in msg


class TestPlatformRegistryNewPlatforms:
    def setup_method(self) -> None:
        from benchbox.core.platform_registry import PlatformRegistry

        PlatformRegistry.clear_cache()

    def test_clickhouse_local_has_metadata(self) -> None:
        from benchbox.core.platform_registry import PlatformRegistry

        metadata = PlatformRegistry.get_all_platform_metadata()
        assert "clickhouse-local" in metadata, "clickhouse-local must be in platform metadata"
        spec = metadata["clickhouse-local"]
        assert "chdb" in spec["installation_command"] or "clickhouse-local" in spec["installation_command"]

    def test_clickhouse_server_has_metadata(self) -> None:
        from benchbox.core.platform_registry import PlatformRegistry

        metadata = PlatformRegistry.get_all_platform_metadata()
        assert "clickhouse-server" in metadata, "clickhouse-server must be in platform metadata"
        spec = metadata["clickhouse-server"]
        assert "clickhouse-driver" in str(spec["requirements"])

    def test_clickhouse_local_capabilities_have_sql_support(self) -> None:
        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("clickhouse-local")
        assert caps is not None
        assert caps.supports_sql is True
        assert caps.platform_family == "clickhouse"

    def test_clickhouse_server_capabilities_have_sql_support(self) -> None:
        from benchbox.core.platform_registry import PlatformRegistry

        caps = PlatformRegistry.get_platform_capabilities("clickhouse-server")
        assert caps is not None
        assert caps.supports_sql is True
        assert caps.platform_family == "clickhouse"

    def test_clickhouse_local_adapter_registered(self) -> None:
        from benchbox.core.platform_registry import PlatformRegistry

        PlatformRegistry._ensure_registered()
        available = PlatformRegistry.get_available_platforms()
        assert "clickhouse-local" in available, "clickhouse-local must be in available platforms"

    def test_clickhouse_server_adapter_registered(self) -> None:
        from benchbox.core.platform_registry import PlatformRegistry

        PlatformRegistry._ensure_registered()
        available = PlatformRegistry.get_available_platforms()
        assert "clickhouse-server" in available, "clickhouse-server must be in available platforms"
