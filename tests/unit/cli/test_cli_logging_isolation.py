from __future__ import annotations

import logging

import click
import pytest
from click.testing import CliRunner

from benchbox.cli.verbose_logging import setup_verbose_logging

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@click.command()
def _quiet_command() -> None:
    setup_verbose_logging(0, quiet=True)


def test_cli_invocation_does_not_leak_logging_configuration(caplog: pytest.LogCaptureFixture) -> None:
    root = logging.getLogger()
    level_before = root.level
    handlers_before = list(root.handlers)

    result = CliRunner().invoke(_quiet_command)

    assert result.exit_code == 0
    assert root.level == level_before
    assert root.handlers == handlers_before
    with caplog.at_level(logging.WARNING):
        logging.getLogger("benchbox.core.example").warning("still captured")
    assert "still captured" in caplog.text


