from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

import benchbox.cli.benchmarks as bench_mod
from benchbox.cli.benchmarks import BenchmarkManager
from benchbox.cli.dryrun import DryRunDisplay, display_interactive_preview
from benchbox.cli.main import cli
from benchbox.core.generation_estimate import DATA_GENERATION, QUERY_GENERATION, estimate_generation
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig, DryRunResult

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

HOST = "darwin-arm64 (Apple M4, 10 cores, 16 GiB)"
PRESENT = " (if the data is not already present)"


def wide_console() -> tuple[Console, StringIO]:
    stream = StringIO()
    return Console(file=stream, force_terminal=False, color_system=None, width=400), stream


def measured_line(kind: str, benchmark: str, scale: float) -> str:
    estimate = estimate_generation(kind, benchmark, scale)
    assert estimate is not None
    return estimate.message(preview=True)


def test_interactive_summary_shows_the_measured_generation_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    console, stream = wide_console()
    monkeypatch.setattr(bench_mod, "console", console)
    manager = BenchmarkManager()

    manager._display_configuration_summary(
        "tpch",
        manager.benchmarks["tpch"],
        scale_factor=1,
        concurrency=None,
        queries=None,
        compress_data=False,
        compression_type="zstd",
        compression_level=None,
    )

    assert (
        f"TPC-H data generation{PRESENT}, scale 1: Rough estimate: generation will take about 12 seconds (measured on {HOST} at scale 0.01, 0.1, 1, 10)."
        in stream.getvalue()
    )
    assert "Est. Time:" in stream.getvalue()


def test_interactive_scale_table_shows_the_measured_generation_estimate_per_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console, stream = wide_console()
    monkeypatch.setattr(bench_mod, "console", console)
    manager = BenchmarkManager()

    manager._display_scale_estimates(
        "tpcds", manager.benchmarks["tpcds"], [1, 100], {"memory_gb": 64, "cpu_cores": 8, "architecture": "arm64"}
    )

    output = stream.getvalue()
    assert measured_line(DATA_GENERATION, "tpcds", 1) in output
    assert measured_line(DATA_GENERATION, "tpcds", 100) in output
    assert "extrapolated for scale 100, above the largest measured scale 10" in output


def test_dry_run_preview_shows_the_measured_generation_estimate() -> None:
    console, stream = wide_console()

    display_interactive_preview(
        database_config=DatabaseConfig(type="duckdb", name="duckdb"),
        benchmark_config=BenchmarkConfig(name="tpcds", display_name="TPC-DS", scale_factor=10),
        phases=["generate", "power"],
        console_obj=console,
    )

    output = stream.getvalue()
    assert measured_line(DATA_GENERATION, "tpcds", 10) in output
    assert measured_line(QUERY_GENERATION, "tpcds", 10) in output


def test_dry_run_result_shows_the_measured_generation_estimate() -> None:
    console, stream = wide_console()
    result = DryRunResult(
        benchmark_config={"name": "tpch", "scale_factor": 10, "options": {}},
        database_config={"type": "duckdb", "name": "DuckDB"},
        system_profile={"cpu_cores_logical": 8, "memory_total_gb": 16.0, "os_name": "Darwin"},
        platform_config={},
        queries={},
    )

    DryRunDisplay(console=console).display_dry_run_results(result)

    assert measured_line(DATA_GENERATION, "tpch", 10) in stream.getvalue()


def test_no_generation_estimate_for_an_unmeasured_benchmark_in_the_dry_run() -> None:
    console, stream = wide_console()
    result = DryRunResult(
        benchmark_config={"name": "clickbench", "scale_factor": 1, "options": {}},
        database_config={"type": "duckdb", "name": "DuckDB"},
        system_profile={"cpu_cores_logical": 8, "memory_total_gb": 16.0, "os_name": "Darwin"},
        platform_config={},
        queries={},
    )

    DryRunDisplay(console=console).display_dry_run_results(result)

    assert "Rough estimate" not in stream.getvalue()


def test_interactive_scale_lines_name_their_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    console, stream = wide_console()
    monkeypatch.setattr(bench_mod, "console", console)
    manager = BenchmarkManager()

    manager._display_scale_estimates(
        "tpch", manager.benchmarks["tpch"], [0.1, 10], {"memory_gb": 64, "cpu_cores": 8, "architecture": "arm64"}
    )

    output = stream.getvalue()
    assert f"TPC-H data generation{PRESENT}, scale 0.1: Rough estimate" in output
    assert f"TPC-H data generation{PRESENT}, scale 10: Rough estimate" in output


@pytest.mark.parametrize(
    ("phases", "data", "query"),
    [
        (["generate"], True, False),
        (["power"], True, True),
        (["generate", "power"], True, True),
        (["load"], True, False),
    ],
)
def test_preview_shows_only_the_estimates_for_the_chosen_phases(phases: list[str], data: bool, query: bool) -> None:
    console, stream = wide_console()

    display_interactive_preview(
        database_config=DatabaseConfig(type="duckdb", name="duckdb"),
        benchmark_config=BenchmarkConfig(name="tpcds", display_name="TPC-DS", scale_factor=10),
        phases=phases,
        console_obj=console,
    )

    output = stream.getvalue()
    assert (f"TPC-DS data generation{PRESENT}, scale 10:" in output) is data
    assert ("TPC-DS query generation, scale 10:" in output) is query


@pytest.mark.parametrize(
    ("requested", "data", "query"),
    [
        (["power"], True, True),
        (["generate"], True, False),
        (None, True, True),
        ([], True, True),
        (["load"], True, False),
    ],
)
def test_dry_run_result_shows_only_the_estimates_for_the_requested_phases(
    requested: list[str] | None, data: bool, query: bool
) -> None:
    console, stream = wide_console()
    options = {} if requested is None else {"requested_phases": requested}
    result = DryRunResult(
        benchmark_config={"name": "tpcds", "scale_factor": 10, "options": options},
        database_config={"type": "duckdb", "name": "DuckDB"},
        system_profile={"cpu_cores_logical": 8, "memory_total_gb": 16.0, "os_name": "Darwin"},
        platform_config={},
        queries={},
    )

    DryRunDisplay(console=console).display_dry_run_results(result)

    output = stream.getvalue()
    assert (f"TPC-DS data generation{PRESENT}, scale 10:" in output) is data
    assert ("TPC-DS query generation, scale 10:" in output) is query


@pytest.mark.parametrize(
    ("phases", "data", "query"),
    [("power", True, True), ("generate", True, False), ("generate,power", True, True), ("load", True, False)],
)
def test_dry_run_command_shows_only_the_estimates_for_the_phases_option(
    tmp_path: Path, phases: str, data: bool, query: bool
) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--dry-run",
            str(tmp_path),
            "--platform",
            "duckdb",
            "--benchmark",
            "tpcds",
            "--scale",
            "1",
            "--phases",
            phases,
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (f"TPC-DS data generation{PRESENT}, scale 1:" in result.output) is data
    assert ("TPC-DS query generation, scale 1:" in result.output) is query
