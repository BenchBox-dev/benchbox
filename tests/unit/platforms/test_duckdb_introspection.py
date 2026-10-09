# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from benchbox import SSB
from benchbox.cli.config import ConfigManager
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tuning.applied_ledger import (
    APPLIED_UNVERIFIED,
    APPLIED_VERIFIED,
    PHASE_DDL,
    PHASE_POST_LOAD,
    AppliedTuningLedger,
    recording_connection,
)
from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
from benchbox.core.tuning.introspection import (
    ABSENT,
    CONSTRAINT_FOREIGN_KEY,
    CONSTRAINT_PRIMARY_KEY,
    CONSTRAINT_UNIQUE,
    CORROBORATED,
    KIND_CONSTRAINT,
    KIND_INDEX,
    MISMATCH,
    UNVERIFIABLE,
    corroborate,
)
from benchbox.platforms import duckdb_introspection
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.platforms.duckdb_introspection import DuckDBTuningIntrospector

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TEMPLATES = Path(__file__).resolve().parents[3] / "benchbox" / "core" / "tuning" / "templates" / "duckdb"


def _con_with_indexes() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE LINEITEM(L_ORDERKEY INTEGER, L_LINENUMBER INTEGER, L_COMMENT VARCHAR)")
    con.execute("CREATE TABLE ORDERS(O_ORDERKEY INTEGER, O_CUSTKEY INTEGER)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_orders_sort ON ORDERS (O_ORDERKEY, O_CUSTKEY)")
    return con


def _ledger_for(*statements: str) -> AppliedTuningLedger:
    ledger = AppliedTuningLedger()
    for stmt in statements:
        ledger.record(stmt, PHASE_DDL)
    return ledger


class TestDuckDBIntrospector:
    def test_reads_indexes_as_structured_facts(self):
        con = _con_with_indexes()
        ledger = _ledger_for(
            "CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)",
            "CREATE INDEX IF NOT EXISTS idx_orders_sort ON ORDERS (O_ORDERKEY, O_CUSTKEY)",
        )
        state = DuckDBTuningIntrospector().introspect(con, ledger)
        assert state.error is None
        by_table = {obj.table.lower(): obj for obj in state.objects if obj.kind == KIND_INDEX}
        assert by_table["lineitem"].columns == ("l_orderkey", "l_linenumber")
        assert by_table["orders"].columns == ("o_orderkey", "o_custkey")

    def test_bounded_to_ledger_tables(self):
        con = _con_with_indexes()
        con.execute("CREATE TABLE PART(P_PARTKEY INTEGER)")
        con.execute("CREATE INDEX idx_part_sort ON PART (P_PARTKEY)")

        ledger = _ledger_for("CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)")
        state = DuckDBTuningIntrospector().introspect(con, ledger)
        tables = {obj.table.lower() for obj in state.objects}
        assert "part" not in tables and "lineitem" in tables

    def test_non_fatal_on_bad_connection(self):
        class _Boom:
            def execute(self, *_a, **_k):
                raise RuntimeError("catalog exploded")

        state = DuckDBTuningIntrospector().introspect(_Boom(), _ledger_for("CREATE INDEX i ON T (a)"))
        assert state.error is not None
        assert state.objects == []


class TestCorroborateAgainstRealCatalog:
    def test_persisted_indexes_corroborate(self):
        con = _con_with_indexes()
        ledger = _ledger_for(
            "CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)",
            "CREATE INDEX IF NOT EXISTS idx_orders_sort ON ORDERS (O_ORDERKEY, O_CUSTKEY)",
        )
        receipt = corroborate(ledger, DuckDBTuningIntrospector().introspect(con, ledger))
        assert receipt.corroborated is True
        assert all(e.verdict == CORROBORATED for e in receipt.entries)

    def test_recording_connection_populates_then_corroborates(self):

        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE LINEITEM(L_ORDERKEY INTEGER, L_LINENUMBER INTEGER)")
        ledger = AppliedTuningLedger()
        rec = recording_connection(con, ledger, PHASE_DDL)
        rec.execute("CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)")
        receipt = corroborate(ledger, DuckDBTuningIntrospector().introspect(con, ledger))
        assert receipt.corroborated is True

    def test_induced_mismatch_downgrades_with_diff(self):
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE LINEITEM(L_ORDERKEY INTEGER, L_LINENUMBER INTEGER)")
        con.execute("CREATE INDEX idx_lineitem_sort ON LINEITEM (L_ORDERKEY)")
        ledger = _ledger_for("CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)")
        receipt = corroborate(ledger, DuckDBTuningIntrospector().introspect(con, ledger))
        assert receipt.corroborated is False
        assert receipt.entries[0].diff is not None

    def test_dropped_index_is_absent(self):

        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE LINEITEM(L_ORDERKEY INTEGER, L_LINENUMBER INTEGER)")
        ledger = _ledger_for("CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)")
        receipt = corroborate(ledger, DuckDBTuningIntrospector().introspect(con, ledger))
        assert receipt.corroborated is False
        assert receipt.entries[0].verdict == ABSENT


class TestAdapterUpgradeWiring:
    def test_adapter_upgrades_to_verified_only_via_corroboration(self):
        con = _con_with_indexes()
        adapter = DuckDBAdapter()
        adapter._applied_tuning_ledger = _ledger_for(
            "CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)",
            "CREATE INDEX IF NOT EXISTS idx_orders_sort ON ORDERS (O_ORDERKEY, O_CUSTKEY)",
        )
        status, receipt = adapter._corroborate_applied_ledger(con, APPLIED_UNVERIFIED)
        assert status == APPLIED_VERIFIED
        assert receipt["corroborated"] is True
        assert receipt["summary"]["corroborated"] == 2

    def test_adapter_stays_unverified_on_mismatch(self):
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE LINEITEM(L_ORDERKEY INTEGER, L_LINENUMBER INTEGER)")
        con.execute("CREATE INDEX idx_lineitem_sort ON LINEITEM (L_ORDERKEY)")
        adapter = DuckDBAdapter()
        adapter._applied_tuning_ledger = _ledger_for(
            "CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)"
        )
        status, receipt = adapter._corroborate_applied_ledger(con, APPLIED_UNVERIFIED)
        assert status == APPLIED_UNVERIFIED
        assert receipt["corroborated"] is False

    def test_adapter_non_unverified_status_is_untouched(self):

        con = _con_with_indexes()
        adapter = DuckDBAdapter()
        adapter._applied_tuning_ledger = _ledger_for(
            "CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)"
        )
        status, receipt = adapter._corroborate_applied_ledger(con, "noop")
        assert status == "noop"
        assert receipt is None

    def test_adapter_introspection_failure_stays_unverified(self):
        class _Boom:
            def execute(self, *_a, **_k):
                raise RuntimeError("boom")

        adapter = DuckDBAdapter()
        adapter._applied_tuning_ledger = _ledger_for(
            "CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)"
        )
        status, receipt = adapter._corroborate_applied_ledger(_Boom(), APPLIED_UNVERIFIED)
        assert status == APPLIED_UNVERIFIED

        assert receipt is not None and receipt["corroborated"] is False

    def test_duckdb_adapter_exposes_introspector(self):
        assert isinstance(DuckDBAdapter().get_tuning_introspector(), DuckDBTuningIntrospector)


def _sorted_tuning_config() -> UnifiedTuningConfiguration:
    config = UnifiedTuningConfiguration()
    config.table_tunings["LINEITEM"] = TableTuning(
        table_name="LINEITEM",
        sorting=[
            TuningColumn(name="L_ORDERKEY", type="INTEGER", order=1),
            TuningColumn(name="L_LINENUMBER", type="INTEGER", order=2),
        ],
    )
    return config


class TestCtasSortIndexReCreation:
    def _adapter_with_loaded_table(self) -> tuple[DuckDBAdapter, object]:
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE LINEITEM(L_ORDERKEY INTEGER, L_LINENUMBER INTEGER, L_COMMENT VARCHAR)")
        con.execute("INSERT INTO LINEITEM VALUES (3, 1, 'c'), (1, 2, 'a'), (2, 1, 'b')")
        adapter = DuckDBAdapter()
        adapter.tuning_enabled = True
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        adapter._applied_layout_operations = []
        return adapter, con

    def test_ctas_sort_recreates_index_and_records_post_load_op(self):
        adapter, con = self._adapter_with_loaded_table()
        config = _sorted_tuning_config()

        adapter.apply_unified_tuning(config, recording_connection(con, adapter._applied_tuning_ledger, PHASE_DDL))

        assert adapter.apply_ctas_sort("LINEITEM", config, con) is True

        indexes = {row[0] for row in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
        assert "idx_lineitem_sort" in indexes
        post_load = [op for op in adapter._applied_layout_operations if op["mechanism"] == "sort_index"]
        assert len(post_load) == 1
        assert post_load[0]["phase"] == PHASE_POST_LOAD
        assert post_load[0]["status"] == "applied"

    def test_full_flow_reaches_applied_verified(self):
        adapter, con = self._adapter_with_loaded_table()
        config = _sorted_tuning_config()
        adapter.apply_unified_tuning(config, recording_connection(con, adapter._applied_tuning_ledger, PHASE_DDL))
        adapter.apply_ctas_sort("LINEITEM", config, con)
        adapter._fold_layout_operations_into_ledger()
        status, receipt = adapter._corroborate_applied_ledger(con, APPLIED_UNVERIFIED)
        assert status == APPLIED_VERIFIED
        assert receipt["corroborated"] is True

    def test_dry_run_does_not_recreate_index(self):
        adapter, con = self._adapter_with_loaded_table()
        adapter.dry_run_mode = True
        config = _sorted_tuning_config()
        adapter.apply_ctas_sort("LINEITEM", config, con)

        assert not [op for op in adapter._applied_layout_operations if op["mechanism"] == "sort_index"]


_CONSTRAINED_DDL = (
    "CREATE TABLE customer (c_custkey INTEGER PRIMARY KEY, c_name VARCHAR NOT NULL)",
    "CREATE TABLE orders (o_orderkey INTEGER NOT NULL, o_custkey INTEGER, o_note VARCHAR, "
    "o_total INTEGER CHECK (o_total >= 0), PRIMARY KEY (o_orderkey), UNIQUE (o_custkey, o_note), "
    "FOREIGN KEY (o_custkey) REFERENCES customer(c_custkey))",
)
_PARSED_ORDERS_DDL = (
    "CREATE TABLE orders (o_orderkey INTEGER NOT NULL, o_custkey INTEGER, o_note VARCHAR, "
    "PRIMARY KEY (o_orderkey), UNIQUE (o_custkey, o_note), FOREIGN KEY (o_custkey) REFERENCES customer(c_custkey))"
)


def _constrained_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    for statement in _CONSTRAINED_DDL:
        con.execute(statement)
    return con


def _constraint_facts(state) -> dict[tuple[str, str], object]:
    return {(obj.table.lower(), obj.constraint_type): obj for obj in state.objects if obj.kind == KIND_CONSTRAINT}


class TestDuckDBConstraintFacts:
    def test_reads_primary_unique_and_foreign_keys_as_structured_facts(self):
        con = _constrained_connection()
        state = DuckDBTuningIntrospector().introspect(con, _ledger_for(*_CONSTRAINED_DDL))

        assert state.error is None
        assert state.constraint_types == {CONSTRAINT_PRIMARY_KEY, CONSTRAINT_UNIQUE, CONSTRAINT_FOREIGN_KEY}
        facts = _constraint_facts(state)
        assert set(facts) == {
            ("customer", CONSTRAINT_PRIMARY_KEY),
            ("orders", CONSTRAINT_PRIMARY_KEY),
            ("orders", CONSTRAINT_UNIQUE),
            ("orders", CONSTRAINT_FOREIGN_KEY),
        }
        assert facts[("orders", CONSTRAINT_UNIQUE)].columns == ("o_custkey", "o_note")
        fk = facts[("orders", CONSTRAINT_FOREIGN_KEY)]
        assert (fk.columns, fk.referenced_table, fk.referenced_columns) == (("o_custkey",), "customer", ("c_custkey",))
        assert fk.evidence["referenced_column_names"] == ["c_custkey"]

    def test_not_null_and_check_rows_are_not_read(self):
        con = _constrained_connection()
        state = DuckDBTuningIntrospector().introspect(con, _ledger_for(*_CONSTRAINED_DDL))
        assert {obj.constraint_type for obj in state.objects} <= {
            CONSTRAINT_PRIMARY_KEY,
            CONSTRAINT_UNIQUE,
            CONSTRAINT_FOREIGN_KEY,
        }

    def test_bounded_to_ledger_tables_in_the_current_schema(self):
        con = _constrained_connection()
        con.execute("CREATE TABLE part (p_partkey INTEGER PRIMARY KEY)")
        con.execute("CREATE SCHEMA other")
        con.execute("CREATE TABLE other.customer (c_name VARCHAR PRIMARY KEY, c_custkey INTEGER)")

        state = DuckDBTuningIntrospector().introspect(con, _ledger_for(_CONSTRAINED_DDL[0]))

        assert [(obj.table, obj.columns) for obj in state.objects] == [("customer", ("c_custkey",))]

    def test_ledger_statements_corroborate_against_the_catalog(self):
        con = _constrained_connection()
        ledger = _ledger_for(_CONSTRAINED_DDL[0], _PARSED_ORDERS_DDL)
        receipt = corroborate(ledger, DuckDBTuningIntrospector().introspect(con, ledger))
        assert receipt.corroborated is True
        assert receipt.summary == {"corroborated": 4, "gate_relevant_total": 4}

    def test_check_constraint_statement_stays_unverifiable(self):
        con = _constrained_connection()
        ledger = _ledger_for(*_CONSTRAINED_DDL)
        receipt = corroborate(ledger, DuckDBTuningIntrospector().introspect(con, ledger))
        assert receipt.corroborated is False
        assert [entry.verdict for entry in receipt.entries] == [CORROBORATED, UNVERIFIABLE]

    def test_foreign_key_to_another_table_in_the_catalog_is_mismatch(self):
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE customer (c_custkey INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE supplier (s_suppkey INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE orders (o_custkey INTEGER, FOREIGN KEY (o_custkey) REFERENCES supplier(s_suppkey))")
        ledger = _ledger_for(
            "CREATE TABLE orders (o_custkey INTEGER, FOREIGN KEY (o_custkey) REFERENCES customer(c_custkey))"
        )
        receipt = corroborate(ledger, DuckDBTuningIntrospector().introspect(con, ledger))
        [entry] = receipt.entries
        assert (entry.constraint_type, entry.verdict) == (CONSTRAINT_FOREIGN_KEY, MISMATCH)

    def test_missing_referenced_catalog_columns_leave_foreign_keys_unverifiable(self):
        con = _constrained_connection()

        class _WithoutReferences:
            def execute(self, sql, params=None):
                if "duckdb_constraints()" in sql:
                    sql = sql.replace("SELECT *", "SELECT * EXCLUDE (referenced_table, referenced_column_names)")
                return con.execute(sql, params) if params is not None else con.execute(sql)

        ledger = _ledger_for(_CONSTRAINED_DDL[0], _PARSED_ORDERS_DDL)
        receipt = corroborate(ledger, DuckDBTuningIntrospector().introspect(_WithoutReferences(), ledger))
        verdicts = {entry.constraint_type: entry.verdict for entry in receipt.entries if entry.table == "orders"}
        assert verdicts == {
            CONSTRAINT_PRIMARY_KEY: CORROBORATED,
            CONSTRAINT_UNIQUE: CORROBORATED,
            CONSTRAINT_FOREIGN_KEY: UNVERIFIABLE,
        }
        assert receipt.corroborated is False

    def test_constraint_read_failure_degrades_the_whole_state(self):
        con = _constrained_connection()

        class _ConstraintsFail:
            def execute(self, sql, params=None):
                if "duckdb_constraints()" in sql:
                    raise RuntimeError("constraint catalog exploded")
                return con.execute(sql)

        state = DuckDBTuningIntrospector().introspect(_ConstraintsFail(), _ledger_for(*_CONSTRAINED_DDL))
        assert state.error is not None and "duckdb_constraints" in state.error
        assert state.objects == []

    def test_constraint_row_bound_marks_the_state_truncated(self, monkeypatch):
        monkeypatch.setattr(duckdb_introspection, "_MAX_CONSTRAINT_ROWS", 2)
        con = _constrained_connection()
        ledger = _ledger_for(_CONSTRAINED_DDL[0], _PARSED_ORDERS_DDL)
        state = DuckDBTuningIntrospector().introspect(con, ledger)
        assert state.truncated is True
        assert corroborate(ledger, state).corroborated is False


def _template_config(benchmark_name: str) -> UnifiedTuningConfiguration:
    return ConfigManager().load_unified_tuning_config(TEMPLATES / f"{benchmark_name}_tuned.yaml", platform="duckdb")


_SCHEMA_BENCHMARKS = {"tpch": TPCHBenchmark, "ssb": SSB}


@pytest.mark.parametrize("benchmark_name", sorted(_SCHEMA_BENCHMARKS))
def test_shipped_template_schema_constraints_all_corroborate(benchmark_name, tmp_path):
    con = duckdb.connect(":memory:")
    adapter = DuckDBAdapter()
    adapter.unified_tuning_configuration = _template_config(benchmark_name)
    ledger = AppliedTuningLedger()
    schema_benchmark = _SCHEMA_BENCHMARKS[benchmark_name](scale_factor=0.01, output_dir=tmp_path)

    adapter.create_schema(schema_benchmark, recording_connection(con, ledger, PHASE_DDL))
    receipt = corroborate(ledger, DuckDBTuningIntrospector().introspect(con, ledger))

    constraint_entries = [entry for entry in receipt.entries if entry.kind == KIND_CONSTRAINT]
    assert constraint_entries
    assert {entry.verdict for entry in constraint_entries} == {CORROBORATED}
    assert {entry.verdict for entry in receipt.entries} == {CORROBORATED}
