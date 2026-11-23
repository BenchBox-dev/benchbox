from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from benchbox.cli import run_time_limit
from benchbox.cli.config import ConfigManager
from benchbox.cli.exit_codes import ExitCode
from benchbox.core import run_service
from benchbox.core.platform_manifest import PLATFORM_MANIFEST
from benchbox.core.run_cleanup_warning import cloud_cleanup_warning
from benchbox.core.schemas import BenchmarkConfig
from benchbox.utils.run_deadline import (
    HARD_STOP_GRACE_SECONDS,
    RUN_TIMEOUT_CONFIG_KEY,
    RunDeadline,
    active_run_deadline,
    current_run_deadline,
    register_remote_cleanup,
    remote_cleanup_warning,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

HARD_STOP_EXIT_SECONDS = 5.0

HARD_STOP_SCRIPT = """
import functools, subprocess, sys, time
from pathlib import Path
from click.testing import CliRunner
import benchbox.utils.run_deadline as run_deadline
from benchbox.cli import run_time_limit
from benchbox.cli.commands import run as _unused
from benchbox.cli.config import ConfigManager
from benchbox.cli.orchestrator import BenchmarkOrchestrator
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig
from benchbox.utils.run_deadline import arm_hard_stop, current_run_deadline, register_remote_cleanup

REAL_GRACE = len(sys.argv) > 3
if not REAL_GRACE:
    run_deadline.HARD_STOP_GRACE_SECONDS = 0.3
    run_deadline.HARD_STOP_EXPORT_MARGIN_SECONDS = 0.1
run_module = sys.modules["benchbox.cli.commands.run"]
orchestrator = BenchmarkOrchestrator(base_dir=sys.argv[1])
BLOCKS = sys.argv[2] == "blocks"


class StartedWork:
    def __init__(self, name, mode):
        self.name = name
        self.mode = mode

    def cancel_remote_work(self):
        Path(sys.argv[1], self.name + ".cancelled").write_text("yes")
        if self.mode == "fails":
            raise RuntimeError("the service refused")
        if self.mode == "blocks":
            time.sleep(300)


def blocks_past_the_limit(*_args, **_kwargs):
    deadline = current_run_deadline()
    Path(sys.argv[1], "started.at").write_text(repr(time.time()))
    modes = (("failing", "fails"), ("working", "ok")) + ((("stuck", "blocks"),) if BLOCKS else ())
    for name, mode in modes:
        work = StartedWork(name, mode)
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
        quiet=True,
        no_progress=True,
        no_monitoring=True,
    )

orchestrator.execute_benchmark = blocks_past_the_limit
run_time_limit.dispatch_run = reaches_the_run
if REAL_GRACE:
    real_hard_stop = run_time_limit.hard_stop

    def recording_hard_stop(deadline):
        Path(sys.argv[1], "hard-stop.at").write_text(repr(time.time()))
        real_hard_stop(deadline)

    run_time_limit.hard_stop = recording_hard_stop
else:
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
    return console


def _output(console: Console) -> str:
    return " ".join(console.export_text().split())


def _run_through_the_orchestrated_wrapper(platform: str, timeout: int | None, configured: Any = None) -> None:
    ran: list[bool] = []

    def execute(*_args: Any, **_kwargs: Any) -> Any:
        ran.append(True)
        return SimpleNamespace(validation_status="PASSED")

    wrapper = run_time_limit.bounded_orchestrated_run(execute)

    def dispatch(_s: Any) -> None:
        wrapper(None, None, None, SimpleNamespace(type=platform), ["power"], quiet=True)

    original = run_time_limit.dispatch_run
    run_time_limit.dispatch_run = dispatch
    try:
        run_time_limit.run_within_time_limit(_settings(platform, timeout, configured))
    finally:
        run_time_limit.dispatch_run = original
    assert ran == [True]


def test_a_cloud_platform_with_cleanup_gets_the_implemented_warning(printed) -> None:
    _run_through_the_orchestrated_wrapper("quanton", 5)

    assert remote_cleanup_warning("Onehouse Quanton", True) in _output(printed)


def test_a_cloud_platform_without_cleanup_gets_the_cannot_cancel_warning(printed) -> None:
    _run_through_the_orchestrated_wrapper("snowflake", 5)

    assert remote_cleanup_warning("Snowflake", False) in _output(printed)


def test_the_warning_is_printed_once_for_a_run(printed) -> None:
    _run_through_the_orchestrated_wrapper("quanton", 5)

    assert _output(printed).count("A run time limit is set.") == 1


def test_an_alias_of_a_cloud_platform_gets_the_warning() -> None:
    aliases = {alias.name: entry.key for entry in PLATFORM_MANIFEST for alias in entry.aliases}
    alias = next(name for name, key in aliases.items() if key == "bigquery")

    assert "try to cancel the work it started on Google BigQuery" in (cloud_cleanup_warning(alias) or "")


def test_every_cloud_platform_gets_exactly_one_of_the_two_warnings() -> None:
    cloud = [entry for entry in PLATFORM_MANIFEST if entry.metadata_dict().get("category") == "cloud"]
    assert cloud
    for entry in cloud:
        implemented = bool(entry.capabilities.get("run_timeout_cleanup", False))
        expected = remote_cleanup_warning(entry.metadata_dict()["display_name"], implemented)
        assert cloud_cleanup_warning(entry.key) == expected, entry.key


@pytest.mark.parametrize("platform", ["duckdb", "sqlite", "datafusion", "polars", "clickhouse-local", "unknown", None])
def test_local_platforms_get_no_warning(printed, platform) -> None:
    assert cloud_cleanup_warning(platform) is None
    if platform:
        _run_through_the_orchestrated_wrapper(platform, 5)
        assert "run time limit" not in _output(printed)


@pytest.mark.parametrize("timeout, configured", [(None, None), (0, None), (0, 30)])
def test_no_limit_means_no_warning_on_a_cloud_platform(printed, timeout, configured) -> None:
    _run_through_the_orchestrated_wrapper("quanton", timeout, configured)

    assert "run time limit" not in _output(printed)


def test_the_configured_limit_also_prints_the_warning(printed) -> None:
    _run_through_the_orchestrated_wrapper("quanton", None, 30)

    assert "A run time limit is set." in _output(printed)


def test_a_run_whose_platform_is_chosen_interactively_gets_the_warning_and_the_limit(monkeypatch, printed) -> None:
    from benchbox.cli import run_interactive

    executed: list[tuple[Any, ...]] = []

    class StubOrchestrator:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def set_verbosity(self, _settings: Any) -> None:
            pass

        def execute_benchmark(self, *args: Any, **_kwargs: Any) -> Any:
            executed.append(args)
            return SimpleNamespace(validation_status="PASSED")

    handled: list[Any] = []
    monkeypatch.setattr(run_interactive._run, "BenchmarkOrchestrator", StubOrchestrator)
    monkeypatch.setattr(run_interactive, "_run_stages_through_cloud_storage", lambda _s: False)
    monkeypatch.setattr(run_interactive, "_build_execution_context_from_plan", lambda _s: None)
    monkeypatch.setattr(run_interactive, "_interactive_handle_result", lambda _s, result, _o: handled.append(result))
    state = SimpleNamespace(
        ctx=None,
        database_config=SimpleNamespace(type="quanton"),
        benchmark_config=SimpleNamespace(name="tpch", scale_factor=0.01),
        quick_restart_used=True,
        output=None,
        non_interactive=True,
        verbosity_settings=None,
        resolved_run_plan=SimpleNamespace(phases=["power"]),
        quiet=True,
        no_progress=True,
        no_monitoring=True,
        logger=None,
    )

    monkeypatch.setattr(
        run_time_limit, "dispatch_run", lambda s: run_interactive._interactive_preflight_and_execute(s, None)
    )
    run_time_limit.run_within_time_limit(SimpleNamespace(**{**vars(state), **vars(_settings("quanton"))}))

    assert len(executed) == 1
    assert len(handled) == 1
    assert remote_cleanup_warning("Onehouse Quanton", True) in _output(printed)


class FailingAdapter:
    def cancel_remote_work(self) -> None:
        raise RuntimeError("the service refused")


def test_a_failing_cleanup_keeps_exit_code_124_and_prints_the_cleanup_error(monkeypatch, printed, capfd) -> None:
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
    assert "Remote cleanup failed: Onehouse: RuntimeError: the service refused" in capfd.readouterr().err
    assert "5-second limit" in _output(printed)


def test_a_cleanup_error_is_printed_even_when_the_console_is_quiet(monkeypatch, capfd) -> None:
    monkeypatch.setattr(run_time_limit, "console", Console(quiet=True))
    deadline = RunDeadline(5)
    deadline.register_remote_cleanup(FailingAdapter(), FailingAdapter().cancel_remote_work, "Onehouse")
    deadline.cancel_remote_work()

    run_time_limit.report_cleanup_errors(deadline)

    assert "Remote cleanup failed: Onehouse: RuntimeError: the service refused" in capfd.readouterr().err


def _run_hard_stop_script(
    tmp_path: Path, mode: str, real_grace: bool = False
) -> tuple[subprocess.CompletedProcess[str], float]:
    completed = subprocess.run(
        [sys.executable, "-c", HARD_STOP_SCRIPT, str(tmp_path), mode, *(["real"] if real_grace else [])],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    finished = time.time()
    started = float((tmp_path / "started.at").read_text(encoding="utf-8"))
    return completed, finished - started


def test_the_backstop_cancels_remote_work_keeps_exit_124_and_reports_the_failure_in_a_quiet_run(tmp_path) -> None:
    completed, _ = _run_hard_stop_script(tmp_path, "ends")

    output = completed.stdout + completed.stderr
    assert completed.returncode == 124, output
    assert (tmp_path / "failing.cancelled").exists(), output
    assert (tmp_path / "working.cancelled").exists(), output
    assert "Remote cleanup failed: failing: RuntimeError: the service refused" in completed.stderr
    bundles = [path for path in tmp_path.rglob("*.json") if "manifest" not in path.name]
    assert len(bundles) == 1, output
    assert json.loads(bundles[0].read_text(encoding="utf-8"))["summary"]["validation"] == "timed_out"


def test_a_blocked_cleanup_does_not_move_the_hard_bound_and_is_reported_unfinished(tmp_path) -> None:
    completed, elapsed = _run_hard_stop_script(tmp_path, "blocks")

    output = completed.stdout + completed.stderr
    assert completed.returncode == 124, output
    assert (tmp_path / "stuck.cancelled").exists(), output
    assert "Remote cleanup did not finish within 0.2 seconds" in completed.stderr
    assert elapsed < 1 + 0.3 + 6, f"the hard stop took {elapsed:.1f}s with a blocked cleanup"
    bundles = [path for path in tmp_path.rglob("*.json") if "manifest" not in path.name]
    assert len(bundles) == 1, output


@pytest.mark.slow
def test_a_cleanup_that_never_returns_starts_the_hard_stop_at_limit_plus_30_and_exits_within_the_stated_bound(
    tmp_path,
) -> None:
    completed, elapsed = _run_hard_stop_script(tmp_path, "blocks", real_grace=True)

    output = completed.stdout + completed.stderr
    assert completed.returncode == 124, output
    assert (tmp_path / "stuck.cancelled").exists(), output
    assert "Remote cleanup did not finish within 25 seconds" in completed.stderr
    started = float((tmp_path / "started.at").read_text(encoding="utf-8"))
    hard_stop_began = float((tmp_path / "hard-stop.at").read_text(encoding="utf-8")) - started
    assert abs(hard_stop_began - (1 + HARD_STOP_GRACE_SECONDS)) <= 0.5, hard_stop_began
    assert elapsed - hard_stop_began < HARD_STOP_EXIT_SECONDS, elapsed
    bundles = [path for path in tmp_path.rglob("*.json") if "manifest" not in path.name]
    assert len(bundles) == 1, output


def test_the_hard_stop_function_waits_only_for_what_is_left_of_the_grace(monkeypatch) -> None:
    order: list[str] = []
    deadline = RunDeadline(5)
    deadline.register_remote_cleanup(FailingAdapter(), lambda: order.append("cancelled"), "Stub")
    deadline.start_remote_cleanup()
    deadline.write_partial_result = lambda: order.append("partial result")
    monkeypatch.setattr(run_time_limit, "console", Console(record=True))
    monkeypatch.setattr(run_time_limit, "end_descendant_processes", lambda: order.append("ended children"))
    monkeypatch.setattr(run_time_limit.os, "_exit", lambda code: order.append(f"exit {code}"))

    with active_run_deadline(deadline):
        run_time_limit.hard_stop(deadline)

    assert order == ["cancelled", "partial result", "ended children", "exit 124"]
