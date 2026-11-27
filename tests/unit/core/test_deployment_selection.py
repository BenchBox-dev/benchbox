from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.deployment import (
    DEPLOYMENT_SELECTOR_PLATFORMS,
    deployment_class,
    normalize_deployment_value,
    resolve_deployment,
    select_adapter_deployment,
)
from benchbox.core.platform_manifest import PLATFORM_MANIFEST

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestNormalizeDeploymentValue:
    def test_selector_values_match_manifest_modes(self) -> None:
        assert normalize_deployment_value("firebolt", "cloud") == "cloud"
        assert normalize_deployment_value("firebolt", " Core ") == "core"
        assert normalize_deployment_value("influxdb", "core") == "core"
        assert normalize_deployment_value("pg-duckdb", "MotherDuck") == "motherduck"
        assert normalize_deployment_value("ducklake", "postgres_catalog_s3") == "postgres_catalog_s3"
        assert normalize_deployment_value("velox", "remote") == "remote"
        assert normalize_deployment_value("lakesail", "distributed") == "distributed"

    def test_clickhouse_embedded_synonym_is_rejected_with_the_replacement(self) -> None:
        with pytest.raises(ValueError, match="'embedded' for 'clickhouse' was removed in BenchBox 0.6.0; use 'local'"):
            normalize_deployment_value("clickhouse", "embedded")

    def test_unknown_value_lists_available_modes(self) -> None:
        with pytest.raises(ValueError, match="does not support deployment mode"):
            normalize_deployment_value("firebolt", "bogus")


class TestResolveDeployment:
    def test_selector_resolves(self) -> None:
        resolution = resolve_deployment("firebolt", "cloud")
        assert (resolution.selected, resolution.explicit) == ("cloud", True)

    def test_no_selector_resolves_to_nothing(self) -> None:
        resolution = resolve_deployment("firebolt", None)
        assert (resolution.selected, resolution.explicit) == (None, False)

    def test_every_platform_with_several_manifest_modes_selects_by_selector(self) -> None:
        multi_mode = {
            entry.key for entry in PLATFORM_MANIFEST if len(entry.capabilities.get("deployment_modes") or {}) > 1
        }
        assert multi_mode == set(DEPLOYMENT_SELECTOR_PLATFORMS)


class TestDeploymentClass:
    def test_manifest_mode_is_reported_as_class(self) -> None:
        assert deployment_class("firebolt", "core") == "local"
        assert deployment_class("firebolt", "cloud") == "managed"
        assert deployment_class("influxdb", "core") == "self-hosted"
        assert deployment_class("influxdb", "cloud") == "managed"
        assert deployment_class("clickhouse", "server") == "self-hosted"
        assert deployment_class("pg-duckdb", "motherduck") == "managed"
        assert deployment_class("velox", "remote") == "self-hosted"
        assert deployment_class("lakesail", "distributed") == "self-hosted"
        assert deployment_class("ducklake", "postgres_catalog") == "self-hosted"

    def test_unknown_mode_has_no_class(self) -> None:
        assert deployment_class("firebolt", "bogus") is None


class TestSelectAdapterDeployment:
    def test_selector_value_is_normalized(self) -> None:
        assert select_adapter_deployment("firebolt", {"_deployment_selector": " Cloud "}, None) == "cloud"

    def test_missing_selector_returns_default(self) -> None:
        assert select_adapter_deployment("ducklake", {}, None) is None
        assert select_adapter_deployment("lakesail", {}, "local") == "local"

    @pytest.mark.parametrize(
        ("platform", "key"),
        [
            ("firebolt", "deployment_mode"),
            ("firebolt", "firebolt_mode"),
            ("velox", "deployment"),
            ("lakesail", "sail_mode"),
            ("influxdb", "mode"),
        ],
    )
    def test_removed_keys_do_not_select_a_deployment(self, platform: str, key: str) -> None:
        assert select_adapter_deployment(platform, {key: "cloud"}, "fallback") == "fallback"


class TestFamilyAdaptersRecordSelection:
    def test_firebolt_core_records_local_class(self) -> None:
        import benchbox.platforms.firebolt as firebolt_module
        from benchbox.platforms.firebolt import FireboltAdapter

        with patch.object(firebolt_module, "FIREBOLT_AVAILABLE", True):
            adapter = FireboltAdapter(_deployment_selector="core")
        assert adapter.deployment_selected == "core"
        assert adapter.deployment_selected_class == "local"

    def test_firebolt_cloud_records_managed_class(self) -> None:
        import benchbox.platforms.firebolt as firebolt_module
        from benchbox.platforms.firebolt import FireboltAdapter

        with patch.object(firebolt_module, "FIREBOLT_AVAILABLE", True):
            adapter = FireboltAdapter(
                _deployment_selector="cloud",
                client_id="id",
                client_secret="secret",
                account_name="account",
                compute_resource="engine",
            )
        assert adapter.deployment_selected == "cloud"
        assert adapter.deployment_selected_class == "managed"

    def test_influxdb_core_records_self_hosted(self) -> None:
        import benchbox.platforms.influxdb.adapter as influx_adapter
        from benchbox.platforms.influxdb.adapter import InfluxDBAdapter

        with patch.object(influx_adapter, "INFLUXDB_AVAILABLE", True):
            adapter = InfluxDBAdapter(_deployment_selector="core")
        assert adapter.deployment_selected == "core"
        assert adapter.deployment_selected_class == "self-hosted"

    def test_pg_duckdb_motherduck_records_managed(self) -> None:
        from benchbox.platforms.pg_duckdb import PgDuckDBAdapter

        adapter = PgDuckDBAdapter(_deployment_selector="motherduck", motherduck_token="token")
        assert adapter.deployment_selected == "motherduck"
        assert adapter.deployment_selected_class == "managed"

    def test_ducklake_deployment_records_manifest_class(self) -> None:
        from benchbox.platforms.ducklake import DuckLakeAdapter

        adapter = DuckLakeAdapter(
            metadata_path="/tmp/probe.ducklake",
            data_path="/tmp/probe-data",
            _deployment_selector="postgres_catalog",
        )
        assert adapter.deployment_selected == "postgres_catalog"
        assert adapter.deployment_selected_class == "self-hosted"

    def test_clickhouse_server_records_self_hosted(self) -> None:
        from benchbox.platforms.clickhouse import ClickHouseAdapter

        with (
            patch(
                "benchbox.platforms.clickhouse.adapter.check_platform_dependencies",
                return_value=(True, []),
            ),
            patch.object(ClickHouseAdapter, "_setup_server_mode", lambda self, config: None),
        ):
            adapter = ClickHouseAdapter(_deployment_selector="server", host="h")
        assert adapter.deployment_selected == "server"
        assert adapter.deployment_selected_class == "self-hosted"

    def test_velox_remote_records_self_hosted(self) -> None:
        import benchbox.platforms.velox as velox_module
        from benchbox.platforms.velox import VeloxAdapter

        with patch.object(velox_module, "SparkSession", object()):
            adapter = VeloxAdapter(_deployment_selector="remote", endpoint="sc://localhost:50051")
        assert adapter.deployment_selected == "remote"
        assert adapter.deployment_selected_class == "self-hosted"

    def test_lakesail_distributed_records_self_hosted(self) -> None:
        import benchbox.platforms.lakesail as lakesail_module
        from benchbox.platforms.lakesail import LakeSailAdapter

        with patch.object(lakesail_module, "SparkSession", object()):
            adapter = LakeSailAdapter(_deployment_selector="distributed", endpoint="sc://localhost:50051")
        assert adapter.deployment_selected == "distributed"
        assert adapter.deployment_selected_class == "self-hosted"


class TestFactoryResolution:
    def test_sql_factory_resolves_selector_suffix(self) -> None:
        import benchbox.platforms.firebolt as firebolt_module
        from benchbox.platforms import get_platform_adapter

        with patch.object(firebolt_module, "FIREBOLT_AVAILABLE", True):
            adapter = get_platform_adapter(
                "firebolt:cloud",
                client_id="id",
                client_secret="secret",
                account_name="account",
                compute_resource="engine",
                database="db",
            )
        assert adapter.deployment_mode == "cloud"
        assert adapter.deployment_selected == "cloud"
        assert adapter.deployment_selected_class == "managed"

    def test_sql_factory_rejects_the_removed_deployment_keyword(self) -> None:
        from benchbox.platforms import get_platform_adapter

        with pytest.raises(TypeError, match="Platform option 'deployment_mode' for 'firebolt' was removed"):
            get_platform_adapter("firebolt:core", deployment_mode="cloud")

    def test_selector_conflict_with_explicit_selector_fails(self) -> None:
        from benchbox.platforms import get_platform_adapter

        with pytest.raises(ValueError, match="conflicts with"):
            get_platform_adapter("firebolt:core", _deployment_selector="cloud")

    def test_firebolt_run_records_selected_and_class(self) -> None:
        import benchbox.platforms.firebolt as firebolt_module
        from benchbox.platforms import get_platform_adapter

        with patch.object(firebolt_module, "FIREBOLT_AVAILABLE", True):
            adapter = get_platform_adapter(
                "firebolt:core",
                database="db",
            )
            metadata = adapter.get_normalized_result_metadata(connection=None)
        deployment = metadata["platform_deployment"]
        assert deployment["selected"] == "core"
        assert deployment["selected_class"] == "local"

    def test_base_metadata_records_adapter_selection(self) -> None:
        from benchbox.platforms.base.runtime_metadata import collect_normalized_result_metadata
        from benchbox.platforms.clickhouse import ClickHouseAdapter

        with (
            patch(
                "benchbox.platforms.clickhouse.adapter.check_platform_dependencies",
                return_value=(True, []),
            ),
            patch.object(ClickHouseAdapter, "_setup_server_mode", lambda self, config: None),
        ):
            adapter = ClickHouseAdapter(_deployment_selector="server", host="h")
            metadata = collect_normalized_result_metadata(adapter, connection=None)
        deployment = metadata["platform_deployment"]
        assert deployment["selected"] == "server"
        assert deployment["selected_class"] == "self-hosted"


class TestSelectorSplitting:
    def test_family_selectors_split(self) -> None:
        from benchbox.cli.run_platform_resolution import _split_deployment_selector

        assert _split_deployment_selector("firebolt:cloud") == ("firebolt", "cloud")
        assert _split_deployment_selector("pg-duckdb:motherduck") == ("pg-duckdb", "motherduck")
        assert _split_deployment_selector("influxdb:core") == ("influxdb", "core")
        assert _split_deployment_selector("ducklake:postgres_catalog") == ("ducklake", "postgres_catalog")
        assert _split_deployment_selector("velox:remote") == ("velox", "remote")
        assert _split_deployment_selector("lakesail:distributed") == ("lakesail", "distributed")

    def test_clickhouse_selectors_map_to_first_class_platforms(self) -> None:
        from benchbox.cli.run_platform_resolution import _split_deployment_selector

        assert _split_deployment_selector("clickhouse:server") == ("clickhouse-server", None)
        assert _split_deployment_selector("clickhouse:local") == ("clickhouse-local", None)

    def test_other_platforms_keep_legacy_behavior(self) -> None:
        from benchbox.cli.run_platform_resolution import _split_deployment_selector

        assert _split_deployment_selector("polars:local") == ("polars:local", None)
        assert _split_deployment_selector("firebolt") == ("firebolt", None)
        assert _split_deployment_selector(None) == (None, None)

    def test_invalid_selector_keeps_full_key_for_downstream_validation(self) -> None:
        from benchbox.cli.run_platform_resolution import _split_deployment_selector

        assert _split_deployment_selector("firebolt:bogus") == ("firebolt:bogus", None)


class TestRemovedDeploymentKeys:
    @pytest.mark.parametrize(
        ("platform", "key"),
        [
            ("clickhouse", "deployment_mode"),
            ("clickhouse", "mode"),
            ("clickhouse", "embedded"),
            ("databend", "deployment_mode"),
            ("ducklake", "deployment_mode"),
            ("firebolt", "deployment_mode"),
            ("firebolt", "firebolt_mode"),
            ("influxdb", "deployment_mode"),
            ("influxdb", "mode"),
            ("lakesail", "deployment_mode"),
            ("lakesail", "sail_mode"),
            ("pg-duckdb", "deployment_mode"),
            ("timescaledb", "deployment_mode"),
            ("velox", "deployment"),
        ],
    )
    def test_cli_option_is_rejected_with_the_selector(self, platform: str, key: str) -> None:
        import importlib

        importlib.import_module("benchbox.platforms")
        from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionError

        with pytest.raises(PlatformOptionError) as raised:
            PlatformHookRegistry.parse_options(platform, [(key, "cloud")])
        assert f"Platform option '{key}' for '{platform}' was removed in BenchBox 0.6.0" in str(raised.value)
        assert f"the platform selector '--platform {platform}:" in str(raised.value)

    def test_database_config_rejects_a_removed_key_in_options(self) -> None:
        from benchbox.core.schemas import DatabaseConfig

        with pytest.raises(TypeError, match="Platform option 'sail_mode' for 'lakesail' was removed"):
            DatabaseConfig(type="lakesail", name="x", options={"sail_mode": "distributed"})

    def test_mcp_rejects_the_removed_clickhouse_key(self) -> None:
        from benchbox.mcp.schemas import MCPValidationError, validate_platform_options

        with pytest.raises(MCPValidationError, match="Platform option 'deployment_mode' for 'clickhouse' was removed"):
            validate_platform_options("clickhouse", {"deployment_mode": "server"})
