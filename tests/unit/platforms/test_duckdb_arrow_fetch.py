from __future__ import annotations

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW, FETCH_MODE_ROWS
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [pytest.mark.unit]

TYPE_MATRIX_SQL = """
SELECT
    1::TINYINT AS tiny, 2::BIGINT AS big, 3::HUGEINT AS huge, 4::UHUGEINT AS uhuge,
    1.5::DECIMAL(10,2) AS dec, 2.5::DOUBLE AS dbl, 'text' AS str, TRUE AS flag,
    DATE '2024-01-02' AS day, TIME '10:11:12.5' AS tod, TIMESTAMP '2024-01-02 03:04:05.123456' AS ts,
    INTERVAL '1 month 2 days 3 seconds 4 microseconds' AS span, UUID '11111111-2222-3333-4444-555555555555' AS id,
    MAP {'k': 1} AS kv, [1, 2] AS items, {'a': 1} AS rec, 'ab'::BLOB AS raw, NULL::INTEGER AS missing,
    TIMESTAMP_NS '2024-01-01 00:00:00' AS ts_ns
"""


@pytest.fixture
def digest_env(monkeypatch):
    monkeypatch.setenv("BENCHBOX_EMIT_RESULT_DIGEST", "1")


def _adapter(mode: str | None = None) -> DuckDBAdapter:
    config = {"database_path": ":memory:"}
    if mode is not None:
        config["fetch_mode"] = mode
    return DuckDBAdapter(**config)


@pytest.fixture(scope="module")
def tpch_connection():
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL tpch")
        connection.execute("LOAD tpch")
        connection.execute("CALL dbgen(sf=0.01)")
    except duckdb.Error as exc:
        pytest.skip(f"DuckDB tpch extension unavailable: {exc}")
    yield connection
    connection.close()


class TestFetchModeConfiguration:
    def test_arrow_is_the_default(self):
        assert _adapter().fetch_mode == FETCH_MODE_ARROW

    def test_config_switches_back_to_rows(self):
        assert _adapter(FETCH_MODE_ROWS).fetch_mode == FETCH_MODE_ROWS

    def test_invalid_mode_is_rejected(self):
        with pytest.raises(ValueError, match="fetch_mode must be one of"):
            _adapter("pandas")

    def test_from_config_forwards_the_switch(self):
        adapter = DuckDBAdapter.from_config(
            {"database_path": ":memory:", "benchmark": "tpch", "scale_factor": 0.01, "fetch_mode": "rows"}
        )
        assert adapter.fetch_mode == FETCH_MODE_ROWS


class TestDigestParity:
    @pytest.mark.parametrize("mode", [FETCH_MODE_ROWS, FETCH_MODE_ARROW])
    def test_result_records_the_mode_actually_used(self, mode, digest_env):
        connection = duckdb.connect()
        result = _adapter(mode).execute_query(connection, "SELECT 1 AS a", "q", validate_row_count=False)
        assert result["fetch_mode"] == mode
        assert result["rows_returned"] == 1
        assert result["first_row"] == (1,)

    def test_type_matrix_rows_are_identical(self, digest_env):
        connection = duckdb.connect()
        rows = _adapter(FETCH_MODE_ROWS).execute_query(connection, TYPE_MATRIX_SQL, "types", validate_row_count=False)
        arrow = _adapter(FETCH_MODE_ARROW).execute_query(connection, TYPE_MATRIX_SQL, "types", validate_row_count=False)
        assert arrow["fetch_mode"] == FETCH_MODE_ARROW
        assert arrow["first_row"] == rows["first_row"]
        assert arrow["result_digest"] == rows["result_digest"]

    def test_all_22_tpch_queries_digest_identically(self, tpch_connection, digest_env):
        queries = tpch_connection.execute("SELECT query_nr, query FROM tpch_queries() ORDER BY query_nr").fetchall()
        assert len(queries) == 22
        rows_adapter, arrow_adapter = _adapter(FETCH_MODE_ROWS), _adapter(FETCH_MODE_ARROW)
        for number, sql in queries:
            rows = rows_adapter.execute_query(tpch_connection, sql, f"Q{number}", validate_row_count=False)
            arrow = arrow_adapter.execute_query(tpch_connection, sql, f"Q{number}", validate_row_count=False)
            assert rows["status"] == arrow["status"] == "SUCCESS", number
            assert rows["fetch_mode"] == FETCH_MODE_ROWS
            assert arrow["fetch_mode"] == FETCH_MODE_ARROW
            assert arrow["rows_returned"] == rows["rows_returned"], number
            assert arrow["result_digest"] == rows["result_digest"], number

    def test_materialized_validation_sees_the_same_rows(self, tpch_connection):
        from benchbox.platforms.base.result_capture import materialized_result_validation

        seen: dict[str, list] = {}
        sql = "SELECT l_returnflag, SUM(l_quantity) FROM lineitem GROUP BY 1 ORDER BY 1"
        with materialized_result_validation(lambda query_id, rows: seen.setdefault(query_id, list(rows))):
            _adapter(FETCH_MODE_ROWS).execute_query(tpch_connection, sql, "rows", validate_row_count=False)
            _adapter(FETCH_MODE_ARROW).execute_query(tpch_connection, sql, "arrow", validate_row_count=False)
        assert seen["rows"] == seen["arrow"]
        assert len(seen["arrow"]) == 3

    def test_arrow_failure_is_reported_as_failed_query(self):
        connection = duckdb.connect()
        result = _adapter(FETCH_MODE_ARROW).execute_query(
            connection, "SELECT * FROM missing_table", "q", validate_row_count=False
        )
        assert result["status"] == "FAILED"
