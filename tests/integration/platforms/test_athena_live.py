# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os

import pytest

from .conftest import get_env_or_skip

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_athena,
    pytest.mark.skipif(
        not os.getenv("ATHENA_REGION") or not os.getenv("ATHENA_S3_OUTPUT"),
        reason="Requires ATHENA_REGION and ATHENA_S3_OUTPUT environment variables",
    ),
]


@pytest.fixture
def athena_adapter():

    from benchbox.platforms.athena import AthenaAdapter

    region = get_env_or_skip("ATHENA_REGION", "Athena")
    s3_output = get_env_or_skip("ATHENA_S3_OUTPUT", "Athena")
    workgroup = os.getenv("ATHENA_WORKGROUP", "primary")
    database = os.getenv("ATHENA_DATABASE", "default")

    adapter = AthenaAdapter(
        region=region,
        s3_output_location=s3_output,
        workgroup=workgroup,
        database=database,
    )
    yield adapter


class TestLiveAthenaConnection:
    def test_connection(self, athena_adapter):

        connection = athena_adapter.create_connection()
        try:
            result = athena_adapter.execute_query(connection, "SELECT 1 AS value", query_id="Q0", benchmark_type="tpch")
            assert result["status"] == "SUCCESS"
        finally:
            athena_adapter.close_connection(connection)

    def test_platform_info(self, athena_adapter):

        connection = athena_adapter.create_connection()
        try:
            info = athena_adapter.get_platform_info(connection)
            assert info["platform_type"] == "athena"
        finally:
            athena_adapter.close_connection(connection)


class TestLiveAthenaQueryExecution:
    def test_show_databases(self, athena_adapter):

        connection = athena_adapter.create_connection()
        try:
            result = athena_adapter.execute_query(
                connection,
                "SHOW DATABASES",
                query_id="Q_databases",
                benchmark_type="tpch",
            )
            assert result["status"] == "SUCCESS"
            assert result["rows_returned"] >= 1
        finally:
            athena_adapter.close_connection(connection)

    def test_query_with_cost_tracking(self, athena_adapter):

        connection = athena_adapter.create_connection()
        try:
            result = athena_adapter.execute_query(
                connection,
                "SELECT current_database, current_timestamp",
                query_id="Q_cost",
                benchmark_type="tpch",
            )
            assert result["status"] == "SUCCESS"
        finally:
            athena_adapter.close_connection(connection)


