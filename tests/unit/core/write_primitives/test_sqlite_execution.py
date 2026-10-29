from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from benchbox.core.tpch.schema import get_create_all_tables_sql
from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark
from benchbox.platforms.sqlite import _SQLiteScriptConnection

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

_ORDERS = 120
_CUSTOMERS = 60


def _seed_tpch(conn: sqlite3.Connection) -> None:
    conn.executescript(get_create_all_tables_sql(enable_primary_keys=False, enable_foreign_keys=False))
    conn.executemany(
        "INSERT INTO region VALUES (?, ?, ?)",
        [(0, "AFRICA", "r")],
    )
    conn.executemany(
        "INSERT INTO nation VALUES (?, ?, ?, ?)",
        [(0, "ALGERIA", 0, "n")],
    )
    conn.executemany(
        "INSERT INTO supplier VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(k, f"Supplier#{k}", "addr", 0, "phone", 100.0 * k, "c") for k in range(1, 11)],
    )
    conn.executemany(
        "INSERT INTO customer VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(k, f"Customer#{k}", "addr", 0, "phone", 50.0 * k, "BUILDING", "c") for k in range(1, _CUSTOMERS + 1)],
    )
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (k, (k % _CUSTOMERS) + 1, "O", 2000.0 * k, "1995-03-01", "1-URGENT", "Clerk#000000001", 0, "c")
            for k in range(1, _ORDERS + 1)
        ],
    )
    conn.executemany(
        "INSERT INTO lineitem VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                k,
                1,
                (line % 10) + 1,
                line,
                5.0,
                100.0,
                0.05,
                0.02,
                "N",
                "O",
                "1995-03-10",
                "1995-03-11",
                "1995-03-12",
                "NONE",
                "AIR",
                "c",
            )
            for k in range(1, _ORDERS + 1)
            for line in (1, 2)
        ],
    )
    conn.commit()


@pytest.fixture()
def sqlite_wp(tmp_path: Path):
    conn = sqlite3.connect(":memory:", factory=_SQLiteScriptConnection)
    _seed_tpch(conn)
    bench = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    bench.setup(conn, force=True, dialect="sqlite")
    yield bench, conn
    conn.close()


def test_every_catalog_operation_runs_or_is_skipped_on_sqlite(sqlite_wp):
    bench, conn = sqlite_wp
    broken: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for op_id in bench.get_all_operations():
        result = bench.execute_operation(op_id, conn, platform_key="sqlite")
        if result.status == "SKIPPED":
            assert result.skip_reason, op_id
            skipped[op_id] = result.skip_reason
            continue
        if result.status != "SUCCESS" or not result.cleanup_success:
            broken[op_id] = f"{result.status}: {result.error or result.cleanup_warning}"
    assert not broken
    assert {"merge_simple_upsert_small", "ddl_truncate_table_small", "update_date_arithmetic"}.isdisjoint(skipped)
    assert all(op_id.startswith(("bulk_load_", "sketch_")) for op_id in skipped)


def test_merge_emulation_upserts_matched_and_unmatched_rows(sqlite_wp):
    bench, conn = sqlite_wp
    conn.execute("DELETE FROM merge_ops_target WHERE o_orderkey > 5")
    conn.execute("UPDATE merge_ops_target SET o_comment = 'before'")
    sql = bench.get_operation("merge_simple_upsert_small").platform_overrides["sqlite"]
    conn.execute(sql)
    rows = dict(conn.execute("SELECT o_orderkey, o_comment FROM merge_ops_target WHERE o_orderkey <= 10").fetchall())
    assert len(rows) == 10
    assert all(rows[k] == "merged_update" for k in range(1, 6))
    assert all(rows[k] != "merged_update" for k in range(6, 11))


def test_update_date_arithmetic_shifts_and_restores_dates(sqlite_wp):
    bench, conn = sqlite_wp
    before = conn.execute("SELECT MIN(o_orderdate), MAX(o_orderdate) FROM update_ops_orders").fetchone()
    result = bench.execute_operation("update_date_arithmetic", conn, platform_key="sqlite")
    assert result.status == "SUCCESS"
    assert result.cleanup_success
    after = conn.execute("SELECT MIN(o_orderdate), MAX(o_orderdate) FROM update_ops_orders").fetchone()
    assert after == before


def test_reset_clears_and_repopulates_staging_tables_on_sqlite(sqlite_wp):
    bench, conn = sqlite_wp
    populated = conn.execute("SELECT COUNT(*) FROM merge_ops_target").fetchone()[0]
    assert populated > 10
    conn.execute("DELETE FROM merge_ops_target WHERE o_orderkey > 10")
    conn.execute("UPDATE ddl_truncate_target SET o_custkey = -1")
    bench.reset(conn)
    assert conn.execute("SELECT COUNT(*) FROM merge_ops_target").fetchone()[0] == populated
    assert conn.execute("SELECT COUNT(*) FROM ddl_truncate_target WHERE o_custkey = -1").fetchone()[0] == 0


def test_is_setup_does_not_rescan_source_tables_on_every_call(sqlite_wp):
    bench, conn = sqlite_wp
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    for _ in range(3):
        assert bench.is_setup(conn)
    conn.set_trace_callback(None)
    for source in ('"orders"', '"lineitem"'):
        scans = [sql for sql in statements if "COUNT(*)" in sql.upper() and source in sql]
        assert len(scans) <= 1
    assert not any("COUNT(*)" in sql.upper() and "update_ops_orders" in sql for sql in statements)


def test_is_setup_detects_emptied_staging_table(sqlite_wp):
    bench, conn = sqlite_wp
    assert bench.is_setup(conn)
    conn.execute("DELETE FROM merge_ops_target")
    assert not bench.is_setup(conn)


def test_merge_emulation_reports_rows_from_every_statement(sqlite_wp):
    bench, conn = sqlite_wp
    conn.execute("DELETE FROM merge_ops_target WHERE o_orderkey BETWEEN 6 AND 10")
    result = bench.execute_operation("merge_simple_upsert_small", conn, platform_key="sqlite")
    assert result.status == "SUCCESS"
    assert result.rows_affected == 10
