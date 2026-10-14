# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchbox.core.cost.models import cost_status_of, published_total_cost
from benchbox.core.results.environment import build_platform_metadata_payload
from benchbox.core.results.execution_variant import ExecutionVariant, platform_info_bundle, read_execution_variant
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.platform_options import sanitize_platform_options
from benchbox.core.results.regression_policy import is_regression

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".benchbox" / "results.db"

SCHEMA_VERSION = 3
_RANKING_METRIC_CONFIG = {
    "geometric_mean": ("geometric_mean_ms", "ASC"),
    "power_at_size": ("power_at_size", "DESC"),
    "cost_efficiency": ("total_cost / NULLIF(successful_queries, 0)", "ASC"),
}
_BACKUP_TABLE_NAME_PATTERN = re.compile(r"^queries_orphan_backup_\d{20}$")


def _validate_backup_table_name(table_name: str) -> str:
    if _BACKUP_TABLE_NAME_PATTERN.fullmatch(table_name) is None:
        raise ValueError(f"Unsafe SQLite backup table name: {table_name!r}")
    return table_name


@dataclass
class StoredResult:
    id: int
    execution_id: str
    platform: str
    platform_version: str | None
    benchmark: str
    scale_factor: float
    timestamp: datetime
    duration_seconds: float
    total_queries: int
    successful_queries: int
    failed_queries: int
    geometric_mean_ms: float | None
    power_at_size: float | None
    throughput_at_size: float | None
    qph_at_size: float | None
    total_cost: float | None
    validation_status: str
    config_hash: str
    metadata: dict[str, Any]
    compliance_class: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> StoredResult:
        keys = row.keys()
        return cls(
            id=row["id"],
            execution_id=row["execution_id"],
            platform=row["platform"],
            platform_version=row["platform_version"],
            benchmark=row["benchmark"],
            scale_factor=row["scale_factor"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            duration_seconds=row["duration_seconds"],
            total_queries=row["total_queries"],
            successful_queries=row["successful_queries"],
            failed_queries=row["failed_queries"],
            geometric_mean_ms=row["geometric_mean_ms"],
            power_at_size=row["power_at_size"],
            throughput_at_size=row["throughput_at_size"],
            qph_at_size=row["qph_at_size"],
            total_cost=row["total_cost"],
            validation_status=row["validation_status"],
            config_hash=row["config_hash"],
            compliance_class=row["compliance_class"] if "compliance_class" in keys else None,
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )


@dataclass
class StoredQuery:
    id: int
    result_id: int
    query_id: str
    stream_id: str | None
    execution_order: int
    execution_time_ms: float
    status: str
    rows_returned: int | None
    cost: float | None
    iteration: int | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> StoredQuery:
        return cls(
            id=row["id"],
            result_id=row["result_id"],
            query_id=row["query_id"],
            stream_id=row["stream_id"],
            execution_order=row["execution_order"],
            execution_time_ms=row["execution_time_ms"],
            status=row["status"],
            rows_returned=row["rows_returned"],
            cost=row["cost"],
            iteration=row["iteration"],
        )


@dataclass
class PlatformRanking:
    rank: int
    platform: str
    platform_version: str | None
    benchmark: str
    scale_factor: float
    score: float
    sample_count: int
    latest_timestamp: datetime
    trend: str
    trend_change: float | None


@dataclass
class PerformanceTrend:
    platform: str
    benchmark: str
    scale_factor: float
    period_start: datetime
    period_end: datetime
    avg_geometric_mean_ms: float
    min_geometric_mean_ms: float
    max_geometric_mean_ms: float
    sample_count: int
    change_pct: float | None
    is_regression: bool
    variant: str | None = None


def _result_variant(result: BenchmarkResults) -> ExecutionVariant:
    run_config = (result.execution_metadata or {}).get("run_config")
    bundle = platform_info_bundle(
        result.platform,
        result.platform_info,
        deployment=result.platform_deployment,
        execution_engine=result.execution_engine,
    )
    if isinstance(run_config, Mapping):
        bundle["config"] = dict(run_config)
    return read_execution_variant(bundle)


def _stored_variant(platform: str, metadata: Mapping[str, Any]) -> ExecutionVariant:
    stored = metadata.get("execution_variant")
    if isinstance(stored, Mapping):
        try:
            return ExecutionVariant(**stored)
        except TypeError:
            pass
    platform_block = metadata.get("platform")
    deployment = platform_block.get("deployment") if isinstance(platform_block, Mapping) else None
    info = metadata.get("platform_info")
    return read_execution_variant(
        platform_info_bundle(platform, info if isinstance(info, Mapping) else None, deployment=deployment)
    )


@dataclass
class RankingConfig:
    metric: str = "geometric_mean"
    min_samples: int = 1
    lookback_days: int = 90
    require_success: bool = True


class ResultDatabase:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            """
            )

            cursor.execute("SELECT version FROM schema_version LIMIT 1")
            row = cursor.fetchone()
            current_version = row[0] if row else 0

            if current_version < SCHEMA_VERSION:
                if current_version == 0:
                    self._create_tables(cursor)
                else:
                    if current_version < 2:
                        self._migrate_to_v2(cursor)
                    if current_version < 3:
                        self._migrate_to_v3(cursor)
                cursor.execute("DELETE FROM schema_version")
                cursor.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
                conn.commit()
                logger.info(f"Database schema migrated to version {SCHEMA_VERSION}")

    def _create_tables(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT UNIQUE NOT NULL,
                platform TEXT NOT NULL,
                platform_version TEXT,
                benchmark TEXT NOT NULL,
                scale_factor REAL NOT NULL,
                timestamp TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                total_queries INTEGER NOT NULL,
                successful_queries INTEGER NOT NULL,
                failed_queries INTEGER NOT NULL,
                geometric_mean_ms REAL,
                power_at_size REAL,
                throughput_at_size REAL,
                qph_at_size REAL,
                total_cost REAL,
                validation_status TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                compliance_class TEXT,
                metadata_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                result_id INTEGER NOT NULL,
                query_id TEXT NOT NULL,
                stream_id TEXT,
                execution_order INTEGER NOT NULL,
                execution_time_ms REAL NOT NULL,
                status TEXT NOT NULL,
                rows_returned INTEGER,
                cost REAL,
                iteration INTEGER,
                FOREIGN KEY (result_id) REFERENCES results(id) ON DELETE CASCADE
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS platforms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                version TEXT,
                config_hash TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                UNIQUE(name, version, config_hash)
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_results_platform ON results(platform)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_results_benchmark ON results(benchmark)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_results_timestamp ON results(timestamp)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_results_platform_benchmark ON results(platform, benchmark, scale_factor)"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_queries_result ON queries(result_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_queries_query_id ON queries(query_id)")

    def _migrate_to_v2(self, cursor: sqlite3.Cursor) -> None:
        try:
            cursor.execute("ALTER TABLE results ADD COLUMN compliance_class TEXT")
            logger.info("Schema v2: added compliance_class column to results table")
        except sqlite3.OperationalError:
            pass

    def _migrate_to_v3(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute(
            "UPDATE results SET validation_status = UPPER(validation_status) "
            "WHERE validation_status <> UPPER(validation_status)"
        )
        logger.info("Schema v3: normalized validation_status to uppercase")

    def _compute_config_hash(self, result: BenchmarkResults) -> str:
        config_data = {
            "platform": result.platform,
            "benchmark": result.benchmark_name,
            "scale_factor": result.scale_factor,
            "tunings": result.tunings_applied or {},
        }
        config_str = json.dumps(config_data, sort_keys=True)
        return hashlib.sha256(config_str.encode()).hexdigest()[:16]

    def store_result(self, result: BenchmarkResults) -> int:
        config_hash = self._compute_config_hash(result)

        platform_version = None
        if result.platform_info:
            platform_version = result.platform_info.get("platform_version")

        total_cost = published_total_cost(result.cost_summary)
        persist_per_query_costs = cost_status_of(result.cost_summary) != "unavailable"

        metadata: dict[str, Any] = {
            "system_profile": result.system_profile,
            "platform_info": sanitize_platform_options(result.platform_info or {}),
            "tunings_applied": result.tunings_applied,
            "test_execution_type": result.test_execution_type,
            "execution_variant": asdict(_result_variant(result)),
        }
        has_explicit_platform_meta = any(
            getattr(result, name, None) is not None
            for name in (
                "platform_deployment",
                "platform_cloud",
                "platform_compute",
                "platform_storage",
                "platform_raw_config",
                "platform_raw_metadata",
            )
        )
        if has_explicit_platform_meta:
            metadata["platform"] = build_platform_metadata_payload(
                platform_info=result.platform_info,
                platform_config=None,
                deployment=getattr(result, "platform_deployment", None),
                cloud=getattr(result, "platform_cloud", None),
                compute=getattr(result, "platform_compute", None),
                storage=getattr(result, "platform_storage", None),
                raw_config=getattr(result, "platform_raw_config", None),
                raw_metadata=getattr(result, "platform_raw_metadata", None),
            )

        with self._connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT id FROM results WHERE execution_id = ?",
                (result.execution_id,),
            )
            if cursor.fetchone():
                raise ValueError(f"Result with execution_id '{result.execution_id}' already exists")

            geometric_mean_ms = None
            if result.geometric_mean_execution_time is not None:
                geometric_mean_ms = result.geometric_mean_execution_time * 1000.0

            compliance_class = getattr(result, "compliance_class", None)
            if compliance_class is not None:
                compliance_class = str(compliance_class)

            cursor.execute(
                """
                INSERT INTO results (
                    execution_id, platform, platform_version, benchmark, scale_factor,
                    timestamp, duration_seconds, total_queries, successful_queries,
                    failed_queries, geometric_mean_ms, power_at_size, throughput_at_size,
                    qph_at_size, total_cost, validation_status, config_hash,
                    compliance_class, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    result.execution_id,
                    result.platform,
                    platform_version,
                    result.benchmark_name,
                    result.scale_factor,
                    result.timestamp.isoformat(),
                    result.duration_seconds,
                    result.total_queries,
                    result.successful_queries,
                    result.failed_queries,
                    geometric_mean_ms,
                    result.power_at_size,
                    result.throughput_at_size,
                    result.qph_at_size,
                    total_cost,
                    result.validation_status.upper(),
                    config_hash,
                    compliance_class,
                    json.dumps(metadata),
                ),
            )
            result_id = cursor.lastrowid or 0

            if result.execution_phases and result.execution_phases.power_test:
                for execution in result.execution_phases.power_test.query_executions:
                    cursor.execute(
                        """
                        INSERT INTO queries (
                            result_id, query_id, stream_id, execution_order,
                            execution_time_ms, status, rows_returned, cost, iteration
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            result_id,
                            execution.query_id,
                            execution.stream_id,
                            execution.execution_order,
                            execution.execution_time_ms,
                            execution.status,
                            execution.rows_returned,
                            execution.cost if persist_per_query_costs else None,
                            execution.iteration,
                        ),
                    )

            cursor.execute(
                """
                INSERT INTO platforms (name, version, config_hash, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name, version, config_hash)
                DO UPDATE SET last_seen = excluded.last_seen
            """,
                (
                    result.platform,
                    platform_version,
                    config_hash,
                    result.timestamp.isoformat(),
                    result.timestamp.isoformat(),
                ),
            )

            conn.commit()
            logger.info(f"Stored result {result.execution_id} for {result.platform}/{result.benchmark_name}")
            return result_id

    def get_result(self, execution_id: str) -> StoredResult | None:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM results WHERE execution_id = ?",
                (execution_id,),
            )
            row = cursor.fetchone()
            return StoredResult.from_row(row) if row else None

    def get_result_by_id(self, result_id: int) -> StoredResult | None:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM results WHERE id = ?", (result_id,))
            row = cursor.fetchone()
            return StoredResult.from_row(row) if row else None

    def get_queries(self, result_id: int) -> list[StoredQuery]:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM queries WHERE result_id = ? ORDER BY execution_order",
                (result_id,),
            )
            return [StoredQuery.from_row(row) for row in cursor.fetchall()]

    def query_results(
        self,
        platform: str | None = None,
        benchmark: str | None = None,
        scale_factor: float | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        validation_status: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredResult]:
        conditions = []
        params: list[Any] = []

        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if benchmark:
            conditions.append("benchmark = ?")
            params.append(benchmark)
        if scale_factor is not None:
            conditions.append("scale_factor = ?")
            params.append(scale_factor)
        if start_date:
            conditions.append("timestamp >= ?")
            params.append(start_date.isoformat())
        if end_date:
            conditions.append("timestamp <= ?")
            params.append(end_date.isoformat())
        if validation_status:
            conditions.append("UPPER(validation_status) = ?")
            params.append(validation_status.upper())

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT * FROM results
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """,
                params,
            )
            return [StoredResult.from_row(row) for row in cursor.fetchall()]

    def count_results(
        self,
        platform: str | None = None,
        benchmark: str | None = None,
    ) -> int:
        conditions = []
        params: list[Any] = []

        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if benchmark:
            conditions.append("benchmark = ?")
            params.append(benchmark)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) FROM results WHERE {where_clause}",
                params,
            )
            return cursor.fetchone()[0]

    def get_platforms(self) -> list[str]:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT platform FROM results ORDER BY platform")
            return [row[0] for row in cursor.fetchall()]

    def get_benchmarks(self) -> list[str]:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT benchmark FROM results ORDER BY benchmark")
            return [row[0] for row in cursor.fetchall()]

    def delete_result(self, execution_id: str) -> bool:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM results WHERE execution_id = ?",
                (execution_id,),
            )
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Deleted result {execution_id}")
            return deleted

    def count_orphaned_queries(self) -> int:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM queries AS q
                WHERE NOT EXISTS (
                    SELECT 1 FROM results AS r WHERE r.id = q.result_id
                )
                """
            ).fetchone()
            return int(row[0])

    def repair_orphaned_queries(self, *, dry_run: bool = True) -> int:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM queries AS q
                WHERE NOT EXISTS (
                    SELECT 1 FROM results AS r WHERE r.id = q.result_id
                )
                """
            ).fetchone()
            orphan_count = int(row[0])
            if dry_run or orphan_count == 0:
                return orphan_count

            backup_table = _validate_backup_table_name(
                f"queries_orphan_backup_{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}"
            )
            conn.execute(
                f"""
                CREATE TABLE {backup_table} AS
                SELECT q.*
                FROM queries AS q
                WHERE NOT EXISTS (
                    SELECT 1 FROM results AS r WHERE r.id = q.result_id
                )
                """
            )
            conn.execute(
                """
                DELETE FROM queries
                WHERE NOT EXISTS (
                    SELECT 1 FROM results AS r WHERE r.id = queries.result_id
                )
                """
            )
            conn.commit()
            logger.warning("Removed %d orphaned query rows; backup table: %s", orphan_count, backup_table)
            return orphan_count

    def calculate_rankings(
        self,
        benchmark: str,
        scale_factor: float,
        config: RankingConfig | None = None,
        include_unofficial: bool = False,
    ) -> list[PlatformRanking]:
        config = config or RankingConfig()

        cutoff = datetime.now(timezone.utc)
        from datetime import timedelta

        cutoff = cutoff - timedelta(days=config.lookback_days)

        with self._connection() as conn:
            cursor = conn.cursor()

            metric_col, order = _RANKING_METRIC_CONFIG.get(config.metric, _RANKING_METRIC_CONFIG["geometric_mean"])

            validation_filter = ""
            if config.require_success:
                validation_filter = "AND UPPER(validation_status) = 'PASSED'"

            unofficial_filter = (
                "" if include_unofficial else "AND (compliance_class = 'official' OR compliance_class IS NULL)"
            )

            cursor.execute(
                f"""
                SELECT
                    platform,
                    platform_version,
                    AVG({metric_col}) as avg_score,
                    COUNT(*) as sample_count,
                    MAX(timestamp) as latest_timestamp
                FROM results
                WHERE benchmark = ?
                    AND scale_factor = ?
                    AND timestamp >= ?
                    AND {metric_col} IS NOT NULL
                    {validation_filter}
                    {unofficial_filter}
                GROUP BY platform, platform_version
                HAVING COUNT(*) >= ?
                ORDER BY avg_score {order}
            """,
                (benchmark, scale_factor, cutoff.isoformat(), config.min_samples),
            )

            current_results = cursor.fetchall()

            prev_cutoff = cutoff - timedelta(days=config.lookback_days)
            cursor.execute(
                f"""
                SELECT
                    platform,
                    AVG({metric_col}) as avg_score
                FROM results
                WHERE benchmark = ?
                    AND scale_factor = ?
                    AND timestamp >= ?
                    AND timestamp < ?
                    AND {metric_col} IS NOT NULL
                    {validation_filter}
                    {unofficial_filter}
                GROUP BY platform
            """,
                (
                    benchmark,
                    scale_factor,
                    prev_cutoff.isoformat(),
                    cutoff.isoformat(),
                ),
            )

            previous_scores = {row["platform"]: row["avg_score"] for row in cursor.fetchall()}

            rankings = []
            for rank, row in enumerate(current_results, 1):
                platform = row["platform"]
                current_score = row["avg_score"]
                prev_score = previous_scores.get(platform)
                trend, trend_change = self._calculate_ranking_trend(current_score, prev_score, config.metric)

                rankings.append(
                    PlatformRanking(
                        rank=rank,
                        platform=platform,
                        platform_version=row["platform_version"],
                        benchmark=benchmark,
                        scale_factor=scale_factor,
                        score=current_score,
                        sample_count=row["sample_count"],
                        latest_timestamp=datetime.fromisoformat(row["latest_timestamp"]),
                        trend=trend,
                        trend_change=trend_change,
                    )
                )

            return rankings

    @staticmethod
    def _calculate_ranking_trend(
        current_score: float,
        previous_score: float | None,
        metric: str,
    ) -> tuple[str, float | None]:
        if previous_score is None:
            return "new", None
        if previous_score == 0:
            return "stable", 0.0

        change_pct = ((current_score - previous_score) / previous_score) * 100
        if metric in ("geometric_mean", "cost_efficiency"):
            if change_pct < -5:
                return "up", change_pct
            if change_pct > 5:
                return "down", change_pct
            return "stable", change_pct

        if change_pct > 5:
            return "up", change_pct
        if change_pct < -5:
            return "down", change_pct
        return "stable", change_pct

    def _variant_rows(
        self, platform: str, benchmark: str, scale_factor: float
    ) -> list[tuple[str, float, ExecutionVariant]]:
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT timestamp, geometric_mean_ms, metadata_json
                FROM results
                WHERE platform = ?
                    AND benchmark = ?
                    AND scale_factor = ?
                    AND geometric_mean_ms IS NOT NULL
                    AND UPPER(validation_status) = 'PASSED'
                ORDER BY timestamp DESC
            """,
                (platform, benchmark, scale_factor),
            )
            return [
                (
                    row["timestamp"],
                    row["geometric_mean_ms"],
                    _stored_variant(platform, json.loads(row["metadata_json"]) if row["metadata_json"] else {}),
                )
                for row in cursor.fetchall()
            ]

    def _distinct_variants(self, platform: str, benchmark: str, scale_factor: float) -> list[ExecutionVariant]:
        variants: dict[tuple[str | None, str, str], ExecutionVariant] = {}
        for _, _, variant in self._variant_rows(platform, benchmark, scale_factor):
            variants.setdefault(variant.comparison_key(), variant)
        return list(variants.values())

    def get_performance_trends(
        self,
        platform: str,
        benchmark: str,
        scale_factor: float,
        periods: int = 6,
        period_days: int = 30,
        variant: ExecutionVariant | None = None,
    ) -> list[PerformanceTrend]:
        rows = self._variant_rows(platform, benchmark, scale_factor)
        if variant is None and rows:
            variant = rows[0][2]
        return self._period_trends(platform, benchmark, scale_factor, rows, variant, periods, period_days)

    @staticmethod
    def _period_trends(
        platform: str,
        benchmark: str,
        scale_factor: float,
        rows: list[tuple[str, float, ExecutionVariant]],
        variant: ExecutionVariant | None,
        periods: int,
        period_days: int,
    ) -> list[PerformanceTrend]:
        from datetime import timedelta

        if variant is None:
            return []
        key = variant.comparison_key()
        values = [(timestamp, value) for timestamp, value, row_variant in rows if row_variant.comparison_key() == key]
        now = datetime.now(timezone.utc)
        trends: list[PerformanceTrend] = []

        for i in range(periods):
            period_end = now - timedelta(days=i * period_days)
            period_start = period_end - timedelta(days=period_days)
            start_text, end_text = period_start.isoformat(), period_end.isoformat()
            window = [value for timestamp, value in values if start_text <= timestamp < end_text]
            if not window:
                continue
            trends.append(
                PerformanceTrend(
                    platform=platform,
                    benchmark=benchmark,
                    scale_factor=scale_factor,
                    period_start=period_start,
                    period_end=period_end,
                    avg_geometric_mean_ms=sum(window) / len(window),
                    min_geometric_mean_ms=min(window),
                    max_geometric_mean_ms=max(window),
                    sample_count=len(window),
                    change_pct=None,
                    is_regression=False,
                    variant=variant.describe(),
                )
            )

        for i in range(len(trends) - 1):
            current = trends[i]
            previous = trends[i + 1]
            if previous.avg_geometric_mean_ms > 0:
                change = (
                    (current.avg_geometric_mean_ms - previous.avg_geometric_mean_ms) / previous.avg_geometric_mean_ms
                ) * 100
                trends[i] = replace(current, change_pct=change, is_regression=is_regression(change))

        return trends

    def detect_regressions(
        self,
        threshold_pct: float = 10.0,
        lookback_days: int = 30,
    ) -> list[PerformanceTrend]:
        regressions = []

        platforms = self.get_platforms()
        benchmarks = self.get_benchmarks()

        for platform in platforms:
            for benchmark in benchmarks:
                with self._connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT DISTINCT scale_factor
                        FROM results
                        WHERE platform = ? AND benchmark = ?
                    """,
                        (platform, benchmark),
                    )
                    scale_factors = [row[0] for row in cursor.fetchall()]

                for sf in scale_factors:
                    for variant in self._distinct_variants(platform, benchmark, sf):
                        trends = self.get_performance_trends(
                            platform, benchmark, sf, periods=2, period_days=lookback_days, variant=variant
                        )
                        regressions.extend(
                            replace(trend, is_regression=True)
                            for trend in trends
                            if is_regression(trend.change_pct, threshold_pct)
                        )

        return regressions

    def get_summary_stats(self) -> dict[str, Any]:
        with self._connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM results")
            total_results = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM queries")
            total_queries = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT platform) FROM results")
            unique_platforms = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT benchmark) FROM results")
            unique_benchmarks = cursor.fetchone()[0]

            cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM results")
            row = cursor.fetchone()
            earliest = row[0]
            latest = row[1]

            return {
                "total_results": total_results,
                "total_queries": total_queries,
                "unique_platforms": unique_platforms,
                "unique_benchmarks": unique_benchmarks,
                "earliest_result": earliest,
                "latest_result": latest,
                "database_path": str(self.db_path),
                "schema_version": SCHEMA_VERSION,
            }

    def import_results_from_directory(
        self,
        directory: Path,
        pattern: str = "**/*.json",
        include_unofficial: bool = False,
    ) -> tuple[int, int, int]:
        from benchbox.core.results.loader import load_result_file

        _unofficial_classes = {"unofficial_nonstandard", "unofficial_subscale"}
        imported = 0
        skipped = 0
        excluded = 0

        for file_path in directory.glob(pattern):
            try:
                result, _ = load_result_file(file_path)
                compliance_class = getattr(result, "compliance_class", None)
                if not include_unofficial:
                    if compliance_class in _unofficial_classes:
                        excluded += 1
                        continue
                if compliance_class is None and getattr(result, "benchmark_name", "").lower() in (
                    "tpc-ds",
                    "tpcds",
                ):
                    logger.warning(
                        "Importing legacy TPC-DS result %s without compliance_class; "
                        "it will be treated as official in ranking queries. "
                        "Re-run the benchmark to generate a tagged result.",
                        file_path.name,
                    )
                self.store_result(result)
                imported += 1
            except ValueError as e:
                if "already exists" in str(e):
                    skipped += 1
                else:
                    logger.warning(f"Failed to import {file_path}: {e}")
                    skipped += 1
            except Exception as e:
                logger.warning(f"Failed to import {file_path}: {e}")
                skipped += 1

        return imported, skipped, excluded
