from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.cost.integration import _extract_platform_config_from_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _results_with(platform_info: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(platform_info=platform_info)


class TestGenericCloudRegionFallback:
    def test_athena_extracts_cloud_aws_and_region_from_platform_info(self) -> None:
        config = _extract_platform_config_from_results(
            _results_with(
                {
                    "platform_type": "athena",
                    "cloud_provider": "aws",
                    "region": "us-west-2",
                }
            )
        )
        assert config["platform_type"] == "athena"
        assert config["cloud"] == "aws"
        assert config["region"] == "us-west-2"
        assert "_defaulted_fields" not in config

    def test_athena_resolves_aws_from_platform_identity_and_omits_region(self) -> None:
        config = _extract_platform_config_from_results(_results_with({"platform_type": "athena"}))

        assert config["cloud"] == "aws"

        assert "region" not in config
        assert config["_defaulted_fields"] == ["region"]

    def test_azure_synapse_extracts_azure_cloud(self) -> None:
        config = _extract_platform_config_from_results(
            _results_with(
                {
                    "platform_type": "azure_synapse",
                    "region": "westeurope",
                }
            )
        )
        assert config["cloud"] == "azure"
        assert config["region"] == "westeurope"

        assert "_defaulted_fields" not in config

    def test_azure_synapse_omits_region_when_missing(self) -> None:
        config = _extract_platform_config_from_results(_results_with({"platform_type": "azure_synapse"}))
        assert config["cloud"] == "azure"
        assert "region" not in config
        assert config["_defaulted_fields"] == ["region"]

    def test_existing_snowflake_branch_still_runs(self) -> None:
        config = _extract_platform_config_from_results(
            _results_with(
                {
                    "platform_type": "snowflake",
                    "cloud_provider": "aws",
                    "region": "us-east-1",
                    "edition": "enterprise",
                    "configuration": {"warehouse_size": "MEDIUM"},
                }
            )
        )
        assert config["platform_type"] == "snowflake"
        assert config["edition"] == "enterprise"
        assert config["warehouse_size"] == "MEDIUM"
        assert config["cloud"] == "aws"
        assert config["region"] == "us-east-1"


