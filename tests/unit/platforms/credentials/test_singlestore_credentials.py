# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import patch

import pytest
from rich.console import Console

from benchbox.platforms.credentials import singlestore as ss
from benchbox.security.credentials import CredentialManager, CredentialStatus

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _run_setup(tmp_path, validation_result):
    manager = CredentialManager(credentials_path=tmp_path / "credentials.yaml")
    console = Console(file=open(tmp_path / "console.log", "w", encoding="utf-8"), force_terminal=False)
    with (
        patch.object(ss, "_auto_detect_singlestore", return_value=None),
        patch("rich.prompt.Confirm.ask", return_value=False),
        patch.object(ss, "prompt_with_default", side_effect=["db.example.com", "3306", "joe", "benchbox"]),
        patch.object(ss, "prompt_secure_field", return_value="PW-SENTINEL"),
        patch.object(ss, "validate_singlestore_credentials", return_value=validation_result),
    ):
        ss.setup_singlestore_credentials(manager, console)
    return manager


class TestSingleStorePersistence:
    def test_singlestore_persistence_on_failed_validation_saves_invalid(self, tmp_path):
        _run_setup(tmp_path, (False, "connection refused"))
        reloaded = CredentialManager(credentials_path=tmp_path / "credentials.yaml")
        saved = reloaded.get_platform_credentials("singlestore")
        assert saved is not None, "failed validation still persists credentials for retry UX"
        assert saved["password"] == "PW-SENTINEL"
        assert reloaded.get_credential_status("singlestore") == CredentialStatus.INVALID

    def test_singlestore_persistence_on_success_saves_valid(self, tmp_path):
        _run_setup(tmp_path, (True, None))
        reloaded = CredentialManager(credentials_path=tmp_path / "credentials.yaml")
        assert reloaded.get_platform_credentials("singlestore")["host"] == "db.example.com"
        assert reloaded.get_credential_status("singlestore") == CredentialStatus.VALID

    def test_singlestore_failure_retry_command_is_available(self):
        from click.testing import CliRunner

        from benchbox.cli.main import cli

        result = CliRunner().invoke(cli, ["setup", "--help"])
        assert result.exit_code == 0
        assert "--platform" in result.output
        assert "--validate-only" in result.output
