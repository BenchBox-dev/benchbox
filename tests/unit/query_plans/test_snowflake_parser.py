import json
from pathlib import Path

import pytest

from benchbox.core.query_plans.parsers.registry import get_parser_for_platform
from benchbox.core.query_plans.parsers.snowflake import SnowflakeQueryPlanParser
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
    return SnowflakeQueryPlanParser()


class TestSnowflakeParserBasics:
    def test_parses_fixture_to_valid_dag(self, parser):
        dag = parser.parse_explain_output("q1", _load("snowflake_explain_sample.json"))
        assert dag is not None
        assert dag.logical_root is not None
        assert dag.plan_fingerprint is not None
        assert dag.platform == "snowflake"

    def test_tree_shape_join_over_two_scans(self, parser):
        dag = parser.parse_explain_output("q1", _load("snowflake_explain_sample.json"))
        nodes = _collect(dag.logical_root)
        types = [n.operator_type for n in nodes]
        assert dag.logical_root.operator_type == LogicalOperatorType.PROJECT
        assert types.count(LogicalOperatorType.SCAN) == 2
        assert LogicalOperatorType.JOIN in types
        assert LogicalOperatorType.AGGREGATE in types
        assert LogicalOperatorType.SORT in types
        tables = {n.table_name for n in nodes if n.table_name}
        assert tables == {"TPCH.PUBLIC.ORDERS", "TPCH.PUBLIC.LINEITEM"}

    def test_join_node_carries_inner_type(self, parser):
        dag = parser.parse_explain_output("q1", _load("snowflake_explain_sample.json"))
        joins = [n for n in _collect(dag.logical_root) if n.operator_type == LogicalOperatorType.JOIN]
        assert len(joins) == 1
        assert joins[0].join_type == JoinType.INNER

    def test_fingerprint_is_stable_across_reparse(self, parser):
        text = _load("snowflake_explain_sample.json")
        first = parser.parse_explain_output("q1", text).plan_fingerprint
        second = SnowflakeQueryPlanParser().parse_explain_output("q1", text).plan_fingerprint
        assert first == second


class TestSnowflakeParserOperatorMapping:
    def test_joinfilter_maps_to_scan_not_join(self, parser):
        payload = (
            '{"Operations": [[{"id": 0, "operation": "Result"}, '
            '{"id": 1, "operation": "JoinFilter", "parentOperators": [0], "objects": ["DB.T"]}]]}'
        )
        dag = parser.parse_explain_output("q", payload)
        types = [n.operator_type for n in _collect(dag.logical_root)]
        assert LogicalOperatorType.SCAN in types
        assert LogicalOperatorType.JOIN not in types

    def test_left_outer_join_detected(self, parser):
        payload = (
            '{"Operations": [[{"id": 0, "operation": "Result"}, '
            '{"id": 1, "operation": "LeftOuterJoin", "parentOperators": [0]}]]}'
        )
        dag = parser.parse_explain_output("q", payload)
        joins = [n for n in _collect(dag.logical_root) if n.operator_type == LogicalOperatorType.JOIN]
        assert joins and joins[0].join_type == JoinType.LEFT


class TestSnowflakeParserMultipleRoots:
    def test_main_subtree_kept_when_multiple_parentless_nodes(self, parser):

        payload = json.dumps(
            {
                "Operations": [
                    [
                        {"id": 0, "operation": "Result"},
                        {"id": 1, "operation": "Aggregate", "parentOperators": [0]},
                        {"id": 2, "operation": "TableScan", "parentOperators": [1], "objects": ["DB.T"]},
                        {"id": 9, "operation": "WithReference"},
                    ]
                ]
            }
        )
        dag = parser.parse_explain_output("q", payload)
        types = [n.operator_type for n in _collect(dag.logical_root)]
        assert LogicalOperatorType.SCAN in types
        assert LogicalOperatorType.AGGREGATE in types


class TestSnowflakeParserErrorHandling:
    def test_empty_output_returns_none(self, parser):
        assert parser.parse_explain_output("q", "") is None

    def test_invalid_json_returns_none(self, parser):
        assert parser.parse_explain_output("q", "not json {") is None

    def test_no_operations_returns_none(self, parser):
        assert parser.parse_explain_output("q", '{"GlobalStats": {}}') is None


class TestSnowflakeRegistry:
    def test_registry_returns_snowflake_parser(self):
        parser = get_parser_for_platform("snowflake")
        assert isinstance(parser, SnowflakeQueryPlanParser)


class TestSnowflakeJoinConditionSorting:
    def _join_payload(self, expressions):
        return json.dumps(
            {
                "Operations": [
                    [
                        {"id": 0, "operation": "Result"},
                        {
                            "id": 1,
                            "operation": "InnerJoin",
                            "parentOperators": [0],
                            "expressions": expressions,
                        },
                        {
                            "id": 2,
                            "operation": "TableScan",
                            "parentOperators": [1],
                            "objects": ["DB.A"],
                        },
                        {
                            "id": 3,
                            "operation": "TableScan",
                            "parentOperators": [1],
                            "objects": ["DB.B"],
                        },
                    ]
                ]
            }
        )

    def test_join_condition_returns_list_of_stripped_strings(self, parser):
        conditions = SnowflakeQueryPlanParser._join_condition({"expressions": ["  a.id = b.id ", "a.type = b.type"]})
        assert conditions == ["a.id = b.id", "a.type = b.type"]

    def test_multi_condition_join_sets_list_directly(self, parser):
        dag = parser.parse_explain_output("q", self._join_payload(["a.id = b.id", "a.type = b.type"]))
        joins = [n for n in _collect(dag.logical_root) if n.operator_type == LogicalOperatorType.JOIN]
        assert len(joins) == 1
        assert joins[0].join_conditions == ["a.id = b.id", "a.type = b.type"]

    def test_multi_condition_join_fingerprint_order_invariant(self, parser):
        first = parser.parse_explain_output(
            "q", self._join_payload(["a.id = b.id", "a.type = b.type"])
        ).plan_fingerprint
        second = parser.parse_explain_output(
            "q", self._join_payload(["a.type = b.type", "a.id = b.id"])
        ).plan_fingerprint
        assert first == second


