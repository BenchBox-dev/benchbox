from pathlib import Path

import pytest

from benchbox.core.query_plans.parsers.clickhouse import ClickHouseQueryPlanParser
from benchbox.core.query_plans.parsers.registry import get_parser_for_platform
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
    return ClickHouseQueryPlanParser()


class TestClickHouseExplainPlan:
    def test_parses_plan_to_valid_dag(self, parser):
        dag = parser.parse_explain_output("q1", _load("clickhouse_explain_plan_sample.txt"))
        assert dag is not None
        assert dag.logical_root is not None
        assert dag.plan_fingerprint is not None
        assert dag.platform == "clickhouse"

    def test_plan_tree_operator_types_and_tables(self, parser):
        dag = parser.parse_explain_output("q2", _load("clickhouse_explain_plan_sample.txt"))
        nodes = _collect(dag.logical_root)
        types = [n.operator_type for n in nodes]
        assert dag.logical_root.operator_type == LogicalOperatorType.PROJECT
        for expected in (
            LogicalOperatorType.SORT,
            LogicalOperatorType.AGGREGATE,
            LogicalOperatorType.FILTER,
            LogicalOperatorType.JOIN,
        ):
            assert expected in types, f"{expected} missing from plan"
        assert types.count(LogicalOperatorType.SCAN) == 2
        tables = {n.table_name for n in nodes if n.table_name}
        assert tables == {"default.lineitem", "default.orders"}

    def test_pipeline_output_parses_without_error(self, parser):
        dag = parser.parse_explain_output("q3", _load("clickhouse_explain_pipeline_sample.txt"))
        assert dag is not None
        types = [n.operator_type for n in _collect(dag.logical_root)]
        assert LogicalOperatorType.SCAN in types


class TestClickHouseOperatorNormalization:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("ReadFromMergeTree", LogicalOperatorType.SCAN),
            ("ReadFromMemoryStorage", LogicalOperatorType.SCAN),
            ("ReadFromPreparedSource", LogicalOperatorType.SCAN),
            ("Aggregating", LogicalOperatorType.AGGREGATE),
            ("MergingAggregated", LogicalOperatorType.AGGREGATE),
            ("Filter", LogicalOperatorType.FILTER),
            ("Join", LogicalOperatorType.JOIN),
            ("Sorting", LogicalOperatorType.SORT),
            ("MergeSorting", LogicalOperatorType.SORT),
            ("MergingSorted", LogicalOperatorType.SORT),
            ("MergingSortedTransform", LogicalOperatorType.SORT),
            ("ArrayJoin", LogicalOperatorType.OTHER),
            ("Limit", LogicalOperatorType.LIMIT),
            ("LimitBy", LogicalOperatorType.LIMIT),
            ("Offset", LogicalOperatorType.LIMIT),
            ("Expression", LogicalOperatorType.PROJECT),
            ("Union", LogicalOperatorType.UNION),
            ("Window", LogicalOperatorType.WINDOW),
            ("Distinct", LogicalOperatorType.OTHER),
        ],
    )
    def test_map_operator_type(self, name, expected):
        assert ClickHouseQueryPlanParser._map_operator_type(name) == expected

    @pytest.mark.parametrize(
        ("details", "expected"),
        [
            ("default.lineitem", "default.lineitem"),
            ("default.lineitem (columns: a, b)", "default.lineitem"),
            ("lineitem", "lineitem"),
            ("Sorting for ORDER BY", None),
            ("WHERE x > 1", None),
            ("", None),
        ],
    )
    def test_extract_table(self, details, expected):
        assert ClickHouseQueryPlanParser._extract_table(details) == expected


class TestClickHouseErrorRecovery:
    def test_empty_input_returns_none(self, parser):
        assert parser.parse_explain_output("q", "") is None
        assert parser.parse_explain_output("q", "   \n  ") is None

    def test_garbage_input_does_not_raise(self, parser):

        result = parser.parse_explain_output("q", "!@#$ %^&*")
        assert result is None or result.logical_root is not None


class TestClickHouseRegistration:
    def test_registered_for_clickhouse(self):
        assert isinstance(get_parser_for_platform("clickhouse"), ClickHouseQueryPlanParser)


