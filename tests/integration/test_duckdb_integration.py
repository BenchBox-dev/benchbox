# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import csv
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.duckdb
class TestDuckDBConnection:
    def test_memory_database_connection(self, duckdb_memory_db: Any) -> None:

        result = duckdb_memory_db.execute("SELECT 1 as test_value").fetchall()
        assert len(result) == 1
        assert result[0][0] == 1

    def test_database_configuration(self, configured_duckdb: Any, database_config: dict[str, Any]) -> None:

        result = configured_duckdb.execute("SELECT 1 as configured_test").fetchall()
        assert len(result) == 1
        assert result[0][0] == 1

    def test_extensions_setup(self, duckdb_with_extensions: Any) -> None:

        result = duckdb_with_extensions.execute("SELECT 1 as extension_test").fetchall()
        assert len(result) == 1
        assert result[0][0] == 1

    def test_connection_persistence(self, duckdb_memory_db: Any) -> None:

        duckdb_memory_db.execute("CREATE TABLE test_persistence (id INTEGER, value TEXT)")
        duckdb_memory_db.execute("INSERT INTO test_persistence VALUES (1, 'test')")

        result = duckdb_memory_db.execute("SELECT * FROM test_persistence").fetchall()
        assert len(result) == 1
        assert result[0][0] == 1
        assert result[0][1] == "test"

        duckdb_memory_db.execute("DROP TABLE test_persistence")


@pytest.mark.integration
@pytest.mark.duckdb
@pytest.mark.olap
class TestDuckDBOLAPFeatures:
    @pytest.fixture
    def benchmark_with_olap_data(self, sample_data_dir: Path, duckdb_memory_db: Any) -> ReadPrimitivesBenchmark:
        benchmark = ReadPrimitivesBenchmark(scale_factor=0.01, output_dir=sample_data_dir)

        benchmark.tables = {
            "region": str(sample_data_dir / "region.csv"),
            "nation": str(sample_data_dir / "nation.csv"),
            "customer": str(sample_data_dir / "customer.csv"),
            "orders": str(sample_data_dir / "orders.csv"),
            "lineitem": str(sample_data_dir / "lineitem.csv"),
        }

        benchmark.load_data_to_database(duckdb_memory_db, tables=list(benchmark.tables.keys()))

        return benchmark

    def test_window_functions_row_number(
        self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:
        query = """
        SELECT
            c_custkey,
            c_name,
            c_nationkey,
            c_acctbal,
            ROW_NUMBER() OVER (ORDER BY c_acctbal DESC) as rn_balance,
            ROW_NUMBER() OVER (PARTITION BY c_nationkey ORDER BY c_acctbal DESC) as rn_nation
        FROM customer
        ORDER BY c_custkey
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) == 5

        max_balance_customer = max(result, key=lambda x: x[3])
        assert max_balance_customer[4] == 1

        nation_groups = {}
        for row in result:
            nation_key = row[2]
            if nation_key not in nation_groups:
                nation_groups[nation_key] = []
            nation_groups[nation_key].append(row)

        for nation_key, rows in nation_groups.items():
            nation_rn_values = [row[5] for row in rows]
            assert min(nation_rn_values) == 1

    def test_window_functions_rank_dense_rank(
        self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:
        query = """
        SELECT
            c_custkey,
            c_acctbal,
            RANK() OVER (ORDER BY c_acctbal DESC) as rank_balance,
            DENSE_RANK() OVER (ORDER BY c_acctbal DESC) as dense_rank_balance
        FROM customer
        ORDER BY c_acctbal DESC
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) == 5

        assert result[0][2] == 1
        assert result[0][3] == 1

        for row in result:
            assert row[2] >= 1
            assert row[3] >= 1

    def test_window_functions_lag_lead(
        self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:
        query = """
        SELECT
            c_custkey,
            c_acctbal,
            LAG(c_acctbal, 1) OVER (ORDER BY c_custkey) as prev_balance,
            LEAD(c_acctbal, 1) OVER (ORDER BY c_custkey) as next_balance,
            c_acctbal - LAG(c_acctbal, 1) OVER (ORDER BY c_custkey) as balance_diff
        FROM customer
        ORDER BY c_custkey
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) == 5

        assert result[0][2] is None

        assert result[-1][3] is None

        for i in range(1, len(result) - 1):
            assert result[i][2] is not None
            assert result[i][3] is not None

    def test_window_functions_aggregate_windows(
        self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:
        query = """
        SELECT
            c_custkey,
            c_acctbal,
            SUM(c_acctbal) OVER (ORDER BY c_custkey ROWS UNBOUNDED PRECEDING) as running_sum,
            AVG(c_acctbal) OVER (ORDER BY c_custkey ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) as moving_avg,
            COUNT(*) OVER (ORDER BY c_custkey ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) as rolling_count
        FROM customer
        ORDER BY c_custkey
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) == 5

        running_sums = [row[2] for row in result]
        for i in range(1, len(running_sums)):
            assert running_sums[i] >= running_sums[i - 1]

        for row in result:
            assert row[2] > 0
            assert row[3] > 0
            assert row[4] > 0

    def test_cte_basic(self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any) -> None:
        query = """
        WITH customer_summary AS (
            SELECT
                c_nationkey,
                COUNT(*) as customer_count,
                AVG(c_acctbal) as avg_balance,
                MAX(c_acctbal) as max_balance
            FROM customer
            GROUP BY c_nationkey
        )
        SELECT
            n.n_name,
            cs.customer_count,
            cs.avg_balance,
            cs.max_balance
        FROM customer_summary cs
        JOIN nation n ON cs.c_nationkey = n.n_nationkey
        ORDER BY cs.customer_count DESC
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) > 0

        for row in result:
            assert row[1] > 0
            assert row[2] > 0
            assert row[3] > 0

    def test_cte_recursive(self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any) -> None:
        query = """
        WITH RECURSIVE order_hierarchy AS (
            SELECT
                o_orderkey,
                o_custkey,
                o_totalprice,
                1 as level
            FROM orders
            WHERE o_orderkey <= 2

            UNION ALL

            SELECT
                o.o_orderkey,
                o.o_custkey,
                o.o_totalprice,
                oh.level + 1
            FROM orders o
            JOIN order_hierarchy oh ON o.o_custkey = oh.o_custkey
            WHERE oh.level < 2 AND o.o_orderkey > oh.o_orderkey
        )
        SELECT
            o_orderkey,
            o_custkey,
            o_totalprice,
            level
        FROM order_hierarchy
        ORDER BY o_orderkey
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) >= 2

        levels = [row[3] for row in result]
        assert min(levels) == 1
        assert max(levels) <= 2

    def test_cte_multiple(self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any) -> None:
        query = """
        WITH customer_stats AS (
            SELECT
                c_nationkey,
                COUNT(*) as customer_count,
                AVG(c_acctbal) as avg_balance
            FROM customer
            GROUP BY c_nationkey
        ),
        order_stats AS (
            SELECT
                c.c_nationkey,
                COUNT(o.o_orderkey) as order_count,
                AVG(o.o_totalprice) as avg_order_value
            FROM customer c
            LEFT JOIN orders o ON c.c_custkey = o.o_custkey
            GROUP BY c.c_nationkey
        )
        SELECT
            n.n_name,
            cs.customer_count,
            cs.avg_balance,
            os.order_count,
            os.avg_order_value
        FROM customer_stats cs
        JOIN order_stats os ON cs.c_nationkey = os.c_nationkey
        JOIN nation n ON cs.c_nationkey = n.n_nationkey
        ORDER BY cs.customer_count DESC
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) > 0

        for row in result:
            assert row[1] >= 0
            assert row[2] >= 0
            assert row[3] >= 0

    def test_grouping_sets(self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any) -> None:

        query = """
        SELECT
            c_nationkey,
            c_mktsegment,
            COUNT(*) as customer_count,
            AVG(c_acctbal) as avg_balance,
            GROUPING(c_nationkey) as nation_grouping,
            GROUPING(c_mktsegment) as segment_grouping
        FROM customer
        GROUP BY GROUPING SETS (
            (c_nationkey, c_mktsegment),
            (c_nationkey),
            (c_mktsegment),
            ()
        )
        ORDER BY nation_grouping, segment_grouping, c_nationkey, c_mktsegment
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) > 5

        for row in result:
            assert row[4] in [0, 1]
            assert row[5] in [0, 1]
            assert row[2] > 0

    def test_rollup(self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any) -> None:

        query = """
        SELECT
            c_nationkey,
            c_mktsegment,
            COUNT(*) as customer_count,
            SUM(c_acctbal) as total_balance
        FROM customer
        GROUP BY ROLLUP (c_nationkey, c_mktsegment)
        ORDER BY c_nationkey, c_mktsegment
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) > 5

        null_nations = [row for row in result if row[0] is None]
        null_segments = [row for row in result if row[1] is None]

        assert len(null_nations) > 0
        assert len(null_segments) > 0

    def test_cube(self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any) -> None:

        query = """
        SELECT
            c_nationkey,
            c_mktsegment,
            COUNT(*) as customer_count,
            ROUND(AVG(c_acctbal), 2) as avg_balance
        FROM customer
        GROUP BY CUBE (c_nationkey, c_mktsegment)
        ORDER BY c_nationkey, c_mktsegment
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) > 5

        grand_total = [row for row in result if row[0] is None and row[1] is None]
        assert len(grand_total) == 1
        assert grand_total[0][2] == 5

    def test_filter_clause_in_aggregation(
        self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        query = """
        SELECT
            c_nationkey,
            COUNT(*) as total_customers,
            COUNT(*) FILTER (WHERE c_acctbal > 1000) as high_balance_customers,
            COUNT(*) FILTER (WHERE c_mktsegment = 'AUTOMOBILE') as auto_customers,
            AVG(c_acctbal) FILTER (WHERE c_acctbal > 1000) as avg_high_balance,
            SUM(c_acctbal) FILTER (WHERE c_mktsegment = 'BUILDING') as building_total_balance
        FROM customer
        GROUP BY c_nationkey
        ORDER BY c_nationkey
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) > 0

        for row in result:
            total_customers = row[1]
            high_balance_customers = row[2]
            auto_customers = row[3]

            assert total_customers >= high_balance_customers
            assert total_customers >= auto_customers
            assert high_balance_customers >= 0
            assert auto_customers >= 0

    def test_advanced_analytical_query(
        self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        query = """
        WITH customer_metrics AS (
            SELECT
                c.c_custkey,
                c.c_nationkey,
                c.c_acctbal,
                c.c_mktsegment,
                COUNT(o.o_orderkey) as order_count,
                SUM(o.o_totalprice) as total_spent,
                ROW_NUMBER() OVER (PARTITION BY c.c_nationkey ORDER BY c.c_acctbal DESC) as balance_rank
            FROM customer c
            LEFT JOIN orders o ON c.c_custkey = o.o_custkey
            GROUP BY c.c_custkey, c.c_nationkey, c.c_acctbal, c.c_mktsegment
        ),
        nation_summary AS (
            SELECT
                c_nationkey,
                COUNT(*) as customers_in_nation,
                AVG(c_acctbal) as avg_nation_balance,
                SUM(total_spent) FILTER (WHERE order_count > 0) as nation_total_spent
            FROM customer_metrics
            GROUP BY c_nationkey
        )
        SELECT
            n.n_name,
            cm.c_custkey,
            cm.c_acctbal,
            cm.balance_rank,
            cm.order_count,
            cm.total_spent,
            ns.customers_in_nation,
            ns.avg_nation_balance,
            CASE
                WHEN cm.c_acctbal > ns.avg_nation_balance THEN 'Above Average'
                ELSE 'Below Average'
            END as balance_category
        FROM customer_metrics cm
        JOIN nation_summary ns ON cm.c_nationkey = ns.c_nationkey
        JOIN nation n ON cm.c_nationkey = n.n_nationkey
        WHERE cm.balance_rank <= 2
        ORDER BY n.n_name, cm.balance_rank
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) > 0

        ranks = [row[3] for row in result]
        assert all(rank in [1, 2] for rank in ranks)

        categories = [row[8] for row in result]
        assert all(cat in ["Above Average", "Below Average"] for cat in categories)

    def test_olap_with_joins(self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any) -> None:

        query = """
        SELECT
            r.r_name,
            n.n_name,
            c.c_custkey,
            c.c_acctbal,
            o.o_orderkey,
            o.o_totalprice,
            SUM(o.o_totalprice) OVER (PARTITION BY r.r_regionkey) as region_total_orders,
            AVG(c.c_acctbal) OVER (PARTITION BY r.r_regionkey) as region_avg_balance,
            ROW_NUMBER() OVER (PARTITION BY r.r_regionkey ORDER BY o.o_totalprice DESC) as region_order_rank
        FROM region r
        JOIN nation n ON r.r_regionkey = n.n_regionkey
        JOIN customer c ON n.n_nationkey = c.c_nationkey
        LEFT JOIN orders o ON c.c_custkey = o.o_custkey
        WHERE o.o_orderkey IS NOT NULL
        ORDER BY r.r_name, region_order_rank
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) > 0

        for row in result:
            assert row[6] > 0
            assert row[7] > 0
            assert row[8] >= 1

    def test_olap_null_handling(self, benchmark_with_olap_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any) -> None:

        query = """
        SELECT
            c.c_custkey,
            c.c_acctbal,
            o.o_totalprice,
            SUM(o.o_totalprice) OVER (ORDER BY c.c_custkey ROWS UNBOUNDED PRECEDING) as running_total,
            COUNT(o.o_orderkey) OVER (ORDER BY c.c_custkey ROWS UNBOUNDED PRECEDING) as running_count,
            FIRST_VALUE(o.o_totalprice) OVER (ORDER BY c.c_custkey ROWS UNBOUNDED PRECEDING) as first_order_value,
            LAST_VALUE(o.o_totalprice) OVER (ORDER BY c.c_custkey ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) as last_order_value
        FROM customer c
        LEFT JOIN orders o ON c.c_custkey = o.o_custkey
        ORDER BY c.c_custkey
        """

        result = duckdb_memory_db.execute(query).fetchall()

        assert len(result) == 5

        for row in result:
            assert row[4] >= 0

            if row[2] is None:
                assert row[3] is None or row[3] == 0


@pytest.mark.integration
@pytest.mark.duckdb
class TestBenchmarkExecution:
    @pytest.fixture
    def benchmark_with_full_data(self, detailed_data_dir: Path, duckdb_memory_db: Any) -> ReadPrimitivesBenchmark:
        benchmark = ReadPrimitivesBenchmark(scale_factor=0.01, output_dir=detailed_data_dir)

        benchmark.tables = {
            "region": str(detailed_data_dir / "region.csv"),
            "nation": str(detailed_data_dir / "nation.csv"),
            "supplier": str(detailed_data_dir / "supplier.csv"),
            "part": str(detailed_data_dir / "part.csv"),
            "customer": str(detailed_data_dir / "customer.csv"),
            "orders": str(detailed_data_dir / "orders.csv"),
            "partsupp": str(detailed_data_dir / "partsupp.csv"),
            "lineitem": str(detailed_data_dir / "lineitem.csv"),
        }

        benchmark.load_data_to_database(duckdb_memory_db, tables=list(benchmark.tables.keys()))

        return benchmark

    def test_primitives_aggregation_queries(
        self, benchmark_with_full_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        aggregation_queries = [
            "aggregation_distinct",
            "aggregation_distinct_groupby",
            "aggregation_groupby_large",
            "aggregation_groupby_small",
        ]

        results = {}
        for query_id in aggregation_queries:
            try:
                query_sql = benchmark_with_full_data.get_query(query_id)
                start_time = time.time()
                result = duckdb_memory_db.execute(query_sql).fetchall()
                end_time = time.time()

                results[query_id] = {
                    "success": True,
                    "rows": len(result),
                    "execution_time": end_time - start_time,
                    "result": result,
                }
            except Exception as e:
                results[query_id] = {
                    "success": False,
                    "error": str(e),
                    "execution_time": 0,
                }

        successful_queries = [q for q, r in results.items() if r["success"]]
        assert len(successful_queries) >= 3, f"Expected at least 3 successful queries, got {len(successful_queries)}"

        for query_id, result in results.items():
            if result["success"]:
                assert result["execution_time"] < 5.0, (
                    f"Query {query_id} took too long: {result['execution_time']:.2f}s"
                )
                assert result["rows"] >= 0, f"Query {query_id} returned negative row count"

    def test_primitives_join_queries(
        self, benchmark_with_full_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        join_queries = [
            """
            SELECT
                r.r_name,
                n.n_name,
                COUNT(c.c_custkey) as customer_count
            FROM region r
            JOIN nation n ON r.r_regionkey = n.n_regionkey
            LEFT JOIN customer c ON n.n_nationkey = c.c_nationkey
            GROUP BY r.r_name, n.n_name
            ORDER BY customer_count DESC
            """,
            """
            SELECT
                s.s_name,
                p.p_name,
                ps.ps_supplycost
            FROM supplier s
            JOIN partsupp ps ON s.s_suppkey = ps.ps_suppkey
            JOIN part p ON ps.ps_partkey = p.p_partkey
            WHERE ps.ps_supplycost > 500
            ORDER BY ps.ps_supplycost DESC
            """,
            """
            SELECT
                c.c_name,
                o.o_orderkey,
                l.l_quantity,
                l.l_extendedprice
            FROM customer c
            JOIN orders o ON c.c_custkey = o.o_custkey
            JOIN lineitem l ON o.o_orderkey = l.l_orderkey
            WHERE l.l_quantity > 20
            ORDER BY l.l_extendedprice DESC
            """,
        ]

        results = {}
        for i, query_sql in enumerate(join_queries):
            query_id = f"join_query_{i + 1}"
            try:
                start_time = time.time()
                result = duckdb_memory_db.execute(query_sql).fetchall()
                end_time = time.time()

                results[query_id] = {
                    "success": True,
                    "rows": len(result),
                    "execution_time": end_time - start_time,
                    "result": result,
                }
            except Exception as e:
                results[query_id] = {
                    "success": False,
                    "error": str(e),
                    "execution_time": 0,
                }

        successful_queries = [q for q, r in results.items() if r["success"]]
        assert len(successful_queries) == len(join_queries), (
            f"Expected all join queries to succeed, got {len(successful_queries)}"
        )

        for query_id, result in results.items():
            if result["success"]:
                assert result["execution_time"] < 5.0, (
                    f"Query {query_id} took too long: {result['execution_time']:.2f}s"
                )
                assert result["rows"] >= 0, f"Query {query_id} returned negative row count"

    def test_primitives_filter_queries(
        self, benchmark_with_full_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        filter_queries = [
            """
            SELECT c_custkey, c_name, c_acctbal
            FROM customer
            WHERE c_acctbal > 1000
            ORDER BY c_acctbal DESC
            """,
            """
            SELECT l_orderkey, l_partkey, l_quantity, l_extendedprice
            FROM lineitem
            WHERE l_shipdate >= '1995-01-01' AND l_shipdate < '1996-01-01'
            ORDER BY l_extendedprice DESC
            """,
            """
            SELECT p_partkey, p_name, p_retailprice
            FROM part
            WHERE p_name LIKE '%blue%' OR p_name LIKE '%green%'
            ORDER BY p_retailprice
            """,
            """
            SELECT n_name, n_regionkey
            FROM nation
            WHERE n_regionkey IN (1, 2, 3)
            ORDER BY n_name
            """,
        ]

        results = {}
        for i, query_sql in enumerate(filter_queries):
            query_id = f"filter_query_{i + 1}"
            try:
                start_time = time.time()
                result = duckdb_memory_db.execute(query_sql).fetchall()
                end_time = time.time()

                results[query_id] = {
                    "success": True,
                    "rows": len(result),
                    "execution_time": end_time - start_time,
                    "result": result,
                }
            except Exception as e:
                results[query_id] = {
                    "success": False,
                    "error": str(e),
                    "execution_time": 0,
                }

        successful_queries = [q for q, r in results.items() if r["success"]]
        assert len(successful_queries) == len(filter_queries), (
            f"Expected all filter queries to succeed, got {len(successful_queries)}"
        )

        for query_id, result in results.items():
            if result["success"]:
                assert result["execution_time"] < 5.0, (
                    f"Query {query_id} took too long: {result['execution_time']:.2f}s"
                )
                assert result["rows"] >= 0, f"Query {query_id} returned negative row count"

    def test_primitives_window_queries(
        self, benchmark_with_full_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        window_queries = [
            """
            SELECT
                c_custkey,
                c_acctbal,
                ROW_NUMBER() OVER (ORDER BY c_acctbal DESC) as balance_rank,
                RANK() OVER (ORDER BY c_acctbal DESC) as balance_rank_ties,
                DENSE_RANK() OVER (ORDER BY c_acctbal DESC) as balance_dense_rank
            FROM customer
            ORDER BY c_custkey
            """,
            """
            SELECT
                l_orderkey,
                l_quantity,
                l_extendedprice,
                SUM(l_quantity) OVER (PARTITION BY l_orderkey ORDER BY l_linenumber) as running_qty,
                AVG(l_extendedprice) OVER (PARTITION BY l_orderkey) as avg_line_price
            FROM lineitem
            ORDER BY l_orderkey, l_linenumber
            """,
            """
            SELECT
                o_orderkey,
                o_custkey,
                o_totalprice,
                LAG(o_totalprice, 1) OVER (PARTITION BY o_custkey ORDER BY o_orderdate) as prev_order_total,
                LEAD(o_totalprice, 1) OVER (PARTITION BY o_custkey ORDER BY o_orderdate) as next_order_total
            FROM orders
            ORDER BY o_custkey, o_orderdate
            """,
        ]

        results = {}
        for i, query_sql in enumerate(window_queries):
            query_id = f"window_query_{i + 1}"
            try:
                start_time = time.time()
                result = duckdb_memory_db.execute(query_sql).fetchall()
                end_time = time.time()

                results[query_id] = {
                    "success": True,
                    "rows": len(result),
                    "execution_time": end_time - start_time,
                    "result": result,
                }
            except Exception as e:
                results[query_id] = {
                    "success": False,
                    "error": str(e),
                    "execution_time": 0,
                }

        successful_queries = [q for q, r in results.items() if r["success"]]
        assert len(successful_queries) == len(window_queries), (
            f"Expected all window queries to succeed, got {len(successful_queries)}"
        )

        for query_id, result in results.items():
            if result["success"]:
                assert result["execution_time"] < 5.0, (
                    f"Query {query_id} took too long: {result['execution_time']:.2f}s"
                )
                assert result["rows"] >= 0, f"Query {query_id} returned negative row count"

    def test_benchmark_run_integration(
        self, benchmark_with_full_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        test_queries = ["aggregation_distinct", "aggregation_groupby_small"]

        start_time = time.time()
        result = benchmark_with_full_data.run_benchmark(duckdb_memory_db, queries=test_queries, iterations=2)
        end_time = time.time()

        assert "benchmark" in result
        assert "scale_factor" in result
        assert "iterations" in result
        assert "queries" in result

        assert result["benchmark"] == "Read Primitives"
        assert result["scale_factor"] == 0.01
        assert result["iterations"] == 2

        for query_id in test_queries:
            assert query_id in result["queries"], f"Query {query_id} missing from results"
            query_result = result["queries"][query_id]

            assert "query_id" in query_result
            assert "iterations" in query_result
            assert "avg_time" in query_result
            assert "min_time" in query_result
            assert "max_time" in query_result

            assert len(query_result["iterations"]) == 2
            assert query_result["avg_time"] > 0
            assert query_result["min_time"] > 0
            assert query_result["max_time"] > 0

        total_time = end_time - start_time
        assert total_time < 10.0, f"Full benchmark took too long: {total_time:.2f}s"

    def test_error_handling_and_recovery(
        self, benchmark_with_full_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        invalid_queries = [
            "SELECT * FROM nonexistent_table",
            "SELECT invalid_column FROM customer",
            "SELECT * FROM customer WHERE invalid_syntax",
        ]

        for i, invalid_query in enumerate(invalid_queries):
            query_id = f"invalid_query_{i + 1}"
            try:
                result = duckdb_memory_db.execute(invalid_query).fetchall()
                raise AssertionError(f"Expected query {query_id} to fail, but it succeeded")
            except Exception as e:
                assert len(str(e)) > 0, "Error message should not be empty"

        valid_query = "SELECT COUNT(*) FROM customer"
        result = duckdb_memory_db.execute(valid_query).fetchall()
        assert len(result) == 1
        assert result[0][0] == 10

    def test_concurrent_execution(
        self, benchmark_with_full_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        def execute_query(query_id: str) -> dict[str, Any]:
            try:
                start_time = time.time()
                result = duckdb_memory_db.execute("SELECT COUNT(*) FROM customer").fetchall()
                end_time = time.time()
                return {
                    "query_id": query_id,
                    "success": True,
                    "result": result,
                    "execution_time": end_time - start_time,
                }
            except Exception as e:
                return {
                    "query_id": query_id,
                    "success": False,
                    "error": str(e),
                    "execution_time": 0,
                }

        query_ids = [f"concurrent_query_{i}" for i in range(3)]
        results = []

        for query_id in query_ids:
            result = execute_query(query_id)
            results.append(result)

        successful_results = [r for r in results if r["success"]]
        assert len(successful_results) == len(query_ids)

        for result in successful_results:
            assert result["result"][0][0] == 10
            assert result["execution_time"] < 1.0

    def test_large_result_set_handling(
        self, benchmark_with_full_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        large_query = """
        SELECT
            l.l_orderkey,
            l.l_partkey,
            l.l_suppkey,
            l.l_linenumber,
            l.l_quantity,
            l.l_extendedprice,
            p.p_name,
            s.s_name,
            c.c_name
        FROM lineitem l
        JOIN part p ON l.l_partkey = p.p_partkey
        JOIN supplier s ON l.l_suppkey = s.s_suppkey
        JOIN orders o ON l.l_orderkey = o.o_orderkey
        JOIN customer c ON o.o_custkey = c.c_custkey
        ORDER BY l.l_extendedprice DESC
        """

        start_time = time.time()
        result = duckdb_memory_db.execute(large_query).fetchall()
        end_time = time.time()

        execution_time = end_time - start_time

        assert len(result) > 0

        assert execution_time < 10.0, f"Large query took too long: {execution_time:.2f}s"

        for row in result:
            assert len(row) == 9

    def test_benchmark_category_execution(
        self, benchmark_with_full_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        try:
            result = benchmark_with_full_data.run_category_benchmark(duckdb_memory_db, "aggregation", iterations=1)

            assert "benchmark" in result
            assert "queries" in result
            assert "categories" in result
            assert result["categories"] == ["aggregation"]

            assert len(result["queries"]) > 0

            for _query_id, query_result in result["queries"].items():
                assert query_result["category"] == "aggregation"

        except Exception as e:
            if "No queries found" not in str(e):
                raise e

    def test_data_type_handling(self, benchmark_with_full_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any) -> None:

        data_type_query = """
        SELECT
            c_custkey,
            c_name,
            c_acctbal,
            c_phone
        FROM customer
        WHERE c_custkey = 1
        """

        result = duckdb_memory_db.execute(data_type_query).fetchall()

        assert len(result) == 1
        row = result[0]

        from decimal import Decimal

        assert isinstance(row[0], int)
        assert isinstance(row[1], str)
        assert isinstance(row[2], (int, float, Decimal))
        assert isinstance(row[3], str)

        date_query = """
        SELECT
            l_shipdate,
            l_commitdate,
            l_receiptdate
        FROM lineitem
        WHERE l_orderkey = 1
        LIMIT 1
        """

        result = duckdb_memory_db.execute(date_query).fetchall()

        assert len(result) == 1
        import datetime

        for date_val in result[0]:
            if date_val is not None:
                assert isinstance(date_val, (str, datetime.date))
                if isinstance(date_val, str):
                    assert len(date_val) == 10

    def test_benchmark_info_integration(
        self, benchmark_with_full_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        info = benchmark_with_full_data.get_benchmark_info()

        assert info["name"] == "Read Primitives Benchmark"
        assert info["scale_factor"] == 0.01
        assert info["schema"] == "TPC-H"
        assert len(info["tables"]) == 8

        for table_name in info["tables"]:
            count_query = f"SELECT COUNT(*) FROM {table_name}"
            result = duckdb_memory_db.execute(count_query).fetchall()
            assert len(result) == 1
            assert result[0][0] >= 0


@pytest.mark.integration
@pytest.mark.duckdb
class TestPerformanceValidation:
    @pytest.fixture
    def simple_data_dir(self) -> Path:
        temp_dir = Path(tempfile.mkdtemp())

        customer_file = temp_dir / "customer.csv"
        customer_data = [
            [
                "1",
                "Customer#000000001",
                "Address1",
                "1",
                "25-989-741-2988",
                "711.56",
                "BUILDING",
                "comment1",
            ],
            [
                "2",
                "Customer#000000002",
                "Address2",
                "2",
                "23-768-687-3665",
                "121.65",
                "AUTOMOBILE",
                "comment2",
            ],
            [
                "3",
                "Customer#000000003",
                "Address3",
                "3",
                "11-719-748-3364",
                "7498.12",
                "AUTOMOBILE",
                "comment3",
            ],
            [
                "4",
                "Customer#000000004",
                "Address4",
                "4",
                "14-128-190-5944",
                "2866.83",
                "MACHINERY",
                "comment4",
            ],
            [
                "5",
                "Customer#000000005",
                "Address5",
                "5",
                "13-750-942-6364",
                "794.47",
                "HOUSEHOLD",
                "comment5",
            ],
        ]

        with open(customer_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="|")
            writer.writerows(customer_data)

        return temp_dir

    @pytest.fixture
    def benchmark_with_simple_data(self, simple_data_dir: Path, duckdb_memory_db: Any) -> ReadPrimitivesBenchmark:
        benchmark = ReadPrimitivesBenchmark(scale_factor=0.01, output_dir=simple_data_dir)

        benchmark.tables = {"customer": str(simple_data_dir / "customer.csv")}

        benchmark.load_data_to_database(duckdb_memory_db, tables=list(benchmark.tables.keys()))

        return benchmark

    def test_performance_validation(
        self, benchmark_with_simple_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        simple_query = "SELECT COUNT(*) FROM customer"

        times = []
        for _i in range(5):
            start_time = time.time()
            result = duckdb_memory_db.execute(simple_query).fetchall()
            end_time = time.time()
            times.append(end_time - start_time)

        assert len(result) == 1
        assert result[0][0] == 5

        avg_time = sum(times) / len(times)
        max_time = max(times)
        min(times)

        assert avg_time < 0.1, f"Simple query took too long on average: {avg_time:.4f}s"
        assert max_time < 0.5, f"Simple query took too long in worst case: {max_time:.4f}s"

        variance = sum((t - avg_time) ** 2 for t in times) / len(times)
        assert variance < 0.01, f"Timing variance too high: {variance:.4f}"

    def test_memory_usage_monitoring(
        self, benchmark_with_simple_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024

        queries = [
            "SELECT COUNT(*) FROM customer",
            "SELECT AVG(c_acctbal) FROM customer",
            "SELECT c_mktsegment, COUNT(*) FROM customer GROUP BY c_mktsegment",
        ]

        for query in queries:
            result = duckdb_memory_db.execute(query).fetchall()
            assert len(result) >= 1

        final_memory = process.memory_info().rss / 1024 / 1024
        memory_increase = final_memory - initial_memory

        assert memory_increase < 100, f"Memory usage increased too much: {memory_increase:.2f}MB"

    def test_olap_performance_characteristics(
        self, benchmark_with_simple_data: ReadPrimitivesBenchmark, duckdb_memory_db: Any
    ) -> None:

        query = """
        SELECT
            c_custkey,
            c_acctbal,
            ROW_NUMBER() OVER (ORDER BY c_acctbal DESC) as rn,
            DENSE_RANK() OVER (ORDER BY c_acctbal DESC) as global_rank,
            PERCENT_RANK() OVER (ORDER BY c_acctbal) as pct_rank,
            LAG(c_acctbal) OVER (ORDER BY c_acctbal) as prev_balance,
            LEAD(c_acctbal) OVER (ORDER BY c_acctbal) as next_balance,
            SUM(c_acctbal) OVER () as total_balance
        FROM customer
        ORDER BY global_rank
        """

        start_time = time.time()
        result = duckdb_memory_db.execute(query).fetchall()
        end_time = time.time()

        execution_time = end_time - start_time

        assert execution_time < 1.0

        assert len(result) == 5

        for row in result:
            assert row[1] > 0
            assert row[2] >= 1
            assert row[3] >= 1
            assert 0 <= row[4] <= 1
            assert row[7] > 0
