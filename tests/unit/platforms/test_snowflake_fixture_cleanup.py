from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from tests.fixtures.platform_fixtures import mock_platform_dependency_checks

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_fixture_restores_real_snowflake_cli_options_on_teardown() -> None:
    from benchbox.platforms.snowflake import SnowflakeAdapter

    body = mock_platform_dependency_checks.__wrapped__
    outer = SnowflakeAdapter.__dict__["add_cli_arguments"]
    real = outer.__func__.real

    with patch.object(SnowflakeAdapter, "add_cli_arguments", staticmethod(real)):
        original = SnowflakeAdapter.__dict__["add_cli_arguments"]

        generator = body(None)
        next(generator)
        try:
            assert SnowflakeAdapter.__dict__["add_cli_arguments"] is not original
            parser = argparse.ArgumentParser()
            SnowflakeAdapter.add_cli_arguments(parser)
            assert parser.parse_args([]) == argparse.Namespace()
        finally:
            generator.close()
        assert SnowflakeAdapter.__dict__["add_cli_arguments"] is original
        parser = argparse.ArgumentParser()
        SnowflakeAdapter.add_cli_arguments(parser)
        args = parser.parse_args(
            [
                "--account",
                "offline-account",
                "--warehouse",
                "offline-warehouse",
                "--schema",
                "offline-schema",
                "--private-key-path",
                "/offline/key.pem",
                "--modify-warehouse-settings",
            ]
        )
        assert args.account == "offline-account"
        assert args.warehouse == "offline-warehouse"
        assert args.schema == "offline-schema"
        assert args.private_key_path == "/offline/key.pem"
        assert args.modify_warehouse_settings is True
    assert SnowflakeAdapter.__dict__["add_cli_arguments"] is outer


