from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli
from tests.integration._cli_e2e_utils import run_cli_command as run_cli_subprocess_command

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
]

_CLI_RUNNER = CliRunner()


def run_cli_command(args: Sequence[str], *, use_subprocess: bool = False) -> SimpleNamespace:
    if use_subprocess:
        result = run_cli_subprocess_command(list(args))
        return SimpleNamespace(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)

    result = _CLI_RUNNER.invoke(cli, list(args), env={"BENCHBOX_NON_INTERACTIVE": "true"})
    return SimpleNamespace(returncode=result.exit_code, stdout=result.output, stderr="")


class TestTPCDSConstraints:
    @pytest.mark.tpcds
    def test_tpcds_fractional_scale_warns_but_succeeds(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpcds",
                "--scale",
                "0.5",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode == 0
        assert "UNOFFICIAL SUBSCALE RUN" in result.stdout

    @pytest.mark.tpcds
    def test_tpcds_fractional_scale_quiet_suppresses_warning(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpcds",
                "--scale",
                "0.5",
                "--quiet",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode == 0
        assert "UNOFFICIAL SUBSCALE RUN" not in result.stdout


