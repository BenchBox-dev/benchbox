from __future__ import annotations

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW, FETCH_MODE_ROWS
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture
def digest_env(monkeypatch):
    monkeypatch.setenv("BENCHBOX_EMIT_RESULT_DIGEST", "1")


def _adapter(mode: str | None = None) -> DuckDBAdapter:
    config = {"database_path": ":memory:"}
    if mode is not None:
        config["fetch_mode"] = mode
    return DuckDBAdapter(**config)


class TestDigestParity:
    def test_all_22_tpch_queries_digest_identically(self, tpch_sf001_duckdb, digest_env):
        tpch_connection, query_map = tpch_sf001_duckdb
        queries = sorted(query_map.items())
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
