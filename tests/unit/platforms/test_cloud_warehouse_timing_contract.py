from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.results.fetch_mode import ConversionClock
from benchbox.core.results.timer_scope import TIMING_METHOD_QUERY_ONLY

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SETUP_DELAY_SECONDS = 0.05


class _SnowflakeCursor:
    def __init__(self, statement_ids, history_rows=(), slow_prefix="ALTER SESSION"):
        self.statement_ids = statement_ids
        self.history_rows = list(history_rows)
        self.slow_prefix = slow_prefix
        self.executed = []
        self.sfqid = None
        self._last = ""

    def execute(self, sql, *args, **kwargs):
        self._last = sql
        self.executed.append(sql)
        if sql.startswith(self.slow_prefix):
            time.sleep(SETUP_DELAY_SECONDS)
        self.sfqid = self.statement_ids.get(sql)

    def fetchall(self):
        if "QUERY_HISTORY" in self._last:
            return self.history_rows
        return [(1, "a"), (2, "b")]

    def fetchone(self):
        return None

    def close(self):
        pass


@pytest.fixture
def snowflake_adapter():
    with (
        patch("benchbox.platforms.snowflake.snowflake"),
        patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])),
    ):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        yield SnowflakeAdapter(account="acct", username="user", password="pw", warehouse="WH", database="DB")


class TestSnowflakeTimingContract:
    def test_declares_method_two_and_history_source(self, snowflake_adapter):
        assert snowflake_adapter.timing_method == TIMING_METHOD_QUERY_ONLY
        assert snowflake_adapter.platform_timing_source == "snowflake.query_history"
        assert snowflake_adapter.platform_timing_definition == "server_complete"

    def test_tag_setup_is_outside_timer_and_history_is_by_statement_id(self, snowflake_adapter):
        history = [("sfqid-1", 1200, 200, 900, 50, 0, 0, 0, 4096, 2)]
        cursor = _SnowflakeCursor({"SELECT 1": "sfqid-1"}, history)
        connection = Mock()
        connection.cursor.return_value = cursor

        clock = ConversionClock().start()
        try:
            result = snowflake_adapter.execute_query(connection, "SELECT 1", "Q1")
        finally:
            excluded = clock.finish()

        assert result["status"] == "SUCCESS"
        assert result["execution_time_seconds"] < SETUP_DELAY_SECONDS
        assert excluded >= SETUP_DELAY_SECONDS
        assert result["request_count"] == 1
        assert result["result_bytes"] > 0
        lookups = [sql for sql in cursor.executed if "QUERY_HISTORY" in sql]
        assert lookups
        assert all("QUERY_ID IN ('sfqid-1')" in sql and "QUERY_TAG" not in sql for sql in lookups)
        timing = result["platform_timing"]
        assert timing["source"] == "snowflake.query_history"
        assert timing["status"] in {"valid", "suspect"}
        assert timing["platform_ms"] == 1200
        assert timing["queue_ms"] == 50
        assert timing["compile_ms"] == 200
        assert timing["exec_ms"] == 900
        assert timing["requests"][0]["id"] == "sfqid-1"
        assert timing["requests"][0]["fields"]["TOTAL_ELAPSED_TIME"] == 1200
        assert result["query_statistics"]["retrieval_attempts"] == 1 or "note" in result["query_statistics"]

    def test_explicit_transaction_records_begin_statements_and_commit(self, snowflake_adapter):
        ids = {"BEGIN": "id-b", "SELECT 1": "id-1", "SELECT 2": "id-2", "COMMIT": "id-c"}
        cursor = _SnowflakeCursor(ids, history_rows=[])
        connection = Mock()
        connection.cursor.return_value = cursor

        with patch.object(snowflake_adapter, "fetch_platform_timings", return_value={}):
            result = snowflake_adapter.execute_query(connection, "SELECT 1; SELECT 2", "Q2")

        assert result["status"] == "SUCCESS"
        assert result["request_count"] == 4
        kinds = [(entry["kind"], entry["id"]) for entry in result["platform_timing"]["requests"]]
        assert kinds == [("begin", "id-b"), ("query", "id-1"), ("query", "id-2"), ("commit", "id-c")]

    def test_history_lag_is_retried_within_bounded_budget(self, snowflake_adapter, monkeypatch):
        cursor = _SnowflakeCursor({})
        rows = [("sfqid-9", 10, 1, 2, 0, 0, 0, 0, 0, 0)]
        batches = iter([[], rows])
        cursor.fetchall = lambda: next(batches)
        connection = Mock()
        connection.cursor.return_value = cursor

        monkeypatch.setattr("benchbox.platforms.snowflake.SNOWFLAKE_HISTORY_RETRY_DELAY_SECONDS", 0.0)
        found = snowflake_adapter.fetch_platform_timings(connection, ["sfqid-9"])

        assert found["sfqid-9"].platform_ms == 10

    def test_unsafe_statement_ids_are_never_interpolated(self, snowflake_adapter):
        connection = Mock()
        assert snowflake_adapter.fetch_platform_timings(connection, ["x'; DROP TABLE t; --"]) == {}
        connection.cursor.assert_not_called()


class TestSnowparkConnectTimingContract:
    def test_declares_method_two_without_platform_source(self):
        with patch("benchbox.platforms.snowpark_connect.check_platform_dependencies", return_value=(True, [])):
            from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

            assert SnowparkConnectAdapter.timing_method == TIMING_METHOD_QUERY_ONLY
            assert SnowparkConnectAdapter.platform_timing_source is None

    def test_execute_counts_the_sql_and_collect_requests_and_result_bytes(self):
        with patch("benchbox.platforms.snowpark_connect.check_platform_dependencies", return_value=(True, [])):
            from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

            adapter = SnowparkConnectAdapter.__new__(SnowparkConnectAdapter)
            adapter._query_count = 0
            adapter._total_execution_time_seconds = 0.0
            adapter._session = None
            session = Mock()
            session.sql.return_value.collect.return_value = [(1, "a"), (2, "b")]
            with patch.object(
                SnowparkConnectAdapter,
                "_build_query_result_with_validation",
                side_effect=lambda **kwargs: {"status": "SUCCESS", **kwargs.get("timing_fields", {})},
            ):
                result = adapter.execute_query(session, "SELECT 1", "Q1")

        assert result["request_count"] == 2
        assert result["result_bytes"] > 0
        assert "platform_timing" not in result


@pytest.fixture
def bigquery_adapter():
    with (
        patch("benchbox.platforms.bigquery.bigquery", MagicMock()),
        patch("benchbox.platforms.bigquery.storage", MagicMock()),
        patch("benchbox.platforms.bigquery.service_account", MagicMock()),
        patch("benchbox.platforms.bigquery.google_auth", MagicMock()),
        patch("benchbox.platforms.bigquery.check_platform_dependencies", return_value=(True, [])),
    ):
        from benchbox.platforms.bigquery import BigQueryAdapter

        yield BigQueryAdapter(project_id="p", dataset_id="d")


def _bigquery_job(job_id="job-1", created=0.0, started=0.2, ended=1.5):
    def moment(seconds):
        return None if seconds is None else datetime.fromtimestamp(seconds, tz=timezone.utc)

    job = Mock()
    job.job_id = job_id
    job.created = moment(created)
    job.started = moment(started)
    job.ended = moment(ended)
    job.slot_millis = 4000
    job.total_bytes_processed = 1000
    job.total_bytes_billed = 10485760
    job.cache_hit = False
    return job


class TestBigQueryTimingContract:
    def test_declares_method_two_and_job_source(self, bigquery_adapter):
        assert bigquery_adapter.timing_method == TIMING_METHOD_QUERY_ONLY
        assert bigquery_adapter.platform_timing_source == "bigquery.jobs"
        assert bigquery_adapter.platform_timing_definition == "server_complete"

    def test_execute_records_job_id_and_job_lookup_by_id(self, bigquery_adapter):
        client = Mock()
        job = _bigquery_job()
        job.result.return_value = [(1, "a"), (2, "b")]
        client.query.return_value = job
        client.get_job.return_value = _bigquery_job()

        result = bigquery_adapter.execute_query(client, "SELECT 1", "Q1")

        assert result["status"] == "SUCCESS"
        assert result["request_count"] == 1
        assert result["result_bytes"] > 0
        client.get_job.assert_called_once_with("job-1")
        timing = result["platform_timing"]
        assert timing["platform_ms"] == pytest.approx(1500.0)
        assert timing["queue_ms"] == pytest.approx(200.0)
        assert timing["exec_ms"] == pytest.approx(1300.0)
        assert timing["cache_hit"] is False
        assert timing["requests"][0]["id"] == "job-1"
        assert timing["requests"][0]["fields"]["totalSlotMs"] == 4000
        assert timing["requests"][0]["fields"]["totalBytesBilled"] == 10485760

    def test_translation_is_outside_timer(self, bigquery_adapter):
        client = Mock()
        job = _bigquery_job()
        job.result.return_value = [(1,)]
        client.query.return_value = job
        client.get_job.return_value = _bigquery_job()

        def slow_translation(sql):
            time.sleep(SETUP_DELAY_SECONDS)
            return sql

        clock = ConversionClock().start()
        try:
            with patch.object(bigquery_adapter, "_apply_safe_division", side_effect=slow_translation):
                result = bigquery_adapter.execute_query(client, "SELECT 1", "Q1")
        finally:
            excluded = clock.finish()

        assert result["execution_time_seconds"] < SETUP_DELAY_SECONDS
        assert excluded >= SETUP_DELAY_SECONDS

    def test_unfinished_job_history_is_retried_then_raises_into_unavailable(self, bigquery_adapter, monkeypatch):
        client = Mock()
        client.get_job.return_value = _bigquery_job(ended=None)
        monkeypatch.setattr("benchbox.platforms.bigquery.BIGQUERY_JOB_STATS_RETRY_DELAY_SECONDS", 0.0)
        assert bigquery_adapter.fetch_platform_timings(client, ["job-1"]) == {}


@pytest.fixture
def redshift_adapter(monkeypatch):
    monkeypatch.setattr("benchbox.platforms.redshift.redshift_connector", MagicMock(), raising=False)
    from benchbox.platforms.redshift import RedshiftAdapter

    return RedshiftAdapter(host="localhost", username="test", password="test")


class _RedshiftCursor:
    def __init__(self, history_rows=()):
        self.history_rows = list(history_rows)
        self.executed = []
        self._last = ""

    def execute(self, sql, params=None):
        self._last = sql
        self.executed.append((sql, params))

    def fetchone(self):
        return (42,) if "pg_last_query_id" in self._last else None

    def fetchall(self):
        return self.history_rows if "sys_query_history" in self._last else [(1,), (2,)]

    def close(self):
        pass


class TestRedshiftTimingContract:
    def test_declares_method_two_and_includes_fetch(self, redshift_adapter):
        assert redshift_adapter.timing_method == TIMING_METHOD_QUERY_ONLY
        assert redshift_adapter.platform_timing_source == "redshift.sys_query_history"
        assert redshift_adapter.platform_timing_definition == "includes_fetch"

    def test_provisioned_cluster_reads_stl_query_by_stl_query_id(self, redshift_adapter):
        redshift_adapter.deployment_type = "provisioned"
        cursor = Mock()
        cursor.fetchall.return_value = [(42, 250000, 10000, 200000)]
        connection = Mock()
        connection.cursor.return_value = cursor

        timings = redshift_adapter.fetch_platform_timings(connection, ["42"])

        sql, params = cursor.execute.call_args.args
        assert "FROM stl_query q LEFT JOIN stl_wlm_query w" in sql
        assert "sys_query_history" not in sql
        assert params == (42,)
        assert redshift_adapter.platform_timing_source == "redshift.stl_query"
        assert timings["42"].platform_ms == 250.0
        assert timings["42"].queue_ms == 10.0
        assert timings["42"].exec_ms == 200.0

    def test_execute_uses_query_id_from_session_and_sys_query_history(self, redshift_adapter):
        history = [(42, 250000, 10000, 20000, 5000, 200000, 0, 2, 100, True)]
        cursor = _RedshiftCursor(history)
        connection = Mock()
        connection.cursor.return_value = cursor
        connection.autocommit = True

        with patch.object(redshift_adapter, "_get_query_statistics", return_value={}):
            result = redshift_adapter.execute_query(connection, "SELECT 1", "Q1")

        assert result["status"] == "SUCCESS"
        assert result["request_count"] == 1
        assert result["result_bytes"] > 0
        lookup = [(sql, params) for sql, params in cursor.executed if "sys_query_history" in sql]
        assert lookup and lookup[0][1] == (42,)
        assert "query_group" not in lookup[0][0].lower() and "tag" not in lookup[0][0].lower()
        timing = result["platform_timing"]
        assert timing["platform_ms"] == 250.0
        assert timing["queue_ms"] == 10.0
        assert timing["compile_ms"] == 20.0
        assert timing["exec_ms"] == 200.0
        assert timing["cache_hit"] is True
        assert timing["requests"][0]["id"] == "42"
        assert timing["requests"][0]["fields"]["elapsed_time"] == 250000

    def test_pg_last_query_id_runs_after_timer_stops(self, redshift_adapter):
        cursor = _RedshiftCursor()

        original = cursor.execute

        def slow_execute(sql, params=None):
            if "pg_last_query_id" in sql:
                time.sleep(SETUP_DELAY_SECONDS)
            original(sql, params)

        cursor.execute = slow_execute
        connection = Mock()
        connection.cursor.return_value = cursor
        connection.autocommit = True

        clock = ConversionClock().start()
        try:
            with patch.object(redshift_adapter, "_get_query_statistics", return_value={}):
                result = redshift_adapter.execute_query(connection, "SELECT 1", "Q1")
        finally:
            excluded = clock.finish()

        assert result["execution_time_seconds"] < SETUP_DELAY_SECONDS
        assert excluded >= SETUP_DELAY_SECONDS

    def test_autocommit_off_records_implicit_begin(self, redshift_adapter):
        cursor = _RedshiftCursor()
        connection = Mock()
        connection.cursor.return_value = cursor
        connection.autocommit = False
        connection.get_transaction_status.return_value = 0

        with (
            patch.object(redshift_adapter, "_get_query_statistics", return_value={}),
            patch.object(redshift_adapter, "fetch_platform_timings", return_value={}),
        ):
            result = redshift_adapter.execute_query(connection, "SELECT 1", "Q1")

        assert result["request_count"] == 2
        assert [entry["kind"] for entry in result["platform_timing"]["requests"]] == ["begin", "query"]


@pytest.fixture
def athena_adapter(tmp_path, monkeypatch):
    aws_dir = tmp_path / ".aws"
    aws_dir.mkdir()
    (aws_dir / "credentials").write_text("[default]\naws_access_key_id = t\naws_secret_access_key = t\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    with patch.dict(
        "sys.modules",
        {"boto3": MagicMock(), "pyathena": MagicMock(), "pyathena.cursor": MagicMock()},
    ):
        from benchbox.platforms.athena import AthenaAdapter

        yield AthenaAdapter(s3_bucket="bucket")


class TestAthenaTimingContract:
    def test_declares_method_two_and_server_complete(self, athena_adapter):
        assert athena_adapter.timing_method == TIMING_METHOD_QUERY_ONLY
        assert athena_adapter.platform_timing_source == "athena.get_query_execution"
        assert athena_adapter.platform_timing_definition == "server_complete"

    def test_execute_records_query_execution_id_and_statistics(self, athena_adapter):
        cursor = MagicMock()
        cursor.query_id = "exec-1"
        cursor.fetchall.return_value = [(1, "a"), (2, "b")]
        cursor.data_scanned_in_bytes = 2048
        connection = MagicMock()
        connection.cursor.return_value = cursor
        connection.client.get_query_execution.return_value = {
            "QueryExecution": {
                "Statistics": {
                    "TotalExecutionTimeInMillis": 900,
                    "QueryQueueTimeInMillis": 100,
                    "QueryPlanningTimeInMillis": 150,
                    "EngineExecutionTimeInMillis": 600,
                    "ServiceProcessingTimeInMillis": 40,
                    "ServicePreProcessingTimeInMillis": 10,
                    "DataScannedInBytes": 2048,
                    "ResultReuseInformation": {"ReusedPreviousResult": False},
                }
            }
        }

        result = athena_adapter.execute_query(connection, "SELECT 1", "Q1")

        assert result["status"] == "SUCCESS"
        assert result["request_count"] == 1
        assert result["result_bytes"] > 0
        connection.client.get_query_execution.assert_called_once_with(QueryExecutionId="exec-1")
        timing = result["platform_timing"]
        assert timing["platform_ms"] == 900.0
        assert timing["queue_ms"] == 100.0
        assert timing["compile_ms"] == 150.0
        assert timing["exec_ms"] == 600.0
        assert timing["cache_hit"] is False
        assert timing["requests"][0]["id"] == "exec-1"
        assert timing["requests"][0]["fields"]["ServiceProcessingTimeInMillis"] == 40

    def test_missing_statistics_total_yields_no_timing(self, athena_adapter):
        connection = MagicMock()
        connection.client.get_query_execution.return_value = {"QueryExecution": {"Statistics": {}}}
        assert athena_adapter.fetch_platform_timings(connection, ["exec-1"]) == {}
