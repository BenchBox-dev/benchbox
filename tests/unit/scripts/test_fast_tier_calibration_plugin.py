from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]
ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("workers", [0, 2])
def test_plugin_records_one_collection_time_per_collecting_process(tmp_path: Path, workers: int) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_sample.py").write_text("def test_one():\n    pass\n", encoding="utf-8")
    timing = tmp_path / "timing" / "collection.jsonl"
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(suite),
        "-p",
        "scripts.fast_tier_calibration",
        "--collection-timing",
        str(timing),
        "-n",
        str(workers),
        "-q",
        "--rootdir",
        str(suite),
        "-c",
        os.devnull,
    ]
    env = {**os.environ, "PYTHONPATH": str(ROOT)}

    result = subprocess.run(command, cwd=suite, env=env, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    lines = [json.loads(line) for line in timing.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == (workers or 1)
    assert {line["process"] for line in lines} == ({"main"} if workers == 0 else {"gw0", "gw1"})
    assert all(line["seconds"] >= 0 for line in lines)


def test_loading_the_plugin_does_not_import_benchbox() -> None:
    probe = "import sys; import scripts.fast_tier_calibration; sys.exit(int('benchbox' in sys.modules))"
    env = {**os.environ, "PYTHONPATH": str(ROOT)}

    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
