import pytest

import benchbox.platforms
from benchbox.core.compute_resource import normalize_compute_options, resolve_resource_kind
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_normalize_canonical_alias_disagreement_fails() -> None:
    with pytest.raises(PlatformOptionError, match="disagrees with 'warehouse="):
        normalize_compute_options("snowflake", {"compute_resource": "NEW_WH", "warehouse": "COMPUTE_WH"})


def test_normalize_matching_canonical_alias_passes() -> None:
    resolved = normalize_compute_options("snowflake", {"compute_resource": "W", "warehouse": "W"})
    assert resolved["compute_resource"] == "W"


def test_normalize_dict_kind_canonical_only_fails() -> None:
    with pytest.raises(PlatformOptionError, match="could be 'workgroup_name', 'cluster_identifier'"):
        normalize_compute_options("redshift", {"compute_resource": "wg"})


def test_normalize_dict_kind_both_aliases_same_value_fails() -> None:
    with pytest.raises(PlatformOptionError, match="Ambiguous compute resource"):
        normalize_compute_options("redshift", {"workgroup_name": "x", "cluster_identifier": "x"})


def test_normalize_dict_kind_single_alias_with_canonical_passes() -> None:
    resolved = normalize_compute_options("redshift", {"compute_resource": "wg", "workgroup_name": "wg"})
    assert resolved["compute_resource"] == "wg"


def test_kind_dict_canonical_only_fails() -> None:
    with pytest.raises(PlatformOptionError, match="could be 'workgroup_name', 'cluster_identifier'"):
        resolve_resource_kind("redshift", {"compute_resource": "wg"})


def test_kind_dict_both_aliases_same_value_fails() -> None:
    with pytest.raises(PlatformOptionError, match="Ambiguous compute resource"):
        resolve_resource_kind("redshift", {"workgroup_name": "x", "cluster_identifier": "x"})


def test_kind_dict_single_alias_resolves() -> None:
    assert resolve_resource_kind("redshift", {"workgroup_name": "wg"}) == "workgroup"
    assert resolve_resource_kind("redshift", {"cluster_identifier": "c"}) == "cluster"


def test_kind_str_platform_unchanged() -> None:
    assert resolve_resource_kind("snowflake", {"compute_resource": "W"}) == "warehouse"
    assert resolve_resource_kind("snowflake", {"warehouse": "W"}) == "warehouse"


def test_parse_options_size_disagreement_fails() -> None:
    with pytest.raises(PlatformOptionError, match="Keep only one spelling"):
        PlatformHookRegistry.parse_options("firebolt", [("engine_size", "S"), ("compute_size", "M")])


