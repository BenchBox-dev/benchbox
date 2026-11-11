import pytest

from benchbox.core.cost.pricing import (
    BIGQUERY_ON_DEMAND_PRICES,
    REDSHIFT_NODE_PRICES,
    SNOWFLAKE_CREDIT_PRICES,
    _map_region_to_tier,
    resolve_bigquery_price_per_tb,
    resolve_redshift_node_price,
    resolve_snowflake_credit_price,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestRegionMapping:
    def test_aws_us_regions(self):
        us_regions = ["us-east-1", "us-east-2", "us-west-1", "us-west-2"]
        for region in us_regions:
            assert _map_region_to_tier(region) == "us", f"Failed for {region}"

    def test_azure_us_regions(self):
        us_regions = ["eastus", "eastus2", "westus", "westus2", "centralus"]
        for region in us_regions:
            assert _map_region_to_tier(region) == "us", f"Failed for {region}"

    def test_gcp_us_regions(self):
        us_regions = ["us-central1", "us-east1", "us-west1", "us-west2"]
        for region in us_regions:
            assert _map_region_to_tier(region) == "us", f"Failed for {region}"

    def test_aws_canada_regions(self):
        assert _map_region_to_tier("ca-central-1") == "ca"

    def test_azure_canada_regions(self):
        ca_regions = ["canadacentral", "canadaeast"]
        for region in ca_regions:
            assert _map_region_to_tier(region) == "ca", f"Failed for {region}"

    def test_gcp_canada_regions(self):
        ca_regions = ["northamerica-northeast1", "northamerica-northeast2"]
        for region in ca_regions:
            assert _map_region_to_tier(region) == "ca", f"Failed for {region}"

    def test_aws_eu_regions(self):
        eu_regions = [
            "eu-west-1",
            "eu-west-2",
            "eu-west-3",
            "eu-central-1",
            "eu-central-2",
            "eu-north-1",
            "eu-south-1",
        ]
        for region in eu_regions:
            assert _map_region_to_tier(region) == "eu", f"Failed for {region}"

    def test_azure_eu_regions(self):
        eu_regions = [
            "northeurope",
            "westeurope",
            "francecentral",
            "germanywestcentral",
            "uksouth",
            "ukwest",
        ]
        for region in eu_regions:
            assert _map_region_to_tier(region) == "eu", f"Failed for {region}"

    def test_gcp_eu_regions(self):
        eu_regions = [
            "europe-west1",
            "europe-west2",
            "europe-west4",
            "europe-central2",
            "europe-north1",
        ]
        for region in eu_regions:
            assert _map_region_to_tier(region) == "eu", f"Failed for {region}"

    def test_aws_ap_regions(self):
        ap_regions = [
            "ap-south-1",
            "ap-northeast-1",
            "ap-southeast-1",
            "ap-southeast-2",
            "ap-east-1",
        ]
        for region in ap_regions:
            assert _map_region_to_tier(region) == "ap", f"Failed for {region}"

    def test_azure_ap_regions(self):
        ap_regions = [
            "eastasia",
            "southeastasia",
            "japaneast",
            "australiaeast",
            "centralindia",
        ]
        for region in ap_regions:
            assert _map_region_to_tier(region) == "ap", f"Failed for {region}"

    def test_gcp_ap_regions(self):
        ap_regions = [
            "asia-east1",
            "asia-northeast1",
            "asia-southeast1",
            "australia-southeast1",
            "asia-south1",
        ]
        for region in ap_regions:
            assert _map_region_to_tier(region) == "ap", f"Failed for {region}"

    def test_middle_east_regions(self):
        me_regions = [
            "me-south-1",
            "me-central-1",
            "uaenorth",
            "qatarcentral",
            "me-west1",
        ]
        for region in me_regions:
            assert _map_region_to_tier(region) == "other", f"Failed for {region}"

    def test_south_america_regions(self):
        sa_regions = [
            "sa-east-1",
            "brazilsouth",
            "southamerica-east1",
        ]
        for region in sa_regions:
            assert _map_region_to_tier(region) == "other", f"Failed for {region}"

    def test_africa_regions(self):
        africa_regions = [
            "af-south-1",
            "southafricanorth",
        ]
        for region in africa_regions:
            assert _map_region_to_tier(region) == "other", f"Failed for {region}"

    def test_case_insensitive(self):
        assert _map_region_to_tier("US-EAST-1") == "us"
        assert _map_region_to_tier("EU-WEST-1") == "eu"
        assert _map_region_to_tier("AP-SOUTHEAST-1") == "ap"

    def test_unknown_region_defaults_to_other(self):
        assert _map_region_to_tier("unknown-region-1") == "other"
        assert _map_region_to_tier("custom-region") == "other"


class TestSnowflakeRegionalPricing:
    def _standard(self, region: str) -> float:
        return resolve_snowflake_credit_price("standard", "aws", region).value

    def test_us_region_pricing_is_floor(self):
        price_us = self._standard("us-east-1")
        assert price_us > 0
        for region in ["eu-west-1", "ap-southeast-1", "ca-central-1", "me-south-1"]:
            assert price_us <= self._standard(region), f"US not floor vs {region}"

    def test_tier_ordering(self):
        assert self._standard("us-east-1") < self._standard("ca-central-1")
        assert self._standard("ca-central-1") < self._standard("eu-west-1")
        assert self._standard("eu-west-1") < self._standard("ap-southeast-1")
        assert self._standard("ap-southeast-1") < self._standard("me-south-1")

    def test_consistent_across_clouds(self):
        price_aws = resolve_snowflake_credit_price("standard", "aws", "us-east-1").value
        price_azure = resolve_snowflake_credit_price("standard", "azure", "eastus").value
        price_gcp = resolve_snowflake_credit_price("standard", "gcp", "us-central1").value

        assert price_aws == price_azure == price_gcp > 0


class TestBigQueryRegionalPricing:
    def test_multi_region_buckets_agree(self):
        price_us = resolve_bigquery_price_per_tb("us").value
        assert price_us > 0
        assert resolve_bigquery_price_per_tb("eu").value == price_us
        assert resolve_bigquery_price_per_tb("asia").value == price_us

        assert resolve_bigquery_price_per_tb("us-multi").value == price_us
        assert resolve_bigquery_price_per_tb("eu-multi").value == price_us
        assert resolve_bigquery_price_per_tb("asia-multi").value == price_us

    def test_captured_single_region_values(self):
        expected = {
            "us-east1": 6.25,
            "us-west2": 8.4375,
            "us-south1": 7.50,
            "europe-west1": 7.50,
            "europe-west2": 7.8125,
            "europe-west10": 9.625,
            "europe-west12": 8.0625,
            "asia-southeast1": 8.4375,
            "asia-east1": 7.1875,
            "asia-northeast1": 7.50,
            "australia-southeast1": 8.125,
            "southamerica-east1": 11.25,
            "southamerica-west1": 8.9375,
            "me-central1": 7.59375,
            "me-central2": 10.00,
            "me-west1": 7.50,
            "africa-south1": 7.50,
            "northamerica-south1": 6.8125,
            "asia-southeast3": 7.50,
            "asia-southeast4": 6.5625,
            "europe-north2": 6.5625,
        }
        for location, price in expected.items():
            resolution = resolve_bigquery_price_per_tb(location)
            assert resolution.value == price, location
            assert resolution.fallback_used is False, location

    def test_every_table_region_resolves_to_its_captured_price(self):
        for region, price in BIGQUERY_ON_DEMAND_PRICES.items():
            if region == "other":
                continue
            resolution = resolve_bigquery_price_per_tb(region)
            assert resolution.value == price, region
            assert resolve_bigquery_price_per_tb(region.upper()).value == price, region

    def test_unlisted_regions_route_to_buckets(self):

        assert resolve_bigquery_price_per_tb("europe-west5").value == BIGQUERY_ON_DEMAND_PRICES["eu-single"]
        assert resolve_bigquery_price_per_tb("us-central2").value == BIGQUERY_ON_DEMAND_PRICES["us-single"]

    def test_unknown_regions_resolve_to_other(self):
        other = BIGQUERY_ON_DEMAND_PRICES["other"]
        unknown = resolve_bigquery_price_per_tb("unknown-future-region")
        assert unknown.value == other
        assert unknown.fallback_used is True

    def test_other_fails_high(self):
        known = [price for region, price in BIGQUERY_ON_DEMAND_PRICES.items() if region != "other"]
        assert BIGQUERY_ON_DEMAND_PRICES["other"] >= max(known)

    def test_us_single_regions_match_multi_region(self):
        price_us = resolve_bigquery_price_per_tb("us").value
        assert resolve_bigquery_price_per_tb("us-central1").value == price_us
        assert resolve_bigquery_price_per_tb("us-east1").value == price_us

    def test_eu_asia_single_regions_carry_premium(self):
        price_us = resolve_bigquery_price_per_tb("us").value
        for location in ["europe-west1", "europe-north1", "asia-southeast1", "asia-northeast1"]:
            assert resolve_bigquery_price_per_tb(location).value > price_us, location

    def test_remote_buckets_carry_premium(self):
        price_us = resolve_bigquery_price_per_tb("us").value
        for location in ["australia-southeast1", "southamerica-east1", "me-west1"]:
            assert resolve_bigquery_price_per_tb(location).value >= price_us, location

    def test_bucket_boundaries(self):
        assert (
            resolve_bigquery_price_per_tb("australia-southeast1").value
            == BIGQUERY_ON_DEMAND_PRICES["australia-southeast1"]
        )
        assert (
            resolve_bigquery_price_per_tb("southamerica-east1").value == BIGQUERY_ON_DEMAND_PRICES["southamerica-east1"]
        )
        assert resolve_bigquery_price_per_tb("me-west1").value == BIGQUERY_ON_DEMAND_PRICES["me-west1"]


class TestRedshiftRegionalPricing:
    def test_us_east_1_pricing(self):
        price = resolve_redshift_node_price("dc2.large", "us-east-1").value
        assert price == REDSHIFT_NODE_PRICES["dc2.large"]["us-east-1"]

    def test_different_regions_same_node_type(self):
        price_us = resolve_redshift_node_price("ra3.4xlarge", "us-east-1").value
        price_eu = resolve_redshift_node_price("ra3.4xlarge", "eu-west-1").value

        assert price_us > 0
        assert price_eu > 0

    def test_node_type_variations(self):
        price_small = resolve_redshift_node_price("dc2.large", "us-east-1").value
        price_medium = resolve_redshift_node_price("ra3.xlplus", "us-east-1").value
        price_large = resolve_redshift_node_price("ra3.16xlarge", "us-east-1").value

        assert price_small < price_medium < price_large


class TestRegionalPricingAccuracy:
    def test_major_regions_have_specific_mappings(self):

        major_regions = [
            ("us-east-1", "us"),
            ("eu-west-1", "eu"),
            ("ap-southeast-1", "ap"),
            ("ca-central-1", "ca"),
            ("eastus", "us"),
            ("westeurope", "eu"),
            ("southeastasia", "ap"),
            ("canadacentral", "ca"),
            ("us-central1", "us"),
            ("europe-west1", "eu"),
            ("asia-southeast1", "ap"),
            ("northamerica-northeast1", "ca"),
        ]

        for region, expected_tier in major_regions:
            actual_tier = _map_region_to_tier(region)
            assert actual_tier == expected_tier, f"Region {region} mapped to {actual_tier}, expected {expected_tier}"

    def test_regional_pricing_variance_within_tolerance(self):

        price_us = resolve_snowflake_credit_price("standard", "aws", "us-east-1").value
        price_eu = resolve_snowflake_credit_price("standard", "aws", "eu-west-1").value
        price_ap = resolve_snowflake_credit_price("standard", "aws", "ap-southeast-1").value

        eu_premium = (price_eu - price_us) / price_us
        ap_premium = (price_ap - price_us) / price_us

        assert 0.20 <= eu_premium <= 0.30, f"EU premium {eu_premium:.1%} outside expected range"
        assert 0.25 <= ap_premium <= 0.35, f"AP premium {ap_premium:.1%} outside expected range"

    def test_no_region_defaults_to_safe_fallback(self):

        tier = _map_region_to_tier("unknown-future-region")
        assert tier == "other"

        resolution = resolve_snowflake_credit_price("standard", "aws", "unknown-future-region")
        assert resolution.value == SNOWFLAKE_CREDIT_PRICES["standard"]["aws"]["other"]
        assert resolution.fallback_used is False


