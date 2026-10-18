# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.metadata_primitives import (
    MetadataPrimitivesBenchmark,
    MetadataPrimitivesQueryManager,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.fixture
def duckdb_connection():
    import duckdb

    conn = duckdb.connect(":memory:")

    conn.execute("""
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(255),
            created_at TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            total_amount DECIMAL(10, 2),
            order_date DATE,
            status VARCHAR(20)
        )
    """)

    conn.execute("""
        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY,
            name VARCHAR(200),
            price DECIMAL(10, 2),
            category VARCHAR(50)
        )
    """)

    conn.execute("""
        CREATE VIEW active_orders AS
        SELECT * FROM orders WHERE status = 'active'
    """)

    yield conn
    conn.close()


@pytest.mark.integration
class TestMetadataPrimitivesQueryExecution:
    def test_schema_list_schemata(self, duckdb_connection):

        manager = MetadataPrimitivesQueryManager()
        sql = manager.get_query("schema_list_schemata", dialect="duckdb")

        result = duckdb_connection.execute(sql).fetchall()
        assert len(result) > 0

        schema_names = [row[0] for row in result]
        assert "main" in schema_names

    def test_schema_list_tables(self, duckdb_connection):

        manager = MetadataPrimitivesQueryManager()
        sql = manager.get_query("schema_list_tables", dialect="duckdb")

        result = duckdb_connection.execute(sql).fetchall()
        assert len(result) >= 3

        table_names = [row[0] for row in result]
        assert "customers" in table_names
        assert "orders" in table_names
        assert "products" in table_names

    def test_schema_list_views(self, duckdb_connection):

        manager = MetadataPrimitivesQueryManager()
        sql = manager.get_query("schema_list_views", dialect="duckdb")

        result = duckdb_connection.execute(sql).fetchall()
        view_names = [row[0] for row in result]
        assert "active_orders" in view_names

    def test_schema_table_count(self, duckdb_connection):

        manager = MetadataPrimitivesQueryManager()
        sql = manager.get_query("schema_table_count", dialect="duckdb")

        result = duckdb_connection.execute(sql).fetchall()
        assert len(result) == 1
        count = result[0][0]
        assert count >= 3

    def test_column_list_all(self, duckdb_connection):

        manager = MetadataPrimitivesQueryManager()
        sql = manager.get_query("column_list_all", dialect="duckdb")

        result = duckdb_connection.execute(sql).fetchall()
        assert len(result) > 0

        column_names = [row[2] for row in result]
        assert "customer_id" in column_names
        assert "order_id" in column_names

    def test_column_data_types(self, duckdb_connection):

        manager = MetadataPrimitivesQueryManager()
        sql = manager.get_query("column_data_types", dialect="duckdb")

        result = duckdb_connection.execute(sql).fetchall()
        assert len(result) > 0

        data_types = [row[0] for row in result]
        data_types_upper = [dt.upper() for dt in data_types]
        has_integer = any("INT" in dt for dt in data_types_upper)
        has_varchar = any("VARCHAR" in dt for dt in data_types_upper)
        assert has_integer or has_varchar

    def test_stats_column_count_summary(self, duckdb_connection):

        manager = MetadataPrimitivesQueryManager()
        sql = manager.get_query("stats_column_count_summary", dialect="duckdb")

        result = duckdb_connection.execute(sql).fetchall()
        assert len(result) == 1

        total_tables, total_columns, avg_columns = result[0]
        assert total_tables >= 3
        assert total_columns >= 10
        assert avg_columns > 0

    def test_query_explain_simple(self, duckdb_connection):

        manager = MetadataPrimitivesQueryManager()
        sql = manager.get_query("query_explain_simple", dialect="duckdb")

        result = duckdb_connection.execute(sql).fetchall()
        assert len(result) > 0


@pytest.mark.integration
class TestMetadataPrimitivesBenchmarkExecution:
    def test_execute_single_query(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.execute_query(
            "schema_list_tables",
            duckdb_connection,
            dialect="duckdb",
        )

        assert result.success is True
        assert result.query_id == "schema_list_tables"
        assert result.category == "schema"
        assert result.execution_time_ms > 0
        assert result.row_count >= 0

    def test_execute_query_with_error(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.execute_query(
            "schema_list_views",
            duckdb_connection,
            dialect="clickhouse",
        )

        assert result.success is False
        assert result.error is not None

    def test_run_benchmark_all_queries(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.run_benchmark(
            duckdb_connection,
            dialect="duckdb",
        )

        assert result.total_queries > 0
        assert result.successful_queries > 0
        assert result.total_time_ms > 0
        assert len(result.results) > 0
        assert len(result.category_summary) > 0

    def test_run_benchmark_by_category(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.run_benchmark(
            duckdb_connection,
            dialect="duckdb",
            categories=["schema"],
        )

        assert result.total_queries > 0
        for qr in result.results:
            assert qr.category == "schema"

    def test_run_benchmark_specific_queries(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.run_benchmark(
            duckdb_connection,
            dialect="duckdb",
            query_ids=["schema_list_tables", "column_list_all"],
        )

        assert result.total_queries == 2
        query_ids = {r.query_id for r in result.results}
        assert "schema_list_tables" in query_ids
        assert "column_list_all" in query_ids

    def test_run_benchmark_with_iterations(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.run_benchmark(
            duckdb_connection,
            dialect="duckdb",
            query_ids=["schema_list_tables"],
            iterations=3,
        )

        assert result.total_queries == 3
        assert all(r.query_id == "schema_list_tables" for r in result.results)

    def test_category_summary(self, duckdb_connection):

        benchmark = MetadataPrimitivesBenchmark()

        result = benchmark.run_benchmark(
            duckdb_connection,
            dialect="duckdb",
            categories=["schema", "column"],
        )

        assert "schema" in result.category_summary
        assert "column" in result.category_summary

        schema_summary = result.category_summary["schema"]
        assert "total_queries" in schema_summary
        assert "successful" in schema_summary
        assert "avg_time_ms" in schema_summary


@pytest.mark.integration
class TestAllQueriesExecute:
    def test_all_duckdb_queries_execute(self, duckdb_connection):
        manager = MetadataPrimitivesQueryManager()
        queries = manager.get_queries_for_dialect("duckdb")

        failed_queries = []
        for query_id, sql in queries.items():
            try:
                duckdb_connection.execute(sql).fetchall()
            except Exception as e:
                failed_queries.append((query_id, str(e)))

        if failed_queries:
            failure_msg = "\n".join(f"  {qid}: {err}" for qid, err in failed_queries)
            pytest.fail(f"The following queries failed:\n{failure_msg}")

    def test_schema_category_queries(self, duckdb_connection):
        manager = MetadataPrimitivesQueryManager()
        schema_queries = manager.get_queries_by_category("schema")

        for query_id in schema_queries:
            try:
                sql = manager.get_query(query_id, dialect="duckdb")
                duckdb_connection.execute(sql).fetchall()
            except ValueError:
                continue

    def test_column_category_queries(self, duckdb_connection):
        manager = MetadataPrimitivesQueryManager()
        column_queries = manager.get_queries_by_category("column")

        for query_id in column_queries:
            try:
                sql = manager.get_query(query_id, dialect="duckdb")
                duckdb_connection.execute(sql).fetchall()
            except ValueError:
                continue

    def test_stats_category_queries(self, duckdb_connection):
        manager = MetadataPrimitivesQueryManager()
        stats_queries = manager.get_queries_by_category("stats")

        for query_id in stats_queries:
            try:
                sql = manager.get_query(query_id, dialect="duckdb")
                duckdb_connection.execute(sql).fetchall()
            except ValueError:
                continue


@pytest.mark.integration
class TestBenchmarkLoaderIntegration:
    def test_benchmark_loads_from_loader(self):

        from benchbox.core.benchmark_loader import get_benchmark_class

        benchmark_class = get_benchmark_class("metadata_primitives")
        assert benchmark_class == MetadataPrimitivesBenchmark

        benchmark = benchmark_class()
        assert benchmark._name == "Metadata Primitives Benchmark"
