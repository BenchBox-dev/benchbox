# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tuning.applied_ledger import (
    APPLIED_UNVERIFIED,
    APPLIED_VERIFIED,
    EXECUTED,
    FAILED,
    HARNESS_DEFAULT,
    LEDGER_PHASES,
    NOOP,
    NOT_APPLICABLE,
    PHASE_DDL,
    PHASE_POST_LOAD,
    PHASE_SESSION,
    STATEMENT_FAILED,
    AppliedTuningLedger,
    harness_connection,
    is_schema_tuning_statement,
    normalize_ledger_phase,
    recording_connection,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeCursor:
    def __init__(self, sink: list[str], fail_on: str | None = None) -> None:
        self._sink = sink
        self._fail_on = fail_on

    def execute(self, statement, *args, **kwargs):
        if self._fail_on is not None and self._fail_on in str(statement):
            raise RuntimeError("boom")
        self._sink.append(str(statement))
        return f"cursor-result::{statement}"

    def close(self) -> None:  # pragma: no cover
        return None


class _FakeConnection:
    def __init__(self, fail_on: str | None = None) -> None:
        self.executed: list[str] = []
        self._fail_on = fail_on

    def execute(self, statement, *args, **kwargs):
        if self._fail_on is not None and self._fail_on in str(statement):
            raise RuntimeError("boom")
        self.executed.append(str(statement))
        return f"result::{statement}"

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self.executed, fail_on=self._fail_on)

    def rollback(self) -> str:
        return "rolled-back"


class TestStatusDerivation:
    def test_not_applicable_when_tuning_disabled(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("CREATE INDEX x", PHASE_DDL)
        assert ledger.overall_status(tuning_enabled=False, has_config=True) == NOT_APPLICABLE

    def test_not_applicable_when_no_config(self) -> None:
        ledger = AppliedTuningLedger()
        assert ledger.overall_status(tuning_enabled=True, has_config=False) == NOT_APPLICABLE

    def test_noop_when_enabled_but_nothing_executed(self) -> None:
        ledger = AppliedTuningLedger()
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == NOOP

    def test_applied_unverified_when_a_statement_executed(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("CREATE INDEX idx_lineitem_sort ON LINEITEM (l_orderkey)", PHASE_DDL)
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == APPLIED_UNVERIFIED

    def test_failed_when_every_statement_failed(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("CREATE INDEX bad", PHASE_DDL, status=STATEMENT_FAILED, error="nope")
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == FAILED

    def test_one_success_among_failures_is_applied_unverified(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("CREATE INDEX bad", PHASE_DDL, status=STATEMENT_FAILED, error="nope")
        ledger.record("CREATE INDEX good ON t (a)", PHASE_DDL)
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == APPLIED_UNVERIFIED

    def test_all_physical_statements_failed_despite_session_success(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("SET use_cached_result=true", PHASE_SESSION)
        ledger.record(
            "ALTER TABLE lineitem CLUSTER BY (l_orderkey)",
            PHASE_POST_LOAD,
            status=STATEMENT_FAILED,
            error="nope",
        )

        assert ledger.overall_status(tuning_enabled=True, has_config=True) == FAILED

    def test_session_only_statements_are_not_applied(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("SET use_cached_result=true", PHASE_SESSION)

        assert ledger.overall_status(tuning_enabled=True, has_config=True) == NOOP

    def test_session_statements_with_mechanism_are_applied(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("SET spark.sql.shuffle.partitions=8", PHASE_SESSION, mechanism="spark_session_config")

        assert ledger.overall_status(tuning_enabled=True, has_config=True) == APPLIED_UNVERIFIED

    def test_session_statements_do_not_mask_failed_physical_tuning(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("ALTER SESSION SET TIMEZONE = 'UTC'", PHASE_SESSION)
        ledger.record("CREATE INDEX bad ON t (a)", PHASE_DDL, status=STATEMENT_FAILED, error="nope")

        assert ledger.overall_status(tuning_enabled=True, has_config=True) == FAILED

    def test_overall_status_never_returns_applied_verified(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("SET a=1", PHASE_SESSION, mechanism="session_config")
        assert ledger.overall_status(tuning_enabled=True, has_config=True) != APPLIED_VERIFIED

    def test_noop_platform_never_reports_applied(self) -> None:
        ledger = AppliedTuningLedger()
        conn = _FakeConnection()
        _ = recording_connection(conn, ledger, PHASE_DDL)
        assert conn.executed == []
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == NOOP

    def test_metadata_persistence_failure_is_not_an_alarming_status(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("CREATE INDEX idx ON t (a)", PHASE_DDL)
        status = ledger.overall_status(tuning_enabled=True, has_config=True)
        assert status == APPLIED_UNVERIFIED
        assert status != FAILED


class TestAppliedLedgerHash:
    def test_none_when_nothing_executed(self) -> None:
        assert AppliedTuningLedger().applied_ledger_hash() is None

    def test_none_when_only_failed_statements(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("CREATE INDEX bad", PHASE_DDL, status=STATEMENT_FAILED, error="nope")
        assert ledger.applied_ledger_hash() is None

    def test_deterministic_for_identical_executed_sequences(self) -> None:
        a = AppliedTuningLedger()
        b = AppliedTuningLedger()
        for statement in ("CREATE INDEX i1 ON t (a)", "CREATE INDEX i2 ON t (b)"):
            a.record(statement, PHASE_DDL)
            b.record(statement, PHASE_DDL)
        assert a.applied_ledger_hash() == b.applied_ledger_hash()
        assert a.applied_ledger_hash() is not None

    def test_order_sensitive(self) -> None:
        forward = AppliedTuningLedger()
        forward.record("CREATE INDEX i1 ON t (a)", PHASE_DDL)
        forward.record("CREATE INDEX i2 ON t (b)", PHASE_DDL)

        reverse = AppliedTuningLedger()
        reverse.record("CREATE INDEX i2 ON t (b)", PHASE_DDL)
        reverse.record("CREATE INDEX i1 ON t (a)", PHASE_DDL)

        assert forward.applied_ledger_hash() != reverse.applied_ledger_hash()

    def test_failed_statements_excluded_from_hash(self) -> None:
        clean = AppliedTuningLedger()
        clean.record("CREATE INDEX i1 ON t (a)", PHASE_DDL)

        with_failure = AppliedTuningLedger()
        with_failure.record("CREATE INDEX i1 ON t (a)", PHASE_DDL)
        with_failure.record("CREATE INDEX bad", PHASE_DDL, status=STATEMENT_FAILED, error="nope")

        assert clean.applied_ledger_hash() == with_failure.applied_ledger_hash()

    def test_harness_defaults_are_payload_only(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("SET spark.plugins=org.example.Plugin", PHASE_SESSION, status=HARNESS_DEFAULT)

        assert ledger.is_empty() is False
        assert ledger.executed_statements == []
        assert ledger.applied_ledger_hash() is None
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == NOOP
        assert ledger.overall_status(tuning_enabled=False, has_config=True) == NOT_APPLICABLE
        assert ledger.to_payload(status=NOOP)["statements"] == [
            {
                "statement": "SET spark.plugins=org.example.Plugin",
                "phase": PHASE_SESSION,
                "status": HARNESS_DEFAULT,
            }
        ]

    def test_harness_defaults_do_not_hide_tuned_statements(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("SET spark.plugins=org.example.Plugin", PHASE_SESSION, status=HARNESS_DEFAULT)
        ledger.record("SET spark.sql.adaptive.enabled=true", PHASE_SESSION, mechanism="spark_session_config")

        assert ledger.overall_status(tuning_enabled=True, has_config=True) == APPLIED_UNVERIFIED
        assert ledger.applied_ledger_hash() is not None


class TestDroppedIntents:
    def test_record_dropped_appears_in_payload(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record_dropped("partitioning:LINEITEM", "handled at data-loading time")
        payload = ledger.to_payload(status=NOOP)
        assert payload["dropped"] == [{"intent": "partitioning:LINEITEM", "reason": "handled at data-loading time"}]

    def test_dropped_intent_does_not_count_as_executed(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record_dropped("distribution:ORDERS", "single-node")
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == NOOP
        assert ledger.applied_ledger_hash() is None

    def test_to_payload_shape(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("CREATE INDEX i ON t (a)", PHASE_DDL, mechanism="index", table="t")
        payload = ledger.to_payload(status=APPLIED_UNVERIFIED)
        assert set(payload) == {"status", "applied_ledger_hash", "statements", "dropped"}
        assert payload["status"] == APPLIED_UNVERIFIED
        assert payload["applied_ledger_hash"] == ledger.applied_ledger_hash()
        assert payload["statements"][0]["mechanism"] == "index"
        assert payload["statements"][0]["table"] == "t"
        assert payload["statements"][0]["status"] == EXECUTED


class TestPhaseNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ddl", PHASE_DDL),
            ("post_load", PHASE_POST_LOAD),
            ("session", PHASE_SESSION),
            ("pre_load", PHASE_DDL),
            ("preload", PHASE_DDL),
            ("schema", PHASE_DDL),
            ("postload", PHASE_POST_LOAD),
            ("maintenance", PHASE_POST_LOAD),
        ],
    )
    def test_aliases_fold_to_closed_set(self, raw: str, expected: str) -> None:
        assert normalize_ledger_phase(raw) == expected

    def test_unknown_phase_coerces_to_ddl(self) -> None:
        assert normalize_ledger_phase("pre-launch") == PHASE_DDL
        assert normalize_ledger_phase("") == PHASE_DDL

    def test_record_normalizes_pre_load_so_it_counts_as_physical(self) -> None:
        ledger = AppliedTuningLedger()
        ledger.record("ALTER TABLE T CLUSTER BY (A)", "pre_load", status=STATEMENT_FAILED)
        assert ledger.statements[0].phase == PHASE_DDL
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == FAILED

    def test_closed_set_members_pass_through(self) -> None:
        assert {normalize_ledger_phase(p) for p in LEDGER_PHASES} == set(LEDGER_PHASES)


class TestRecordingConnectionHarness:
    def test_captured_statements_match_what_the_connection_ran(self) -> None:
        ledger = AppliedTuningLedger()
        conn = _FakeConnection()
        wrapped = recording_connection(conn, ledger, PHASE_DDL)

        statements = [
            "CREATE INDEX idx_lineitem_sort ON LINEITEM (l_orderkey)",
            "CREATE INDEX idx_orders_cluster ON ORDERS (o_custkey)",
        ]
        results = [wrapped.execute(sql) for sql in statements]

        assert results == [f"result::{sql}" for sql in statements]
        assert conn.executed == statements
        assert [s.statement for s in ledger.executed_statements] == statements
        assert all(s.phase == PHASE_DDL and s.status == EXECUTED for s in ledger.executed_statements)

    def test_cursor_execute_is_captured(self) -> None:
        ledger = AppliedTuningLedger()
        conn = _FakeConnection()
        wrapped = recording_connection(conn, ledger, PHASE_SESSION)

        cursor = wrapped.cursor()
        cursor.execute("SET query_timeout = 30")

        assert conn.executed == ["SET query_timeout = 30"]
        assert [s.statement for s in ledger.executed_statements] == ["SET query_timeout = 30"]
        assert ledger.statements[0].phase == PHASE_SESSION

    def test_failed_execute_is_recorded_then_reraised(self) -> None:
        ledger = AppliedTuningLedger()
        conn = _FakeConnection(fail_on="CREATE INDEX bad")
        wrapped = recording_connection(conn, ledger, PHASE_DDL)

        with pytest.raises(RuntimeError):
            wrapped.execute("CREATE INDEX bad ON t (a)")

        assert conn.executed == []
        assert len(ledger.statements) == 1
        assert ledger.statements[0].status == STATEMENT_FAILED
        assert ledger.statements[0].error is not None
        assert ledger.executed_statements == []

    def test_none_ledger_returns_raw_connection_unwrapped(self) -> None:
        conn = _FakeConnection()
        assert recording_connection(conn, None, PHASE_DDL) is conn

    def test_non_execute_attributes_delegate_transparently(self) -> None:
        ledger = AppliedTuningLedger()
        conn = _FakeConnection()
        wrapped = recording_connection(conn, ledger, PHASE_DDL)
        assert wrapped.rollback() == "rolled-back"
        assert ledger.is_empty()

    def test_readback_select_executes_but_is_not_recorded(self) -> None:
        ledger = AppliedTuningLedger()
        conn = _FakeConnection()
        wrapped = recording_connection(conn, ledger, PHASE_SESSION)

        result = wrapped.execute("SELECT name, value FROM system.settings WHERE name = 'x'")

        assert result.startswith("result::")
        assert conn.executed == ["SELECT name, value FROM system.settings WHERE name = 'x'"]
        assert ledger.is_empty()

    def test_only_mutating_statement_recorded_when_interleaved_with_readback(self) -> None:
        ledger = AppliedTuningLedger()
        conn = _FakeConnection()
        wrapped = recording_connection(conn, ledger, PHASE_SESSION)

        wrapped.execute("SET max_threads = 8")
        wrapped.execute("SELECT value FROM system.settings WHERE name = 'max_threads'")

        assert conn.executed == [
            "SET max_threads = 8",
            "SELECT value FROM system.settings WHERE name = 'max_threads'",
        ]
        assert [s.statement for s in ledger.statements] == ["SET max_threads = 8"]

    def test_cursor_readback_is_not_recorded(self) -> None:
        ledger = AppliedTuningLedger()
        conn = _FakeConnection()
        cursor = recording_connection(conn, ledger, PHASE_SESSION).cursor()

        cursor.execute("SHOW VARIABLES LIKE 'max_threads'")

        assert conn.executed == ["SHOW VARIABLES LIKE 'max_threads'"]
        assert ledger.is_empty()

    def test_executescript_records_tuning_ddl_but_not_baseline_schema(self) -> None:
        import sqlite3

        ledger = AppliedTuningLedger()
        conn = sqlite3.connect(":memory:")
        wrapped = recording_connection(
            conn,
            ledger,
            PHASE_DDL,
            statement_filter=is_schema_tuning_statement,
        )

        wrapped.executescript("CREATE TABLE baseline (id INTEGER);\nCREATE TABLE tuned (id INTEGER PRIMARY KEY);\n")

        assert [statement.statement for statement in ledger.executed_statements] == [
            "CREATE TABLE tuned (id INTEGER PRIMARY KEY);"
        ]
        assert is_schema_tuning_statement("CREATE TABLE baseline (id INTEGER);") is False
        assert (
            is_schema_tuning_statement(
                'CREATE TABLE IF NOT EXISTS lake.main."__benchbox_run_identity" '
                "(identity_key VARCHAR, identity_value VARCHAR)"
            )
            is False
        )
        assert is_schema_tuning_statement('DELETE FROM lake.main."__benchbox_run_identity"') is False
        assert conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall() == [
            ("baseline",),
            ("tuned",),
        ]


class TestHarnessRecording:
    def test_harness_connection_records_statements_as_harness_default(self) -> None:
        ledger = AppliedTuningLedger()
        conn = _FakeConnection()
        wrapped = recording_connection(conn, ledger, PHASE_SESSION, harness=True)

        wrapped.execute("ALTER SESSION SET TIMEZONE = 'UTC'")
        wrapped.cursor().execute("USE WAREHOUSE W")

        assert conn.executed == ["ALTER SESSION SET TIMEZONE = 'UTC'", "USE WAREHOUSE W"]
        assert [s.status for s in ledger.statements] == [HARNESS_DEFAULT, HARNESS_DEFAULT]
        assert ledger.tuning_statements == []
        assert ledger.applied_ledger_hash() is None
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == NOOP

    def test_harness_failure_is_not_a_tuning_failure(self) -> None:
        ledger = AppliedTuningLedger()
        wrapped = recording_connection(_FakeConnection(fail_on="TIMEZONE"), ledger, PHASE_SESSION, harness=True)

        with pytest.raises(RuntimeError):
            wrapped.execute("ALTER SESSION SET TIMEZONE = 'UTC'")

        assert ledger.statements[0].status == HARNESS_DEFAULT
        assert ledger.statements[0].error == "boom"
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == NOOP

    def test_harness_and_tuned_views_share_one_connection(self) -> None:
        ledger = AppliedTuningLedger()
        conn = _FakeConnection()
        tuned = recording_connection(conn, ledger, PHASE_SESSION, mechanism="session_config")

        harness_connection(tuned).execute("SET max_threads = 4")
        tuned.execute("SET max_bytes_in_join = 1")

        assert [(s.statement, s.status, s.mechanism) for s in ledger.statements] == [
            ("SET max_threads = 4", HARNESS_DEFAULT, None),
            ("SET max_bytes_in_join = 1", EXECUTED, "session_config"),
        ]
        assert [s.statement for s in ledger.tuning_statements] == ["SET max_bytes_in_join = 1"]

    def test_harness_connection_passes_plain_connections_through(self) -> None:
        conn = _FakeConnection()

        assert harness_connection(conn) is conn
