from unittest.mock import MagicMock

import pytest

from benchbox.platforms.motherduck import MotherDuckAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def adapter():
    return MotherDuckAdapter(token="test-token", capture_plans=True, analyze_plans=True)


def _mock_conn():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [("analyzed_plan", "{}")]
    return conn


class TestMotherDuckDMLPlanGuard:
    @pytest.mark.parametrize(
        "dml",
        [
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET x = 1",
            "DELETE FROM t WHERE id = 1",
            "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET x = 1",
            "COPY t FROM 'data.csv'",
        ],
    )
    def test_dml_query_does_not_use_analyze(self, adapter, dml):
        conn = _mock_conn()

        adapter.get_query_plan(conn, dml)

        called_sql = conn.execute.call_args[0][0].upper()
        assert "ANALYZE" not in called_sql, f"DML must not run EXPLAIN ANALYZE: {called_sql}"
        assert "FORMAT JSON" in called_sql

    @pytest.mark.parametrize(
        "write_ddl",
        [
            "CREATE TABLE t2 AS SELECT * FROM t",
            "CREATE OR REPLACE TABLE t2 AS SELECT * FROM t",
            "CREATE MATERIALIZED VIEW mv AS SELECT * FROM t",
            "SELECT * INTO t2 FROM t",
        ],
    )
    def test_ctas_query_does_not_use_analyze(self, adapter, write_ddl):

        conn = _mock_conn()

        adapter.get_query_plan(conn, write_ddl)

        called_sql = conn.execute.call_args[0][0].upper()
        assert "ANALYZE" not in called_sql, f"Write DDL must not run EXPLAIN ANALYZE: {called_sql}"
        assert "FORMAT JSON" in called_sql

    def test_plain_create_table_still_uses_analyze(self, adapter):
        conn = _mock_conn()

        adapter.get_query_plan(conn, "CREATE TABLE t (id INT)")

        called_sql = conn.execute.call_args[0][0].upper()
        assert "ANALYZE" in called_sql, "Column DDL writes no rows; ANALYZE must be kept"

    def test_select_query_still_uses_analyze(self, adapter):
        conn = _mock_conn()

        adapter.get_query_plan(conn, "SELECT * FROM t WHERE id = 1")

        called_sql = conn.execute.call_args[0][0].upper()
        assert "ANALYZE" in called_sql, "Non-DML SELECT must still use EXPLAIN ANALYZE"
        assert "FORMAT JSON" in called_sql


class TestMotherDuckStrictPlanCapture:
    @staticmethod
    def _break_plan_capture(adapter, monkeypatch):
        def boom(connection, query):
            raise RuntimeError("EXPLAIN blew up")

        monkeypatch.setattr(adapter, "get_query_plan", boom)

    def test_strict_capture_failure_propagates(self, monkeypatch):
        from benchbox.core.errors import PlanCaptureError

        adapter = MotherDuckAdapter(token="test-token", capture_plans=True, strict_plan_capture=True)
        self._break_plan_capture(adapter, monkeypatch)
        conn = _mock_conn()

        with pytest.raises(PlanCaptureError):
            adapter.execute_query(conn, "SELECT * FROM t", "q_strict")

    def test_non_strict_capture_failure_is_silent(self, monkeypatch):
        adapter = MotherDuckAdapter(token="test-token", capture_plans=True, strict_plan_capture=False)
        self._break_plan_capture(adapter, monkeypatch)
        conn = _mock_conn()

        result = adapter.execute_query(conn, "SELECT * FROM t", "q_nonstrict")

        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result

    def test_strict_mode_does_not_mask_real_query_failure(self):

        adapter = MotherDuckAdapter(token="test-token", capture_plans=True, strict_plan_capture=True)
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("no such table: t")

        result = adapter.execute_query(conn, "SELECT * FROM t", "q_bad")

        assert result["status"] == "FAILED"
        assert "no such table" in result["error"]


