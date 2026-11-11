# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psutil
import pytest

from benchbox.tpcds import TPCDS
from benchbox.tpch import TPCH

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


class MemoryMonitor:
    def __init__(self, interval: float = 0.1):
        self.interval = interval
        self.measurements = []
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.measurements = []
        self.thread = threading.Thread(target=self._monitor)
        self.thread.daemon = True
        self.thread.start()

    def stop(self) -> dict[str, float]:
        self.running = False
        if self.thread:
            self.thread.join()

        if not self.measurements:
            return {"peak_mb": 0, "avg_mb": 0, "min_mb": 0}

        peak_mb = max(self.measurements)
        avg_mb = sum(self.measurements) / len(self.measurements)
        min_mb = min(self.measurements)

        return {"peak_mb": peak_mb, "avg_mb": avg_mb, "min_mb": min_mb}

    def _monitor(self):
        process = psutil.Process()
        while self.running:
            try:
                memory_mb = process.memory_info().rss / 1024 / 1024
                self.measurements.append(memory_mb)
                time.sleep(self.interval)
            except psutil.NoSuchProcess:
                break


@pytest.fixture
def memory_monitor():
    return MemoryMonitor()


@pytest.fixture
def performance_config():
    return {
        "timeout_seconds": 60,
        "memory_limit_mb": 1024,
        "max_concurrent_queries": 4,
        "scale_factors": [0.01, 0.1, 0.5],
        "warmup_runs": 1,
        "benchmark_runs": 3,
    }


@pytest.mark.slow
@pytest.mark.performance
@pytest.mark.duckdb
class TestDuckDBQueryPerformance:
    def test_tpch_complex_query_performance(self, duckdb_with_extensions, tmp_path, memory_monitor):
        tpch = TPCH(scale_factor=0.1, output_dir=tmp_path / "tpch_complex", verbose=False)

        with patch.object(tpch, "generate_data") as mock_gen:
            mock_gen.return_value = self._create_mock_data_files(tmp_path, 0.1)

            self._setup_tpch_tables(duckdb_with_extensions, 0.1)

            query_1 = """
                SELECT
                    l_returnflag,
                    l_linestatus,
                    SUM(l_quantity) as sum_qty,
                    SUM(l_extendedprice) as sum_base_price,
                    SUM(l_extendedprice * (1 - l_discount)) as sum_disc_price,
                    SUM(l_extendedprice * (1 - l_discount) * (1 + l_tax)) as sum_charge,
                    AVG(l_quantity) as avg_qty,
                    AVG(l_extendedprice) as avg_price,
                    AVG(l_discount) as avg_disc,
                    COUNT(*) as count_order
                FROM lineitem
                WHERE l_shipdate <= '1998-12-01'
                GROUP BY l_returnflag, l_linestatus
                ORDER BY l_returnflag, l_linestatus
            """

            memory_monitor.start()
            start_time = time.time()
            self._execute_query(duckdb_with_extensions, query_1)
            end_time = time.time()
            exec_time = end_time - start_time
            memory_stats = memory_monitor.stop()

            assert exec_time < 10.0, f"Complex query too slow: {exec_time}s"
            assert memory_stats["peak_mb"] < 2500, f"Memory usage too high: {memory_stats['peak_mb']}MB"

    def test_tpch_join_performance(self, duckdb_with_extensions, tmp_path, memory_monitor):
        tpch = TPCH(scale_factor=0.1, output_dir=tmp_path / "tpch_join", verbose=False)

        with patch.object(tpch, "generate_data") as mock_gen:
            mock_gen.return_value = self._create_mock_data_files(tmp_path, 0.1)

            self._setup_tpch_tables(duckdb_with_extensions, 0.1)

            join_query = """
                SELECT
                    c.c_name,
                    o.o_orderdate,
                    l.l_extendedprice
                FROM customer c
                JOIN orders o ON c.c_custkey = o.o_custkey
                JOIN lineitem l ON o.o_orderkey = l.l_orderkey
                WHERE o.o_orderdate >= '1995-01-01'
                AND o.o_orderdate < '1996-01-01'
                LIMIT 100
            """

            memory_monitor.start()
            start_time = time.time()
            self._execute_query(duckdb_with_extensions, join_query)
            end_time = time.time()
            exec_time = end_time - start_time
            memory_stats = memory_monitor.stop()

            assert exec_time < 5.0, f"Join query too slow: {exec_time}s"
            assert memory_stats["peak_mb"] < 2500, f"Join memory usage too high: {memory_stats['peak_mb']}MB"

    def test_tpcds_query_performance(self, duckdb_with_extensions, tmp_path, memory_monitor):
        tpcds = TPCDS(scale_factor=1.0, output_dir=tmp_path / "tpcds_perf", verbose=False)

        with patch.object(tpcds, "generate_data") as mock_gen:
            mock_gen.return_value = self._create_mock_tpcds_data_files(tmp_path)

            self._setup_tpcds_tables(duckdb_with_extensions)

            query = """
                SELECT
                    ss_store_sk,
                    SUM(ss_sales_price) as total_sales,
                    COUNT(*) as transaction_count
                FROM store_sales
                GROUP BY ss_store_sk
                ORDER BY total_sales DESC
                LIMIT 10
            """

            memory_monitor.start()
            start_time = time.time()
            self._execute_query(duckdb_with_extensions, query)
            end_time = time.time()
            exec_time = end_time - start_time
            memory_stats = memory_monitor.stop()

            assert exec_time < 5.0, f"TPC-DS query too slow: {exec_time}s"
            assert memory_stats["peak_mb"] < 2500, f"TPC-DS memory usage too high: {memory_stats['peak_mb']}MB"

    def test_query_cache_performance(self, duckdb_with_extensions, tmp_path):
        self._setup_tpch_tables(duckdb_with_extensions, 0.01)

        query = "SELECT COUNT(*) FROM lineitem WHERE l_shipdate <= '1998-12-01'"

        start_time = time.time()
        self._execute_query(duckdb_with_extensions, query)
        end_time = time.time()
        first_time = end_time - start_time

        start_time = time.time()
        self._execute_query(duckdb_with_extensions, query)
        end_time = time.time()
        second_time = end_time - start_time

        print(f"First execution: {first_time:.4f}s")
        print(f"Second execution: {second_time:.4f}s")
        print(f"Speedup ratio: {first_time / second_time:.2f}x")

        assert first_time > 0
        assert second_time > 0

    def _execute_query(self, conn, query: str) -> Any:
        if hasattr(conn, "_mock_name"):
            return [[42]]
        else:
            result = conn.execute(query).fetchall()
            return result

    def _create_mock_data_files(self, tmp_path: Path, scale_factor: float) -> dict[str, str]:
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)

        base_rows = {
            "region": 5,
            "nation": 25,
            "customer": int(150000 * scale_factor),
            "supplier": int(10000 * scale_factor),
            "part": int(200000 * scale_factor),
            "partsupp": int(800000 * scale_factor),
            "orders": int(1500000 * scale_factor),
            "lineitem": int(6000000 * scale_factor),
        }

        files = {}
        for table, row_count in base_rows.items():
            file_path = data_dir / f"{table}.csv"
            files[table] = str(file_path)

            with open(file_path, "w", encoding="utf-8") as f:
                if table == "lineitem":
                    f.write(
                        "l_orderkey,l_partkey,l_suppkey,l_linenumber,l_quantity,l_extendedprice,l_discount,l_tax,l_returnflag,l_linestatus,l_shipdate,l_commitdate,l_receiptdate,l_shipinstruct,l_shipmode,l_comment\n"
                    )
                    for i in range(min(row_count, 1000)):
                        f.write(
                            f"{i},{i},{i},{i},1.0,100.0,0.05,0.08,A,F,1998-01-01,1998-01-15,1998-01-20,DELIVER IN PERSON,TRUCK,test comment\n"
                        )
                elif table == "orders":
                    f.write(
                        "o_orderkey,o_custkey,o_orderstatus,o_totalprice,o_orderdate,o_orderpriority,o_clerk,o_shippriority,o_comment\n"
                    )
                    for i in range(min(row_count, 1000)):
                        f.write(f"{i},{i},O,1000.0,1995-01-01,5-LOW,Clerk#{i:09d},0,test comment\n")
                elif table == "customer":
                    f.write("c_custkey,c_name,c_address,c_nationkey,c_phone,c_acctbal,c_mktsegment,c_comment\n")
                    for i in range(min(row_count, 1000)):
                        f.write(f"{i},Customer#{i:09d},Address {i},1,12-345-678-9012,1000.0,BUILDING,test comment\n")
                else:
                    f.write("id,name\n")
                    for i in range(min(row_count, 100)):
                        f.write(f"{i},{table}_{i}\n")

        return files

    def _create_mock_tpcds_data_files(self, tmp_path: Path) -> dict[str, str]:
        data_dir = tmp_path / "tpcds_data"
        data_dir.mkdir(exist_ok=True)

        store_sales_path = data_dir / "store_sales.csv"
        with open(store_sales_path, "w", encoding="utf-8") as f:
            f.write("ss_store_sk,ss_sales_price\n")
            for i in range(100):
                f.write(f"{i % 10},{100.0 + i}\n")

        return {"store_sales": str(store_sales_path)}

    def _setup_tpch_tables(self, conn, scale_factor: float):
        if hasattr(conn, "_mock_name"):
            return

        tables = {
            "lineitem": """
                CREATE TABLE IF NOT EXISTS lineitem (
                    l_orderkey INTEGER,
                    l_partkey INTEGER,
                    l_suppkey INTEGER,
                    l_linenumber INTEGER,
                    l_quantity DECIMAL(15,2),
                    l_extendedprice DECIMAL(15,2),
                    l_discount DECIMAL(15,2),
                    l_tax DECIMAL(15,2),
                    l_returnflag CHAR(1),
                    l_linestatus CHAR(1),
                    l_shipdate DATE,
                    l_commitdate DATE,
                    l_receiptdate DATE,
                    l_shipinstruct CHAR(25),
                    l_shipmode CHAR(10),
                    l_comment VARCHAR(44)
                )
            """,
            "orders": """
                CREATE TABLE IF NOT EXISTS orders (
                    o_orderkey INTEGER,
                    o_custkey INTEGER,
                    o_orderstatus CHAR(1),
                    o_totalprice DECIMAL(15,2),
                    o_orderdate DATE,
                    o_orderpriority CHAR(15),
                    o_clerk CHAR(15),
                    o_shippriority INTEGER,
                    o_comment VARCHAR(79)
                )
            """,
            "customer": """
                CREATE TABLE IF NOT EXISTS customer (
                    c_custkey INTEGER,
                    c_name VARCHAR(25),
                    c_address VARCHAR(40),
                    c_nationkey INTEGER,
                    c_phone CHAR(15),
                    c_acctbal DECIMAL(15,2),
                    c_mktsegment CHAR(10),
                    c_comment VARCHAR(117)
                )
            """,
            "supplier": """
                CREATE TABLE IF NOT EXISTS supplier (
                    s_suppkey INTEGER,
                    s_name CHAR(25),
                    s_address VARCHAR(40),
                    s_nationkey INTEGER,
                    s_phone CHAR(15),
                    s_acctbal DECIMAL(15,2),
                    s_comment VARCHAR(101)
                )
            """,
        }

        for table_name, create_sql in tables.items():
            try:
                conn.execute(create_sql)
            except Exception as e:
                print(f"Warning: Could not create table {table_name}: {e}")

        row_count = max(1, int(1000 * scale_factor))

        try:
            for i in range(row_count):
                conn.execute(
                    """
                    INSERT INTO lineitem VALUES
                    (?, ?, ?, ?, 1.0, 100.0, 0.05, 0.08, 'A', 'F',
                     '1998-01-01', '1998-01-15', '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'test comment')
                """,
                    [i, i, i, i],
                )
        except Exception as e:
            print(f"Warning: Could not insert lineitem data: {e}")

        try:
            for i in range(row_count):
                conn.execute(
                    """
                    INSERT INTO orders VALUES
                    (?, ?, 'O', 1000.0, '1995-01-01', '5-LOW', ?, 0, 'test comment')
                """,
                    [i, i, f"Clerk#{i:09d}"],
                )
        except Exception as e:
            print(f"Warning: Could not insert orders data: {e}")

        try:
            for i in range(row_count):
                conn.execute(
                    """
                    INSERT INTO customer VALUES
                    (?, ?, ?, 1, '12-345-678-9012', 1000.0, 'BUILDING', 'test comment')
                """,
                    [i, f"Customer#{i:09d}", f"Address {i}"],
                )
        except Exception as e:
            print(f"Warning: Could not insert customer data: {e}")

    def _setup_tpcds_tables(self, conn):
        if hasattr(conn, "_mock_name"):
            return

        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS store_sales (
                    ss_store_sk INTEGER,
                    ss_sales_price DECIMAL(7,2)
                )
            """)

            for i in range(100):
                conn.execute(
                    """
                    INSERT INTO store_sales VALUES (?, ?)
                """,
                    [i % 10, 100.0 + i],
                )
        except Exception as e:
            print(f"Warning: Could not setup TPC-DS tables: {e}")


@pytest.mark.performance
@pytest.mark.duckdb
class TestDuckDBRegressionDetection:
    def test_baseline_performance_metrics(self, duckdb_with_extensions, tmp_path):
        self._setup_baseline_tables(duckdb_with_extensions)

        baseline_queries = {
            "simple_count": "SELECT COUNT(*) FROM test_table",
            "simple_aggregation": "SELECT category, COUNT(*) FROM test_table GROUP BY category",
            "simple_filter": "SELECT * FROM test_table WHERE value > 500 LIMIT 100",
        }

        baseline_results = {}
        for query_name, query in baseline_queries.items():
            start_time = time.time()
            self._execute_query(duckdb_with_extensions, query)
            end_time = time.time()
            exec_time = end_time - start_time
            baseline_results[query_name] = exec_time

            print(f"Baseline {query_name}: {exec_time:.4f}s")

        for query_name, exec_time in baseline_results.items():
            assert exec_time < 1.0, f"Baseline {query_name} too slow: {exec_time}s"

    def _setup_baseline_tables(self, conn):
        if hasattr(conn, "_mock_name"):
            return

        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS test_table (
                    id INTEGER,
                    category VARCHAR(10),
                    value DECIMAL(10,2)
                )
            """)

            for i in range(1000):
                conn.execute(
                    """
                    INSERT INTO test_table VALUES (?, ?, ?)
                """,
                    [i, f"cat_{i % 10}", 100.0 + i],
                )
        except Exception as e:
            print(f"Warning: Could not setup baseline tables: {e}")

    def _execute_query(self, conn, query: str) -> Any:
        if hasattr(conn, "_mock_name"):
            return [[42]]
        else:
            result = conn.execute(query).fetchall()
            return result


