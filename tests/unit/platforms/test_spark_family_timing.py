from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.results.fetch_mode import ConversionClock
from benchbox.core.results.timer_scope import TIMING_METHOD_QUERY_ONLY, RequestLedger
from benchbox.platforms.azure._livy_mixin import LivyStatementMixin
from benchbox.platforms.azure.spark_execution_utils import execute_livy_statement
from benchbox.platforms.base.spark_execution_mixin import SparkQueryExecutionMixin
from benchbox.platforms.base.timing_contract import TimingContractMixin

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _Host(TimingContractMixin, SparkQueryExecutionMixin):
    dry_run_mode = False
    disable_cache = False
    fetch_mode = "rows"

    def __init__(self) -> None:
        self.built: dict[str, Any] = {}
        self.events: list[str] = []

    def log_verbose(self, message: str) -> None:
        return None

    def log_very_verbose(self, message: str) -> None:
        return None

    def _build_query_result_with_validation(self, **kwargs: Any) -> dict[str, Any]:
        self.built = kwargs
        return {"status": "SUCCESS", **kwargs}

    def _merge_plan_capture_into_result(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _build_query_failure_result(self, query_id: str, start_time: float, exception: Exception) -> dict[str, Any]:
        raise exception


def _session(rows: list[tuple[Any, ...]]) -> MagicMock:
    spark = MagicMock()
    spark.sql.return_value.collect.return_value = rows
    return spark


def test_spark_mixin_records_one_request_and_result_bytes() -> None:
    host = _Host()
    spark = _session([(1, "a"), (2, "b")])

    host._execute_query_spark(spark, "select 1", "Q1", validate_row_count=False)

    assert host.built["timing_fields"]["request_count"] == 1
    assert host.built["timing_fields"]["result_bytes"] > 0
    spark.sql.assert_called_once_with("select 1")


def test_spark_mixin_keeps_cache_clear_and_validation_outside_the_timer() -> None:
    host = _Host()
    host.disable_cache = True
    spark = _session([(1,)])
    clock_depths: list[int] = []

    from benchbox.core.results import fetch_mode

    def record_depth() -> None:
        clock_depths.append(fetch_mode.EXCLUSION_DEPTH.get())

    spark.catalog.clearCache.side_effect = record_depth

    clock = ConversionClock().start()
    try:
        host._execute_query_spark(spark, "select 1", "Q1", validate_row_count=False)
    finally:
        clock.finish()

    assert clock_depths == [1]
    assert clock.seconds > 0


def test_spark_timing_method_declared_for_local_spark_family() -> None:
    from benchbox.platforms.lakesail import LakeSailAdapter
    from benchbox.platforms.spark import SparkAdapter
    from benchbox.platforms.velox import VeloxAdapter

    assert SparkAdapter.timing_method == TIMING_METHOD_QUERY_ONLY
    assert LakeSailAdapter.timing_method == TIMING_METHOD_QUERY_ONLY
    assert VeloxAdapter.timing_method == TIMING_METHOD_QUERY_ONLY


class _Livy(LivyStatementMixin):
    livy_endpoint = "https://livy.example.com"
    timeout_minutes = 5

    def __init__(self) -> None:
        self._total_statement_time_seconds = 0.0
        self._query_count = 0
        self.session_calls = 0

    def _get_headers(self) -> dict[str, str]:
        return {}

    def _ensure_session(self) -> int:
        self.session_calls += 1
        return 7


def test_livy_measured_statement_records_statement_id() -> None:
    adapter = _Livy()
    ledger = RequestLedger()

    def fake_execute(**kwargs: Any) -> tuple[dict[str, Any], float]:
        kwargs["on_submit"](11)
        return {"data": {"values": []}}, 0.5

    with patch("benchbox.platforms.azure.spark_execution_utils.execute_livy_statement", side_effect=fake_execute):
        result, elapsed = adapter._execute_statement_measured("SELECT 1", ledger)

    assert elapsed == 0.5
    assert result == {"data": {"values": []}}
    assert ledger.count == 1
    assert ledger.statement_ids() == ["11"]
    assert adapter._query_count == 1
    assert adapter.session_calls == 1


def test_execute_livy_statement_reports_submitted_id() -> None:
    post_resp = MagicMock(status_code=201)
    post_resp.json.return_value = {"id": 42}
    seen: list[int] = []

    with patch("benchbox.platforms.azure.spark_execution_utils.wait_for_livy_statement", return_value={}):
        execute_livy_statement(
            "https://api",
            1,
            "SELECT 1",
            "sql",
            dict,
            1.0,
            on_submit=seen.append,
            post=lambda *args, **kwargs: post_resp,
        )

    assert seen == [42]


def _glue_adapter() -> Any:
    from benchbox.platforms.aws.glue_adapter import AWSGlueAdapter

    with (
        patch("benchbox.platforms.aws.glue_adapter.BOTO3_AVAILABLE", True),
        patch("benchbox.platforms.aws.glue_adapter.boto3", MagicMock()),
        patch("benchbox.platforms.aws.glue_adapter.CloudSparkStaging") as mock_staging,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        return AWSGlueAdapter(
            s3_staging_dir="s3://my-bucket/benchbox-data",
            job_role="arn:aws:iam::123456789012:role/GlueRole",
        )


def test_glue_execute_query_reports_request_and_statement_id() -> None:
    adapter = _glue_adapter()
    assert adapter.timing_method == TIMING_METHOD_QUERY_ONLY

    with (
        patch.object(adapter, "_ensure_job_exists", return_value="job"),
        patch.object(adapter, "_submit_job_run", return_value="jr-1"),
        patch.object(adapter, "_wait_for_job", return_value="SUCCEEDED"),
        patch.object(adapter, "_retrieve_results", return_value=[{"c": 1}]),
    ):
        result = adapter.execute_query(None, "SELECT 1", "q1")

    assert result["status"] == "SUCCESS"
    assert result["request_count"] == 1
    assert result["result_bytes"] > 0


@pytest.mark.parametrize(
    "module,cls",
    [
        ("benchbox.platforms.aws.athena_spark_adapter", "AthenaSparkAdapter"),
        ("benchbox.platforms.aws.emr_serverless_adapter", "EMRServerlessAdapter"),
        ("benchbox.platforms.aws.glue_adapter", "AWSGlueAdapter"),
        ("benchbox.platforms.azure.fabric_spark_adapter", "FabricSparkAdapter"),
        ("benchbox.platforms.azure.synapse_spark_adapter", "SynapseSparkAdapter"),
        ("benchbox.platforms.gcp.dataproc_adapter", "DataprocAdapter"),
        ("benchbox.platforms.gcp.dataproc_serverless_adapter", "DataprocServerlessAdapter"),
        ("benchbox.platforms.onehouse.quanton_adapter", "QuantonAdapter"),
    ],
)
def test_cloud_spark_adapters_declare_query_only_timing(module: str, cls: str) -> None:
    import importlib

    adapter_class = getattr(importlib.import_module(module), cls)

    assert adapter_class.timing_method == TIMING_METHOD_QUERY_ONLY
    assert adapter_class.platform_timing_source is None
