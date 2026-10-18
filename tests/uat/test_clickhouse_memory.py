from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from tests.uat import clickhouse_memory

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _trace(
    *,
    outcome: str = "passed",
    application_batch_rows: int | None = None,
    rung: clickhouse_memory.MemoryRung | None = None,
) -> clickhouse_memory.ClickHouseMemoryTrace:
    selected_rung = rung or clickhouse_memory.DEFAULT_MEMORY_RUNGS[0]
    trace = clickhouse_memory.ClickHouseMemoryTrace(
        platform="clickhouse-server",
        rung=selected_rung,
        started_at_utc=clickhouse_memory.utc_now(),
        driver_timeout_s=300,
        driver_timeout_source="test fixture",
        application_batch_rows=application_batch_rows,
    )
    trace.samples.append(
        clickhouse_memory.TraceSample(
            observed_at_utc=clickhouse_memory.utc_now(),
            elapsed_s=0.1,
            host=clickhouse_memory.HostMemorySample(available_gib=4.0, free_gib=1.0, swap_used_percent=0.0),
            engine=clickhouse_memory.EngineMemorySample(
                "mocker",
                "clickhouse",
                512 * 1024**2,
                int(selected_rung.requested_memory_gib * clickhouse_memory.GIB),
                oom_killed=False,
                running=True,
            ),
            clickhouse_metrics={
                "metric.MemoryTracking": 1.0,
                "async.MemoryResident": 1.0,
                "event.InsertedRows": 1.0,
                "event.InsertedBytes": 1.0,
            },
            responsiveness_ms=2.0,
            server_reachable=True,
        )
    )
    trace.finish(outcome=outcome)
    return trace


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1GiB", 1024**3), ("1.5GiB", int(1.5 * 1024**3)), ("512MiB / 1GiB", 512 * 1024**2), ("bad", None)],
)
def test_parse_memory_bytes_preserves_engine_units(value, expected):
    assert clickhouse_memory.parse_memory_bytes(value) == expected


def test_parse_engine_stats_handles_json_and_mem_usage():
    sample = clickhouse_memory.parse_engine_stats(
        '{"Name":"benchbox-clickhouse","MemUsage":"512MiB / 1GiB"}\n',
        engine="mocker",
        service="clickhouse",
    )
    assert sample.usage_bytes == 512 * 1024**2
    assert sample.limit_bytes == 1024**3
    assert sample.raw_status == "benchbox-clickhouse"


def test_parse_engine_stats_handles_mocker_table_output():
    sample = clickhouse_memory.parse_engine_stats(
        "CONTAINER ID   NAME                                      CPU %    MEM USAGE / LIMIT   MEM %\n"
        "benchbox-cal   benchbox-clickhouse-1                     8.40%   70.3MB / 1.00GB     6.87%\n",
        engine="mocker",
        service="clickhouse",
    )
    assert sample.usage_bytes == int(70.3 * 1000**2)
    assert sample.limit_bytes == 1000**3
    assert sample.raw_status == "benchbox-clickhouse-1"


def test_parse_engine_inspect_captures_oom_and_running_state():
    sample = clickhouse_memory.parse_engine_inspect(
        '[{"State":{"OOMKilled":false,"Running":true,"Status":"running"}}]',
        engine="mocker",
        service="clickhouse",
    )
    assert sample.oom_killed is False
    assert sample.running is True
    assert sample.raw_status == "running"


def test_native_loader_contract_rejects_legacy_application_batching():
    ok, reason = clickhouse_memory.verify_native_streaming_loader()
    assert ok, reason


def test_trace_rejects_application_batch_rows_even_when_run_passes():
    trace = _trace(application_batch_rows=1000)
    assert trace.valid_for_calibration is False
    payload = trace.to_dict()
    assert payload["loader_contract"]["legacy_1000_row_fallback"] is True


def test_trace_ignores_startup_samples_before_insert_counters():
    trace = _trace()
    startup_sample = replace(trace.samples[0], clickhouse_metrics={})
    trace.samples.insert(0, startup_sample)
    assert trace.valid_for_calibration is True
    assert trace.to_dict()["summary"]["calibration_sample_count"] == 1


def test_trace_rejects_later_telemetry_gap_after_warmup():
    trace = _trace()
    trace.samples.append(replace(trace.samples[0], clickhouse_metrics={}))
    assert trace.valid_for_calibration is False


def test_trace_requires_native_streaming_and_responsive_sample():
    trace = _trace()
    trace.native_streaming = False
    assert trace.valid_for_calibration is False

    trace = _trace()
    trace.samples[0] = clickhouse_memory.TraceSample(
        observed_at_utc=trace.samples[0].observed_at_utc,
        elapsed_s=trace.samples[0].elapsed_s,
        host=trace.samples[0].host,
        engine=trace.samples[0].engine,
        clickhouse_metrics=trace.samples[0].clickhouse_metrics,
        responsiveness_ms=None,
        server_reachable=False,
    )
    assert trace.valid_for_calibration is False


def test_trace_rejects_runtime_usage_above_declared_limit():
    trace = _trace()
    trace.samples[0] = clickhouse_memory.TraceSample(
        observed_at_utc=trace.samples[0].observed_at_utc,
        elapsed_s=trace.samples[0].elapsed_s,
        host=trace.samples[0].host,
        engine=clickhouse_memory.EngineMemorySample(
            "mocker",
            "clickhouse",
            2 * clickhouse_memory.GIB,
            1 * clickhouse_memory.GIB,
            oom_killed=False,
            running=True,
        ),
        clickhouse_metrics=trace.samples[0].clickhouse_metrics,
        responsiveness_ms=trace.samples[0].responsiveness_ms,
        server_reachable=True,
    )
    assert trace.valid_for_calibration is False
    assert trace.to_dict()["summary"]["memory_limit_exceeded"] is True


def test_trace_summary_marks_command_reported_memory_failure():
    trace = _trace(outcome="failed")
    trace.finish(outcome="failed", failure_reason="DB::Exception: memory limit exceeded while joining")
    assert trace.valid_for_calibration is False
    assert trace.to_dict()["summary"]["memory_limit_exceeded"] is True


def test_trace_rejects_runtime_limit_outside_named_rung_unit_window():
    trace = _trace()
    sample = trace.samples[0]
    trace.samples[0] = replace(
        sample,
        engine=replace(sample.engine, limit_bytes=2 * clickhouse_memory.GIB),
    )
    assert trace.valid_for_calibration is False


def test_trace_accepts_decimal_runtime_spelling_for_named_gib_rung():
    trace = _trace()
    sample = trace.samples[0]
    trace.samples[0] = replace(
        sample,
        engine=replace(sample.engine, limit_bytes=1_000_000_000),
    )
    assert trace.valid_for_calibration is True


@pytest.mark.parametrize(
    ("runtime_limit", "requested_bytes", "expected"),
    [
        (8_000_000_000, 8_000_000_000, True),
        (8 * clickhouse_memory.GIB, 8_000_000_000, False),
        (7_500_000_000, 8_000_000_000, False),
    ],
)
def test_runtime_admission_compares_the_resolved_byte_count(runtime_limit, requested_bytes, expected):
    assert (
        clickhouse_memory.runtime_limit_matches_rung(
            runtime_limit,
            requested_bytes / clickhouse_memory.GIB,
            requested_bytes=requested_bytes,
        )
        is expected
    )


@pytest.mark.parametrize("missing", ["usage", "oom", "running", "host", "metrics"])
def test_trace_fails_closed_on_required_telemetry_gaps(missing):
    trace = _trace()
    sample = trace.samples[0]
    engine = sample.engine
    host = sample.host
    metrics = sample.clickhouse_metrics
    if missing == "usage":
        engine = replace(engine, usage_bytes=None)
    elif missing == "oom":
        engine = replace(engine, oom_killed=None)
    elif missing == "running":
        engine = replace(engine, running=False)
    elif missing == "host":
        host = replace(host, available_gib=None)
    else:
        metrics = {}
    trace.samples[0] = replace(sample, engine=engine, host=host, clickhouse_metrics=metrics)
    assert trace.valid_for_calibration is False


@pytest.mark.parametrize(
    ("metric_name", "replacement"),
    [
        ("metric.MemoryTracking", "metric.NotMemoryTracking"),
        ("async.MemoryResident", "async.NotMemoryResident"),
        ("event.InsertedRows", "event.NotInsertedRows"),
        ("event.InsertedBytes", "event.NotInsertedBytes"),
    ],
)
def test_trace_requires_each_named_clickhouse_metric(metric_name, replacement):
    trace = _trace()
    sample = trace.samples[0]
    metrics = dict(sample.clickhouse_metrics)
    metrics[replacement] = metrics.pop(metric_name)
    trace.samples[0] = replace(sample, clickhouse_metrics=metrics)
    assert trace.valid_for_calibration is False


def test_trace_rejects_non_finite_required_telemetry():
    trace = _trace()
    sample = trace.samples[0]
    trace.samples[0] = replace(
        sample,
        host=replace(sample.host, available_gib=float("nan")),
    )
    assert trace.valid_for_calibration is False

    trace = _trace()
    sample = trace.samples[0]
    metrics = dict(sample.clickhouse_metrics)
    metrics["event.InsertedBytes"] = float("inf")
    trace.samples[0] = replace(sample, clickhouse_metrics=metrics)
    assert trace.valid_for_calibration is False


def test_trace_summary_preserves_unknown_oom_state():
    trace = _trace()
    sample = trace.samples[0]
    trace.samples[0] = replace(sample, engine=replace(sample.engine, oom_killed=None))
    assert trace.to_dict()["summary"]["oom_killed"] is None


def test_trace_rejects_missing_engine_limit_sample():
    trace = _trace()
    sample = trace.samples[0]
    trace.samples.append(
        clickhouse_memory.TraceSample(
            observed_at_utc=sample.observed_at_utc,
            elapsed_s=sample.elapsed_s + 1,
            host=sample.host,
            engine=clickhouse_memory.EngineMemorySample(
                "mocker", "clickhouse", 512, None, oom_killed=False, running=True
            ),
            clickhouse_metrics=sample.clickhouse_metrics,
            responsiveness_ms=sample.responsiveness_ms,
            server_reachable=True,
        )
    )
    assert trace.valid_for_calibration is False


def test_select_lowest_successful_rung_does_not_choose_failed_or_unmeasured():
    low = _trace()
    high = _trace(rung=clickhouse_memory.DEFAULT_MEMORY_RUNGS[1])
    failed_low = _trace(outcome="failed")
    failed_low.rung = clickhouse_memory.DEFAULT_MEMORY_RUNGS[0]
    assert clickhouse_memory.select_lowest_successful_rung([failed_low, high]) == high.rung
    assert clickhouse_memory.select_lowest_successful_rung([low, high]) == low.rung


def test_select_lowest_successful_rung_fails_closed_without_trace():
    with pytest.raises(ValueError, match="no valid passing memory trace"):
        clickhouse_memory.select_lowest_successful_rung([_trace(outcome="timed-out")])


def test_write_trace_is_atomic_and_contains_schema_and_summary(tmp_path: Path):
    output = tmp_path / "trace.json"
    clickhouse_memory.write_trace(output, _trace())
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["trace_schema_version"] == clickhouse_memory.TRACE_SCHEMA_VERSION
    assert payload["summary"]["valid_for_calibration"] is True
    assert not (tmp_path / ".trace.json.tmp").exists()


def test_read_trace_reconstructs_loader_and_engine_guards(tmp_path: Path):
    output = tmp_path / "trace.json"
    original = _trace()
    clickhouse_memory.write_trace(output, original)
    loaded = clickhouse_memory.read_trace(output)
    assert loaded.valid_for_calibration is True
    assert loaded.rung == original.rung
    assert loaded.samples[0].engine.limit_bytes == original.samples[0].engine.limit_bytes


def test_collector_records_injected_engine_stats_without_live_daemon(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        clickhouse_memory,
        "read_host_memory",
        lambda: clickhouse_memory.HostMemorySample(available_gib=3.0, free_gib=1.0, swap_used_percent=4.0),
    )
    monkeypatch.setattr(
        clickhouse_memory,
        "read_clickhouse_metrics",
        lambda *args, **kwargs: ({"event.InsertedRows": 42.0}, 3.5),
    )
    seen_argv = []

    def command_runner(argv):
        seen_argv.append(tuple(argv))
        return 0, '{"Name":"clickhouse","MemUsage":"1GiB / 4GiB"}\n', ""

    trace = clickhouse_memory.ClickHouseMemoryTrace(
        platform="clickhouse-server",
        rung=clickhouse_memory.DEFAULT_MEMORY_RUNGS[1],
        started_at_utc=clickhouse_memory.utc_now(),
    )
    collector = clickhouse_memory.MemoryTraceCollector(
        trace=trace,
        output_path=tmp_path / "trace.json",
        interval_s=0.01,
        engine="mocker",
        project_name="benchbox-uat-test",
        compose_files=[Path("docker/clickhouse/docker-compose.yml")],
        command_runner=command_runner,
    )
    collector.start()
    assert json.loads((tmp_path / "trace.json").read_text(encoding="utf-8"))["outcome"] == "running"
    collector.stop(outcome="passed")
    assert trace.samples
    assert trace.samples[0].engine.usage_bytes == clickhouse_memory.GIB
    assert trace.samples[0].responsiveness_ms == 3.5
    assert seen_argv[0][-1] == "benchbox-uat-test-clickhouse-1"


def test_main_replaces_stale_artifact_before_child_start(monkeypatch, tmp_path: Path):
    output = tmp_path / "trace.json"
    output.write_text('{"outcome":"passed"}\n', encoding="utf-8")
    started_payloads = []

    class FakeCollector:
        def __init__(self, *, trace, output_path, **kwargs):
            self.trace = trace
            self.output_path = output_path

        def start(self):
            started_payloads.append(json.loads(self.output_path.read_text(encoding="utf-8")))

        def stop(self, *, outcome, failure_reason=None):
            self.trace.finish(outcome=outcome, failure_reason=failure_reason)
            clickhouse_memory.write_trace(self.output_path, self.trace)

    monkeypatch.setattr(clickhouse_memory, "verify_native_streaming_loader", lambda: (True, "ok"))
    monkeypatch.setattr(clickhouse_memory, "resolve_driver_timeout", lambda command: (300, "test"))
    monkeypatch.setattr(clickhouse_memory, "MemoryTraceCollector", FakeCollector)
    monkeypatch.setattr(
        clickhouse_memory.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""),
    )

    assert clickhouse_memory.main(["--output", str(output), "--rung", "baseline-1g", "--", "true"]) == 0
    assert started_payloads and started_payloads[0]["outcome"] == "running"
    assert started_payloads[0]["trace_schema_version"] == clickhouse_memory.TRACE_SCHEMA_VERSION


def test_main_leaves_running_artifact_when_child_is_interrupted(monkeypatch, tmp_path: Path):
    output = tmp_path / "trace.json"
    output.write_text('{"outcome":"passed"}\n', encoding="utf-8")

    class FakeCollector:
        def __init__(self, *, trace, output_path, **kwargs):
            self.trace = trace
            self.output_path = output_path

        def start(self):
            pass

        def stop(self, *, outcome, failure_reason=None):
            raise AssertionError("interrupted child must not publish a passing/final trace")

    monkeypatch.setattr(clickhouse_memory, "verify_native_streaming_loader", lambda: (True, "ok"))
    monkeypatch.setattr(clickhouse_memory, "resolve_driver_timeout", lambda command: (300, "test"))
    monkeypatch.setattr(clickhouse_memory, "MemoryTraceCollector", FakeCollector)

    def interrupt_child(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(clickhouse_memory.subprocess, "run", interrupt_child)

    with pytest.raises(KeyboardInterrupt):
        clickhouse_memory.main(["--output", str(output), "--rung", "baseline-1g", "--", "true"])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["outcome"] == "running"
    assert payload["summary"]["valid_for_calibration"] is False


def test_rung_matrix_includes_experimental_525g_candidate():
    rendered = clickhouse_memory.rung_matrix_text()
    assert "candidate-5.25g\t5.25\t5.25\t300" in rendered


def test_rung_matrix_excludes_1000_row_language():
    rendered = clickhouse_memory.rung_matrix_text()
    assert "baseline-1g" in rendered
    assert "1000" not in rendered


def test_summarize_command_failure_preserves_memory_root_cause():
    reason = clickhouse_memory.summarize_command_failure(
        "Code: 241. DB::Exception: memory limit exceeded: current RSS: 871 MiB",
        1,
    )
    assert "memory limit exceeded" in reason
    assert "871 MiB" in reason


def test_parse_driver_timeout_requires_explicit_platform_option():
    assert clickhouse_memory.parse_driver_timeout(["benchbox", "--platform-option", "send_receive_timeout=600"]) == 600
    assert clickhouse_memory.parse_driver_timeout(["benchbox", "--platform-option", "password=benchbox"]) is None


def test_default_driver_timeout_matches_server_setup():
    assert clickhouse_memory.default_clickhouse_driver_timeout_s() == clickhouse_memory.DEFAULT_DRIVER_TIMEOUT_S
    timeout, source = clickhouse_memory.resolve_driver_timeout(["benchbox", "--platform-option", "password=benchbox"])
    assert timeout == 300
    assert source == "ClickHouseSetupMixin server default"


def test_read_trace_rejects_unknown_schema(tmp_path: Path):
    output = tmp_path / "trace.json"
    clickhouse_memory.write_trace(output, _trace())
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["trace_schema_version"] = 1
    output.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported ClickHouse memory trace schema"):
        clickhouse_memory.read_trace(output)
