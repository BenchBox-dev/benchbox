from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scripts import analyze_polars_version_matrix as analyzer, run_polars_version_matrix as runner
from scripts.polars_matrix_spec import POLARS, SETUP_ENGINES, PolarsMatrixSpec, round_order

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GOLDEN_DRY_RUN = Path(__file__).resolve().parents[2] / "fixtures" / "version_matrix" / "polars_dry_run.txt"
REFERENCE = {"tpch": {"0:1": 4, "0:2": 5}}
PINS = {"benchbox_commit": "abc", "pyarrow": "1.0", "thread_count": 10, "datagen_manifests": {"tpch_sf10": "h"}}


def _small_spec(qualification_sha: str = "") -> PolarsMatrixSpec:
    return replace(
        POLARS,
        versions=("1.31.0", "2.0.0"),
        setup_versions=(("A", ("1.31.0", "2.0.0")), ("B", ("2.0.0",)), ("C", ("2.0.0",))),
        benchmarks=(("tpch", 10.0),),
        expected_queries=(("tpch", 2),),
        reference_version="2.0.0",
        qualification_sha256=qualification_sha,
    )


def _qualification(tmp_path: Path, *, clean: bool = True, divergences: dict[str, Any] | None = None) -> Path:
    rows = [
        {"version": version, "setup": setup, "benchmark": "tpch", "clean": clean}
        for version, setup in (("1.31.0", "A"), ("2.0.0", "A"), ("2.0.0", "B"), ("2.0.0", "C"))
    ]
    payload = {
        "scale_factor": 10.0,
        "eligibility": rows,
        "reference": {"row_counts": REFERENCE},
        "known_divergences": divergences or {},
    }
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _record(
    round_index: int,
    version: str,
    setup: str,
    total_ms: float,
    *,
    outcome: str = "completed",
    validation: str = "uncertain",
    rows: tuple[int, int] = (4, 5),
    rechunk_effective: bool | None = False,
) -> dict[str, Any]:
    return {
        "round": round_index,
        "position": 1,
        "benchmark": "tpch",
        "version": version,
        "setup": setup,
        "engine": SETUP_ENGINES[setup],
        "scale": 10.0,
        "outcome": outcome,
        "peak_rss_gib": 4.0,
        "swap_out_growth_gib": 0.0,
        "validation": validation,
        "rechunk_effective": rechunk_effective,
        "engine_requested": SETUP_ENGINES[setup],
        "polars_version": version,
        "polars_runtime_version": version,
        "installed": {"polars": version},
        "queries": {
            "0:1": {"rows": rows[0], "status": "SUCCESS", "ms": total_ms / 2},
            "0:2": {"rows": rows[1], "status": "SUCCESS", "ms": total_ms / 2},
        },
    }


def _write_manifest(tmp_path: Path, spec: PolarsMatrixSpec, records: list[dict[str, Any]], name: str = "m") -> Path:
    manifest = {
        "spec": spec.snapshot(),
        "complete": True,
        "workload_seed": None,
        "shuffle_seed": 7,
        "pins": PINS,
        "records": records,
    }
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _full_records(timings: dict[tuple[str, str], list[float]], **overrides: Any) -> list[dict[str, Any]]:
    return [
        _record(index, version, setup, totals[index - 1], **overrides)
        for (version, setup), totals in timings.items()
        for index in (1, 2, 3)
    ]


TIMINGS = {
    ("1.31.0", "A"): [100.0, 101.0, 102.0],
    ("2.0.0", "A"): [50.0, 51.0, 52.0],
    ("2.0.0", "B"): [95.0, 96.0, 97.0],
    ("2.0.0", "C"): [90.0, 105.0, 120.0],
}


def _analyze(tmp_path: Path, records: list[dict[str, Any]], **qualification: Any) -> dict[str, Any]:
    qualification_path = _qualification(tmp_path, **qualification)
    spec = _small_spec(hashlib.sha256(qualification_path.read_bytes()).hexdigest())
    return analyzer.analyze(spec, [_write_manifest(tmp_path, spec, records)], qualification_path)


def _cell(result: dict[str, Any], version: str, setup: str) -> dict[str, Any]:
    return next(cell for cell in result["cells"] if cell["version"] == version and cell["setup"] == setup)


def test_spec_records_versions_scales_setups_and_qualification_hash() -> None:
    assert POLARS.versions == ("1.31.0", "1.35.2", "1.40.1", "1.44.2", "2.0.0")
    assert POLARS.setups == {
        "A": ("1.31.0", "1.35.2", "1.40.1", "1.44.2", "2.0.0"),
        "B": ("2.0.0",),
        "C": ("1.31.0", "1.40.1", "1.44.2", "2.0.0"),
    }
    assert POLARS.benchmarks == (("tpch", 10.0), ("tpcds", 10.0), ("clickbench", 10.0), ("ssb", 10.0))
    assert POLARS.thread_count == 10
    assert POLARS.rounds == 3
    assert POLARS.iterations == 1
    assert len(POLARS.qualification_sha256) == 64


def test_dry_run_lists_every_invocation_and_matches_the_formula(capsys: pytest.CaptureFixture[str]) -> None:
    assert runner.main(["--output-dir", "unused", "--shuffle-seed", "1", "--dry-run"]) == 0

    lines = capsys.readouterr().out.splitlines()
    invocations = [line for line in lines if line.startswith("round ")]
    per_benchmark = len(POLARS.setups["A"]) + len(POLARS.setups["B"]) + len(POLARS.setups["C"])
    assert len(invocations) == POLARS.rounds * per_benchmark * len(POLARS.benchmarks) == 120
    assert len(invocations) == POLARS.expected_invocations
    assert lines == GOLDEN_DRY_RUN.read_text(encoding="utf-8").splitlines()


def test_round_order_is_a_reproducible_shuffle_covering_every_cell_once() -> None:
    expected = sorted(cell.cell_id for cell in POLARS.cells())
    for round_index in (1, 2, 3):
        order = round_order(POLARS, 11, round_index)
        assert sorted(cell.cell_id for cell in order) == expected
        assert order == round_order(POLARS, 11, round_index)
    assert round_order(POLARS, 11, 1) != round_order(POLARS, 11, 2)
    assert round_order(POLARS, 11, 1) != round_order(POLARS, 12, 1)


def test_shuffle_seed_is_never_a_workload_seed() -> None:
    lines = runner.plan_lines(POLARS, shuffle_seed=12345, first_round=1, rounds=3)

    assert not any("--seed" in line.split() for line in lines)
    assert not any("12345" in line.replace("seed 12345", "") for line in lines)


def test_cell_command_pins_rechunk_engine_and_single_iteration() -> None:
    cell = next(item for item in POLARS.cells() if item.setup == "B")
    command = runner.cell_command(POLARS, cell)

    assert command[command.index("--iterations") + 1] == "1"
    assert command[command.index("--platform") + 1] == "polars-df"
    assert command[command.index("rechunk=false") - 1 : command.index("rechunk=false") + 1] == [
        "--platform-option",
        "rechunk=false",
    ]
    assert command[command.index("--execution-engine") + 1] == "in-memory"
    assert not any(part.startswith("engine=") for part in command)


def test_environment_check_rejects_stale_runtime_and_wrong_version() -> None:
    packages = runner.parse_freeze("polars==1.31.0\npolars-runtime-32==1.44.2\npyarrow==20.0.0\n")

    with pytest.raises(RuntimeError, match="stale Polars runtime"):
        runner.verify_installation(packages, "1.31.0")
    with pytest.raises(RuntimeError, match="expected polars==2.0.0"):
        runner.verify_installation(packages, "2.0.0")
    assert runner.verify_installation({"polars": "1.40.1", "polars-runtime-64": "1.40.1"}, "1.40.1")["runtimes"] == {
        "polars-runtime-64": "1.40.1"
    }


@pytest.mark.parametrize("key", ["benchbox_commit", "pyarrow", "datagen_manifests"])
def test_runner_refuses_to_continue_when_a_pinned_value_changes(key: str) -> None:
    changed = {**PINS, key: "different"}

    with pytest.raises(RuntimeError, match=key):
        runner.check_pins(PINS, changed)
    runner.check_pins(PINS, dict(PINS))


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "completed"),
        ({"peak_rss_gib": 15.0}, "resource-policy-exceeded"),
        ({"swap_growth_gib": 1.5}, "resource-policy-exceeded"),
        ({"safety_abort": True, "exit_code": -9}, "resource-policy-exceeded"),
        ({"timed_out": True, "exit_code": -9}, "timeout"),
        ({"exit_code": -9}, "oom-killed"),
        ({"exit_code": 1}, "execution-failure"),
        ({"result_ok": False}, "execution-failure"),
    ],
)
def test_outcome_classes_follow_the_resource_policy(kwargs: dict[str, Any], expected: str) -> None:
    arguments = {
        "exit_code": 0,
        "timed_out": False,
        "safety_abort": False,
        "result_ok": True,
        "peak_rss_gib": 5.0,
        "swap_growth_gib": 0.0,
    }
    arguments.update(kwargs)

    assert runner.classify_outcome(POLARS, **arguments) == expected


def test_result_extraction_records_engine_runtime_and_per_query_row_counts() -> None:
    cell = next(
        item for item in POLARS.cells() if item.benchmark == "tpch" and item.setup == "C" and item.version == "2.0.0"
    )
    queries = [
        {"id": str(number), "stream": 0, "run_type": "warmup", "rows": 1, "ms": 1.0, "status": "SUCCESS"}
        for number in range(1, 23)
    ]
    queries += [
        {"id": str(number), "stream": 0, "run_type": "measurement", "rows": number, "ms": 2.0, "status": "SUCCESS"}
        for number in range(1, 23)
    ]
    payload = {
        "platform": {
            "version": cell.version,
            "config": {
                "rechunk_effective": False,
                "engine_requested": "streaming",
                "collect_engine_argument": "streaming",
                "polars_runtime_package": "polars-runtime-32",
                "polars_runtime_version": cell.version,
            },
        },
        "summary": {"validation": "uncertain", "queries": {"total": 22, "failed": 0, "passed": 22}},
        "queries": queries,
    }

    extracted = runner.extract_result(payload, POLARS, cell)

    assert extracted["result_ok"] is True
    assert extracted["queries"]["0:7"] == {"rows": 7, "status": "SUCCESS", "ms": 2.0}
    assert len(extracted["queries"]) == 22
    assert extracted["collect_engine_argument"] == "streaming"
    payload["platform"]["config"]["rechunk_effective"] = True
    assert runner.extract_result(payload, POLARS, cell)["result_ok"] is False


def test_result_extraction_reads_the_execution_engine_receipt() -> None:
    cell = next(
        item for item in POLARS.cells() if item.benchmark == "tpch" and item.setup == "C" and item.version == "2.0.0"
    )
    queries = [
        {"id": str(number), "stream": 0, "run_type": "measurement", "rows": number, "ms": 2.0, "status": "SUCCESS"}
        for number in range(1, 23)
    ]
    payload = {
        "platform": {
            "version": cell.version,
            "execution_engine": {
                "requested": "streaming",
                "applied": "streaming",
                "applied_class": "streaming",
                "applied_native": {"collect_kwargs": {"engine": "streaming"}},
                "resolution": "explicit",
                "observed": "not_captured",
                "observed_source": "none",
            },
            "config": {
                "rechunk_effective": False,
                "polars_runtime_package": "polars-runtime-32",
                "polars_runtime_version": cell.version,
            },
        },
        "summary": {"validation": "uncertain", "queries": {"total": 22, "failed": 0, "passed": 22}},
        "queries": queries,
    }

    extracted = runner.extract_result(payload, POLARS, cell)

    assert extracted["result_ok"] is True
    assert extracted["engine_requested"] == "streaming"
    assert extracted["execution_engine"] == payload["platform"]["execution_engine"]
    assert extracted["collect_engine_argument"] == "streaming"


def test_analyzer_accepts_uncertain_status_only_with_clean_evidence_and_matching_counts(tmp_path: Path) -> None:
    accepted = _analyze(tmp_path, _full_records(TIMINGS))

    assert all(cell["qualified"] for cell in accepted["cells"])


def test_analyzer_rejects_a_cell_without_qualification_evidence(tmp_path: Path) -> None:
    result = _analyze(tmp_path, _full_records(TIMINGS), clean=False)

    cell = _cell(result, "2.0.0", "A")
    assert cell["qualified"] is False
    assert any("without clean qualification evidence" in reason for reason in cell["reasons"])
    assert cell["headline"] is None


def test_analyzer_accepts_a_passed_status_without_evidence_rows(tmp_path: Path) -> None:
    result = _analyze(tmp_path, _full_records(TIMINGS, validation="passed"), clean=False)

    assert all(cell["qualified"] for cell in result["cells"])


def test_analyzer_rejects_row_counts_that_disagree_with_the_reference(tmp_path: Path) -> None:
    records = _full_records(TIMINGS)
    for record in records:
        if record["version"] == "2.0.0" and record["setup"] == "B" and record["round"] == 2:
            record["queries"]["0:2"]["rows"] = 6

    result = _analyze(tmp_path, records)

    cell = _cell(result, "2.0.0", "B")
    assert cell["qualified"] is False
    assert any("row counts differ from the DuckDB reference: 0:2" in reason for reason in cell["reasons"])
    assert _cell(result, "2.0.0", "A")["qualified"] is True


def test_analyzer_rejects_cells_with_rechunk_effective_true_or_wrong_status(tmp_path: Path) -> None:
    records = _full_records(TIMINGS)
    for record in records:
        if record["version"] == "1.31.0":
            record["rechunk_effective"] = True
        if record["version"] == "2.0.0" and record["setup"] == "C":
            record["validation"] = "failed"

    result = _analyze(tmp_path, records)

    assert any("rechunk_effective" in reason for reason in _cell(result, "1.31.0", "A")["reasons"])
    assert any("validation status failed" in reason for reason in _cell(result, "2.0.0", "C")["reasons"])


def test_analyzer_reads_the_execution_engine_receipt(tmp_path: Path) -> None:
    records = _full_records(TIMINGS)
    for record in records:
        record["execution_engine"] = {"requested": record.pop("engine_requested")}

    accepted = _analyze(tmp_path, records)

    assert all(cell["qualified"] for cell in accepted["cells"])
    broken = _full_records(TIMINGS)
    for record in broken:
        record["execution_engine"] = {"requested": "streaming"}
    rejected = _analyze(tmp_path, broken)

    assert any("engine_requested streaming" in reason for cell in rejected["cells"] for reason in cell["reasons"])


def test_analyzer_keeps_known_divergences_out_of_the_common_subset(tmp_path: Path) -> None:
    records = _full_records(TIMINGS)
    for record in records:
        record["queries"]["0:2"]["rows"] = 9
    divergences = {"tpch": {"0:2": {"reference": 5, "observed": 9}}}

    result = _analyze(tmp_path, records, divergences=divergences)

    cell = _cell(result, "1.31.0", "A")
    assert cell["qualified"] is True
    assert cell["common_query_exclusions"] == ["0:2"]
    assert cell["headline"]["full_ms"]["median"] == 101.0
    assert cell["headline"]["common_ms"]["median"] == 50.5


def test_analyzer_excluded_outcomes_are_reported_but_not_compared(tmp_path: Path) -> None:
    records = _full_records(TIMINGS)
    for record in records:
        if record["version"] == "2.0.0" and record["setup"] == "B":
            record["outcome"] = "resource-policy-exceeded"

    result = _analyze(tmp_path, records)

    cell = _cell(result, "2.0.0", "B")
    assert cell["qualified"] is False
    assert cell["headline"] is None
    assert not any(item["setup"] == "B" for item in result["comparisons"])


def test_analyzer_output_has_medians_ranges_speedups_movers_and_comparison_count(tmp_path: Path) -> None:
    result = _analyze(tmp_path, _full_records(TIMINGS))

    headline = _cell(result, "2.0.0", "C")["headline"]["common_ms"]
    assert headline == {"median": 105.0, "min": 90.0, "max": 120.0}
    assert result["comparison_count"] == 4
    by_key = {(item["version"], item["setup"], item["baseline_frame"]): item for item in result["comparisons"]}
    assert by_key[("2.0.0", "A", "oldest")]["speedup"] == pytest.approx(101.0 / 51.0)
    assert by_key[("1.31.0", "A", "reference")]["speedup"] == pytest.approx(51.0 / 101.0)
    assert by_key[("2.0.0", "B", "oldest")]["baseline_setup"] == "A"
    movers = {(item["version"], item["setup"]) for item in result["candidate_movers"]}
    assert movers == {("1.31.0", "A"), ("2.0.0", "A")}
    assert by_key[("2.0.0", "B", "oldest")]["ranges_overlap"] is False
    assert by_key[("2.0.0", "B", "oldest")]["candidate_mover"] is False
    assert by_key[("2.0.0", "C", "oldest")]["ranges_overlap"] is True
    assert by_key[("2.0.0", "C", "oldest")]["candidate_mover"] is False


def test_analyzer_refuses_a_qualification_file_that_differs_from_the_spec(tmp_path: Path) -> None:
    qualification_path = _qualification(tmp_path)
    spec = _small_spec("0" * 64)

    with pytest.raises(ValueError, match="differs from the spec hash"):
        analyzer.load_qualification(qualification_path, spec)


def test_analyzer_refuses_duplicate_or_missing_rounds(tmp_path: Path) -> None:
    records = _full_records(TIMINGS)

    with pytest.raises(ValueError, match="does not have exactly rounds"):
        _analyze(tmp_path, records[:-1])
    with pytest.raises(ValueError, match="duplicate record"):
        _analyze(tmp_path, [*records, records[0]])


def test_polars_entry_points_delegate_to_the_shared_core() -> None:
    from scripts import analyze_version_matrix as shared_analyzer, run_version_matrix as shared_runner
    from scripts.version_matrix_specs import POLARS as SHARED_POLARS

    assert POLARS is SHARED_POLARS
    assert runner.plan_lines is shared_runner.plan_lines
    assert runner.cell_command is shared_runner.cell_command
    assert runner.parse_freeze is shared_runner.parse_freeze
    assert runner.verify_installation is shared_runner.verify_installation
    assert runner.check_pins is shared_runner.check_pins
    assert runner.classify_outcome is shared_runner.classify_outcome
    assert runner.extract_result is shared_runner.extract_result
    assert runner.run_matrix is shared_runner.run_polars_matrix
    assert analyzer.analyze is shared_analyzer.analyze_polars
    assert analyzer.compare is shared_analyzer.compare_cells
    assert analyzer.write_outputs is shared_analyzer.write_polars_outputs
    assert analyzer.load_qualification is shared_analyzer.load_qualification


def test_shared_runner_serves_the_polars_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts import run_version_matrix as shared_runner

    assert shared_runner.main(["--engine", "polars", "--output-dir", "unused", "--shuffle-seed", "1", "--dry-run"]) == 0

    assert capsys.readouterr().out.splitlines() == GOLDEN_DRY_RUN.read_text(encoding="utf-8").splitlines()


def test_shared_analyzer_serves_the_polars_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import analyze_version_matrix as shared_analyzer
    from scripts.version_matrix_specs import SPECS

    records = []
    for cell in POLARS.cells():
        for index in (1, 2, 3):
            record = _record(index, cell.version, cell.setup, 100.0)
            record["benchmark"] = cell.benchmark
            records.append(record)
    rows = [
        {"version": cell.version, "setup": cell.setup, "benchmark": cell.benchmark, "clean": True}
        for cell in POLARS.cells()
    ]
    reference = {benchmark: {"0:1": 4, "0:2": 5} for benchmark in POLARS.benchmark_ids}
    qualification_path = tmp_path / "qualification.json"
    qualification_path.write_text(
        json.dumps({"scale_factor": 10.0, "eligibility": rows, "reference": {"row_counts": reference}}),
        encoding="utf-8",
    )
    spec = replace(POLARS, qualification_sha256=hashlib.sha256(qualification_path.read_bytes()).hexdigest())
    manifest_path = _write_manifest(tmp_path, spec, records)
    monkeypatch.setitem(SPECS, "polars", spec)
    output_dir = tmp_path / "out"

    assert (
        shared_analyzer.main(
            [
                "--engine",
                "polars",
                str(manifest_path),
                "--qualification",
                str(qualification_path),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )

    result = json.loads((output_dir / "polars-version-matrix-analysis.json").read_text(encoding="utf-8"))
    assert result["comparison_count"] == len(result["comparisons"])
    assert all(cell["qualified"] for cell in result["cells"])
    assert (output_dir / "polars-version-matrix-comparisons.csv").is_file()
