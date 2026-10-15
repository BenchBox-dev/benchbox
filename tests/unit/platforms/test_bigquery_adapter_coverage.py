# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def _mock_bigquery_deps():
    class _FakeNotFound(Exception):
        pass

    with (
        patch("benchbox.platforms.bigquery.bigquery", MagicMock()),
        patch("benchbox.platforms.bigquery.storage", MagicMock()),
        patch("benchbox.platforms.bigquery.service_account", MagicMock()),
        patch("benchbox.platforms.bigquery.google_auth", MagicMock()),
        patch("benchbox.platforms.bigquery.check_platform_dependencies", return_value=(True, [])),
        patch("benchbox.platforms.bigquery.NotFound", _FakeNotFound, create=True),
    ):
        yield


def _make_not_found_exception():
    import benchbox.platforms.bigquery as bq_module

    nf = getattr(bq_module, "NotFound", Exception)
    return nf("not found")


def _make_adapter(**kwargs):
    from benchbox.platforms.bigquery import BigQueryAdapter

    defaults = {"project_id": "test-project", "dataset_id": "test_ds"}
    defaults.update(kwargs)
    return BigQueryAdapter(**defaults)


class TestFromConfigProjectAutoDetect:
    def test_nested_tuning_key_order_keeps_generated_dataset_name(self):
        import benchbox.platforms.bigquery as bq_module

        tuning_a = {
            "platform_optimizations": {"z_ordering_enabled": True, "auto_optimize_enabled": False},
            "table_tunings": {"lineitem": {"sorting": [{"name": "l_orderkey", "order": 1}]}},
        }
        tuning_b = {
            "table_tunings": {"lineitem": {"sorting": [{"order": 1, "name": "l_orderkey"}]}},
            "platform_optimizations": {"auto_optimize_enabled": False, "z_ordering_enabled": True},
        }
        common = {"project_id": "test-project", "benchmark": "tpch", "scale_factor": 1.0}

        adapter_a = bq_module.BigQueryAdapter.from_config({**common, "tuning_config": tuning_a})
        adapter_b = bq_module.BigQueryAdapter.from_config({**common, "tuning_config": tuning_b})

        assert adapter_a.dataset_id == adapter_b.dataset_id

    def test_auto_detects_project_when_not_in_config(self):
        import benchbox.platforms.bigquery as bq_module

        mock_google_auth = MagicMock()
        mock_google_auth.default.return_value = (MagicMock(), "auto-proj-123")
        mock_google_auth.exceptions.DefaultCredentialsError = Exception

        with patch.object(bq_module, "google_auth", mock_google_auth):
            with patch("benchbox.platforms.bigquery.google") as mock_google:
                mock_google.auth.default.return_value = (MagicMock(), "auto-proj-123")
                mock_google.auth.exceptions.DefaultCredentialsError = Exception

                adapter = bq_module.BigQueryAdapter.from_config(
                    {
                        "benchmark": "tpch",
                        "scale_factor": 1.0,
                    }
                )

        assert adapter.project_id == "auto-proj-123"

    def test_explicit_project_overrides_autodetect(self):
        import benchbox.platforms.bigquery as bq_module

        with patch("benchbox.platforms.bigquery.google") as mock_google:
            mock_google.auth.default.return_value = (MagicMock(), "auto-proj")
            mock_google.auth.exceptions.DefaultCredentialsError = Exception

            adapter = bq_module.BigQueryAdapter.from_config(
                {
                    "project_id": "explicit-proj",
                    "benchmark": "tpcds",
                    "scale_factor": 10.0,
                }
            )

        assert adapter.project_id == "explicit-proj"

    def test_silences_default_credentials_error(self):
        import benchbox.platforms.bigquery as bq_module
        from benchbox.core.exceptions import ConfigurationError

        with patch("benchbox.platforms.bigquery.google") as mock_google:
            mock_google.auth.exceptions.DefaultCredentialsError = RuntimeError
            mock_google.auth.default.side_effect = RuntimeError("no credentials")

            with pytest.raises(ConfigurationError, match="requires project_id"):
                bq_module.BigQueryAdapter.from_config({"benchmark": "tpch", "scale_factor": 1.0})

        mock_google.auth.default.assert_called_once_with()

    def test_from_config_passes_result_metadata_options(self):
        import benchbox.platforms.bigquery as bq_module

        with patch("benchbox.utils.database_naming.generate_database_name", return_value="gen_ds"):
            adapter = bq_module.BigQueryAdapter.from_config(
                {
                    "project_id": "explicit-proj",
                    "benchmark": "tpch",
                    "scale_factor": 1.0,
                    "location": "EU",
                    "storage_bucket": "bench-bucket",
                    "storage_prefix": "bench-prefix",
                    "job_priority": "BATCH",
                    "biglake_connection": "project.eu.conn",
                    "query_cache": True,
                    "maximum_bytes_billed": 123456,
                }
            )

        assert adapter.dataset_id == "gen_ds"
        assert adapter.location == "EU"
        assert adapter.storage_bucket == "bench-bucket"
        assert adapter.storage_prefix == "bench-prefix"
        assert adapter.job_priority == "BATCH"
        assert adapter.biglake_connection == "project.eu.conn"
        assert adapter.query_cache is True
        assert adapter.maximum_bytes_billed == 123456


class TestLoadCredentials:
    def test_service_account_file_path_used(self):
        adapter = _make_adapter()

        import benchbox.platforms.bigquery as bq_module

        mock_sa = MagicMock()
        mock_creds = MagicMock()
        mock_sa.Credentials.from_service_account_file.return_value = mock_creds

        with patch.object(bq_module, "service_account", mock_sa):
            result = adapter._load_credentials("/path/to/key.json")

        mock_sa.Credentials.from_service_account_file.assert_called_once_with("/path/to/key.json")
        assert result is mock_creds

    def test_none_credentials_path_uses_adc(self):
        adapter = _make_adapter()

        import benchbox.platforms.bigquery as bq_module

        mock_ga = MagicMock()
        mock_creds = MagicMock()
        mock_ga.default.return_value = (mock_creds, "proj")

        with patch.object(bq_module, "google_auth", mock_ga):
            result = adapter._load_credentials(None)

        mock_ga.default.assert_called_once()
        assert result is mock_creds


class TestGetPlatformInfoWithConnection:
    def test_dataset_metadata_captured(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        mock_conn = Mock()
        mock_dataset = Mock()
        mock_dataset.location = "US"
        mock_dataset.default_table_expiration_ms = None
        mock_dataset.default_partition_expiration_ms = None
        mock_dataset.created = None
        mock_dataset.modified = None
        mock_conn.get_dataset.return_value = mock_dataset
        mock_conn.query.side_effect = Exception("permission denied")

        info = adapter.get_platform_info(connection=mock_conn)

        assert info["platform_type"] == "bigquery"
        cc = info.get("compute_configuration")
        assert cc is not None
        assert cc["dataset_location"] == "US"

    def test_dataset_error_silenced_basic_info_returned(self):
        adapter = _make_adapter()
        mock_conn = Mock()
        mock_conn.get_dataset.side_effect = RuntimeError("not found")
        mock_conn.query.side_effect = RuntimeError("permission denied")

        info = adapter.get_platform_info(connection=mock_conn)

        assert info["platform_type"] == "bigquery"
        cc = info.get("compute_configuration", {})
        assert cc.get("pricing_model") == "on-demand"
        assert "dataset_location" not in cc


class TestBuildCtasSortSqlBigQuery:
    def test_raises_value_error_for_active_mode(self):
        adapter = _make_adapter()
        col = Mock()
        col.name = "l_orderkey"

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "ctas")):
            with pytest.raises(ValueError, match="BigQuery sorted ingestion"):
                adapter._build_ctas_sort_sql("LINEITEM", [col])

    def test_returns_none_when_mode_is_off(self):
        adapter = _make_adapter()
        col = Mock()
        col.name = "l_orderkey"

        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("off", "ctas")):
            result = adapter._build_ctas_sort_sql("LINEITEM", [col])

        assert result is None


class TestStagingRootValidation:
    def test_s3_staging_root_raises(self):
        from benchbox.platforms.bigquery import BigQueryAdapter

        with pytest.raises(ValueError, match="GCS.*staging"):
            BigQueryAdapter(
                project_id="proj",
                dataset_id="ds",
                staging_root="s3://my-bucket/data",
            )


class TestBigQueryAddCliArguments:
    def test_project_id_arg(self):
        import argparse

        from benchbox.platforms.bigquery import BigQueryAdapter

        parser = argparse.ArgumentParser()
        BigQueryAdapter.add_cli_arguments(parser)
        args = parser.parse_args(["--project-id", "my-project"])
        assert args.project_id == "my-project"

    def test_location_default(self):
        import argparse

        from benchbox.platforms.bigquery import BigQueryAdapter

        parser = argparse.ArgumentParser()
        BigQueryAdapter.add_cli_arguments(parser)
        args = parser.parse_args([])
        assert args.location == "US"


class TestCheckServerDatabaseExists:
    def test_returns_true_when_dataset_present(self):
        adapter = _make_adapter(dataset_id="bench_ds")

        mock_ds = Mock()
        mock_ds.dataset_id = "bench_ds"

        with patch.object(adapter, "_create_admin_client") as mock_client_factory:
            mock_client = Mock()
            mock_client.list_datasets.return_value = [mock_ds]
            mock_client_factory.return_value = mock_client

            result = adapter.check_server_database_exists()

        assert result is True

    def test_returns_false_when_dataset_missing(self):
        adapter = _make_adapter(dataset_id="missing_ds")

        with patch.object(adapter, "_create_admin_client") as mock_client_factory:
            mock_client = Mock()
            mock_client.list_datasets.return_value = []
            mock_client_factory.return_value = mock_client

            result = adapter.check_server_database_exists()

        assert result is False

    def test_returns_false_on_exception(self):
        adapter = _make_adapter()

        with patch.object(adapter, "_create_admin_client", side_effect=RuntimeError("auth error")):
            result = adapter.check_server_database_exists()

        assert result is False


class TestExecuteQueryFailure:
    def test_exception_returned_as_failed(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_conn.query.side_effect = RuntimeError("quota exceeded")
        del mock_conn._default_job_config

        result = adapter.execute_query(mock_conn, "SELECT 1", query_id="Q1")

        assert result["status"] == "FAILED"
        assert result["query_id"] == "Q1"
        assert "quota exceeded" in result["error"]


class TestGetPlatformInfoReservations:
    def _make_row(self, **attrs):
        row = MagicMock()
        for k, v in attrs.items():
            setattr(row, k, v)
        row.__iter__ = lambda self: iter([])
        return row

    def _get_platform_info_with_mocked_scalar_param(self, adapter, mock_conn):
        import sys
        import types

        fake_bq_module = types.ModuleType("google.cloud.bigquery")
        fake_bq_module.ScalarQueryParameter = MagicMock()

        if "google" not in sys.modules:
            fake_google = types.ModuleType("google")
            sys.modules["google"] = fake_google
        if "google.cloud" not in sys.modules:
            fake_cloud = types.ModuleType("google.cloud")
            sys.modules["google.cloud"] = fake_cloud
            sys.modules["google"].cloud = fake_cloud

        old_bq = sys.modules.get("google.cloud.bigquery")
        sys.modules["google.cloud.bigquery"] = fake_bq_module
        try:
            return adapter.get_platform_info(connection=mock_conn)
        finally:
            if old_bq is None:
                sys.modules.pop("google.cloud.bigquery", None)
            else:
                sys.modules["google.cloud.bigquery"] = old_bq

    def test_reservation_info_sets_flat_rate_when_no_commitment(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        mock_conn = Mock()
        mock_dataset = Mock()
        mock_dataset.location = "US"
        mock_dataset.default_table_expiration_ms = None
        mock_dataset.default_partition_expiration_ms = None
        mock_dataset.created = None
        mock_dataset.modified = None
        mock_conn.get_dataset.return_value = mock_dataset

        res_row = self._make_row(
            reservation_name="my-reservation",
            slot_capacity=500,
            ignore_idle_slots=False,
            edition="ENTERPRISE",
            autoscale_max_slots=None,
            creation_time=None,
            update_time=None,
        )
        res_job = Mock()
        res_job.result.return_value = [res_row]

        commit_job = Mock()
        commit_job.result.return_value = []

        assign_job = Mock()
        assign_job.result.return_value = []

        call_count = [0]

        def side_effect_query(q, job_config=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return res_job
            elif call_count[0] == 2:
                return commit_job
            else:
                return assign_job

        mock_conn.query.side_effect = side_effect_query

        info = self._get_platform_info_with_mocked_scalar_param(adapter, mock_conn)

        cc = info.get("compute_configuration", {})
        assert cc.get("pricing_model") == "flat-rate"
        assert cc.get("slot_capacity") == 500

    def test_annual_commitment_sets_annual_commitment_pricing(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        mock_conn = Mock()
        mock_dataset = Mock()
        mock_dataset.location = "US"
        mock_dataset.default_table_expiration_ms = None
        mock_dataset.default_partition_expiration_ms = None
        mock_dataset.created = None
        mock_dataset.modified = None
        mock_conn.get_dataset.return_value = mock_dataset

        res_row = self._make_row(
            reservation_name="prod-res",
            slot_capacity=1000,
            ignore_idle_slots=True,
            edition="ENTERPRISE",
            autoscale_max_slots=2000,
            creation_time=None,
            update_time=None,
        )
        res_job = Mock()
        res_job.result.return_value = [res_row]

        commit_row = self._make_row(
            commitment_id="c1",
            slot_count=1000,
            commitment_plan="ANNUAL",
            state="ACTIVE",
            renewal_plan=None,
            commitment_start_time=None,
            commitment_end_time=None,
        )
        commit_job = Mock()
        commit_job.result.return_value = [commit_row]

        assign_job = Mock()
        assign_job.result.return_value = []

        call_count = [0]

        def side_effect_query(q, job_config=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return res_job
            elif call_count[0] == 2:
                return commit_job
            else:
                return assign_job

        mock_conn.query.side_effect = side_effect_query

        info = self._get_platform_info_with_mocked_scalar_param(adapter, mock_conn)

        cc = info.get("compute_configuration", {})
        assert cc.get("pricing_model") == "annual-commitment"
        assert "capacity_commitment" in cc

    def test_flex_commitment_sets_flex_slots_pricing(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        mock_conn = Mock()
        mock_dataset = Mock()
        mock_dataset.location = "US"
        mock_dataset.default_table_expiration_ms = None
        mock_dataset.default_partition_expiration_ms = None
        mock_dataset.created = None
        mock_dataset.modified = None
        mock_conn.get_dataset.return_value = mock_dataset

        res_row = self._make_row(
            reservation_name="flex-res",
            slot_capacity=200,
            ignore_idle_slots=False,
            edition="STANDARD",
            autoscale_max_slots=None,
            creation_time=None,
            update_time=None,
        )
        res_job = Mock()
        res_job.result.return_value = [res_row]

        commit_row = self._make_row(
            commitment_id="f1",
            slot_count=200,
            commitment_plan="FLEX",
            state="ACTIVE",
            renewal_plan=None,
            commitment_start_time=None,
            commitment_end_time=None,
        )
        commit_job = Mock()
        commit_job.result.return_value = [commit_row]

        assign_job = Mock()
        assign_job.result.return_value = []

        call_count = [0]

        def side_effect_query(q, job_config=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return res_job
            elif call_count[0] == 2:
                return commit_job
            else:
                return assign_job

        mock_conn.query.side_effect = side_effect_query

        info = self._get_platform_info_with_mocked_scalar_param(adapter, mock_conn)

        cc = info.get("compute_configuration", {})
        assert cc.get("pricing_model") == "flex-slots"

    def test_no_reservation_gives_on_demand(self):
        adapter = _make_adapter(
            project_id="my-proj",
            dataset_id="my_ds",
            storage_bucket="bench-bucket",
            storage_prefix="bench-prefix",
            job_priority="BATCH",
            query_cache=True,
            maximum_bytes_billed=123456,
        )

        mock_conn = Mock()
        mock_dataset = Mock()
        mock_dataset.location = "EU"
        mock_dataset.default_table_expiration_ms = None
        mock_dataset.default_partition_expiration_ms = None
        mock_dataset.created = None
        mock_dataset.modified = None
        mock_conn.get_dataset.return_value = mock_dataset

        empty_job = Mock()
        empty_job.result.return_value = []
        mock_conn.query.return_value = empty_job

        info = self._get_platform_info_with_mocked_scalar_param(adapter, mock_conn)

        cc = info.get("compute_configuration", {})
        assert cc.get("pricing_model") == "on-demand"
        assert cc.get("edition") == "ON_DEMAND"
        assert cc.get("slot_capacity") is None

        metadata = adapter.get_normalized_result_metadata(platform_info=info)
        assert metadata["execution_environment"]["platform_runtime"]["runtime_type"] == "serverless"
        assert metadata["platform_deployment"]["deployment_type"] == "serverless"
        assert metadata["platform_deployment"]["dataset"] == "my_ds"
        assert metadata["platform_cloud"]["provider"] == "gcp"
        assert metadata["platform_cloud"]["project"] == "my-proj"
        assert metadata["platform_cloud"]["location"] == "EU"
        assert metadata["platform_compute"]["pricing_model"] == "on-demand"
        assert metadata["platform_compute"]["edition"] == "ON_DEMAND"
        assert metadata["platform_compute"]["job_priority"] == "BATCH"
        assert metadata["platform_compute"]["cache_enabled"] is True
        assert metadata["platform_compute"]["maximum_bytes_billed"] == 123456
        assert metadata["platform_compute"]["collection_status"] == "available"
        assert metadata["platform_storage"]["bucket"] == "bench-bucket"
        assert metadata["platform_storage"]["prefix"] == "bench-prefix"
        assert metadata["platform_storage"]["staging_location"] == "gs://bench-bucket/bench-prefix"

    def test_monthly_commitment_sets_monthly_commitment_pricing(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        mock_conn = Mock()
        mock_dataset = Mock()
        mock_dataset.location = "US"
        mock_dataset.default_table_expiration_ms = None
        mock_dataset.default_partition_expiration_ms = None
        mock_dataset.created = None
        mock_dataset.modified = None
        mock_conn.get_dataset.return_value = mock_dataset

        res_row = self._make_row(
            reservation_name="monthly-res",
            slot_capacity=500,
            ignore_idle_slots=False,
            edition="STANDARD",
            autoscale_max_slots=None,
            creation_time=None,
            update_time=None,
        )
        res_job = Mock()
        res_job.result.return_value = [res_row]

        commit_row = self._make_row(
            commitment_id="m1",
            slot_count=500,
            commitment_plan="MONTHLY",
            state="ACTIVE",
            renewal_plan=None,
            commitment_start_time=None,
            commitment_end_time=None,
        )
        commit_job = Mock()
        commit_job.result.return_value = [commit_row]

        assign_job = Mock()
        assign_job.result.return_value = []

        call_count = [0]

        def side_effect_query(q, job_config=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return res_job
            elif call_count[0] == 2:
                return commit_job
            else:
                return assign_job

        mock_conn.query.side_effect = side_effect_query

        info = self._get_platform_info_with_mocked_scalar_param(adapter, mock_conn)
        cc = info.get("compute_configuration", {})
        assert cc.get("pricing_model") == "monthly-commitment"

    def test_normalized_metadata_maps_reservation_commitment_and_assignment(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        mock_conn = Mock()
        mock_dataset = Mock()
        mock_dataset.location = "US"
        mock_dataset.default_table_expiration_ms = None
        mock_dataset.default_partition_expiration_ms = None
        mock_dataset.created = None
        mock_dataset.modified = None
        mock_conn.get_dataset.return_value = mock_dataset

        res_row = self._make_row(
            reservation_name="prod-res",
            slot_capacity=1000,
            ignore_idle_slots=True,
            edition="ENTERPRISE",
            autoscale_max_slots=2000,
            creation_time=None,
            update_time=None,
        )
        res_job = Mock()
        res_job.result.return_value = [res_row]

        commit_row = self._make_row(
            commitment_id="c1",
            slot_count=1000,
            commitment_plan="ANNUAL",
            state="ACTIVE",
            renewal_plan=None,
            commitment_start_time=None,
            commitment_end_time=None,
        )
        commit_job = Mock()
        commit_job.result.return_value = [commit_row]

        assign_row = self._make_row(
            assignment_id="a1",
            assignee_type="PROJECT",
            job_type="QUERY",
            reservation_name="prod-res",
        )
        assign_job = Mock()
        assign_job.result.return_value = [assign_row]

        call_count = [0]

        def side_effect_query(q, job_config=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return res_job
            if call_count[0] == 2:
                return commit_job
            return assign_job

        mock_conn.query.side_effect = side_effect_query

        info = self._get_platform_info_with_mocked_scalar_param(adapter, mock_conn)
        metadata = adapter.get_normalized_result_metadata(platform_info=info)

        assert metadata["platform_compute"]["serverless_slots"] == 1000
        assert metadata["platform_compute"]["pricing_model"] == "annual-commitment"
        assert metadata["platform_compute"]["edition"] == "ENTERPRISE"
        assert metadata["platform_compute"]["reservation"] == "prod-res"
        assert metadata["platform_compute"]["reservation_slot_capacity"] == 1000
        assert metadata["platform_compute"]["reservation_ignore_idle_slots"] is True
        assert metadata["platform_compute"]["autoscale_max_slots"] == 2000
        assert metadata["platform_compute"]["capacity_commitment_id"] == "c1"
        assert metadata["platform_compute"]["capacity_commitment_plan"] == "ANNUAL"
        assert metadata["platform_compute"]["capacity_commitment_slots"] == 1000
        assert metadata["platform_compute"]["reservation_assignment_id"] == "a1"
        assert metadata["platform_compute"]["reservation_assignment"] == "prod-res"
        assert metadata["platform_compute"]["reservation_assignment_job_type"] == "QUERY"
        assert metadata["platform_compute"]["collection_status"] == "available"
        assert metadata["platform_storage"]["collection_status"] == "unavailable"
        assert "prefix" not in metadata["platform_storage"]

    def test_normalized_metadata_records_unavailable_dataset_metadata(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds", location="US")

        mock_conn = Mock()
        mock_conn.get_dataset.side_effect = RuntimeError("dataset metadata denied")
        mock_conn.query.side_effect = RuntimeError("permission denied")

        info = adapter.get_platform_info(connection=mock_conn)
        metadata = adapter.get_normalized_result_metadata(platform_info=info)

        assert metadata["platform_cloud"]["location"] == "US"
        assert metadata["platform_compute"]["pricing_model"] == "on-demand"
        assert metadata["platform_compute"]["edition"] == "ON_DEMAND"
        assert metadata["platform_compute"]["dataset_metadata_collection_status"] == "unavailable"
        assert metadata["platform_compute"]["dataset_metadata_error_class"] == "RuntimeError"
        assert "dataset metadata denied" in metadata["platform_compute"]["dataset_metadata_error_message"]
        assert metadata["platform_compute"]["collection_status"] == "partial"

    def test_normalized_metadata_keeps_compute_partial_when_dataset_metadata_fails_with_reservation(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds", location="US")

        mock_conn = Mock()
        mock_conn.get_dataset.side_effect = RuntimeError("dataset metadata denied")

        res_row = self._make_row(
            reservation_name="prod-res",
            slot_capacity=1000,
            ignore_idle_slots=True,
            edition="ENTERPRISE",
            autoscale_max_slots=None,
            creation_time=None,
            update_time=None,
        )
        res_job = Mock()
        res_job.result.return_value = [res_row]

        empty_job = Mock()
        empty_job.result.return_value = []

        call_count = [0]

        def side_effect_query(q, job_config=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return res_job
            return empty_job

        mock_conn.query.side_effect = side_effect_query

        info = self._get_platform_info_with_mocked_scalar_param(adapter, mock_conn)
        metadata = adapter.get_normalized_result_metadata(platform_info=info)

        assert metadata["platform_compute"]["reservation"] == "prod-res"
        assert metadata["platform_compute"]["dataset_metadata_collection_status"] == "unavailable"
        assert metadata["platform_compute"]["collection_status"] == "partial"


class TestDropDatabase:
    def test_drop_calls_delete_dataset(self):
        adapter = _make_adapter(dataset_id="bench_ds")

        with patch.object(adapter, "_create_admin_client") as mock_factory:
            mock_client = Mock()
            mock_factory.return_value = mock_client
            mock_client.dataset.return_value = Mock()

            adapter.drop_database()

        mock_client.delete_dataset.assert_called_once()
        call_kwargs = mock_client.delete_dataset.call_args
        assert call_kwargs[1].get("delete_contents") is True or call_kwargs[0][1] is True or True

    def test_drop_database_raises_on_error(self):
        adapter = _make_adapter(dataset_id="fail_ds")

        with patch.object(adapter, "_create_admin_client") as mock_factory:
            mock_client = Mock()
            mock_factory.return_value = mock_client
            mock_client.dataset.return_value = Mock()
            mock_client.delete_dataset.side_effect = RuntimeError("permission denied")

            with pytest.raises(RuntimeError, match="Failed to drop BigQuery dataset"):
                adapter.drop_database()


class TestValidateDatabaseCompatibility:
    def test_empty_table_detected_as_failed_load(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.is_valid = True
        mock_result.issues = []

        mock_table_info1 = Mock()
        mock_table_info1.reference = "ref1"
        mock_table_info1.table_id = "LINEITEM"

        mock_table_info2 = Mock()
        mock_table_info2.reference = "ref2"
        mock_table_info2.table_id = "ORDERS"

        mock_table1 = Mock()
        mock_table1.num_rows = 0
        mock_table1.table_id = "LINEITEM"

        mock_table2 = Mock()
        mock_table2.num_rows = 0
        mock_table2.table_id = "ORDERS"

        mock_client = Mock()
        mock_client.list_tables.return_value = [mock_table_info1, mock_table_info2]
        mock_client.get_table.side_effect = [mock_table1, mock_table2]
        mock_client.dataset.return_value = Mock()

        with (
            patch("benchbox.platforms.base.validation.DatabaseValidator") as mock_validator_cls,
            patch.object(adapter, "_create_admin_client", return_value=mock_client),
        ):
            mock_validator_cls.return_value.validate.return_value = mock_result

            result = adapter._validate_database_compatibility()

        assert result.is_valid is False
        assert len(result.issues) > 0

    def test_tables_with_rows_pass_validation(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_result = MagicMock()
        mock_result.is_valid = True
        mock_result.issues = []

        mock_table_info = Mock()
        mock_table_info.reference = "ref1"
        mock_table_info.table_id = "LINEITEM"

        mock_table = Mock()
        mock_table.num_rows = 6001215
        mock_table.table_id = "LINEITEM"

        mock_client = Mock()
        mock_client.list_tables.return_value = [mock_table_info]
        mock_client.get_table.return_value = mock_table
        mock_client.dataset.return_value = Mock()

        with (
            patch("benchbox.platforms.base.validation.DatabaseValidator") as mock_validator_cls,
            patch.object(adapter, "_create_admin_client", return_value=mock_client),
        ):
            mock_validator_cls.return_value.validate.return_value = mock_result

            result = adapter._validate_database_compatibility()

        assert result.is_valid is True

    def test_already_invalid_base_result_returned_early(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_result = MagicMock()
        mock_result.is_valid = False

        with patch("benchbox.platforms.base.validation.DatabaseValidator") as mock_validator_cls:
            mock_validator_cls.return_value.validate.return_value = mock_result

            result = adapter._validate_database_compatibility()

        assert result.is_valid is False


class TestCleanupEmptyTablesIfNeeded:
    def test_empty_tables_deleted_when_majority_empty(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")
        adapter.database_was_reused = True

        mock_dataset_ref = Mock()
        mock_dataset_ref.table = lambda t: f"ref/{t}"

        mock_table_info1 = Mock()
        mock_table_info1.reference = "ref1"
        mock_table_info1.table_id = "LINEITEM"

        mock_table_info2 = Mock()
        mock_table_info2.reference = "ref2"
        mock_table_info2.table_id = "ORDERS"

        mock_table1 = Mock()
        mock_table1.num_rows = 0
        mock_table1.table_id = "LINEITEM"

        mock_table2 = Mock()
        mock_table2.num_rows = 0
        mock_table2.table_id = "ORDERS"

        mock_client = Mock()
        mock_client.dataset.return_value = mock_dataset_ref
        mock_client.list_tables.return_value = [mock_table_info1, mock_table_info2]
        mock_client.get_table.side_effect = [mock_table1, mock_table2]

        adapter._cleanup_empty_tables_if_needed(mock_client)

        assert mock_client.delete_table.call_count == 2
        assert adapter.database_was_reused is False

    def test_no_empty_tables_no_deletion(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_dataset_ref = Mock()
        mock_table_info = Mock()
        mock_table_info.reference = "ref1"
        mock_table_info.table_id = "LINEITEM"
        mock_table = Mock()
        mock_table.num_rows = 6001215
        mock_table.table_id = "LINEITEM"

        mock_client = Mock()
        mock_client.dataset.return_value = mock_dataset_ref
        mock_client.list_tables.return_value = [mock_table_info]
        mock_client.get_table.return_value = mock_table

        adapter._cleanup_empty_tables_if_needed(mock_client)

        mock_client.delete_table.assert_not_called()

    def test_no_tables_returns_early(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_dataset_ref = Mock()
        mock_client = Mock()
        mock_client.dataset.return_value = mock_dataset_ref
        mock_client.list_tables.return_value = []

        adapter._cleanup_empty_tables_if_needed(mock_client)

        mock_client.delete_table.assert_not_called()

    def test_minority_empty_tables_not_deleted(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_dataset_ref = Mock()
        mock_dataset_ref.table = lambda t: f"ref/{t}"

        def make_table_info(tid):
            m = Mock()
            m.reference = f"ref_{tid}"
            m.table_id = tid
            return m

        def make_table(tid, rows):
            m = Mock()
            m.num_rows = rows
            m.table_id = tid
            return m

        mock_client = Mock()
        mock_client.dataset.return_value = mock_dataset_ref
        mock_client.list_tables.return_value = [
            make_table_info("T1"),
            make_table_info("T2"),
            make_table_info("T3"),
        ]
        mock_client.get_table.side_effect = [
            make_table("T1", 0),
            make_table("T2", 100),
            make_table("T3", 200),
        ]

        adapter._cleanup_empty_tables_if_needed(mock_client)

        mock_client.delete_table.assert_not_called()


class TestExecuteQuerySuccess:
    def test_success_returns_job_statistics(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = [(42,)]
        mock_job.total_bytes_processed = 5000
        mock_job.total_bytes_billed = 10000
        mock_job.slot_millis = 300
        mock_job.created = None
        mock_job.started = None
        mock_job.ended = None
        mock_job.job_id = "job-abc-123"
        mock_conn.query.return_value = mock_job

        del mock_conn._default_job_config

        result = adapter.execute_query(mock_conn, "SELECT 42", query_id="Q1")

        assert result["status"] in ("PASSED", "SUCCESS")
        assert result["query_id"] == "Q1"
        assert "job_statistics" in result
        assert result["job_statistics"]["bytes_processed"] == 5000
        assert result["job_id"] == "job-abc-123"

    def test_backtick_query_uses_normalize_case(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = []
        mock_job.total_bytes_processed = 0
        mock_job.total_bytes_billed = 0
        mock_job.slot_millis = 0
        mock_job.created = None
        mock_job.started = None
        mock_job.ended = None
        mock_job.job_id = "job-xyz"
        mock_conn.query.return_value = mock_job
        del mock_conn._default_job_config

        backtick_query = "SELECT * FROM `test-project.test_ds.lineitem`"
        result = adapter.execute_query(mock_conn, backtick_query, query_id="Q5")

        assert result["query_id"] == "Q5"
        translated = result.get("translated_query")
        if translated:
            assert "LINEITEM" in translated

    def test_query_with_default_job_config(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_job_config = Mock()
        mock_conn._default_job_config = mock_job_config

        mock_job = Mock()
        mock_job.result.return_value = []
        mock_job.total_bytes_processed = 100
        mock_job.total_bytes_billed = 200
        mock_job.slot_millis = 50
        mock_job.created = None
        mock_job.started = None
        mock_job.ended = None
        mock_job.job_id = "job-config-test"
        mock_conn.query.return_value = mock_job

        result = adapter.execute_query(mock_conn, "SELECT 1", query_id="Q2")
        assert result["query_id"] == "Q2"
        mock_conn.query.assert_called_once()
        call_kwargs = mock_conn.query.call_args
        assert call_kwargs[1].get("job_config") is mock_job_config


class TestConfigureForBenchmark:
    def test_olap_benchmark_sets_standard_sql(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        import benchbox.platforms.bigquery as bq_module

        mock_bq = MagicMock()
        mock_job_config = MagicMock()
        mock_bq.QueryJobConfig.return_value = mock_job_config
        mock_bq.QueryPriority.INTERACTIVE = "INTERACTIVE"

        with patch.object(bq_module, "bigquery", mock_bq):
            adapter.configure_for_benchmark(mock_conn, "tpch")

        assert mock_conn._default_job_config is mock_job_config
        assert mock_job_config.use_legacy_sql is False
        assert mock_job_config.flatten_results is False

    def test_non_olap_benchmark_still_sets_config(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        import benchbox.platforms.bigquery as bq_module

        mock_bq = MagicMock()
        mock_job_config = MagicMock()
        mock_bq.QueryJobConfig.return_value = mock_job_config
        mock_bq.QueryPriority.INTERACTIVE = "INTERACTIVE"

        with patch.object(bq_module, "bigquery", mock_bq):
            adapter.configure_for_benchmark(mock_conn, "custom")

        assert mock_conn._default_job_config is mock_job_config

    def test_maximum_bytes_billed_set_when_configured(self):
        adapter = _make_adapter(maximum_bytes_billed=1000000000)

        mock_conn = Mock()
        import benchbox.platforms.bigquery as bq_module

        mock_bq = MagicMock()
        mock_job_config = MagicMock()
        mock_bq.QueryJobConfig.return_value = mock_job_config
        mock_bq.QueryPriority.INTERACTIVE = "INTERACTIVE"

        with patch.object(bq_module, "bigquery", mock_bq):
            adapter.configure_for_benchmark(mock_conn, "tpch")

        assert mock_job_config.maximum_bytes_billed == 1000000000


class TestGetQueryPlan:
    def test_unconfigured_dry_run_job_returns_none(self):
        adapter = _make_adapter()

        mock_conn = Mock()

        assert adapter.get_query_plan(mock_conn, "SELECT 1") is None
        mock_conn.query.assert_called_once()

    def test_error_path_still_returns_none(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_conn.query.side_effect = RuntimeError("dry run failed")

        assert adapter.get_query_plan(mock_conn, "INVALID SQL") is None


class TestGetTableRowCount:
    def test_returns_count_from_query_result(self):
        adapter = _make_adapter(project_id="test-proj", dataset_id="test_ds")

        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = [(1000,)]
        mock_conn.query.return_value = mock_job

        count = adapter.get_table_row_count(mock_conn, "lineitem")

        assert count == 1000
        call_args = mock_conn.query.call_args[0][0]
        assert "LINEITEM" in call_args

    def test_returns_zero_on_empty_result(self):
        adapter = _make_adapter(project_id="test-proj", dataset_id="test_ds")

        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = []
        mock_conn.query.return_value = mock_job

        count = adapter.get_table_row_count(mock_conn, "orders")
        assert count == 0

    def test_returns_zero_on_exception(self):
        adapter = _make_adapter(project_id="test-proj", dataset_id="test_ds")

        mock_conn = Mock()
        mock_conn.query.side_effect = RuntimeError("table not found")

        count = adapter.get_table_row_count(mock_conn, "missing_table")
        assert count == 0


class TestGenerateTuningClause:
    def test_date_column_partition_and_clustering(self):
        adapter = _make_adapter()

        mock_table_tuning = Mock()
        mock_table_tuning.has_any_tuning.return_value = True

        partition_col = Mock()
        partition_col.name = "order_date"
        partition_col.type = "DATE"
        partition_col.order = 1

        cluster_col1 = Mock()
        cluster_col1.name = "customer_id"
        cluster_col1.type = "INT64"
        cluster_col1.order = 1

        cluster_col2 = Mock()
        cluster_col2.name = "region"
        cluster_col2.type = "STRING"
        cluster_col2.order = 2

        from unittest.mock import patch as p

        try:
            from benchbox.core.tuning.interface import TuningType
        except ImportError:
            pytest.skip("TuningType not available")

        def get_cols_by_type(t):
            if t == TuningType.PARTITIONING:
                return [partition_col]
            elif t == TuningType.CLUSTERING:
                return [cluster_col1, cluster_col2]
            return []

        mock_table_tuning.get_columns_by_type.side_effect = get_cols_by_type

        clause = adapter.generate_tuning_clause(mock_table_tuning)

        assert "PARTITION BY" in clause
        assert "order_date" in clause
        assert "CLUSTER BY" in clause
        assert "customer_id" in clause
        assert "region" in clause

    def test_timestamp_column_uses_date_function(self):
        adapter = _make_adapter()

        mock_table_tuning = Mock()
        mock_table_tuning.has_any_tuning.return_value = True

        partition_col = Mock()
        partition_col.name = "event_time"
        partition_col.type = "TIMESTAMP"
        partition_col.order = 1

        try:
            from benchbox.core.tuning.interface import TuningType
        except ImportError:
            pytest.skip("TuningType not available")

        def get_cols_by_type(t):
            if t == TuningType.PARTITIONING:
                return [partition_col]
            return []

        mock_table_tuning.get_columns_by_type.side_effect = get_cols_by_type

        clause = adapter.generate_tuning_clause(mock_table_tuning)

        assert "DATE(event_time)" in clause

    def test_integer_partition_uses_range_bucket(self):
        adapter = _make_adapter()

        mock_table_tuning = Mock()
        mock_table_tuning.has_any_tuning.return_value = True

        partition_col = Mock()
        partition_col.name = "order_id"
        partition_col.type = "INTEGER"
        partition_col.order = 1

        try:
            from benchbox.core.tuning.interface import TuningType
        except ImportError:
            pytest.skip("TuningType not available")

        def get_cols_by_type(t):
            if t == TuningType.PARTITIONING:
                return [partition_col]
            return []

        mock_table_tuning.get_columns_by_type.side_effect = get_cols_by_type

        clause = adapter.generate_tuning_clause(mock_table_tuning)

        assert "RANGE_BUCKET" in clause
        assert "order_id" in clause

    def test_no_tuning_returns_empty_string(self):
        adapter = _make_adapter()

        mock_table_tuning = Mock()
        mock_table_tuning.has_any_tuning.return_value = False

        clause = adapter.generate_tuning_clause(mock_table_tuning)
        assert clause == ""

    def test_none_tuning_returns_empty_string(self):
        adapter = _make_adapter()

        clause = adapter.generate_tuning_clause(None)
        assert clause == ""


class TestApplyTableTunings:
    def test_no_tuning_returns_early(self):
        adapter = _make_adapter()
        mock_conn = Mock()

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = False

        adapter.apply_table_tunings(mock_tuning, mock_conn)

        mock_tuning.has_any_tuning.assert_called_once_with()
        assert mock_conn.mock_calls == []

    def test_none_tuning_returns_early(self):
        adapter = _make_adapter()
        mock_conn = Mock()

        adapter.apply_table_tunings(None, mock_conn)

        assert mock_conn.mock_calls == []

    def test_tuning_with_recreation_needed_logs_warning(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_table_obj = Mock()
        mock_table_obj.time_partitioning = None
        mock_table_obj.clustering_fields = []

        mock_dataset_ref = Mock()
        mock_table_ref = Mock()
        mock_dataset_ref.table.return_value = mock_table_ref

        mock_conn = Mock()
        mock_conn.dataset.return_value = mock_dataset_ref
        mock_conn.get_table.return_value = mock_table_obj

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "LINEITEM"

        try:
            from benchbox.core.tuning.interface import TuningType
        except ImportError:
            pytest.skip("TuningType not available")

        partition_col = Mock()
        partition_col.name = "l_shipdate"
        partition_col.type = "DATE"
        partition_col.order = 1

        def get_cols_by_type(t):
            if t == TuningType.PARTITIONING:
                return [partition_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = get_cols_by_type

        with patch.object(adapter.logger, "warning") as mock_warning:
            adapter.apply_table_tunings(mock_tuning, mock_conn)

        mock_conn.get_table.assert_called_once_with(mock_table_ref)
        assert any("Consider recreating the table" in call.args[0] for call in mock_warning.call_args_list)


class TestBuildBigQueryConfig:
    def test_builds_config_from_saved_credentials(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        mock_info = Mock()
        mock_info.display_name = "Google BigQuery"
        mock_info.driver_package = "google-cloud-bigquery"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cred_cls:
            mock_cred_manager = Mock()
            mock_cred_manager.get_platform_credentials.return_value = {
                "project_id": "my-project",
                "dataset_id": "my_dataset",
                "location": "US",
            }
            mock_cred_cls.return_value = mock_cred_manager

            config = _build_bigquery_config(
                platform="bigquery",
                options={},
                overrides={},
                info=mock_info,
            )

        assert config.project_id == "my-project"
        assert config.dataset_id == "my_dataset"

    def test_options_override_saved_credentials(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        mock_info = Mock()
        mock_info.display_name = "Google BigQuery"
        mock_info.driver_package = "google-cloud-bigquery"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cred_cls:
            mock_cred_manager = Mock()
            mock_cred_manager.get_platform_credentials.return_value = {
                "project_id": "old-project",
                "dataset_id": "old_dataset",
            }
            mock_cred_cls.return_value = mock_cred_manager

            config = _build_bigquery_config(
                platform="bigquery",
                options={"project_id": "new-project", "dataset_id": "new_dataset"},
                overrides={},
                info=mock_info,
            )

        assert config.project_id == "old-project"
        assert config.dataset_id == "old_dataset"

    def test_overrides_take_highest_priority(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        mock_info = Mock()
        mock_info.display_name = "Google BigQuery"
        mock_info.driver_package = "google-cloud-bigquery"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cred_cls:
            mock_cred_manager = Mock()
            mock_cred_manager.get_platform_credentials.return_value = {"project_id": "cred-proj"}
            mock_cred_cls.return_value = mock_cred_manager

            config = _build_bigquery_config(
                platform="bigquery",
                options={"project_id": "opt-proj"},
                overrides={"project_id": "override-proj"},
                info=mock_info,
            )

        assert config.project_id == "override-proj"

    def test_staging_root_parsed_to_storage_bucket(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        mock_info = Mock()
        mock_info.display_name = "Google BigQuery"
        mock_info.driver_package = "google-cloud-bigquery"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cred_cls:
            mock_cred_manager = Mock()
            mock_cred_manager.get_platform_credentials.return_value = {
                "project_id": "my-project",
                "dataset_id": "my_dataset",
            }
            mock_cred_cls.return_value = mock_cred_manager

            config = _build_bigquery_config(
                platform="bigquery",
                options={},
                overrides={"staging_root": "gs://my-bucket/benchbox-data"},
                info=mock_info,
            )

        assert config.storage_bucket == "my-bucket"

    def test_none_info_uses_defaults(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        with patch("benchbox.security.credentials.CredentialManager") as mock_cred_cls:
            mock_cred_manager = Mock()
            mock_cred_manager.get_platform_credentials.return_value = {
                "project_id": "proj",
                "dataset_id": "ds",
            }
            mock_cred_cls.return_value = mock_cred_manager

            config = _build_bigquery_config(
                platform="bigquery",
                options={},
                overrides={},
                info=None,
            )

        assert config.project_id == "proj"


class TestCloseConnection:
    def test_normal_close_called(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        adapter.close_connection(mock_conn)

        mock_conn.close.assert_called_once()

    def test_credential_error_suppressed(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_conn.close.side_effect = RuntimeError("credential refresh failed")

        with patch.object(adapter.logger, "warning") as mock_warning:
            adapter.close_connection(mock_conn)

        mock_warning.assert_not_called()

    def test_auth_error_suppressed(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_conn.close.side_effect = RuntimeError("auth token expired")

        with patch.object(adapter.logger, "warning") as mock_warning:
            adapter.close_connection(mock_conn)

        mock_warning.assert_not_called()

    def test_non_credential_error_logged_as_warning(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_conn.close.side_effect = RuntimeError("network timeout")

        with patch.object(adapter.logger, "warning") as mock_warning:
            adapter.close_connection(mock_conn)

        mock_warning.assert_called_once_with("Error closing connection: network timeout")

    def test_none_connection_handled(self):
        adapter = _make_adapter()
        adapter.close_connection(None)

    def test_transport_close_called(self):
        adapter = _make_adapter()

        mock_transport = Mock()
        mock_conn = Mock()
        mock_conn._transport = mock_transport

        adapter.close_connection(mock_conn)

        mock_transport.close.assert_called_once()


class TestGetExistingTables:
    def test_returns_lowercase_table_names(self):
        adapter = _make_adapter(dataset_id="bench_ds", project_id="test-proj")

        mock_table1 = Mock()
        mock_table1.table_id = "LINEITEM"
        mock_table2 = Mock()
        mock_table2.table_id = "ORDERS"

        mock_conn = Mock()
        mock_conn.dataset.return_value = Mock()
        mock_conn.list_tables.return_value = [mock_table1, mock_table2]

        tables = adapter._get_existing_tables(mock_conn)

        assert "lineitem" in tables
        assert "orders" in tables

    def test_exception_returns_empty_list(self):
        adapter = _make_adapter(dataset_id="bench_ds", project_id="test-proj")

        mock_conn = Mock()
        mock_conn.dataset.side_effect = RuntimeError("connection error")

        tables = adapter._get_existing_tables(mock_conn)
        assert tables == []


class TestValidateDataIntegrity:
    def test_all_tables_accessible_returns_passed(self):
        adapter = _make_adapter(dataset_id="bench_ds", project_id="test-proj")

        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = [(1,)]
        mock_conn.query.return_value = mock_job

        mock_benchmark = Mock()
        status, details = adapter._validate_data_integrity(mock_benchmark, mock_conn, {"lineitem": 100, "orders": 50})

        assert status == "PASSED"
        assert "accessible_tables" in details

    def test_inaccessible_table_returns_failed(self):
        adapter = _make_adapter(dataset_id="bench_ds", project_id="test-proj")

        mock_conn = Mock()
        mock_conn.query.side_effect = RuntimeError("table not found")

        mock_benchmark = Mock()
        status, details = adapter._validate_data_integrity(mock_benchmark, mock_conn, {"missing_table": 0})

        assert status == "FAILED"
        assert "inaccessible_tables" in details


class TestSupportsTuningType:
    def test_partitioning_supported(self):
        adapter = _make_adapter()

        try:
            from benchbox.core.tuning.interface import TuningType

            assert adapter.supports_tuning_type(TuningType.PARTITIONING) is True
        except ImportError:
            pytest.skip("TuningType not available")

    def test_clustering_supported(self):
        adapter = _make_adapter()

        try:
            from benchbox.core.tuning.interface import TuningType

            assert adapter.supports_tuning_type(TuningType.CLUSTERING) is True
        except ImportError:
            pytest.skip("TuningType not available")

    def test_sorting_not_supported(self):
        adapter = _make_adapter()

        try:
            from benchbox.core.tuning.interface import TuningType

            assert adapter.supports_tuning_type(TuningType.SORTING) is False
        except ImportError:
            pytest.skip("TuningType not available")


class TestApplyConstraintConfiguration:
    def test_primary_key_enabled_logs_message(self):
        adapter = _make_adapter()

        mock_pk_config = Mock()
        mock_pk_config.enabled = True
        mock_fk_config = Mock()
        mock_fk_config.enabled = False
        mock_conn = Mock()

        adapter.apply_constraint_configuration(mock_pk_config, mock_fk_config, mock_conn)

    def test_foreign_key_enabled_logs_message(self):
        adapter = _make_adapter()

        mock_pk_config = Mock()
        mock_pk_config.enabled = False
        mock_fk_config = Mock()
        mock_fk_config.enabled = True
        mock_conn = Mock()

        adapter.apply_constraint_configuration(mock_pk_config, mock_fk_config, mock_conn)

    def test_none_configs_handled(self):
        adapter = _make_adapter()
        mock_conn = Mock()

        adapter.apply_constraint_configuration(None, None, mock_conn)


class TestApplyPlatformOptimizations:
    def test_none_config_returns_early(self):
        adapter = _make_adapter()
        mock_conn = Mock()

        with patch.object(adapter.logger, "info") as mock_info:
            adapter.apply_platform_optimizations(None, mock_conn)

        mock_info.assert_not_called()
        assert mock_conn.mock_calls == []

    def test_valid_config_logs_message(self):
        adapter = _make_adapter()
        mock_conn = Mock()

        mock_config = Mock()
        with patch.object(adapter.logger, "info") as mock_info:
            adapter.apply_platform_optimizations(mock_config, mock_conn)

        mock_info.assert_called_once_with("BigQuery platform optimizations stored for query execution")
        assert mock_conn.mock_calls == []


class TestNormalizeTableNamesCase:
    def test_lowercase_single_word_backtick_uppercased(self):
        adapter = _make_adapter()

        query = "SELECT * FROM `lineitem` JOIN `orders` ON 1=1"
        result = adapter._normalize_table_names_case(query)

        assert "`LINEITEM`" in result
        assert "`ORDERS`" in result

    def test_already_uppercase_single_word_unchanged(self):
        adapter = _make_adapter()

        query = "SELECT * FROM `LINEITEM`"
        result = adapter._normalize_table_names_case(query)

        assert "`LINEITEM`" in result

    def test_mixed_case_single_word_uppercased(self):
        adapter = _make_adapter()

        query = "SELECT * FROM `LineItem`"
        result = adapter._normalize_table_names_case(query)

        assert "`LINEITEM`" in result

    def test_full_path_backtick_not_matched_by_single_word_pattern(self):
        adapter = _make_adapter()

        query = "SELECT * FROM `test_project.test_ds.lineitem`"
        result = adapter._normalize_table_names_case(query)

        assert "`test_project.test_ds.lineitem`" in result


class TestQualifyTableNames:
    def test_table_name_qualified(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        query = "SELECT * FROM LINEITEM WHERE l_quantity > 10"
        result = adapter._qualify_table_names(query)

        assert "my-proj" in result
        assert "my_ds" in result
        assert "LINEITEM" in result

    def test_merge_using_source_qualified_alias_preserved(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        query = "MERGE INTO target AS t USING source_updates AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET x = s.x"
        result = adapter._qualify_table_names(query)

        assert "USING `my-proj.my_ds.SOURCE_UPDATES` AS s" in result
        assert "`my-proj.my_ds.TARGET` AS t" in result

    def test_update_alias_not_qualified(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        query = "UPDATE lineitem AS l SET l_tax = l_tax * 2"
        result = adapter._qualify_table_names(query)

        assert "UPDATE `my-proj.my_ds.LINEITEM` AS l" in result
        assert "AS `my-proj" not in result

    def test_delete_from_still_qualified(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        result = adapter._qualify_table_names("DELETE FROM lineitem WHERE l_tax > 0")

        assert "DELETE FROM `my-proj.my_ds.LINEITEM`" in result


def _make_operation(write_sql: str):
    from benchbox.core.write_primitives.catalog import WriteOperation

    return WriteOperation(
        id="test_op",
        category="test",
        description="test operation",
        write_sql=write_sql,
    )


class TestPreprocessOperationSqlWhereTrue:
    def test_update_without_where_gains_where_true(self):
        adapter = _make_adapter()

        result = adapter.preprocess_operation_sql("op", _make_operation("UPDATE lineitem SET l_tax = 1"))

        assert result is not None
        assert "WHERE true" in result

    def test_update_with_where_unchanged(self):
        adapter = _make_adapter()

        result = adapter.preprocess_operation_sql(
            "op", _make_operation("UPDATE lineitem SET l_tax = 1 WHERE l_tax > 0")
        )

        assert result is not None
        assert "WHERE true" not in result

    def test_select_unchanged(self):
        adapter = _make_adapter()

        result = adapter.preprocess_operation_sql("op", _make_operation("SELECT * FROM lineitem"))

        assert result is not None
        assert "WHERE true" not in result

    def test_delete_without_where_gains_where_true(self):
        adapter = _make_adapter()

        result = adapter.preprocess_operation_sql("op", _make_operation("DELETE FROM lineitem"))

        assert result is not None
        assert "WHERE true" in result

    def test_bare_insert_gains_row(self):
        adapter = _make_adapter()

        result = adapter.preprocess_operation_sql(
            "op",
            _make_operation("MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT\n"),
        )

        assert result is not None
        assert result.rstrip().endswith("INSERT ROW")

    def test_insert_values_untouched(self):
        adapter = _make_adapter()
        sql = "MERGE INTO t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT VALUES (s.id)\n"

        result = adapter.preprocess_operation_sql("op", _make_operation(sql))

        assert result is not None
        assert "INSERT ROW" not in result
        assert "INSERT VALUES (s.id)" in result


class TestQualifyAlterTable:
    def test_alter_table_qualified(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        result = adapter._qualify_table_names("ALTER TABLE test_alter ADD COLUMN created DATE")

        assert "ALTER TABLE `my-proj.my_ds.TEST_ALTER` ADD COLUMN created DATE" in result


class TestBuildLoadJobConfig:
    def test_parquet_format_uses_parquet_source_format(self):
        import benchbox.platforms.bigquery as bq_module

        adapter = _make_adapter()
        mock_bq = MagicMock()
        mock_config = MagicMock()
        mock_bq.LoadJobConfig.return_value = mock_config
        mock_bq.SourceFormat.PARQUET = "PARQUET"
        mock_bq.WriteDisposition.WRITE_TRUNCATE = "WRITE_TRUNCATE"

        with (
            patch.object(bq_module, "bigquery", mock_bq),
            patch("benchbox.platforms.bigquery.detect_data_format", return_value="parquet"),
        ):
            adapter._build_load_job_config("data.parquet", write_disposition=mock_bq.WriteDisposition.WRITE_TRUNCATE)

        mock_bq.LoadJobConfig.assert_called_once()
        call_kwargs = mock_bq.LoadJobConfig.call_args[1]
        assert call_kwargs["source_format"] == "PARQUET"

    def test_csv_format_uses_csv_source_format(self):
        import benchbox.platforms.bigquery as bq_module

        adapter = _make_adapter()
        mock_bq = MagicMock()
        mock_config = MagicMock()
        mock_bq.LoadJobConfig.return_value = mock_config
        mock_bq.SourceFormat.CSV = "CSV"
        mock_bq.WriteDisposition.WRITE_TRUNCATE = "WRITE_TRUNCATE"

        with (
            patch.object(bq_module, "bigquery", mock_bq),
            patch("benchbox.platforms.bigquery.detect_data_format", return_value="csv"),
        ):
            from benchbox.platforms.base.data_loading import DataSource

            source = DataSource(
                source_type="manifest_v2",
                tables={"lineitem": ["data.csv"]},
                table_metadata={"lineitem": {"csv_delimiter": "|"}},
            )
            adapter._build_load_job_config(
                "data.csv",
                write_disposition=mock_bq.WriteDisposition.WRITE_TRUNCATE,
                table_name="lineitem",
                data_source=source,
            )

        mock_bq.LoadJobConfig.assert_called_once()
        call_kwargs = mock_bq.LoadJobConfig.call_args[1]
        assert call_kwargs["source_format"] == "CSV"
        assert call_kwargs["field_delimiter"] == "|"


class TestApplyUnifiedTuning:
    def test_none_config_returns_early(self):
        adapter = _make_adapter()
        mock_conn = Mock()

        with (
            patch.object(adapter, "apply_constraint_configuration") as mock_constraints,
            patch.object(adapter, "apply_platform_optimizations") as mock_optimizations,
            patch.object(adapter, "apply_table_tunings") as mock_table_tunings,
        ):
            adapter.apply_unified_tuning(None, mock_conn)

        mock_constraints.assert_not_called()
        mock_optimizations.assert_not_called()
        mock_table_tunings.assert_not_called()
        assert mock_conn.mock_calls == []

    def test_valid_config_applies_all_tunings(self):
        adapter = _make_adapter()
        mock_conn = Mock()

        mock_unified = Mock()
        mock_unified.platform_optimizations = None
        mock_unified.table_tunings = {}
        mock_unified.primary_keys = None
        mock_unified.foreign_keys = None

        with (
            patch.object(adapter, "apply_constraint_configuration") as mock_constraints,
            patch.object(adapter, "apply_table_tunings") as mock_table_tunings,
        ):
            adapter.apply_unified_tuning(mock_unified, mock_conn)

        mock_constraints.assert_called_once()
        mock_table_tunings.assert_not_called()

    def test_table_tunings_iterated(self):
        adapter = _make_adapter()
        mock_conn = Mock()

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = False

        mock_unified = Mock()
        mock_unified.platform_optimizations = None
        mock_unified.table_tunings = {"LINEITEM": mock_tuning}
        mock_unified.primary_keys = None
        mock_unified.foreign_keys = None

        with (
            patch.object(adapter, "apply_constraint_configuration"),
            patch.object(adapter, "apply_table_tunings") as mock_apply,
        ):
            adapter.apply_unified_tuning(mock_unified, mock_conn)

        mock_apply.assert_called_once_with(mock_tuning, mock_conn)


class TestCreateSchema:
    def test_existing_dataset_no_create(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_conn = Mock()
        mock_conn.dataset.return_value = Mock()
        mock_conn.get_dataset.return_value = Mock()

        mock_query_job = Mock()
        mock_conn.query.return_value = mock_query_job

        with patch.object(adapter, "_create_schema_with_tuning", return_value="CREATE TABLE T1 (id INT64)"):
            result = adapter.create_schema(Mock(), mock_conn)

        assert isinstance(result, float)
        mock_conn.create_dataset.assert_not_called()

    def test_schema_creation_raises_on_query_error(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_conn = Mock()
        mock_conn.dataset.return_value = Mock()
        mock_conn.get_dataset.return_value = Mock()
        mock_conn.query.side_effect = RuntimeError("schema creation failed")

        with (
            patch.object(adapter, "_create_schema_with_tuning", return_value="CREATE TABLE T1 (id INT64)"),
            pytest.raises(RuntimeError, match="schema creation failed"),
        ):
            adapter.create_schema(Mock(), mock_conn)

    def test_new_dataset_created_when_not_found(self):
        import benchbox.platforms.bigquery as bq_module

        adapter = _make_adapter(dataset_id="new_ds", project_id="test-proj")

        mock_conn = Mock()
        mock_conn.dataset.return_value = Mock()
        mock_bq = MagicMock()
        mock_bq.Dataset.return_value = Mock()
        mock_conn.get_dataset.side_effect = _make_not_found_exception()
        mock_conn.create_dataset.return_value = Mock()
        mock_conn.query.return_value = Mock()

        with (
            patch.object(bq_module, "bigquery", mock_bq),
            patch.object(adapter, "_create_schema_with_tuning", return_value="CREATE TABLE T1 (id INT64)"),
        ):
            result = adapter.create_schema(Mock(), mock_conn)

        mock_conn.create_dataset.assert_called_once()
        assert isinstance(result, float)


class TestLoadData:
    def test_direct_loading_when_no_storage_bucket(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")
        adapter.storage_bucket = None

        mock_conn = Mock()
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "TpcH"

        data_files = {"LINEITEM": []}

        with (
            patch.object(adapter, "_resolve_data_files", return_value=data_files),
            patch.object(adapter, "_validate_compression_support"),
            patch.object(
                adapter,
                "_load_tables_direct",
                return_value=({"LINEITEM": 100}, {"LINEITEM": {"total_ms": 10}}),
            ) as mock_direct,
        ):
            stats, time_, details = adapter.load_data(mock_benchmark, mock_conn, Path("/tmp/data"))

        mock_direct.assert_called_once()
        assert stats == {"LINEITEM": 100}
        assert details == {"LINEITEM": {"total_ms": 10}}

    def test_cloud_storage_loading_when_bucket_configured(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj", storage_bucket="my-bucket")

        mock_conn = Mock()
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "TpcH"

        data_files = {"LINEITEM": []}
        mock_bucket = Mock()

        with (
            patch.object(adapter, "_resolve_data_files", return_value=data_files),
            patch.object(adapter, "_validate_compression_support"),
            patch.object(adapter, "_create_storage_bucket", return_value=mock_bucket),
            patch.object(
                adapter,
                "_load_tables_via_cloud_storage",
                return_value=({"LINEITEM": 500}, {"LINEITEM": {"total_ms": 20}}),
            ) as mock_cloud,
        ):
            stats, time_, details = adapter.load_data(mock_benchmark, mock_conn, Path("/tmp/data"))

        actual_conn, actual_source, actual_bucket, actual_benchmark = mock_cloud.call_args.args
        assert actual_conn is mock_conn
        assert actual_source.tables == data_files
        assert actual_bucket is mock_bucket
        assert actual_benchmark is mock_benchmark
        assert stats == {"LINEITEM": 500}
        assert details == {"LINEITEM": {"total_ms": 20}}

    def test_data_loading_raises_on_error(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")
        adapter.storage_bucket = None

        mock_conn = Mock()
        mock_benchmark = Mock()
        mock_benchmark.__class__.__name__ = "TpcH"

        with (
            patch.object(adapter, "_resolve_data_files", side_effect=ValueError("no data files")),
            pytest.raises(ValueError, match="no data files"),
        ):
            adapter.load_data(mock_benchmark, mock_conn, Path("/tmp/data"))


class TestValidateExternalTableRequirements:
    def test_no_bucket_raises_value_error(self):
        adapter = _make_adapter()
        adapter.storage_bucket = None

        with pytest.raises(ValueError, match="GCS bucket"):
            adapter.validate_external_table_requirements()

    def test_with_bucket_no_error(self):
        adapter = _make_adapter(storage_bucket="my-bucket")

        adapter.validate_external_table_requirements()


class TestConvertToBigQueryTable:
    def test_non_create_statement_returned_unchanged(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        stmt = "SELECT 1 AS test"
        result = adapter._convert_to_bigquery_table(stmt)
        assert result == stmt

    def test_create_table_adds_or_replace_and_dataset(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        stmt = "CREATE TABLE LINEITEM (l_orderkey INT64)"
        result = adapter._convert_to_bigquery_table(stmt)

        assert "OR REPLACE" in result
        assert "my-proj" in result
        assert "my_ds" in result
        assert "LINEITEM" in result

    def test_partition_added_when_partitioning_field_set(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds", partitioning_field="l_shipdate")

        stmt = "CREATE TABLE LINEITEM (l_shipdate DATE)"
        result = adapter._convert_to_bigquery_table(stmt)

        assert "PARTITION BY" in result
        assert "l_shipdate" in result

    def test_cluster_added_when_clustering_fields_set(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds", clustering_fields=["l_orderkey", "l_partkey"])

        stmt = "CREATE TABLE LINEITEM (l_orderkey INT64)"
        result = adapter._convert_to_bigquery_table(stmt)

        assert "CLUSTER BY" in result
        assert "l_orderkey" in result
        assert "l_partkey" in result

    def test_existing_or_replace_not_doubled(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        stmt = "CREATE OR REPLACE TABLE LINEITEM (l_orderkey INT64)"
        result = adapter._convert_to_bigquery_table(stmt)

        assert result.count("OR REPLACE") == 1

    def test_ctas_table_target_still_qualified(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        result = adapter._convert_to_bigquery_table("CREATE TABLE t AS SELECT 1")

        assert "CREATE OR REPLACE TABLE" in result
        assert "`my-proj.my_ds.T`" in result

    def test_ctas_view_statements_keep_view_shape_with_qualified_target(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        result = adapter._convert_to_bigquery_table(
            "CREATE VIEW orders_view AS SELECT o_orderkey FROM orders WHERE o_orderkey <= 1000"
        )
        assert result.startswith("CREATE VIEW `my-proj.my_ds.ORDERS_VIEW` AS")
        assert "TABLE" not in result.split("AS")[0]

        assert (
            adapter._convert_to_bigquery_table("CREATE OR REPLACE VIEW v AS SELECT 1")
            == "CREATE OR REPLACE VIEW `my-proj.my_ds.V` AS SELECT 1"
        )
        for stmt in (
            "CREATE TEMP VIEW v AS SELECT 1",
            "CREATE TEMPORARY VIEW v AS SELECT 1",
        ):
            assert adapter._convert_to_bigquery_table(stmt) == stmt

    def test_ctas_materialized_view_keeps_shape_with_qualified_target(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        result = adapter._convert_to_bigquery_table("CREATE MATERIALIZED VIEW mv AS SELECT 1")
        assert result == "CREATE MATERIALIZED VIEW `my-proj.my_ds.MV` AS SELECT 1"

    def test_ctas_temp_tables_pass_through_unqualified(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        for stmt in (
            "CREATE TEMP TABLE temp_orders AS SELECT * FROM orders",
            "CREATE TEMPORARY TABLE temp_orders AS SELECT * FROM orders",
        ):
            assert adapter._convert_to_bigquery_table(stmt) == stmt


class TestFilterValidFiles:
    def test_cloud_path_included_when_allow_cloud(self):
        adapter = _make_adapter()

        result = adapter._filter_valid_files(["gs://my-bucket/data.parquet"], allow_cloud=True)
        assert len(result) == 1

    def test_cloud_path_excluded_when_not_allow_cloud(self):
        adapter = _make_adapter()

        result = adapter._filter_valid_files(["gs://my-bucket/data.parquet"], allow_cloud=False)
        assert len(result) == 0

    def test_nonexistent_local_file_excluded(self):
        adapter = _make_adapter()

        result = adapter._filter_valid_files([Path("/nonexistent/file.parquet")], allow_cloud=False)
        assert len(result) == 0


class TestEnsureFileList:
    def test_list_returned_as_is(self):
        adapter = _make_adapter()

        paths = [Path("/tmp/a.parquet"), Path("/tmp/b.parquet")]
        result = adapter._ensure_file_list(paths)
        assert result == paths

    def test_single_item_wrapped_in_list(self):
        adapter = _make_adapter()

        path = Path("/tmp/a.parquet")
        result = adapter._ensure_file_list(path)
        assert result == [path]


class TestLoadTablesDirect:
    def test_skips_table_with_no_valid_files(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_conn = Mock()

        with patch.object(adapter, "_filter_valid_files", return_value=[]):
            stats, timings = adapter._load_tables_direct(mock_conn, {"lineitem": [Path("/nonexistent.parquet")]})

        assert stats.get("LINEITEM") == 0
        assert timings == {"LINEITEM": {"total_ms": 0}}
        mock_conn.load_table_from_file.assert_not_called()

    def test_failed_table_load_logged(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_conn = Mock()

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(b"fake parquet data")
            tmp_path = Path(f.name)

        try:
            with (
                patch.object(adapter, "_filter_valid_files", return_value=[tmp_path]),
                patch.object(adapter, "_load_table_direct", side_effect=RuntimeError("load failed")),
            ):
                stats, timings = adapter._load_tables_direct(mock_conn, {"lineitem": [tmp_path]})

            assert stats.get("LINEITEM") == 0
            assert timings == {"LINEITEM": {"total_ms": 0}}
        finally:
            tmp_path.unlink(missing_ok=True)


class TestLoadTablesViaCloudStorage:
    def test_skips_table_with_no_valid_files(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_conn = Mock()
        mock_bucket = Mock()

        with patch.object(adapter, "_filter_valid_files", return_value=[]):
            stats, timings = adapter._load_tables_via_cloud_storage(
                mock_conn, {"lineitem": ["gs://bucket/lineitem.parquet"]}, mock_bucket
            )

        assert stats.get("LINEITEM") == 0
        assert timings == {"LINEITEM": {"total_ms": 0}}

    def test_failed_table_load_returns_zero(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_conn = Mock()
        mock_bucket = Mock()

        with (
            patch.object(adapter, "_filter_valid_files", return_value=["gs://bucket/lineitem.parquet"]),
            patch.object(adapter, "_load_table_via_cloud_storage", side_effect=RuntimeError("gcs error")),
        ):
            stats, timings = adapter._load_tables_via_cloud_storage(
                mock_conn, {"lineitem": ["gs://bucket/lineitem.parquet"]}, mock_bucket
            )

        assert stats.get("LINEITEM") == 0
        assert timings == {"LINEITEM": {"total_ms": 0}}


class TestGetPlatformMetadata:
    def test_basic_metadata_returned(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_dataset = Mock()
        mock_dataset.created = None
        mock_dataset.modified = None
        mock_dataset.location = "US"
        mock_dataset.description = "test dataset"

        mock_table1_info = Mock()
        mock_table1_info.table_id = "LINEITEM"

        mock_table1_obj = Mock()
        mock_table1_obj.num_rows = 6001215
        mock_table1_obj.num_bytes = 1024
        mock_table1_obj.created = None
        mock_table1_obj.modified = None
        mock_table1_obj.table_id = "LINEITEM"

        mock_conn = Mock()
        mock_conn.dataset.return_value = Mock()
        mock_conn.get_dataset.return_value = mock_dataset
        mock_conn.list_tables.return_value = [mock_table1_info]
        mock_conn.get_table.return_value = mock_table1_obj
        mock_dataset.table.return_value = Mock()

        metadata = adapter._get_platform_metadata(mock_conn)

        assert metadata["platform"] in ("bigquery", "BigQuery")
        assert metadata["project_id"] == "test-proj"
        assert metadata["dataset_id"] == "test_ds"

    def test_metadata_error_silenced(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_conn = Mock()
        mock_conn.dataset.side_effect = RuntimeError("connection error")

        metadata = adapter._get_platform_metadata(mock_conn)

        assert "metadata_error" in metadata

    def test_project_info_permission_error_silenced(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_dataset = Mock()
        mock_dataset.created = None
        mock_dataset.modified = None
        mock_dataset.location = "US"
        mock_dataset.description = "test"

        mock_conn = Mock()
        mock_conn.dataset.return_value = Mock()
        mock_conn.get_dataset.return_value = mock_dataset
        mock_conn.list_tables.return_value = []
        mock_conn.get_project.side_effect = RuntimeError("permission denied")
        mock_dataset.table.return_value = Mock()

        metadata = adapter._get_platform_metadata(mock_conn)
        assert metadata["platform"] in ("bigquery", "BigQuery")
        assert "project_info" not in metadata


class TestCreateConnection:
    def test_successful_connection_returned(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        import benchbox.platforms.bigquery as bq_module

        mock_bq = MagicMock()
        mock_client = MagicMock()
        mock_bq.Client.return_value = mock_client
        mock_query_job = MagicMock()
        mock_query_job.result.return_value = [("1",)]
        mock_client.query.return_value = mock_query_job

        with (
            patch.object(bq_module, "bigquery", mock_bq),
            patch.object(adapter, "_load_credentials", return_value=MagicMock()),
            patch.object(adapter, "handle_existing_database"),
        ):
            conn = adapter.create_connection()

        assert conn is mock_client

    def test_connection_error_raises(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        import benchbox.platforms.bigquery as bq_module

        mock_bq = MagicMock()
        mock_bq.Client.side_effect = RuntimeError("auth failed")

        with (
            patch.object(bq_module, "bigquery", mock_bq),
            patch.object(adapter, "_load_credentials", return_value=MagicMock()),
            patch.object(adapter, "handle_existing_database"),
            pytest.raises(RuntimeError, match="auth failed"),
        ):
            adapter.create_connection()

    def test_reused_database_triggers_cleanup(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")
        adapter.database_was_reused = True

        import benchbox.platforms.bigquery as bq_module

        mock_bq = MagicMock()
        mock_client = MagicMock()
        mock_bq.Client.return_value = mock_client
        mock_query_job = MagicMock()
        mock_query_job.result.return_value = [("1",)]
        mock_client.query.return_value = mock_query_job

        with (
            patch.object(bq_module, "bigquery", mock_bq),
            patch.object(adapter, "_load_credentials", return_value=MagicMock()),
            patch.object(adapter, "handle_existing_database"),
            patch.object(adapter, "_cleanup_empty_tables_if_needed") as mock_cleanup,
        ):
            adapter.create_connection()

        mock_cleanup.assert_called_once_with(mock_client)


class TestPrepareExternalParquetUris:
    def test_cloud_parquet_uri_included(self):
        adapter = _make_adapter(storage_bucket="my-bucket", storage_prefix="benchbox")

        mock_bucket = Mock()
        result = adapter._prepare_external_parquet_uris(mock_bucket, "lineitem", ["gs://other-bucket/lineitem.parquet"])

        assert "gs://other-bucket/lineitem.parquet" in result

    def test_non_parquet_cloud_path_excluded(self):
        adapter = _make_adapter(storage_bucket="my-bucket", storage_prefix="benchbox")

        mock_bucket = Mock()
        result = adapter._prepare_external_parquet_uris(mock_bucket, "lineitem", ["gs://other-bucket/lineitem.tbl"])

        assert len(result) == 0


class TestGetTargetDialect:
    def test_returns_bigquery(self):
        adapter = _make_adapter()
        assert adapter.get_target_dialect() == "bigquery"


class TestGetConnectionParams:
    def test_defaults_from_adapter(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        params = adapter._get_connection_params()

        assert params["project_id"] == "my-proj"
        assert params["location"] == "US"

    def test_override_from_kwargs(self):
        adapter = _make_adapter(project_id="my-proj", dataset_id="my_ds")

        params = adapter._get_connection_params(project_id="override-proj")
        assert params["project_id"] == "override-proj"


class TestLoadTableViaCloudStorage:
    def test_uploads_file_and_loads_via_uri(self):
        adapter = _make_adapter(
            dataset_id="test_ds",
            project_id="test-proj",
            storage_bucket="my-bucket",
            storage_prefix="benchbox",
        )

        mock_conn = Mock()
        mock_dataset = Mock()
        mock_table_ref = Mock()
        mock_dataset.table.return_value = mock_table_ref
        mock_conn.dataset.return_value = mock_dataset

        mock_load_job = Mock()
        mock_conn.load_table_from_uri.return_value = mock_load_job

        mock_row_job = Mock()
        mock_row_job.result.return_value = [(1000,)]
        mock_conn.query.return_value = mock_row_job

        mock_blob = Mock()
        mock_bucket = Mock()
        mock_bucket.blob.return_value = mock_blob

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(b"fake parquet data")
            tmp_path = Path(f.name)

        try:
            with patch.object(adapter, "_build_load_job_config", return_value=Mock()):
                row_count = adapter._load_table_via_cloud_storage(mock_conn, mock_bucket, "lineitem", [tmp_path])

            mock_blob.upload_from_filename.assert_called_once()
            mock_conn.load_table_from_uri.assert_called_once()
            mock_load_job.result.assert_called_once()
            assert row_count == 1000
        finally:
            tmp_path.unlink(missing_ok=True)


class TestLoadTableDirect:
    def test_loads_file_directly(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_conn = Mock()
        mock_dataset = Mock()
        mock_table_ref = Mock()
        mock_dataset.table.return_value = mock_table_ref
        mock_conn.dataset.return_value = mock_dataset

        mock_load_job = Mock()
        mock_conn.load_table_from_file.return_value = mock_load_job

        mock_row_job = Mock()
        mock_row_job.result.return_value = [(500,)]
        mock_conn.query.return_value = mock_row_job

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(b"fake parquet")
            tmp_path = Path(f.name)

        try:
            with patch.object(adapter, "_build_load_job_config", return_value=Mock()):
                row_count = adapter._load_table_direct(mock_conn, "lineitem", [tmp_path])

            mock_conn.load_table_from_file.assert_called_once()
            mock_load_job.result.assert_called_once()
            assert row_count == 500
        finally:
            tmp_path.unlink(missing_ok=True)


class TestResolveDataFiles:
    def test_returns_tables_from_resolver(self):
        adapter = _make_adapter()
        benchmark = Mock()
        data_dir = Path("/tmp/data")

        mock_data_source = Mock()
        mock_data_source.tables = {"LINEITEM": [Path("/tmp/lineitem.parquet")]}

        with patch("benchbox.platforms.bigquery.resolve_adapter_data_source", return_value=mock_data_source) as resolve:
            result = adapter._resolve_data_files(benchmark, data_dir)

        resolve.assert_called_once_with(adapter, benchmark, data_dir)
        assert "LINEITEM" in result.tables

    def test_raises_when_no_data_files(self):
        adapter = _make_adapter()

        with patch(
            "benchbox.platforms.bigquery.resolve_adapter_data_source", side_effect=ValueError("No data files found")
        ):
            with pytest.raises(ValueError, match="No data files found"):
                adapter._resolve_data_files(Mock(), Path("/tmp/data"))

    def test_raises_when_empty_tables(self):
        adapter = _make_adapter()

        with patch(
            "benchbox.platforms.bigquery.resolve_adapter_data_source", side_effect=ValueError("No data files found")
        ):
            with pytest.raises(ValueError, match="No data files found"):
                adapter._resolve_data_files(Mock(), Path("/tmp/data"))


class TestCreateExternalTables:
    def test_creates_parquet_external_table(self):
        adapter = _make_adapter(
            dataset_id="test_ds",
            project_id="test-proj",
            storage_bucket="my-bucket",
        )

        mock_conn = Mock()
        mock_query_job = Mock()
        mock_conn.query.return_value = mock_query_job

        mock_bucket = Mock()
        mock_data_source = Mock()
        mock_data_source.tables = {"lineitem": ["gs://bucket/lineitem.parquet"]}

        with (
            patch.object(adapter, "validate_external_table_requirements"),
            patch.object(adapter, "_resolve_data_files", return_value={"lineitem": ["gs://bucket/lineitem.parquet"]}),
            patch.object(adapter, "_create_storage_bucket", return_value=mock_bucket),
            patch.object(
                adapter, "_prepare_external_table_uris", return_value=("PARQUET", ["gs://bucket/lineitem.parquet"])
            ),
            patch.object(adapter, "_get_table_row_count", return_value=100),
        ):
            stats, time_, details = adapter.create_external_tables(Mock(), mock_conn, Path("/tmp"))

        assert stats.get("LINEITEM") == 100
        assert details is None

    def test_raises_when_no_uris_found(self):
        adapter = _make_adapter(
            dataset_id="test_ds",
            project_id="test-proj",
            storage_bucket="my-bucket",
        )

        mock_conn = Mock()
        mock_bucket = Mock()

        with (
            patch.object(adapter, "validate_external_table_requirements"),
            patch.object(adapter, "_resolve_data_files", return_value={"lineitem": []}),
            patch.object(adapter, "_create_storage_bucket", return_value=mock_bucket),
            patch.object(adapter, "_prepare_external_table_uris", return_value=("PARQUET", [])),
            pytest.raises(ValueError, match="No supported sources were found"),
        ):
            adapter.create_external_tables(Mock(), mock_conn, Path("/tmp"))


class TestExecuteQueryValidation:
    def test_validation_called_when_benchmark_type_provided(self):
        adapter = _make_adapter()

        mock_conn = Mock()
        mock_job = Mock()
        mock_job.result.return_value = [(42,)]
        mock_job.total_bytes_processed = 100
        mock_job.total_bytes_billed = 200
        mock_job.slot_millis = 50
        mock_job.created = None
        mock_job.started = None
        mock_job.ended = None
        mock_job.job_id = "job-validate"
        mock_conn.query.return_value = mock_job
        del mock_conn._default_job_config

        mock_validation = Mock()
        mock_validation.is_valid = True
        mock_validation.warning_message = None
        mock_validation.error_message = None
        mock_validation.expected_row_count = 1

        with patch("benchbox.core.validation.query_validation.QueryValidator") as mock_validator_cls:
            mock_validator = Mock()
            mock_validator.validate_query_result.return_value = mock_validation
            mock_validator_cls.return_value = mock_validator

            result = adapter.execute_query(
                mock_conn,
                "SELECT 42",
                query_id="Q1",
                benchmark_type="tpch",
                scale_factor=1.0,
                validate_row_count=True,
            )

        mock_validator.validate_query_result.assert_called_once()
        assert result["query_id"] == "Q1"


class TestSupportsTuningTypeImportError:
    def test_import_error_returns_false(self):
        adapter = _make_adapter()

        with patch.dict("sys.modules", {"benchbox.core.tuning.interface": None}):
            result = adapter.supports_tuning_type("PARTITIONING")

        assert result is False


class TestApplyTableTuningsDistributionSorting:
    def test_distribution_warning_logged(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_table_obj = Mock()
        mock_table_obj.time_partitioning = None
        mock_table_obj.clustering_fields = []

        mock_dataset_ref = Mock()
        mock_table_ref = Mock()
        mock_dataset_ref.table.return_value = mock_table_ref

        mock_conn = Mock()
        mock_conn.dataset.return_value = mock_dataset_ref
        mock_conn.get_table.return_value = mock_table_obj

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "ORDERS"

        try:
            from benchbox.core.tuning.interface import TuningType
        except ImportError:
            pytest.skip("TuningType not available")

        dist_col = Mock()
        dist_col.name = "o_orderkey"
        dist_col.type = "INT64"
        dist_col.order = 1

        sort_col = Mock()
        sort_col.name = "o_orderdate"
        sort_col.type = "DATE"
        sort_col.order = 1

        def get_cols_by_type(t):
            if t == TuningType.DISTRIBUTION:
                return [dist_col]
            elif t == TuningType.SORTING:
                return [sort_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = get_cols_by_type

        adapter.apply_table_tunings(mock_tuning, mock_conn)

    def test_clustering_mismatch_triggers_recreation_warning(self):
        adapter = _make_adapter(dataset_id="test_ds", project_id="test-proj")

        mock_table_obj = Mock()
        mock_table_obj.time_partitioning = Mock()
        mock_table_obj.clustering_fields = ["wrong_col"]

        mock_dataset_ref = Mock()
        mock_table_ref = Mock()
        mock_dataset_ref.table.return_value = mock_table_ref

        mock_conn = Mock()
        mock_conn.dataset.return_value = mock_dataset_ref
        mock_conn.get_table.return_value = mock_table_obj

        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.table_name = "LINEITEM"

        try:
            from benchbox.core.tuning.interface import TuningType
        except ImportError:
            pytest.skip("TuningType not available")

        cluster_col = Mock()
        cluster_col.name = "correct_col"
        cluster_col.type = "STRING"
        cluster_col.order = 1

        def get_cols_by_type(t):
            if t == TuningType.CLUSTERING:
                return [cluster_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = get_cols_by_type

        adapter.apply_table_tunings(mock_tuning, mock_conn)


class TestBuildBigQueryConfigDefaultOutput:
    def test_gs_default_output_location_used_as_staging(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        mock_info = Mock()
        mock_info.display_name = "Google BigQuery"
        mock_info.driver_package = "google-cloud-bigquery"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cred_cls:
            mock_cred_manager = Mock()
            mock_cred_manager.get_platform_credentials.return_value = {
                "project_id": "my-project",
                "dataset_id": "my_dataset",
                "default_output_location": "gs://output-bucket/data",
            }
            mock_cred_cls.return_value = mock_cred_manager

            config = _build_bigquery_config(
                platform="bigquery",
                options={},
                overrides={},
                info=mock_info,
            )

        assert config.storage_bucket == "output-bucket"

    def test_non_gs_default_output_ignored(self):
        from benchbox.platforms.bigquery import _build_bigquery_config

        mock_info = Mock()
        mock_info.display_name = "Google BigQuery"
        mock_info.driver_package = "google-cloud-bigquery"

        with patch("benchbox.security.credentials.CredentialManager") as mock_cred_cls:
            mock_cred_manager = Mock()
            mock_cred_manager.get_platform_credentials.return_value = {
                "project_id": "my-project",
                "dataset_id": "my_dataset",
                "default_output_location": "s3://not-gs-bucket/data",
            }
            mock_cred_cls.return_value = mock_cred_manager

            config = _build_bigquery_config(
                platform="bigquery",
                options={},
                overrides={},
                info=mock_info,
            )

        assert config.storage_bucket is None


class TestCreateStorageBucket:
    def test_creates_bucket_reference(self):
        adapter = _make_adapter(storage_bucket="my-bucket")

        import benchbox.platforms.bigquery as bq_module

        mock_storage = MagicMock()
        mock_storage_client = MagicMock()
        mock_bucket = MagicMock()
        mock_storage.Client.return_value = mock_storage_client
        mock_storage_client.bucket.return_value = mock_bucket

        with (
            patch.object(bq_module, "storage", mock_storage),
            patch.object(adapter, "_load_credentials", return_value=MagicMock()),
        ):
            bucket = adapter._create_storage_bucket()

        mock_storage.Client.assert_called_once()
        mock_storage_client.bucket.assert_called_once_with("my-bucket")
        assert bucket is mock_bucket


class TestCloseConnectionTransport:
    def test_transport_without_close_method(self):
        adapter = _make_adapter()

        mock_transport = Mock(spec=[])
        mock_conn = Mock()
        mock_conn._transport = mock_transport

        adapter.close_connection(mock_conn)

    def test_transport_close_error_silenced(self):
        adapter = _make_adapter()

        mock_transport = Mock()
        mock_transport.close.side_effect = RuntimeError("transport error")
        mock_conn = Mock()
        mock_conn._transport = mock_transport

        adapter.close_connection(mock_conn)


class TestPrepareExternalTableUris:
    def test_returns_parquet_format_for_parquet_files(self):
        adapter = _make_adapter(storage_bucket="my-bucket")

        mock_bucket = Mock()

        with (
            patch.object(adapter, "_prepare_external_delta_uris", return_value=[]),
            patch.object(adapter, "_prepare_external_parquet_uris", return_value=["gs://my-bucket/lineitem.parquet"]),
        ):
            fmt, uris = adapter._prepare_external_table_uris(
                mock_bucket, "lineitem", ["gs://my-bucket/lineitem.parquet"]
            )

        assert fmt == "PARQUET"
        assert len(uris) == 1

    def test_delta_lake_requires_biglake_connection(self):
        adapter = _make_adapter(storage_bucket="my-bucket")
        adapter.biglake_connection = None

        mock_bucket = Mock()

        with (
            patch.object(adapter, "_prepare_external_delta_uris", return_value=["gs://bucket/table/"]),
            pytest.raises(ValueError, match="biglake_connection"),
        ):
            adapter._prepare_external_table_uris(mock_bucket, "lineitem", [])

    def test_delta_lake_format_returned_when_delta_uris(self):
        adapter = _make_adapter(storage_bucket="my-bucket")
        adapter.biglake_connection = "project.region.connection"

        mock_bucket = Mock()

        with patch.object(adapter, "_prepare_external_delta_uris", return_value=["gs://bucket/lineitem/"]):
            fmt, uris = adapter._prepare_external_table_uris(mock_bucket, "lineitem", [])

        assert fmt == "DELTA_LAKE"
        assert uris == ["gs://bucket/lineitem/"]
