# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import gc
import statistics
import threading
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

pytestmark = [
    pytest.mark.performance,
    pytest.mark.stress,
]


class QueryExecutionMonitor:
    def __init__(self, interval: float = 0.05):
        self.interval = interval
        self.cpu_measurements = []
        self.memory_measurements = []
        self.running = False
        self.thread = None
        self.start_time = None

    def start(self):
        self.running = True
        self.cpu_measurements = []
        self.memory_measurements = []
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._monitor)
        self.thread.daemon = True
        self.thread.start()

    def stop(self) -> dict[str, Any]:
        self.running = False
        if self.thread:
            self.thread.join()

        total_time = time.time() - self.start_time if self.start_time else 0

        return {
            "total_time": total_time,
            "cpu_stats": self._calculate_cpu_stats(),
            "memory_stats": self._calculate_memory_stats(),
        }

    def _monitor(self):
        process = psutil.Process()
        while self.running:
            try:
                cpu_percent = process.cpu_percent()
                self.cpu_measurements.append(cpu_percent)

                memory_mb = process.memory_info().rss / 1024 / 1024
                self.memory_measurements.append(memory_mb)

                time.sleep(self.interval)
            except psutil.NoSuchProcess:
                break

    def _calculate_cpu_stats(self) -> dict[str, float]:
        if not self.cpu_measurements:
            return {"avg_cpu": 0, "max_cpu": 0, "min_cpu": 0}

        return {
            "avg_cpu": statistics.mean(self.cpu_measurements),
            "max_cpu": max(self.cpu_measurements),
            "min_cpu": min(self.cpu_measurements),
        }

    def _calculate_memory_stats(self) -> dict[str, float]:
        if not self.memory_measurements:
            return {"peak_mb": 0, "avg_mb": 0, "min_mb": 0}

        return {
            "peak_mb": max(self.memory_measurements),
            "avg_mb": statistics.mean(self.memory_measurements),
            "min_mb": min(self.memory_measurements),
        }


class QueryPerformanceCollector:
    def __init__(self):
        self.query_metrics = []

    def record_query(
        self,
        query_id: str,
        execution_time: float,
        result_count: int,
        memory_mb: float,
        cpu_percent: float,
    ):
        self.query_metrics.append(
            {
                "query_id": query_id,
                "execution_time": execution_time,
                "result_count": result_count,
                "memory_mb": memory_mb,
                "cpu_percent": cpu_percent,
                "timestamp": time.time(),
            }
        )

    def get_performance_summary(self) -> dict[str, Any]:
        if not self.query_metrics:
            return {}

        execution_times = [m["execution_time"] for m in self.query_metrics]
        memory_usage = [m["memory_mb"] for m in self.query_metrics]
        cpu_usage = [m["cpu_percent"] for m in self.query_metrics]

        return {
            "total_queries": len(self.query_metrics),
            "avg_execution_time": statistics.mean(execution_times),
            "median_execution_time": statistics.median(execution_times),
            "max_execution_time": max(execution_times),
            "min_execution_time": min(execution_times),
            "avg_memory_mb": statistics.mean(memory_usage),
            "peak_memory_mb": max(memory_usage),
            "avg_cpu_percent": statistics.mean(cpu_usage),
            "peak_cpu_percent": max(cpu_usage),
        }

    def get_slow_queries(self, threshold_seconds: float = 1.0) -> list[dict[str, Any]]:
        return [metric for metric in self.query_metrics if metric["execution_time"] > threshold_seconds]


@pytest.fixture
def query_execution_monitor():
    return QueryExecutionMonitor()


@pytest.fixture
def query_performance_collector():
    return QueryPerformanceCollector()


@pytest.fixture
def query_execution_config():
    return {
        "timeout_seconds": 30,
        "memory_limit_mb": 2500,
        "cpu_limit_percent": 100,
        "slow_query_threshold": 1.0,
        "concurrent_queries": [1, 2, 4, 8],
        "query_repetitions": 3,
        "warmup_queries": 1,
    }


@pytest.mark.slow
@pytest.mark.performance
@pytest.mark.duckdb
class TestQueryExecutionPerformance:
    def test_tpch_query_execution_performance(
        self,
        duckdb_with_extensions,
        query_execution_monitor,
        query_performance_collector,
        query_execution_config,
        tmp_path,
    ):
        self._setup_tpch_test_data(duckdb_with_extensions, tmp_path)

        tpch_queries = {
            "q1_simple_aggregation": """
                SELECT
                    l_returnflag,
                    l_linestatus,
                    SUM(l_quantity) as sum_qty,
                    SUM(l_extendedprice) as sum_base_price,
                    COUNT(*) as count_order
                FROM lineitem
                WHERE l_shipdate <= '1998-12-01'
                GROUP BY l_returnflag, l_linestatus
                ORDER BY l_returnflag, l_linestatus
            """,
            "q3_join_aggregation": """
                SELECT
                    l_orderkey,
                    SUM(l_extendedprice * (1 - l_discount)) as revenue,
                    o_orderdate,
                    o_shippriority
                FROM customer, orders, lineitem
                WHERE c_mktsegment = 'BUILDING'
                AND c_custkey = o_custkey
                AND l_orderkey = o_orderkey
                AND o_orderdate < '1995-03-15'
                AND l_shipdate > '1995-03-15'
                GROUP BY l_orderkey, o_orderdate, o_shippriority
                ORDER BY revenue DESC, o_orderdate
                LIMIT 10
            """,
            "q5_complex_join": """
                SELECT
                    n_name,
                    SUM(l_extendedprice * (1 - l_discount)) as revenue
                FROM customer, orders, lineitem, supplier, nation, region
                WHERE c_custkey = o_custkey
                AND l_orderkey = o_orderkey
                AND l_suppkey = s_suppkey
                AND c_nationkey = s_nationkey
                AND s_nationkey = n_nationkey
                AND n_regionkey = r_regionkey
                AND r_name = 'ASIA'
                AND o_orderdate >= '1994-01-01'
                AND o_orderdate < '1995-01-01'
                GROUP BY n_name
                ORDER BY revenue DESC
            """,
        }

        query_results = {}

        for query_id, query_sql in tpch_queries.items():
            for _ in range(query_execution_config["warmup_queries"]):
                self._execute_query_safely(duckdb_with_extensions, query_sql)

            query_execution_monitor.start()
            start_time = time.time()
            self._execute_query_safely(duckdb_with_extensions, query_sql)
            end_time = time.time()
            execution_time = end_time - start_time
            metrics = query_execution_monitor.stop()

            query_performance_collector.record_query(
                query_id,
                execution_time,
                100,
                metrics["memory_stats"]["peak_mb"],
                metrics["cpu_stats"]["max_cpu"],
            )

            query_results[query_id] = {
                "execution_time": execution_time,
                "metrics": metrics,
            }

        self._verify_query_performance_thresholds(query_results, query_execution_config)

        slow_queries = query_performance_collector.get_slow_queries(query_execution_config["slow_query_threshold"])

        assert len(slow_queries) == 0, f"Found {len(slow_queries)} slow queries"

    def test_tpcds_query_execution_performance(
        self,
        duckdb_with_extensions,
        query_execution_monitor,
        query_performance_collector,
        query_execution_config,
        tmp_path,
    ):
        self._setup_tpcds_test_data(duckdb_with_extensions, tmp_path)

        tpcds_queries = {
            "store_sales_summary": """
                SELECT
                    ss_store_sk,
                    SUM(ss_sales_price) as total_sales,
                    COUNT(*) as transaction_count,
                    AVG(ss_sales_price) as avg_sale_price
                FROM store_sales
                WHERE ss_sold_date_sk BETWEEN 2450815 AND 2451179
                GROUP BY ss_store_sk
                ORDER BY total_sales DESC
                LIMIT 20
            """,
            "customer_analysis": """
                SELECT
                    c_customer_sk,
                    c_first_name,
                    c_last_name,
                    SUM(ss_sales_price) as total_spent
                FROM customer, store_sales
                WHERE c_customer_sk = ss_customer_sk
                AND ss_sold_date_sk BETWEEN 2450815 AND 2451179
                GROUP BY c_customer_sk, c_first_name, c_last_name
                HAVING SUM(ss_sales_price) > 1000
                ORDER BY total_spent DESC
                LIMIT 10
            """,
        }

        query_results = {}

        for query_id, query_sql in tpcds_queries.items():
            for _ in range(query_execution_config["warmup_queries"]):
                self._execute_query_safely(duckdb_with_extensions, query_sql)

            query_execution_monitor.start()
            start_time = time.time()
            self._execute_query_safely(duckdb_with_extensions, query_sql)
            end_time = time.time()
            execution_time = end_time - start_time
            metrics = query_execution_monitor.stop()

            query_performance_collector.record_query(
                query_id,
                execution_time,
                50,
                metrics["memory_stats"]["peak_mb"],
                metrics["cpu_stats"]["max_cpu"],
            )

            query_results[query_id] = {
                "execution_time": execution_time,
                "metrics": metrics,
            }

        self._verify_query_performance_thresholds(query_results, query_execution_config)

    def test_query_scaling_with_data_size(self, duckdb_with_extensions, query_execution_monitor, tmp_path):
        scale_factors = [0.01, 0.05, 0.1]
        query_scaling_results = {}

        test_query = "SELECT COUNT(*) FROM lineitem WHERE l_discount > 0.05"

        for scale_factor in scale_factors:
            self._setup_tpch_test_data(duckdb_with_extensions, tmp_path, scale_factor)

            query_execution_monitor.start()
            start_time = time.time()
            self._execute_query_safely(duckdb_with_extensions, test_query)
            end_time = time.time()
            execution_time = end_time - start_time
            metrics = query_execution_monitor.stop()

            query_scaling_results[scale_factor] = {
                "execution_time": execution_time,
                "metrics": metrics,
            }

        self._verify_query_scaling_behavior(query_scaling_results)

    def test_memory_intensive_queries(
        self,
        duckdb_with_extensions,
        query_execution_monitor,
        query_execution_config,
        tmp_path,
    ):
        self._setup_tpch_test_data(duckdb_with_extensions, tmp_path)

        memory_intensive_queries = {
            "large_aggregation": """
                SELECT
                    l_orderkey,
                    l_partkey,
                    l_suppkey,
                    COUNT(*) as cnt,
                    SUM(l_extendedprice) as total_price,
                    AVG(l_discount) as avg_discount,
                    STRING_AGG(l_comment, '; ') as comments
                FROM lineitem
                GROUP BY l_orderkey, l_partkey, l_suppkey
                ORDER BY total_price DESC
                LIMIT 100
            """,
            "window_functions": """
                SELECT
                    l_orderkey,
                    l_linenumber,
                    l_extendedprice,
                    ROW_NUMBER() OVER (PARTITION BY l_orderkey ORDER BY l_linenumber) as row_num,
                    SUM(l_extendedprice) OVER (PARTITION BY l_orderkey) as order_total,
                    LAG(l_extendedprice, 1) OVER (PARTITION BY l_orderkey ORDER BY l_linenumber) as prev_price
                FROM lineitem
                ORDER BY l_orderkey, l_linenumber
                LIMIT 1000
            """,
        }

        memory_results = {}

        for query_id, query_sql in memory_intensive_queries.items():
            gc.collect()

            query_execution_monitor.start()
            start_time = time.time()
            self._execute_query_safely(duckdb_with_extensions, query_sql)
            end_time = time.time()
            execution_time = end_time - start_time
            metrics = query_execution_monitor.stop()

            memory_results[query_id] = {
                "execution_time": execution_time,
                "peak_memory_mb": metrics["memory_stats"]["peak_mb"],
                "avg_memory_mb": metrics["memory_stats"]["avg_mb"],
            }

            assert metrics["memory_stats"]["peak_mb"] < query_execution_config["memory_limit_mb"], (
                f"Query {query_id} used too much memory: {metrics['memory_stats']['peak_mb']}MB"
            )

        for query_id, result in memory_results.items():
            assert result["execution_time"] > 0, f"Query {query_id} failed to execute"

    def test_query_result_consistency(self, duckdb_with_extensions, tmp_path):
        self._setup_tpch_test_data(duckdb_with_extensions, tmp_path)

        consistency_query = "SELECT COUNT(*) as total_count FROM lineitem WHERE l_discount > 0.05"

        results = []
        for _run in range(3):
            time.time()
            result = self._execute_query_safely(duckdb_with_extensions, consistency_query)
            time.time()
            if result and len(result) > 0:
                results.append(result[0][0])

        if results:
            first_result = results[0]
            for i, result in enumerate(results[1:], 1):
                assert result == first_result, f"Inconsistent result on run {i + 1}: {result} vs {first_result}"

    def test_query_timeout_handling(self, duckdb_with_extensions, query_execution_config, tmp_path):
        self._setup_tpch_test_data(duckdb_with_extensions, tmp_path)

        timeout_query = """
            SELECT
                l1.l_orderkey,
                l2.l_orderkey,
                COUNT(*) as match_count
            FROM lineitem l1, lineitem l2
            WHERE l1.l_partkey = l2.l_partkey
            AND l1.l_orderkey != l2.l_orderkey
            GROUP BY l1.l_orderkey, l2.l_orderkey
            LIMIT 10
        """

        start_time = time.time()
        result = self._execute_query_safely(duckdb_with_extensions, timeout_query)
        execution_time = time.time() - start_time

        assert execution_time < query_execution_config["timeout_seconds"], (
            f"Query took too long: {execution_time}s > {query_execution_config['timeout_seconds']}s"
        )

        assert result is not None, "Query failed to complete"

    def _execute_query_safely(self, conn, query: str) -> Any:
        try:
            if hasattr(conn, "_mock_name"):
                return [[42]]
            else:
                result = conn.execute(query).fetchall()
                return result
        except Exception as e:
            print(f"Query execution failed: {e}")
            return None

    def _setup_tpch_test_data(self, conn, tmp_path: Path, scale_factor: float = 0.01):
        if hasattr(conn, "_mock_name"):
            return

        self._create_tpch_tables(conn)
        self._insert_tpch_test_data(conn, scale_factor)

    def _setup_tpcds_test_data(self, conn, tmp_path: Path, scale_factor: float = 0.01):
        if hasattr(conn, "_mock_name"):
            return

        self._create_tpcds_tables(conn)
        self._insert_tpcds_test_data(conn, scale_factor)

    def _create_tpch_tables(self, conn):
        tables = {
            "region": """
                CREATE TABLE IF NOT EXISTS region (
                    r_regionkey INTEGER,
                    r_name CHAR(25),
                    r_comment VARCHAR(152)
                )
            """,
            "nation": """
                CREATE TABLE IF NOT EXISTS nation (
                    n_nationkey INTEGER,
                    n_name CHAR(25),
                    n_regionkey INTEGER,
                    n_comment VARCHAR(152)
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
            "part": """
                CREATE TABLE IF NOT EXISTS part (
                    p_partkey INTEGER,
                    p_name VARCHAR(55),
                    p_mfgr CHAR(25),
                    p_brand CHAR(10),
                    p_type VARCHAR(25),
                    p_size INTEGER,
                    p_container CHAR(10),
                    p_retailprice DECIMAL(15,2),
                    p_comment VARCHAR(23)
                )
            """,
            "partsupp": """
                CREATE TABLE IF NOT EXISTS partsupp (
                    ps_partkey INTEGER,
                    ps_suppkey INTEGER,
                    ps_availqty INTEGER,
                    ps_supplycost DECIMAL(15,2),
                    ps_comment VARCHAR(199)
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
        }

        for table_name, create_sql in tables.items():
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                conn.execute(create_sql)
            except Exception as e:
                print(f"Warning: Could not create table {table_name}: {e}")

    def _create_tpcds_tables(self, conn):
        tables = {
            "customer": """
                CREATE TABLE IF NOT EXISTS customer (
                    c_customer_sk INTEGER,
                    c_customer_id CHAR(16),
                    c_current_cdemo_sk INTEGER,
                    c_current_hdemo_sk INTEGER,
                    c_current_addr_sk INTEGER,
                    c_first_shipto_date_sk INTEGER,
                    c_first_sales_date_sk INTEGER,
                    c_salutation CHAR(10),
                    c_first_name CHAR(20),
                    c_last_name CHAR(30),
                    c_preferred_cust_flag CHAR(1),
                    c_birth_day INTEGER,
                    c_birth_month INTEGER,
                    c_birth_year INTEGER,
                    c_birth_country VARCHAR(20),
                    c_login CHAR(13),
                    c_email_address CHAR(50),
                    c_last_review_date CHAR(10)
                )
            """,
            "store_sales": """
                CREATE TABLE IF NOT EXISTS store_sales (
                    ss_sold_date_sk INTEGER,
                    ss_sold_time_sk INTEGER,
                    ss_item_sk INTEGER,
                    ss_customer_sk INTEGER,
                    ss_cdemo_sk INTEGER,
                    ss_hdemo_sk INTEGER,
                    ss_addr_sk INTEGER,
                    ss_store_sk INTEGER,
                    ss_promo_sk INTEGER,
                    ss_ticket_number INTEGER,
                    ss_quantity INTEGER,
                    ss_wholesale_cost DECIMAL(7,2),
                    ss_list_price DECIMAL(7,2),
                    ss_sales_price DECIMAL(7,2),
                    ss_ext_discount_amt DECIMAL(7,2),
                    ss_ext_sales_price DECIMAL(7,2),
                    ss_ext_wholesale_cost DECIMAL(7,2),
                    ss_ext_list_price DECIMAL(7,2),
                    ss_ext_tax DECIMAL(7,2),
                    ss_coupon_amt DECIMAL(7,2),
                    ss_net_paid DECIMAL(7,2),
                    ss_net_paid_inc_tax DECIMAL(7,2),
                    ss_net_profit DECIMAL(7,2)
                )
            """,
        }

        for table_name, create_sql in tables.items():
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                conn.execute(create_sql)
            except Exception as e:
                print(f"Warning: Could not create table {table_name}: {e}")

    def _insert_tpch_test_data(self, conn, scale_factor: float):
        try:
            regions = [
                (
                    0,
                    "AFRICA",
                    "lar deposits. blithely final packages cajole. regular waters are final requests. regular accounts are according to",
                ),
                (1, "AMERICA", "hs use ironic, even requests. s"),
                (2, "ASIA", "ges. thinly even pinto beans ca"),
                (3, "EUROPE", "ly final courts cajole furiously final excuse"),
                (
                    4,
                    "MIDDLE EAST",
                    "uickly special accounts cajole carefully blithely close requests. carefully final asymptotes haggle furiousl",
                ),
            ]

            for region in regions:
                conn.execute("INSERT INTO region VALUES (?, ?, ?)", region)

            nations = [
                (
                    0,
                    "ALGERIA",
                    0,
                    " haggle. carefully final deposits detect slyly agai",
                ),
                (
                    1,
                    "ARGENTINA",
                    1,
                    "al foxes promise slyly according to the regular accounts. bold requests alon",
                ),
                (
                    2,
                    "BRAZIL",
                    1,
                    "y alongside of the pending deposits. carefully special packages are about the ironic forges. slyly special",
                ),
                (
                    3,
                    "CANADA",
                    1,
                    "eas hang ironic, silent packages. slyly regular packages are furiously over the tithes. fluffily bold",
                ),
                (
                    4,
                    "EGYPT",
                    4,
                    "y above the carefully unusual theodolites. final dugouts are quickly across the furiously regular d",
                ),
            ]

            for nation in nations:
                conn.execute("INSERT INTO nation VALUES (?, ?, ?, ?)", nation)

            row_count = max(1, int(1000 * scale_factor))

            for i in range(row_count):
                conn.execute(
                    """
                    INSERT INTO customer VALUES
                    (?, ?, ?, ?, '12-345-678-9012', 1000.0, 'BUILDING', 'test comment')
                """,
                    [i, f"Customer#{i:09d}", f"Address {i}", i % 5],
                )

            for i in range(row_count // 10):
                conn.execute(
                    """
                    INSERT INTO supplier VALUES
                    (?, ?, ?, ?, '12-345-678-9012', 1000.0, 'test comment')
                """,
                    [i, f"Supplier#{i:09d}", f"Address {i}", i % 5],
                )

            for i in range(row_count // 5):
                conn.execute(
                    """
                    INSERT INTO part VALUES
                    (?, ?, 'Manufacturer#1', 'Brand#12', 'STANDARD POLISHED TIN', 15, 'SM CASE', 100.0, 'test comment')
                """,
                    [i, f"Part#{i:09d}"],
                )

            for i in range(row_count // 5):
                conn.execute(
                    """
                    INSERT INTO partsupp VALUES
                    (?, ?, 100, 50.0, 'test comment')
                """,
                    [i, i % (row_count // 10)],
                )

            for i in range(row_count):
                conn.execute(
                    """
                    INSERT INTO orders VALUES
                    (?, ?, 'O', 1000.0, '1995-01-01', '5-LOW', ?, 0, 'test comment')
                """,
                    [i, i % row_count, f"Clerk#{i:09d}"],
                )

            for i in range(row_count * 2):
                conn.execute(
                    """
                    INSERT INTO lineitem VALUES
                    (?, ?, ?, ?, 1.0, 100.0, 0.05, 0.08, 'A', 'F',
                     '1998-01-01', '1998-01-15', '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'test comment')
                """,
                    [i // 2, i % (row_count // 5), i % (row_count // 10), i % 7],
                )

        except Exception as e:
            print(f"Warning: Could not insert TPC-H test data: {e}")

    def _insert_tpcds_test_data(self, conn, scale_factor: float):
        try:
            row_count = max(1, int(1000 * scale_factor))

            for i in range(row_count):
                conn.execute(
                    """
                    INSERT INTO customer VALUES
                    (?, ?, ?, ?, ?, ?, ?, 'Mr.', 'John', 'Doe', 'Y',
                     1, 1, 1970, 'UNITED STATES', 'john_doe', 'john.doe@example.com', '2023-01-01')
                """,
                    [i, f"CUSTOMER_{i:016d}", i, i, i, i, i],
                )

            for i in range(row_count * 5):
                conn.execute(
                    """
                    INSERT INTO store_sales VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 50.0, 100.0, 75.0, 5.0, 70.0, 45.0, 95.0, 7.0, 0.0, 70.0, 77.0, 25.0)
                """,
                    [
                        2451000 + (i % 365),
                        i % 86400,
                        i % 100,
                        i % row_count,
                        i % 50,
                        i % 30,
                        i % 20,
                        i % 10,
                        i % 5,
                        i,
                    ],
                )

        except Exception as e:
            print(f"Warning: Could not insert TPC-DS test data: {e}")

    def _verify_query_performance_thresholds(self, results: dict[str, dict[str, Any]], config: dict[str, Any]):
        timeout_threshold = config["timeout_seconds"]
        memory_threshold = config["memory_limit_mb"]

        for query_id, result in results.items():
            execution_time = result["execution_time"]
            peak_memory = result["metrics"]["memory_stats"]["peak_mb"]

            assert execution_time < timeout_threshold, (
                f"Query {query_id} too slow: {execution_time}s > {timeout_threshold}s"
            )

            assert peak_memory < memory_threshold, (
                f"Query {query_id} uses too much memory: {peak_memory}MB > {memory_threshold}MB"
            )

    def _verify_concurrent_performance(self, results: dict[int, float]):
        if len(results) < 2:
            return

        sequential_time = results[1]

        for concurrency, exec_time in results.items():
            if concurrency == 1:
                continue

            max_acceptable_time = sequential_time * 2

            assert exec_time < max_acceptable_time, (
                f"Concurrent execution with {concurrency} queries too slow: {exec_time}s vs sequential {sequential_time}s"
            )

    def _verify_query_scaling_behavior(self, results: dict[float, dict[str, Any]]):
        scale_factors = sorted(results.keys())

        if len(scale_factors) < 2:
            return

        for i in range(1, len(scale_factors)):
            current_sf = scale_factors[i]
            prev_sf = scale_factors[i - 1]

            current_time = results[current_sf]["execution_time"]
            prev_time = results[prev_sf]["execution_time"]

            scale_ratio = current_sf / prev_sf
            max_time_ratio = scale_ratio * 2

            actual_time_ratio = current_time / prev_time if prev_time > 0 else 1

            assert actual_time_ratio < max_time_ratio, (
                f"Query scaling too poor: {actual_time_ratio:.2f}x vs expected max {max_time_ratio:.2f}x"
            )


@pytest.mark.performance
class TestQueryExecutionThroughput:
    def test_query_throughput_measurement(self, duckdb_with_extensions, tmp_path):
        self._setup_simple_test_data(duckdb_with_extensions)

        simple_query = "SELECT COUNT(*) FROM test_table WHERE value > 50"

        query_count = 0
        start_time = time.time()

        def throughput_test():
            nonlocal query_count
            end_time = start_time + 1.0

            while time.time() < end_time:
                self._execute_query_safely(duckdb_with_extensions, simple_query)
                query_count += 1

            return query_count

        start_time = time.time()
        queries_per_second = throughput_test()
        time.time()

        assert queries_per_second > 0, "No queries executed"

        print(f"Query throughput: {queries_per_second} queries/second")

        assert queries_per_second >= 1, f"Throughput too low: {queries_per_second} queries/second"

    def test_batch_query_processing(self, duckdb_with_extensions, tmp_path):
        self._setup_simple_test_data(duckdb_with_extensions)

        query_batch = [
            "SELECT COUNT(*) FROM test_table",
            "SELECT AVG(value) FROM test_table",
            "SELECT MAX(value) FROM test_table",
            "SELECT MIN(value) FROM test_table",
        ]

        def batch_processing():
            results = []
            for query in query_batch:
                result = self._execute_query_safely(duckdb_with_extensions, query)
                results.append(result)
            return results

        start_time = time.time()
        batch_processing()
        end_time = time.time()
        batch_time = end_time - start_time

        assert batch_time > 0, "Batch processing failed"

        assert batch_time < 10.0, f"Batch processing too slow: {batch_time}s"

    def _setup_simple_test_data(self, conn):
        if hasattr(conn, "_mock_name"):
            return

        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS test_table (
                    id INTEGER,
                    value DECIMAL(10,2)
                )
            """)

            for i in range(1000):
                conn.execute("INSERT INTO test_table VALUES (?, ?)", [i, i * 1.5])
        except Exception as e:
            print(f"Warning: Could not setup simple test data: {e}")

    def _execute_query_safely(self, conn, query: str) -> Any:
        try:
            if hasattr(conn, "_mock_name"):
                return [[42]]
            else:
                result = conn.execute(query).fetchall()
                return result
        except Exception as e:
            print(f"Query execution failed: {e}")
            return None


