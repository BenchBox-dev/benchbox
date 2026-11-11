from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.gcp import DataprocServerlessAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _adapter() -> DataprocServerlessAdapter:
    with (
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.GOOGLE_CLOUD_AVAILABLE", True),
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.dataproc_v1", MagicMock()),
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.storage", MagicMock()),
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.CloudSparkStaging") as mock_staging,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        return DataprocServerlessAdapter(project_id="proj", gcs_staging_dir="gs://bucket/path")


def test_client_helpers_cache_clients() -> None:
    adapter = _adapter()

    batch_client = MagicMock()
    storage_client = MagicMock()

    with (
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.dataproc_v1") as mock_dp,
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.storage") as mock_storage,
    ):
        mock_dp.BatchControllerClient.return_value = batch_client
        mock_storage.Client.return_value = storage_client

        assert adapter._get_batch_client() is adapter._get_batch_client()
        assert adapter._get_storage_client() is adapter._get_storage_client()


def test_create_connection_wraps_batch_client_errors() -> None:
    adapter = _adapter()
    bad_client = MagicMock()
    bad_client.list_batches.side_effect = RuntimeError("permission denied")

    with patch.object(adapter, "_get_batch_client", return_value=bad_client):
        with pytest.raises(ConfigurationError, match="Failed to connect"):
            adapter.create_connection()


def test_apply_tuning_configuration_collects_results() -> None:
    adapter = _adapter()
    adapter.apply_primary_keys = MagicMock(return_value={"pk": 1})
    adapter.apply_foreign_keys = MagicMock(return_value={"fk": 1})
    adapter.apply_platform_optimizations = MagicMock(return_value={"opt": 1})

    config = SimpleNamespace(
        scale_factor=3.0,
        primary_keys=["a"],
        foreign_keys=["b"],
        platform_optimizations={"x": 1},
    )
    result = adapter.apply_tuning_configuration(config)

    assert adapter._scale_factor == 3.0
    assert result["primary_keys"] == {"pk": 1}
    assert result["foreign_keys"] == {"fk": 1}
    assert result["platform_optimizations"] == {"opt": 1}


