from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import generator_volume_gate as gate
from scripts.measure_sf1_sizes import GENERATORS

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
AMPLAB_GENERATOR = "benchbox/core/amplab/generator.py"


def baseline_with(**sizes: int) -> dict[str, object]:
    return {"tolerance": 0.05, "sizes": {"amplab": 1_000_000, "tpcdi": 2_000_000, **sizes}}


def healthy(dataset_bytes: int) -> gate.SmokeResult:
    return gate.SmokeResult(dataset_bytes, 24, 0, 28_750, 12.0)


class RecordingSmoke:
    def __init__(self, results: dict[str, gate.SmokeResult]):
        self.results = results
        self.calls: list[str] = []

    def __call__(self, benchmark: str) -> gate.SmokeResult:
        self.calls.append(benchmark)
        return self.results[benchmark]


def test_a_generator_module_change_selects_its_benchmark() -> None:
    assert gate.affected_benchmarks([AMPLAB_GENERATOR, "README.md"]) == ["amplab"]


def test_a_generator_spec_change_selects_its_benchmark() -> None:
    assert gate.affected_benchmarks(["benchbox/core/tpcdi/generator_specs.yaml"]) == ["tpcdi"]
    assert gate.affected_benchmarks(["benchbox/core/tsbs_devops/generator_specs.yaml"]) == ["tsbs_devops"]


def test_a_package_generator_change_selects_tpcdi() -> None:
    assert gate.affected_benchmarks(["benchbox/core/tpcdi/generator/facts.py"]) == ["tpcdi"]


@pytest.mark.parametrize(
    "path",
    [
        "benchbox/core/amplab/queries.py",
        "benchbox/core/amplab/schema_specs.yaml",
        "benchbox/core/tpcdi/schema_specs.yaml",
        "examples/amplab_demo.py",
        "tests/unit/generators/sf1_size_baseline.json",
        "benchbox/core/tpch/generator.py",
    ],
)
def test_unaffected_paths_select_nothing(path: str) -> None:
    assert gate.affected_benchmarks([path]) == []


def test_an_unaffected_change_skips_without_generating_anything() -> None:
    smoke = RecordingSmoke({})
    report = gate.run_gate(["README.md"], baseline_with(), None, smoke=smoke)
    assert report["status"] == "skipped"
    assert smoke.calls == []
    assert gate.render_markdown(report).count("no pure-Python generator") == 1


def test_output_within_tolerance_passes() -> None:
    smoke = RecordingSmoke({"amplab": healthy(1_040_000)})
    report = gate.run_gate([AMPLAB_GENERATOR], baseline_with(), baseline_with(), smoke=smoke)
    assert report["status"] == "pass"
    assert smoke.calls == ["amplab"]


def test_volume_drift_without_a_baseline_update_fails_and_names_the_fix() -> None:
    smoke = RecordingSmoke({"amplab": healthy(3_000_000)})
    report = gate.run_gate([AMPLAB_GENERATOR], baseline_with(), baseline_with(), smoke=smoke)
    assert report["status"] == "fail"
    (record,) = report["benchmarks"]
    assert record["drift"] == pytest.approx(2.0)
    assert "update tests/unit/generators/sf1_size_baseline.json" in record["reason"]


def test_a_shrinking_generator_fails_too() -> None:
    smoke = RecordingSmoke({"amplab": healthy(400_000)})
    assert gate.run_gate([AMPLAB_GENERATOR], baseline_with(), baseline_with(), smoke=smoke)["status"] == "fail"


def test_drift_with_the_baseline_updated_in_the_same_diff_passes() -> None:
    smoke = RecordingSmoke({"amplab": healthy(3_000_000)})
    report = gate.run_gate(
        [AMPLAB_GENERATOR], baseline_with(amplab=3_010_000), baseline_with(amplab=1_000_000), smoke=smoke
    )
    assert report["status"] == "pass"


def test_a_baseline_edit_that_does_not_match_the_generator_still_fails() -> None:
    smoke = RecordingSmoke({"amplab": healthy(3_000_000)})
    report = gate.run_gate(
        [AMPLAB_GENERATOR], baseline_with(amplab=2_000_000), baseline_with(amplab=1_000_000), smoke=smoke
    )
    assert report["status"] == "fail"
    assert "changed in this diff but still does not match" in report["benchmarks"][0]["reason"]


def test_an_updated_baseline_cannot_hide_a_broken_load_or_power_run() -> None:
    broken = gate.SmokeResult(3_000_000, 20, 4, 28_750, 9.0)
    smoke = RecordingSmoke({"amplab": broken})
    report = gate.run_gate(
        [AMPLAB_GENERATOR], baseline_with(amplab=3_000_000), baseline_with(amplab=1_000_000), smoke=smoke
    )
    assert report["status"] == "fail"
    assert "4 power-phase queries failed" in report["benchmarks"][0]["reason"]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (gate.SmokeResult(0, 0, 0, 0, 1.0, "benchbox run exited 1: boom"), "benchbox run exited 1"),
        (gate.SmokeResult(5, 0, 0, 10, 1.0), "no power-phase query passed"),
        (gate.SmokeResult(5, 3, 0, 0, 1.0), "no rows were loaded"),
    ],
)
def test_smoke_failures_are_reported(result: gate.SmokeResult, expected: str) -> None:
    assert expected in (gate.evaluate_smoke(result) or "")


def test_a_benchmark_missing_from_the_baseline_fails() -> None:
    smoke = RecordingSmoke({"amplab": healthy(1_000_000)})
    report = gate.run_gate([AMPLAB_GENERATOR], {"tolerance": 0.05, "sizes": {}}, None, smoke=smoke)
    assert report["status"] == "fail"


@pytest.mark.parametrize("tolerance", [0, 1, -0.1, "0.05", None, True])
def test_a_baseline_without_a_usable_tolerance_is_rejected(tolerance: Any) -> None:
    with pytest.raises(ValueError):
        gate.baseline_tolerance({"tolerance": tolerance})


def test_every_affected_benchmark_is_checked_and_runtime_is_reported() -> None:
    changed = [AMPLAB_GENERATOR, "benchbox/core/tpcdi/generator_specs.yaml"]
    smoke = RecordingSmoke({"amplab": healthy(1_000_000), "tpcdi": healthy(9_000_000)})
    report = gate.run_gate(changed, baseline_with(), baseline_with(), smoke=smoke)
    assert smoke.calls == ["tpcdi", "amplab"]
    assert report["status"] == "fail"
    text = gate.render_markdown(report)
    assert "| amplab | pass |" in text and "| tpcdi | fail |" in text
    assert "Total runtime:" in text


def write_inputs(tmp_path: Path, changed: list[str], head: dict[str, object], base: dict[str, object]) -> list[str]:
    (tmp_path / "changed.txt").write_text("\n".join(changed) + "\n", encoding="utf-8")
    (tmp_path / "head.json").write_text(json.dumps(head), encoding="utf-8")
    (tmp_path / "base.json").write_text(json.dumps(base), encoding="utf-8")
    return [
        "--changed-files",
        str(tmp_path / "changed.txt"),
        "--baseline",
        str(tmp_path / "head.json"),
        "--base-baseline",
        str(tmp_path / "base.json"),
        "--summary",
        str(tmp_path / "summary.md"),
    ]


def test_the_command_exits_one_on_drift_and_writes_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(gate, "run_smoke", RecordingSmoke({"amplab": healthy(3_000_000)}))
    args = write_inputs(tmp_path, [AMPLAB_GENERATOR], baseline_with(), baseline_with())
    assert gate.main(args) == 1
    assert "::error title=generator-volume::amplab" in capsys.readouterr().out
    assert "Result: **fail**" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_the_command_exits_zero_once_the_baseline_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "run_smoke", RecordingSmoke({"amplab": healthy(3_000_000)}))
    args = write_inputs(tmp_path, [AMPLAB_GENERATOR], baseline_with(amplab=3_000_000), baseline_with())
    assert gate.main(args) == 0


def test_the_command_skips_an_unrelated_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    smoke = RecordingSmoke({})
    monkeypatch.setattr(gate, "run_smoke", smoke)
    args = write_inputs(tmp_path, ["README.md"], baseline_with(), baseline_with())
    assert gate.main(args) == 0
    assert smoke.calls == []


def test_the_command_tolerates_an_unreadable_base_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "run_smoke", RecordingSmoke({"amplab": healthy(3_000_000)}))
    args = write_inputs(tmp_path, [AMPLAB_GENERATOR], baseline_with(), baseline_with())
    (tmp_path / "base.json").write_text("", encoding="utf-8")
    assert gate.main(args) == 1


def test_list_affected_prints_the_selected_benchmarks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = write_inputs(tmp_path, [AMPLAB_GENERATOR, "x"], baseline_with(), baseline_with())
    assert gate.main([*args, "--list-affected"]) == 0
    assert json.loads(capsys.readouterr().out) == ["amplab"]


class FakeBenchboxRun:
    def __init__(self, *, returncode: int = 0, failed: int = 0, bundle: bool = True):
        self.returncode = returncode
        self.failed = failed
        self.bundle = bundle
        self.command: list[str] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.command = command
        output = Path(kwargs["env"]["BENCHBOX_OUTPUT_DIR"])
        (output / "datagen" / "amplab_sf1").mkdir(parents=True)
        (output / "datagen" / "amplab_sf1" / "rankings.tbl").write_bytes(b"x" * 1000)
        (output / "datagen" / "amplab_sf1" / "_datagen_manifest.json").write_text("{}", encoding="utf-8")
        (output / "datagen" / "amplab_sf1" / "rankings.tbl.zst").write_bytes(b"x" * 500)
        if self.bundle:
            (output / "results").mkdir()
            summary = {"queries": {"passed": 24 - self.failed, "failed": self.failed}, "data": {"rows_loaded": 5}}
            (output / "results" / "r.json").write_text(json.dumps({"summary": summary}), encoding="utf-8")
        return subprocess.CompletedProcess(command, self.returncode, "", "boom\n" if self.returncode else "")


def test_the_smoke_runs_duckdb_at_sf1_and_counts_only_generated_source_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeBenchboxRun()
    monkeypatch.setattr(gate.subprocess, "run", fake)
    result = gate.run_smoke("amplab")
    assert result.dataset_bytes == 1000
    assert (result.queries_passed, result.queries_failed, result.rows_loaded) == (24, 0, 5)
    command = " ".join(fake.command)
    assert "--platform duckdb --benchmark amplab --scale 1 --phases generate,load,power" in command
    assert "--compression none" in command


def test_a_failing_benchbox_run_is_a_smoke_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate.subprocess, "run", FakeBenchboxRun(returncode=1))
    assert "benchbox run exited 1: boom" in (gate.run_smoke("amplab").error or "")


def test_a_run_without_a_result_bundle_is_a_smoke_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate.subprocess, "run", FakeBenchboxRun(bundle=False))
    assert "no result bundle" in (gate.run_smoke("amplab").error or "")


def test_the_smoke_surfaces_failed_power_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate.subprocess, "run", FakeBenchboxRun(failed=3))
    assert gate.evaluate_smoke(gate.run_smoke("amplab")) == "3 power-phase queries failed on DuckDB at SF 1"


def test_every_gated_benchmark_has_a_committed_baseline_and_a_measurement_entry() -> None:
    committed = json.loads((REPO_ROOT / gate.BASELINE_PATH).read_text(encoding="utf-8"))
    measured = {entry[0] for entry in GENERATORS}
    measured |= {"tpcdi"}
    for benchmark in gate.PURE_PYTHON_GENERATORS:
        assert gate.baseline_size(committed, benchmark) is not None
        assert benchmark in measured


def test_every_gated_generator_path_exists_or_is_a_documented_pattern() -> None:
    for benchmark, patterns in gate.PURE_PYTHON_GENERATORS.items():
        generator_pattern = patterns[0]
        stem = generator_pattern.removesuffix("/*")
        assert (REPO_ROOT / stem).exists(), f"{benchmark}: {generator_pattern}"


def ci_jobs() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def test_core_requires_the_generator_volume_job_through_its_aggregate() -> None:
    jobs = ci_jobs()
    assert "generator-volume" in jobs["core"]["needs"]
    aggregate = next(step for step in jobs["core"]["steps"] if step.get("id") == "aggregate")
    assert (
        "--expect generator-volume=${{ needs.ci-paths.outputs.generator-volume-needed == 'true' }}" in aggregate["run"]
    )


def test_the_generator_volume_job_runs_only_when_the_path_filter_fires() -> None:
    job = ci_jobs()["generator-volume"]
    assert job["if"] == "${{ needs.ci-paths.outputs.generator-volume-needed == 'true' }}"
    assert job["needs"] == "ci-paths"
    step = next(step for step in job["steps"] if step.get("name") == "Run the generator volume gate")
    assert step["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert '--base-ref "$BASE_SHA"' in step["run"]


def path_filter_regex() -> re.Pattern[str]:
    step = next(step for step in ci_jobs()["ci-paths"]["steps"] if step.get("id") == "path-flags")
    match = re.search(
        r"if grep -qE '(\^benchbox/core/\(tpcdi[^']*)' \.ci-paths/changed\.txt; then generator_volume", step["run"]
    )
    assert match, "the generator-volume path filter is missing from ci-paths"
    return re.compile(match.group(1))


def test_the_workflow_path_filter_selects_exactly_what_the_gate_selects() -> None:
    pattern = path_filter_regex()
    for benchmark, patterns in gate.PURE_PYTHON_GENERATORS.items():
        for glob in patterns:
            sample = glob.replace("*", "facts.py")
            assert pattern.match(sample), sample
            assert gate.affected_benchmarks([sample]) == [benchmark]
    for unrelated in (
        "benchbox/core/amplab/queries.py",
        "benchbox/core/tpcdi/schema_specs.yaml",
        "benchbox/core/tpch/generator.py",
        "README.md",
    ):
        assert not pattern.match(unrelated)
        assert gate.affected_benchmarks([unrelated]) == []


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return result.stdout.strip()


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", message)


@pytest.fixture
def repo_with_base(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "tests/unit/generators").mkdir(parents=True)
    (tmp_path / "tests/unit/generators/sf1_size_baseline.json").write_text(
        json.dumps(baseline_with()), encoding="utf-8"
    )
    (tmp_path / "benchbox/core/amplab").mkdir(parents=True)
    (tmp_path / "benchbox/core/amplab/generator.py").write_text("VOLUME = 1\n", encoding="utf-8")
    commit_all(tmp_path, "base")
    git(tmp_path, "tag", "base")
    return tmp_path


def test_the_base_ref_selects_the_files_the_branch_changed(
    repo_with_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(repo_with_base, "checkout", "-q", "-b", "feature")
    (repo_with_base / "benchbox/core/amplab/generator.py").write_text("VOLUME = 3\n", encoding="utf-8")
    (repo_with_base / "README.md").write_text("x\n", encoding="utf-8")
    commit_all(repo_with_base, "change")
    monkeypatch.chdir(repo_with_base)
    assert sorted(gate.changed_since("base")) == ["README.md", "benchbox/core/amplab/generator.py"]
    assert gate.baseline_at("base") == baseline_with()


def test_a_base_without_a_baseline_file_reads_as_none(repo_with_base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo_with_base)
    assert gate.baseline_at("base:no-such") is None


def test_the_base_ref_command_fails_on_drift_and_passes_once_the_baseline_moves(
    repo_with_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(repo_with_base, "checkout", "-q", "-b", "feature")
    (repo_with_base / "benchbox/core/amplab/generator.py").write_text("VOLUME = 3\n", encoding="utf-8")
    commit_all(repo_with_base, "triple the volume")
    monkeypatch.chdir(repo_with_base)
    monkeypatch.setattr(gate, "run_smoke", RecordingSmoke({"amplab": healthy(3_000_000)}))
    baseline_file = repo_with_base / "tests/unit/generators/sf1_size_baseline.json"
    args = ["--base-ref", "base", "--baseline", str(baseline_file)]
    assert gate.main(args) == 1
    baseline_file.write_text(json.dumps(baseline_with(amplab=3_000_000)), encoding="utf-8")
    assert gate.main(args) == 0


def test_the_base_ref_command_skips_when_only_other_files_changed(
    repo_with_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(repo_with_base, "checkout", "-q", "-b", "feature")
    (repo_with_base / "README.md").write_text("x\n", encoding="utf-8")
    commit_all(repo_with_base, "docs only")
    monkeypatch.chdir(repo_with_base)
    smoke = RecordingSmoke({})
    monkeypatch.setattr(gate, "run_smoke", smoke)
    args = ["--base-ref", "base", "--baseline", str(repo_with_base / "tests/unit/generators/sf1_size_baseline.json")]
    assert gate.main(args) == 0
    assert smoke.calls == []
