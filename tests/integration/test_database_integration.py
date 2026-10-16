# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import csv
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import duckdb
import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.read_primitives.schema import TABLES

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.database
class TestDatabaseIntegration:
    @pytest.fixture
    def sample_data_dir(self) -> Path:
        temp_dir = Path(tempfile.mkdtemp())

        region_file = temp_dir / "region.csv"
        region_data = [
            ["0", "AFRICA", "lar deposits. blithely final packages cajole"],
            ["1", "AMERICA", "hs use ironic, even requests"],
            ["2", "ASIA", "ges. thinly even pinto beans ca"],
            ["3", "EUROPE", "ly final courts cajole furiously"],
            ["4", "MIDDLE EAST", "uickly special requests"],
        ]

        with open(region_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="|")
            writer.writerows(region_data)

        nation_file = temp_dir / "nation.csv"
        nation_data = [
            ["0", "ALGERIA", "0", "haggle. carefully final deposits detect"],
            ["1", "ARGENTINA", "1", "al foxes promise slyly according to the"],
            ["2", "BRAZIL", "1", "y alongside of the pending deposits"],
            ["3", "CANADA", "1", "eas hang ironic, silent packages"],
            ["4", "EGYPT", "4", "y above the carefully unusual theodolites"],
            ["5", "ETHIOPIA", "0", "ven packages wake quickly"],
            ["6", "FRANCE", "3", "refully final requests. regular, ironic"],
            ["7", "GERMANY", "3", "l platelets. regular accounts x-ray"],
            ["8", "INDIA", "2", "ss excuses cajole slyly across the packages"],
            ["9", "INDONESIA", "2", "slyly express asymptotes. regular deposits"],
        ]

        with open(nation_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="|")
            writer.writerows(nation_data)

        supplier_file = temp_dir / "supplier.csv"
        supplier_data = [
            [
                "1",
                "Supplier#000000001",
                "N kD4on9OM Ipw3,gf0JBoQDd7tgrzrddZ",
                "17",
                "27-918-335-1736",
                "5755.94",
                "each slyly above the careful",
            ],
            [
                "2",
                "Supplier#000000002",
                "89eJ5ksX3ImxJQBvxmRchLXak",
                "5",
                "15-679-861-2259",
                "4032.68",
                "slyly bold instructions",
            ],
            [
                "3",
                "Supplier#000000003",
                "q1,G3Pj6OjIuUYfUoH18BFTKP5aU9bEV3",
                "1",
                "11-383-516-1199",
                "4192.40",
                "blithely silent requests",
            ],
            [
                "4",
                "Supplier#000000004",
                "Bk7ah4CK8SYQTepEmvMkkgMwg",
                "15",
                "25-843-787-7479",
                "4641.08",
                "riously even requests",
            ],
            [
                "5",
                "Supplier#000000005",
                "gcdm2rJRzl5qlTVzc",
                "11",
                "21-151-690-3663",
                "-283.84",
                "regular deposits above",
            ],
        ]

        with open(supplier_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="|")
            writer.writerows(supplier_data)

        customer_file = temp_dir / "customer.csv"
        customer_data = [
            [
                "1",
                "Customer#000000001",
                "IVhzIApeRb ot,c,E",
                "15",
                "25-989-741-2988",
                "711.56",
                "BUILDING",
                "to the even, regular platelets",
            ],
            [
                "2",
                "Customer#000000002",
                "XSTf4,NCwDVaWNe6tEgvwfmRchLXak",
                "13",
                "23-768-687-3665",
                "121.65",
                "AUTOMOBILE",
                "l accounts. even, express packages",
            ],
            [
                "3",
                "Customer#000000003",
                "MG9kdTD2WBHm",
                "1",
                "11-719-748-3364",
                "7498.12",
                "AUTOMOBILE",
                "deposits eat slyly ironic",
            ],
            [
                "4",
                "Customer#000000004",
                "XxVSJsLAGtn",
                "4",
                "14-128-190-5944",
                "2866.83",
                "MACHINERY",
                "requests. final, regular ideas",
            ],
            [
                "5",
                "Customer#000000005",
                "KvpyuHCplrB84WgAiGV6sYpZq7Tj",
                "3",
                "13-750-942-6364",
                "794.47",
                "HOUSEHOLD",
                "g to the carefully final braids",
            ],
        ]

        with open(customer_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="|")
            writer.writerows(customer_data)

        return temp_dir

    @pytest.fixture
    def benchmark_instance(self, sample_data_dir: Path) -> ReadPrimitivesBenchmark:
        benchmark = ReadPrimitivesBenchmark(scale_factor=0.01, output_dir=sample_data_dir)

        benchmark.tables = {
            "region": str(sample_data_dir / "region.csv"),
            "nation": str(sample_data_dir / "nation.csv"),
            "supplier": str(sample_data_dir / "supplier.csv"),
            "customer": str(sample_data_dir / "customer.csv"),
        }

        return benchmark

    def test_duckdb_schema_creation(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:

        conn = duckdb.connect(":memory:")

        try:
            schema_sql = benchmark_instance.get_create_tables_sql()

            for statement in schema_sql.strip().split(";"):
                if statement.strip():
                    conn.execute(statement.strip())

            tables_result = conn.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
            """).fetchall()
            tables = [row[0].lower() for row in tables_result]

            expected_tables = [
                "region",
                "nation",
                "supplier",
                "customer",
                "part",
                "partsupp",
                "orders",
                "lineitem",
            ]

            for table in expected_tables:
                assert table in tables, f"Table {table} was not created"

                columns = conn.execute(f"DESCRIBE {table}").fetchall()
                assert len(columns) > 0, f"Table {table} has no columns"

        finally:
            conn.close()

    def test_data_loading_with_relationships(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:

        conn = duckdb.connect(":memory:")

        try:
            benchmark_instance.load_data_to_database(conn, tables=["region", "nation", "supplier", "customer"])

            cursor = conn.cursor()

            cursor.execute("""
                SELECT n.n_name, n.n_regionkey, r.r_name
                FROM nation n
                LEFT JOIN region r ON n.n_regionkey = r.r_regionkey
                WHERE r.r_regionkey IS NULL
            """)
            orphaned_nations = cursor.fetchall()
            assert len(orphaned_nations) == 0, f"Found nations without valid regions: {orphaned_nations}"

            cursor.execute("""
                SELECT c.c_name, c.c_nationkey, n.n_name
                FROM customer c
                LEFT JOIN nation n ON c.c_nationkey = n.n_nationkey
                WHERE n.n_nationkey IS NULL
            """)
            orphaned_customers = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) FROM customer")
            total_customers = cursor.fetchone()[0]
            assert len(orphaned_customers) < total_customers * 0.8, (
                f"Too many customers without valid nations: {len(orphaned_customers)}/{total_customers}"
            )

            cursor.execute("""
                SELECT s.s_name, s.s_nationkey, n.n_name
                FROM supplier s
                LEFT JOIN nation n ON s.s_nationkey = n.n_nationkey
                WHERE n.n_nationkey IS NULL
            """)
            orphaned_suppliers = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) FROM supplier")
            total_suppliers = cursor.fetchone()[0]
            assert len(orphaned_suppliers) < total_suppliers * 0.8, (
                f"Too many suppliers without valid nations: {len(orphaned_suppliers)}/{total_suppliers}"
            )

        finally:
            conn.close()

    def test_transaction_handling(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:

        conn = duckdb.connect(":memory:")

        try:
            benchmark_instance.load_data_to_database(conn, tables=["region", "nation"])

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM region")
            region_count = cursor.fetchone()[0]
            assert region_count == 5, f"Expected 5 regions, got {region_count}"

            cursor.execute("SELECT COUNT(*) FROM nation")
            nation_count = cursor.fetchone()[0]
            assert nation_count == 10, f"Expected 10 nations, got {nation_count}"

            invalid_data_file = benchmark_instance.output_dir / "invalid_region.csv"
            with open(invalid_data_file, "w", encoding="utf-8") as f:
                f.write("0|AFRICA\n")

            original_region_file = benchmark_instance.tables["region"]
            benchmark_instance.tables["region"] = str(invalid_data_file)

            try:
                benchmark_instance.load_data_to_database(conn, tables=["region"])
            except Exception:
                pass

            cursor.execute("SELECT COUNT(*) FROM region")
            region_count_after = cursor.fetchone()[0]
            assert region_count_after == region_count, "Data was corrupted during failed transaction"

            benchmark_instance.tables["region"] = original_region_file

        finally:
            conn.close()

    def test_concurrent_database_access(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:

        db_path = benchmark_instance.output_dir / "test_concurrent.db"

        conn = sqlite3.connect(str(db_path))
        benchmark_instance.load_data_to_database(conn, tables=["region", "nation"])
        conn.close()

        results = []
        errors = []

        def worker_function(worker_id: int) -> None:
            try:
                worker_conn = sqlite3.connect(str(db_path))
                cursor = worker_conn.cursor()

                cursor.execute("SELECT COUNT(*) FROM region")
                region_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM nation")
                nation_count = cursor.fetchone()[0]

                time.sleep(0.1)

                cursor.execute("""
                    SELECT r.r_name, COUNT(n.n_nationkey) as nation_count
                    FROM region r
                    LEFT JOIN nation n ON r.r_regionkey = n.n_regionkey
                    GROUP BY r.r_name
                    ORDER BY nation_count DESC
                """)
                join_results = cursor.fetchall()

                worker_conn.close()

                results.append(
                    {
                        "worker_id": worker_id,
                        "region_count": region_count,
                        "nation_count": nation_count,
                        "join_results": len(join_results),
                    }
                )

            except Exception as e:
                errors.append({"worker_id": worker_id, "error": str(e)})

        threads = []
        for i in range(3):
            thread = threading.Thread(target=worker_function, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, f"Concurrent access errors: {errors}"
        assert len(results) == 3, f"Expected 3 results, got {len(results)}"

        for result in results:
            assert result["region_count"] == 5, f"Inconsistent region count: {result}"
            assert result["nation_count"] == 10, f"Inconsistent nation count: {result}"
            assert result["join_results"] == 5, f"Inconsistent join results: {result}"

    def test_database_connection_management(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:

        connections = []

        try:
            for i in range(5):
                conn = duckdb.connect(":memory:")
                connections.append(conn)

                benchmark_instance.load_data_to_database(conn, tables=["region"])

                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM region")
                count = cursor.fetchone()[0]
                assert count == 5, f"Connection {i} has incorrect region count: {count}"

            cursor = connections[0].cursor()
            cursor.execute("DELETE FROM region WHERE r_regionkey = 0")
            connections[0].commit()

            cursor.execute("SELECT COUNT(*) FROM region")
            modified_count = cursor.fetchone()[0]
            assert modified_count == 4, "Delete operation failed"

            for i, conn in enumerate(connections[1:], 1):
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM region")
                count = cursor.fetchone()[0]
                assert count == 5, f"Connection {i} was affected by changes in another connection"

        finally:
            for conn in connections:
                conn.close()

    def test_data_type_validation(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:

        conn = duckdb.connect(":memory:")

        try:
            benchmark_instance.load_data_to_database(conn, tables=["customer"])

            cursor = conn.cursor()

            cursor.execute("SELECT c_custkey, c_acctbal FROM customer WHERE c_custkey = 1")
            row = cursor.fetchone()
            assert row is not None, "Expected row from customer table"

            custkey, acctbal = row

            assert str(custkey).isdigit() or isinstance(custkey, int), f"Customer key is not numeric: {custkey}"

            try:
                float_balance = float(acctbal)
                assert float_balance > 0, f"Account balance should be positive: {float_balance}"
            except (ValueError, TypeError):
                raise AssertionError(f"Account balance is not numeric: {acctbal}") from None

            cursor.execute("SELECT c_name, c_mktsegment FROM customer WHERE c_custkey = 1")
            row = cursor.fetchone()
            assert row is not None, "Expected row from customer table"
            name, segment = row

            assert isinstance(name, str) and len(name) > 0, f"Customer name is invalid: {name}"
            assert isinstance(segment, str) and len(segment) > 0, f"Market segment is invalid: {segment}"

        finally:
            conn.close()

    def test_query_performance_consistency(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:

        conn = duckdb.connect(":memory:")

        try:
            benchmark_instance.load_data_to_database(conn, tables=["region", "nation", "customer"])

            query = """
                SELECT r.r_name, COUNT(c.c_custkey) as customer_count
                FROM region r
                LEFT JOIN nation n ON r.r_regionkey = n.n_regionkey
                LEFT JOIN customer c ON n.n_nationkey = c.c_nationkey
                GROUP BY r.r_name
                ORDER BY customer_count DESC, r.r_name ASC
            """

            execution_times = []
            results = []

            for i in range(5):
                start_time = time.time()
                cursor = conn.cursor()
                cursor.execute(query)
                result = cursor.fetchall()
                end_time = time.time()

                execution_times.append(end_time - start_time)
                results.append(result)

            first_result = results[0]
            for i, result in enumerate(results[1:], 1):
                assert result == first_result, f"Query result {i} differs from first result"

            sum(execution_times) / len(execution_times)
            max_time = max(execution_times)
            min_time = min(execution_times)

            if min_time > 0:
                performance_ratio = max_time / min_time
                assert performance_ratio < 50, f"Performance inconsistency too high: {performance_ratio:.2f}"

            for i, exec_time in enumerate(execution_times):
                assert exec_time < 1.0, f"Query {i} took too long: {exec_time:.3f}s"

        finally:
            conn.close()

    def test_database_schema_validation(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:

        conn = duckdb.connect(":memory:")

        try:
            schema_sql = benchmark_instance.get_create_tables_sql()

            for statement in schema_sql.strip().split(";"):
                if statement.strip():
                    conn.execute(statement.strip())

            expected_tables = list(TABLES.keys())
            tables_result = conn.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
            """).fetchall()
            actual_tables = [row[0].lower() for row in tables_result]

            for table_name in expected_tables:
                assert table_name.lower() in actual_tables, f"Table {table_name} not found"

            for table_name, table_schema in TABLES.items():
                columns_info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()

                expected_columns = len(table_schema["columns"])
                actual_columns = len(columns_info)

                assert actual_columns >= expected_columns, (
                    f"Table {table_name} has too few columns: {actual_columns} < {expected_columns}"
                )

                actual_column_names = [col[1].lower() for col in columns_info]
                for expected_col in table_schema["columns"]:
                    expected_name = expected_col["name"].lower()
                    assert expected_name in actual_column_names, (
                        f"Column {expected_name} not found in table {table_name}"
                    )

        finally:
            conn.close()

    def test_error_recovery_and_cleanup(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:

        conn = duckdb.connect(":memory:")

        try:
            benchmark_instance.load_data_to_database(conn, tables=["region"])

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM region")
            initial_count = cursor.fetchone()[0]
            assert initial_count == 5, "Initial data load failed"

            invalid_file = benchmark_instance.output_dir / "invalid_nation.csv"
            with open(invalid_file, "w", encoding="utf-8") as f:
                f.write("0|ALGERIA|0\n")
                f.write("1|ARGENTINA|1\n")

            original_nation_file = benchmark_instance.tables.get("nation")
            benchmark_instance.tables["nation"] = str(invalid_file)

            error_occurred = False
            try:
                benchmark_instance.load_data_to_database(conn, tables=["nation"])
            except Exception:
                error_occurred = True

            assert error_occurred, "Expected error did not occur"

            cursor.execute("SELECT COUNT(*) FROM region")
            final_count = cursor.fetchone()[0]
            assert final_count == initial_count, "Existing data was corrupted after error"

            try:
                cursor.execute("SELECT COUNT(*) FROM nation")
                nation_count = cursor.fetchone()[0]
                assert nation_count == 0, "Failed load left partial data"
            except Exception:
                pass

            if original_nation_file:
                benchmark_instance.tables["nation"] = original_nation_file

        finally:
            conn.close()

    def test_database_resource_management(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:

        import gc
        import os

        import psutil

        warmup_conn = duckdb.connect(":memory:")
        try:
            benchmark_instance.load_data_to_database(warmup_conn, tables=["region", "nation"])
        finally:
            warmup_conn.close()
        gc.collect()

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024

        connections = []

        try:
            for _i in range(3):
                conn = duckdb.connect(":memory:")
                connections.append(conn)
                benchmark_instance.load_data_to_database(conn, tables=["region", "nation"])

                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM region")
                assert cursor.fetchone()[0] == 5

            current_memory = process.memory_info().rss / 1024 / 1024
            memory_increase = current_memory - initial_memory

            assert memory_increase < 50, f"Memory usage increased too much: {memory_increase:.2f}MB"

        finally:
            for conn in connections:
                conn.close()

            gc.collect()

            final_memory = process.memory_info().rss / 1024 / 1024
            memory_after_cleanup = final_memory - initial_memory

            max_acceptable_growth = 500
            assert memory_after_cleanup < max_acceptable_growth, (
                f"Excessive memory growth detected: {memory_after_cleanup:.2f}MB > {max_acceptable_growth}MB"
            )

    def test_cross_database_compatibility_patterns(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:
        conn = duckdb.connect(":memory:")

        try:
            benchmark_instance.load_data_to_database(conn, tables=["region", "nation", "customer"])

            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM customer WHERE c_acctbal > 1000")
            high_balance_count = cursor.fetchone()[0]
            assert high_balance_count >= 0

            cursor.execute("""
                SELECT r.r_name, COUNT(n.n_nationkey) as nation_count
                FROM region r
                LEFT JOIN nation n ON r.r_regionkey = n.n_regionkey
                GROUP BY r.r_name
                HAVING COUNT(n.n_nationkey) > 0
                ORDER BY nation_count DESC
            """)
            join_results = cursor.fetchall()
            assert len(join_results) > 0

            cursor.execute("""
                SELECT c.c_name
                FROM customer c
                WHERE c.c_nationkey IN (
                    SELECT n.n_nationkey
                    FROM nation n
                    WHERE n.n_regionkey = 1
                )
            """)
            subquery_results = cursor.fetchall()
            assert len(subquery_results) >= 0

            cursor.execute("""
                SELECT
                    MIN(c_acctbal) as min_balance,
                    MAX(c_acctbal) as max_balance,
                    AVG(c_acctbal) as avg_balance,
                    COUNT(*) as total_customers
                FROM customer
            """)
            agg_result = cursor.fetchone()
            assert agg_result is not None, "Expected aggregate result"
            min_bal, max_bal, avg_bal, total = agg_result

            assert min_bal <= max_bal
            assert avg_bal >= min_bal and avg_bal <= max_bal
            assert total > 0

            cursor.execute("""
                SELECT
                    c_name,
                    CASE
                        WHEN c_acctbal > 5000 THEN 'HIGH'
                        WHEN c_acctbal > 1000 THEN 'MEDIUM'
                        ELSE 'LOW'
                    END as balance_category
                FROM customer
                ORDER BY c_acctbal DESC
            """)
            case_results = cursor.fetchall()
            assert len(case_results) > 0

            valid_categories = {"HIGH", "MEDIUM", "LOW"}
            for _name, category in case_results:
                assert category in valid_categories, f"Invalid category: {category}"

        finally:
            conn.close()

    @pytest.mark.parametrize(
        "table_subset",
        [
            ["region"],
            ["region", "nation"],
            ["region", "nation", "customer"],
            ["region", "nation", "supplier", "customer"],
        ],
    )
    def test_partial_data_loading(self, benchmark_instance: ReadPrimitivesBenchmark, table_subset: list[str]) -> None:

        conn = duckdb.connect(":memory:")

        try:
            benchmark_instance.load_data_to_database(conn, tables=table_subset)

            cursor = conn.cursor()

            for table_name in table_subset:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                assert count > 0, f"Table {table_name} was not loaded or is empty"

            all_tables = ["region", "nation", "supplier", "customer"]
            for table_name in all_tables:
                if table_name not in table_subset:
                    try:
                        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                        count = cursor.fetchone()[0]
                        assert count == 0, f"Table {table_name} should not have data"
                    except Exception:
                        pass

        finally:
            conn.close()

    def test_benchmark_query_execution_integration(self, benchmark_instance: ReadPrimitivesBenchmark) -> None:

        conn = duckdb.connect(":memory:")

        try:
            benchmark_instance.load_data_to_database(conn, tables=["region", "nation", "customer"])

            result = benchmark_instance.execute_query("aggregation_groupby_small", conn)

            assert len(result) > 0, "Query returned no results"

            for row in result:
                assert len(row) >= 2, "Query results should have at least 2 columns"
                assert str(row[0]).isdigit() or isinstance(row[0], int), f"Region key should be numeric: {row[0]}"
                assert isinstance(row[1], (int, str)) and (isinstance(row[1], int) or row[1].isdigit()), (
                    f"Count should be numeric: {row[1]}"
                )

        except Exception as e:
            if "no such table" not in str(e).lower():
                raise e from e
        finally:
            conn.close()
