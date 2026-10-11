from __future__ import annotations

from dataclasses import dataclass

from benchbox.core.tuning.workload_profiles import (
    DISTRIBUTION_CANDIDATE,
    FACT_DIMENSION_JOIN,
    GROUP_ORDER_LOCALITY,
    HIGH_SELECTIVITY_FILTER,
    JOIN_LOCALITY,
    TEMPORAL_PARTITION,
    WorkloadTuningCandidate,
)

PARTITIONING = "partitioning"
CLUSTERING = "clustering"
DISTRIBUTION = "distribution"
SORTING = "sorting"
SORTED_INGESTION = "sorted_ingestion"
Z_ORDER = "z_order"
LIQUID_CLUSTERING = "liquid_clustering"
LIQUID_CLUSTERING_AUTO = "liquid_clustering_auto"
OPTIMIZE = "optimize"
OPTIMIZE_FULL = "optimize_full"

DATABRICKS_Z_ORDER_RENDERING = "databricks_z_order"
DATABRICKS_LIQUID_MANUAL_RENDERING = "databricks_liquid_manual"
DATABRICKS_LIQUID_AUTO_RENDERING = "databricks_liquid_auto"
DATABRICKS_RENDERINGS = frozenset(
    {
        DATABRICKS_Z_ORDER_RENDERING,
        DATABRICKS_LIQUID_MANUAL_RENDERING,
        DATABRICKS_LIQUID_AUTO_RENDERING,
    }
)

MAPPED = "mapped"
UNSUPPORTED = "unsupported"
WAIVED = "waived"
LOCALITY_ROLES = frozenset({JOIN_LOCALITY, FACT_DIMENSION_JOIN, HIGH_SELECTIVITY_FILTER, GROUP_ORDER_LOCALITY})


@dataclass(frozen=True)
class PlatformTuningMapping:
    platform: str
    tuning_types: tuple[str, ...]
    physical_mechanisms: tuple[str, ...]
    decision: str = MAPPED
    reason: str = ""
    max_columns: int | None = None
    capped_tuning_types: tuple[str, ...] | None = None
    physical_rendering_id: str | None = None

    @property
    def is_required_in_template(self) -> bool:
        return self.decision == MAPPED and bool(self.tuning_types)


def map_candidate_to_platform(
    platform: str,
    candidate: WorkloadTuningCandidate,
    *,
    physical_rendering_id: str | None = None,
) -> PlatformTuningMapping:
    from benchbox.core.tuning.capability_registry import WORKLOAD_PROFILE_MAPPED_PLATFORMS

    platform_key = platform.lower().replace("_", "-")
    roles = set(candidate.roles)

    if platform_key not in WORKLOAD_PROFILE_MAPPED_PLATFORMS:
        return PlatformTuningMapping(
            platform=platform_key,
            tuning_types=(),
            physical_mechanisms=(),
            decision=UNSUPPORTED,
            reason=f"{platform_key} has no TPC logical profile mapping yet",
        )

    if platform_key == "databricks":
        return _map_databricks(roles, physical_rendering_id or DATABRICKS_Z_ORDER_RENDERING)
    if platform_key == "duckdb":
        return _map_duckdb(roles)
    if platform_key == "bigquery":
        return _map_bigquery(roles)
    if platform_key == "redshift":
        return _map_redshift(roles)
    return _map_snowflake(roles)


def _map_databricks(roles: set[str], physical_rendering_id: str) -> PlatformTuningMapping:
    if physical_rendering_id == DATABRICKS_LIQUID_AUTO_RENDERING:
        return PlatformTuningMapping(
            platform="databricks",
            tuning_types=(),
            physical_mechanisms=(LIQUID_CLUSTERING_AUTO, OPTIMIZE),
            reason=(
                "Automatic Liquid Clustering delegates key selection to Databricks; logical candidates are "
                "reported as workload intent, not as BenchBox-selected physical keys."
            ),
            physical_rendering_id=physical_rendering_id,
        )

    if physical_rendering_id == DATABRICKS_LIQUID_MANUAL_RENDERING:
        if roles & ({TEMPORAL_PARTITION, DISTRIBUTION_CANDIDATE} | LOCALITY_ROLES):
            reason = (
                "Manual Liquid Clustering folds partitioning, locality, and ZORDER-style columns into clustering keys."
            )
            if DISTRIBUTION_CANDIDATE in roles:
                reason += " Databricks has no user-managed distribution key."
            return PlatformTuningMapping(
                platform="databricks",
                tuning_types=(CLUSTERING,),
                physical_mechanisms=(LIQUID_CLUSTERING, OPTIMIZE),
                reason=reason,
                max_columns=4,
                physical_rendering_id=physical_rendering_id,
            )
        return _unsupported("databricks", roles, physical_rendering_id=physical_rendering_id)

    if TEMPORAL_PARTITION in roles:
        return PlatformTuningMapping(
            platform="databricks",
            tuning_types=(),
            physical_mechanisms=(),
            decision=UNSUPPORTED,
            reason=(
                "Databricks recommends Liquid Clustering over Delta partitioning for new tables, "
                "so temporal locality has no PARTITIONED BY mapping in BenchBox tuned templates."
            ),
            physical_rendering_id=physical_rendering_id,
        )
    if DISTRIBUTION_CANDIDATE in roles:
        return PlatformTuningMapping(
            platform="databricks",
            tuning_types=(CLUSTERING,),
            physical_mechanisms=(Z_ORDER, OPTIMIZE),
            reason=(
                "Legacy Databricks Z-ORDER templates fold logical distribution candidates into ZORDER locality; "
                "Databricks has no user-managed distribution key."
            ),
            physical_rendering_id=physical_rendering_id,
        )
    if roles & LOCALITY_ROLES:
        return PlatformTuningMapping(
            platform="databricks",
            tuning_types=(CLUSTERING,),
            physical_mechanisms=(Z_ORDER, OPTIMIZE),
            reason="Logical locality maps to Databricks clustering, consumed by the Z-ORDER path in current templates.",
            physical_rendering_id=physical_rendering_id,
        )
    return _unsupported("databricks", roles, physical_rendering_id=physical_rendering_id)


def _map_duckdb(roles: set[str]) -> PlatformTuningMapping:
    if TEMPORAL_PARTITION in roles:
        return PlatformTuningMapping(
            platform="duckdb",
            tuning_types=(),
            physical_mechanisms=(),
            decision=UNSUPPORTED,
            reason="DuckDB never applies partitioning, so temporal locality has no DuckDB template mapping.",
        )
    if roles & {
        JOIN_LOCALITY,
        FACT_DIMENSION_JOIN,
        HIGH_SELECTIVITY_FILTER,
        GROUP_ORDER_LOCALITY,
        DISTRIBUTION_CANDIDATE,
    }:
        return PlatformTuningMapping(
            platform="duckdb",
            tuning_types=(SORTING,),
            physical_mechanisms=(SORTING, SORTED_INGESTION),
            reason="DuckDB uses sort/index and CTAS-sorted layout semantics, not Z-ORDER.",
        )
    return _unsupported("duckdb", roles)


def _map_bigquery(roles: set[str]) -> PlatformTuningMapping:
    if TEMPORAL_PARTITION in roles:
        return PlatformTuningMapping(
            platform="bigquery",
            tuning_types=(PARTITIONING,),
            physical_mechanisms=(PARTITIONING,),
            reason="Temporal locality maps to BigQuery partitioning when the column shape is supported.",
        )
    if roles & LOCALITY_ROLES:
        reason = "Logical locality maps to BigQuery clustering with its four-column table limit."
        if DISTRIBUTION_CANDIDATE in roles:
            reason += " BigQuery has no user-visible distribution key."
        return PlatformTuningMapping(
            platform="bigquery",
            tuning_types=(CLUSTERING,),
            physical_mechanisms=(CLUSTERING,),
            reason=reason,
            max_columns=4,
        )
    if DISTRIBUTION_CANDIDATE in roles:
        return PlatformTuningMapping(
            platform="bigquery",
            tuning_types=(),
            physical_mechanisms=(),
            decision=UNSUPPORTED,
            reason="BigQuery has no user-visible distribution key; use clustering roles where present.",
        )
    return _unsupported("bigquery", roles)


def _map_redshift(roles: set[str]) -> PlatformTuningMapping:
    if DISTRIBUTION_CANDIDATE in roles:
        if roles & ({TEMPORAL_PARTITION} | LOCALITY_ROLES):
            return PlatformTuningMapping(
                platform="redshift",
                tuning_types=(DISTRIBUTION, SORTING),
                physical_mechanisms=(DISTRIBUTION, SORTING),
                reason=(
                    "Distribution candidates map to Redshift DISTKEY decisions, while locality roles map to "
                    "SORTKEY decisions; DISTKEY remains single-key limited."
                ),
                max_columns=1,
                capped_tuning_types=(DISTRIBUTION,),
            )
        return PlatformTuningMapping(
            platform="redshift",
            tuning_types=(DISTRIBUTION,),
            physical_mechanisms=(DISTRIBUTION,),
            reason="Distribution candidates map to Redshift DISTKEY decisions with single-key limitations.",
            max_columns=1,
            capped_tuning_types=(DISTRIBUTION,),
        )
    if roles & ({TEMPORAL_PARTITION} | LOCALITY_ROLES):
        return PlatformTuningMapping(
            platform="redshift",
            tuning_types=(SORTING,),
            physical_mechanisms=(SORTING,),
            reason="Logical locality maps to Redshift SORTKEY decisions.",
        )
    return _unsupported("redshift", roles)


def _map_snowflake(roles: set[str]) -> PlatformTuningMapping:
    if roles & ({TEMPORAL_PARTITION} | LOCALITY_ROLES):
        reason = "Logical locality maps to Snowflake clustering keys where useful."
        if DISTRIBUTION_CANDIDATE in roles:
            reason += " Snowflake has no user-managed distribution key."
        return PlatformTuningMapping(
            platform="snowflake",
            tuning_types=(CLUSTERING,),
            physical_mechanisms=(CLUSTERING,),
            reason=reason
            + " Tables carry at most four clustering columns so the adapter resumes automatic reclustering.",
            max_columns=4,
        )
    if DISTRIBUTION_CANDIDATE in roles:
        return PlatformTuningMapping(
            platform="snowflake",
            tuning_types=(),
            physical_mechanisms=(),
            decision=UNSUPPORTED,
            reason="Snowflake has no user-managed distribution key.",
        )
    return _unsupported("snowflake", roles)


def _unsupported(platform: str, roles: set[str], *, physical_rendering_id: str | None = None) -> PlatformTuningMapping:
    sorted_roles = ", ".join(sorted(roles)) or "none"
    return PlatformTuningMapping(
        platform=platform,
        tuning_types=(),
        physical_mechanisms=(),
        decision=UNSUPPORTED,
        reason=f"No physical tuning mapping for roles: {sorted_roles}",
        physical_rendering_id=physical_rendering_id,
    )
