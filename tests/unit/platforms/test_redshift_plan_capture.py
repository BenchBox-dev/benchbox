from unittest.mock import MagicMock

import pytest

from benchbox.platforms.redshift import RedshiftAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


_STUB_CREDS = {"host": "localhost", "username": "test", "password": "test"}


@pytest.fixture()
def adapter(monkeypatch):
    monkeypatch.setattr("benchbox.platforms.redshift.redshift_connector", MagicMock(), raising=False)
    return RedshiftAdapter(**_STUB_CREDS, capture_plans=True)


@pytest.fixture()
def adapter_no_capture(monkeypatch):
    monkeypatch.setattr("benchbox.platforms.redshift.redshift_connector", MagicMock(), raising=False)
    return RedshiftAdapter(**_STUB_CREDS, capture_plans=False)


def _make_connection(plan_text="XN Seq Scan on orders\n  (cost=0.00..10.00 rows=1 width=4)"):
    cursor = MagicMock()
    cursor.fetchall.return_value = [(line,) for line in plan_text.split("\n")]
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


class TestRedshiftPlanCapture:
    def test_get_query_plan_parser_returns_instance(self, adapter):
        from benchbox.core.query_plans.parsers.redshift import RedshiftQueryPlanParser

        parser = adapter.get_query_plan_parser()
        assert parser is not None
        assert isinstance(parser, RedshiftQueryPlanParser)

    def test_get_query_plan_returns_text(self, adapter):
        conn, _ = _make_connection("XN Seq Scan on orders")
        result = adapter.get_query_plan(conn, "SELECT 1")
        assert result is not None
        assert "XN Seq Scan" in result

    def test_get_query_plan_returns_none_on_error(self, adapter):
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = Exception("network error")
        result = adapter.get_query_plan(conn, "SELECT 1")
        assert result is None

    def test_execute_query_with_capture_adds_plan_fields(self, adapter, monkeypatch):
        conn, cursor = _make_connection()
        cursor.fetchall.return_value = [(0, "col1")]

        mock_plan = MagicMock()
        mock_plan.plan_fingerprint = "rs_fingerprint"
        monkeypatch.setattr(adapter, "capture_query_plan", lambda *a, **k: (mock_plan, 3.0))
        monkeypatch.setattr(adapter, "_get_query_statistics", lambda *a, **k: {})

        result = adapter.execute_query(connection=conn, query="SELECT 1", query_id="rq1", validate_row_count=False)

        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is mock_plan
        assert result["plan_fingerprint"] == "rs_fingerprint"
        assert result["plan_capture_time_ms"] == 3.0

    def test_execute_query_failed_status_skips_capture(self, adapter, monkeypatch):
        conn, cursor = _make_connection()
        cursor.fetchall.return_value = [(0, "col1")]

        capture_called = []
        monkeypatch.setattr(adapter, "capture_query_plan", lambda *a, **k: capture_called.append(True) or (None, 0.0))
        monkeypatch.setattr(adapter, "_get_query_statistics", lambda *a, **k: {})
        monkeypatch.setattr(adapter, "_build_query_result_with_validation", lambda **kw: {"status": "FAILED"})

        adapter.execute_query(connection=conn, query="SELECT 1", query_id="rq_fail", validate_row_count=False)

        assert not capture_called, "capture_query_plan must not be called on FAILED results"

    def test_execute_query_without_capture_has_no_plan(self, adapter_no_capture, monkeypatch):
        conn, cursor = _make_connection()
        cursor.fetchall.return_value = [(0, "col1")]
        monkeypatch.setattr(adapter_no_capture, "_get_query_statistics", lambda *a, **k: {})

        result = adapter_no_capture.execute_query(
            connection=conn, query="SELECT 1", query_id="rq2", validate_row_count=False
        )

        assert "query_plan" not in result or result.get("query_plan") is None

    def test_execute_query_calls_display_when_not_capturing(self, adapter_no_capture, monkeypatch):
        conn, cursor = _make_connection()
        cursor.fetchall.return_value = [(0, "col1")]
        monkeypatch.setattr(adapter_no_capture, "_get_query_statistics", lambda *a, **k: {})

        display_calls = []
        monkeypatch.setattr(
            adapter_no_capture,
            "display_query_plan_if_enabled",
            lambda *a, **k: display_calls.append(True),
        )

        adapter_no_capture.execute_query(
            connection=conn, query="SELECT 1", query_id="rq_disp", validate_row_count=False
        )

        assert len(display_calls) == 1, "display_query_plan_if_enabled must be called exactly once"

    def test_get_query_plan_does_not_use_analyze(self, adapter):
        cursor = MagicMock()
        cursor.fetchall.return_value = [("XN Seq Scan",)]
        conn = MagicMock()
        conn.cursor.return_value = cursor

        adapter.get_query_plan(conn, "SELECT 1")

        call_args = cursor.execute.call_args[0][0].upper()
        assert "EXPLAIN ANALYZE" not in call_args, "Redshift does not support EXPLAIN ANALYZE"


class TestRedshiftStrictPlanCapture:
    @staticmethod
    def _make_adapter(monkeypatch, strict):
        monkeypatch.setattr("benchbox.platforms.redshift.redshift_connector", MagicMock(), raising=False)
        adapter = RedshiftAdapter(**_STUB_CREDS, capture_plans=True, strict_plan_capture=strict)
        monkeypatch.setattr(adapter, "_get_query_statistics", lambda *a, **k: {})

        def boom(connection, query, explain_options=None):
            raise RuntimeError("EXPLAIN blew up")

        monkeypatch.setattr(adapter, "get_query_plan", boom)
        return adapter

    def test_strict_capture_failure_propagates(self, monkeypatch):
        from benchbox.core.errors import PlanCaptureError

        adapter = self._make_adapter(monkeypatch, strict=True)
        conn, cursor = _make_connection()
        cursor.fetchall.return_value = [(0, "col1")]

        with pytest.raises(PlanCaptureError):
            adapter.execute_query(connection=conn, query="SELECT 1", query_id="rq_strict", validate_row_count=False)

    def test_non_strict_capture_failure_is_silent(self, monkeypatch):
        adapter = self._make_adapter(monkeypatch, strict=False)
        conn, cursor = _make_connection()
        cursor.fetchall.return_value = [(0, "col1")]

        result = adapter.execute_query(
            connection=conn, query="SELECT 1", query_id="rq_nonstrict", validate_row_count=False
        )

        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result

    def test_strict_mode_does_not_mask_real_query_failure(self, monkeypatch):

        adapter = self._make_adapter(monkeypatch, strict=True)
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = RuntimeError("no such table: t")

        result = adapter.execute_query(connection=conn, query="SELECT 1", query_id="rq_bad", validate_row_count=False)

        assert result["status"] == "FAILED"
        assert "no such table" in result["error"]


