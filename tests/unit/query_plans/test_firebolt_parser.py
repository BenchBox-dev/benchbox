from pathlib import Path

import pytest

from benchbox.core.query_plans.parsers.firebolt import FireboltQueryPlanParser
from benchbox.core.query_plans.parsers.registry import get_parser_for_platform
from benchbox.core.results.query_plan_models import JoinType, LogicalOperator, LogicalOperatorType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "query_plans"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text()


def _collect(op: LogicalOperator) -> list[LogicalOperator]:
    nodes = [op]
    for child in op.children:
        nodes.extend(_collect(child))
    return nodes


@pytest.fixture()
def parser():
    return FireboltQueryPlanParser()


class TestFireboltParserBasics:
    def test_parses_fixture_to_valid_dag(self, parser):
        dag = parser.parse_explain_output("q1", _load("firebolt_explain_sample.txt"))
        assert dag is not None
        assert dag.logical_root is not None
        assert dag.plan_fingerprint is not None
        assert dag.platform == "firebolt"

    def test_tree_shape_join_over_two_scans(self, parser):
        dag = parser.parse_explain_output("q1", _load("firebolt_explain_sample.txt"))
        nodes = _collect(dag.logical_root)
        types = [n.operator_type for n in nodes]
        assert dag.logical_root.operator_type == LogicalOperatorType.PROJECT
        assert types.count(LogicalOperatorType.SCAN) == 2
        assert LogicalOperatorType.JOIN in types
        assert LogicalOperatorType.AGGREGATE in types
        assert LogicalOperatorType.SORT in types

    def test_scan_tables_and_join_condition(self, parser):
        dag = parser.parse_explain_output("q1", _load("firebolt_explain_sample.txt"))
        nodes = _collect(dag.logical_root)
        tables = {n.table_name for n in nodes if n.table_name}
        assert tables == {"orders", "lineitem"}
        joins = [n for n in nodes if n.operator_type == LogicalOperatorType.JOIN]
        assert joins[0].join_type == JoinType.INNER
        assert joins[0].join_conditions == ["o_orderkey = l_orderkey"]

    def test_two_scans_are_siblings_under_join(self, parser):
        dag = parser.parse_explain_output("q1", _load("firebolt_explain_sample.txt"))
        joins = [n for n in _collect(dag.logical_root) if n.operator_type == LogicalOperatorType.JOIN]
        assert len(joins[0].children) == 2


class TestFireboltParserDetailExtraction:
    def test_join_condition_with_nested_parens_not_truncated(self, parser):
        text = (
            "Join [type=inner] [condition: lower(o_comment) = lower(l_comment)]\n"
            " \\_TableScan [table: orders]\n"
            " \\_TableScan [table: lineitem]"
        )
        dag = parser.parse_explain_output("q", text)
        join = next(n for n in _collect(dag.logical_root) if n.operator_type == LogicalOperatorType.JOIN)

        assert join.join_conditions == ["lower(o_comment) = lower(l_comment)"]


class TestFireboltParserErrorHandling:
    def test_empty_returns_none(self, parser):
        assert parser.parse_explain_output("q", "") is None

    def test_whitespace_only_returns_none(self, parser):
        assert parser.parse_explain_output("q", "   \n  \n") is None


class TestFireboltRegistry:
    def test_registry_returns_firebolt_parser(self):
        assert isinstance(get_parser_for_platform("firebolt"), FireboltQueryPlanParser)


