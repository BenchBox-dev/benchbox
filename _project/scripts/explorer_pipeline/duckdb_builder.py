from __future__ import annotations

import json
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Any

from _project.scripts.explorer_pipeline.contract import EXPLORER_READ_MODEL_VERSION
from _project.scripts.explorer_pipeline.models import (
    BenchmarkSummary,
    DetailResult,
    ManifestEntry,
    PlatformRow,
    QueryDisplayTiming,
    TimingEligibility,
    display_timing_is_valid,
    is_ranking_eligible,
    timing_eligibility,
    timing_exclusion_reason,
)
from _project.scripts.explorer_pipeline.ranking import rank_platforms
from _project.scripts.explorer_pipeline.transformer import normalize_cpu_family

logger = logging.getLogger(__name__)

_SummaryKey = tuple[str, float, str, int | None]

_COHORT_PLATFORM_REQUIRED_KEYS = frozenset({"platform_id", "platform", "result_id", "short_id", "trust_label"})

_NORMALIZED_COST_COLUMNS = [
    ("normalized_cost_usd", "DOUBLE"),
    ("cost_model_version", "VARCHAR"),
    ("cost_model_source", "VARCHAR"),
    ("cost_scope", "VARCHAR"),
    ("cost_status", "VARCHAR"),
    ("billing_unit", "VARCHAR"),
    ("pricing_region", "VARCHAR"),
]

_ENVIRONMENT_FACET_COLUMNS = [
    ("deployment_class", "VARCHAR"),
    ("cloud_provider", "VARCHAR"),
    ("cloud_region", "VARCHAR"),
    ("instance_or_warehouse", "VARCHAR"),
    ("storage_format", "VARCHAR"),
]

_LEGACY_COST_DEPLOYMENT_COLUMNS = [
    ("instance_type", "VARCHAR"),
    ("warehouse_size", "VARCHAR"),
    ("node_count", "INTEGER"),
    ("cluster_size", "VARCHAR"),
    ("storage_tier", "VARCHAR"),
]

_COST_SCOPES = frozenset({"compute_only", "compute_plus_storage"})
_COST_STATUSES = frozenset({"normalized", "not_applicable_local", "unavailable"})


def _finite_float_or_none(entry: ManifestEntry, key: str, value: Any) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{entry.result_id}: normalized_cost.{key} must be finite; got {value!r}")
    return parsed


def _required_cost_string(entry: ManifestEntry, value: Any, key: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{entry.result_id}: normalized_cost.{key} must be a non-empty string")
    return value


def _required_cost_choice(entry: ManifestEntry, value: Any, key: str, choices: frozenset[str]) -> str:
    checked = _required_cost_string(entry, value, key)
    if checked not in choices:
        raise ValueError(f"{entry.result_id}: normalized_cost.{key} must be one of {sorted(choices)}; got {value!r}")
    return checked


def _normalized_cost_column_values(entry: ManifestEntry) -> tuple:
    cost = entry.normalized_cost
    return (
        _finite_float_or_none(entry, "normalized_cost_usd", cost.normalized_cost_usd),
        _required_cost_string(entry, cost.cost_model_version, "cost_model_version"),
        _required_cost_string(entry, cost.cost_model_source, "cost_model_source"),
        _required_cost_choice(entry, cost.cost_scope, "cost_scope", _COST_SCOPES),
        _required_cost_choice(entry, cost.cost_status, "cost_status", _COST_STATUSES),
        _required_cost_string(entry, cost.billing_unit, "billing_unit"),
        _required_cost_string(entry, cost.pricing_region, "pricing_region"),
    )


def _environment_facet_column_values(entry: ManifestEntry) -> tuple:
    return (
        entry.deployment_class,
        entry.cloud_provider,
        entry.cloud_region,
        entry.instance_or_warehouse,
        entry.storage_format,
    )


def _legacy_cost_deployment_column_values(entry: ManifestEntry) -> tuple:
    deployment = entry.normalized_cost.deployment
    return (
        deployment.instance_type,
        deployment.warehouse_size,
        deployment.node_count,
        deployment.cluster_size,
        deployment.storage_tier,
    )


def _platform_row_timing_eligibility(row: PlatformRow, query_count: int) -> TimingEligibility:
    display_timings = [
        QueryDisplayTiming(query_id=query_id, display_ms=display_ms, sample_count=0)
        for query_id, display_ms in row.timings.items()
    ]
    return timing_eligibility(display_timings, query_count)


def _entry_timing_eligibility(entry: ManifestEntry) -> TimingEligibility:
    return TimingEligibility(
        has_display_timing=entry.has_display_timing,
        logical_query_count=entry.logical_query_count,
        valid_query_count=entry.valid_query_count,
        missing_query_count=entry.missing_query_count,
        zero_timing_count=entry.zero_timing_count,
        display_exclusion_reason=entry.display_exclusion_reason,
        comparison_exclusion_reason=entry.comparison_exclusion_reason,
    )


class DuckDBSnapshotBuilder:
    _COLUMNS = [
        ("result_id", "VARCHAR"),
        ("benchmark", "VARCHAR"),
        ("scale_factor", "DOUBLE"),
        ("platform", "VARCHAR"),
        ("driver_version", "VARCHAR"),
        ("run_date", "VARCHAR"),
        ("power_score", "DOUBLE"),
        ("total_duration_s", "DOUBLE"),
        ("geomean_ms", "DOUBLE"),
        ("display_geomean_ms", "DOUBLE"),
        ("query_count", "INTEGER"),
        ("logical_query_count", "INTEGER"),
        ("has_display_timing", "BOOLEAN"),
        ("valid_query_count", "INTEGER"),
        ("missing_query_count", "INTEGER"),
        ("zero_timing_count", "INTEGER"),
        ("display_exclusion_reason", "VARCHAR"),
        ("comparison_exclusion_reason", "VARCHAR"),
        ("ranking_exclusion_reason", "VARCHAR"),
        ("trust_label", "VARCHAR"),
        ("visibility", "VARCHAR"),
        ("funding", "VARCHAR"),
        ("platform_version", "VARCHAR"),
        ("execution_mode", "VARCHAR"),
        ("tuning_mode", "VARCHAR"),
        ("tuning_hash", "VARCHAR"),
        ("test_type", "VARCHAR"),
        ("validation_status", "VARCHAR"),
        ("cost_usd", "DOUBLE"),
        *_NORMALIZED_COST_COLUMNS,
        *_ENVIRONMENT_FACET_COLUMNS,
        *_LEGACY_COST_DEPLOYMENT_COLUMNS,
        ("benchmark_support_status", "VARCHAR"),
        ("throughput_at_size", "DOUBLE"),
        ("stream_count", "INTEGER"),
    ]

    def build(self, entries: list[ManifestEntry], output_path: Path) -> None:
        try:
            import duckdb
        except ImportError as exc:
            raise ImportError(
                "duckdb is required for DuckDBSnapshotBuilder. Install the explorer dependency set with: uv sync --extra explorer"
            ) from exc

        col_names = [name for name, _ in self._COLUMNS]
        _sentinel = self._entry_to_tuple(
            ManifestEntry(
                result_id="",
                benchmark="",
                scale_factor=0.0,
                platform="",
                driver_version=None,
                run_date="",
                power_score=None,
                total_duration_s=0.0,
                query_count=0,
                trust_label="",
                visibility="",
            )
        )
        if len(_sentinel) != len(col_names):
            raise AssertionError(
                f"_entry_to_tuple returns {len(_sentinel)} values but _COLUMNS has {len(col_names)} columns"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path = self._candidate_path(output_path)

        col_defs = ", ".join(f"{name} {dtype}" for name, dtype in self._COLUMNS)

        rows = [self._entry_to_tuple(e) for e in entries]

        try:
            with duckdb.connect(str(candidate_path)) as con:
                self._create_metadata(con)
                con.execute(f"CREATE TABLE results ({col_defs})")
                if rows:
                    placeholders = ", ".join("?" * len(col_names))
                    con.executemany(f"INSERT INTO results VALUES ({placeholders})", rows)
            os.replace(candidate_path, output_path)
        finally:
            self._cleanup_candidate(candidate_path)

        logger.info("Wrote %d rows to %s", len(rows), output_path)

    def build_full(
        self,
        entries: list[ManifestEntry],
        details_map: dict[str, DetailResult],
        summaries: list[tuple[_SummaryKey, BenchmarkSummary]],
        short_id_map: dict[str, str],
        full_to_short: dict[str, str],
        meta: dict[str, Any],
        bundle_url_prefix: str,
        output_path: Path,
    ) -> None:
        try:
            import duckdb
        except ImportError as exc:
            raise ImportError(
                "duckdb is required for DuckDBSnapshotBuilder. Install the explorer dependency set with: uv sync --extra explorer"
            ) from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path = self._candidate_path(output_path)

        prefix = bundle_url_prefix.rstrip("/")

        try:
            with duckdb.connect(str(candidate_path)) as con:
                self._create_schema(con)
                self._create_metadata(con)
                self._populate_supporting_tables(con, entries, details_map)
                cohort_ranking_reasons = {
                    row.result_id: row.ranking_exclusion_reason
                    for _, summary in summaries
                    for row in summary.platforms
                    if row.ranking_exclusion_reason is not None
                }
                self._populate_results(con, entries, details_map, prefix, cohort_ranking_reasons)
                self._populate_result_basis_availability(con, entries, details_map)
                self._populate_query_display_timings(con, entries, details_map)
                self._populate_query_executions(con, entries, details_map)
                self._populate_benchmark_matrix_cells(con, summaries)
                entries_by_result = {entry.result_id: entry for entry in entries}
                self._populate_benchmark_rankings(con, summaries, full_to_short, entries_by_result)
                self._populate_cohort_metadata(con, meta, summaries, entries_by_result)
                self._populate_meta_leaderboard(con, meta)
                self._populate_short_ids(con, short_id_map)
            os.replace(candidate_path, output_path)
        finally:
            self._cleanup_candidate(candidate_path)

        logger.info("Built full DuckDB browser store (%d results) at %s", len(entries), output_path)

    @staticmethod
    def _candidate_path(output_path: Path) -> Path:
        return output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")

    @staticmethod
    def _cleanup_candidate(candidate_path: Path) -> None:
        for path in (candidate_path, candidate_path.with_name(candidate_path.name + ".wal")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _create_schema(self, con: Any) -> None:
        con.execute("""
            CREATE TABLE result_environment (
                result_id   VARCHAR PRIMARY KEY,
                os          VARCHAR,
                arch        VARCHAR,
                cpu_count   INTEGER,
                memory_gb   DOUBLE,
                python      VARCHAR,
                cpu_model   VARCHAR,
                cpu_family  VARCHAR,
                cpu_identity_provenance VARCHAR,
                client_region VARCHAR,
                client_cloud VARCHAR,
                statement_overhead_min_ms DOUBLE,
                statement_overhead_median_ms DOUBLE,
                link_status VARCHAR
            )
        """)
        con.execute("""
            CREATE TABLE result_phase_durations (
                result_id  VARCHAR NOT NULL,
                phase      VARCHAR NOT NULL,
                duration_s DOUBLE  NOT NULL,
                PRIMARY KEY (result_id, phase)
            )
        """)
        con.execute("""
            CREATE TABLE result_basis_availability (
                result_id               VARCHAR PRIMARY KEY,
                has_warmup              BOOLEAN NOT NULL,
                measurement_pass_count  INTEGER NOT NULL,
                warmup_status           VARCHAR NOT NULL,
                available_bases         VARCHAR NOT NULL,
                varying_pass_queries    VARCHAR
            )
        """)
        con.execute("""
            CREATE TABLE results (
                result_id            VARCHAR  PRIMARY KEY,
                benchmark            VARCHAR  NOT NULL,
                scale_factor         DOUBLE   NOT NULL,
                platform             VARCHAR  NOT NULL,
                platform_id          VARCHAR  NOT NULL,
                driver_version       VARCHAR,
                run_date             VARCHAR  NOT NULL,
                power_score          DOUBLE,
                total_duration_s     DOUBLE   NOT NULL,
                geomean_ms           DOUBLE,
                display_geomean_ms   DOUBLE,
                query_count          INTEGER  NOT NULL,
                logical_query_count  INTEGER  NOT NULL,
                has_display_timing   BOOLEAN  NOT NULL,
                valid_query_count    INTEGER  NOT NULL,
                missing_query_count  INTEGER  NOT NULL,
                zero_timing_count    INTEGER  NOT NULL,
                display_exclusion_reason    VARCHAR,
                comparison_exclusion_reason VARCHAR,
                ranking_exclusion_reason    VARCHAR,
                trust_label          VARCHAR  NOT NULL,
                visibility           VARCHAR  NOT NULL,
                funding              VARCHAR  NOT NULL,
                platform_version     VARCHAR,
                execution_mode       VARCHAR,
                tuning_mode          VARCHAR,
                tuning_hash          VARCHAR,
                requested_config_hash VARCHAR,
                applied_ledger_hash   VARCHAR,
                tuning_validation_status VARCHAR,
                applied_receipt      VARCHAR,
                override_rules       VARCHAR,
                override_evidence    VARCHAR,
                override_approver    VARCHAR,
                override_expires     VARCHAR,
                tuning_policy_generation VARCHAR,
                template_evidence      VARCHAR,
                test_type            VARCHAR,
                validation_status    VARCHAR,
                cost_usd             DOUBLE,
                normalized_cost_usd  DOUBLE,
                cost_model_version   VARCHAR,
                cost_model_source    VARCHAR,
                cost_scope           VARCHAR,
                cost_status          VARCHAR  NOT NULL,
                billing_unit         VARCHAR,
                pricing_region       VARCHAR,
                deployment_class     VARCHAR,
                cloud_provider       VARCHAR,
                cloud_region         VARCHAR,
                instance_or_warehouse VARCHAR,
                storage_format       VARCHAR,
                instance_type        VARCHAR,
                warehouse_size       VARCHAR,
                node_count           INTEGER,
                cluster_size         VARCHAR,
                storage_tier         VARCHAR,
                compliance_class     VARCHAR,
                is_ranking_eligible  BOOLEAN  NOT NULL,
                has_plans            BOOLEAN  NOT NULL,
                plans_published      BOOLEAN  NOT NULL,
                has_tuning           BOOLEAN  NOT NULL,
                bundle_download_url  VARCHAR  NOT NULL,
                physical_mechanisms   VARCHAR,
                physical_rendering_id VARCHAR,
                benchmark_support_status VARCHAR,
                throughput_at_size   DOUBLE,
                stream_count         INTEGER
            )
        """)
        con.execute("""
            CREATE VIEW result_detail_metrics AS
            SELECT
                r.result_id,
                r.benchmark,
                r.scale_factor,
                r.platform,
                r.platform_id,
                r.driver_version,
                r.run_date,
                r.power_score,
                r.total_duration_s,
                r.geomean_ms,
                r.display_geomean_ms,
                r.query_count,
                r.logical_query_count,
                r.has_display_timing,
                r.valid_query_count,
                r.missing_query_count,
                r.zero_timing_count,
                r.display_exclusion_reason,
                r.comparison_exclusion_reason,
                r.ranking_exclusion_reason,
                r.trust_label,
                r.visibility,
                r.funding,
                r.platform_version,
                r.execution_mode,
                r.tuning_mode,
                r.tuning_hash,
                r.requested_config_hash,
                r.applied_ledger_hash,
                r.tuning_validation_status,
                r.applied_receipt,
                r.override_rules,
                r.override_evidence,
                r.override_approver,
                r.override_expires,
                r.tuning_policy_generation,
                r.template_evidence,
                r.test_type,
                r.validation_status,
                r.cost_usd,
                r.normalized_cost_usd,
                r.cost_model_version,
                r.cost_model_source,
                r.cost_scope,
                r.cost_status,
                r.billing_unit,
                r.pricing_region,
                r.deployment_class,
                r.cloud_provider,
                r.cloud_region,
                r.instance_or_warehouse,
                r.instance_type,
                r.warehouse_size,
                r.node_count,
                r.cluster_size,
                r.storage_format,
                r.storage_tier,
                r.compliance_class,
                r.has_plans,
                r.plans_published,
                r.has_tuning,
                r.bundle_download_url,
                r.physical_mechanisms,
                r.physical_rendering_id,
                r.benchmark_support_status,
                r.throughput_at_size,
                r.stream_count,
                e.os,
                e.arch,
                e.cpu_count,
                e.memory_gb,
                e.python,
                e.cpu_model,
                e.cpu_family,
                e.cpu_identity_provenance,
                e.client_region,
                e.client_cloud,
                e.statement_overhead_min_ms,
                e.statement_overhead_median_ms,
                e.link_status
            FROM results r
            LEFT JOIN result_environment e USING (result_id)
        """)
        con.execute("""
            CREATE TABLE query_display_timings (
                result_id    VARCHAR  NOT NULL,
                query_id     VARCHAR  NOT NULL,
                display_ms   DOUBLE,
                sample_count INTEGER  NOT NULL,
                is_valid_display_timing BOOLEAN NOT NULL,
                timing_exclusion_reason VARCHAR,
                PRIMARY KEY (result_id, query_id)
            )
        """)
        con.execute("""
            CREATE TABLE query_executions (
                result_id    VARCHAR  NOT NULL,
                query_id     VARCHAR  NOT NULL,
                duration_ms  DOUBLE   NOT NULL,
                status       VARCHAR  NOT NULL,
                run_type     VARCHAR,
                iter         INTEGER,
                stream       INTEGER
            )
        """)
        con.execute("CREATE INDEX idx_query_executions_result ON query_executions (result_id)")
        con.execute("""
            CREATE TABLE benchmark_matrix_cells (
                benchmark    VARCHAR  NOT NULL,
                scale_factor DOUBLE   NOT NULL,
                phase        VARCHAR  NOT NULL,
                result_id    VARCHAR  NOT NULL,
                platform_id  VARCHAR  NOT NULL,
                query_id     VARCHAR  NOT NULL,
                display_ms   DOUBLE,
                is_valid_display_timing BOOLEAN NOT NULL,
                timing_exclusion_reason VARCHAR,
                stream_count INTEGER,
                PRIMARY KEY (benchmark, scale_factor, phase, result_id, query_id)
            )
        """)
        con.execute("CREATE INDEX idx_matrix_cells_cohort ON benchmark_matrix_cells (benchmark, scale_factor, phase)")
        con.execute("""
            CREATE TABLE benchmark_rankings (
                benchmark                    VARCHAR  NOT NULL,
                scale_factor                 DOUBLE   NOT NULL,
                phase                        VARCHAR  NOT NULL,
                result_id                    VARCHAR  NOT NULL,
                platform_id                  VARCHAR  NOT NULL,
                platform                     VARCHAR  NOT NULL,
                short_id                     VARCHAR  NOT NULL,
                trust_label                  VARCHAR  NOT NULL,
                funding                      VARCHAR  NOT NULL,
                tuning_mode                  VARCHAR,
                tuning_hash                  VARCHAR,
                execution_mode               VARCHAR,
                compliance_class             VARCHAR,
                run_date                     VARCHAR  NOT NULL,
                is_ranking_eligible          BOOLEAN  NOT NULL,
                has_display_timing           BOOLEAN  NOT NULL,
                logical_query_count           INTEGER  NOT NULL,
                valid_query_count            INTEGER  NOT NULL,
                missing_query_count          INTEGER  NOT NULL,
                zero_timing_count            INTEGER  NOT NULL,
                display_exclusion_reason     VARCHAR,
                comparison_exclusion_reason  VARCHAR,
                ranking_exclusion_reason     VARCHAR,
                power_score                  DOUBLE,
                display_geomean_ms           DOUBLE,
                sample_geomean_ms            DOUBLE,
                cost_usd                     DOUBLE,
                primary_metric               VARCHAR  NOT NULL,
                primary_order                VARCHAR  NOT NULL,
                rank                         INTEGER,
                total_in_cohort              INTEGER  NOT NULL,
                cohort_ranked_count          INTEGER  NOT NULL,
                cohort_ranking_exclusion_reason VARCHAR,
                percentile_p50               DOUBLE,
                percentile_p90               DOUBLE,
                percentile_p95               DOUBLE,
                percentile_p99               DOUBLE,
                speedup_vs_best              DOUBLE,
                speedup_vs_slowest_in_cohort DOUBLE,
                throughput_at_size           DOUBLE,
                stream_count                 INTEGER,
                PRIMARY KEY (benchmark, scale_factor, phase, result_id)
            )
        """)
        con.execute("CREATE INDEX idx_benchmark_rankings_cohort ON benchmark_rankings (benchmark, scale_factor, phase)")
        con.execute("""
            CREATE VIEW platform_index_rows AS
            SELECT
                result_id,
                benchmark,
                scale_factor,
                platform,
                platform_id,
                driver_version,
                run_date,
                power_score,
                total_duration_s,
                geomean_ms,
                display_geomean_ms,
                query_count,
                logical_query_count,
                has_display_timing,
                valid_query_count,
                missing_query_count,
                zero_timing_count,
                display_exclusion_reason,
                comparison_exclusion_reason,
                ranking_exclusion_reason,
                trust_label,
                funding,
                tuning_mode,
                execution_mode,
                compliance_class,
                cost_usd,
                normalized_cost_usd,
                cost_model_version,
                cost_model_source,
                cost_scope,
                cost_status,
                billing_unit,
                pricing_region,
                deployment_class,
                cloud_provider,
                cloud_region,
                instance_or_warehouse,
                instance_type,
                warehouse_size,
                node_count,
                cluster_size,
                storage_format,
                storage_tier
            FROM results
        """)
        con.execute("""
            CREATE TABLE cohort_metadata (
                cohort_key       VARCHAR  NOT NULL,
                benchmark        VARCHAR  NOT NULL,
                scale_factor     DOUBLE   NOT NULL,
                phase            VARCHAR  NOT NULL,
                cohort_label     VARCHAR  NOT NULL,
                cohort_href      VARCHAR  NOT NULL,
                platform_count   INTEGER  NOT NULL,
                cohort_ranked_count INTEGER NOT NULL,
                cohort_ranking_exclusion_reason VARCHAR,
                primary_metric   VARCHAR  NOT NULL,
                primary_order    VARCHAR  NOT NULL,
                platform_id      VARCHAR  NOT NULL,
                platform         VARCHAR  NOT NULL,
                result_id        VARCHAR  NOT NULL,
                short_id         VARCHAR  NOT NULL,
                tuning_mode      VARCHAR,
                trust_label      VARCHAR  NOT NULL,
                has_display_timing BOOLEAN NOT NULL,
                logical_query_count INTEGER NOT NULL,
                valid_query_count INTEGER NOT NULL,
                missing_query_count INTEGER NOT NULL,
                zero_timing_count INTEGER NOT NULL,
                display_exclusion_reason VARCHAR,
                comparison_exclusion_reason VARCHAR,
                ranking_exclusion_reason VARCHAR,
                rank             INTEGER,
                metric_value     DOUBLE,
                speedup_vs_best  DOUBLE,
                stream_count     INTEGER,
                PRIMARY KEY (cohort_key, result_id)
            )
        """)
        con.execute("CREATE INDEX idx_cohort_metadata_key ON cohort_metadata (cohort_key)")
        con.execute("CREATE INDEX idx_cohort_metadata_platform ON cohort_metadata (cohort_key, platform_id)")
        con.execute("""
            CREATE TABLE meta_leaderboard (
                platform_id  VARCHAR  PRIMARY KEY,
                platform     VARCHAR  NOT NULL,
                avg_rank     DOUBLE,
                n_cohorts    INTEGER  NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE short_ids (
                short_id   VARCHAR  PRIMARY KEY,
                result_id  VARCHAR  NOT NULL UNIQUE
            )
        """)

    def _create_metadata(self, con: Any) -> None:
        con.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                read_model_version INTEGER NOT NULL
            )
        """)
        con.execute("DELETE FROM metadata")
        con.execute("INSERT INTO metadata VALUES (?)", [EXPLORER_READ_MODEL_VERSION])

    def _populate_environment(
        self,
        con: Any,
        entries: list[ManifestEntry],
        details_map: dict[str, DetailResult],
    ) -> None:
        env_rows: list[tuple] = []
        for entry in entries:
            detail = details_map.get(entry.result_id)
            if detail is None:
                continue
            env = detail.environment
            raw_cpu_model = env.cpu_model
            if isinstance(raw_cpu_model, str):
                raw_cpu_model = raw_cpu_model.strip() or None
            cpu_family = env.cpu_family or normalize_cpu_family(raw_cpu_model)
            if not raw_cpu_model:
                raw_cpu_model = None
                cpu_family = None
            env_rows.append(
                (
                    entry.result_id,
                    env.os,
                    env.arch,
                    env.cpu_count,
                    env.memory_gb,
                    env.python,
                    raw_cpu_model,
                    cpu_family,
                    env.cpu_identity_provenance,
                    env.client_region,
                    env.client_cloud,
                    env.statement_overhead_min_ms,
                    env.statement_overhead_median_ms,
                    env.link_status,
                )
            )

        if env_rows:
            con.executemany(
                "INSERT INTO result_environment (result_id, os, arch, cpu_count, memory_gb,"
                " python, cpu_model, cpu_family, cpu_identity_provenance,"
                " client_region, client_cloud, statement_overhead_min_ms,"
                " statement_overhead_median_ms, link_status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                env_rows,
            )

    def _populate_supporting_tables(
        self,
        con: Any,
        entries: list[ManifestEntry],
        details_map: dict[str, DetailResult],
    ) -> None:
        self._populate_environment(con, entries, details_map)
        phase_rows: list[tuple] = []
        for entry in entries:
            detail = details_map.get(entry.result_id)
            if detail is None:
                continue
            if detail.phase_durations:
                for phase, duration_s in detail.phase_durations.items():
                    phase_rows.append((entry.result_id, phase, duration_s))

        if phase_rows:
            con.executemany("INSERT INTO result_phase_durations VALUES (?, ?, ?)", phase_rows)

    def _populate_results(
        self,
        con: Any,
        entries: list[ManifestEntry],
        details_map: dict[str, DetailResult],
        bundle_url_prefix: str,
        cohort_ranking_reasons: dict[str, str],
    ) -> None:
        rows: list[tuple] = []
        for entry in entries:
            effective_ranking_reason = cohort_ranking_reasons.get(
                entry.result_id,
                entry.ranking_exclusion_reason,
            )
            detail = details_map.get(entry.result_id)
            has_plans = detail.has_plans if detail is not None else False
            plans_published = detail.plans_published if detail is not None else False
            has_tuning = detail.has_tuning if detail is not None else False
            bundle_download_url = f"{bundle_url_prefix}/{entry.result_id}.json"
            physical_mechanisms = (
                None
                if detail is None or detail.physical_mechanisms is None
                else ",".join(sorted(detail.physical_mechanisms))
            )
            physical_rendering_id = detail.physical_rendering_id if detail is not None else None
            rows.append(
                (
                    entry.result_id,
                    entry.benchmark,
                    entry.scale_factor,
                    entry.platform,
                    entry.platform_id,
                    entry.driver_version,
                    entry.run_date,
                    entry.power_score,
                    entry.total_duration_s,
                    entry.geomean_ms,
                    entry.display_geomean_ms,
                    entry.query_count,
                    entry.logical_query_count,
                    entry.has_display_timing,
                    entry.valid_query_count,
                    entry.missing_query_count,
                    entry.zero_timing_count,
                    entry.display_exclusion_reason,
                    entry.comparison_exclusion_reason,
                    effective_ranking_reason,
                    entry.trust_label,
                    entry.visibility,
                    entry.funding,
                    entry.platform_version,
                    entry.execution_mode,
                    entry.tuning_mode,
                    entry.tuning_hash,
                    entry.requested_config_hash,
                    entry.applied_ledger_hash,
                    entry.tuning_validation_status,
                    entry.applied_receipt,
                    json.dumps(entry.override_rules) if entry.override_rules else None,
                    entry.override_evidence,
                    entry.override_approver,
                    entry.override_expires,
                    entry.tuning_policy_generation,
                    entry.template_evidence,
                    entry.test_type,
                    entry.validation_status,
                    entry.cost_usd,
                    *_normalized_cost_column_values(entry),
                    *_environment_facet_column_values(entry),
                    *_legacy_cost_deployment_column_values(entry),
                    entry.compliance_class,
                    is_ranking_eligible(entry) and effective_ranking_reason is None,
                    has_plans,
                    plans_published,
                    has_tuning,
                    bundle_download_url,
                    physical_mechanisms,
                    physical_rendering_id,
                    entry.benchmark_support_status,
                    entry.throughput_at_size,
                    entry.stream_count,
                )
            )
        if rows:
            placeholders = ", ".join(["?"] * len(rows[0]))
            con.executemany(f"INSERT INTO results VALUES ({placeholders})", rows)

    def _populate_result_basis_availability(
        self,
        con: Any,
        entries: list[ManifestEntry],
        details_map: dict[str, DetailResult],
    ) -> None:
        rows: list[tuple] = []
        for entry in entries:
            detail = details_map.get(entry.result_id)
            basis_avail = detail.basis_availability if detail is not None else None
            if basis_avail is None:
                basis_avail = entry.basis_availability
            if basis_avail is None:
                from _project.scripts.explorer_pipeline.transformer import _compute_basis_availability

                queries = detail.queries if detail is not None else []
                basis_avail = _compute_basis_availability(queries)
            varying_json = (
                json.dumps(basis_avail.varying_pass_queries, sort_keys=True)
                if basis_avail.varying_pass_queries
                else None
            )
            rows.append(
                (
                    entry.result_id,
                    basis_avail.has_warmup,
                    basis_avail.measurement_pass_count,
                    basis_avail.warmup_status,
                    ",".join(basis_avail.available_bases),
                    varying_json,
                )
            )
        if rows:
            con.executemany("INSERT INTO result_basis_availability VALUES (?, ?, ?, ?, ?, ?)", rows)

    def _populate_query_display_timings(
        self,
        con: Any,
        entries: list[ManifestEntry],
        details_map: dict[str, DetailResult],
    ) -> None:
        rows: list[tuple] = []
        for entry in entries:
            detail = details_map.get(entry.result_id)
            if detail is None:
                continue
            for dt in detail.display_timings:
                rows.append(
                    (
                        entry.result_id,
                        dt.query_id,
                        dt.display_ms,
                        dt.sample_count,
                        display_timing_is_valid(dt.display_ms),
                        timing_exclusion_reason(dt.display_ms),
                    )
                )
        if rows:
            con.executemany("INSERT INTO query_display_timings VALUES (?, ?, ?, ?, ?, ?)", rows)

    def _populate_query_executions(
        self,
        con: Any,
        entries: list[ManifestEntry],
        details_map: dict[str, DetailResult],
    ) -> None:
        rows: list[tuple] = []
        for entry in entries:
            detail = details_map.get(entry.result_id)
            if detail is None:
                continue
            for qt in detail.queries:
                rows.append(
                    (
                        entry.result_id,
                        qt.query_id,
                        qt.duration_ms,
                        qt.status,
                        qt.run_type,
                        qt.iter,
                        qt.stream,
                    )
                )
        if rows:
            con.executemany("INSERT INTO query_executions VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    def _populate_benchmark_matrix_cells(
        self,
        con: Any,
        summaries: list[tuple[_SummaryKey, BenchmarkSummary]],
    ) -> None:
        rows: list[tuple] = []
        for (benchmark, scale_factor, phase, stream_count), summary in summaries:
            for platform_row in summary.platforms:
                for query_id, display_ms in platform_row.timings.items():
                    rows.append(
                        (
                            benchmark,
                            scale_factor,
                            phase,
                            platform_row.result_id,
                            platform_row.platform_id,
                            query_id,
                            display_ms,
                            display_timing_is_valid(display_ms),
                            timing_exclusion_reason(display_ms),
                            stream_count,
                        )
                    )
        if rows:
            con.executemany("INSERT INTO benchmark_matrix_cells VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def _populate_benchmark_rankings(
        self,
        con: Any,
        summaries: list[tuple[_SummaryKey, BenchmarkSummary]],
        full_to_short: dict[str, str],
        entries_by_result: dict[str, ManifestEntry],
    ) -> None:
        rows: list[tuple] = []
        for (benchmark, scale_factor, phase, stream_count), summary in summaries:
            ranked = rank_platforms(summary)

            for ranked_row in ranked.rows:
                platform_row = ranked_row.row
                entry = entries_by_result.get(platform_row.result_id)
                timing_contract = (
                    _entry_timing_eligibility(entry)
                    if entry is not None
                    else _platform_row_timing_eligibility(platform_row, len(summary.query_ids))
                )
                ranking_reason = ranked_row.ranking_exclusion_reason or (
                    entry.ranking_exclusion_reason if entry is not None else None
                )
                ps = platform_row.percentile_stats
                rows.append(
                    (
                        benchmark,
                        scale_factor,
                        phase,
                        platform_row.result_id,
                        platform_row.platform_id,
                        platform_row.platform,
                        full_to_short.get(platform_row.result_id, ""),
                        platform_row.trust_label,
                        entry.funding if entry is not None else "unspecified",
                        platform_row.tuning_mode,
                        platform_row.tuning_hash,
                        platform_row.execution_mode,
                        platform_row.compliance_class,
                        platform_row.run_date,
                        platform_row.is_ranking_eligible,
                        timing_contract.has_display_timing,
                        timing_contract.logical_query_count,
                        timing_contract.valid_query_count,
                        timing_contract.missing_query_count,
                        timing_contract.zero_timing_count,
                        timing_contract.display_exclusion_reason,
                        timing_contract.comparison_exclusion_reason,
                        ranking_reason,
                        platform_row.power_score,
                        platform_row.display_geomean_ms,
                        platform_row.sample_geomean_ms,
                        platform_row.cost_usd,
                        ranked.primary_metric,
                        ranked.primary_order,
                        ranked_row.rank,
                        ranked_row.total_ranked,
                        ranked.total_ranked,
                        "no_rankable_results" if ranked.total_ranked == 0 else None,
                        ps.p50 if ps else None,
                        ps.p90 if ps else None,
                        ps.p95 if ps else None,
                        ps.p99 if ps else None,
                        ranked_row.speedup_vs_best,
                        ranked_row.speedup_vs_slowest,
                        platform_row.throughput_at_size,
                        stream_count,
                    )
                )
        if rows:
            placeholders = ", ".join(["?"] * len(rows[0]))
            con.executemany(f"INSERT INTO benchmark_rankings VALUES ({placeholders})", rows)

    def _populate_cohort_metadata(
        self,
        con: Any,
        meta: dict[str, Any],
        summaries: list[tuple[_SummaryKey, BenchmarkSummary]],
        entries_by_result: dict[str, ManifestEntry],
    ) -> None:
        rows: list[tuple] = []
        ranking_context_by_result: dict[str, tuple[TimingEligibility, str | None, int, str | None]] = {}
        for _, summary in summaries:
            ranked = rank_platforms(summary)
            cohort_reason = "no_rankable_results" if ranked.total_ranked == 0 else None
            for ranked_row in ranked.rows:
                entry = entries_by_result.get(ranked_row.row.result_id)
                timing_contract = (
                    _entry_timing_eligibility(entry)
                    if entry is not None
                    else _platform_row_timing_eligibility(ranked_row.row, len(summary.query_ids))
                )
                ranking_context_by_result[ranked_row.row.result_id] = (
                    timing_contract,
                    ranked_row.ranking_exclusion_reason
                    or (entry.ranking_exclusion_reason if entry is not None else None),
                    ranked.total_ranked,
                    cohort_reason,
                )

        for cohort in meta.get("cohorts", []):
            cohort_key = cohort["key"]
            benchmark = cohort["benchmark"]
            scale_factor = cohort["scale_factor"]
            phase = cohort["phase"]
            cohort_label = cohort["label"]
            cohort_href = cohort["href"]
            platform_count = cohort["platform_count"]
            cohort_ranked_count = cohort.get("cohort_ranked_count", platform_count)
            cohort_ranking_exclusion_reason = cohort.get(
                "cohort_ranking_exclusion_reason",
                "no_rankable_results" if cohort_ranked_count == 0 else None,
            )
            primary_metric = cohort["primary_metric"]
            primary_order = cohort["primary_order"]
            stream_count = cohort.get("stream_count")
            for p in cohort.get("platforms", []):
                timing_contract, row_ranking_reason, ranked_count, cohort_reason = ranking_context_by_result.get(
                    p["result_id"],
                    (
                        TimingEligibility(
                            has_display_timing=False,
                            logical_query_count=0,
                            valid_query_count=0,
                            missing_query_count=0,
                            zero_timing_count=0,
                            display_exclusion_reason="missing_timing_context",
                            comparison_exclusion_reason="missing_timing_context",
                        ),
                        p.get("ranking_exclusion_reason"),
                        cohort_ranked_count,
                        cohort_ranking_exclusion_reason,
                    ),
                )
                missing = _COHORT_PLATFORM_REQUIRED_KEYS - p.keys()
                if missing:
                    raise ValueError(
                        f"cohort platform row missing required keys {sorted(missing)} "
                        f"for cohort {cohort_key!r}; upstream contract broken"
                    )
                rows.append(
                    (
                        cohort_key,
                        benchmark,
                        scale_factor,
                        phase,
                        cohort_label,
                        cohort_href,
                        platform_count,
                        ranked_count,
                        cohort_reason,
                        primary_metric,
                        primary_order,
                        p["platform_id"],
                        p["platform"],
                        p["result_id"],
                        p["short_id"],
                        p.get("tuning_mode"),
                        p["trust_label"],
                        timing_contract.has_display_timing,
                        timing_contract.logical_query_count,
                        timing_contract.valid_query_count,
                        timing_contract.missing_query_count,
                        timing_contract.zero_timing_count,
                        timing_contract.display_exclusion_reason,
                        timing_contract.comparison_exclusion_reason,
                        row_ranking_reason,
                        p.get("rank"),
                        p.get("metric_value"),
                        p.get("speedup_vs_best"),
                        stream_count,
                    )
                )

        if rows:
            placeholders = ", ".join(["?"] * len(rows[0]))
            con.executemany(f"INSERT INTO cohort_metadata VALUES ({placeholders})", rows)

    def _populate_meta_leaderboard(self, con: Any, meta: dict[str, Any]) -> None:
        rows: list[tuple] = []
        for p in meta.get("platforms", []):
            rows.append(
                (
                    p["platform_id"],
                    p["platform"],
                    p.get("avg_rank"),
                    p.get("n_cohorts", 0),
                )
            )
        if rows:
            con.executemany("INSERT INTO meta_leaderboard VALUES (?, ?, ?, ?)", rows)

    def _populate_short_ids(self, con: Any, short_id_map: dict[str, str]) -> None:
        rows = list(short_id_map.items())
        if rows:
            con.executemany("INSERT INTO short_ids VALUES (?, ?)", rows)

    @staticmethod
    def _entry_to_tuple(entry: ManifestEntry) -> tuple:
        return (
            entry.result_id,
            entry.benchmark,
            entry.scale_factor,
            entry.platform,
            entry.driver_version,
            entry.run_date,
            entry.power_score,
            entry.total_duration_s,
            entry.geomean_ms,
            entry.display_geomean_ms,
            entry.query_count,
            entry.logical_query_count,
            entry.has_display_timing,
            entry.valid_query_count,
            entry.missing_query_count,
            entry.zero_timing_count,
            entry.display_exclusion_reason,
            entry.comparison_exclusion_reason,
            entry.ranking_exclusion_reason,
            entry.trust_label,
            entry.visibility,
            entry.funding,
            entry.platform_version,
            entry.execution_mode,
            entry.tuning_mode,
            entry.tuning_hash,
            entry.test_type,
            entry.validation_status,
            entry.cost_usd,
            *_normalized_cost_column_values(entry),
            *_environment_facet_column_values(entry),
            *_legacy_cost_deployment_column_values(entry),
            entry.benchmark_support_status,
            entry.throughput_at_size,
            entry.stream_count,
        )


__all__ = ["DuckDBSnapshotBuilder"]
