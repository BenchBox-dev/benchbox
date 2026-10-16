# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import ast
import inspect

import pytest

from benchbox.core.tuning import ddl_generator as ddl_generator_module
from benchbox.core.tuning.capability_registry import (
    PLATFORM_TUNING_CAPABILITIES,
    WORKLOAD_PROFILE_MAPPED_PLATFORMS,
    get_capability,
    interface_compatibility_map,
    known_registry_platforms,
    resolve_platform_key,
)
from benchbox.core.tuning.ddl_generator import get_ddl_generator
from benchbox.core.tuning.interface import (
    _KNOWN_COMPATIBILITY_PLATFORMS,
    _PLATFORM_COMPATIBILITY_MAP,
    TuningType,
)
from benchbox.core.tuning.platform_capabilities import map_candidate_to_platform
from benchbox.core.tuning.workload_profiles import VALID_ROLES, WorkloadTuningCandidate

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


EXPECTED_INTERFACE_VS_REGISTRY_DRIFT: frozenset[str] = frozenset()

EXPECTED_GENERATOR_PLATFORMS_WITHOUT_REGISTRY_ENTRY: frozenset[str] = frozenset()


EXPECTED_PRE_REFACTOR_COMPATIBILITY_BASELINE_20260716: dict[str, frozenset[str]] = {
    "duckdb": frozenset(
        {
            "sorting",
            "partitioning",
            "primary_keys",
            "foreign_keys",
            "unique_constraints",
            "check_constraints",
        }
    ),
    "snowflake": frozenset(
        {
            "clustering",
            "partitioning",
            "primary_keys",
            "foreign_keys",
            "unique_constraints",
            "check_constraints",
            "materialized_views",
        }
    ),
    "bigquery": frozenset(
        {
            "partitioning",
            "clustering",
            "primary_keys",
            "foreign_keys",
            "unique_constraints",
            "check_constraints",
            "materialized_views",
        }
    ),
    "redshift": frozenset(
        {
            "distribution",
            "sorting",
            "partitioning",
            "primary_keys",
            "foreign_keys",
            "unique_constraints",
            "check_constraints",
            "materialized_views",
        }
    ),
    "clickhouse": frozenset(
        {
            "partitioning",
            "sorting",
            "clustering",
            "primary_keys",
            "foreign_keys",
            "unique_constraints",
            "check_constraints",
            "materialized_views",
        }
    ),
    "databricks": frozenset(
        {
            "partitioning",
            "clustering",
            "distribution",
            "primary_keys",
            "foreign_keys",
            "unique_constraints",
            "check_constraints",
            "z_ordering",
            "liquid_clustering",
            "auto_optimize",
            "auto_compact",
            "bloom_filters",
            "materialized_views",
        }
    ),
    "sqlite": frozenset(
        {
            "primary_keys",
            "foreign_keys",
            "unique_constraints",
            "check_constraints",
        }
    ),
    "postgresql": frozenset(
        {
            "partitioning",
            "clustering",
            "primary_keys",
            "foreign_keys",
            "unique_constraints",
            "check_constraints",
            "bloom_filters",
            "materialized_views",
        }
    ),
    "mysql": frozenset(
        {
            "partitioning",
            "primary_keys",
            "foreign_keys",
            "unique_constraints",
            "check_constraints",
        }
    ),
}


def test_compatibility_matches_frozen_pre_refactor_baseline():
    assert frozenset(EXPECTED_PRE_REFACTOR_COMPATIBILITY_BASELINE_20260716) == _KNOWN_COMPATIBILITY_PLATFORMS

    for platform, expected_values in EXPECTED_PRE_REFACTOR_COMPATIBILITY_BASELINE_20260716.items():
        actual_values = frozenset(tuning_type.value for tuning_type in _PLATFORM_COMPATIBILITY_MAP[platform])
        assert actual_values == expected_values, (
            f"{platform}: _PLATFORM_COMPATIBILITY_MAP diverged from the frozen 2026-07-16 baseline "
            f"(expected {sorted(expected_values)}, got {sorted(actual_values)})"
        )

        for tuning_type in TuningType:
            expected_compatible = tuning_type.value in expected_values
            actual_compatible = tuning_type.is_compatible_with_platform(platform)
            assert actual_compatible == expected_compatible, (
                f"{platform}/{tuning_type.value}: is_compatible_with_platform()={actual_compatible} "
                f"diverges from the frozen 2026-07-16 baseline (expected {expected_compatible})"
            )


def test_interface_compatibility_map_matches_registry_derivation():
    assert interface_compatibility_map() == _PLATFORM_COMPATIBILITY_MAP
    assert not EXPECTED_INTERFACE_VS_REGISTRY_DRIFT


def test_known_compatibility_platforms_is_a_subset_of_registry_platforms():
    missing = _KNOWN_COMPATIBILITY_PLATFORMS - known_registry_platforms()
    assert missing == set(), f"interface.py known platforms missing from the registry: {sorted(missing)}"


def test_registry_has_platforms_interface_deliberately_excludes():
    extra = known_registry_platforms() - _KNOWN_COMPATIBILITY_PLATFORMS
    assert extra == {
        "starrocks",
        "doris",
        "trino",
        "presto",
        "athena",
        "firebolt",
        "azure-synapse",
        "timescaledb",
        "questdb",
        "pg-duckdb",
        "pg-mooncake",
        "cedardb",
        "citus",
        "paradedb",
        "singlestore",
        "databend",
        "datafusion",
        "motherduck",
        "fabric-warehouse",
    }


def test_workload_profile_mapped_platforms_matches_actual_dispatch():
    from benchbox.core.tuning.workload_profiles import ACCEPTED, TEMPORAL_PARTITION

    candidate = WorkloadTuningCandidate(
        benchmark="tpch",
        table="probe_table",
        column="probe_column",
        type="date",
        roles=(TEMPORAL_PARTITION,),
        query_count=1,
        query_ids=("Q1",),
        status=ACCEPTED,
        rationale="probe candidate for dispatch-coverage testing",
        evidence_source="test_capability_source_drift",
    )

    for platform in WORKLOAD_PROFILE_MAPPED_PLATFORMS:
        mapping = map_candidate_to_platform(platform, candidate)
        assert mapping.reason != f"{platform} has no TPC logical profile mapping yet", (
            f"{platform} is listed as workload-profile-mapped but the dispatcher has no branch for it"
        )

    mapping = map_candidate_to_platform("some-unmapped-platform", candidate)
    assert mapping.reason == "some-unmapped-platform has no TPC logical profile mapping yet"


def test_ddl_generator_platforms_without_registry_entry_are_the_expected_set():
    generator_backed_aliases = GENERATOR_REGISTRY_KEYS
    registry_platforms = known_registry_platforms()
    without_entry = frozenset(
        alias for alias in generator_backed_aliases if resolve_platform_key(alias) not in registry_platforms
    )
    assert without_entry == EXPECTED_GENERATOR_PLATFORMS_WITHOUT_REGISTRY_ENTRY

    for alias in generator_backed_aliases:
        generator = get_ddl_generator(alias)
        assert type(generator).__name__ != "NoOpDDLGenerator", f"{alias} unexpectedly has no real DDL generator"


@pytest.mark.parametrize("platform", ["duckdb", "clickhouse", "databricks", "starrocks"])
def test_w2_migration_order_platforms_have_registry_entries(platform):
    assert platform in PLATFORM_TUNING_CAPABILITIES
    assert PLATFORM_TUNING_CAPABILITIES[platform], f"{platform} has an empty capability entry"


@pytest.mark.parametrize(
    "platform",
    [
        "trino",
        "presto",
        "athena",
        "firebolt",
        "azure-synapse",
        "timescaledb",
        "questdb",
        "pg-duckdb",
        "pg-mooncake",
    ],
)
def test_coverage_20260716_platforms_have_registry_entries(platform):
    assert platform in PLATFORM_TUNING_CAPABILITIES
    assert PLATFORM_TUNING_CAPABILITIES[platform], f"{platform} has an empty capability entry"


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("azure_synapse", "azure-synapse"),
        ("synapse", "azure-synapse"),
        ("pg_duckdb", "pg-duckdb"),
        ("pg-duckdb", "pg-duckdb"),
        ("pg_mooncake", "pg-mooncake"),
        ("pg-mooncake", "pg-mooncake"),
    ],
)
def test_coverage_20260716_aliases_resolve_to_canonical_registry_key(alias, canonical):
    assert resolve_platform_key(alias) == canonical
    assert canonical in PLATFORM_TUNING_CAPABILITIES


def test_pg_duckdb_sorting_has_a_registry_entry():
    capability = get_capability("pg-duckdb", TuningType.SORTING)
    assert capability is not None
    assert capability.rendered_via == "none"


def _discover_generator_registry_keys() -> frozenset[str]:
    source = inspect.getsource(get_ddl_generator)
    tree = ast.parse(source)
    function_node = tree.body[0]
    assert isinstance(function_node, ast.FunctionDef)

    for node in ast.walk(function_node):
        is_generators_assign = isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "generators" for target in node.targets
        )
        is_generators_annassign = (
            isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "generators"
        )
        if not (is_generators_assign or is_generators_annassign):
            continue

        dict_node = node.value
        assert isinstance(dict_node, ast.Dict)
        keys: set[str] = set()
        for key in dict_node.keys:
            assert isinstance(key, ast.Constant) and isinstance(key.value, str), (
                f"Expected a string literal key in generators dict, got {key!r}"
            )
            keys.add(key.value)
        return frozenset(keys)

    raise AssertionError("Could not find a `generators = {...}` assignment in get_ddl_generator()")


def _discover_tuning_free_platform_keys() -> frozenset[str]:
    return frozenset(ddl_generator_module._TUNING_FREE_PLATFORMS)


def _make_candidate(role: str) -> WorkloadTuningCandidate:
    return WorkloadTuningCandidate(
        benchmark="tpch",
        table="t",
        column="c",
        type="DATE",
        roles=(role,),
        query_count=1,
        query_ids=("Q1",),
        status="accepted",
        rationale="capability-source-drift probe",
        evidence_source="test_capability_source_drift.py",
    )


def _discover_capability_supported_platforms(candidate_platform_keys: frozenset[str]) -> frozenset[str]:
    supported: set[str] = set()
    for platform in candidate_platform_keys:
        for role in VALID_ROLES:
            mapping = map_candidate_to_platform(platform, _make_candidate(role))
            if mapping.decision == "mapped":
                supported.add(platform)
                break
    return frozenset(supported)


GENERATOR_REGISTRY_KEYS = _discover_generator_registry_keys()
COMPAT_MAP_KEYS = frozenset(_PLATFORM_COMPATIBILITY_MAP)
CAPABILITY_SUPPORTED_KEYS = _discover_capability_supported_platforms(GENERATOR_REGISTRY_KEYS | COMPAT_MAP_KEYS)

EXPECTED_GENERATOR_ONLY_KEYS = frozenset(
    {
        "doris",
        "starrocks",
        "clickhouse-local",
        "clickhouse-server",
        "clickhouse-cloud",
        "chdb",
        "firebolt",
        "azure_synapse",
        "synapse",
        "trino",
        "presto",
        "athena",
        "spark",
        "delta",
        "fabric_warehouse",
        "questdb",
        "pg-duckdb",
        "pg_duckdb",
        "pg-mooncake",
        "pg_mooncake",
        "timescaledb",
    }
)

EXPECTED_COMPAT_ONLY_KEYS = frozenset({"sqlite", "mysql"})

EXPECTED_COMPAT_NOT_IN_CAPABILITY = frozenset({"clickhouse", "postgresql", "mysql", "sqlite"})

EXPECTED_CAPABILITY_NOT_IN_COMPAT: frozenset[str] = frozenset()


class TestGeneratorRegistryVsCompatibilityMapDrift:
    def test_generator_only_keys_match_allowlist(self) -> None:
        actual = GENERATOR_REGISTRY_KEYS - COMPAT_MAP_KEYS
        assert actual == EXPECTED_GENERATOR_ONLY_KEYS, (
            f"New drift between get_ddl_generator()'s registry and "
            f"_PLATFORM_COMPATIBILITY_MAP: {sorted(actual - EXPECTED_GENERATOR_ONLY_KEYS)} newly "
            f"generator-only, {sorted(EXPECTED_GENERATOR_ONLY_KEYS - actual)} no longer generator-only "
            "(stale allowlist entry -- remove it). Update EXPECTED_GENERATOR_ONLY_KEYS with a reason, "
            "or add a _PLATFORM_COMPATIBILITY_MAP entry if this platform should now enforce "
            "tuning-type compatibility."
        )

    def test_compat_only_keys_match_allowlist(self) -> None:
        actual = COMPAT_MAP_KEYS - GENERATOR_REGISTRY_KEYS
        assert actual == EXPECTED_COMPAT_ONLY_KEYS, (
            f"New drift: {sorted(actual - EXPECTED_COMPAT_ONLY_KEYS)} newly compat-only, "
            f"{sorted(EXPECTED_COMPAT_ONLY_KEYS - actual)} no longer compat-only (stale allowlist "
            "entry -- remove it, or register a DDL generator / add to _TUNING_FREE_PLATFORMS)."
        )

    def test_stale_tuning_free_exemption_is_not_hiding_new_compat_only_drift(self) -> None:
        tuning_free = _discover_tuning_free_platform_keys()
        assert "sqlite" in tuning_free


class TestCapabilityMapperVsCompatibilityMapDrift:
    def test_compat_keys_missing_from_capability_mapper_match_allowlist(self) -> None:
        actual = COMPAT_MAP_KEYS - CAPABILITY_SUPPORTED_KEYS
        assert actual == EXPECTED_COMPAT_NOT_IN_CAPABILITY, (
            f"New drift: {sorted(actual - EXPECTED_COMPAT_NOT_IN_CAPABILITY)} newly unsupported by "
            f"map_candidate_to_platform, {sorted(EXPECTED_COMPAT_NOT_IN_CAPABILITY - actual)} no longer "
            "unsupported (stale allowlist entry -- remove it, since the mapper now covers this platform)."
        )

    def test_capability_mapper_keys_missing_from_compat_map_match_allowlist(self) -> None:
        actual = CAPABILITY_SUPPORTED_KEYS - COMPAT_MAP_KEYS
        assert actual == EXPECTED_CAPABILITY_NOT_IN_COMPAT, (
            f"map_candidate_to_platform now supports a platform absent from "
            f"_PLATFORM_COMPATIBILITY_MAP: {sorted(actual)}. Add a compatibility-map entry, or "
            "document the divergence in EXPECTED_CAPABILITY_NOT_IN_COMPAT with a reason."
        )

    def test_capability_mapper_supported_set_is_the_expected_five_platforms(self) -> None:
        assert frozenset({"databricks", "duckdb", "bigquery", "redshift", "snowflake"}) == CAPABILITY_SUPPORTED_KEYS


_ADAPTER_MIXIN_MECHANISM = r"^adapter_mixin:(\w+)\.(\w+)$"
_platform_source_cache: tuple[tuple[str, str], ...] | None = None


def _platform_source_texts() -> tuple[tuple[str, str], ...]:
    global _platform_source_cache
    if _platform_source_cache is None:
        import pathlib

        import benchbox.core.tuning.capability_registry as capreg

        platforms_dir = pathlib.Path(capreg.__file__).resolve().parents[2] / "platforms"
        sources: list[tuple[str, str]] = []
        for path in sorted(platforms_dir.rglob("*.py")):
            try:
                sources.append((str(path), path.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                continue
        _platform_source_cache = tuple(sources)
    return _platform_source_cache


def _iter_registry_capabilities():
    for platform, entries in PLATFORM_TUNING_CAPABILITIES.items():
        for tuning_type, capability in entries.items():
            yield platform, tuning_type, capability


def _method_has_call_site(method_name: str) -> bool:
    needle = f".{method_name}("
    return any(needle in text for _path, text in _platform_source_texts())


def _find_adapter_method_source(class_name: str, method_name: str) -> str | None:
    for _path, text in _platform_source_texts():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                        return ast.get_source_segment(text, item)
    return None


def _method_only_returns_empty_string(method_source: str) -> bool:
    import textwrap

    func = ast.parse(textwrap.dedent(method_source)).body[0]
    returns = [node for node in ast.walk(func) if isinstance(node, ast.Return)]
    if not returns:
        return False
    return all(isinstance(node.value, ast.Constant) and node.value.value == "" for node in returns)


def test_generate_tuning_clause_has_no_production_call_site():
    assert not _method_has_call_site("generate_tuning_clause"), (
        "generate_tuning_clause now has a production call site under benchbox/platforms/; the capability "
        "registry's 'no ddl claim rests on this dead mixin' invariant needs revisiting."
    )


def test_no_registry_entry_rests_on_generate_tuning_clause_mixin():
    offenders = [
        f"{platform}/{tuning_type.value} (rendered_via={cap.rendered_via}, mechanism={cap.mechanism_id})"
        for platform, tuning_type, cap in _iter_registry_capabilities()
        if "generate_tuning_clause" in cap.mechanism_id
    ]
    assert offenders == [], (
        "Capability entries claim rendering via the dead generate_tuning_clause mixin (no production call "
        "site -- see test_generate_tuning_clause_has_no_production_call_site):\n  " + "\n  ".join(offenders)
    )


def test_adapter_mixin_rendering_mechanisms_are_live_and_nonempty():
    import re

    offenders: list[str] = []
    for platform, tuning_type, cap in _iter_registry_capabilities():
        if cap.rendered_via not in ("ddl", "post_load", "session"):
            continue
        match = re.match(_ADAPTER_MIXIN_MECHANISM, cap.mechanism_id)
        if match is None:
            continue
        class_name, method_name = match.group(1), match.group(2)
        label = f"{platform}/{tuning_type.value} (mechanism={cap.mechanism_id})"
        source = _find_adapter_method_source(class_name, method_name)
        if source is None:
            offenders.append(f"{label}: {class_name}.{method_name} is not defined under benchbox/platforms/")
            continue
        if _method_only_returns_empty_string(source):
            offenders.append(f"{label}: {class_name}.{method_name} unconditionally returns '' (dead stub)")
        if not _method_has_call_site(method_name):
            offenders.append(f"{label}: {method_name} has no production call site under benchbox/platforms/")
    assert offenders == [], "adapter_mixin rendering mechanisms that are dead, empty, or unresolved:\n  " + "\n  ".join(
        offenders
    )
