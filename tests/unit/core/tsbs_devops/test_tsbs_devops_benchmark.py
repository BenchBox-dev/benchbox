# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path

import pytest

from benchbox.core.tsbs_devops.benchmark import TSBSDevOpsBenchmark
from benchbox.core.tsbs_devops.schema import TSBS_DEVOPS_SCHEMA

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestBenchmarkInitialization:
    def test_default_init(self):
        benchmark = TSBSDevOpsBenchmark()
        assert benchmark.scale_factor == 1.0
        assert benchmark.num_hosts == 100
        assert benchmark.duration_days == 2
        assert benchmark.interval_seconds == 10

    def test_custom_scale_factor(self):
        benchmark = TSBSDevOpsBenchmark(scale_factor=0.5)
        assert benchmark.scale_factor == 0.5
        assert benchmark.num_hosts == 50

    def test_invalid_scale_factor(self):
        with pytest.raises(ValueError, match="must be positive"):
            TSBSDevOpsBenchmark(scale_factor=0)

        with pytest.raises(ValueError, match="must be positive"):
            TSBSDevOpsBenchmark(scale_factor=-1.0)

    def test_custom_num_hosts(self):
        benchmark = TSBSDevOpsBenchmark(num_hosts=50)
        assert benchmark.num_hosts == 50

    def test_custom_duration(self):
        benchmark = TSBSDevOpsBenchmark(duration_days=7)
        assert benchmark.duration_days == 7

    def test_custom_interval(self):
        benchmark = TSBSDevOpsBenchmark(interval_seconds=60)
        assert benchmark.interval_seconds == 60

    def test_custom_output_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            benchmark = TSBSDevOpsBenchmark(output_dir=tmpdir)
            assert benchmark.output_dir == Path(tmpdir)

    def test_seed_for_reproducibility(self, seed):
        benchmark = TSBSDevOpsBenchmark(seed=seed)
        assert benchmark.seed == seed


class TestGenerateData:
    @pytest.fixture
    def tsbs_benchmark(self, seed, start_time):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield TSBSDevOpsBenchmark(
                num_hosts=3,
                duration_days=1,
                interval_seconds=3600,
                output_dir=tmpdir,
                start_time=start_time,
                seed=seed,
            )

    def test_generate_returns_paths(self, tsbs_benchmark):
        paths = tsbs_benchmark.generate_data()
        assert isinstance(paths, list)
        assert len(paths) == 5

    def test_generate_creates_files(self, tsbs_benchmark):
        paths = tsbs_benchmark.generate_data()
        for path in paths:
            assert Path(path).exists()

    def test_tables_populated_after_generate(self, tsbs_benchmark):
        tsbs_benchmark.generate_data()
        assert len(tsbs_benchmark.tables) == 5
        assert "cpu" in tsbs_benchmark.tables
        assert "mem" in tsbs_benchmark.tables


class TestGetQueries:
    @pytest.fixture
    def tsbs_benchmark(self, seed, start_time):
        return TSBSDevOpsBenchmark(
            num_hosts=100,
            start_time=start_time,
            seed=seed,
        )

    def test_get_queries_returns_dict(self, tsbs_benchmark):
        queries = tsbs_benchmark.get_queries()
        assert isinstance(queries, dict)
        assert len(queries) == 18

    def test_get_queries_accepts_dialect(self, tsbs_benchmark):
        queries = tsbs_benchmark.get_queries(dialect="duckdb")
        assert isinstance(queries, dict)

    @pytest.mark.parametrize("dialect", ["snowflake", "bigquery", "databricks"])
    def test_get_queries_translates_for_cloud_dialects(self, seed, start_time, dialect):
        queries = TSBSDevOpsBenchmark(num_hosts=100, start_time=start_time, seed=seed).get_queries(dialect=dialect)

        assert len(queries) == 18
        if dialect == "bigquery":
            for sql in queries.values():
                assert "DATE_TRUNC('minute'" not in sql
            assert "TIMESTAMP_TRUNC(`time`, MINUTE)" in queries["double-groupby-1-hr"]

    @pytest.mark.parametrize("dialect", [None, "duckdb"])
    def test_get_queries_keeps_duckdb_source(self, seed, start_time, dialect):
        source = TSBSDevOpsBenchmark(num_hosts=100, start_time=start_time, seed=seed).get_queries()
        queries = TSBSDevOpsBenchmark(num_hosts=100, start_time=start_time, seed=seed).get_queries(dialect=dialect)

        assert queries == source

    def test_get_query_translates_for_dialect(self, tsbs_benchmark):
        sql = tsbs_benchmark.get_query("double-groupby-1-hr", dialect="bigquery")

        assert "TIMESTAMP_TRUNC(`time`, MINUTE)" in sql

    def test_get_query_by_id(self, tsbs_benchmark):
        query = tsbs_benchmark.get_query("single-host-12-hr")
        assert isinstance(query, str)
        assert "SELECT" in query
        assert "cpu" in query

    def test_get_query_with_params(self, tsbs_benchmark):
        query = tsbs_benchmark.get_query("single-host-12-hr", params={"hostname": "test_host"})
        assert "test_host" in query

    def test_get_query_unknown_raises(self, tsbs_benchmark):
        with pytest.raises(ValueError):
            tsbs_benchmark.get_query("nonexistent")


class TestGetSchema:
    def test_get_schema_returns_dict(self):
        benchmark = TSBSDevOpsBenchmark()
        schema = benchmark.get_schema()
        assert schema == TSBS_DEVOPS_SCHEMA

    def test_get_schema_has_all_tables(self):
        benchmark = TSBSDevOpsBenchmark()
        schema = benchmark.get_schema()
        assert set(schema.keys()) == {"tags", "cpu", "mem", "disk", "net"}


class TestGetCreateTablesSql:
    def test_generates_standard_sql(self):
        benchmark = TSBSDevOpsBenchmark()
        sql = benchmark.get_create_tables_sql(dialect="standard")
        assert "CREATE TABLE" in sql
        assert "tags" in sql
        assert "cpu" in sql

    def test_generates_duckdb_sql(self):
        benchmark = TSBSDevOpsBenchmark()
        sql = benchmark.get_create_tables_sql(dialect="duckdb")
        assert "CREATE TABLE" in sql

    def test_generates_clickhouse_sql(self):
        benchmark = TSBSDevOpsBenchmark()
        sql = benchmark.get_create_tables_sql(dialect="clickhouse")
        assert "ENGINE = MergeTree()" in sql

    def test_generates_timescale_sql(self):
        benchmark = TSBSDevOpsBenchmark()
        sql = benchmark.get_create_tables_sql(dialect="timescale")
        assert "create_hypertable" in sql


class TestGetBenchmarkInfo:
    def test_info_has_required_fields(self):
        benchmark = TSBSDevOpsBenchmark()
        info = benchmark.get_benchmark_info()

        assert info["name"] == "TSBS DevOps"
        assert "description" in info
        assert "reference" in info
        assert "version" in info

    def test_info_has_scale_info(self):
        benchmark = TSBSDevOpsBenchmark(scale_factor=2.0, num_hosts=50, duration_days=3)
        info = benchmark.get_benchmark_info()

        assert info["scale_factor"] == 2.0
        assert info["num_hosts"] == 50
        assert info["duration_days"] == 3

    def test_info_has_query_count(self):
        benchmark = TSBSDevOpsBenchmark()
        info = benchmark.get_benchmark_info()

        assert info["num_queries"] == 18
        assert "query_categories" in info
        assert len(info["query_categories"]) > 0

    def test_info_has_tables_list(self):
        benchmark = TSBSDevOpsBenchmark()
        info = benchmark.get_benchmark_info()

        assert info["tables"] == ["tags", "cpu", "mem", "disk", "net"]

    def test_info_has_metric_descriptions(self):
        benchmark = TSBSDevOpsBenchmark()
        info = benchmark.get_benchmark_info()

        assert "metrics" in info
        assert "cpu" in info["metrics"]
        assert "mem" in info["metrics"]


class TestQueryInfo:
    def test_get_query_info(self):
        benchmark = TSBSDevOpsBenchmark()
        info = benchmark.get_query_info("single-host-12-hr")

        assert info["id"] == "1"
        assert info["category"] == "single-host"
        assert "description" in info

    def test_get_queries_by_category(self):
        benchmark = TSBSDevOpsBenchmark()
        threshold_queries = benchmark.get_queries_by_category("threshold")

        assert len(threshold_queries) > 0
        for qid in threshold_queries:
            info = benchmark.get_query_info(qid)
            assert info["category"] == "threshold"


class TestGenerationStats:
    def test_get_generation_stats(self):
        benchmark = TSBSDevOpsBenchmark(
            num_hosts=10,
            duration_days=1,
            interval_seconds=3600,
        )
        stats = benchmark.get_generation_stats()

        assert stats["num_hosts"] == 10
        assert stats["duration_days"] == 1
        assert stats["interval_seconds"] == 3600
        assert "rows" in stats
        assert "total_rows" in stats


class TestTopLevelInterface:
    def test_import_from_benchbox(self):
        from benchbox import TSBSDevOps

        assert callable(TSBSDevOps)

    def test_tsbs_devops_interface(self, seed, start_time):
        from benchbox import TSBSDevOps

        with tempfile.TemporaryDirectory() as tmpdir:
            benchmark = TSBSDevOps(
                scale_factor=0.1,
                num_hosts=5,
                output_dir=tmpdir,
                start_time=start_time,
                seed=seed,
            )

            assert benchmark.num_hosts == 5
            assert benchmark.get_benchmark_info()["name"] == "TSBS DevOps"

            queries = benchmark.get_queries()
            assert len(queries) == 18

    def test_tsbs_devops_data_generation(self, seed, start_time):
        from benchbox import TSBSDevOps

        with tempfile.TemporaryDirectory() as tmpdir:
            benchmark = TSBSDevOps(
                num_hosts=3,
                duration_days=1,
                interval_seconds=3600,
                output_dir=tmpdir,
                start_time=start_time,
                seed=seed,
            )

            paths = benchmark.generate_data()
            assert len(paths) == 5

            assert "cpu" in benchmark.tables
            assert "mem" in benchmark.tables


class TestBenchmarkLoader:
    def test_loader_can_load_tsbs_devops(self):
        from benchbox.core.benchmark_loader import get_benchmark_class

        cls = get_benchmark_class("tsbs_devops")
        assert cls.__name__ == "TSBSDevOpsBenchmark"

    def test_loader_creates_instance(self):
        from benchbox.core.benchmark_loader import get_benchmark_instance
        from benchbox.core.schemas import BenchmarkConfig

        config = BenchmarkConfig(
            name="tsbs_devops",
            display_name="TSBS DevOps",
            scale_factor=0.1,
        )
        instance = get_benchmark_instance(config, system_profile=None)

        assert instance is not None
        assert hasattr(instance, "generate_data")
        assert hasattr(instance, "get_queries")
        assert hasattr(instance, "create_enhanced_benchmark_result")
        assert hasattr(instance, "create_minimal_benchmark_result")
        assert hasattr(instance, "validate_preflight")
        assert hasattr(instance, "validate_manifest")
        assert hasattr(instance, "validate_loaded_data")


