from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core import run_service
from benchbox.core.schemas import BenchmarkConfig
from benchbox.platforms.base.adapter import PlatformAdapter
from benchbox.utils.run_deadline import (
    RUN_TIMEOUT_OPTION_KEY,
    TIMED_OUT_VALIDATION_STATUS,
    RunDeadline,
    RunTimeoutError,
    active_run_deadline,
    current_run_deadline,
    register_remote_cleanup,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

DATABASE = SimpleNamespace(type="duckdb")


class StubAdapter:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    def cancel_remote_work(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


def _config(**options: Any) -> BenchmarkConfig:
    return BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01, options=options)


def _execute(config: BenchmarkConfig, adapter_factory: Any = None, output_root: Any = None) -> Any:
    return run_service.execute_run(
        config=config,
        benchmark_instance=None,
        database_config=DATABASE,
        system_profile=None,
        platform_config=None,
        output_root=output_root,
        phases_to_run=["load", "power"],
        adapter_factory=adapter_factory,
        verbosity=None,
    )


def _stops_at_the_limit(adapter: StubAdapter):
    def stopped(**_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        register_remote_cleanup(adapter, adapter.cancel_remote_work)
        deadline.enter_phase("execute")
        raise deadline.record_timeout()

    return stopped


def test_the_timeout_path_cancels_remote_work_and_returns_the_marked_result(monkeypatch) -> None:
    adapter = StubAdapter()
    monkeypatch.setattr(run_service, "_execute_run", _stops_at_the_limit(adapter))

    result = _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 5}))

    assert adapter.calls == 1
    assert result.validation_status == TIMED_OUT_VALIDATION_STATUS


def test_a_failing_cleanup_does_not_hide_the_timeout_and_its_error_is_kept(monkeypatch) -> None:
    adapter = StubAdapter(RuntimeError("the service refused"))
    monkeypatch.setattr(run_service, "_execute_run", _stops_at_the_limit(adapter))
    deadline = RunDeadline(5)

    with active_run_deadline(deadline):
        result = _execute(_config())

    assert result.validation_status == TIMED_OUT_VALIDATION_STATUS
    assert result.validation_details["timeout"]["phase"] == "execute"
    assert deadline.cleanup_errors == ["StubAdapter: RuntimeError: the service refused"]


def test_a_run_that_finishes_inside_the_limit_cancels_nothing(monkeypatch) -> None:
    adapter = StubAdapter()

    def finishes(**_kwargs: Any) -> str:
        register_remote_cleanup(adapter, adapter.cancel_remote_work)
        return "done"

    monkeypatch.setattr(run_service, "_execute_run", finishes)

    assert _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 600})) == "done"
    assert adapter.calls == 0


def test_an_error_raised_after_the_limit_still_cancels_remote_work_and_stays_an_error(monkeypatch) -> None:
    adapter = StubAdapter()

    def fails_late(**_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        register_remote_cleanup(adapter, adapter.cancel_remote_work)
        deadline.record_timeout()
        raise ValueError("late failure")

    monkeypatch.setattr(run_service, "_execute_run", fails_late)

    with pytest.raises(ValueError, match="late failure"):
        _execute(_config(**{RUN_TIMEOUT_OPTION_KEY: 5}))

    assert adapter.calls == 1


def test_the_adapter_the_run_creates_is_registered_for_cleanup(monkeypatch, tmp_path) -> None:
    adapter = StubAdapter()

    def lifecycle(**_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        raise deadline.record_timeout()

    monkeypatch.setattr(run_service, "run_benchmark_lifecycle", lifecycle)

    result = _execute(
        _config(**{RUN_TIMEOUT_OPTION_KEY: 5}),
        adapter_factory=lambda **_kwargs: adapter,
        output_root=tmp_path,
    )

    assert result.validation_status == TIMED_OUT_VALIDATION_STATUS
    assert adapter.calls == 1


def test_an_adapter_without_the_hook_is_not_registered(monkeypatch, tmp_path) -> None:
    def lifecycle(**_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        raise deadline.record_timeout()

    monkeypatch.setattr(run_service, "run_benchmark_lifecycle", lifecycle)

    result = _execute(
        _config(**{RUN_TIMEOUT_OPTION_KEY: 5}),
        adapter_factory=lambda **_kwargs: SimpleNamespace(),
        output_root=tmp_path,
    )

    assert result.validation_status == TIMED_OUT_VALIDATION_STATUS


class PythonRunAdapter:
    cancel_remote_work = StubAdapter.cancel_remote_work

    def __init__(self, run: Any, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error
        self.run_enhanced_benchmark = run


def _python_run(adapter: PythonRunAdapter) -> Any:
    return PlatformAdapter.run_benchmark(adapter, object(), **{RUN_TIMEOUT_OPTION_KEY: 5})


def test_a_python_run_that_times_out_cancels_remote_work_and_raises_the_timeout() -> None:
    def runs(_benchmark: Any, **_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        deadline.record_timeout()
        return SimpleNamespace(validation_status="PASSED", validation_details={}, compliance_class=None)

    adapter = PythonRunAdapter(runs)

    with pytest.raises(RunTimeoutError) as excinfo:
        _python_run(adapter)

    assert adapter.calls == 1
    assert excinfo.value.partial_result.validation_status == TIMED_OUT_VALIDATION_STATUS


def test_a_python_run_still_raises_the_timeout_when_the_cleanup_fails() -> None:
    def runs(_benchmark: Any, **_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        deadline.record_timeout()
        return SimpleNamespace(validation_status="PASSED", validation_details={}, compliance_class=None)

    adapter = PythonRunAdapter(runs, RuntimeError("the service refused"))

    with pytest.raises(RunTimeoutError):
        _python_run(adapter)

    assert adapter.calls == 1


def test_a_python_run_that_stops_with_the_timeout_error_cancels_remote_work() -> None:
    def stops(_benchmark: Any, **_kwargs: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        raise deadline.record_timeout()

    adapter = PythonRunAdapter(stops)

    with pytest.raises(RunTimeoutError):
        _python_run(adapter)

    assert adapter.calls == 1


def test_a_python_run_without_a_timeout_cancels_nothing() -> None:
    result = SimpleNamespace(validation_status="PASSED", validation_details={}, compliance_class=None)
    adapter = PythonRunAdapter(lambda _benchmark, **_kwargs: result)

    assert _python_run(adapter) is result
    assert adapter.calls == 0
