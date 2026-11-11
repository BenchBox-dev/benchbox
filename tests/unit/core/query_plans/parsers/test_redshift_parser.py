from __future__ import annotations

import pytest

from benchbox.core.query_plans.parsers.redshift import RedshiftQueryPlanParser
from benchbox.core.results.query_plan_models import JoinType, LogicalOperatorType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


SIMPLE_SEQ_SCAN = """
XN Seq Scan on orders  (cost=0.00..15.50 rows=500 width=48)
"""

SEQ_SCAN_WITH_FILTER = """
XN Seq Scan on lineitem  (cost=0.00..254.00 rows=100 width=64)
  Filter: (l_shipdate <= '1998-12-01'::date)
"""

SIMPLE_LIMIT = """
XN Limit  (cost=0.00..0.05 rows=10 width=48)
  ->  XN Seq Scan on orders  (cost=0.00..15.50 rows=500 width=48)
"""

AGGREGATE_WITH_SCAN = """
XN HashAggregate  (cost=50.00..55.00 rows=100 width=40)
  ->  XN Seq Scan on lineitem  (cost=0.00..25.00 rows=1000 width=40)
"""

SIMPLE_HASH_JOIN = """
XN Hash Join DS_DIST_INNER  (cost=10.50..100.00 rows=200 width=80)
  Hash Cond: (o.o_custkey = c.c_custkey)
  ->  XN Seq Scan on orders o  (cost=0.00..15.50 rows=500 width=48)
  ->  XN Hash  (cost=10.00..10.00 rows=100 width=32)
        ->  XN Seq Scan on customer c  (cost=0.00..10.00 rows=100 width=32)
"""

LEFT_JOIN = """
XN Nested Loop Left  (cost=0.00..50.00 rows=100 width=64)
  ->  XN Seq Scan on orders o  (cost=0.00..15.50 rows=500 width=48)
  ->  XN Seq Scan on lineitem l  (cost=0.00..0.07 rows=1 width=16)
"""

SORT_WITH_LIMIT = """
XN Limit  (cost=100.00..100.25 rows=10 width=48)
  ->  XN Sort  (cost=100.00..105.00 rows=500 width=48)
        Sort Key: o_totalprice
        ->  XN Seq Scan on orders o  (cost=0.00..15.50 rows=500 width=48)
"""

COMPLEX_TPC_LIKE = """
XN Limit  (cost=1000000000000.00..1000000000000.00 rows=1 width=0)
  ->  XN Merge  (cost=1000000000000.00..1000000000000.00 rows=1 width=0)
        Merge Key: c_custkey
        ->  XN Network  (cost=1000000000000.00..1000000000000.00 rows=1 width=0)
              Send to leader
              ->  XN Sort  (cost=1000000000000.00..1000000000000.00 rows=1 width=0)
                    Sort Key: c_custkey
                    ->  XN HashAggregate  (cost=1000000000000.00..1000000000000.00 rows=1 width=0)
                          ->  XN Hash Join DS_DIST_ALL_INNER  (cost=0.00..0.00 rows=1 width=0)
                                Inner Dist Key: l_orderkey
                                Hash Cond: ("outer".o_orderkey = "inner".l_orderkey)
                                ->  XN Hash Join DS_DIST_INNER  (cost=0.00..0.00 rows=1 width=0)
                                      Inner Dist Key: c_custkey
                                      Hash Cond: ("outer".o_custkey = "inner".c_custkey)
                                      ->  XN Seq Scan on orders o  (cost=0.00..0.00 rows=1 width=0)
                                      ->  XN Hash  (cost=0.00..0.00 rows=1 width=0)
                                            ->  XN Seq Scan on customer c  (cost=0.00..0.00 rows=1 width=0)
                                ->  XN Hash  (cost=0.00..0.00 rows=1 width=0)
                                      ->  XN Seq Scan on lineitem l  (cost=0.00..0.00 rows=1 width=0)
"""


class TestRedshiftParser:
    @pytest.fixture
    def parser(self) -> RedshiftQueryPlanParser:
        return RedshiftQueryPlanParser()

    def test_parser_platform_name(self, parser: RedshiftQueryPlanParser) -> None:
        assert parser.platform_name == "redshift"

    def test_parse_simple_seq_scan(self, parser: RedshiftQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q1", SIMPLE_SEQ_SCAN)

        assert plan is not None
        assert plan.query_id == "q1"
        assert plan.platform == "redshift"
        assert plan.logical_root is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SCAN
        assert plan.logical_root.table_name == "orders"
        assert plan.estimated_cost == 15.50
        assert plan.estimated_rows == 500

    def test_parse_seq_scan_with_filter(self, parser: RedshiftQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q2", SEQ_SCAN_WITH_FILTER)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.SCAN
        assert plan.logical_root.table_name == "lineitem"
        assert plan.logical_root.filter_expressions is not None
        assert len(plan.logical_root.filter_expressions) == 1
        assert "l_shipdate" in plan.logical_root.filter_expressions[0]

    def test_parse_limit_with_child(self, parser: RedshiftQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q3", SIMPLE_LIMIT)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.LIMIT
        assert len(plan.logical_root.children) == 1
        assert plan.logical_root.children[0].operator_type == LogicalOperatorType.SCAN

    def test_parse_aggregate(self, parser: RedshiftQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q4", AGGREGATE_WITH_SCAN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.AGGREGATE
        assert len(plan.logical_root.children) == 1
        assert plan.logical_root.children[0].operator_type == LogicalOperatorType.SCAN

    def test_parse_hash_join(self, parser: RedshiftQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q5", SIMPLE_HASH_JOIN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.JOIN
        assert plan.logical_root.join_type == JoinType.INNER
        assert plan.logical_root.join_conditions is not None
        assert len(plan.logical_root.join_conditions) == 1
        assert "o_custkey" in plan.logical_root.join_conditions[0]

    def test_parse_left_join(self, parser: RedshiftQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q6", LEFT_JOIN)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.JOIN
        assert plan.logical_root.join_type == JoinType.LEFT
        assert len(plan.logical_root.children) == 2

    def test_parse_sort_with_limit(self, parser: RedshiftQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q7", SORT_WITH_LIMIT)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.LIMIT
        assert len(plan.logical_root.children) == 1

        sort_op = plan.logical_root.children[0]
        assert sort_op.operator_type == LogicalOperatorType.SORT

    def test_parse_complex_plan(self, parser: RedshiftQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q8", COMPLEX_TPC_LIKE)

        assert plan is not None
        assert plan.logical_root.operator_type == LogicalOperatorType.LIMIT

        assert len(plan.logical_root.children) > 0

    def test_empty_output_raises_error(self, parser: RedshiftQueryPlanParser) -> None:
        result = parser.parse_explain_output("q9", "")
        assert result is None

    def test_fingerprint_is_computed(self, parser: RedshiftQueryPlanParser) -> None:
        plan = parser.parse_explain_output("q10", SIMPLE_SEQ_SCAN)

        assert plan is not None
        assert plan.plan_fingerprint is not None
        assert len(plan.plan_fingerprint) == 64

    def test_operator_id_counter_resets_per_parse(self, parser: RedshiftQueryPlanParser) -> None:
        plan1 = parser.parse_explain_output("q1", SIMPLE_SEQ_SCAN)
        plan2 = parser.parse_explain_output("q2", SIMPLE_SEQ_SCAN)

        assert plan1 is not None
        assert plan2 is not None

        assert plan1.logical_root.operator_id == plan2.logical_root.operator_id

    def test_serialization_round_trip(self, parser: RedshiftQueryPlanParser) -> None:
        from benchbox.core.results.query_plan_models import QueryPlanDAG

        plan = parser.parse_explain_output("q11", SIMPLE_HASH_JOIN)
        assert plan is not None

        json_str = plan.to_json()

        restored = QueryPlanDAG.from_json(json_str)

        assert restored.query_id == plan.query_id
        assert restored.platform == plan.platform
        assert restored.plan_fingerprint == plan.plan_fingerprint


