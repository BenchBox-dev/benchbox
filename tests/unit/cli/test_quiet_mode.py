import io
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner

import benchbox.cli.commands.run
import benchbox.cli.main
from benchbox.cli.main import cli
from benchbox.utils.printing import get_console, quiet_console, set_quiet

_run_mod = sys.modules["benchbox.cli.commands.run"]
_main_mod = sys.modules["benchbox.cli.main"]

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def test_quiet_mode_suppresses_decorative_output_generate_phase():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--phases",
            "generate",
            "--quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Benchmark completed" not in result.output
    assert "Initializing" not in result.output
    for line in result.output.splitlines():
        if line.strip():
            assert line.strip().startswith("/") or "benchmark_runs" in line, (
                f"Unexpected non-path output in quiet mode: {line!r}"
            )


def test_default_mode_outputs_messages():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--phases",
            "generate",
        ],
    )
    assert result.exit_code == 0
    assert "Initializing" in result.output or "Data-only" in result.output


def test_quiet_flag_suppresses_adapter_run_benchmark_output():
    set_quiet(True)
    try:
        console = get_console()
        assert isinstance(console.file, io.StringIO)

        adapter_messages = [
            "Connecting to DuckDB...",
            "✅ Database being reused - skipping schema creation and data loading",
            "Validating benchmark data...",
            "✅ Data validation passed",
            "Executing benchmark queries (power mode)...",
        ]
        for msg in adapter_messages:
            quiet_console.print(msg)

        sink = console.file
        assert isinstance(sink, io.StringIO)
    finally:
        set_quiet(False)


def test_adapter_uses_quiet_console_not_local_console():
    from benchbox.platforms.base import adapter as adapter_module

    assert hasattr(adapter_module, "quiet_console")
    assert adapter_module.quiet_console is quiet_console


@pytest.mark.parametrize(
    ("module_path", "attr_name"),
    [
        ("benchbox.cli.commands.results", "console"),
        ("benchbox.cli.commands.report", "console"),
        ("benchbox.cli.commands.show_plan", "console"),
        ("benchbox.cli.commands.compare_plans", "console"),
        ("benchbox.cli.commands.plan_history", "console"),
    ],
)
def test_cli_commands_use_shared_quiet_console(module_path: str, attr_name: str) -> None:
    import importlib

    module = importlib.import_module(module_path)
    assert getattr(module, attr_name) is quiet_console


def test_quiet_console_proxy_delegates_when_not_quiet():
    set_quiet(False)
    try:
        console = get_console()
        with console.capture() as capture:
            quiet_console.print("visible message")
        assert "visible message" in capture.get()
    finally:
        set_quiet(False)


def _invoke_run_quiet_with_export(quiet: bool):
    from benchbox.core.schemas import DatabaseConfig

    mock_result = Mock()
    mock_result.validation_status = "PASSED"
    mock_result.execution_id = "test-exec-id"

    database_config = DatabaseConfig(type="duckdb", name="DuckDB")
    mock_db_manager = MagicMock()
    mock_db_manager.create_config.return_value = database_config

    mock_bench_manager = MagicMock()
    mock_bench_manager.benchmarks = {
        "tpch": {
            "display_name": "TPC-H",
            "class": Mock(),
            "description": "TPC-H",
            "estimated_time_range": (1, 5),
        }
    }
    mock_bench_manager.validate_scale_factor = Mock()

    mock_orchestrator = MagicMock()
    mock_orchestrator.execute_benchmark.return_value = mock_result
    mock_orchestrator.directory_manager.get_result_path.return_value = "/tmp/test.json"
    mock_orchestrator.directory_manager.results_dir = "/tmp"

    runner = CliRunner()
    args = [
        "run",
        "--platform",
        "duckdb",
        "--benchmark",
        "tpch",
        "--scale",
        "0.01",
        "--non-interactive",
        "--phases",
        "power",
    ]
    if quiet:
        args.append("--quiet")

    with (
        patch.object(_run_mod, "DatabaseManager", return_value=mock_db_manager),
        patch.object(_run_mod, "BenchmarkManager", return_value=mock_bench_manager),
        patch.object(_run_mod, "BenchmarkOrchestrator", return_value=mock_orchestrator),
        patch.object(_run_mod, "SystemProfiler"),
        patch.object(_main_mod, "get_config_manager") as mock_cfg,
        patch.object(_run_mod, "_execute_orchestrated_run", return_value=mock_result),
        patch.object(_run_mod, "_export_orchestrated_result", return_value={"json": "/tmp/result.json"}),
        patch.object(_run_mod, "_render_post_run_charts"),
        patch("benchbox.cli.preferences.save_last_run_config"),
    ):
        mock_cfg.return_value.get.side_effect = lambda key, default=None: default
        return runner.invoke(cli, args)


def test_quiet_mode_emits_result_filepath_to_stdout():
    result = _invoke_run_quiet_with_export(quiet=True)
    assert result.exit_code == 0, result.output
    assert "/tmp/result.json" in result.output


def test_quiet_mode_suppresses_decorative_output():
    result = _invoke_run_quiet_with_export(quiet=True)
    assert result.exit_code == 0, result.output
    assert "Benchmark completed" not in result.output
    assert "JSON:" not in result.output


def test_normal_mode_does_not_double_print_filepath():
    result = _invoke_run_quiet_with_export(quiet=False)
    assert result.exit_code == 0, result.output
    assert result.output.count("/tmp/result.json") == 1
