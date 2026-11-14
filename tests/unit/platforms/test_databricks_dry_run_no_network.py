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


@pytest.fixture(autouse=True)
def databricks_dependencies(monkeypatch):
    for name in (*DATABRICKS_OAUTH_ENV_VARS, "DATABRICKS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with (
        patch("benchbox.platforms.databricks.adapter.check_platform_dependencies", return_value=(True, [])),
        patch("benchbox.platforms.databricks.adapter.databricks_sql"),
    ):
        yield


def _incomplete_config(**overrides):
    return {"catalog": "main", **overrides}


def test_dry_run_from_config_makes_no_workspace_call():
    with (
        patch.object(DatabricksAdapter, "_auto_detect_databricks_config") as auto_detect,
        pytest.raises(ConfigurationError, match="configuration is incomplete"),
    ):
        DatabricksAdapter.from_config(_incomplete_config(dry_run=True))

    auto_detect.assert_not_called()


def test_non_dry_run_from_config_still_auto_detects_missing_credentials():
    auto_config = {
        "server_hostname": "detected.cloud.databricks.com",
        "http_path": "/sql/1.0/warehouses/abc",
        "access_token": "tok",
    }
    with patch.object(DatabricksAdapter, "_auto_detect_databricks_config", return_value=auto_config) as auto_detect:
        adapter = DatabricksAdapter.from_config(_incomplete_config())

    auto_detect.assert_called_once()
    assert adapter.server_hostname == "detected.cloud.databricks.com"
