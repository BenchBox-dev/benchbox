# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.platform_manifest import PLATFORM_MANIFEST, get_platform_aliases
from benchbox.core.platform_registry import PlatformRegistry

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


CANONICAL_SQL_PLATFORMS = {entry.key for entry in PLATFORM_MANIFEST if entry.capabilities["supports_sql"]}
PLATFORM_ALIASES = get_platform_aliases("cli")
DATAFRAME_ONLY_PLATFORMS = {
    entry.key
    for entry in PLATFORM_MANIFEST
    if entry.capabilities["supports_dataframe"] and not entry.capabilities["supports_sql"] and entry.adapter is None
}
HYBRID_DATAFRAME_PLATFORMS = {
    entry.key
    for entry in PLATFORM_MANIFEST
    if entry.capabilities["supports_dataframe"] and not entry.capabilities["supports_sql"] and entry.adapter is not None
}


class TestPlatformRegistrationAlignment:
    def setup_method(self):
        PlatformRegistry.clear_cache()

    def test_all_canonical_platforms_in_registry_metadata(self):
        metadata = PlatformRegistry.get_all_platform_metadata()

        missing = CANONICAL_SQL_PLATFORMS - set(metadata.keys())
        assert not missing, f"Platforms missing from PlatformRegistry metadata: {missing}"

    def test_all_canonical_platforms_registered_in_registry(self):
        registered = set(PlatformRegistry.get_available_platforms())

        missing = CANONICAL_SQL_PLATFORMS - registered
        assert not missing, (
            f"Platforms missing from PlatformRegistry._adapters: {missing}. "
            "Add registration in auto_register_platforms()."
        )


class TestPlatformRequirementsAlignment:
    def setup_method(self):
        PlatformRegistry.clear_cache()

    def test_all_platforms_have_metadata_requirements(self):
        metadata = PlatformRegistry.get_all_platform_metadata()

        for platform in CANONICAL_SQL_PLATFORMS:
            if platform in metadata:
                spec = metadata[platform]
                assert "installation_command" in spec, f"Platform '{platform}' missing installation_command in metadata"
                assert spec["installation_command"], f"Platform '{platform}' has empty installation_command"

    def test_all_platforms_have_driver_package(self):
        metadata = PlatformRegistry.get_all_platform_metadata()

        for platform in CANONICAL_SQL_PLATFORMS:
            if platform in metadata:
                spec = metadata[platform]
                assert "driver_package" in spec, f"Platform '{platform}' missing driver_package key in metadata"


class TestPlatformCapabilitiesAlignment:
    def setup_method(self):
        PlatformRegistry.clear_cache()

    def test_all_sql_platforms_support_sql_mode(self):
        for platform in CANONICAL_SQL_PLATFORMS:
            caps = PlatformRegistry.get_platform_capabilities(platform)
            assert caps is not None, f"Platform '{platform}' has no capabilities defined"
            assert caps.supports_sql, f"Platform '{platform}' is a SQL platform but supports_sql=False"

    def test_dataframe_only_platforms_dont_support_sql(self):
        for platform in DATAFRAME_ONLY_PLATFORMS:
            caps = PlatformRegistry.get_platform_capabilities(platform)
            if caps is not None:
                assert not caps.supports_sql, f"DataFrame-only platform '{platform}' should have supports_sql=False"
                assert caps.supports_dataframe, (
                    f"DataFrame-only platform '{platform}' should have supports_dataframe=True"
                )

    def test_hybrid_dataframe_platforms_dont_support_sql(self):
        for platform in HYBRID_DATAFRAME_PLATFORMS:
            caps = PlatformRegistry.get_platform_capabilities(platform)
            if caps is not None:
                assert not caps.supports_sql, (
                    f"Hybrid platform '{platform}' should have supports_sql=False "
                    "(adapter exists for data loading only)"
                )
                assert caps.supports_dataframe, f"Hybrid platform '{platform}' should have supports_dataframe=True"

    def test_dual_mode_platforms_support_both(self):
        dual_mode = PlatformRegistry.get_dual_mode_platforms()

        for platform in dual_mode:
            caps = PlatformRegistry.get_platform_capabilities(platform)
            assert caps.supports_sql, f"Dual-mode platform '{platform}' missing supports_sql"
            assert caps.supports_dataframe, f"Dual-mode platform '{platform}' missing supports_dataframe"

    def test_manifest_adapter_refs_resolve_to_platform_adapters(self):
        import importlib

        from benchbox.platforms.base import PlatformAdapter

        unresolvable = []
        for entry in PLATFORM_MANIFEST:
            if entry.adapter is None:
                continue
            try:
                module = importlib.import_module(entry.adapter.module)
                cls = getattr(module, entry.adapter.class_name, None)
            except ImportError as exc:
                unresolvable.append(f"{entry.key}: cannot import {entry.adapter.module}: {exc}")
                continue
            if not (isinstance(cls, type) and issubclass(cls, PlatformAdapter)):
                unresolvable.append(f"{entry.key}: {entry.adapter.class_name} is not a PlatformAdapter subclass")
        assert not unresolvable, f"Manifest adapter refs with no executable adapter: {unresolvable}"

    def test_manifest_carries_no_legacy_unsupported_benchmarks(self):
        stale = [entry.key for entry in PLATFORM_MANIFEST if (entry.capabilities or {}).get("unsupported_benchmarks")]
        assert not stale, (
            f"Platforms with legacy manifest gate dicts (ignored by the registry): {stale}. "
            "Express the gate as a benchmark_gate rule instead."
        )

    def test_default_mode_is_a_supported_mode(self):
        for entry in PLATFORM_MANIFEST:
            caps = entry.capabilities or {}
            default = caps.get("default_mode", "sql")
            assert caps.get(f"supports_{default}"), (
                f"Platform '{entry.key}' defaults to mode '{default}' without supports_{default}"
            )

    def test_dataframe_adapter_mapping_matches_registry_capabilities(self):
        from benchbox.platforms import _DATAFRAME_PLATFORM_INFO

        for spelling in _DATAFRAME_PLATFORM_INFO:
            canonical = PLATFORM_ALIASES.get(spelling, spelling)
            caps = PlatformRegistry.get_platform_capabilities(canonical)
            assert caps is not None, (
                f"DataFrame factory spelling '{spelling}' resolves to missing platform '{canonical}'"
            )
            assert caps.supports_dataframe, (
                f"DataFrame factory spelling '{spelling}' resolves to platform '{canonical}' without supports_dataframe"
            )


class TestPlatformRegistryAliasResolution:
    def setup_method(self):
        PlatformRegistry.clear_cache()

    def test_resolve_platform_name_canonical(self):
        assert PlatformRegistry.resolve_platform_name("duckdb") == "duckdb"
        assert PlatformRegistry.resolve_platform_name("sqlite") == "sqlite"
        assert PlatformRegistry.resolve_platform_name("synapse") == "synapse"

    def test_resolve_platform_name_aliases(self):
        assert PlatformRegistry.resolve_platform_name("sqlite3") == "sqlite"
        assert PlatformRegistry.resolve_platform_name("azure_synapse") == "synapse"

    def test_resolve_platform_name_case_insensitive(self):
        assert PlatformRegistry.resolve_platform_name("SQLITE3") == "sqlite"
        assert PlatformRegistry.resolve_platform_name("SQLite3") == "sqlite"
        assert PlatformRegistry.resolve_platform_name("Azure_Synapse") == "synapse"
        assert PlatformRegistry.resolve_platform_name("AZURE_SYNAPSE") == "synapse"

    def test_resolve_platform_name_unknown(self):
        assert PlatformRegistry.resolve_platform_name("unknown") == "unknown"
        assert PlatformRegistry.resolve_platform_name("UNKNOWN") == "unknown"

    def test_get_all_aliases(self):
        aliases = PlatformRegistry.get_all_aliases()
        assert isinstance(aliases, dict)
        assert "sqlite3" in aliases
        assert aliases["sqlite3"] == "sqlite"
        assert "azure_synapse" in aliases
        assert aliases["azure_synapse"] == "synapse"

    def test_get_all_aliases_returns_copy(self):
        aliases1 = PlatformRegistry.get_all_aliases()
        aliases2 = PlatformRegistry.get_all_aliases()
        assert aliases1 == aliases2
        assert aliases1 is not aliases2
        aliases1["test"] = "value"
        assert "test" not in aliases2

    def test_get_platform_info_with_alias(self):
        info = PlatformRegistry.get_platform_info("sqlite3")
        assert info is not None
        assert info.name == "sqlite"
        assert info.display_name == "SQLite"

        info = PlatformRegistry.get_platform_info("azure_synapse")
        assert info is not None
        assert info.name == "synapse"

    def test_get_platform_capabilities_with_alias(self):
        caps = PlatformRegistry.get_platform_capabilities("sqlite3")
        assert caps is not None
        assert caps.supports_sql is True

        caps = PlatformRegistry.get_platform_capabilities("azure_synapse")
        assert caps is not None
        assert caps.supports_sql is True

    def test_get_adapter_class_with_alias(self):
        try:
            adapter_class = PlatformRegistry.get_adapter_class("sqlite3")
            assert adapter_class is not None
            canonical_class = PlatformRegistry.get_adapter_class("sqlite")
            assert adapter_class is canonical_class
        except ValueError:
            pass


class TestMetadataConsistency:
    def setup_method(self):
        PlatformRegistry.clear_cache()

    def test_registered_platforms_have_metadata(self):
        registered = PlatformRegistry.get_available_platforms()
        metadata = PlatformRegistry.get_all_platform_metadata()

        for platform in registered:
            assert platform in metadata, (
                f"Registered platform '{platform}' has no metadata. Add entry in _build_platform_metadata()."
            )

    def test_metadata_platforms_can_get_info(self):
        metadata = PlatformRegistry.get_all_platform_metadata()

        for platform in metadata:
            info = PlatformRegistry.get_platform_info(platform)
            if info is not None:
                assert info.name == platform
                assert info.display_name == metadata[platform]["display_name"]

    def test_no_orphaned_registrations(self):
        registered = set(PlatformRegistry.get_available_platforms())
        metadata_platforms = set(PlatformRegistry.get_all_platform_metadata().keys())

        orphaned = registered - metadata_platforms
        assert not orphaned, (
            f"Platforms registered but missing metadata: {orphaned}. Add metadata in _build_platform_metadata()."
        )


