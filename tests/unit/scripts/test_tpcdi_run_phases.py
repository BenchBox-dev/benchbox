from __future__ import annotations

from pathlib import Path

import pytest

from scripts.tpcdi import run_phases

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        ("FAILED", 1),
        ("ERROR", 1),
        ("PARTIAL", 1),
        ("TIMED_OUT", 1),
        ("interrupted", 1),
        ("NOT_VALIDATED", 0),
    ],
)
def test_exit_code_fails_every_cli_failure_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, exit_code: int
) -> None:
    summary = {"validation_status": status, "validation_details": {"tpcdi_audit": {"tests": []}}}
    monkeypatch.setattr(run_phases, "run", lambda args: summary)
    argv = [
        "--scale-factor",
        "3",
        "--staging",
        str(tmp_path / "digen"),
        "--database",
        str(tmp_path / "tpcdi.duckdb"),
        "--output",
        str(tmp_path / "result.json"),
    ]

    assert run_phases.main(argv) == exit_code
