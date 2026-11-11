from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.postgresql import PostgreSQLAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def adapter(monkeypatch):

    monkeypatch.setattr("benchbox.platforms.postgresql.psycopg", MagicMock())
    return PostgreSQLAdapter(capture_plans=True)


@pytest.fixture()
def adapter_no_capture(monkeypatch):
    monkeypatch.setattr("benchbox.platforms.postgresql.psycopg", MagicMock())
    return PostgreSQLAdapter(capture_plans=False)


def _make_connection(plan_json='[{"Plan": {"Node Type": "Result"}}]'):

    cursor = MagicMock()
    cursor.fetchall.return_value = [(plan_json,)]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


class TestPostgreSQLPlanCapture:
    def test_get_query_plan_parser_returns_instance(self, adapter):
        from benchbox.core.query_plans.parsers.postgresql import PostgreSQLQueryPlanParser

        parser = adapter.get_query_plan_parser()
        assert parser is not None
        assert isinstance(parser, PostgreSQLQueryPlanParser)

    def test_get_query_plan_returns_json(self, adapter):
        conn = _make_connection('[{"Plan": {"Node Type": "Seq Scan"}}]')
        result = adapter.get_query_plan(conn, "SELECT 1")
        assert result is not None
        assert "Plan" in result or "Seq Scan" in result

    def test_get_query_plan_returns_none_on_error(self, adapter):
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = Exception("db error")
        result = adapter.get_query_plan(conn, "SELECT 1")
        assert result is None

    def test_get_query_plan_accepts_cursor_backed_stream(self, adapter):

        fresh_cursor = MagicMock(spec=["execute", "fetchall", "close"])
        fresh_cursor.fetchall.return_value = [('[{"Plan": {"Node Type": "Seq Scan"}}]',)]
        stream_cursor = MagicMock(spec=["execute", "fetchall", "close", "connection"])
        stream_cursor.connection.cursor.return_value = fresh_cursor

        result = adapter.get_query_plan(stream_cursor, "SELECT 1")

        assert result is not None
        assert "Seq Scan" in result

        fresh_cursor.execute.assert_called_once()
        stream_cursor.execute.assert_not_called()

        fresh_cursor.close.assert_called_once()
        stream_cursor.close.assert_not_called()

    def test_execute_query_with_capture_adds_plan_fields(self, adapter, monkeypatch):
        conn = _make_connection()

        mock_plan = MagicMock()
        mock_plan.plan_fingerprint = "abc123"
        monkeypatch.setattr(adapter, "capture_query_plan", lambda *a, **k: (mock_plan, 5.0))

        monkeypatch.setattr(
            "benchbox.platforms.base.psycopg2_mixin.PsycopgConnectionMixin.execute_query",
            lambda self, **kw: {"query_id": kw["query_id"], "status": "SUCCESS", "rows_returned": 1},
        )

        result = adapter.execute_query(connection=conn, query="SELECT 1", query_id="q1")

        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is mock_plan
        assert result["plan_fingerprint"] == "abc123"
        assert result["plan_capture_time_ms"] == 5.0

    def test_execute_query_without_capture_has_no_plan(self, adapter_no_capture, monkeypatch):
        conn = _make_connection()

        monkeypatch.setattr(
            "benchbox.platforms.base.psycopg2_mixin.PsycopgConnectionMixin.execute_query",
            lambda self, **kw: {"query_id": kw["query_id"], "status": "SUCCESS", "rows_returned": 1},
        )

        result = adapter_no_capture.execute_query(connection=conn, query="SELECT 1", query_id="q1")

        assert "query_plan" not in result or result.get("query_plan") is None

    def test_execute_query_failed_status_skips_capture(self, adapter, monkeypatch):
        conn = _make_connection()

        capture_called = []
        monkeypatch.setattr(adapter, "capture_query_plan", lambda *a, **k: capture_called.append(True) or (None, 0.0))

        monkeypatch.setattr(
            "benchbox.platforms.base.psycopg2_mixin.PsycopgConnectionMixin.execute_query",
            lambda self, **kw: {"query_id": kw["query_id"], "status": "FAILED", "rows_returned": 0},
        )

        adapter.execute_query(connection=conn, query="SELECT 1", query_id="q1")

        assert not capture_called, "capture_query_plan must not be called on FAILED queries"

    def test_get_query_plan_uses_format_json(self, adapter):
        cursor = MagicMock()
        cursor.fetchall.return_value = [('{"Plan":{}}',)]
        conn = MagicMock()
        conn.cursor.return_value = cursor

        adapter.get_query_plan(conn, "SELECT 1")

        call_args = cursor.execute.call_args[0][0]
        assert "FORMAT JSON" in call_args.upper()
        assert "FORMAT TEXT" not in call_args.upper()

    @pytest.mark.parametrize(
        "dml_query",
        [
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET a = 1 WHERE id = 2",
            "DELETE FROM t WHERE id = 3",
            "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = s.a",
            "COPY t FROM '/tmp/data.csv'",
            "  insert into t values (1)",
        ],
    )
    def test_get_query_plan_dml_omits_analyze(self, adapter, dml_query):

        cursor = MagicMock()
        cursor.fetchall.return_value = [('{"Plan":{}}',)]
        conn = MagicMock()
        conn.cursor.return_value = cursor

        adapter.get_query_plan(conn, dml_query)

        call_args = cursor.execute.call_args[0][0]
        assert "ANALYZE" not in call_args.upper(), f"EXPLAIN ANALYZE must not be used for DML query: {dml_query!r}"
        assert "FORMAT JSON" in call_args.upper()

    @pytest.mark.parametrize(
        "write_ddl",
        [
            "CREATE TABLE t2 AS SELECT * FROM t",
            "CREATE MATERIALIZED VIEW mv AS SELECT * FROM t",
            "SELECT * INTO t2 FROM t",
        ],
    )
    def test_get_query_plan_ctas_omits_analyze(self, adapter, write_ddl):

        cursor = MagicMock()
        cursor.fetchall.return_value = [('{"Plan":{}}',)]
        conn = MagicMock()
        conn.cursor.return_value = cursor

        adapter.get_query_plan(conn, write_ddl)

        call_args = cursor.execute.call_args[0][0]
        assert "ANALYZE" not in call_args.upper(), f"EXPLAIN ANALYZE must not be used for write DDL: {write_ddl!r}"
        assert "FORMAT JSON" in call_args.upper()

    def test_get_query_plan_select_uses_analyze(self, monkeypatch):

        monkeypatch.setattr("benchbox.platforms.postgresql.psycopg", MagicMock())
        adapter = PostgreSQLAdapter(capture_plans=True, analyze_plans=True)
        cursor = MagicMock()
        cursor.fetchall.return_value = [('{"Plan":{}}',)]
        conn = MagicMock()
        conn.cursor.return_value = cursor

        adapter.get_query_plan(conn, "SELECT count(*) FROM orders")

        call_args = cursor.execute.call_args[0][0]
        assert "ANALYZE" in call_args.upper()
        assert "BUFFERS" in call_args.upper()
        assert "FORMAT JSON" in call_args.upper()

    def test_get_query_plan_select_omits_analyze_when_disabled(self, monkeypatch):

        monkeypatch.setattr("benchbox.platforms.postgresql.psycopg", MagicMock())
        adapter = PostgreSQLAdapter(capture_plans=True, analyze_plans=False)
        cursor = MagicMock()
        cursor.fetchall.return_value = [('{"Plan":{}}',)]
        conn = MagicMock()
        conn.cursor.return_value = cursor

        adapter.get_query_plan(conn, "SELECT count(*) FROM orders")

        call_args = cursor.execute.call_args[0][0]
        assert "ANALYZE" not in call_args.upper(), "analyze_plans=False must not re-execute the SELECT"
        assert "FORMAT JSON" in call_args.upper()


class TestPostgreSQLSubclassInheritance:
    @pytest.mark.parametrize(
        "adapter_class_path,module_path",
        [
            ("benchbox.platforms.timescaledb.TimescaleDBAdapter", "benchbox.platforms.timescaledb"),
            ("benchbox.platforms.cedardb.CedarDBAdapter", "benchbox.platforms.cedardb"),
            ("benchbox.platforms.pg_mooncake.PgMooncakeAdapter", "benchbox.platforms.pg_mooncake"),
        ],
    )
    def test_subclass_returns_postgresql_parser(self, adapter_class_path, module_path, monkeypatch):
        from benchbox.core.query_plans.parsers.postgresql import PostgreSQLQueryPlanParser

        module_name, class_name = adapter_class_path.rsplit(".", 1)
        monkeypatch.setattr("benchbox.platforms.postgresql.psycopg", MagicMock())

        try:
            import importlib

            module = importlib.import_module(module_name)

            cls = getattr(module, class_name)
            adapter = cls.__new__(cls)

            adapter.__dict__["capture_plans"] = True

            parser = PostgreSQLAdapter.get_query_plan_parser(adapter)
            assert isinstance(parser, PostgreSQLQueryPlanParser), (
                f"{class_name} should inherit PostgreSQLQueryPlanParser via PostgreSQLAdapter"
            )
        except (ImportError, AttributeError):
            pytest.skip(f"Could not import {adapter_class_path}")


