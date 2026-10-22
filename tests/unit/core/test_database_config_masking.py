from __future__ import annotations

import io
import logging

import pytest

from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.schemas import DatabaseConfig

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PASSWORD = "hunter2-top-level"
OPTION_PASSWORD = "hunter2-in-options"
NESTED_KEY = "nested-api-key-value"
TOKEN = "token-value-123"
URI_PASSWORD = "uri-embedded-pw"
SECRETS = (PASSWORD, OPTION_PASSWORD, NESTED_KEY, TOKEN, URI_PASSWORD)


def _config() -> DatabaseConfig:
    return DatabaseConfig(
        type="snowflake",
        name="Snowflake",
        connection_string=f"postgres://bob:{URI_PASSWORD}@host/db",
        options={
            "account": "acme",
            "username": "bob",
            "password": OPTION_PASSWORD,
            "nested": {"api_key": NESTED_KEY, "region": "us-east-1"},
            "endpoint": f"https://bob:{URI_PASSWORD}@host/path",
        },
        password=PASSWORD,
        access_token=TOKEN,
    )


def _log_output(config: DatabaseConfig) -> str:
    stream = io.StringIO()
    logger = logging.getLogger("benchbox.test.config-masking")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.info("config=%s", config)
        logger.info("config=%r", config)
        logger.info(f"config={config}")
    finally:
        logger.removeHandler(handler)
    return stream.getvalue()


@pytest.mark.parametrize(
    "render",
    [repr, str, lambda config: f"{config}", lambda config: f"{config!r}", _log_output],
    ids=["repr", "str", "format", "format-repr", "logging"],
)
def test_no_rendering_of_a_config_shows_a_secret(render) -> None:
    rendered = render(_config())
    for secret in SECRETS:
        assert secret not in rendered


def test_the_rendering_still_identifies_the_config() -> None:
    rendered = repr(_config())
    assert "snowflake" in rendered
    assert "acme" in rendered
    assert "bob" in rendered
    assert "us-east-1" in rendered
    assert "<redacted>" in rendered


def test_the_connection_code_still_reads_the_real_values() -> None:
    config = _config()
    assert config.options["password"] == OPTION_PASSWORD
    assert config.options["nested"]["api_key"] == NESTED_KEY
    assert config.password == PASSWORD
    assert config.access_token == TOKEN
    assert config.connection_string is not None and URI_PASSWORD in config.connection_string
    dumped = config.model_dump()
    assert dumped["options"]["password"] == OPTION_PASSWORD
    assert dumped["password"] == PASSWORD


def test_an_unset_secret_is_not_shown_as_redacted() -> None:
    rendered = repr(DatabaseConfig(type="snowflake", name="Snowflake", password=None, options={"password": None}))
    assert "password=<redacted>" not in rendered
    assert "password='<redacted>'" not in rendered


def test_a_config_built_from_the_registry_masks_its_saved_credentials(tmp_path, monkeypatch) -> None:
    credentials = tmp_path / ".benchbox" / "credentials.yaml"
    credentials.parent.mkdir()
    credentials.write_text(f"snowflake:\n  account: acme\n  username: bob\n  password: {PASSWORD}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    config = PlatformHookRegistry.build_database_config("snowflake", {})

    assert config.options["password"] == PASSWORD
    rendered = repr(config) + str(config) + _log_output(config)
    assert PASSWORD not in rendered
    assert "acme" in rendered
