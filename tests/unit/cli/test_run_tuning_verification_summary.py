from __future__ import annotations

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_duckdb_tuned_run_prints_tuning_verification_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path))

    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--tuning",
            "tuned",
            "--scale",
            "0.01",
            "--queries",
            "1",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Tuning verification: applied_unverified" in result.output
    assert "Verdicts:" in result.output
    assert "Top reasons:" in result.output
    assert "Dropped intents" in result.output
