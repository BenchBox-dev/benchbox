from __future__ import annotations

import importlib

import click
import pytest
from click.testing import CliRunner

auth_cmd = importlib.import_module("benchbox.cli.commands.auth")
submit_auth = importlib.import_module("benchbox.cli.submit_auth")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class FakeKeyring:
    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.passwords.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.passwords[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.passwords.pop((service_name, username), None)


def test_normalize_service_url_strips_case_and_trailing_slash() -> None:
    assert submit_auth.normalize_service_url("HTTPS://API.BENCHBOX.DEV/v1/") == "https://api.benchbox.dev/v1"


def test_auth_store_saves_reads_and_deletes_token() -> None:
    fake = FakeKeyring()
    store = submit_auth.SubmissionAuthStore(keyring_backend=fake)
    service_url = "https://api.benchbox.dev/v1/"

    assert store.delete_token(service_url) is False

    store.save_token(service_url, " secret-token ")
    assert store.get_token(service_url) == "secret-token"

    assert store.delete_token(service_url) is True
    assert store.get_token(service_url) is None


def test_resolve_submission_token_prefers_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHBOX_SUBMIT_TOKEN", " env-token ")
    monkeypatch.setenv("BENCHBOX_SERVICE_TOKEN", " fallback-token ")
    store = submit_auth.SubmissionAuthStore(keyring_backend=FakeKeyring())

    token = submit_auth.resolve_submission_token(prompt=False, store=store)

    assert token.token == "env-token"
    assert token.source == "environment:BENCHBOX_SUBMIT_TOKEN"


def test_resolve_submission_token_accepts_service_token_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHBOX_SUBMIT_TOKEN", raising=False)
    monkeypatch.setenv("BENCHBOX_SERVICE_TOKEN", " fallback-token ")
    store = submit_auth.SubmissionAuthStore(keyring_backend=FakeKeyring())

    token = submit_auth.resolve_submission_token(prompt=False, store=store)

    assert token.token == "fallback-token"
    assert token.source == "environment:BENCHBOX_SERVICE_TOKEN"


def test_resolve_submission_token_uses_keyring_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BENCHBOX_SUBMIT_TOKEN", raising=False)
    monkeypatch.delenv("BENCHBOX_SERVICE_TOKEN", raising=False)
    fake = FakeKeyring()
    store = submit_auth.SubmissionAuthStore(keyring_backend=fake)
    store.save_token(submit_auth.DEFAULT_SERVICE_URL, "stored-token")

    token = submit_auth.resolve_submission_token(prompt=False, store=store)

    assert token.token == "stored-token"
    assert token.source == "keyring"


def test_resolve_submission_token_prompts_and_stores_without_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BENCHBOX_SUBMIT_TOKEN", raising=False)
    monkeypatch.delenv("BENCHBOX_SERVICE_TOKEN", raising=False)
    fake = FakeKeyring()
    store = submit_auth.SubmissionAuthStore(keyring_backend=fake)

    @click.command()
    def command() -> None:
        token = submit_auth.resolve_submission_token(store=store)
        click.echo(token.source)

    result = CliRunner().invoke(command, [], input="prompt-token\n")

    assert result.exit_code == 0
    assert "keyring" in result.output
    assert "prompt-token" not in result.output
    assert store.get_token(submit_auth.DEFAULT_SERVICE_URL) == "prompt-token"


def test_refresh_submission_token_refuses_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHBOX_SUBMIT_TOKEN", "stale-env-token")
    store = submit_auth.SubmissionAuthStore(keyring_backend=FakeKeyring())

    with pytest.raises(submit_auth.SubmissionAuthError, match="environment:BENCHBOX_SUBMIT_TOKEN"):
        submit_auth.refresh_submission_token(store=store)


def test_auth_command_login_status_logout_do_not_print_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BENCHBOX_SUBMIT_TOKEN", raising=False)
    monkeypatch.delenv("BENCHBOX_SERVICE_TOKEN", raising=False)
    fake = FakeKeyring()
    monkeypatch.setattr(submit_auth, "_load_keyring", lambda: fake)
    runner = CliRunner()

    login = runner.invoke(auth_cmd.auth, ["login", "--token", "cli-secret"])
    assert login.exit_code == 0
    assert "credentials saved" in login.output.lower()
    assert "cli-secret" not in login.output

    status = runner.invoke(auth_cmd.auth, ["status"])
    assert status.exit_code == 0
    assert "Authenticated" in status.output
    assert "keyring" in status.output
    assert "cli-secret" not in status.output

    logout = runner.invoke(auth_cmd.auth, ["logout"])
    assert logout.exit_code == 0
    assert "removed" in logout.output
    assert "cli-secret" not in logout.output


def test_auth_login_prompt_stores_token_when_environment_token_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BENCHBOX_SUBMIT_TOKEN", "env-token")
    fake = FakeKeyring()
    monkeypatch.setattr(submit_auth, "_load_keyring", lambda: fake)
    runner = CliRunner()

    result = runner.invoke(auth_cmd.auth, ["login"], input="prompt-secret\n")

    assert result.exit_code == 0, result.output
    assert "credentials saved" in result.output.lower()
    assert "prompt-secret" not in result.output
    username = submit_auth.normalize_service_url(submit_auth.DEFAULT_SERVICE_URL)
    assert fake.get_password(submit_auth.KEYRING_SERVICE_NAME, username) == "prompt-secret"


def test_auth_command_registered_on_root_cli() -> None:
    from benchbox.cli.main import cli

    result = CliRunner().invoke(cli, ["auth", "--help"])

    assert result.exit_code == 0
    assert "Manage hosted result-submission credentials" in result.output


def test_auth_login_token_help_warns_about_command_line_secret_exposure() -> None:
    result = CliRunner().invoke(auth_cmd.auth, ["login", "--help"])
    normalized = " ".join(result.output.split())

    assert result.exit_code == 0
    assert "--token" in result.output
    assert "shell history" in normalized
    assert "process listings" in normalized


def test_auth_login_token_stdin_reads_first_line_and_does_not_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BENCHBOX_SUBMIT_TOKEN", raising=False)
    monkeypatch.delenv("BENCHBOX_SERVICE_TOKEN", raising=False)
    fake = FakeKeyring()
    monkeypatch.setattr(submit_auth, "_load_keyring", lambda: fake)
    runner = CliRunner()

    result = runner.invoke(
        auth_cmd.auth,
        ["login", "--token-stdin"],
        input="stdin-secret\nignored-second-line\n",
    )

    assert result.exit_code == 0, result.output
    assert "credentials saved" in result.output.lower()
    assert "stdin-secret" not in result.output
    username = submit_auth.normalize_service_url(submit_auth.DEFAULT_SERVICE_URL)
    assert fake.get_password(submit_auth.KEYRING_SERVICE_NAME, username) == "stdin-secret"


def test_auth_login_token_stdin_rejects_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHBOX_SUBMIT_TOKEN", raising=False)
    monkeypatch.delenv("BENCHBOX_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(submit_auth, "_load_keyring", lambda: FakeKeyring())

    result = CliRunner().invoke(auth_cmd.auth, ["login", "--token-stdin"], input="\n")

    assert result.exit_code == 1
    assert "empty token" in result.output.lower()


def test_auth_login_rejects_combined_token_and_token_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHBOX_SUBMIT_TOKEN", raising=False)
    monkeypatch.delenv("BENCHBOX_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(submit_auth, "_load_keyring", lambda: FakeKeyring())

    result = CliRunner().invoke(
        auth_cmd.auth,
        ["login", "--token", "argv-secret", "--token-stdin"],
        input="stdin-secret\n",
    )

    assert result.exit_code == 1
    assert "not both" in result.output.lower()


def test_auth_store_delete_handles_missing_via_typed_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    from keyring.errors import PasswordDeleteError

    class FakeRacingKeyring:
        def __init__(self) -> None:
            self.passwords: dict[tuple[str, str], str] = {
                (
                    submit_auth.KEYRING_SERVICE_NAME,
                    submit_auth.normalize_service_url(submit_auth.DEFAULT_SERVICE_URL),
                ): "x"
            }

        def get_password(self, service_name: str, username: str) -> str | None:
            return self.passwords.get((service_name, username))

        def set_password(self, service_name: str, username: str, password: str) -> None:
            self.passwords[(service_name, username)] = password

        def delete_password(self, service_name: str, username: str) -> None:

            raise PasswordDeleteError("Item not in some-locale-specific-text")

    store = submit_auth.SubmissionAuthStore(keyring_backend=FakeRacingKeyring())
    assert store.delete_token(submit_auth.DEFAULT_SERVICE_URL) is False


