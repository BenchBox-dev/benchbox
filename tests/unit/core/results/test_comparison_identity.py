from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from benchbox.core.platform_manifest import get_platform_manifest_entry
from benchbox.core.results import comparison_identity as identity

pytestmark = [pytest.mark.unit, pytest.mark.fast]

MODULE_PATH = Path(identity.__file__)


def bundle(name: str = "engine", **platform_fields: object) -> dict:
    return {"platform": {"name": name, **platform_fields}}


def test_every_table_key_is_a_platform_manifest_key() -> None:
    keys = set(identity.load_table()["platforms"])

    assert keys
    assert [key for key in keys if get_platform_manifest_entry(key) is None] == []


def test_the_default_entry_reads_the_platform_version() -> None:
    resolved = identity.resolve_comparison_version(bundle(version="4.2.0"))

    assert (resolved.version, resolved.is_release, resolved.source) == ("4.2.0", True, "platform.version")


def test_a_table_entry_names_the_field_that_holds_the_version() -> None:
    data = bundle("DuckDB", version="2.0.0-alpha38615", client_version="1.6.0.dev365")
    data["execution"] = {"driver_version_resolved": "1.6.0.dev365"}

    resolved = identity.resolve_comparison_version(data)

    assert (resolved.version, resolved.is_release) == ("1.6.0.dev365", False)
    assert resolved.source == "execution.driver_version_resolved"


def test_a_table_entry_falls_back_to_the_next_named_field() -> None:
    resolved = identity.resolve_comparison_version(bundle("DuckDB", version="1.5.5", client_version="unknown"))

    assert (resolved.version, resolved.source) == ("1.5.5", "platform.version")


@pytest.mark.parametrize("version", ["1.6.0.dev365", "2.0.0-alpha38615", "4.0.3-rc03", "1.0a1", "3.1.0-SNAPSHOT"])
def test_pre_release_versions_are_recognised(version: str) -> None:
    assert identity.resolve_comparison_version(bundle(version=version)).is_release is False


@pytest.mark.parametrize("version", ["1.3.2", "2026.36", "10.35.101", "53.0.0", "3.53.1"])
def test_release_versions_are_recognised(version: str) -> None:
    assert identity.resolve_comparison_version(bundle(version=version)).is_release is True


@pytest.mark.parametrize(
    "platform",
    [
        bundle(),
        bundle(version="unknown"),
        bundle(version=""),
        bundle(version=None),
        bundle(version=True),
        {"platform": "engine"},
        {},
    ],
)
def test_an_unresolvable_version_is_one_identity_and_never_a_release(platform: dict) -> None:
    resolved = identity.resolve_comparison_version(platform)

    assert resolved.version is None
    assert resolved.recorded is False
    assert resolved.is_release is False
    assert resolved.text == identity.NOT_RECORDED == "version not recorded"
    assert identity.comparison_identity(platform, "engine").label == "engine (version not recorded)"


def test_identity_keeps_the_variant_and_the_version_apart() -> None:
    first = identity.comparison_identity(bundle(version="1.0.0"), "engine+streaming")
    second = identity.comparison_identity(bundle(version="1.1.0"), "engine+streaming")

    assert first.label == "engine+streaming v1.0.0"
    assert first != second
    assert first.cohort_key == second.cohort_key == "engine+streaming"


def test_platform_key_ignores_trust_suffix_and_mode_words() -> None:
    assert identity.platform_key(bundle("DuckDB-trust-ci")) == "duckdb"
    assert identity.platform_key(bundle("ClickHouse Local")) == "clickhouse-local"
    assert identity.platform_key(bundle("Polars (DataFrame)")) == "polars"


def test_a_platform_without_a_table_entry_uses_the_default_fields() -> None:
    data = bundle("Unlisted", client_version="9.9.9")
    data["execution"] = {"driver_version_resolved": "8.8.8"}

    assert identity.resolve_comparison_version(data).version is None


def test_the_table_is_declarative_data_with_a_default_and_unrecorded_values() -> None:
    table = json.loads(identity.DATA_PATH.read_text(encoding="utf-8"))

    assert set(table) == {"default", "platforms", "unrecorded_values"}
    assert table["default"]["fields"] and table["default"]["pre_release"]
    assert all(entry["fields"] for entry in table["platforms"].values())


def test_module_imports_only_the_standard_library() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and not node.level
    }

    assert imported <= set(sys.stdlib_module_names)


def test_module_names_no_platform_and_no_folder() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8").lower()

    assert not [name for name in ("duckdb", "polars", "snowflake", "bigquery", "matrix", "parent.name") if name in text]
