# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
import time

import duckdb
import psutil
import pytest

from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.core.tpch.benchmark import TPCHBenchmark

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


class TestTPCPerformanceDuckDB:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()

    def test_tpc_metrics_calculation_performance(self):
        start_time = time.time()

        for i in range(1000):
            power_time = 100.0 + i * 0.1
            throughput_time = 200.0 + i * 0.2
            power_at_size = 3600.0 / power_time
            assert TPCMetricsCalculator.calculate_power_at_size([power_time], 1.0) == pytest.approx(power_at_size)
            assert TPCMetricsCalculator.calculate_throughput_at_size(2, throughput_time, 1.0, 2) > 0

        calculation_time = time.time() - start_time

        assert calculation_time < 1.0, f"Metrics calculation too slow: {calculation_time:.4f}s"
        print(f"Metrics calculation time: {calculation_time:.4f}s")

    def test_duckdb_connection_performance(self):
        start_time = time.time()

        for _i in range(100):
            conn = duckdb.connect(":memory:")
            try:
                result = conn.execute("SELECT 1 as test_value").fetchall()
                assert result == [(1,)]
            finally:
                conn.close()

        connection_time = time.time() - start_time

        assert connection_time < 5.0, f"DuckDB connection performance too slow: {connection_time:.4f}s"
        print(f"DuckDB connection cycles time: {connection_time:.4f}s")

    def test_benchmark_initialization_performance(self):
        start_time = time.time()

        TPCHBenchmark(scale_factor=0.01, output_dir=self.temp_dir)
        tpch_time = time.time() - start_time

        tpcds_start = time.time()
        TPCDSBenchmark(scale_factor=1.0, output_dir=self.temp_dir)
        tpcds_time = time.time() - tpcds_start

        assert tpch_time < 2.0, f"TPC-H init too slow: {tpch_time:.4f}s"
        assert tpcds_time < 2.0, f"TPC-DS init too slow: {tpcds_time:.4f}s"

        print(f"TPC-H init time: {tpch_time:.4f}s")
        print(f"TPC-DS init time: {tpcds_time:.4f}s")

    def test_memory_usage_with_duckdb(self):
        process = psutil.Process()
        initial_memory = process.memory_info().rss / 1024 / 1024

        TPCHBenchmark(scale_factor=0.01, output_dir=self.temp_dir)

        connections = []
        try:
            for _i in range(10):
                conn = duckdb.connect(":memory:")
                connections.append(conn)

                conn.execute("""
                    CREATE TABLE test_table (
                        id INTEGER,
                        name VARCHAR(50),
                        value DECIMAL(10,2)
                    )
                """)

                for j in range(100):
                    conn.execute(
                        "INSERT INTO test_table VALUES (?, ?, ?)",
                        [j, f"name_{j}", float(j * 1.5)],
                    )

                result = conn.execute("SELECT COUNT(*) FROM test_table").fetchone()
                assert result[0] == 100

            final_memory = process.memory_info().rss / 1024 / 1024
            memory_increase = final_memory - initial_memory

            assert memory_increase < 100.0, f"Memory usage too high: {memory_increase:.2f} MB"
            print(f"Memory increase: {memory_increase:.2f} MB")

        finally:
            for conn in connections:
                conn.close()

    def test_query_execution_performance(self):
        conn = duckdb.connect(":memory:")

        try:
            conn.execute("""
                CREATE TABLE customer (
                    c_custkey INTEGER,
                    c_name VARCHAR(25),
                    c_address VARCHAR(40),
                    c_nationkey INTEGER,
                    c_phone CHAR(15),
                    c_acctbal DECIMAL(15,2),
                    c_mktsegment CHAR(10),
                    c_comment VARCHAR(117)
                )
            """)

            start_time = time.time()
            for i in range(1000):
                conn.execute(
                    """
                    INSERT INTO customer VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    [
                        i,
                        f"Customer#{i:09d}",
                        f"Address{i}",
                        i % 25,
                        f"15-{i:03d}-{i:03d}-{i:04d}",
                        float(i * 100.5),
                        "BUILDING",
                        f"Comment {i}",
                    ],
                )

            insert_time = time.time() - start_time

            query_start = time.time()
            result = conn.execute("""
                SELECT c_mktsegment, COUNT(*), AVG(c_acctbal)
                FROM customer
                GROUP BY c_mktsegment
                ORDER BY c_mktsegment
            """).fetchall()

            query_time = time.time() - query_start

            assert len(result) > 0

            assert insert_time < 5.0, f"Insert performance too slow: {insert_time:.4f}s"
            assert query_time < 1.0, f"Query performance too slow: {query_time:.4f}s"

            print(f"Insert time: {insert_time:.4f}s")
            print(f"Query time: {query_time:.4f}s")
            print(f"Records per second: {1000 / insert_time:.0f}")

        finally:
            conn.close()

    def test_concurrent_operations_performance(self):
        import concurrent.futures

        def worker_task(worker_id: int) -> float:
            conn = duckdb.connect(":memory:")
            start_time = time.time()

            try:
                conn.execute(f"""
                    CREATE TABLE worker_{worker_id} (
                        id INTEGER,
                        value DOUBLE
                    )
                """)

                for i in range(100):
                    conn.execute(
                        f"INSERT INTO worker_{worker_id} VALUES (?, ?)",
                        [i, float(i * worker_id)],
                    )

                result = conn.execute(f"SELECT COUNT(*), AVG(value) FROM worker_{worker_id}").fetchone()
                assert result[0] == 100

                return time.time() - start_time

            finally:
                conn.close()

        start_time = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker_task, i) for i in range(5)]
            worker_times = [future.result() for future in concurrent.futures.as_completed(futures)]

        total_time = time.time() - start_time
        avg_worker_time = sum(worker_times) / len(worker_times)

        assert total_time < 10.0, f"Concurrent operations too slow: {total_time:.4f}s"
        assert avg_worker_time < 5.0, f"Average worker time too slow: {avg_worker_time:.4f}s"

        print(f"Concurrent operations total time: {total_time:.4f}s")
        print(f"Average worker time: {avg_worker_time:.4f}s")

    def test_benchmark_schema_creation_performance(self):
        benchmark = TPCHBenchmark(scale_factor=0.01, output_dir=self.temp_dir)

        conn = duckdb.connect(":memory:")

        try:
            start_time = time.time()

            schema_sql = benchmark.get_create_tables_sql()
            for statement in schema_sql.strip().split(";"):
                if statement.strip():
                    conn.execute(statement.strip())

            schema_time = time.time() - start_time

            tables = conn.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
            """).fetchall()

            table_names = [row[0].lower() for row in tables]
            expected_tables = [
                "customer",
                "lineitem",
                "nation",
                "orders",
                "part",
                "partsupp",
                "region",
                "supplier",
            ]

            for table in expected_tables:
                assert table in table_names, f"Table {table} not created"

            assert schema_time < 2.0, f"Schema creation too slow: {schema_time:.4f}s"
            print(f"Schema creation time: {schema_time:.4f}s")

        finally:
            conn.close()


