# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os

import pytest

from benchbox import TPCH

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_bigquery,
    pytest.mark.skipif(
        not os.getenv("BIGQUERY_PROJECT") or not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        reason="Requires BIGQUERY_PROJECT and GOOGLE_APPLICATION_CREDENTIALS. See .env.example for setup.",
    ),
]


class TestLiveBigQueryConnection:
    def test_bigquery_live_connection(self, live_bigquery_adapter):

        connection = live_bigquery_adapter.create_connection()
        try:
            query_job = connection.query("SELECT 1 as test")
            results = list(query_job.result())
            assert len(results) == 1
            assert results[0][0] == 1

        finally:
            live_bigquery_adapter.close_connection(connection)

    def test_bigquery_live_version_info(self, live_bigquery_adapter):

        connection = live_bigquery_adapter.create_connection()
        try:
            metadata = live_bigquery_adapter.get_platform_info(connection)

            assert metadata["platform_name"] == "BigQuery"
            assert "project_id" in metadata
            assert metadata["connection_type"] in ["bigquery", "serverless"]

        finally:
            live_bigquery_adapter.close_connection(connection)

    def test_bigquery_live_project_access(self, live_bigquery_adapter):

        connection = live_bigquery_adapter.create_connection()
        try:
            project_id = connection.project
            assert project_id is not None
            print(f"Connected to project: {project_id}")

            datasets = list(connection.list_datasets())
            print(f"Found {len(datasets)} datasets in project")

        finally:
            live_bigquery_adapter.close_connection(connection)


class TestLiveBigQueryDatasetManagement:
    def test_bigquery_live_dataset_creation(self, live_bigquery_adapter, unique_test_schema, cleanup_test_schema):
        connection = live_bigquery_adapter.create_connection()
        cleanup_test_schema(live_bigquery_adapter, unique_test_schema)

        try:
            from google.cloud import bigquery

            dataset_id = f"{connection.project}.{unique_test_schema}"
            dataset = bigquery.Dataset(dataset_id)
            dataset.location = "US"
            dataset = connection.create_dataset(dataset, exists_ok=True)

            datasets = list(connection.list_datasets())
            dataset_names = [ds.dataset_id for ds in datasets]
            assert unique_test_schema in dataset_names

        finally:
            live_bigquery_adapter.close_connection(connection)


class TestLiveBigQueryDataLoading:
    def test_bigquery_live_tpch_data_load(
        self, live_bigquery_adapter, unique_test_schema, test_scale_factor, test_output_dir, cleanup_test_schema
    ):
        cleanup_test_schema(live_bigquery_adapter, unique_test_schema)

        tpch = TPCH(scale_factor=test_scale_factor, output_dir=test_output_dir, verbose=False)

        data_files = tpch.generate_data()
        assert len(data_files) > 0, "No data files generated"

        connection = live_bigquery_adapter.create_connection()
        try:
            from google.cloud import bigquery

            dataset_id = f"{connection.project}.{unique_test_schema}"
            dataset = bigquery.Dataset(dataset_id)
            dataset.location = "US"
            connection.create_dataset(dataset, exists_ok=True)

            create_sql = tpch.get_create_tables_sql(dialect="bigquery")
            for statement in create_sql.split(";"):
                if statement.strip():
                    qualified_statement = statement.replace("CREATE TABLE ", f"CREATE TABLE {dataset_id}.")
                    query_job = connection.query(qualified_statement)
                    query_job.result()

            stats, errors, _ = live_bigquery_adapter.load_data(tpch, connection, test_output_dir)

            assert len(stats) > 0, "No tables loaded"
            assert all(count > 0 for count in stats.values()), "Some tables have zero rows"

            query = f"SELECT COUNT(*) FROM `{dataset_id}.LINEITEM`"
            query_job = connection.query(query)
            results = list(query_job.result())
            lineitem_count = results[0][0]
            assert lineitem_count > 0, "LINEITEM table is empty"

        finally:
            live_bigquery_adapter.close_connection(connection)


class TestLiveBigQueryQueryExecution:
    def test_bigquery_live_simple_query(self, live_bigquery_adapter, unique_test_schema):
        connection = live_bigquery_adapter.create_connection()
        try:
            query = "SELECT COUNT(*) as cnt, SUM(1) as total FROM UNNEST([1, 2]) as num"
            query_job = connection.query(query)
            results = list(query_job.result())

            assert len(results) == 1
            assert results[0][0] == 2
            assert results[0][1] == 2

        finally:
            live_bigquery_adapter.close_connection(connection)

    def test_bigquery_live_tpch_query_execution(
        self, live_bigquery_adapter, unique_test_schema, test_scale_factor, test_output_dir
    ):

        tpch = TPCH(scale_factor=test_scale_factor, output_dir=test_output_dir, verbose=False)

        connection = live_bigquery_adapter.create_connection()
        try:
            dataset_id = f"{connection.project}.{unique_test_schema}"
            try:
                query = f"SELECT COUNT(*) FROM `{dataset_id}.LINEITEM`"
                query_job = connection.query(query)
                results = list(query_job.result())
                count = results[0][0]
                if count == 0:
                    pytest.skip("No data loaded - run data load test first")
            except Exception:
                pytest.skip("Dataset not found - run dataset creation test first")

            query1 = tpch.get_query(1, seed=42)

            query1_bigquery = query1.replace("LINEITEM", f"`{dataset_id}.LINEITEM`")

            query_job = connection.query(query1_bigquery)
            results = list(query_job.result())

            assert len(results) > 0, "Query 1 returned no results"
            print(f"Query 1 returned {len(results)} rows")

        finally:
            live_bigquery_adapter.close_connection(connection)


class TestLiveBigQuerySpecificFeatures:
    def test_bigquery_live_gcs_load(
        self, live_bigquery_adapter, unique_test_schema, test_output_dir, cleanup_test_schema
    ):

        cleanup_test_schema(live_bigquery_adapter, unique_test_schema)

        connection = live_bigquery_adapter.create_connection()
        try:
            from google.cloud import bigquery

            dataset_id = f"{connection.project}.{unique_test_schema}"
            dataset = bigquery.Dataset(dataset_id)
            dataset.location = "US"
            connection.create_dataset(dataset, exists_ok=True)

            table_id = f"{dataset_id}.test_copy"
            schema = [
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("value", "STRING"),
            ]
            table = bigquery.Table(table_id, schema=schema)
            connection.create_table(table)

            test_file = test_output_dir / "test_data.csv"
            test_file.write_text("1,test1\n2,test2\n3,test3\n")

            query = f"SELECT COUNT(*) FROM `{table_id}`"
            query_job = connection.query(query)
            results = list(query_job.result())
            initial_count = results[0][0]
            assert initial_count == 0

        finally:
            live_bigquery_adapter.close_connection(connection)

    def test_bigquery_live_query_cost(self, live_bigquery_adapter):

        connection = live_bigquery_adapter.create_connection()
        try:
            from google.cloud import bigquery

            job_config = bigquery.QueryJobConfig(dry_run=True)
            query = "SELECT COUNT(*) FROM UNNEST(GENERATE_ARRAY(1, 1000)) as num"

            query_job = connection.query(query, job_config=job_config)

            assert query_job.total_bytes_processed is not None
            assert query_job.total_bytes_processed >= 0
            print(f"Query would process {query_job.total_bytes_processed} bytes")

        finally:
            live_bigquery_adapter.close_connection(connection)

    def test_bigquery_live_cleanup(self, live_bigquery_adapter, unique_test_schema):

        connection = live_bigquery_adapter.create_connection()
        try:
            dataset_id = f"{connection.project}.{unique_test_schema}"
            connection.delete_dataset(dataset_id, delete_contents=True, not_found_ok=True)

            datasets = list(connection.list_datasets())
            dataset_names = [ds.dataset_id for ds in datasets]
            assert unique_test_schema not in dataset_names

        finally:
            live_bigquery_adapter.close_connection(connection)


class TestLiveBigQueryQueryPlanCapture:
    @staticmethod
    def _capture_adapter(bigquery_credentials):
        from benchbox.platforms.bigquery import BigQueryAdapter

        return BigQueryAdapter(**{**bigquery_credentials, "capture_plans": True})

    @staticmethod
    def _connect(adapter):
        connection = adapter.create_connection()
        adapter.configure_for_benchmark(connection, "olap")
        return connection

    def test_execute_query_with_capture_returns_plan(self, bigquery_credentials):
        adapter = self._capture_adapter(bigquery_credentials)
        connection = self._connect(adapter)
        try:
            result = adapter.execute_query(connection, "SELECT 1", query_id="Q0", validate_row_count=False)
            assert result["status"] == "SUCCESS"
            assert result.get("query_plan") is not None
            fingerprint = result.get("plan_fingerprint")
            assert fingerprint is not None
            assert len(fingerprint) == 64
            job_stats = result.get("job_statistics") or {}
            assert job_stats.get("bytes_processed") == 0
        finally:
            adapter.close_connection(connection)

    def test_plan_fingerprint_stable(self, bigquery_credentials):
        adapter = self._capture_adapter(bigquery_credentials)
        connection = self._connect(adapter)
        try:
            first = adapter.execute_query(connection, "SELECT 1", query_id="Q0", validate_row_count=False)
            second = adapter.execute_query(connection, "SELECT 1", query_id="Q0", validate_row_count=False)
            assert first["status"] == "SUCCESS"
            assert second["status"] == "SUCCESS"
            first_fingerprint = first.get("plan_fingerprint")
            second_fingerprint = second.get("plan_fingerprint")
            assert first_fingerprint is not None
            assert second_fingerprint is not None
            assert first_fingerprint == second_fingerprint
        finally:
            adapter.close_connection(connection)


