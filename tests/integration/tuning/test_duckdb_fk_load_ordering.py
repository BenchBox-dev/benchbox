# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tuning.interface import UnifiedTuningConfiguration
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]

_SCALE_FACTOR = 0.01


def _fk_enabled_tuning_config() -> UnifiedTuningConfiguration:
    return UnifiedTuningConfiguration.from_dict(
        {
            "primary_keys": {"enabled": True},
            "foreign_keys": {"enabled": True, "enforce_referential_integrity": True},
        }
    )


@pytest.fixture(scope="module")
def tpch_fk_enforced_duckdb(tmp_path_factory: pytest.TempPathFactory):
    base = tmp_path_factory.mktemp("tpch_fk_load_order")
    db_path = str(base / "tpch.duckdb")
    adapter = DuckDBAdapter(database_path=db_path)
    adapter.unified_tuning_configuration = _fk_enabled_tuning_config()

    conn = adapter.create_connection()
    bench = TPCHBenchmark(scale_factor=_SCALE_FACTOR, output_dir=str(base / "data"))
    bench.generate_data()

    adapter.create_schema(bench, conn)
    table_stats, _duration, _extra = adapter.load_data(bench, conn, Path(base / "data"))

    yield adapter, bench, conn, table_stats
    conn.close()


def test_fk_enforced_tuned_load_completes_for_every_table(tpch_fk_enforced_duckdb):
    _adapter, _bench, _conn, table_stats = tpch_fk_enforced_duckdb

    expected_tables = {"region", "nation", "supplier", "part", "partsupp", "customer", "orders", "lineitem"}
    assert set(table_stats) == expected_tables
    for table_name, row_count in table_stats.items():
        assert row_count > 0, f"table {table_name} loaded 0 rows (FK violation would zero it out)"

    assert table_stats["orders"] == 15_000
    assert table_stats["lineitem"] > 0


def test_fk_constraint_is_actually_enforced_after_load(tpch_fk_enforced_duckdb):
    _adapter, _bench, conn, _table_stats = tpch_fk_enforced_duckdb

    with pytest.raises(Exception, match="[Ff]oreign key"):
        conn.execute(
            "INSERT INTO orders (o_orderkey, o_custkey, o_orderstatus, o_totalprice, "
            "o_orderdate, o_orderpriority, o_clerk, o_shippriority, o_comment) "
            "VALUES (999999999, 999999999, 'O', 1.0, '2026-01-01', '1-URGENT', 'Clerk#1', 0, 'x')"
        )


def test_table_loading_order_used_by_the_loader_is_fk_safe(tpch_fk_enforced_duckdb):
    _adapter, bench, _conn, table_stats = tpch_fk_enforced_duckdb

    order = bench.get_table_loading_order(list(table_stats.keys()))
    position = {name: i for i, name in enumerate(order)}

    assert position["region"] < position["nation"]
    assert position["nation"] < position["customer"]
    assert position["nation"] < position["supplier"]
    assert position["customer"] < position["orders"]
    assert position["orders"] < position["lineitem"]
    assert position["part"] < position["lineitem"]
    assert position["supplier"] < position["lineitem"]
    assert position["part"] < position["partsupp"]
    assert position["supplier"] < position["partsupp"]


