from __future__ import annotations

import duckdb
import pytest

from benchbox.core.tuning.applied_ledger import PHASE_DDL, AppliedTuningLedger, recording_connection
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.platforms.ducklake import DuckLakeAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

UNSUPPORTED_REASON = "ducklake: CREATE INDEX unsupported"


def _config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.table_tunings["LINEITEM"] = TableTuning(
        table_name="LINEITEM",
        sorting=[TuningColumn(name="L_ORDERKEY", type="INTEGER", order=1)],
        clustering=[TuningColumn(name="L_PARTKEY", type="INTEGER", order=1)],
    )
    return config


def _tuned(adapter: DuckDBAdapter) -> tuple[AppliedTuningLedger, duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(":memory:")
    connection.execute('CREATE TABLE "lineitem" ("l_orderkey" INT, "l_partkey" INT)')
    ledger = AppliedTuningLedger()
    adapter.tuning_enabled = True
    adapter._applied_tuning_ledger = ledger
    adapter.apply_unified_tuning(_config(), recording_connection(connection, ledger, PHASE_DDL))
    return ledger, connection


def test_ducklake_records_dropped_index_intents_instead_of_issuing_create_index():
    ledger, connection = _tuned(DuckLakeAdapter())

    assert ledger.statements == []
    assert {(item.intent, item.reason) for item in ledger.dropped} == {
        ("sorting:LINEITEM", UNSUPPORTED_REASON),
        ("clustering:LINEITEM", UNSUPPORTED_REASON),
    }
    assert connection.execute("SELECT count(*) FROM duckdb_indexes()").fetchone() == (0,)


def test_ducklake_does_not_use_the_duckdb_index_introspector():
    assert DuckLakeAdapter().get_tuning_introspector() is None
    assert DuckDBAdapter().get_tuning_introspector() is not None


def test_duckdb_still_creates_the_indexes():
    ledger, connection = _tuned(DuckDBAdapter())

    assert ledger.dropped == []
    assert {row[0] for row in connection.execute("SELECT index_name FROM duckdb_indexes()").fetchall()} == {
        "idx_lineitem_sort",
        "idx_lineitem_cluster",
    }


