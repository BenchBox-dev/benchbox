from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from benchbox.core.query_plans.parsers.databend import DatabendQueryPlanParser
from benchbox.core.query_plans.parsers.doris import DorisQueryPlanParser
from benchbox.core.query_plans.parsers.duckdb import DuckDBQueryPlanParser
from benchbox.core.query_plans.parsers.questdb import QuestDBQueryPlanParser
from benchbox.core.query_plans.parsers.registry import get_parser_for_platform
from benchbox.core.query_plans.parsers.singlestore import SingleStoreQueryPlanParser
from benchbox.core.query_plans.parsers.spark import SparkQueryPlanParser
from benchbox.core.results.query_plan_models import JoinType, LogicalOperator, LogicalOperatorType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "query_plans"


def _load(name: str) -> str:

    return (_FIXTURES / name).read_text(encoding="utf-8")


def _collect(op: LogicalOperator) -> list[LogicalOperator]:
    nodes = [op]
    for child in op.children:
        nodes.extend(_collect(child))
    return nodes


@pytest.mark.parametrize(
    ("parser", "fixture", "root_type", "tables", "present", "scan_count"),
    [
        (
            DatabendQueryPlanParser(),
            "databend_explain_sample.txt",
            LogicalOperatorType.SORT,
            {"default.tpch.lineitem", "default.tpch.orders"},
            (LogicalOperatorType.AGGREGATE, LogicalOperatorType.JOIN, LogicalOperatorType.FILTER),
            2,
        ),
        (
            QuestDBQueryPlanParser(),
            "questdb_explain_sample.txt",
            LogicalOperatorType.SORT,
            {"trades"},
            (LogicalOperatorType.AGGREGATE, LogicalOperatorType.FILTER, LogicalOperatorType.SCAN),
            1,
        ),
        (
            DorisQueryPlanParser(),
            "doris_shape_plan_sample.txt",
            LogicalOperatorType.OTHER,
            {"lineitem", "orders"},
            (LogicalOperatorType.SORT, LogicalOperatorType.AGGREGATE, LogicalOperatorType.JOIN),
            2,
        ),
        (
            SingleStoreQueryPlanParser(),
            "singlestore_explain_sample.txt",
            LogicalOperatorType.PROJECT,
            {"db.lineitem", "db.orders"},
            (LogicalOperatorType.SORT, LogicalOperatorType.AGGREGATE, LogicalOperatorType.JOIN),
            2,
        ),
        (
            SparkQueryPlanParser(),
            "velox_explain_extended_sample.txt",
            LogicalOperatorType.OTHER,
            {"default.lineitem", "default.orders"},
            (LogicalOperatorType.AGGREGATE, LogicalOperatorType.JOIN, LogicalOperatorType.SCAN),
            2,
        ),
    ],
)
def test_parser_builds_valid_dag(parser, fixture, root_type, tables, present, scan_count):
    dag = parser.parse_explain_output("q1", _load(fixture))
    assert dag is not None
    assert dag.logical_root is not None
    assert dag.plan_fingerprint is not None
    assert dag.logical_root.operator_type == root_type

    nodes = _collect(dag.logical_root)
    types = {n.operator_type for n in nodes}
    for expected in present:
        assert expected in types, f"{expected} missing from {fixture}"

    found_tables = {n.table_name for n in nodes if n.table_name}
    assert found_tables == tables

    assert sum(1 for n in nodes if n.operator_type == LogicalOperatorType.SCAN) == scan_count


@pytest.mark.parametrize(
    ("parser", "fixture"),
    [
        (DatabendQueryPlanParser(), "databend_explain_sample.txt"),
        (DorisQueryPlanParser(), "doris_shape_plan_sample.txt"),
        (SingleStoreQueryPlanParser(), "singlestore_explain_sample.txt"),
    ],
)
def test_join_type_classified_inner(parser, fixture):
    dag = parser.parse_explain_output("q", _load(fixture))
    joins = [n for n in _collect(dag.logical_root) if n.operator_type == LogicalOperatorType.JOIN]
    assert joins, "expected at least one join"
    assert all(j.join_type == JoinType.INNER for j in joins)


@pytest.mark.parametrize(
    "parser",
    [
        DatabendQueryPlanParser(),
        QuestDBQueryPlanParser(),
        DorisQueryPlanParser(),
        SingleStoreQueryPlanParser(),
    ],
)
def test_empty_and_garbage_input_degrade_gracefully(parser):
    assert parser.parse_explain_output("q", "") is None
    assert parser.parse_explain_output("q", "   \n  ") is None

    result = parser.parse_explain_output("q", "!@#$ %^&*")
    assert result is None or result.logical_root is not None


@pytest.mark.parametrize(
    ("platform", "parser_cls"),
    [
        ("motherduck", DuckDBQueryPlanParser),
        ("databend", DatabendQueryPlanParser),
        ("questdb", QuestDBQueryPlanParser),
        ("velox", SparkQueryPlanParser),
        ("doris", DorisQueryPlanParser),
        ("singlestore", SingleStoreQueryPlanParser),
    ],
)
def test_registry_resolves_platform(platform, parser_cls):
    assert isinstance(get_parser_for_platform(platform), parser_cls)


class TestMotherDuckWiring:
    @pytest.fixture()
    def adapter(self):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        return MotherDuckAdapter(token="test-token", capture_plans=True)

    @staticmethod
    def _conn():
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(1,)]
        return conn

    def test_parser_is_duckdb(self, adapter):
        assert isinstance(adapter.get_query_plan_parser(), DuckDBQueryPlanParser)

    def test_execute_query_captures_plan(self, adapter, monkeypatch):
        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: _load("motherduck_duckdb_explain_sample.json"))
        result = adapter.execute_query(self._conn(), "SELECT 1", "q1")
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] == result["query_plan"].plan_fingerprint

    def test_no_capture_when_disabled(self, adapter, monkeypatch):
        adapter.capture_plans = False
        boom = MagicMock(side_effect=AssertionError("EXPLAIN must not run when capture is disabled"))
        monkeypatch.setattr(adapter, "get_query_plan", boom)
        result = adapter.execute_query(self._conn(), "SELECT 1", "q2")
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result

    def test_graceful_when_plan_unavailable(self, adapter, monkeypatch):
        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: None)
        result = adapter.execute_query(self._conn(), "SELECT 1", "q3")
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result


def _cursor_conn():

    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchall.return_value = [(1,)]
    cursor.fetchone.return_value = (1,)
    return conn


def _make_questdb():
    from benchbox.platforms.questdb import QuestDBAdapter

    return QuestDBAdapter(capture_plans=True)


def _make_doris():
    from benchbox.platforms.doris import DorisAdapter

    return DorisAdapter(capture_plans=True, host="localhost")


def _make_singlestore(monkeypatch):
    import benchbox.platforms.singlestore as ss

    monkeypatch.setattr(ss, "check_platform_dependencies", lambda *a, **k: (True, []))
    return ss.SingleStoreAdapter(capture_plans=True, host="localhost")


class TestCursorAdapterWiring:
    @pytest.mark.parametrize(
        ("make", "fixture", "parser_cls"),
        [
            (_make_questdb, "questdb_explain_sample.txt", QuestDBQueryPlanParser),
            (_make_doris, "doris_shape_plan_sample.txt", DorisQueryPlanParser),
            (None, "singlestore_explain_sample.txt", SingleStoreQueryPlanParser),
        ],
    )
    def test_parser_and_capture(self, make, fixture, parser_cls, monkeypatch):
        adapter = _make_singlestore(monkeypatch) if make is None else make()
        assert isinstance(adapter.get_query_plan_parser(), parser_cls)

        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: _load(fixture))
        result = adapter.execute_query(_cursor_conn(), "SELECT 1", "q1", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] == result["query_plan"].plan_fingerprint

    @pytest.mark.parametrize(
        "make",
        [_make_questdb, _make_doris, None],
    )
    def test_no_capture_when_disabled(self, make, monkeypatch):
        adapter = _make_singlestore(monkeypatch) if make is None else make()
        adapter.capture_plans = False
        boom = MagicMock(side_effect=AssertionError("EXPLAIN must not run when capture is disabled"))
        monkeypatch.setattr(adapter, "get_query_plan", boom)
        result = adapter.execute_query(_cursor_conn(), "SELECT 1", "q2", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result

    @pytest.mark.parametrize(
        "make",
        [_make_questdb, _make_doris, None],
    )
    def test_graceful_when_plan_unavailable(self, make, monkeypatch):
        adapter = _make_singlestore(monkeypatch) if make is None else make()
        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: None)
        result = adapter.execute_query(_cursor_conn(), "SELECT 1", "q3", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result


def _make_databend(monkeypatch):
    import benchbox.platforms.databend.adapter as dba

    monkeypatch.setattr(dba, "check_platform_dependencies", lambda *a, **k: (True, []))
    return dba.DatabendAdapter(capture_plans=True, host="localhost")


def _databend_conn():
    conn = MagicMock()
    conn.query_iter.return_value = [(1,)]
    return conn


class TestDatabendWiring:
    def test_parser_is_databend(self, monkeypatch):
        adapter = _make_databend(monkeypatch)
        assert isinstance(adapter.get_query_plan_parser(), DatabendQueryPlanParser)

    def test_execute_query_captures_plan(self, monkeypatch):
        adapter = _make_databend(monkeypatch)
        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: _load("databend_explain_sample.txt"))
        result = adapter.execute_query(_databend_conn(), "SELECT 1", "q1", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] == result["query_plan"].plan_fingerprint

    def test_no_capture_when_disabled(self, monkeypatch):
        adapter = _make_databend(monkeypatch)
        adapter.capture_plans = False
        boom = MagicMock(side_effect=AssertionError("EXPLAIN must not run when capture is disabled"))
        monkeypatch.setattr(adapter, "get_query_plan", boom)
        result = adapter.execute_query(_databend_conn(), "SELECT 1", "q2", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result

    def test_graceful_when_plan_unavailable(self, monkeypatch):
        adapter = _make_databend(monkeypatch)
        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: None)
        result = adapter.execute_query(_databend_conn(), "SELECT 1", "q3", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result


class _DF:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


class _FakeSpark:
    def __init__(self):
        self.queries = []

    def sql(self, query):
        self.queries.append(query)
        if query.strip().upper().startswith("EXPLAIN"):
            return _DF([(_load("velox_explain_extended_sample.txt"),)])
        return _DF([(1,)])


class TestVeloxWiring:
    @pytest.fixture()
    def adapter(self):
        from benchbox.platforms.velox import VeloxAdapter

        adapter = VeloxAdapter(capture_plans=True)
        adapter.disable_cache = False
        return adapter

    def test_parser_is_spark(self, adapter):

        assert isinstance(adapter.get_query_plan_parser(), SparkQueryPlanParser)

    def test_execute_query_captures_plan(self, adapter, monkeypatch):
        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: _load("velox_explain_extended_sample.txt"))
        result = adapter.execute_query(_FakeSpark(), "SELECT 1", "q1", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert result["query_plan"] is not None
        assert result["plan_fingerprint"] == result["query_plan"].plan_fingerprint

    def test_no_capture_when_disabled(self, adapter):
        adapter.capture_plans = False
        spark = _FakeSpark()
        result = adapter.execute_query(spark, "SELECT 1", "q2", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result or result.get("query_plan") is None
        assert not any(q.strip().upper().startswith("EXPLAIN") for q in spark.queries)

    def test_graceful_when_plan_unavailable(self, adapter, monkeypatch):
        monkeypatch.setattr(adapter, "get_query_plan", lambda *a, **k: None)
        result = adapter.execute_query(_FakeSpark(), "SELECT 1", "q3", validate_row_count=False)
        assert result["status"] == "SUCCESS"
        assert "query_plan" not in result or result.get("query_plan") is None


class TestErrorChannelCleanup:
    def test_questdb_failure_records_explain_failed(self):
        adapter = _make_questdb()
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = Exception("EXPLAIN boom")
        assert adapter.get_query_plan(conn, "SELECT 1") is None
        plan, _ = adapter.capture_query_plan(conn, "SELECT 1", "q-err-qdb")
        assert plan is None
        assert adapter.plan_capture_errors[-1]["reason"] == "explain_failed"

    def test_doris_failure_records_explain_failed(self):
        adapter = _make_doris()
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = Exception("EXPLAIN boom")
        assert adapter.get_query_plan(conn, "SELECT 1") is None
        plan, _ = adapter.capture_query_plan(conn, "SELECT 1", "q-err-doris")
        assert plan is None
        assert adapter.plan_capture_errors[-1]["reason"] == "explain_failed"

    def test_singlestore_failure_records_explain_failed(self, monkeypatch):
        adapter = _make_singlestore(monkeypatch)
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = Exception("EXPLAIN boom")
        assert adapter.get_query_plan(conn, "SELECT 1") is None
        plan, _ = adapter.capture_query_plan(conn, "SELECT 1", "q-err-ss")
        assert plan is None
        assert adapter.plan_capture_errors[-1]["reason"] == "explain_failed"
