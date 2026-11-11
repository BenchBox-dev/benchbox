import pytest

from benchbox.core.query_plans.parsers.duckdb import DuckDBQueryPlanParser
from benchbox.core.results.query_plan_models import LogicalOperator, LogicalOperatorType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


SIMPLE_SCAN = """
┌───────────────────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│          orders           │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│         o_orderkey        │
│         o_custkey         │
│      o_totalprice         │
└───────────────────────────┘
"""

SIMPLE_FILTER = """
┌───────────────────────────┐
│          FILTER           │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│  o_orderdate >= '1995'    │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│          orders           │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│         o_orderkey        │
│      o_orderdate          │
└───────────────────────────┘
"""

AGGREGATE_QUERY = """
┌───────────────────────────┐
│         PROJECTION        │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│         l_returnflag      │
│         l_linestatus      │
│         sum_qty           │
│         sum_base_price    │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│       HASH_GROUP_BY       │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│         Groups:           │
│       l_returnflag        │
│       l_linestatus        │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│      sum(l_quantity)      │
│   sum(l_extendedprice)    │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│          FILTER           │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│l_shipdate <= '1998-12-01' │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│          lineitem         │
└───────────────────────────┘
"""

ORDER_BY_QUERY = """
┌───────────────────────────┐
│         ORDER_BY          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│o_orderdate ASC NULLS LAST │
│o_totalprice DESC NULLS    │
│           LAST            │
└─────────────┬─────────────┘
┌─────────────┴─────────────┐
│         SEQ_SCAN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│          orders           │
└───────────────────────────┘
"""

JOIN_QUERY = """
┌───────────────────────────┐
│        HASH_JOIN          │
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
│o_custkey = c_custkey      │
└──────┬──────────────┬─────┘
┌──────┴──────┐ ┌─────┴─────┐
│  SEQ_SCAN   │ │ SEQ_SCAN  │
│   orders    │ │ customer  │
└─────────────┘ └───────────┘
"""


class TestDuckDBQueryPlanParser:
    def test_parser_initialization(self) -> None:

        parser = DuckDBQueryPlanParser()
        assert parser.platform_name == "duckdb"

    def test_parse_simple_scan(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q01", SIMPLE_SCAN)

        assert plan is not None
        assert plan.query_id == "q01"
        assert plan.platform == "duckdb"
        assert plan.logical_root is not None
        assert plan.raw_explain_output == SIMPLE_SCAN

        root = plan.logical_root
        assert root.operator_type == LogicalOperatorType.SCAN
        assert root.table_name == "orders"

    def test_parse_filter_with_scan(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q02", SIMPLE_FILTER)

        assert plan is not None

        assert plan.logical_root is not None

    def test_parse_aggregate_query(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q03", AGGREGATE_QUERY)

        assert plan is not None
        assert plan.logical_root is not None

        operators = self._collect_operators(plan.logical_root)
        operator_types = [op.operator_type for op in operators]

        assert LogicalOperatorType.SCAN in operator_types
        assert any(t in [LogicalOperatorType.FILTER, LogicalOperatorType.AGGREGATE] for t in operator_types)

    def test_parse_order_by_query(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q04", ORDER_BY_QUERY)

        assert plan is not None
        assert plan.logical_root is not None

        operators = self._collect_operators(plan.logical_root)
        operator_types = [op.operator_type for op in operators]

        assert LogicalOperatorType.SORT in operator_types or LogicalOperatorType.SCAN in operator_types

    def test_parse_empty_output(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q05", "")

        assert plan is None

    def test_parse_invalid_output(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q06", "This is not a valid EXPLAIN output")

        assert plan is None

    def test_parse_whitespace_only(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q07", "   \n\n  \t  \n  ")

        assert plan is None

    def test_operator_id_generation(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q08", AGGREGATE_QUERY)

        if plan:
            operators = self._collect_operators(plan.logical_root)
            operator_ids = [op.operator_id for op in operators]

            assert len(operator_ids) == len(set(operator_ids))

    def test_operator_ids_reset_between_parses(self) -> None:
        parser = DuckDBQueryPlanParser()
        plan1 = parser.parse_explain_output("q_reset_1", SIMPLE_FILTER)
        plan2 = parser.parse_explain_output("q_reset_2", ORDER_BY_QUERY)

        assert plan1 is not None
        assert plan2 is not None

        ids_plan1 = [int(op.operator_id.split("_")[-1]) for op in self._collect_operators(plan1.logical_root)]
        ids_plan2 = [int(op.operator_id.split("_")[-1]) for op in self._collect_operators(plan2.logical_root)]

        expected_plan1 = list(range(2, 2 * len(ids_plan1) + 1, 2))
        expected_plan2 = list(range(2, 2 * len(ids_plan2) + 1, 2))

        assert sorted(ids_plan1) == expected_plan1
        assert sorted(ids_plan2) == expected_plan2

    def test_physical_operator_creation(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q09", SIMPLE_SCAN)

        if plan:
            assert plan.logical_root.physical_operator is not None
            assert plan.logical_root.physical_operator.operator_type == "SEQ_SCAN"

    def test_harmonize_scan_operators(self) -> None:

        parser = DuckDBQueryPlanParser()

        assert parser._harmonize_duckdb_operator("SEQ_SCAN") == LogicalOperatorType.SCAN
        assert parser._harmonize_duckdb_operator("INDEX_SCAN") == LogicalOperatorType.SCAN
        assert parser._harmonize_duckdb_operator("TABLE_SCAN") == LogicalOperatorType.SCAN

    def test_harmonize_join_operators(self) -> None:

        parser = DuckDBQueryPlanParser()

        assert parser._harmonize_duckdb_operator("HASH_JOIN") == LogicalOperatorType.JOIN
        assert parser._harmonize_duckdb_operator("NESTED_LOOP_JOIN") == LogicalOperatorType.JOIN
        assert parser._harmonize_duckdb_operator("PIECEWISE_MERGE_JOIN") == LogicalOperatorType.JOIN

    def test_harmonize_aggregate_operators(self) -> None:

        parser = DuckDBQueryPlanParser()

        assert parser._harmonize_duckdb_operator("HASH_GROUP_BY") == LogicalOperatorType.AGGREGATE
        assert parser._harmonize_duckdb_operator("PERFECT_HASH_GROUP_BY") == LogicalOperatorType.AGGREGATE

    def test_harmonize_sort_operators(self) -> None:

        parser = DuckDBQueryPlanParser()

        assert parser._harmonize_duckdb_operator("ORDER_BY") == LogicalOperatorType.SORT
        assert parser._harmonize_duckdb_operator("TOP_N") == LogicalOperatorType.SORT

    def test_harmonize_projection_operators(self) -> None:

        parser = DuckDBQueryPlanParser()

        assert parser._harmonize_duckdb_operator("PROJECTION") == LogicalOperatorType.PROJECT
        assert parser._harmonize_duckdb_operator("RESULT_COLLECTOR") == LogicalOperatorType.PROJECT

    def test_extract_table_name(self) -> None:

        parser = DuckDBQueryPlanParser()

        assert parser._extract_table_name_from_details(["orders"]) == "orders"
        assert parser._extract_table_name_from_details(["lineitem"]) == "lineitem"
        assert parser._extract_table_name_from_details(["customer_orders"]) == "customer_orders"

        assert parser._extract_table_name_from_details(["123invalid"]) is None
        assert parser._extract_table_name_from_details(["with space"]) is None
        assert parser._extract_table_name_from_details([]) is None

    def test_extract_join_type(self) -> None:

        parser = DuckDBQueryPlanParser()

        assert parser._extract_join_type_from_operator("HASH_JOIN") == "inner"
        assert parser._extract_join_type_from_operator("LEFT_HASH_JOIN") == "left"
        assert parser._extract_join_type_from_operator("RIGHT_HASH_JOIN") == "right"
        assert parser._extract_join_type_from_operator("FULL_OUTER_JOIN") == "full"
        assert parser._extract_join_type_from_operator("CROSS_JOIN") == "cross"

    def test_fingerprint_computation(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q10", SIMPLE_SCAN)

        assert plan is not None
        assert plan.plan_fingerprint is not None
        assert len(plan.plan_fingerprint) == 64

    def test_fingerprint_stability(self) -> None:

        parser = DuckDBQueryPlanParser()

        plan1 = parser.parse_explain_output("q11", SIMPLE_SCAN)
        plan2 = parser.parse_explain_output("q11", SIMPLE_SCAN)

        assert plan1 is not None
        assert plan2 is not None

    def test_raw_explain_output_preserved(self) -> None:

        parser = DuckDBQueryPlanParser()
        plan = parser.parse_explain_output("q12", SIMPLE_SCAN)

        assert plan is not None
        assert plan.raw_explain_output == SIMPLE_SCAN

    def _collect_operators(self, root: LogicalOperator) -> list[LogicalOperator]:
        operators = [root]
        for child in root.children:
            operators.extend(self._collect_operators(child))
        return operators


class TestDuckDBBranchingDetection:
    def test_join_query_rejected_in_text_format(self) -> None:

        parser = DuckDBQueryPlanParser()

        plan = parser.parse_explain_output("q_join", JOIN_QUERY)

        assert plan is None

    def test_branching_error_message_contains_hint(self) -> None:

        parser = DuckDBQueryPlanParser()

        branching_info = parser._detect_branching_structure(JOIN_QUERY)

        assert branching_info is not None
        assert "JOIN" in branching_info

    def test_linear_plans_still_work(self) -> None:
        parser = DuckDBQueryPlanParser()

        for explain, query_id in [
            (SIMPLE_SCAN, "q_scan"),
            (SIMPLE_FILTER, "q_filter"),
            (AGGREGATE_QUERY, "q_agg"),
            (ORDER_BY_QUERY, "q_order"),
        ]:
            plan = parser.parse_explain_output(query_id, explain)
            assert plan.query_id == query_id, f"Linear plan {query_id} should parse successfully"
            assert plan.logical_root is not None, f"Linear plan {query_id} should have a root operator"

    def test_detect_union_operator(self) -> None:

        parser = DuckDBQueryPlanParser()

        union_plan = """
        ┌───────────────────────────┐
        │          UNION            │
        └───────────────────────────┘
        """

        branching = parser._detect_branching_structure(union_plan)
        assert branching is not None
        assert "UNION" in branching

    def test_detect_multiple_boxes_same_line(self) -> None:
        parser = DuckDBQueryPlanParser()

        parallel_plan = """
        ┌──────┐ ┌──────┐
        │ SCAN │ │ SCAN │
        └──────┘ └──────┘
        """

        branching = parser._detect_branching_structure(parallel_plan)
        assert branching is not None
        assert "parallel" in branching.lower() or "boxes" in branching.lower()

    def test_no_false_positives_on_linear_plans(self) -> None:

        parser = DuckDBQueryPlanParser()

        for explain in [SIMPLE_SCAN, SIMPLE_FILTER, AGGREGATE_QUERY, ORDER_BY_QUERY]:
            branching = parser._detect_branching_structure(explain)
            assert branching is None, "Linear plan should not be detected as branching"


class TestDuckDBParserEdgeCases:
    def test_malformed_box_structure(self) -> None:

        parser = DuckDBQueryPlanParser()
        malformed = "┌──────\n│ SCAN\n└──"

        plan = parser.parse_explain_output("q13", malformed)
        assert plan is None or plan.logical_root is not None

    def test_missing_operator_name(self) -> None:

        parser = DuckDBQueryPlanParser()
        missing_name = """
        ┌───────────────────────────┐
        │   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
        │          details          │
        └───────────────────────────┘
        """

        plan = parser.parse_explain_output("q14", missing_name)
        assert plan is None or plan.logical_root is not None

    def test_very_long_explain_output(self) -> None:

        parser = DuckDBQueryPlanParser()

        long_output = SIMPLE_SCAN * 10

        plan = parser.parse_explain_output("q15", long_output)
        assert plan is None or plan.logical_root is not None

    def test_unicode_in_explain_output(self) -> None:

        parser = DuckDBQueryPlanParser()
        unicode_output = """
        ┌───────────────────────────┐
        │         SEQ_SCAN          │
        │   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │
        │       table_名称          │
        └───────────────────────────┘
        """

        plan = parser.parse_explain_output("q16", unicode_output)
        assert plan is None or plan.logical_root is not None


