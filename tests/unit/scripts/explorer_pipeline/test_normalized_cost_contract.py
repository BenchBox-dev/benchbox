from decimal import Decimal

import pytest

from benchbox.core.cost.models import DeploymentMetadata, NormalizedCost

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_normalized_cost_contract_covers_all_statuses() -> None:
    deployment = DeploymentMetadata(
        cloud_provider="aws",
        cloud_region="us-east-1",
        instance_type="r7i.4xlarge",
        node_count=2,
        storage_format="parquet",
        storage_tier="s3-standard",
    )
    cases = [
        NormalizedCost(
            normalized_cost_usd=Decimal("12.3456"),
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="normalized",
            billing_unit="instance_hour",
            pricing_region="us-east-1",
            deployment=deployment,
        ),
        NormalizedCost(
            normalized_cost_usd=Decimal("0"),
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="not_applicable_local",
            billing_unit="not_applicable",
            pricing_region="not_applicable",
        ),
        NormalizedCost(
            normalized_cost_usd=None,
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="unavailable",
            billing_unit="unknown",
            pricing_region="unknown",
        ),
    ]

    assert [case.cost_status for case in cases] == ["normalized", "not_applicable_local", "unavailable"]
    assert cases[0].to_dict()["normalized_cost_usd"] == "12.3456"
    assert cases[1].to_dict()["normalized_cost_usd"] == "0"
    assert cases[2].to_dict()["normalized_cost_usd"] is None
    assert cases[0].to_dict()["deployment"]["cloud_provider"] == "aws"
    assert "unavailability_reasons" not in cases[0].to_dict()


def test_normalized_cost_serializes_unavailability_reasons_only_when_present() -> None:
    reason = "normalized cost unavailable: pricing provenance is unknown for snowflake_credit_prices"
    cost = NormalizedCost(
        normalized_cost_usd=None,
        cost_model_version="2026.05.0",
        cost_model_source="benchbox.core.cost.pricing",
        cost_scope="compute_only",
        cost_status="unavailable",
        billing_unit="credit",
        pricing_region="us-east-1",
        unavailability_reasons=(reason,),
    )

    assert cost.to_dict()["unavailability_reasons"] == [reason]


def test_normalized_cost_rejects_unavailability_reasons_for_available_cost() -> None:
    with pytest.raises(ValueError, match="require unavailable cost status"):
        NormalizedCost(
            normalized_cost_usd=Decimal("1"),
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="normalized",
            billing_unit="credit",
            pricing_region="us-east-1",
            unavailability_reasons=("pricing provenance is unknown",),
        )


def test_normalized_cost_rejects_ambiguous_missing_or_fake_values() -> None:
    with pytest.raises(ValueError, match="requires normalized_cost_usd"):
        NormalizedCost(
            normalized_cost_usd=None,
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="normalized",
            billing_unit="instance_hour",
            pricing_region="us-east-1",
        )

    with pytest.raises(ValueError, match="must not carry normalized_cost_usd"):
        NormalizedCost(
            normalized_cost_usd=Decimal("0"),
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="unavailable",
            billing_unit="unknown",
            pricing_region="unknown",
        )

    with pytest.raises(ValueError, match="cannot be negative"):
        NormalizedCost(
            normalized_cost_usd=Decimal("-0.01"),
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="normalized",
            billing_unit="instance_hour",
            pricing_region="us-east-1",
        )

    with pytest.raises(ValueError, match="explicit zero"):
        NormalizedCost(
            normalized_cost_usd=None,
            cost_model_version="2026.05.0",
            cost_model_source="benchbox.core.cost.pricing",
            cost_scope="compute_only",
            cost_status="not_applicable_local",
            billing_unit="not_applicable",
            pricing_region="not_applicable",
        )


def test_deployment_metadata_serializes_stable_contract_keys() -> None:
    payload = DeploymentMetadata(warehouse_size="large", cluster_size="2x").to_dict()

    assert list(payload) == [
        "cloud_provider",
        "cloud_region",
        "instance_type",
        "warehouse_size",
        "node_count",
        "cluster_size",
        "storage_format",
        "storage_tier",
    ]
    assert payload["warehouse_size"] == "large"
    assert payload["cloud_provider"] is None


def test_cost_usd_alias_behavior() -> None:
    normalized_compute = NormalizedCost(
        normalized_cost_usd=Decimal("1.25"),
        cost_model_version="2026.05.0",
        cost_model_source="benchbox.core.cost.pricing",
        cost_scope="compute_only",
        cost_status="normalized",
        billing_unit="instance_hour",
        pricing_region="us-east-1",
    )
    normalized_storage = NormalizedCost(
        normalized_cost_usd=Decimal("1.50"),
        cost_model_version="2026.05.0",
        cost_model_source="benchbox.core.cost.pricing",
        cost_scope="compute_plus_storage",
        cost_status="normalized",
        billing_unit="instance_hour",
        pricing_region="us-east-1",
    )
    local_not_applicable = NormalizedCost(
        normalized_cost_usd=Decimal("0"),
        cost_model_version="2026.05.0",
        cost_model_source="benchbox.core.cost.pricing",
        cost_scope="compute_only",
        cost_status="not_applicable_local",
        billing_unit="not_applicable",
        pricing_region="not_applicable",
    )

    assert normalized_compute.cost_usd == Decimal("1.25")
    assert normalized_compute.to_dict()["cost_usd"] == "1.25"
    assert normalized_storage.cost_usd is None
    assert normalized_storage.to_dict()["cost_usd"] is None
    assert local_not_applicable.cost_usd is None
