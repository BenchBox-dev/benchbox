from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from benchbox.platforms.datafusion import DataFusionAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


_EXPLAIN_TEXT = (
    "logical_plan | TableScan: t1 projection=[id, val]\nphysical_plan | DataSourceExec: file_groups={1 group}"
)


def _make_explain_batch(plan_type_val, plan_text_val):

    col0 = MagicMock()
    col0.__getitem__ = lambda self, i: MagicMock(as_py=lambda: plan_type_val)
    col1 = MagicMock()
    col1.__getitem__ = lambda self, i: MagicMock(as_py=lambda: plan_text_val)
    batch = MagicMock()
    batch.num_rows = 1
    batch.column = lambda i: col0 if i == 0 else col1
    return batch


@pytest.fixture()
def adapter(monkeypatch):
    monkeypatch.setattr("benchbox.platforms.datafusion.datafusion", MagicMock(), raising=False)
    return DataFusionAdapter(capture_plans=True)


@pytest.fixture()
def adapter_no_capture(monkeypatch):
    monkeypatch.setattr("benchbox.platforms.datafusion.datafusion", MagicMock(), raising=False)
    return DataFusionAdapter(capture_plans=False)


class TestDataFusionPlanCapture:
    def test_get_query_plan_parser_returns_instance(self, adapter):
        from benchbox.core.query_plans.parsers.datafusion import DataFusionQueryPlanParser

        parser = adapter.get_query_plan_parser()
        assert parser is not None
        assert isinstance(parser, DataFusionQueryPlanParser)

    def test_get_query_plan_returns_formatted_text(self, adapter):
        batch = _make_explain_batch("logical_plan", "TableScan: t1")
        conn = MagicMock()
        conn.sql.return_value.collect.return_value = [batch]

        result = adapter.get_query_plan(conn, "SELECT 1")
        assert result is not None
        assert "logical_plan" in result
        assert "TableScan" in result

    def test_get_query_plan_returns_none_on_empty(self, adapter):
        conn = MagicMock()
        conn.sql.return_value.collect.return_value = []
        assert adapter.get_query_plan(conn, "SELECT 1") is None

    def test_get_query_plan_returns_none_on_error(self, adapter):
        conn = MagicMock()
        conn.sql.side_effect = Exception("datafusion error")
        assert adapter.get_query_plan(conn, "SELECT 1") is None

    def test_get_query_plan_does_not_use_format_json(self, adapter):
        batch = _make_explain_batch("physical_plan", "DataSourceExec: ...")
        conn = MagicMock()
        conn.sql.return_value.collect.return_value = [batch]

        adapter.get_query_plan(conn, "SELECT 1")

        called_sql = conn.sql.call_args[0][0]
        assert "FORMAT JSON" not in called_sql.upper(), "DataFusionQueryPlanParser expects text/indent format, not JSON"

    def test_execute_query_with_capture_adds_plan_fields(self, adapter, monkeypatch):
        conn = MagicMock()
        conn.sql.return_value.collect.return_value = MagicMock()
        conn.sql.return_value.collect.return_value.__iter__ = lambda s: iter([])

        mock_plan = MagicMock()
        mock_plan.plan_fingerprint = "df_fp"
        monkeypatch.setattr(adapter, "capture_query_plan", lambda *a, **k: (mock_plan, 2.5))

        from unittest.mock import patch as _patch

        fake_result = MagicMock()
        fake_result.collect.return_value = []
        fake_result.schema.return_value = pa.schema([("a", pa.int64())])
        conn.sql.return_value = fake_result

        with _patch.object(
            adapter,
            "_build_query_result_with_validation",
            return_value={"query_id": "df_q1", "status": "SUCCESS", "rows_returned": 0},
        ):
            result = adapter.execute_query(
                connection=conn, query="SELECT 1", query_id="df_q1", validate_row_count=False
            )

        assert result["query_plan"] is mock_plan
        assert result["plan_fingerprint"] == "df_fp"
        assert result["plan_capture_time_ms"] == 2.5

    def test_execute_query_without_capture_has_no_plan(self, adapter_no_capture, monkeypatch):
        conn = MagicMock()
        fake_result = MagicMock()
        fake_result.collect.return_value = []
        fake_result.schema.return_value = pa.schema([("a", pa.int64())])
        conn.sql.return_value = fake_result

        with patch.object(
            adapter_no_capture,
            "_build_query_result_with_validation",
            return_value={"query_id": "df_q2", "status": "SUCCESS", "rows_returned": 0},
        ):
            result = adapter_no_capture.execute_query(
                connection=conn, query="SELECT 1", query_id="df_q2", validate_row_count=False
            )

        assert "query_plan" not in result or result.get("query_plan") is None

    def test_execute_query_failed_status_skips_capture(self, adapter, monkeypatch):
        conn = MagicMock()
        fake_result = MagicMock()
        fake_result.collect.return_value = []
        fake_result.schema.return_value = pa.schema([("a", pa.int64())])
        conn.sql.return_value = fake_result

        capture_called = []
        monkeypatch.setattr(adapter, "capture_query_plan", lambda *a, **k: capture_called.append(True) or (None, 0.0))
        monkeypatch.setattr(
            adapter,
            "_build_query_result_with_validation",
            lambda **kw: {"query_id": "df_fail", "status": "FAILED", "rows_returned": 0},
        )

        adapter.execute_query(connection=conn, query="SELECT 1", query_id="df_fail", validate_row_count=False)

        assert not capture_called, "capture_query_plan must not be called on FAILED results"

    def test_execute_query_calls_display_when_not_capturing(self, adapter_no_capture, monkeypatch):
        conn = MagicMock()
        fake_result = MagicMock()
        fake_result.collect.return_value = []
        fake_result.schema.return_value = pa.schema([("a", pa.int64())])
        conn.sql.return_value = fake_result

        display_calls = []
        monkeypatch.setattr(
            adapter_no_capture,
            "display_query_plan_if_enabled",
            lambda *a, **k: display_calls.append(True),
        )
        monkeypatch.setattr(
            adapter_no_capture,
            "_build_query_result_with_validation",
            lambda **kw: {"query_id": "df_disp", "status": "SUCCESS", "rows_returned": 0},
        )

        adapter_no_capture.execute_query(
            connection=conn, query="SELECT 1", query_id="df_disp", validate_row_count=False
        )

        assert len(display_calls) == 1, "display_query_plan_if_enabled must be called exactly once"


