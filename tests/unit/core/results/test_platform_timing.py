from __future__ import annotations

import time

import pytest

from benchbox.core.results.fetch_mode import ConversionClock
from benchbox.core.results.platform_timing import (
    NET_ESTIMATE_CONFORMANCE,
    StatementTiming,
    build_platform_timing,
    resolve_net_estimate_status,
    rows_result_bytes,
    summarize_run_platform_timing,
    unavailable_platform_timing,
)
from benchbox.core.results.timer_scope import RequestLedger, outside_query_timer
from benchbox.core.results.timing_fields import platform_timing_problems

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
