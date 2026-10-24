# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.databricks.credentials import (
    _auto_detect_databricks,
    _prompt_default_output_location,
    setup_databricks_credentials,
    validate_databricks_credentials,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

MODULE = "benchbox.platforms.databricks.credentials"


def _cred_manager(existing: dict | None = None) -> MagicMock:
    m = MagicMock()
    m.get_platform_credentials.return_value = existing
    m.credentials_path = "/tmp/fake-creds.json"
    return m


def _console() -> MagicMock:
    return MagicMock()


def _mock_sql_modules(connect_return=None, connect_side_effect=None):

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [(1,)]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_sql = MagicMock()
    if connect_side_effect is not None:
        mock_sql.connect.side_effect = connect_side_effect
    else:
        mock_sql.connect.return_value = connect_return or mock_conn
    mock_db_pkg = MagicMock()
    mock_db_pkg.sql = mock_sql
    return mock_db_pkg, mock_sql, mock_conn, mock_cursor


class TestValidateDatabricksCredentials:
    def test_no_credentials_returns_false(self):
        mgr = _cred_manager(None)
        ok, err = validate_databricks_credentials(mgr)
        assert ok is False
        assert err == "No credentials found"

    def test_missing_required_fields(self):
        mgr = _cred_manager({"server_hostname": "host"})
        ok, err = validate_databricks_credentials(mgr)
        assert ok is False
        assert "Missing required fields" in err
        assert "http_path" in err
        assert "access_token" in err

    def test_sql_connector_not_installed(self):
        mgr = _cred_manager({"server_hostname": "host", "http_path": "/sql/1.0/w/x", "access_token": "tok"})
        with patch.dict("sys.modules", {"databricks": None, "databricks.sql": None}):
            ok, err = validate_databricks_credentials(mgr)
        assert ok is False
        assert "not installed" in err

    def test_successful_connection(self):
        mgr = _cred_manager({"server_hostname": "host", "http_path": "/sql/1.0/w/x", "access_token": "tok"})
        mock_db_pkg, mock_sql, _conn, _cursor = _mock_sql_modules()
        with patch.dict("sys.modules", {"databricks": mock_db_pkg, "databricks.sql": mock_sql}):
            ok, err = validate_databricks_credentials(mgr)
        assert ok is True
        assert err is None

    def test_verifies_against_certifi_bundle_by_default(self, monkeypatch):
        import certifi

        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
        mgr = _cred_manager({"server_hostname": "host", "http_path": "/sql/1.0/w/x", "access_token": "tok"})
        mock_db_pkg, mock_sql, _conn, _cursor = _mock_sql_modules()
        with patch.dict("sys.modules", {"databricks": mock_db_pkg, "databricks.sql": mock_sql}):
            validate_databricks_credentials(mgr)
        assert mock_sql.connect.call_args.kwargs["_tls_trusted_ca_file"] == certifi.where()

    def test_successful_connection_with_catalog(self):
        mgr = _cred_manager(
            {
                "server_hostname": "host",
                "http_path": "/sql/1.0/w/x",
                "access_token": "tok",
                "catalog": "mycat",
            }
        )
        mock_db_pkg, mock_sql, _conn, mock_cursor = _mock_sql_modules()
        with patch.dict("sys.modules", {"databricks": mock_db_pkg, "databricks.sql": mock_sql}):
            ok, err = validate_databricks_credentials(mgr)
        assert ok is True
        calls = [str(c) for c in mock_cursor.execute.call_args_list]
        assert any("mycat" in c for c in calls)

    def test_authentication_error(self):
        mgr = _cred_manager({"server_hostname": "host", "http_path": "/sql/1.0/w/x", "access_token": "bad"})
        mock_db_pkg, mock_sql, _conn, _cursor = _mock_sql_modules(
            connect_side_effect=Exception("authentication failed: invalid token")
        )
        with patch.dict("sys.modules", {"databricks": mock_db_pkg, "databricks.sql": mock_sql}):
            ok, err = validate_databricks_credentials(mgr)
        assert ok is False
        assert "Authentication failed" in err

    def test_warehouse_error(self):
        mgr = _cred_manager({"server_hostname": "host", "http_path": "/bad/warehouse/path", "access_token": "tok"})
        mock_db_pkg, mock_sql, _conn, _cursor = _mock_sql_modules(
            connect_side_effect=Exception("warehouse not found: check http_path")
        )
        with patch.dict("sys.modules", {"databricks": mock_db_pkg, "databricks.sql": mock_sql}):
            ok, err = validate_databricks_credentials(mgr)
        assert ok is False
        assert "warehouse" in err.lower()

    def test_catalog_error(self):
        mgr = _cred_manager(
            {
                "server_hostname": "host",
                "http_path": "/sql/1.0/w/x",
                "access_token": "tok",
                "catalog": "missing_cat",
            }
        )
        mock_db_pkg, mock_sql, mock_conn, mock_cursor = _mock_sql_modules()
        mock_cursor.execute.side_effect = [None, Exception("catalog 'missing_cat' not found")]
        with patch.dict("sys.modules", {"databricks": mock_db_pkg, "databricks.sql": mock_sql}):
            ok, err = validate_databricks_credentials(mgr)
        assert ok is False
        assert "missing_cat" in err

    def test_generic_connection_error(self):
        mgr = _cred_manager({"server_hostname": "host", "http_path": "/sql/1.0/w/x", "access_token": "tok"})
        mock_db_pkg, mock_sql, _conn, _cursor = _mock_sql_modules(connect_side_effect=Exception("network timeout"))
        with patch.dict("sys.modules", {"databricks": mock_db_pkg, "databricks.sql": mock_sql}):
            ok, err = validate_databricks_credentials(mgr)
        assert ok is False
        assert "Connection failed" in err
        assert "network timeout" in err


class TestAutoDetectDatabricks:
    def test_sdk_not_installed_returns_none(self):
        console = _console()
        with patch.dict("sys.modules", {"databricks.sdk": None}):
            result = _auto_detect_databricks(console)
        assert result is None
        console.print.assert_called()

    def test_sdk_exception_returns_none(self):
        console = _console()
        mock_sdk = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.config.host = "https://myhost.cloud.databricks.com"
        mock_workspace.config.token = "mytoken"
        mock_workspace.api_client = MagicMock()
        mock_sdk.WorkspaceClient.return_value = mock_workspace

        mock_warehouses_api = MagicMock()
        mock_warehouses_api.list.side_effect = Exception("SDK connection error")
        mock_sdk.service = MagicMock()
        mock_sdk.service.sql = MagicMock()
        mock_sdk.service.sql.WarehousesAPI.return_value = mock_warehouses_api

        with patch.dict(
            "sys.modules",
            {
                "databricks.sdk": mock_sdk,
                "databricks.sdk.service": mock_sdk.service,
                "databricks.sdk.service.sql": mock_sdk.service.sql,
            },
        ):
            result = _auto_detect_databricks(console)

        assert result is None

    def test_sdk_no_warehouses(self):
        console = _console()
        mock_workspace_client = MagicMock()
        mock_workspace_client.config.host = "https://myhost.cloud.databricks.com"
        mock_workspace_client.config.token = "mytoken"
        mock_workspace_client.api_client = MagicMock()

        mock_warehouses_api = MagicMock()
        mock_warehouses_api.list.return_value = []

        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.return_value = mock_workspace_client
        mock_sdk_sql = MagicMock()
        mock_sdk_sql.WarehousesAPI.return_value = mock_warehouses_api

        with (
            patch.dict(
                "sys.modules",
                {
                    "databricks.sdk": mock_sdk,
                    "databricks.sdk.service": MagicMock(),
                    "databricks.sdk.service.sql": mock_sdk_sql,
                },
            ),
            patch(f"{MODULE}.WorkspaceClient", mock_sdk.WorkspaceClient, create=True),
            patch(f"{MODULE}.WarehousesAPI", mock_sdk_sql.WarehousesAPI, create=True),
        ):
            result = _auto_detect_databricks(console)

        if result is not None:
            assert result.get("http_path") is None
            assert result.get("server_hostname") == "myhost.cloud.databricks.com"

    def test_sdk_with_running_warehouses(self):
        console = _console()

        wh1 = MagicMock()
        wh1.state = "RUNNING"
        wh1.name = "My Warehouse"
        wh1.id = "abc123"
        wh1.cluster_size = "Small"

        mock_workspace_client = MagicMock()
        mock_workspace_client.config.host = "https://workspace.cloud.databricks.com"
        mock_workspace_client.config.token = "tok"
        mock_workspace_client.api_client = MagicMock()

        mock_warehouses_api = MagicMock()
        mock_warehouses_api.list.return_value = [wh1]

        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient.return_value = mock_workspace_client
        mock_sdk_sql = MagicMock()
        mock_sdk_sql.WarehousesAPI.return_value = mock_warehouses_api

        with (
            patch.dict(
                "sys.modules",
                {
                    "databricks.sdk": mock_sdk,
                    "databricks.sdk.service": MagicMock(),
                    "databricks.sdk.service.sql": mock_sdk_sql,
                },
            ),
            patch(f"{MODULE}.WorkspaceClient", mock_sdk.WorkspaceClient, create=True),
            patch(f"{MODULE}.WarehousesAPI", mock_sdk_sql.WarehousesAPI, create=True),
            patch(f"{MODULE}.IntPrompt") as mock_int_prompt,
        ):
            mock_int_prompt.ask.return_value = 1
            result = _auto_detect_databricks(console)

        if result is not None:
            assert result.get("server_hostname") == "workspace.cloud.databricks.com"
            assert result.get("http_path") == "/sql/1.0/warehouses/abc123"


class TestSetupDatabricksCredentials:
    def test_existing_creds_skips_auto_detect_success_path(self):
        existing = {
            "server_hostname": "existing.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/existing",
            "access_token": "existingtoken",
            "catalog": "mycat",
            "schema": "myschema",
        }
        mgr = _cred_manager(existing)
        console = _console()

        with (
            patch(f"{MODULE}.prompt_with_default") as mock_pwd,
            patch(f"{MODULE}.prompt_secure_field") as mock_psf,
            patch(f"{MODULE}.Confirm"),
            patch(f"{MODULE}.validate_databricks_credentials") as mock_validate,
            patch(f"{MODULE}._prompt_default_output_location"),
        ):
            mock_pwd.side_effect = [
                "existing.cloud.databricks.com",
                "/sql/1.0/warehouses/existing",
                "mycat",
                "myschema",
            ]
            mock_psf.return_value = "existingtoken"
            mock_validate.return_value = (True, None)

            setup_databricks_credentials(mgr, console)

        mock_validate.assert_called_once()
        mgr.update_validation_status.assert_called()
        mgr.save_credentials.assert_called()

    def test_missing_server_hostname_returns_early(self):
        mgr = _cred_manager(None)
        console = _console()

        with (
            patch(f"{MODULE}.Confirm") as mock_confirm,
            patch(f"{MODULE}.prompt_with_default") as mock_pwd,
            patch(f"{MODULE}._auto_detect_databricks") as mock_auto,
        ):
            mock_confirm.ask.return_value = False
            mock_auto.return_value = None
            mock_pwd.return_value = ""

            setup_databricks_credentials(mgr, console)

        mgr.set_platform_credentials.assert_not_called()

    def test_missing_http_path_returns_early(self):
        mgr = _cred_manager(None)
        console = _console()

        with (
            patch(f"{MODULE}.Confirm") as mock_confirm,
            patch(f"{MODULE}.prompt_with_default") as mock_pwd,
            patch(f"{MODULE}._auto_detect_databricks") as mock_auto,
        ):
            mock_confirm.ask.return_value = False
            mock_auto.return_value = None
            mock_pwd.side_effect = ["my.workspace.com", ""]

            setup_databricks_credentials(mgr, console)

        mgr.set_platform_credentials.assert_not_called()

    def test_missing_access_token_returns_early(self):
        mgr = _cred_manager(None)
        console = _console()

        with (
            patch(f"{MODULE}.Confirm") as mock_confirm,
            patch(f"{MODULE}.prompt_with_default") as mock_pwd,
            patch(f"{MODULE}.prompt_secure_field") as mock_psf,
            patch(f"{MODULE}._auto_detect_databricks") as mock_auto,
        ):
            mock_confirm.ask.return_value = False
            mock_auto.return_value = None
            mock_pwd.side_effect = ["my.workspace.com", "/sql/1.0/warehouses/x", "main", "benchbox"]
            mock_psf.return_value = ""

            setup_databricks_credentials(mgr, console)

        mgr.set_platform_credentials.assert_not_called()

    def test_validation_failure_path(self):
        mgr = _cred_manager(None)
        console = _console()

        with (
            patch(f"{MODULE}.Confirm") as mock_confirm,
            patch(f"{MODULE}.prompt_with_default") as mock_pwd,
            patch(f"{MODULE}.prompt_secure_field") as mock_psf,
            patch(f"{MODULE}.validate_databricks_credentials") as mock_validate,
            patch(f"{MODULE}._auto_detect_databricks") as mock_auto,
        ):
            mock_confirm.ask.return_value = False
            mock_auto.return_value = None
            mock_pwd.side_effect = ["my.workspace.com", "/sql/1.0/warehouses/x", "main", "benchbox"]
            mock_psf.return_value = "badtoken"
            mock_validate.return_value = (False, "Connection failed: timeout")

            setup_databricks_credentials(mgr, console)

        mock_validate.assert_called_once()
        mgr.save_credentials.assert_called()

    def test_auto_detect_success_path(self):
        mgr = _cred_manager(None)
        console = _console()

        auto_result = {
            "server_hostname": "auto.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/auto123",
            "access_token": "auto_token",
        }

        with (
            patch(f"{MODULE}.Confirm") as mock_confirm,
            patch(f"{MODULE}.prompt_with_default") as mock_pwd,
            patch(f"{MODULE}.prompt_secure_field") as mock_psf,
            patch(f"{MODULE}.validate_databricks_credentials") as mock_validate,
            patch(f"{MODULE}._auto_detect_databricks") as mock_auto,
            patch(f"{MODULE}._prompt_default_output_location"),
        ):
            mock_confirm.ask.return_value = True
            mock_auto.return_value = auto_result
            mock_pwd.side_effect = ["main", "benchbox"]
            mock_psf.return_value = "auto_token"
            mock_validate.return_value = (True, None)

            setup_databricks_credentials(mgr, console)

        mock_validate.assert_called_once()
        set_call_args = mgr.set_platform_credentials.call_args
        assert set_call_args is not None
        creds = set_call_args[0][1]
        assert creds["server_hostname"] == "auto.cloud.databricks.com"

    def test_auto_detect_no_http_path_falls_back_to_prompt(self):
        mgr = _cred_manager(None)
        console = _console()

        auto_result = {
            "server_hostname": "auto.cloud.databricks.com",
            "http_path": None,
            "access_token": "tok",
        }

        with (
            patch(f"{MODULE}.Confirm") as mock_confirm,
            patch(f"{MODULE}.prompt_with_default") as mock_pwd,
            patch(f"{MODULE}.prompt_secure_field") as mock_psf,
            patch(f"{MODULE}.validate_databricks_credentials") as mock_validate,
            patch(f"{MODULE}._auto_detect_databricks") as mock_auto,
            patch(f"{MODULE}._prompt_default_output_location"),
        ):
            mock_confirm.ask.return_value = True
            mock_auto.return_value = auto_result
            mock_pwd.side_effect = ["main", "benchbox"]
            mock_psf.return_value = "tok"
            mock_validate.return_value = (True, None)

            setup_databricks_credentials(mgr, console)

        mock_validate.assert_called_once()


class TestPromptDefaultOutputLocation:
    def test_declines_output_location(self):
        mgr = _cred_manager(None)
        console = _console()
        creds = {"server_hostname": "h", "http_path": "/p", "access_token": "t"}

        with patch(f"{MODULE}.Confirm") as mock_confirm:
            mock_confirm.ask.return_value = False
            _prompt_default_output_location(mgr, console, creds)

        mgr.set_platform_credentials.assert_not_called()

    def test_valid_cloud_path_saved(self):
        mgr = _cred_manager(None)
        console = _console()
        creds = {
            "server_hostname": "h",
            "http_path": "/p",
            "access_token": "t",
            "catalog": "main",
            "schema": "bench",
        }

        with (
            patch(f"{MODULE}.Confirm") as mock_confirm,
            patch(f"{MODULE}.Prompt") as mock_prompt,
            patch("benchbox.utils.cloud_storage.is_cloud_path", return_value=True),
        ):
            mock_confirm.ask.side_effect = [True, True]
            mock_prompt.ask.return_value = "s3://my-bucket/benchbox"

            _prompt_default_output_location(mgr, console, creds)

        mgr.set_platform_credentials.assert_called_once()
        mgr.save_credentials.assert_called_once()
        assert creds["default_output_location"] == "s3://my-bucket/benchbox"

    def test_invalid_cloud_path_with_proceed(self):
        mgr = _cred_manager(None)
        console = _console()
        creds = {"server_hostname": "h", "http_path": "/p", "access_token": "t"}

        with (
            patch(f"{MODULE}.Confirm") as mock_confirm,
            patch(f"{MODULE}.Prompt") as mock_prompt,
            patch("benchbox.utils.cloud_storage.is_cloud_path", return_value=False),
        ):
            mock_confirm.ask.side_effect = [True, True, True]
            mock_prompt.ask.return_value = "/local/path"

            _prompt_default_output_location(mgr, console, creds)

        mgr.set_platform_credentials.assert_called_once()

    def test_invalid_cloud_path_retry_then_empty(self):
        mgr = _cred_manager(None)
        console = _console()
        creds = {"server_hostname": "h", "http_path": "/p", "access_token": "t"}

        with (
            patch(f"{MODULE}.Confirm") as mock_confirm,
            patch(f"{MODULE}.Prompt") as mock_prompt,
            patch("benchbox.utils.cloud_storage.is_cloud_path", return_value=False),
        ):
            mock_confirm.ask.side_effect = [True, False]
            mock_prompt.ask.side_effect = ["/bad/path", ""]

            _prompt_default_output_location(mgr, console, creds)

        mgr.set_platform_credentials.assert_not_called()

    def test_empty_path_skips(self):
        mgr = _cred_manager(None)
        console = _console()
        creds = {"server_hostname": "h", "http_path": "/p", "access_token": "t"}

        with (
            patch(f"{MODULE}.Confirm") as mock_confirm,
            patch(f"{MODULE}.Prompt") as mock_prompt,
            patch("benchbox.utils.cloud_storage.is_cloud_path", return_value=True),
        ):
            mock_confirm.ask.return_value = True
            mock_prompt.ask.return_value = ""

            _prompt_default_output_location(mgr, console, creds)

        mgr.set_platform_credentials.assert_not_called()

    def test_path_not_confirmed_then_confirmed_on_retry(self):
        mgr = _cred_manager(None)
        console = _console()
        creds = {"server_hostname": "h", "http_path": "/p", "access_token": "t"}

        with (
            patch(f"{MODULE}.Confirm") as mock_confirm,
            patch(f"{MODULE}.Prompt") as mock_prompt,
            patch("benchbox.utils.cloud_storage.is_cloud_path", return_value=True),
        ):
            mock_confirm.ask.side_effect = [True, False, True]
            mock_prompt.ask.side_effect = ["s3://bucket/path", "s3://other/path"]

            _prompt_default_output_location(mgr, console, creds)

        mgr.set_platform_credentials.assert_called_once()
        assert creds["default_output_location"] == "s3://other/path"
