from __future__ import annotations

import importlib.machinery
import types
from unittest.mock import patch

import pytest

from benchbox.platforms.clickhouse.deployment_mode import (
    clickhouse_cloud_mode_error_message,
    normalize_clickhouse_deployment_mode,
    resolve_clickhouse_deployment_mode,
)
from benchbox.platforms.clickhouse.metadata import ClickHouseMetadataMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_normalize_clickhouse_deployment_mode_accepts_canonical_values() -> None:
    assert normalize_clickhouse_deployment_mode("local") == "local"
    assert normalize_clickhouse_deployment_mode("server") == "server"


def test_normalize_clickhouse_deployment_mode_maps_embedded_alias_to_local() -> None:
    assert normalize_clickhouse_deployment_mode("embedded") == "local"


def test_normalize_clickhouse_deployment_mode_maps_legacy_embedded_boolean() -> None:
    assert normalize_clickhouse_deployment_mode(True) == "local"
    assert normalize_clickhouse_deployment_mode(False) == "server"


def test_normalize_clickhouse_deployment_mode_rejects_base_cloud_mode() -> None:
    with pytest.raises(ValueError, match="clickhouse-cloud"):
        normalize_clickhouse_deployment_mode("cloud")


def test_normalize_clickhouse_deployment_mode_allows_cloud_for_cloud_subclass() -> None:
    assert normalize_clickhouse_deployment_mode("cloud", allow_cloud=True) == "cloud"


def test_normalize_clickhouse_deployment_mode_is_case_and_whitespace_insensitive() -> None:
    assert normalize_clickhouse_deployment_mode("SERVER") == "server"
    assert normalize_clickhouse_deployment_mode(" Local ") == "local"
    assert normalize_clickhouse_deployment_mode("Embedded") == "local"


def test_normalize_clickhouse_deployment_mode_returns_default_for_empty_string() -> None:
    assert normalize_clickhouse_deployment_mode("") == "local"
    assert normalize_clickhouse_deployment_mode("   ") == "local"


def test_normalize_clickhouse_deployment_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="Invalid ClickHouse deployment mode 'invalid'"):
        normalize_clickhouse_deployment_mode("invalid")


def test_resolve_clickhouse_deployment_mode_prefers_canonical_key() -> None:
    config = {"deployment_mode": "server", "mode": "embedded", "embedded": True}
    assert resolve_clickhouse_deployment_mode(config) == "server"


def test_resolve_clickhouse_deployment_mode_falls_back_to_legacy_mode_alias() -> None:
    assert resolve_clickhouse_deployment_mode({"mode": "embedded"}) == "local"


def test_resolve_clickhouse_deployment_mode_falls_back_to_legacy_embedded_flag() -> None:
    assert resolve_clickhouse_deployment_mode({"embedded": False}) == "server"


def test_resolve_empty_string_deployment_mode_falls_through_to_legacy_keys() -> None:

    assert resolve_clickhouse_deployment_mode({"deployment_mode": "", "mode": "server"}) == "server"
    assert resolve_clickhouse_deployment_mode({"deployment_mode": "", "mode": ""}) == "local"


def test_resolve_none_embedded_falls_through_to_default() -> None:

    assert resolve_clickhouse_deployment_mode({"embedded": None}) == "local"


def test_resolve_empty_config_returns_default() -> None:
    assert resolve_clickhouse_deployment_mode({}) == "local"
    assert resolve_clickhouse_deployment_mode({}, default="server") == "server"


def test_cloud_error_message_mentions_dedicated_platform() -> None:
    message = clickhouse_cloud_mode_error_message()
    assert "clickhouse-cloud" in message
    assert "clickhouse:cloud" in message


class _RecordingMetadataAdapter(ClickHouseMetadataMixin):
    def __init__(self, **config):
        self.config = config
        self.deployment_mode = config["deployment_mode"]


def test_clickhouse_metadata_from_config_reads_legacy_mode_alias() -> None:
    adapter = _RecordingMetadataAdapter.from_config({"mode": "server"})

    assert adapter.deployment_mode == "server"
    assert adapter.config["deployment_mode"] == "server"
    assert "mode" not in adapter.config


def test_clickhouse_metadata_from_config_reads_legacy_embedded_alias() -> None:
    adapter = _RecordingMetadataAdapter.from_config({"mode": "embedded"})

    assert adapter.deployment_mode == "local"
    assert adapter.config["deployment_mode"] == "local"


def test_clickhouse_adapter_reads_legacy_mode_alias() -> None:
    from benchbox.platforms.clickhouse import ClickHouseAdapter

    with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
        adapter = ClickHouseAdapter(mode="server", host="localhost", port=9000)

    assert adapter.deployment_mode == "server"
    assert adapter.host == "localhost"
    assert adapter.port == 9000


def test_clickhouse_adapter_reads_embedded_alias_as_local() -> None:
    from benchbox.platforms.clickhouse import ClickHouseAdapter

    fake_chdb = types.ModuleType("chdb")
    fake_chdb.__spec__ = importlib.machinery.ModuleSpec("chdb", None)

    with patch.dict("sys.modules", {"chdb": fake_chdb}):
        adapter = ClickHouseAdapter(mode="embedded", data_path="/tmp/chdb")

    assert adapter.deployment_mode == "local"
    assert adapter.data_path == "/tmp/chdb"


def test_clickhouse_adapter_rejects_cloud_legacy_mode_alias() -> None:
    from benchbox.platforms.clickhouse import ClickHouseAdapter

    with pytest.raises(ValueError, match="clickhouse-cloud"):
        ClickHouseAdapter(mode="cloud")


def test_adapter_get_platform_info_emits_mode_compat_alias() -> None:

    from benchbox.platforms.clickhouse import ClickHouseAdapter

    fake_chdb = types.ModuleType("chdb")
    fake_chdb.__spec__ = importlib.machinery.ModuleSpec("chdb", None)

    with patch.dict("sys.modules", {"chdb": fake_chdb}):
        adapter = ClickHouseAdapter(deployment_mode="local", data_path="/tmp/chdb")

    info = adapter.get_platform_info(connection=None)

    assert info["configuration"]["deployment_mode"] == "local"
    assert info["configuration"]["mode"] == "local"
    assert info["connection_mode"] == "local"


def test_adapter_get_platform_info_server_emits_mode_compat_alias() -> None:

    from benchbox.platforms.clickhouse import ClickHouseAdapter

    with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
        adapter = ClickHouseAdapter(deployment_mode="server", host="localhost", port=9000)

    info = adapter.get_platform_info(connection=None)

    assert info["configuration"]["deployment_mode"] == "server"
    assert info["configuration"]["mode"] == "server"
    assert info["connection_mode"] == "server"


def test_adapter_get_platform_info_resolves_to_metadata_mixin() -> None:

    from benchbox.platforms.clickhouse import ClickHouseAdapter
    from benchbox.platforms.clickhouse.metadata import ClickHouseMetadataMixin

    for cls in ClickHouseAdapter.__mro__:
        if "get_platform_info" in cls.__dict__:
            assert cls is ClickHouseMetadataMixin, (
                f"get_platform_info resolved to {cls.__name__}, expected ClickHouseMetadataMixin"
            )
            break


def test_cloud_adapter_get_platform_info_emits_cloud_deployment() -> None:

    from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

    fake_cc = types.ModuleType("clickhouse_connect")
    fake_cc.__spec__ = importlib.machinery.ModuleSpec("clickhouse_connect", None)

    with patch.dict("sys.modules", {"clickhouse_connect": fake_cc}):
        adapter = ClickHouseCloudAdapter(host="test.clickhouse.cloud", password="secret")

    info = adapter.get_platform_info(connection=None)

    assert info["configuration"]["deployment_mode"] == "cloud"
    assert info["configuration"]["mode"] == "cloud"
    assert info["connection_mode"] == "cloud"


def test_platform_hook_registry_default_matches_normalizer_default() -> None:
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

    spec = PlatformHookRegistry.list_option_specs("clickhouse")["deployment_mode"]

    assert spec.default in ("server", "local")
