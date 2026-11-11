from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.aws import AthenaSparkAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _adapter() -> AthenaSparkAdapter:
    with (
        patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
        patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
        patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        return AthenaSparkAdapter(workgroup="wg", s3_staging_dir="s3://bucket/path")


def _benchmark(*tables: str) -> SimpleNamespace:
    return SimpleNamespace(get_table_names=lambda: list(tables))


def test_client_helpers_cache_clients() -> None:
    adapter = _adapter()

    athena_client = MagicMock()
    glue_client = MagicMock()
    s3_client = MagicMock()

    with patch("benchbox.platforms.aws.athena_spark_adapter.boto3") as mock_boto:
        mock_boto.client.side_effect = [athena_client, glue_client, s3_client]
        assert adapter._get_athena_client() is adapter._get_athena_client()
        assert adapter._get_glue_client() is adapter._get_glue_client()
        assert adapter._get_s3_client() is adapter._get_s3_client()


def test_close_clears_session_when_terminate_fails() -> None:
    adapter = _adapter()
    adapter._session_id = "session-1"
    client = MagicMock()
    client.terminate_session.side_effect = RuntimeError("terminate failed")

    with patch.object(adapter, "_get_athena_client", return_value=client):
        adapter.close()

    assert adapter._session_id is None


def test_apply_tuning_configuration_collects_results() -> None:
    adapter = _adapter()
    adapter.apply_primary_keys = MagicMock(return_value={"pk": 1})
    adapter.apply_foreign_keys = MagicMock(return_value={"fk": 1})
    adapter.apply_platform_optimizations = MagicMock(return_value={"opt": 1})

    config = SimpleNamespace(
        scale_factor=10.0,
        primary_keys=["a"],
        foreign_keys=["b"],
        platform_optimizations={"x": 1},
    )
    result = adapter.apply_tuning_configuration(config)

    assert adapter._scale_factor == 10.0
    assert result["primary_keys"] == {"pk": 1}
    assert result["foreign_keys"] == {"fk": 1}
    assert result["platform_optimizations"] == {"opt": 1}


def test_athena_spark_session_states_defined() -> None:
    from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkSessionState

    assert AthenaSparkSessionState.CREATING == "CREATING"
    assert AthenaSparkSessionState.IDLE == "IDLE"
    assert AthenaSparkSessionState.FAILED == "FAILED"
    assert "IDLE" in AthenaSparkSessionState.READY_STATES
    assert "FAILED" in AthenaSparkSessionState.TERMINAL_STATES


def test_athena_spark_calculation_states_defined() -> None:
    from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkCalculationState

    assert AthenaSparkCalculationState.QUEUED == "QUEUED"
    assert AthenaSparkCalculationState.RUNNING == "RUNNING"
    assert AthenaSparkCalculationState.COMPLETED == "COMPLETED"
    assert AthenaSparkCalculationState.FAILED == "FAILED"
    assert AthenaSparkCalculationState.CANCELED == "CANCELED"
    assert "COMPLETED" in AthenaSparkCalculationState.TERMINAL_STATES
    assert "COMPLETED" in AthenaSparkCalculationState.SUCCESS_STATES


def test_wait_for_session_ready_succeeds_on_idle_state() -> None:
    adapter = _adapter()
    adapter._session_id = "sess-1"
    client = MagicMock()
    client.get_session_status.return_value = {"Status": {"State": "IDLE"}}

    with patch.object(adapter, "_get_athena_client", return_value=client):
        adapter._wait_for_session_ready(timeout_seconds=10)

    client.get_session_status.assert_called_once_with(SessionId="sess-1")


def test_wait_for_session_ready_raises_on_failed_state() -> None:
    from benchbox.core.exceptions import ConfigurationError

    adapter = _adapter()
    adapter._session_id = "sess-1"
    client = MagicMock()
    client.get_session_status.return_value = {"Status": {"State": "FAILED", "StateChangeReason": "Out of capacity"}}

    with patch.object(adapter, "_get_athena_client", return_value=client):
        with pytest.raises(ConfigurationError, match="Session failed"):
            adapter._wait_for_session_ready(timeout_seconds=10)


def test_wait_for_session_ready_raises_on_timeout() -> None:
    from benchbox.core.exceptions import ConfigurationError

    adapter = _adapter()
    adapter._session_id = "sess-1"
    client = MagicMock()
    client.get_session_status.return_value = {"Status": {"State": "CREATING"}}

    with (
        patch.object(adapter, "_get_athena_client", return_value=client),
        patch("benchbox.platforms.aws.athena_spark_adapter.elapsed_seconds", return_value=999),
    ):
        with pytest.raises(ConfigurationError, match="timed out"):
            adapter._wait_for_session_ready(timeout_seconds=1)


def test_wait_for_calculation_complete_returns_completed() -> None:
    adapter = _adapter()
    client = MagicMock()
    client.get_calculation_execution_status.return_value = {"Status": {"State": "COMPLETED"}}

    with patch.object(adapter, "_get_athena_client", return_value=client):
        state = adapter._wait_for_calculation_complete("calc-1", timeout_seconds=10)

    assert state == "COMPLETED"


def test_wait_for_calculation_complete_raises_on_timeout() -> None:
    adapter = _adapter()
    client = MagicMock()
    client.get_calculation_execution_status.return_value = {"Status": {"State": "RUNNING"}}

    with (
        patch.object(adapter, "_get_athena_client", return_value=client),
        patch("benchbox.platforms.aws.athena_spark_adapter.elapsed_seconds", return_value=9999),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            adapter._wait_for_calculation_complete("calc-1", timeout_seconds=1)


def test_get_platform_info_basic_fields() -> None:
    adapter = _adapter()
    info = adapter.get_platform_info()
    assert info["platform"] == "athena-spark"
    assert info["vendor"] == "AWS"
    assert "workgroup" in info


def test_create_connection_reuses_existing_session() -> None:
    adapter = _adapter()
    adapter._session_id = "existing-session"
    client = MagicMock()
    client.get_session_status.return_value = {"Status": {"State": "IDLE"}}

    with patch.object(adapter, "_get_athena_client", return_value=client):
        result = adapter.create_connection()

    assert result["session_id"] == "existing-session"
    assert result["status"] == "connected"


def test_create_connection_starts_new_session() -> None:
    adapter = _adapter()
    client = MagicMock()
    client.start_session.return_value = {"SessionId": "new-session", "State": "CREATING"}
    client.get_session_status.return_value = {"Status": {"State": "IDLE"}}

    with (
        patch.object(adapter, "_get_athena_client", return_value=client),
        patch.object(adapter, "_wait_for_session_ready"),
    ):
        result = adapter.create_connection()

    assert result["session_id"] == "new-session"


def test_create_connection_raises_on_client_error() -> None:
    from benchbox.core.exceptions import ConfigurationError

    adapter = _adapter()
    client = MagicMock()

    client.start_session.side_effect = type(
        "ClientError",
        (Exception,),
        {"response": {"Error": {"Code": "InvalidRequestException", "Message": "workgroup not spark-enabled"}}},
    )()

    with (
        patch.object(adapter, "_get_athena_client", return_value=client),
        pytest.raises(Exception),
    ):
        adapter.create_connection()


def test_create_schema_database_already_exists() -> None:
    adapter = _adapter()
    glue = MagicMock()
    glue.get_database.return_value = {"Database": {"Name": "benchbox"}}
    glue.exceptions.EntityNotFoundException = type("ENFE", (Exception,), {})

    with patch.object(adapter, "_get_glue_client", return_value=glue):
        adapter.create_schema(_benchmark(), None)

    glue.create_database.assert_not_called()


def test_create_schema_creates_database_when_missing() -> None:
    adapter = _adapter()
    glue = MagicMock()
    glue.exceptions.EntityNotFoundException = type("ENFE", (Exception,), {})
    glue.get_database.side_effect = glue.exceptions.EntityNotFoundException("not found")

    with patch.object(adapter, "_get_glue_client", return_value=glue):
        adapter.create_schema(_benchmark(), None)

    glue.create_database.assert_called_once()


def test_submit_calculation_raises_without_session() -> None:
    from benchbox.core.exceptions import ConfigurationError

    adapter = _adapter()
    with pytest.raises(ConfigurationError, match="No active session"):
        adapter._submit_calculation("SELECT 1")


def test_submit_calculation_with_wait() -> None:
    adapter = _adapter()
    adapter._session_id = "sess-1"
    client = MagicMock()
    client.start_calculation_execution.return_value = {
        "CalculationExecutionId": "calc-1",
        "State": "RUNNING",
    }

    with (
        patch.object(adapter, "_get_athena_client", return_value=client),
        patch.object(adapter, "_wait_for_calculation_complete", return_value="COMPLETED") as mock_wait,
    ):
        calc_id, state = adapter._submit_calculation("SELECT 1", wait_for_completion=True)

    assert calc_id == "calc-1"
    assert state == "COMPLETED"
    mock_wait.assert_called_once_with("calc-1")


def test_submit_calculation_escapes_quote_bearing_sql() -> None:
    import json

    adapter = _adapter()
    adapter._session_id = "sess-1"
    client = MagicMock()
    client.start_calculation_execution.return_value = {
        "CalculationExecutionId": "calc-3",
        "State": "COMPLETED",
    }

    query = "SELECT 'it''s' FROM t WHERE x = 'a\nb\\'"
    with patch.object(adapter, "_get_athena_client", return_value=client):
        adapter._submit_calculation(query, wait_for_completion=False)

    code = client.start_calculation_execution.call_args.kwargs["CodeBlock"]
    compile(code, "<generated>", "exec")
    assert json.dumps(query) in code


def test_submit_calculation_python_code_type() -> None:
    adapter = _adapter()
    adapter._session_id = "sess-1"
    client = MagicMock()
    client.start_calculation_execution.return_value = {
        "CalculationExecutionId": "calc-2",
        "State": "COMPLETED",
    }

    with patch.object(adapter, "_get_athena_client", return_value=client):
        calc_id, state = adapter._submit_calculation("print('hello')", code_type="PYTHON", wait_for_completion=False)

    assert calc_id == "calc-2"
    call_code = client.start_calculation_execution.call_args[1]["CodeBlock"]
    assert "print('hello')" in call_code
    assert "spark.sql" not in call_code


def test_get_calculation_result_from_s3_uri() -> None:
    adapter = _adapter()
    client = MagicMock()
    client.get_calculation_execution.return_value = {"Result": {"ResultS3Uri": "s3://bucket/results/calc.json"}}

    with (
        patch.object(adapter, "_get_athena_client", return_value=client),
        patch.object(adapter, "_fetch_results_from_s3", return_value=[{"x": 1}]) as mock_fetch,
    ):
        results = adapter._get_calculation_result("calc-1")

    assert results == [{"x": 1}]
    mock_fetch.assert_called_once_with("s3://bucket/results/calc.json")


def test_get_calculation_result_falls_back_to_stdout() -> None:
    adapter = _adapter()
    client = MagicMock()
    client.get_calculation_execution.return_value = {"Result": {"StdOutS3Uri": "s3://bucket/stdout/calc.txt"}}

    with (
        patch.object(adapter, "_get_athena_client", return_value=client),
        patch.object(adapter, "_fetch_results_from_s3", return_value=[{"output": "hello"}]) as mock_fetch,
    ):
        results = adapter._get_calculation_result("calc-1")

    assert len(results) == 1
    mock_fetch.assert_called_once_with("s3://bucket/stdout/calc.txt")


def test_get_calculation_result_returns_empty_on_exception() -> None:
    adapter = _adapter()
    client = MagicMock()
    client.get_calculation_execution.side_effect = RuntimeError("boom")

    with patch.object(adapter, "_get_athena_client", return_value=client):
        results = adapter._get_calculation_result("calc-1")

    assert results == []


def test_fetch_results_from_s3_parses_json_lines() -> None:
    adapter = _adapter()
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: b'{"a": 1}\n{"b": 2}\n')}

    with patch.object(adapter, "_get_s3_client", return_value=s3):
        results = adapter._fetch_results_from_s3("s3://bucket/key/data.json")

    assert len(results) == 2


def test_fetch_results_from_s3_handles_plain_text() -> None:
    adapter = _adapter()
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"not json\n")}

    with patch.object(adapter, "_get_s3_client", return_value=s3):
        results = adapter._fetch_results_from_s3("s3://bucket/key/data.txt")

    assert results[0]["output"] == "not json"


def test_fetch_results_from_s3_returns_empty_on_error() -> None:
    adapter = _adapter()
    s3 = MagicMock()
    s3.get_object.side_effect = RuntimeError("no such key")

    with patch.object(adapter, "_get_s3_client", return_value=s3):
        results = adapter._fetch_results_from_s3("s3://bucket/key/missing.json")

    assert results == []


def test_load_data_skips_when_tables_exist(tmp_path) -> None:
    adapter = _adapter()
    adapter._session_id = "sess-1"
    adapter._staging = MagicMock()
    adapter._staging.tables_exist.return_value = True
    adapter._staging.get_table_uri.side_effect = lambda t: f"s3://bucket/{t}"

    stats, _, metadata = adapter.load_data(_benchmark("lineitem"), None, tmp_path)

    assert "lineitem" in stats
    assert metadata is not None
    assert "lineitem" in metadata["table_uris"]
    adapter._staging.upload_tables.assert_not_called()


def test_load_data_uploads_and_creates_tables(tmp_path) -> None:
    adapter = _adapter()
    adapter._session_id = "sess-1"
    adapter._staging = MagicMock()
    adapter._staging.tables_exist.return_value = False

    with patch.object(adapter, "_submit_calculation", return_value=("c1", "COMPLETED")):
        stats, _, metadata = adapter.load_data(_benchmark("lineitem"), None, tmp_path)

    assert "lineitem" in stats
    assert metadata is not None
    assert "lineitem" in metadata["table_uris"]
    adapter._staging.upload_tables.assert_called_once()


def test_load_data_raises_on_missing_dir() -> None:
    from benchbox.core.exceptions import ConfigurationError

    adapter = _adapter()
    with pytest.raises(ConfigurationError, match="Source directory not found"):
        adapter.load_data(_benchmark("lineitem"), None, "/nonexistent/path")


def test_execute_query_returns_results() -> None:
    adapter = _adapter()
    adapter._session_id = "sess-1"

    with (
        patch.object(adapter, "_submit_calculation", return_value=("calc-1", "COMPLETED")),
        patch.object(adapter, "_get_calculation_result", return_value=[{"x": 42}]),
    ):
        result = adapter.execute_query(None, "SELECT 42 as x", "q1")

    assert result["status"] == "SUCCESS"
    assert result["results"] == [{"x": 42}]
    assert adapter._query_count == 1


def test_execute_query_raises_on_failed_state() -> None:
    adapter = _adapter()
    adapter._session_id = "sess-1"

    with patch.object(adapter, "_submit_calculation", return_value=("calc-1", "FAILED")):
        result = adapter.execute_query(None, "SELECT 1", "q1")

    assert result["status"] == "FAILED"
    assert "calculation failed" in result["error"]


def test_close_logs_execution_time() -> None:
    adapter = _adapter()
    adapter._total_execution_time_seconds = 15.0
    adapter.close()


def test_add_cli_arguments_registers_options() -> None:
    from benchbox.platforms.aws import AthenaSparkAdapter

    parser = MagicMock()
    group = MagicMock()
    parser.add_argument_group.return_value = group
    AthenaSparkAdapter.add_cli_arguments(parser)
    assert group.add_argument.call_count >= 5


def test_from_config_creates_adapter() -> None:
    from benchbox.platforms.aws import AthenaSparkAdapter

    config = {"workgroup": "my-wg", "s3_staging_dir": "s3://bucket/path", "region": "us-west-2"}
    adapter = AthenaSparkAdapter.from_config(config)
    assert adapter.workgroup == "my-wg"
    assert adapter.region == "us-west-2"


def test_apply_tuning_configuration_sets_scale_factor() -> None:
    adapter = _adapter()
    config = SimpleNamespace(
        scale_factor=2.5,
        primary_keys=None,
        foreign_keys=None,
        platform_optimizations=None,
    )
    result = adapter.apply_tuning_configuration(config)
    assert adapter._scale_factor == 2.5
    assert result == {}


def test_get_target_dialect_returns_spark() -> None:
    adapter = _adapter()
    assert adapter.get_target_dialect() == "spark"


