import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_cli_version_flag_displays_version_report():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert "BenchBox Version:" in result.output
    assert "Version Consistency:" in result.output
    assert "Python Version:" in result.output


