from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.results.result_digest import compute_result_digest
from benchbox.platforms.base.data_loading import DataSource
from benchbox.platforms.gizmosql import (
    GizmoSQLAdapter,
    GizmoSQLReadOnlyError,
    GizmoSQLSettingScopeError,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_gizmosql,
]

PINNED_SERVER_VERSION = "v1.39.0"
PASSWORD = "live-test-password"
USERNAME = "gizmosql"
TPCH_COUNTS = {
    "customer": 1500,
    "lineitem": 60175,
    "nation": 25,
    "orders": 15000,
    "part": 2000,
    "partsupp": 8000,
    "region": 5,
    "supplier": 100,
}
TPCDS_TABLE_COUNT = 24
TPCDS_REPRESENTATIVE = ("3", "7", "12", "19", "25", "36", "42", "52", "55", "65", "73", "96", "98")


def require_live() -> bool:
    return os.environ.get("BENCHBOX_REQUIRE_LIVE_GIZMOSQL") == "1"


def unavailable(reason: str) -> None:
    message = (
        f"GizmoSQL live tests need a gizmosql_server {PINNED_SERVER_VERSION} binary: {reason}. "
        "Allow the first-run download from GitHub releases or set GIZMOSQL_SERVER_BINARY to an offline binary."
    )
    if require_live():
        pytest.fail(message)
    pytest.skip(message)


def start_server(directory: Path, name: str, *, database: Path | None = None, extra_args: tuple[str, ...] = ()) -> Any:
    try:
        import gizmosql
    except ImportError as exc:
        unavailable(f"the gizmosql package is not installed ({exc})")
    log = (directory / f"{name}.log").open("wb")
    binary = os.environ.get("GIZMOSQL_SERVER_BINARY")
    try:
        server = gizmosql.Server(
            username=USERNAME,
            password=PASSWORD,
            version=PINNED_SERVER_VERSION,
            database_filename=database,
            extra_args=extra_args,
            binary=binary,
            startup_timeout=60,
            stdout=log,
            stderr=log,
        )
        server.start()
    except (gizmosql.ServerError, OSError) as exc:
        log.close()
        unavailable(str(exc))
    return server


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    directory = tmp_path_factory.mktemp("gizmosql-server")
    running = start_server(directory, "main", database=directory / "main.duckdb")
    yield running
    running.stop()


def make_adapter(server: Any, **overrides: Any) -> GizmoSQLAdapter:
    config: dict[str, Any] = {
        "host": server.host,
        "port": server.port,
        "username": USERNAME,
        "password": PASSWORD,
        "tls": False,
        "allow_insecure_connection": True,
    }
    config.update(overrides)
    return GizmoSQLAdapter(**config)


def setup_schema(adapter: GizmoSQLAdapter, benchmark: Any, data_dir: Path) -> tuple[Any, dict[str, int]]:
    adapter.benchmark = benchmark
    connection = adapter.create_connection()
    adapter.create_schema(benchmark, connection)
    stats, _, _ = adapter.load_data(benchmark, connection, data_dir)
    return connection, stats


def embedded_duckdb(benchmark: Any, data_dir: Path) -> Any:
    pytest.importorskip("duckdb")
    from benchbox.platforms.duckdb import DuckDBAdapter

    adapter = DuckDBAdapter(database_path=":memory:")
    connection = adapter.create_connection()
    adapter.create_schema(benchmark, connection)
    adapter.load_data(benchmark, connection, data_dir)
    return connection


def digest(rows: Any) -> str:
    normalized = [
        tuple(int(cell) if isinstance(cell, Decimal) and cell.as_tuple().exponent == 0 else cell for cell in row)
        for row in rows
    ]
    return compute_result_digest(normalized)


def count_rows(connection: Any, table: str) -> int:
    return connection.execute(f'SELECT count(*) FROM "{table}"').fetchall()[0][0]


@pytest.fixture(scope="module")
def tpch(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, Path]:
    from benchbox import TPCH

    data_dir = tmp_path_factory.mktemp("tpch-sf001")
    benchmark = TPCH(scale_factor=0.01, output_dir=data_dir, verbose=False)
    benchmark.generate_data()
    return benchmark, data_dir


@pytest.fixture(scope="module")
def tpcds(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, Path]:
    from benchbox import TPCDS

    data_dir = tmp_path_factory.mktemp("tpcds-sf001")
    benchmark = TPCDS(scale_factor=0.01, output_dir=data_dir, verbose=False)
    benchmark.generate_data()
    return benchmark, data_dir


class TestTpchLive:
    def test_load_twice_counts_and_result_parity(self, server, tpch):
        benchmark, data_dir = tpch
        adapter = make_adapter(server, schema="live_tpch")
        connection, stats = setup_schema(adapter, benchmark, data_dir)

        assert stats == TPCH_COUNTS
        again, _, _ = adapter.load_data(benchmark, connection, data_dir)
        assert again == TPCH_COUNTS
        assert {table: count_rows(connection, table) for table in TPCH_COUNTS} == TPCH_COUNTS

        rerun = make_adapter(server, schema="live_tpch")
        rerun.benchmark = benchmark
        rerun_connection = rerun.create_connection()
        assert rerun.database_was_reused is True
        stats_again, _, _ = rerun.load_data(benchmark, rerun_connection, data_dir)
        assert stats_again == TPCH_COUNTS

        queries = benchmark.get_queries(dialect="duckdb")
        assert len(queries) == 22
        reference = embedded_duckdb(benchmark, data_dir)
        for query_id, sql in queries.items():
            result = adapter.execute_query(connection, sql, str(query_id))
            assert result["status"] == "SUCCESS", f"Q{query_id}: {result.get('error')}"
            expected = reference.execute(sql).fetchall()
            actual = connection.execute(sql).fetchall()
            assert result["rows_returned"] == len(expected), f"Q{query_id} row count"
            assert digest(actual) == digest(expected), f"Q{query_id} digest"
        connection.close()
        rerun_connection.close()

    def test_partial_load_reloads_only_the_empty_table(self, server, tpch):
        benchmark, data_dir = tpch
        adapter = make_adapter(server, schema="live_tpch_partial")
        connection, _ = setup_schema(adapter, benchmark, data_dir)
        connection.execute('DELETE FROM "region"')
        connection.close()

        recover = make_adapter(server, schema="live_tpch_partial")
        recover.benchmark = benchmark
        connection = recover.create_connection()
        assert recover.database_was_reused is False
        recover.create_schema(benchmark, connection)
        stats, _, _ = recover.load_data(benchmark, connection, data_dir)

        assert stats == TPCH_COUNTS
        assert {table: count_rows(connection, table) for table in TPCH_COUNTS} == TPCH_COUNTS
        connection.close()

    def test_force_recreate_rebuilds_the_benchmark_tables(self, server, tpch):
        benchmark, data_dir = tpch
        make_adapter(server, schema="live_tpch_force").create_connection().close()
        first = make_adapter(server, schema="live_tpch_force")
        first_connection, _ = setup_schema(first, benchmark, data_dir)
        first_connection.close()

        forced = make_adapter(server, schema="live_tpch_force", force_recreate=True)
        forced.benchmark = benchmark
        connection = forced.create_connection()
        assert forced.database_was_reused is False
        forced.create_schema(benchmark, connection)
        assert count_rows(connection, "region") == 0
        stats, _, _ = forced.load_data(benchmark, connection, data_dir)

        assert stats == TPCH_COUNTS
        connection.close()

    def test_failed_load_leaves_the_table_unchanged(self, server, tpch, tmp_path):
        benchmark, data_dir = tpch
        adapter = make_adapter(server, schema="live_tpch_failure")
        adapter.benchmark = benchmark
        connection = adapter.create_connection()
        adapter.create_schema(benchmark, connection)
        broken = tmp_path / "region.tbl"
        broken.write_text("0|AFRICA|comment|\nnot-a-number|ASIA|comment|\n")
        loader = adapter._connect(autocommit=False)
        adapter._configure_session(loader)
        try:
            with pytest.raises(Exception, match="region"):
                adapter.load_table_files(loader, "region", [broken], benchmark, DataSource(source_type="t", tables={}))
        finally:
            loader.close()

        assert count_rows(connection, "region") == 0
        connection.close()


class TestTpcdsLive:
    def test_loads_all_tables_and_matches_embedded_duckdb(self, server, tpcds):
        benchmark, data_dir = tpcds
        adapter = make_adapter(server, schema="live_tpcds")
        connection, stats = setup_schema(adapter, benchmark, data_dir)
        reference = embedded_duckdb(benchmark, data_dir)

        assert len(set(stats) - {"dbgen_version"}) == TPCDS_TABLE_COUNT
        for table in stats:
            columns = [row[0] for row in reference.execute(f"SELECT name FROM pragma_table_info('{table}')").fetchall()]
            signature = "count(*), " + ", ".join(f'count("{column}")' for column in columns)
            expected = reference.execute(f'SELECT {signature} FROM "{table}"').fetchall()
            actual = connection.execute(f'SELECT {signature} FROM "{table}"').fetchall()
            assert actual == expected, f"{table} counts or NULL parsing differ"
            assert stats[table] == expected[0][0]
        connection.close()

    def test_all_99_queries_render_and_representative_set_matches(self, server, tpcds):
        benchmark, data_dir = tpcds
        queries = benchmark.get_queries(dialect="duckdb")
        assert len(queries) == 99
        assert all(isinstance(sql, str) and sql.strip() for sql in queries.values())

        adapter = make_adapter(server, schema="live_tpcds")
        adapter.benchmark = benchmark
        connection = adapter.create_connection()
        assert adapter.database_was_reused is True
        reference = embedded_duckdb(benchmark, data_dir)
        by_name = {str(key): sql for key, sql in queries.items()}
        for query_id in TPCDS_REPRESENTATIVE:
            sql = by_name[query_id]
            result = adapter.execute_query(connection, sql, query_id)
            assert result["status"] == "SUCCESS", f"Q{query_id}: {result.get('error')}"
            assert digest(connection.execute(sql).fetchall()) == digest(reference.execute(sql).fetchall()), (
                f"Q{query_id} digest"
            )
        connection.close()


class TestStreamsLive:
    def test_streams_do_not_share_cursors_transactions_or_session_state(self, server, tpch):
        benchmark, data_dir = tpch
        adapter = make_adapter(server, schema="live_tpch_streams", session_settings="enable_profiling=json")
        connection, _ = setup_schema(adapter, benchmark, data_dir)
        first = adapter.new_stream_connection(connection, benchmark_type="olap")
        second = adapter.new_stream_connection(connection, benchmark_type="olap")

        assert first.raw is not second.raw
        first.execute("BEGIN")
        first.execute("INSERT INTO region VALUES (99, 'ISOLATED', 'x')")
        assert count_rows(second, "region") == 5
        first.execute("ROLLBACK")
        first.execute("CREATE TEMP TABLE only_here AS SELECT 1 AS a")
        with pytest.raises(Exception, match="only_here"):
            second.execute("SELECT * FROM only_here").fetchall()
        first.execute("SET enable_profiling = 'no_output'")
        assert second.execute("SELECT current_setting('enable_profiling')").fetchall()[0][0] == "json"

        errors: list[BaseException] = []

        def work(stream: Any) -> None:
            try:
                for query_id, sql in list(benchmark.get_queries(dialect="duckdb").items())[:6]:
                    assert adapter.execute_query(stream, sql, str(query_id))["status"] == "SUCCESS"
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(stream,)) for stream in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        first.close()
        assert count_rows(second, "region") == 5
        second.close()
        connection.close()


class TestSessionIsolationLive:
    def test_session_settings_do_not_leak_to_other_sessions(self, server):
        adapter = make_adapter(server, session_settings="enable_profiling=json")
        mine = adapter.create_connection()
        other = make_adapter(server).create_connection()

        assert mine.execute("SELECT current_setting('enable_profiling')").fetchall()[0][0] == "json"
        assert other.execute("SELECT current_setting('enable_profiling')").fetchall()[0][0] != "json"
        assert adapter.effective_session_settings == {"enable_profiling": "json"}
        mine.close()
        other.close()

    def test_global_settings_are_refused_and_the_server_is_unchanged(self, server):
        probe = make_adapter(server).create_connection()
        before = probe.execute("SELECT current_setting('threads')").fetchall()[0][0]

        with pytest.raises(GizmoSQLSettingScopeError):
            make_adapter(server, session_settings="threads=1").create_connection()

        assert probe.execute("SELECT current_setting('threads')").fetchall()[0][0] == before
        probe.close()


class TestTimeoutLive:
    def test_timeout_cancels_the_query_and_the_session_survives(self, server):
        adapter = make_adapter(server, query_timeout=1)
        connection = adapter.create_connection()

        result = adapter.execute_query(connection, "SELECT sum(hash(i)) FROM range(100000000000) t(i)", "slow")

        assert result["status"] == "FAILED"
        assert "query_timeout" in result["error"]
        assert adapter.execute_query(connection, "SELECT 1", "fast")["status"] == "SUCCESS"
        connection.close()


class TestWideRowsLive:
    def test_wide_rows_load_below_the_flight_message_limit(self, server, tmp_path):
        adapter = make_adapter(server, schema="live_wide")
        connection = adapter.create_connection()
        connection.execute("CREATE TABLE wide (id BIGINT, payload VARCHAR)")
        path = tmp_path / "wide.tbl"
        payload = "w" * 300_000
        with path.open("w") as handle:
            for index in range(120):
                handle.write(f"{index}|{payload}|\n")
        loader = adapter._connect(autocommit=False)
        adapter._configure_session(loader)
        try:
            loaded = adapter.load_table_files(loader, "wide", [path], object(), DataSource(source_type="t", tables={}))
        finally:
            loader.close()

        assert loaded == 120
        assert count_rows(connection, "wide") == 120
        connection.close()


class TestReadOnlyLive:
    def test_query_only_runs_work_and_mutations_fail_before_sending(self, tpch, tmp_path):
        benchmark, data_dir = tpch
        database = tmp_path / "readonly.duckdb"
        writable = start_server(tmp_path, "writable", database=database)
        try:
            adapter = make_adapter(writable, schema="main")
            connection, stats = setup_schema(adapter, benchmark, data_dir)
            assert stats == TPCH_COUNTS
            connection.close()
        finally:
            writable.stop()

        read_only = start_server(tmp_path, "readonly", database=database, extra_args=("--readonly",))
        try:
            adapter = make_adapter(read_only, schema="main")
            adapter.benchmark = benchmark
            connection = adapter.create_connection()
            assert adapter.server_read_only is True
            assert adapter.database_was_reused is True
            sql = benchmark.get_queries(dialect="duckdb")[next(iter(benchmark.get_queries(dialect="duckdb")))]
            assert adapter.execute_query(connection, sql, "Q1")["status"] == "SUCCESS"
            with pytest.raises(GizmoSQLReadOnlyError):
                adapter.create_schema(benchmark, connection)
            with pytest.raises(GizmoSQLReadOnlyError):
                adapter.load_data(benchmark, connection, data_dir)
            with pytest.raises(GizmoSQLReadOnlyError):
                adapter.gather_statistics(connection, ["region"])
            with pytest.raises(GizmoSQLReadOnlyError):
                connection.execute("DELETE FROM region")
            assert count_rows(connection, "region") == 5
            connection.close()
        finally:
            read_only.stop()


class TestCliLive:
    def run_cli(self, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        code = "import sys; from benchbox.cli.app import main; sys.argv[0] = 'benchbox'; main()"
        return subprocess.run(
            [sys.executable, "-c", code, *args],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
            timeout=900,
            check=False,
        )

    def test_platform_commands_and_dry_run(self, tmp_path):
        env = {"BENCHBOX_OUTPUT_DIR": str(tmp_path)}

        listing = self.run_cli(["platforms", "list"], env)
        status = self.run_cli(["platforms", "status", "gizmosql"], env)
        deps = self.run_cli(["check-deps", "--platform", "gizmosql"], env)
        dry = self.run_cli(
            [
                "run",
                "--dry-run",
                str(tmp_path / "dry"),
                "--platform",
                "gizmosql",
                "--benchmark",
                "tpch",
                "--non-interactive",
            ],
            env,
        )

        assert "GizmoSQL" in listing.stdout
        assert status.returncode == 0 and "Unknown platform" not in status.stdout
        assert "gizmosql dependencies are installed" in deps.stdout
        assert dry.returncode == 0, dry.stdout + dry.stderr
        payload = json.loads(next((tmp_path / "dry").glob("tpch_gizmosql_2*.json")).read_text())
        assert payload["warnings"] == []

    def test_real_non_interactive_run(self, server, tmp_path):
        env = {
            "BENCHBOX_OUTPUT_DIR": str(tmp_path),
            "GIZMOSQL_USERNAME": USERNAME,
            "GIZMOSQL_PASSWORD": PASSWORD,
        }
        completed = self.run_cli(
            [
                "run",
                "--platform",
                "gizmosql",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--non-interactive",
                "--phases",
                "generate,load,power",
                "--platform-option",
                f"host={server.host}",
                "--platform-option",
                f"port={server.port}",
                "--platform-option",
                "tls=false",
                "--platform-option",
                "allow_insecure_connection=true",
                "--platform-option",
                "schema=live_cli",
            ],
            env,
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert PASSWORD not in completed.stdout + completed.stderr
        result_file = next((tmp_path / "results").glob("tpch_sf001_gizmosql_sql_*.json"))
        raw = result_file.read_text()
        assert PASSWORD not in raw
        payload = json.loads(raw)
        assert payload["summary"]["queries"]["failed"] == 0
        assert payload["summary"]["queries"]["passed"] >= 22
        assert payload["platform"]["name"] == "GizmoSQL"
