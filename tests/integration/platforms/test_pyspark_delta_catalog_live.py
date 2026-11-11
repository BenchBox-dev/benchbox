# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.transaction_primitives.catalog.loader import load_transaction_primitives_catalog

from .delta_live_helpers import delta_live_skip_reason, make_delta_spark_session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_delta,
    pytest.mark.skipif(
        delta_live_skip_reason() is not None,
        reason=delta_live_skip_reason() or "PySpark + Delta Lake runtime unavailable",
    ),
]


@pytest.fixture(scope="module")
def spark(tmp_path_factory, pyspark_test_environment):

    warehouse = tmp_path_factory.mktemp("delta_catalog_warehouse")
    session = make_delta_spark_session(warehouse, app_name="benchbox-delta-catalog")
    yield session
    session.stop()


@pytest.fixture
def table_dir(tmp_path: Path) -> Path:
    target = tmp_path / "tables"
    target.mkdir()
    return target


LINEITEM_DDL = """(
    l_orderkey BIGINT, l_partkey INT, l_suppkey INT, l_linenumber INT,
    l_quantity DOUBLE, l_extendedprice DOUBLE, l_discount DOUBLE, l_tax DOUBLE,
    l_returnflag STRING, l_linestatus STRING,
    l_shipdate DATE, l_commitdate DATE, l_receiptdate DATE,
    l_shipinstruct STRING, l_shipmode STRING, l_comment STRING
)"""

ORDERS_DDL = """(
    o_orderkey BIGINT, o_custkey INT, o_orderstatus STRING, o_totalprice DOUBLE,
    o_orderdate DATE, o_orderpriority STRING, o_clerk STRING,
    o_shippriority INT, o_comment STRING
)"""

COMMIT_ROWS = [
    "(9100001, 1, 1, 1, 10.0, 1000.0, 0.05, 0.02, 'N', 'O', DATE '1998-01-01', DATE '1998-01-15', DATE '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'tx1')",
    "(9100002, 2, 2, 1, 20.0, 2000.0, 0.05, 0.02, 'N', 'O', DATE '1998-01-01', DATE '1998-01-15', DATE '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'tx2')",
    "(9100003, 3, 3, 1, 30.0, 3000.0, 0.05, 0.02, 'N', 'O', DATE '1998-01-01', DATE '1998-01-15', DATE '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'tx3')",
    "(9100004, 4, 4, 1, 40.0, 4000.0, 0.05, 0.02, 'N', 'O', DATE '1998-01-01', DATE '1998-01-15', DATE '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'tx4')",
    "(9100005, 5, 5, 1, 50.0, 5000.0, 0.05, 0.02, 'N', 'O', DATE '1998-01-01', DATE '1998-01-15', DATE '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'tx5')",
    "(9100006, 6, 6, 1, 60.0, 6000.0, 0.05, 0.02, 'N', 'O', DATE '1998-01-01', DATE '1998-01-15', DATE '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'tx6')",
    "(9100007, 7, 7, 1, 70.0, 7000.0, 0.05, 0.02, 'N', 'O', DATE '1998-01-01', DATE '1998-01-15', DATE '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'tx7')",
    "(9100008, 8, 8, 1, 80.0, 8000.0, 0.05, 0.02, 'N', 'O', DATE '1998-01-01', DATE '1998-01-15', DATE '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'tx8')",
    "(9100009, 9, 9, 1, 90.0, 9000.0, 0.05, 0.02, 'N', 'O', DATE '1998-01-01', DATE '1998-01-15', DATE '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'tx9')",
    "(9100010, 10, 10, 1, 100.0, 10000.0, 0.05, 0.02, 'N', 'O', DATE '1998-01-01', DATE '1998-01-15', DATE '1998-01-20', 'DELIVER IN PERSON', 'TRUCK', 'tx10')",
]


class TestCatalogQueriesOnDelta:
    def _create_table(self, spark, name: str, ddl: str, table_dir: Path) -> None:
        path = str(table_dir / name).replace("\\", "/")
        spark.sql(f"DROP TABLE IF EXISTS {name}")
        spark.sql(f"CREATE TABLE {name} {ddl} USING DELTA LOCATION '{path}'")

    def test_verify_commit_query(self, spark, table_dir: Path):
        catalog = load_transaction_primitives_catalog()
        op = catalog.operations["transaction_commit_small"]
        verify = next(q for q in op.validation_queries if q.id == "verify_commit")

        self._create_table(spark, "txn_lineitem", LINEITEM_DDL, table_dir)
        spark.sql(f"INSERT INTO txn_lineitem VALUES {', '.join(COMMIT_ROWS)}")

        rows = spark.sql(verify.sql).collect()
        assert len(rows) == verify.expected_rows == 10
        assert [row["l_orderkey"] for row in rows] == list(range(9100001, 9100011))

    def test_verify_rollback_query_on_empty_table(self, spark, table_dir: Path):
        catalog = load_transaction_primitives_catalog()
        op = catalog.operations["transaction_rollback_small"]
        verify = next(q for q in op.validation_queries if q.id == "verify_rollback")

        self._create_table(spark, "txn_orders", ORDERS_DDL, table_dir)
        rows = spark.sql(verify.sql).collect()
        assert len(rows) == verify.expected_rows == 1
        assert rows[0]["cnt"] == 0

    def test_cleanup_sql_removes_seeded_rows(self, spark, table_dir: Path):
        catalog = load_transaction_primitives_catalog()
        op = catalog.operations["transaction_commit_small"]
        assert op.cleanup_sql

        self._create_table(spark, "txn_lineitem", LINEITEM_DDL, table_dir)
        spark.sql(f"INSERT INTO txn_lineitem VALUES {', '.join(COMMIT_ROWS)}")
        spark.sql(op.cleanup_sql)
        remaining = spark.sql("SELECT COUNT(*) AS cnt FROM txn_lineitem").collect()[0]["cnt"]
        assert remaining == 0


