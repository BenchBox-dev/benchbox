# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]

_SCALE_FACTOR = 0.01


def _fk_and_sort_enabled_tuning_config() -> UnifiedTuningConfiguration:
    return UnifiedTuningConfiguration.from_dict(
        {
            "primary_keys": {"enabled": True, "enforce_uniqueness": True},
            "foreign_keys": {"enabled": True, "enforce_referential_integrity": True},
            "table_tunings": {
                "SUPPLIER": {
                    "table_name": "SUPPLIER",
                    "sorting": [
                        {"name": "S_SUPPKEY", "type": "INTEGER", "order": 1},
                        {"name": "S_NATIONKEY", "type": "INTEGER", "order": 2},
                    ],
                },
                "CUSTOMER": {
                    "table_name": "CUSTOMER",
                    "sorting": [
                        {"name": "C_CUSTKEY", "type": "INTEGER", "order": 1},
                    ],
                },
                "ORDERS": {
                    "table_name": "ORDERS",
                    "sorting": [
                        {"name": "O_ORDERKEY", "type": "INTEGER", "order": 1},
                        {"name": "O_CUSTKEY", "type": "INTEGER", "order": 2},
                    ],
                },
                "LINEITEM": {
                    "table_name": "LINEITEM",
                    "sorting": [
                        {"name": "L_ORDERKEY", "type": "INTEGER", "order": 1},
                        {"name": "L_LINENUMBER", "type": "INTEGER", "order": 2},
                    ],
                },
                "PART": {
                    "table_name": "PART",
                    "sorting": [
                        {"name": "P_PARTKEY", "type": "INTEGER", "order": 1},
                    ],
                },
                "PARTSUPP": {
                    "table_name": "PARTSUPP",
                    "sorting": [
                        {"name": "PS_PARTKEY", "type": "INTEGER", "order": 1},
                        {"name": "PS_SUPPKEY", "type": "INTEGER", "order": 2},
                    ],
                },
            },
        }
    )


@pytest.fixture(scope="module")
def tpch_fk_and_sort_duckdb(tmp_path_factory: pytest.TempPathFactory):
    base = tmp_path_factory.mktemp("tpch_fk_drop_order")
    db_path = str(base / "tpch_tuned.duckdb")
    adapter = DuckDBAdapter(database_path=db_path)
    adapter.tuning_enabled = True
    adapter.unified_tuning_configuration = _fk_and_sort_enabled_tuning_config()

    conn = adapter.create_connection()
    bench = TPCHBenchmark(scale_factor=_SCALE_FACTOR, output_dir=str(base / "data"))
    bench.generate_data()

    adapter.create_schema(bench, conn)
    table_stats, _duration, _extra = adapter.load_data(bench, conn, Path(base / "data"))

    yield adapter, bench, conn, table_stats
    conn.close()


def test_fk_and_sort_tuned_load_completes_for_every_table(tpch_fk_and_sort_duckdb):
    _adapter, _bench, _conn, table_stats = tpch_fk_and_sort_duckdb

    expected_tables = {"region", "nation", "supplier", "part", "partsupp", "customer", "orders", "lineitem"}
    assert set(table_stats) == expected_tables
    for table_name, row_count in table_stats.items():
        assert row_count > 0, f"table {table_name} loaded 0 rows"

    assert table_stats["supplier"] == 100
    assert table_stats["customer"] == 1_500
    assert table_stats["orders"] == 15_000
    assert table_stats["lineitem"] > 0


def test_fk_constraint_is_still_enforced_after_ctas_sort(tpch_fk_and_sort_duckdb):
    _adapter, _bench, conn, _table_stats = tpch_fk_and_sort_duckdb

    with pytest.raises(Exception, match="[Ff]oreign key"):
        conn.execute(
            "INSERT INTO orders (o_orderkey, o_custkey, o_orderstatus, o_totalprice, "
            "o_orderdate, o_orderpriority, o_clerk, o_shippriority, o_comment) "
            "VALUES (999999999, 999999999, 'O', 1.0, '2026-01-01', '1-URGENT', 'Clerk#1', 0, 'x')"
        )

    with pytest.raises(Exception, match="[Ff]oreign key"):
        conn.execute(
            "INSERT INTO partsupp (ps_partkey, ps_suppkey, ps_availqty, ps_supplycost, ps_comment) "
            "VALUES (1, 999999999, 10, 1.0, 'x')"
        )


def test_table_data_is_physically_sorted_after_ctas_sort(tpch_fk_and_sort_duckdb):

    _adapter, _bench, conn, _table_stats = tpch_fk_and_sort_duckdb

    supp_rows = conn.execute("SELECT s_suppkey FROM supplier").fetchall()
    supp_keys = [r[0] for r in supp_rows]
    assert supp_keys == sorted(supp_keys), "supplier rows must be sorted by s_suppkey"

    orders_rows = conn.execute("SELECT o_orderkey FROM orders").fetchall()
    orders_keys = [r[0] for r in orders_rows]
    assert orders_keys == sorted(orders_keys), "orders rows must be sorted by o_orderkey"


def test_populated_parent_rewrite_preserves_dependents_and_fk_enforcement(tmp_path: Path):
    adapter = DuckDBAdapter(database_path=str(tmp_path / "populated_parent.duckdb"))
    conn = adapter.create_connection()
    conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY, value INTEGER)")
    conn.execute("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id))")
    conn.execute("INSERT INTO parent VALUES (2, 20), (1, 10)")
    conn.execute("INSERT INTO child VALUES (100, 1)")
    tuning_config = UnifiedTuningConfiguration.from_dict(
        {
            "foreign_keys": {"enabled": False, "enforce_referential_integrity": False},
            "table_tunings": {
                "parent": {
                    "table_name": "parent",
                    "sorting": [{"name": "id", "type": "INTEGER", "order": 1}],
                }
            },
        }
    )
    adapter._applied_tuning_ledger = AppliedTuningLedger()

    assert adapter.apply_ctas_sort("parent", tuning_config, conn) is False
    assert conn.execute("SELECT * FROM child").fetchall() == [(100, 1)]
    assert set(conn.execute("SELECT id FROM parent").fetchall()) == {(1,), (2,)}
    with pytest.raises(Exception, match="[Ff]oreign key"):
        conn.execute("INSERT INTO child VALUES (101, 999)")
    assert len(adapter._applied_tuning_ledger.dropped) == 1
    conn.close()


def test_physical_pk_not_null_and_fk_constraints_survive_sort_when_config_flags_are_off(tmp_path: Path):
    adapter = DuckDBAdapter(database_path=str(tmp_path / "physical_constraints.duckdb"))
    conn = adapter.create_connection()
    conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY, required_value INTEGER NOT NULL)")
    conn.execute("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id))")
    conn.execute("INSERT INTO parent VALUES (2, 20), (1, 10)")
    tuning_config = UnifiedTuningConfiguration.from_dict(
        {
            "primary_keys": {"enabled": False, "enforce_uniqueness": False},
            "foreign_keys": {"enabled": False, "enforce_referential_integrity": False},
            "table_tunings": {
                "parent": {
                    "table_name": "parent",
                    "sorting": [{"name": "id", "type": "INTEGER", "order": 1}],
                }
            },
        }
    )
    constraints_before = conn.execute(
        "SELECT table_name, constraint_type, constraint_text FROM duckdb_constraints() "
        "WHERE table_name IN ('parent', 'child') ORDER BY table_name, constraint_index"
    ).fetchall()

    assert adapter.apply_ctas_sort("parent", tuning_config, conn) is True

    constraints_after = conn.execute(
        "SELECT table_name, constraint_type, constraint_text FROM duckdb_constraints() "
        "WHERE table_name IN ('parent', 'child') ORDER BY table_name, constraint_index"
    ).fetchall()
    assert constraints_after == constraints_before
    assert conn.execute("SELECT * FROM parent").fetchall() == [(1, 10), (2, 20)]
    with pytest.raises(Exception, match="[Pp]rimary key|[Dd]uplicate"):
        conn.execute("INSERT INTO parent VALUES (1, 99)")
    with pytest.raises(Exception, match="(?i)not null"):
        conn.execute("INSERT INTO parent VALUES (3, NULL)")
    with pytest.raises(Exception, match="[Ff]oreign key"):
        conn.execute("INSERT INTO child VALUES (1, 999)")
    conn.close()


def test_active_caller_transaction_is_preserved_without_nested_begin(tmp_path: Path):
    adapter = DuckDBAdapter(database_path=str(tmp_path / "active_transaction.duckdb"))
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    conn = adapter.create_connection()
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO items VALUES (2), (1)")
    tuning_config = UnifiedTuningConfiguration.from_dict(
        {
            "table_tunings": {
                "items": {
                    "table_name": "items",
                    "sorting": [{"name": "id", "type": "INTEGER", "order": 1}],
                }
            }
        }
    )

    conn.execute("BEGIN")
    assert adapter.apply_ctas_sort("items", tuning_config, conn) is False
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone() == (2,)
    conn.execute("ROLLBACK")
    assert len(adapter._applied_tuning_ledger.dropped) == 1
    assert "active transaction" in adapter._applied_tuning_ledger.dropped[0].reason
    conn.close()


