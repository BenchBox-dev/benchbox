# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import duckdb
import pytest

from benchbox.core.metadata_primitives import (
    ComplexityBenchmarkResult,
    ConstraintDensity,
    MetadataComplexityConfig,
    MetadataGenerator,
    MetadataPrimitivesBenchmark,
    TypeComplexity,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.fixture
def duckdb_connection():
    conn = duckdb.connect(":memory:")
    yield conn
    conn.close()


@pytest.mark.integration
class TestMetadataGeneratorDuckDB:
    def test_setup_minimal_config(self, duckdb_connection):

        config = MetadataComplexityConfig(
            width_factor=10,
            view_depth=0,
            catalog_size=1,
        )
        generator = MetadataGenerator()

        generated = generator.setup(duckdb_connection, "duckdb", config)

        assert len(generated.tables) > 0
        assert generated.total_objects > 0

        generator.teardown(duckdb_connection, "duckdb", generated)

    def test_setup_wide_table(self, duckdb_connection):

        config = MetadataComplexityConfig(
            width_factor=100,
            view_depth=0,
            catalog_size=1,
        )
        generator = MetadataGenerator()

        generated = generator.setup(duckdb_connection, "duckdb", config)

        result = duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_name = 'benchbox_wide_100'"
        ).fetchone()
        assert result[0] == 100

        generator.teardown(duckdb_connection, "duckdb", generated)

    def test_setup_catalog_tables(self, duckdb_connection):

        config = MetadataComplexityConfig(
            width_factor=10,
            view_depth=0,
            catalog_size=20,
        )
        generator = MetadataGenerator()

        generated = generator.setup(duckdb_connection, "duckdb", config)

        result = duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'benchbox_catalog_%'"
        ).fetchone()
        assert result[0] == 20

        generator.teardown(duckdb_connection, "duckdb", generated)

    def test_setup_view_hierarchy(self, duckdb_connection):

        config = MetadataComplexityConfig(
            width_factor=20,
            view_depth=3,
            catalog_size=1,
        )
        generator = MetadataGenerator()

        generated = generator.setup(duckdb_connection, "duckdb", config)

        assert len(generated.views) == 3
        assert "benchbox_view_d1" in generated.views
        assert "benchbox_view_d2" in generated.views
        assert "benchbox_view_d3" in generated.views

        for view_name in generated.views:
            result = duckdb_connection.execute(f"SELECT * FROM {view_name} LIMIT 1").fetchall()
            assert result is not None

        generator.teardown(duckdb_connection, "duckdb", generated)

    def test_setup_complex_types_basic(self, duckdb_connection):

        config = MetadataComplexityConfig(
            width_factor=20,
            view_depth=0,
            catalog_size=1,
            type_complexity=TypeComplexity.BASIC,
        )
        generator = MetadataGenerator()

        generated = generator.setup(duckdb_connection, "duckdb", config)

        result = duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'benchbox_complex_basic'"
        ).fetchone()
        assert result[0] == 1

        generator.teardown(duckdb_connection, "duckdb", generated)

    def test_setup_complex_types_nested(self, duckdb_connection):

        config = MetadataComplexityConfig(
            width_factor=20,
            view_depth=0,
            catalog_size=1,
            type_complexity=TypeComplexity.NESTED,
        )
        generator = MetadataGenerator()

        generated = generator.setup(duckdb_connection, "duckdb", config)

        result = duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'benchbox_complex_%'"
        ).fetchone()
        assert result[0] == 2

        generator.teardown(duckdb_connection, "duckdb", generated)

    def test_setup_fk_tables(self, duckdb_connection):

        config = MetadataComplexityConfig(
            width_factor=10,
            view_depth=0,
            catalog_size=1,
            constraint_density=ConstraintDensity.SPARSE,
        )
        generator = MetadataGenerator()

        generated = generator.setup(duckdb_connection, "duckdb", config)

        result = duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'benchbox_fk_%'"
        ).fetchone()
        assert result[0] >= 3

        generator.teardown(duckdb_connection, "duckdb", generated)

    def test_teardown_cleans_up(self, duckdb_connection):

        config = MetadataComplexityConfig(
            width_factor=20,
            view_depth=2,
            catalog_size=5,
        )
        generator = MetadataGenerator()

        generated = generator.setup(duckdb_connection, "duckdb", config)

        result_before = duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'benchbox_%'"
        ).fetchone()
        assert result_before[0] > 0

        generator.teardown(duckdb_connection, "duckdb", generated)

        result_after = duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'benchbox_%'"
        ).fetchone()
        assert result_after[0] == 0

    def test_cleanup_all(self, duckdb_connection):

        generator = MetadataGenerator()

        duckdb_connection.execute("CREATE TABLE benchbox_test1 (id INT)")
        duckdb_connection.execute("CREATE TABLE benchbox_test2 (id INT)")
        duckdb_connection.execute("CREATE VIEW benchbox_test_view AS SELECT 1")

        dropped = generator.cleanup_all(duckdb_connection, "duckdb", "benchbox_")
        assert dropped >= 3

        result = duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'benchbox_%'"
        ).fetchone()
        assert result[0] == 0


@pytest.mark.integration
class TestComplexityBenchmarkDuckDB:
    def test_run_complexity_benchmark_minimal(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.run_complexity_benchmark(
            duckdb_connection,
            "duckdb",
            "minimal",
            iterations=1,
        )

        assert isinstance(result, ComplexityBenchmarkResult)
        assert result.setup_time_ms > 0
        assert result.teardown_time_ms > 0
        assert result.benchmark_result is not None
        assert result.benchmark_result.total_queries > 0

    def test_run_complexity_benchmark_wide_tables(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.run_complexity_benchmark(
            duckdb_connection,
            "duckdb",
            "wide_tables",
            iterations=1,
            categories=["wide_table"],
        )

        assert result.benchmark_result is not None
        assert result.benchmark_result.total_queries > 0
        assert "wide_table" in result.benchmark_result.category_summary

    def test_run_complexity_benchmark_deep_views(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.run_complexity_benchmark(
            duckdb_connection,
            "duckdb",
            "deep_views",
            iterations=1,
            categories=["view_hierarchy"],
        )

        assert result.benchmark_result is not None
        assert result.benchmark_result.total_queries > 0
        assert "view_hierarchy" in result.benchmark_result.category_summary

    def test_run_complexity_benchmark_complex_types(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.run_complexity_benchmark(
            duckdb_connection,
            "duckdb",
            "complex_types",
            iterations=1,
            categories=["complex_type"],
        )

        assert result.benchmark_result is not None
        assert result.benchmark_result.total_queries > 0
        assert "complex_type" in result.benchmark_result.category_summary

    def test_run_complexity_benchmark_large_catalog(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.run_complexity_benchmark(
            duckdb_connection,
            "duckdb",
            "large_catalog",
            iterations=1,
            categories=["large_catalog"],
        )

        assert result.benchmark_result is not None
        assert result.benchmark_result.total_queries > 0
        assert "large_catalog" in result.benchmark_result.category_summary

    def test_run_complexity_benchmark_full(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.run_complexity_benchmark(
            duckdb_connection,
            "duckdb",
            "full",
            iterations=1,
        )

        assert result.benchmark_result is not None
        assert result.benchmark_result.total_queries > 0
        assert len(result.benchmark_result.category_summary) > 1

    def test_setup_teardown_lifecycle(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        generated = benchmark.setup_complexity(duckdb_connection, "duckdb", "baseline")
        assert generated.total_objects > 0

        result = duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'benchbox_%'"
        ).fetchone()
        assert result[0] > 0

        benchmark_result = benchmark.run_benchmark(
            duckdb_connection,
            dialect="duckdb",
            categories=["wide_table"],
        )
        assert benchmark_result.total_queries > 0

        benchmark.teardown_complexity(duckdb_connection, "duckdb", generated)

        result = duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'benchbox_%'"
        ).fetchone()
        assert result[0] == 0


@pytest.mark.integration
class TestWideTableQueriesDuckDB:
    @pytest.fixture
    def setup_wide_tables(self, duckdb_connection):
        config = MetadataComplexityConfig(
            width_factor=100,
            view_depth=0,
            catalog_size=1,
        )
        generator = MetadataGenerator()
        generated = generator.setup(duckdb_connection, "duckdb", config)
        yield generated
        generator.teardown(duckdb_connection, "duckdb", generated)

    def test_wide_table_column_list_query(self, duckdb_connection, setup_wide_tables):

        benchmark = MetadataPrimitivesBenchmark()
        result = benchmark.execute_query("wide_table_column_list", duckdb_connection, dialect="duckdb")

        assert result.success
        assert result.row_count == 100

    def test_wide_table_column_count_query(self, duckdb_connection, setup_wide_tables):

        benchmark = MetadataPrimitivesBenchmark()
        result = benchmark.execute_query("wide_table_column_count", duckdb_connection, dialect="duckdb")

        assert result.success
        assert result.row_count >= 1

    def test_wide_table_type_distribution_query(self, duckdb_connection, setup_wide_tables):

        benchmark = MetadataPrimitivesBenchmark()
        result = benchmark.execute_query("wide_table_type_distribution", duckdb_connection, dialect="duckdb")

        assert result.success
        assert result.row_count > 1


@pytest.mark.integration
class TestViewHierarchyQueriesDuckDB:
    @pytest.fixture
    def setup_view_hierarchy(self, duckdb_connection):
        config = MetadataComplexityConfig(
            width_factor=20,
            view_depth=3,
            catalog_size=1,
        )
        generator = MetadataGenerator()
        generated = generator.setup(duckdb_connection, "duckdb", config)
        yield generated
        generator.teardown(duckdb_connection, "duckdb", generated)

    def test_view_hierarchy_list_query(self, duckdb_connection, setup_view_hierarchy):

        benchmark = MetadataPrimitivesBenchmark()
        result = benchmark.execute_query("view_hierarchy_list", duckdb_connection, dialect="duckdb")

        assert result.success
        assert result.row_count == 3

    def test_view_hierarchy_depth_analysis_query(self, duckdb_connection, setup_view_hierarchy):

        benchmark = MetadataPrimitivesBenchmark()
        result = benchmark.execute_query("view_hierarchy_depth_analysis", duckdb_connection, dialect="duckdb")

        assert result.success
        assert result.row_count == 3


@pytest.mark.integration
class TestLargeCatalogQueriesDuckDB:
    @pytest.fixture
    def setup_large_catalog(self, duckdb_connection):
        config = MetadataComplexityConfig(
            width_factor=10,
            view_depth=0,
            catalog_size=50,
        )
        generator = MetadataGenerator()
        generated = generator.setup(duckdb_connection, "duckdb", config)
        yield generated
        generator.teardown(duckdb_connection, "duckdb", generated)

    def test_large_catalog_table_list_query(self, duckdb_connection, setup_large_catalog):

        benchmark = MetadataPrimitivesBenchmark()
        result = benchmark.execute_query("large_catalog_table_list", duckdb_connection, dialect="duckdb")

        assert result.success
        assert result.row_count == 50

    def test_large_catalog_table_count_query(self, duckdb_connection, setup_large_catalog):

        benchmark = MetadataPrimitivesBenchmark()
        result = benchmark.execute_query("large_catalog_table_count", duckdb_connection, dialect="duckdb")

        assert result.success
        assert result.row_count == 1


@pytest.mark.integration
class TestComplexityErrorHandling:
    def test_cleanup_on_manual_objects(self, duckdb_connection):

        generator = MetadataGenerator()

        duckdb_connection.execute("CREATE TABLE benchbox_manual_1 (id INT)")
        duckdb_connection.execute("CREATE TABLE benchbox_manual_2 (id INT)")

        dropped = generator.cleanup_all(duckdb_connection, "duckdb", "benchbox_")
        assert dropped >= 2

        result = duckdb_connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'benchbox_manual_%'"
        ).fetchone()
        assert result[0] == 0
