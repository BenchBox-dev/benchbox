from __future__ import annotations

import pytest

from benchbox.core.cost.models import BenchmarkCost, PhaseCost, QueryCost
from benchbox.core.cost.optimizer import (
    ConfidenceLevel,
    CostOptimizer,
    ImplementationEffort,
    ImplementationGuide,
    OptimizationCategory,
    OptimizationReport,
    Recommendation,
    SavingsEstimate,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSavingsEstimate:
    def test_basic_savings(self):

        savings = SavingsEstimate(
            amount=1000.0,
            currency="USD",
            period="annual",
            confidence=ConfidenceLevel.HIGH,
        )

        assert savings.amount == 1000.0
        assert savings.currency == "USD"
        assert savings.period == "annual"
        assert savings.confidence == ConfidenceLevel.HIGH

    def test_savings_with_range(self):

        savings = SavingsEstimate(
            amount=1000.0,
            range_low=800.0,
            range_high=1200.0,
            percentage=25.0,
        )

        assert savings.range_low == 800.0
        assert savings.range_high == 1200.0
        assert savings.percentage == 25.0

    def test_to_dict(self):

        savings = SavingsEstimate(
            amount=1234.567,
            currency="USD",
            period="annual",
            confidence=ConfidenceLevel.MEDIUM,
            percentage=15.5,
        )

        result = savings.to_dict()

        assert result["amount"] == 1234.57
        assert result["currency"] == "USD"
        assert result["period"] == "annual"
        assert result["confidence"] == "medium"
        assert result["percentage"] == 15.5


class TestImplementationGuide:
    def test_basic_guide(self):

        guide = ImplementationGuide(
            steps=["Step 1", "Step 2", "Step 3"],
        )

        assert len(guide.steps) == 3
        assert guide.prerequisites == []
        assert guide.risks == []

    def test_full_guide(self):

        guide = ImplementationGuide(
            steps=["Step 1", "Step 2"],
            prerequisites=["Prereq 1"],
            risks=["Risk 1", "Risk 2"],
            rollback="Revert by doing X",
            estimated_time="1-2 weeks",
        )

        assert len(guide.prerequisites) == 1
        assert len(guide.risks) == 2
        assert guide.rollback == "Revert by doing X"
        assert guide.estimated_time == "1-2 weeks"

    def test_to_dict(self):

        guide = ImplementationGuide(
            steps=["Step 1"],
            risks=["Risk 1"],
            estimated_time="1 day",
        )

        result = guide.to_dict()

        assert result["steps"] == ["Step 1"]
        assert result["risks"] == ["Risk 1"]
        assert result["estimated_time"] == "1 day"
        assert "prerequisites" not in result


class TestRecommendation:
    def test_basic_recommendation(self):

        rec = Recommendation(
            id="test-rec-1",
            title="Test Recommendation",
            description="This is a test recommendation",
            category=OptimizationCategory.PLATFORM_TIER,
            savings=SavingsEstimate(amount=1000.0),
            effort=ImplementationEffort.LOW,
            guide=ImplementationGuide(steps=["Do this"]),
        )

        assert rec.id == "test-rec-1"
        assert rec.title == "Test Recommendation"
        assert rec.category == OptimizationCategory.PLATFORM_TIER
        assert rec.priority == 50

    def test_recommendation_with_config(self):

        rec = Recommendation(
            id="test-rec-2",
            title="Config Change",
            description="Change config",
            category=OptimizationCategory.RESOURCE_SIZING,
            savings=SavingsEstimate(amount=500.0),
            effort=ImplementationEffort.MEDIUM,
            guide=ImplementationGuide(steps=["Step 1"]),
            platform="snowflake",
            current_config={"edition": "enterprise"},
            recommended_config={"edition": "standard"},
        )

        assert rec.platform == "snowflake"
        assert rec.current_config["edition"] == "enterprise"
        assert rec.recommended_config["edition"] == "standard"

    def test_to_dict(self):

        rec = Recommendation(
            id="test-rec-3",
            title="Test",
            description="Test desc",
            category=OptimizationCategory.QUERY,
            savings=SavingsEstimate(amount=100.0),
            effort=ImplementationEffort.HIGH,
            guide=ImplementationGuide(steps=["Step"]),
            priority=80,
            platform="bigquery",
        )

        result = rec.to_dict()

        assert result["id"] == "test-rec-3"
        assert result["category"] == "query"
        assert result["effort"] == "high"
        assert result["priority"] == 80
        assert result["platform"] == "bigquery"


class TestOptimizationReport:
    def test_empty_report(self):

        report = OptimizationReport()

        assert report.recommendations == []
        assert report.total_potential_savings == 0.0
        assert report.currency == "USD"

    def test_report_with_recommendations(self):

        rec1 = Recommendation(
            id="rec-1",
            title="Rec 1",
            description="Desc 1",
            category=OptimizationCategory.PLATFORM_TIER,
            savings=SavingsEstimate(amount=1000.0),
            effort=ImplementationEffort.LOW,
            guide=ImplementationGuide(steps=["Step"]),
            priority=80,
        )
        rec2 = Recommendation(
            id="rec-2",
            title="Rec 2",
            description="Desc 2",
            category=OptimizationCategory.QUERY,
            savings=SavingsEstimate(amount=500.0),
            effort=ImplementationEffort.HIGH,
            guide=ImplementationGuide(steps=["Step"]),
            priority=60,
        )

        report = OptimizationReport(
            recommendations=[rec1, rec2],
            total_potential_savings=1500.0,
            platform="snowflake",
        )

        assert len(report.recommendations) == 2
        assert report.total_potential_savings == 1500.0
        assert report.platform == "snowflake"

    def test_get_by_category(self):

        rec1 = Recommendation(
            id="rec-1",
            title="Tier Rec",
            description="Desc",
            category=OptimizationCategory.PLATFORM_TIER,
            savings=SavingsEstimate(amount=1000.0),
            effort=ImplementationEffort.LOW,
            guide=ImplementationGuide(steps=["Step"]),
        )
        rec2 = Recommendation(
            id="rec-2",
            title="Query Rec",
            description="Desc",
            category=OptimizationCategory.QUERY,
            savings=SavingsEstimate(amount=500.0),
            effort=ImplementationEffort.LOW,
            guide=ImplementationGuide(steps=["Step"]),
        )
        rec3 = Recommendation(
            id="rec-3",
            title="Another Tier Rec",
            description="Desc",
            category=OptimizationCategory.PLATFORM_TIER,
            savings=SavingsEstimate(amount=300.0),
            effort=ImplementationEffort.LOW,
            guide=ImplementationGuide(steps=["Step"]),
        )

        report = OptimizationReport(recommendations=[rec1, rec2, rec3])

        tier_recs = report.get_by_category(OptimizationCategory.PLATFORM_TIER)
        assert len(tier_recs) == 2

        query_recs = report.get_by_category(OptimizationCategory.QUERY)
        assert len(query_recs) == 1

        region_recs = report.get_by_category(OptimizationCategory.REGION)
        assert len(region_recs) == 0

    def test_get_quick_wins(self):

        rec_trivial = Recommendation(
            id="rec-trivial",
            title="Trivial",
            description="Desc",
            category=OptimizationCategory.PLATFORM_TIER,
            savings=SavingsEstimate(amount=100.0),
            effort=ImplementationEffort.TRIVIAL,
            guide=ImplementationGuide(steps=["Step"]),
        )
        rec_low = Recommendation(
            id="rec-low",
            title="Low",
            description="Desc",
            category=OptimizationCategory.QUERY,
            savings=SavingsEstimate(amount=500.0),
            effort=ImplementationEffort.LOW,
            guide=ImplementationGuide(steps=["Step"]),
        )
        rec_high = Recommendation(
            id="rec-high",
            title="High",
            description="Desc",
            category=OptimizationCategory.REGION,
            savings=SavingsEstimate(amount=2000.0),
            effort=ImplementationEffort.HIGH,
            guide=ImplementationGuide(steps=["Step"]),
        )

        report = OptimizationReport(recommendations=[rec_trivial, rec_low, rec_high])

        quick_wins = report.get_quick_wins()
        assert len(quick_wins) == 2
        assert quick_wins[0].id == "rec-low"
        assert quick_wins[1].id == "rec-trivial"

        trivial_wins = report.get_quick_wins(max_effort=ImplementationEffort.TRIVIAL)
        assert len(trivial_wins) == 1

    def test_to_dict(self):

        report = OptimizationReport(
            recommendations=[],
            total_potential_savings=1500.0,
            platform="bigquery",
        )

        result = report.to_dict()

        assert result["total_potential_savings"] == 1500.0
        assert result["platform"] == "bigquery"
        assert result["recommendation_count"] == 0


class TestCostOptimizer:
    @pytest.fixture
    def optimizer(self):
        return CostOptimizer()

    @pytest.fixture
    def snowflake_cost(self):
        return BenchmarkCost(
            total_cost=150.0,
            currency="USD",
            phase_costs=[
                PhaseCost(
                    phase_name="power_test",
                    total_cost=100.0,
                    query_count=22,
                    query_costs=[
                        QueryCost(compute_cost=50.0),
                        QueryCost(compute_cost=10.0),
                        QueryCost(compute_cost=10.0),
                        QueryCost(compute_cost=10.0),
                        QueryCost(compute_cost=10.0),
                        QueryCost(compute_cost=10.0),
                    ],
                ),
                PhaseCost(
                    phase_name="throughput_test",
                    total_cost=50.0,
                    query_count=44,
                ),
            ],
            platform_details={"platform": "snowflake"},
        )

    @pytest.fixture
    def snowflake_config(self):
        return {
            "platform": "snowflake",
            "edition": "enterprise",
            "cloud": "aws",
            "region": "eu-west-1",
        }

    def test_analyze_snowflake_tier(self, optimizer, snowflake_cost, snowflake_config):

        report = optimizer.analyze(
            benchmark_cost=snowflake_cost,
            platform_config=snowflake_config,
            annual_runs=12,
        )

        tier_recs = report.get_by_category(OptimizationCategory.PLATFORM_TIER)
        assert len(tier_recs) >= 1

        tier_rec = tier_recs[0]
        assert "snowflake" in tier_rec.id.lower()
        assert tier_rec.savings.amount > 0
        assert tier_rec.platform == "snowflake"

    def test_analyze_snowflake_region(self, optimizer, snowflake_cost, snowflake_config):

        report = optimizer.analyze(
            benchmark_cost=snowflake_cost,
            platform_config=snowflake_config,
            annual_runs=12,
        )

        region_recs = report.get_by_category(OptimizationCategory.REGION)
        assert len(region_recs) >= 1

        region_rec = region_recs[0]
        assert region_rec.savings.amount > 0

    def test_analyze_high_cost_queries(self, optimizer, snowflake_cost, snowflake_config):

        report = optimizer.analyze(
            benchmark_cost=snowflake_cost,
            platform_config=snowflake_config,
            annual_runs=12,
        )

        query_recs = report.get_by_category(OptimizationCategory.QUERY)
        assert len(query_recs) >= 1

    def test_analyze_reserved_capacity(self, optimizer, snowflake_cost, snowflake_config):

        high_cost = BenchmarkCost(
            total_cost=1000.0,
            currency="USD",
            platform_details={"platform": "snowflake"},
        )

        report = optimizer.analyze(
            benchmark_cost=high_cost,
            platform_config=snowflake_config,
            annual_runs=12,
        )

        pricing_recs = report.get_by_category(OptimizationCategory.PRICING_MODEL)
        assert len(pricing_recs) >= 1

        pricing_rec = pricing_recs[0]
        assert "reserved" in pricing_rec.id.lower()
        assert pricing_rec.savings.amount > 0

    def test_analyze_bigquery(self, optimizer):

        bigquery_cost = BenchmarkCost(
            total_cost=50.0,
            currency="USD",
            platform_details={
                "platform": "bigquery",
                "pricing_details": {
                    "total_bytes_processed": 10 * (1024**4),
                },
            },
        )

        bigquery_config = {
            "platform": "bigquery",
            "location": "australia-southeast1",
        }

        report = optimizer.analyze(
            benchmark_cost=bigquery_cost,
            platform_config=bigquery_config,
            annual_runs=12,
        )

        region_recs = report.get_by_category(OptimizationCategory.REGION)
        assert len(region_recs) >= 1

        data_recs = report.get_by_category(OptimizationCategory.DATA_MANAGEMENT)
        assert len(data_recs) >= 1

    def test_analyze_redshift(self, optimizer):

        redshift_cost = BenchmarkCost(
            total_cost=100.0,
            currency="USD",
            platform_details={"platform": "redshift"},
        )

        redshift_config = {
            "platform": "redshift",
            "node_type": "dc2.large",
            "node_count": 2,
            "region": "eu-west-1",
        }

        report = optimizer.analyze(
            benchmark_cost=redshift_cost,
            platform_config=redshift_config,
            annual_runs=12,
        )

        region_recs = report.get_by_category(OptimizationCategory.REGION)
        assert len(region_recs) >= 1

    def test_analyze_redshift_retired_node_no_sizing_rec(self, optimizer):

        redshift_cost = BenchmarkCost(
            total_cost=100.0,
            currency="USD",
            platform_details={"platform": "redshift"},
        )

        redshift_config = {
            "platform": "redshift",
            "node_type": "ds2.8xlarge",
            "node_count": 1,
            "region": "us-east-1",
        }

        report = optimizer.analyze(
            benchmark_cost=redshift_cost,
            platform_config=redshift_config,
            annual_runs=12,
        )

        sizing_recs = report.get_by_category(OptimizationCategory.RESOURCE_SIZING)
        assert len(sizing_recs) == 0

    def test_analyze_databricks(self, optimizer):

        databricks_cost = BenchmarkCost(
            total_cost=200.0,
            currency="USD",
            platform_details={"platform": "databricks"},
        )

        databricks_config = {
            "platform": "databricks",
            "tier": "enterprise",
            "cloud": "aws",
            "workload_type": "all_purpose",
        }

        report = optimizer.analyze(
            benchmark_cost=databricks_cost,
            platform_config=databricks_config,
            annual_runs=12,
        )

        tier_recs = report.get_by_category(OptimizationCategory.PLATFORM_TIER)
        assert len(tier_recs) >= 1

        sizing_recs = report.get_by_category(OptimizationCategory.RESOURCE_SIZING)
        assert len(sizing_recs) >= 1

    def test_analyze_empty_cost(self, optimizer):

        empty_cost = BenchmarkCost(
            total_cost=0.0,
            currency="USD",
            platform_details={},
        )

        report = optimizer.analyze(
            benchmark_cost=empty_cost,
            platform_config={"platform": "duckdb"},
            annual_runs=12,
        )

        assert isinstance(report, OptimizationReport)

    def test_total_potential_savings(self, optimizer, snowflake_cost, snowflake_config):

        report = optimizer.analyze(
            benchmark_cost=snowflake_cost,
            platform_config=snowflake_config,
            annual_runs=12,
        )

        expected_total = sum(r.savings.amount for r in report.recommendations)
        assert report.total_potential_savings == expected_total

    def test_recommendations_sorted_by_priority(self, optimizer, snowflake_cost, snowflake_config):

        report = optimizer.analyze(
            benchmark_cost=snowflake_cost,
            platform_config=snowflake_config,
            annual_runs=12,
        )

        if len(report.recommendations) >= 2:
            priorities = [r.priority for r in report.recommendations]
            assert priorities == sorted(priorities, reverse=True)

    def test_report_metadata(self, optimizer, snowflake_cost, snowflake_config):

        report = optimizer.analyze(
            benchmark_cost=snowflake_cost,
            platform_config=snowflake_config,
            annual_runs=24,
        )

        assert report.metadata["annual_runs"] == 24
        assert report.metadata["rules_evaluated"] > 0
        assert "recommendations_generated" in report.metadata


class TestCostOptimizerEdgeCases:
    @pytest.fixture
    def optimizer(self):
        return CostOptimizer()

    def test_unknown_platform(self, optimizer):

        cost = BenchmarkCost(
            total_cost=100.0,
            platform_details={"platform": "unknown_db"},
        )

        report = optimizer.analyze(
            benchmark_cost=cost,
            platform_config={"platform": "unknown_db"},
        )

        assert isinstance(report, OptimizationReport)

    def test_already_optimized_snowflake(self, optimizer):

        cost = BenchmarkCost(
            total_cost=100.0,
            platform_details={"platform": "snowflake"},
        )

        config = {
            "platform": "snowflake",
            "edition": "standard",
            "cloud": "aws",
            "region": "us-east-1",
        }

        report = optimizer.analyze(
            benchmark_cost=cost,
            platform_config=config,
        )

        tier_recs = report.get_by_category(OptimizationCategory.PLATFORM_TIER)
        region_recs = report.get_by_category(OptimizationCategory.REGION)
        assert len(tier_recs) == 0
        assert len(region_recs) == 0

    def test_low_spend_no_reserved_recommendation(self, optimizer):

        cost = BenchmarkCost(
            total_cost=50.0,
            platform_details={"platform": "snowflake"},
        )

        report = optimizer.analyze(
            benchmark_cost=cost,
            platform_config={"platform": "snowflake"},
            annual_runs=12,
        )

        pricing_recs = report.get_by_category(OptimizationCategory.PRICING_MODEL)
        assert len(pricing_recs) == 0

    def test_no_query_costs_no_query_recommendations(self, optimizer):

        cost = BenchmarkCost(
            total_cost=100.0,
            phase_costs=[
                PhaseCost(
                    phase_name="test",
                    total_cost=100.0,
                    query_count=10,
                    query_costs=None,
                ),
            ],
            platform_details={"platform": "snowflake"},
        )

        report = optimizer.analyze(
            benchmark_cost=cost,
            platform_config={"platform": "snowflake"},
        )

        query_recs = report.get_by_category(OptimizationCategory.QUERY)
        assert len(query_recs) == 0


class TestConfidenceLevels:
    def test_tier_recommendations_high_confidence(self):

        optimizer = CostOptimizer()
        cost = BenchmarkCost(
            total_cost=100.0,
            platform_details={"platform": "snowflake"},
        )

        config = {
            "platform": "snowflake",
            "edition": "enterprise",
            "cloud": "aws",
            "region": "us-east-1",
        }

        report = optimizer.analyze(benchmark_cost=cost, platform_config=config)

        tier_recs = report.get_by_category(OptimizationCategory.PLATFORM_TIER)
        if tier_recs:
            assert tier_recs[0].savings.confidence == ConfidenceLevel.HIGH

    def test_query_recommendations_low_confidence(self):

        optimizer = CostOptimizer()
        cost = BenchmarkCost(
            total_cost=100.0,
            phase_costs=[
                PhaseCost(
                    phase_name="test",
                    total_cost=100.0,
                    query_count=5,
                    query_costs=[
                        QueryCost(compute_cost=50.0),
                        QueryCost(compute_cost=10.0),
                        QueryCost(compute_cost=10.0),
                        QueryCost(compute_cost=10.0),
                        QueryCost(compute_cost=10.0),
                    ],
                ),
            ],
            platform_details={"platform": "snowflake"},
        )

        report = optimizer.analyze(
            benchmark_cost=cost,
            platform_config={"platform": "snowflake"},
        )

        query_recs = report.get_by_category(OptimizationCategory.QUERY)
        if query_recs:
            assert query_recs[0].savings.confidence == ConfidenceLevel.LOW


class TestImplementationEffortLevels:
    def test_databricks_workload_low_effort(self):

        optimizer = CostOptimizer()
        cost = BenchmarkCost(
            total_cost=100.0,
            platform_details={"platform": "databricks"},
        )

        config = {
            "platform": "databricks",
            "tier": "premium",
            "cloud": "aws",
            "workload_type": "all_purpose",
        }

        report = optimizer.analyze(benchmark_cost=cost, platform_config=config)

        sizing_recs = report.get_by_category(OptimizationCategory.RESOURCE_SIZING)
        workload_recs = [r for r in sizing_recs if "workload" in r.id]
        if workload_recs:
            assert workload_recs[0].effort == ImplementationEffort.LOW

    def test_region_change_high_effort(self):

        optimizer = CostOptimizer()
        cost = BenchmarkCost(
            total_cost=100.0,
            platform_details={"platform": "snowflake"},
        )

        config = {
            "platform": "snowflake",
            "edition": "standard",
            "cloud": "aws",
            "region": "eu-west-1",
        }

        report = optimizer.analyze(benchmark_cost=cost, platform_config=config)

        region_recs = report.get_by_category(OptimizationCategory.REGION)
        if region_recs:
            assert region_recs[0].effort == ImplementationEffort.HIGH


