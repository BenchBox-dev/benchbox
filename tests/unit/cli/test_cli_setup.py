# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from benchbox.cli.main import cli
from benchbox.security.credentials import CredentialManager, CredentialStatus

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def _credentials_file(home: Path) -> Path:
    return home / ".benchbox" / "credentials.yaml"


def _seed_credentials(home: Path, entries: dict[str, tuple[dict, CredentialStatus]]) -> CredentialManager:
    del home
    manager = CredentialManager()
    for platform, (creds, status) in entries.items():
        manager.set_platform_credentials(platform, creds, status)
        manager.update_validation_status(platform, status)
    manager.save_credentials()
    return manager


def _read_credentials(home: Path) -> dict:
    with open(_credentials_file(home), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class TestSetupCommand:
    def test_setup_command_exists(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "setup" in result.output

    def test_setup_help(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--help"])

        assert result.exit_code == 0
        assert "Interactive setup for cloud platform credentials" in result.output
        assert "--platform" in result.output
        assert "--validate-only" in result.output
        assert "--list-platforms" in result.output
        assert "--status" in result.output
        assert "--remove" in result.output

    def test_setup_no_platform_error(self):

        runner = CliRunner()
        result = runner.invoke(cli, ["setup"])

        assert result.exit_code == 0
        assert "Error: --platform is required" in result.output
        assert "Available platforms:" in result.output

    def test_setup_list_platforms(self, isolated_home: Path):

        _seed_credentials(
            isolated_home,
            {
                "databricks": ({"host": "https://x.cloud.databricks.com"}, CredentialStatus.VALID),
                "snowflake": ({"account": "xy12345"}, CredentialStatus.MISSING),
            },
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--list-platforms"])

        assert result.exit_code == 0
        assert "Cloud Platforms Requiring Credentials" in result.output
        assert "Databricks" in result.output
        assert "Snowflake" in result.output
        assert "BigQuery" in result.output
        assert "Redshift" in result.output
        assert "MotherDuck" in result.output
        assert "SingleStore" in result.output
        assert "✅ Configured" in result.output
        assert "○ Not configured" in result.output

    def test_setup_status_no_credentials(self, isolated_home: Path):

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--status"])

        assert result.exit_code == 0
        assert "No credentials configured yet" in result.output
        assert "benchbox setup --platform" in result.output
        assert not _credentials_file(isolated_home).exists()

    def test_setup_status_with_credentials(self, isolated_home: Path):

        _seed_credentials(
            isolated_home,
            {
                "databricks": ({"host": "https://x.cloud.databricks.com"}, CredentialStatus.VALID),
                "snowflake": ({"account": "xy12345"}, CredentialStatus.INVALID),
            },
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--status"])

        assert result.exit_code == 0
        assert "Credential Status" in result.output
        assert "✅ Valid" in result.output
        assert "❌ Invalid" in result.output

    def test_setup_remove_confirmed(self, isolated_home: Path):

        _seed_credentials(
            isolated_home,
            {"databricks": ({"host": "https://x.cloud.databricks.com"}, CredentialStatus.VALID)},
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "databricks", "--remove"], input="y\n")

        assert result.exit_code == 0
        assert "Removed credentials for databricks" in result.output
        assert "databricks" not in _read_credentials(isolated_home)

    def test_setup_remove_cancelled(self, isolated_home: Path):

        _seed_credentials(
            isolated_home,
            {"databricks": ({"host": "https://x.cloud.databricks.com"}, CredentialStatus.VALID)},
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "databricks", "--remove"], input="n\n")

        assert result.exit_code == 0
        assert "Cancelled" in result.output
        assert "databricks" in _read_credentials(isolated_home)

    def test_setup_remove_no_credentials(self, isolated_home: Path):

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "databricks", "--remove"])

        assert result.exit_code == 0
        assert "No credentials found for databricks" in result.output

    @patch("benchbox.platforms.databricks.credentials.validate_databricks_credentials")
    def test_setup_validate_only_success(self, mock_validate, isolated_home: Path):

        manager = _seed_credentials(
            isolated_home,
            {"databricks": ({"host": "https://x.cloud.databricks.com"}, CredentialStatus.NOT_VALIDATED)},
        )
        assert manager.get_credential_status("databricks") == CredentialStatus.NOT_VALIDATED
        mock_validate.return_value = (True, None)

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "databricks", "--validate-only"])

        assert result.exit_code == 0
        assert "Databricks credentials are valid" in result.output
        mock_validate.assert_called_once()
        assert isinstance(mock_validate.call_args[0][0], CredentialManager)
        stored = _read_credentials(isolated_home)["databricks"]
        assert stored["status"] == CredentialStatus.VALID.value
        assert "last_validated" in stored
        assert CredentialManager().get_credential_status("databricks") == CredentialStatus.VALID

    @patch("benchbox.platforms.databricks.credentials.validate_databricks_credentials")
    def test_setup_validate_only_failure(self, mock_validate, isolated_home: Path):

        _seed_credentials(
            isolated_home,
            {"databricks": ({"host": "https://x.cloud.databricks.com"}, CredentialStatus.NOT_VALIDATED)},
        )
        mock_validate.return_value = (False, "Authentication failed")

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "databricks", "--validate-only"])

        assert result.exit_code == 0
        assert "Databricks credentials are invalid" in result.output
        assert "Authentication failed" in result.output
        stored = _read_credentials(isolated_home)["databricks"]
        assert stored["status"] == CredentialStatus.INVALID.value
        assert stored["error_message"] == "Authentication failed"

    def test_setup_validate_only_no_credentials(self, isolated_home: Path):

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "databricks", "--validate-only"])

        assert result.exit_code == 0
        assert "No credentials found for databricks" in result.output
        assert "Setup credentials: benchbox setup --platform databricks" in result.output

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    def test_setup_missing_dependencies(self, mock_check_deps, isolated_home: Path):
        del isolated_home
        mock_check_deps.return_value = (False, ["databricks-sdk", "databricks-connect"])
        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "databricks"])

        assert result.exit_code == 0
        assert "Missing dependencies for databricks" in result.output
        assert "databricks-sdk" in result.output
        assert "databricks-connect" in result.output
        assert "Install with:" in result.output

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.platforms.databricks.credentials.setup_databricks_credentials")
    def test_setup_interactive_databricks(self, mock_setup_databricks, mock_check_deps, isolated_home: Path):

        del isolated_home
        mock_check_deps.return_value = (True, [])

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "databricks"])

        assert result.exit_code == 0
        assert "Databricks Credentials Setup" in result.output
        mock_setup_databricks.assert_called_once()
        call_args = mock_setup_databricks.call_args
        assert isinstance(call_args[0][0], CredentialManager)

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.platforms.credentials.snowflake.setup_snowflake_credentials")
    def test_setup_interactive_snowflake(self, mock_setup_snowflake, mock_check_deps, isolated_home: Path):

        del isolated_home
        mock_check_deps.return_value = (True, [])

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "snowflake"])

        assert result.exit_code == 0
        assert "Snowflake Credentials Setup" in result.output
        mock_setup_snowflake.assert_called_once()
        call_args = mock_setup_snowflake.call_args
        assert isinstance(call_args[0][0], CredentialManager)

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.platforms.credentials.redshift.setup_redshift_credentials")
    def test_setup_interactive_redshift(self, mock_setup_redshift, mock_check_deps, isolated_home: Path):

        del isolated_home
        mock_check_deps.return_value = (True, [])

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "redshift"])

        assert result.exit_code == 0
        assert "Redshift Credentials Setup" in result.output
        mock_setup_redshift.assert_called_once()
        call_args = mock_setup_redshift.call_args
        assert isinstance(call_args[0][0], CredentialManager)

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.platforms.credentials.motherduck.setup_motherduck_credentials")
    def test_setup_interactive_motherduck(self, mock_setup_motherduck, mock_check_deps, isolated_home: Path):

        del isolated_home
        mock_check_deps.return_value = (True, [])

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "motherduck"])

        assert result.exit_code == 0
        assert "MotherDuck Credentials Setup" in result.output
        mock_setup_motherduck.assert_called_once()
        call_args = mock_setup_motherduck.call_args
        assert isinstance(call_args[0][0], CredentialManager)

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.platforms.credentials.singlestore.setup_singlestore_credentials")
    def test_setup_interactive_singlestore(self, mock_setup_singlestore, mock_check_deps, isolated_home: Path):

        del isolated_home
        mock_check_deps.return_value = (True, [])

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "singlestore"])

        assert result.exit_code == 0
        assert "SingleStore Credentials Setup" in result.output
        mock_setup_singlestore.assert_called_once()
        assert isinstance(mock_setup_singlestore.call_args[0][0], CredentialManager)

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.platforms.credentials.bigquery.setup_bigquery_credentials")
    def test_setup_interactive_bigquery(self, mock_setup_bigquery, mock_check_deps, isolated_home: Path):

        del isolated_home
        mock_check_deps.return_value = (True, [])

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "bigquery"])

        assert result.exit_code == 0
        assert "Bigquery Credentials Setup" in result.output
        mock_setup_bigquery.assert_called_once()
        call_args = mock_setup_bigquery.call_args
        assert isinstance(call_args[0][0], CredentialManager)

    @patch("benchbox.utils.dependencies.check_platform_dependencies")
    @patch("benchbox.platforms.databricks.credentials.setup_databricks_credentials")
    def test_setup_platform_case_insensitive(self, mock_setup_databricks, mock_check_deps, isolated_home: Path):

        del isolated_home
        mock_check_deps.return_value = (True, [])

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "DATABRICKS"])

        assert result.exit_code == 0
        assert "Databricks Credentials Setup" in result.output


class TestSetupValidation:
    @patch("benchbox.platforms.credentials.snowflake.validate_snowflake_credentials")
    def test_validate_snowflake(self, mock_validate, isolated_home: Path):

        _seed_credentials(
            isolated_home,
            {"snowflake": ({"account": "xy12345", "user": "tester"}, CredentialStatus.NOT_VALIDATED)},
        )
        mock_validate.return_value = (True, None)

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "snowflake", "--validate-only"])

        assert result.exit_code == 0
        assert "Snowflake credentials are valid" in result.output
        assert _read_credentials(isolated_home)["snowflake"]["status"] == CredentialStatus.VALID.value

    @patch("benchbox.platforms.credentials.bigquery.validate_bigquery_credentials")
    def test_validate_bigquery(self, mock_validate, isolated_home: Path):

        _seed_credentials(
            isolated_home,
            {"bigquery": ({"project": "test-project"}, CredentialStatus.NOT_VALIDATED)},
        )
        mock_validate.return_value = (True, None)

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "bigquery", "--validate-only"])

        assert result.exit_code == 0
        assert "Bigquery credentials are valid" in result.output
        assert _read_credentials(isolated_home)["bigquery"]["status"] == CredentialStatus.VALID.value

    @patch("benchbox.platforms.credentials.redshift.validate_redshift_credentials")
    def test_validate_redshift(self, mock_validate, isolated_home: Path):

        _seed_credentials(
            isolated_home,
            {"redshift": ({"host": "cluster.example.com"}, CredentialStatus.NOT_VALIDATED)},
        )
        mock_validate.return_value = (True, None)

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "redshift", "--validate-only"])

        assert result.exit_code == 0
        assert "Redshift credentials are valid" in result.output
        assert _read_credentials(isolated_home)["redshift"]["status"] == CredentialStatus.VALID.value

    @patch("benchbox.platforms.credentials.motherduck.validate_motherduck_credentials")
    def test_validate_motherduck_without_stored_credentials(self, mock_validate, isolated_home: Path):
        mock_validate.return_value = (True, None)

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "motherduck", "--validate-only"])

        assert result.exit_code == 0
        assert "MotherDuck credentials are valid" in result.output
        mock_validate.assert_called_once()
        assert isinstance(mock_validate.call_args[0][0], CredentialManager)
        stored = _read_credentials(isolated_home)["motherduck"]
        assert stored["database"] == "benchbox"
        assert stored["token_env_var"] == "MOTHERDUCK_TOKEN"

    @patch("benchbox.platforms.credentials.singlestore.validate_singlestore_credentials")
    def test_validate_singlestore(self, mock_validate, isolated_home: Path):

        _seed_credentials(
            isolated_home,
            {"singlestore": ({"host": "localhost"}, CredentialStatus.NOT_VALIDATED)},
        )
        mock_validate.return_value = (True, None)

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "singlestore", "--validate-only"])

        assert result.exit_code == 0
        assert "SingleStore credentials are valid" in result.output
        mock_validate.assert_called_once()
        assert isinstance(mock_validate.call_args[0][0], CredentialManager)
        assert _read_credentials(isolated_home)["singlestore"]["status"] == CredentialStatus.VALID.value


class TestSetupIntegration:
    def test_setup_real_execution_list(self, isolated_home: Path):

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--list-platforms"])

        assert result.exit_code == 0
        assert "Cloud Platforms Requiring Credentials" in result.output
        assert "Databricks" in result.output
        assert "Snowflake" in result.output
        assert "MotherDuck" in result.output

    def test_setup_real_execution_status(self, isolated_home: Path):

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--status"])

        assert result.exit_code == 0
        assert "No credentials configured yet" in result.output

    @patch("benchbox.platforms.credentials.motherduck.validate_motherduck_credentials")
    def test_setup_motherduck_interactive_flow_writes_real_file(
        self, mock_validate, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MOTHERDUCK_TOKEN", "test-token")
        mock_validate.return_value = (True, None)

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--platform", "motherduck"], input="\n")

        assert result.exit_code == 0
        assert "MotherDuck Credentials Setup" in result.output
        stored = _read_credentials(isolated_home)["motherduck"]
        assert stored["database"] == "benchbox"
        assert stored["token_env_var"] == "MOTHERDUCK_TOKEN"
        assert stored["status"] == CredentialStatus.VALID.value
        assert "test-token" not in _credentials_file(isolated_home).read_text(encoding="utf-8")

    def test_setup_status_reflects_previous_setup_run(self, isolated_home: Path):
        _seed_credentials(
            isolated_home,
            {"snowflake": ({"account": "xy12345", "user": "tester"}, CredentialStatus.VALID)},
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["setup", "--status"])

        assert result.exit_code == 0
        assert "Credential Status" in result.output
        assert "✅ Valid" in result.output

        remove = runner.invoke(cli, ["setup", "--platform", "snowflake", "--remove"], input="y\n")
        assert remove.exit_code == 0

        status = runner.invoke(cli, ["setup", "--status"])
        assert status.exit_code == 0
        assert "No credentials configured yet" in status.output


