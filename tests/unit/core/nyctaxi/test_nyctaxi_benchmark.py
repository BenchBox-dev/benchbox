# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path

import pytest

from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark
from benchbox.core.nyctaxi.schema import NYC_TAXI_SCHEMA, TaxiType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestBenchmarkInitialization:
    def test_default_init(self):
        bm = NYCTaxiBenchmark()
        assert bm.scale_factor == 1.0
        assert bm.year == 2019
        assert bm.months is None

    def test_custom_scale_factor(self):
        bm = NYCTaxiBenchmark(scale_factor=0.5)
        assert bm.scale_factor == 0.5

    def test_invalid_scale_factor(self):
        with pytest.raises(ValueError, match="must be positive"):
            NYCTaxiBenchmark(scale_factor=0)

        with pytest.raises(ValueError, match="must be positive"):
            NYCTaxiBenchmark(scale_factor=-1.0)

    def test_custom_year(self):
        bm = NYCTaxiBenchmark(year=2020)
        assert bm.year == 2020

    def test_custom_months(self):
        bm = NYCTaxiBenchmark(months=[1, 2, 3])
        assert bm.months == [1, 2, 3]

    def test_custom_output_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bm = NYCTaxiBenchmark(output_dir=tmpdir)
            assert bm.output_dir == Path(tmpdir)

    def test_seed_for_reproducibility(self, seed):
        bm = NYCTaxiBenchmark(seed=seed)
        assert bm.seed == seed


class TestGenerateData:
    @pytest.fixture(scope="class")
    def nyc_benchmark_with_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            benchmark = NYCTaxiBenchmark(
                scale_factor=0.01,
                output_dir=tmpdir,
                year=2019,
                months=[1],
                seed=42,
            )
            paths = benchmark.generate_data()
            yield benchmark, paths

    def test_generate_returns_paths(self, nyc_benchmark_with_data):
        benchmark, paths = nyc_benchmark_with_data
        assert isinstance(paths, list)
        assert len(paths) == 2

    def test_generate_creates_files(self, nyc_benchmark_with_data):
        benchmark, paths = nyc_benchmark_with_data
        for path in paths:
            assert Path(path).exists()

    def test_tables_populated_after_generate(self, nyc_benchmark_with_data):
        benchmark, paths = nyc_benchmark_with_data
        assert len(benchmark.tables) == 2
        assert "taxi_zones" in benchmark.tables
        assert "trips" in benchmark.tables


class TestGetQueries:
    @pytest.fixture
    def nyc_benchmark(self, seed):
        return NYCTaxiBenchmark(
            scale_factor=0.01,
            year=2019,
            seed=seed,
        )

    def test_get_queries_returns_dict(self, nyc_benchmark):
        queries = nyc_benchmark.get_queries()
        assert isinstance(queries, dict)
        assert len(queries) == 25

    def test_get_queries_accepts_dialect(self, nyc_benchmark):
        queries = nyc_benchmark.get_queries(dialect="duckdb")
        assert isinstance(queries, dict)

    def test_get_query_by_id(self, nyc_benchmark):
        query = nyc_benchmark.get_query("trips-per-hour")
        assert isinstance(query, str)
        assert "SELECT" in query
        assert "trips" in query

    def test_get_query_with_params(self, nyc_benchmark):
        query = nyc_benchmark.get_query("zone-detail", params={"zone_id": 161})
        assert "161" in query

    def test_get_query_unknown_raises(self, nyc_benchmark):
        with pytest.raises(ValueError):
            nyc_benchmark.get_query("nonexistent")


class TestGetSchema:
    def test_get_schema_returns_dict(self):
        bm = NYCTaxiBenchmark()
        schema = bm.get_schema()
        assert isinstance(schema, dict)
        assert set(schema.keys()) == {"trips", "taxi_zones"}

    def test_get_schema_has_all_tables(self):
        bm = NYCTaxiBenchmark(taxi_types=[TaxiType.YELLOW, TaxiType.GREEN, TaxiType.HVFHV, TaxiType.FHV])
        schema = bm.get_schema()
        required = {"trips", "taxi_zones", "green_trips", "hvfhv_trips", "fhv_trips"}
        assert required.issubset(set(schema.keys()))


class TestGetCreateTablesSql:
    def test_generates_standard_sql(self):
        bm = NYCTaxiBenchmark()
        sql = bm.get_create_tables_sql(dialect="standard")
        assert "CREATE TABLE" in sql
        assert "trips" in sql
        assert "taxi_zones" in sql

    def test_generates_duckdb_sql(self):
        bm = NYCTaxiBenchmark()
        sql = bm.get_create_tables_sql(dialect="duckdb")
        assert "CREATE TABLE" in sql

    def test_generates_clickhouse_sql(self):
        bm = NYCTaxiBenchmark()
        sql = bm.get_create_tables_sql(dialect="clickhouse")
        assert "ENGINE = MergeTree()" in sql

    def test_generates_postgres_sql(self):
        bm = NYCTaxiBenchmark()
        sql = bm.get_create_tables_sql(dialect="postgres")
        assert "TIMESTAMPTZ" in sql


class TestGetBenchmarkInfo:
    def test_info_has_required_fields(self):
        bm = NYCTaxiBenchmark()
        info = bm.get_benchmark_info()

        assert info["name"] == "NYC Taxi OLAP"
        assert "description" in info
        assert "reference" in info
        assert "version" in info

    def test_info_has_scale_info(self):
        bm = NYCTaxiBenchmark(scale_factor=2.0, year=2020, months=[1, 2, 3])
        info = bm.get_benchmark_info()

        assert info["scale_factor"] == 2.0
        assert info["year"] == 2020
        assert info["months"] == [1, 2, 3]

    def test_info_has_query_count(self):
        bm = NYCTaxiBenchmark()
        info = bm.get_benchmark_info()

        assert info["num_queries"] == 25
        assert "query_categories" in info
        assert len(info["query_categories"]) > 0

    def test_info_has_tables_list(self):
        bm = NYCTaxiBenchmark()
        info = bm.get_benchmark_info()

        assert info["tables"] == ["taxi_zones", "trips"]

    def test_info_indicates_real_data(self):
        bm = NYCTaxiBenchmark()
        info = bm.get_benchmark_info()

        assert info["data_type"] == "real"


class TestQueryInfo:
    def test_get_query_info(self):
        bm = NYCTaxiBenchmark()
        info = bm.get_query_info("trips-per-hour")

        assert info["id"] == "1"
        assert info["category"] == "temporal"
        assert "description" in info

    def test_get_queries_by_category(self):
        bm = NYCTaxiBenchmark()
        temporal_queries = bm.get_queries_by_category("temporal")

        assert len(temporal_queries) > 0
        for qid in temporal_queries:
            info = bm.get_query_info(qid)
            assert info["category"] == "temporal"


class TestDownloadStats:
    def test_get_download_stats(self):
        bm = NYCTaxiBenchmark(
            scale_factor=1.0,
            year=2019,
            months=[1, 2],
        )
        stats = bm.get_download_stats()

        assert stats["scale_factor"] == 1.0
        assert stats["year"] == 2019
        assert stats["months"] == [1, 2]


class TestTopLevelInterface:
    def test_import_from_benchbox(self):
        from benchbox import NYCTaxi

        assert callable(NYCTaxi)

    def test_nyctaxi_interface(self, seed):
        from benchbox import NYCTaxi

        with tempfile.TemporaryDirectory() as tmpdir:
            bm = NYCTaxi(
                scale_factor=0.01,
                year=2019,
                months=[1],
                output_dir=tmpdir,
                seed=seed,
            )

            assert bm.year == 2019
            assert bm.get_benchmark_info()["name"] == "NYC Taxi OLAP"

            queries = bm.get_queries()
            assert len(queries) == 25


class TestBenchmarkLoader:
    def test_loader_can_load_nyctaxi(self):
        from benchbox.core.benchmark_loader import get_benchmark_class

        cls = get_benchmark_class("nyctaxi")
        assert cls.__name__ == "NYCTaxiBenchmark"

    def test_loader_creates_instance(self):
        from benchbox.core.benchmark_loader import get_benchmark_instance
        from benchbox.core.schemas import BenchmarkConfig

        config = BenchmarkConfig(
            name="nyctaxi",
            display_name="NYC Taxi",
            scale_factor=0.01,
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


