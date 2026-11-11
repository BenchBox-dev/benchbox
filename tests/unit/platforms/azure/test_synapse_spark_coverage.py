from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.azure import SynapseSparkAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _adapter() -> SynapseSparkAdapter:
    with (
        patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
        patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
        patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
        patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging") as mock_staging,
    ):
        mock_staging.from_uri.return_value = MagicMock()
        return SynapseSparkAdapter(
            workspace_name="ws",
            spark_pool_name="pool",
            storage_account="acct",
            storage_container="container",
        )


def _benchmark(*tables: str) -> SimpleNamespace:
    return SimpleNamespace(get_table_names=lambda: list(tables))


def test_get_access_token_refreshes_and_reuses_cached_token() -> None:
    adapter = _adapter()
    token = SimpleNamespace(token="abc", expires_on=9999999999)
    cred = MagicMock()
    cred.get_token.return_value = token

    adapter._token_provider._credential = cred

    assert adapter._get_access_token() == "abc"
    assert adapter._get_access_token() == "abc"

    cred.get_token.assert_called_once_with("https://dev.azuresynapse.net/.default")


def test_get_headers_includes_bearer_token() -> None:
    adapter = _adapter()

    with patch.object(adapter._token_provider, "access_token", return_value="token123"):
        headers = adapter._get_headers()

    assert headers["Authorization"] == "Bearer token123"
    assert headers["Content-Type"] == "application/json"


def test_apply_unified_tuning_calls_platform_tuning_when_present() -> None:
    adapter = _adapter()
    adapter.apply_platform_tuning = MagicMock()

    adapter.apply_unified_tuning(
        SimpleNamespace(platform_optimization={"spark.sql.shuffle.partitions": "4"}),
        None,
    )

    call_args = adapter.apply_platform_tuning.call_args
    assert call_args is not None, "apply_platform_tuning should have been called"
    assert "spark.sql.shuffle.partitions" in str(call_args), "Should pass Spark config"


def test_create_schema_default_and_non_default_paths() -> None:
    adapter = _adapter()
    adapter._execute_statement = MagicMock()

    adapter.create_schema(_benchmark(), None)
    adapter.database = "benchbox"
    adapter.create_schema(_benchmark(), None)

    call_args = adapter._execute_statement.call_args
    assert call_args is not None, "_execute_statement should have been called"
    assert "benchbox" in str(call_args).lower(), "Should reference 'benchbox' schema"


def test_load_data_validates_source_dir_and_builds_table_uris(tmp_path: Path) -> None:
    adapter = _adapter()
    adapter._staging = MagicMock()
    adapter._staging.tables_exist.return_value = False
    adapter._execute_statement = MagicMock(side_effect=[None, None])

    with pytest.raises(ConfigurationError, match="Source directory not found"):
        adapter.load_data(_benchmark("lineitem"), None, tmp_path / "missing")

    (tmp_path / "lineitem.parquet").write_text("data", encoding="utf-8")
    stats, _, metadata = adapter.load_data(_benchmark("lineitem", "orders"), None, tmp_path)

    assert "lineitem" in stats
    assert metadata is not None
    assert metadata["table_uris"]["orders"].endswith("/tables/orders")


def test_execute_query_shapes_result_payload() -> None:
    adapter = _adapter()
    adapter._execute_statement_measured = MagicMock(
        return_value=(
            {
                "data": {
                    "schema": {"fields": [{"name": "c1"}, {"name": "c2"}]},
                    "values": [[1, "a"], [2, "b"]],
                }
            },
            0.25,
        )
    )

    output = adapter.execute_query(None, "SELECT * FROM t", "q1")

    assert output["status"] == "SUCCESS"
    assert output["rows_returned"] == 2
    assert output["columns"] == ["c1", "c2"]
    assert output["execution_time_seconds"] == 0.25
    assert output["request_count"] == 0


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (401, "Authentication failed"),
        (403, "Access denied"),
        (404, "not found"),
        (500, "Failed to access Synapse"),
    ],
)
def test_create_connection_maps_http_failures(status_code: int, message: str) -> None:
    adapter = _adapter()
    response = MagicMock()
    response.status_code = status_code
    response.text = "error"
    response.json.return_value = {}

    with (
        patch.object(adapter, "_get_access_token", return_value="token"),
        patch.object(adapter, "_get_headers", return_value={"Authorization": "Bearer token"}),
        patch("benchbox.platforms.azure.synapse_spark_adapter.requests.get", return_value=response),
    ):
        with pytest.raises(ConfigurationError, match=message):
            adapter.create_connection()


def test_close_clears_session_id_when_delete_raises(caplog) -> None:
    adapter = _adapter()
    adapter._session_id = 7
    adapter._session_created_by_us = True

    with (
        patch.object(adapter, "_get_headers", return_value={"Authorization": "Bearer token"}),
        patch("benchbox.platforms.azure.synapse_spark_adapter.requests.delete", side_effect=RuntimeError("boom")),
        caplog.at_level("WARNING", logger="benchbox.platforms.azure.synapse_spark_adapter"),
    ):
        adapter.close()

    assert adapter._session_id is None
    assert any("Failed to close session" in r.getMessage() for r in caplog.records)


