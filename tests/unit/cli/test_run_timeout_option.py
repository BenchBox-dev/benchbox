from __future__ import annotations

import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from typing import Any

import psutil
import pytest
from click.testing import CliRunner

from benchbox.cli import run_time_limit
from benchbox.cli.commands.datagen import datagen
from benchbox.cli.commands.run import run
from benchbox.cli.commands.run_official import run_official
from benchbox.cli.config import ConfigManager
from benchbox.cli.exit_codes import ExitCode
from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core import run_service
from benchbox.core.schemas import BenchmarkConfig
from benchbox.utils.run_deadline import RUN_TIMEOUT_CONFIG_KEY, TIMED_OUT_VALIDATION_STATUS, current_run_deadline

_run_module = run_time_limit

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

RUN_ARGS = ["--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
OFFICIAL_ARGS = ["tpch", "--platform", "duckdb", "--scale", "1", "--phases", "power,throughput", "--streams", "2"]
OFFICIAL_ARGS += ["--seed", "42"]
DATAGEN_ARGS = ["--benchmark", "tpch", "--scale", "0.01"]

HARD_STOP_SCRIPT = """
import functools, subprocess, sys, time
from pathlib import Path
from click.testing import CliRunner
from benchbox.cli import run_time_limit
from benchbox.cli.commands import run as _unused
from benchbox.cli.config import ConfigManager
from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig
from benchbox.utils.run_deadline import arm_hard_stop, current_run_deadline

run_module = sys.modules["benchbox.cli.commands.run"]
orchestrator = BenchmarkOrchestrator(base_dir=sys.argv[1])

def ignores_the_stop_request(*_args, **_kwargs):
    untracked = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    Path(sys.argv[1], "untracked.pid").write_text(str(untracked.pid))
    deadline = current_run_deadline()
    deadline.enter_phase("execute")
    deadline.enter_query("Q3")
    deadline.partial_query_results = [{"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.1}]
    time.sleep(120)

def reaches_the_run(_s):
    run_module._execute_orchestrated_run(
        orchestrator,
        BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01),
        None,
        DatabaseConfig(type="duckdb", name="stub"),
        ["power"],
        quiet=True,
        no_progress=True,
        no_monitoring=True,
    )

orchestrator.execute_benchmark = ignores_the_stop_request
run_time_limit.dispatch_run = reaches_the_run
run_time_limit.arm_hard_stop = functools.partial(arm_hard_stop, grace_seconds=0.3)
CliRunner().invoke(
    run_module.run,
    ["--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", "--non-interactive", "--timeout", "1"],
    obj={"config": ConfigManager()},
)
sys.exit(0)
"""


def _obj(configured: Any = None) -> dict[str, Any]:
    config = ConfigManager()
    if configured is not None:
        config.set(RUN_TIMEOUT_CONFIG_KEY, configured)
    return {"config": config}


@pytest.fixture
def seen_limits(monkeypatch) -> list[int | None]:
    seen: list[int | None] = []

    def record(_s: Any) -> None:
        deadline = current_run_deadline()
        seen.append(None if deadline is None else deadline.timeout_seconds)

    monkeypatch.setattr(_run_module, "dispatch_run", record)
    return seen


@pytest.mark.parametrize(
    ("command", "args"),
    [(run, RUN_ARGS), (run_official, OFFICIAL_ARGS), (datagen, DATAGEN_ARGS)],
    ids=["run", "run-official", "datagen"],
)
def test_the_option_reaches_the_deadline_from_each_command(command, args, seen_limits) -> None:
    result = CliRunner().invoke(command, [*args, "--timeout", "7"], obj=_obj())

    assert result.exit_code == 0, result.output
    assert seen_limits == [7]


@pytest.mark.parametrize(
    ("option", "configured", "expected"),
    [
        ([], None, None),
        ([], 300, 300),
        (["--timeout", "60"], 300, 60),
        (["--timeout", "0"], 300, None),
        ([], 0, None),
    ],
    ids=["unset", "config-only", "command-line-wins", "zero-disables-config", "config-zero"],
)
def test_the_command_line_wins_over_the_config_file_and_zero_disables(
    option, configured, expected, seen_limits
) -> None:
    result = CliRunner().invoke(run, [*RUN_ARGS, *option], obj=_obj(configured))

    assert result.exit_code == 0, result.output
    assert seen_limits == [expected]


@pytest.mark.parametrize("value", ["-1", "1.5", "soon"])
@pytest.mark.parametrize(
    ("command", "args"),
    [(run, RUN_ARGS), (run_official, OFFICIAL_ARGS), (datagen, DATAGEN_ARGS)],
    ids=["run", "run-official", "datagen"],
)
def test_negative_and_fractional_values_are_usage_errors(command, args, value, seen_limits) -> None:
    result = CliRunner().invoke(command, [*args, "--timeout", value], obj=_obj())

    assert result.exit_code == ExitCode.USAGE
    assert seen_limits == []


def test_an_invalid_config_value_is_a_usage_error_naming_the_key(seen_limits) -> None:
    result = CliRunner().invoke(run, RUN_ARGS, obj=_obj(-5))

    assert result.exit_code == ExitCode.USAGE
    assert RUN_TIMEOUT_CONFIG_KEY in result.output
    assert seen_limits == []


def test_a_run_that_stops_at_the_limit_exits_124(monkeypatch) -> None:
    def stops(_s: Any) -> None:
        deadline = current_run_deadline()
        assert deadline is not None
        deadline.enter_phase("load")
        deadline.record_timeout()
        deadline.run_timed_out = True
        sys.exit(1)

    monkeypatch.setattr(_run_module, "dispatch_run", stops)

    result = CliRunner().invoke(run, [*RUN_ARGS, "--timeout", "5"], obj=_obj())

    assert result.exit_code == ExitCode.TIMEOUT == 124
    assert "5-second limit" in result.output
    assert "during load" in result.output


def test_a_run_that_finished_before_the_limit_passed_keeps_exit_zero_and_the_clock_waits_for_the_run(
    monkeypatch,
) -> None:
    remaining: list[float] = []

    def finishes_then_the_limit_passes(_s: Any) -> None:
        deadline = current_run_deadline()
        assert deadline is not None
        remaining.append(deadline.remaining())
        deadline.record_timeout()

    monkeypatch.setattr(_run_module, "dispatch_run", finishes_then_the_limit_passes)

    result = CliRunner().invoke(run, [*RUN_ARGS, "--timeout", "5"], obj=_obj())

    assert result.exit_code == 0, result.output
    assert remaining == [5.0]


def test_a_failure_before_the_limit_keeps_its_own_exit_code(monkeypatch) -> None:
    monkeypatch.setattr(_run_module, "dispatch_run", lambda _s: sys.exit(1))

    result = CliRunner().invoke(run, [*RUN_ARGS, "--timeout", "600"], obj=_obj())

    assert result.exit_code == 1


def test_the_orchestrator_returns_the_timed_out_result_not_a_failed_one(monkeypatch, tmp_path) -> None:
    def stopped(**_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        deadline.enter_phase("load")
        raise deadline.record_timeout()

    monkeypatch.setattr(run_service, "_execute_run", stopped)
    orchestrator = BenchmarkOrchestrator(base_dir=str(tmp_path))
    monkeypatch.setattr(orchestrator, "_get_benchmark_instance", lambda *_args, **_kwargs: object())
    config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01, options={"timeout_seconds": 5})

    result = orchestrator.execute_benchmark(config, None, None, ["load"])

    assert result.validation_status == TIMED_OUT_VALIDATION_STATUS
    assert result.validation_details["timeout"]["phase"] == "load"


def test_a_run_that_ignores_the_stop_request_is_ended_with_a_marked_partial_result(tmp_path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", HARD_STOP_SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )

    assert completed.returncode == 124, completed.stdout + completed.stderr
    bundles = [path for path in tmp_path.rglob("*.json") if "manifest" not in path.name]
    assert len(bundles) == 1, completed.stdout + completed.stderr
    bundle = json.loads(bundles[0].read_text(encoding="utf-8"))
    assert bundle["summary"]["validation"] == "timed_out"
    assert bundle["execution"]["timeout"] == {"timeout_seconds": 1, "phase": "execute"}
    assert len(bundle["queries"]) == 1
    untracked = int((tmp_path / "untracked.pid").read_text(encoding="utf-8"))
    gone = threading.Event()
    for _ in range(100):
        if not psutil.pid_exists(untracked) or psutil.Process(untracked).status() == psutil.STATUS_ZOMBIE:
            gone.set()
            break
        gone.wait(0.1)
    assert gone.is_set(), "the hard stop left a child process running"


def test_no_option_or_config_key_changes_the_grace() -> None:
    for command in (run, run_official, datagen):
        assert not [param.name for param in command.params if "grace" in (param.name or "")]
    assert ConfigManager().get("execution.timeout_grace_seconds") is None
