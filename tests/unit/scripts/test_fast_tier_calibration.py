from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import fast_tier_calibration as calibration

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.unit
@pytest.mark.fast
def test_workload_is_deterministic_and_depends_on_iterations() -> None:
    assert calibration.run_workload(1000) == calibration.run_workload(1000)
    assert calibration.run_workload(1000) != calibration.run_workload(1001)


@pytest.mark.unit
@pytest.mark.fast
def test_measure_workload_reports_every_repeat_and_the_best() -> None:
    result = calibration.measure_workload(iterations=1000, repeats=3)

    assert result["iterations"] == 1000
    assert result["checksum"] == calibration.run_workload(1000)
    assert len(result["run_seconds"]) == 3
    assert result["best_seconds"] == min(result["run_seconds"])


@pytest.mark.unit
@pytest.mark.fast
def test_record_aggregates_collection_lines_and_tolerates_a_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calibration, "CALIBRATION_ITERATIONS", 1000)
    collection = tmp_path / "collection.jsonl"
    collection.write_text(
        '{"process": "gw0", "seconds": 1.5}\n{"process": "gw1", "seconds": 2.5}\n',
        encoding="utf-8",
    )
    output = tmp_path / "out" / "calibration.json"

    assert calibration.main(["--collection-file", str(collection), "--output", str(output)]) == 0
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["schema"] == 1
    assert record["collection"] == {"processes": 2, "max_seconds": 2.5, "seconds": [1.5, 2.5]}
    assert record["workload"]["best_seconds"] >= 0

    missing = tmp_path / "missing.jsonl"
    assert calibration.build_record(missing)["collection"] == {"processes": 0, "max_seconds": None, "seconds": []}


@pytest.mark.unit
@pytest.mark.medium
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
