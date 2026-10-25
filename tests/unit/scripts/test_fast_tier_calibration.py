from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import fast_tier_calibration as calibration

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_workload_is_deterministic_and_depends_on_iterations() -> None:
    assert calibration.run_workload(1000) == calibration.run_workload(1000)
    assert calibration.run_workload(1000) != calibration.run_workload(1001)


def test_measure_workload_reports_every_repeat_and_the_best() -> None:
    result = calibration.measure_workload(iterations=1000, repeats=3)

    assert result["iterations"] == 1000
    assert result["checksum"] == calibration.run_workload(1000)
    assert len(result["run_seconds"]) == 3
    assert result["best_seconds"] == min(result["run_seconds"])


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
