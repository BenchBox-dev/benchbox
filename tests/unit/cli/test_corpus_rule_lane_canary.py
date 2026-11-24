from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli
from benchbox.core.results import corpus_rule
from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.models import (
    BenchmarkResults,
    ExecutionPhases,
    PowerTestPhase,
    SetupPhase,
    ThroughputStream,
    ThroughputTestPhase,
)
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.submit_classification import result_refused_at_submission
from benchbox.core.run_service import map_phases_to_execution_type
from tests.unit.core.results.test_tpch_refresh_metrics import _function

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_run_module = sys.modules["benchbox.cli.commands.run"]

STREAMS = 2
LANE_CASES = {
    "combined": (["--scale", "1", "--phases", "power,throughput,maintenance"], (corpus_rule.LANE_COMBINED,), True),
    "power-only": (["--scale", "1", "--phases", "power"], (corpus_rule.LANE_POWER_ONLY,), True),
    "throughput": (["--scale", "1", "--phases", "throughput"], (corpus_rule.LANE_THROUGHPUT,), True),
    "subscale": (["--scale", "0.01", "--phases", "power"], (corpus_rule.LANE_SUBSCALE,), False),
    "unranked": (["--scale", "2", "--phases", "power"], (), True),
}


def _refresh_stream() -> dict[str, Any]:
    return {
        "first_set": 6,
        "status": "COMPLETED",
        "pair_count": 2,
        "pairs": [
            {
                "update_set": set_id,
                "status": "SUCCESS",
                "functions": [_function("RF1", set_id), _function("RF2", set_id)],
            }
            for set_id in (6, 7)
        ],
        "start_time": "2026-01-01T00:00:00",
        "end_time": "2026-01-01T00:00:01",
        "error": None,
    }


def _measured_rows(stream_id: int, test_type: str) -> list[dict[str, Any]]:
    return [
        normalize_query_result(
            {
                "query_id": str(query_id),
                "status": "SUCCESS",
                "execution_time_seconds": 1.0,
                "iteration": 1,
                "stream_id": stream_id,
                "run_type": "measurement",
                "test_type": test_type,
                "rows_returned": 1,
                "row_count_validation": {"status": "PASSED", "expected": 1, "actual": 1},
            }
        )
        for query_id in range(1, 23)
    ]


def _result_from_cli_request(captured: dict[str, Any]) -> BenchmarkResults:
    phases: list[str] = captured["phases"]
    config = captured["config"]
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name="TPC-H",
            scale_factor=config.scale_factor,
            test_type=map_phases_to_execution_type(phases),
            benchmark_id="tpch",
        ),
        platform=PlatformInfoInput(name="Stub", execution_mode="sql"),
    )
    refresh = "maintenance" in phases
    power = throughput = None
    if "power" in phases:
        for row in _measured_rows(0, "power"):
            builder.add_query_result(row)
        power = PowerTestPhase(
            start_time="2026-01-01T00:00:00",
            end_time="2026-01-01T00:00:01",
            duration_ms=1000,
            query_executions=[],
            geometric_mean_time=1.0,
            power_at_size=100.0,
            refresh_first_set=5 if refresh else None,
            refresh_functions=[_function("RF1", 5), _function("RF2", 5)] if refresh else None,
            query_count=22,
            success=True,
        )
    if "throughput" in phases:
        for stream_id in range(1, STREAMS + 1):
            for row in _measured_rows(stream_id, "throughput"):
                builder.add_query_result(row)
        throughput = ThroughputTestPhase(
            start_time="2026-01-01T00:00:00",
            end_time="2026-01-01T00:00:01",
            duration_ms=1000,
            num_streams=STREAMS,
            streams=[
                ThroughputStream(stream_id=stream_id, start_time="", end_time="", duration_ms=500, query_executions=[])
                for stream_id in range(1, STREAMS + 1)
            ],
            total_queries_executed=22 * STREAMS,
            throughput_at_size=200.0,
            success=True,
            refresh_stream=_refresh_stream() if refresh else None,
        )
    builder.set_execution_phases(ExecutionPhases(setup=SetupPhase(), power_test=power, throughput_test=throughput))
    return builder.build()


def _build_through_the_cli(cli_benchmark_mocks: dict[str, Any], arguments: list[str], output_dir: Path) -> Path:
    captured: dict[str, Any] = {}
    exported: dict[str, Any] = {}

    def stub_executor(orchestrator, benchmark_config, system_profile, database_config, phases_to_run, **_kwargs):
        captured.update(phases=list(phases_to_run), config=benchmark_config)
        return _result_from_cli_request(captured)

    def export_result(*, result, **_kwargs):
        exported.update(ResultExporter(output_dir=output_dir, anonymize=False).export_result(result, ["json"]))
        return exported

    platform = cli_benchmark_mocks["db_manager"].return_value.create_config().type
    with (
        patch.object(_run_module, "_execute_orchestrated_run", stub_executor),
        patch.object(_run_module, "_export_orchestrated_result", export_result),
    ):
        outcome = CliRunner().invoke(
            cli,
            ["run", "--platform", platform, "--benchmark", "tpch", *arguments, "--non-interactive"],
        )
    assert outcome.exit_code == 0, outcome.output
    assert captured, "the CLI never reached the executor"
    return Path(exported["json"])


@pytest.mark.parametrize("lane", sorted(LANE_CASES))
def test_a_result_built_through_the_cli_enters_its_lane(
    cli_benchmark_mocks: dict[str, Any], lane: str, tmp_path: Path
) -> None:
    arguments, expected_lanes, publicly_admitted = LANE_CASES[lane]

    path = _build_through_the_cli(cli_benchmark_mocks, arguments, tmp_path)
    facts = corpus_rule.facts_from_path(path)
    outcome = corpus_rule.derive_outcome(facts)

    assert outcome.lanes == expected_lanes, (lane, outcome)
    assert outcome.class_name != corpus_rule.CLASS_UNDETERMINED
    assert outcome.maintainer_admission
    assert outcome.public_admission is publicly_admitted
    assert corpus_rule.public_refusal("tpch", facts.scale_factor) == (
        None if publicly_admitted else corpus_rule.CLASS_SUBSCALE
    )


def test_a_stub_run_through_the_cli_is_not_refused_by_the_submit_classifier_unless_subscale(
    cli_benchmark_mocks: dict[str, Any], tmp_path: Path
) -> None:
    from benchbox.core.results.loader import load_result_file

    for lane, (arguments, _lanes, publicly_admitted) in LANE_CASES.items():
        path = _build_through_the_cli(cli_benchmark_mocks, arguments, tmp_path / lane)
        result, _raw = load_result_file(path)
        assert result_refused_at_submission(result) is (not publicly_admitted), lane
