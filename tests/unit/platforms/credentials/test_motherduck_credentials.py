from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.credentials.motherduck import (
    MOTHERDUCK_TOKEN_ENV,
    setup_motherduck_credentials,
    validate_motherduck_credentials,
)
from benchbox.security.credentials import CredentialStatus

pytestmark = [pytest.mark.unit, pytest.mark.fast]

MODULE = "benchbox.platforms.credentials.motherduck"


def _cred_manager(existing: dict | None = None) -> MagicMock:
    manager = MagicMock()
    manager.get_platform_credentials.return_value = existing
    manager.credentials_path = "/tmp/fake-credentials.yaml"
    return manager


def test_validate_requires_environment_token(monkeypatch):
    monkeypatch.delenv(MOTHERDUCK_TOKEN_ENV, raising=False)

    ok, error = validate_motherduck_credentials(_cred_manager({"database": "benchbox"}))

    assert ok is False
    assert error == f"{MOTHERDUCK_TOKEN_ENV} is not set"


def test_validate_uses_env_token_and_stored_database(monkeypatch):
    monkeypatch.setenv(MOTHERDUCK_TOKEN_ENV, "env-token")
    manager = _cred_manager({"database": "analytics"})
    adapter = MagicMock()
    adapter_cls = MagicMock(return_value=adapter)

    with patch("benchbox.platforms.motherduck.MotherDuckAdapter", adapter_cls):
        ok, error = validate_motherduck_credentials(manager)

    assert ok is True
    assert error is None
    adapter_cls.assert_called_once_with(token="env-token", database="analytics")
    adapter.create_connection.assert_called_once_with()
    adapter.close_connection.assert_called_once_with()


def test_validate_redacts_token_from_errors(monkeypatch):
    monkeypatch.setenv(MOTHERDUCK_TOKEN_ENV, "secret-token")
    adapter = MagicMock()
    adapter.create_connection.side_effect = RuntimeError(
        "failed to connect to md:benchbox?motherduck_token=secret-token"
    )

    with patch("benchbox.platforms.motherduck.MotherDuckAdapter", return_value=adapter):
        ok, error = validate_motherduck_credentials(_cred_manager({"database": "benchbox"}))

    assert ok is False
    assert "secret-token" not in error
    assert "motherduck_token=****" in error
    adapter.close_connection.assert_called_once_with()


@patch(f"{MODULE}.validate_motherduck_credentials", return_value=(True, None))
@patch(f"{MODULE}.prompt_secure_field")
@patch(f"{MODULE}.prompt_with_default", return_value="analytics")
def test_setup_uses_existing_env_token_without_saving_secret(
    mock_database_prompt,
    mock_secure_prompt,
    mock_validate,
    monkeypatch,
):
    monkeypatch.setenv(MOTHERDUCK_TOKEN_ENV, "env-token")
    manager = _cred_manager(None)
    console = MagicMock()

    result = setup_motherduck_credentials(manager, console)

    assert result is True
    mock_secure_prompt.assert_not_called()
    mock_validate.assert_called_once_with(manager, token="env-token", database="analytics")
    saved_credentials = manager.set_platform_credentials.call_args[0][1]
    assert saved_credentials == {"database": "analytics", "token_env_var": MOTHERDUCK_TOKEN_ENV}
    assert "token" not in saved_credentials
    manager.update_validation_status.assert_called_once_with("motherduck", CredentialStatus.VALID)
    manager.save_credentials.assert_called_once_with()


@patch(f"{MODULE}.validate_motherduck_credentials", return_value=(True, None))
@patch(f"{MODULE}.prompt_secure_field", return_value="prompt-token")
@patch(f"{MODULE}.prompt_with_default", return_value="benchbox")
def test_setup_prompt_token_is_validated_but_not_persisted(
    mock_database_prompt,
    mock_secure_prompt,
    mock_validate,
    monkeypatch,
):
    from tests.utilities.session_isolation import own_environment

    own_environment(monkeypatch, [MOTHERDUCK_TOKEN_ENV])
    monkeypatch.delenv(MOTHERDUCK_TOKEN_ENV, raising=False)
    manager = _cred_manager(None)
    console = MagicMock()

    result = setup_motherduck_credentials(manager, console)

    assert result is True
    mock_secure_prompt.assert_called_once_with("MotherDuck token", current_value=None, console=console)
    mock_validate.assert_called_once_with(manager, token="prompt-token", database="benchbox")
    saved_credentials = manager.set_platform_credentials.call_args[0][1]
    assert saved_credentials == {"database": "benchbox", "token_env_var": MOTHERDUCK_TOKEN_ENV}
    assert "token" not in saved_credentials
    manager.update_validation_status.assert_called_once_with("motherduck", CredentialStatus.NOT_VALIDATED)
    manager.save_credentials.assert_called_once_with()
    assert os.environ[MOTHERDUCK_TOKEN_ENV] == "prompt-token"


