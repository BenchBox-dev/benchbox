import sqlite3

import pytest

from benchbox.platforms.sqlite import SQLiteAdapter, _format_sqlite_query_plan

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def adapter():
    return SQLiteAdapter(capture_plans=True)


@pytest.fixture()
def adapter_no_capture():
    return SQLiteAdapter(capture_plans=False)


@pytest.fixture()
def conn():

    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL)")
    c.execute("INSERT INTO orders VALUES (1, 99.9), (2, 49.9)")
    c.execute("CREATE TABLE lineitem (order_id INTEGER, qty INTEGER)")
    c.execute("INSERT INTO lineitem VALUES (1, 3), (2, 1)")
    yield c
    c.close()


class TestFormatSQLiteQueryPlan:
    def test_single_root_node(self):
        rows = [(2, 0, 0, "SCAN orders")]
        text = _format_sqlite_query_plan(rows)
        assert text is not None
        assert "QUERY PLAN" in text
        assert "2|0|SCAN orders" in text

    def test_multiple_root_nodes(self):
        rows = [(2, 0, 0, "SCAN orders"), (4, 0, 0, "SCAN lineitem")]
        text = _format_sqlite_query_plan(rows)
        assert "2|0|SCAN orders" in text
        assert "4|0|SCAN lineitem" in text

    def test_parent_child_nesting(self):
        rows = [(2, 0, 0, "SCAN customer"), (4, 2, 0, "SEARCH lineitem USING INDEX")]
        text = _format_sqlite_query_plan(rows)
        assert "2|0|SCAN customer" in text
        assert "4|2|SEARCH lineitem USING INDEX" in text

    def test_empty_rows_returns_none(self):
        assert _format_sqlite_query_plan([]) is None


class TestSQLitePlanCapture:
    def test_get_query_plan_parser_returns_instance(self, adapter):
        from benchbox.core.query_plans.parsers.sqlite import SQLiteQueryPlanParser

        parser = adapter.get_query_plan_parser()
        assert parser is not None
        assert isinstance(parser, SQLiteQueryPlanParser)

    def test_get_query_plan_returns_text(self, adapter, conn):
        result = adapter.get_query_plan(conn, "SELECT * FROM orders")
        assert result is not None
        assert "QUERY PLAN" in result or "SCAN" in result

    def test_get_query_plan_returns_none_on_error(self, adapter):
        from unittest.mock import MagicMock

        bad_conn = MagicMock()
        bad_conn.cursor.return_value.execute.side_effect = Exception("error")
        assert adapter.get_query_plan(bad_conn, "SELECT 1") is None

    def test_execute_query_with_capture_adds_plan_fields(self, adapter, conn):
        result = adapter.execute_query(
            connection=conn,
            query="SELECT * FROM orders",
            query_id="sq1",
            validate_row_count=False,
        )
        assert result["status"] == "SUCCESS"

        if "query_plan" in result and result["query_plan"] is not None:
            assert result["plan_fingerprint"] is not None
            assert result["plan_capture_time_ms"] >= 0

    def test_execute_query_without_capture_has_no_plan(self, adapter_no_capture, conn):
        result = adapter_no_capture.execute_query(
            connection=conn,
            query="SELECT * FROM orders",
            query_id="sq2",
            validate_row_count=False,
        )
        assert "query_plan" not in result or result.get("query_plan") is None

    def test_execute_query_capture_does_not_affect_row_count(self, adapter, conn):
        result = adapter.execute_query(
            connection=conn,
            query="SELECT * FROM orders",
            query_id="sq3",
            validate_row_count=False,
        )
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2

    def test_execute_query_failed_status_skips_capture(self, adapter, conn, monkeypatch):
        capture_called = []
        monkeypatch.setattr(adapter, "capture_query_plan", lambda *a, **k: capture_called.append(True) or (None, 0.0))
        monkeypatch.setattr(
            adapter,
            "_build_query_result_with_validation",
            lambda **kw: {"query_id": "sq_fail", "status": "FAILED", "rows_returned": 0},
        )

        adapter.execute_query(
            connection=conn, query="SELECT * FROM orders", query_id="sq_fail", validate_row_count=False
        )

        assert not capture_called, "capture_query_plan must not be called on FAILED results"

    def test_execute_query_calls_display_when_not_capturing(self, adapter_no_capture, conn, monkeypatch):
        display_calls = []
        monkeypatch.setattr(
            adapter_no_capture,
            "display_query_plan_if_enabled",
            lambda *a, **k: display_calls.append(True),
        )

        adapter_no_capture.execute_query(
            connection=conn, query="SELECT * FROM orders", query_id="sq_disp", validate_row_count=False
        )

        assert len(display_calls) == 1, "display_query_plan_if_enabled must be called exactly once"


class TestSQLiteFingerprintIntegration:
    @pytest.fixture()
    def mt_conn(self):

        c = sqlite3.connect(":memory:", check_same_thread=False)
        c.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL)")
        c.executemany("INSERT INTO orders VALUES (?, ?)", [(i, float(i)) for i in range(1, 400)])
        yield c
        c.close()

    def test_fingerprint_is_non_none_for_real_select(self, adapter, mt_conn):
        plan, capture_ms = adapter.capture_query_plan(mt_conn, "SELECT * FROM orders WHERE id = 1", "fp_nn")
        assert plan is not None, "Real SQLite SELECT should produce a parsed plan"
        assert plan.plan_fingerprint is not None
        assert capture_ms >= 0

    def test_fingerprint_is_idempotent_across_calls(self, adapter, mt_conn):
        query = "SELECT * FROM orders WHERE id = 1"
        plan1, _ = adapter.capture_query_plan(mt_conn, query, "fp_a")
        plan2, _ = adapter.capture_query_plan(mt_conn, query, "fp_b")
        assert plan1 is not None and plan2 is not None

        assert plan1.plan_fingerprint == plan2.plan_fingerprint

    def test_fingerprint_stable_across_index_addition(self, adapter, mt_conn):

        query = "SELECT * FROM orders WHERE amount = 250.0"

        before, _ = adapter.capture_query_plan(mt_conn, query, "fp_before_idx")
        mt_conn.execute("CREATE INDEX idx_orders_amount ON orders(amount)")
        after, _ = adapter.capture_query_plan(mt_conn, query, "fp_after_idx")

        assert before is not None and after is not None
        assert before.plan_fingerprint == after.plan_fingerprint, (
            "Adding an index is a physical change; the logical fingerprint must be stable"
        )

    def test_fingerprint_differs_for_logically_distinct_plans(self, adapter, mt_conn):

        scan_plan, _ = adapter.capture_query_plan(mt_conn, "SELECT * FROM orders", "fp_scan")
        join_plan, _ = adapter.capture_query_plan(
            mt_conn, "SELECT o.id FROM orders o JOIN orders o2 ON o.id = o2.id", "fp_join"
        )

        assert scan_plan is not None and join_plan is not None
        assert scan_plan.plan_fingerprint != join_plan.plan_fingerprint, "A join must change the logical plan shape"

    def test_explain_text_is_non_empty_with_expected_keywords(self, adapter, mt_conn):
        text = adapter.get_query_plan(mt_conn, "SELECT * FROM orders WHERE id = 1")
        assert text, "EXPLAIN QUERY PLAN text should be non-empty"
        assert any(kw in text for kw in ("QUERY PLAN", "SCAN", "SEARCH"))


class TestSQLiteParserModernFormat:
    def test_infer_scan_without_table_keyword(self):
        from benchbox.core.query_plans.parsers.sqlite import SQLiteQueryPlanParser

        parser = SQLiteQueryPlanParser()
        op_type = parser._infer_operator_type("SCAN orders")
        assert op_type == "SCAN"

    def test_infer_search_without_table_keyword(self):
        from benchbox.core.query_plans.parsers.sqlite import SQLiteQueryPlanParser

        parser = SQLiteQueryPlanParser()
        op_type = parser._infer_operator_type("SEARCH orders USING INDEX idx_customer (c_custkey=?)")
        assert op_type == "INDEX_SCAN"

    def test_extract_details_without_table_keyword(self):
        from benchbox.core.query_plans.parsers.sqlite import SQLiteQueryPlanParser

        parser = SQLiteQueryPlanParser()
        details = parser._extract_details("SCAN orders")
        assert details.get("table_name") == "orders"

    def test_parse_modern_explain_output(self, conn):

        from benchbox.core.query_plans.parsers.sqlite import SQLiteQueryPlanParser

        cursor = conn.cursor()
        cursor.execute("EXPLAIN QUERY PLAN SELECT * FROM orders WHERE id = 1")
        rows = cursor.fetchall()
        cursor.close()

        from benchbox.platforms.sqlite import _format_sqlite_query_plan

        plan_text = _format_sqlite_query_plan(rows)

        if plan_text:
            parser = SQLiteQueryPlanParser()
            dag = parser.parse_explain_output("test_q", plan_text)
            assert dag is not None
            assert dag.logical_root is not None


class TestSQLiteStrictPlanCapture:
    @staticmethod
    def _break_plan_capture(adapter, monkeypatch):
        def boom(connection, query, explain_options=None):
            raise RuntimeError("EXPLAIN blew up")

        monkeypatch.setattr(adapter, "get_query_plan", boom)

    def test_strict_capture_failure_propagates(self, conn, monkeypatch):
        from benchbox.core.errors import PlanCaptureError

        adapter = SQLiteAdapter(capture_plans=True, strict_plan_capture=True)
        self._break_plan_capture(adapter, monkeypatch)

        with pytest.raises(PlanCaptureError):
            adapter.execute_query(conn, "SELECT * FROM orders", "q_strict", validate_row_count=False)

    def test_non_strict_capture_failure_is_silent(self, conn, monkeypatch):
        adapter = SQLiteAdapter(capture_plans=True, strict_plan_capture=False)
        self._break_plan_capture(adapter, monkeypatch)

        result = adapter.execute_query(conn, "SELECT * FROM orders", "q_nonstrict", validate_row_count=False)

        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result
        assert result["rows_returned"] == 2

    def test_strict_mode_does_not_mask_real_query_failure(self, conn):

        adapter = SQLiteAdapter(capture_plans=True, strict_plan_capture=True)

        result = adapter.execute_query(conn, "SELECT * FROM no_such_table", "q_bad", validate_row_count=False)

        assert result["status"] == "FAILED"
        assert result["error"]
