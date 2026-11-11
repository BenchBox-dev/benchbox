from pathlib import Path

import pytest

from benchbox.core.query_plans.parsers.registry import get_parser_for_platform
from benchbox.core.query_plans.parsers.spark import SparkQueryPlanParser
from benchbox.core.results.query_plan_models import LogicalOperator, LogicalOperatorType

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
    return SparkQueryPlanParser()


class TestSparkPhysicalPlan:
    def test_parses_to_valid_dag(self, parser):
        dag = parser.parse_explain_output("q1", _load("spark_explain_sample.txt"))
        assert dag is not None
        assert dag.logical_root is not None
        assert dag.plan_fingerprint is not None
        assert dag.platform == "spark"

    def test_only_physical_section_parsed(self, parser):
        dag = parser.parse_explain_output("q2", _load("spark_explain_sample.txt"))
        assert dag.logical_root.operator_type == LogicalOperatorType.AGGREGATE
        assert dag.logical_root.physical_operator.operator_type == "HashAggregate"

    def test_tree_has_join_over_two_scans(self, parser):
        dag = parser.parse_explain_output("q3", _load("spark_explain_sample.txt"))
        nodes = _collect(dag.logical_root)
        types = [n.operator_type for n in nodes]
        assert LogicalOperatorType.JOIN in types
        assert types.count(LogicalOperatorType.SCAN) == 2
        tables = {n.table_name for n in nodes if n.table_name}
        assert tables == {"default.lineitem", "default.orders"}

    def test_bare_physical_plan_without_header(self, parser):
        bare = "*(1) Filter isnotnull(x#1)\n+- FileScan parquet default.t[x#1]\n"
        dag = parser.parse_explain_output("q4", bare)
        assert dag is not None
        types = [n.operator_type for n in _collect(dag.logical_root)]
        assert types == [LogicalOperatorType.FILTER, LogicalOperatorType.SCAN]


class TestSparkOperatorNormalization:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("FileScan", LogicalOperatorType.SCAN),
            ("BatchScan", LogicalOperatorType.SCAN),
            ("InMemoryTableScan", LogicalOperatorType.SCAN),
            ("Filter", LogicalOperatorType.FILTER),
            ("Project", LogicalOperatorType.PROJECT),
            ("HashAggregate", LogicalOperatorType.AGGREGATE),
            ("ObjectHashAggregate", LogicalOperatorType.AGGREGATE),
            ("SortAggregate", LogicalOperatorType.AGGREGATE),
            ("BroadcastHashJoin", LogicalOperatorType.JOIN),
            ("SortMergeJoin", LogicalOperatorType.JOIN),
            ("BroadcastNestedLoopJoin", LogicalOperatorType.JOIN),
            ("ShuffledHashJoin", LogicalOperatorType.JOIN),
            ("Sort", LogicalOperatorType.SORT),
            ("TakeOrderedAndProject", LogicalOperatorType.SORT),
            ("GlobalLimit", LogicalOperatorType.LIMIT),
            ("CollectLimit", LogicalOperatorType.LIMIT),
            ("Window", LogicalOperatorType.WINDOW),
            ("Union", LogicalOperatorType.UNION),
            ("Exchange", LogicalOperatorType.OTHER),
            ("BroadcastExchange", LogicalOperatorType.OTHER),
            ("ColumnarToRow", LogicalOperatorType.OTHER),
        ],
    )
    def test_map_operator_type(self, name, expected):
        assert SparkQueryPlanParser._map_operator_type(name) == expected


class TestSparkErrorRecovery:
    def test_empty_input_returns_none(self, parser):
        assert parser.parse_explain_output("q", "") is None

    def test_physical_header_with_no_ops_returns_none(self, parser):
        assert parser.parse_explain_output("q", "== Physical Plan ==\n") is None

    def test_garbage_input_does_not_raise(self, parser):
        result = parser.parse_explain_output("q", "!@#$ %^&*")
        assert result is None or result.logical_root is not None

    def test_error_sentinel_returns_none(self, parser):
        assert parser.parse_explain_output("q", "Failed to get query plan: connection reset") is None

    def test_none_input_returns_none(self, parser):
        assert parser.parse_explain_output("q", None) is None


class TestSparkRegistration:
    @pytest.mark.parametrize("dialect", ["spark", "databricks"])
    def test_registered_for_dialect(self, dialect):
        assert isinstance(get_parser_for_platform(dialect), SparkQueryPlanParser)


