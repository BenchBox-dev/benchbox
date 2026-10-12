import pytest

import benchbox.core.cost.calculator as calculator_module
from benchbox.core.cost.calculator import CostCalculator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _serverless_databricks_config() -> dict:
    return {
        "cloud": "azure",
        "tier": "enterprise",
        "workload_type": "serverless_sql",
        "cluster_size_dbu_per_hour": 8.0,
        "warehouse_size": "Medium",
        "region": "eastus",
    }


def test_fallback_priced_databricks_serverless_run_is_unavailable() -> None:
    calculator = CostCalculator()
    query_cost = calculator.calculate_query_cost(
        "databricks",
        {"execution_time_seconds": 3600.0},
        _serverless_databricks_config(),
    )
    assert query_cost is not None
    assert query_cost.compute_cost == pytest.approx(4.40)
    assert "price_unavailable" in query_cost.pricing_details

    phase_cost = calculator.calculate_phase_cost("power_test", [query_cost])
    benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": "databricks"})
    normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(
        "databricks", benchmark_cost, _serverless_databricks_config()
    )

    assert normalized_cost.cost_status == "unavailable"
    assert normalized_cost.normalized_cost_usd is None
    assert any("databricks_dbu_prices" in warning for warning in warnings)
    assert normalized_cost.unavailability_reasons == tuple(warnings)


def test_fallback_priced_snowflake_run_is_unavailable() -> None:
    calculator = CostCalculator()
    config = {"edition": "nonexistent", "cloud": "aws", "region": "us-east-1"}
    query_cost = calculator.calculate_query_cost("snowflake", {"credits_used": 1.0}, config)
    assert query_cost is not None
    assert "price_unavailable" in query_cost.pricing_details

    phase_cost = calculator.calculate_phase_cost("power_test", [query_cost])
    benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": "snowflake"})
    normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost("snowflake", benchmark_cost, config)

    assert normalized_cost.cost_status == "unavailable"
    assert normalized_cost.normalized_cost_usd is None
    assert any("snowflake_credit_prices" in warning for warning in warnings)
    assert normalized_cost.unavailability_reasons == tuple(warnings)


def test_stale_pricing_table_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calculator_module, "get_pricing_age_days", lambda table=None: 313)

    calculator = CostCalculator()
    phase_cost = calculator.calculate_phase_cost(
        "power_test",
        [calculator.calculate_query_cost("snowflake", {"credits_used": 0.5}, {"edition": "standard"})],
    )
    benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": "snowflake"})
    normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(
        "snowflake",
        benchmark_cost,
        {"edition": "standard", "cloud": "aws", "region": "us-east-1", "warehouse_size": "MEDIUM"},
    )

    assert normalized_cost.cost_status == "unavailable"
    assert normalized_cost.normalized_cost_usd is None
    assert any("pricing tables are stale" in warning for warning in warnings)


def test_fresh_pricing_table_emits_no_staleness_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calculator_module, "get_pricing_age_days", lambda table=None: 1)
    calculator = CostCalculator()
    phase_cost = calculator.calculate_phase_cost(
        "power_test",
        [calculator.calculate_query_cost("snowflake", {"credits_used": 0.5}, {"edition": "standard"})],
    )
    benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": "snowflake"})
    normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(
        "snowflake",
        benchmark_cost,
        {"edition": "standard", "cloud": "aws", "region": "us-east-1", "warehouse_size": "MEDIUM"},
    )

    assert normalized_cost.cost_status == "normalized"
    assert not any("days old" in warning for warning in warnings)


def test_athena_sao_paulo_publishes_regional_rate() -> None:
    calculator = CostCalculator()
    query_cost = calculator.calculate_query_cost("athena", {"data_scanned_bytes": 10**12}, {"region": "sa-east-1"})
    assert query_cost is not None
    assert query_cost.compute_cost == pytest.approx(9.0)

    phase_cost = calculator.calculate_phase_cost("power_test", [query_cost])
    benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": "athena"})
    normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(
        "athena", benchmark_cost, {"region": "sa-east-1", "cloud": "aws"}
    )

    assert normalized_cost.cost_status == "normalized"
    assert normalized_cost.normalized_cost_usd == pytest.approx(9.0)
    assert not any("athena_price_per_tb" in warning for warning in warnings)


def test_athena_unlisted_region_is_unavailable() -> None:
    calculator = CostCalculator()
    query_cost = calculator.calculate_query_cost("athena", {"data_scanned_bytes": 10**12}, {"region": "moon-east-1"})
    assert query_cost is not None
    assert query_cost.compute_cost == pytest.approx(5.0)

    phase_cost = calculator.calculate_phase_cost("power_test", [query_cost])
    benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": "athena"})
    normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(
        "athena", benchmark_cost, {"region": "moon-east-1", "cloud": "aws"}
    )

    assert normalized_cost.cost_status == "unavailable"
    assert normalized_cost.normalized_cost_usd is None
    assert any("athena_price_per_tb" in warning for warning in warnings)


def test_synapse_serverless_unlisted_region_is_unavailable() -> None:
    calculator = CostCalculator()
    query_cost = calculator.calculate_query_cost(
        "synapse",
        {"bytes_processed": 1024**4},
        {"mode": "serverless", "region": "moon-central9"},
    )
    assert query_cost is not None

    phase_cost = calculator.calculate_phase_cost("power_test", [query_cost])
    benchmark_cost = calculator.calculate_benchmark_cost([phase_cost], {"platform": "synapse"})
    normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(
        "synapse",
        benchmark_cost,
        {"mode": "serverless", "region": "moon-central9", "cloud": "azure"},
    )

    assert normalized_cost.cost_status == "unavailable"
    assert normalized_cost.normalized_cost_usd is None
    assert any("synapse_serverless_price_per_tb" in warning for warning in warnings)


def test_verified_regions_stay_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calculator_module, "get_pricing_age_days", lambda table=None: 1)
    calculator = CostCalculator()

    athena_cost = calculator.calculate_query_cost("athena", {"data_scanned_bytes": 1024**4}, {"region": "us-east-1"})
    assert athena_cost is not None
    athena_bench = calculator.calculate_benchmark_cost(
        [calculator.calculate_phase_cost("power_test", [athena_cost])], {"platform": "athena"}
    )
    athena_norm, _ = calculator.calculate_normalized_benchmark_cost(
        "athena", athena_bench, {"region": "us-east-1", "cloud": "aws"}
    )
    assert athena_norm.cost_status == "normalized"

    synapse_cost = calculator.calculate_query_cost(
        "synapse",
        {"bytes_processed": 1024**4},
        {"mode": "serverless", "region": "eastus"},
    )
    assert synapse_cost is not None
    synapse_bench = calculator.calculate_benchmark_cost(
        [calculator.calculate_phase_cost("power_test", [synapse_cost])], {"platform": "synapse"}
    )
    synapse_norm, _ = calculator.calculate_normalized_benchmark_cost(
        "synapse",
        synapse_bench,
        {"mode": "serverless", "region": "eastus", "cloud": "azure"},
    )
    assert synapse_norm.cost_status == "normalized"

    databricks_cost = calculator.calculate_query_cost(
        "databricks",
        {"execution_time_seconds": 3600.0},
        {
            "cloud": "aws",
            "tier": "premium",
            "workload_type": "serverless_sql",
            "cluster_size_dbu_per_hour": 1.0,
        },
    )
    assert databricks_cost is not None
    databricks_bench = calculator.calculate_benchmark_cost(
        [calculator.calculate_phase_cost("power_test", [databricks_cost])],
        {"platform": "databricks"},
    )
    databricks_norm, databricks_warnings = calculator.calculate_normalized_benchmark_cost(
        "databricks",
        databricks_bench,
        {
            "cloud": "aws",
            "tier": "premium",
            "workload_type": "serverless_sql",
            "cluster_size_dbu_per_hour": 1.0,
            "warehouse_size": "2X-Small",
            "region": "us-east-1",
        },
    )
    assert databricks_norm.cost_status == "normalized"
    assert databricks_warnings == []


def test_fallback_marker_emits_one_warning_per_table() -> None:
    calculator = CostCalculator()
    query_costs = [
        calculator.calculate_query_cost("athena", {"data_scanned_bytes": 1024**4}, {"region": "moon-east-1"})
        for _ in range(3)
    ]
    assert all(query_cost is not None for query_cost in query_costs)
    benchmark_cost = calculator.calculate_benchmark_cost(
        [calculator.calculate_phase_cost("power_test", [qc for qc in query_costs if qc is not None])],
        {"platform": "athena"},
    )
    normalized_cost, warnings = calculator.calculate_normalized_benchmark_cost(
        "athena", benchmark_cost, {"region": "moon-east-1", "cloud": "aws"}
    )

    assert normalized_cost.cost_status == "unavailable"
    fallback_warnings = [warning for warning in warnings if "fallback pricing used" in warning]
    assert len(fallback_warnings) == 1
