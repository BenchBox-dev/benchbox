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
        patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
        patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
        patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging") as mock_staging,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        from benchbox.platforms.aws import AthenaSparkAdapter

        return AthenaSparkAdapter(
            workgroup="spark-workgroup",
            s3_staging_dir="s3://my-bucket/benchbox-data",
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
    def test_submits_create_external_table_calculation(self, adapter):
        with patch.object(adapter, "_submit_calculation", return_value=("calc-1", "COMPLETED")) as submit:
            adapter._register_external_table("lineitem", "s3://my-bucket/benchbox-data/lineitem/", "parquet")

        sql = submit.call_args[0][0]
        assert "CREATE OR REPLACE TABLE benchbox.lineitem" in sql
        assert "USING PARQUET" in sql
        assert "LOCATION 's3://my-bucket/benchbox-data/lineitem/'" in sql
        assert submit.call_args[1].get("code_type") == "SQL"
        assert submit.call_args[1].get("wait_for_completion") is True

    def test_failed_calculation_raises(self, adapter):
        with patch.object(adapter, "_submit_calculation", return_value=("calc-9", "FAILED")):
            with pytest.raises(RuntimeError, match="calc-9"):
                adapter._register_external_table("lineitem", "s3://my-bucket/benchbox-data/lineitem/", "parquet")


class TestCreateExternalTables:
    def test_end_to_end_with_counts(self, adapter, tmp_path):
        staging = MagicMock()
        staging.tables_exist.return_value = False
        staging.upload_tables.return_value = {}
        staging.get_table_uri.side_effect = lambda table: f"s3://my-bucket/benchbox-data/{table}/"
        adapter._staging = staging
        counts = {"lineitem": 6000}

        def _execute(connection, query, query_id, **kwargs):
            table = query_id.replace("external-count-", "")
            return {"status": "SUCCESS", "results": [{"row_count": counts[table]}]}

        with (
            patch.object(adapter, "_submit_calculation", return_value=("calc-1", "COMPLETED")) as submit,
            patch.object(adapter, "create_schema", return_value=0.0) as schema,
            patch.object(adapter, "execute_query", side_effect=_execute),
        ):
            stats, elapsed, meta = adapter.create_external_tables(_benchmark("lineitem"), None, tmp_path)

        assert stats == counts
        assert elapsed >= 0.0
        schema.assert_called_once()
        submit.assert_called_once()
        assert meta == {"table_uris": {"lineitem": "s3://my-bucket/benchbox-data/lineitem/"}}


