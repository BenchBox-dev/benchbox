from pathlib import Path

import pytest

from benchbox.core.query_plans.parsers.azure_synapse import AzureSynapseQueryPlanParser
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
    return AzureSynapseQueryPlanParser()


class TestAzureSynapseParserBasics:
    def test_parses_fixture_to_valid_dag(self, parser):
        dag = parser.parse_explain_output("q1", _load("azure_synapse_explain_sample.xml"))
        assert dag is not None
        assert dag.logical_root is not None
        assert dag.plan_fingerprint is not None
        assert dag.platform == "azure_synapse"

    def test_return_is_root_pipeline_ordered(self, parser):
        dag = parser.parse_explain_output("q1", _load("azure_synapse_explain_sample.xml"))

        assert dag.logical_root.operator_type == LogicalOperatorType.PROJECT

        node = dag.logical_root
        while node.children:
            assert len(node.children) == 1
            node = node.children[0]

    def test_shuffle_move_with_join_sql_refined_to_join(self, parser):
        dag = parser.parse_explain_output("q1", _load("azure_synapse_explain_sample.xml"))
        types = [n.operator_type for n in _collect(dag.logical_root)]
        assert LogicalOperatorType.JOIN in types
        joins = [n for n in _collect(dag.logical_root) if n.operator_type == LogicalOperatorType.JOIN]
        assert joins[0].join_type == JoinType.INNER

    def test_output_rows_recorded(self, parser):
        dag = parser.parse_explain_output("q1", _load("azure_synapse_explain_sample.xml"))
        assert any(n.physical_operator.properties.get("output_rows") for n in _collect(dag.logical_root))


class TestAzureSynapseJoinClassification:
    def test_literal_containing_right_does_not_misclassify_inner_join(self, parser):
        xml = (
            "<dsql_query><dsql_operations>"
            "<dsql_operation operation_type='SHUFFLE_MOVE'>"
            "<source_statement>SELECT * FROM a INNER JOIN b ON a.k = b.k "
            "WHERE a.mode = 'RIGHT_OF_WAY'</source_statement>"
            "</dsql_operation>"
            "<dsql_operation operation_type='RETURN'></dsql_operation>"
            "</dsql_operations></dsql_query>"
        )
        dag = parser.parse_explain_output("q", xml)
        joins = [n for n in _collect(dag.logical_root) if n.operator_type == LogicalOperatorType.JOIN]
        assert joins and joins[0].join_type == JoinType.INNER

    def test_left_outer_join_detected(self, parser):
        xml = (
            "<dsql_query><dsql_operations>"
            "<dsql_operation operation_type='SHUFFLE_MOVE'>"
            "<source_statement>SELECT * FROM a LEFT OUTER JOIN b ON a.k = b.k</source_statement>"
            "</dsql_operation>"
            "<dsql_operation operation_type='RETURN'></dsql_operation>"
            "</dsql_operations></dsql_query>"
        )
        dag = parser.parse_explain_output("q", xml)
        joins = [n for n in _collect(dag.logical_root) if n.operator_type == LogicalOperatorType.JOIN]
        assert joins and joins[0].join_type == JoinType.LEFT


class TestAzureSynapseParserErrorHandling:
    def test_empty_returns_none(self, parser):
        assert parser.parse_explain_output("q", "") is None

    def test_invalid_xml_returns_none(self, parser):
        assert parser.parse_explain_output("q", "<dsql_query><unclosed>") is None

    def test_no_operations_returns_none(self, parser):
        assert parser.parse_explain_output("q", "<dsql_query><sql>SELECT 1</sql></dsql_query>") is None


class TestAzureSynapseRegistry:
    def test_registry_returns_synapse_parser(self):
        assert isinstance(get_parser_for_platform("azure_synapse"), AzureSynapseQueryPlanParser)


