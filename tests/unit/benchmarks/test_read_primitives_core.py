# Copyright 2026 Joe Harris / BenchBox Project

# This implementation is derived from TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.read_primitives.queries import ReadPrimitivesQueryManager
from benchbox.core.read_primitives.schema import (
    TABLES,
    get_all_create_table_sql,
    get_create_table_sql,
)
from benchbox.read_primitives import ReadPrimitives

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.unit
class TestPrimitivesWrapper:
    def test_primitives_initialization(self):

        read_primitives = ReadPrimitives(scale_factor=0.1)
        assert read_primitives.scale_factor == 0.1
        assert hasattr(read_primitives, "_impl")
        assert isinstance(read_primitives._impl, ReadPrimitivesBenchmark)

    def test_primitives_get_queries(self):

        read_primitives = ReadPrimitives()
        queries = read_primitives.get_queries()
        assert isinstance(queries, dict)
        assert len(queries) > 0

    def test_primitives_get_query_categories(self):

        read_primitives = ReadPrimitives()
        categories = read_primitives.get_query_categories()
        assert isinstance(categories, list)
        assert len(categories) > 0

    def test_primitives_get_schema(self):

        read_primitives = ReadPrimitives()
        schema = read_primitives.get_schema()
        assert isinstance(schema, dict)
        assert len(schema) == 8

    def test_primitives_delegation(self):

        read_primitives = ReadPrimitives()

        query = read_primitives.get_query("aggregation_simple")
        assert isinstance(query, str)
        assert "SELECT" in query.upper()

        cat_queries = read_primitives.get_queries_by_category("aggregation")
        assert isinstance(cat_queries, dict)
        assert len(cat_queries) > 0


@pytest.mark.unit
class TestPrimitivesSchema:
    def test_tables_exist(self):

        expected_tables = [
            "region",
            "nation",
            "customer",
            "supplier",
            "part",
            "partsupp",
            "orders",
            "lineitem",
        ]
        for table in expected_tables:
            assert table in TABLES, f"Missing TPC-H table: {table}"
        assert set(TABLES.keys()) == set(expected_tables)

    def test_create_table_sql(self):

        sql = get_create_table_sql("region")
        assert "CREATE TABLE region" in sql
        assert "r_regionkey INTEGER PRIMARY KEY" in sql
        assert "r_name CHAR(25)" in sql
        assert "r_comment VARCHAR(152)" in sql

    def test_create_table_sql_foreign_keys(self):

        sql = get_create_table_sql("nation", enable_foreign_keys=True)
        assert "FOREIGN KEY (n_regionkey) REFERENCES region(r_regionkey)" in sql

        sql_no_fk = get_create_table_sql("nation", enable_foreign_keys=False)
        assert "FOREIGN KEY" not in sql_no_fk

    def test_create_table_sql_composite_pk_with_fks(self):
        sql = get_create_table_sql("partsupp")
        assert "PRIMARY KEY (ps_partkey, ps_suppkey)" in sql
        assert "FOREIGN KEY (ps_partkey) REFERENCES part(p_partkey)" in sql
        assert "FOREIGN KEY (ps_suppkey) REFERENCES supplier(s_suppkey)" in sql

    def test_all_create_table_sql(self):

        sql = get_all_create_table_sql()
        for table_name in TABLES:
            assert f"CREATE TABLE {table_name}" in sql

    def test_invalid_table(self):

        with pytest.raises(ValueError, match="Unknown table"):
            get_create_table_sql("invalid_table")


@pytest.mark.unit
class TestReadPrimitivesQueryManager:
    def test_query_manager_initialization(self):

        manager = ReadPrimitivesQueryManager()
        queries = manager.get_all_queries()
        assert len(queries) > 0
        assert isinstance(queries, dict)

    def test_get_query(self):

        manager = ReadPrimitivesQueryManager()

        query = manager.get_query("aggregation_simple")
        assert "SELECT" in query
        assert "COUNT(*)" in query
        assert "orders" in query

        with pytest.raises(ValueError, match="Invalid query ID"):
            manager.get_query("invalid_query")

    def test_query_categories(self):

        manager = ReadPrimitivesQueryManager()

        categories = manager.get_query_categories()
        assert "aggregation" in categories
        assert "window" in categories
        assert "filter" in categories

        agg_queries = manager.get_queries_by_category("aggregation")
        assert len(agg_queries) > 0


@pytest.mark.unit
class TestReadPrimitivesBenchmark:
    def test_benchmark_initialization(self, temp_dir, small_scale_factor):

        benchmark = ReadPrimitivesBenchmark(scale_factor=small_scale_factor, output_dir=str(temp_dir))
        assert benchmark.scale_factor == small_scale_factor
        assert benchmark._name == "Read Primitives Benchmark"
        assert benchmark._version == "1.0"

    def test_get_queries(self):

        benchmark = ReadPrimitivesBenchmark()

        queries = benchmark.get_queries()
        assert len(queries) > 0
        assert "aggregation_simple" in queries

        query = benchmark.get_query("aggregation_simple")
        assert "SELECT" in query

    def test_query_categories(self):

        benchmark = ReadPrimitivesBenchmark()

        categories = benchmark.get_query_categories()
        assert "aggregation" in categories

        agg_queries = benchmark.get_queries_by_category("aggregation")
        assert len(agg_queries) > 0

    def test_schema_operations(self):

        benchmark = ReadPrimitivesBenchmark()

        schema = benchmark.get_schema()
        assert schema == TABLES

        sql = benchmark.get_create_tables_sql()
        assert "CREATE TABLE region" in sql
        assert "CREATE TABLE lineitem" in sql

    def test_data_generation_integration(self, temp_dir, small_scale_factor):

        benchmark = ReadPrimitivesBenchmark(scale_factor=small_scale_factor, output_dir=str(temp_dir))

        from unittest.mock import Mock

        mock_file_paths = {
            "region": str(temp_dir / "region.csv"),
            "nation": str(temp_dir / "nation.csv"),
        }
        benchmark.data_generator.generate_data = Mock(return_value=mock_file_paths)

        file_paths = benchmark.generate_data(["region", "nation"])

        assert "region" in file_paths
        assert "nation" in file_paths
        assert benchmark.tables == file_paths

    def test_invalid_operations(self):

        benchmark = ReadPrimitivesBenchmark()

        with pytest.raises(ValueError, match="Invalid query ID"):
            benchmark.get_query("invalid_query")

        with pytest.raises(ValueError, match="Invalid table names"):
            benchmark.generate_data(["invalid_table"])

        with pytest.raises(ValueError, match="No data generated"):
            benchmark.load_data_to_database(None)

    def test_benchmark_info(self):

        benchmark = ReadPrimitivesBenchmark(scale_factor=2.0)

        info = benchmark.get_benchmark_info()
        assert info["name"] == "Read Primitives Benchmark"
        assert info["version"] == "1.0"
        assert info["scale_factor"] == 2.0
        assert info["schema"] == "TPC-H"
        assert "aggregation" in info["categories"]
        assert len(info["tables"]) == 8
        assert info["total_queries"] > 0

    def test_data_source_declaration(self):

        benchmark = ReadPrimitivesBenchmark(scale_factor=1.0)

        data_source = benchmark.get_data_source_benchmark()
        assert data_source == "tpch"

    def test_default_output_path_uses_tpch(self):

        benchmark = ReadPrimitivesBenchmark(scale_factor=1.0)

        output_path = str(benchmark.output_dir)
        assert ("tpch_sf1.0" in output_path) or ("tpch_sf1" in output_path)
        assert "primitives_sf" not in output_path

    def test_custom_output_path_respected(self, temp_dir):

        custom_path = str(temp_dir / "custom_primitives")
        benchmark = ReadPrimitivesBenchmark(scale_factor=1.0, output_dir=custom_path)

        assert str(benchmark.output_dir) == custom_path


