from __future__ import annotations

import io

import pytest

pa = pytest.importorskip("pyarrow")

from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW, FETCH_MODE_ROWS
from benchbox.core.results.result_digest import compute_result_digest
from benchbox.platforms.base.result_capture import materialized_result_validation

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TABLE = pa.table({"a": [1, 2, None], "b": ["x", None, "z"], "c": [1.5, 2.5, 3.5]})
ROWS = [(1, "x", 1.5), (2, None, 2.5), (None, "z", 3.5)]
DENSE = pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"], "c": [1.5, 2.5, 3.5]})
DENSE_ROWS = [(1, "x", 1.5), (2, "y", 2.5), (3, "z", 3.5)]


def _arrow_stream_bytes(table: pa.Table) -> bytes:
    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue()


class TestDataFusionFetchMode:
    def _adapter(self, tmp_path, mode=None):
        pytest.importorskip("datafusion")
        from benchbox.platforms.datafusion import DataFusionAdapter

        config = {"data_format": "parquet", "output_dir": str(tmp_path)}
        if mode is not None:
            config["fetch_mode"] = mode
        return DataFusionAdapter(**config)

    def test_arrow_is_the_default(self, tmp_path):
        assert self._adapter(tmp_path).fetch_mode == FETCH_MODE_ARROW

    def test_config_switches_back_to_rows(self, tmp_path):
        assert self._adapter(tmp_path, FETCH_MODE_ROWS).fetch_mode == FETCH_MODE_ROWS

    def test_from_config_forwards_the_switch(self, tmp_path):
        pytest.importorskip("datafusion")
        from benchbox.platforms.datafusion import DataFusionAdapter

        adapter = DataFusionAdapter.from_config(
            {"benchmark": "tpch", "scale_factor": 0.01, "output_dir": str(tmp_path), "fetch_mode": "rows"}
        )
        assert adapter.fetch_mode == FETCH_MODE_ROWS

    @pytest.mark.parametrize("mode", [FETCH_MODE_ROWS, FETCH_MODE_ARROW])
    def test_result_records_the_mode_actually_used(self, tmp_path, mode):
        adapter = self._adapter(tmp_path, mode)
        result = adapter.execute_query(adapter.create_connection(), "SELECT 1 AS a", "q", validate_row_count=False)
        assert result["fetch_mode"] == mode
        assert result["rows_returned"] == 1
        assert result["first_row"] == (1,)

    def test_empty_result_keeps_the_schema_in_arrow_mode(self, tmp_path):
        adapter = self._adapter(tmp_path, FETCH_MODE_ARROW)
        result = adapter.execute_query(
            adapter.create_connection(), "SELECT 1 AS a WHERE 1 = 0", "q", validate_row_count=False
        )
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 0
        assert result["first_row"] is None

    def test_all_22_tpch_queries_digest_identically(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        source = duckdb.connect()
        try:
            source.execute("LOAD tpch")
            source.execute("CALL dbgen(sf=0.01)")
        except duckdb.Error as exc:
            pytest.skip(f"DuckDB tpch extension unavailable: {exc}")
        tables = [row[0] for row in source.execute("SHOW TABLES").fetchall()]
        for table in tables:
            source.execute(f"COPY {table} TO '{tmp_path}/{table}.parquet' (FORMAT parquet)")
        queries = source.execute("SELECT query_nr, query FROM tpch_queries() ORDER BY query_nr").fetchall()
        assert len(queries) == 22

        outcomes: dict[str, dict[int, tuple]] = {}
        for mode in (FETCH_MODE_ROWS, FETCH_MODE_ARROW):
            adapter = self._adapter(tmp_path, mode)
            connection = adapter.create_connection()
            for table in tables:
                connection.register_parquet(table, f"{tmp_path}/{table}.parquet")
            outcomes[mode] = {}
            for number, sql in queries:
                seen: dict[str, list] = {}
                with materialized_result_validation(
                    lambda query_id, rows, seen=seen: seen.setdefault(query_id, list(rows))
                ):
                    result = adapter.execute_query(connection, sql, f"Q{number}", validate_row_count=False)
                assert result["status"] == "SUCCESS", number
                assert result["fetch_mode"] == mode
                outcomes[mode][number] = (result["rows_returned"], compute_result_digest(seen[f"Q{number}"]))
        assert outcomes[FETCH_MODE_ARROW] == outcomes[FETCH_MODE_ROWS]


class StubGizmoCursor:
    def __init__(self, table: pa.Table, description=((("a"),),)):
        self.description = description
        self._table = table

    def execute(self, query: str) -> None:
        pass

    def fetchall(self) -> list[tuple]:
        return list(zip(*(column.to_pylist() for column in self._table.columns)))

    def fetch_arrow_table(self) -> pa.Table:
        return self._table


class TestGizmoSQLFetchMode:
    def _adapter(self, mode=None):
        from benchbox.platforms.gizmosql import GizmoSQLAdapter

        config = {"host": "localhost", "password": "secret", "tls": False, "allow_insecure_connection": True}
        if mode is not None:
            config["fetch_mode"] = mode
        return GizmoSQLAdapter(**config)

    def test_arrow_is_the_default(self):
        assert self._adapter().fetch_mode == FETCH_MODE_ARROW

    def test_config_switches_back_to_rows(self):
        assert self._adapter(FETCH_MODE_ROWS).fetch_mode == FETCH_MODE_ROWS

    @pytest.mark.parametrize("mode", [FETCH_MODE_ROWS, FETCH_MODE_ARROW])
    def test_rows_are_identical_in_both_modes(self, mode, monkeypatch):
        monkeypatch.setenv("BENCHBOX_EMIT_RESULT_DIGEST", "1")
        result = self._adapter(mode).execute_query(StubGizmoCursor(TABLE), "SELECT 1", "q", validate_row_count=False)
        assert result["fetch_mode"] == mode
        assert result["rows_returned"] == 3
        assert result["first_row"] == ROWS[0]
        assert result["result_digest"] == compute_result_digest(ROWS)

    def test_statement_without_a_result_set_returns_no_rows(self):
        cursor = StubGizmoCursor(TABLE, description=None)
        result = self._adapter(FETCH_MODE_ARROW).execute_query(cursor, "SET x = 1", "q", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 0

    def test_arrow_fetch_of_a_statement_without_a_result_set_is_an_empty_table(self):
        from benchbox.platforms.gizmosql import _fetch_arrow_table

        assert _fetch_arrow_table(StubGizmoCursor(TABLE, description=None)).num_rows == 0


class StubChdbConnection:
    def __init__(self, table: pa.Table):
        self.table = table
        self.formats: list[str] = []

    def query(self, query: str, format: str = "CSV"):
        self.formats.append(format)
        payload = _arrow_stream_bytes(self.table)
        return type("Result", (), {"bytes": lambda self: payload})()


class TestClickHouseLocalFetchMode:
    def _client(self, table):
        from benchbox.platforms.clickhouse.client import ClickHouseLocalClient

        client = object.__new__(ClickHouseLocalClient)
        client._conn = StubChdbConnection(table)
        client._is_persistent = False
        return client

    def test_execute_arrow_returns_the_arrow_table_unchanged(self):
        assert self._client(TABLE).execute_arrow("SELECT 1").equals(TABLE)

    def test_execute_arrow_of_an_empty_payload_is_an_empty_table(self):
        client = self._client(TABLE)
        client._conn.query = lambda query, format="CSV": type("Result", (), {"bytes": lambda self: b""})()
        assert client.execute_arrow("SELECT 1").num_rows == 0

    def test_execute_still_returns_a_dataframe_result(self):
        result = self._client(TABLE).execute("SELECT 1")
        assert len(result) == 3

    def test_rows_stay_the_default(self):
        from benchbox.platforms.clickhouse import ClickHouseAdapter

        assert ClickHouseAdapter.default_fetch_mode == FETCH_MODE_ROWS

    @pytest.mark.parametrize("mode", [FETCH_MODE_ROWS, FETCH_MODE_ARROW])
    def test_workload_records_the_mode_and_returns_identical_rows(self, mode, monkeypatch):
        from benchbox.platforms.clickhouse import ClickHouseAdapter

        monkeypatch.setenv("BENCHBOX_EMIT_RESULT_DIGEST", "1")
        adapter = object.__new__(ClickHouseAdapter)
        adapter.fetch_mode = mode
        adapter.deployment_mode = "local"
        seen: dict[str, list] = {}
        with materialized_result_validation(lambda query_id, rows: seen.setdefault(query_id, list(rows))):
            result = adapter.execute_query(self._client(DENSE), "SELECT 1", "q", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert result["fetch_mode"] == mode
        assert result["rows_returned"] == 3
        assert result["first_row"] == DENSE_ROWS[0]
        assert seen["q"] == DENSE_ROWS
