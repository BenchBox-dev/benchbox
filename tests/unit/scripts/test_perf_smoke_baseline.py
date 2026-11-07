from __future__ import annotations

import importlib.util
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
BASELINE = ROOT / "_project" / "baselines" / "perf_smoke_duckdb_tpch_001.json"
SOURCES = ROOT / "_project" / "baselines" / "perf_smoke_duckdb_tpch_001.sources.json"
WORKFLOWS = (ROOT / ".github" / "workflows" / "nightly-v2.yml",)


def _load():
    spec = importlib.util.spec_from_file_location("perf_smoke_baseline", ROOT / "scripts/perf_smoke_baseline.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


baseline_tool = _load()


def _result(
    timestamp: str, values: dict[str, list[float]], *, reverse: bool = False, cpu: str = "cpu-a", total: float = 100.0
) -> dict[str, Any]:
    entries = []
    for query_id, samples in values.items():
        entries.append(
            {"id": query_id, "iter": 0, "ms": samples[0], "run_type": "warmup", "status": "SUCCESS", "rows": 1}
        )
    for iteration in (1, 2):
        for query_id, samples in values.items():
            entries.append(
                {
                    "id": query_id,
                    "iter": iteration,
                    "ms": samples[iteration],
                    "run_type": "measurement",
                    "status": "SUCCESS",
                    "rows": 1,
                }
            )
    if reverse:
        entries.reverse()
    return {
        "execution": {"timestamp": timestamp},
        "queries": entries,
        "run": {"id": timestamp, "query_time_ms": 0},
        "summary": {"timing": {"total_ms": total}},
        "environment": {"cpu_model": cpu, "cpu_count": 4, "os": "Linux"},
        "platform": {"version": "1.5.5"},
    }


def _results(spread: float = 2.0, *, count: int = 5, cpu: str = "cpu-a", total: float = 100.0) -> list[dict[str, Any]]:
    results = []
    for index in range(count):
        offset = spread * index / 4
        results.append(
            _result(
                f"2026-10-0{index + 1}T00:00:00",
                {"1": [10.0 + offset, 10.0 + offset, 10.0 + offset], "2": [20.0, 20.0 + offset, 20.0 - offset]},
                reverse=index % 2 == 1,
                cpu=cpu,
                total=total,
            )
        )
    return results


def test_every_entry_is_the_median_of_the_same_entry_across_results() -> None:
    merged = baseline_tool.median_result(_results())

    by_key = {(entry["id"], entry["run_type"], entry["iter"]): entry["ms"] for entry in merged["queries"]}
    assert by_key[("1", "measurement", 1)] == 11.0
    assert by_key[("2", "measurement", 1)] == 21.0
    assert by_key[("2", "measurement", 2)] == 19.0


def test_summary_timing_is_recomputed_from_the_measured_entries() -> None:
    merged = baseline_tool.median_result(_results())

    measured = [entry["ms"] for entry in merged["queries"] if entry["run_type"] == "measurement"]
    timing = merged["summary"]["timing"]
    assert timing["total_ms"] == round(sum(measured), 1)
    assert timing["avg_ms"] == round(statistics.mean(measured), 1)
    assert timing["min_ms"] == min(measured)
    assert timing["max_ms"] == max(measured)
    assert merged["run"]["query_time_ms"] == round(sum(measured))


def test_other_fields_come_from_the_newest_result() -> None:
    merged = baseline_tool.median_result(_results())

    assert merged["execution"]["timestamp"] == "2026-10-05T00:00:00"


def test_sources_are_not_modified() -> None:
    results = _results()
    snapshot = json.dumps(results, sort_keys=True)

    baseline_tool.median_result(results)

    assert json.dumps(results, sort_keys=True) == snapshot


def test_fewer_than_five_results_are_refused() -> None:
    with pytest.raises(baseline_tool.BaselineError, match="at least 5"):
        baseline_tool.median_result(_results()[:4])


def test_results_with_different_queries_are_refused() -> None:
    results = _results()
    results[2]["queries"].pop()

    with pytest.raises(baseline_tool.BaselineError, match="different queries"):
        baseline_tool.median_result(results)


def test_a_failed_query_in_a_source_is_refused() -> None:
    results = _results()
    results[1]["queries"][0]["status"] = "FAILED"

    with pytest.raises(baseline_tool.BaselineError, match="failed query"):
        baseline_tool.median_result(results)


def test_spread_is_measured_on_the_last_value_recorded_for_each_query() -> None:
    spread, query = baseline_tool.largest_spread_ms(_results(spread=8.0))

    assert spread == pytest.approx(8.0)
    assert query in {"1", "2"}


@pytest.mark.parametrize(
    ("spread", "floor"),
    [(0.0, 5), (3.9, 5), (4.0, 5), (4.1, 6), (5.4, 7), (8.0, 10), (11.0, 14), (12.0, 15), (40.0, 15)],
)
def test_floor_is_flat_until_the_spread_passes_four_milliseconds(spread: float, floor: int) -> None:
    assert baseline_tool.regression_floor_ms(spread) == floor


def test_the_slowest_cpu_model_with_enough_results_is_selected() -> None:
    results = _results(cpu="Fast CPU", total=400.0) + _results(cpu="Slow  CPU", total=550.0)

    assert baseline_tool.select_runner_class(results) == "slow cpu"


def test_a_slower_cpu_model_with_too_few_results_is_not_selected() -> None:
    results = _results(cpu="Fast CPU", total=400.0) + _results(count=2, cpu="Slow CPU", total=900.0)

    assert baseline_tool.select_runner_class(results) == "fast cpu"


def test_no_cpu_model_with_enough_results_is_refused() -> None:
    results = _results(count=3, cpu="A") + _results(count=3, cpu="B")

    with pytest.raises(baseline_tool.BaselineError, match="no CPU model has 5"):
        baseline_tool.select_runner_class(results)


def test_an_explicit_cpu_model_overrides_the_choice_but_still_needs_enough_results() -> None:
    results = _results(cpu="Fast CPU", total=400.0) + _results(cpu="Slow CPU", total=550.0)

    assert baseline_tool.select_runner_class(results, cpu_model="fast cpu") == "fast cpu"
    with pytest.raises(baseline_tool.BaselineError, match="fewer than 5"):
        baseline_tool.select_runner_class(results, cpu_model="other")


def _write_sources(tmp_path: Path, results: list[dict[str, Any]]) -> list[str]:
    arguments = []
    for index, result in enumerate(results):
        path = tmp_path / f"result-{index}.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        arguments.append(f"{9000 + index}={path}")
    return arguments


def test_main_builds_from_the_selected_cpu_model_and_records_every_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fast = _results(spread=5.4, cpu="Fast CPU", total=400.0)
    slow = _results(spread=5.4, cpu="Slow CPU", total=550.0)
    for index, result in enumerate(fast):
        result["execution"]["timestamp"] = f"2026-09-0{index + 1}T00:00:00"
    arguments = _write_sources(tmp_path, fast + slow)
    output, sources_output = tmp_path / "baseline.json", tmp_path / "sources.json"

    code = baseline_tool.main([*arguments, "--output", str(output), "--sources-output", str(sources_output)])

    assert code == 0
    record = json.loads(sources_output.read_text(encoding="utf-8"))
    assert record["baseline_cpu_model"] == "Slow CPU"
    assert record["regression_floor"]["floor_ms"] == 7
    assert [run["in_baseline"] for run in record["source_runs"]].count(True) == 5
    assert {run["cpu_model"] for run in record["source_runs"] if not run["in_baseline"]} == {"Fast CPU"}
    assert json.loads(output.read_text(encoding="utf-8"))["environment"]["cpu_model"] == "Slow CPU"
    assert "floor 7 ms" in capsys.readouterr().out


def test_main_reports_a_bad_source_argument(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = baseline_tool.main(
        ["not-a-pair", "--output", str(tmp_path / "b.json"), "--sources-output", str(tmp_path / "s.json")]
    )

    assert code == 2
    assert "RUN_ID=PATH" in capsys.readouterr().err


def test_checked_in_baseline_has_a_sources_record_with_enough_runs() -> None:
    record = json.loads(SOURCES.read_text(encoding="utf-8"))
    in_baseline = [run for run in record["source_runs"] if run["in_baseline"]]

    assert len(in_baseline) >= baseline_tool.MIN_SOURCE_RESULTS
    assert {run["cpu_model"] for run in in_baseline} == {record["baseline_cpu_model"]}
    assert len({run["workflow_run_id"] for run in record["source_runs"]}) == len(record["source_runs"])
    assert json.loads(BASELINE.read_text(encoding="utf-8"))["queries"]


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_the_gate_floor_matches_the_one_recorded_with_the_baseline(workflow: Path) -> None:
    record = json.loads(SOURCES.read_text(encoding="utf-8"))
    used = re.findall(r"--min-regression-delta (\d+)ms", workflow.read_text(encoding="utf-8"))

    assert used == [str(record["regression_floor"]["floor_ms"])]
