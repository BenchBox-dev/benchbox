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
