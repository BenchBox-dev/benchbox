from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.tuning.applied_ledger import PHASE_POST_LOAD, AppliedTuningLedger
from benchbox.core.tuning.introspection import (
    ATTESTED,
    KIND_INDEX,
    IntrospectedObject,
    IntrospectedState,
    _attested_sort_columns,
    corroborate,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


SORT_STATEMENT = "CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem ORDER BY l_shipdate, l_orderkey"
INDEX_STATEMENT = 'CREATE INDEX idx_lineitem_sort ON "lineitem" ("l_shipdate")'
ATTESTED_REASON = "physical sort executed; no catalog footprint"


def _index_state() -> IntrospectedState:
    return IntrospectedState(
        platform="duckdb",
        objects=[IntrospectedObject(kind=KIND_INDEX, table="lineitem", columns=("l_shipdate",))],
    )


def _sort_ledger() -> AppliedTuningLedger:
    ledger = AppliedTuningLedger()
    ledger.record(INDEX_STATEMENT, PHASE_POST_LOAD)
    ledger.record(SORT_STATEMENT, PHASE_POST_LOAD, mechanism="sorted_ingestion", table="lineitem")
    return ledger


class TestAttestedVerdict:
    def test_executed_rewrite_is_attested_not_catalog_checked(self) -> None:
        receipt = corroborate(_sort_ledger(), _index_state())

        attested = [entry for entry in receipt.entries if entry.verdict == ATTESTED]
        assert len(attested) == 1
        assert attested[0].reason == ATTESTED_REASON
        assert attested[0].kind == "sort_key"
        assert attested[0].table == "lineitem"
        assert list(attested[0].expected_columns) == ["l_shipdate", "l_orderkey"]

    def test_index_corroboration_plus_rewrite_is_applied_verified(self) -> None:
        receipt = corroborate(_sort_ledger(), _index_state())

        assert receipt.corroborated is True
        assert any(entry.verdict == "corroborated" for entry in receipt.entries)
        assert any(entry.verdict == ATTESTED for entry in receipt.entries)

    def test_attested_only_never_reaches_verified(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record(SORT_STATEMENT, PHASE_POST_LOAD, mechanism="sorted_ingestion", table="lineitem")

        receipt = corroborate(ledger, _index_state())

        assert receipt.corroborated is False
        assert [entry.verdict for entry in receipt.entries] == [ATTESTED]

    def test_requested_sort_without_rewrite_is_dropped_and_blocks(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record(INDEX_STATEMENT, PHASE_POST_LOAD)
        ledger.record_dropped("sorting:LINEITEM (L_SHIPDATE)", "adapter rendered no statement")

        receipt = corroborate(ledger, _index_state())

        assert receipt.corroborated is False
        assert [entry.verdict for entry in receipt.entries if entry.verdict == ATTESTED] == []
        assert len(ledger.dropped) == 1

    def test_failed_rewrite_blocks_corroboration(self) -> None:
        from benchbox.core.tuning.applied_ledger import STATEMENT_FAILED

        ledger = AppliedTuningLedger()
        ledger.record(
            SORT_STATEMENT,
            PHASE_POST_LOAD,
            status=STATEMENT_FAILED,
            mechanism="sorted_ingestion",
            table="lineitem",
            error=RuntimeError("denied"),
        )

        receipt = corroborate(ledger, _index_state())

        assert receipt.corroborated is False
        assert all(entry.verdict != ATTESTED for entry in receipt.entries)

    def test_attested_verdict_is_excluded_from_gate_totals(self) -> None:
        receipt = corroborate(_sort_ledger(), _index_state())

        assert receipt.summary["gate_relevant_total"] == 1
        assert receipt.summary[ATTESTED] == 1


class TestAttestedSortColumns:
    def test_parenthesized_order_by(self) -> None:
        assert _attested_sort_columns("CREATE TABLE t (a INT) ORDER BY (a, b)") == ("a", "b")

    def test_bare_order_by_with_directions(self) -> None:
        assert _attested_sort_columns("CREATE TABLE t AS SELECT * FROM t ORDER BY a DESC, b ASC") == ("a", "b")

    def test_missing_order_by_is_empty(self) -> None:
        assert _attested_sort_columns("CREATE TABLE t (a INT)") == ()


class TestAttestedPromotion:
    def _adapter(self, ledger: AppliedTuningLedger) -> MagicMock:
        adapter = MagicMock()
        adapter._applied_tuning_ledger = ledger
        introspector = MagicMock()
        introspector.introspect.return_value = _index_state()
        adapter.get_tuning_introspector.return_value = introspector
        return adapter

    def test_rewrite_plus_index_promotes_to_verified(self) -> None:
        from benchbox.platforms.base.tuning_trust import corroborate_applied_ledger

        status, payload = corroborate_applied_ledger(self._adapter(_sort_ledger()), MagicMock(), "applied_unverified")

        assert status == "applied_verified"
        assert payload is not None

    def test_attested_only_stays_unverified(self) -> None:
        from benchbox.platforms.base.tuning_trust import corroborate_applied_ledger

        ledger = AppliedTuningLedger()
        ledger.record(SORT_STATEMENT, PHASE_POST_LOAD, mechanism="sorted_ingestion", table="lineitem")
        status, _payload = corroborate_applied_ledger(self._adapter(ledger), MagicMock(), "applied_unverified")

        assert status == "applied_unverified"


class TestCtasSortRecordsLedgerStatement:
    @pytest.fixture(autouse=True)
    def _require_duckdb(self):
        pytest.importorskip("duckdb")

    def test_successful_rewrite_records_post_load_statement(self) -> None:
        import duckdb

        from benchbox.platforms.duckdb import DuckDBAdapter
        from tests.unit.platforms.test_duckdb_sorted_loading import (
            _make_table_tuning_with_sort,
            _make_tuning_config,
        )

        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE lineitem (l_orderkey INTEGER, l_shipdate DATE)")
        conn.execute("INSERT INTO lineitem VALUES (2, '2024-02-01'), (1, '2024-01-01')")

        with patch("benchbox.platforms.duckdb.duckdb"):
            adapter = DuckDBAdapter()
        adapter._applied_tuning_ledger = AppliedTuningLedger()

        result = adapter.apply_ctas_sort(
            "lineitem", _make_tuning_config({"lineitem": _make_table_tuning_with_sort(["l_shipdate"])}), conn
        )

        assert result is True
        recorded = [
            statement
            for statement in adapter._applied_tuning_ledger.statements
            if statement.mechanism == "sorted_ingestion" and statement.phase == PHASE_POST_LOAD
        ]
        assert len(recorded) == 1
        assert recorded[0].table == "lineitem"
        post_load_executed = [
            statement
            for statement in adapter._applied_tuning_ledger.statements
            if statement.phase == PHASE_POST_LOAD and statement.status == "executed"
        ]
        assert len(post_load_executed) == 1

        from benchbox.core.tuning.introspection import ATTESTED, IntrospectedState, corroborate

        receipt = corroborate(adapter._applied_tuning_ledger, IntrospectedState(platform="duckdb", objects=[]))
        assert [entry.verdict for entry in receipt.entries] == [ATTESTED]
        assert receipt.entries[0].reason == "physical sort executed; no catalog footprint"
        assert list(receipt.entries[0].expected_columns) == ["l_shipdate"]
        assert receipt.corroborated is False
