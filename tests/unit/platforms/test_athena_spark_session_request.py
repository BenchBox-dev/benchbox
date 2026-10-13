from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber
from click.testing import CliRunner

from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager
from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms import get_platform_adapter
from benchbox.platforms.aws import athena_spark_adapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

RUNTIME_VERSION = "PySpark engine version 3"


@pytest.fixture
def athena_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, Stubber]]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    client = boto3.client("athena", region_name="us-east-1")
    monkeypatch.setattr(athena_spark_adapter.boto3, "client", lambda service, region_name=None: client)
    with Stubber(client) as stubber:
        yield client, stubber


def _cli_dry_run_platform_config(tmp_path: Path, *platform_options: str) -> dict[str, Any]:
    preview_dir = tmp_path / "preview"
    args = ["--platform", "athena-spark", "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
    for option in platform_options:
        args.extend(["--platform-option", option])
    result = CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})
    assert result.exit_code == 0, result.output
    [summary] = preview_dir.glob("tpch_athena-spark_*.json")
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["warnings"] == []
    return payload["platform_config"]


def _expect_session_start(stubber: Stubber, workgroup: str, engine_configuration: dict[str, int]) -> None:
    stubber.add_response(
        "start_session",
        {"SessionId": "s-1", "State": "CREATING"},
        {
            "WorkGroup": workgroup,
            "EngineConfiguration": engine_configuration,
            "SessionIdleTimeoutInMinutes": 15,
        },
    )
    stubber.add_response("get_session_status", {"SessionId": "s-1", "Status": {"State": "IDLE"}}, {"SessionId": "s-1"})


def test_documented_cli_options_start_a_session_the_athena_api_accepts(tmp_path: Path, athena_client) -> None:
    _, stubber = athena_client
    platform_config = _cli_dry_run_platform_config(
        tmp_path,
        "compute_resource=spark-wg",
        "s3_staging_dir=s3://bucket/benchbox",
        f"runtime_version={RUNTIME_VERSION}",
        "max_concurrent_dpus=40",
    )
    adapter = get_platform_adapter("athena-spark", **platform_config)
    _expect_session_start(
        stubber, "spark-wg", {"CoordinatorDpuSize": 1, "MaxConcurrentDpus": 40, "DefaultExecutorDpuSize": 1}
    )

    connection = adapter.create_connection()

    stubber.assert_no_pending_responses()
    assert connection["session_id"] == "s-1"
    info = adapter.get_platform_info()
    assert info["runtime_version"] == RUNTIME_VERSION
    assert info["runtime_version_source"] == "requested"
    assert "engine_version" not in info


def test_deprecated_engine_version_option_is_recorded_and_never_sent(tmp_path: Path, athena_client) -> None:
    _, stubber = athena_client
    platform_config = _cli_dry_run_platform_config(
        tmp_path,
        "compute_resource=spark-wg",
        "s3_staging_dir=s3://bucket/benchbox",
        f"engine_version={RUNTIME_VERSION}",
    )
    assert platform_config["runtime_version"] == RUNTIME_VERSION
    adapter = get_platform_adapter("athena-spark", **platform_config)
    _expect_session_start(
        stubber, "spark-wg", {"CoordinatorDpuSize": 1, "MaxConcurrentDpus": 20, "DefaultExecutorDpuSize": 1}
    )

    adapter.create_connection()

    stubber.assert_no_pending_responses()
    assert adapter.get_platform_info()["runtime_version"] == RUNTIME_VERSION


def test_unrequested_runtime_version_is_not_recorded(athena_client) -> None:
    adapter = get_platform_adapter("athena-spark", compute_resource="spark-wg", s3_staging_dir="s3://bucket/benchbox")

    info = adapter.get_platform_info()

    assert info["runtime_version"] is None
    assert info["runtime_version_source"] is None


def test_rejected_session_request_is_a_configuration_error(athena_client) -> None:
    _, stubber = athena_client
    adapter = get_platform_adapter(
        "athena-spark",
        compute_resource="spark-wg",
        s3_staging_dir="s3://bucket/benchbox",
        coordinator_dpu_size="large",
    )
    stubber.add_response("start_session", {"SessionId": "s-1", "State": "CREATING"})

    with pytest.raises(ConfigurationError, match="Invalid Athena Spark session request for workgroup 'spark-wg'"):
        adapter.create_connection()

    assert adapter._session_id is None


def test_conflicting_runtime_version_spellings_fail(athena_client) -> None:
    with pytest.raises(ConfigurationError, match="Conflicting Athena Spark runtime versions"):
        get_platform_adapter(
            "athena-spark",
            compute_resource="spark-wg",
            s3_staging_dir="s3://bucket/benchbox",
            runtime_version="PySpark engine version 3",
            engine_version="PySpark engine version 2",
        )
