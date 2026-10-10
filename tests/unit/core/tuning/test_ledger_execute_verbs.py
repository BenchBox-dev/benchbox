from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from benchbox.core.tuning.applied_ledger import (
    EXECUTED,
    PHASE_DDL,
    STATEMENT_FAILED,
    AppliedTuningLedger,
    is_schema_tuning_statement,
    recording_connection,
)
from benchbox.core.tuning.introspection import corroborate

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _SqlDriver:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self._fail_on = fail_on

    def sql(self, statement, *args, **kwargs):
        if self._fail_on is not None and self._fail_on in str(statement):
            raise RuntimeError("driver rejected statement")
        self.calls.append(str(statement))
        return None

    def execute(self, statement, *args, **kwargs):
        self.calls.append(str(statement))
        return "executed"


class _FakeJob:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.state = "RUNNING"
        self.job_id = "job-1"

    def result(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        self.state = "DONE"
        return "rows"

    def done(self) -> bool:
        return self.state == "DONE"

    def exception(self, *args, **kwargs):
        return self._error


class _QueryClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.submitted: list[str] = []
        self.jobs: list[_FakeJob] = []
        self._error = error

    def query(self, statement, job_config=None, **kwargs):
        self.submitted.append(str(statement))
        job = _FakeJob(self._error)
        self.jobs.append(job)
        return job

    def dataset(self, dataset_id):
        return f"ref::{dataset_id}"

    def get_dataset(self, ref):
        return ref


def _ddl_proxy(driver, verbs, ledger=None):
    ledger = ledger if ledger is not None else AppliedTuningLedger()
    return (
        recording_connection(
            driver,
            ledger,
            PHASE_DDL,
            statement_filter=is_schema_tuning_statement,
            execute_verbs=verbs,
        ),
        ledger,
    )


class TestSqlVerb:
    def test_partitioned_and_clustered_ddl_is_recorded_as_ddl(self) -> None:
        driver = _SqlDriver()
        proxy, ledger = _ddl_proxy(driver, ("sql",))

        proxy.sql("CREATE TABLE t (a INT, b INT) USING delta PARTITIONED BY (a) CLUSTER BY (b)")

        assert [(s.phase, s.status) for s in ledger.statements] == [(PHASE_DDL, EXECUTED)]
        assert "PARTITIONED BY (a)" in ledger.statements[0].statement

    def test_readback_through_sql_executes_but_is_not_recorded(self) -> None:
        driver = _SqlDriver()
        proxy, ledger = _ddl_proxy(driver, ("sql",))

        for readback in ("SELECT 1", "SHOW TABLES", "DESCRIBE t", "EXPLAIN SELECT 1"):
            proxy.sql(readback)

        assert len(driver.calls) == 4
        assert ledger.is_empty()

    def test_verb_is_not_wrapped_unless_declared(self) -> None:
        driver = _SqlDriver()
        proxy, ledger = _ddl_proxy(driver, ())

        proxy.sql("CREATE TABLE t (a INT) PARTITIONED BY (a)")

        assert driver.calls == ["CREATE TABLE t (a INT) PARTITIONED BY (a)"]
        assert ledger.is_empty()

    def test_failed_statement_is_recorded_then_reraised(self) -> None:
        driver = _SqlDriver(fail_on="PARTITIONED")
        proxy, ledger = _ddl_proxy(driver, ("sql",))

        with pytest.raises(RuntimeError, match="driver rejected"):
            proxy.sql("CREATE TABLE t (a INT) PARTITIONED BY (a)")

        assert [s.status for s in ledger.statements] == [STATEMENT_FAILED]
        assert "driver rejected" in (ledger.statements[0].error or "")

    def test_capture_failure_does_not_break_the_statement(self) -> None:
        class _Unprintable:
            def __str__(self) -> str:
                raise ValueError("cannot render")

        class _Driver:
            def __init__(self) -> None:
                self.ran = False

            def sql(self, statement):
                self.ran = True

        driver = _Driver()
        proxy, ledger = _ddl_proxy(driver, ("sql",))

        proxy.sql(_Unprintable())

        assert driver.ran
        assert ledger.is_empty()


class TestScriptSplitting:
    def test_transient_prefix_does_not_launder_a_tuning_statement(self) -> None:
        driver = _SqlDriver()
        ledger = AppliedTuningLedger()
        proxy = recording_connection(driver, ledger, PHASE_DDL, execute_verbs=("sql",))

        proxy.sql("SET x=1; CREATE TABLE t (a INT, z INT) ORDER BY (z)")

        assert [s.statement for s in ledger.statements] == [
            "SET x=1;",
            "CREATE TABLE t (a INT, z INT) ORDER BY (z)",
        ]
        receipt = corroborate(ledger, None)
        verdicts = {entry.statement: entry.verdict for entry in receipt.entries}
        assert verdicts["SET x=1;"] == "transient"
        assert verdicts["CREATE TABLE t (a INT, z INT) ORDER BY (z)"] != "transient"

    def test_execute_splits_multi_statement_string_under_the_filter(self) -> None:
        driver = _SqlDriver()
        proxy, ledger = _ddl_proxy(driver, ())

        proxy.execute("SET x=1; CREATE TABLE t (a INT, z INT) ORDER BY (z)")

        assert driver.calls == ["SET x=1; CREATE TABLE t (a INT, z INT) ORDER BY (z)"]
        assert [s.statement for s in ledger.statements] == ["CREATE TABLE t (a INT, z INT) ORDER BY (z)"]

    def test_readback_prefix_does_not_hide_a_following_mutation(self) -> None:
        driver = _SqlDriver()
        ledger = AppliedTuningLedger()
        proxy = recording_connection(driver, ledger, PHASE_DDL, execute_verbs=("sql",))

        proxy.sql("SELECT 1; CREATE TABLE t (a INT) PARTITIONED BY (a)")

        assert [s.statement for s in ledger.statements] == ["CREATE TABLE t (a INT) PARTITIONED BY (a)"]

    def test_single_statement_is_recorded_unchanged(self) -> None:
        driver = _SqlDriver()
        ledger = AppliedTuningLedger()
        proxy = recording_connection(driver, ledger, PHASE_DDL, execute_verbs=("sql",))
        statement = "  CREATE TABLE t (a INT)  \n"

        proxy.sql(statement)

        assert [s.statement for s in ledger.statements] == [statement]


_TRAILING_SQL_COMMENT = "-- trailing comment"
_READBACK_SQL_COMMENT = "-- readback with a trailing comment"


class TestCommentOnlyFragments:
    def test_trailing_comment_after_execute_is_not_recorded(self) -> None:
        driver = _SqlDriver()
        ledger = AppliedTuningLedger()
        proxy = recording_connection(driver, ledger, PHASE_DDL)

        proxy.execute(f"CREATE INDEX idx ON t(x); {_TRAILING_SQL_COMMENT}")

        assert driver.calls == [f"CREATE INDEX idx ON t(x); {_TRAILING_SQL_COMMENT}"]
        assert [s.statement for s in ledger.statements] == ["CREATE INDEX idx ON t(x);"]

    def test_trailing_comment_after_verb_call_is_not_recorded(self) -> None:
        driver = _SqlDriver()
        ledger = AppliedTuningLedger()
        proxy = recording_connection(driver, ledger, PHASE_DDL, execute_verbs=("sql",))

        proxy.sql(f"CREATE INDEX idx ON t(x); {_TRAILING_SQL_COMMENT}")

        assert [s.statement for s in ledger.statements] == ["CREATE INDEX idx ON t(x);"]
        receipt = corroborate(ledger, None)
        assert [entry.statement for entry in receipt.entries] == ["CREATE INDEX idx ON t(x);"]

    def test_trailing_comment_after_executescript_is_not_recorded(self) -> None:
        class _ScriptDriver:
            def __init__(self) -> None:
                self.scripts: list[str] = []

            def executescript(self, script, *args, **kwargs):
                self.scripts.append(str(script))

        driver = _ScriptDriver()
        ledger = AppliedTuningLedger()
        proxy = recording_connection(driver, ledger, PHASE_DDL)

        proxy.executescript(f"CREATE INDEX idx ON t(x); {_TRAILING_SQL_COMMENT}")

        assert [s.statement for s in ledger.statements] == ["CREATE INDEX idx ON t(x);"]

    def test_readback_script_with_comment_tail_records_nothing(self) -> None:
        driver = _SqlDriver()
        ledger = AppliedTuningLedger()
        proxy = recording_connection(driver, ledger, PHASE_DDL)

        proxy.execute(f"SELECT 1; {_READBACK_SQL_COMMENT}")

        assert driver.calls == [f"SELECT 1; {_READBACK_SQL_COMMENT}"]
        assert ledger.is_empty()

    def test_empty_split_fragment_after_execute_is_not_recorded(self) -> None:
        driver = _SqlDriver()
        ledger = AppliedTuningLedger()
        proxy = recording_connection(driver, ledger, PHASE_DDL)

        proxy.execute("CREATE INDEX idx ON t(x);;")

        assert driver.calls == ["CREATE INDEX idx ON t(x);;"]
        assert [s.statement for s in ledger.statements] == ["CREATE INDEX idx ON t(x);"]

    def test_empty_split_fragment_after_verb_call_is_not_recorded(self) -> None:
        driver = _SqlDriver()
        ledger = AppliedTuningLedger()
        proxy = recording_connection(driver, ledger, PHASE_DDL, execute_verbs=("sql",))

        proxy.sql("CREATE INDEX idx ON t(x);;")

        assert [s.statement for s in ledger.statements] == ["CREATE INDEX idx ON t(x);"]

    def test_empty_split_fragment_after_executescript_is_not_recorded(self) -> None:
        class _ScriptDriver:
            def __init__(self) -> None:
                self.scripts: list[str] = []

            def executescript(self, script, *args, **kwargs):
                self.scripts.append(str(script))

        driver = _ScriptDriver()
        ledger = AppliedTuningLedger()
        proxy = recording_connection(driver, ledger, PHASE_DDL)

        proxy.executescript("CREATE INDEX idx ON t(x);;")

        assert [s.statement for s in ledger.statements] == ["CREATE INDEX idx ON t(x);"]

    def test_delimiter_and_comment_tail_after_execute_is_not_recorded(self) -> None:
        driver = _SqlDriver()
        ledger = AppliedTuningLedger()
        proxy = recording_connection(driver, ledger, PHASE_DDL)

        proxy.execute(f"CREATE INDEX idx ON t(x);; {_TRAILING_SQL_COMMENT}")

        assert [s.statement for s in ledger.statements] == ["CREATE INDEX idx ON t(x);"]


class TestQueryJobs:
    def test_submission_alone_is_not_recorded(self) -> None:
        client = _QueryClient()
        proxy, ledger = _ddl_proxy(client, ("query",))

        proxy.query("CREATE TABLE d.t (a INT64) PARTITION BY DATE(ts) CLUSTER BY a")

        assert client.submitted
        assert ledger.is_empty()

    def test_successful_job_is_recorded_when_it_finishes(self) -> None:
        client = _QueryClient()
        proxy, ledger = _ddl_proxy(client, ("query",))

        job = proxy.query("CREATE TABLE d.t (a INT64) PARTITION BY DATE(ts) CLUSTER BY a")
        assert job.job_id == "job-1"
        job.result()
        job.result()

        assert [(s.phase, s.status) for s in ledger.statements] == [(PHASE_DDL, EXECUTED)]

    def test_job_failing_after_submission_is_recorded_failed(self) -> None:
        client = _QueryClient(error=RuntimeError("Quota exceeded"))
        proxy, ledger = _ddl_proxy(client, ("query",))

        job = proxy.query("CREATE TABLE d.t (a INT64) PARTITION BY DATE(ts)")
        with pytest.raises(RuntimeError, match="Quota exceeded"):
            job.result()

        assert [s.status for s in ledger.statements] == [STATEMENT_FAILED]
        assert "Quota exceeded" in (ledger.statements[0].error or "")

    def test_exception_accessor_settles_the_final_state(self) -> None:
        client = _QueryClient(error=RuntimeError("Not found"))
        proxy, ledger = _ddl_proxy(client, ("query",))

        job = proxy.query("CREATE TABLE d.t (a INT64) CLUSTER BY a")
        assert str(job.exception()) == "Not found"

        assert [s.status for s in ledger.statements] == [STATEMENT_FAILED]

    def test_timeout_while_waiting_is_not_a_final_state(self) -> None:
        from concurrent.futures import TimeoutError as FutureTimeoutError

        client = _QueryClient(error=FutureTimeoutError("still running"))
        proxy, ledger = _ddl_proxy(client, ("query",))

        job = proxy.query("CREATE TABLE d.t (a INT64) CLUSTER BY a")
        with pytest.raises(FutureTimeoutError):
            job.result(timeout=1)

        assert ledger.is_empty()

    def test_dry_run_and_readback_jobs_are_not_recorded(self) -> None:
        client = _QueryClient()
        proxy, ledger = _ddl_proxy(client, ("query",))

        dry = proxy.query(
            "CREATE TABLE d.t (a INT64) CLUSTER BY a",
            job_config=SimpleNamespace(dry_run=True),
        )
        select = proxy.query("SELECT * FROM d.t")
        dry.result()
        select.result()

        assert ledger.is_empty()

    def test_submission_error_is_recorded_failed(self) -> None:
        class _Rejecting(_QueryClient):
            def query(self, statement, job_config=None, **kwargs):
                raise RuntimeError("bad request")

        proxy, ledger = _ddl_proxy(_Rejecting(), ("query",))

        with pytest.raises(RuntimeError, match="bad request"):
            proxy.query("CREATE TABLE d.t (a INT64) CLUSTER BY a")

        assert [s.status for s in ledger.statements] == [STATEMENT_FAILED]


class TestFootprint:
    @pytest.mark.parametrize(
        "statement",
        [
            "CREATE TABLE t (a INT) USING parquet PARTITIONED BY (a)",
            "CREATE TABLE t (a INT) USING parquet CLUSTERED BY (a) INTO 4 BUCKETS",
            "CREATE TABLE t (a INT) DISTKEY(a)",
            "CREATE TABLE t (a INT) DISTSTYLE KEY",
            "CREATE TABLE t (a INT) DUPLICATE KEY (a) DISTRIBUTED BY HASH (a)",
            "CREATE TABLE t (a INT) PRIMARY INDEX (a)",
            "CREATE TABLE t (a INT) WITH (distribution = 'hash')",
            "CREATE TABLE t (a INT) WITH (partitioning = ARRAY['a'])",
            "CREATE TABLE t (a INT) WITH (sorted_by = ARRAY['a'])",
            "CREATE TABLE t (a INT) WITH (bucketed_by = ARRAY['a'], bucket_count = 4)",
        ],
    )
    def test_new_footprint_keywords_are_schema_tuning(self, statement: str) -> None:
        assert is_schema_tuning_statement(statement)

    def test_plain_table_is_still_not_schema_tuning(self) -> None:
        assert not is_schema_tuning_statement("CREATE TABLE t (a INT, b INT)")

    def test_keyword_inside_a_literal_does_not_count(self) -> None:
        assert not is_schema_tuning_statement("CREATE TABLE t (a INT) COMMENT 'partitioned by day'")

    @pytest.mark.parametrize(
        "statement",
        [
            "CREATE TABLE t (distkey INT)",
            "CREATE TABLE t (a INT, distkey TEXT)",
            "CREATE TABLE t (diststyle VARCHAR(16))",
            "CREATE TABLE t (sortkey INT)",
            "CREATE TABLE t (a INT, sorted_by DOUBLE)",
            "CREATE TABLE t (bucketed_by INT)",
            "CREATE TABLE t (unique INT)",
            "CREATE TABLE t (check INT)",
        ],
    )
    def test_bare_keyword_as_a_column_name_is_not_schema_tuning(self, statement: str) -> None:
        assert not is_schema_tuning_statement(statement)

    @pytest.mark.parametrize(
        "statement",
        [
            "CREATE TABLE t (a INT) PARTITIONED BY (a)",
            "CREATE TABLE t (a INT) CLUSTERED BY (a) INTO 4 BUCKETS",
            "CREATE TABLE t (a INT) DISTSTYLE EVEN DISTKEY (a)",
            "CREATE TABLE t (a INT) DUPLICATE KEY (a)",
            "CREATE TABLE t (a INT) PRIMARY INDEX (a)",
            "CREATE TABLE t (a INT) WITH (distribution = 'hash')",
            "CREATE TABLE t (a INT) WITH (partitioning = ARRAY['a'])",
            "CREATE TABLE t (a INT) WITH (sorted_by = ARRAY['a'])",
            "CREATE TABLE t (a INT) WITH (bucketed_by = ARRAY['a'])",
            "CREATE TABLE t (a INT UNIQUE)",
            "CREATE TABLE t (a INT, UNIQUE NULLS NOT DISTINCT (a))",
            "CREATE TABLE t (a INT, UNIQUE NULLS DISTINCT (a))",
        ],
    )
    def test_bare_keyword_in_clause_syntax_is_schema_tuning(self, statement: str) -> None:
        assert is_schema_tuning_statement(statement)


class TestAdapterCreateSchema:
    def test_spark_tuned_schema_ddl_reaches_the_ledger(self) -> None:
        from benchbox.platforms.spark import SparkAdapter

        driver = _SqlDriver()
        proxy, ledger = _ddl_proxy(driver, SparkAdapter.ledger_execute_verbs)
        fake_self = SimpleNamespace(
            _create_schema_with_tuning=lambda *a, **k: (
                "CREATE TABLE orders (o_id INT, o_date DATE) USING parquet PARTITIONED BY (o_date);\n"
                "CREATE TABLE lineitem (l_id INT, l_key INT) USING delta CLUSTER BY (l_key);\n"
                "CREATE TABLE plain (p_id INT);"
            ),
            table_format="parquet",
            logger=logging.getLogger("test"),
            _remove_orphaned_table_location=lambda *a, **k: None,
        )

        SparkAdapter.create_schema(fake_self, SimpleNamespace(), proxy)

        assert len(driver.calls) == 3
        recorded = [s.statement for s in ledger.statements]
        assert len(recorded) == 2
        assert any("PARTITIONED BY" in s for s in recorded)
        assert any("CLUSTER BY" in s for s in recorded)
        assert {s.phase for s in ledger.statements} == {PHASE_DDL}
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == "applied_unverified"

    def test_bigquery_tuned_schema_ddl_records_final_job_state(self) -> None:
        from benchbox.platforms.bigquery import BigQueryAdapter

        client = _QueryClient()
        proxy, ledger = _ddl_proxy(client, BigQueryAdapter.ledger_execute_verbs)
        fake_self = SimpleNamespace(
            dataset_id="ds",
            location="US",
            _create_schema_with_tuning=lambda *a, **k: (
                "CREATE TABLE t1 (a INT64, ts TIMESTAMP) PARTITION BY DATE(ts) CLUSTER BY a;\nCREATE TABLE t2 (b INT64);"
            ),
            _convert_to_bigquery_table=lambda statement: statement,
            logger=logging.getLogger("test"),
        )

        with patch("benchbox.platforms.bigquery.bigquery"):
            BigQueryAdapter.create_schema(fake_self, SimpleNamespace(), proxy)

        assert len(client.submitted) == 2
        assert [s.status for s in ledger.statements] == [EXECUTED]
        assert "CLUSTER BY a" in ledger.statements[0].statement

    def test_datafusion_tuned_ddl_through_sql_is_recorded(self) -> None:
        from benchbox.platforms.datafusion import DataFusionAdapter

        driver = _SqlDriver()
        proxy, ledger = _ddl_proxy(driver, DataFusionAdapter.ledger_execute_verbs)

        proxy.sql("CREATE TABLE t (a INT) PARTITIONED BY (a)")
        proxy.sql("SELECT COUNT(*) FROM t")

        assert len(driver.calls) == 2
        assert [(s.phase, s.status) for s in ledger.statements] == [(PHASE_DDL, EXECUTED)]
