from __future__ import annotations

from unittest.mock import patch

import pytest

from benchbox.core.deployment import (
    DEPLOYMENT_ALIAS_KEYS,
    deployment_class,
    normalize_deployment_value,
    resolve_deployment,
    select_adapter_deployment,
)

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

    def test_clickhouse_embedded_synonym_maps_to_local(self) -> None:
        assert normalize_deployment_value("clickhouse", "embedded") == "local"

    def test_unknown_value_lists_available_modes(self) -> None:
        with pytest.raises(ValueError, match="does not support deployment mode"):
            normalize_deployment_value("firebolt", "bogus")


class TestResolveDeployment:
    def test_selector_alone_resolves(self) -> None:
        resolution = resolve_deployment("firebolt", "cloud", {}, None)
        assert (resolution.selected, resolution.explicit) == ("cloud", True)

    def test_no_input_resolves_to_nothing(self) -> None:
        assert resolve_deployment("firebolt", None, {}, set()) == resolve_deployment("firebolt", None, {}, set())
        resolution = resolve_deployment("firebolt", None, {}, set())
        assert (resolution.selected, resolution.explicit) == (None, False)

    def test_explicit_alias_resolves(self) -> None:
        resolution = resolve_deployment("firebolt", None, {"deployment_mode": "cloud"}, None)
        assert (resolution.selected, resolution.explicit) == ("cloud", True)

    def test_spec_default_without_explicit_marker_is_ignored(self) -> None:
        resolution = resolve_deployment("clickhouse", None, {"deployment_mode": "server"}, set())
        assert (resolution.selected, resolution.explicit) == (None, False)

    def test_selector_beats_matching_alias(self) -> None:
        resolution = resolve_deployment("firebolt", "cloud", {"deployment_mode": "cloud"}, {"deployment_mode"})
        assert (resolution.selected, resolution.explicit) == ("cloud", True)

    def test_selector_conflict_with_alias_fails(self) -> None:
        with pytest.raises(ValueError, match="conflicts with"):
            resolve_deployment("firebolt", "core", {"deployment_mode": "cloud"}, {"deployment_mode"})

    def test_disagreeing_aliases_fail(self) -> None:
        with pytest.raises(ValueError, match="different"):
            resolve_deployment("firebolt", None, {"deployment_mode": "core", "firebolt_mode": "cloud"}, None)

    def test_no_input_resolves_to_nothing(self) -> None:
        resolution = resolve_deployment("firebolt", None, {}, set())
        assert (resolution.selected, resolution.explicit) == (None, False)

    def test_alias_warns_once_per_run(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING", logger="benchbox.core.deprecation_notices"):
            resolve_deployment("firebolt", None, {"deployment_mode": "cloud"}, None)
            resolve_deployment("firebolt", None, {"deployment_mode": "cloud"}, None)
        warnings = [record for record in caplog.records if "deprecated" in record.message]
        assert len(warnings) == 1

    def test_all_families_declare_alias_keys(self) -> None:
        assert set(DEPLOYMENT_ALIAS_KEYS) == {
            "velox",
            "lakesail",
            "influxdb",
            "clickhouse",
            "firebolt",
            "ducklake",
            "pg-duckdb",
        }


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
    def test_canonical_value_wins_without_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING", logger="benchbox.core.deprecation_notices"):
            assert select_adapter_deployment("firebolt", {"deployment_mode": "cloud"}, None) == "cloud"
        assert [record for record in caplog.records if "deprecated" in record.message] == []

    def test_legacy_fallback_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("WARNING", logger="benchbox.core.deprecation_notices"):
            assert select_adapter_deployment("firebolt", {"firebolt_mode": "cloud"}, None) == "cloud"
        assert len([record for record in caplog.records if "deprecated" in record.message]) == 1

    def test_non_explicit_spec_default_falls_back_to_default(self) -> None:
        assert select_adapter_deployment("velox", {"deployment": "local"}, "local") == "local"

    def test_canonical_and_legacy_conflict_fails(self) -> None:
        with pytest.raises(ValueError, match="conflicts with"):
            select_adapter_deployment("firebolt", {"deployment_mode": "core", "firebolt_mode": "cloud"}, None)

    def test_missing_keys_return_default(self) -> None:
        assert select_adapter_deployment("ducklake", {}, None) is None
        assert select_adapter_deployment("lakesail", {}, "local") == "local"


class TestFamilyAdaptersRecordSelection:
    def test_firebolt_core_records_local_class(self) -> None:
        import benchbox.platforms.firebolt as firebolt_module
        from benchbox.platforms.firebolt import FireboltAdapter

        with patch.object(firebolt_module, "FIREBOLT_AVAILABLE", True):
            adapter = FireboltAdapter(deployment_mode="core")
        assert adapter.deployment_selected == "core"
        assert adapter.deployment_selected_class == "local"

    def test_firebolt_alias_key_matches_selector(self) -> None:
        import benchbox.platforms.firebolt as firebolt_module
        from benchbox.platforms.firebolt import FireboltAdapter

        with patch.object(firebolt_module, "FIREBOLT_AVAILABLE", True):
            adapter = FireboltAdapter(
                firebolt_mode="cloud",
                client_id="id",
                client_secret="secret",
                account_name="account",
                engine_name="engine",
            )
        assert adapter.deployment_selected == "cloud"
        assert adapter.deployment_selected_class == "managed"

    def test_influxdb_mode_alias_matches_selector(self) -> None:
        import benchbox.platforms.influxdb.adapter as influx_adapter
        from benchbox.platforms.influxdb.adapter import InfluxDBAdapter

        with patch.object(influx_adapter, "INFLUXDB_AVAILABLE", True):
            adapter = InfluxDBAdapter(mode="core")
        assert adapter.deployment_selected == "core"
        assert adapter.deployment_selected_class == "self-hosted"

    def test_pg_duckdb_motherduck_records_managed(self) -> None:
        from benchbox.platforms.pg_duckdb import PgDuckDBAdapter

        adapter = PgDuckDBAdapter(deployment_mode="motherduck", motherduck_token="token")
        assert adapter.deployment_selected == "motherduck"
        assert adapter.deployment_selected_class == "managed"

    def test_ducklake_deployment_records_manifest_class(self) -> None:
        from benchbox.platforms.ducklake import DuckLakeAdapter

        adapter = DuckLakeAdapter(
            metadata_path="/tmp/probe.ducklake",
            data_path="/tmp/probe-data",
            deployment_mode="postgres_catalog",
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
            adapter = ClickHouseAdapter(deployment_mode="server", host="h")
        assert adapter.deployment_selected == "server"
        assert adapter.deployment_selected_class == "self-hosted"

    def test_velox_remote_records_self_hosted(self) -> None:
        import benchbox.platforms.velox as velox_module
        from benchbox.platforms.velox import VeloxAdapter

        with patch.object(velox_module, "SparkSession", object()):
            adapter = VeloxAdapter(deployment="remote", endpoint="sc://localhost:50051")
        assert adapter.deployment_selected == "remote"
        assert adapter.deployment_selected_class == "self-hosted"

    def test_lakesail_distributed_records_self_hosted(self) -> None:
        import benchbox.platforms.lakesail as lakesail_module
        from benchbox.platforms.lakesail import LakeSailAdapter

        with patch.object(lakesail_module, "SparkSession", object()):
            adapter = LakeSailAdapter(sail_mode="distributed", endpoint="sc://localhost:50051")
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
                engine_name="engine",
                database="db",
            )
        assert adapter.deployment_mode == "cloud"
        assert adapter.deployment_selected == "cloud"
        assert adapter.deployment_selected_class == "managed"

    def test_sql_factory_selector_conflict_with_alias_fails(self) -> None:
        from benchbox.platforms import get_platform_adapter

        with pytest.raises(ValueError, match="conflicts with"):
            get_platform_adapter("firebolt:core", deployment_mode="cloud")

    def test_dataframe_factory_resolves_explicit_deployment(self) -> None:
        from benchbox.platforms.adapter_factory import _resolve_family_deployment

        resolution = _resolve_family_deployment("firebolt", "cloud", None, {})
        assert (resolution.selected, resolution.explicit) == ("cloud", True)

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
            adapter = ClickHouseAdapter(deployment_mode="server", host="h")
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

    def _namespace(self, **fields):
        import types
        from unittest.mock import MagicMock

        namespace = types.SimpleNamespace(
            platform_key=None,
            deployment_selector=None,
            platform_option_pairs=(),
            parsed_platform_options={},
            logger=None,
            ctx=MagicMock(),
        )
        for key, value in fields.items():
            setattr(namespace, key, value)
        namespace.ctx.exit.side_effect = SystemExit(1)
        return namespace

    def test_selector_conflict_with_spec_alias_exits(self) -> None:
        import importlib

        importlib.import_module("benchbox.platforms")
        from benchbox.cli.run_platform_resolution import _apply_deployment_selector_option

        namespace = self._namespace(
            platform_key="lakesail",
            deployment_selector="distributed",
            platform_option_pairs=(("sail_mode", "local"),),
            parsed_platform_options={"deployment_mode": "local"},
        )
        with pytest.raises(SystemExit):
            _apply_deployment_selector_option(namespace)

    def test_agreeing_alias_warns_and_keeps_mode(self, caplog: pytest.LogCaptureFixture) -> None:
        import importlib

        importlib.import_module("benchbox.platforms")
        from benchbox.cli.run_platform_resolution import _apply_deployment_selector_option

        namespace = self._namespace(
            platform_key="lakesail",
            deployment_selector="distributed",
            platform_option_pairs=(("sail_mode", "distributed"),),
            parsed_platform_options={"deployment_mode": "distributed"},
        )
        with caplog.at_level("WARNING", logger="benchbox.core.deprecation_notices"):
            _apply_deployment_selector_option(namespace)
        assert [record for record in caplog.records if "deprecated" in record.message]
        assert namespace.deployment_selector == "distributed"


class TestSavedConfigAndCredentialCompat:
    def test_saved_yaml_alias_keys_parse_to_canonical(self) -> None:
        import importlib

        importlib.import_module("benchbox.platforms")
        importlib.import_module("benchbox.platforms.influxdb")
        from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

        lakesail_options = PlatformHookRegistry.parse_options("lakesail", [("sail_mode", "distributed")])
        assert lakesail_options["deployment_mode"] == "distributed"
        influx_options = PlatformHookRegistry.parse_options("influxdb", [("mode", "core")])
        assert influx_options["deployment_mode"] == "core"
        firebolt_options = PlatformHookRegistry.parse_options("firebolt", [("firebolt_mode", "cloud")])
        assert firebolt_options["deployment_mode"] == "cloud"

    def test_credential_alias_marks_explicit_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        import importlib

        importlib.import_module("benchbox.platforms")
        from benchbox.cli.database import DatabaseManager
        from benchbox.security.credentials import CredentialManager

        with (
            patch.object(CredentialManager, "get_platform_credentials", return_value={"sail_mode": "distributed"}),
            caplog.at_level("WARNING", logger="benchbox.core.deprecation_notices"),
        ):
            config = DatabaseManager().create_config("lakesail", {})
            assert config.options["sail_mode"] == "distributed"
            assert config.options["_explicit_platform_options"].get("sail_mode") == "distributed"
            resolution = resolve_deployment(
                "lakesail",
                None,
                dict(config.options),
                set(config.options["_explicit_platform_options"]),
            )
            assert resolution.selected == "distributed"
        assert [record for record in caplog.records if "deprecated" in record.message]

    def test_mcp_clickhouse_alias_stays_allowlisted(self) -> None:
        from benchbox.mcp.schemas import validate_platform_options

        assert validate_platform_options("clickhouse", {"deployment_mode": "server"}) == {"deployment_mode": "server"}
