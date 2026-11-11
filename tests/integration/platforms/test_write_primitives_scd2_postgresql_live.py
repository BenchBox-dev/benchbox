# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


from __future__ import annotations

import os

import pytest

from benchbox.write_primitives import WritePrimitives

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_postgresql,
]

SCD2_OPS = (
    "merge_scd_type2_basic",
    "merge_scd_type2_no_change",
    "merge_scd_type2_new_keys_only",
)

_SCHEMA_NAME = f"scd2_wp_test_{os.environ.get('PYTEST_XDIST_WORKER', 'master')}"


@pytest.fixture
def pg_scd2_env(temp_dir):
    skip_unless_docker_service("localhost", 5432, platform="PostgreSQL")
    psycopg = pytest.importorskip("psycopg")

    conn = psycopg.connect(
        "host=localhost port=5432 user=benchbox password=benchbox dbname=benchbox_test",
        autocommit=True,
        connect_timeout=5,
    )
    conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA_NAME} CASCADE")
    conn.execute(f"CREATE SCHEMA {_SCHEMA_NAME}")
    conn.execute(f"SET search_path TO {_SCHEMA_NAME}")
    conn.execute(
        "CREATE TABLE orders (o_orderkey INTEGER PRIMARY KEY, o_custkey INTEGER, "
        "o_orderstatus CHAR(1), o_totalprice DECIMAL(15,2), o_orderdate DATE, "
        "o_orderpriority CHAR(15), o_clerk CHAR(15), o_shippriority INTEGER, o_comment VARCHAR(79))"
    )
    conn.execute(
        "CREATE TABLE lineitem (l_orderkey INTEGER, l_partkey INTEGER, l_suppkey INTEGER, "
        "l_linenumber INTEGER, l_quantity DECIMAL(15,2), l_extendedprice DECIMAL(15,2), "
        "l_discount DECIMAL(15,2), l_tax DECIMAL(15,2), l_returnflag CHAR(1), l_linestatus CHAR(1), "
        "l_shipdate DATE, l_commitdate DATE, l_receiptdate DATE, l_shipinstruct CHAR(25), "
        "l_shipmode CHAR(10), l_comment VARCHAR(44))"
    )
    conn.execute(
        "CREATE TABLE customer (c_custkey INTEGER PRIMARY KEY, c_name VARCHAR(25), "
        "c_address VARCHAR(40), c_nationkey INTEGER, c_phone VARCHAR(15), c_acctbal DECIMAL(15,2), "
        "c_mktsegment VARCHAR(10), c_comment VARCHAR(117))"
    )
    conn.execute("INSERT INTO orders VALUES (1, 1, 'O', 10.0, DATE '2024-01-01', '1-URGENT', 'C#1', 0, 'o')")
    conn.execute(
        "INSERT INTO lineitem VALUES (1, 1, 1, 1, 1.0, 1.0, 0.0, 0.0, 'N', 'O', "
        "DATE '2024-01-02', DATE '2024-01-01', DATE '2024-01-03', 'NONE', 'TRUCK', 'c')"
    )
    conn.execute(
        "INSERT INTO customer "
        "SELECT i, 'Customer#' || CAST(i AS VARCHAR), 'Addr ' || CAST(i AS VARCHAR), "
        "(i % 25), '555-' || CAST(i AS VARCHAR), (i * 10.0), "
        "CASE WHEN i % 2 = 0 THEN 'BUILDING' ELSE 'AUTOMOBILE' END, "
        "'comment ' || CAST(i AS VARCHAR) "
        "FROM generate_series(1, 50) AS t(i)"
    )

    write_bench = WritePrimitives(scale_factor=0.01, output_dir=temp_dir, quiet=True)
    write_bench._impl.setup(conn, force=True, dialect="postgres")
    yield write_bench, conn
    conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA_NAME} CASCADE")
    conn.close()


def _current_state(conn):
    return conn.execute(
        "SELECT COUNT(*), "
        "SUM(CASE WHEN is_current THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN NOT is_current THEN 1 ELSE 0 END) "
        "FROM scd2_ops_dim_customer"
    ).fetchone()


class TestWritePrimitivesSCD2PostgreSQL:
    def test_setup_seeds_dimension_and_stage(self, pg_scd2_env):
        _, conn = pg_scd2_env
        total, current, closed = _current_state(conn)
        assert (total, current, closed) == (50, 50, 0)
        stage = dict(
            conn.execute("SELECT change_type, COUNT(*) FROM scd2_ops_stage_customer GROUP BY change_type").fetchall()
        )
        assert stage == {"changed": 20, "unchanged": 20, "new": 20}
        stamps = dict(
            conn.execute(
                "SELECT change_type, MAX(effective_ts) FROM scd2_ops_stage_customer GROUP BY change_type"
            ).fetchall()
        )
        assert [str(stamps[k]) for k in ("changed", "unchanged", "new")] == [
            "2026-01-01",
            "2026-01-02",
            "2026-01-03",
        ]
        agree = conn.execute(
            "SELECT COUNT(*) FROM scd2_ops_stage_customer s "
            "JOIN scd2_ops_dim_customer d USING (c_custkey) "
            "WHERE s.change_type = 'unchanged' AND s.row_hash = d.row_hash"
        ).fetchone()[0]
        assert agree == 20

    @pytest.mark.parametrize("op_id", SCD2_OPS)
    def test_scd2_op_not_skipped_on_postgres(self, pg_scd2_env, op_id):
        write_bench, conn = pg_scd2_env
        op = write_bench.get_operation(op_id)
        effective_sql, skip_reason = write_bench._impl._get_effective_write_sql(
            op, platform_key="postgres", connection=conn
        )
        assert skip_reason is None
        assert effective_sql is not None

    @pytest.mark.parametrize("op_id", SCD2_OPS)
    def test_scd2_op_executes_validates_and_restores_seed(self, pg_scd2_env, op_id):
        write_bench, conn = pg_scd2_env
        before = _current_state(conn)
        result = write_bench.execute_operation(op_id, conn, platform_key="postgres")
        assert result.success is True, result.error
        assert result.validation_passed is True, result.error
        assert result.cleanup_success is True
        assert _current_state(conn) == before == (50, 50, 0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


