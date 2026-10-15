# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.tuning.applied_ledger import (
    APPLIED_UNVERIFIED,
    APPLIED_VERIFIED,
    PHASE_DDL,
    PHASE_SESSION,
    AppliedTuningLedger,
)
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.core.tuning.introspection import KIND_PARTITION_KEY, KIND_SORT_KEY, corroborate
from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter
from benchbox.platforms.clickhouse.introspection import ClickHouseTuningIntrospector

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.usefixtures("chdb_probe_satisfied"),
]


class _FakeCHConnection:
    def __init__(self, rows, fail: bool = False):
        self._rows = rows
        self._fail = fail
        self.queries: list[str] = []

    def execute(self, query, *args, **kwargs):
        self.queries.append(str(query))
        if self._fail:
            raise RuntimeError("system.tables unavailable")
        return self._rows


def _optimize_ledger() -> AppliedTuningLedger:

    ledger = AppliedTuningLedger()
    ledger.record("OPTIMIZE TABLE lineitem", PHASE_DDL)
    ledger.record("SET max_threads = 4", PHASE_SESSION)
    return ledger


class TestClickHouseIntrospector:
    def test_reports_sort_and_partition_keys(self):
        rows = [("lineitem", "l_orderkey, l_linenumber", "toYYYYMM(l_shipdate)")]
        state = ClickHouseTuningIntrospector().introspect(_FakeCHConnection(rows), _optimize_ledger())
        assert state.error is None
        kinds = {obj.kind: obj for obj in state.objects}
        assert kinds[KIND_SORT_KEY].columns == ("l_orderkey", "l_linenumber")
        assert kinds[KIND_PARTITION_KEY].table == "lineitem"

    def test_canonicalizes_tuple_valued_partition_key(self):
        rows = [("lineitem", "l_orderkey", "tuple(region, shard)")]
        state = ClickHouseTuningIntrospector().introspect(_FakeCHConnection(rows), _optimize_ledger())
        partition = next(obj for obj in state.objects if obj.kind == KIND_PARTITION_KEY)
        assert partition.columns == ("region", "shard")

    def test_query_uses_structured_catalog_not_show_create(self):
        conn = _FakeCHConnection([("lineitem", "l_orderkey", "")])
        ClickHouseTuningIntrospector().introspect(conn, _optimize_ledger())
        assert conn.queries, "introspector must issue a catalog query"
        q = conn.queries[0].lower()
        assert "system.tables" in q and "show create" not in q
        assert "lower(name) in ('lineitem')" in q

    def test_bounded_to_ledger_tables(self):
        rows = [
            ("lineitem", "l_orderkey", ""),
            ("nation", "n_nationkey", ""),
        ]
        state = ClickHouseTuningIntrospector().introspect(_FakeCHConnection(rows), _optimize_ledger())
        tables = {obj.table for obj in state.objects}
        assert tables == {"lineitem"}

    def test_raw_rows_at_catalog_cap_mark_snapshot_truncated(self):
        rows = [("lineitem", f"l_orderkey_{index}", "") for index in range(1000)]
        state = ClickHouseTuningIntrospector().introspect(_FakeCHConnection(rows), _optimize_ledger())
        assert state.truncated is True

    def test_raw_rows_under_catalog_cap_do_not_mark_truncated(self):
        rows = [("lineitem", f"l_orderkey_{index}", "") for index in range(999)]
        state = ClickHouseTuningIntrospector().introspect(_FakeCHConnection(rows), _optimize_ledger())
        assert state.truncated is False

    def test_non_fatal_on_query_failure(self):
        state = ClickHouseTuningIntrospector().introspect(_FakeCHConnection([], fail=True), _optimize_ledger())
        assert state.error is not None
        assert state.objects == []

    def test_optimize_ledger_reports_keys_but_stays_unverified(self):

        rows = [("lineitem", "l_orderkey, l_linenumber", "")]
        ledger = _optimize_ledger()
        receipt = corroborate(ledger, ClickHouseTuningIntrospector().introspect(_FakeCHConnection(rows), ledger))
        assert receipt.corroborated is False
        assert receipt.summary["gate_relevant_total"] == 0
        assert any(o.kind == KIND_SORT_KEY for o in receipt.observed)


def _sorted_tuning_config(*, partition_on: str | None = None) -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.table_tunings["LINEITEM"] = TableTuning(
        table_name="LINEITEM",
        sorting=[
            TuningColumn(name="l_orderkey", type="INTEGER", order=1),
            TuningColumn(name="l_linenumber", type="INTEGER", order=2),
        ],
        partitioning=([TuningColumn(name=partition_on, type="DATE", order=1)] if partition_on else []),
    )
    return config


def _partitioned_only_tuning_config() -> UnifiedTuningConfiguration:

    config = UnifiedTuningConfiguration()
    config.table_tunings["ORDERS"] = TableTuning(
        table_name="ORDERS",
        partitioning=[TuningColumn(name="o_orderdate", type="DATE", order=1)],
    )
    return config


def _sort_key_statements(ledger: AppliedTuningLedger) -> list:
    return [s for s in ledger.executed_statements if s.mechanism in {"sort_key", "table_keys"}]


class TestTunedSortKeyFold:
    def _tuned_adapter(self) -> ClickHouseAdapter:
        adapter = ClickHouseAdapter()
        adapter.tuning_enabled = True
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        adapter._applied_layout_operations = []
        return adapter

    def test_tuned_order_by_records_sort_key_statement(self):
        adapter = self._tuned_adapter()
        tunings = _sorted_tuning_config().table_tunings
        original = "CREATE TABLE lineitem (l_orderkey INTEGER, l_linenumber INTEGER, l_comment VARCHAR)"
        optimized = adapter._optimize_table_definition(original, tunings, nullable_columns=set())
        assert "ORDER BY (l_orderkey, l_linenumber)" in optimized

        adapter._record_tuned_sort_key_op(original, optimized, "lineitem", tunings)
        recorded = _sort_key_statements(adapter._applied_tuning_ledger)
        assert len(recorded) == 1
        assert recorded[0].phase == PHASE_DDL and recorded[0].table == "lineitem"

    def test_create_table_recorded_before_optimize(self):

        adapter = self._tuned_adapter()
        tunings = _sorted_tuning_config().table_tunings
        original = "CREATE TABLE lineitem (l_orderkey INTEGER, l_linenumber INTEGER, l_comment VARCHAR)"
        optimized = adapter._optimize_table_definition(original, tunings, nullable_columns=set())

        adapter._record_tuned_sort_key_op(original, optimized, "lineitem", tunings)

        adapter._applied_tuning_ledger.record("OPTIMIZE TABLE lineitem FINAL", PHASE_DDL)
        adapter._fold_layout_operations_into_ledger()

        order = [s.statement.split()[0].upper() for s in adapter._applied_tuning_ledger.executed_statements]
        assert order == ["CREATE", "OPTIMIZE"]

    def test_full_flow_reaches_applied_verified(self):
        adapter = self._tuned_adapter()
        tunings = _sorted_tuning_config().table_tunings
        original = "CREATE TABLE lineitem (l_orderkey INTEGER, l_linenumber INTEGER, l_comment VARCHAR)"
        optimized = adapter._optimize_table_definition(original, tunings, nullable_columns=set())
        adapter._record_tuned_sort_key_op(original, optimized, "lineitem", tunings)

        conn = _FakeCHConnection([("lineitem", "l_orderkey, l_linenumber", "")])
        status, receipt = adapter._corroborate_applied_ledger(conn, APPLIED_UNVERIFIED)
        assert status == APPLIED_VERIFIED
        assert receipt["corroborated"] is True

    def test_baseline_order_by_not_recorded(self):

        adapter = self._tuned_adapter()
        tunings = _sorted_tuning_config().table_tunings
        original = "CREATE TABLE nation (n_nationkey INTEGER PRIMARY KEY, n_name VARCHAR)"
        optimized = adapter._optimize_table_definition(original, tunings, nullable_columns=set())
        adapter._record_tuned_sort_key_op(original, optimized, "nation", tunings)
        assert _sort_key_statements(adapter._applied_tuning_ledger) == []

    def test_no_record_when_tuning_disabled(self):
        adapter = self._tuned_adapter()
        adapter.tuning_enabled = False
        tunings = _sorted_tuning_config().table_tunings
        original = "CREATE TABLE lineitem (l_orderkey INTEGER, l_linenumber INTEGER)"
        adapter._record_tuned_sort_key_op(original, original, "lineitem", tunings)
        assert adapter._applied_tuning_ledger.executed_statements == []

    def test_schema_phase_tuning_exports_hash_without_duplicate_ddl(self, tmp_path):
        adapter = self._tuned_adapter()
        config = _sorted_tuning_config()
        adapter.unified_tuning_configuration = config

        benchmark = SimpleNamespace(
            output_dir=tmp_path,
            get_create_tables_sql=lambda dialect, tuning_config: (
                "CREATE TABLE lineitem (l_orderkey INTEGER, l_linenumber INTEGER, l_shipdate DATE)"
            ),
            get_schema=lambda: {
                "lineitem": {
                    "columns": [
                        {"name": "l_orderkey", "nullable": False},
                        {"name": "l_linenumber", "nullable": False},
                        {"name": "l_shipdate", "nullable": False},
                    ]
                }
            },
        )
        connection = _FakeCHConnection([])
        adapter.apply_unified_tuning = lambda _config, _connection: None
        adapter.save_tuning_metadata = lambda _connection: True
        adapter.load_data = lambda _benchmark, _connection, _data_dir: ({"lineitem": 1}, 0.0, None)

        adapter._setup_fresh_database_phases(benchmark, connection, config)

        assert len(adapter._applied_tuning_ledger.executed_statements) == 1
        assert adapter._applied_tuning_ledger.executed_statements[0].table == "lineitem"
        assert adapter._applied_tuning_ledger.applied_ledger_hash()


class TestCombinedPartitionAndSortKey:
    def _tuned_adapter(self) -> ClickHouseAdapter:
        adapter = ClickHouseAdapter()
        adapter.tuning_enabled = True
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        adapter._applied_layout_operations = []
        return adapter

    def _record_combined(self, adapter: ClickHouseAdapter) -> str:
        tunings = _sorted_tuning_config(partition_on="l_shipdate").table_tunings
        original = (
            "CREATE TABLE lineitem (l_orderkey INTEGER, l_linenumber INTEGER, l_shipdate DATE, l_comment VARCHAR)"
        )
        optimized = adapter._optimize_table_definition(original, tunings, nullable_columns=set())
        adapter._record_tuned_sort_key_op(original, optimized, "lineitem", tunings)
        return optimized

    def test_combined_ddl_renders_both_clauses(self):
        adapter = self._tuned_adapter()
        optimized = self._record_combined(adapter)
        assert "PARTITION BY" in optimized.upper()
        assert "ORDER BY (l_orderkey, l_linenumber)" in optimized

    def test_uncorroborated_partition_key_blocks_verification(self):
        adapter = self._tuned_adapter()
        self._record_combined(adapter)

        conn = _FakeCHConnection([("lineitem", "l_orderkey, l_linenumber", "tuple()")])
        status, receipt = adapter._corroborate_applied_ledger(conn, APPLIED_UNVERIFIED)
        assert status == APPLIED_UNVERIFIED
        assert receipt["corroborated"] is False
        verdicts = {e["kind"]: e["verdict"] for e in receipt["entries"] if e.get("kind")}
        assert verdicts[KIND_SORT_KEY] == "corroborated"
        assert verdicts[KIND_PARTITION_KEY] == "mismatch"

    def test_absent_partition_key_blocks_verification(self):
        adapter = self._tuned_adapter()
        self._record_combined(adapter)

        conn = _FakeCHConnection([("lineitem", "l_orderkey, l_linenumber", "")])
        status, receipt = adapter._corroborate_applied_ledger(conn, APPLIED_UNVERIFIED)
        assert status == APPLIED_UNVERIFIED
        assert receipt["corroborated"] is False

    def test_both_keys_corroborated_earns_verification(self):
        adapter = self._tuned_adapter()
        self._record_combined(adapter)

        conn = _FakeCHConnection([("lineitem", "l_orderkey, l_linenumber", "toYYYYMM(l_shipdate)")])
        status, receipt = adapter._corroborate_applied_ledger(conn, APPLIED_UNVERIFIED)
        assert status == APPLIED_VERIFIED
        assert receipt["corroborated"] is True

    def test_partition_only_table_is_recorded(self):

        adapter = self._tuned_adapter()
        tunings = _partitioned_only_tuning_config().table_tunings
        original = "CREATE TABLE orders (o_orderkey INTEGER PRIMARY KEY, o_orderdate DATE)"
        optimized = adapter._optimize_table_definition(original, tunings, nullable_columns=set())
        assert "PARTITION BY (toYYYYMM(o_orderdate))" in optimized

        adapter._record_tuned_sort_key_op(original, optimized, "orders", tunings)
        recorded = adapter._applied_tuning_ledger.executed_statements
        assert len(recorded) == 1 and recorded[0].mechanism == "partition_key"

    def test_partition_only_table_with_order_by_literal_decoy_is_recorded(self):
        adapter = self._tuned_adapter()
        tunings = _partitioned_only_tuning_config().table_tunings
        original = (
            "CREATE TABLE orders (o_orderkey INTEGER PRIMARY KEY, "
            "note VARCHAR DEFAULT 'ORDER BY (decoy)', o_orderdate DATE)"
        )
        optimized = adapter._optimize_table_definition(original, tunings, nullable_columns=set())

        adapter._record_tuned_sort_key_op(original, optimized, "orders", tunings)

        recorded = adapter._applied_tuning_ledger.executed_statements
        assert len(recorded) == 1 and recorded[0].mechanism == "partition_key"

    def test_partition_only_table_with_unapplied_partition_blocks_run(self):
        adapter = self._tuned_adapter()

        sort_tunings = _sorted_tuning_config().table_tunings
        lineitem = "CREATE TABLE lineitem (l_orderkey INTEGER, l_linenumber INTEGER)"
        adapter._record_tuned_sort_key_op(
            lineitem,
            adapter._optimize_table_definition(lineitem, sort_tunings, nullable_columns=set()),
            "lineitem",
            sort_tunings,
        )

        part_tunings = _partitioned_only_tuning_config().table_tunings
        orders = "CREATE TABLE orders (o_orderkey INTEGER PRIMARY KEY, o_orderdate DATE)"
        adapter._record_tuned_sort_key_op(
            orders,
            adapter._optimize_table_definition(orders, part_tunings, nullable_columns=set()),
            "orders",
            part_tunings,
        )

        conn = _FakeCHConnection(
            [
                ("lineitem", "l_orderkey, l_linenumber", ""),
                ("orders", "o_orderkey", ""),
            ]
        )
        status, receipt = adapter._corroborate_applied_ledger(conn, APPLIED_UNVERIFIED)
        assert status == APPLIED_UNVERIFIED
        assert receipt["corroborated"] is False

    def test_expression_partition_key_is_not_truncated(self):

        adapter = self._tuned_adapter()
        optimized = self._record_combined(adapter)
        assert "PARTITION BY (toYYYYMM(l_shipdate))" in optimized

        conn = _FakeCHConnection([("lineitem", "l_orderkey, l_linenumber", "toYYYYMM(l_shipdate)")])
        _, receipt = adapter._corroborate_applied_ledger(conn, APPLIED_UNVERIFIED)
        partition_entry = next(e for e in receipt["entries"] if e.get("kind") == KIND_PARTITION_KEY)
        assert partition_entry["expected_columns"] == ["toyyyymm(l_shipdate)"]
        assert partition_entry["verdict"] == "corroborated"
