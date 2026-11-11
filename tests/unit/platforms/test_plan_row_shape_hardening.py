from pathlib import Path
from unittest.mock import MagicMock

import pytest

from benchbox.platforms.base.sql_execution import join_explain_rows

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "query_plans"


class TestJoinExplainRows:
    def test_single_row_full_text(self):
        text = '{"a": 1}\n{"b": 2}'
        assert join_explain_rows([(text,)]) == text

    def test_per_line_rows(self):
        assert join_explain_rows([("a",), ("b",)]) == "a\nb"

    def test_dict_cells_serialize_to_json(self):
        import json

        rows = [({"a": 1},), ({"b": 2},)]
        assert join_explain_rows(rows) == json.dumps({"a": 1}) + "\n" + json.dumps({"b": 2})

    def test_bytes_cells_decoded(self):
        assert join_explain_rows([(b"hello",)]) == "hello"

    def test_none_cells_and_rows_skipped(self):
        assert join_explain_rows([(None,), None, ("x",)]) == "x"

    def test_extra_columns_ignored(self):
        assert join_explain_rows([("plan", "extra")]) == "plan"

    def test_empty_returns_none(self):
        assert join_explain_rows([]) is None
        assert join_explain_rows(None) is None
        assert join_explain_rows([(None,)]) is None
        assert join_explain_rows([(), ("a",)]) == "a"

    def test_subscriptable_non_tuple_row(self):
        class _Row:
            def __getitem__(self, index):
                assert index == 0
                return "line A"

        assert join_explain_rows([_Row(), ("line B",)]) == "line A\nline B"

    def test_single_key_dict_row_yields_its_value(self):

        assert join_explain_rows([{"QUERY PLAN": "Seq Scan on t"}]) == "Seq Scan on t"

    def test_multi_key_dict_row_serializes_defensively(self):
        import json

        row = {"a": 1, "b": 2}
        assert join_explain_rows([row]) == json.dumps(row)

    def test_none_valued_single_key_dict_row_skipped(self):
        assert join_explain_rows([{"QUERY PLAN": None}, {"QUERY PLAN": "Seq Scan"}]) == "Seq Scan"


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self._last_sql = ""

    def execute(self, sql, *args, **kwargs):
        self._last_sql = sql

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.cursor_obj = _FakeCursor(rows)

    def cursor(self):
        return self.cursor_obj


class TestAdapterRowShapes:
    def test_presto_single_row_and_chunked_agree(self, monkeypatch):
        monkeypatch.setattr("benchbox.platforms.presto.prestodb", MagicMock(), raising=False)
        from benchbox.platforms.presto import PrestoAdapter

        full = (_FIXTURES / "presto_explain_sample.json").read_text()
        adapter = PrestoAdapter(capture_plans=True)
        single = adapter.get_query_plan(_FakeConn([(full,)]), "SELECT 1")
        chunked = adapter.get_query_plan(_FakeConn([{"id": "6", "name": "Output"}]), "SELECT 1")
        assert single == full
        assert chunked.strip().startswith("{")
        import json

        assert json.loads(chunked)["name"] == "Output"

    def test_clickhouse_single_row_and_per_line_agree(self):
        from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter

        adapter = ClickHouseAdapter.__new__(ClickHouseAdapter)
        lines = ["Expression ((Projection))", "  Aggregating", "    ReadFromMergeTree (t)"]

        class _CHConn:
            def __init__(self, rows):
                self._rows = rows

            def execute(self, sql):
                return self._rows

        per_line = adapter.get_query_plan(_CHConn([(line,) for line in lines]), "SELECT 1")
        single = adapter.get_query_plan(_CHConn(["\n".join(lines)]), "SELECT 1")
        assert per_line == single == "\n".join(lines)

    def test_spark_helper_single_row_and_per_line_agree(self):
        from benchbox.platforms import _spark_helpers

        lines = (_FIXTURES / "spark_explain_sample.txt").read_text().splitlines()

        class _DF:
            def __init__(self, rows):
                self._rows = rows

            def collect(self):
                return self._rows

        class _Spark:
            def __init__(self, rows):
                self._rows = rows

            def sql(self, _):
                return _DF(self._rows)

        per_line = _spark_helpers.get_spark_query_plan(_Spark([(line,) for line in lines]), "SELECT 1")
        single = _spark_helpers.get_spark_query_plan(_Spark([("\n".join(lines),)]), "SELECT 1")
        assert per_line == single
        assert "== Physical Plan ==" in per_line

    def test_databricks_single_row_and_per_line_agree(self, monkeypatch):
        monkeypatch.setattr("benchbox.platforms.databricks.adapter.databricks", MagicMock(), raising=False)
        from benchbox.platforms.databricks.adapter import DatabricksAdapter

        lines = (_FIXTURES / "spark_explain_sample.txt").read_text().splitlines()
        adapter = DatabricksAdapter.__new__(DatabricksAdapter)
        per_line = adapter.get_query_plan(_FakeConn([(line,) for line in lines]), "SELECT 1")
        single = adapter.get_query_plan(_FakeConn([("\n".join(lines),)]), "SELECT 1")
        assert per_line == single

    def test_databricks_empty_returns_none(self, monkeypatch):

        monkeypatch.setattr("benchbox.platforms.databricks.adapter.databricks", MagicMock(), raising=False)
        from benchbox.platforms.databricks.adapter import DatabricksAdapter

        adapter = DatabricksAdapter.__new__(DatabricksAdapter)
        assert adapter.get_query_plan(_FakeConn([]), "SELECT 1") is None

    def test_cursor_helper_empty_returns_empty_string(self):

        from benchbox.platforms.base.sql_execution import get_query_plan_from_cursor

        assert get_query_plan_from_cursor(_FakeConn([]), "SELECT 1") == ""
        assert get_query_plan_from_cursor(_FakeConn([("a",), ("b",)]), "SELECT 1") == "a\nb"

    def test_presto_empty_returns_empty_string(self, monkeypatch):
        monkeypatch.setattr("benchbox.platforms.presto.prestodb", MagicMock(), raising=False)
        from benchbox.platforms.presto import PrestoAdapter

        adapter = PrestoAdapter(capture_plans=True)
        assert adapter.get_query_plan(_FakeConn([]), "SELECT 1") == ""


