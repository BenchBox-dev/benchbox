# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import duckdb
import pytest

from benchbox import TransactionPrimitives
from benchbox.core.transaction_primitives.benchmark import OperationResult

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.duckdb
@pytest.mark.transaction_primitives
class TestTransactionPrimitivesDuckDBLifecycle:
    @pytest.fixture
    def duckdb_conn(self):
        conn = duckdb.connect(":memory:")
        yield conn
        conn.close()

    @pytest.fixture
    def loaded_tpch_conn(self, duckdb_conn):
        duckdb_conn.execute("""
            CREATE TABLE orders (
                o_orderkey INTEGER PRIMARY KEY,
                o_custkey INTEGER,
                o_orderstatus CHAR(1),
                o_totalprice DECIMAL(15,2),
                o_orderdate DATE,
                o_orderpriority CHAR(15),
                o_clerk CHAR(15),
                o_shippriority INTEGER,
                o_comment VARCHAR(79)
            )
        """)

        duckdb_conn.execute("""
            CREATE TABLE lineitem (
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
        """)

        duckdb_conn.execute("""
            CREATE TABLE customer (
                c_custkey INTEGER PRIMARY KEY,
                c_name VARCHAR(25),
                c_address VARCHAR(40),
                c_nationkey INTEGER,
                c_phone CHAR(15),
                c_acctbal DECIMAL(15,2),
                c_mktsegment CHAR(10),
                c_comment VARCHAR(117)
            )
        """)

        duckdb_conn.execute("""
            INSERT INTO orders VALUES
            (1, 100, 'O', 150.50, '2024-01-01', '1-URGENT', 'Clerk#001', 0, 'test order 1'),
            (2, 101, 'O', 250.75, '2024-01-02', '2-HIGH', 'Clerk#002', 0, 'test order 2'),
            (3, 102, 'O', 350.00, '2024-01-03', '3-MEDIUM', 'Clerk#003', 0, 'test order 3'),
            (4, 103, 'F', 450.25, '2024-01-04', '1-URGENT', 'Clerk#004', 0, 'test order 4'),
            (5, 104, 'F', 550.50, '2024-01-05', '2-HIGH', 'Clerk#005', 0, 'test order 5')
        """)

        duckdb_conn.execute("""
            INSERT INTO lineitem VALUES
            (1, 1001, 201, 1, 10.0, 100.0, 0.05, 0.01, 'N', 'O', '2024-01-15', '2024-01-10', '2024-01-20', 'DELIVER IN PERSON', 'TRUCK', 'comment 1'),
            (1, 1002, 202, 2, 20.0, 200.0, 0.05, 0.01, 'N', 'O', '2024-01-16', '2024-01-11', '2024-01-21', 'DELIVER IN PERSON', 'MAIL', 'comment 2'),
            (2, 1003, 203, 1, 15.0, 150.0, 0.10, 0.02, 'N', 'O', '2024-01-17', '2024-01-12', '2024-01-22', 'TAKE BACK RETURN', 'SHIP', 'comment 3'),
            (3, 1004, 204, 1, 25.0, 250.0, 0.00, 0.00, 'R', 'F', '2024-01-18', '2024-01-13', '2024-01-23', 'NONE', 'AIR', 'comment 4'),
            (4, 1005, 205, 1, 30.0, 300.0, 0.05, 0.01, 'A', 'F', '2024-01-19', '2024-01-14', '2024-01-24', 'DELIVER IN PERSON', 'RAIL', 'comment 5')
        """)

        duckdb_conn.execute("""
            INSERT INTO customer VALUES
            (100, 'Customer#000000100', '123 Main St', 1, '555-1234', 1000.00, 'AUTOMOBILE', 'test customer 1'),
            (101, 'Customer#000000101', '456 Oak Ave', 1, '555-5678', 2000.00, 'BUILDING', 'test customer 2'),
            (102, 'Customer#000000102', '789 Pine Rd', 1, '555-9999', 3000.00, 'MACHINERY', 'test customer 3')
        """)

        return duckdb_conn

    @pytest.fixture
    def txn_bench(self, small_scale_factor, temp_dir):
        return TransactionPrimitives(scale_factor=small_scale_factor, output_dir=temp_dir, quiet=True)

    def test_setup_creates_staging_tables(self, txn_bench, loaded_tpch_conn):
        result = txn_bench.setup(loaded_tpch_conn, force=False)

        assert result["success"] is True
        assert "txn_orders" in result["tables_created"]
        assert "txn_lineitem" in result["tables_created"]
        assert "txn_customer" in result["tables_created"]

        txn_orders_count = loaded_tpch_conn.execute("SELECT COUNT(*) FROM txn_orders").fetchone()[0]
        txn_lineitem_count = loaded_tpch_conn.execute("SELECT COUNT(*) FROM txn_lineitem").fetchone()[0]
        txn_customer_count = loaded_tpch_conn.execute("SELECT COUNT(*) FROM txn_customer").fetchone()[0]

        assert txn_orders_count == 5
        assert txn_lineitem_count == 5
        assert txn_customer_count == 3

    def test_execute_transaction_commit_small(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_commit_small", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        assert result.write_duration_ms > 0

    def test_execute_transaction_rollback_small(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_rollback_small", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        count_value = result.validation_results[0]["sample"][0][0]
        assert count_value == 0

    def test_get_operation_categories(self, txn_bench):

        categories = txn_bench.get_operation_categories()

        assert "overhead" in categories
        assert "isolation" in categories
        assert "savepoint" in categories
        assert "multi_statement" in categories
        assert "advanced" in categories

    def test_get_operations_by_category(self, txn_bench):

        overhead_ops = txn_bench.get_operations_by_category("overhead")
        assert len(overhead_ops) >= 5

        isolation_ops = txn_bench.get_operations_by_category("isolation")
        assert len(isolation_ops) >= 3

        multi_stmt_ops = txn_bench.get_operations_by_category("multi_statement")
        assert len(multi_stmt_ops) >= 8

    def test_reset_restores_staging_tables(self, txn_bench, loaded_tpch_conn):
        txn_bench.setup(loaded_tpch_conn, force=False)

        loaded_tpch_conn.execute(
            "INSERT INTO txn_orders VALUES (999, 999, 'O', 999.0, '2024-12-31', '1-URGENT', 'Clerk#999', 0, 'dirty')"
        )
        dirty_count = loaded_tpch_conn.execute("SELECT COUNT(*) FROM txn_orders WHERE o_orderkey = 999").fetchone()[0]
        assert dirty_count == 1

        txn_bench.reset(loaded_tpch_conn)

        clean_count = loaded_tpch_conn.execute("SELECT COUNT(*) FROM txn_orders WHERE o_orderkey = 999").fetchone()[0]
        assert clean_count == 0

        original_count = loaded_tpch_conn.execute("SELECT COUNT(*) FROM txn_orders").fetchone()[0]
        assert original_count == 5

    def test_is_setup_detects_uninitialized_state(self, txn_bench, loaded_tpch_conn):
        assert txn_bench.is_setup(loaded_tpch_conn) is False

        txn_bench.setup(loaded_tpch_conn, force=False)
        assert txn_bench.is_setup(loaded_tpch_conn) is True

    def test_teardown_removes_staging_tables(self, txn_bench, loaded_tpch_conn):
        txn_bench.setup(loaded_tpch_conn, force=False)
        assert txn_bench.is_setup(loaded_tpch_conn) is True

        txn_bench.teardown(loaded_tpch_conn)

        with pytest.raises(Exception):
            loaded_tpch_conn.execute("SELECT COUNT(*) FROM txn_orders")


@pytest.mark.integration
@pytest.mark.duckdb
@pytest.mark.transaction_primitives
class TestTransactionPrimitivesQuickSanity:
    @pytest.fixture
    def duckdb_conn(self):
        conn = duckdb.connect(":memory:")
        yield conn
        conn.close()

    def test_import_and_instantiate(self):

        txn_bench = TransactionPrimitives(scale_factor=0.01, quiet=True)
        assert txn_bench is not None
        assert txn_bench.get_benchmark_info()["name"] == "Transaction Primitives Benchmark"

    def test_get_schema(self):

        txn_bench = TransactionPrimitives(scale_factor=0.01, quiet=True)
        schema = txn_bench.get_schema()

        assert "txn_orders" in schema
        assert "txn_lineitem" in schema
        assert "txn_customer" in schema

    def test_operation_count(self):

        txn_bench = TransactionPrimitives(scale_factor=0.01, quiet=True)
        all_ops = txn_bench.get_all_operations()

        assert len(all_ops) >= 23


@pytest.mark.integration
@pytest.mark.duckdb
@pytest.mark.transaction_primitives
class TestTransactionPrimitivesMultiStatement:
    @pytest.fixture
    def duckdb_conn(self):
        conn = duckdb.connect(":memory:")
        yield conn
        conn.close()

    @pytest.fixture
    def loaded_tpch_conn(self, duckdb_conn):
        duckdb_conn.execute("""
            CREATE TABLE orders (
                o_orderkey INTEGER PRIMARY KEY,
                o_custkey INTEGER,
                o_orderstatus CHAR(1),
                o_totalprice DECIMAL(15,2),
                o_orderdate DATE,
                o_orderpriority CHAR(15),
                o_clerk CHAR(15),
                o_shippriority INTEGER,
                o_comment VARCHAR(79)
            )
        """)

        duckdb_conn.execute("""
            CREATE TABLE lineitem (
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
        """)

        duckdb_conn.execute("""
            CREATE TABLE customer (
                c_custkey INTEGER PRIMARY KEY,
                c_name VARCHAR(25),
                c_address VARCHAR(40),
                c_nationkey INTEGER,
                c_phone CHAR(15),
                c_acctbal DECIMAL(15,2),
                c_mktsegment CHAR(10),
                c_comment VARCHAR(117)
            )
        """)

        duckdb_conn.execute("""
            INSERT INTO orders VALUES
            (1, 100, 'O', 150.50, '2024-01-01', '1-URGENT', 'Clerk#001', 0, 'test order 1'),
            (2, 101, 'O', 250.75, '2024-01-02', '2-HIGH', 'Clerk#002', 0, 'test order 2'),
            (3, 102, 'O', 350.00, '2024-01-03', '3-MEDIUM', 'Clerk#003', 0, 'test order 3'),
            (4, 103, 'F', 450.25, '2024-01-04', '1-URGENT', 'Clerk#004', 0, 'test order 4'),
            (5, 104, 'F', 550.50, '2024-01-05', '2-HIGH', 'Clerk#005', 0, 'test order 5')
        """)

        duckdb_conn.execute("""
            INSERT INTO lineitem VALUES
            (1, 1001, 201, 1, 10.0, 100.0, 0.05, 0.01, 'N', 'O', '2024-01-15', '2024-01-10', '2024-01-20', 'DELIVER IN PERSON', 'TRUCK', 'comment 1'),
            (1, 1002, 202, 2, 20.0, 200.0, 0.05, 0.01, 'N', 'O', '2024-01-16', '2024-01-11', '2024-01-21', 'DELIVER IN PERSON', 'MAIL', 'comment 2'),
            (2, 1003, 203, 1, 15.0, 150.0, 0.10, 0.02, 'N', 'O', '2024-01-17', '2024-01-12', '2024-01-22', 'TAKE BACK RETURN', 'SHIP', 'comment 3'),
            (3, 1004, 204, 1, 25.0, 250.0, 0.00, 0.00, 'R', 'F', '2024-01-18', '2024-01-13', '2024-01-23', 'NONE', 'AIR', 'comment 4'),
            (4, 1005, 205, 1, 30.0, 300.0, 0.05, 0.01, 'A', 'F', '2024-01-19', '2024-01-14', '2024-01-24', 'DELIVER IN PERSON', 'RAIL', 'comment 5')
        """)

        duckdb_conn.execute("""
            INSERT INTO customer VALUES
            (100, 'Customer#000000100', '123 Main St', 1, '555-1234', 1000.00, 'AUTOMOBILE', 'test customer 1'),
            (101, 'Customer#000000101', '456 Oak Ave', 1, '555-5678', 2000.00, 'BUILDING', 'test customer 2'),
            (102, 'Customer#000000102', '789 Pine Rd', 1, '555-9999', 3000.00, 'MACHINERY', 'test customer 3')
        """)

        return duckdb_conn

    @pytest.fixture
    def txn_bench(self, small_scale_factor, temp_dir):
        return TransactionPrimitives(scale_factor=small_scale_factor, output_dir=temp_dir, quiet=True)

    def test_execute_mixed_dml_small(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_mixed_dml_small", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        assert result.validation_results[0]["actual_rows"] == 2

    def test_execute_insert_update_chain(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_insert_update_chain", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        assert result.validation_results[0]["actual_rows"] == 2

    def test_execute_delete_insert_same_key(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_delete_insert_same_key", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        row_data = result.validation_results[0]["sample"][0]
        assert row_data[0] == 2
        assert row_data[1] == "replaced"

    def test_execute_read_your_writes(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_read_your_writes", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        price = result.validation_results[0]["sample"][0][0]
        assert price == 2000.0

    def test_execute_insert_with_subquery(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_insert_with_subquery", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        assert result.validation_results[0]["actual_rows"] == 2

    def test_execute_multi_table_writes(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_multi_table_writes", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        count = result.validation_results[0]["sample"][0][0]
        assert count == 2

    def test_run_all_multi_statement_operations(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        multi_stmt_ops = txn_bench.get_operations_by_category("multi_statement")
        assert len(multi_stmt_ops) >= 6

        success_count = 0
        for i, op_id in enumerate(multi_stmt_ops.keys()):
            if i >= 6:
                break
            result = txn_bench.execute_operation(op_id, loaded_tpch_conn)
            if result.success and result.validation_passed:
                success_count += 1

        assert success_count >= 6


@pytest.mark.integration
@pytest.mark.duckdb
@pytest.mark.transaction_primitives
class TestTransactionPrimitivesIsolation:
    @pytest.fixture
    def duckdb_conn(self):
        conn = duckdb.connect(":memory:")
        yield conn
        conn.close()

    @pytest.fixture
    def loaded_tpch_conn(self, duckdb_conn):
        duckdb_conn.execute("""
            CREATE TABLE orders (
                o_orderkey INTEGER PRIMARY KEY,
                o_custkey INTEGER,
                o_orderstatus CHAR(1),
                o_totalprice DECIMAL(15,2),
                o_orderdate DATE,
                o_orderpriority CHAR(15),
                o_clerk CHAR(15),
                o_shippriority INTEGER,
                o_comment VARCHAR(79)
            )
        """)

        duckdb_conn.execute("""
            CREATE TABLE lineitem (
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
        """)

        duckdb_conn.execute("""
            CREATE TABLE customer (
                c_custkey INTEGER PRIMARY KEY,
                c_name VARCHAR(25),
                c_address VARCHAR(40),
                c_nationkey INTEGER,
                c_phone CHAR(15),
                c_acctbal DECIMAL(15,2),
                c_mktsegment CHAR(10),
                c_comment VARCHAR(117)
            )
        """)

        duckdb_conn.execute("""
            INSERT INTO orders VALUES
            (1, 100, 'O', 150.50, '2024-01-01', '1-URGENT', 'Clerk#001', 0, 'test order 1'),
            (2, 101, 'O', 250.75, '2024-01-02', '2-HIGH', 'Clerk#002', 0, 'test order 2'),
            (3, 102, 'O', 350.00, '2024-01-03', '3-MEDIUM', 'Clerk#003', 0, 'test order 3')
        """)

        duckdb_conn.execute("""
            INSERT INTO lineitem VALUES
            (1, 1001, 201, 1, 10.0, 100.0, 0.05, 0.01, 'N', 'O', '2024-01-15', '2024-01-10', '2024-01-20', 'DELIVER IN PERSON', 'TRUCK', 'comment 1')
        """)

        duckdb_conn.execute("""
            INSERT INTO customer VALUES
            (100, 'Customer#000000100', '123 Main St', 1, '555-1234', 1000.00, 'AUTOMOBILE', 'test customer 1')
        """)

        return duckdb_conn

    @pytest.fixture
    def txn_bench(self, small_scale_factor, temp_dir):
        return TransactionPrimitives(scale_factor=small_scale_factor, output_dir=temp_dir, quiet=True)


@pytest.mark.integration
@pytest.mark.duckdb
@pytest.mark.transaction_primitives
class TestTransactionPrimitivesAdvanced:
    @pytest.fixture
    def duckdb_conn(self):
        conn = duckdb.connect(":memory:")
        yield conn
        conn.close()

    @pytest.fixture
    def loaded_tpch_conn(self, duckdb_conn):
        duckdb_conn.execute("""
            CREATE TABLE orders (
                o_orderkey INTEGER PRIMARY KEY,
                o_custkey INTEGER,
                o_orderstatus CHAR(1),
                o_totalprice DECIMAL(15,2),
                o_orderdate DATE,
                o_orderpriority CHAR(15),
                o_clerk CHAR(15),
                o_shippriority INTEGER,
                o_comment VARCHAR(79)
            )
        """)

        duckdb_conn.execute("""
            CREATE TABLE lineitem (
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
        """)

        duckdb_conn.execute("""
            CREATE TABLE customer (
                c_custkey INTEGER PRIMARY KEY,
                c_name VARCHAR(25),
                c_address VARCHAR(40),
                c_nationkey INTEGER,
                c_phone CHAR(15),
                c_acctbal DECIMAL(15,2),
                c_mktsegment CHAR(10),
                c_comment VARCHAR(117)
            )
        """)

        duckdb_conn.execute("""
            INSERT INTO orders VALUES
            (1, 100, 'O', 150.50, '2024-01-01', '1-URGENT', 'Clerk#001', 0, 'test order 1'),
            (2, 101, 'O', 250.75, '2024-01-02', '2-HIGH', 'Clerk#002', 0, 'test order 2'),
            (3, 102, 'O', 350.00, '2024-01-03', '3-MEDIUM', 'Clerk#003', 0, 'test order 3')
        """)

        duckdb_conn.execute("""
            INSERT INTO lineitem VALUES
            (1, 1001, 201, 1, 10.0, 100.0, 0.05, 0.01, 'N', 'O', '2024-01-15', '2024-01-10', '2024-01-20', 'DELIVER IN PERSON', 'TRUCK', 'comment 1')
        """)

        duckdb_conn.execute("""
            INSERT INTO customer VALUES
            (100, 'Customer#000000100', '123 Main St', 1, '555-1234', 1000.00, 'AUTOMOBILE', 'test customer 1')
        """)

        return duckdb_conn

    @pytest.fixture
    def txn_bench(self, small_scale_factor, temp_dir):
        return TransactionPrimitives(scale_factor=small_scale_factor, output_dir=temp_dir, quiet=True)

    def test_execute_truncate_in_transaction(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_truncate_in_transaction", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        count = result.validation_results[0]["sample"][0][0]
        assert count == 1

        txn_bench.reset(loaded_tpch_conn)

    def test_execute_create_temp_table(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_create_temp_table", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        count = result.validation_results[0]["sample"][0][0]
        assert count == 1

    def test_execute_with_cte(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_with_cte", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        count, total = result.validation_results[0]["sample"][0]
        assert count == 10
        assert total == 110000.0

    def test_execute_nested_subquery_updates(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_nested_subquery_updates", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        distinct_prices = result.validation_results[0]["sample"][0][0]
        assert distinct_prices == 1

    def test_execute_rollback_after_error(self, txn_bench, loaded_tpch_conn):

        txn_bench.setup(loaded_tpch_conn, force=False)

        result = txn_bench.execute_operation("transaction_rollback_after_error", loaded_tpch_conn)

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.validation_passed is True
        assert result.cleanup_success is True
        count = result.validation_results[0]["sample"][0][0]
        assert count == 1
