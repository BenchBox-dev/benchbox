# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.cost.models import BenchmarkCost
from benchbox.core.cost.tco import (
    BudgetAlert,
    BudgetThreshold,
    DiscountConfig,
    DiscountType,
    GrowthConfig,
    GrowthModel,
    TCOCalculator,
    TCOProjection,
    YearlyProjection,
    create_standard_tco_scenarios,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGrowthModel:
    def test_growth_model_values(self):

        assert GrowthModel.NONE.value == "none"
        assert GrowthModel.LINEAR.value == "linear"
        assert GrowthModel.COMPOUND.value == "compound"


class TestGrowthConfig:
    def test_default_config(self):

        config = GrowthConfig()

        assert config.model == GrowthModel.NONE
        assert config.annual_rate == 0.0
        assert config.data_growth_rate is None

    def test_custom_config(self):

        config = GrowthConfig(
            model=GrowthModel.COMPOUND,
            annual_rate=0.15,
            data_growth_rate=0.20,
        )

        assert config.model == GrowthModel.COMPOUND
        assert config.annual_rate == 0.15
        assert config.data_growth_rate == 0.20

    def test_get_data_growth_rate_with_value(self):

        config = GrowthConfig(annual_rate=0.10, data_growth_rate=0.20)
        assert config.get_data_growth_rate() == 0.20

    def test_get_data_growth_rate_defaults_to_annual(self):

        config = GrowthConfig(annual_rate=0.10)
        assert config.get_data_growth_rate() == 0.10

    def test_calculate_multiplier_no_growth(self):

        config = GrowthConfig(model=GrowthModel.NONE, annual_rate=0.10)

        assert config.calculate_multiplier(1) == 1.0
        assert config.calculate_multiplier(2) == 1.0
        assert config.calculate_multiplier(5) == 1.0

    def test_calculate_multiplier_first_year(self):
        config = GrowthConfig(model=GrowthModel.COMPOUND, annual_rate=0.10)
        assert config.calculate_multiplier(1) == 1.0

    def test_calculate_multiplier_linear(self):

        config = GrowthConfig(model=GrowthModel.LINEAR, annual_rate=0.10)

        assert config.calculate_multiplier(1) == 1.0
        assert config.calculate_multiplier(2) == pytest.approx(1.1, rel=0.001)
        assert config.calculate_multiplier(3) == pytest.approx(1.2, rel=0.001)
        assert config.calculate_multiplier(5) == pytest.approx(1.4, rel=0.001)

    def test_calculate_multiplier_compound(self):

        config = GrowthConfig(model=GrowthModel.COMPOUND, annual_rate=0.10)

        assert config.calculate_multiplier(1) == 1.0
        assert config.calculate_multiplier(2) == pytest.approx(1.1, rel=0.001)
        assert config.calculate_multiplier(3) == pytest.approx(1.21, rel=0.001)
        assert config.calculate_multiplier(5) == pytest.approx(1.4641, rel=0.001)


class TestDiscountType:
    def test_discount_type_values(self):

        assert DiscountType.NONE.value == "none"
        assert DiscountType.RESERVED.value == "reserved"
        assert DiscountType.COMMITTED_USE.value == "committed_use"
        assert DiscountType.ENTERPRISE.value == "enterprise"
        assert DiscountType.VOLUME.value == "volume"


class TestDiscountConfig:
    def test_default_config(self):

        config = DiscountConfig()

        assert config.discount_type == DiscountType.NONE
        assert config.discount_percent == 0.0
        assert config.commitment_years == 1
        assert config.effective_start_year == 1

    def test_custom_config(self):

        config = DiscountConfig(
            discount_type=DiscountType.RESERVED,
            discount_percent=0.30,
            commitment_years=3,
            effective_start_year=2,
        )

        assert config.discount_type == DiscountType.RESERVED
        assert config.discount_percent == 0.30
        assert config.commitment_years == 3
        assert config.effective_start_year == 2

    def test_get_discount_multiplier_no_discount(self):

        config = DiscountConfig(discount_type=DiscountType.NONE)

        assert config.get_discount_multiplier(1) == 1.0
        assert config.get_discount_multiplier(5) == 1.0

    def test_get_discount_multiplier_with_discount(self):

        config = DiscountConfig(
            discount_type=DiscountType.RESERVED,
            discount_percent=0.20,
        )

        assert config.get_discount_multiplier(1) == 0.80
        assert config.get_discount_multiplier(5) == 0.80

    def test_get_discount_multiplier_delayed_start(self):

        config = DiscountConfig(
            discount_type=DiscountType.COMMITTED_USE,
            discount_percent=0.25,
            effective_start_year=2,
        )

        assert config.get_discount_multiplier(1) == 1.0
        assert config.get_discount_multiplier(2) == 0.75
        assert config.get_discount_multiplier(5) == 0.75


class TestBudgetThreshold:
    def test_create_threshold(self):
        threshold = BudgetThreshold(
            name="warning",
            amount=100000.0,
            period="annual",
        )

        assert threshold.name == "warning"
        assert threshold.amount == 100000.0
        assert threshold.period == "annual"

    def test_default_period(self):

        threshold = BudgetThreshold(name="test", amount=50000.0)
        assert threshold.period == "annual"

    def test_is_exceeded_same_period(self):

        threshold = BudgetThreshold(name="test", amount=10000.0, period="annual")

        assert threshold.is_exceeded(5000.0, "annual") is False
        assert threshold.is_exceeded(10000.0, "annual") is False
        assert threshold.is_exceeded(10001.0, "annual") is True

    def test_is_exceeded_monthly_to_annual(self):

        threshold = BudgetThreshold(name="test", amount=12000.0, period="annual")

        assert threshold.is_exceeded(1000.0, "monthly") is False
        assert threshold.is_exceeded(1001.0, "monthly") is True

    def test_is_exceeded_annual_to_monthly(self):

        threshold = BudgetThreshold(name="test", amount=1000.0, period="monthly")

        assert threshold.is_exceeded(12000.0, "annual") is False
        assert threshold.is_exceeded(12012.0, "annual") is True


class TestBudgetAlert:
    def test_create_alert(self):
        threshold = BudgetThreshold(name="critical", amount=50000.0)
        alert = BudgetAlert(
            threshold=threshold,
            actual_cost=60000.0,
            year=2,
            period="annual",
            message="Budget exceeded in year 2",
        )

        assert alert.threshold == threshold
        assert alert.actual_cost == 60000.0
        assert alert.year == 2
        assert alert.period == "annual"
        assert "Budget exceeded" in alert.message


class TestYearlyProjection:
    def test_create_projection(self):

        projection = YearlyProjection(
            year=1,
            calendar_year=2025,
            base_cost=100000.0,
            growth_multiplier=1.0,
            discount_multiplier=0.85,
            projected_cost=85000.0,
            cumulative_cost=85000.0,
            monthly_cost=7083.33,
        )

        assert projection.year == 1
        assert projection.calendar_year == 2025
        assert projection.projected_cost == 85000.0

    def test_to_dict(self):

        projection = YearlyProjection(
            year=2,
            calendar_year=2026,
            base_cost=100000.0,
            growth_multiplier=1.1,
            discount_multiplier=0.85,
            projected_cost=93500.0,
            cumulative_cost=178500.0,
            monthly_cost=7791.67,
        )

        result = projection.to_dict()

        assert result["year"] == 2
        assert result["calendar_year"] == 2026
        assert result["base_cost"] == 100000.0
        assert result["growth_multiplier"] == 1.1
        assert result["discount_multiplier"] == 0.85
        assert result["projected_cost"] == 93500.0
        assert result["cumulative_cost"] == 178500.0


class TestTCOProjection:
    def test_default_projection(self):

        projection = TCOProjection(
            platform="snowflake",
            base_annual_cost=100000.0,
        )

        assert projection.platform == "snowflake"
        assert projection.base_annual_cost == 100000.0
        assert projection.currency == "USD"
        assert projection.projection_years == 5
        assert projection.total_tco == 0.0
        assert projection.yearly_projections == []
        assert projection.budget_alerts == []

    def test_to_dict(self):

        projection = TCOProjection(
            platform="bigquery",
            base_annual_cost=50000.0,
            currency="USD",
            projection_years=3,
            start_year=2025,
            total_tco=165000.0,
            average_annual_cost=55000.0,
        )

        result = projection.to_dict()

        assert result["platform"] == "bigquery"
        assert result["base_annual_cost"] == 50000.0
        assert result["currency"] == "USD"
        assert result["projection_years"] == 3
        assert result["start_year"] == 2025
        assert result["total_tco"] == 165000.0
        assert result["average_annual_cost"] == 55000.0


class TestTCOCalculator:
    @pytest.fixture
    def sample_benchmark_cost(self) -> BenchmarkCost:
        return BenchmarkCost(
            total_cost=1000.0,
            currency="USD",
            platform_details={"platform": "snowflake"},
        )

    @pytest.fixture
    def calculator(self) -> TCOCalculator:
        return TCOCalculator()

    def test_calculate_tco_basic(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=12,
            projection_years=5,
        )

        assert projection.base_annual_cost == 12000.0
        assert projection.projection_years == 5
        assert projection.platform == "snowflake"
        assert len(projection.yearly_projections) == 5

        assert projection.total_tco == pytest.approx(60000.0, rel=0.001)

    def test_calculate_tco_with_growth(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):

        growth_config = GrowthConfig(model=GrowthModel.COMPOUND, annual_rate=0.10)

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=12,
            projection_years=5,
            growth_config=growth_config,
        )

        assert projection.yearly_projections[0].growth_multiplier == 1.0
        assert projection.yearly_projections[1].growth_multiplier == pytest.approx(1.1, rel=0.001)
        assert projection.total_tco > 60000.0

    def test_calculate_tco_with_discount(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):

        discount_config = DiscountConfig(
            discount_type=DiscountType.RESERVED,
            discount_percent=0.20,
        )

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=12,
            projection_years=5,
            discount_config=discount_config,
        )

        assert projection.yearly_projections[0].discount_multiplier == 0.80
        assert projection.total_tco == pytest.approx(48000.0, rel=0.001)

    def test_calculate_tco_with_delayed_discount(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):
        discount_config = DiscountConfig(
            discount_type=DiscountType.COMMITTED_USE,
            discount_percent=0.25,
            effective_start_year=2,
        )

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=12,
            projection_years=5,
            discount_config=discount_config,
        )

        assert projection.yearly_projections[0].discount_multiplier == 1.0
        assert projection.yearly_projections[1].discount_multiplier == 0.75
        assert projection.total_tco == pytest.approx(48000.0, rel=0.001)

    def test_calculate_tco_with_growth_and_discount(
        self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost
    ):

        growth_config = GrowthConfig(model=GrowthModel.COMPOUND, annual_rate=0.10)
        discount_config = DiscountConfig(
            discount_type=DiscountType.RESERVED,
            discount_percent=0.20,
        )

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=12,
            projection_years=3,
            growth_config=growth_config,
            discount_config=discount_config,
        )

        assert projection.yearly_projections[0].projected_cost == pytest.approx(9600.0, rel=0.001)
        assert projection.yearly_projections[1].projected_cost == pytest.approx(10560.0, rel=0.001)
        assert projection.yearly_projections[2].projected_cost == pytest.approx(11616.0, rel=0.001)

    def test_calculate_tco_custom_platform(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=1,
            platform="databricks",
        )

        assert projection.platform == "databricks"

    def test_calculate_tco_custom_start_year(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=1,
            start_year=2030,
            projection_years=3,
        )

        assert projection.start_year == 2030
        assert projection.yearly_projections[0].calendar_year == 2030
        assert projection.yearly_projections[1].calendar_year == 2031
        assert projection.yearly_projections[2].calendar_year == 2032

    def test_calculate_tco_metadata(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=12,
        )

        assert "benchmark_run_cost" in projection.metadata
        assert projection.metadata["benchmark_run_cost"] == 1000.0
        assert projection.metadata["annual_runs"] == 12
        assert "generated_at" in projection.metadata

    def test_calculate_tco_from_annual_cost(self, calculator: TCOCalculator):

        projection = calculator.calculate_tco_from_annual_cost(
            annual_cost=50000.0,
            platform="redshift",
            projection_years=3,
        )

        assert projection.base_annual_cost == 50000.0
        assert projection.platform == "redshift"
        assert projection.total_tco == pytest.approx(150000.0, rel=0.001)

    def test_calculate_tco_cumulative_cost(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):

        growth_config = GrowthConfig(model=GrowthModel.LINEAR, annual_rate=0.20)

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=1,
            projection_years=3,
            growth_config=growth_config,
        )

        assert projection.yearly_projections[0].cumulative_cost == pytest.approx(1000.0, rel=0.001)
        assert projection.yearly_projections[1].cumulative_cost == pytest.approx(2200.0, rel=0.001)
        assert projection.yearly_projections[2].cumulative_cost == pytest.approx(3600.0, rel=0.001)
        assert projection.total_tco == projection.yearly_projections[-1].cumulative_cost

    def test_calculate_tco_monthly_cost(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=12,
            projection_years=1,
        )

        assert projection.yearly_projections[0].monthly_cost == pytest.approx(1000.0, rel=0.001)

    def test_calculate_tco_average_annual_cost(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):

        growth_config = GrowthConfig(model=GrowthModel.COMPOUND, annual_rate=0.10)

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=1,
            projection_years=5,
            growth_config=growth_config,
        )

        expected_average = projection.total_tco / 5
        assert projection.average_annual_cost == pytest.approx(expected_average, rel=0.001)


class TestTCOCalculatorBudgetAlerts:
    @pytest.fixture
    def calculator(self) -> TCOCalculator:
        return TCOCalculator()

    @pytest.fixture
    def sample_benchmark_cost(self) -> BenchmarkCost:
        return BenchmarkCost(
            total_cost=10000.0,
            currency="USD",
            platform_details={"platform": "snowflake"},
        )

    def test_add_budget_threshold(self, calculator: TCOCalculator):
        threshold = BudgetThreshold(name="warning", amount=100000.0)
        calculator.add_budget_threshold(threshold)

        assert len(calculator._budget_thresholds) == 1
        assert calculator._budget_thresholds[0].name == "warning"

    def test_clear_budget_thresholds(self, calculator: TCOCalculator):
        calculator.add_budget_threshold(BudgetThreshold(name="test1", amount=100.0))
        calculator.add_budget_threshold(BudgetThreshold(name="test2", amount=200.0))

        assert len(calculator._budget_thresholds) == 2

        calculator.clear_budget_thresholds()

        assert len(calculator._budget_thresholds) == 0

    def test_annual_budget_alert_triggered(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):
        calculator.add_budget_threshold(BudgetThreshold(name="annual_limit", amount=15000.0, period="annual"))

        growth_config = GrowthConfig(model=GrowthModel.COMPOUND, annual_rate=0.60)

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=1,
            projection_years=3,
            growth_config=growth_config,
        )

        assert len(projection.budget_alerts) == 1
        assert projection.budget_alerts[0].threshold.name == "annual_limit"
        assert projection.budget_alerts[0].year == 2

    def test_monthly_budget_alert_triggered(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):
        calculator.add_budget_threshold(BudgetThreshold(name="monthly_limit", amount=800.0, period="monthly"))

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=1,
            projection_years=1,
        )

        assert len(projection.budget_alerts) == 1
        assert projection.budget_alerts[0].threshold.name == "monthly_limit"
        assert projection.budget_alerts[0].period == "monthly"

    def test_total_budget_alert_triggered(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):
        calculator.add_budget_threshold(BudgetThreshold(name="total_limit", amount=25000.0, period="total"))

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=1,
            projection_years=3,
        )

        assert len(projection.budget_alerts) == 1
        assert projection.budget_alerts[0].threshold.name == "total_limit"
        assert projection.budget_alerts[0].period == "total"

    def test_no_alert_when_under_budget(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):
        calculator.add_budget_threshold(BudgetThreshold(name="high_limit", amount=1000000.0, period="annual"))

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=1,
            projection_years=5,
        )

        assert len(projection.budget_alerts) == 0

    def test_multiple_thresholds(self, calculator: TCOCalculator, sample_benchmark_cost: BenchmarkCost):
        calculator.add_budget_threshold(BudgetThreshold(name="warning", amount=5000.0, period="annual"))
        calculator.add_budget_threshold(BudgetThreshold(name="critical", amount=8000.0, period="annual"))

        projection = calculator.calculate_tco(
            benchmark_cost=sample_benchmark_cost,
            annual_runs=1,
            projection_years=1,
        )

        assert len(projection.budget_alerts) == 2


class TestTCOCalculatorPlatformComparison:
    def test_compare_platforms_basic(self):

        calculator = TCOCalculator()

        projections = [
            TCOProjection(platform="snowflake", base_annual_cost=100000.0, total_tco=500000.0, projection_years=5),
            TCOProjection(platform="bigquery", base_annual_cost=80000.0, total_tco=400000.0, projection_years=5),
            TCOProjection(platform="redshift", base_annual_cost=120000.0, total_tco=600000.0, projection_years=5),
        ]

        comparison = calculator.compare_platforms(projections)

        assert comparison["cheapest_platform"] == "bigquery"
        assert comparison["most_expensive_platform"] == "redshift"
        assert comparison["max_savings"] == 200000.0
        assert len(comparison["rankings"]) == 3
        assert comparison["rankings"][0]["platform"] == "bigquery"
        assert comparison["rankings"][0]["rank"] == 1

    def test_compare_platforms_savings_calculation(self):

        calculator = TCOCalculator()

        projections = [
            TCOProjection(platform="platform_a", base_annual_cost=100.0, total_tco=100.0),
            TCOProjection(platform="platform_b", base_annual_cost=80.0, total_tco=80.0),
        ]

        comparison = calculator.compare_platforms(projections)

        assert comparison["rankings"][0]["platform"] == "platform_b"
        assert comparison["rankings"][0]["savings_vs_max"] == 20.0
        assert comparison["rankings"][0]["savings_percent"] == 20.0

        assert comparison["rankings"][1]["platform"] == "platform_a"
        assert comparison["rankings"][1]["savings_vs_max"] == 0.0

    def test_compare_platforms_empty(self):

        calculator = TCOCalculator()

        comparison = calculator.compare_platforms([])

        assert "error" in comparison

    def test_compare_platforms_single(self):

        calculator = TCOCalculator()

        projections = [TCOProjection(platform="only_one", base_annual_cost=100.0, total_tco=500.0)]

        comparison = calculator.compare_platforms(projections)

        assert comparison["cheapest_platform"] == "only_one"
        assert comparison["most_expensive_platform"] == "only_one"
        assert comparison["max_savings"] == 0.0


class TestCreateStandardTCOScenarios:
    @pytest.fixture
    def sample_benchmark_cost(self) -> BenchmarkCost:
        return BenchmarkCost(
            total_cost=1000.0,
            currency="USD",
            platform_details={"platform": "test"},
        )

    def test_creates_three_scenarios(self, sample_benchmark_cost: BenchmarkCost):

        scenarios = create_standard_tco_scenarios(sample_benchmark_cost)

        assert len(scenarios) == 3
        assert "conservative" in scenarios
        assert "moderate" in scenarios
        assert "aggressive" in scenarios

    def test_conservative_scenario(self, sample_benchmark_cost: BenchmarkCost):

        scenarios = create_standard_tco_scenarios(sample_benchmark_cost, annual_runs=12)

        conservative = scenarios["conservative"]

        assert conservative.growth_config.model == GrowthModel.NONE
        assert conservative.discount_config.discount_type == DiscountType.NONE
        assert conservative.total_tco == pytest.approx(60000.0, rel=0.001)

    def test_moderate_scenario(self, sample_benchmark_cost: BenchmarkCost):

        scenarios = create_standard_tco_scenarios(sample_benchmark_cost, annual_runs=12)

        moderate = scenarios["moderate"]

        assert moderate.growth_config.model == GrowthModel.COMPOUND
        assert moderate.growth_config.annual_rate == 0.10
        assert moderate.discount_config.discount_type == DiscountType.RESERVED
        assert moderate.discount_config.discount_percent == 0.15

    def test_aggressive_scenario(self, sample_benchmark_cost: BenchmarkCost):

        scenarios = create_standard_tco_scenarios(sample_benchmark_cost, annual_runs=12)

        aggressive = scenarios["aggressive"]

        assert aggressive.growth_config.model == GrowthModel.COMPOUND
        assert aggressive.growth_config.annual_rate == 0.25
        assert aggressive.discount_config.discount_type == DiscountType.NONE

        conservative = scenarios["conservative"]
        assert aggressive.total_tco > conservative.total_tco

    def test_all_scenarios_have_5_year_projection(self, sample_benchmark_cost: BenchmarkCost):
        scenarios = create_standard_tco_scenarios(sample_benchmark_cost)

        for scenario in scenarios.values():
            assert scenario.projection_years == 5
            assert len(scenario.yearly_projections) == 5

    def test_custom_annual_runs(self, sample_benchmark_cost: BenchmarkCost):

        scenarios = create_standard_tco_scenarios(sample_benchmark_cost, annual_runs=52)

        assert scenarios["conservative"].base_annual_cost == 52000.0


