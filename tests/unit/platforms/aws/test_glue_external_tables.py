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
        patch("benchbox.platforms.aws.glue_adapter.BOTO3_AVAILABLE", True),
        patch("benchbox.platforms.aws.glue_adapter.boto3", MagicMock()),
        patch("benchbox.platforms.aws.glue_adapter.CloudSparkStaging") as mock_staging,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        from benchbox.platforms.aws import AWSGlueAdapter

        return AWSGlueAdapter(
            s3_staging_dir="s3://my-bucket/benchbox-data",
            job_role="arn:aws:iam::123456789012:role/GlueRole",
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
    def test_reuses_catalog_table_creation(self, adapter):
        with patch.object(adapter, "_create_catalog_table") as create:
            adapter._register_external_table("lineitem", "s3://my-bucket/benchbox-data/lineitem/", "parquet")

        create.assert_called_once_with("lineitem", "parquet", "s3://my-bucket/benchbox-data/lineitem/", replace=True)

    def test_replace_deletes_before_create(self, adapter):
        ClientError = pytest.importorskip("botocore.exceptions").ClientError

        mock_client = MagicMock()
        mock_client.delete_table.side_effect = ClientError(
            {"Error": {"Code": "EntityNotFoundException"}}, "DeleteTable"
        )
        with patch.object(adapter, "_get_glue_client", return_value=mock_client):
            adapter._create_catalog_table("lineitem", "parquet", "s3://b/lineitem/", replace=True)

        mock_client.delete_table.assert_called_once_with(DatabaseName=adapter.database, Name="lineitem")
        mock_client.create_table.assert_called_once()

    def test_replace_surfaces_unexpected_delete_errors(self, adapter):
        ClientError = pytest.importorskip("botocore.exceptions").ClientError

        mock_client = MagicMock()
        mock_client.delete_table.side_effect = ClientError({"Error": {"Code": "AccessDeniedException"}}, "DeleteTable")
        with patch.object(adapter, "_get_glue_client", return_value=mock_client):
            with pytest.raises(ClientError):
                adapter._create_catalog_table("lineitem", "parquet", "s3://b/lineitem/", replace=True)
        mock_client.create_table.assert_not_called()


class TestJobScript:
    def test_script_resolves_job_run_id(self, adapter):

        mock_s3 = MagicMock()
        with patch.object(adapter, "_get_s3_client", return_value=mock_s3):
            adapter._upload_job_script()

        body = mock_s3.put_object.call_args[1]["Body"].decode("utf-8")
        assert "'JOB_RUN_ID'" in body
        assert "args['JOB_RUN_ID']" in body


class TestCreateExternalTables:
    def test_end_to_end_with_counts(self, adapter, tmp_path):
        staging = MagicMock()
        staging.tables_exist.return_value = False
        staging.upload_tables.return_value = {"lineitem": "s3://my-bucket/benchbox-data/lineitem/"}
        adapter._staging = staging

        def _execute(connection, query, query_id, **kwargs):
            return {"status": "SUCCESS", "results": [{"row_count": 6000}]}

        with (
            patch.object(adapter, "_create_catalog_table") as create,
            patch.object(adapter, "create_schema", return_value=0.0),
            patch.object(adapter, "execute_query", side_effect=_execute),
        ):
            stats, elapsed, meta = adapter.create_external_tables(_benchmark("lineitem"), None, tmp_path)

        assert stats == {"lineitem": 6000}
        assert elapsed >= 0.0
        create.assert_called_once_with("lineitem", "parquet", "s3://my-bucket/benchbox-data/lineitem/", replace=True)
        assert meta == {"table_uris": {"lineitem": "s3://my-bucket/benchbox-data/lineitem/"}}


