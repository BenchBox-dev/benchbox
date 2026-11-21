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
