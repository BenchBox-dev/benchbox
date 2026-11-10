from __future__ import annotations

import json
import logging
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import pytest

import benchbox
from benchbox.core.results.schema import build_result_payload
from benchbox.core.runner.runner import _attach_translation_metadata
from benchbox.core.throughput.executor import DaemonStreamExecutor
from benchbox.core.throughput.result import ThroughputResult, ThroughputStreamResult
from benchbox.core.throughput.runner import StreamRunner
from benchbox.utils.dialect_utils import (
    current_sql_translation_strict_mode,
    record_sql_translation_outcome,
    sql_translation_context,
    translate_sql_query,
)
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@dataclass
class _Config:
    num_streams: int = 3
    max_workers: int | None = 3
    base_seed: int = 7
    stream_timeout: float = 0
    scale_factor: float = 1.0
    verbose: bool = False
    cancel_on_timeout: bool = False


def _empty_result() -> ThroughputResult:
    return ThroughputResult(
        start_time="2026-01-01T00:00:00",
        end_time="",
        total_time=0.0,
        throughput_at_size=0.0,
        streams_executed=0,
        streams_successful=0,
    )


def _stream_result(stream_id: int) -> ThroughputStreamResult:
    return ThroughputStreamResult(
        stream_id=stream_id,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        queries_executed=1,
        queries_successful=1,
        queries_failed=0,
        success=True,
        error=None,
    )


def _vendored_runtime_pin() -> dict:
    return json.loads((Path(benchbox.__file__).parent / "_vendor" / "sqlglot.json").read_text(encoding="utf-8"))


def test_streams_that_translate_inline_record_the_wheel_runtime_in_the_result() -> None:
    strictness: list[bool] = []
    lock = threading.Lock()

    def stream_fn(stream_id: int, seed: int, config: _Config) -> ThroughputStreamResult:
        with lock:
            strictness.append(current_sql_translation_strict_mode())
        translate_sql_query("SELECT a FROM t", "sqlite", "duckdb")
        return _stream_result(stream_id)

    config = _Config(num_streams=3)
    with sql_translation_context(strict=True) as outcomes:
        StreamRunner.execute(stream_fn, config, _empty_result(), logging.getLogger("test-translation-context"))

    assert len(outcomes) == config.num_streams
    assert strictness == [True] * config.num_streams

    pin = _vendored_runtime_pin()
    payload = build_result_payload(
        _attach_translation_metadata(make_benchmark_results(execution_metadata={}), outcomes, strict_mode=True)
    )
    assert payload["execution"]["translation"]["runtime"] == {
        "distribution": pin["distribution"],
        "version": pin["version"],
        "fork_sha": pin["fork_sha"],
        "upstream_sha": pin["upstream_sha"],
        "wheel_sha256": pin["sha256"],
    }


def test_a_reused_worker_takes_the_context_of_each_submission() -> None:
    executor = DaemonStreamExecutor(max_workers=1)
    try:
        with sql_translation_context() as first:
            executor.submit(translate_sql_query, "SELECT a FROM t", "sqlite", "duckdb").result(timeout=30)
        with sql_translation_context() as second:
            executor.submit(translate_sql_query, "SELECT a FROM t", "sqlite", "duckdb").result(timeout=30)
            executor.submit(translate_sql_query, "SELECT b FROM t", "sqlite", "duckdb").result(timeout=30)
    finally:
        executor.shutdown(wait=True)

    assert (len(first), len(second)) == (1, 2)


def test_work_submitted_outside_a_translation_context_records_nothing() -> None:
    executor = DaemonStreamExecutor(max_workers=1)
    try:
        executor.submit(translate_sql_query, "SELECT a FROM t", "sqlite", "duckdb").result(timeout=30)
        with sql_translation_context() as outcomes:
            pass
    finally:
        executor.shutdown(wait=True)

    assert outcomes == []


def test_context_changes_made_by_one_item_do_not_reach_the_next_item_or_the_caller() -> None:
    marker: ContextVar[str] = ContextVar("test_translation_context_marker", default="unset")

    def set_marker() -> None:
        marker.set("leaked")

    executor = DaemonStreamExecutor(max_workers=1)
    try:
        executor.submit(set_marker).result(timeout=30)
        assert executor.submit(marker.get).result(timeout=30) == "unset"
    finally:
        executor.shutdown(wait=True)

    assert marker.get() == "unset"


def test_outcomes_recorded_from_many_workers_are_all_kept() -> None:
    from benchbox.utils.dialect_utils import SqlTranslationOutcome

    def record() -> None:
        record_sql_translation_outcome(
            SqlTranslationOutcome(
                source_dialect="duckdb",
                target_dialect="sqlite",
                normalized_source_dialect="duckdb",
                normalized_target_dialect="sqlite",
                translator="sqlglot",
                status="success",
                strict_mode=False,
            )
        )

    executor = DaemonStreamExecutor(max_workers=8)
    try:
        with sql_translation_context() as outcomes:
            futures = [executor.submit(record) for _ in range(200)]
            for future in futures:
                future.result(timeout=30)
    finally:
        executor.shutdown(wait=True)

    assert len(outcomes) == 200
