from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.core.gateway import INSECURE_GATEWAY_ENV, GatewayConfigurationError, UnsupportedGatewayError
from benchbox.platforms.snowflake import SnowflakeAdapter

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.cloud_import]

BASE = {"account": "acct", "username": "user", "password": "mock-password", "warehouse": "WH", "database": "DB"}


@pytest.fixture(autouse=True)
def snowflake_dependencies(monkeypatch):
    monkeypatch.delenv(INSECURE_GATEWAY_ENV, raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    with (
        patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])),
        patch("benchbox.platforms.snowflake.snowflake") as mock_snowflake,
    ):
        yield mock_snowflake


def _adapter(**extra) -> SnowflakeAdapter:
    return SnowflakeAdapter(**BASE, **extra)


def test_native_adapter_has_no_gateway_endpoint() -> None:
    adapter = _adapter()
    assert adapter.gateway_endpoint is None
    params = adapter._connect_params({}, include_database=True)
    assert not {"host", "port", "protocol", "insecure_mode", "ocsp_fail_open"} & set(params)
    assert "gateway" not in adapter.get_platform_info()


def test_explicit_native_gateway_is_native() -> None:
    assert _adapter(gateway="native").gateway_endpoint is None


@pytest.mark.parametrize("gateway", ["espresso", "greybeam", "custom"])
def test_gateway_requires_host(gateway: str) -> None:
    with pytest.raises(GatewayConfigurationError, match="requires gateway_host"):
        _adapter(gateway=gateway)


def test_unknown_gateway_is_rejected_with_allowed_values() -> None:
    with pytest.raises(UnsupportedGatewayError, match="Allowed: native, custom, espresso, greybeam"):
        _adapter(gateway="bogus", gateway_host="gw.example.com")


@pytest.mark.parametrize(
    "option",
    [
        {"gateway_host": "gw.example.com"},
        {"gateway_port": 8443},
        {"gateway_protocol": "http"},
        {"gateway_ca_bundle": "/tmp/ca.pem"},
        {"gateway_ocsp_fail_open": True},
        {"allow_insecure_gateway": True},
    ],
)
def test_gateway_options_without_gateway_are_rejected(option: dict) -> None:
    with pytest.raises(GatewayConfigurationError, match="require a gateway other than native"):
        _adapter(**option)


def test_default_https_protocol_without_gateway_is_accepted() -> None:
    assert _adapter(gateway_protocol="https").gateway_endpoint is None


def test_gateway_maps_connection_params_for_benchmark_and_admin_paths(snowflake_dependencies) -> None:
    connect = snowflake_dependencies.connector.connect
    connect.return_value = Mock()
    adapter = _adapter(gateway="espresso", gateway_host="gw.example.com", gateway_port=8443)

    adapter.create_connection()
    benchmark_kwargs = connect.call_args[1]
    connect.reset_mock()
    adapter._create_admin_connection()
    admin_kwargs = connect.call_args[1]

    for kwargs in (benchmark_kwargs, admin_kwargs):
        assert kwargs["host"] == "gw.example.com"
        assert kwargs["port"] == 8443
        assert kwargs["protocol"] == "https"
        assert kwargs["account"] == "acct"
        assert "ocsp_fail_open" not in kwargs
        assert "insecure_mode" not in kwargs
    assert "database" in benchmark_kwargs
    assert "database" not in admin_kwargs


def test_port_omitted_when_not_configured() -> None:
    params = _adapter(gateway="custom", gateway_host="gw.example.com")._connect_params({}, include_database=False)
    assert "port" not in params
    assert params["host"] == "gw.example.com"


@pytest.mark.parametrize("value", [True, False])
def test_ocsp_fail_open_forwarded_only_when_set(value: bool) -> None:
    adapter = _adapter(gateway="custom", gateway_host="gw.example.com", gateway_ocsp_fail_open=value)
    assert adapter._connect_params({}, include_database=False)["ocsp_fail_open"] is value


@pytest.mark.parametrize(
    "host", ["alice:secret@gw.example.com", "https://alice:secret@gw.example.com/path", " gw.example.com "]
)
def test_host_userinfo_is_stripped(host: str) -> None:
    adapter = _adapter(gateway="custom", gateway_host=host)
    assert adapter.gateway_endpoint is not None
    assert adapter.gateway_endpoint.host == "gw.example.com"
    assert "secret" not in repr(adapter.gateway_endpoint)


def test_missing_ca_bundle_file_raises(tmp_path: Path) -> None:
    with pytest.raises(GatewayConfigurationError, match="gateway_ca_bundle"):
        _adapter(gateway="custom", gateway_host="gw.example.com", gateway_ca_bundle=str(tmp_path / "absent.pem"))


def test_ca_bundle_is_scoped_to_the_connection(tmp_path: Path, snowflake_dependencies, monkeypatch) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("mock-ca", encoding="utf-8")
    seen: list[str | None] = []
    snowflake_dependencies.connector.connect.side_effect = lambda **_: seen.append(os.environ.get("REQUESTS_CA_BUNDLE"))
    adapter = _adapter(gateway="custom", gateway_host="gw.example.com", gateway_ca_bundle=str(bundle))

    adapter._create_admin_connection()
    assert seen == [str(bundle)]
    assert "REQUESTS_CA_BUNDLE" not in os.environ

    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/preexisting.pem")
    adapter._create_admin_connection()
    assert seen[-1] == str(bundle)
    assert os.environ["REQUESTS_CA_BUNDLE"] == "/preexisting.pem"


def test_ca_bundle_restored_when_connect_fails(tmp_path: Path, snowflake_dependencies) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("mock-ca", encoding="utf-8")
    snowflake_dependencies.connector.connect.side_effect = RuntimeError("boom")
    adapter = _adapter(gateway="custom", gateway_host="gw.example.com", gateway_ca_bundle=str(bundle))

    with pytest.raises(RuntimeError):
        adapter._create_admin_connection()
    assert "REQUESTS_CA_BUNDLE" not in os.environ


def test_insecure_gateway_requires_environment_opt_in() -> None:
    with pytest.raises(GatewayConfigurationError, match=INSECURE_GATEWAY_ENV):
        _adapter(gateway="custom", gateway_host="gw.example.com", allow_insecure_gateway=True)


def test_insecure_gateway_maps_insecure_mode_and_warns(monkeypatch, caplog) -> None:
    monkeypatch.setenv(INSECURE_GATEWAY_ENV, "1")
    with caplog.at_level("WARNING", logger="benchbox.core.gateway"):
        adapter = _adapter(gateway="custom", gateway_host="gw.example.com", allow_insecure_gateway=True)
    assert adapter._connect_params({}, include_database=False)["insecure_mode"] is True
    assert any("without TLS verification" in record.message for record in caplog.records)


def test_plain_http_to_remote_host_requires_environment_opt_in(monkeypatch, caplog) -> None:
    with pytest.raises(GatewayConfigurationError, match=INSECURE_GATEWAY_ENV):
        _adapter(gateway="custom", gateway_host="gw.example.com", gateway_protocol="http")

    monkeypatch.setenv(INSECURE_GATEWAY_ENV, "1")
    with caplog.at_level("WARNING", logger="benchbox.core.gateway"):
        adapter = _adapter(gateway="custom", gateway_host="gw.example.com", gateway_protocol="http")
    params = adapter._connect_params({}, include_database=False)
    assert params["protocol"] == "http"
    assert "insecure_mode" not in params
    assert caplog.records


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_plain_http_to_loopback_needs_no_opt_in(host: str) -> None:
    adapter = _adapter(gateway="custom", gateway_host=host, gateway_protocol="http")
    assert adapter._connect_params({}, include_database=False)["protocol"] == "http"


def test_from_config_forwards_gateway_fields() -> None:
    adapter = SnowflakeAdapter.from_config(
        {
            **BASE,
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "gateway": "greybeam",
            "gateway_host": "gw.example.com",
            "gateway_port": 443,
            "gateway_protocol": "https",
            "gateway_ocsp_fail_open": False,
        }
    )
    endpoint = adapter.gateway_endpoint
    assert endpoint is not None
    assert (endpoint.name, endpoint.host, endpoint.port, endpoint.ocsp_fail_open) == (
        "greybeam",
        "gw.example.com",
        443,
        False,
    )


def test_from_config_native_default_has_no_endpoint() -> None:
    adapter = SnowflakeAdapter.from_config({**BASE, "benchmark": "tpch", "scale_factor": 0.01, "gateway": None})
    assert adapter.gateway_endpoint is None


def test_routed_platform_info_records_gateway() -> None:
    info = _adapter(gateway="greybeam", gateway_host="gw.example.com").get_platform_info()
    assert info["gateway"] == {"name": "greybeam", "routed": True}
    assert "gw.example.com" not in repr(info)


def test_connector_accepts_every_gateway_parameter(monkeypatch) -> None:
    connection = pytest.importorskip("snowflake.connector.connection")
    monkeypatch.setenv(INSECURE_GATEWAY_ENV, "1")
    adapter = _adapter(
        gateway="custom",
        gateway_host="gw.example.com",
        gateway_port=8443,
        gateway_ocsp_fail_open=False,
        allow_insecure_gateway=True,
    )
    params = adapter._connect_params({}, include_database=True)
    assert set(params) <= set(connection.DEFAULT_CONFIGURATION)


def test_explicit_gateway_options_reach_the_adapter_config() -> None:
    from benchbox.core.platform_config import get_platform_config
    from benchbox.core.schemas import DatabaseConfig

    database_config = DatabaseConfig(
        type="snowflake",
        name="Snowflake",
        gateway="espresso",
        options={"_explicit_platform_options": {"gateway_host": "gw.example.com", "gateway_port": 8443}},
    )
    platform_config = get_platform_config(database_config, None)
    assert platform_config["gateway"] == "espresso"
    assert platform_config["gateway_host"] == "gw.example.com"
    assert platform_config["gateway_port"] == 8443
    adapter = SnowflakeAdapter(**BASE, **{k: v for k, v in platform_config.items() if k.startswith("gateway")})
    assert adapter.gateway_endpoint is not None
    assert adapter.gateway_endpoint.port == 8443
