from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Optional

CostScope = Literal["compute_only", "compute_plus_storage"]
CostStatus = Literal["normalized", "not_applicable_local", "unavailable", "estimated"]


COST_UNAVAILABLE_WARNING_PREFIX = "normalized cost unavailable"


_PUBLISHABLE_COST_STATUSES = frozenset({"normalized", "not_applicable_local"})


def normalized_cost_allows_direct_total(normalized_cost: Mapping[str, Any] | None) -> bool:

    if normalized_cost is None:
        return True
    if not isinstance(normalized_cost, Mapping):
        return False
    if normalized_cost.get("cost_status") not in _PUBLISHABLE_COST_STATUSES:
        return False
    return normalized_cost.get("normalized_cost_usd") is not None


def cost_status_of(cost_summary: Mapping[str, Any] | None) -> str | None:

    if not isinstance(cost_summary, Mapping):
        return None
    normalized = cost_summary.get("normalized_cost")
    if not isinstance(normalized, Mapping):
        return None
    status = normalized.get("cost_status")
    return status if isinstance(status, str) else None


def published_total_cost(cost_summary: Mapping[str, Any] | None) -> float | None:

    if not isinstance(cost_summary, Mapping):
        return None
    if not normalized_cost_allows_direct_total(cost_summary.get("normalized_cost")):
        return None
    return cost_summary.get("total_cost")


def unavailable_cost_warning(warnings: list[str] | tuple[str, ...] | None) -> str | None:

    for warning in warnings or []:
        if isinstance(warning, str) and warning.startswith(COST_UNAVAILABLE_WARNING_PREFIX):
            return warning
    return None


@dataclass(frozen=True)
class DeploymentMetadata:
    cloud_provider: str | None = None
    cloud_region: str | None = None
    instance_type: str | None = None
    warehouse_size: str | None = None
    node_count: int | None = None
    cluster_size: str | None = None
    storage_format: str | None = None
    storage_tier: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:

        return {
            "cloud_provider": self.cloud_provider,
            "cloud_region": self.cloud_region,
            "instance_type": self.instance_type,
            "warehouse_size": self.warehouse_size,
            "node_count": self.node_count,
            "cluster_size": self.cluster_size,
            "storage_format": self.storage_format,
            "storage_tier": self.storage_tier,
        }


@dataclass(frozen=True)
class NormalizedCost:
    normalized_cost_usd: Decimal | None
    cost_model_version: str
    cost_model_source: str
    cost_scope: CostScope
    cost_status: CostStatus
    billing_unit: str
    pricing_region: str
    deployment: DeploymentMetadata = field(default_factory=DeploymentMetadata)
    unavailability_reasons: tuple[str, ...] = ()
    estimated_cost_usd: Decimal | None = None
    cost_estimate_method: str | None = None
    cost_estimate_source: str | None = None
    cost_estimate_pricing_date: str | None = None
    cost_estimate_assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.normalized_cost_usd is not None and not isinstance(self.normalized_cost_usd, Decimal):
            object.__setattr__(self, "normalized_cost_usd", Decimal(str(self.normalized_cost_usd)))
        if self.estimated_cost_usd is not None and not isinstance(self.estimated_cost_usd, Decimal):
            object.__setattr__(self, "estimated_cost_usd", Decimal(str(self.estimated_cost_usd)))
        if self.normalized_cost_usd is not None and self.normalized_cost_usd < 0:
            raise ValueError("normalized cost cannot be negative")
        if self.estimated_cost_usd is not None and self.estimated_cost_usd < 0:
            raise ValueError("estimated cost cannot be negative")
        if self.cost_status == "normalized" and self.normalized_cost_usd is None:
            raise ValueError("normalized cost requires normalized_cost_usd")
        if self.cost_status == "not_applicable_local" and self.normalized_cost_usd != Decimal("0"):
            raise ValueError("local not-applicable cost must carry explicit zero normalized_cost_usd")
        if self.cost_status == "unavailable" and self.normalized_cost_usd is not None:
            raise ValueError("unavailable cost must not carry normalized_cost_usd")
        if self.cost_status == "estimated":
            if self.normalized_cost_usd is not None:
                raise ValueError("estimated cost must not carry normalized_cost_usd")
            if self.estimated_cost_usd is None:
                raise ValueError("estimated cost requires estimated_cost_usd")
            estimate_metadata = (
                self.cost_estimate_method,
                self.cost_estimate_source,
                self.cost_estimate_pricing_date,
            )
            if any(not isinstance(value, str) or not value.strip() for value in estimate_metadata):
                raise ValueError("estimated cost requires method, source, and pricing date")
            if not self.cost_estimate_assumptions:
                raise ValueError("estimated cost requires explicit assumptions")
        elif (
            any(
                value is not None
                for value in (
                    self.estimated_cost_usd,
                    self.cost_estimate_method,
                    self.cost_estimate_source,
                    self.cost_estimate_pricing_date,
                )
            )
            or self.cost_estimate_assumptions
        ):
            raise ValueError("cost estimate fields require estimated cost status")
        if any(not isinstance(reason, str) or not reason.strip() for reason in self.unavailability_reasons):
            raise ValueError("normalized cost unavailability reasons must be non-empty strings")
        if self.unavailability_reasons and self.cost_status != "unavailable":
            raise ValueError("normalized cost unavailability reasons require unavailable cost status")
        if any(
            not isinstance(assumption, str) or not assumption.strip() for assumption in self.cost_estimate_assumptions
        ):
            raise ValueError("cost estimate assumptions must be non-empty strings")

    @property
    def cost_usd(self) -> Decimal | None:

        if self.cost_status == "normalized" and self.cost_scope == "compute_only":
            return self.normalized_cost_usd
        return None

    def to_dict(self) -> dict[str, Any]:

        cost_usd = self.cost_usd
        result: dict[str, Any] = {
            "normalized_cost_usd": str(self.normalized_cost_usd) if self.normalized_cost_usd is not None else None,
            "cost_usd": str(cost_usd) if cost_usd is not None else None,
            "cost_model_version": self.cost_model_version,
            "cost_model_source": self.cost_model_source,
            "cost_scope": self.cost_scope,
            "cost_status": self.cost_status,
            "billing_unit": self.billing_unit,
            "pricing_region": self.pricing_region,
            "deployment": self.deployment.to_dict(),
        }
        if self.unavailability_reasons:
            result["unavailability_reasons"] = list(self.unavailability_reasons)
        if self.cost_status == "estimated":
            result.update(
                {
                    "estimated_cost_usd": str(self.estimated_cost_usd),
                    "cost_estimate_method": self.cost_estimate_method,
                    "cost_estimate_source": self.cost_estimate_source,
                    "cost_estimate_pricing_date": self.cost_estimate_pricing_date,
                    "cost_estimate_assumptions": list(self.cost_estimate_assumptions),
                }
            )
        return result


@dataclass
class QueryCost:
    compute_cost: float
    currency: str = "USD"
    pricing_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:

        return {
            "compute_cost": self.compute_cost,
            "currency": self.currency,
            "pricing_details": self.pricing_details,
        }


@dataclass
class PhaseCost:
    phase_name: str
    total_cost: float
    query_count: int
    currency: str = "USD"
    query_costs: Optional[list[QueryCost]] = None
    wall_clock_duration_seconds: Optional[float] = None
    concurrent_streams: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:

        result: dict[str, Any] = {
            "phase_name": self.phase_name,
            "total_cost": self.total_cost,
            "query_count": self.query_count,
            "currency": self.currency,
        }
        if self.wall_clock_duration_seconds is not None:
            result["wall_clock_duration_seconds"] = self.wall_clock_duration_seconds

            if self.wall_clock_duration_seconds > 0:
                result["effective_cost_per_hour"] = self.total_cost / (self.wall_clock_duration_seconds / 3600.0)
        if self.concurrent_streams is not None:
            result["concurrent_streams"] = self.concurrent_streams
        if self.query_costs is not None:
            result["query_costs"] = [qc.to_dict() for qc in self.query_costs]
        return result


@dataclass
class BenchmarkCost:
    total_cost: float
    currency: str = "USD"
    phase_costs: list[PhaseCost] = field(default_factory=list)
    platform_details: dict[str, Any] = field(default_factory=dict)
    cost_model: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    storage_cost: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:

        result = {
            "total_cost": self.total_cost,
            "currency": self.currency,
            "phase_costs": [pc.to_dict() for pc in self.phase_costs],
            "platform_details": self.platform_details,
        }
        if self.cost_model:
            result["cost_model"] = self.cost_model
        if self.warnings:
            result["warnings"] = self.warnings
        if self.storage_cost is not None:
            result["storage_cost"] = self.storage_cost
        return result

    @classmethod
    def from_phase_costs(
        cls,
        phase_costs: list[PhaseCost],
        platform_details: Optional[dict[str, Any]] = None,
        currency: str = "USD",
    ) -> "BenchmarkCost":

        total = sum(pc.total_cost for pc in phase_costs)
        return cls(
            total_cost=total,
            currency=currency,
            phase_costs=phase_costs,
            platform_details=platform_details or {},
        )
