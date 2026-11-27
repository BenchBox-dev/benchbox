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


def test_normalize_clickhouse_deployment_mode_rejects_base_cloud_mode() -> None:
    with pytest.raises(ValueError, match="clickhouse-cloud"):
        normalize_clickhouse_deployment_mode("cloud")


def test_normalize_clickhouse_deployment_mode_allows_cloud_for_cloud_subclass() -> None:
    assert normalize_clickhouse_deployment_mode("cloud", allow_cloud=True) == "cloud"


def test_normalize_clickhouse_deployment_mode_is_case_and_whitespace_insensitive() -> None:
    assert normalize_clickhouse_deployment_mode("SERVER") == "server"
    assert normalize_clickhouse_deployment_mode(" Local ") == "local"


def test_normalize_clickhouse_deployment_mode_returns_default_for_empty_string() -> None:
    assert normalize_clickhouse_deployment_mode("") == "local"
    assert normalize_clickhouse_deployment_mode("   ") == "local"


def test_normalize_clickhouse_deployment_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="Invalid ClickHouse deployment mode 'invalid'"):
        normalize_clickhouse_deployment_mode("invalid")


def test_resolve_clickhouse_deployment_mode_reads_selector_only() -> None:
    assert resolve_clickhouse_deployment_mode({"_deployment_selector": "server"}) == "server"
    assert (
        resolve_clickhouse_deployment_mode({"deployment_mode": "server", "mode": "server", "embedded": False})
        == "local"
    )


def test_normalize_clickhouse_deployment_mode_rejects_embedded_value() -> None:
    with pytest.raises(ValueError, match="embedded"):
        normalize_clickhouse_deployment_mode("embedded")


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


def test_clickhouse_metadata_from_config_uses_selector() -> None:
    adapter = _RecordingMetadataAdapter.from_config({"_deployment_selector": "server"})

    assert adapter.config["_deployment_selector"] == "server"
    assert "deployment_mode" not in adapter.config
    assert "mode" not in adapter.config


def test_clickhouse_metadata_from_config_forwards_http_port() -> None:
    adapter = _RecordingMetadataAdapter.from_config({"_deployment_selector": "server", "http_port": 18123})

    assert adapter.config["http_port"] == 18123


@pytest.mark.parametrize(
    ("kwargs", "replacement"),
    [
        ({"deployment_mode": "server"}, "--platform clickhouse:"),
        ({"mode": "server"}, "--platform clickhouse:"),
        ({"embedded": True}, "--platform clickhouse:"),
    ],
)
def test_clickhouse_adapters_reject_removed_deployment_keywords(kwargs, replacement) -> None:
    from benchbox.platforms.clickhouse import ClickHouseAdapter
    from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter
    from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter
    from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

    for adapter_class in (ClickHouseAdapter, ClickHouseLocalAdapter, ClickHouseServerAdapter, ClickHouseCloudAdapter):
        with pytest.raises(TypeError, match=replacement):
            adapter_class(**kwargs)


def test_clickhouse_cloud_adapter_rejects_removed_service_id() -> None:
    from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

    with pytest.raises(TypeError, match="compute_resource"):
        ClickHouseCloudAdapter(service_id="svc")


def test_clickhouse_parse_options_rejects_removed_deployment_keys() -> None:
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry, PlatformOptionError

    for key in ("deployment_mode", "mode", "embedded"):
        with pytest.raises(PlatformOptionError, match="--platform clickhouse"):
            PlatformHookRegistry.parse_options("clickhouse", [(key, "server")])


def test_clickhouse_adapter_reads_selector() -> None:
    from benchbox.platforms.clickhouse import ClickHouseAdapter

    with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
        adapter = ClickHouseAdapter(_deployment_selector="server", host="localhost", port=9000)

    assert adapter.deployment_mode == "server"
    assert adapter.host == "localhost"
    assert adapter.port == 9000


def test_clickhouse_adapter_rejects_cloud_selector() -> None:
    from benchbox.platforms.clickhouse import ClickHouseAdapter

    with pytest.raises(ValueError, match="clickhouse-cloud"):
        ClickHouseAdapter(_deployment_selector="cloud")


def test_adapter_get_platform_info_emits_mode_compat_alias() -> None:

    from benchbox.platforms.clickhouse import ClickHouseAdapter

    fake_chdb = types.ModuleType("chdb")
    fake_chdb.__spec__ = importlib.machinery.ModuleSpec("chdb", None)

    with patch.dict("sys.modules", {"chdb": fake_chdb}):
        adapter = ClickHouseAdapter(_deployment_selector="local", data_path="/tmp/chdb")

    info = adapter.get_platform_info(connection=None)

    assert info["configuration"]["deployment_mode"] == "local"
    assert info["configuration"]["mode"] == "local"
    assert info["connection_mode"] == "local"


def test_adapter_get_platform_info_server_emits_mode_compat_alias() -> None:

    from benchbox.platforms.clickhouse import ClickHouseAdapter

    with patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, [])):
        adapter = ClickHouseAdapter(_deployment_selector="server", host="localhost", port=9000)

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
