from __future__ import annotations

import time

import pytest

from benchbox.core.results.fetch_mode import ConversionClock
from benchbox.core.results.platform_timing import (
    NET_ESTIMATE_CONFORMANCE,
    StatementTiming,
    build_platform_timing,
    deferred_platform_timing,
    is_throughput_stream_active,
    resolve_net_estimate_status,
    rows_result_bytes,
    summarize_run_platform_timing,
    throughput_stream_context,
    unavailable_platform_timing,
)
from benchbox.core.results.timer_scope import RequestLedger, outside_query_timer
from benchbox.core.results.timing_fields import platform_timing_problems, required_timing_problems

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PROBE = {"statement_overhead_ms": {"samples": 5, "min": 2.0, "median": 2.5}, "platform_timing": {"status": "valid"}}


def _statement(statement_id="s1", platform_ms=10.0, **kwargs):
    return StatementTiming(statement_id, fields={"TOTAL": platform_ms}, platform_ms=platform_ms, **kwargs)


def test_outside_query_timer_is_subtracted_once_even_when_nested():
    clock = ConversionClock().start()
    with outside_query_timer():
        with outside_query_timer():
            time.sleep(0.02)
        ConversionClock.record_since(time.perf_counter() - 5.0)
    excluded = clock.finish()
    assert 0.02 <= excluded < 1.0


def test_outside_query_timer_without_a_clock_is_inert():
    with outside_query_timer():
        pass


def test_request_ledger_counts_every_request_kind():
    ledger = RequestLedger()
    ledger.add_request("begin")
    ledger.add_request("query", "id-1")
    ledger.add_request("commit")
    assert ledger.count == 3
    assert ledger.statement_ids() == ["id-1"]
    assert ledger.statement_ids("begin") == []


def test_valid_platform_timing_sums_requests_and_conforms_to_the_contract():
    timing = build_platform_timing(
        "snowflake.query_history",
        [_statement("a", 10.0, kind="begin"), _statement("b", 30.0, compile_ms=5.0, exec_ms=20.0)],
        client_ms=60.0,
    )
    assert timing["status"] == "valid"
    assert timing["platform_ms"] == 40.0
    assert [request["id"] for request in timing["requests"]] == ["a", "b"]
    assert platform_timing_problems(timing) == []


def test_platform_time_above_client_time_is_marked_suspect_not_deleted():
    timing = build_platform_timing("src", [_statement(platform_ms=80.0)], client_ms=40.0)
    assert timing["status"] == "suspect"
    assert timing["platform_ms"] == 80.0
    assert platform_timing_problems(timing) == []


def test_missing_statement_id_or_timing_is_unavailable_with_a_reason():
    no_id = build_platform_timing("src", [StatementTiming(None)], client_ms=1.0)
    assert no_id["status"] == "unavailable" and "id missing" in no_id["reason"]
    no_time = build_platform_timing("src", [StatementTiming("x")], client_ms=1.0)
    assert no_time["status"] == "unavailable" and no_time["requests"][0]["id"] == "x"
    assert unavailable_platform_timing("src", "why")["status"] == "unavailable"
    assert platform_timing_problems(no_time) == []


def test_run_summary_reports_valid_coverage():
    queries = [
        {"status": "SUCCESS", "platform_timing": {"status": "valid"}},
        {"status": "SUCCESS", "platform_timing": {"status": "unavailable"}},
        {"status": "FAILED"},
    ]
    summary = summarize_run_platform_timing(queries, source="src", definition="server_complete")
    assert summary == {"source": "src", "definition": "server_complete", "coverage": 0.5}
    assert summarize_run_platform_timing([{"status": "SUCCESS"}], source="src", definition="server_complete") is None


def test_run_summary_exists_whenever_any_query_carries_platform_timing():
    queries = [
        {"status": "FAILED", "platform_timing": {"status": "valid"}},
        {"status": "SUCCESS", "run_type": "warmup", "platform_timing": {"status": "valid"}},
    ]
    summary = summarize_run_platform_timing(queries, source="src", definition="server_complete")
    assert summary == {"source": "src", "definition": "server_complete", "coverage": 0.0}
    payload = {"run": {"timing_method": 1, "net_estimate_status": "x", "platform_timing": summary}, "queries": queries}
    assert required_timing_problems(payload) == []


def test_rows_result_bytes_counts_client_values():
    assert rows_result_bytes([("ab", 1, None)]) == 2 + 8
    assert rows_result_bytes(None) is None


@pytest.mark.parametrize(
    ("endpoint", "method", "client_link", "expected"),
    [
        ("embedded_process", 2, PROBE, "embedded"),
        ("unknown", 2, PROBE, "endpoint_unknown"),
        ("cloud_endpoint", 1, PROBE, "timing_method"),
        ("cloud_endpoint", 2, {"statement_overhead_ms": {"samples": 2, "min": 1.0}}, "probe_samples"),
        ("cloud_endpoint", 2, None, "probe_samples"),
        ("cloud_endpoint", 2, PROBE, "probe_unverified"),
    ],
)
def test_every_adapter_without_conformance_stays_ineligible(endpoint, method, client_link, expected):
    assert not NET_ESTIMATE_CONFORMANCE
    status = resolve_net_estimate_status(
        platform="snowflake", timing_method=method, endpoint_class=endpoint, client_link=client_link, queries=[]
    )
    assert status == expected


def test_eligible_needs_a_conformance_entry_and_a_run_that_passes_the_floor_rule(monkeypatch):
    from benchbox.core.results import platform_timing

    monkeypatch.setattr(platform_timing, "NET_ESTIMATE_CONFORMANCE", {"snowflake": "run-1"})
    good = {
        "status": "SUCCESS",
        "ms": 100.0,
        "request_count": 2,
        "platform_timing": {"status": "valid", "platform_ms": 50.0},
    }
    args = {"platform": "snowflake", "timing_method": 2, "endpoint_class": "cloud_endpoint", "client_link": PROBE}
    assert resolve_net_estimate_status(queries=[good], **args) == "eligible"
    tight = {**good, "ms": 54.0}
    assert resolve_net_estimate_status(queries=[tight], **args) == "floor_suspect"
    assert resolve_net_estimate_status(queries=[good], **{**args, "platform": "bigquery"}) == "probe_unverified"


def test_normalized_query_results_keep_timing_fields():
    from benchbox.core.results.query_normalizer import normalize_query_result

    normalized = normalize_query_result(
        {
            "query_id": "1",
            "status": "SUCCESS",
            "execution_time_seconds": 0.1,
            "rows_returned": 1,
            "request_count": 2,
            "result_bytes": 5,
            "platform_timing": {"status": "unavailable", "reason": "r"},
        }
    )
    assert (normalized.request_count, normalized.result_bytes) == (2, 5)
    assert normalized.platform_timing == {"status": "unavailable", "reason": "r"}


def test_throughput_stream_skips_platform_history_polling():
    from unittest.mock import Mock

    from benchbox.platforms.base.timing_contract import TimingContractMixin

    class _Adapter(TimingContractMixin):
        platform_timing_source = "mock.history"

    adapter = _Adapter()
    adapter.fetch_platform_timings = Mock(return_value={})
    ledger = RequestLedger()
    ledger.add_request("query", "stmt-1")

    assert not is_throughput_stream_active()

    with throughput_stream_context():
        assert is_throughput_stream_active()
        fields = adapter.timing_fields_for(Mock(), ledger, elapsed_seconds=0.1)
        assert fields["platform_timing"]["status"] == "unavailable"
        assert fields["platform_timing"]["reason"] == "skipped during throughput stream to preserve stream timing"
        assert fields["platform_timing"]["requests"][0]["id"] == "stmt-1"
        adapter.fetch_platform_timings.assert_not_called()

    assert not is_throughput_stream_active()

    fields_stream = adapter.timing_fields_for(Mock(), ledger, elapsed_seconds=0.1, stream_id=1)
    assert fields_stream["platform_timing"]["status"] == "unavailable"
    assert fields_stream["platform_timing"]["reason"] == "skipped during throughput stream to preserve stream timing"
    adapter.fetch_platform_timings.assert_not_called()


def test_measured_phase_defers_platform_history_until_resolved():
    from unittest.mock import Mock

    from benchbox.platforms.base.timing_contract import TimingContractMixin

    class _Adapter(TimingContractMixin):
        platform_timing_source = "mock.history"

    adapter = _Adapter()
    adapter.fetch_platform_timings = Mock(
        return_value={"stmt-1": _statement("stmt-1", 40.0), "stmt-2": _statement("stmt-2", 60.0)}
    )
    connection = Mock()
    first, second = RequestLedger(), RequestLedger()
    first.add_request("query", "stmt-1")
    second.add_request("query", "stmt-2")

    with deferred_platform_timing() as deferred:
        first_fields = adapter.timing_fields_for(connection, first, elapsed_seconds=0.1)
        second_fields = adapter.timing_fields_for(connection, second, elapsed_seconds=0.1)
        adapter.fetch_platform_timings.assert_not_called()
        assert first_fields["platform_timing"]["status"] == "unavailable"
        assert first_fields["platform_timing"]["reason"] == (
            "platform history lookup deferred until the measured phase ends"
        )
        deferred.resolve()

    adapter.fetch_platform_timings.assert_called_once_with(connection, ["stmt-1", "stmt-2"])
    assert first_fields["platform_timing"]["status"] == "valid"
    assert first_fields["platform_timing"]["platform_ms"] == 40.0
    assert second_fields["platform_timing"]["platform_ms"] == 60.0


def test_deferred_lookup_failure_marks_records_unavailable():
    from unittest.mock import Mock

    from benchbox.platforms.base.timing_contract import TimingContractMixin

    class _Adapter(TimingContractMixin):
        platform_timing_source = "mock.history"

    adapter = _Adapter()
    adapter.fetch_platform_timings = Mock(side_effect=RuntimeError("history down"))
    ledger = RequestLedger()
    ledger.add_request("query", "stmt-1")

    with deferred_platform_timing() as deferred:
        fields = adapter.timing_fields_for(Mock(), ledger, elapsed_seconds=0.1)
        deferred.resolve()

    assert fields["platform_timing"]["status"] == "unavailable"
    assert fields["platform_timing"]["reason"] == "platform history lookup failed: RuntimeError"


def test_deferred_lookup_widens_the_history_window_to_cover_the_phase():
    import datetime
    from unittest.mock import Mock

    from benchbox.core.results.platform_timing import history_lookup_minutes
    from benchbox.platforms.base.timing_contract import TimingContractMixin

    seen_minutes: list[int] = []

    class _Adapter(TimingContractMixin):
        platform_timing_source = "mock.history"

        def fetch_platform_timings(self, connection, statement_ids):
            seen_minutes.append(history_lookup_minutes(5))
            return {}

    adapter = _Adapter()
    ledger = RequestLedger()
    ledger.add_request("query", "stmt-1")

    with deferred_platform_timing() as deferred:
        adapter.timing_fields_for(Mock(), ledger, elapsed_seconds=0.1)
        deferred.entries[0].queued_at -= datetime.timedelta(minutes=40)
        deferred.resolve()

    assert seen_minutes and seen_minutes[0] >= 41
    assert history_lookup_minutes(5) == 5


def test_snowflake_history_sql_uses_the_widened_window():
    import datetime

    from benchbox.core.results.platform_timing import history_lookup_window
    from benchbox.platforms.snowflake import SnowflakeAdapter

    assert "DATEADD('MINUTE', -5," in SnowflakeAdapter._query_history_sql(["QUERY_ID"], ["01a2"])
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=90)
    with history_lookup_window(since):
        sql = SnowflakeAdapter._query_history_sql(["QUERY_ID"], ["01a2"])
    assert "DATEADD('MINUTE', -92," in sql


def test_non_deferrable_history_is_skipped_inside_a_measured_phase():
    from unittest.mock import Mock

    from benchbox.platforms.base.timing_contract import TimingContractMixin

    class _Adapter(TimingContractMixin):
        platform_timing_source = "mock.history"
        platform_history_deferrable = False

    adapter = _Adapter()
    adapter.fetch_platform_timings = Mock(return_value={})
    ledger = RequestLedger()
    ledger.add_request("query", "stmt-1")

    with deferred_platform_timing() as deferred:
        fields = adapter.timing_fields_for(Mock(), ledger, elapsed_seconds=0.1)
        assert deferred.entries == []
        deferred.resolve()

    adapter.fetch_platform_timings.assert_not_called()
    assert fields["platform_timing"]["reason"] == (
        "skipped inside a measured phase: platform history is kept too briefly to look up afterwards"
    )


def test_tpcds_power_test_resolves_platform_history_after_phase_end(monkeypatch):
    from unittest.mock import Mock

    from benchbox.core.results import platform_timing
    from benchbox.core.tpcds.power_test import TPCDSPowerTest

    events: list[str] = []
    real_resolve = platform_timing.DeferredPlatformTiming.resolve

    def tracking_resolve(self):
        events.append("resolve")
        real_resolve(self)

    monkeypatch.setattr(platform_timing.DeferredPlatformTiming, "resolve", tracking_resolve)

    def execute(position, query_info, connection, seed, result):
        assert platform_timing.active_deferred_platform_timing() is not None
        events.append("query")

    connection = Mock()
    connection.close.side_effect = lambda: events.append("close")
    test = TPCDSPowerTest.__new__(TPCDSPowerTest)
    test._execute_one_query = execute
    test._finalize_power_metrics = lambda result, start_time: events.append("finalize")
    test._determine_available_query_ids = lambda: [1]
    test._build_queries_to_execute = lambda ids: [(1, None)]
    test._preflight_validate_generation = lambda ids: None
    test.connection_factory = lambda: connection
    test.config = Mock(verbose=False, seed=1, stream_id=0)
    test.logger = Mock()

    test.run()

    assert events == ["query", "finalize", "resolve", "close"]
    assert platform_timing.active_deferred_platform_timing() is None
