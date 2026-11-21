from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from _project.scripts.explorer_pipeline.models import ranking_exclusion_reason
from benchbox.base import BaseBenchmark
from benchbox.mcp.jobs import derive_job_outcome
from benchbox.mcp.tools import benchmark as mcp_benchmark
from benchbox.platforms.base import execution
from benchbox.tpch import TPCH
from benchbox.utils import run_deadline
from benchbox.utils.run_deadline import RunDeadline, RunTimeoutError, active_run_deadline, current_run_deadline
from tests.unit.platforms.test_base_adapter import MockPlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
QUERIES = {"1": "SELECT 1", "2": "SELECT 2", "3": "SELECT 3"}


class TinyBenchmark(BaseBenchmark):
    def __init__(self, generate: Any) -> None:
        self._generate = generate

    def generate_data(self, tables: list[str] | None = None) -> Any:
        return self._generate(tables)

    def get_queries(self) -> dict[str, str]:
        return {}

    def get_query(self, query_id: Any, *, params: Any = None) -> str:
        return ""


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(run_deadline, "mono_time", lambda: fake.now)
    monkeypatch.setattr(run_deadline, "elapsed_seconds", lambda started: fake.now - started)
    return fake


def _benchmark() -> Mock:
    benchmark = Mock()
    benchmark.get_queries.return_value = dict(QUERIES)
    benchmark.get_platform_skip_queries.return_value = []
    return benchmark


def _adapter_recording(events: list[str], clock: FakeClock | None = None) -> MockPlatformAdapter:
    adapter = MockPlatformAdapter()

    def timed_query(_benchmark: Any, _connection: Any, query_id: Any, _sql: str, **_kwargs: Any) -> dict[str, Any]:
        events.append(f"start:{query_id}")
        if clock is not None:
            clock.now += 6
        events.append(f"end:{query_id}")
        return {"query_id": str(query_id), "status": "SUCCESS", "execution_time_seconds": 6.0, "rows_returned": 1}

    adapter._execute_single_query = timed_query
    return adapter


def test_the_query_loop_checks_the_deadline_between_queries_never_inside_one(monkeypatch) -> None:
    events: list[str] = []
    real_check = execution.stop_requested_before_query

    def recording_check(query_id: Any) -> bool:
        events.append(f"check:{query_id}")
        return real_check(query_id)

    monkeypatch.setattr(execution, "stop_requested_before_query", recording_check)

    with active_run_deadline(RunDeadline(600)):
        results = _adapter_recording(events)._execute_all_queries(_benchmark(), Mock(), {"benchmark_name": "tpch"})

    assert len(results) == 3
    assert events == [f"{step}:{query}" for query in QUERIES for step in ("check", "start", "end")]


def test_the_query_loop_stops_before_the_next_query_and_keeps_the_finished_ones(clock) -> None:
    events: list[str] = []
    deadline = RunDeadline(10)

    with active_run_deadline(deadline):
        deadline.enter_phase("execute")
        results = _adapter_recording(events, clock)._execute_all_queries(
            _benchmark(), Mock(), {"benchmark_name": "tpch"}
        )

    assert [row["query_id"] for row in results] == ["1", "2"]
    assert events == ["start:1", "end:1", "start:2", "end:2"]
    assert deadline.timeout is not None
    assert (deadline.timeout.phase, deadline.timeout.query_id) == ("execute", "3")
    assert deadline.partial_query_results is results


def test_the_query_loop_is_unchanged_without_a_deadline() -> None:
    events: list[str] = []
    results = _adapter_recording(events)._execute_all_queries(_benchmark(), Mock(), {"benchmark_name": "tpch"})
    assert len(results) == 3


def test_run_benchmark_passes_its_limit_to_the_deadline_and_not_to_the_run(monkeypatch) -> None:
    adapter = MockPlatformAdapter()
    seen: list[Any] = []

    def finishes(_benchmark: Any, **run_config: Any) -> str:
        deadline = current_run_deadline()
        seen.append((None if deadline is None else deadline.timeout_seconds, dict(run_config)))
        return "done"

    monkeypatch.setattr(adapter, "run_enhanced_benchmark", finishes)

    assert adapter.run_benchmark(Mock(), timeout_seconds=90, test_execution_type="power") == "done"
    assert adapter.run_benchmark(Mock(), test_execution_type="power") == "done"
    assert adapter.run_benchmark(Mock(), timeout_seconds=0) == "done"
    assert seen == [(90, {"test_execution_type": "power"}), (None, {"test_execution_type": "power"}), (None, {})]
    assert current_run_deadline() is None


def test_run_benchmark_raises_the_timeout_with_the_partial_result_and_does_not_exit(monkeypatch) -> None:
    adapter = MockPlatformAdapter()
    partial = SimpleNamespace(
        query_results=[{"query_id": "1"}],
        validation_status="PASSED",
        validation_details=None,
        compliance_class="official",
    )

    def stopped_between_queries(_benchmark: Any, **_run_config: Any) -> Any:
        deadline = current_run_deadline()
        assert deadline is not None
        deadline.enter_phase("execute")
        deadline.enter_query("2")
        deadline.record_timeout()
        return partial

    monkeypatch.setattr(adapter, "run_enhanced_benchmark", stopped_between_queries)

    with pytest.raises(RunTimeoutError) as excinfo:
        adapter.run_benchmark(Mock(), timeout_seconds=1)

    assert excinfo.value.partial_result is partial
    assert (partial.validation_status, partial.compliance_class) == ("TIMED_OUT", None)
    assert partial.validation_details["timeout"] == {"timeout_seconds": 1, "phase": "execute", "query_id": "2"}
    assert (excinfo.value.timeout_seconds, excinfo.value.phase, excinfo.value.query_id) == (1, "execute", "2")


@pytest.mark.parametrize("value", [-1, 1.5])
def test_the_python_calls_refuse_negative_and_fractional_limits(value) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        MockPlatformAdapter().run_benchmark(Mock(), timeout_seconds=value)
    with pytest.raises(ValueError, match="timeout_seconds"):
        TinyBenchmark(lambda _tables: []).generate_data(timeout_seconds=value)


def test_generate_data_accepts_the_limit_on_every_benchmark_class() -> None:
    seen: list[Any] = []

    def generate(tables: Any) -> list[str]:
        deadline = current_run_deadline()
        seen.append((None if deadline is None else (deadline.timeout_seconds, deadline.phase), tables))
        return ["lineitem.tbl"]

    benchmark = TinyBenchmark(generate)

    assert benchmark.generate_data(["lineitem"], timeout_seconds=45) == ["lineitem.tbl"]
    assert benchmark.generate_data() == ["lineitem.tbl"]
    assert seen == [((45, "generate"), ["lineitem"]), (None, None)]

    wrapper = TPCH.__new__(TPCH)
    wrapper._impl = SimpleNamespace(generate_data=lambda: seen.append(current_run_deadline().timeout_seconds))
    wrapper.generate_data(timeout_seconds=30)
    assert seen[-1] == 30


def test_generate_data_keeps_its_own_error_after_the_limit(clock) -> None:
    def fails_late(_tables: Any) -> Any:
        clock.now += 5
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        TinyBenchmark(fails_late).generate_data(timeout_seconds=2)


def test_generate_data_keeps_its_own_error_before_the_limit() -> None:
    def fails_early(_tables: Any) -> Any:
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        TinyBenchmark(fails_early).generate_data(timeout_seconds=600)


def test_generate_data_that_finishes_after_the_limit_raises_the_timeout_with_its_output(clock) -> None:
    def finishes_late(_tables: Any) -> list[str]:
        clock.now += 5
        return ["lineitem.tbl"]

    with pytest.raises(RunTimeoutError) as excinfo:
        TinyBenchmark(finishes_late).generate_data(timeout_seconds=2)

    assert excinfo.value.phase == "generate"
    assert excinfo.value.partial_result == ["lineitem.tbl"]


def test_a_timed_out_mcp_run_is_an_error_result_and_an_incomplete_job_that_is_not_retried() -> None:
    response = {"mcp_metadata": {"status": "timed_out", "timeout": {"timeout_seconds": 5, "phase": "load"}}}

    assert derive_job_outcome(response) == "incomplete"
    assert response.get("status") != "failed"
    assert mcp_benchmark._as_tool_result(dict(response)).is_error is True


def test_a_timed_out_bundle_is_excluded_from_ranking_and_fails_corpus_validation() -> None:
    entry = SimpleNamespace(
        visibility="public-curated",
        trust_label="maintainer-run",
        compliance_class=None,
        failed_query_count=0,
        validation_status="timed_out",
    )
    spec = importlib.util.spec_from_file_location(
        "validate_corpus_timeout", REPO_ROOT / "results-data/validate_corpus.py"
    )
    assert spec is not None and spec.loader is not None
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    payload = {"summary": {"validation": "timed_out"}, "benchmark": {"id": "tpch", "scale_factor": 1}}

    assert ranking_exclusion_reason(entry) == "validation_not_clean"
    assert validator.exclusion_reason(payload) == "validation_not_clean"
