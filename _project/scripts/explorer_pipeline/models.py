from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from benchbox.core.cost.models import NormalizedCost
from benchbox.core.cost.pricing import PRICING_VERSION
from benchbox.core.results.status import validation_status_is_non_clean

_COST_MODEL_SOURCE = "benchbox.core.cost.pricing"


def unavailable_normalized_cost_payload() -> dict[str, Any]:
    return _unavailable_normalized_cost().to_dict()


def _unavailable_normalized_cost() -> NormalizedCost:
    return NormalizedCost(
        normalized_cost_usd=None,
        cost_model_version=PRICING_VERSION,
        cost_model_source=_COST_MODEL_SOURCE,
        cost_scope="compute_only",
        cost_status="unavailable",
        billing_unit="unknown",
        pricing_region="unknown",
    )


CANONICAL_BENCHMARK_ALIASES: dict[str, str] = {
    "star_schema": "ssb",
}


def canonical_benchmark_slug(raw: str) -> str:
    normalized = raw.strip().lower()
    return CANONICAL_BENCHMARK_ALIASES.get(normalized, normalized)


_PHASE_ALIASES = {"standard": "power"}


def canonical_phase(raw: str | None) -> str:
    normalized = (raw or "").strip().lower()
    if not normalized:
        return "unknown"
    return _PHASE_ALIASES.get(normalized, normalized)


_PROVENANCE_SUFFIX_RE = re.compile(
    r"[-_]trust[-_](?:ci|community|local|unknown)",
    re.IGNORECASE,
)


def _platform_id(raw: str) -> str:
    stripped = _PROVENANCE_SUFFIX_RE.sub("", raw).strip()
    return stripped.lower().replace(" ", "-")


RANKING_ELIGIBLE_VISIBILITIES: frozenset[str] = frozenset(
    {
        "public-curated",
        "public-verified",
        "public-vendor-reported",
    }
)

RANKING_ELIGIBLE_TRUST_LABELS: frozenset[str] = frozenset(
    {
        "maintainer-run",
        "ci",
        "ci-verified",
        "vendor-supplied",
    }
)

UNOFFICIAL_COMPLIANCE_CLASSES: frozenset[str] = frozenset(
    {
        "unofficial_nonstandard",
        "unofficial_subscale",
    }
)

APPLIED_TUNING_STATUSES: frozenset[str] = frozenset({"applied_unverified", "applied_verified"})


class ManifestEntry(BaseModel):
    result_id: str
    benchmark: str
    scale_factor: float
    platform: str
    platform_id: str = ""
    driver_version: str | None
    run_date: str
    power_score: float | None
    throughput_at_size: float | None = None
    stream_count: int | None = None
    total_duration_s: float
    geomean_ms: float | None = None
    display_geomean_ms: float | None = None
    query_count: int
    logical_query_count: int = 0
    has_display_timing: bool = False
    valid_query_count: int = 0
    missing_query_count: int = 0
    zero_timing_count: int = 0
    display_exclusion_reason: str | None = None
    comparison_exclusion_reason: str | None = None
    ranking_exclusion_reason: str | None = None
    trust_label: str
    visibility: str
    funding: str = "unspecified"
    platform_version: str | None = None
    execution_mode: str | None = None
    tuning_mode: str | None = None
    tuning_hash: str | None = None
    requested_config_hash: str | None = None
    applied_ledger_hash: str | None = None
    tuning_validation_status: str | None = None
    applied_receipt: str | None = None
    override_rules: list[str] = Field(default_factory=list)
    override_evidence: str | None = None
    override_approver: str | None = None
    override_expires: str | None = None
    tuning_policy_generation: str | None = None
    template_evidence: str | None = None
    test_type: str | None = None
    validation_status: str | None = None
    failed_query_count: int = 0
    benchmark_support_status: str | None = None
    cost_usd: float | None = None
    normalized_cost: NormalizedCost = Field(default_factory=_unavailable_normalized_cost)
    deployment_class: str | None = None
    cloud_provider: str | None = None
    cloud_region: str | None = None
    instance_or_warehouse: str | None = None
    storage_format: str | None = None
    compliance_class: str | None = None
    basis_availability: BasisAvailability | None = None

    @field_serializer("normalized_cost")
    def _serialize_normalized_cost(self, cost: NormalizedCost) -> dict[str, Any]:
        return cost.to_dict()

    @model_validator(mode="after")
    def _default_logical_query_count(self) -> ManifestEntry:
        if self.logical_query_count == 0 and self.query_count > 0:
            self.logical_query_count = self.query_count
        return self


class BasisAvailability(BaseModel):
    has_warmup: bool = False
    measurement_pass_count: int = 0
    warmup_status: str = "no_warmup_recorded"
    available_bases: list[str] = Field(default_factory=list)
    unavailable_bases: dict[str, str] = Field(default_factory=dict)
    query_pass_counts: dict[str, int] = Field(default_factory=dict)
    varying_pass_queries: dict[str, int] = Field(default_factory=dict)


class QueryTiming(BaseModel):
    query_id: str
    duration_ms: float
    status: str
    run_type: str | None = None
    iter: int | None = None
    stream: int | None = None


class QueryDisplayTiming(BaseModel):
    query_id: str
    display_ms: float | None
    sample_count: int


@dataclass(frozen=True)
class TimingEligibility:
    has_display_timing: bool
    logical_query_count: int
    valid_query_count: int
    missing_query_count: int
    zero_timing_count: int
    display_exclusion_reason: str | None
    comparison_exclusion_reason: str | None


def display_timing_is_valid(display_ms: float | None) -> bool:
    return display_ms is not None and math.isfinite(float(display_ms)) and float(display_ms) > 0


def timing_exclusion_reason(display_ms: float | None) -> str | None:
    if display_timing_is_valid(display_ms):
        return None
    if display_ms is None:
        return "missing_timing"
    value = float(display_ms)
    if not math.isfinite(value):
        return "invalid_timing"
    if value == 0:
        return "zero_timing"
    return "non_positive_timing"


def timing_eligibility(
    display_timings: list[QueryDisplayTiming],
    logical_query_count: int | None = None,
    *,
    query_count: int | None = None,
) -> TimingEligibility:
    if logical_query_count is None:
        if query_count is None:
            raise TypeError("timing_eligibility requires logical_query_count")
        logical_query_count = query_count

    valid_query_count = 0
    missing_query_count = 0
    zero_timing_count = 0
    seen_query_ids: set[str] = set()

    for timing in display_timings:
        seen_query_ids.add(timing.query_id)
        reason = timing_exclusion_reason(timing.display_ms)
        if reason is None:
            valid_query_count += 1
        elif reason == "zero_timing":
            zero_timing_count += 1
        else:
            missing_query_count += 1

    logical_count = int(logical_query_count)
    missing_query_count += max(logical_count - len(seen_query_ids), 0)
    has_display_timing = valid_query_count > 0
    display_reason = _display_exclusion_reason(
        query_count=logical_count,
        valid_query_count=valid_query_count,
        missing_query_count=missing_query_count,
        zero_timing_count=zero_timing_count,
    )
    comparison_reason = _comparison_exclusion_reason(
        query_count=logical_count,
        valid_query_count=valid_query_count,
        display_exclusion_reason=display_reason,
    )
    return TimingEligibility(
        has_display_timing=has_display_timing,
        logical_query_count=logical_count,
        valid_query_count=valid_query_count,
        missing_query_count=missing_query_count,
        zero_timing_count=zero_timing_count,
        display_exclusion_reason=display_reason,
        comparison_exclusion_reason=comparison_reason,
    )


def _display_exclusion_reason(
    *,
    query_count: int,
    valid_query_count: int,
    missing_query_count: int,
    zero_timing_count: int,
) -> str | None:
    if valid_query_count > 0:
        return None
    if query_count <= 0:
        return "no_queries"
    if zero_timing_count > 0 and missing_query_count == 0:
        return "zero_timings_only"
    if missing_query_count > 0 and zero_timing_count == 0:
        return "missing_timings"
    return "no_valid_display_timing"


def _comparison_exclusion_reason(
    *,
    query_count: int,
    valid_query_count: int,
    display_exclusion_reason: str | None,
) -> str | None:
    if display_exclusion_reason is not None:
        return display_exclusion_reason
    if valid_query_count < 2:
        return "insufficient_valid_queries"
    if query_count > 0 and valid_query_count * 2 < query_count:
        return "insufficient_query_coverage"
    return None


class ExplorerEnvironment(BaseModel):
    model_config = ConfigDict(extra="allow")

    os: Any = None
    arch: Any = None
    cpu_count: Any = None
    memory_gb: Any = None
    python: Any = None
    cpu_model: str | None = None
    cpu_family: str | None = None
    cpu_identity_provenance: Any = None
    client_region: str | None = None
    client_cloud: str | None = None
    link_status: str | None = None
    statement_overhead_min_ms: float | None = None
    statement_overhead_median_ms: float | None = None


class DetailResult(BaseModel):
    result_id: str
    benchmark: str
    scale_factor: float
    platform: str
    platform_id: str = ""
    driver_version: str | None
    run_date: str
    total_duration_s: float
    geomean_ms: float | None = None
    display_geomean_ms: float | None = None
    power_score: float | None
    throughput_at_size: float | None = None
    stream_count: int | None = None
    has_display_timing: bool = False
    valid_query_count: int = 0
    logical_query_count: int = 0
    missing_query_count: int = 0
    zero_timing_count: int = 0
    display_exclusion_reason: str | None = None
    comparison_exclusion_reason: str | None = None
    ranking_exclusion_reason: str | None = None
    environment: ExplorerEnvironment
    queries: list[QueryTiming]
    display_timings: list[QueryDisplayTiming] = []
    has_plans: bool
    plans_published: bool = False
    has_tuning: bool
    bundle_download_url: str
    trust_label: str
    visibility: str
    platform_version: str | None = None
    execution_mode: str | None = None
    tuning_mode: str | None = None
    tuning_hash: str | None = None
    requested_config_hash: str | None = None
    applied_ledger_hash: str | None = None
    tuning_validation_status: str | None = None
    applied_receipt: str | None = None
    override_rules: list[str] = Field(default_factory=list)
    override_evidence: str | None = None
    override_approver: str | None = None
    override_expires: str | None = None
    tuning_policy_generation: str | None = None
    template_evidence: str | None = None
    test_type: str | None = None
    validation_status: str | None = None
    failed_query_count: int = 0
    cost_usd: float | None = None
    normalized_cost: NormalizedCost = Field(default_factory=_unavailable_normalized_cost)
    compliance_class: str | None = None
    phase_durations: dict[str, float] | None = None
    physical_mechanisms: list[str] | None = None
    physical_rendering_id: str | None = None
    basis_availability: BasisAvailability | None = None

    @field_serializer("normalized_cost")
    def _serialize_normalized_cost(self, cost: NormalizedCost) -> dict[str, Any]:
        return cost.to_dict()

    @field_serializer("environment")
    def _serialize_environment(self, env: ExplorerEnvironment) -> dict[str, Any]:
        return env.model_dump(mode="json", exclude_unset=True)


KNOWN_DEFECT_RANKING_EXCLUSION = "known_defective_data"


def is_ranking_eligible(entry: ManifestEntry) -> bool:
    return (
        entry.visibility in RANKING_ELIGIBLE_VISIBILITIES
        and entry.trust_label in RANKING_ELIGIBLE_TRUST_LABELS
        and entry.compliance_class not in UNOFFICIAL_COMPLIANCE_CLASSES
        and entry.failed_query_count == 0
        and not validation_status_is_non_clean(entry.validation_status)
        and custom_tuning_is_materially_applied(entry)
        and entry.comparison_exclusion_reason is None
    )


def custom_tuning_is_materially_applied(entry: ManifestEntry) -> bool:
    return entry.tuning_mode != "custom" or entry.tuning_validation_status in APPLIED_TUNING_STATUSES


def ranking_exclusion_reason(entry: ManifestEntry, primary_metric: str | None = None) -> str | None:
    if entry.visibility not in RANKING_ELIGIBLE_VISIBILITIES:
        return "visibility_not_rankable"
    if entry.trust_label not in RANKING_ELIGIBLE_TRUST_LABELS:
        return "trust_not_rankable"
    if entry.compliance_class in UNOFFICIAL_COMPLIANCE_CLASSES:
        return "unofficial_compliance"
    if entry.failed_query_count != 0:
        return "failed_queries"
    if validation_status_is_non_clean(entry.validation_status):
        return "validation_not_clean"
    if not custom_tuning_is_materially_applied(entry):
        return "tuning_not_applied"
    if entry.comparison_exclusion_reason is not None:
        return entry.comparison_exclusion_reason

    metric = primary_metric or get_ranking_config(entry.benchmark, canonical_phase(entry.test_type)).primary_metric
    value = primary_metric_value(entry, metric)
    if value is None:
        return "missing_primary_metric"
    if not math.isfinite(float(value)) or float(value) <= 0:
        return "non_positive_primary_metric"
    return None


def select_canonical_row(entries: list[ManifestEntry]) -> ManifestEntry | None:
    if not entries:
        return None
    return max(
        entries,
        key=lambda e: (is_ranking_eligible(e), e.run_date, e.result_id),
    )


class RankingConfig(BaseModel):
    primary_metric: str
    secondary_metric: str
    primary_order: str


RANKING_METRIC_BY_FAMILY: dict[str, RankingConfig] = {
    "tpch": RankingConfig(
        primary_metric="power_score",
        secondary_metric="display_geomean_ms",
        primary_order="desc",
    ),
    "tpcds": RankingConfig(
        primary_metric="power_score",
        secondary_metric="display_geomean_ms",
        primary_order="desc",
    ),
    "star_schema": RankingConfig(
        primary_metric="display_geomean_ms",
        secondary_metric="power_score",
        primary_order="asc",
    ),
    "ssb": RankingConfig(
        primary_metric="display_geomean_ms",
        secondary_metric="power_score",
        primary_order="asc",
    ),
    "clickbench": RankingConfig(
        primary_metric="display_geomean_ms",
        secondary_metric="power_score",
        primary_order="asc",
    ),
    "nyctaxi": RankingConfig(
        primary_metric="display_geomean_ms",
        secondary_metric="power_score",
        primary_order="asc",
    ),
    "tsbs-devops": RankingConfig(
        primary_metric="display_geomean_ms",
        secondary_metric="power_score",
        primary_order="asc",
    ),
    "h2odb": RankingConfig(
        primary_metric="display_geomean_ms",
        secondary_metric="power_score",
        primary_order="asc",
    ),
    "datavault": RankingConfig(
        primary_metric="display_geomean_ms",
        secondary_metric="power_score",
        primary_order="asc",
    ),
}

THROUGHPUT_PHASE = "throughput"

RANKING_METRIC_BY_FAMILY_PHASE: dict[tuple[str, str], RankingConfig] = {
    ("tpch", THROUGHPUT_PHASE): RankingConfig(
        primary_metric="throughput_at_size",
        secondary_metric="display_geomean_ms",
        primary_order="desc",
    ),
    ("tpcds", THROUGHPUT_PHASE): RankingConfig(
        primary_metric="throughput_at_size",
        secondary_metric="display_geomean_ms",
        primary_order="desc",
    ),
}

SCALAR_PRIMARY_METRICS: frozenset[str] = frozenset({"power_score", "throughput_at_size"})

_DEFAULT_RANKING = RankingConfig(
    primary_metric="display_geomean_ms",
    secondary_metric="power_score",
    primary_order="asc",
)


def get_ranking_config(benchmark: str, phase: str | None = None) -> RankingConfig:
    if phase is not None:
        phase_config = RANKING_METRIC_BY_FAMILY_PHASE.get((canonical_benchmark_slug(benchmark), phase))
        if phase_config is not None:
            return phase_config
    return RANKING_METRIC_BY_FAMILY.get(benchmark, _DEFAULT_RANKING)


def primary_metric_value(source: Any, metric: str) -> float | None:
    column = metric if metric in SCALAR_PRIMARY_METRICS else "display_geomean_ms"
    return getattr(source, column)


class PercentileStats(BaseModel):
    p50: float
    p90: float
    p95: float
    p99: float


class PlatformRow(BaseModel):
    result_id: str
    short_id: str = ""
    platform_id: str
    platform: str
    platform_version: str | None
    tuning_mode: str | None
    tuning_hash: str | None
    execution_mode: str | None
    trust_label: str
    run_date: str
    is_ranking_eligible: bool
    has_display_timing: bool = False
    logical_query_count: int = 0
    valid_query_count: int = 0
    missing_query_count: int = 0
    zero_timing_count: int = 0
    display_exclusion_reason: str | None = None
    comparison_exclusion_reason: str | None = None
    ranking_exclusion_reason: str | None = None
    power_score: float | None
    throughput_at_size: float | None = None
    display_geomean_ms: float | None
    sample_geomean_ms: float | None
    cost_usd: float | None
    compliance_class: str | None = None
    percentile_stats: PercentileStats | None = None
    phase_durations: dict[str, float] | None = None
    timings: dict[str, float | None]


class BenchmarkSummary(BaseModel):
    benchmark: str
    scale_factor: float
    phase: str
    stream_count: int | None = None
    query_ids: list[str]
    platforms: list[PlatformRow]
    cell_reduction: str = "median_successful_measurement_ms"
    ranking: RankingConfig | None = None


class MetaRank(BaseModel):
    rank: int
    total: int
    metric_value: float | None = None
    speedup_vs_best: float | None = None
    primary_metric: str | None = None
    primary_order: str | None = None


class _BundleBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _coerce_mapping(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            return data
        return {}


class BundleRunBlock(_BundleBlock):
    timestamp: Any = None
    total_duration_ms: float = 0.0

    @field_validator("total_duration_ms", mode="before")
    @classmethod
    def _coerce_duration(cls, value: Any) -> Any:
        if value is None:
            return 0.0
        return value


class BundleBenchmarkBlock(_BundleBlock):
    id: str = "unknown"
    scale_factor: float = 0.0
    test_type: str | None = None
    compliance_class: str | None = None

    @field_validator("test_type", mode="before")
    @classmethod
    def _coerce_test_type(cls, value: Any) -> Any:
        return str(value) if value else None

    @field_validator("compliance_class", mode="before")
    @classmethod
    def _coerce_compliance_class(cls, value: Any) -> Any:
        return None if value is None else str(value)


class BundleLogicalProfile(_BundleBlock):
    physical_mechanisms: Any = None
    physical_rendering_id: Any = None


class BundleAppliedBlock(_BundleBlock):
    receipt: Any = None


class BundleTuningBlock(_BundleBlock):
    requested_config_hash: str | None = None
    applied_ledger_hash: str | None = None
    validation_status: str | None = None
    tuning_policy_generation: str | None = None
    template_evidence: str | None = None
    requested: Any = None
    applied: BundleAppliedBlock = Field(default_factory=BundleAppliedBlock)
    logical_profile: BundleLogicalProfile | None = None

    @field_validator("logical_profile", mode="before")
    @classmethod
    def _coerce_logical_profile(cls, value: Any) -> Any:
        if value is None or isinstance(value, Mapping):
            return value
        return None

    @field_validator(
        "requested_config_hash",
        "applied_ledger_hash",
        "validation_status",
        "tuning_policy_generation",
        "template_evidence",
        mode="before",
    )
    @classmethod
    def _coerce_verbatim_hash(cls, value: Any) -> Any:
        return str(value) if value else None


class BundleCloudBlock(_BundleBlock):
    provider: Any = None
    region: Any = None
    location: Any = None


class BundleComputeBlock(_BundleBlock):
    node_type: Any = None
    warehouse_size: Any = None
    warehouse: Any = None
    cluster_id: Any = None
    cluster_name: Any = None
    rpu: Any = None
    serverless_slots: Any = None
    worker_shape: Any = None
    driver_shape: Any = None


class BundleStorageBlock(_BundleBlock):
    table_format: Any = None


class BundlePlatformConfig(_BundleBlock):
    execution_mode: Any = None


class BundleDeploymentBlock(_BundleBlock):
    deployment_type: Any = None
    endpoint_class: Any = None
    cloud_provider: Any = None
    cloud_region: Any = None
    instance_type: Any = None
    warehouse_size: Any = None
    node_count: Any = None
    cluster_size: Any = None
    storage_format: Any = None
    storage_tier: Any = None


class BundlePlatformRuntime(_BundleBlock):
    runtime_type: Any = None


class BundleContainerBlock(_BundleBlock):
    pass


class BundlePlatformBlock(_BundleBlock):
    name: str = "unknown"
    version: Any = None
    client_version: Any = None
    config: BundlePlatformConfig = Field(default_factory=BundlePlatformConfig)
    tuning: BundleTuningBlock = Field(default_factory=BundleTuningBlock)
    deployment: BundleDeploymentBlock = Field(default_factory=BundleDeploymentBlock)
    cloud: BundleCloudBlock = Field(default_factory=BundleCloudBlock)
    compute: BundleComputeBlock = Field(default_factory=BundleComputeBlock)
    storage: BundleStorageBlock = Field(default_factory=BundleStorageBlock)

    @field_validator("name", mode="before")
    @classmethod
    def _coerce_name(cls, value: Any) -> Any:
        if value is None:
            return "unknown"
        return value


class BundleConfigBlock(_BundleBlock):
    tuning_mode: Any = None
    tuning_config: Any = None
    tuning: Any = None
    execution_mode: Any = None
    mode: Any = None
    options: Any = None


class BundleExecutionBlock(_BundleBlock):
    tuning_mode: Any = None
    execution_mode: Any = None
    mode: Any = None
    driver_version_actual: Any = None
    driver_version_resolved: Any = None
    driver_version_requested: Any = None
    driver_actual_version: Any = None
    driver_resolved_version: Any = None
    driver_requested_version: Any = None


class BundleSummaryQueries(_BundleBlock):
    total: Any = None


class BundleTpcMetrics(_BundleBlock):
    power_at_size: Any = None
    throughput_at_size: Any = None
    qphh_at_size: Any = None
    qphds_at_size: Any = None


class BundleSummaryBlock(_BundleBlock):
    queries: BundleSummaryQueries = Field(default_factory=BundleSummaryQueries)
    validation: Any = None
    tpc_metrics: BundleTpcMetrics = Field(default_factory=BundleTpcMetrics)


class BundlePhaseBlock(_BundleBlock):
    duration_ms: Any = None
    status: Any = None
    stream_results: Any = None
    errors: Any = None
    outstanding_work: Any = None


class BundleQueryRow(_BundleBlock):
    query_id: str = ""
    run_type: str | None = None
    status: str = "pass"
    ms: Any = None
    execution_time_ms: Any = None
    iter: Any = None
    stream: Any = None
    dataframe_skip_summary: Any = None

    @model_validator(mode="before")
    @classmethod
    def _resolve_identity(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return {}
        resolved = dict(data)
        resolved["query_id"] = str(data.get("id") or data.get("query_id", ""))
        if "status" in data and data["status"] is None:
            resolved["status"] = "fail"
        return resolved

    @field_validator("run_type", mode="before")
    @classmethod
    def _coerce_run_type(cls, value: Any) -> Any:
        if value is None:
            return None
        return value if isinstance(value, str) else str(value)


class BundleStatementOverhead(_BundleBlock):
    min: Any = None
    median: Any = None


class BundleClientLink(_BundleBlock):
    collection_status: Any = None
    client_region: Any = None
    client_cloud: Any = None
    statement_overhead_ms: BundleStatementOverhead = Field(default_factory=BundleStatementOverhead)


class BundleEnvironmentBlock(_BundleBlock):
    os: Any = None
    arch: Any = None
    cpu_count: Any = None
    memory_gb: Any = None
    python: Any = None
    cpu_model: Any = None
    cpu_identity_provenance: Any = None
    platform_runtime: BundlePlatformRuntime = Field(default_factory=BundlePlatformRuntime)
    container: BundleContainerBlock = Field(default_factory=BundleContainerBlock)
    client_link: BundleClientLink = Field(default_factory=BundleClientLink)


class BundleProvenanceBlock(_BundleBlock):
    funding: Any = None


class BundleDocument(_BundleBlock):
    run: BundleRunBlock = Field(default_factory=BundleRunBlock)
    benchmark: BundleBenchmarkBlock = Field(default_factory=BundleBenchmarkBlock)
    platform: BundlePlatformBlock = Field(default_factory=BundlePlatformBlock)
    config: BundleConfigBlock = Field(default_factory=BundleConfigBlock)
    execution: BundleExecutionBlock = Field(default_factory=BundleExecutionBlock)
    summary: BundleSummaryBlock = Field(default_factory=BundleSummaryBlock)
    phases: dict[str, BundlePhaseBlock] = Field(default_factory=dict)
    queries: list[BundleQueryRow] = Field(default_factory=list)
    environment: BundleEnvironmentBlock = Field(default_factory=BundleEnvironmentBlock)
    provenance: BundleProvenanceBlock = Field(default_factory=BundleProvenanceBlock)
    cost: dict[str, Any] | None = Field(default=None)
    normalized_cost: dict[str, Any] | None = Field(default=None)

    @field_validator("phases", mode="before")
    @classmethod
    def _coerce_phases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return {}
        return {str(name): block for name, block in value.items()}

    @field_validator("queries", mode="before")
    @classmethod
    def _coerce_queries(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return []
        return [row for row in value if isinstance(row, Mapping)]

    @field_validator("cost", "normalized_cost", mode="before")
    @classmethod
    def _coerce_cost_block(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, Mapping):
            return dict(value)
        return None


__all__ = [
    "BenchmarkSummary",
    "BundleAppliedBlock",
    "BundleBenchmarkBlock",
    "BundleConfigBlock",
    "BundleDeploymentBlock",
    "BundleDocument",
    "BundleEnvironmentBlock",
    "BundleExecutionBlock",
    "BundleLogicalProfile",
    "BundlePlatformBlock",
    "BundleProvenanceBlock",
    "BundleQueryRow",
    "BundleRunBlock",
    "BundleSummaryBlock",
    "BundleTuningBlock",
    "CANONICAL_BENCHMARK_ALIASES",
    "DetailResult",
    "ExplorerEnvironment",
    "ManifestEntry",
    "PercentileStats",
    "PlatformRow",
    "QueryDisplayTiming",
    "QueryTiming",
    "RankingConfig",
    "RANKING_ELIGIBLE_TRUST_LABELS",
    "UNOFFICIAL_COMPLIANCE_CLASSES",
    "RANKING_ELIGIBLE_VISIBILITIES",
    "RANKING_METRIC_BY_FAMILY",
    "RANKING_METRIC_BY_FAMILY_PHASE",
    "THROUGHPUT_PHASE",
    "primary_metric_value",
    "TimingEligibility",
    "display_timing_is_valid",
    "get_ranking_config",
    "canonical_benchmark_slug",
    "canonical_phase",
    "is_ranking_eligible",
    "MetaRank",
    "ranking_exclusion_reason",
    "select_canonical_row",
    "timing_eligibility",
    "timing_exclusion_reason",
    "unavailable_normalized_cost_payload",
]
