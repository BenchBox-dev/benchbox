# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.experimental.multiregion.config import (
    AWS_REGIONS,
    AZURE_REGIONS,
    GCP_REGIONS,
    CloudProvider,
    Region,
    RegionConfig,
    calculate_distance_km,
    get_region,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestRegion:
    def test_basic_creation(self, us_east_region):
        assert us_east_region.name == "US East"
        assert us_east_region.code == "us-east-1"
        assert us_east_region.provider == CloudProvider.AWS

    def test_with_coordinates(self, us_east_region):
        assert us_east_region.latitude == 38.9
        assert us_east_region.longitude == -77.0

    def test_without_coordinates(self):
        region = Region(
            name="Test",
            code="test-1",
            provider=CloudProvider.CUSTOM,
        )
        assert region.latitude is None
        assert region.longitude is None

    def test_hash_and_equality(self, us_east_region):
        same_region = Region(
            name="Different Name",
            code="us-east-1",
            provider=CloudProvider.AWS,
        )
        assert us_east_region == same_region
        assert hash(us_east_region) == hash(same_region)

    def test_inequality(self, us_east_region, eu_west_region):
        assert us_east_region != eu_west_region


class TestRegionConfig:
    def test_basic_creation(self, us_east_config, us_east_region):
        assert us_east_config.region == us_east_region
        assert us_east_config.endpoint == "us-east.example.com"
        assert us_east_config.port == 5432

    def test_connection_string(self, us_east_config):
        assert us_east_config.connection_string == "us-east.example.com:5432"

    def test_custom_port(self, us_east_region):
        config = RegionConfig(
            region=us_east_region,
            endpoint="test.example.com",
            port=3306,
        )
        assert config.connection_string == "test.example.com:3306"

    def test_connection_params(self, us_east_region):
        config = RegionConfig(
            region=us_east_region,
            endpoint="test.example.com",
            connection_params={"ssl": True},
        )
        assert config.connection_params["ssl"] is True


class TestMultiRegionConfig:
    def test_basic_creation(self, multi_region_config, us_east_config, eu_west_config):
        assert multi_region_config.primary_region == us_east_config
        assert len(multi_region_config.secondary_regions) == 1

    def test_all_regions(self, multi_region_config):
        all_regions = multi_region_config.all_regions
        assert len(all_regions) == 2

    def test_get_region_by_code(self, multi_region_config):
        region = multi_region_config.get_region_by_code("us-east-1")
        assert region is not None
        assert region.region.code == "us-east-1"

    def test_get_region_not_found(self, multi_region_config):
        region = multi_region_config.get_region_by_code("unknown-region")
        assert region is None


class TestCloudProvider:
    def test_all_providers(self):
        providers = list(CloudProvider)
        assert CloudProvider.AWS in providers
        assert CloudProvider.GCP in providers
        assert CloudProvider.AZURE in providers
        assert CloudProvider.SNOWFLAKE in providers

    def test_string_values(self):
        assert CloudProvider.AWS.value == "aws"
        assert CloudProvider.GCP.value == "gcp"


class TestPreDefinedRegions:
    def test_aws_regions(self):
        assert len(AWS_REGIONS) > 0
        assert "us-east-1" in AWS_REGIONS
        assert "eu-west-1" in AWS_REGIONS

    def test_gcp_regions(self):
        assert len(GCP_REGIONS) > 0
        assert "us-east1" in GCP_REGIONS
        assert "europe-west1" in GCP_REGIONS

    def test_azure_regions(self):
        assert len(AZURE_REGIONS) > 0
        assert "eastus" in AZURE_REGIONS
        assert "westeurope" in AZURE_REGIONS

    def test_region_has_coordinates(self):
        for code, region in AWS_REGIONS.items():
            assert region.latitude is not None
            assert region.longitude is not None


class TestGetRegion:
    def test_get_aws_region(self):
        region = get_region(CloudProvider.AWS, "us-east-1")
        assert region is not None
        assert region.code == "us-east-1"

    def test_get_gcp_region(self):
        region = get_region(CloudProvider.GCP, "us-east1")
        assert region is not None
        assert region.code == "us-east1"

    def test_get_unknown_region(self):
        region = get_region(CloudProvider.AWS, "unknown-region")
        assert region is None

    def test_get_custom_provider_region(self):
        region = get_region(CloudProvider.CUSTOM, "any")
        assert region is None


class TestCalculateDistance:
    def test_same_location(self, us_east_region):
        distance = calculate_distance_km(us_east_region, us_east_region)
        assert distance is not None
        assert distance == 0

    def test_cross_atlantic(self, us_east_region, eu_west_region):
        distance = calculate_distance_km(us_east_region, eu_west_region)
        assert distance is not None
        assert 5000 < distance < 6500

    def test_missing_coordinates(self, us_east_region):
        no_coords = Region(
            name="No Coords",
            code="no-coords",
            provider=CloudProvider.CUSTOM,
        )
        distance = calculate_distance_km(us_east_region, no_coords)
        assert distance is None

    def test_us_to_asia(self, us_east_region, ap_region):
        distance = calculate_distance_km(us_east_region, ap_region)
        assert distance is not None
        assert 10000 < distance < 12000


