from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.throughput.result import ThroughputResult, ThroughputStreamResult
from benchbox.core.throughput.runner import StreamRunner

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpch]


def _stream(stream_id: int, start: float, end: float, start_wall: str, end_wall: str) -> ThroughputStreamResult:
    return ThroughputStreamResult(
        stream_id=stream_id,
        start_time=start,
        end_time=end,
        duration=end - start,
        queries_executed=22,
        queries_successful=22,
        queries_failed=0,
        start_wall_time=start_wall,
        end_wall_time=end_wall,
    )


def test_total_time_spans_refresh_stream_window_when_it_brackets_the_query_streams() -> None:
    result = ThroughputResult(
        start_time="",
        end_time="",
        total_time=0,
        throughput_at_size=0,
        streams_executed=2,
        streams_successful=2,
        stream_results=[
            _stream(1, 10.0, 18.0, "query-1-start", "query-1-end"),
            _stream(2, 12.0, 20.0, "query-2-start", "query-2-end"),
        ],
    )
    result.refresh_stream = SimpleNamespace(
        start_monotonic=8.0,
        end_monotonic=22.0,
        start_time="refresh-start",
        end_time="refresh-end",
        success=True,
    )

    success = StreamRunner.compute_metrics(result, SimpleNamespace(num_streams=2, scale_factor=1.0), 10.0)

    assert success is True
    assert result.total_time == 14.0
    assert result.start_time == "refresh-start"
    assert result.end_time == "refresh-end"
    assert result.throughput_at_size == pytest.approx(44 * 3600 / 14)


def test_sub_millisecond_refresh_functions_keep_positive_duration_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.core.tpch import power_test, throughput_test
    from benchbox.core.tpch.power_test import TPCHPowerTest
    from benchbox.core.tpch.throughput_test import TPCHThroughputTest

    update_set = SimpleNamespace(set_id=1)
    connection = SimpleNamespace(
        close=lambda: None, platform_adapter=SimpleNamespace(record_refresh_applied=lambda _connection: None)
    )
    monkeypatch.setattr(power_test, "check_rf1_applied", lambda *_args: None)
    monkeypatch.setattr(power_test, "apply_rf1", lambda *_args: 1)
    monkeypatch.setattr(throughput_test, "apply_rf1", lambda *_args: 1)
    monkeypatch.setattr(power_test, "elapsed_seconds", lambda _start: 0.0001)
    monkeypatch.setattr(throughput_test, "elapsed_seconds", lambda _start: 0.0001)

    power = TPCHPowerTest(
        benchmark=object(),
        connection=connection,
        refresh_update_set=update_set,
        refresh_connection_factory=lambda: connection,
    )
    throughput = TPCHThroughputTest(
        benchmark=object(),
        connection_factory=lambda: connection,
        refresh_enabled=True,
        refresh_connection_factory=lambda: connection,
    )

    power_evidence = power._execute_refresh_function("RF1", connection)
    throughput_evidence = throughput._execute_refresh_function("RF1", update_set, connection)

    assert power_evidence["status"] == throughput_evidence["status"] == "SUCCESS"
    assert power_evidence["duration_ms"] == throughput_evidence["duration_ms"] == 1


def test_failed_refresh_withholds_throughput_without_discarding_refresh_evidence() -> None:
    pairs = [{"update_set": 2, "status": "FAILED", "functions": [{"function": "RF1", "status": "SUCCESS"}]}]
    result = ThroughputResult(
        start_time="",
        end_time="",
        total_time=0,
        throughput_at_size=99.0,
        streams_executed=2,
        streams_successful=2,
        stream_results=[
            _stream(1, 10.0, 18.0, "query-1-start", "query-1-end"),
            _stream(2, 12.0, 20.0, "query-2-start", "query-2-end"),
        ],
    )
    result.refresh_stream = SimpleNamespace(
        start_monotonic=8.0,
        end_monotonic=22.0,
        start_time="refresh-start",
        end_time="refresh-end",
        status="FAILED",
        success=False,
        pairs=pairs,
    )

    success = StreamRunner.compute_metrics(result, SimpleNamespace(num_streams=2, scale_factor=1.0), 10.0)

    assert success is False
    assert result.throughput_at_size == 0.0
    assert result.query_throughput == 0.0
    assert result.refresh_stream.pairs == pairs
    assert result.stream_results[0].queries_executed == 22
    assert result.stream_results[1].queries_executed == 22


def _refresh_test(factory):
    from benchbox.core.tpch.throughput_test import TPCHThroughputTest

    return TPCHThroughputTest(
        benchmark=object(),
        connection_factory=lambda: None,
        scale_factor=0.01,
        num_streams=1,
        refresh_enabled=True,
        refresh_connection_factory=factory,
    )


def _run_refresh_stream(test):
    from benchbox.core.tpch.throughput_test import TPCHRefreshStreamResult

    stream = TPCHRefreshStreamResult(first_set=2)
    return test._execute_refresh_stream({}, 0, stream)


def test_refresh_stream_window_starts_after_refresh_connection_opens() -> None:
    import time

    from benchbox.utils.clock import mono_time

    opened_at = []

    def factory():
        time.sleep(0.2)
        opened_at.append(mono_time())
        return SimpleNamespace(
            close=lambda: None, platform_adapter=SimpleNamespace(record_refresh_applied=lambda _connection: None)
        )

    stream = _run_refresh_stream(_refresh_test(factory))

    assert stream.status == "COMPLETED"
    assert stream.start_monotonic >= opened_at[0]
    assert stream.end_monotonic - stream.start_monotonic < 0.1


def test_throughput_total_time_excludes_refresh_connection_setup() -> None:
    import time

    def factory():
        time.sleep(0.2)
        return SimpleNamespace(
            close=lambda: None, platform_adapter=SimpleNamespace(record_refresh_applied=lambda _connection: None)
        )

    refresh = _run_refresh_stream(_refresh_test(factory))
    base = refresh.start_monotonic
    result = ThroughputResult(
        start_time="",
        end_time="",
        total_time=0,
        throughput_at_size=0,
        streams_executed=1,
        streams_successful=1,
        stream_results=[_stream(1, base + 0.01, base + 0.02, "query-start", "query-end")],
    )
    result.refresh_stream = refresh

    success = StreamRunner.compute_metrics(result, SimpleNamespace(num_streams=1, scale_factor=1.0), base - 0.2)

    assert success is True
    assert result.total_time < 0.1


@pytest.mark.parametrize("function", ["RF1", "RF2"])
def test_the_refresh_marker_is_written_before_the_timer_starts(monkeypatch: pytest.MonkeyPatch, function: str) -> None:
    from benchbox.core.tpch import maintenance_test, power_test, throughput_test
    from benchbox.core.tpch.maintenance_test import TPCHMaintenanceTest
    from benchbox.core.tpch.power_test import TPCHPowerTest
    from benchbox.core.tpch.refresh import TPCHRF2Deletes
    from benchbox.core.tpch.throughput_test import TPCHThroughputTest

    update_set = SimpleNamespace(set_id=1)
    connection = SimpleNamespace(
        close=lambda: None, platform_adapter=SimpleNamespace(record_refresh_applied=lambda _connection: None)
    )
    events: list[str] = []
    applied = 1 if function == "RF1" else TPCHRF2Deletes(orders=1, lineitems=1)
    apply_name = "apply_rf1" if function == "RF1" else "apply_rf2"
    for module in (power_test, throughput_test, maintenance_test):
        monkeypatch.setattr(module, "record_refresh_applied", lambda _connection: events.append("marker"))
        monkeypatch.setattr(module, "mono_time", lambda: events.append("timer") or 0.0)
        monkeypatch.setattr(module, apply_name, lambda *_args: events.append("apply") or applied)
    for module in (power_test, maintenance_test):
        monkeypatch.setattr(module, "capture_rf2_baseline", lambda *_args: None)
        monkeypatch.setattr(module, "verify_rf2_deletes", lambda *_args: None)
    monkeypatch.setattr(power_test, "check_rf1_applied", lambda *_args: None)
    monkeypatch.setattr(power_test, "check_rf2_applied", lambda *_args: None)
    monkeypatch.setattr(maintenance_test, "check_rf1_applied", lambda *_args: None)
    monkeypatch.setattr(maintenance_test, "check_rf2_applied", lambda *_args: None)

    power = TPCHPowerTest(
        benchmark=object(),
        connection=connection,
        refresh_update_set=update_set,
        refresh_connection_factory=lambda: connection,
    )
    throughput = TPCHThroughputTest(
        benchmark=object(),
        connection_factory=lambda: connection,
        refresh_enabled=True,
        refresh_connection_factory=lambda: connection,
    )
    maintenance = TPCHMaintenanceTest(connection_factory=lambda: connection)
    run_maintenance = maintenance._execute_rf1 if function == "RF1" else maintenance._execute_rf2

    for run in (
        lambda: power._execute_refresh_function(function, connection),
        lambda: run_maintenance(0, update_set),
    ):
        events.clear()
        run()
        assert events.index("marker") < events.index("timer") < events.index("apply")

    events.clear()
    throughput._execute_refresh_function(function, update_set, connection)
    assert "marker" not in events


def test_the_throughput_refresh_marker_is_written_before_the_refresh_stream_interval_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from benchbox.core.tpch import throughput_test
    from benchbox.core.tpch.refresh import TPCHRF2Deletes
    from benchbox.core.tpch.throughput_test import TPCHRefreshStreamResult, TPCHThroughputTest

    events: list[str] = []
    connection = SimpleNamespace(
        close=lambda: None, platform_adapter=SimpleNamespace(record_refresh_applied=lambda _connection: None)
    )
    monkeypatch.setattr(throughput_test, "record_refresh_applied", lambda _connection: events.append("marker"))
    monkeypatch.setattr(throughput_test, "mono_time", lambda: events.append("timer") or 0.0)
    monkeypatch.setattr(throughput_test, "apply_rf1", lambda *_args: events.append("apply") or 1)
    monkeypatch.setattr(
        throughput_test, "apply_rf2", lambda *_args: events.append("apply") or TPCHRF2Deletes(orders=1, lineitems=1)
    )
    throughput = TPCHThroughputTest(
        benchmark=object(),
        connection_factory=lambda: connection,
        refresh_enabled=True,
        refresh_connection_factory=lambda: connection,
    )

    throughput._execute_refresh_stream({1: SimpleNamespace(set_id=1)}, 1, TPCHRefreshStreamResult(first_set=1))

    assert events.count("marker") == 1
    assert events.index("marker") < events.index("timer")
