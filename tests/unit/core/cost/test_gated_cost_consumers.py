from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.analysis.comparison import PlatformComparison
from benchbox.core.cost.integration import add_cost_estimation_to_results
from benchbox.core.cost.models import BenchmarkCost, PhaseCost
from benchbox.core.cost.optimizer import CostOptimizer
from benchbox.core.cost.tco import TCOCalculator
from benchbox.core.results.database import ResultDatabase
from benchbox.core.results.models import ExecutionPhases, PowerTestPhase, QueryExecution, SetupPhase
from benchbox.core.results.normalizer import normalize_result_dict
from benchbox.core.visualization.result_plotter import ResultPlotter
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_UNAVAILABLE_SUMMARY = {
    "total_cost": 4.40,
    "cost_model": "estimated",
    "normalized_cost": {"cost_status": "unavailable", "normalized_cost_usd": None},
}

_NORMALIZED_SUMMARY = {
    "total_cost": 4.40,
    "cost_model": "actual",
    "normalized_cost": {"cost_status": "normalized", "normalized_cost_usd": "4.40"},
}


def _athena_results(region: str):
    return make_benchmark_results(
        benchmark_name="TPC-H",
        platform="athena",
        scale_factor=1,
        duration_seconds=10.0,
        total_queries=1,
        successful_queries=1,
        platform_info={"platform_type": "athena", "region": region},
        query_results=[
            {
                "query_id": "Q1",
                "execution_time": 1.5,
                "resource_usage": {"data_scanned_bytes": 1024**4},
            },
        ],
    )


def _phases_with_cost(per_query_cost: float | None) -> ExecutionPhases:
    ts = datetime.now().isoformat()
    return ExecutionPhases(
        setup=SetupPhase(),
        power_test=PowerTestPhase(
            start_time=ts,
            end_time=ts,
            duration_ms=125,
            query_executions=[
                QueryExecution(
                    query_id="1",
                    stream_id="power",
                    execution_order=1,
                    execution_time_ms=50,
                    status="SUCCESS",
                    rows_returned=10,
                    cost=per_query_cost,
                    iteration=1,
                ),
            ],
            geometric_mean_time=0.05,
            power_at_size=1000.0,
        ),
    )


def _comparable_result(platform: str, cost_summary: dict | None):
    return make_benchmark_results(
        benchmark_name="TPC-H",
        platform=platform,
        scale_factor=10.0,
        duration_seconds=10.0,
        total_queries=22,
        successful_queries=22,
        total_execution_time=10.0,
        average_query_time=0.45,
        query_results=[{"query_id": "Q1", "execution_time_ms": 450, "status": "success"}],
        cost_summary=cost_summary,
    )


class TestPerQueryCostGatedAtStamp:
    def test_fallback_region_stamps_no_per_query_cost(self) -> None:

        results = add_cost_estimation_to_results(_athena_results("moon-east-1"))

        stamped = results.query_results[0]
        assert stamped["cost"] is None
        assert "cost_status" not in stamped
        assert results.cost_summary["normalized_cost"]["cost_status"] == "unavailable"

    def test_verified_region_stamps_per_query_cost(self) -> None:
        results = add_cost_estimation_to_results(_athena_results("us-east-1"))

        stamped = results.query_results[0]

        assert stamped["cost"] == pytest.approx((1024**4) / (10**12) * 5.0)
        assert "cost_status" not in stamped
        assert results.cost_summary["normalized_cost"]["cost_status"] == "normalized"


class TestDatabaseGatesUnavailableCosts:
    def test_unavailable_run_persists_no_costs(self, tmp_path) -> None:
        db = ResultDatabase(tmp_path / "test.db")
        result = make_benchmark_results(
            benchmark_name="TPC-H",
            platform="Snowflake",
            scale_factor=1.0,
            execution_id="gated-exec-001",
            duration_seconds=10.0,
            total_queries=22,
            successful_queries=22,
            platform_info={"platform_version": "1.0.0"},
            cost_summary=dict(_UNAVAILABLE_SUMMARY),
            execution_phases=_phases_with_cost(4.40),
        )

        result_id = db.store_result(result)

        assert db.get_result_by_id(result_id).total_cost is None
        assert db.get_queries(result_id)[0].cost is None

    def test_normalized_run_persists_costs(self, tmp_path) -> None:
        db = ResultDatabase(tmp_path / "test.db")
        result = make_benchmark_results(
            benchmark_name="TPC-H",
            platform="Snowflake",
            scale_factor=1.0,
            execution_id="gated-exec-002",
            duration_seconds=10.0,
            total_queries=22,
            successful_queries=22,
            platform_info={"platform_version": "1.0.0"},
            cost_summary=dict(_NORMALIZED_SUMMARY),
            execution_phases=_phases_with_cost(4.40),
        )

        result_id = db.store_result(result)

        assert db.get_result_by_id(result_id).total_cost == pytest.approx(4.40)
        assert db.get_queries(result_id)[0].cost == pytest.approx(4.40)


class TestComparisonExcludesUnavailableRuns:
    def test_unavailable_platform_has_no_ranking_or_savings(self) -> None:
        comparison = PlatformComparison(
            [
                _comparable_result("snowflake", dict(_NORMALIZED_SUMMARY)),
                _comparable_result("redshift", dict(_UNAVAILABLE_SUMMARY)),
            ]
        )

        analysis = comparison._analyze_cost_performance()

        assert analysis is not None
        assert analysis.platforms == ["snowflake"]
        assert "redshift" not in analysis.cost_rankings
        assert "redshift" not in analysis.potential_savings


class TestPlotterOmitsUnavailableCosts:
    def test_unavailable_run_carries_no_plot_cost(self) -> None:
        normalized = ResultPlotter._normalize_benchmark_result(
            _comparable_result("snowflake", dict(_UNAVAILABLE_SUMMARY))
        )

        assert normalized.cost_total is None

    def test_normalized_run_carries_plot_cost(self) -> None:
        normalized = ResultPlotter._normalize_benchmark_result(
            _comparable_result("snowflake", dict(_NORMALIZED_SUMMARY))
        )

        assert normalized.cost_total == pytest.approx(4.40)


class TestNormalizerGatesBundleTotals:
    def _v2_bundle(self, normalized_block: dict | None) -> dict:
        bundle = {
            "version": "2.2",
            "benchmark": {"name": "TPC-H", "scale_factor": 1.0},
            "platform": {"name": "Snowflake"},
            "run": {"id": "exec-1", "timestamp": "2026-09-18T00:00:00"},
            "summary": {"timing": {}, "queries": {"total": 1, "passed": 1, "failed": 0}},
            "cost": {"total_usd": 4.40, "model": "estimated"},
            "queries": [],
        }
        if normalized_block is not None:
            bundle["normalized_cost"] = normalized_block
        return bundle

    def test_v2_unavailable_bundle_yields_no_cost_total(self) -> None:
        bundle = self._v2_bundle({"cost_status": "unavailable", "normalized_cost_usd": None})

        assert normalize_result_dict(bundle).cost_total is None

    def test_v2_legacy_bundle_without_block_passes_through(self) -> None:
        assert normalize_result_dict(self._v2_bundle(None)).cost_total == pytest.approx(4.40)

    def test_v1_unavailable_bundle_yields_no_cost_total(self) -> None:
        data = {
            "benchmark": {"name": "TPC-H", "scale_factor": 1.0},
            "execution": {"platform": "Snowflake", "id": "exec-1", "timestamp": "2026-09-18T00:00:00"},
            "results": {"timing": {}, "queries": {"total": 1, "successful": 1, "failed": 0}},
            "cost_summary": {"total_cost": 4.40},
            "normalized_cost": {"cost_status": "unavailable", "normalized_cost_usd": None},
        }

        assert normalize_result_dict(data).cost_total is None


class TestTcoRefusesUnavailableCosts:
    def _cost(self, warnings: list[str]) -> BenchmarkCost:
        return BenchmarkCost(
            total_cost=150.0,
            currency="USD",
            phase_costs=[PhaseCost(phase_name="power_test", total_cost=150.0, query_count=1)],
            platform_details={"platform": "snowflake"},
            warnings=warnings,
        )

    def test_unavailable_cost_raises_with_reason(self) -> None:
        calculator = TCOCalculator()
        cost = self._cost(["normalized cost unavailable: fallback pricing used for snowflake_credit_prices (x)"])

        with pytest.raises(ValueError, match="unavailable"):
            calculator.calculate_tco(cost, annual_runs=12)

    def test_verified_cost_projects(self) -> None:
        projection = TCOCalculator().calculate_tco(self._cost([]), annual_runs=12)

        assert projection.base_annual_cost == pytest.approx(1800.0)


class TestOptimizerSuppressesFallbackPrices:
    def _redshift_cost(self, warnings: list[str] | None = None) -> BenchmarkCost:
        return BenchmarkCost(
            total_cost=150.0,
            currency="USD",
            phase_costs=[PhaseCost(phase_name="power_test", total_cost=150.0, query_count=1)],
            platform_details={"platform": "redshift"},
            warnings=warnings or [],
        )

    def test_unknown_node_type_suppresses_region_rule_with_reason(self) -> None:
        report = CostOptimizer().analyze(
            benchmark_cost=self._redshift_cost(),
            platform_config={"platform": "redshift", "region": "us-east-1", "node_type": "mystery.xlarge"},
            annual_runs=12,
        )

        assert report.recommendations == []
        suppressed = {entry["rule"]: entry["reason"] for entry in report.metadata["suppressed_rules"]}
        assert "redshift_region" in suppressed
        assert "fallback" in suppressed["redshift_region"]

    def test_unavailable_input_suppresses_every_rule(self) -> None:
        cost = self._redshift_cost(["normalized cost unavailable: fallback pricing used for redshift_node_prices (x)"])
        report = CostOptimizer().analyze(
            benchmark_cost=cost,
            platform_config={"platform": "redshift", "region": "us-east-1", "node_type": "dc2.large"},
            annual_runs=12,
        )

        assert report.recommendations == []
        assert report.total_potential_savings == 0.0
        suppressed_rules = {entry["rule"] for entry in report.metadata["suppressed_rules"]}
        assert suppressed_rules == {name for name, _ in CostOptimizer()._rules}

    def test_verified_prices_still_recommend(self) -> None:
        report = CostOptimizer().analyze(
            benchmark_cost=self._redshift_cost(),
            platform_config={"platform": "redshift", "region": "eu-west-1", "node_type": "dc2.large"},
            annual_runs=12,
        )

        assert any(rec.id == "redshift-region-optimization" for rec in report.recommendations)


