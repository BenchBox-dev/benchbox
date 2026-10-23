# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tuning.applied_ledger import (
    APPLIED_UNVERIFIED,
    APPLIED_VERIFIED,
    PHASE_DDL,
    AppliedTuningLedger,
)
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.core.tuning.introspection import corroborate
from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter
from benchbox.platforms.clickhouse.client import ClickHouseLocalClient
from benchbox.platforms.clickhouse.introspection import ClickHouseTuningIntrospector
from tests.utilities.optional_engines import require_chdb

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]

chdb = require_chdb()

SOURCE_DDL = "CREATE TABLE lineitem (l_orderkey INTEGER, l_linenumber INTEGER, l_shipdate DATE)"
TUNED_PARTITION_CLAUSE = " PARTITION BY (toYYYYMM(l_shipdate))"


def _tuned_adapter() -> ClickHouseAdapter:
    adapter = ClickHouseAdapter()
    adapter.tuning_enabled = True
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    adapter._applied_layout_operations = []
    adapter.unified_tuning_configuration = _combined_tuning_config()
    return adapter


def _combined_tuning_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.table_tunings["LINEITEM"] = TableTuning(
        table_name="LINEITEM",
        sorting=[
            TuningColumn(name="l_orderkey", type="INTEGER", order=1),
            TuningColumn(name="l_linenumber", type="INTEGER", order=2),
        ],
        partitioning=[TuningColumn(name="l_shipdate", type="DATE", order=1)],
    )
    return config


def _combined_tunings() -> dict:
    return _combined_tuning_config().table_tunings


class _Benchmark:
    def get_create_tables_sql(self, **_kwargs):
        return SOURCE_DDL

    def get_schema(self):
        return {}


@pytest.fixture
def connection():
    conn = ClickHouseLocalClient()
    conn.execute("DROP TABLE IF EXISTS lineitem")
    yield conn
    conn.execute("DROP TABLE IF EXISTS lineitem")


def _run(connection, *, apply_partition: bool) -> tuple[str, dict[str, str]]:
    adapter = _tuned_adapter()
    tunings = _combined_tunings()
    optimized = adapter._translate_schema_statement(SOURCE_DDL, tunings, nullable_columns=set())

    executed = optimized if apply_partition else optimized.replace(TUNED_PARTITION_CLAUSE, "")
    connection.execute(executed)
    adapter._record_tuned_sort_key_op(SOURCE_DDL, optimized, "lineitem", tunings)

    status, receipt = adapter._corroborate_applied_ledger(connection, APPLIED_UNVERIFIED)
    verdicts = {entry["kind"]: entry["verdict"] for entry in receipt["entries"] if entry.get("kind")}
    return status, verdicts


class TestAppliedLedgerAgainstLiveClickHouse:
    def test_multicolumn_partition_key_earns_verification(self, connection):
        ddl = "CREATE TABLE lineitem (a Int32, b Int32, c Int32) ENGINE=MergeTree PARTITION BY (a, b) ORDER BY (c)"
        connection.execute(ddl)
        ledger = AppliedTuningLedger()
        ledger.record(ddl, PHASE_DDL, table="lineitem")

        state = ClickHouseTuningIntrospector().introspect(connection, ledger)
        receipt = corroborate(ledger, state)

        assert receipt.corroborated is True
        assert {entry.kind: entry.verdict for entry in receipt.entries} == {
            "sort_key": "corroborated",
            "partition_key": "corroborated",
        }

    def test_catalog_expression_sugar_mismatch_fails_closed(self, connection):
        ddl = (
            "CREATE TABLE lineitem (ts DateTime, id Int32) ENGINE=MergeTree "
            "PARTITION BY (toStartOfInterval(ts, INTERVAL 1 DAY)) ORDER BY (id)"
        )
        connection.execute(ddl)
        ledger = AppliedTuningLedger()
        ledger.record(ddl, PHASE_DDL, table="lineitem")

        state = ClickHouseTuningIntrospector().introspect(connection, ledger)
        receipt = corroborate(ledger, state)
        partition = next(entry for entry in receipt.entries if entry.kind == "partition_key")

        assert receipt.corroborated is False
        assert partition.verdict == "mismatch"
        assert partition.evidence == {"partition_key": "toStartOfInterval(ts, toIntervalDay(1))"}

    def test_catalog_reports_the_partition_expression_with_its_closing_paren(self, connection):
        adapter = _tuned_adapter()
        optimized = adapter._translate_schema_statement(SOURCE_DDL, _combined_tunings(), nullable_columns=set())
        connection.execute(optimized)

        rows = list(
            connection.execute("SELECT name, sorting_key, partition_key FROM system.tables WHERE name = 'lineitem'")
        )
        assert rows, "live ClickHouse reported no lineitem row in system.tables"
        _, sorting_key, partition_key = rows[0][0], rows[0][1], rows[0][2]
        assert partition_key == "toYYYYMM(l_shipdate)"
        assert sorting_key == "l_orderkey, l_linenumber"

    def test_date_partitioned_table_earns_verification(self, connection):
        status, verdicts = _run(connection, apply_partition=True)
        assert status == APPLIED_VERIFIED
        assert verdicts["sort_key"] == "corroborated"
        assert verdicts["partition_key"] == "corroborated"

    def test_unapplied_partition_key_blocks_verification(self, connection):
        status, verdicts = _run(connection, apply_partition=False)
        assert status == APPLIED_UNVERIFIED
        assert verdicts["sort_key"] == "corroborated"
        assert verdicts["partition_key"] in {"absent", "mismatch"}

    def test_ledger_orders_create_table_before_optimize(self, connection):
        adapter = _tuned_adapter()
        adapter.create_schema(_Benchmark(), connection)
        connection.execute("OPTIMIZE TABLE lineitem FINAL")
        adapter._applied_tuning_ledger.record("OPTIMIZE TABLE lineitem FINAL", PHASE_DDL)
        adapter._fold_layout_operations_into_ledger()

        order = [s.statement.split()[0].upper() for s in adapter._applied_tuning_ledger.executed_statements]
        assert order == ["CREATE", "OPTIMIZE"]
