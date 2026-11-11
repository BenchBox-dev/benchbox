# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def adapter():
    with (
        patch("benchbox.platforms.aws.emr_serverless_adapter.BOTO3_AVAILABLE", True),
        patch("benchbox.platforms.aws.emr_serverless_adapter.boto3", MagicMock()),
        patch("benchbox.platforms.aws.emr_serverless_adapter.CloudSparkStaging") as mock_staging,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        from benchbox.platforms.aws import EMRServerlessAdapter

        return EMRServerlessAdapter(
            application_id="app-123",
            s3_staging_dir="s3://my-bucket/benchbox-data",
            execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
        )


def _benchmark(*tables: str) -> SimpleNamespace:
    return SimpleNamespace(tables=list(tables))


class TestCapability:
    def test_supports_external_tables(self, adapter):
        assert adapter.supports_external_tables is True

    def test_requires_s3_staging_dir(self, adapter):
        adapter.s3_staging_dir = None
        with pytest.raises(ValueError, match="staging location"):
            adapter.validate_external_table_requirements()


class TestRegisterExternalTable:
    def test_submits_ddl_and_waits(self, adapter):
        with (
            patch.object(adapter, "_ensure_application_started") as ensure,
            patch.object(adapter, "_submit_job_run", return_value="job-1") as submit,
            patch.object(adapter, "_wait_for_job_run", return_value=("SUCCESS", {})) as wait,
        ):
            adapter._register_external_table("lineitem", "s3://my-bucket/benchbox-data/lineitem/", "parquet")

        sql = submit.call_args[0][0]
        assert "CREATE OR REPLACE TABLE benchbox.lineitem" in sql
        assert "USING PARQUET" in sql
        assert "LOCATION 's3://my-bucket/benchbox-data/lineitem/'" in sql
        ensure.assert_called_once_with()
        wait.assert_called_once_with("job-1")

    def test_wait_failure_propagates(self, adapter):
        with (
            patch.object(adapter, "_ensure_application_started"),
            patch.object(adapter, "_submit_job_run", return_value="job-1"),
            patch.object(adapter, "_wait_for_job_run", side_effect=RuntimeError("FAILED")),
        ):
            with pytest.raises(RuntimeError, match="FAILED"):
                adapter._register_external_table("lineitem", "s3://x/lineitem/", "parquet")


class TestCreateExternalTables:
    def test_end_to_end_with_counts(self, adapter, tmp_path):
        staging = MagicMock()
        staging.tables_exist.return_value = False
        staging.upload_tables.return_value = {}
        staging.get_table_uri.side_effect = lambda table: f"s3://my-bucket/benchbox-data/{table}/"
        adapter._staging = staging

        def _execute(connection, query, query_id, **kwargs):
            assert "FROM benchbox.orders" in query
            return {"status": "SUCCESS", "results": [{"row_count": 1500}]}

        with (
            patch.object(adapter, "_ensure_application_started"),
            patch.object(adapter, "_submit_job_run", return_value="job-1"),
            patch.object(adapter, "_wait_for_job_run", return_value=("SUCCESS", {})),
            patch.object(adapter, "create_schema", return_value=0.0),
            patch.object(adapter, "execute_query", side_effect=_execute),
        ):
            stats, elapsed, meta = adapter.create_external_tables(_benchmark("orders"), None, tmp_path)

        assert stats == {"orders": 1500}
        assert elapsed >= 0.0
        assert meta == {"table_uris": {"orders": "s3://my-bucket/benchbox-data/orders/"}}


