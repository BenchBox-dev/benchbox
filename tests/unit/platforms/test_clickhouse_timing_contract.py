import uuid

import pytest

from benchbox.core.results.timer_scope import TIMING_METHOD_QUERY_ONLY
from benchbox.platforms.clickhouse import timing as ch_timing

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.usefixtures("chdb_probe_satisfied"),
]


def _make(mode, **kwargs):
    if mode == "local":
        from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter

        return ClickHouseLocalAdapter(**kwargs)
    if mode == "server":
        from benchbox.platforms.clickhouse_server import ClickHouseServerAdapter

        return ClickHouseServerAdapter(**kwargs)
    from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

    return ClickHouseCloudAdapter(host="h", password="p", **kwargs)


class _LogConnection:
    def __init__(self, log_rows=None, rows=None):
        self.statements = []
        self.options = []
        self.log_rows = log_rows if log_rows is not None else []
        self.rows = rows if rows is not None else [(1,), (2,)]

    def execute(self, sql, *args, **kwargs):
        self.statements.append(sql)
        self.options.append(kwargs)
        if "system.query_log" in sql and sql.lstrip().upper().startswith("SELECT"):
            return list(self.log_rows)
        if sql.startswith("SYSTEM"):
            return []
        return self.rows


def _log_row(query_id, duration=12):
    return (query_id, duration, 100, 2048, 0, 0, 2, 16, 4096)


@pytest.mark.parametrize("mode", ["local", "server", "cloud"])
def test_every_clickhouse_mode_uses_query_only_timer(mode):
    assert _make(mode).timing_method == TIMING_METHOD_QUERY_ONLY


def test_local_mode_has_no_platform_timing_source():
    assert _make("local").platform_timing_source is None


@pytest.mark.parametrize("mode", ["server", "cloud"])
def test_networked_modes_source_query_log(mode):
    adapter = _make(mode)
    assert adapter.platform_timing_source == ch_timing.QUERY_LOG_SOURCE


def test_query_duration_ms_definition_includes_result_delivery():
    assert _make("server").platform_timing_definition == "includes_fetch"


def test_server_execute_query_assigns_client_query_id_and_records_it():
    adapter = _make("server")
    connection = _LogConnection()
    seen = {}

    def capture(conn, ledger, **kwargs):
        seen["ids"] = ledger.statement_ids()
        return {"request_count": ledger.count}

    adapter.timing_fields_for = capture
    result = adapter.execute_query(connection, "SELECT 1", "q1", validate_row_count=False)
    assert result["status"] == "SUCCESS"
    assert result["request_count"] == 1
    sent = connection.options[0]["query_id"]
    uuid.UUID(sent)
    assert seen["ids"] == [sent]


def test_local_execute_query_sends_no_query_id():
    adapter = _make("local")
    connection = _LogConnection()
    result = adapter.execute_query(connection, "SELECT 1", "q1", validate_row_count=False)
    assert connection.options[0] == {}
    assert result["request_count"] == 1
    assert "platform_timing" not in result
    assert result["result_bytes"] > 0


def test_server_execute_query_reports_platform_timing_by_query_id(monkeypatch):
    monkeypatch.setattr(ch_timing, "QUERY_LOG_POLL_INTERVAL_SECONDS", 0)
    adapter = _make("server")
    connection = _LogConnection()

    def execute(sql, *args, **kwargs):
        connection.statements.append(sql)
        connection.options.append(kwargs)
        if "query_log" in sql and sql.startswith("SELECT"):
            return [_log_row(connection.options[0]["query_id"], duration=7)]
        if sql.startswith("SYSTEM"):
            return []
        return [(1,)]

    connection.execute = execute
    result = adapter.execute_query(connection, "SELECT 1", "q1", validate_row_count=False)
    timing = result["platform_timing"]
    assert timing["source"] == ch_timing.QUERY_LOG_SOURCE
    assert timing["platform_ms"] == 7.0
    assert timing["requests"][0]["id"] == connection.options[0]["query_id"]
    assert timing["requests"][0]["fields"]["read_bytes"] == 2048


def test_lookup_by_query_id_never_by_tag(monkeypatch):
    monkeypatch.setattr(ch_timing, "QUERY_LOG_POLL_INTERVAL_SECONDS", 0)
    adapter = _make("server")
    query_id = str(uuid.uuid4())
    connection = _LogConnection(log_rows=[_log_row(query_id)])
    timings = adapter.fetch_platform_timings(connection, [query_id])
    lookup = next(sql for sql in connection.statements if sql.startswith("SELECT"))
    assert f"query_id IN ('{query_id}')" in lookup
    assert "type = 'QueryFinish'" in lookup
    assert "system.query_log" in lookup
    assert "clusterAllReplicas" not in lookup
    assert connection.statements[0] == "SYSTEM FLUSH LOGS"
    assert timings[query_id].platform_ms == 12.0
    assert timings[query_id].fields["memory_usage"] == 4096


def test_cloud_lookup_spans_replicas(monkeypatch):
    monkeypatch.setattr(ch_timing, "QUERY_LOG_POLL_INTERVAL_SECONDS", 0)
    adapter = _make("cloud")
    query_id = str(uuid.uuid4())
    connection = _LogConnection(log_rows=[_log_row(query_id)])
    adapter.fetch_platform_timings(connection, [query_id])
    assert any("clusterAllReplicas(default, system.query_log)" in sql for sql in connection.statements)


def test_lookup_polls_until_log_row_is_visible(monkeypatch):
    monkeypatch.setattr(ch_timing, "QUERY_LOG_POLL_INTERVAL_SECONDS", 0)
    adapter = _make("server")
    query_id = str(uuid.uuid4())
    connection = _LogConnection()
    responses = [[], [], [_log_row(query_id)]]
    original = connection.execute

    def execute(sql, *args, **kwargs):
        if sql.startswith("SELECT"):
            connection.statements.append(sql)
            return responses.pop(0)
        return original(sql, *args, **kwargs)

    connection.execute = execute
    timings = adapter.fetch_platform_timings(connection, [query_id])
    assert query_id in timings
    assert responses == []


def test_lookup_is_bounded_when_row_never_appears(monkeypatch):
    monkeypatch.setattr(ch_timing, "QUERY_LOG_POLL_INTERVAL_SECONDS", 0)
    adapter = _make("server")
    connection = _LogConnection()
    assert adapter.fetch_platform_timings(connection, [str(uuid.uuid4())]) == {}
    selects = [sql for sql in connection.statements if sql.startswith("SELECT")]
    assert len(selects) == ch_timing.QUERY_LOG_POLL_ATTEMPTS


def test_lookup_rejects_unsafe_statement_id():
    adapter = _make("server")
    with pytest.raises(ValueError):
        adapter.fetch_platform_timings(_LogConnection(), ["x' OR '1'='1"])


def test_probe_cursor_assigns_query_id_to_probe_statements():
    adapter = _make("server")
    connection = _LogConnection(rows=[(1,)])
    probe = ch_timing._ProbeConnection(connection)
    cursor = probe.cursor()
    cursor.execute("SELECT 1")
    assert cursor.fetchall() == [(1,)]
    assert adapter.probe_statement_id(cursor) == connection.options[0]["query_id"]
    uuid.UUID(cursor.query_id)


def test_link_probe_collects_statement_ids_for_server():
    from benchbox.platforms.base.link_probe import probe_statement_overhead

    adapter = _make("server")
    connection = _LogConnection(rows=[(1,)])
    result = probe_statement_overhead(
        ch_timing._ProbeConnection(connection), statement_id_of=adapter.probe_statement_id
    )
    assert result["collection_status"] == "available"
    assert len(result["statement_ids"]) == 5
    assert set(result["statement_ids"]) <= {options["query_id"] for options in connection.options}


def test_probe_platform_timing_unwraps_probe_connection(monkeypatch):
    monkeypatch.setattr(ch_timing, "QUERY_LOG_POLL_INTERVAL_SECONDS", 0)
    adapter = _make("server")
    query_id = str(uuid.uuid4())
    connection = _LogConnection(log_rows=[_log_row(query_id, duration=3)])
    timing = adapter.capture_probe_platform_timing(ch_timing._ProbeConnection(connection), [query_id])
    assert timing["status"] == "valid"
    assert timing["platform_ms"] == 3.0


def test_cloud_client_sends_query_id_as_setting():
    from unittest.mock import MagicMock

    from benchbox.platforms.clickhouse.client import ClickHouseCloudClient

    client = ClickHouseCloudClient.__new__(ClickHouseCloudClient)
    client._client = MagicMock()
    client._client.query.return_value.result_set = [(1,)]
    assert client.execute("SELECT 1", query_id="abc") == [(1,)]
    client._client.query.assert_called_once_with("SELECT 1", settings={"query_id": "abc"})


def test_validation_digest_and_timing_lookup_run_outside_query_timer(monkeypatch):
    from benchbox.core.results.fetch_mode import EXCLUSION_DEPTH
    from benchbox.platforms.base import result_capture

    adapter = _make("server")
    depths = {}
    monkeypatch.setattr(
        result_capture,
        "apply_materialized_result_validation",
        lambda *args, **kwargs: depths.__setitem__("digest", EXCLUSION_DEPTH.get()),
    )

    def fetch(connection, ids):
        depths["lookup"] = EXCLUSION_DEPTH.get()
        return {}

    adapter.fetch_platform_timings = fetch
    adapter.execute_query(_LogConnection(), "SELECT 1", "q1", validate_row_count=False)
    assert depths["digest"] > 0
    assert depths["lookup"] > 0
