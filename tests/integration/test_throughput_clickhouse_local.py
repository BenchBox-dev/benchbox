from __future__ import annotations

import threading
from pathlib import Path

import pytest

from benchbox.platforms.base.connection_wrappers import (
    StreamConnectionCapability,
    open_stream_connection,
    require_throughput_stream_capability,
)
from benchbox.platforms.clickhouse.client import ClickHouseLocalClient
from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter
from tests.utilities.optional_engines import require_chdb

pytestmark = [pytest.mark.integration, pytest.mark.medium]

require_chdb()

STREAMS = 4


@pytest.fixture()
def base_client(tmp_path: Path):
    client = ClickHouseLocalClient(db_path=str(tmp_path / "streams.chdb"))
    client.execute("CREATE TABLE t (a Int64) ENGINE = MergeTree ORDER BY tuple()")
    client.execute("INSERT INTO t SELECT number FROM numbers(1000)")
    try:
        yield client
    finally:
        client.close()


def _value(client, sql: str):
    return client.execute(sql).fetchone()[0]


def test_stream_sessions_see_the_data_and_apply_the_refresh_settings(base_client) -> None:
    adapter = ClickHouseLocalAdapter()
    stream = open_stream_connection(adapter, base_client, "tpcds")
    try:
        assert stream is not base_client
        assert _value(stream, "SELECT count() FROM t") == 1000
        assert int(_value(stream, "SELECT getSetting('join_use_nulls')")) == 1
        assert int(_value(stream, "SELECT getSetting('mutations_sync')")) == 2
        assert int(_value(stream, "SELECT getSetting('lightweight_deletes_sync')")) == 2
        assert int(_value(stream, "SELECT getSetting('data_type_default_nullable')")) == 1
    finally:
        stream.close()


def test_stream_session_settings_do_not_leak_between_sessions(base_client) -> None:
    adapter = ClickHouseLocalAdapter()
    first = adapter.new_stream_connection(base_client, benchmark_type="tpch")
    second = adapter.new_stream_connection(base_client, benchmark_type="tpch")
    try:
        first.execute("SET max_threads = 1")
        assert int(_value(first, "SELECT getSetting('max_threads')")) == 1
        assert int(_value(second, "SELECT getSetting('max_threads')")) != 1
    finally:
        first.close()
        second.close()


def test_closing_one_stream_leaves_the_others_and_the_base_session_usable(base_client) -> None:
    adapter = ClickHouseLocalAdapter()
    first = adapter.new_stream_connection(base_client, benchmark_type="tpch")
    second = adapter.new_stream_connection(base_client, benchmark_type="tpch")
    first.close()
    try:
        assert _value(second, "SELECT count() FROM t") == 1000
        assert _value(base_client, "SELECT count() FROM t") == 1000
    finally:
        second.close()


def test_concurrent_streams_run_queries_through_the_adapter(base_client) -> None:
    adapter = ClickHouseLocalAdapter()
    require_throughput_stream_capability(adapter, connection=base_client)
    outcomes: dict[int, dict] = {}
    errors: list[BaseException] = []

    def run(stream_id: int) -> None:
        stream = adapter.new_stream_connection(base_client, benchmark_type="tpch")
        try:
            outcomes[stream_id] = adapter.execute_query(
                stream, "SELECT count() FROM t WHERE a >= 0", f"Q{stream_id}", validate_row_count=False
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            stream.close()

    threads = [threading.Thread(target=run, args=(stream_id,)) for stream_id in range(STREAMS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == []
    assert sorted(outcomes) == list(range(STREAMS))
    assert all(outcome["status"] == "SUCCESS" for outcome in outcomes.values())


def test_in_memory_database_is_refused_for_streams() -> None:
    adapter = ClickHouseLocalAdapter()
    client = ClickHouseLocalClient(db_path=None)
    try:
        with pytest.raises(RuntimeError, match="persistent database path"):
            adapter.new_stream_connection(client, benchmark_type="tpch")
        with pytest.raises(RuntimeError, match="persistent database path"):
            require_throughput_stream_capability(adapter, connection=client)
    finally:
        client.close()


def test_capability_is_independent_connection() -> None:
    from benchbox.platforms.base.connection_wrappers import resolve_stream_connection_capability

    assert resolve_stream_connection_capability(ClickHouseLocalAdapter) == (
        StreamConnectionCapability.INDEPENDENT_CONNECTION,
        True,
    )
