from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

import benchbox.core.results.anonymization as anonymization
from benchbox.core.execution_engine import (
    DEFAULT_EXECUTION_ENGINE,
    DEPRECATED_OPTION_ALIASES,
    EXECUTION_ENGINE_CLASSES,
    RESERVED_OPTION_KEYS,
    supported_execution_engines,
)
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionSpec
from benchbox.core.platform_manifest import PLATFORM_MANIFEST, get_all_platform_aliases
from benchbox.mcp.schemas import MCP_PLATFORM_OPTION_ALLOWLIST, MCP_PLATFORM_OPTION_CONTRACT

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GRANDFATHERED_KEYS: frozenset[tuple[str, str]] = frozenset()
REGISTERED_PLATFORM_SENTINELS = ("polars-df", "snowflake", "databricks", "redshift", "firebolt", "influxdb")
CORE_VOCABULARY_KEYS = ("execution_engine", "compute_resource", "compute_size", "gateway")


class Registration(NamedTuple):
    surface: str
    platform: str
    key: str
    is_alias: bool


def _import_adapter_modules() -> None:
    importlib.import_module("benchbox.platforms")
    for entry in PLATFORM_MANIFEST:
        if entry.adapter is not None:
            pytest.importorskip(entry.adapter.module)


def _registrations() -> list[Registration]:
    _import_adapter_modules()
    found: list[Registration] = []
    for platform, specs in PlatformHookRegistry._option_specs.items():
        for name, spec in specs.items():
            found.append(Registration("option spec", platform, name, False))
            found.extend(Registration("option spec", platform, alias.lower(), True) for alias in spec.aliases)
    for platform, options in MCP_PLATFORM_OPTION_ALLOWLIST.items():
        found.extend(Registration("MCP allowlist", platform, name, False) for name in options)
    for platform, contracts in MCP_PLATFORM_OPTION_CONTRACT.items():
        for contract in contracts.values():
            found.extend(Registration("MCP contract", platform, alias, True) for alias in contract.aliases)
    return found


def _platforms_without_option_specs(platforms: tuple[str, ...]) -> list[str]:
    aliases = get_all_platform_aliases()
    return [name for name in platforms if not PlatformHookRegistry._option_specs.get(aliases.get(name, name))]


def _violations(registrations: list[Registration]) -> list[Registration]:
    return [
        item
        for item in registrations
        if item.key in RESERVED_OPTION_KEYS
        and (item.platform, item.key) not in GRANDFATHERED_KEYS
        and not (item.is_alias and (item.platform, item.key) in DEPRECATED_OPTION_ALIASES)
    ]


def _declared_engines() -> list[tuple[str, str, str]]:
    return [
        (entry.key, name, engine["class"])
        for entry in PLATFORM_MANIFEST
        for name, engine in entry.capabilities.get("execution_engines", {}).items()
    ]


def test_manifest_engine_classes_are_core_classes() -> None:
    unknown = [item for item in _declared_engines() if item[2] not in EXECUTION_ENGINE_CLASSES]
    assert unknown == []


def test_engine_name_maps_to_one_class_on_every_platform() -> None:
    classes: dict[str, set[str]] = {}
    for _platform, name, engine_class in _declared_engines():
        classes.setdefault(name, set()).add(engine_class)
    assert {name: values for name, values in classes.items() if len(values) > 1} == {}
    assert DEFAULT_EXECUTION_ENGINE not in classes


@pytest.mark.parametrize(
    ("platform", "document"),
    [
        ("pg-duckdb", "pg_duckdb.md"),
        ("dask-df", "dask-dataframe.md"),
        ("databricks", "databricks.md"),
        ("velox", "velox.md"),
    ],
)
def test_platform_guides_list_declared_execution_engines(platform: str, document: str) -> None:
    doc_path = Path(__file__).resolve().parents[3] / "docs" / "platforms" / document
    contents = doc_path.read_text(encoding="utf-8")
    assert all(f"`{name}`" in contents for name in supported_execution_engines(platform))


def test_option_registry_holds_the_sentinel_platforms() -> None:
    _import_adapter_modules()
    assert _platforms_without_option_specs(REGISTERED_PLATFORM_SENTINELS) == []


def test_sentinel_check_fails_when_the_option_registry_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PlatformHookRegistry, "_option_specs", {})
    assert _platforms_without_option_specs(REGISTERED_PLATFORM_SENTINELS) == list(REGISTERED_PLATFORM_SENTINELS)


def test_no_unlisted_reserved_option_keys() -> None:
    registrations = _registrations()
    assert registrations
    assert _violations(registrations) == []


def test_grandfather_list_matches_live_registrations() -> None:
    live = {(item.platform, item.key) for item in _registrations() if item.key in RESERVED_OPTION_KEYS}
    assert live - set(DEPRECATED_OPTION_ALIASES) == GRANDFATHERED_KEYS


def test_new_reserved_canonical_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        PlatformHookRegistry._option_specs,
        "fixture-platform",
        {"engine": PlatformOptionSpec(name="engine"), "routing": PlatformOptionSpec(name="routing", aliases=("mode",))},
    )
    assert {(item.key, item.is_alias) for item in _violations(_registrations())} == {("engine", False), ("mode", True)}


def test_deprecated_aliases_target_canonical_names() -> None:
    for (platform, alias), entry in DEPRECATED_OPTION_ALIASES.items():
        assert entry.target not in RESERVED_OPTION_KEYS, (platform, alias)
        assert re.fullmatch(r"\d+\.\d+\.\d+", entry.removed_in), (platform, alias)


def test_anonymizer_does_not_hash_core_vocabulary_keys() -> None:
    specs_path = Path(anonymization.__file__).with_name("anonymization_specs.yaml")
    identifier_keys = yaml.safe_load(specs_path.read_text(encoding="utf-8"))["identifier_keys"]
    compact = {re.sub(r"[^a-z0-9]+", "", key) for key in CORE_VOCABULARY_KEYS}
    assert compact.isdisjoint(identifier_keys)
