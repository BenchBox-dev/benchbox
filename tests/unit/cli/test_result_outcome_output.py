from __future__ import annotations

import csv
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from benchbox.cli.output import ConsoleResultFormatter
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.models import ExecutionPhases, PowerTestPhase, QueryExecution, SetupPhase
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

DERIVED_WORDING = "Derived from TPC-H, not an official TPC result"
POWER_ONLY_WORDING = "Power test only"


def _power_only_result(scale_factor: float = 1.0, stamp: str | None = None):
    ts = datetime.now(timezone.utc)
    executions = [
        QueryExecution(
            query_id=str(number),
            stream_id="power",
            execution_order=number,
            execution_time_ms=50.0 * number,
            status="SUCCESS",
            rows_returned=1,
            iteration=1,
        )
        for number in (1, 2)
    ]
    return make_benchmark_results(
        benchmark_id="tpch",
        benchmark_name="TPC-H",
        platform="duckdb",
        scale_factor=scale_factor,
        execution_id="power-only",
        timestamp=ts,
        total_queries=2,
        successful_queries=2,
        validation_status="PASSED",
        power_at_size=1234.5,
        compliance_class=stamp,
        query_results=[
            {"query_id": str(number), "execution_time_ms": 50.0 * number, "status": "SUCCESS", "rows_returned": 1}
            for number in (1, 2)
        ],
        execution_phases=ExecutionPhases(
            setup=SetupPhase(),
            power_test=PowerTestPhase(
                start_time=ts.isoformat(),
                end_time=ts.isoformat(),
                duration_ms=150,
                query_executions=executions,
                geometric_mean_time=0.07,
                power_at_size=1234.5,
            ),
        ),
    )


@pytest.mark.parametrize("stamp", [None, "official", "unofficial_subscale", "unofficial_nonstandard"])
def test_statistics_print_the_derived_class_whatever_the_stamp(stamp):
    stats = ConsoleResultFormatter.format_execution_statistics(_power_only_result(stamp=stamp))

    assert stats["class"] == "power_only"
    assert stats["label"].startswith(f"{DERIVED_WORDING}; {POWER_ONLY_WORDING}")
    assert stats["power_at_size"] == "1234.50"


@pytest.mark.parametrize(
    ("scale_factor", "expected_class", "expected_power"),
    [(0.1, "subscale", "1234.50 (subscale, unofficial)"), (2.0, "nonstandard_scale", None)],
)
def test_statistics_follow_the_scale_not_the_stamp(scale_factor, expected_class, expected_power):
    stats = ConsoleResultFormatter.format_execution_statistics(_power_only_result(scale_factor, stamp="official"))

    assert stats["class"] == expected_class
    assert stats.get("power_at_size") == expected_power


def test_result_without_facts_prints_undetermined():
    stats = ConsoleResultFormatter.format_execution_statistics(
        make_benchmark_results(
            benchmark_id="tpch", benchmark_name="TPC-H", scale_factor=1.0, compliance_class="official"
        )
    )

    assert stats["class"] != "official"
    assert stats["class"] in {"none", "undetermined"}


@patch("benchbox.cli.output.console")
def test_cli_result_output_prints_class_and_wording(mock_console):
    ConsoleResultFormatter.display_benchmark_summary(_power_only_result())

    printed = "\n".join(str(call.args[0]) for call in mock_console.print.call_args_list)
    assert "Class:[/bold blue] power_only" in printed
    assert f"{DERIVED_WORDING}; {POWER_ONLY_WORDING}" in printed
    assert "official" not in printed.replace("not an official TPC result", "")


def test_csv_export_carries_class_and_wording(tmp_path):
    exported = ResultExporter(output_dir=tmp_path).export_result(_power_only_result(), ["csv"])

    with open(exported["csv"], newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert rows
    assert {row["class"] for row in rows} == {"power_only"}
    assert all(row["label"].startswith(f"{DERIVED_WORDING}; {POWER_ONLY_WORDING}") for row in rows)
