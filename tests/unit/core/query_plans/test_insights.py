import pytest

from benchbox.core.query_plans.insights import (
    PlanInsight,
    analyze_plan,
    compute_complexity_score,
    detect_antipatterns,
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


def _create_scan_operator(table_name: str, op_id: str = "scan_1") -> LogicalOperator:
    return LogicalOperator(
        operator_type=LogicalOperatorType.SCAN,
        operator_id=op_id,
        table_name=table_name,
        children=[],
    )


def _create_join_operator(
    left: LogicalOperator,
    right: LogicalOperator,
    join_type: JoinType = JoinType.INNER,
    join_conditions: list[str] | None = None,
    op_id: str = "join_1",
) -> LogicalOperator:
    return LogicalOperator(
        operator_type=LogicalOperatorType.JOIN,
        operator_id=op_id,
        join_type=join_type,
        join_conditions=join_conditions,
        children=[left, right],
    )


def _create_plan(root: LogicalOperator, query_id: str = "q01") -> QueryPlanDAG:
    return QueryPlanDAG(
        query_id=query_id,
        platform="test",
        logical_root=root,
        raw_explain_output="test",
    )


class TestPlanComplexityScore:
    def test_simple_scan_low_complexity(self) -> None:
        scan = _create_scan_operator("orders")
        plan = _create_plan(scan)

        score = compute_complexity_score(plan)

        assert score.total_operators == 1
        assert score.scan_count == 1
        assert score.join_count == 0
        assert score.complexity_level == "low"
        assert score.overall_score < 20

    def test_join_increases_complexity(self) -> None:
        left = _create_scan_operator("orders", "scan_1")
        right = _create_scan_operator("customers", "scan_2")
        join = _create_join_operator(left, right)
        plan = _create_plan(join)

        score = compute_complexity_score(plan)

        assert score.join_count == 1
        assert score.join_complexity >= 1
        assert score.overall_score > 5

    def test_cross_join_high_complexity(self) -> None:
        left = _create_scan_operator("orders", "scan_1")
        right = _create_scan_operator("customers", "scan_2")
        cross_join = _create_join_operator(left, right, join_type=JoinType.CROSS)
        plan = _create_plan(cross_join)

        score = compute_complexity_score(plan)

        assert score.join_complexity >= 3

    def test_multiple_joins_high_complexity(self) -> None:

        tables = ["t1", "t2", "t3", "t4", "t5", "t6"]
        current = _create_scan_operator(tables[0], "scan_0")

        for i, table in enumerate(tables[1:], 1):
            right = _create_scan_operator(table, f"scan_{i}")
            current = _create_join_operator(current, right, op_id=f"join_{i}")

        plan = _create_plan(current)
        score = compute_complexity_score(plan)

        assert score.join_count == 5
        assert score.complexity_level in ("medium", "high", "very_high")

    def test_aggregation_adds_complexity(self) -> None:
        scan = _create_scan_operator("orders")
        agg = LogicalOperator(
            operator_type=LogicalOperatorType.AGGREGATE,
            operator_id="agg_1",
            children=[scan],
        )
        plan = _create_plan(agg)

        score = compute_complexity_score(plan)

        assert score.aggregation_count == 1
        assert score.overall_score > 5

    def test_deep_tree_adds_complexity(self) -> None:

        current = _create_scan_operator("orders")
        for i in range(10):
            current = LogicalOperator(
                operator_type=LogicalOperatorType.FILTER,
                operator_id=f"filter_{i}",
                children=[current],
            )

        plan = _create_plan(current)
        score = compute_complexity_score(plan)

        assert score.max_tree_depth >= 10
        assert score.filter_count == 10

    def test_to_dict(self) -> None:
        scan = _create_scan_operator("orders")
        plan = _create_plan(scan)
        score = compute_complexity_score(plan)

        result = score.to_dict()

        assert "total_operators" in result
        assert "overall_score" in result
        assert "complexity_level" in result


class TestAntiPatternDetection:
    def test_no_antipatterns_for_simple_plan(self) -> None:
        scan = _create_scan_operator("orders")
        plan = _create_plan(scan)

        insights = detect_antipatterns(plan)

        warnings = [i for i in insights if i.category == "warning"]
        assert len(warnings) == 0

    def test_detect_cross_join(self) -> None:
        left = _create_scan_operator("orders", "scan_1")
        right = _create_scan_operator("customers", "scan_2")
        cross_join = _create_join_operator(left, right, join_type=JoinType.CROSS)
        plan = _create_plan(cross_join)

        insights = detect_antipatterns(plan)

        cross_join_insights = [i for i in insights if "Cross Join" in i.title]
        assert len(cross_join_insights) == 1
        assert cross_join_insights[0].severity == "high"

    def test_detect_multiple_scans_same_table(self) -> None:
        left = _create_scan_operator("orders", "scan_1")
        right = _create_scan_operator("orders", "scan_2")
        join = _create_join_operator(left, right)
        plan = _create_plan(join)

        insights = detect_antipatterns(plan)

        multiple_scan_insights = [i for i in insights if "Multiple Scans" in i.title]
        assert len(multiple_scan_insights) == 1
        assert "orders" in multiple_scan_insights[0].description

    def test_detect_deep_nesting(self) -> None:
        current = _create_scan_operator("orders")
        for i in range(12):
            current = LogicalOperator(
                operator_type=LogicalOperatorType.FILTER,
                operator_id=f"filter_{i}",
                children=[current],
            )

        plan = _create_plan(current)
        insights = detect_antipatterns(plan)

        deep_nesting_insights = [i for i in insights if "Deep" in i.title]
        assert len(deep_nesting_insights) > 0

    def test_insight_to_dict(self) -> None:
        insight = PlanInsight(
            category="warning",
            title="Test",
            description="Test description",
            operator_id="op_1",
            severity="high",
        )

        result = insight.to_dict()

        assert result["category"] == "warning"
        assert result["title"] == "Test"
        assert result["severity"] == "high"


class TestAnalyzePlan:
    def test_analyze_simple_plan(self) -> None:
        scan = _create_scan_operator("orders")
        plan = _create_plan(scan, "q01")

        result = analyze_plan(plan)

        assert result.query_id == "q01"
        assert result.complexity is not None
        assert isinstance(result.insights, list)

    def test_analyze_complex_plan_adds_insights(self) -> None:

        tables = ["t1", "t2", "t3", "t4", "t5", "t6", "t7"]
        current = _create_scan_operator(tables[0], "scan_0")

        for i, table in enumerate(tables[1:], 1):
            right = _create_scan_operator(table, f"scan_{i}")
            current = _create_join_operator(current, right, op_id=f"join_{i}")

        plan = _create_plan(current, "complex_q")
        result = analyze_plan(plan)

        many_joins = [i for i in result.insights if "Many Joins" in i.title]
        assert len(many_joins) > 0

    def test_analysis_result_to_dict(self) -> None:
        scan = _create_scan_operator("orders")
        plan = _create_plan(scan, "q01")

        result = analyze_plan(plan)
        result_dict = result.to_dict()

        assert result_dict["query_id"] == "q01"
        assert "complexity" in result_dict
        assert "insights" in result_dict

    def test_analyze_with_antipatterns(self) -> None:
        left = _create_scan_operator("orders", "scan_1")
        right = _create_scan_operator("customers", "scan_2")
        cross_join = _create_join_operator(left, right, join_type=JoinType.CROSS)
        plan = _create_plan(cross_join)

        result = analyze_plan(plan)

        cross_warnings = [i for i in result.insights if "Cross" in i.title]
        assert len(cross_warnings) > 0

    def test_empty_plan_root(self) -> None:
        plan = QueryPlanDAG(
            query_id="q01",
            platform="test",
            logical_root=None,
            raw_explain_output="test",
        )

        score = compute_complexity_score(plan)
        insights = detect_antipatterns(plan)

        assert score.total_operators == 0
        assert len(insights) == 0


class TestComplexityLevels:
    def test_low_complexity_threshold(self) -> None:
        scan = _create_scan_operator("orders")
        plan = _create_plan(scan)
        score = compute_complexity_score(plan)

        assert score.overall_score < 20
        assert score.complexity_level == "low"

    def test_high_complexity_many_operations(self) -> None:

        current = _create_scan_operator("t1", "scan_0")
        for i in range(5):
            right = _create_scan_operator(f"t{i + 2}", f"scan_{i + 1}")
            current = _create_join_operator(current, right, op_id=f"join_{i}")

        current = LogicalOperator(
            operator_type=LogicalOperatorType.AGGREGATE,
            operator_id="agg_1",
            children=[current],
        )

        plan = _create_plan(current)
        score = compute_complexity_score(plan)

        assert score.complexity_level in ("medium", "high", "very_high")
        assert score.overall_score >= 20


