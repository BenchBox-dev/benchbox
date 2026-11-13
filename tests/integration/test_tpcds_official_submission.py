from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
]


def test_official_tpcds_run_produces_a_submittable_bundle(tmp_path: Path) -> None:

    output_root = tmp_path / "benchmark-runs"
    env = {
        "BENCHBOX_OUTPUT_DIR": str(output_root),
        "BENCHBOX_MACHINE_ID_SALT": "integration-test-salt",
    }

    run = run_cli_command(
        [
            "run",
            "--official",
            "--seed",
            "42",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpcds",
            "--scale",
            "1",
            "--phases",
            "load,power,throughput,maintenance",
            "--streams",
            "4",
            "--iterations",
            "1",
            "--no-progress",
            "--non-interactive",
        ],
        env=env,
        timeout=600.0,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    result_files = sorted((output_root / "results").glob("*.json"))
    assert len(result_files) == 1, f"Expected one result bundle, found {result_files}"

    payload = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert payload["benchmark"]["compliance_class"] == "official"
    assert payload["benchmark"]["scale_factor"] == 1.0

    submit = run_cli_command(
        [
            "submit",
            str(result_files[0]),
            "--output",
            str(tmp_path / "submission"),
            "--dry-run",
        ],
        env=env,
    )

    assert submit.returncode == 0, submit.stdout + submit.stderr
    assert "Dry-run preview" in submit.stdout
    assert "Submission refused" not in submit.stdout
