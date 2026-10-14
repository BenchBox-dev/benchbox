from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from benchbox.core.results.database import (
    DEFAULT_DB_PATH,
    SCHEMA_VERSION,
    PerformanceTrend,
    PlatformRanking,
    RankingConfig,
    ResultDatabase,
    StoredQuery,
    StoredResult,
    _validate_backup_table_name,
)
from benchbox.core.results.models import (
    BenchmarkResults,
    ExecutionPhases,
    PowerTestPhase,
    QueryExecution,
    SetupPhase,
)
from tests.fixtures.result_dict_fixtures import make_benchmark_results, make_v2_result_dict

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def create_test_result(
    execution_id: str = "test-exec-001",
    platform: str = "DuckDB",
    benchmark: str = "TPC-H",
    scale_factor: float = 1.0,
    timestamp: datetime | None = None,
    geometric_mean_ms: float = 100.0,
    power_at_size: float = 1000.0,
    validation_status: str = "PASSED",
    include_queries: bool = False,
    platform_version: str | None = "1.0.0",
    total_cost: float | None = None,
    compliance_class: str | None = None,
) -> BenchmarkResults:
    ts = timestamp or datetime.now(timezone.utc)

    execution_phases = None
    if include_queries:
        query_executions = [
            QueryExecution(
                query_id="1",
                stream_id="power",
                execution_order=1,
                execution_time_ms=50,
                status="SUCCESS",
                rows_returned=10,
                cost=0.01,
                iteration=1,
            ),
            QueryExecution(
                query_id="2",
                stream_id="power",
                execution_order=2,
                execution_time_ms=75,
                status="SUCCESS",
                rows_returned=20,
                cost=0.02,
                iteration=1,
            ),
        ]
        execution_phases = ExecutionPhases(
            setup=SetupPhase(),
            power_test=PowerTestPhase(
                start_time=ts.isoformat(),
                end_time=ts.isoformat(),
                duration_ms=125,
                query_executions=query_executions,
                geometric_mean_time=geometric_mean_ms / 1000.0,
                power_at_size=power_at_size,
            ),
        )

    platform_info = {"platform_version": platform_version} if platform_version else {}
    cost_summary = {"total_cost": total_cost} if total_cost is not None else None

    return make_benchmark_results(
        benchmark_name=benchmark,
        platform=platform,
        scale_factor=scale_factor,
        execution_id=execution_id,
        timestamp=ts,
        duration_seconds=10.0,
        total_queries=22,
        successful_queries=22,
        validation_status=validation_status,
        geometric_mean_execution_time=geometric_mean_ms / 1000.0,
        power_at_size=power_at_size,
        platform_info=platform_info,
        cost_summary=cost_summary,
        execution_phases=execution_phases,
        compliance_class=compliance_class,
    )


class TestResultDatabase:
    def test_init_creates_database(self, tmp_path):

        db_path = tmp_path / "test.db"
        db = ResultDatabase(db_path)
        assert db.db_path == db_path
        assert db_path.exists()

    def test_init_creates_parent_directories(self, tmp_path):

        db_path = tmp_path / "nested" / "dirs" / "test.db"
        ResultDatabase(db_path)
        assert db_path.exists()

    def test_init_default_path(self):

        db = ResultDatabase.__new__(ResultDatabase)
        db.db_path = None
        from tests.utilities.session_isolation import session_home

        assert session_home() / ".benchbox" / "results.db" == DEFAULT_DB_PATH

    def test_schema_version_stored(self, tmp_path):

        db_path = tmp_path / "test.db"
        db = ResultDatabase(db_path)

        with db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT version FROM schema_version")
            version = cursor.fetchone()[0]
            assert version == SCHEMA_VERSION

    def test_open_upgrades_lowercase_validation_status_rows(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        db = ResultDatabase(db_path)
        now = datetime.now(timezone.utc)
        for execution_id, geometric_mean in (("legacy-a", 0.1), ("legacy-b", 0.2), ("legacy-c", 0.3)):
            db.store_result(
                make_benchmark_results(
                    benchmark_name="TPC-H",
                    platform=f"Platform-{execution_id}",
                    execution_id=execution_id,
                    scale_factor=1.0,
                    timestamp=now,
                    total_queries=22,
                    successful_queries=22,
                    validation_status="PASSED",
                    geometric_mean_execution_time=geometric_mean,
                )
            )
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE results SET validation_status = 'passed' WHERE execution_id IN ('legacy-a', 'legacy-b')"
            )
            conn.execute("UPDATE results SET validation_status = 'Failed' WHERE execution_id = 'legacy-c'")
            conn.execute("UPDATE schema_version SET version = 2")
            conn.commit()

        reopened = ResultDatabase(db_path)
        with sqlite3.connect(db_path) as conn:
            statuses = dict(conn.execute("SELECT execution_id, validation_status FROM results").fetchall())
            version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert statuses == {"legacy-a": "PASSED", "legacy-b": "PASSED", "legacy-c": "FAILED"}
        assert version == SCHEMA_VERSION
        ranked = {entry.platform for entry in reopened.calculate_rankings("TPC-H", 1.0)}
        assert ranked == {"Platform-legacy-a", "Platform-legacy-b"}

        ResultDatabase(db_path)
        with sqlite3.connect(db_path) as conn:
            assert dict(conn.execute("SELECT execution_id, validation_status FROM results").fetchall()) == statuses

    def test_store_result_normalizes_status_and_filters_ignore_case(self, tmp_path):
        db = ResultDatabase(tmp_path / "status.db")
        db.store_result(
            make_benchmark_results(
                benchmark_name="TPC-H",
                platform="DuckDB",
                execution_id="lower",
                scale_factor=1.0,
                total_queries=1,
                successful_queries=1,
                validation_status="passed",
                geometric_mean_execution_time=0.1,
            )
        )

        assert [r.validation_status for r in db.query_results()] == ["PASSED"]
        assert len(db.query_results(validation_status="passed")) == 1
        assert len(db.query_results(validation_status="PASSED")) == 1
        assert [r.platform for r in db.calculate_rankings("TPC-H", 1.0)] == ["DuckDB"]

    def test_foreign_keys_enabled_on_every_connection(self, tmp_path):
        db = ResultDatabase(tmp_path / "test.db")

        with db._connection() as conn:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_delete_result_cascades_queries(self, tmp_path):
        db = ResultDatabase(tmp_path / "test.db")
        result_id = db.store_result(create_test_result(execution_id="cascade", include_queries=True))

        assert len(db.get_queries(result_id)) == 2
        assert db.delete_result("cascade") is True
        assert db.get_queries(result_id) == []

    def test_backup_table_identifier_validation_rejects_sql(self):
        valid_name = "queries_orphan_backup_20260826215959123456"
        assert _validate_backup_table_name(valid_name) == valid_name

        with pytest.raises(ValueError, match="Unsafe SQLite backup table name"):
            _validate_backup_table_name("queries_orphan_backup_20260826215959123456; DROP TABLE results")

    def test_orphan_repair_is_dry_run_by_default_and_backed_up_when_applied(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = ResultDatabase(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO queries
                    (result_id, query_id, execution_order, execution_time_ms, status)
                    VALUES (999999, 'orphan', 1, 1.0, 'ERROR')"""
            )
            conn.commit()

        assert db.repair_orphaned_queries() == 1
        assert db.count_orphaned_queries() == 1
        assert db.repair_orphaned_queries(dry_run=False) == 1
        assert db.count_orphaned_queries() == 0
        with db._connection() as conn:
            backup_tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'queries_orphan_backup_%'"
            ).fetchall()
            assert len(backup_tables) == 1


class TestStoreAndRetrieve:
    def test_store_result_returns_id(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        result = create_test_result()
        result_id = db.store_result(result)
        assert isinstance(result_id, int)
        assert result_id > 0

    def test_store_and_get_result(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        result = create_test_result(
            execution_id="test-123",
            platform="DuckDB",
            benchmark="TPC-H",
            scale_factor=10.0,
            geometric_mean_ms=150.5,
            power_at_size=2000.0,
        )

        db.store_result(result)
        stored = db.get_result("test-123")

        assert stored is not None
        assert stored.execution_id == "test-123"
        assert stored.platform == "DuckDB"
        assert stored.benchmark == "TPC-H"
        assert stored.scale_factor == 10.0
        assert stored.geometric_mean_ms == pytest.approx(150.5, rel=1e-3)
        assert stored.power_at_size == pytest.approx(2000.0, rel=1e-3)
        assert stored.validation_status == "PASSED"

    def test_get_result_not_found(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        result = db.get_result("nonexistent")
        assert result is None

    def test_get_result_by_id(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        result = create_test_result(execution_id="test-by-id")
        result_id = db.store_result(result)

        stored = db.get_result_by_id(result_id)
        assert stored is not None
        assert stored.execution_id == "test-by-id"

    def test_store_result_with_queries(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        result = create_test_result(execution_id="with-queries", include_queries=True)
        result_id = db.store_result(result)

        queries = db.get_queries(result_id)
        assert len(queries) == 2
        assert queries[0].query_id == "1"
        assert queries[0].execution_time_ms == 50
        assert queries[1].query_id == "2"
        assert queries[1].execution_time_ms == 75

    def test_duplicate_execution_id_raises(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        result1 = create_test_result(execution_id="duplicate")
        result2 = create_test_result(execution_id="duplicate")

        db.store_result(result1)
        with pytest.raises(ValueError, match="already exists"):
            db.store_result(result2)

    def test_store_result_with_cost(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        result = create_test_result(execution_id="with-cost", total_cost=5.50)
        db.store_result(result)

        stored = db.get_result("with-cost")
        assert stored.total_cost == pytest.approx(5.50, rel=1e-3)

    def test_stored_result_from_row(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        ts = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = create_test_result(execution_id="row-test", timestamp=ts)
        db.store_result(result)

        stored = db.get_result("row-test")
        assert isinstance(stored.timestamp, datetime)
        assert stored.timestamp.year == 2025
        assert stored.timestamp.month == 1
        assert stored.timestamp.day == 15


class TestQueryResults:
    def test_query_results_no_filters(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        for i in range(5):
            db.store_result(create_test_result(execution_id=f"test-{i}"))

        results = db.query_results()
        assert len(results) == 5

    def test_query_results_by_platform(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        db.store_result(create_test_result(execution_id="duck1", platform="DuckDB"))
        db.store_result(create_test_result(execution_id="duck2", platform="DuckDB"))
        db.store_result(create_test_result(execution_id="snow1", platform="Snowflake"))

        results = db.query_results(platform="DuckDB")
        assert len(results) == 2
        assert all(r.platform == "DuckDB" for r in results)

    def test_query_results_by_benchmark(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        db.store_result(create_test_result(execution_id="h1", benchmark="TPC-H"))
        db.store_result(create_test_result(execution_id="ds1", benchmark="TPC-DS"))

        results = db.query_results(benchmark="TPC-H")
        assert len(results) == 1
        assert results[0].benchmark == "TPC-H"

    def test_query_results_by_scale_factor(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        db.store_result(create_test_result(execution_id="sf1", scale_factor=1.0))
        db.store_result(create_test_result(execution_id="sf10", scale_factor=10.0))
        db.store_result(create_test_result(execution_id="sf100", scale_factor=100.0))

        results = db.query_results(scale_factor=10.0)
        assert len(results) == 1
        assert results[0].scale_factor == 10.0

    def test_query_results_by_date_range(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(create_test_result(execution_id="old", timestamp=now - timedelta(days=30)))
        db.store_result(create_test_result(execution_id="recent", timestamp=now - timedelta(days=5)))
        db.store_result(create_test_result(execution_id="new", timestamp=now))

        results = db.query_results(start_date=now - timedelta(days=10))
        assert len(results) == 2

    def test_query_results_by_validation_status(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        db.store_result(create_test_result(execution_id="pass1", validation_status="PASSED"))
        db.store_result(create_test_result(execution_id="fail1", validation_status="FAILED"))

        passed = db.query_results(validation_status="PASSED")
        assert len(passed) == 1
        assert passed[0].validation_status == "PASSED"

    def test_query_results_limit_offset(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        for i in range(10):
            db.store_result(create_test_result(execution_id=f"test-{i:02d}"))

        page1 = db.query_results(limit=3, offset=0)
        page2 = db.query_results(limit=3, offset=3)

        assert len(page1) == 3
        assert len(page2) == 3
        page1_ids = {r.execution_id for r in page1}
        page2_ids = {r.execution_id for r in page2}
        assert page1_ids.isdisjoint(page2_ids)

    def test_count_results(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        db.store_result(create_test_result(execution_id="c1", platform="DuckDB"))
        db.store_result(create_test_result(execution_id="c2", platform="DuckDB"))
        db.store_result(create_test_result(execution_id="c3", platform="Snowflake"))

        assert db.count_results() == 3
        assert db.count_results(platform="DuckDB") == 2
        assert db.count_results(benchmark="TPC-H") == 3

    def test_get_platforms(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        db.store_result(create_test_result(execution_id="p1", platform="DuckDB"))
        db.store_result(create_test_result(execution_id="p2", platform="Snowflake"))
        db.store_result(create_test_result(execution_id="p3", platform="DuckDB"))

        platforms = db.get_platforms()
        assert sorted(platforms) == ["DuckDB", "Snowflake"]

    def test_get_benchmarks(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        db.store_result(create_test_result(execution_id="b1", benchmark="TPC-H"))
        db.store_result(create_test_result(execution_id="b2", benchmark="TPC-DS"))

        benchmarks = db.get_benchmarks()
        assert sorted(benchmarks) == ["TPC-DS", "TPC-H"]


class TestDeleteResult:
    def test_delete_result(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        db.store_result(create_test_result(execution_id="to-delete"))

        assert db.get_result("to-delete") is not None
        deleted = db.delete_result("to-delete")
        assert deleted is True
        assert db.get_result("to-delete") is None

    def test_delete_nonexistent_result(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        deleted = db.delete_result("nonexistent")
        assert deleted is False


class TestRankings:
    def test_calculate_rankings_geometric_mean(self, tmp_path):
        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="duck1",
                platform="DuckDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                geometric_mean_ms=100.0,
                timestamp=now,
            )
        )
        db.store_result(
            create_test_result(
                execution_id="snow1",
                platform="Snowflake",
                benchmark="TPC-H",
                scale_factor=1.0,
                geometric_mean_ms=200.0,
                timestamp=now,
            )
        )

        config = RankingConfig(metric="geometric_mean", lookback_days=90)
        rankings = db.calculate_rankings("TPC-H", 1.0, config)

        assert len(rankings) == 2
        assert rankings[0].platform == "DuckDB"
        assert rankings[0].rank == 1
        assert rankings[1].platform == "Snowflake"
        assert rankings[1].rank == 2

    def test_calculate_rankings_power_at_size(self, tmp_path):
        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="duck1",
                platform="DuckDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                power_at_size=1000.0,
                timestamp=now,
            )
        )
        db.store_result(
            create_test_result(
                execution_id="snow1",
                platform="Snowflake",
                benchmark="TPC-H",
                scale_factor=1.0,
                power_at_size=2000.0,
                timestamp=now,
            )
        )

        config = RankingConfig(metric="power_at_size", lookback_days=90)
        rankings = db.calculate_rankings("TPC-H", 1.0, config)

        assert rankings[0].platform == "Snowflake"
        assert rankings[1].platform == "DuckDB"

    def test_calculate_rankings_min_samples(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        for i in range(3):
            db.store_result(
                create_test_result(
                    execution_id=f"duck{i}",
                    platform="DuckDB",
                    benchmark="TPC-H",
                    scale_factor=1.0,
                    timestamp=now - timedelta(days=i),
                )
            )

        db.store_result(
            create_test_result(
                execution_id="snow1",
                platform="Snowflake",
                benchmark="TPC-H",
                scale_factor=1.0,
                timestamp=now,
            )
        )

        config = RankingConfig(min_samples=2, lookback_days=90)
        rankings = db.calculate_rankings("TPC-H", 1.0, config)

        assert len(rankings) == 1
        assert rankings[0].platform == "DuckDB"
        assert rankings[0].sample_count == 3

    def test_calculate_rankings_lookback_days(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="recent",
                platform="DuckDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                timestamp=now - timedelta(days=5),
            )
        )

        db.store_result(
            create_test_result(
                execution_id="old",
                platform="Snowflake",
                benchmark="TPC-H",
                scale_factor=1.0,
                timestamp=now - timedelta(days=100),
            )
        )

        config = RankingConfig(lookback_days=30)
        rankings = db.calculate_rankings("TPC-H", 1.0, config)

        assert len(rankings) == 1
        assert rankings[0].platform == "DuckDB"

    def test_calculate_rankings_trend_detection(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="duck-current",
                platform="DuckDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                geometric_mean_ms=80.0,
                timestamp=now - timedelta(days=5),
            )
        )

        db.store_result(
            create_test_result(
                execution_id="duck-prev",
                platform="DuckDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                geometric_mean_ms=100.0,
                timestamp=now - timedelta(days=95),
            )
        )

        config = RankingConfig(lookback_days=90)
        rankings = db.calculate_rankings("TPC-H", 1.0, config)

        assert len(rankings) == 1
        assert rankings[0].trend == "up"
        assert rankings[0].trend_change is not None
        assert rankings[0].trend_change < 0

    def test_calculate_rankings_new_platform(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="new-platform",
                platform="NewDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                timestamp=now - timedelta(days=5),
            )
        )

        config = RankingConfig(lookback_days=90)
        rankings = db.calculate_rankings("TPC-H", 1.0, config)

        assert len(rankings) == 1
        assert rankings[0].trend == "new"
        assert rankings[0].trend_change is None


class TestRankingsComplianceFilter:
    def test_unofficial_subscale_excluded_by_default(self, tmp_path):
        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="official-run",
                platform="OfficialDB",
                benchmark="TPC-DS",
                scale_factor=1.0,
                timestamp=now - timedelta(days=5),
                compliance_class="official",
            )
        )
        db.store_result(
            create_test_result(
                execution_id="unofficial-run",
                platform="UnofficialDB",
                benchmark="TPC-DS",
                scale_factor=1.0,
                timestamp=now - timedelta(days=5),
                compliance_class="unofficial_subscale",
            )
        )

        config = RankingConfig(lookback_days=90)
        rankings = db.calculate_rankings("TPC-DS", 1.0, config)

        platform_names = [r.platform for r in rankings]
        assert "OfficialDB" in platform_names
        assert "UnofficialDB" not in platform_names

    def test_unofficial_excluded_with_include_unofficial_false(self, tmp_path):
        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="subscale-run",
                platform="SubscaleDB",
                benchmark="TPC-DS",
                scale_factor=1.0,
                timestamp=now - timedelta(days=5),
                compliance_class="unofficial_subscale",
            )
        )

        config = RankingConfig(lookback_days=90)
        rankings = db.calculate_rankings("TPC-DS", 1.0, config, include_unofficial=False)
        assert len(rankings) == 0

    def test_unofficial_included_with_include_unofficial_true(self, tmp_path):
        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="subscale-run2",
                platform="SubscaleDB2",
                benchmark="TPC-DS",
                scale_factor=1.0,
                timestamp=now - timedelta(days=5),
                compliance_class="unofficial_subscale",
            )
        )

        config = RankingConfig(lookback_days=90)
        rankings = db.calculate_rankings("TPC-DS", 1.0, config, include_unofficial=True)
        platform_names = [r.platform for r in rankings]
        assert "SubscaleDB2" in platform_names

    def test_legacy_no_compliance_class_included_by_default(self, tmp_path):
        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="legacy-run",
                platform="LegacyDB",
                benchmark="TPC-DS",
                scale_factor=1.0,
                timestamp=now - timedelta(days=5),
                compliance_class=None,
            )
        )

        config = RankingConfig(lookback_days=90)
        rankings = db.calculate_rankings("TPC-DS", 1.0, config)
        platform_names = [r.platform for r in rankings]
        assert "LegacyDB" in platform_names


class TestPerformanceTrends:
    def test_get_performance_trends(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        for i in range(3):
            db.store_result(
                create_test_result(
                    execution_id=f"trend-{i}",
                    platform="DuckDB",
                    benchmark="TPC-H",
                    scale_factor=1.0,
                    geometric_mean_ms=100.0 + i * 10,
                    timestamp=now - timedelta(days=i * 30 + 5),
                )
            )

        trends = db.get_performance_trends("DuckDB", "TPC-H", 1.0, periods=3, period_days=30)

        assert len(trends) > 0
        for trend in trends:
            assert trend.platform == "DuckDB"
            assert trend.benchmark == "TPC-H"
            assert trend.scale_factor == 1.0

    def test_performance_trends_empty_periods(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="single",
                platform="DuckDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                timestamp=now - timedelta(days=5),
            )
        )

        trends = db.get_performance_trends("DuckDB", "TPC-H", 1.0, periods=6, period_days=30)

        assert len(trends) <= 1


class TestRegressionDetection:
    def test_detect_regressions(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="regress-current",
                platform="DuckDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                geometric_mean_ms=150.0,
                timestamp=now - timedelta(days=5),
            )
        )

        db.store_result(
            create_test_result(
                execution_id="regress-prev",
                platform="DuckDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                geometric_mean_ms=100.0,
                timestamp=now - timedelta(days=35),
            )
        )

        regressions = db.detect_regressions(threshold_pct=10.0, lookback_days=30)

        assert any(r.platform == "DuckDB" and r.benchmark == "TPC-H" and r.is_regression for r in regressions)

    def test_detect_no_regressions(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        now = datetime.now(timezone.utc)

        db.store_result(
            create_test_result(
                execution_id="stable-current",
                platform="DuckDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                geometric_mean_ms=100.0,
                timestamp=now - timedelta(days=5),
            )
        )

        db.store_result(
            create_test_result(
                execution_id="stable-prev",
                platform="DuckDB",
                benchmark="TPC-H",
                scale_factor=1.0,
                geometric_mean_ms=102.0,
                timestamp=now - timedelta(days=35),
            )
        )

        regressions = db.detect_regressions(threshold_pct=10.0, lookback_days=30)

        regressed_platforms = [r for r in regressions if r.change_pct and r.change_pct > 10.0]
        assert len(regressed_platforms) == 0


class TestSummaryStats:
    def test_get_summary_stats(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")

        db.store_result(
            create_test_result(
                execution_id="sum1",
                platform="DuckDB",
                benchmark="TPC-H",
                include_queries=True,
            )
        )
        db.store_result(
            create_test_result(
                execution_id="sum2",
                platform="Snowflake",
                benchmark="TPC-DS",
                include_queries=True,
            )
        )

        stats = db.get_summary_stats()

        assert stats["total_results"] == 2
        assert stats["total_queries"] == 4
        assert stats["unique_platforms"] == 2
        assert stats["unique_benchmarks"] == 2
        assert stats["schema_version"] == SCHEMA_VERSION
        assert "database_path" in stats

    def test_get_summary_stats_empty_db(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        stats = db.get_summary_stats()

        assert stats["total_results"] == 0
        assert stats["total_queries"] == 0
        assert stats["unique_platforms"] == 0


class TestPlatformMetadataCredentialBoundary:
    def test_store_result_filters_all_platform_metadata_sources(self, tmp_path):
        gates = (
            "RAW_CONFIG_GATE",
            "RAW_METADATA_GATE",
            "DEPLOYMENT_GATE",
            "CLOUD_GATE",
            "COMPUTE_GATE",
            "STORAGE_GATE",
        )
        result = create_test_result(execution_id="platform-meta-db")
        result.platform_raw_config = {"password": "RAW_CONFIG_GATE", "threads": 4, "sort_key": "o_orderkey"}
        result.platform_raw_metadata = {"password": "RAW_METADATA_GATE", "partition_key": "l_orderkey"}
        result.platform_deployment = {"token": "DEPLOYMENT_GATE", "connection_mode": "embedded"}
        result.platform_cloud = {"access_key": "CLOUD_GATE", "region": "us-east-1"}
        result.platform_compute = {"connection_string": "COMPUTE_GATE", "warehouse": "BENCH_WH"}
        result.platform_storage = {"secret": "STORAGE_GATE", "bucket": "bench-bucket"}

        db = ResultDatabase(tmp_path / "test.db")
        db.store_result(result)
        raw = (tmp_path / "test.db").read_bytes()
        for gate in gates:
            assert gate.encode() not in raw, f"results.db leaked {gate}"

        stored = db.get_result("platform-meta-db")
        assert stored is not None
        platform = stored.metadata["platform"]
        assert platform["raw_config"]["password"] == "<redacted>"
        assert platform["raw_config"]["threads"] == 4
        assert platform["raw_config"]["sort_key"] == "o_orderkey"
        assert platform["raw_metadata"]["password"] == "<redacted>"
        assert platform["raw_metadata"]["partition_key"] == "l_orderkey"
        assert platform["deployment"]["token"] == "<redacted>"
        assert platform["cloud"]["access_key"] == "<redacted>"
        assert platform["compute"]["connection_string"] == "<redacted>"
        assert platform["compute"]["warehouse"] == "BENCH_WH"
        assert platform["storage"]["secret"] == "<redacted>"
        assert platform["storage"]["bucket"] == "bench-bucket"


class TestImportResults:
    def test_import_results_from_directory(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        results_dir = tmp_path / "results"
        results_dir.mkdir()

        result_data = make_v2_result_dict(
            version="2.0",
            benchmark_id="tpc_h",
            benchmark_name="TPC-H Benchmark",
            platform="DuckDB",
            execution_id="import-test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=800,
            scale_factor=1.0,
            queries=[{"id": "1", "ms": 50.0, "rows": 4}],
        )

        with open(results_dir / "result1.json", "w", encoding="utf-8") as f:
            json.dump(result_data, f)

        imported, skipped, excluded = db.import_results_from_directory(results_dir)

        assert imported == 1
        assert skipped == 0

        stored = db.get_result("import-test")
        assert stored is not None
        assert stored.platform == "DuckDB"

    def test_import_skips_duplicates(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        results_dir = tmp_path / "results"
        results_dir.mkdir()

        result_data = make_v2_result_dict(
            version="2.0",
            benchmark_id="tpc_h",
            benchmark_name="TPC-H",
            platform="DuckDB",
            execution_id="duplicate-test",
            timestamp="2025-01-01T10:00:00",
            query_time_ms=800,
            scale_factor=1.0,
            total_ms=800,
            queries=[{"id": "1", "ms": 50.0, "rows": 4}],
        )

        with open(results_dir / "result1.json", "w", encoding="utf-8") as f:
            json.dump(result_data, f)

        imported1, skipped1, excluded1 = db.import_results_from_directory(results_dir)
        imported2, skipped2, excluded2 = db.import_results_from_directory(results_dir)

        assert imported1 == 1
        assert skipped1 == 0
        assert imported2 == 0
        assert skipped2 == 1

    def test_import_skips_invalid_files(self, tmp_path):

        db = ResultDatabase(tmp_path / "test.db")
        results_dir = tmp_path / "results"
        results_dir.mkdir()

        with open(results_dir / "invalid.json", "w", encoding="utf-8") as f:
            f.write("{ invalid json")

        imported, skipped, excluded = db.import_results_from_directory(results_dir)

        assert imported == 0
        assert skipped == 1


class TestDataclasses:
    def test_stored_result_dataclass(self):

        result = StoredResult(
            id=1,
            execution_id="test",
            platform="DuckDB",
            platform_version="1.0.0",
            benchmark="TPC-H",
            scale_factor=1.0,
            timestamp=datetime.now(timezone.utc),
            duration_seconds=10.0,
            total_queries=22,
            successful_queries=22,
            failed_queries=0,
            geometric_mean_ms=100.0,
            power_at_size=1000.0,
            throughput_at_size=None,
            qph_at_size=None,
            total_cost=None,
            validation_status="PASSED",
            config_hash="abc123",
            metadata={},
        )

        assert result.execution_id == "test"
        assert result.platform == "DuckDB"

    def test_stored_query_dataclass(self):

        query = StoredQuery(
            id=1,
            result_id=1,
            query_id="1",
            stream_id="power",
            execution_order=1,
            execution_time_ms=50.0,
            status="SUCCESS",
            rows_returned=10,
            cost=0.01,
            iteration=1,
        )

        assert query.query_id == "1"
        assert query.execution_time_ms == 50.0

    def test_platform_ranking_dataclass(self):

        ranking = PlatformRanking(
            rank=1,
            platform="DuckDB",
            platform_version="1.0.0",
            benchmark="TPC-H",
            scale_factor=1.0,
            score=100.0,
            sample_count=5,
            latest_timestamp=datetime.now(timezone.utc),
            trend="up",
            trend_change=-5.0,
        )

        assert ranking.rank == 1
        assert ranking.platform == "DuckDB"
        assert ranking.trend == "up"

    def test_performance_trend_dataclass(self):

        now = datetime.now(timezone.utc)
        trend = PerformanceTrend(
            platform="DuckDB",
            benchmark="TPC-H",
            scale_factor=1.0,
            period_start=now - timedelta(days=30),
            period_end=now,
            avg_geometric_mean_ms=100.0,
            min_geometric_mean_ms=90.0,
            max_geometric_mean_ms=110.0,
            sample_count=10,
            change_pct=-5.0,
            is_regression=False,
        )

        assert trend.platform == "DuckDB"
        assert trend.is_regression is False

    def test_ranking_config_defaults(self):

        config = RankingConfig()

        assert config.metric == "geometric_mean"
        assert config.min_samples == 1
        assert config.lookback_days == 90
        assert config.require_success is True
