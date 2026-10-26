from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_fast_duration_baseline as baseline

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GENERATED_AT = "2026-10-25T00:00:00Z"


def write_run(
    tmp_path: Path,
    name: str,
    *,
    best_seconds: object = 0.5,
    cases: tuple[tuple[str, float], ...] = (("test_alpha", 1.0), ("test_beta", 3.0)),
    calibration: bool = True,
) -> Path:
    run_dir = tmp_path / name
    run_dir.mkdir()
    lines = ['<testsuite name="fast">']
    for case, duration in cases:
        lines.append(
            f'<testcase classname="tests.test_sample.TestSample" file="tests/test_sample.py" '
            f'name="{case}" time="{duration}" />'
        )
    lines.append("</testsuite>")
    (run_dir / "junit.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if calibration:
        (run_dir / "calibration.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "workload": {"best_seconds": best_seconds},
                    "python": "3.12.0",
                    "cpu_count": 4,
                }
            ),
            encoding="utf-8",
        )
    return run_dir


def write_manifest(tmp_path: Path, run_dirs: list[Path]) -> Path:
    manifest = tmp_path / "manifest.json"
    entries = [
        {
            "run_id": 100 + index,
            "pr": 3500 + index,
            "sha": f"sha{index}",
            "artifact_id": 200 + index,
            "dir": str(run_dir),
        }
        for index, run_dir in enumerate(run_dirs)
    ]
    manifest.write_text(json.dumps(entries), encoding="utf-8")
    return manifest


def build_fixture(tmp_path: Path) -> tuple[Path, list[Path]]:
    runs = [
        write_run(tmp_path, "run-a", best_seconds=0.5, cases=(("test_alpha", 1.0), ("test_beta", 3.0))),
        write_run(tmp_path, "run-b", best_seconds=1.0, cases=(("test_alpha", 2.0), ("test_beta", 6.0))),
    ]
    return write_manifest(tmp_path, runs), runs


def test_build_normalizes_by_runner_speed_factor(tmp_path: Path) -> None:
    manifest, _runs = build_fixture(tmp_path)

    built, rejected = baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)

    assert rejected == []
    assert built["schema"] == 1
    assert built["generated_at"] == GENERATED_AT
    assert built["reference_calibration_best_seconds"] == pytest.approx(0.75)
    factors = {run["run_id"]: run["speed_factor"] for run in built["runs"]}
    assert factors[100] == pytest.approx(1.5)
    assert factors[101] == pytest.approx(0.75)
    tests = {record["id"]: record["p50_seconds"] for record in built["tests"]}
    assert [record["id"] for record in built["tests"]] == sorted(tests)
    assert tests["tests/test_sample.py::TestSample::test_alpha"] == pytest.approx(1.5)
    assert tests["tests/test_sample.py::TestSample::test_beta"] == pytest.approx(4.5)


def test_build_is_deterministic_for_the_same_inputs(tmp_path: Path) -> None:
    manifest, _runs = build_fixture(tmp_path)
    first, _ = baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)
    second, _ = baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)

    assert json.dumps(first, indent=2, sort_keys=True) == json.dumps(second, indent=2, sort_keys=True)


def test_build_records_runs_missing_calibration_as_rejected(tmp_path: Path) -> None:
    good = write_run(tmp_path, "good", best_seconds=0.5)
    bad = write_run(tmp_path, "bad", calibration=False)
    manifest = write_manifest(tmp_path, [good, bad])

    built, rejected = baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)

    assert len(built["runs"]) == 1
    assert len(rejected) == 1
    assert rejected[0]["run_id"] == 101
    assert "calibration" in rejected[0]["reason"]
    assert built["rejected_count"] == 1
    assert built["rejected_runs"] == rejected


@pytest.mark.parametrize("best", [0, -0.5, "fast", None, True])
def test_build_rejects_non_positive_or_non_numeric_calibration(tmp_path: Path, best: object) -> None:
    run_dir = write_run(tmp_path, "run", best_seconds=best)
    manifest = write_manifest(tmp_path, [run_dir])

    with pytest.raises(baseline.BaselineError, match="calibration"):
        baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)


def test_build_fails_when_every_run_is_missing_calibration(tmp_path: Path) -> None:
    bad = write_run(tmp_path, "bad", calibration=False)
    manifest = write_manifest(tmp_path, [bad])

    with pytest.raises(baseline.BaselineError, match="no usable source runs"):
        baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)


def test_check_reports_gate_numbers(tmp_path: Path) -> None:
    manifest, _runs = build_fixture(tmp_path)
    built, _ = baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)
    collected = {
        "tests/test_sample.py::TestSample::test_alpha",
        "tests/test_sample.py::TestSample::test_beta",
        "tests/test_sample.py::TestSample::test_gamma",
    }

    report = baseline.check_baseline(built, baseline.load_manifest(manifest), collected)

    assert report["baseline_sum_seconds"] == pytest.approx(6.0)
    assert report["median_run_sum_seconds"] == pytest.approx(6.0)
    assert report["sum_ratio"] == pytest.approx(1.0)
    assert report["sum_within_tolerance"] is True
    assert report["coverage"] == pytest.approx(2 / 3)
    assert report["coverage_within_minimum"] is False


def test_check_command_writes_report(tmp_path: Path) -> None:
    manifest, _runs = build_fixture(tmp_path)
    built, _ = baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)
    baseline_dir = tmp_path / "baseline"
    baseline.write_sharded_baseline(baseline_dir, built)
    ids_path = tmp_path / "ids.txt"
    ids_path.write_text(
        "tests/test_sample.py::TestSample::test_alpha\ntests/test_sample.py::TestSample::test_beta\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"

    assert (
        baseline.main(
            [
                "check",
                "--baseline",
                str(baseline_dir),
                "--manifest",
                str(manifest),
                "--collected-ids",
                str(ids_path),
                "--report-out",
                str(report_path),
            ]
        )
        == 0
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["coverage"] == pytest.approx(1.0)
    assert report["coverage_within_minimum"] is True


def test_medium_drift_recommends_refresh_above_threshold(tmp_path: Path) -> None:
    recorded = {
        "tests/test_a.py::test_one": 1.0,
        "tests/test_a.py::test_two": 3.0,
    }
    measured = {
        "shard 0": {"tests/test_a.py::test_one": 1.0, "tests/test_a.py::test_two": 3.0},
        "shard 1": {"tests/test_a.py::test_one": 2.0, "tests/test_a.py::test_two": 6.0},
    }

    report = baseline.medium_drift_report(recorded, measured)

    assert report["matched_test_count"] == 4
    assert report["overall_measured_test_count"] == 4
    assert report["overall_median_ratio_skew"] == pytest.approx(0.5)
    assert report["recommends_refresh"] is True
    assert "Matched tests: 4 of 4 measured" in baseline.drift_markdown(report)


def test_medium_drift_stays_quiet_within_threshold() -> None:
    recorded = {"tests/test_a.py::test_one": 1.0}
    measured = {"shard 0": {"tests/test_a.py::test_one": 1.05}}

    report = baseline.medium_drift_report(recorded, measured)

    assert report["recommends_refresh"] is False


def test_medium_refresh_takes_p95_across_runs_and_round_trips(tmp_path: Path) -> None:
    run_dirs = []
    for run_index, duration in enumerate((1.0, 3.0)):
        shard_dir = tmp_path / f"run{run_index}" / "t2-medium-shard-0-abc"
        shard_dir.mkdir(parents=True)
        (shard_dir / "shard-0-junit.xml").write_text(
            '<testsuite><testcase classname="tests.test_sample.TestSample" file="tests/test_sample.py" '
            f'name="test_alpha" time="{duration}"/></testsuite>',
            encoding="utf-8",
        )
        run_dirs.append(str(tmp_path / f"run{run_index}"))
    output = tmp_path / "durations.json"
    argv = ["medium-refresh", "--output", str(output), "--source", "two runs", "--generated-at", GENERATED_AT]
    for run_dir in run_dirs:
        argv += ["--run-dir", run_dir]

    assert baseline.main(argv) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["source"] == "two runs"
    assert payload["tests"] == {"tests/test_sample.py::TestSample::test_alpha": {"p95_seconds": 2.9}}
    assert payload["default_seconds"] == pytest.approx(2.9)
    assert baseline.read_medium_recorded(output) == {"tests/test_sample.py::TestSample::test_alpha": 2.9}


def test_medium_refresh_rejects_a_run_without_junit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shard_dir = tmp_path / "run0" / "t2-medium-shard-0-abc"
    shard_dir.mkdir(parents=True)
    (shard_dir / "shard-0-junit.xml").write_text(
        '<testsuite><testcase classname="tests.test_sample.TestSample" file="tests/test_sample.py" '
        'name="test_alpha" time="1.0"/></testsuite>',
        encoding="utf-8",
    )
    (tmp_path / "empty").mkdir()
    output = tmp_path / "durations.json"
    argv = ["medium-refresh", "--output", str(output), "--source", "x"]
    argv += ["--run-dir", str(tmp_path / "run0"), "--run-dir", str(tmp_path / "empty")]

    assert baseline.main(argv) == 1

    assert "run 1 has no usable testcase samples" in capsys.readouterr().err
    assert not output.exists()


def test_build_command_round_trips_through_subprocess(tmp_path: Path) -> None:
    manifest, _runs = build_fixture(tmp_path)
    output = tmp_path / "baseline"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.build_fast_duration_baseline",
            "build",
            "--manifest",
            str(manifest),
            "--output",
            str(output),
            "--generated-at",
            GENERATED_AT,
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[3],
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = baseline.read_sharded_baseline(output)
    assert payload["schema"] == 1
    assert len(payload["tests"]) == 2


def test_sharded_baseline_round_trips_and_shards_by_stable_hash(tmp_path: Path) -> None:
    manifest, _runs = build_fixture(tmp_path)
    built, _ = baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)

    first = tmp_path / "first"
    second = tmp_path / "second"
    baseline.write_sharded_baseline(first, built)
    baseline.write_sharded_baseline(second, built)

    for index in range(baseline.TEST_SHARD_COUNT):
        name = f"tests-{index:02d}.json"
        assert (first / name).read_bytes() == (second / name).read_bytes()
    reread = baseline.read_sharded_baseline(first)
    assert baseline.baseline_tests_map(reread) == baseline.baseline_tests_map(built)
    assert reread["test_shard_count"] == baseline.TEST_SHARD_COUNT


def test_added_test_only_touches_its_own_shard(tmp_path: Path) -> None:
    manifest, runs = build_fixture(tmp_path)
    built, _ = baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)
    before_dir = tmp_path / "before"
    baseline.write_sharded_baseline(before_dir, built)
    extra = write_run(
        tmp_path,
        "run-c",
        best_seconds=0.75,
        cases=(("test_alpha", 1.5), ("test_beta", 4.5), ("test_gamma", 9.0)),
    )
    grown_manifest = write_manifest(tmp_path, [*runs, extra])
    grown, _ = baseline.build_baseline(baseline.load_manifest(grown_manifest), GENERATED_AT)
    after_dir = tmp_path / "after"
    baseline.write_sharded_baseline(after_dir, grown)
    changed = [
        index
        for index in range(baseline.TEST_SHARD_COUNT)
        if (before_dir / f"tests-{index:02d}.json").read_bytes() != (after_dir / f"tests-{index:02d}.json").read_bytes()
    ]
    assert changed == [baseline.test_shard_index("tests/test_sample.py::TestSample::test_gamma")]


def test_check_command_fails_when_a_gate_fails(tmp_path: Path) -> None:
    manifest, _runs = build_fixture(tmp_path)
    built, _ = baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)
    baseline_dir = tmp_path / "baseline"
    baseline.write_sharded_baseline(baseline_dir, built)
    ids_path = tmp_path / "ids.txt"
    ids_path.write_text("tests/other.py::test_missing\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    status = baseline.main(
        [
            "check",
            "--baseline",
            str(baseline_dir),
            "--manifest",
            str(manifest),
            "--collected-ids",
            str(ids_path),
            "--report-out",
            str(report_path),
        ]
    )

    assert status != 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["coverage_within_minimum"] is False


def test_verify_threshold_and_mode_defaults() -> None:
    assert baseline.VERIFY_MODE == "report"
    assert baseline.VERIFY_RATIO_THRESHOLD == 0.30
    assert baseline.VERIFY_DELTA_SECONDS == 360.0
    assert baseline.VERIFY_NEW_TEST_ALLOWANCE_SECONDS == 0.5
