from __future__ import annotations

import io
import json
import re
import stat
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def test_medium_drift_reports_stale_and_unrecorded_ids_and_shard_balance() -> None:
    recorded = {"tests/test_a.py::test_one": 1.0, "tests/test_a.py::test_gone": 2.0}
    measured = {
        "shard 0": {"tests/test_a.py::test_one": 1.0, "tests/test_b.py::test_new": 2.0},
        "shard 1": {"tests/test_c.py::test_new": 1.0},
    }

    report = baseline.medium_drift_report(recorded, measured)

    assert report["stale_ids"] == ["tests/test_a.py::test_gone"]
    assert report["unrecorded_test_count"] == 2
    assert report["unrecorded_measured_seconds"] == pytest.approx(3.0)
    assert report["max_to_mean_shard_seconds"] == pytest.approx(3.0 / 2.0)
    text = baseline.drift_markdown(report)
    assert "Stale recorded ids (recorded, not measured): 1" in text
    assert "- `tests/test_a.py::test_gone`" in text
    assert "Unrecorded measured tests: 2 (3.00 s measured)" in text
    assert "Busiest shard measured time / average: 1.500" in text


def test_drift_markdown_caps_the_stale_list() -> None:
    recorded = {f"tests/test_a.py::test_{index}": 1.0 for index in range(60)}
    measured = {"shard 0": {"tests/test_a.py::test_other": 1.0}}

    text = baseline.drift_markdown(baseline.medium_drift_report(recorded, measured))

    assert "Stale recorded ids (recorded, not measured): 60" in text
    assert text.count("- `tests/test_a.py::test_") == 50
    assert "- ... and 10 more" in text


def test_medium_drift_projects_the_ratio_with_refreshed_durations(tmp_path: Path) -> None:
    ids = [f"tests/test_{name}.py::test_x" for name in "abcd"]
    measured = {"shard 0": {ids[0]: 8.0, ids[1]: 8.0, ids[2]: 1.0, ids[3]: 1.0}}
    durations = tmp_path / "durations.json"
    durations.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_seconds": 0.01,
                "tests": {
                    ids[0]: {"p95_seconds": 8.0},
                    ids[1]: {"p95_seconds": 8.0},
                    ids[2]: {"p95_seconds": 1.0},
                    ids[3]: {"p95_seconds": 1.0},
                },
            }
        ),
        encoding="utf-8",
    )

    assert baseline.projected_max_to_mean(durations, set(ids), measured) == pytest.approx(8.0 / 4.5)


def test_medium_refresh_drops_ids_missing_from_the_collected_list(tmp_path: Path) -> None:
    shard_dir = tmp_path / "run0" / "t2-medium-shard-0-abc"
    shard_dir.mkdir(parents=True)
    (shard_dir / "shard-0-junit.xml").write_text(
        "<testsuite>"
        '<testcase classname="tests.test_sample.TestSample" file="tests/test_sample.py" name="test_kept" time="1.0"/>'
        '<testcase classname="tests.test_sample.TestSample" file="tests/test_sample.py" name="test_deleted" time="2.0"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    collected = tmp_path / "collected.txt"
    collected.write_text("tests/test_sample.py::TestSample::test_kept\n", encoding="utf-8")
    output = tmp_path / "durations.json"
    argv = ["medium-refresh", "--output", str(output), "--source", "x", "--run-dir", str(tmp_path / "run0")]

    assert baseline.main([*argv, "--collected", str(collected)]) == 0

    assert list(json.loads(output.read_text(encoding="utf-8"))["tests"]) == [
        "tests/test_sample.py::TestSample::test_kept"
    ]


def test_medium_refresh_fails_when_the_collected_list_matches_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shard_dir = tmp_path / "run0" / "t2-medium-shard-0-abc"
    shard_dir.mkdir(parents=True)
    (shard_dir / "shard-0-junit.xml").write_text(
        '<testsuite><testcase classname="tests.test_sample.TestSample" file="tests/test_sample.py" '
        'name="test_alpha" time="1.0"/></testsuite>',
        encoding="utf-8",
    )
    collected = tmp_path / "collected.txt"
    collected.write_text("tests/other.py::test_x\n", encoding="utf-8")
    output = tmp_path / "durations.json"
    argv = ["medium-refresh", "--output", str(output), "--source", "x", "--run-dir", str(tmp_path / "run0")]

    assert baseline.main([*argv, "--collected", str(collected)]) == 1

    assert "no measured test is listed in the collected node ids" in capsys.readouterr().err
    assert not output.exists()


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
    assert baseline.VERIFY_MODE == "block"
    assert baseline.VERIFY_RATIO_THRESHOLD == 0.30
    assert baseline.VERIFY_DELTA_SECONDS == 360.0
    assert baseline.VERIFY_NEW_TEST_ALLOWANCE_SECONDS == 0.5


def medium_payload(tests: dict[str, float], default: float = 1.0) -> dict[str, object]:
    return {
        "schema_version": 1,
        "generated_at": GENERATED_AT,
        "source": "old",
        "default_seconds": default,
        "tests": {nodeid: {"p95_seconds": value} for nodeid, value in tests.items()},
    }


@pytest.mark.parametrize(
    ("old", "new", "kept"),
    [
        (10.0, 11.9, True),
        (10.0, 12.1, False),
        (10.0, 8.1, True),
        (10.0, 7.9, False),
        (0.5, 0.59, True),
        (0.5, 0.61, False),
        (0.2, 0.29, True),
        (0.2, 0.31, False),
        (0.5, 0.7, False),
        (0.5, 0.4, False),
    ],
)
def test_medium_hysteresis_keeps_old_value_unless_both_thresholds_are_met(old: float, new: float, kept: bool) -> None:
    result = baseline.apply_medium_hysteresis(medium_payload({"t": old}), medium_payload({"t": new}))

    assert result["tests"]["t"]["p95_seconds"] == (old if kept else new)


def test_medium_hysteresis_applies_both_thresholds_independently() -> None:
    small_relative = baseline.apply_medium_hysteresis(medium_payload({"t": 100.0}), medium_payload({"t": 105.0}))
    small_absolute = baseline.apply_medium_hysteresis(medium_payload({"t": 0.05}), medium_payload({"t": 0.09}))
    assert small_relative["tests"]["t"]["p95_seconds"] == 100.0
    assert small_absolute["tests"]["t"]["p95_seconds"] == 0.05


def test_medium_hysteresis_keeps_additions_and_removals_exact() -> None:
    previous = medium_payload({"kept": 1.0, "removed": 2.0})
    fresh = medium_payload({"kept": 1.01, "added": 0.0123})

    result = baseline.apply_medium_hysteresis(previous, fresh)

    assert result["tests"] == {"kept": {"p95_seconds": 1.0}, "added": {"p95_seconds": 0.0123}}


def test_medium_hysteresis_keeps_default_unless_it_moves_twenty_percent() -> None:
    previous = medium_payload({"t": 1.0}, default=1.0)
    assert (
        baseline.apply_medium_hysteresis(previous, medium_payload({"t": 1.0}, default=1.19))["default_seconds"] == 1.0
    )
    assert (
        baseline.apply_medium_hysteresis(previous, medium_payload({"t": 1.0}, default=1.21))["default_seconds"] == 1.21
    )


def test_medium_hysteresis_without_previous_returns_fresh() -> None:
    fresh = medium_payload({"t": 1.0})
    assert baseline.apply_medium_hysteresis(None, fresh) is fresh


def test_medium_render_matches_the_committed_layout_byte_for_byte() -> None:
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "medium_shard_durations.json"
    text = fixture.read_text(encoding="utf-8")

    assert baseline.render_medium_durations(json.loads(text)) == text


def _write_medium_run(tmp_path: Path, name: str, cases: dict[str, float]) -> Path:
    shard_dir = tmp_path / name / "t2-medium-shard-0-abc"
    shard_dir.mkdir(parents=True)
    body = "".join(
        f'<testcase classname="tests.test_sample.TestSample" file="tests/test_sample.py" name="{case}" time="{value}"/>'
        for case, value in cases.items()
    )
    (shard_dir / "shard-0-junit.xml").write_text(f"<testsuite>{body}</testsuite>", encoding="utf-8")
    return tmp_path / name


def test_medium_refresh_leaves_the_file_untouched_when_only_jitter_moved(tmp_path: Path) -> None:
    output = tmp_path / "durations.json"
    first = _write_medium_run(tmp_path, "run0", {"test_alpha": 1.0, "test_beta": 2.0})
    assert baseline.main(["medium-refresh", "--output", str(output), "--source", "a", "--run-dir", str(first)]) == 0
    before = output.read_text(encoding="utf-8")
    second = _write_medium_run(tmp_path, "run1", {"test_alpha": 1.05, "test_beta": 2.02})

    argv = ["medium-refresh", "--output", str(output), "--source", "b", "--run-dir", str(second)]
    assert baseline.main([*argv, "--generated-at", "2027-01-01T00:00:00Z"]) == 0

    assert output.read_text(encoding="utf-8") == before


def test_medium_refresh_rewrites_header_and_moved_tests_only(tmp_path: Path) -> None:
    output = tmp_path / "durations.json"
    first = _write_medium_run(tmp_path, "run0", {"test_alpha": 1.0, "test_beta": 2.0})
    assert baseline.main(["medium-refresh", "--output", str(output), "--source", "a", "--run-dir", str(first)]) == 0
    second = _write_medium_run(tmp_path, "run1", {"test_alpha": 1.05, "test_beta": 4.0, "test_gamma": 0.5})

    assert baseline.main(["medium-refresh", "--output", str(output), "--source", "b", "--run-dir", str(second)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source"] == "b"
    assert payload["tests"] == {
        "tests/test_sample.py::TestSample::test_alpha": {"p95_seconds": 1.0},
        "tests/test_sample.py::TestSample::test_beta": {"p95_seconds": 4.0},
        "tests/test_sample.py::TestSample::test_gamma": {"p95_seconds": 0.5},
    }


def test_refreshed_medium_file_loads_in_every_consumer(tmp_path: Path) -> None:
    from scripts.release_canary_sharding import load_durations

    output = tmp_path / "durations.json"
    run = _write_medium_run(tmp_path, "run0", {"test_alpha": 1.0, "test_beta": 2.0})
    assert baseline.main(["medium-refresh", "--output", str(output), "--source", "a", "--run-dir", str(run)]) == 0

    durations = load_durations(output)

    assert durations.weight("tests/test_sample.py::TestSample::test_beta") == 2.0
    assert baseline.read_medium_recorded(output) == {
        "tests/test_sample.py::TestSample::test_alpha": 1.0,
        "tests/test_sample.py::TestSample::test_beta": 2.0,
    }


def write_ceiling(
    path: Path, seconds: object = 100.0, *, reason: object = "Provisional", reference: object = 0.5
) -> Path:
    payload = {
        "schema": 1,
        "ceiling_seconds": seconds,
        "reference_calibration_best_seconds": reference,
        "reason": reason,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_projected_total_under_the_ceiling_passes() -> None:
    base = {"a": 10.0, "b": 20.0}
    report = baseline.verify_durations(base, dict(base), ceiling_seconds=31.0)
    assert report["verdict"] == "pass"
    assert report["projected_sum_seconds"] == 30.0
    assert report["ceiling_seconds"] == 31.0
    assert report["over_ceiling"] is False


def test_projected_total_over_the_ceiling_blocks_without_the_ratio_rule() -> None:
    base = {"a": 10.0, "b": 20.0}
    report = baseline.verify_durations(base, dict(base), ceiling_seconds=29.0)
    assert report["verdict"] == "would-block"
    assert report["over_ceiling"] is True
    assert report["ratio_rule_fired"] is False


def test_shared_tests_use_baseline_values_not_the_pr_measurement() -> None:
    base = {"a": 10.0, "b": 20.0}
    report = baseline.verify_durations(base, {"a": 1.0, "b": 2.0, "c": 5.0}, ceiling_seconds=34.0)
    assert report["projected_sum_seconds"] == 35.0
    assert report["verdict"] == "would-block"


def test_removed_tests_do_not_count_toward_the_projection() -> None:
    base = {"a": 10.0, "b": 20.0}
    report = baseline.verify_durations(base, {"a": 10.0}, ceiling_seconds=10.0)
    assert report["projected_sum_seconds"] == 10.0
    assert report["over_ceiling"] is False


def test_no_ceiling_never_flags_over() -> None:
    report = baseline.verify_durations({"a": 10.0}, {"a": 10.0})
    assert report["over_ceiling"] is False
    assert report["ceiling_seconds"] is None


def test_read_ceiling_accepts_a_valid_file(tmp_path: Path) -> None:
    assert baseline.read_ceiling(write_ceiling(tmp_path / "c.json", 12, reference=0.4)) == (12.0, 0.4)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": 2, "ceiling_seconds": 1, "reference_calibration_best_seconds": 1, "reason": "x"},
        {"ceiling_seconds": 1, "reference_calibration_best_seconds": 1, "reason": "x"},
        {"schema": 1, "ceiling_seconds": True, "reference_calibration_best_seconds": 1, "reason": "x"},
        {"schema": 1, "ceiling_seconds": "5", "reference_calibration_best_seconds": 1, "reason": "x"},
        {"schema": 1, "ceiling_seconds": 0, "reference_calibration_best_seconds": 1, "reason": "x"},
        {"schema": 1, "ceiling_seconds": -3, "reference_calibration_best_seconds": 1, "reason": "x"},
        {"schema": 1, "ceiling_seconds": float("inf"), "reference_calibration_best_seconds": 1, "reason": "x"},
        {"schema": 1, "ceiling_seconds": float("nan"), "reference_calibration_best_seconds": 1, "reason": "x"},
        {"schema": 1, "ceiling_seconds": 5, "reason": "x"},
        {"schema": 1, "ceiling_seconds": 5, "reference_calibration_best_seconds": 0, "reason": "x"},
        {"schema": 1, "ceiling_seconds": 5, "reference_calibration_best_seconds": "1", "reason": "x"},
        {"schema": 1, "ceiling_seconds": 5, "reference_calibration_best_seconds": 1, "reason": "  "},
        {"schema": 1, "ceiling_seconds": 5, "reference_calibration_best_seconds": 1},
        [1],
    ],
)
def test_read_ceiling_rejects_invalid_content(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(baseline.BaselineError):
        baseline.read_ceiling(path)


def test_read_ceiling_rejects_missing_and_unparseable_files(tmp_path: Path) -> None:
    with pytest.raises(baseline.BaselineError):
        baseline.read_ceiling(tmp_path / "absent.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(baseline.BaselineError):
        baseline.read_ceiling(bad)


def verify_fixture(tmp_path: Path, seconds: float = 1.0) -> tuple[Path, Path]:
    source = write_run(tmp_path, "source", cases=cases_for(10, seconds))
    entries = [{"run_id": 1, "pr": 1, "sha": "a", "artifact_id": 1, "dir": str(source)}]
    built, _rejected = baseline.build_baseline(entries, GENERATED_AT)
    baseline_dir = tmp_path / "baseline"
    baseline.write_sharded_baseline(baseline_dir, built)
    pr_run = write_run(tmp_path, "pr", cases=cases_for(10, seconds))
    return baseline_dir, pr_run


def cases_for(count: int, seconds: float) -> tuple[tuple[str, float], ...]:
    return tuple((f"test_{index:04d}", seconds) for index in range(count))


def test_verify_run_rejects_a_missing_or_invalid_ceiling(tmp_path: Path) -> None:
    baseline_dir, pr_run = verify_fixture(tmp_path)
    report = baseline.verify_run(baseline_dir, pr_run, tmp_path / "absent.json")
    assert report["verdict"] == "rejected"
    assert "ceiling" in str(report["reason"])
    invalid = write_ceiling(tmp_path / "invalid.json", 0)
    assert baseline.verify_run(baseline_dir, pr_run, invalid)["verdict"] == "rejected"


def test_verify_cli_blocks_on_a_missing_ceiling(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline_dir, pr_run = verify_fixture(tmp_path)
    status = baseline.main(
        [
            "verify",
            "--baseline",
            str(baseline_dir),
            "--run-dir",
            str(pr_run),
            "--ceiling",
            str(tmp_path / "absent.json"),
            "--mode",
            "block",
        ]
    )
    assert status == 1
    assert "::error" in capsys.readouterr().out


def test_verify_cli_names_the_ceiling_rule_when_it_fires(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline_dir, pr_run = verify_fixture(tmp_path)
    ceiling = write_ceiling(tmp_path / "ceiling.json", 5.0)
    report_path = tmp_path / "verdict.json"
    status = baseline.main(
        [
            "verify",
            "--baseline",
            str(baseline_dir),
            "--run-dir",
            str(pr_run),
            "--ceiling",
            str(ceiling),
            "--report-out",
            str(report_path),
            "--mode",
            "block",
        ]
    )
    out = capsys.readouterr().out
    assert status == 1
    assert "::error title=verify-fast-duration::" in out
    assert "ceiling" in out.split("::error", 1)[1]
    assert "summed ratio" not in out
    assert json.loads(report_path.read_text(encoding="utf-8"))["over_ceiling"] is True
    assert "Projected fast-tier total" in out


def test_verify_cli_passes_under_the_ceiling(tmp_path: Path) -> None:
    baseline_dir, pr_run = verify_fixture(tmp_path)
    ceiling = write_ceiling(tmp_path / "ceiling.json", 1000.0)
    args = ["verify", "--baseline", str(baseline_dir), "--run-dir", str(pr_run), "--ceiling", str(ceiling)]
    assert baseline.main([*args, "--mode", "block"]) == 0


def ceiling_check_args(tmp_path: Path, ceiling_seconds: float, *extra: str, best_seconds: float = 0.5) -> list[str]:
    baseline_dir = tmp_path / "baseline"
    previous = tmp_path / "previous"
    for target, cases in (
        (baseline_dir, {"test_a": 10.0, "test_b": 5.0, "test_new": 7.0}),
        (previous, {"test_a": 4.0, "test_b": 5.0}),
    ):
        run = write_run(tmp_path, f"src-{target.name}", best_seconds=best_seconds, cases=tuple(cases.items()))
        entries = [{"run_id": 1, "pr": 1, "sha": "a", "artifact_id": 1, "dir": str(run)}]
        built, _rejected = baseline.build_baseline(entries, GENERATED_AT)
        baseline.write_sharded_baseline(target, built)
    ids = tmp_path / "ids.txt"
    ids.write_text(
        "tests/test_sample.py::TestSample::test_a\ntests/test_sample.py::TestSample::test_new\n", encoding="utf-8"
    )
    ceiling = write_ceiling(tmp_path / "ceiling.json", ceiling_seconds)
    return [
        "ceiling-check",
        "--baseline",
        str(baseline_dir),
        "--collected-ids",
        str(ids),
        "--ceiling",
        str(ceiling),
        *extra,
    ]


def test_ceiling_check_exits_zero_under_the_ceiling(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    assert baseline.main(ceiling_check_args(tmp_path, 17.0, "--report-out", str(report_path))) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["total"] == pytest.approx(17.0, rel=0.01)
    assert report["over"] is False


def test_ceiling_check_exits_three_over_the_ceiling(tmp_path: Path) -> None:
    assert baseline.main(ceiling_check_args(tmp_path, 10.0)) == 3


def test_ceiling_holds_when_a_refresh_picks_a_faster_reference_runner(tmp_path: Path) -> None:
    assert baseline.main(ceiling_check_args(tmp_path, 17.0, best_seconds=0.25)) == 3


def test_verify_scales_the_ceiling_to_the_committed_baseline_reference(tmp_path: Path) -> None:
    baseline_dir, pr_run = verify_fixture(tmp_path)
    assert baseline.verify_run(baseline_dir, pr_run, write_ceiling(tmp_path / "a.json", 11.0))["verdict"] == "pass"
    slower_reference = write_ceiling(tmp_path / "b.json", 11.0, reference=1.0)
    assert baseline.verify_run(baseline_dir, pr_run, slower_reference)["verdict"] == "would-block"


def test_ceiling_check_exits_one_on_an_invalid_ceiling(tmp_path: Path) -> None:
    args = ceiling_check_args(tmp_path, 10.0)
    write_ceiling(tmp_path / "ceiling.json", -1)
    assert baseline.main(args) == 1


def test_ceiling_check_lists_the_files_that_grew_most(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    markdown_path = tmp_path / "ceiling.md"
    args = ceiling_check_args(
        tmp_path,
        10.0,
        "--previous",
        str(tmp_path / "previous"),
        "--report-out",
        str(report_path),
        "--markdown-out",
        str(markdown_path),
    )
    assert baseline.main(args) == 3
    growth = json.loads(report_path.read_text(encoding="utf-8"))["top_growth_files"]
    assert growth[0]["file"] == "tests/test_sample.py"
    assert growth[0]["growth_seconds"] > 0
    assert "tests/test_sample.py" in markdown_path.read_text(encoding="utf-8")


def test_ceiling_growth_counts_absent_files_from_zero_and_keeps_fifteen() -> None:
    current = {f"tests/f{index:02d}.py::t": float(index + 1) for index in range(20)}
    previous = {"tests/f19.py::t": 20.0}
    growth = baseline.ceiling_growth(current, previous, set(current))
    assert len(growth) == 15
    assert growth[0]["file"] == "tests/f18.py"
    assert growth[0]["growth_seconds"] == 19.0
    assert all(row["file"] != "tests/f19.py" for row in growth)


SHA = [f"{index:040x}" for index in range(1, 11)]
CEILING_PAYLOAD = {"schema": 1, "ceiling_seconds": 1000, "reference_calibration_best_seconds": 0.75, "reason": "x"}
RUN_CREATED_AT = "2026-10-24T00:00:00Z"
NOT_AFTER = datetime(2026, 11, 1, tzinfo=timezone.utc)
RUN_PATH = "repos/o/r/actions/runs/100"
ARTIFACT_PATH = "repos/o/r/actions/artifacts/200"
PULL_PATH = "repos/o/r/pulls/3500"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout


def zip_bytes(members: dict[str, bytes], *, symlinks: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            if name in symlinks:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(info, data)
    return buffer.getvalue()


class ReproduceFixture:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        (tmp_path / "checkout").mkdir()
        monkeypatch.chdir(tmp_path / "checkout")
        (tmp_path / "source").mkdir()
        self.runs = [
            write_run(
                tmp_path,
                f"source/run-{100 + index}",
                best_seconds=0.5 * (index % 3 + 1),
                cases=(("test_alpha", 1.0 * (index % 3 + 1)), ("test_beta", 3.0 * (index % 3 + 1))),
            )
            for index in range(10)
        ]
        self.entries = [
            {
                "run_id": 100 + index,
                "pr": 3500 + index,
                "sha": SHA[index],
                "artifact_id": 200 + index,
                "dir": str(run_dir),
            }
            for index, run_dir in enumerate(self.runs)
        ]
        self.archives = {
            entry["artifact_id"]: zip_bytes(
                {path.name: path.read_bytes() for path in sorted(Path(entry["dir"]).iterdir())}
            )
            for entry in self.entries
        }
        self.previous = tmp_path / "previous"
        previous, _ = baseline.build_baseline(self.entries[:1], GENERATED_AT)
        previous["tests"] = [{"id": "tests/test_sample.py::TestSample::test_alpha", "p50_seconds": 1.0}]
        baseline.write_sharded_baseline(self.previous, previous)
        self.repo = tmp_path / "repo"
        self.directory = self.repo / "tests" / "fixtures" / "fast_tier_baseline"
        built, _ = baseline.build_baseline(self.entries, GENERATED_AT)
        baseline.write_sharded_baseline(self.directory, built)
        baseline.write_summary(self.previous, self.directory)
        self.ids = {record["id"] for record in built["tests"]}
        self.ceiling = tmp_path / "ceiling.json"
        self.ceiling.write_text(json.dumps(CEILING_PAYLOAD), encoding="utf-8")
        git(self.repo, "init", "-q")
        self.commit()
        self.attempts = 0

    def commit(self, *, stage_worktree: bool = True) -> None:
        if stage_worktree:
            git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "baseline")

    def api(self, overrides: dict[str, dict[str, object]] | None = None) -> baseline.Api:
        payloads: dict[str, dict[str, object]] = {}
        for entry in self.entries:
            run_id, pr, sha = entry["run_id"], entry["pr"], entry["sha"]
            payloads[f"repos/o/r/actions/runs/{run_id}"] = {
                "id": run_id,
                "path": ".github/workflows/ci.yml",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "success",
                "head_sha": sha,
                "created_at": RUN_CREATED_AT,
                "head_repository": {"full_name": "o/r"},
                "pull_requests": [],
            }
            payloads[f"repos/o/r/actions/artifacts/{entry['artifact_id']}"] = {
                "name": f"fast-junit-{pr}-{sha}",
                "workflow_run": {"id": run_id, "head_sha": sha},
            }
            payloads[f"repos/o/r/pulls/{pr}"] = {
                "merged": True,
                "merged_at": "2026-10-24T12:00:00Z",
                "head": {"sha": sha, "repo": {"full_name": "o/r"}},
            }
        for path, changes in (overrides or {}).items():
            payloads[path] = {**payloads[path], **changes}

        def api(path: str) -> object:
            if path not in payloads:
                raise baseline.BaselineError("unknown")
            return payloads[path]

        return api

    def fetch(self, path: str) -> bytes:
        artifact_id = int(path.split("/")[-2])
        if artifact_id not in self.archives:
            raise baseline.BaselineError("unknown")
        return self.archives[artifact_id]

    def problems(
        self,
        api: baseline.Api | None = None,
        fetch: baseline.Fetch | None = None,
        not_after: datetime = NOT_AFTER,
    ) -> list[str]:
        self.attempts += 1
        return baseline.reproduce_problems(
            self.directory,
            self.previous,
            self.tmp_path / f"work-{self.attempts}",
            rev="HEAD",
            repo="o/r",
            api=api or self.api(),
            fetch=fetch or self.fetch,
            collected_ids=self.ids,
            ceiling_path=self.ceiling,
            not_after=not_after,
        )


@pytest.fixture
def reproduce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReproduceFixture:
    return ReproduceFixture(tmp_path, monkeypatch)


def test_reproduce_passes_on_a_faithful_rebuild(reproduce: ReproduceFixture) -> None:
    assert reproduce.problems() == []


def test_reproduce_detects_an_extra_file_in_a_subdirectory(reproduce: ReproduceFixture) -> None:
    (reproduce.directory / "nested").mkdir()
    (reproduce.directory / "nested" / "hidden.json").write_text("{}\n", encoding="utf-8")
    reproduce.commit()
    assert reproduce.problems() == ['extra file "nested/hidden.json"']


def test_reproduce_detects_a_missing_summary(reproduce: ReproduceFixture) -> None:
    git(reproduce.repo, "rm", "-q", "tests/fixtures/fast_tier_baseline/SUMMARY.md")
    reproduce.commit()
    assert reproduce.problems() == ['missing file "SUMMARY.md"']


def test_reproduce_detects_a_mode_change(reproduce: ReproduceFixture) -> None:
    git(reproduce.repo, "update-index", "--chmod=+x", "tests/fixtures/fast_tier_baseline/tests-00.json")
    reproduce.commit(stage_worktree=False)
    assert reproduce.problems() == ['file mode 100755 on "tests-00.json"']


def test_reproduce_detects_a_changed_byte(reproduce: ReproduceFixture) -> None:
    target = next(
        path
        for path in sorted(reproduce.directory.glob("tests-*.json"))
        if "p50_seconds" in path.read_text(encoding="utf-8")
    )
    target.write_text(
        target.read_text(encoding="utf-8").replace('"p50_seconds": ', '"p50_seconds": 9', 1), encoding="utf-8"
    )
    reproduce.commit()
    assert reproduce.problems() == [f'changed bytes in "{target.name}"']


def test_reproduce_reads_the_commit_not_the_worktree(reproduce: ReproduceFixture) -> None:
    (reproduce.directory / "SUMMARY.md").write_text("edited\n", encoding="utf-8")
    assert reproduce.problems() == []


@pytest.mark.parametrize(
    ("path", "changes", "codes"),
    [
        (RUN_PATH, {"head_sha": SHA[5]}, ["run-sha"]),
        (RUN_PATH, {"event": "push"}, ["run-event"]),
        (RUN_PATH, {"conclusion": "failure"}, ["run-conclusion"]),
        (RUN_PATH, {"path": ".github/workflows/evil.yml"}, ["run-workflow"]),
        (RUN_PATH, {"path": ".github/workflows/ci.yml@refs/pull/3500/merge"}, []),
        (RUN_PATH, {"pull_requests": []}, []),
        (RUN_PATH, {"pull_requests": [{"number": 1}]}, []),
        (RUN_PATH, {"head_repository": {"full_name": "attacker/r"}}, ["run-fork"]),
        (RUN_PATH, {"head_repository": None}, ["run-fork"]),
        (RUN_PATH, {"created_at": "2026-09-01T00:00:00Z"}, ["generated-too-late"]),
        (RUN_PATH, {"created_at": "2026-10-26T00:00:00Z"}, ["generated-before-run", "run-after-merge"]),
        (RUN_PATH, {"created_at": "yesterday"}, ["run-created"]),
        (ARTIFACT_PATH, {"name": f"fast-junit-9999-{SHA[0]}"}, ["artifact-name"]),
        (ARTIFACT_PATH, {"workflow_run": {"id": 7, "head_sha": SHA[0]}}, ["artifact-run"]),
        (ARTIFACT_PATH, {"workflow_run": {"id": 100, "head_sha": SHA[5]}}, ["artifact-sha"]),
        (PULL_PATH, {"merged": False}, ["pr-not-merged"]),
        (PULL_PATH, {"head": {"sha": SHA[5], "repo": {"full_name": "o/r"}}}, ["pr-head"]),
        (PULL_PATH, {"head": {"sha": SHA[0], "repo": {"full_name": "attacker/r"}}}, ["pr-fork"]),
        (PULL_PATH, {"head": {"sha": SHA[0], "repo": None}}, ["pr-fork"]),
        (PULL_PATH, {"merged_at": "2026-10-23T23:59:59Z"}, ["run-after-merge"]),
        (PULL_PATH, {"merged_at": RUN_CREATED_AT}, []),
        (PULL_PATH, {"merged_at": None}, ["pr-merged-at"]),
    ],
)
def test_reproduce_rejects_source_runs_whose_provenance_does_not_match(
    reproduce: ReproduceFixture, path: str, changes: dict[str, object], codes: list[str]
) -> None:
    problems = reproduce.problems(reproduce.api({path: changes}))
    assert problems == [f"run 100: {code}" for code in codes]


def test_reproduce_downloads_nothing_until_provenance_passes(reproduce: ReproduceFixture) -> None:
    def forbidden(path: str) -> bytes:
        raise AssertionError(path)

    problems = reproduce.problems(reproduce.api({PULL_PATH: {"merged": False}}), fetch=forbidden)
    assert problems == ["run 100: pr-not-merged"]
    assert not (reproduce.tmp_path / f"work-{reproduce.attempts}" / "fast-refresh").exists()


def test_reproduce_rejects_an_unreachable_api_without_trusting_the_artifact(reproduce: ReproduceFixture) -> None:
    def unreachable(path: str) -> object:
        raise baseline.BaselineError("down")

    problems = reproduce.problems(unreachable)
    assert "run 100: run-unavailable" in problems
    assert "run 100: artifact-unavailable" in problems
    assert "run 100: pr-unavailable" in problems


def test_reproduce_reports_a_failed_download_and_a_hostile_archive(reproduce: ReproduceFixture) -> None:
    def broken(path: str) -> bytes:
        raise baseline.BaselineError("down")

    assert reproduce.problems(fetch=broken)[0] == "run 100: artifact-download"
    reproduce.archives[200] = zip_bytes({"junit.xml": b"<x/>", "extra.txt": b"x"})
    assert reproduce.problems() == ["run 100: artifact-members"]


def test_reproduce_requires_a_fresh_working_directory_outside_the_checkout(reproduce: ReproduceFixture) -> None:
    kwargs: dict[str, Any] = {
        "rev": "HEAD",
        "repo": "o/r",
        "api": reproduce.api(),
        "fetch": reproduce.fetch,
        "collected_ids": reproduce.ids,
        "ceiling_path": reproduce.ceiling,
        "not_after": NOT_AFTER,
    }
    with pytest.raises(baseline.BaselineError, match="outside the current directory"):
        baseline.reproduce_problems(reproduce.directory, reproduce.previous, Path.cwd() / "inside", **kwargs)
    occupied = reproduce.tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "leftover").write_text("x", encoding="utf-8")
    with pytest.raises(baseline.BaselineError, match="must be empty"):
        baseline.reproduce_problems(reproduce.directory, reproduce.previous, occupied, **kwargs)


def test_reproduce_pins_generated_at_to_utc_and_to_the_event_time(reproduce: ReproduceFixture) -> None:
    assert reproduce.problems(not_after=datetime(2026, 10, 1, tzinfo=timezone.utc)) == [
        "manifest: generated_at is after the pull request event"
    ]
    manifest = reproduce.directory / "manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(GENERATED_AT, "2026-10-25T00:00:00+00:00"))
    reproduce.commit()
    assert reproduce.problems() == ["manifest: generated_at is not a UTC timestamp"]


def test_source_run_problems_bound_the_run_count_and_forbid_duplicates() -> None:
    generated = datetime(2026, 10, 25, tzinfo=timezone.utc)

    def entry(run_id: int, pr: int, artifact_id: int | None = None) -> dict[str, object]:
        return {"run_id": run_id, "pr": pr, "sha": SHA[0], "artifact_id": artifact_id or run_id}

    def api(path: str) -> object:
        raise baseline.BaselineError("down")

    many = [entry(index, index) for index in range(1, 22)]
    assert baseline.source_run_problems(many, "o/r", api, generated) == ["source runs: more than 20"]
    few = [entry(index, index) for index in range(1, 10)]
    assert baseline.source_run_problems(few, "o/r", api, generated) == ["source runs: fewer than 10"]
    base = [entry(index, index) for index in range(10, 20)]
    duplicated = baseline.source_run_problems(
        [*base, entry(10, 50), entry(51, 11), entry(52, 53, 12)], "o/r", api, generated
    )
    assert "source runs: run 10 appears more than once" in duplicated
    assert "source runs: pr 11 appears more than once" in duplicated
    assert "source runs: artifact 12 appears more than once" in duplicated
    assert not any(problem.startswith("run ") for problem in duplicated)


def test_extract_artifact_accepts_only_regular_allowed_members(tmp_path: Path) -> None:
    good = zip_bytes({"junit.xml": b"<a/>", "calibration.json": b"{}", "collection.jsonl": b""})
    baseline.extract_artifact(good, tmp_path / "ok")
    assert sorted(path.name for path in (tmp_path / "ok").iterdir()) == [
        "calibration.json",
        "collection.jsonl",
        "junit.xml",
    ]
    hostile = {
        "traversal": zip_bytes({"../junit.xml": b"x"}),
        "absolute": zip_bytes({"/etc/junit.xml": b"x"}),
        "nested": zip_bytes({"p/junit.xml": b"x"}),
        "other": zip_bytes({"__init__.py": b"x"}),
        "directory": zip_bytes({"junit.xml/": b""}),
        "symlink": zip_bytes({"junit.xml": b"../../scripts"}, symlinks=("junit.xml",)),
    }
    for label, archive in hostile.items():
        with pytest.raises(baseline.SourceRunError) as caught:
            baseline.extract_artifact(archive, tmp_path / label)
        assert caught.value.code == "artifact-members", label
        assert not (tmp_path / label).exists(), label
    with pytest.raises(baseline.SourceRunError) as caught:
        baseline.extract_artifact(b"not a zip", tmp_path / "garbage")
    assert caught.value.code == "artifact-unreadable"


def test_extract_artifact_enforces_the_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(baseline, "ARTIFACT_MAX_BYTES", 3)
    with pytest.raises(baseline.SourceRunError):
        baseline.extract_artifact(zip_bytes({"junit.xml": b"1234"}), tmp_path / "big")


def test_reproduce_enforces_the_ceiling_against_the_rebuilt_baseline(reproduce: ReproduceFixture) -> None:
    reproduce.ceiling.write_text(json.dumps({**CEILING_PAYLOAD, "ceiling_seconds": 1}), encoding="utf-8")
    assert reproduce.problems() == ["ceiling check: the baseline is over the fast-duration ceiling"]


def test_reproduce_gate_check_needs_the_collected_fast_tier_covered(reproduce: ReproduceFixture) -> None:
    reproduce.ids = {"tests/other.py::t1", "tests/other.py::t2"}
    assert reproduce.problems() == ["gate check: coverage of the collected fast tier is below the minimum"]


def test_reproduce_command_reports_problems_and_exit_codes(
    reproduce: ReproduceFixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    collected = reproduce.tmp_path / "collected.txt"
    collected.write_text("\n".join(sorted(reproduce.ids)) + "\n", encoding="utf-8")

    def argv(workdir: str) -> list[str]:
        return [
            "reproduce",
            "--baseline",
            str(reproduce.directory),
            "--previous",
            str(reproduce.previous),
            "--workdir",
            str(reproduce.tmp_path / workdir),
            "--collected-ids",
            str(collected),
            "--ceiling",
            str(reproduce.ceiling),
            "--repo",
            "o/r",
            "--not-after",
            "2026-11-01T00:00:00Z",
        ]

    monkeypatch.setattr(baseline, "github_api", reproduce.api())
    monkeypatch.setattr(baseline, "github_artifact_zip", reproduce.fetch)
    assert baseline.main(argv("work-cli-1")) == 0
    monkeypatch.setattr(baseline, "github_api", reproduce.api({"repos/o/r/pulls/3501": {"merged": False}}))
    assert baseline.main(argv("work-cli-2")) == 2
    assert "run 101: pr-not-merged" in capsys.readouterr().out
    assert baseline.main([*argv("work-cli-3")[:-1], "tomorrow"]) == 1


def summary_pair() -> tuple[dict[str, object], dict[str, object]]:
    hostile = "tests/a.py::t[x|y `z` \nw]"
    previous = {
        "reference_calibration_best_seconds": 1.0,
        "tests": [{"id": hostile, "p50_seconds": 1.0}, {"id": "tests/b.py::gone", "p50_seconds": 2.0}],
    }
    current = {
        "reference_calibration_best_seconds": 1.0,
        "tests": [{"id": hostile, "p50_seconds": 2.0}, {"id": "tests/c.py::new", "p50_seconds": 0.5}],
        "runs": [{"run_id": 1, "pr": 2, "sha": SHA[0], "speed_factor": 1.0, "test_count": 2}],
        "rejected_runs": [
            {"run_id": 3, "pr": 4, "reason": "junit-unreadable"},
            {"run_id": 5, "pr": 6, "reason": "missing or unreadable junit.xml: /home/runner/work/secret.xml"},
        ],
    }
    return previous, current


def table_cells(line: str) -> list[str]:
    return re.split(r"(?<!\\)\|", line.strip())[1:-1]


def test_summary_escapes_table_cells_and_keeps_each_row_on_one_line() -> None:
    text = baseline.summary_markdown(*summary_pair())
    rows = [line for line in text.splitlines() if "t[x" in line]
    assert rows
    for row in rows:
        assert len(table_cells(row)) in {4, 2}
        assert "``tests/a.py::t[x\\|y `z`  w]``" in row
    assert "\nw]" not in text


def test_summary_reduces_rejected_reasons_to_fixed_codes() -> None:
    text = baseline.summary_markdown(*summary_pair())
    assert "- run 3 (pr 4): junit-unreadable" in text
    assert "- run 5 (pr 6): unrecognized" in text
    assert "/home/runner" not in text
    assert "secret" not in text


def test_summary_is_deterministic_and_separates_the_list_with_blank_lines() -> None:
    first = baseline.summary_markdown(*summary_pair())
    assert first == baseline.summary_markdown(*summary_pair())
    assert "Rejected source runs: 2.\n\n- run 3" in first
    assert first.endswith("\n") and not first.endswith("\n\n")


def test_summary_command_writes_the_summary_next_to_the_baseline(reproduce: ReproduceFixture) -> None:
    (reproduce.directory / "SUMMARY.md").unlink()
    argv = ["summary", "--previous", str(reproduce.previous), "--baseline", str(reproduce.directory)]
    assert baseline.main(argv) == 0
    assert (reproduce.directory / "SUMMARY.md").read_text(encoding="utf-8").startswith("# Fast-tier duration baseline")


def test_rejected_source_runs_carry_fixed_codes(tmp_path: Path) -> None:
    good = write_run(tmp_path, "good")
    no_calibration = write_run(tmp_path, "bad-a", calibration=False)
    bad_value = write_run(tmp_path, "bad-b", best_seconds=-1)
    bad_time = write_run(tmp_path, "bad-c", cases=(("test_alpha", "oops"),))
    no_junit = write_run(tmp_path, "bad-d")
    (no_junit / "junit.xml").unlink()
    manifest = write_manifest(tmp_path, [good, no_calibration, bad_value, bad_time, no_junit])
    _built, rejected = baseline.build_baseline(baseline.load_manifest(manifest), GENERATED_AT)
    assert [item["reason"] for item in rejected] == [
        "calibration-unreadable",
        "calibration-invalid",
        "junit-time-invalid",
        "junit-unreadable",
    ]
