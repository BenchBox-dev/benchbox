# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark
from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.duckdb, pytest.mark.database]


def _minimal_tpch_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE orders (
            o_orderkey INTEGER, o_custkey INTEGER, o_orderstatus VARCHAR,
            o_totalprice DECIMAL(15,2), o_orderdate DATE, o_orderpriority VARCHAR,
            o_clerk VARCHAR, o_shippriority INTEGER, o_comment VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE lineitem (
            l_orderkey INTEGER, l_partkey INTEGER, l_suppkey INTEGER, l_linenumber INTEGER,
            l_quantity DECIMAL(15,2), l_extendedprice DECIMAL(15,2), l_discount DECIMAL(15,2),
            l_tax DECIMAL(15,2), l_returnflag VARCHAR, l_linestatus VARCHAR,
            l_shipdate DATE, l_commitdate DATE, l_receiptdate DATE,
            l_shipinstruct VARCHAR, l_shipmode VARCHAR, l_comment VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE customer (
            c_custkey INTEGER, c_name VARCHAR, c_address VARCHAR, c_nationkey INTEGER,
            c_phone VARCHAR, c_acctbal DECIMAL(15,2), c_mktsegment VARCHAR, c_comment VARCHAR
        )
    """)
    conn.execute("""
        INSERT INTO orders VALUES
        (1, 100, 'O', 150.50, '2024-01-01', '1-URGENT', 'Clerk#001', 0, 'o1'),
        (2, 101, 'O', 250.75, '2024-01-02', '2-HIGH', 'Clerk#002', 0, 'o2'),
        (3, 102, 'O', 350.00, '2024-01-03', '3-MEDIUM', 'Clerk#003', 0, 'o3')
    """)
    conn.execute("""
        INSERT INTO lineitem VALUES
        (1, 1001, 201, 1, 10.0, 100.0, 0.05, 0.01, 'N', 'O',
         '2024-01-15', '2024-01-10', '2024-01-20', 'NONE', 'TRUCK', 'l1'),
        (2, 1002, 202, 1, 20.0, 200.0, 0.05, 0.01, 'N', 'O',
         '2024-01-16', '2024-01-11', '2024-01-21', 'NONE', 'MAIL', 'l2'),
        (3, 1003, 203, 1, 15.0, 150.0, 0.10, 0.02, 'N', 'O',
         '2024-01-17', '2024-01-12', '2024-01-22', 'NONE', 'SHIP', 'l3')
    """)
    conn.execute("""
        INSERT INTO customer VALUES
        (100, 'Customer#100', '123 Main St', 1, '555-1234', 1000.00, 'AUTOMOBILE', 'c1'),
        (101, 'Customer#101', '456 Oak Ave', 1, '555-5678', 2000.00, 'BUILDING', 'c2'),
        (102, 'Customer#102', '789 Pine Rd', 1, '555-9999', 3000.00, 'MACHINERY', 'c3')
    """)
    return conn


def _grow_tpch_source(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("INSERT INTO orders SELECT * REPLACE (o_orderkey + 1000 AS o_orderkey) FROM orders")
    conn.execute("INSERT INTO lineitem SELECT * REPLACE (l_orderkey + 1000 AS l_orderkey) FROM lineitem")
    conn.execute("INSERT INTO customer SELECT * REPLACE (c_custkey + 1000 AS c_custkey) FROM customer")


def _rows(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


_TXN_GATED_TABLES = ("txn_orders", "txn_lineitem", "txn_customer")
_WRITE_GATED_TABLES = (
    "update_ops_orders",
    "delete_ops_orders",
    "delete_ops_lineitem",
    "merge_ops_target",
    "merge_ops_source",
    "merge_ops_lineitem_target",
    "ddl_truncate_target",
)


def _snapshot(conn: duckdb.DuckDBPyConnection, tables: tuple[str, ...]) -> dict[str, int]:
    return {t: _rows(conn, t) for t in tables}


def _assert_all_rebuilt(conn: duckdb.DuckDBPyConnection, before: dict[str, int], tables: tuple[str, ...]) -> None:
    after = _snapshot(conn, tables)
    stale = {t: (before[t], after[t]) for t in tables if after[t] <= before[t]}
    assert not stale, f"staging tables left stale after rebuild (before, after): {stale}"


@pytest.fixture
def loaded_tpch_conn():
    conn = _minimal_tpch_conn()
    yield conn
    conn.close()


class TestTransactionPrimitivesStagingProvenance:
    def test_no_manifest_is_not_setup(self, tmp_path: Path, loaded_tpch_conn):
        bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        assert bench.is_setup(loaded_tpch_conn) is False

    def test_matching_manifest_skips_rebuild(self, tmp_path: Path, loaded_tpch_conn):
        bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        bench.setup(loaded_tpch_conn, force=False)
        assert bench.is_setup(loaded_tpch_conn) is True

        same_scale_bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        assert same_scale_bench.is_setup(loaded_tpch_conn) is True

    def test_scale_mismatch_forces_rebuild(self, tmp_path: Path, loaded_tpch_conn):
        small = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        small.setup(loaded_tpch_conn, force=False)
        assert small.is_setup(loaded_tpch_conn) is True

        large = TransactionPrimitivesBenchmark(scale_factor=1.0, output_dir=tmp_path)
        assert large.is_setup(loaded_tpch_conn) is False

    def test_legacy_populated_unmanifested_database_forces_rebuild(self, tmp_path: Path, loaded_tpch_conn):
        bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)

        loaded_tpch_conn.execute("CREATE TABLE txn_orders AS SELECT * FROM orders LIMIT 1")
        loaded_tpch_conn.execute("CREATE TABLE txn_lineitem AS SELECT * FROM lineitem LIMIT 1")
        loaded_tpch_conn.execute("CREATE TABLE txn_customer AS SELECT * FROM customer LIMIT 1")

        assert bench.is_setup(loaded_tpch_conn) is False

        bench.setup(loaded_tpch_conn, force=False)
        assert _rows(loaded_tpch_conn, "txn_orders") == _rows(loaded_tpch_conn, "orders")
        assert _rows(loaded_tpch_conn, "txn_lineitem") == _rows(loaded_tpch_conn, "lineitem")
        assert bench.is_setup(loaded_tpch_conn) is True

    def test_stale_scale_rebuild_repopulates_staging(self, tmp_path: Path, loaded_tpch_conn):
        small = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        small.setup(loaded_tpch_conn, force=False)
        before = _snapshot(loaded_tpch_conn, _TXN_GATED_TABLES)

        _grow_tpch_source(loaded_tpch_conn)
        large = TransactionPrimitivesBenchmark(scale_factor=1.0, output_dir=tmp_path)
        assert large.is_setup(loaded_tpch_conn) is False

        large.setup(loaded_tpch_conn, force=False)

        _assert_all_rebuilt(loaded_tpch_conn, before, _TXN_GATED_TABLES)
        assert _rows(loaded_tpch_conn, "txn_orders") == _rows(loaded_tpch_conn, "orders")
        assert large.is_setup(loaded_tpch_conn) is True

    def test_manifest_from_the_pre_rebuild_generation_is_not_reused(self, tmp_path: Path, loaded_tpch_conn):
        bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        benchmark_id, scale, spec_version = bench._staging_provenance_key()
        digest = bench._staging_source_digest(loaded_tpch_conn, ["orders", "lineitem", "customer"])

        for legacy in bench._LEGACY_STAGING_MANIFEST_TABLES:
            loaded_tpch_conn.execute(
                f"CREATE TABLE {legacy} (benchmark VARCHAR, scale VARCHAR, spec_version VARCHAR, "
                "source_digest VARCHAR, created_at VARCHAR)"
            )
            loaded_tpch_conn.execute(
                f"INSERT INTO {legacy} VALUES "
                f"('{benchmark_id}', '{scale}', '{spec_version}', '{digest}', '2026-08-05T00:00:00Z')"
            )
        loaded_tpch_conn.execute("CREATE TABLE txn_orders AS SELECT * FROM orders LIMIT 1")
        loaded_tpch_conn.execute("CREATE TABLE txn_lineitem AS SELECT * FROM lineitem LIMIT 1")
        loaded_tpch_conn.execute("CREATE TABLE txn_customer AS SELECT * FROM customer LIMIT 1")

        assert bench.is_setup(loaded_tpch_conn) is False

        bench.setup(loaded_tpch_conn, force=False)
        assert _rows(loaded_tpch_conn, "txn_orders") == _rows(loaded_tpch_conn, "orders")
        for legacy in bench._LEGACY_STAGING_MANIFEST_TABLES:
            with pytest.raises(duckdb.CatalogException):
                loaded_tpch_conn.execute(f"SELECT 1 FROM {legacy}")

    def test_v2_manifest_row_from_before_catalog_managed_ddl_is_not_reused(self, tmp_path: Path, loaded_tpch_conn):
        bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        benchmark_id, scale, spec_version = bench._staging_provenance_key()
        digest = bench._staging_source_digest(loaded_tpch_conn, ["orders", "lineitem", "customer"])

        loaded_tpch_conn.execute(
            "CREATE TABLE benchbox_staging_manifest_v2 (benchmark VARCHAR, scale VARCHAR, "
            "spec_version VARCHAR, source_digest VARCHAR, created_at VARCHAR)"
        )
        loaded_tpch_conn.execute(
            "INSERT INTO benchbox_staging_manifest_v2 VALUES "
            f"('{benchmark_id}', '{scale}', '{spec_version}', '{digest}', '2026-09-01T00:00:00Z')"
        )
        loaded_tpch_conn.execute("CREATE TABLE txn_orders AS SELECT * FROM orders LIMIT 1")
        loaded_tpch_conn.execute("CREATE TABLE txn_lineitem AS SELECT * FROM lineitem LIMIT 1")
        loaded_tpch_conn.execute("CREATE TABLE txn_customer AS SELECT * FROM customer LIMIT 1")

        assert bench.is_setup(loaded_tpch_conn) is False

        bench.setup(loaded_tpch_conn, force=False)
        assert _rows(loaded_tpch_conn, "txn_orders") == _rows(loaded_tpch_conn, "orders")
        assert bench.is_setup(loaded_tpch_conn) is True
        with pytest.raises(duckdb.CatalogException):
            loaded_tpch_conn.execute("SELECT 1 FROM benchbox_staging_manifest_v2")


class TestWritePrimitivesStagingProvenance:
    def test_no_manifest_is_not_setup(self, tmp_path: Path, loaded_tpch_conn):
        bench = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        assert bench.is_setup(loaded_tpch_conn) is False

    def test_matching_manifest_skips_rebuild(self, tmp_path: Path, loaded_tpch_conn):
        bench = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        bench.setup(loaded_tpch_conn, force=False)
        assert bench.is_setup(loaded_tpch_conn) is True

        same_scale_bench = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        assert same_scale_bench.is_setup(loaded_tpch_conn) is True

    def test_scale_mismatch_forces_rebuild(self, tmp_path: Path, loaded_tpch_conn):
        small = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        small.setup(loaded_tpch_conn, force=False)
        assert small.is_setup(loaded_tpch_conn) is True

        large = WritePrimitivesBenchmark(scale_factor=1.0, output_dir=tmp_path)
        assert large.is_setup(loaded_tpch_conn) is False

    def test_legacy_populated_unmanifested_database_forces_rebuild(self, tmp_path: Path, loaded_tpch_conn):
        bench = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)

        loaded_tpch_conn.execute("CREATE TABLE update_ops_orders AS SELECT * FROM orders LIMIT 1")
        loaded_tpch_conn.execute("CREATE TABLE delete_ops_orders AS SELECT * FROM orders LIMIT 1")
        loaded_tpch_conn.execute("CREATE TABLE delete_ops_lineitem AS SELECT * FROM lineitem LIMIT 1")
        loaded_tpch_conn.execute("CREATE TABLE merge_ops_target AS SELECT * FROM orders LIMIT 1")
        loaded_tpch_conn.execute("CREATE TABLE merge_ops_source AS SELECT * FROM orders LIMIT 1")
        loaded_tpch_conn.execute("CREATE TABLE merge_ops_lineitem_target AS SELECT * FROM lineitem LIMIT 1")
        loaded_tpch_conn.execute(
            "CREATE TABLE ddl_truncate_target AS SELECT o_orderkey, o_custkey, o_orderdate FROM orders LIMIT 1"
        )

        assert bench.is_setup(loaded_tpch_conn) is False

        bench.setup(loaded_tpch_conn, force=False)
        assert _rows(loaded_tpch_conn, "delete_ops_lineitem") == _rows(loaded_tpch_conn, "lineitem")
        assert bench.is_setup(loaded_tpch_conn) is True

    def test_stale_scale_rebuild_repopulates_staging(self, tmp_path: Path, loaded_tpch_conn):
        small = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        small.setup(loaded_tpch_conn, force=False)
        before = _snapshot(loaded_tpch_conn, _WRITE_GATED_TABLES)

        _grow_tpch_source(loaded_tpch_conn)
        large = WritePrimitivesBenchmark(scale_factor=1.0, output_dir=tmp_path)
        assert large.is_setup(loaded_tpch_conn) is False

        large.setup(loaded_tpch_conn, force=False)

        _assert_all_rebuilt(loaded_tpch_conn, before, _WRITE_GATED_TABLES)
        assert _rows(loaded_tpch_conn, "delete_ops_lineitem") == _rows(loaded_tpch_conn, "lineitem")
        assert large.is_setup(loaded_tpch_conn) is True


class TestStagingManifestHelpers:
    def test_source_digest_reflects_row_counts(self, tmp_path: Path, loaded_tpch_conn):
        bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        digest_before = bench._staging_source_digest(loaded_tpch_conn, ["orders", "lineitem", "customer"])

        loaded_tpch_conn.execute(
            "INSERT INTO orders VALUES (4, 103, 'O', 50.0, '2024-01-04', '3-MEDIUM', 'Clerk#004', 0, 'o4')"
        )
        digest_after = bench._staging_source_digest(loaded_tpch_conn, ["orders", "lineitem", "customer"])

        assert digest_before != digest_after

    def test_manifest_missing_table_does_not_match(self, tmp_path: Path, loaded_tpch_conn):
        bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        assert bench._staging_manifest_matches(loaded_tpch_conn, ["orders", "lineitem", "customer"]) is False

    def test_write_then_match_round_trips(self, tmp_path: Path, loaded_tpch_conn):
        bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        bench._write_staging_manifest(loaded_tpch_conn, ["orders", "lineitem", "customer"])
        assert bench._staging_manifest_matches(loaded_tpch_conn, ["orders", "lineitem", "customer"]) is True

    @pytest.mark.parametrize(
        ("dialect", "expected"),
        [
            ("databricks", "STRING"),
            ("bigquery", "STRING"),
            ("snowflake", "VARCHAR"),
            ("singlestore", "TEXT"),
            ("duckdb", "VARCHAR"),
            ("standard", "VARCHAR"),
        ],
    )
    def test_manifest_text_type_matches_dialect(self, tmp_path: Path, dialect: str, expected: str):
        bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        bench._setup_dialect = dialect
        assert bench._manifest_text_type() == expected

    def test_different_spec_version_does_not_match(self, tmp_path: Path, loaded_tpch_conn):
        bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        bench._write_staging_manifest(loaded_tpch_conn, ["orders", "lineitem", "customer"])

        bench._version = "2.0"
        assert bench._staging_manifest_matches(loaded_tpch_conn, ["orders", "lineitem", "customer"]) is False

    def test_prepare_operation_seeds_setup_dialect_before_reuse_probe(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

        bench = TransactionPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        assert bench._setup_dialect == "standard"

        seen_sql: list[str] = []

        connection = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        connection.execute.side_effect = lambda sql: (seen_sql.append(sql), cursor)[1]

        operation = MagicMock()
        operation.requires_setup = True
        bench.operations_manager = MagicMock()
        bench.operations_manager.get_operation.return_value = operation
        bench.setup = MagicMock()

        bench._prepare_operation("op1", connection, platform_key="bigquery")

        assert bench._setup_dialect == "bigquery"
        assert bench.setup.call_count == 0
        assert any("`TXN_ORDERS`" in sql for sql in seen_sql), seen_sql
