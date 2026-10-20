# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any

import pytest

from benchbox.platforms.base.adapter import PlatformAdapter
from benchbox.platforms.base.connection_wrappers import (
    PlatformAdapterConnection,
    StreamConnectionCapability,
)
from benchbox.platforms.doris import DorisAdapter
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.platforms.postgresql import PostgreSQLAdapter
from benchbox.platforms.singlestore import SingleStoreAdapter
from benchbox.platforms.sqlite import SQLiteAdapter
from benchbox.utils.clock import elapsed_seconds, mono_time

from .platforms.conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


class _SQLiteServerStyleAdapter(PlatformAdapter):
    stream_connection_capability = StreamConnectionCapability.INDEPENDENT_CONNECTION

    def __init__(self, db_path: str, **config: Any) -> None:
        super().__init__(**config)
        self._db_path = db_path

    @staticmethod
    def add_cli_arguments(parser) -> None:
        pass

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(db_path=config["db_path"])

    def get_target_dialect(self) -> str | None:
        return "sqlite"

    def create_connection(self, **connection_config: Any) -> Any:
        return sqlite3.connect(self._db_path)

    def create_schema(self, benchmark, connection: Any) -> float:
        return 0.0

    def apply_platform_optimizations(self, platform_config, connection: Any) -> None:
        pass

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection: Any) -> None:
        pass

    def load_data(self, benchmark, connection: Any, data_dir):
        return {}, 0.0, None

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        pass

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        cursor = connection.execute(query)
        rows = cursor.fetchall()
        return {
            "query_id": query_id,
            "status": "SUCCESS",
            "execution_time_seconds": 0.0,
            "rows_returned": len(rows),
            "rows": rows,
        }

    def new_stream_connection(self, connection: Any) -> Any:
        return sqlite3.connect(self._db_path)


def _stream_wrapper(adapter: PlatformAdapter, shared_connection: Any) -> PlatformAdapterConnection:
    stream_connection = adapter.new_stream_connection(shared_connection)
    return PlatformAdapterConnection(stream_connection, adapter)


class TestIndependentConnectionCapabilityIsolatesSessions:
    def test_temp_table_does_not_leak_across_streams(self, tmp_path) -> None:
        db_path = str(tmp_path / "server_style.db")
        adapter = _SQLiteServerStyleAdapter(db_path=db_path)
        assert adapter.stream_connection_capability is StreamConnectionCapability.INDEPENDENT_CONNECTION

        shared_connection = adapter.create_connection()
        try:
            stream_a = _stream_wrapper(adapter, shared_connection)
            stream_b = _stream_wrapper(adapter, shared_connection)
            try:
                assert stream_a.connection is not stream_b.connection
                assert stream_a.connection is not shared_connection
                assert stream_b.connection is not shared_connection

                stream_a.execute("CREATE TEMP TABLE stream_local (value INTEGER)")
                stream_a.execute("INSERT INTO stream_local VALUES (42)")
                assert stream_a.execute("SELECT value FROM stream_local").fetchall() == [(42,)]

                with pytest.raises(sqlite3.OperationalError, match="no such table"):
                    stream_b.execute("SELECT value FROM stream_local")
            finally:
                stream_a.close()
                stream_b.close()
        finally:
            shared_connection.close()

    def test_undeclared_override_fails_fast(self) -> None:

        class _ForgotToOverride(_SQLiteServerStyleAdapter):
            new_stream_connection = PlatformAdapter.new_stream_connection

        adapter = _ForgotToOverride(db_path=":memory:")
        with pytest.raises(NotImplementedError, match="INDEPENDENT_CONNECTION"):
            adapter.new_stream_connection(object())


class TestSharedCursorCapabilityIsDuckDBDefault:
    def test_duckdb_does_not_override_capability(self) -> None:
        adapter = DuckDBAdapter()
        assert adapter.stream_connection_capability is StreamConnectionCapability.SHARED_CURSOR
        assert type(adapter).new_stream_connection is PlatformAdapter.new_stream_connection

    def test_streams_share_one_connection_no_new_connections_opened(self) -> None:
        adapter = DuckDBAdapter()
        shared_connection = adapter.create_connection()
        try:
            stream_a = _stream_wrapper(adapter, shared_connection)
            stream_b = _stream_wrapper(adapter, shared_connection)

            stream_a.execute("CREATE TABLE shared_state (value INTEGER)")
            stream_a.execute("INSERT INTO shared_state VALUES (7)")
            assert stream_b.execute("SELECT value FROM shared_state").fetchall() == [(7,)]

            stream_a.close()
            assert stream_b.execute("SELECT value FROM shared_state").fetchall() == [(7,)]
        finally:
            shared_connection.close()


class TestPostgreSQLIndependentConnectionIsolatesSessions:
    pytestmark = [
        pytest.mark.docker_integration,
        pytest.mark.live_integration,
        pytest.mark.live_postgresql,
        pytest.mark.slow,
    ]

    @pytest.fixture
    def postgresql_adapter(self):
        skip_unless_docker_service("localhost", 5432, platform="PostgreSQL")
        adapter = PostgreSQLAdapter(
            host="localhost",
            port=5432,
            username="benchbox",
            password="benchbox",
            database="benchbox_test",
        )
        adapter.skip_database_management = True
        return adapter

    def test_declares_independent_connection(self, postgresql_adapter) -> None:
        assert postgresql_adapter.stream_connection_capability is StreamConnectionCapability.INDEPENDENT_CONNECTION
        assert type(postgresql_adapter).new_stream_connection is not PlatformAdapter.new_stream_connection

    def test_session_local_set_does_not_leak_across_streams(self, postgresql_adapter) -> None:
        shared_connection = postgresql_adapter.create_connection()
        try:
            stream_a = _stream_wrapper(postgresql_adapter, shared_connection)
            stream_b = _stream_wrapper(postgresql_adapter, shared_connection)
            try:
                assert stream_a.connection is not stream_b.connection
                assert stream_a.connection is not shared_connection
                assert stream_b.connection is not shared_connection

                stream_a.connection.execute("SET application_name = 'stream_a_marker'")
                own_name = stream_a.connection.execute("SHOW application_name").fetchone()[0]
                assert own_name == "stream_a_marker"

                other_name = stream_b.connection.execute("SHOW application_name").fetchone()[0]
                assert other_name != "stream_a_marker"
            finally:
                stream_a.close()
                stream_b.close()
        finally:
            shared_connection.close()

    def test_concurrent_statements_do_not_serialize_on_one_connection(self, postgresql_adapter) -> None:
        shared_connection = postgresql_adapter.create_connection()
        try:
            stream_a = _stream_wrapper(postgresql_adapter, shared_connection)
            stream_b = _stream_wrapper(postgresql_adapter, shared_connection)
            try:
                results: dict[str, float] = {}
                errors: dict[str, BaseException] = {}

                def run(name: str, wrapper: PlatformAdapterConnection) -> None:
                    start = mono_time()
                    try:
                        wrapper.connection.execute("SELECT pg_sleep(1)")
                        results[name] = elapsed_seconds(start)
                    except BaseException as exc:
                        errors[name] = exc

                thread_a = threading.Thread(target=run, args=("a", stream_a))
                thread_b = threading.Thread(target=run, args=("b", stream_b))
                overall_start = mono_time()
                thread_a.start()
                thread_b.start()
                thread_a.join(timeout=15)
                thread_b.join(timeout=15)
                overall_duration = elapsed_seconds(overall_start)

                assert not errors, f"concurrent pg_sleep() on independent connections raised: {errors}"
                assert "a" in results and "b" in results, "one or both streams did not complete in time"
                assert overall_duration < 1.8, (
                    f"streams appear serialized on one connection: {overall_duration:.2f}s for two 1s sleeps"
                )
            finally:
                stream_a.close()
                stream_b.close()
        finally:
            shared_connection.close()

    def test_stream_session_replays_setup_tuning(self, postgresql_adapter) -> None:
        postgresql_adapter.tuning_enabled = True
        shared_connection = postgresql_adapter.create_connection()
        try:
            stream_connection = postgresql_adapter.new_stream_connection(shared_connection, benchmark_type="olap")
            stream = PlatformAdapterConnection(stream_connection, postgresql_adapter)
            try:
                work_mem = stream.connection.execute("SHOW work_mem").fetchone()[0]
                assert work_mem == "256MB", f"stream work_mem not replayed: {work_mem!r}"
                page_cost = stream.connection.execute("SHOW random_page_cost").fetchone()[0]
                assert page_cost == "1.1", f"stream missing OLAP tuning replay: random_page_cost={page_cost!r}"
            finally:
                stream.close()
        finally:
            shared_connection.close()

    def test_baseline_stream_session_keeps_server_planner_defaults(self, postgresql_adapter) -> None:
        assert postgresql_adapter.tuning_enabled is False
        shared_connection = postgresql_adapter.create_connection()
        try:
            stream_connection = postgresql_adapter.new_stream_connection(shared_connection, benchmark_type="olap")
            stream = PlatformAdapterConnection(stream_connection, postgresql_adapter)
            try:
                work_mem = stream.connection.execute("SHOW work_mem").fetchone()[0]
                assert work_mem == "256MB", f"stream work_mem not replayed: {work_mem!r}"
                page_cost = stream.connection.execute("SHOW random_page_cost").fetchone()[0]
                server_default = shared_connection.execute("SHOW random_page_cost").fetchone()[0]
                assert page_cost == server_default, (
                    f"baseline stream changed planner settings: random_page_cost={page_cost!r} vs {server_default!r}"
                )
            finally:
                stream.close()
        finally:
            shared_connection.close()


class TestSQLiteIndependentStreamConnections:
    def test_concurrent_read_streams_run_in_isolated_sessions_and_close_cleanly(self, tmp_path) -> None:
        adapter = SQLiteAdapter(database_path=str(tmp_path / "streams.db"))
        assert adapter.stream_connection_capability is StreamConnectionCapability.INDEPENDENT_CONNECTION

        shared_connection = adapter.create_connection()
        try:
            setup = _stream_wrapper(adapter, shared_connection)
            try:
                setup.execute("CREATE TABLE probe (value INTEGER)")
                for value in range(10):
                    setup.execute(f"INSERT INTO probe VALUES ({value})")
                setup.commit()
            finally:
                setup.close()

            barrier = threading.Barrier(2)
            errors: dict[str, BaseException] = {}
            counts: dict[str, list[int]] = {}

            def run(name: str) -> None:
                try:
                    wrapper = _stream_wrapper(adapter, shared_connection)
                    try:
                        barrier.wait(timeout=30)
                        row_counts = []
                        for _ in range(5):
                            row = wrapper.execute("SELECT COUNT(*) FROM probe").fetchone()
                            row_counts.append(row[0])
                        counts[name] = row_counts
                    finally:
                        wrapper.close()
                except BaseException as exc:
                    errors[name] = exc

            threads = [threading.Thread(target=run, args=(name,)) for name in ("a", "b")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)

            assert not errors, f"concurrent independent read streams raised: {errors}"
            assert counts == {"a": [10] * 5, "b": [10] * 5}, f"independent streams mismeasured: {counts}"
            assert shared_connection.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 10
        finally:
            shared_connection.close()

    def test_identical_concurrent_reads_never_see_another_streams_state(self, tmp_path) -> None:
        adapter = SQLiteAdapter(database_path=str(tmp_path / "streams.db"))
        shared_connection = adapter.create_connection()
        try:
            setup = _stream_wrapper(adapter, shared_connection)
            try:
                setup.execute("CREATE TABLE probe (value INTEGER)")
                for value in range(10):
                    setup.execute(f"INSERT INTO probe VALUES ({value})")
                setup.commit()
            finally:
                setup.close()

            barrier = threading.Barrier(4)
            lock = threading.Lock()
            errors: dict[str, BaseException] = {}
            wrong: list[Any] = []

            def run(name: str) -> None:
                try:
                    barrier.wait(timeout=30)
                    for _ in range(500):
                        wrapper = _stream_wrapper(adapter, shared_connection)
                        try:
                            rows = wrapper.execute("SELECT COUNT(*) FROM probe").fetchall()
                        finally:
                            wrapper.close()
                        if rows != [(10,)]:
                            with lock:
                                wrong.append(rows)
                except BaseException as exc:
                    with lock:
                        errors[name] = exc

            threads = [threading.Thread(target=run, args=(f"stream-{index}",)) for index in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
            assert not any(thread.is_alive() for thread in threads), "independent read streams hung"

            assert not errors, f"concurrent independent read streams raised: {errors}"
            assert not wrong, f"{len(wrong)} independent reads saw another stream's state: {wrong[:3]}"
        finally:
            shared_connection.close()


class TestMySQLWireIndependentConnections:
    def test_declares_independent_with_mixin_override(self) -> None:
        pytest.importorskip("pymysql")
        for adapter_cls in (DorisAdapter, SingleStoreAdapter):
            assert adapter_cls.stream_connection_capability is StreamConnectionCapability.INDEPENDENT_CONNECTION, (
                adapter_cls.__name__
            )
            assert adapter_cls.new_stream_connection is not PlatformAdapter.new_stream_connection, adapter_cls.__name__

    @pytest.fixture
    def doris_adapter(self):
        pymysql = pytest.importorskip("pymysql")
        port = int(os.getenv("DORIS_HOST_PORT", "19031"))
        skip_unless_docker_service("localhost", port, platform="Doris")
        try:
            conn = pymysql.connect(host="localhost", port=port, user="root", password="", autocommit=True)
            try:
                conn.cursor().execute("CREATE DATABASE IF NOT EXISTS benchbox_test")
            finally:
                conn.close()
        except Exception:
            pytest.skip(f"Doris not reachable at localhost:{port}")
        adapter = DorisAdapter(
            host="localhost",
            port=port,
            username="root",
            password="",
            database="benchbox_test",
        )
        adapter.skip_database_management = True
        return adapter

    @pytest.fixture
    def singlestore_adapter(self):
        singlestoredb = pytest.importorskip("singlestoredb")
        port = int(os.getenv("SINGLESTORE_HOST_PORT", "13306"))
        skip_unless_docker_service("localhost", port, platform="SingleStore")
        password = os.getenv("SINGLESTORE_PASSWORD", "benchbox")
        try:
            conn = singlestoredb.connect(host="localhost", port=port, user="root", password=password)
            try:
                conn.cursor().execute("CREATE DATABASE IF NOT EXISTS benchbox_test")
            finally:
                conn.close()
        except Exception:
            pytest.skip(f"SingleStore not reachable at localhost:{port}")
        adapter = SingleStoreAdapter(
            host="localhost",
            port=port,
            username="root",
            password=password,
            database="benchbox_test",
        )
        adapter.skip_database_management = True
        return adapter

    @pytest.mark.docker_integration
    @pytest.mark.live_integration
    @pytest.mark.live_doris
    @pytest.mark.slow
    def test_doris_streams_are_independent_sessions(self, doris_adapter) -> None:
        self._prove_wire_sessions_are_independent(doris_adapter, dialect="doris")

    @pytest.mark.docker_integration
    @pytest.mark.live_integration
    @pytest.mark.slow
    def test_singlestore_streams_are_independent_sessions(self, singlestore_adapter) -> None:
        self._prove_wire_sessions_are_independent(singlestore_adapter, dialect="singlestore")

    @staticmethod
    def _prove_wire_sessions_are_independent(adapter: PlatformAdapter, *, dialect: str) -> None:
        shared_connection = adapter.create_connection()
        try:
            raw_a = adapter.new_stream_connection(shared_connection, benchmark_type="olap")
            raw_b = adapter.new_stream_connection(shared_connection, benchmark_type="olap")
            try:
                cursor_a = raw_a.cursor()
                cursor_b = raw_b.cursor()
                try:
                    cursor_a.execute("SELECT CONNECTION_ID()")
                    cursor_b.execute("SELECT CONNECTION_ID()")
                    id_a = cursor_a.fetchone()[0]
                    id_b = cursor_b.fetchone()[0]
                    assert id_a != id_b, "streams share one server session"

                    cursor_a.execute("CREATE TEMPORARY TABLE stream_local (value INT)")
                    cursor_a.execute("INSERT INTO stream_local VALUES (42)")
                    sibling_error: Exception | None = None
                    try:
                        cursor_b.execute("SELECT COUNT(*) FROM stream_local")
                        cursor_b.fetchall()
                    except Exception as exc:
                        sibling_error = exc
                    assert sibling_error is not None, f"{dialect} sibling stream saw another session's TEMPORARY table"
                    assert "stream_local" in str(sibling_error).lower()
                finally:
                    cursor_a.close()
                    cursor_b.close()

                if dialect == "doris":
                    check = raw_a.cursor()
                    try:
                        check.execute("SHOW VARIABLES LIKE 'enable_sql_cache'")
                        row = check.fetchone()
                        assert row is not None and str(row[1]).lower() in ("false", "0", "off"), (
                            f"doris stream did not replay cache tuning: {row!r}"
                        )
                    finally:
                        check.close()
            finally:
                raw_a.close()
                raw_b.close()
            cursor = shared_connection.cursor()
            try:
                cursor.execute("SELECT 1")
                assert cursor.fetchone()[0] == 1
            finally:
                cursor.close()
        finally:
            shared_connection.close()
