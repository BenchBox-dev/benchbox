from importlib import resources

import pytest
import yaml

from benchbox.core.cost import pricing
from benchbox.core.cost.calculator import BYTES_PER_UNIT
from benchbox.core.cost.pricing import (
    BYTE_PRICED_TABLES,
    PROVENANCE_METHODS,
    resolve_athena_price_per_tb,
    resolve_bigquery_price_per_tb,
    resolve_databricks_dbu_price,
    resolve_firebolt_fbu_price,
    resolve_redshift_node_price,
    resolve_snowflake_credit_price,
    resolve_synapse_dedicated_price,
    resolve_synapse_serverless_price_per_tb,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

PRICE_TABLES = [
    "snowflake_credit_prices",
    "athena_price_per_tb",
    "bigquery_on_demand_prices",
    "redshift_node_prices",
    "databricks_dbu_prices",
    "synapse_serverless_price_per_tb",
    "synapse_dedicated_dwu_prices",
    "fabric_cu_prices",
    "fabric_sku_cu_map",
    "firebolt_node_fbu_rates",
    "firebolt_fbu_price",
]

REQUIRED_PROVENANCE_KEYS = {"source", "retrieved", "upstream_published", "method", "verified_regions"}


def _raw_yaml() -> dict:
    with resources.files("benchbox.core.cost").joinpath("pricing_data.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.mark.parametrize("table", PRICE_TABLES)
def test_every_price_table_has_provenance(table):
    entry = pricing.PRICE_TABLE_PROVENANCE.get(table)
    assert isinstance(entry, dict), f"missing provenance block for {table!r}"
    assert set(entry) >= REQUIRED_PROVENANCE_KEYS, f"incomplete provenance for {table!r}"
    assert entry["method"] in PROVENANCE_METHODS, f"unknown provenance method for {table!r}"
    assert isinstance(entry["source"], str) and entry["source"], f"missing source for {table!r}"
    assert isinstance(entry["verified_regions"], list), f"verified_regions must be a list for {table!r}"


@pytest.mark.parametrize("table", sorted(BYTE_PRICED_TABLES))
def test_every_byte_priced_table_declares_unit(table):
    assert pricing.PRICE_TABLE_UNITS.get(table) in BYTES_PER_UNIT, f"missing unit for {table!r}"


def test_units_resolve_to_defined_divisors():
    assert set(pricing.PRICE_TABLE_UNITS.values()) <= set(BYTES_PER_UNIT)
    assert BYTES_PER_UNIT["tebibyte"] == 1024**4
    assert BYTES_PER_UNIT["terabyte"] == 10**12


def test_declared_units_match_expected_definitions():
    assert pricing.PRICE_TABLE_UNITS["bigquery_on_demand_prices"] == "tebibyte"
    assert pricing.PRICE_TABLE_UNITS["athena_price_per_tb"] == "terabyte"
    assert pricing.PRICE_TABLE_UNITS["synapse_serverless_price_per_tb"] == "terabyte"


def test_metadata_no_longer_asserts_a_refresh_that_did_not_occur():
    metadata = _raw_yaml()["metadata"]
    assert "last_updated" not in metadata
    assert pricing.PRICING_LAST_UPDATED == "unknown"
    assert pricing.get_pricing_age_days() is None
    assert pricing.is_pricing_stale() is False


def test_golden_bigquery_us_multi_region():
    assert resolve_bigquery_price_per_tb("us").value == 6.25


def test_golden_firebolt_fbu_price():
    assert resolve_firebolt_fbu_price().value == 0.23


def test_golden_redshift_rg_12xlarge():
    assert resolve_redshift_node_price("rg.12xlarge", "us-east-1").value == 9.128


def test_golden_synapse_dedicated_dw100c():
    assert resolve_synapse_dedicated_price("dw100c", "eastus").value == 1.51


def test_golden_snowflake_standard_us():
    assert resolve_snowflake_credit_price("standard", "aws", "us-east-1").value == 2.0


def test_golden_databricks_sql_serverless():
    assert resolve_databricks_dbu_price("aws", "premium", "serverless_sql").value == 0.70


def test_golden_athena_regional_rates():
    assert resolve_athena_price_per_tb("us-east-1").value == 5.0
    assert resolve_athena_price_per_tb("sa-east-1").value == 9.0


def test_golden_synapse_serverless_regional_rates():
    assert resolve_synapse_serverless_price_per_tb("eastus").value == 5.0
    assert resolve_synapse_serverless_price_per_tb("southeastasia").value == 6.75
    assert resolve_synapse_serverless_price_per_tb("canadacentral").value == 5.5
    assert resolve_synapse_serverless_price_per_tb("brazilsouth").value == 9.0


