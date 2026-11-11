# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import contextlib
import uuid
from pathlib import Path
from typing import Any

import pytest

from benchbox.platforms.redshift import RedshiftAdapter
from benchbox.utils.cloud_storage import get_cloud_path_info

from .conftest import has_redshift_credentials

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_redshift,
    pytest.mark.skipif(
        not has_redshift_credentials(),
        reason="Requires Redshift credentials in ~/.benchbox/credentials.yaml or REDSHIFT_HOST env var",
    ),
]


class _RedshiftStagedCopyBenchmark:
    def __init__(self, table_files: list[Path]) -> None:
        self.tables = {"test_copy": table_files}

    def get_create_tables_sql(self, dialect: str = "standard", tuning_config=None) -> str:
        del dialect, tuning_config
        return """
            CREATE TABLE test_copy (
                id INTEGER,
                value VARCHAR(32)
            );
        """


def _build_redshift_staging_config(
    base_credentials: dict[str, Any], schema_name: str
) -> tuple[dict[str, Any], str, str]:

    staging_root = base_credentials.get("staging_root")
    if staging_root:
        path_info = get_cloud_path_info(staging_root)
        bucket = path_info["bucket"]
        base_prefix = path_info.get("path", "").strip("/")
    else:
        bucket = str(base_credentials["s3_bucket"])
        base_prefix = str(base_credentials.get("s3_prefix") or "benchbox-data").strip("/")

    unique_prefix = "/".join(part for part in [base_prefix, "live-redshift", schema_name, uuid.uuid4().hex] if part)

    adapter_config = dict(base_credentials)
    adapter_config.pop("staging_root", None)
    adapter_config["schema"] = schema_name
    adapter_config["s3_bucket"] = bucket
    adapter_config["s3_prefix"] = unique_prefix
    return adapter_config, bucket, unique_prefix


def _write_copy_test_chunks(output_dir: Path) -> list[Path]:

    output_dir.mkdir(parents=True, exist_ok=True)

    chunk_one = output_dir / "test_copy_part1.csv"
    chunk_two = output_dir / "test_copy_part2.csv"
    chunk_one.write_text("1,alpha\n2,beta\n")
    chunk_two.write_text("3,gamma\n4,delta\n")
    return [chunk_one, chunk_two]


def _delete_s3_prefix(s3_client: Any, bucket: str, prefix: str) -> None:

    continuation_token = None
    while True:
        request = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            request["ContinuationToken"] = continuation_token

        response = s3_client.list_objects_v2(**request)
        contents = response.get("Contents", [])
        if contents:
            s3_client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in contents], "Quiet": True},
            )

        if not response.get("IsTruncated"):
            return
        continuation_token = response["NextContinuationToken"]


class TestLiveRedshiftConnection:
    def test_connection(self, live_redshift_adapter):

        connection = live_redshift_adapter.create_connection()
        try:
            result = live_redshift_adapter.execute_query(
                connection, "SELECT 1 AS value", query_id="Q0", benchmark_type="tpch"
            )
            assert result["status"] == "SUCCESS"
        finally:
            live_redshift_adapter.close_connection(connection)

    def test_platform_info(self, live_redshift_adapter):

        connection = live_redshift_adapter.create_connection()
        try:
            info = live_redshift_adapter.get_platform_info(connection)
            assert info["platform_type"] == "redshift"
        finally:
            live_redshift_adapter.close_connection(connection)


class TestLiveRedshiftQueryExecution:
    def test_create_schema(self, live_redshift_adapter):

        connection = live_redshift_adapter.create_connection()
        try:
            live_redshift_adapter.execute_query(
                connection,
                "CREATE SCHEMA IF NOT EXISTS benchbox_live_test",
                query_id="schema_create",
                benchmark_type="tpch",
            )
            live_redshift_adapter.execute_query(
                connection,
                "DROP SCHEMA IF EXISTS benchbox_live_test CASCADE",
                query_id="schema_drop",
                benchmark_type="tpch",
            )
        finally:
            live_redshift_adapter.close_connection(connection)

    def test_execute_analytical_query(self, live_redshift_adapter):

        connection = live_redshift_adapter.create_connection()
        try:
            result = live_redshift_adapter.execute_query(
                connection,
                "SELECT current_database(), current_schema(), current_user",
                query_id="Q_info",
                benchmark_type="tpch",
            )
            assert result["status"] == "SUCCESS"
            assert result["rows_returned"] >= 1
        finally:
            live_redshift_adapter.close_connection(connection)


class TestLiveRedshiftDataLoading:
    def test_s3_upload_and_copy_load(
        self,
        redshift_staging_credentials,
        unique_test_schema,
        test_output_dir,
        cleanup_test_schema,
    ):

        adapter_config, bucket, prefix = _build_redshift_staging_config(
            redshift_staging_credentials, unique_test_schema
        )
        adapter = RedshiftAdapter(**adapter_config)
        cleanup_test_schema(adapter, unique_test_schema)

        benchmark = _RedshiftStagedCopyBenchmark(_write_copy_test_chunks(test_output_dir / unique_test_schema))

        try:
            s3_client = adapter._create_s3_client()
        except ValueError as exc:
            if "AWS credentials not found" in str(exc):
                pytest.skip(f"Skipping Redshift live staging test: {exc}")
            raise

        expected_keys = [
            f"{prefix}/test_copy_0.csv",
            f"{prefix}/test_copy_1.csv",
            f"{prefix}/test_copy_manifest.json",
        ]

        connection = adapter.create_connection()
        try:
            adapter.create_schema(benchmark, connection)
            table_stats, _, _ = adapter.load_data(benchmark, connection, test_output_dir / unique_test_schema)

            assert table_stats["test_copy"] == 4

            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"""
                    SELECT COUNT(*), MIN(id), MAX(id), COUNT(DISTINCT value)
                    FROM {unique_test_schema}.test_copy
                    """
                )
                row_count, min_id, max_id, distinct_values = cursor.fetchone()
            finally:
                cursor.close()

            assert (row_count, min_id, max_id, distinct_values) == (4, 1, 4, 4)

            for key in expected_keys:
                metadata = s3_client.head_object(Bucket=bucket, Key=key)
                assert metadata["ContentLength"] > 0

        finally:
            adapter.close_connection(connection)
            with contextlib.suppress(Exception):
                _delete_s3_prefix(s3_client, bucket, prefix)


@pytest.fixture(scope="module")
def capture_adapter(redshift_credentials):
    from benchbox.platforms.redshift import RedshiftAdapter

    return RedshiftAdapter(capture_plans=True, **redshift_credentials)


class TestLiveRedshiftQueryPlanCapture:
    def test_get_query_plan_returns_text(self, capture_adapter):
        connection = capture_adapter.create_connection()
        try:
            plan = capture_adapter.get_query_plan(connection, "SELECT 1")
            assert plan is not None
            assert isinstance(plan, str)
            assert len(plan.strip()) > 0
        finally:
            capture_adapter.close_connection(connection)

    def test_capture_query_plan_returns_dag(self, capture_adapter):
        connection = capture_adapter.create_connection()
        try:
            plan, capture_ms = capture_adapter.capture_query_plan(connection, "SELECT 1", "q_test")
            assert plan is not None, "Expected a QueryPlanDAG but got None"
            assert capture_ms >= 0.0
            assert plan.logical_root is not None
        finally:
            capture_adapter.close_connection(connection)

    def test_capture_query_plan_has_fingerprint(self, capture_adapter):
        connection = capture_adapter.create_connection()
        try:
            plan, _ = capture_adapter.capture_query_plan(connection, "SELECT 1", "q_fp")
            assert plan is not None
            assert plan.plan_fingerprint
            assert len(plan.plan_fingerprint) == 64
        finally:
            capture_adapter.close_connection(connection)

    def test_capture_query_plan_fingerprint_stable(self, capture_adapter):
        connection = capture_adapter.create_connection()
        try:
            plan1, _ = capture_adapter.capture_query_plan(connection, "SELECT 1", "q_stable")
            plan2, _ = capture_adapter.capture_query_plan(connection, "SELECT 1", "q_stable")
            assert plan1 is not None and plan2 is not None
            assert plan1.plan_fingerprint == plan2.plan_fingerprint
        finally:
            capture_adapter.close_connection(connection)


