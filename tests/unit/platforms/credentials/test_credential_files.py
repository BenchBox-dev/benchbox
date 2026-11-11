from __future__ import annotations

import json
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from benchbox.security.credentials import CredentialManager, CredentialStatus

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def cred_file(tmp_path):

    def _make(data: dict) -> CredentialManager:
        path = tmp_path / "credentials.yaml"
        path.write_text(yaml.dump(data, default_flow_style=False))
        return CredentialManager(credentials_path=path)

    return _make


def _make_module(name: str, **attrs) -> ModuleType:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


class TestCredentialManagerLoading:
    def test_load_from_yaml_file(self, cred_file):
        mgr = cred_file({"snowflake": {"account": "org-acct", "username": "usr"}})
        creds = mgr.get_platform_credentials("snowflake")
        assert creds["account"] == "org-acct"
        assert creds["username"] == "usr"

    def test_missing_file_returns_empty(self, tmp_path):
        mgr = CredentialManager(credentials_path=tmp_path / "nonexistent.yaml")
        assert mgr.get_platform_credentials("snowflake") is None
        assert mgr.has_credentials("snowflake") is False

    def test_malformed_yaml_raises_error(self, tmp_path):
        bad = tmp_path / "credentials.yaml"
        bad.write_text(": :\n  - [invalid yaml{{{")
        with pytest.raises(ValueError, match="Failed to load"):
            CredentialManager(credentials_path=bad)

    def test_env_var_substitution(self, cred_file, monkeypatch):
        monkeypatch.setenv("BENCH_ACCOUNT", "my-account")
        monkeypatch.setenv("BENCH_TOKEN", "secret-token-123")
        mgr = cred_file(
            {
                "databricks": {
                    "server_hostname": "${BENCH_ACCOUNT}.cloud.databricks.com",
                    "access_token": "$BENCH_TOKEN",
                }
            }
        )
        creds = mgr.get_platform_credentials("databricks")
        assert creds["server_hostname"] == "my-account.cloud.databricks.com"
        assert creds["access_token"] == "secret-token-123"

    def test_unset_env_var_kept_as_literal(self, cred_file):
        mgr = cred_file({"test": {"key": "${NONEXISTENT_VAR_XYZ}"}})
        creds = mgr.get_platform_credentials("test")
        assert creds["key"] == "${NONEXISTENT_VAR_XYZ}"

    def test_case_insensitive_platform_lookup(self, cred_file):
        mgr = cred_file({"snowflake": {"account": "a"}})

        assert mgr.get_platform_credentials("Snowflake") is not None
        assert mgr.get_platform_credentials("SNOWFLAKE") is not None
        assert mgr.get_platform_credentials("snowflake") is not None


class TestCredentialManagerSaveLoad:
    def test_save_and_reload_roundtrip(self, tmp_path):
        path = tmp_path / "credentials.yaml"
        mgr = CredentialManager(credentials_path=path)
        mgr.set_platform_credentials(
            "redshift",
            {
                "host": "cluster.us-east-1.redshift.amazonaws.com",
                "username": "admin",
                "password": "s3cret",
            },
        )
        mgr.save_credentials()

        mgr2 = CredentialManager(credentials_path=path)
        creds = mgr2.get_platform_credentials("redshift")
        assert creds["host"] == "cluster.us-east-1.redshift.amazonaws.com"
        assert creds["username"] == "admin"
        assert creds["password"] == "s3cret"

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows does not support Unix file permissions")
    def test_save_sets_secure_permissions(self, tmp_path):
        path = tmp_path / "credentials.yaml"
        mgr = CredentialManager(credentials_path=path)
        mgr.set_platform_credentials("test", {"key": "val"})
        mgr.save_credentials()
        mode = oct(path.stat().st_mode & 0o777)
        assert mode == "0o600"

    def test_save_includes_metadata(self, tmp_path):
        path = tmp_path / "credentials.yaml"
        mgr = CredentialManager(credentials_path=path)
        mgr.set_platform_credentials("test", {"key": "val"})
        mgr.save_credentials()

        raw = yaml.safe_load(path.read_text())
        assert "_metadata" in raw
        assert raw["_metadata"]["version"] == "1.0"


class TestCredentialManagerStatus:
    def test_missing_platform_returns_missing_status(self, cred_file):
        mgr = cred_file({})
        assert mgr.get_credential_status("snowflake") == CredentialStatus.MISSING

    def test_set_and_query_status(self, cred_file):
        mgr = cred_file({"bigquery": {"project_id": "p"}})
        mgr.update_validation_status("bigquery", CredentialStatus.VALID)
        assert mgr.get_credential_status("bigquery") == CredentialStatus.VALID

    def test_update_status_with_error_message(self, cred_file):
        mgr = cred_file({"snowflake": {"account": "a"}})
        mgr.update_validation_status("snowflake", CredentialStatus.INVALID, "Auth failed")
        creds = mgr.get_platform_credentials("snowflake")
        assert creds["error_message"] == "Auth failed"
        assert creds["status"] == "invalid"

    def test_clear_error_on_valid_status(self, cred_file):
        mgr = cred_file({"snowflake": {"account": "a", "error_message": "old error"}})
        mgr.update_validation_status("snowflake", CredentialStatus.VALID)
        creds = mgr.get_platform_credentials("snowflake")
        assert "error_message" not in creds

    def test_list_platforms_skips_metadata(self, tmp_path):
        path = tmp_path / "credentials.yaml"
        mgr = CredentialManager(credentials_path=path)
        mgr.set_platform_credentials("snowflake", {"account": "a"})
        mgr.set_platform_credentials("redshift", {"host": "h"})
        mgr.save_credentials()

        mgr2 = CredentialManager(credentials_path=path)
        platforms = mgr2.list_platforms()
        assert "_metadata" not in platforms
        assert "snowflake" in platforms
        assert "redshift" in platforms


class TestCredentialManagerMasking:
    def test_sensitive_fields_are_masked(self, cred_file):
        mgr = cred_file(
            {
                "snowflake": {
                    "account": "org-acct",
                    "username": "admin",
                    "password": "super-secret-long-password-123",
                    "access_token": "tok_abcdefghijklmnop",
                }
            }
        )
        display = mgr.get_display_credentials("snowflake")
        assert display["account"] == "org-acct"
        assert display["username"] == "admin"
        assert "..." in display["password"]
        assert display["password"].startswith("supe")
        assert display["password"].endswith("123")
        assert "..." in display["access_token"]

    def test_short_sensitive_value_fully_masked(self, cred_file):
        mgr = cred_file({"test": {"password": "short"}})
        display = mgr.get_display_credentials("test")
        assert display["password"] == "****"

    def test_empty_sensitive_value_shows_not_set(self, cred_file):
        mgr = cred_file({"test": {"password": ""}})
        display = mgr.get_display_credentials("test")
        assert display["password"] == "(not set)"

    def test_missing_platform_returns_empty_dict(self, cred_file):
        mgr = cred_file({})
        assert mgr.get_display_credentials("nonexistent") == {}


class TestCredentialManagerRemove:
    def test_remove_existing_platform(self, cred_file):
        mgr = cred_file({"snowflake": {"account": "a"}})
        assert mgr.remove_platform_credentials("snowflake") is True
        assert mgr.has_credentials("snowflake") is False

    def test_remove_nonexistent_returns_false(self, cred_file):
        mgr = cred_file({})
        assert mgr.remove_platform_credentials("snowflake") is False


class TestSnowflakeValidation:
    def test_missing_credentials(self, cred_file):
        from benchbox.platforms.credentials import snowflake

        mgr = cred_file({})
        ok, err = snowflake.validate_snowflake_credentials(mgr)
        assert ok is False
        assert "No credentials" in err

    def test_missing_required_fields(self, cred_file):
        from benchbox.platforms.credentials import snowflake

        mgr = cred_file({"snowflake": {"account": "a"}})
        ok, err = snowflake.validate_snowflake_credentials(mgr)
        assert ok is False
        assert "Missing required fields" in err

    def test_auto_detect_from_env(self, cred_file, monkeypatch):
        from benchbox.platforms.credentials import snowflake

        console = SimpleNamespace(print=lambda *a, **k: None)
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "org-acct.snowflakecomputing.com")
        monkeypatch.setenv("SNOWFLAKE_USERNAME", "user")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "pwd")
        monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "wh")
        monkeypatch.setenv("SNOWFLAKE_DATABASE", "db")

        detected = snowflake._auto_detect_snowflake(console)
        assert detected is not None
        assert detected["account"] == "org-acct"

    def test_auto_detect_missing_env_returns_none(self, monkeypatch):
        from benchbox.platforms.credentials import snowflake

        console = SimpleNamespace(print=lambda *a, **k: None)
        for var in [
            "SNOWFLAKE_ACCOUNT",
            "SNOWFLAKE_USERNAME",
            "SNOWFLAKE_PASSWORD",
            "SNOWFLAKE_WAREHOUSE",
            "SNOWFLAKE_DATABASE",
        ]:
            monkeypatch.delenv(var, raising=False)
        assert snowflake._auto_detect_snowflake(console) is None

    def test_validation_succeeds_with_schema_and_role(self, cred_file, monkeypatch):
        from benchbox.platforms.credentials import snowflake

        mgr = cred_file(
            {
                "snowflake": {
                    "account": "org-acct",
                    "username": "user",
                    "password": "pwd",
                    "warehouse": "COMPUTE_WH",
                    "database": "BENCHBOX",
                    "schema": "ANALYTICS",
                    "role": "REPORTING",
                }
            }
        )
        cursor = Mock()
        connection = Mock()
        connection.cursor.return_value = cursor
        connector = _make_module("snowflake.connector", connect=Mock(return_value=connection))
        snowflake_pkg = _make_module("snowflake", connector=connector)

        monkeypatch.setitem(sys.modules, "snowflake", snowflake_pkg)
        monkeypatch.setitem(sys.modules, "snowflake.connector", connector)

        ok, err = snowflake.validate_snowflake_credentials(mgr)

        assert (ok, err) == (True, None)
        connect_kwargs = connector.connect.call_args.kwargs
        assert connect_kwargs["user"] == "user"
        assert connect_kwargs["schema"] == "ANALYTICS"
        assert connect_kwargs["role"] == "REPORTING"
        assert [call.args[0] for call in cursor.execute.call_args_list] == [
            "SELECT 1",
            "SELECT CURRENT_WAREHOUSE()",
            "SELECT CURRENT_DATABASE()",
        ]

    def test_validation_maps_authentication_errors(self, cred_file, monkeypatch):
        from benchbox.platforms.credentials import snowflake

        mgr = cred_file(
            {
                "snowflake": {
                    "account": "org-acct",
                    "username": "user",
                    "password": "bad-password",
                    "warehouse": "COMPUTE_WH",
                    "database": "BENCHBOX",
                }
            }
        )
        connector = _make_module(
            "snowflake.connector",
            connect=Mock(side_effect=Exception("Incorrect username or password was specified")),
        )
        snowflake_pkg = _make_module("snowflake", connector=connector)

        monkeypatch.setitem(sys.modules, "snowflake", snowflake_pkg)
        monkeypatch.setitem(sys.modules, "snowflake.connector", connector)

        ok, err = snowflake.validate_snowflake_credentials(mgr)

        assert ok is False
        assert err == "Authentication failed. Check your username and password."


class TestDatabricksValidation:
    def test_missing_credentials(self, cred_file):
        from benchbox.platforms.databricks import credentials as dbx

        mgr = cred_file({})
        ok, err = dbx.validate_databricks_credentials(mgr)
        assert ok is False
        assert "No credentials" in err

    def test_missing_required_fields(self, cred_file):
        from benchbox.platforms.databricks import credentials as dbx

        mgr = cred_file({"databricks": {"server_hostname": "h"}})
        ok, err = dbx.validate_databricks_credentials(mgr)
        assert ok is False
        assert "Missing required fields" in err

    def test_validation_succeeds_and_uses_catalog(self, cred_file, monkeypatch):
        from benchbox.platforms.databricks import credentials as dbx

        mgr = cred_file(
            {
                "databricks": {
                    "server_hostname": "workspace.cloud.databricks.com",
                    "http_path": "/sql/1.0/warehouses/abc123",
                    "access_token": "token",
                    "catalog": "main",
                }
            }
        )
        cursor = Mock()
        connection = Mock()
        connection.cursor.return_value = cursor
        sql_module = _make_module("databricks.sql", connect=Mock(return_value=connection))
        databricks_pkg = _make_module("databricks", sql=sql_module)

        monkeypatch.setitem(sys.modules, "databricks", databricks_pkg)
        monkeypatch.setitem(sys.modules, "databricks.sql", sql_module)

        ok, err = dbx.validate_databricks_credentials(mgr)

        assert (ok, err) == (True, None)
        assert [call.args[0] for call in cursor.execute.call_args_list] == ["SELECT 1", "USE CATALOG main"]

    def test_validation_maps_catalog_errors(self, cred_file, monkeypatch):
        from benchbox.platforms.databricks import credentials as dbx

        mgr = cred_file(
            {
                "databricks": {
                    "server_hostname": "workspace.cloud.databricks.com",
                    "http_path": "/sql/1.0/warehouses/abc123",
                    "access_token": "token",
                    "catalog": "finance",
                }
            }
        )
        cursor = Mock()
        cursor.execute.side_effect = [None, Exception("Catalog finance not found")]
        connection = Mock()
        connection.cursor.return_value = cursor
        sql_module = _make_module("databricks.sql", connect=Mock(return_value=connection))
        databricks_pkg = _make_module("databricks", sql=sql_module)

        monkeypatch.setitem(sys.modules, "databricks", databricks_pkg)
        monkeypatch.setitem(sys.modules, "databricks.sql", sql_module)

        ok, err = dbx.validate_databricks_credentials(mgr)

        assert ok is False
        assert err == "Catalog 'finance' not found or not accessible."


class TestDatabricksAutoDetect:
    def test_auto_detect_returns_none_for_missing_sql_warehouses(self, monkeypatch):
        from benchbox.platforms.databricks import credentials as dbx

        workspace = SimpleNamespace(
            config=SimpleNamespace(host="https://workspace.cloud.databricks.com", token="token"),
            api_client=object(),
        )

        class FakeWarehousesAPI:
            def __init__(self, api_client):
                self.api_client = api_client

            def list(self):
                return []

        sdk_module = _make_module("databricks.sdk", WorkspaceClient=lambda: workspace)
        sql_service_module = _make_module("databricks.sdk.service.sql", WarehousesAPI=FakeWarehousesAPI)
        service_module = _make_module("databricks.sdk.service", sql=sql_service_module)
        databricks_pkg = _make_module("databricks", sdk=sdk_module)
        sdk_module.service = service_module

        monkeypatch.setitem(sys.modules, "databricks", databricks_pkg)
        monkeypatch.setitem(sys.modules, "databricks.sdk", sdk_module)
        monkeypatch.setitem(sys.modules, "databricks.sdk.service", service_module)
        monkeypatch.setitem(sys.modules, "databricks.sdk.service.sql", sql_service_module)

        result = dbx._auto_detect_databricks(SimpleNamespace(print=lambda *a, **k: None))

        assert result == {
            "server_hostname": "workspace.cloud.databricks.com",
            "http_path": None,
            "access_token": "token",
        }

    def test_auto_detect_selects_running_warehouse(self, monkeypatch):
        from benchbox.platforms.databricks import credentials as dbx

        workspace = SimpleNamespace(
            config=SimpleNamespace(host="https://workspace.cloud.databricks.com", token="token"),
            api_client=object(),
        )
        warehouse = SimpleNamespace(name="Analytics", cluster_size="2X-Small", id="abc123", state="RUNNING")

        class FakeWarehousesAPI:
            def __init__(self, api_client):
                self.api_client = api_client

            def list(self):
                return [warehouse]

        sdk_module = _make_module("databricks.sdk", WorkspaceClient=lambda: workspace)
        sql_service_module = _make_module("databricks.sdk.service.sql", WarehousesAPI=FakeWarehousesAPI)
        service_module = _make_module("databricks.sdk.service", sql=sql_service_module)
        databricks_pkg = _make_module("databricks", sdk=sdk_module)
        sdk_module.service = service_module

        monkeypatch.setitem(sys.modules, "databricks", databricks_pkg)
        monkeypatch.setitem(sys.modules, "databricks.sdk", sdk_module)
        monkeypatch.setitem(sys.modules, "databricks.sdk.service", service_module)
        monkeypatch.setitem(sys.modules, "databricks.sdk.service.sql", sql_service_module)
        monkeypatch.setattr(dbx.IntPrompt, "ask", lambda *args, **kwargs: 1)

        result = dbx._auto_detect_databricks(SimpleNamespace(print=lambda *a, **k: None))

        assert result == {
            "server_hostname": "workspace.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/abc123",
            "access_token": "token",
        }


class TestBigQueryValidation:
    def test_missing_credentials(self, cred_file):
        from benchbox.platforms.credentials import bigquery

        mgr = cred_file({})
        ok, err = bigquery.validate_bigquery_credentials(mgr)
        assert ok is False
        assert "No credentials" in err

    def test_missing_required_fields(self, cred_file):
        from benchbox.platforms.credentials import bigquery

        mgr = cred_file({"bigquery": {"project_id": "p"}})
        ok, err = bigquery.validate_bigquery_credentials(mgr)
        assert ok is False
        assert "Missing required fields" in err

    def test_credentials_file_not_found(self, cred_file, tmp_path):
        from benchbox.platforms.credentials import bigquery

        mgr = cred_file(
            {
                "bigquery": {
                    "project_id": "p",
                    "credentials_path": str(tmp_path / "missing.json"),
                }
            }
        )
        ok, err = bigquery.validate_bigquery_credentials(mgr)
        assert ok is False
        assert "not found" in err

    def test_invalid_json_credentials_file(self, cred_file, tmp_path):
        from benchbox.platforms.credentials import bigquery

        bad_json = tmp_path / "bad.json"
        bad_json.write_text("not-json{{{")
        mgr = cred_file(
            {
                "bigquery": {
                    "project_id": "p",
                    "credentials_path": str(bad_json),
                }
            }
        )
        ok, err = bigquery.validate_bigquery_credentials(mgr)
        assert ok is False
        assert "not valid JSON" in err

    def test_auto_detect_from_env(self, monkeypatch, tmp_path):
        from benchbox.platforms.credentials import bigquery

        console = SimpleNamespace(print=lambda *a, **k: None)
        creds_file = tmp_path / "svc.json"
        creds_file.write_text(json.dumps({"type": "service_account"}))
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(creds_file))
        monkeypatch.setenv("BIGQUERY_PROJECT", "proj")
        monkeypatch.delenv("BIGQUERY_DATASET", raising=False)
        monkeypatch.delenv("BIGQUERY_LOCATION", raising=False)

        detected = bigquery._auto_detect_bigquery(console)
        assert detected is not None
        assert detected["dataset_id"] == "benchbox"
        assert detected["location"] == "US"

    def test_credentials_path_must_be_a_file(self, cred_file, tmp_path):
        from benchbox.platforms.credentials import bigquery

        creds_dir = tmp_path / "creds-dir"
        creds_dir.mkdir()
        mgr = cred_file(
            {
                "bigquery": {
                    "project_id": "p",
                    "credentials_path": str(creds_dir),
                }
            }
        )

        ok, err = bigquery.validate_bigquery_credentials(mgr)

        assert ok is False
        assert err == f"Credentials path is not a file: {creds_dir}"

    def test_validation_maps_permission_denied_errors(self, cred_file, monkeypatch, tmp_path):
        from benchbox.platforms.credentials import bigquery

        creds_file = tmp_path / "svc.json"
        creds_file.write_text(json.dumps({"type": "service_account"}))
        mgr = cred_file(
            {
                "bigquery": {
                    "project_id": "proj",
                    "credentials_path": str(creds_file),
                }
            }
        )
        client = Mock()
        client.query.side_effect = Exception("403 Permission denied")
        bigquery_module = _make_module(
            "google.cloud.bigquery",
            Client=SimpleNamespace(from_service_account_json=Mock(return_value=client)),
        )
        cloud_module = _make_module("google.cloud", bigquery=bigquery_module)
        google_module = _make_module("google", cloud=cloud_module)

        monkeypatch.setitem(sys.modules, "google", google_module)
        monkeypatch.setitem(sys.modules, "google.cloud", cloud_module)
        monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bigquery_module)

        ok, err = bigquery.validate_bigquery_credentials(mgr)

        assert ok is False
        assert err == "Permission denied. Service account needs BigQuery Data Editor and BigQuery Job User roles."

    def test_validation_checks_storage_bucket_access(self, cred_file, monkeypatch, tmp_path):
        from benchbox.platforms.credentials import bigquery

        creds_file = tmp_path / "svc.json"
        creds_file.write_text(json.dumps({"type": "service_account"}))
        mgr = cred_file(
            {
                "bigquery": {
                    "project_id": "proj",
                    "credentials_path": str(creds_file),
                    "storage_bucket": "benchbox-data",
                }
            }
        )
        first_job = Mock()
        first_job.result.return_value = []
        second_job = Mock()
        second_job.result.return_value = [SimpleNamespace(project="proj")]
        query_client = Mock()
        query_client.query.side_effect = [first_job, second_job]
        query_client.list_datasets.return_value = []

        bucket = Mock()
        bucket.reload.side_effect = Exception("403 forbidden")
        storage_client = Mock()
        storage_client.bucket.return_value = bucket

        bigquery_module = _make_module(
            "google.cloud.bigquery",
            Client=SimpleNamespace(from_service_account_json=Mock(return_value=query_client)),
        )
        storage_module = _make_module(
            "google.cloud.storage",
            Client=SimpleNamespace(from_service_account_json=Mock(return_value=storage_client)),
        )
        cloud_module = _make_module("google.cloud", bigquery=bigquery_module, storage=storage_module)
        google_module = _make_module("google", cloud=cloud_module)

        monkeypatch.setitem(sys.modules, "google", google_module)
        monkeypatch.setitem(sys.modules, "google.cloud", cloud_module)
        monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bigquery_module)
        monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_module)

        ok, err = bigquery.validate_bigquery_credentials(mgr)

        assert ok is False
        assert err == (
            "No access to Cloud Storage bucket 'benchbox-data'. Service account needs Storage Object Admin role"
        )

    def test_auto_detect_returns_none_when_credentials_file_is_missing(self, monkeypatch, tmp_path):
        from benchbox.platforms.credentials import bigquery

        console = Mock()
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "missing.json"))
        monkeypatch.setenv("BIGQUERY_PROJECT", "proj")

        detected = bigquery._auto_detect_bigquery(console)

        assert detected is None
        assert "Credentials file not found" in str(console.print.call_args_list[0])


class TestRedshiftValidation:
    def test_missing_credentials(self, cred_file):
        from benchbox.platforms.credentials import redshift as rs

        mgr = cred_file({})
        ok, err = rs.validate_redshift_credentials(mgr, console=None)
        assert ok is False
        assert "No credentials" in err

    def test_missing_required_fields(self, cred_file):
        from benchbox.platforms.credentials import redshift as rs

        mgr = cred_file({"redshift": {"host": "h"}})
        ok, err = rs.validate_redshift_credentials(mgr, console=None)
        assert ok is False
        assert "Missing required fields" in err

    def test_auto_detect_from_env(self, monkeypatch):
        from benchbox.platforms.credentials import redshift as rs

        console = SimpleNamespace(print=lambda *a, **k: None)
        monkeypatch.setenv("REDSHIFT_HOST", "wg.123.us-east-1.redshift-serverless.amazonaws.com")
        monkeypatch.setenv("REDSHIFT_DATABASE", "dev")
        monkeypatch.setenv("REDSHIFT_USERNAME", "admin")
        monkeypatch.setenv("REDSHIFT_PASSWORD", "pw")

        detected = rs._auto_detect_redshift(console)
        assert detected is not None
        assert detected["port"] == 5439

    def test_auto_detect_invalid_port_falls_back_to_default(self, monkeypatch):
        from benchbox.platforms.credentials import redshift as rs

        console = SimpleNamespace(print=lambda *a, **k: None)
        monkeypatch.setenv("REDSHIFT_HOST", "cluster.redshift.amazonaws.com")
        monkeypatch.setenv("REDSHIFT_DATABASE", "dev")
        monkeypatch.setenv("REDSHIFT_USERNAME", "admin")
        monkeypatch.setenv("REDSHIFT_PASSWORD", "pw")
        monkeypatch.setenv("REDSHIFT_PORT", "not-a-port")

        detected = rs._auto_detect_redshift(console)

        assert detected is not None
        assert detected["port"] == 5439

    def test_validation_returns_timeout_when_tcp_check_fails(self, cred_file, monkeypatch):
        from benchbox.platforms.credentials import redshift as rs

        mgr = cred_file(
            {
                "redshift": {
                    "host": "cluster.redshift.amazonaws.com",
                    "username": "admin",
                    "password": "secret",
                }
            }
        )

        class FailingSocket:
            def settimeout(self, timeout):
                self.timeout = timeout

            def connect_ex(self, addr):
                return 111

            def close(self):
                return None

        connector = _make_module("redshift_connector", connect=Mock())
        monkeypatch.setitem(sys.modules, "redshift_connector", connector)
        monkeypatch.setattr(rs.socket, "socket", lambda *args, **kwargs: FailingSocket())

        ok, err = rs.validate_redshift_credentials(mgr, console=None)

        assert ok is False
        assert err == "Connection timeout. Check VPC/security group settings and network connectivity."

    def test_validation_maps_authentication_errors(self, cred_file, monkeypatch):
        from benchbox.platforms.credentials import redshift as rs

        mgr = cred_file(
            {
                "redshift": {
                    "host": "cluster.redshift.amazonaws.com",
                    "username": "admin",
                    "password": "bad-secret",
                    "database": "dev",
                }
            }
        )

        class ReachableSocket:
            def settimeout(self, timeout):
                self.timeout = timeout

            def connect_ex(self, addr):
                return 0

            def close(self):
                return None

        adapter = Mock()
        adapter._create_direct_connection.side_effect = Exception("password authentication failed for user admin")

        monkeypatch.setattr(rs, "_build_redshift_adapter", lambda creds: adapter)
        monkeypatch.setattr(rs.socket, "socket", lambda *args, **kwargs: ReachableSocket())

        ok, err = rs.validate_redshift_credentials(mgr, console=None)

        assert ok is False
        assert err == "Authentication failed. Check your username and password."

    def test_validation_uses_shared_adapter_connection_path(self, cred_file, monkeypatch):
        from benchbox.platforms.credentials import redshift as rs

        mgr = cred_file(
            {
                "redshift": {
                    "host": "cluster.redshift.amazonaws.com",
                    "username": "admin",
                    "password": "secret",
                    "database": "dev",
                }
            }
        )

        class ReachableSocket:
            def settimeout(self, timeout):
                self.timeout = timeout

            def connect_ex(self, addr):
                return 0

            def close(self):
                return None

        cursor = Mock()
        connection = Mock()
        connection.cursor.return_value = cursor

        adapter = Mock()
        adapter._create_direct_connection.return_value = connection

        monkeypatch.setattr(rs, "_build_redshift_adapter", lambda creds: adapter)
        monkeypatch.setattr(rs.socket, "socket", lambda *args, **kwargs: ReachableSocket())

        ok, err = rs.validate_redshift_credentials(mgr, console=None)

        assert (ok, err) == (True, None)
        adapter._create_direct_connection.assert_called_once_with(database="dev", connect_timeout=10)
        assert [call.args[0] for call in cursor.execute.call_args_list] == [
            "SELECT 1",
            "SELECT version()",
            "SELECT current_database()",
        ]

    def test_validation_maps_server_refuses_ssl_to_friendly_message(self, cred_file, monkeypatch):
        from benchbox.platforms.credentials import redshift as rs

        mgr = cred_file(
            {
                "redshift": {
                    "host": "cluster.redshift.amazonaws.com",
                    "username": "admin",
                    "password": "secret",
                    "database": "dev",
                }
            }
        )

        class ReachableSocket:
            def settimeout(self, timeout):
                self.timeout = timeout

            def connect_ex(self, addr):
                return 0

            def close(self):
                return None

        adapter = Mock()
        adapter._create_direct_connection.side_effect = Exception("Server refuses SSL")

        monkeypatch.setattr(rs, "_build_redshift_adapter", lambda creds: adapter)
        monkeypatch.setattr(rs.socket, "socket", lambda *args, **kwargs: ReachableSocket())

        ok, err = rs.validate_redshift_credentials(mgr, console=None)

        assert ok is False
        assert (
            err
            == "SSL negotiation failed. Check that the endpoint is a Redshift endpoint that accepts TLS on port 5439."
        )

    def test_validation_maps_timed_out_errors_to_network_guidance(self, cred_file, monkeypatch):
        from benchbox.platforms.credentials import redshift as rs

        mgr = cred_file(
            {
                "redshift": {
                    "host": "cluster.redshift.amazonaws.com",
                    "username": "admin",
                    "password": "secret",
                    "database": "dev",
                }
            }
        )

        class ReachableSocket:
            def settimeout(self, timeout):
                self.timeout = timeout

            def connect_ex(self, addr):
                return 0

            def close(self):
                return None

        adapter = Mock()
        adapter._create_direct_connection.side_effect = Exception("The read operation timed out")

        monkeypatch.setattr(rs, "_build_redshift_adapter", lambda creds: adapter)
        monkeypatch.setattr(rs.socket, "socket", lambda *args, **kwargs: ReachableSocket())
        monkeypatch.setattr(rs.time, "sleep", lambda _: None)

        ok, err = rs.validate_redshift_credentials(mgr, console=None)

        assert ok is False
        assert err == "Connection timeout. Check VPC/security group settings and network connectivity."

    def test_env_var_credentials_through_real_manager(self, tmp_path, monkeypatch):

        from benchbox.platforms.credentials import redshift as rs

        monkeypatch.setenv("RS_HOST", "cluster.redshift.amazonaws.com")
        monkeypatch.setenv("RS_USER", "admin")
        monkeypatch.setenv("RS_PASS", "s3cret")

        path = tmp_path / "credentials.yaml"
        path.write_text(
            yaml.dump(
                {
                    "redshift": {
                        "host": "${RS_HOST}",
                        "username": "$RS_USER",
                        "password": "$RS_PASS",
                        "database": "dev",
                    }
                }
            )
        )
        mgr = CredentialManager(credentials_path=path)
        creds = mgr.get_platform_credentials("redshift")
        assert creds["host"] == "cluster.redshift.amazonaws.com"
        assert creds["username"] == "admin"
        assert creds["password"] == "s3cret"

        class FailingSocket:
            def settimeout(self, timeout):
                self.timeout = timeout

            def connect_ex(self, addr):
                return 111

            def close(self):
                return None

        monkeypatch.setattr(rs.socket, "socket", lambda *args, **kwargs: FailingSocket())

        ok, err = rs.validate_redshift_credentials(mgr, console=None)

        assert "Missing required fields" not in (err or "")


