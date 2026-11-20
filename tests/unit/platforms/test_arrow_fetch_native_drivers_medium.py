from __future__ import annotations

import pytest

from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW, FETCH_MODE_ROWS
from benchbox.core.results.result_digest import compute_result_digest
from benchbox.platforms.base.result_capture import materialized_result_validation

pytestmark = [pytest.mark.unit, pytest.mark.medium]


class TestDataFusionFetchMode:
    def _adapter(self, tmp_path, mode=None):
        pytest.importorskip("datafusion")
        from benchbox.platforms.datafusion import DataFusionAdapter

        config = {"data_format": "parquet", "output_dir": str(tmp_path)}
        if mode is not None:
            config["fetch_mode"] = mode
        return DataFusionAdapter(**config)

    def test_all_22_tpch_queries_digest_identically(self, tmp_path, tpch_sf001_duckdb):
        source, query_map = tpch_sf001_duckdb
        tables = [row[0] for row in source.execute("SHOW TABLES").fetchall()]
        assert len(tables) == 8
        for table in tables:
            source.execute(f"COPY {table} TO '{tmp_path}/{table}.parquet' (FORMAT parquet)")
        queries = sorted(query_map.items())
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
