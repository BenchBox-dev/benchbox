from pathlib import Path

import pytest

from benchbox.core.query_plans.parsers.fabric_warehouse import FabricWarehouseQueryPlanParser
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
    return FabricWarehouseQueryPlanParser()


class TestFabricWarehouseParserBasics:
    def test_parses_fixture_to_valid_dag(self, parser):
        dag = parser.parse_explain_output("q1", _load("fabric_warehouse_showplan_sample.txt"))
        assert dag is not None
        assert dag.logical_root is not None
        assert dag.plan_fingerprint is not None
        assert dag.platform == "fabric_warehouse"

    def test_statement_line_is_not_an_operator(self, parser):
        dag = parser.parse_explain_output("q1", _load("fabric_warehouse_showplan_sample.txt"))

        assert dag.logical_root.operator_type == LogicalOperatorType.SORT

    def test_tree_shape_join_over_two_scans(self, parser):
        dag = parser.parse_explain_output("q1", _load("fabric_warehouse_showplan_sample.txt"))
        types = [n.operator_type for n in _collect(dag.logical_root)]
        assert types.count(LogicalOperatorType.SCAN) == 2
        assert LogicalOperatorType.JOIN in types
        assert LogicalOperatorType.AGGREGATE in types

    def test_hash_match_aggregate_maps_to_aggregate(self, parser):
        dag = parser.parse_explain_output("q1", _load("fabric_warehouse_showplan_sample.txt"))
        aggregates = [
            n
            for n in _collect(dag.logical_root)
            if n.operator_type == LogicalOperatorType.AGGREGATE and n.physical_operator.operator_type == "Hash Match"
        ]
        assert len(aggregates) == 1

    def test_scan_tables_and_inner_join(self, parser):
        dag = parser.parse_explain_output("q1", _load("fabric_warehouse_showplan_sample.txt"))
        nodes = _collect(dag.logical_root)
        tables = {n.table_name for n in nodes if n.table_name}
        assert tables == {"dbo.orders", "dbo.lineitem"}
        joins = [n for n in nodes if n.operator_type == LogicalOperatorType.JOIN]
        assert joins[0].join_type == JoinType.INNER


class TestFabricWarehouseParserMapping:
    def test_hash_match_join_maps_to_join(self, parser):
        text = "SELECT 1\n  |--Hash Match(Inner Join, HASH:([a]))\n       |--Table Scan(OBJECT:([db].[dbo].[t]))"
        dag = parser.parse_explain_output("q", text)
        assert dag.logical_root.operator_type == LogicalOperatorType.JOIN


class TestFabricWarehouseParserErrorHandling:
    def test_empty_returns_none(self, parser):
        assert parser.parse_explain_output("q", "") is None

    def test_no_connector_lines_returns_none(self, parser):
        assert parser.parse_explain_output("q", "SELECT 1\nFROM t") is None


class TestFabricWarehouseRegistry:
    def test_registry_returns_fabric_parser(self):
        assert isinstance(get_parser_for_platform("fabric_warehouse"), FabricWarehouseQueryPlanParser)


