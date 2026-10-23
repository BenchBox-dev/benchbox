import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchbox.core.platform_manifest import (
    _PLATFORM_MANIFEST_JSON,
    PLATFORM_MANIFEST,
    _load_manifest,
    get_adapter_imports,
    get_all_platform_aliases,
    get_platform_alias_modes,
    get_platform_aliases,
    get_platform_metadata,
)
from benchbox.core.platform_registry import (
    DeploymentCapability,
    PlatformCapability,
    PlatformRegistry,
)
from benchbox.platforms.base import PlatformAdapter

pytestmark = [pytest.mark.unit, pytest.mark.medium]

PLATFORM_MANIFEST_GENERATOR = Path(__file__).resolve().parents[3] / "_project" / "scripts" / "platform_manifest.py"
requires_dev_manifest_generator = pytest.mark.skipif(
    not PLATFORM_MANIFEST_GENERATOR.is_file(),
    reason="platform-manifest drift checks require development-only _project tooling",
)

EXPECTED_CLI_ALIASES = {
    "azure-synapse": "synapse",
    "azuresynapse": "synapse",
    "bq": "bigquery",
    "ch": "clickhouse-local",
    "cudf-df": "cudf",
    "dask-df": "dask",
    "datafusion-df": "datafusion",
    "dbx": "databricks",
    "duck": "duckdb",
    "fabric-dw": "fabric_dw",
    "fusion": "datafusion",
    "gbq": "bigquery",
    "lakesail-df": "lakesail",
    "pandas-df": "pandas",
    "pg": "postgresql",
    "pgsql": "postgresql",
    "polars-df": "polars",
    "postgres": "postgresql",
    "prestodb": "presto",
    "pyspark-df": "pyspark",
    "rs": "redshift",
    "snow": "snowflake",
    "trinodb": "trino",
}
EXPECTED_REGISTRY_ALIASES = {
    "azure_synapse": "synapse",
    "fabric-dw": "fabric_dw",
    "fabric_lakehouse": "fabric-lakehouse",
    "pg_duckdb": "pg-duckdb",
    "sqlite3": "sqlite",
}
EXPECTED_ADAPTER_REGISTRATION_ORDER = (
    "duckdb",
    "motherduck",
    "ducklake",
    "datafusion",
    "databricks",
    "databricks-df",
    "clickhouse",
    "clickhouse-local",
    "clickhouse-server",
    "clickhouse-cloud",
    "starrocks",
    "sqlite",
    "bigquery",
    "redshift",
    "snowflake",
    "trino",
    "starburst",
    "presto",
    "postgresql",
    "timescaledb",
    "pg-duckdb",
    "pg-mooncake",
    "questdb",
    "cedardb",
    "synapse",
    "pyspark",
    "firebolt",
    "databend",
    "doris",
    "singlestore",
    "influxdb",
    "fabric_dw",
    "athena",
    "glue",
    "emr-serverless",
    "athena-spark",
    "dataproc",
    "dataproc-serverless",
    "fabric-spark",
    "fabric-lakehouse",
    "synapse-spark",
    "spark",
    "lakesail",
    "velox",
    "polars",
    "snowpark-connect",
    "quanton",
    "paradedb",
    "citus",
    "gizmosql",
)


class TestAliasResolution:
    def test_resolve_sqlite3_alias(self):
        assert PlatformRegistry.resolve_platform_name("sqlite3") == "sqlite"

    def test_resolve_azure_synapse_alias(self):
        assert PlatformRegistry.resolve_platform_name("azure_synapse") == "synapse"

    def test_resolve_fabric_dw_alias(self):
        assert PlatformRegistry.resolve_platform_name("fabric-dw") == "fabric_dw"

    def test_resolve_canonical_name_unchanged(self):
        assert PlatformRegistry.resolve_platform_name("duckdb") == "duckdb"

    def test_resolve_is_case_insensitive(self):
        assert PlatformRegistry.resolve_platform_name("DuckDB") == "duckdb"
        assert PlatformRegistry.resolve_platform_name("SQLITE3") == "sqlite"

    def test_resolve_unknown_name_returns_lowered(self):
        assert PlatformRegistry.resolve_platform_name("NoSuchPlatform") == "nosuchplatform"

    def test_get_all_aliases_returns_copy(self):
        aliases = PlatformRegistry.get_all_aliases()
        assert isinstance(aliases, dict)
        assert "sqlite3" in aliases
        aliases["bogus_alias"] = "bogus"
        assert "bogus_alias" not in PlatformRegistry.get_all_aliases()

    def test_cli_dataframe_alias_is_not_a_core_registry_alias(self):
        from benchbox.cli.platform import normalize_platform_name

        assert normalize_platform_name("polars-df") == "polars"
        assert PlatformRegistry.resolve_platform_name("polars-df") == "polars-df"
        with pytest.raises(ValueError, match="not registered"):
            PlatformRegistry.get_adapter_class("polars-df")

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        EXPECTED_REGISTRY_ALIASES.items(),
    )
    def test_preexisting_core_aliases_still_resolve(self, alias, canonical):
        assert PlatformRegistry.resolve_platform_name(alias) == canonical


class TestAdapterRegistration:
    def setup_method(self):
        PlatformRegistry.clear_cache()

    def test_get_available_platforms_includes_duckdb(self):
        available = PlatformRegistry.get_available_platforms()
        assert "duckdb" in available

    def test_get_adapter_class_for_duckdb(self):
        from benchbox.platforms.duckdb import DuckDBAdapter

        cls = PlatformRegistry.get_adapter_class("duckdb")
        assert cls is DuckDBAdapter

    def test_get_adapter_class_resolves_alias(self):
        cls = PlatformRegistry.get_adapter_class("sqlite3")
        assert cls.__name__.lower().startswith("sqlite")

    def test_get_adapter_class_unknown_raises(self):
        with pytest.raises(ValueError, match="not registered"):
            PlatformRegistry.get_adapter_class("nosuchplatform_xyz")

    def test_register_adapter_clears_availability_cache(self):
        PlatformRegistry.get_platform_availability()
        assert PlatformRegistry._availability_cache is not None

        from benchbox.platforms.duckdb import DuckDBAdapter

        PlatformRegistry.register_adapter("duckdb", DuckDBAdapter)
        assert PlatformRegistry._availability_cache is None

    def test_registration_rejects_non_adapter_class(self, monkeypatch):
        class NotAnAdapter:
            pass

        monkeypatch.setattr(PlatformRegistry, "_adapters", {})
        with pytest.raises(TypeError, match="must subclass PlatformAdapter"):
            PlatformRegistry.register_adapter("duckdb", NotAnAdapter)

    def test_registration_rejects_alias_or_builtin_without_adapter(self, monkeypatch):
        from benchbox.platforms.duckdb import DuckDBAdapter

        monkeypatch.setattr(PlatformRegistry, "_adapters", {})
        with pytest.raises(ValueError, match="alias"):
            PlatformRegistry.register_adapter("duck", DuckDBAdapter)
        with pytest.raises(ValueError, match="alias"):
            PlatformRegistry.register_adapter("sqlite3", DuckDBAdapter)
        with pytest.raises(ValueError, match="no runtime adapter"):
            PlatformRegistry.register_adapter("pandas", DuckDBAdapter)

    def test_third_party_adapter_registration_remains_supported(self, monkeypatch):
        from benchbox.platforms.duckdb import DuckDBAdapter

        class ThirdPartyAdapter(DuckDBAdapter):
            pass

        monkeypatch.setattr(PlatformRegistry, "_adapters", {})
        monkeypatch.setattr(PlatformRegistry, "_auto_registered", True)
        PlatformRegistry.register_adapter("third-party", ThirdPartyAdapter)
        assert PlatformRegistry.get_adapter_class("third-party") is ThirdPartyAdapter
        assert PlatformRegistry.get_available_platforms() == ["third-party"]

    def test_registration_rejects_conflicting_class(self, monkeypatch):
        from benchbox.platforms.duckdb import DuckDBAdapter

        class ConflictingDuckDBAdapter(DuckDBAdapter):
            pass

        monkeypatch.setattr(PlatformRegistry, "_adapters", {})
        PlatformRegistry.register_adapter("duckdb", DuckDBAdapter)
        with pytest.raises(ValueError, match="already registered"):
            PlatformRegistry.register_adapter("duckdb", ConflictingDuckDBAdapter)

    def test_registered_classes_preserve_platform_adapter_invariant(self):
        PlatformRegistry.clear_cache()
        registered = PlatformRegistry.get_available_platforms()
        assert "duckdb" in registered
        for adapter_class in PlatformRegistry._adapters.values():
            assert issubclass(adapter_class, PlatformAdapter)


class TestLazySingleAdapterRegistration:
    @pytest.fixture
    def registered_names(self, monkeypatch):
        from benchbox.core import platform_registry

        monkeypatch.setattr(PlatformRegistry, "_adapters", {})
        monkeypatch.setattr(PlatformRegistry, "_auto_registered", False)
        monkeypatch.setattr(PlatformRegistry, "_availability_cache", None)
        names: list[str] = []
        original = platform_registry._try_register_adapter

        def recording(name, module_path, class_name):
            names.append(name)
            original(name, module_path, class_name)

        monkeypatch.setattr(platform_registry, "_try_register_adapter", recording)
        return names

    def test_get_adapter_class_registers_only_requested_platform(self, registered_names):
        from benchbox.platforms.sqlite import SQLiteAdapter

        assert PlatformRegistry.get_adapter_class("sqlite3") is SQLiteAdapter
        assert registered_names == ["sqlite"]
        assert PlatformRegistry._auto_registered is False

    def test_is_platform_available_registers_only_requested_platform(self, registered_names):
        assert PlatformRegistry.is_platform_available("duckdb") is True
        assert registered_names == ["duckdb"]

    def test_is_platform_available_unknown_platform_registers_nothing(self, registered_names):
        assert PlatformRegistry.is_platform_available("nosuchplatform_xyz") is False
        assert registered_names == []

    def test_get_platform_info_registers_only_requested_platform(self, registered_names):
        info = PlatformRegistry.get_platform_info("sqlite")
        assert info is not None
        assert info.enabled is True
        assert registered_names == ["sqlite"]

    def test_failed_single_registration_is_not_retried(self, registered_names, monkeypatch):
        from benchbox.core import platform_registry

        monkeypatch.setitem(
            platform_registry._OPTIONAL_ADAPTER_REGISTRATION_DIAGNOSTICS,
            "sqlite",
            {"status": "missing_optional_dependency"},
        )
        assert PlatformRegistry.is_platform_available("sqlite") is False
        info = PlatformRegistry.get_platform_info("sqlite")
        assert info is not None
        assert info.enabled is False
        assert registered_names == []

    def test_run_path_in_fresh_process_loads_only_requested_adapter(self):
        code = """
import sys
import benchbox.cli.main
from benchbox.cli.database import DatabaseManager
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.platform_registry import PlatformRegistry

manager = DatabaseManager()
assert PlatformRegistry.is_platform_available("duckdb")
assert "velox" not in PlatformHookRegistry._config_builders
PlatformRegistry.get_platform_info("velox")
assert "velox" in PlatformHookRegistry._config_builders
assert not PlatformRegistry._auto_registered
assert sorted(PlatformRegistry._adapters) == ["duckdb", "velox"], sorted(PlatformRegistry._adapters)
assert "benchbox.platforms.snowflake" not in sys.modules
assert "benchbox.platforms.bigquery" not in sys.modules
assert "benchbox.platforms.dataframe.dask_df" not in sys.modules
assert manager.available_databases
assert PlatformRegistry._auto_registered
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr or result.stdout

    def test_unknown_platform_error_lists_all_registered_platforms(self, registered_names):
        with pytest.raises(ValueError) as excinfo:
            PlatformRegistry.get_adapter_class("nosuchplatform_xyz")
        available = PlatformRegistry.get_available_platforms()
        assert str(excinfo.value) == (
            f"Platform 'nosuchplatform_xyz' not registered. Available: {', '.join(available)}"
        )
        assert "duckdb" in available
        assert PlatformRegistry._auto_registered is True

    def test_full_registration_after_single_keeps_manifest_order(self, registered_names):
        PlatformRegistry.get_adapter_class("sqlite")
        PlatformRegistry.get_adapter_class("duckdb")
        available = PlatformRegistry.get_available_platforms()
        manifest_order = [name for name, _, _ in get_adapter_imports()]
        assert available == [name for name in manifest_order if name in available]


class TestPlatformAvailability:
    def setup_method(self):
        PlatformRegistry.clear_cache()

    def test_duckdb_is_available(self):
        assert PlatformRegistry.is_platform_available("duckdb") is True

    def test_unknown_platform_is_not_available(self):
        assert PlatformRegistry.is_platform_available("nosuchplatform_xyz") is False

    def test_availability_cache_is_populated_after_first_call(self):
        PlatformRegistry.get_platform_availability()
        assert PlatformRegistry._availability_cache is not None

    def test_availability_returns_copy(self):
        avail1 = PlatformRegistry.get_platform_availability()
        avail2 = PlatformRegistry.get_platform_availability()
        assert avail1 == avail2
        assert avail1 is not avail2


class TestPlatformInfo:
    def test_get_platform_info_duckdb(self):
        info = PlatformRegistry.get_platform_info("duckdb")
        assert info is not None
        assert info.name == "duckdb"
        assert info.display_name == "DuckDB"
        assert info.category == "analytical"
        assert info.available is True
        assert "duckdb" in [lib.name for lib in info.libraries]

    def test_get_platform_info_unknown_returns_none(self):
        info = PlatformRegistry.get_platform_info("nosuchplatform_xyz")
        assert info is None

    def test_get_platform_info_resolves_alias(self):
        info = PlatformRegistry.get_platform_info("sqlite3")
        assert info is not None
        assert info.name == "sqlite"

    def test_driver_package_from_metadata(self):
        info = PlatformRegistry.get_platform_info("duckdb")
        assert info.driver_package == "duckdb"

    def test_get_platform_requirements_returns_string(self):
        reqs = PlatformRegistry.get_platform_requirements("duckdb")
        assert isinstance(reqs, str)
        assert "duckdb" in reqs.lower()

    def test_get_platform_requirements_unknown_returns_fallback(self):
        reqs = PlatformRegistry.get_platform_requirements("nosuchplatform_xyz")
        assert isinstance(reqs, str)


class TestPlatformFiltering:
    def test_get_platforms_by_category_analytical(self):
        analytical = PlatformRegistry.get_platforms_by_category("analytical")
        assert "duckdb" in analytical

    def test_get_platforms_by_category_embedded(self):
        embedded = PlatformRegistry.get_platforms_by_category("embedded")
        assert "sqlite" in embedded

    def test_get_platforms_by_category_nonexistent_returns_empty(self):
        result = PlatformRegistry.get_platforms_by_category("does_not_exist")
        assert result == []

    def test_get_platforms_by_adoption_mainstream(self):
        mainstream = PlatformRegistry.get_platforms_by_adoption("mainstream")
        assert "duckdb" in mainstream

    def test_get_platforms_by_adoption_niche_includes_sqlite(self):
        niche = PlatformRegistry.get_platforms_by_adoption("niche")
        assert "sqlite" in niche

    def test_get_platforms_by_adoption_nonexistent_returns_empty(self):
        result = PlatformRegistry.get_platforms_by_adoption("ultra_rare")
        assert result == []


class TestPlatformCapabilities:
    def test_get_platform_capabilities_duckdb(self):
        caps = PlatformRegistry.get_platform_capabilities("duckdb")
        assert isinstance(caps, PlatformCapability)
        assert caps.supports_sql is True
        assert caps.supports_dataframe is False
        assert caps.default_mode == "sql"

    def test_get_platform_capabilities_unknown_returns_none(self):
        caps = PlatformRegistry.get_platform_capabilities("nosuchplatform_xyz")
        assert caps is None

    def test_supports_mode_sql(self):
        assert PlatformRegistry.supports_mode("duckdb", "sql") is True

    def test_supports_mode_dataframe_false_for_duckdb(self):
        assert PlatformRegistry.supports_mode("duckdb", "dataframe") is False

    def test_supports_mode_unknown_platform_returns_false(self):
        assert PlatformRegistry.supports_mode("nosuchplatform_xyz", "sql") is False

    def test_supports_mode_unknown_mode_returns_false(self):
        assert PlatformRegistry.supports_mode("duckdb", "graphql") is False

    def test_get_default_mode_duckdb(self):
        assert PlatformRegistry.get_default_mode("duckdb") == "sql"

    def test_get_default_mode_unknown_returns_sql(self):
        assert PlatformRegistry.get_default_mode("nosuchplatform_xyz") == "sql"

    def test_get_dual_mode_platforms(self):
        dual = PlatformRegistry.get_dual_mode_platforms()
        assert isinstance(dual, list)
        assert "datafusion" in dual


class TestDeploymentCapabilities:
    def test_duckdb_has_local_deployment(self):
        caps = PlatformRegistry.get_platform_capabilities("duckdb")
        assert "local" in caps.deployment_modes
        local_dep = caps.deployment_modes["local"]
        assert isinstance(local_dep, DeploymentCapability)
        assert local_dep.mode == "local"
        assert local_dep.requires_credentials is False
        assert local_dep.default_for_platform is True

    def test_motherduck_has_managed_deployment(self):
        caps = PlatformRegistry.get_platform_capabilities("motherduck")
        assert caps is not None
        assert "managed" in caps.deployment_modes
        managed = caps.deployment_modes["managed"]
        assert managed.mode == "managed"
        assert managed.requires_credentials is True
        assert managed.requires_network is True

    def test_get_deployment_capability_specific_mode(self):
        dep = PlatformRegistry.get_deployment_capability("duckdb", "local")
        assert dep is not None
        assert dep.mode == "local"

    def test_get_deployment_capability_nonexistent_mode_returns_none(self):
        dep = PlatformRegistry.get_deployment_capability("duckdb", "cloud")
        assert dep is None

    def test_get_deployment_capability_unknown_platform_returns_none(self):
        dep = PlatformRegistry.get_deployment_capability("nosuchplatform_xyz", "local")
        assert dep is None

    def test_get_default_deployment_duckdb(self):
        assert PlatformRegistry.get_default_deployment("duckdb") == "local"

    def test_get_default_deployment_motherduck(self):
        assert PlatformRegistry.get_default_deployment("motherduck") == "managed"

    def test_get_default_deployment_unknown_returns_none(self):
        assert PlatformRegistry.get_default_deployment("nosuchplatform_xyz") is None

    def test_get_available_deployment_modes_duckdb(self):
        modes = PlatformRegistry.get_available_deployment_modes("duckdb")
        assert "local" in modes

    def test_get_available_deployment_modes_clickhouse(self):
        modes = PlatformRegistry.get_available_deployment_modes("clickhouse")
        assert "local" in modes
        assert "server" in modes

    def test_get_available_deployment_modes_unknown_returns_empty(self):
        modes = PlatformRegistry.get_available_deployment_modes("nosuchplatform_xyz")
        assert modes == []

    def test_supports_deployment_mode_true(self):
        assert PlatformRegistry.supports_deployment_mode("duckdb", "local") is True

    def test_supports_deployment_mode_false(self):
        assert PlatformRegistry.supports_deployment_mode("duckdb", "managed") is False

    @pytest.mark.parametrize("platform", ["snowflake", "databricks", "polars", "sqlite"])
    def test_platforms_without_deployment_modes_accept_only_local(self, platform: str):
        assert PlatformRegistry.get_available_deployment_modes(platform) == []
        assert PlatformRegistry.supports_deployment_mode(platform, "local") is True
        assert PlatformRegistry.supports_deployment_mode(platform, "cloud") is False
        assert PlatformRegistry.supports_deployment_mode(platform, "managed") is False

    def test_requires_cloud_storage_for_deployment_motherduck(self):
        assert PlatformRegistry.requires_cloud_storage_for_deployment("motherduck") is False

    def test_requires_cloud_storage_for_deployment_unknown(self):
        assert PlatformRegistry.requires_cloud_storage_for_deployment("nosuchplatform_xyz") is False


class TestPlatformFamilyAndInheritance:
    def test_get_platform_family_duckdb(self):
        assert PlatformRegistry.get_platform_family("duckdb") == "duckdb"

    def test_get_platform_family_motherduck(self):
        assert PlatformRegistry.get_platform_family("motherduck") == "duckdb"

    def test_get_platform_family_unknown_returns_none(self):
        assert PlatformRegistry.get_platform_family("nosuchplatform_xyz") is None

    def test_get_inherited_platform_motherduck(self):
        assert PlatformRegistry.get_inherited_platform("motherduck") == "duckdb"

    def test_get_inherited_platform_duckdb_returns_none(self):
        assert PlatformRegistry.get_inherited_platform("duckdb") is None

    def test_get_inherited_platform_unknown_returns_none(self):
        assert PlatformRegistry.get_inherited_platform("nosuchplatform_xyz") is None


class TestPlatformConflicts:
    def test_pg_duckdb_conflicts_with_pg_mooncake(self):
        conflicts = PlatformRegistry.get_platform_conflicts("pg-duckdb")
        assert "pg-mooncake" in conflicts

    def test_pg_mooncake_conflicts_with_pg_duckdb(self):
        conflicts = PlatformRegistry.get_platform_conflicts("pg-mooncake")
        assert "pg-duckdb" in conflicts

    def test_duckdb_has_no_conflicts(self):
        conflicts = PlatformRegistry.get_platform_conflicts("duckdb")
        assert conflicts == []

    def test_unknown_platform_has_no_conflicts(self):
        conflicts = PlatformRegistry.get_platform_conflicts("nosuchplatform_xyz")
        assert conflicts == []


class TestExtractRequirementPackage:
    def test_simple_package(self):
        assert PlatformRegistry._extract_requirement_package("duckdb") == "duckdb"

    def test_with_version_specifier(self):
        assert PlatformRegistry._extract_requirement_package("duckdb>=0.8.0") == "duckdb"

    def test_with_tilde_specifier(self):
        assert PlatformRegistry._extract_requirement_package("polars~=0.20.0") == "polars"

    def test_with_not_equal_specifier(self):
        assert PlatformRegistry._extract_requirement_package("pandas!=2.1.0") == "pandas"

    def test_builtin_description_format(self):
        result = PlatformRegistry._extract_requirement_package("sqlite3 (built-in)")
        assert result == "sqlite3"

    def test_empty_string_returns_none(self):
        assert PlatformRegistry._extract_requirement_package("") is None

    def test_none_input(self):
        assert PlatformRegistry._extract_requirement_package(None) is None

    def test_whitespace_only_returns_none(self):
        assert PlatformRegistry._extract_requirement_package("   ") is None


class TestPlatformManifest:
    def test_platforms_manifest_compatibility_facade_reexports_core_authority(self):
        from benchbox.core import platform_manifest as core_manifest
        from benchbox.platforms import manifest as compatibility_manifest

        assert compatibility_manifest.PLATFORM_MANIFEST is core_manifest.PLATFORM_MANIFEST
        assert compatibility_manifest.PlatformManifestEntry is core_manifest.PlatformManifestEntry
        assert compatibility_manifest.get_adapter_imports is core_manifest.get_adapter_imports

    def test_registry_and_cli_are_exact_manifest_projections(self):
        from benchbox.cli.platform import PLATFORM_ALIASES
        from benchbox.core.platform_registry import _OPTIONAL_ADAPTERS

        assert len(PLATFORM_MANIFEST) == 53
        assert PlatformRegistry.get_all_platform_metadata() == get_platform_metadata()
        assert get_platform_aliases("cli") == EXPECTED_CLI_ALIASES
        assert get_platform_aliases("registry") == EXPECTED_REGISTRY_ALIASES
        assert get_all_platform_aliases() == EXPECTED_CLI_ALIASES | EXPECTED_REGISTRY_ALIASES
        assert PlatformRegistry.get_all_aliases() == EXPECTED_REGISTRY_ALIASES
        assert PLATFORM_ALIASES == EXPECTED_CLI_ALIASES
        assert get_platform_alias_modes("cli") == {
            alias: "dataframe" for alias in EXPECTED_CLI_ALIASES if alias.endswith("-df")
        }
        assert get_adapter_imports() == _OPTIONAL_ADAPTERS

    def test_adapter_registration_order_preserves_public_availability_order(self, monkeypatch):
        from benchbox.core.platform_registry import _OPTIONAL_ADAPTERS

        assert tuple(name for name, _module, _class_name in _OPTIONAL_ADAPTERS) == EXPECTED_ADAPTER_REGISTRATION_ORDER
        monkeypatch.setattr(PlatformRegistry, "_adapters", {})
        monkeypatch.setattr(PlatformRegistry, "_auto_registered", False)
        assert PlatformRegistry.get_available_platforms() == list(EXPECTED_ADAPTER_REGISTRATION_ORDER)

    def test_static_manifest_import_does_not_load_optional_sdks(self):
        code = """
import sys
from benchbox.core.platform_manifest import PLATFORM_MANIFEST
blocked = {'chdb', 'datafusion', 'databricks.sql', 'google.cloud.bigquery', 'polars', 'pyspark', 'snowflake'}
loaded = sorted(name for name in blocked if name in sys.modules)
raise SystemExit('optional SDKs loaded: ' + ', '.join(loaded) if loaded else 0)
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr or result.stdout

    def test_duplicate_canonical_key_is_rejected(self):
        payload = json.loads(_PLATFORM_MANIFEST_JSON)
        payload.append(copy.deepcopy(payload[0]))
        with pytest.raises(ValueError, match="Duplicate canonical platform keys"):
            _load_manifest(json.dumps(payload))

    def test_duplicate_alias_is_rejected(self):
        payload = json.loads(_PLATFORM_MANIFEST_JSON)
        payload[1]["aliases"].append(copy.deepcopy(payload[0]["aliases"][0]))
        payload[1]["aliases"].sort(key=lambda alias: alias["name"])
        with pytest.raises(ValueError, match="Duplicate platform alias"):
            _load_manifest(json.dumps(payload))

    def test_dataframe_alias_requires_typed_mode_semantics(self):
        payload = json.loads(_PLATFORM_MANIFEST_JSON)
        payload[1]["aliases"][0].pop("implied_mode")
        with pytest.raises(ValueError, match="must declare implied_mode='dataframe'"):
            _load_manifest(json.dumps(payload))

    def test_duplicate_adapter_registration_is_rejected(self):
        payload = json.loads(_PLATFORM_MANIFEST_JSON)
        payload[1]["adapter"] = copy.deepcopy(payload[0]["adapter"])
        with pytest.raises(ValueError, match="Duplicate adapter registrations"):
            _load_manifest(json.dumps(payload))

    def test_invalid_execution_capabilities_are_rejected(self):
        payload = json.loads(_PLATFORM_MANIFEST_JSON)
        payload[0]["capabilities"]["supports_sql"] = False
        payload[0]["capabilities"]["supports_dataframe"] = True
        with pytest.raises(ValueError, match="defaults to unsupported SQL mode"):
            _load_manifest(json.dumps(payload))

    @requires_dev_manifest_generator
    def test_generated_inventory_and_semantic_surfaces_are_current(self):
        result = subprocess.run(
            [sys.executable, str(PLATFORM_MANIFEST_GENERATOR), "--check"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout

    @requires_dev_manifest_generator
    def test_dataframe_availability_typo_is_detected_and_restoration_passes(self, monkeypatch):
        import benchbox.platforms as platform_package
        from _project.scripts.platform_manifest import validate_platform_surfaces

        adapter_name, availability_name, guidance = platform_package._DATAFRAME_PLATFORM_INFO["polars-df"]
        assert availability_name == "POLARS_AVAILABLE"
        with monkeypatch.context() as scoped:
            scoped.setitem(
                platform_package._DATAFRAME_PLATFORM_INFO,
                "polars-df",
                (adapter_name, "POLARS_AVAILABL", guidance),
            )
            assert any(
                "polars-df" in error and "POLARS_AVAILABL" in error and "unknown lazy availability constant" in error
                for error in validate_platform_surfaces()
            )
        assert not any("POLARS_AVAILABL" in error for error in validate_platform_surfaces())
