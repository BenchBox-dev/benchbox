# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tuning.applied_ledger import (
    PHASE_DDL,
    PHASE_POST_LOAD,
    PHASE_SESSION,
    AppliedTuningLedger,
)
from benchbox.core.tuning.introspection import KIND_CLUSTER_KEY, corroborate
from benchbox.platforms.snowflake_introspection import (
    SnowflakeTuningIntrospector,
    parse_clustering_key,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _FakeCursor:
    def __init__(self, rows, fail: bool):
        self._rows = rows
        self._fail = fail
        self.closed = False
        self.queries: list[str] = []
        self.calls: list[tuple[str, object]] = []

    def execute(self, query, params=None):
        self.queries.append(str(query))
        self.calls.append((str(query), params))
        if self._fail:
            raise RuntimeError("INFORMATION_SCHEMA unavailable")

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


class _FakeSnowflakeConnection:
    def __init__(self, rows, fail: bool = False):
        self._rows = rows
        self._fail = fail
        self.cursors: list[_FakeCursor] = []

    def cursor(self):
        cursor = _FakeCursor(self._rows, self._fail)
        self.cursors.append(cursor)
        return cursor


def _clustered_ledger(table: str = "LINEITEM") -> AppliedTuningLedger:
    ledger = AppliedTuningLedger()
    ledger.record(f"ALTER TABLE {table} CLUSTER BY (l_orderkey, l_shipdate)", PHASE_DDL, table=table)
    ledger.record(f"ALTER TABLE {table} RESUME RECLUSTER", PHASE_DDL, table=table)
    ledger.record("ALTER SESSION SET USE_CACHED_RESULT = FALSE", PHASE_SESSION)
    return ledger


class TestParseClusteringKey:
    @pytest.mark.parametrize(
        "raw",
        ["LINEAR(L_ORDERKEY, L_SHIPDATE)", "(L_ORDERKEY, L_SHIPDATE)", "L_ORDERKEY, L_SHIPDATE"],
    )
    def test_accepts_every_reported_form(self, raw):
        assert parse_clustering_key(raw) == ("l_orderkey", "l_shipdate")

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_unclustered_table_yields_no_columns(self, raw):
        assert parse_clustering_key(raw) == ()

    def test_preserves_key_order(self):

        assert parse_clustering_key("LINEAR(B, A)") == ("b", "a")


class TestSnowflakeIntrospector:
    def test_reports_cluster_key_for_ledger_tables(self):
        conn = _FakeSnowflakeConnection([("LINEITEM", "LINEAR(L_ORDERKEY, L_SHIPDATE)")])
        state = SnowflakeTuningIntrospector().introspect(conn, _clustered_ledger())
        assert state.error is None
        assert len(state.objects) == 1
        obj = state.objects[0]
        assert obj.kind == KIND_CLUSTER_KEY
        assert obj.columns == ("l_orderkey", "l_shipdate")
        assert obj.evidence["clustering_key"] == "LINEAR(L_ORDERKEY, L_SHIPDATE)"

    def test_query_reads_information_schema_not_show_create(self):
        conn = _FakeSnowflakeConnection([("LINEITEM", "LINEAR(A)")])
        SnowflakeTuningIntrospector(schema="BENCH").introspect(conn, _clustered_ledger())
        raw_query = conn.cursors[0].queries[0]
        query = raw_query.upper()
        assert "INFORMATION_SCHEMA.TABLES" in query
        assert "SHOW CREATE" not in query
        assert "TABLE_SCHEMA = %s" in raw_query
        assert "UPPER(TABLE_NAME) IN (%s)" in raw_query
        assert conn.cursors[0].calls[0][1] == ("BENCH", "LINEITEM")

    def test_lowercase_schema_is_normalized_and_bound(self):
        conn = _FakeSnowflakeConnection([("LINEITEM", "LINEAR(A)")])
        state = SnowflakeTuningIntrospector(schema="bench").introspect(conn, _clustered_ledger())

        assert state.error is None
        query, params = conn.cursors[0].calls[0]
        assert "TABLE_SCHEMA = %s" in query
        assert params == ("BENCH", "LINEITEM")
        assert "'bench'" not in query

    def test_unsafe_schema_degrades_without_interpolation(self):
        conn = _FakeSnowflakeConnection([("LINEITEM", "LINEAR(A)")])
        state = SnowflakeTuningIntrospector(schema="bench' OR TRUE --").introspect(conn, _clustered_ledger())

        assert state.error is not None
        assert state.objects == []
        assert conn.cursors == []

    def test_bounded_to_ledger_tables(self):
        conn = _FakeSnowflakeConnection(
            [("LINEITEM", "LINEAR(A)"), ("ORDERS", "LINEAR(B)")],
        )
        state = SnowflakeTuningIntrospector().introspect(conn, _clustered_ledger())
        assert {obj.table for obj in state.objects} == {"LINEITEM"}
        query, params = conn.cursors[0].calls[0]
        assert "UPPER(TABLE_NAME) IN (%s)" in query
        assert params == ("LINEITEM",)

    def test_alter_target_is_not_filtered_when_another_statement_activates_bounding(self):
        ledger = AppliedTuningLedger()
        ledger.record("ALTER TABLE LINEITEM CLUSTER BY (A)", PHASE_POST_LOAD)
        ledger.record("OPTIMIZE TABLE ORDERS", PHASE_POST_LOAD)
        conn = _FakeSnowflakeConnection([("LINEITEM", "LINEAR(A)"), ("NATION", "LINEAR(N)")])

        state = SnowflakeTuningIntrospector().introspect(conn, ledger)

        assert {obj.table for obj in state.objects} == {"LINEITEM"}

    def test_unrelated_rows_at_catalog_cap_do_not_mark_relevant_snapshot_truncated(self):
        rows = [(f"OTHER_{index}", "LINEAR(ID)") for index in range(999)]
        rows.append(("LINEITEM", "LINEAR(A)"))

        state = SnowflakeTuningIntrospector().introspect(_FakeSnowflakeConnection(rows), _clustered_ledger())

        assert state.truncated is False
        assert {obj.table for obj in state.objects} == {"LINEITEM"}

    def test_non_fatal_on_query_failure(self):
        conn = _FakeSnowflakeConnection([], fail=True)
        state = SnowflakeTuningIntrospector().introspect(conn, _clustered_ledger())
        assert state.error is not None
        assert state.objects == []

    def test_closes_its_cursor(self):
        conn = _FakeSnowflakeConnection([("LINEITEM", "LINEAR(A)")])
        SnowflakeTuningIntrospector().introspect(conn, _clustered_ledger())
        assert conn.cursors[0].closed is True


class TestSnowflakeCorroboration:
    def test_matching_cluster_key_earns_verification(self):
        ledger = _clustered_ledger()
        conn = _FakeSnowflakeConnection([("LINEITEM", "LINEAR(L_ORDERKEY, L_SHIPDATE)")])
        receipt = corroborate(ledger, SnowflakeTuningIntrospector().introspect(conn, ledger))

        verdicts = [(e.kind, e.verdict) for e in receipt.entries]
        assert (KIND_CLUSTER_KEY, "corroborated") in verdicts

        assert receipt.summary["gate_relevant_total"] == 1
        assert receipt.corroborated is True

    def test_different_cluster_key_blocks_verification(self):
        ledger = _clustered_ledger()
        conn = _FakeSnowflakeConnection([("LINEITEM", "LINEAR(L_SHIPDATE)")])
        receipt = corroborate(ledger, SnowflakeTuningIntrospector().introspect(conn, ledger))

        entry = next(e for e in receipt.entries if e.kind == KIND_CLUSTER_KEY)
        assert entry.verdict == "mismatch"
        assert receipt.corroborated is False

    def test_unclustered_table_blocks_verification(self):

        ledger = _clustered_ledger()
        conn = _FakeSnowflakeConnection([("LINEITEM", None)])
        receipt = corroborate(ledger, SnowflakeTuningIntrospector().introspect(conn, ledger))

        entry = next(e for e in receipt.entries if e.kind == KIND_CLUSTER_KEY)
        assert entry.verdict == "absent"
        assert receipt.corroborated is False

    def test_degraded_introspection_blocks_verification(self):
        ledger = _clustered_ledger()
        conn = _FakeSnowflakeConnection([], fail=True)
        receipt = corroborate(ledger, SnowflakeTuningIntrospector().introspect(conn, ledger))
        assert receipt.corroborated is False

    def test_key_order_is_significant(self):

        ledger = _clustered_ledger()
        conn = _FakeSnowflakeConnection([("LINEITEM", "LINEAR(L_SHIPDATE, L_ORDERKEY)")])
        receipt = corroborate(ledger, SnowflakeTuningIntrospector().introspect(conn, ledger))
        assert receipt.corroborated is False


