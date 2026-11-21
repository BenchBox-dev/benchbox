from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from benchbox.cli import run_time_limit
from benchbox.cli.config import ConfigManager
from benchbox.cli.exit_codes import ExitCode
from benchbox.core import run_service
from benchbox.core.platform_manifest import PLATFORM_MANIFEST
from benchbox.core.schemas import BenchmarkConfig
from benchbox.utils.run_deadline import (
    RUN_TIMEOUT_CONFIG_KEY,
    current_run_deadline,
    register_remote_cleanup,
    remote_cleanup_warning,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

HARD_STOP_SCRIPT = """
import functools, subprocess, sys, time
from pathlib import Path
from click.testing import CliRunner
from benchbox.cli import run_time_limit
from benchbox.cli.commands import run as _unused
from benchbox.cli.config import ConfigManager
from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig
from benchbox.utils.run_deadline import arm_hard_stop, current_run_deadline, register_remote_cleanup

run_module = sys.modules["benchbox.cli.commands.run"]
orchestrator = BenchmarkOrchestrator(base_dir=sys.argv[1])


class StartedWork:
    def __init__(self, name, fails):
        self.name = name
        self.fails = fails

    def cancel_remote_work(self):
        Path(sys.argv[1], self.name + ".cancelled").write_text("yes")
        if self.fails:
            raise RuntimeError("the service refused")


def blocks_past_the_limit(*_args, **_kwargs):
    deadline = current_run_deadline()
    for name, fails in (("failing", True), ("working", False)):
        work = StartedWork(name, fails)
        register_remote_cleanup(work, work.cancel_remote_work, name)
    deadline.enter_phase("execute")
    time.sleep(300)


def reaches_the_run(_s):
    run_module._execute_orchestrated_run(
        orchestrator,
        BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01),
        None,
        DatabaseConfig(type="duckdb", name="stub"),
        ["power"],
        quiet=False,
        no_progress=True,
        no_monitoring=True,
    )

orchestrator.execute_benchmark = blocks_past_the_limit
run_time_limit.dispatch_run = reaches_the_run
run_time_limit.arm_hard_stop = functools.partial(arm_hard_stop, grace_seconds=0.3)
run_module.run.main(
    ["--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01", "--non-interactive", "--timeout", "1"],
    obj={"config": ConfigManager()},
)
"""


def _settings(platform: str | None, timeout: int | None = 5, configured: Any = None) -> SimpleNamespace:
    config = ConfigManager()
    if configured is not None:
        config.set(RUN_TIMEOUT_CONFIG_KEY, configured)
    return SimpleNamespace(platform=platform, timeout=timeout, config=config)


@pytest.fixture
def printed(monkeypatch) -> Console:
    console = Console(record=True, width=60, force_terminal=False)
    monkeypatch.setattr(run_time_limit, "console", console)
    monkeypatch.setattr(run_time_limit, "dispatch_run", lambda _s: None)
    return console


def _output(console: Console) -> str:
    return console.export_text()


def test_a_cloud_platform_with_cleanup_gets_the_implemented_warning(printed) -> None:
    run_time_limit.run_within_time_limit(_settings("quanton"))

    assert remote_cleanup_warning("Onehouse Quanton", True) in " ".join(_output(printed).split())


def test_a_cloud_platform_without_cleanup_gets_the_cannot_cancel_warning(printed) -> None:
    run_time_limit.run_within_time_limit(_settings("snowflake"))

    assert remote_cleanup_warning("Snowflake", False) in " ".join(_output(printed).split())


def test_an_alias_of_a_cloud_platform_gets_the_warning(printed) -> None:
    aliases = {alias.name: entry.key for entry in PLATFORM_MANIFEST for alias in entry.aliases}
    alias = next(name for name, key in aliases.items() if key == "bigquery")

    run_time_limit.run_within_time_limit(_settings(alias))

    assert "try to cancel the work it started on Google BigQuery" in " ".join(_output(printed).split())


def test_every_cloud_platform_gets_exactly_one_of_the_two_warnings(printed) -> None:
    cloud = [entry for entry in PLATFORM_MANIFEST if entry.metadata_dict().get("category") == "cloud"]
    assert cloud
    for entry in cloud:
        warning = run_time_limit.cloud_cleanup_warning(entry.key)
        implemented = bool(entry.capabilities.get("run_timeout_cleanup", False))
        assert warning == remote_cleanup_warning(entry.metadata_dict()["display_name"], implemented), entry.key


@pytest.mark.parametrize("platform", ["duckdb", "sqlite", "datafusion", "polars", "clickhouse-local", None])
def test_local_platforms_get_no_warning(printed, platform) -> None:
    run_time_limit.run_within_time_limit(_settings(platform))

    assert "run time limit" not in _output(printed)


@pytest.mark.parametrize("timeout, configured", [(None, None), (0, None), (0, 30)])
def test_no_limit_means_no_warning_on_a_cloud_platform(printed, timeout, configured) -> None:
    run_time_limit.run_within_time_limit(_settings("quanton", timeout, configured))

    assert "run time limit" not in _output(printed)


def test_the_configured_limit_also_prints_the_warning(printed) -> None:
    run_time_limit.run_within_time_limit(_settings("quanton", None, 30))

    assert "A run time limit is set." in _output(printed)


class FailingAdapter:
    def cancel_remote_work(self) -> None:
        raise RuntimeError("the service refused")


def test_a_failing_cleanup_keeps_exit_code_124_and_prints_the_cleanup_error(monkeypatch, printed) -> None:
    def times_out_in_the_run(**_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        adapter = FailingAdapter()
        register_remote_cleanup(adapter, adapter.cancel_remote_work, "Onehouse")
        deadline.enter_phase("execute")
        raise deadline.record_timeout()

    def dispatch(_s: Any) -> None:
        result = run_service.execute_run(
            config=BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01),
            benchmark_instance=None,
            database_config=SimpleNamespace(type="quanton"),
            system_profile=None,
            platform_config=None,
            output_root=None,
            phases_to_run=["power"],
            adapter_factory=None,
            verbosity=None,
        )
        deadline = current_run_deadline()
        assert deadline is not None
        deadline.run_timed_out = result.validation_status == "TIMED_OUT"
        raise SystemExit(1)

    monkeypatch.setattr(run_service, "_execute_run", times_out_in_the_run)
    monkeypatch.setattr(run_time_limit, "dispatch_run", dispatch)

    with pytest.raises(SystemExit) as excinfo:
        run_time_limit.run_within_time_limit(_settings("quanton"))

    assert excinfo.value.code == ExitCode.TIMEOUT == 124
    output = " ".join(_output(printed).split())
    assert "Remote cleanup failed: Onehouse: RuntimeError: the service refused" in output
    assert "5-second limit" in output


def test_the_backstop_cancels_remote_work_keeps_exit_124_and_reports_the_failure(tmp_path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", HARD_STOP_SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 124, output
    assert (tmp_path / "failing.cancelled").exists(), output
    assert (tmp_path / "working.cancelled").exists(), output
    assert "Remote cleanup failed: failing: RuntimeError: the service refused" in " ".join(output.split())
    bundles = [path for path in tmp_path.rglob("*.json") if "manifest" not in path.name]
    assert len(bundles) == 1, output
    assert json.loads(bundles[0].read_text(encoding="utf-8"))["summary"]["validation"] == "timed_out"


def test_the_hard_stop_function_cancels_before_exiting(monkeypatch) -> None:
    from benchbox.utils.run_deadline import RunDeadline

    order: list[str] = []
    deadline = RunDeadline(5)
    adapter = SimpleNamespace(cancel_remote_work=lambda: order.append("cancelled"))
    deadline.register_remote_cleanup(adapter, adapter.cancel_remote_work)
    deadline.write_partial_result = lambda: order.append("partial result")
    monkeypatch.setattr(run_time_limit, "console", Console(record=True))
    monkeypatch.setattr(run_time_limit, "end_descendant_processes", lambda: order.append("ended children"))
    monkeypatch.setattr(run_time_limit.os, "_exit", lambda code: order.append(f"exit {code}"))

    run_time_limit.hard_stop(deadline)

    assert order == ["partial result", "cancelled", "ended children", "exit 124"]
