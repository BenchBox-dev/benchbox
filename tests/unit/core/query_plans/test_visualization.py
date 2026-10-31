import pytest

from benchbox.core.query_plans.comparison import compare_query_plans
from benchbox.core.query_plans.visualization import (
    QueryPlanVisualizer,
    VisualizationOptions,
    render_comparison,
    render_plan,
    render_summary,
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


class TestQueryPlanVisualization:
    @pytest.fixture
    def simple_plan(self) -> QueryPlanDAG:
        root = LogicalOperator(
            operator_id="scan_1",
            operator_type=LogicalOperatorType.SCAN,
            table_name="orders",
        )
        return QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=root,
            estimated_cost=100.5,
            estimated_rows=1000,
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
            filter_expressions=["o_orderdate > '2023-01-01'", "o_totalprice > 100"],
            children=[scan_left],
        )
        scan_right = LogicalOperator(
            operator_id="scan_2",
            operator_type=LogicalOperatorType.SCAN,
            table_name="customer",
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
            aggregation_functions=["COUNT(*)", "SUM(o_totalprice)"],
            children=[join],
        )
        return QueryPlanDAG(
            query_id="q5",
            platform="duckdb",
            logical_root=aggregate,
            estimated_cost=500.25,
            estimated_rows=50,
        )

    def test_render_simple_plan(self, simple_plan):
        output = render_plan(simple_plan)

        assert "Query Plan: q1" in output
        assert "Platform: duckdb" in output
        assert "Cost: 100.50" in output
        assert "Rows: 1,000" in output
        assert "Scan" in output
        assert "orders" in output

    def test_render_complex_plan(self, complex_plan):
        output = render_plan(complex_plan)

        assert "Query Plan: q5" in output
        assert "Aggregate" in output
        assert "Join" in output
        assert "Filter" in output
        assert "Scan" in output
        assert "orders" in output
        assert "customer" in output

    def test_render_tree_structure(self, complex_plan):
        output = render_plan(complex_plan)

        assert "└──" in output or "├──" in output

    def test_render_summary(self, complex_plan):
        output = render_summary(complex_plan)

        assert "Query: q5" in output
        assert "Total Operators:" in output
        assert "Max Depth:" in output
        assert "Estimated Cost: 500.25" in output
        assert "Estimated Rows: 50" in output
        assert "Operator Breakdown:" in output

    def test_summary_operator_counts(self, complex_plan):
        output = render_summary(complex_plan)

        assert "Scan: 2" in output
        assert "Filter: 1" in output
        assert "Join: 1" in output
        assert "Aggregate: 1" in output

    def test_visualizer_with_options(self, complex_plan):
        options = VisualizationOptions(
            show_properties=False,
            compact=True,
        )
        visualizer = QueryPlanVisualizer(options)
        output = visualizer.render_plan(complex_plan)

        assert "Aggregate" in output
        assert "Join" in output

    def test_max_depth_limiting(self, complex_plan):
        options = VisualizationOptions(max_depth=2)
        visualizer = QueryPlanVisualizer(options)
        output = visualizer.render_plan(complex_plan)

        assert "..." in output

    def test_render_comparison(self, simple_plan, complex_plan):
        comparison = compare_query_plans(simple_plan, complex_plan)
        output = render_comparison(comparison)

        assert "QUERY PLAN COMPARISON" in output
        assert "Similarity Metrics:" in output
        assert "Overall:" in output
        assert "Structural:" in output
        assert "Operator:" in output
        assert "Property:" in output

    def test_comparison_shows_differences(self):

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
                table_name="customers",
            ),
        )

        comparison = compare_query_plans(plan1, plan2)
        output = render_comparison(comparison)

        assert "Property Differences" in output or "Property mismatches" in output

    def test_identical_plans_comparison(self, simple_plan):
        comparison = compare_query_plans(simple_plan, simple_plan)
        output = render_comparison(comparison)

        assert "100.0%" in output or "identical" in output.lower()

    def test_filter_expressions_display(self):
        plan = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="filter_1",
                operator_type=LogicalOperatorType.FILTER,
                filter_expressions=[
                    "col1 > 100",
                    "col2 = 'test'",
                    "col3 < 50",
                ],
                children=[
                    LogicalOperator(
                        operator_id="scan_1",
                        operator_type=LogicalOperatorType.SCAN,
                        table_name="table1",
                    )
                ],
            ),
        )

        output = render_plan(plan)
        assert "Filter" in output

    def test_aggregation_display(self):
        plan = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="agg_1",
                operator_type=LogicalOperatorType.AGGREGATE,
                aggregation_functions=["COUNT(*)", "SUM(price)", "AVG(quantity)"],
                children=[
                    LogicalOperator(
                        operator_id="scan_1",
                        operator_type=LogicalOperatorType.SCAN,
                        table_name="orders",
                    )
                ],
            ),
        )

        output = render_plan(plan)
        assert "Aggregate" in output

    def test_join_type_display(self):
        plan = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="join_1",
                operator_type=LogicalOperatorType.JOIN,
                join_type=JoinType.LEFT,
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

        output = render_plan(plan)
        assert "Join" in output
        assert "left" in output.lower()

    def test_plan_without_costs(self):
        plan = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="scan_1",
                operator_type=LogicalOperatorType.SCAN,
                table_name="orders",
            ),
            estimated_cost=None,
            estimated_rows=None,
        )

        output = render_plan(plan)
        assert "Query Plan: q1" in output

    def test_deep_nested_tree(self):

        leaf = LogicalOperator(
            operator_id="scan_1",
            operator_type=LogicalOperatorType.SCAN,
            table_name="table1",
        )

        current = leaf
        for i in range(5):
            current = LogicalOperator(
                operator_id=f"filter_{i}",
                operator_type=LogicalOperatorType.FILTER,
                filter_expressions=[f"col{i} > {i}"],
                children=[current],
            )

        plan = QueryPlanDAG(
            query_id="deep",
            platform="duckdb",
            logical_root=current,
        )

        output = render_plan(plan)
        assert "Filter" in output
        assert "Scan" in output


class TestStringOperatorTypeVisualization:
    def test_render_plan_with_string_operator_type(self):
        plan = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="custom_1",
                operator_type="CustomScan",
                table_name="orders",
            ),
        )

        output = render_plan(plan)

        assert "CustomScan" in output
        assert "orders" in output
        assert "Query Plan: q1" in output

    def test_render_summary_with_string_operator_types(self):
        plan = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="agg_1",
                operator_type="ParallelAggregate",
                children=[
                    LogicalOperator(
                        operator_id="custom_1",
                        operator_type="IndexScan",
                        table_name="orders",
                    ),
                    LogicalOperator(
                        operator_id="custom_2",
                        operator_type="IndexScan",
                        table_name="customers",
                    ),
                ],
            ),
        )

        output = render_summary(plan)

        assert "ParallelAggregate: 1" in output
        assert "IndexScan: 2" in output
        assert "Total Operators: 3" in output

    def test_render_plan_with_string_join_type(self):
        plan = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="join_1",
                operator_type=LogicalOperatorType.JOIN,
                join_type="parallel_hash",
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

        output = render_plan(plan)

        assert "Join" in output
        assert "parallel_hash" in output

    def test_render_comparison_with_string_operator_types(self):
        plan1 = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="custom_1",
                operator_type="CustomScan",
            ),
        )

        plan2 = QueryPlanDAG(
            query_id="q2",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="custom_1",
                operator_type="IndexScan",
            ),
        )

        comparison = compare_query_plans(plan1, plan2)
        output = render_comparison(comparison)

        assert "QUERY PLAN COMPARISON" in output
        assert "CustomScan" in output
        assert "IndexScan" in output

    def test_render_mixed_enum_and_string_types(self):
        plan = QueryPlanDAG(
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

        output = render_plan(plan)

        assert "Aggregate" in output
        assert "ParallelHashJoin" in output
        assert "Scan" in output
        assert "IndexSeek" in output

    def test_visualizer_max_depth_with_string_types(self):
        plan = QueryPlanDAG(
            query_id="q1",
            platform="duckdb",
            logical_root=LogicalOperator(
                operator_id="custom_1",
                operator_type="CustomOp1",
                children=[
                    LogicalOperator(
                        operator_id="custom_2",
                        operator_type="CustomOp2",
                        children=[
                            LogicalOperator(
                                operator_id="custom_3",
                                operator_type="CustomOp3",
                            ),
                        ],
                    ),
                ],
            ),
        )

        options = VisualizationOptions(max_depth=1)
        visualizer = QueryPlanVisualizer(options)
        output = visualizer.render_plan(plan)

        assert "CustomOp1" in output
        assert "..." in output


class TestJoinConditionRendering:
    @staticmethod
    def _join_plan(conditions: list[str]) -> QueryPlanDAG:
        root = LogicalOperator(
            operator_id="join_1",
            operator_type=LogicalOperatorType.JOIN,
            join_type=JoinType.INNER,
            join_conditions=conditions,
            children=[
                LogicalOperator(operator_id="s1", operator_type=LogicalOperatorType.SCAN, table_name="a"),
                LogicalOperator(operator_id="s2", operator_type=LogicalOperatorType.SCAN, table_name="b"),
            ],
        )
        return QueryPlanDAG(query_id="q", platform="duckdb", logical_root=root)

    def test_real_duckdb_join_plan_shows_conditions(self):
        duckdb = pytest.importorskip("duckdb")
        from benchbox.core.query_plans.parsers.duckdb import DuckDBQueryPlanParser

        conn = duckdb.connect()
        conn.execute("CREATE TABLE a(k INT, v INT); CREATE TABLE b(k INT, w INT)")
        explain = conn.execute("EXPLAIN (FORMAT JSON) SELECT * FROM a JOIN b ON a.k = b.k AND a.v < b.w").fetchall()[0][
            1
        ]
        plan = DuckDBQueryPlanParser().parse_explain_output("q", explain)

        rendered = render_plan(plan)

        assert "Join (type=inner)" in rendered
        assert "k = k" in rendered
        assert "v < w" in rendered

    def test_long_condition_truncated(self):
        rendered = render_plan(self._join_plan(["x" * 300]))
        assert "x" * 117 + "..." in rendered
        assert "x" * 121 not in rendered

    def test_condition_count_capped(self):
        rendered = render_plan(self._join_plan([f"a{i} = b{i}" for i in range(8)]))
        assert "a4 = b4" in rendered
        assert "a5 = b5" not in rendered
        assert "+3 more" in rendered

    def test_compact_hides_conditions(self):
        rendered = render_plan(self._join_plan(["a = b"]), VisualizationOptions(compact=True))
        assert "a = b" not in rendered

    def test_json_wrapped_conditions_unwrapped(self):
        wrapped = '{"Join Type": "INNER", "Conditions": "l_orderkey = o_orderkey"}'
        rendered = render_plan(self._join_plan([wrapped]))
        assert "- l_orderkey = o_orderkey" in rendered
        assert "Join Type" not in rendered
