# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tuning.applied_ledger import (
    HARNESS_DEFAULT,
    PHASE_DDL,
    PHASE_POST_LOAD,
    PHASE_SESSION,
    STATEMENT_FAILED,
    AppliedStatement,
    AppliedTuningLedger,
)
from benchbox.core.tuning.introspection import (
    ABSENT,
    CORROBORATED,
    KIND_CLUSTER_KEY,
    KIND_INDEX,
    KIND_PARTITION_KEY,
    KIND_SORT_KEY,
    MAINTENANCE,
    MISMATCH,
    TRANSIENT,
    UNVERIFIABLE,
    IntrospectedObject,
    IntrospectedState,
    IntrospectionReceipt,
    Introspector,
    _classify,
    _Intent,
    _match_object,
    corroborate,
    ledger_tables,
    normalize_columns,
    normalize_identifier,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _index_ledger(
    statement: str = "CREATE INDEX IF NOT EXISTS idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)",
):
    ledger = AppliedTuningLedger()
    ledger.record(statement, PHASE_DDL)
    return ledger


def _index_state(columns=("l_orderkey", "l_linenumber"), table="lineitem", name="idx_lineitem_sort"):
    return IntrospectedState(
        platform="duckdb",
        objects=[
            IntrospectedObject(
                kind=KIND_INDEX, table=table, columns=tuple(columns), name=name, evidence={"index_name": name}
            )
        ],
    )


class TestNormalization:
    def test_normalize_identifier_casefolds_and_strips_quotes(self):
        assert normalize_identifier('  "L_OrderKey" ') == "l_orderkey"
        assert normalize_identifier("`col`") == "col"
        assert normalize_identifier("[col]") == "col"

    def test_normalize_columns_from_comma_string(self):
        assert normalize_columns("A, b ,C") == ("a", "b", "c")

    def test_normalize_columns_from_bracketed_catalog_list(self):
        assert normalize_columns("[L_ORDERKEY, L_LINENUMBER]") == ("l_orderkey", "l_linenumber")

    @pytest.mark.parametrize("raw", ["a, b", "(a, b)", "tuple(a, b)", "[a, b]"])
    def test_normalize_columns_unifies_multicolumn_catalog_wrappers(self, raw):
        assert normalize_columns(raw) == ("a", "b")

    def test_normalize_columns_strips_only_a_balanced_plain_outer_wrapper(self):
        assert normalize_columns("((a, b))") == ("(a", "b)")
        assert normalize_columns("(a, b") == ("(a", "b")
        assert normalize_columns("f(a), b") != normalize_columns("f(a, b)")

    def test_normalize_columns_preserves_single_expression_verbatim(self):
        assert normalize_columns("toYYYYMM(event_ts)") == ("toyyyymm(event_ts)",)

    def test_classifier_ignores_clause_decoys_in_literals_and_comments(self):
        statement = AppliedStatement(
            statement=(
                "CREATE TABLE t (c String DEFAULT 'order by (decoy)') "
                "ENGINE=MergeTree /* PARTITION BY (also_decoy) */ ORDER BY (real_col)"
            ),
            phase=PHASE_DDL,
        )

        klass, intents = _classify(statement)

        assert klass == "verifiable"
        assert [(intent.kind, intent.columns) for intent in intents] == [(KIND_SORT_KEY, ("real_col",))]

    def test_normalize_columns_from_iterable_and_empty(self):
        assert normalize_columns(["A", " B "]) == ("a", "b")
        assert normalize_columns("") == ()
        assert normalize_columns(None) == ()


class TestCorroborate:
    def test_corroborated_index_upgrades(self):
        receipt = corroborate(_index_ledger(), _index_state())
        assert receipt.corroborated is True
        assert receipt.summary["corroborated"] == 1
        assert receipt.summary["gate_relevant_total"] == 1
        entry = receipt.entries[0]
        assert entry.verdict == CORROBORATED
        assert entry.kind == KIND_INDEX
        assert entry.expected_columns == ("l_orderkey", "l_linenumber")
        assert entry.observed_columns == ("l_orderkey", "l_linenumber")

    def test_column_order_is_significant(self):
        receipt = corroborate(_index_ledger(), _index_state(columns=("l_linenumber", "l_orderkey")))
        assert receipt.corroborated is False
        assert receipt.entries[0].verdict == MISMATCH

    def test_empty_columns_cannot_corroborate_trivially(self):
        verdict, _ = _match_object(
            _Intent(kind=KIND_SORT_KEY, table="t", columns=()),
            IntrospectedState(
                platform="x",
                objects=[IntrospectedObject(kind=KIND_SORT_KEY, table="t", columns=())],
            ),
        )

        assert verdict != CORROBORATED

    def test_schema_qualified_intent_does_not_suffix_match_bare_catalog_fact(self):
        verdict, _ = _match_object(
            _Intent(kind=KIND_SORT_KEY, table="analytics.t", columns=("a",)),
            IntrospectedState(
                platform="x",
                objects=[IntrospectedObject(kind=KIND_SORT_KEY, table="t", columns=("a",))],
            ),
        )

        assert verdict == ABSENT

    def test_mismatch_has_diff_and_no_upgrade(self):
        receipt = corroborate(_index_ledger(), _index_state(columns=("l_orderkey",)))
        assert receipt.corroborated is False
        entry = receipt.entries[0]
        assert entry.verdict == MISMATCH
        assert entry.diff and "expected" in entry.diff and "observed" in entry.diff

    def test_absent_when_no_catalog_object(self):
        receipt = corroborate(_index_ledger(), IntrospectedState(platform="duckdb", objects=[]))
        assert receipt.corroborated is False
        assert receipt.entries[0].verdict == ABSENT
        assert receipt.entries[0].table == "lineitem"
        assert receipt.entries[0].reason == "no index found in catalog"

    def test_one_absent_among_corroborated_blocks_upgrade(self):
        ledger = _index_ledger()
        ledger.record("CREATE INDEX idx_orders_sort ON ORDERS (O_ORDERKEY)", PHASE_DDL)
        receipt = corroborate(ledger, _index_state())
        assert receipt.corroborated is False
        verdicts = {e.verdict for e in receipt.entries}
        assert CORROBORATED in verdicts and ABSENT in verdicts

    def test_empty_ledger_no_upgrade(self):
        receipt = corroborate(AppliedTuningLedger(), _index_state())
        assert receipt.corroborated is False
        assert receipt.summary["gate_relevant_total"] == 0

    def test_failed_physical_statement_gets_blocking_receipt_entry(self):
        ledger = AppliedTuningLedger()
        ledger.record(
            "CREATE INDEX idx_lineitem_sort ON LINEITEM (L_ORDERKEY, L_LINENUMBER)",
            PHASE_DDL,
            status=STATEMENT_FAILED,
            error="permission denied",
        )
        receipt = corroborate(ledger, _index_state())
        assert receipt.corroborated is False
        assert receipt.entries[0].verdict == UNVERIFIABLE
        assert receipt.entries[0].reason == "ddl/post_load statement failed -- blocks corroboration"

    def test_harness_defaults_are_not_corroboration_targets(self):
        ledger = AppliedTuningLedger()
        ledger.record("SET spark.plugins=org.example.Plugin", PHASE_SESSION, status=HARNESS_DEFAULT)

        receipt = corroborate(ledger, _index_state())

        assert receipt.entries == []
        assert receipt.corroborated is False
        assert receipt.summary["gate_relevant_total"] == 0

    def test_failed_physical_statement_blocks_corroborated_sibling(self):
        ledger = _index_ledger()
        ledger.record(
            "ALTER TABLE orders CLUSTER BY (o_orderkey)",
            PHASE_POST_LOAD,
            status=STATEMENT_FAILED,
            error="boom",
        )

        receipt = corroborate(ledger, _index_state())

        assert receipt.corroborated is False
        assert [entry.verdict for entry in receipt.entries] == [CORROBORATED, UNVERIFIABLE]

    def test_dropped_intent_blocks_corroborated_statement_and_is_in_payload(self):
        ledger = _index_ledger()
        ledger.record_dropped("clustering:orders", "capability filtered")

        receipt = corroborate(ledger, _index_state())

        assert receipt.corroborated is False
        assert receipt.to_payload()["dropped"] == [{"intent": "clustering:orders", "reason": "capability filtered"}]


class TestSessionAndMaintenance:
    def test_session_sets_are_transient_and_non_blocking(self):
        ledger = _index_ledger()
        ledger.record("SET threads=4", PHASE_SESSION)
        receipt = corroborate(ledger, _index_state())
        assert receipt.corroborated is True
        verdicts = [e.verdict for e in receipt.entries]
        assert TRANSIENT in verdicts
        assert receipt.summary["gate_relevant_total"] == 1

    def test_failed_session_statement_is_transient_and_non_blocking(self):
        ledger = _index_ledger()
        ledger.record("SET threads=4", PHASE_SESSION, status=STATEMENT_FAILED, error="unsupported")

        receipt = corroborate(ledger, _index_state())

        assert receipt.corroborated is True
        assert [entry.verdict for entry in receipt.entries] == [CORROBORATED, TRANSIENT]
        assert receipt.entries[1].reason == "session/config statement failed -- transient, not corroboration-eligible"

    def test_session_only_ledger_never_verifies(self):
        ledger = AppliedTuningLedger()
        ledger.record("SET threads=4", PHASE_SESSION)
        ledger.record("PRAGMA memory_limit='1GB'", PHASE_SESSION)
        receipt = corroborate(ledger, IntrospectedState(platform="duckdb", objects=[]))
        assert receipt.corroborated is False
        assert receipt.summary["gate_relevant_total"] == 0
        assert all(e.verdict == TRANSIENT for e in receipt.entries)

    def test_pragma_in_ddl_phase_is_transient(self):
        ledger = AppliedTuningLedger()
        ledger.record("PRAGMA threads=4", PHASE_DDL)
        receipt = corroborate(ledger, IntrospectedState(platform="duckdb", objects=[]))
        assert receipt.entries[0].verdict == TRANSIENT
        assert receipt.corroborated is False

    def test_optimize_is_maintenance_non_blocking(self):
        ledger = _index_ledger()
        ledger.record("OPTIMIZE TABLE LINEITEM FINAL", PHASE_POST_LOAD)
        receipt = corroborate(ledger, _index_state())
        assert receipt.corroborated is True
        assert any(e.verdict == MAINTENANCE for e in receipt.entries)


class TestUnverifiableAndDegraded:
    def test_unknown_ddl_statement_blocks_upgrade(self):
        ledger = _index_ledger()
        ledger.record("CALL some_extension_tuning('x')", PHASE_DDL)
        receipt = corroborate(ledger, _index_state())
        assert receipt.corroborated is False
        assert any(e.verdict == UNVERIFIABLE for e in receipt.entries)

    def test_errored_state_refuses_upgrade(self):
        receipt = corroborate(_index_ledger(), IntrospectedState(platform="duckdb", objects=[], error="timeout"))
        assert receipt.corroborated is False
        assert receipt.error == "timeout"
        assert receipt.entries[0].verdict == UNVERIFIABLE
        assert receipt.entries[0].reason == "introspection degraded"
        assert receipt.entries[0].detail == "timeout"

    def test_none_state_refuses_upgrade(self):
        receipt = corroborate(_index_ledger(), None)
        assert receipt.corroborated is False
        assert receipt.entries[0].verdict == UNVERIFIABLE


class TestSortKeyAndPartition:
    def test_create_table_order_by_corroborates_sort_key(self):
        ledger = AppliedTuningLedger()
        ledger.record("CREATE TABLE LINEITEM (a INT) ORDER BY (L_ORDERKEY, L_LINENUMBER)", PHASE_DDL)
        state = IntrospectedState(
            platform="clickhouse",
            objects=[IntrospectedObject(kind=KIND_SORT_KEY, table="lineitem", columns=("l_orderkey", "l_linenumber"))],
        )
        receipt = corroborate(ledger, state)
        assert receipt.corroborated is True
        assert receipt.entries[0].kind == KIND_SORT_KEY

    def test_create_table_partition_by_mismatch(self):
        ledger = AppliedTuningLedger()
        ledger.record("CREATE TABLE LINEITEM (a INT) PARTITION BY (L_SHIPDATE)", PHASE_DDL)
        state = IntrospectedState(
            platform="clickhouse",
            objects=[IntrospectedObject(kind=KIND_PARTITION_KEY, table="lineitem", columns=("l_receiptdate",))],
        )
        receipt = corroborate(ledger, state)
        assert receipt.corroborated is False
        assert receipt.entries[0].verdict == MISMATCH


class TestPayloadAndProtocol:
    def test_receipt_additions_preserve_existing_positional_constructor(self):
        receipt = IntrospectionReceipt("x", False, [], [], "catalog failed")

        assert receipt.error == "catalog failed"
        assert receipt.dropped == []

    def test_receipt_payload_shape(self):
        receipt = corroborate(_index_ledger(), _index_state())
        payload = receipt.to_payload()
        assert payload["platform"] == "duckdb"
        assert payload["corroborated"] is True
        assert payload["summary"]["corroborated"] == 1
        assert isinstance(payload["entries"], list)
        assert payload["observed"][0]["kind"] == KIND_INDEX

    def test_ledger_tables_covers_index_and_optimize(self):
        ledger = AppliedTuningLedger()
        ledger.record("CREATE INDEX idx ON LINEITEM (a)", PHASE_DDL)
        ledger.record("OPTIMIZE TABLE ORDERS FINAL", PHASE_POST_LOAD)
        assert ledger_tables(ledger) == {"lineitem", "orders"}

    def test_ledger_tables_covers_alter_and_optimize_targets(self):
        ledger = AppliedTuningLedger()
        ledger.record("ALTER TABLE LINEITEM CLUSTER BY (a)", PHASE_POST_LOAD)
        ledger.record("OPTIMIZE TABLE ORDERS FINAL", PHASE_POST_LOAD)
        assert ledger_tables(ledger) == {"lineitem", "orders"}

    def test_introspector_protocol_is_runtime_checkable(self):
        class _Impl:
            platform = "x"

            def introspect(self, connection, ledger):
                return IntrospectedState(platform="x")

        assert isinstance(_Impl(), Introspector)


class TestMultiClauseCreateTable:
    _DDL = "CREATE TABLE lineitem (a Int64) ENGINE = MergeTree() PARTITION BY (toYYYYMM(d)) ORDER BY (a, b)"

    def _ledger(self) -> AppliedTuningLedger:
        ledger = AppliedTuningLedger()
        ledger.record(self._DDL, PHASE_DDL, table="lineitem")
        return ledger

    def _state(self, *, sort_cols=("a", "b"), partition_cols=("toyyyymm(d)",)) -> IntrospectedState:
        objects = []
        if sort_cols:
            objects.append(IntrospectedObject(kind=KIND_SORT_KEY, table="lineitem", columns=sort_cols))
        if partition_cols:
            objects.append(IntrospectedObject(kind=KIND_PARTITION_KEY, table="lineitem", columns=partition_cols))
        return IntrospectedState(platform="clickhouse", objects=objects)

    def test_both_clauses_yield_separate_entries(self):
        receipt = corroborate(self._ledger(), self._state())
        kinds = [e.kind for e in receipt.entries]
        assert kinds == [KIND_SORT_KEY, KIND_PARTITION_KEY]
        assert receipt.summary["gate_relevant_total"] == 2
        assert receipt.corroborated is True

    def test_uncorroborated_partition_blocks_upgrade(self):
        receipt = corroborate(self._ledger(), self._state(partition_cols=("wrong",)))
        assert receipt.corroborated is False
        verdicts = {e.kind: e.verdict for e in receipt.entries}
        assert verdicts[KIND_SORT_KEY] == CORROBORATED
        assert verdicts[KIND_PARTITION_KEY] == MISMATCH

    def test_absent_partition_blocks_upgrade(self):
        receipt = corroborate(self._ledger(), self._state(partition_cols=()))
        assert receipt.corroborated is False
        assert {e.kind: e.verdict for e in receipt.entries}[KIND_PARTITION_KEY] == ABSENT

    def test_expression_clause_keeps_its_closing_paren(self):
        receipt = corroborate(self._ledger(), self._state())
        partition = next(e for e in receipt.entries if e.kind == KIND_PARTITION_KEY)
        assert partition.expected_columns == ("toyyyymm(d)",)

    def test_degraded_state_marks_every_clause_unverifiable(self):
        degraded = IntrospectedState(platform="clickhouse", error="catalog read failed")
        receipt = corroborate(self._ledger(), degraded)
        assert receipt.corroborated is False
        assert [e.verdict for e in receipt.entries] == [UNVERIFIABLE, UNVERIFIABLE]


class TestUnparsableKeyClauseFailsClosed:
    def _corroborate(self, ddl: str) -> object:
        ledger = AppliedTuningLedger()
        ledger.record(ddl, PHASE_DDL, table="t")
        state = IntrospectedState(
            platform="clickhouse",
            objects=[
                IntrospectedObject(kind=KIND_SORT_KEY, table="t", columns=("a",)),
                IntrospectedObject(kind=KIND_PARTITION_KEY, table="t", columns=("d",)),
            ],
        )
        return corroborate(ledger, state)

    def test_unparenthesized_partition_clause_blocks(self):
        receipt = self._corroborate("CREATE TABLE t (a Int64) PARTITION BY toYYYYMM(d) ORDER BY (a)")
        assert receipt.corroborated is False
        assert [e.verdict for e in receipt.entries] == [UNVERIFIABLE]

    def test_double_nested_expression_blocks(self):
        receipt = self._corroborate("CREATE TABLE t (a Int64) PARTITION BY (toYYYYMM(toDate(d))) ORDER BY (a)")
        assert receipt.corroborated is False
        assert [e.verdict for e in receipt.entries] == [UNVERIFIABLE]

    def test_order_by_tuple_is_an_explicit_no_sort_key_not_a_dropped_clause(self):
        receipt = self._corroborate("CREATE TABLE t (a Int64) PARTITION BY (d) ORDER BY tuple()")
        assert [e.kind for e in receipt.entries] == [KIND_PARTITION_KEY]
        assert receipt.corroborated is True


class TestClusterKeyClassification:
    def _ledger(self, statement: str) -> AppliedTuningLedger:
        ledger = AppliedTuningLedger()
        ledger.record(statement, PHASE_DDL, table="lineitem")
        return ledger

    def _state(self, columns=("a", "b")) -> IntrospectedState:
        return IntrospectedState(
            platform="snowflake",
            objects=[IntrospectedObject(kind=KIND_CLUSTER_KEY, table="lineitem", columns=columns)],
        )

    def test_alter_table_cluster_by_is_corroboratable(self):
        receipt = corroborate(self._ledger("ALTER TABLE lineitem CLUSTER BY (a, b)"), self._state())
        assert [(e.kind, e.verdict) for e in receipt.entries] == [(KIND_CLUSTER_KEY, CORROBORATED)]
        assert receipt.corroborated is True

    def test_create_table_cluster_by_is_corroboratable(self):
        receipt = corroborate(self._ledger("CREATE TABLE lineitem (a INT, b INT) CLUSTER BY (a, b)"), self._state())
        assert [(e.kind, e.verdict) for e in receipt.entries] == [(KIND_CLUSTER_KEY, CORROBORATED)]

    def test_mismatched_cluster_key_blocks(self):
        receipt = corroborate(self._ledger("ALTER TABLE lineitem CLUSTER BY (a, b)"), self._state(columns=("a",)))
        assert receipt.entries[0].verdict == MISMATCH
        assert receipt.corroborated is False

    @pytest.mark.parametrize(
        "statement",
        [
            "ALTER TABLE lineitem RECLUSTER",
            "ALTER TABLE lineitem RESUME RECLUSTER",
            "ALTER TABLE lineitem SUSPEND RECLUSTER",
        ],
    )
    def test_recluster_is_maintenance_not_blocking(self, statement):
        receipt = corroborate(self._ledger(statement), self._state())
        assert receipt.entries[0].verdict == MAINTENANCE
        assert receipt.summary["gate_relevant_total"] == 0

    def test_unparsable_cluster_clause_fails_closed(self):
        receipt = corroborate(self._ledger("ALTER TABLE lineitem CLUSTER BY a, b"), self._state())
        assert receipt.entries[0].verdict == UNVERIFIABLE
        assert receipt.corroborated is False

    def test_other_alter_table_still_blocks(self):
        receipt = corroborate(self._ledger("ALTER TABLE lineitem ADD COLUMN c INT"), self._state())
        assert receipt.entries[0].verdict == UNVERIFIABLE
