import pytest

from benchbox.core.query_plans.comparison import (
    QueryPlanComparator,
    compare_query_plans,
)
from benchbox.core.results.query_plan_models import (
    JoinType,
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQueryPlanComparison:
    @pytest.fixture
    def simple_scan_plan(self) -> QueryPlanDAG:
        root = LogicalOperator(
            operator_id="scan_1",
            operator_type=LogicalOperatorType.SCAN,
            table_name="orders",
        )
        return QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=root,
        )

    @pytest.fixture
    def simple_scan_plan_different_table(self) -> QueryPlanDAG:
        root = LogicalOperator(
            operator_id="scan_1",
            operator_type=LogicalOperatorType.SCAN,
            table_name="customers",
        )
        return QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=root,
        )

    @pytest.fixture
    def join_plan(self) -> QueryPlanDAG:
        scan_left = LogicalOperator(
            operator_id="scan_1",
            operator_type=LogicalOperatorType.SCAN,
            table_name="orders",
        )
        scan_right = LogicalOperator(
            operator_id="scan_2",
            operator_type=LogicalOperatorType.SCAN,
            table_name="customers",
        )
        join = LogicalOperator(
            operator_id="join_1",
            operator_type=LogicalOperatorType.JOIN,
            join_type=JoinType.INNER,
            children=[scan_left, scan_right],
        )
        return QueryPlanDAG(
            query_id="q3",
            platform="duckdb",
            logical_root=join,
        )

    @pytest.fixture
    def join_plan_different_type(self) -> QueryPlanDAG:
        scan_left = LogicalOperator(
            operator_id="scan_1",
            operator_type=LogicalOperatorType.SCAN,
            table_name="orders",
        )
        scan_right = LogicalOperator(
            operator_id="scan_2",
            operator_type=LogicalOperatorType.SCAN,
            table_name="customers",
        )
        join = LogicalOperator(
            operator_id="join_1",
            operator_type=LogicalOperatorType.JOIN,
            join_type=JoinType.LEFT,
            children=[scan_left, scan_right],
        )
        return QueryPlanDAG(
            query_id="q4",
            platform="duckdb",
            logical_root=join,
        )

    @pytest.fixture
    def complex_plan(self) -> QueryPlanDAG:
        scan_left = LogicalOperator(
            operator_id="scan_1",
            operator_type=LogicalOperatorType.SCAN,
            table_name="orders",
        )
        filter_op = LogicalOperator(
            operator_id="filter_1",
            operator_type=LogicalOperatorType.FILTER,
            filter_expressions=["o_orderdate > '2023-01-01'"],
            children=[scan_left],
        )
        scan_right = LogicalOperator(
            operator_id="scan_2",
            operator_type=LogicalOperatorType.SCAN,
            table_name="customers",
        )
        join = LogicalOperator(
            operator_id="join_1",
            operator_type=LogicalOperatorType.JOIN,
            join_type=JoinType.INNER,
            children=[filter_op, scan_right],
        )
        aggregate = LogicalOperator(
            operator_id="agg_1",
            operator_type=LogicalOperatorType.AGGREGATE,
            aggregation_functions=["SUM(o_totalprice)"],
            children=[join],
        )
        return QueryPlanDAG(
            query_id="q5",
            platform="duckdb",
            logical_root=aggregate,
        )

    def test_identical_plans(self, simple_scan_plan):

        result = compare_query_plans(simple_scan_plan, simple_scan_plan)

        assert result.plans_identical is True
        assert result.fingerprints_match is True
        assert result.similarity.overall_similarity == 1.0
        assert result.similarity.matching_operators > 0
        assert result.similarity.type_mismatches == 0
        assert result.similarity.property_mismatches == 0

    def test_different_table_name(self, simple_scan_plan, simple_scan_plan_different_table):

        result = compare_query_plans(simple_scan_plan, simple_scan_plan_different_table)

        assert result.plans_identical is False
        assert result.fingerprints_match is False
        assert result.similarity.property_mismatches > 0
        assert result.similarity.overall_similarity < 1.0

        property_diffs = [d for d in result.operator_diffs if d.diff_type == "property_mismatch"]
        assert len(property_diffs) > 0
        assert "table_name" in property_diffs[0].differences

    def test_different_join_type(self, join_plan, join_plan_different_type):

        result = compare_query_plans(join_plan, join_plan_different_type)

        assert result.plans_identical is False
        assert result.similarity.property_mismatches > 0

        property_diffs = [d for d in result.operator_diffs if d.diff_type == "property_mismatch"]
        assert len(property_diffs) > 0
        assert "join_type" in property_diffs[0].differences

    def test_different_operator_types(self, simple_scan_plan, join_plan):

        result = compare_query_plans(simple_scan_plan, join_plan)

        assert result.plans_identical is False
        assert result.similarity.type_mismatches > 0
        assert result.similarity.overall_similarity < 0.5

    def test_complex_plan_comparison(self, complex_plan):

        result = compare_query_plans(complex_plan, complex_plan)

        assert result.plans_identical is True
        assert result.similarity.overall_similarity == 1.0
        assert result.similarity.total_operators_left > 3

    def test_similarity_score_calculation(self, join_plan, join_plan_different_type):

        result = compare_query_plans(join_plan, join_plan_different_type)

        assert result.similarity.structural_similarity == 1.0
        assert result.similarity.operator_similarity == 1.0
        assert result.similarity.property_similarity < 1.0
        assert result.similarity.property_mismatches == 1

    def test_structure_mismatch(self, simple_scan_plan, complex_plan):

        result = compare_query_plans(simple_scan_plan, complex_plan)

        assert result.plans_identical is False
        assert result.similarity.structure_mismatches > 0
        assert result.similarity.overall_similarity < 0.5

    def test_filter_expression_comparison(self):

        scan1 = LogicalOperator(
            operator_id="scan_1",
            operator_type=LogicalOperatorType.SCAN,
            table_name="orders",
        )
        filter1 = LogicalOperator(
            operator_id="filter_1",
            operator_type=LogicalOperatorType.FILTER,
            filter_expressions=["o_orderdate > '2023-01-01'"],
            children=[scan1],
        )
        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=filter1,
        )

        scan2 = LogicalOperator(
            operator_id="scan_1",
            operator_type=LogicalOperatorType.SCAN,
            table_name="orders",
        )
        filter2 = LogicalOperator(
            operator_id="filter_1",
            operator_type=LogicalOperatorType.FILTER,
            filter_expressions=["o_orderdate > '2024-01-01'"],
            children=[scan2],
        )
        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=filter2,
        )

        result = compare_query_plans(plan1, plan2)

        assert result.plans_identical is False
        property_diffs = [d for d in result.operator_diffs if d.diff_type == "property_mismatch"]
        assert len(property_diffs) > 0
        assert "filter_expressions" in property_diffs[0].differences

    def test_aggregation_function_comparison(self):

        scan1 = LogicalOperator(
            operator_id="scan_1",
            operator_type=LogicalOperatorType.SCAN,
            table_name="orders",
        )
        agg1 = LogicalOperator(
            operator_id="agg_1",
            operator_type=LogicalOperatorType.AGGREGATE,
            aggregation_functions=["SUM(o_totalprice)"],
            children=[scan1],
        )
        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=agg1,
        )

        scan2 = LogicalOperator(
            operator_id="scan_1",
            operator_type=LogicalOperatorType.SCAN,
            table_name="orders",
        )
        agg2 = LogicalOperator(
            operator_id="agg_1",
            operator_type=LogicalOperatorType.AGGREGATE,
            aggregation_functions=["AVG(o_totalprice)"],
            children=[scan2],
        )
        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=agg2,
        )

        result = compare_query_plans(plan1, plan2)

        assert result.plans_identical is False
        property_diffs = [d for d in result.operator_diffs if d.diff_type == "property_mismatch"]
        assert len(property_diffs) > 0
        assert "aggregation_functions" in property_diffs[0].differences

    def test_summary_generation(self, simple_scan_plan, join_plan):

        result = compare_query_plans(simple_scan_plan, join_plan)

        assert result.summary
        assert isinstance(result.summary, str)
        assert len(result.summary) > 0

    def test_comparator_reusable(self, simple_scan_plan, join_plan):

        comparator = QueryPlanComparator()

        result1 = comparator.compare_plans(simple_scan_plan, simple_scan_plan)
        result2 = comparator.compare_plans(join_plan, join_plan)

        assert result1.plans_identical is True
        assert result2.plans_identical is True

    def test_cross_platform_comparison(self):

        duckdb_plan = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )

        sqlite_plan = QueryPlanDAG(
            query_id="q1",
            platform="sqlite",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )

        result = compare_query_plans(duckdb_plan, sqlite_plan)

        assert result.similarity.overall_similarity == 1.0

    def test_empty_children_handling(self):

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
                children=None,
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
                children=[],
            ),
        )

        result = compare_query_plans(plan1, plan2)

        assert result.similarity.overall_similarity == 1.0

    def test_unequal_children_count(self):

        join1 = LogicalOperator(
            operator_id="join_1",
            operator_type=LogicalOperatorType.JOIN,
            join_type=JoinType.INNER,
            children=[
                LogicalOperator(
                    operator_id="scan_1",
                    operator_type=LogicalOperatorType.SCAN,
                    table_name="orders",
                ),
                LogicalOperator(
                    operator_id="scan_2",
                    operator_type=LogicalOperatorType.SCAN,
                    table_name="customers",
                ),
            ],
        )
        plan1 = QueryPlanDAG(query_id="q1", platform="duckdb", logical_root=join1)

        join2 = LogicalOperator(
            operator_id="join_1",
            operator_type=LogicalOperatorType.JOIN,
            join_type=JoinType.INNER,
            children=[
                LogicalOperator(
                    operator_id="scan_1",
                    operator_type=LogicalOperatorType.SCAN,
                    table_name="orders",
                ),
            ],
        )
        plan2 = QueryPlanDAG(query_id="q2", platform="duckdb", logical_root=join2)

        result = compare_query_plans(plan1, plan2)

        assert result.plans_identical is False
        assert result.similarity.structure_mismatches > 0


class TestFingerprintIntegrityInComparison:
    def test_identical_comparison_requires_trusted_fingerprints(self):

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )
        plan2 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )

        assert plan1.is_fingerprint_trusted()
        assert plan2.is_fingerprint_trusted()
        result = compare_query_plans(plan1, plan2)
        assert result.plans_identical is True
        assert result.fingerprints_match is True

    def test_stale_fingerprint_forces_full_comparison(self):

        from benchbox.core.results.query_plan_models import FingerprintIntegrity

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )
        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )

        plan1.fingerprint_integrity = FingerprintIntegrity.STALE

        result = compare_query_plans(plan1, plan2)
        assert result.similarity.overall_similarity == 1.0

    def test_unverified_fingerprint_forces_full_comparison(self):

        from benchbox.core.results.query_plan_models import FingerprintIntegrity

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )
        plan1.fingerprint_integrity = FingerprintIntegrity.UNVERIFIED

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )

        plan1.plan_fingerprint = plan2.plan_fingerprint

        result = compare_query_plans(plan1, plan2)
        assert result.similarity.overall_similarity == 1.0


class TestFingerprintVersionMismatchInComparison:
    def _plan(self, table: str = "orders") -> QueryPlanDAG:
        return QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1", operator_type=LogicalOperatorType.SCAN, table_name=table
            ),
        )

    def test_version_mismatch_falls_back_to_tree_walk_even_if_fingerprints_equal(self):
        plan1 = self._plan()
        plan2 = self._plan()
        plan2.plan_fingerprint = plan1.plan_fingerprint
        plan1.fingerprint_version = 2
        plan2.fingerprint_version = 1
        assert plan1.is_fingerprint_trusted() and plan2.is_fingerprint_trusted()

        result = compare_query_plans(plan1, plan2)

        assert result.plans_identical is False
        assert result.fingerprints_match is False
        assert result.similarity.overall_similarity == 1.0

    def test_same_version_trusted_equal_fingerprints_uses_fast_path(self):
        plan1 = self._plan()
        plan2 = self._plan()
        assert plan1.fingerprint_version == plan2.fingerprint_version

        result = compare_query_plans(plan1, plan2)

        assert result.plans_identical is True
        assert result.fingerprints_match is True


class TestStringOperatorTypeHandling:
    def test_compare_plans_with_string_operator_types(self):

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="custom_1",
                operator_type="CustomScan",
                table_name="orders",
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="custom_1",
                operator_type="CustomScan",
                table_name="orders",
            ),
        )

        result = compare_query_plans(plan1, plan2)

        assert result.fingerprints_match is True
        assert result.plans_identical is True
        assert result.similarity.overall_similarity == 1.0

    def test_compare_mixed_enum_and_string_types(self):

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type="Scan",
                table_name="orders",
            ),
        )

        result = compare_query_plans(plan1, plan2)

        assert result.plans_identical is True
        assert result.similarity.overall_similarity == 1.0

    def test_compare_different_string_operator_types(self):

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="op_1",
                operator_type="CustomScan",
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="op_1",
                operator_type="IndexScan",
            ),
        )

        result = compare_query_plans(plan1, plan2)

        assert result.plans_identical is False
        assert result.similarity.type_mismatches > 0

        type_diffs = [d for d in result.operator_diffs if d.diff_type == "type_mismatch"]
        assert len(type_diffs) > 0
        assert type_diffs[0].differences["left_type"] == "CustomScan"
        assert type_diffs[0].differences["right_type"] == "IndexScan"

    def test_compare_with_string_join_type(self):

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="join_1",
                operator_type=LogicalOperatorType.JOIN,
                join_type="custom_join",
                children=[
                    LogicalOperator(
                        operator_id="scan_1",
                        operator_type=LogicalOperatorType.SCAN,
                        table_name="orders",
                    ),
                    LogicalOperator(
                        operator_id="scan_2",
                        operator_type=LogicalOperatorType.SCAN,
                        table_name="customers",
                    ),
                ],
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="join_1",
                operator_type=LogicalOperatorType.JOIN,
                join_type="different_join",
                children=[
                    LogicalOperator(
                        operator_id="scan_1",
                        operator_type=LogicalOperatorType.SCAN,
                        table_name="orders",
                    ),
                    LogicalOperator(
                        operator_id="scan_2",
                        operator_type=LogicalOperatorType.SCAN,
                        table_name="customers",
                    ),
                ],
            ),
        )

        result = compare_query_plans(plan1, plan2)

        assert result.plans_identical is False
        property_diffs = [d for d in result.operator_diffs if d.diff_type == "property_mismatch"]
        assert len(property_diffs) > 0
        assert "join_type" in property_diffs[0].differences

    def test_complex_plan_with_mixed_operator_types(self):

        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="agg_1",
                operator_type=LogicalOperatorType.AGGREGATE,
                aggregation_functions=["SUM(total)"],
                children=[
                    LogicalOperator(
                        operator_id="custom_1",
                        operator_type="ParallelHashJoin",
                        children=[
                            LogicalOperator(
                                operator_id="scan_1",
                                operator_type=LogicalOperatorType.SCAN,
                                table_name="orders",
                            ),
                            LogicalOperator(
                                operator_id="scan_2",
                                operator_type="IndexSeek",
                                table_name="customers",
                            ),
                        ],
                    ),
                ],
            ),
        )

        result = compare_query_plans(plan1, plan1)

        assert result.plans_identical is True
        assert result.similarity.overall_similarity == 1.0


def _leaf_chain(leaf_table: str, depth: int = 3) -> QueryPlanDAG:
    root = None
    for level in reversed(range(depth)):
        leaf = root is None
        root = LogicalOperator(
            operator_id=f"op_{level}",
            operator_type=LogicalOperatorType.SCAN,
            table_name=leaf_table if leaf else "lineitem",
            children=[root] if root is not None else [],
        )
    assert root is not None
    return QueryPlanDAG(query_id="q1", platform="duckdb", logical_root=root)


def _reload(plan: QueryPlanDAG) -> QueryPlanDAG:
    return QueryPlanDAG.from_dict(plan.to_dict())


def _reload_truncated(plan: QueryPlanDAG, max_depth: int = 1) -> QueryPlanDAG:
    return QueryPlanDAG.from_dict(plan.to_dict(max_depth=max_depth))


def _reasons(result) -> list[str]:
    return [str(diff.differences.get("reason", "")) for diff in result.operator_diffs]


class TestTruncationCaveat:
    def test_truncated_pair_with_differing_full_fingerprints_is_flagged(self):
        left = _reload_truncated(_leaf_chain("lineitem"))
        right = _reload_truncated(_leaf_chain("orders"))

        assert left.truncated_at_depth == 2
        assert right.truncated_at_depth == 2
        result = compare_query_plans(left, right)

        assert result.similarity.overall_similarity < 1.0
        assert result.similarity.structure_mismatches >= 1
        assert any("depth-truncated" in reason for reason in _reasons(result))
        assert not result.plans_identical
        assert not result.fingerprints_match

    def test_truncated_identical_pair_compares_clean(self):
        left = _reload_truncated(_leaf_chain("lineitem"))
        right = _reload_truncated(_leaf_chain("lineitem"))

        result = compare_query_plans(left, right)

        assert result.similarity.overall_similarity == 1.0
        assert result.similarity.structure_mismatches == 0
        assert not any("depth-truncated" in reason for reason in _reasons(result))

    def test_non_truncated_difference_has_no_caveat(self):
        left = _reload(_leaf_chain("lineitem"))
        right = _reload(_leaf_chain("orders"))

        result = compare_query_plans(left, right)

        assert result.similarity.overall_similarity < 1.0
        assert not any("depth-truncated" in reason for reason in _reasons(result))


