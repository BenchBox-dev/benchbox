from unittest.mock import patch

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.databricks import DatabricksAdapter
from benchbox.platforms.databricks.adapter import DATABRICKS_OAUTH_ENV_VARS

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]

GATEWAY_HOSTNAME = "workspace.espressocomputing.com"


@pytest.fixture(autouse=True)
def databricks_dependencies(monkeypatch):
    for name in (*DATABRICKS_OAUTH_ENV_VARS, "DATABRICKS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with (
        patch("benchbox.platforms.databricks.adapter.check_platform_dependencies", return_value=(True, [])),
        patch("benchbox.platforms.databricks.adapter.databricks_sql"),
    ):
        yield


def _config(**overrides):
    return {
        "server_hostname": GATEWAY_HOSTNAME,
        "http_path": "/sql/1.0/warehouses/test",
        "access_token": "mock-token",
        **overrides,
    }


def test_gateway_defaults_to_native():
    assert DatabricksAdapter(**_config()).gateway == "native"


@pytest.mark.parametrize("gateway", ["native", "espresso", "custom"])
def test_gateway_is_stored(gateway):
    assert DatabricksAdapter(**_config(gateway=gateway)).gateway == gateway


@pytest.mark.parametrize("gateway", ["espresso", "custom"])
def test_from_config_forwards_gateway_and_hostname_unchanged(gateway):
    adapter = DatabricksAdapter.from_config(_config(gateway=gateway))

    assert adapter.gateway == gateway
    assert adapter.server_hostname == GATEWAY_HOSTNAME
    assert adapter.http_path == "/sql/1.0/warehouses/test"
    assert adapter._get_connection_params()["server_hostname"] == GATEWAY_HOSTNAME


def test_from_config_without_gateway_is_native():
    assert DatabricksAdapter.from_config(_config()).gateway == "native"


def test_unknown_gateway_is_rejected():
    with pytest.raises(ConfigurationError, match="does not support gateway 'greybeam'"):
        DatabricksAdapter(**_config(gateway="greybeam"))


@pytest.mark.parametrize("env_var", DATABRICKS_OAUTH_ENV_VARS)
@pytest.mark.parametrize("gateway", ["espresso", "custom"])
def test_oauth_environment_with_gateway_is_rejected(monkeypatch, env_var, gateway):
    monkeypatch.setenv(env_var, "mock-value")

    with pytest.raises(ConfigurationError, match=f"personal access token.*{env_var}"):
        DatabricksAdapter(**_config(gateway=gateway))


def test_oauth_environment_with_gateway_is_rejected_from_config(monkeypatch):
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "mock-client")

    with pytest.raises(ConfigurationError, match="DATABRICKS_CLIENT_ID"):
        DatabricksAdapter.from_config(_config(gateway="espresso"))


@pytest.mark.parametrize("env_var", DATABRICKS_OAUTH_ENV_VARS)
def test_oauth_environment_with_native_is_allowed(monkeypatch, env_var):
    monkeypatch.setenv(env_var, "mock-value")

    assert DatabricksAdapter(**_config(gateway="native")).gateway == "native"
    assert DatabricksAdapter(**_config()).gateway == "native"


def _platform_info(adapter):
    with patch.object(DatabricksAdapter, "_warehouse_id_from_http_path", return_value=None):
        return adapter.get_platform_info()


def _payload(adapter):
    from benchbox.core.results.models import QueryExecution
    from benchbox.core.results.schema import build_result_payload
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    result = make_benchmark_results(
        platform="databricks",
        platform_info=_platform_info(adapter),
        query_results=[QueryExecution(query_id="1", execution_time_ms=10, rows_returned=1)],
    )
    result.tpc_compliant = True
    return build_result_payload(result)


@pytest.mark.parametrize("gateway", ["espresso", "custom"])
def test_routed_run_is_recorded_with_gateway_name_and_variant(gateway):
    adapter = DatabricksAdapter(**_config(gateway=gateway))

    assert _platform_info(adapter)["gateway"] == {"name": gateway, "routed": True}
    payload = _payload(adapter)
    assert payload["platform"]["gateway"] == {"name": gateway, "routed": True}
    assert payload["platform"]["variant"].endswith(f"@{gateway}")
    assert payload["tpc_compliant"] is False


def test_native_run_emits_no_gateway():
    adapter = DatabricksAdapter(**_config())

    assert "gateway" not in _platform_info(adapter)
    payload = _payload(adapter)
    assert "gateway" not in payload["platform"]
    assert "@" not in payload["platform"]["variant"]


@pytest.mark.parametrize("gateway", ["espresso", "custom"])
@pytest.mark.parametrize("missing", ["server_hostname", "access_token"])
def test_from_config_with_gateway_requires_explicit_credentials(gateway, missing):
    config = _config(gateway=gateway)
    del config[missing]

    with (
        patch.object(DatabricksAdapter, "_auto_detect_databricks_config") as auto_detect,
        pytest.raises(ConfigurationError, match=f"explicit {missing}"),
    ):
        DatabricksAdapter.from_config(config)

    auto_detect.assert_not_called()


def test_from_config_with_gateway_rejects_placeholder_hostname():
    config = _config(gateway="espresso", server_hostname="your-workspace.cloud.databricks.com")

    with (
        patch.object(DatabricksAdapter, "_auto_detect_databricks_config") as auto_detect,
        pytest.raises(ConfigurationError, match="explicit server_hostname"),
    ):
        DatabricksAdapter.from_config(config)

    auto_detect.assert_not_called()


def test_from_config_with_gateway_uses_explicit_credentials_without_auto_detect():
    with patch.object(DatabricksAdapter, "_auto_detect_databricks_config") as auto_detect:
        adapter = DatabricksAdapter.from_config(_config(gateway="espresso"))

    auto_detect.assert_not_called()
    assert adapter.server_hostname == GATEWAY_HOSTNAME
    assert adapter.access_token == "mock-token"


def test_from_config_native_still_auto_detects_missing_credentials():
    detected = {
        "server_hostname": "native.cloud.databricks.com",
        "http_path": "/sql/1.0/warehouses/native",
        "access_token": "mock-native-token",
    }
    with patch.object(DatabricksAdapter, "_auto_detect_databricks_config", return_value=detected) as auto_detect:
        adapter = DatabricksAdapter.from_config({"catalog": "main"})

    auto_detect.assert_called_once()
    assert adapter.gateway == "native"
    assert adapter.server_hostname == "native.cloud.databricks.com"
