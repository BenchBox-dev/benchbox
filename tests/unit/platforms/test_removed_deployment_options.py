from __future__ import annotations

import pytest

from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionError
from benchbox.core.removed_options import DEPLOYMENT_OPTION_KEYS
from benchbox.platforms import get_platform_adapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PLATFORMS = ("databend", "ducklake", "firebolt", "influxdb", "lakesail", "pg-duckdb", "timescaledb", "velox")
REMOVED_CASES = [(platform, key) for platform in PLATFORMS for key in DEPLOYMENT_OPTION_KEYS[platform]]
COMPUTE_CASES = [
    ("databend", "warehouse", "compute_resource"),
    ("firebolt", "engine_name", "compute_resource"),
    ("firebolt", "engine_size", "compute_size"),
]


@pytest.mark.parametrize(("platform", "key"), REMOVED_CASES)
def test_platform_option_rejects_removed_deployment_key(platform: str, key: str) -> None:
    with pytest.raises(PlatformOptionError, match=f"'{key}' for '{platform}' was removed in BenchBox 0.6.0"):
        PlatformHookRegistry.parse_options(platform, [(key, "x")])


@pytest.mark.parametrize(("platform", "key"), REMOVED_CASES)
def test_get_platform_adapter_rejects_removed_deployment_key(platform: str, key: str) -> None:
    with pytest.raises(TypeError, match=f"'{key}' for '{platform}' was removed.*--platform {platform}:"):
        get_platform_adapter(platform, **{key: "x"})


@pytest.mark.parametrize(("platform", "key", "replacement"), COMPUTE_CASES)
def test_removed_compute_key_names_replacement(platform: str, key: str, replacement: str) -> None:
    with pytest.raises(PlatformOptionError, match=f"use '{replacement}' instead"):
        PlatformHookRegistry.parse_options(platform, [(key, "x")])
    with pytest.raises(TypeError, match=f"use '{replacement}' instead"):
        get_platform_adapter(platform, **{key: "x"})
