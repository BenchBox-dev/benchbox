from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import build_fast_duration_baseline as baseline

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GENERATED_AT = "2026-10-25T00:00:00Z"


def write_run(
    root: Path,
    name: str,
    cases: dict[str, float],
    *,
    best_seconds: float | None = 0.5,
    collection_max: float | None = 4.0,
) -> Path:
    run_dir = root / name
    run_dir.mkdir()
    lines = ['<testsuite name="fast">']
    for case, duration in cases.items():
        lines.append(
            f'<testcase classname="tests.test_sample.TestSample" file="tests/test_sample.py" '
            f'name="{case}" time="{duration}" />'
        )
    lines.append("</testsuite>")
    (run_dir / "junit.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if best_seconds is not None:
        payload = {
            "schema": 1,
            "workload": {"best_seconds": best_seconds},
            "collection": {"max_seconds": collection_max},
        }
        (run_dir / "calibration.json").write_text(json.dumps(payload), encoding="utf-8")
    return run_dir


def committed_baseline(root: Path, cases: dict[str, float], *, best_seconds: float = 0.5) -> Path:
    source = write_run(root, "source", cases, best_seconds=best_seconds)
    entries = [{"run_id": 1, "pr": 1, "sha": "a", "artifact_id": 1, "dir": str(source)}]
    built, _rejected = baseline.build_baseline(entries, GENERATED_AT)
    output = root / "baseline"
    baseline.write_sharded_baseline(output, built)
    return output


def ids(count: int, seconds: float) -> dict[str, float]:
    return {f"tests/test_sample.py::test_{index:04d}": seconds for index in range(count)}


def cases(count: int, seconds: float) -> dict[str, float]:
    return {f"test_{index:04d}": seconds for index in range(count)}


def test_unchanged_code_passes_with_unit_ratios() -> None:
    base = ids(100, 4.0)

    report = baseline.verify_durations(base, dict(base))

    assert report["verdict"] == "pass"
    assert report["sum_ratio"] == pytest.approx(1.0)
    assert report["median_test_ratio"] == pytest.approx(1.0)
    assert report["delta_seconds"] == pytest.approx(0.0)
    assert report["coverage"] == 1.0
    assert report["coverage_warning"] is False


def test_module_teardown_overhead_spread_over_tests_would_block() -> None:
    base = ids(1000, 0.328)
    slowed = {nodeid: seconds + 713.0 / 1000 for nodeid, seconds in base.items()}

    report = baseline.verify_durations(base, slowed)

    assert report["verdict"] == "would-block"
    assert report["delta_seconds"] == pytest.approx(713.0)
    assert report["median_test_ratio"] == pytest.approx(1 + 0.713 / 0.328)


def test_overhead_hitting_a_few_tests_moves_sum_but_not_median() -> None:
    base = ids(100, 4.0)
    slowed = dict(base)
    for nodeid in list(slowed)[:10]:
        slowed[nodeid] += 50.0

    report = baseline.verify_durations(base, slowed)

    assert report["verdict"] == "would-block"
    assert report["median_test_ratio"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("base_total", "pr_total", "verdict"),
    [
        (1000.0, 1150.0, "pass"),
        (1000.0, 1150.5, "would-block"),
        (200.0, 260.0, "pass"),
        (200.0, 260.5, "would-block"),
        (100.0, 130.0, "pass"),
        (1000.0, 1000.0, "pass"),
        (1000.0, 900.0, "pass"),
    ],
)
def test_threshold_edges_require_both_ratio_and_delta(base_total: float, pr_total: float, verdict: str) -> None:
    base = {"tests/test_sample.py::test_only": base_total}
    current = {"tests/test_sample.py::test_only": pr_total}

    assert baseline.verify_durations(base, current)["verdict"] == verdict


def test_new_tests_are_allowed_a_per_test_budget() -> None:
    base = ids(10, 10.0)
    current = dict(base)
    current.update({f"tests/test_new.py::test_{index}": 0.4 for index in range(100)})

    report = baseline.verify_durations(base, current)

    assert report["new_test_count"] == 100
    assert report["new_test_excess_seconds"] == 0.0
    assert report["delta_seconds"] == pytest.approx(0.0)


def test_new_tests_over_allowance_count_toward_the_delta() -> None:
    base = ids(10, 10.0)
    current = dict(base)
    current["tests/test_new.py::test_slow"] = 100.0

    report = baseline.verify_durations(base, current)

    assert report["new_test_excess_seconds"] == pytest.approx(99.5)
    assert report["delta_seconds"] == pytest.approx(99.5)
    assert report["verdict"] == "would-block"


def test_renamed_tests_leave_the_intersection_and_lower_coverage() -> None:
    base = ids(100, 4.0)
    current = dict(base)
    removed = list(current)[:10]
    for nodeid in removed:
        current[nodeid.replace("test_", "test_renamed_")] = current.pop(nodeid)

    report = baseline.verify_durations(base, current)

    assert report["shared_test_count"] == 90
    assert report["missing_test_count"] == 10
    assert report["coverage"] == pytest.approx(0.9)
    assert report["coverage_warning"] is True


def test_parametrized_ids_are_matched_exactly() -> None:
    base = {"tests/test_sample.py::test_a[1]": 2.0, "tests/test_sample.py::test_a[2]": 2.0}
    current = {"tests/test_sample.py::test_a[1]": 2.0, "tests/test_sample.py::test_a[3]": 2.0}

    report = baseline.verify_durations(base, current)

    assert report["shared_test_count"] == 1
    assert report["new_test_count"] == 1
    assert report["missing_test_count"] == 1


def test_runner_speed_is_normalized_before_comparison(tmp_path: Path) -> None:
    baseline_dir = committed_baseline(tmp_path, cases(50, 2.0), best_seconds=0.5)
    slow = write_run(tmp_path, "slow", cases(50, 3.4), best_seconds=0.85)

    report = baseline.verify_run(baseline_dir, slow)

    assert report["verdict"] == "pass"
    assert report["speed_factor"] == pytest.approx(0.5 / 0.85)
    assert report["sum_ratio"] == pytest.approx(1.0)
    assert report["median_test_ratio"] == pytest.approx(1.0)


def test_real_overhead_on_a_slow_runner_still_would_block(tmp_path: Path) -> None:
    baseline_dir = committed_baseline(tmp_path, cases(1000, 0.4), best_seconds=0.5)
    slow = write_run(tmp_path, "slow", cases(1000, 0.4 * 1.7 + 0.713 * 1.7), best_seconds=0.85)

    report = baseline.verify_run(baseline_dir, slow)

    assert report["verdict"] == "would-block"
    assert report["delta_seconds"] == pytest.approx(713.0)


def test_run_without_calibration_is_rejected_not_passed(tmp_path: Path) -> None:
    baseline_dir = committed_baseline(tmp_path, cases(5, 2.0))
    run = write_run(tmp_path, "uncalibrated", cases(5, 2.0), best_seconds=None)

    report = baseline.verify_run(baseline_dir, run)

    assert report["verdict"] == "rejected"
    assert "calibration" in str(report["reason"])


@pytest.mark.parametrize("best", [0.0, -1.0])
def test_run_with_non_positive_calibration_is_rejected(tmp_path: Path, best: float) -> None:
    baseline_dir = committed_baseline(tmp_path, cases(5, 2.0))
    run = write_run(tmp_path, "bad", cases(5, 2.0), best_seconds=best)

    assert baseline.verify_run(baseline_dir, run)["verdict"] == "rejected"


def test_missing_junit_is_rejected(tmp_path: Path) -> None:
    baseline_dir = committed_baseline(tmp_path, cases(5, 2.0))
    run = write_run(tmp_path, "nojunit", cases(5, 2.0))
    (run / "junit.xml").unlink()

    assert baseline.verify_run(baseline_dir, run)["verdict"] == "rejected"


def test_missing_baseline_is_a_neutral_verdict(tmp_path: Path) -> None:
    run = write_run(tmp_path, "run", cases(5, 2.0))

    report = baseline.verify_run(tmp_path / "absent", run)

    assert report["verdict"] == "neutral"
    assert "baseline" in str(report["reason"])


def test_markdown_reports_ratio_median_collection_and_speed(tmp_path: Path) -> None:
    baseline_dir = committed_baseline(tmp_path, cases(5, 2.0))
    run = write_run(tmp_path, "run", cases(5, 2.0), collection_max=7.5)

    text = baseline.verify_markdown(baseline.verify_run(baseline_dir, run))

    assert "Verdict: **pass**" in text
    assert "Median per-test ratio" in text
    assert "Summed-time ratio" in text
    assert "7.5 s" in text
    assert "Runner speed factor" in text


def test_markdown_for_neutral_and_rejected_names_the_reason() -> None:
    assert "Reason: nothing" in baseline.verify_markdown({"verdict": "neutral", "reason": "nothing"})
    assert "Verdict: **rejected**" in baseline.verify_markdown({"verdict": "rejected", "reason": "x"})


def test_cli_exits_zero_even_for_would_block_and_writes_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline_dir = committed_baseline(tmp_path, cases(100, 5.0))
    run = write_run(tmp_path, "slowed", cases(100, 5.0 + 1.0))
    summary = tmp_path / "summary.md"
    report_out = tmp_path / "report.json"

    status = baseline.main(
        [
            "verify",
            "--baseline",
            str(baseline_dir),
            "--run-dir",
            str(run),
            "--summary",
            str(summary),
            "--report-out",
            str(report_out),
        ]
    )

    assert status == 0
    assert "Verdict: **would-block**" in summary.read_text(encoding="utf-8")
    assert json.loads(report_out.read_text(encoding="utf-8"))["verdict"] == "would-block"
    assert "::warning title=verify-fast-duration" in capsys.readouterr().out


def test_cli_exits_zero_for_missing_baseline_and_missing_calibration(tmp_path: Path) -> None:
    run = write_run(tmp_path, "run", cases(5, 2.0), best_seconds=None)

    status = baseline.main(["verify", "--baseline", str(tmp_path / "absent"), "--run-dir", str(run)])

    assert status == 0


def test_committed_baseline_verifies_against_itself_as_pass() -> None:
    root = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "fast_tier_baseline"
    committed = baseline.baseline_tests_map(baseline.read_sharded_baseline(root))

    report = baseline.verify_durations(committed, dict(committed))

    assert report["verdict"] == "pass"
    assert report["coverage"] == 1.0


def test_new_test_allowance_is_applied_per_test() -> None:
    base = {"tests/test_sample.py::test_base": 100.0}
    current = dict(base)
    current["tests/test_new.py::test_slow"] = 100.0
    current.update({f"tests/test_new.py::test_cheap_{index}": 0.0 for index in range(199)})

    report = baseline.verify_durations(base, current)

    assert report["new_test_excess_seconds"] == pytest.approx(99.5)
    assert report["verdict"] == "would-block"


def test_disjoint_ids_are_neutral_not_pass() -> None:
    report = baseline.verify_durations({"tests/a.py::test_a": 5.0}, {"tests/b.py::test_b": 5.0})

    assert report["verdict"] == "neutral"
    assert "reason" in report
    assert "Verdict: **neutral**" in baseline.verify_markdown(report)


def test_zero_baseline_sum_is_neutral() -> None:
    report = baseline.verify_durations({"tests/a.py::test_a": 0.0}, {"tests/a.py::test_a": 5.0})

    assert report["verdict"] == "neutral"
