from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.core.results.result_factory import build_enhanced_benchmark_result
from benchbox.core.results.schema import _build_tuning_summary
from benchbox.core.tuning.applied_ledger import (
    APPLIED_UNVERIFIED,
    HARNESS_DEFAULT,
    NOOP,
    PHASE_DDL,
    PHASE_SESSION,
    STATEMENT_FAILED,
    AppliedTuningLedger,
)
from benchbox.core.tuning.introspection import TRANSIENT, UNVERIFIABLE, IntrospectedState, corroborate
from benchbox.core.tuning.reconciliation import applied_tuning_counts
from benchbox.platforms.base.adapter import PlatformAdapter
from benchbox.platforms.clickhouse import ClickHouseAdapter
from benchbox.platforms.postgresql import PostgreSQLAdapter
from benchbox.platforms.snowflake import SnowflakeAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _status(ledger: AppliedTuningLedger) -> str:
    return ledger.overall_status(tuning_enabled=True, has_config=True)


def _snowflake(**kwargs):
    adapter = SnowflakeAdapter(
        account="acct",
        username="user",
        password="pass",
        warehouse="WH",
        database="DB",
        strict_validation=False,
        **kwargs,
    )
    adapter._start_applied_tuning_ledger()
    return adapter


class TestSessionStatementsAreNotTuning:
    @patch("benchbox.platforms.snowflake.snowflake")
    def test_snowflake_harness_statements_are_identical_for_tuned_and_untuned_runs(self, _snowflake_module):
        recorded = {}
        for tuned in (False, True):
            adapter = _snowflake()
            adapter.tuning_enabled = tuned
            connection = Mock()
            adapter.configure_for_benchmark(adapter._session_recording_connection(connection), "olap")
            ledger = adapter._applied_tuning_ledger
            assert ledger.tuning_statements == []
            assert ledger.applied_ledger_hash() is None
            assert _status(ledger) == NOOP
            recorded[tuned] = [(s.statement, s.status) for s in ledger.statements]

        assert recorded[False] == recorded[True]
        statements = [statement for statement, _status_name in recorded[True]]
        assert any("TIMEZONE" in statement for statement in statements)
        assert any("AUTOCOMMIT" in statement for statement in statements)
        assert any("USE_CACHED_RESULT" in statement for statement in statements)
        assert any(statement.startswith("USE WAREHOUSE") for statement in statements)
        assert {status for _statement, status in recorded[True]} == {HARNESS_DEFAULT}

    @patch("benchbox.platforms.snowflake.snowflake")
    def test_failed_harness_setting_does_not_turn_a_noop_run_into_a_failure(self, _snowflake_module):
        adapter = _snowflake()
        connection = Mock()
        connection.cursor.return_value.execute.side_effect = RuntimeError("denied")

        with pytest.raises(RuntimeError):
            adapter.configure_for_benchmark(adapter._session_recording_connection(connection), "olap")

        assert _status(adapter._applied_tuning_ledger) == NOOP

    def test_platforms_declare_whether_configure_applies_tuning(self):
        assert SnowflakeAdapter.configure_session_is_tuning is False
        assert PostgreSQLAdapter.configure_session_is_tuning is True
        assert ClickHouseAdapter.configure_session_is_tuning is True

    def test_tuning_session_connection_tags_statements_with_the_session_mechanism(self):
        stub = SimpleNamespace(
            configure_session_is_tuning=True,
            _applied_tuning_ledger=AppliedTuningLedger(),
            ledger_execute_verbs=(),
        )
        connection = Mock()

        PlatformAdapter._session_recording_connection(stub, connection).execute("SET random_page_cost = 1.1")

        assert [(s.mechanism, s.status) for s in stub._applied_tuning_ledger.statements] == [
            ("session_config", "executed")
        ]
        assert _status(stub._applied_tuning_ledger) == APPLIED_UNVERIFIED

    @patch("benchbox.platforms.clickhouse.adapter.check_platform_dependencies", return_value=(True, []))
    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_clickhouse_records_only_the_tuned_olap_settings(self, mock_client_class, _deps):
        mock_client_class.return_value = Mock()
        adapter = ClickHouseAdapter(deployment_mode="server", strict_validation=False)
        adapter.tuning_enabled = True
        adapter._start_applied_tuning_ledger()
        connection = adapter.create_connection()

        adapter.configure_for_benchmark(adapter._session_recording_connection(connection), "olap")

        ledger = adapter._applied_tuning_ledger
        tuned = [s.statement for s in ledger.tuning_statements]
        assert tuned and all("max_bytes" in s or "optimize_aggregation" in s or "group_by" in s for s in tuned)
        harness = [s.statement for s in ledger.statements if s.status == HARNESS_DEFAULT]
        assert any("join_use_nulls" in s for s in harness)


class TestFailedRequestedSessionSettings:
    def test_failed_tuning_session_setting_blocks_verification(self):
        ledger = AppliedTuningLedger()
        ledger.record("SET a = 1", PHASE_SESSION, status=STATEMENT_FAILED, mechanism="session_config", error="no")

        receipt = corroborate(ledger, IntrospectedState(platform="duckdb"))

        assert [entry.verdict for entry in receipt.entries] == [UNVERIFIABLE]
        assert receipt.corroborated is False

    def test_failed_unattributed_session_statement_stays_transient(self):
        ledger = AppliedTuningLedger()
        ledger.record("SET a = 1", PHASE_SESSION, status=STATEMENT_FAILED, error="no")

        receipt = corroborate(ledger, IntrospectedState(platform="duckdb"))

        assert [entry.verdict for entry in receipt.entries] == [TRANSIENT]


def _payload(*statements: dict, satisfied: list[dict] | None = None) -> dict:
    return {"statements": list(statements), "satisfied": satisfied or []}


def _stmt(statement: str, phase: str = PHASE_DDL, status: str = "executed", mechanism: str | None = None) -> dict:
    item = {"statement": statement, "phase": phase, "status": status}
    if mechanism:
        item["mechanism"] = mechanism
    return item


_TABLE_TUNINGS = {
    "LINEITEM": {"clustering": [{"name": "l_shipdate", "order": 1}]},
    "ORDERS": {"sorting": [{"name": "o_orderdate", "order": 1}]},
}


class TestCountsDeriveFromAppliedStatements:
    def test_requested_types_without_rendered_statements_are_not_counted(self):
        payload = _payload(
            _stmt("ALTER TABLE LINEITEM CLUSTER BY (l_shipdate)"),
            _stmt("ALTER SESSION SET TIMEZONE = 'UTC'", PHASE_SESSION),
        )

        counts = applied_tuning_counts(
            ["check_constraints", "unique_constraints", "primary_keys", "clustering", "sorting"],
            _TABLE_TUNINGS,
            payload,
        )

        assert counts == {"tables_tuned": 1, "tuning_types": ["clustering"]}

    def test_constraints_rendered_in_ddl_are_counted(self):
        payload = _payload(_stmt("CREATE TABLE t (a INT, PRIMARY KEY (a))"))

        counts = applied_tuning_counts(["primary_keys", "foreign_keys"], {}, payload)

        assert counts["tuning_types"] == ["primary_keys"]

    def test_failed_statements_do_not_count(self):
        payload = _payload(_stmt("ALTER TABLE LINEITEM CLUSTER BY (l_shipdate)", status="failed"))

        counts = applied_tuning_counts(["clustering"], _TABLE_TUNINGS, payload)

        assert counts == {"tables_tuned": 0, "tuning_types": []}

    def test_session_config_requires_an_attributed_session_statement(self):
        harness_only = _payload(_stmt("SET x = 1", PHASE_SESSION))
        attributed = _payload(_stmt("SET spark.x=1", PHASE_SESSION, mechanism="spark_session_config"))

        assert applied_tuning_counts(["session_config"], {}, harness_only)["tuning_types"] == []
        assert applied_tuning_counts(["session_config"], {}, attributed)["tuning_types"] == ["session_config"]

    def test_empty_ledger_reports_zero_counts(self):
        assert applied_tuning_counts(["primary_keys"], _TABLE_TUNINGS, {}) == {"tables_tuned": 0, "tuning_types": []}


class TestResultSummaryCounts:
    def _result(self, ledger: dict | None, requested: dict) -> object:
        benchmark = SimpleNamespace(benchmark_name="tpch", scale_factor=0.01, compliance_class=None)
        return build_enhanced_benchmark_result(
            benchmark=benchmark,
            platform="snowflake",
            query_results=[],
            tunings_applied=requested,
            tuning_config_hash="a" * 64,
            tuning_source="auto_discovered",
            tuning_validation_status="noop",
            applied_tuning_ledger=ledger,
        )

    def test_summary_counts_ignore_requested_but_unrendered_tuning(self):
        requested = {
            "primary_keys": {"enabled": True},
            "check_constraints": {"enabled": True},
            "unique_constraints": {"enabled": True},
            "table_tunings": {
                "LINEITEM": {"table_name": "LINEITEM", "clustering": [{"name": "l_shipdate", "order": 1}]}
            },
        }
        ledger = {
            "status": "applied_unverified",
            "statements": [_stmt("ALTER TABLE LINEITEM CLUSTER BY (l_shipdate)")],
            "dropped": [],
        }

        summary = _build_tuning_summary(self._result(ledger, requested))

        assert summary["counts"] == {"tables_tuned": 1, "tuning_types": ["clustering"]}

    def test_summary_counts_are_zero_when_ledger_is_empty_for_a_ledger_era_run(self):
        requested = {
            "table_tunings": {
                "LINEITEM": {"table_name": "LINEITEM", "clustering": [{"name": "l_shipdate", "order": 1}]}
            }
        }

        summary = _build_tuning_summary(self._result(None, requested))

        assert summary["counts"] == {"tables_tuned": 0, "tuning_types": []}
