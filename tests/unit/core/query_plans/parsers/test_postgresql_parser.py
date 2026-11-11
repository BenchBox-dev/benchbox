from __future__ import annotations

import json

import pytest

from benchbox.core.query_plans.parsers.postgresql import PostgreSQLQueryPlanParser
from benchbox.core.results.query_plan_models import JoinType, LogicalOperatorType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


SIMPLE_SEQ_SCAN = json.dumps(
    [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "orders",
                "Alias": "o",
                "Startup Cost": 0.00,
                "Total Cost": 15.50,
                "Plan Rows": 500,
                "Plan Width": 48,
            }
        }
    ]
)

SEQ_SCAN_WITH_FILTER = json.dumps(
    [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "lineitem",
                "Alias": "l",
                "Startup Cost": 0.00,
                "Total Cost": 254.00,
                "Plan Rows": 100,
                "Plan Width": 64,
                "Filter": "(l_shipdate <= '1998-12-01'::date)",
            }
        }
    ]
)

INDEX_SCAN = json.dumps(
    [
        {
            "Plan": {
                "Node Type": "Index Scan",
                "Index Name": "orders_pkey",
                "Relation Name": "orders",
                "Alias": "o",
                "Startup Cost": 0.29,
                "Total Cost": 8.30,
                "Plan Rows": 1,
                "Plan Width": 48,
                "Index Cond": "(o_orderkey = 123)",
            }
        }
    ]
)

SIMPLE_JOIN = json.dumps(
    [
        {
            "Plan": {
                "Node Type": "Hash Join",
                "Join Type": "Inner",
                "Startup Cost": 10.50,
                "Total Cost": 100.00,
                "Plan Rows": 200,
                "Plan Width": 80,
                "Hash Cond": "(o.o_custkey = c.c_custkey)",
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Relation Name": "orders",
                        "Alias": "o",
                        "Startup Cost": 0.00,
                        "Total Cost": 15.50,
                        "Plan Rows": 500,
                        "Plan Width": 48,
                    },
                    {
                        "Node Type": "Hash",
                        "Startup Cost": 10.00,
                        "Total Cost": 10.00,
                        "Plan Rows": 100,
                        "Plan Width": 32,
                        "Plans": [
                            {
                                "Node Type": "Seq Scan",
                                "Relation Name": "customer",
                                "Alias": "c",
                                "Startup Cost": 0.00,
                                "Total Cost": 10.00,
                                "Plan Rows": 100,
                                "Plan Width": 32,
                            }
                        ],
                    },
                ],
            }
        }
    ]
)

LEFT_JOIN = json.dumps(
    [
        {
            "Plan": {
                "Node Type": "Nested Loop",
                "Join Type": "Left",
                "Startup Cost": 0.00,
                "Total Cost": 50.00,
                "Plan Rows": 100,
                "Plan Width": 64,
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Relation Name": "orders",
                        "Alias": "o",
                        "Startup Cost": 0.00,
                        "Total Cost": 15.50,
                        "Plan Rows": 500,
                        "Plan Width": 48,
                    },
                    {
                        "Node Type": "Index Scan",
                        "Index Name": "lineitem_pkey",
                        "Relation Name": "lineitem",
                        "Alias": "l",
                        "Startup Cost": 0.00,
                        "Total Cost": 0.07,
                        "Plan Rows": 1,
                        "Plan Width": 16,
                    },
                ],
            }
        }
    ]
)

AGGREGATE_WITH_GROUP_BY = json.dumps(
    [
        {
            "Plan": {
                "Node Type": "HashAggregate",
                "Strategy": "Hashed",
                "Startup Cost": 50.00,
                "Total Cost": 55.00,
                "Plan Rows": 100,
                "Plan Width": 40,
                "Group Key": ["l_returnflag", "l_linestatus"],
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Relation Name": "lineitem",
                        "Alias": "l",
                        "Startup Cost": 0.00,
                        "Total Cost": 25.00,
                        "Plan Rows": 1000,
                        "Plan Width": 40,
                    }
                ],
            }
        }
    ]
)

SORT_WITH_LIMIT = json.dumps(
    [
        {
            "Plan": {
                "Node Type": "Limit",
                "Startup Cost": 100.00,
                "Total Cost": 100.25,
                "Plan Rows": 10,
                "Plan Width": 48,
                "Plans": [
                    {
                        "Node Type": "Sort",
                        "Startup Cost": 100.00,
                        "Total Cost": 105.00,
                        "Plan Rows": 500,
                        "Plan Width": 48,
                        "Sort Key": ["o_totalprice DESC", "o_orderdate ASC"],
                        "Plans": [
                            {
                                "Node Type": "Seq Scan",
                                "Relation Name": "orders",
                                "Alias": "o",
                                "Startup Cost": 0.00,
                                "Total Cost": 15.50,
                                "Plan Rows": 500,
                                "Plan Width": 48,
                            }
                        ],
                    }
                ],
            }
        }
    ]
)

WINDOW_FUNCTION = json.dumps(
    [
        {
            "Plan": {
                "Node Type": "WindowAgg",
                "Startup Cost": 50.00,
                "Total Cost": 75.00,
                "Plan Rows": 500,
                "Plan Width": 56,
                "Plans": [
                    {
                        "Node Type": "Sort",
                        "Startup Cost": 50.00,
                        "Total Cost": 52.50,
                        "Plan Rows": 500,
                        "Plan Width": 48,
                        "Sort Key": ["o_custkey"],
                        "Plans": [
                            {
                                "Node Type": "Seq Scan",
                                "Relation Name": "orders",
                                "Alias": "o",
                                "Startup Cost": 0.00,
                                "Total Cost": 15.50,
                                "Plan Rows": 500,
                                "Plan Width": 48,
                            }
                        ],
                    }
                ],
            }
        }
    ]
)


class TestPostgreSQLParser:
    @pytest.fixture
    def parser(self) -> PostgreSQLQueryPlanParser:
        return PostgreSQLQueryPlanParser()

    def test_parser_platform_name(self, parser: PostgreSQLQueryPlanParser) -> None:
        assert parser.platform_name == "postgresql"

    def test_parse_simple_seq_scan(self, parser: PostgreSQLQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q1", SIMPLE_SEQ_SCAN)

        assert plan is not None
        assert plan.query_id == "q1"
        assert plan.platform == "postgresql"
        assert plan.logical_root is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SCAN
        assert plan.logical_root.table_name == "orders"
        assert plan.estimated_cost == 15.50
        assert plan.estimated_rows == 500

    def test_parse_seq_scan_with_filter(self, parser: PostgreSQLQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q2", SEQ_SCAN_WITH_FILTER)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SCAN
        assert plan.logical_root.table_name == "lineitem"
        assert plan.logical_root.filter_expressions is not None
        assert len(plan.logical_root.filter_expressions) == 1
        assert "l_shipdate" in plan.logical_root.filter_expressions[0]

    def test_parse_index_scan(self, parser: PostgreSQLQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q3", INDEX_SCAN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SCAN
        assert plan.logical_root.table_name == "orders"
        assert plan.logical_root.physical_operator is not None
        assert plan.logical_root.physical_operator.operator_type == "Index Scan"

    def test_parse_hash_join(self, parser: PostgreSQLQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q4", SIMPLE_JOIN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.JOIN
        assert plan.logical_root.join_type == JoinType.INNER
        assert plan.logical_root.join_conditions is not None
        assert len(plan.logical_root.join_conditions) == 1
        assert "o_custkey" in plan.logical_root.join_conditions[0]

        assert len(plan.logical_root.children) == 2

        assert plan.logical_root.children[0].operator_type == LogicalOperatorType.SCAN
        assert plan.logical_root.children[0].table_name == "orders"

        assert plan.logical_root.children[1].operator_type == LogicalOperatorType.OTHER

    def test_parse_left_join(self, parser: PostgreSQLQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q5", LEFT_JOIN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.JOIN
        assert plan.logical_root.join_type == JoinType.LEFT
        assert len(plan.logical_root.children) == 2

    def test_parse_aggregate_with_group_by(self, parser: PostgreSQLQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q6", AGGREGATE_WITH_GROUP_BY)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.AGGREGATE
        assert plan.logical_root.group_by_keys is not None
        assert "l_returnflag" in plan.logical_root.group_by_keys
        assert "l_linestatus" in plan.logical_root.group_by_keys

        assert len(plan.logical_root.children) == 1
        assert plan.logical_root.children[0].operator_type == LogicalOperatorType.SCAN

    def test_parse_sort_with_limit(self, parser: PostgreSQLQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q7", SORT_WITH_LIMIT)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.LIMIT
        assert len(plan.logical_root.children) == 1

        sort_op = plan.logical_root.children[0]
        assert sort_op.operator_type == LogicalOperatorType.SORT
        assert sort_op.sort_keys is not None
        assert len(sort_op.sort_keys) == 2
        assert sort_op.sort_keys[0]["direction"] == "DESC"
        assert sort_op.sort_keys[1]["direction"] == "ASC"

    def test_parse_window_function(self, parser: PostgreSQLQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q8", WINDOW_FUNCTION)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.WINDOW

    def test_empty_output_raises_error(self, parser: PostgreSQLQueryPlanParser) -> None:
        result = parser.parse_explain_output("q9", "")
        assert result is None

    def test_invalid_json_raises_error(self, parser: PostgreSQLQueryPlanParser) -> None:
        result = parser.parse_explain_output("q10", "not valid json")
        assert result is None

    def test_missing_plan_key_raises_error(self, parser: PostgreSQLQueryPlanParser) -> None:
        result = parser.parse_explain_output("q11", json.dumps([{"NotPlan": {}}]))
        assert result is None

    def test_fingerprint_is_computed(self, parser: PostgreSQLQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q12", SIMPLE_SEQ_SCAN)

        assert plan is not None
        assert plan.plan_fingerprint is not None
        assert len(plan.plan_fingerprint) == 64

    def test_operator_id_counter_resets_per_parse(self, parser: PostgreSQLQueryPlanParser) -> None:
        plan1 = parser.parse_explain_output("q1", SIMPLE_SEQ_SCAN)
        plan2 = parser.parse_explain_output("q2", SIMPLE_SEQ_SCAN)

        assert plan1 is not None
        assert plan2 is not None

        assert plan1.logical_root.operator_id == plan2.logical_root.operator_id

    def test_serialization_round_trip(self, parser: PostgreSQLQueryPlanParser) -> None:
        from benchbox.core.results.query_plan_models import QueryPlanDAG

        plan = parser.parse_explain_output("q13", SIMPLE_JOIN)
        assert plan is not None

        json_str = plan.to_json()

        restored = QueryPlanDAG.from_json(json_str)

        assert restored.query_id == plan.query_id
        assert restored.platform == plan.platform
        assert restored.plan_fingerprint == plan.plan_fingerprint


