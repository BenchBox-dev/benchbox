from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms.base.adapter import PlatformAdapter
from benchbox.platforms.base.runtime_metadata import (
    build_default_normalized_result_metadata,
    collect_normalized_result_metadata,
)
from benchbox.platforms.databricks.adapter import DatabricksAdapter
from benchbox.platforms.firebolt import FireboltAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _DummyAdapter(PlatformAdapter):
    platform_name = "dummy"

    def __init__(self, **config) -> None:
        super().__init__(**config)

    @staticmethod
    def add_cli_arguments(parser) -> None:
        pass

    @classmethod
    def from_config(cls, config):
        return cls(**config)

    def get_target_dialect(self) -> str:
        return "duckdb"

    def create_connection(self, **kwargs):
        return None

    def apply_constraint_configuration(self, *args, **kwargs):
        pass

    def apply_platform_optimizations(self, *args, **kwargs):
        pass

    def configure_for_benchmark(self, *args, **kwargs):
        pass

    def create_schema(self, *args, **kwargs):
        pass

    def execute_query(self, *args, **kwargs):
        pass

    def load_data(self, *args, **kwargs):
        pass


def test_reset_run_scoped_state_clears_client_link_metadata() -> None:
    adapter = _DummyAdapter()
    adapter._client_link_metadata = {
        "collection_status": "available",
        "source": "observed",
        "client_region": "us-east-1",
    }
    adapter._reset_run_scoped_state()
    assert adapter._client_link_metadata is None


def test_collect_client_link_metadata_with_probe_and_region() -> None:
    adapter = _DummyAdapter()
    conn = MagicMock()

    with (
        patch(
            "benchbox.platforms.base.adapter.probe_statement_overhead",
            return_value={
                "collection_status": "available",
                "source": "observed",
                "statement_overhead_ms": {"samples": 5, "min": 1.2, "median": 1.5},
            },
        ),
        patch(
            "benchbox.platforms.base.adapter.discover_client_region",
            return_value={
                "client_region": "us-east-1",
                "client_cloud": "aws",
                "source": "observed",
            },
        ),
    ):
        adapter._collect_client_link_metadata(conn, {"link_probe": True})

    probed_at = adapter._client_link_metadata.pop("probed_at")
    assert datetime.fromisoformat(probed_at).tzinfo is not None
    assert adapter._client_link_metadata == {
        "collection_status": "available",
        "source": "observed",
        "client_region": "us-east-1",
        "client_cloud": "aws",
        "statement_overhead_ms": {"samples": 5, "min": 1.2, "median": 1.5},
        "collection_error_class": None,
        "collection_error_message": None,
    }


def test_collect_client_link_metadata_link_probe_disabled() -> None:
    adapter = _DummyAdapter()
    conn = MagicMock()

    with (
        patch("benchbox.platforms.base.adapter.probe_statement_overhead") as mock_probe,
        patch(
            "benchbox.platforms.base.adapter.discover_client_region",
            return_value={
                "client_region": "us-west-2",
                "client_cloud": "aws",
                "source": "cli_option",
            },
        ),
    ):
        adapter._collect_client_link_metadata(conn, {"link_probe": False, "client_region": "us-west-2"})
        mock_probe.assert_not_called()

    assert adapter._client_link_metadata == {
        "collection_status": "partial",
        "source": "cli_option",
        "client_region": "us-west-2",
        "client_cloud": "aws",
        "statement_overhead_ms": None,
        "collection_error_class": None,
        "collection_error_message": None,
    }


def test_build_default_normalized_result_metadata_merges_client_link() -> None:
    adapter = _DummyAdapter()
    adapter._client_link_metadata = {
        "collection_status": "available",
        "source": "observed",
        "client_region": "us-east-1",
        "client_cloud": "aws",
        "statement_overhead_ms": {"samples": 5, "min": 2.1, "median": 2.3},
    }

    metadata = build_default_normalized_result_metadata(adapter)
    exec_env = metadata.get("execution_environment", {})
    assert "client_link" in exec_env
    assert exec_env["client_link"]["client_region"] == "us-east-1"
    assert exec_env["client_link"]["statement_overhead_ms"]["median"] == 2.3


def test_collect_normalized_result_metadata_merges_client_link() -> None:
    adapter = _DummyAdapter()
    adapter._client_link_metadata = {
        "collection_status": "available",
        "source": "cli_option",
        "client_region": "eu-central-1",
        "client_cloud": "aws",
    }

    metadata = collect_normalized_result_metadata(adapter)
    exec_env = metadata.get("execution_environment", {})
    assert "client_link" in exec_env
    assert exec_env["client_link"]["client_region"] == "eu-central-1"
    assert exec_env["client_link"]["source"] == "cli_option"


def test_firebolt_adapter_preserves_client_link_metadata() -> None:
    adapter = FireboltAdapter(url="http://localhost:8123")
    adapter._client_link_metadata = {
        "collection_status": "available",
        "source": "observed",
        "client_region": "us-east-1",
        "client_cloud": "aws",
        "statement_overhead_ms": {"samples": 5, "min": 1.1, "median": 1.4},
    }

    metadata = adapter.get_normalized_result_metadata()
    assert "execution_environment" in metadata
    assert "client_link" in metadata["execution_environment"]
    assert metadata["execution_environment"]["client_link"]["client_region"] == "us-east-1"


def test_databricks_adapter_state_reset_and_metadata_inheritance() -> None:
    adapter = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/warehouses/w123",
        access_token="test-token",
    )
    adapter._client_link_metadata = {
        "collection_status": "available",
        "source": "observed",
        "client_region": "us-east-1",
        "client_cloud": "aws",
        "statement_overhead_ms": {"samples": 5, "min": 1.5, "median": 1.8},
    }

    metadata = adapter.get_normalized_result_metadata(platform_info={})
    assert "execution_environment" in metadata
    assert "client_link" in metadata["execution_environment"]
    assert metadata["execution_environment"]["client_link"]["client_region"] == "us-east-1"

    adapter._reset_run_scoped_state()
    assert adapter._client_link_metadata is None


def test_collect_client_link_metadata_dry_run_skips_probe() -> None:
    adapter = _DummyAdapter()
    adapter.dry_run_mode = True
    conn = MagicMock()

    with (
        patch("benchbox.platforms.base.adapter.probe_statement_overhead") as mock_probe,
        patch(
            "benchbox.platforms.base.adapter.discover_client_region",
            return_value={
                "client_region": None,
                "client_cloud": None,
                "source": "unavailable",
            },
        ),
    ):
        adapter._collect_client_link_metadata(conn, {"link_probe": True})
        mock_probe.assert_not_called()

    assert adapter._client_link_metadata is not None
    assert adapter._client_link_metadata["collection_status"] == "not_requested"
    assert adapter._client_link_metadata["statement_overhead_ms"] is None


def test_collect_client_link_metadata_surprise_error_degrades() -> None:
    adapter = _DummyAdapter()
    conn = MagicMock()

    with (
        patch("benchbox.platforms.base.adapter.probe_statement_overhead") as mock_probe,
        patch(
            "benchbox.platforms.base.adapter.discover_client_region",
            side_effect=RuntimeError("IMDS cache poisoned"),
        ),
    ):
        adapter._collect_client_link_metadata(conn, {"link_probe": True})
        mock_probe.assert_called_once()

    assert adapter._client_link_metadata is not None
    assert adapter._client_link_metadata["collection_status"] == "unavailable"
    assert adapter._client_link_metadata["collection_error_class"] == "RuntimeError"


def test_collect_client_link_metadata_failed_probe_without_region_is_unavailable() -> None:
    adapter = _DummyAdapter()
    conn = MagicMock()

    with (
        patch(
            "benchbox.platforms.base.adapter.probe_statement_overhead",
            return_value={
                "collection_status": "partial",
                "source": "unavailable",
                "collection_error_class": "ConnectionError",
                "collection_error_message": "ConnectionError: statement overhead probe failed",
            },
        ),
        patch(
            "benchbox.platforms.base.adapter.discover_client_region",
            return_value={
                "client_region": None,
                "client_cloud": None,
                "source": "unavailable",
            },
        ),
    ):
        adapter._collect_client_link_metadata(conn, {"link_probe": True})

    assert adapter._client_link_metadata is not None
    assert adapter._client_link_metadata["collection_status"] == "unavailable"
    assert adapter._client_link_metadata["statement_overhead_ms"] is None
    assert adapter._link_probe_timed_out is False


def test_collect_client_link_metadata_timeout_marks_connection_tainted() -> None:
    adapter = _DummyAdapter()
    conn = MagicMock()

    with (
        patch(
            "benchbox.platforms.base.adapter.probe_statement_overhead",
            return_value={
                "collection_status": "partial",
                "source": "unavailable",
                "collection_error_class": "TimeoutError",
                "collection_error_message": "TimeoutError: statement overhead probe failed",
            },
        ),
        patch(
            "benchbox.platforms.base.adapter.discover_client_region",
            return_value={
                "client_region": "us-east-1",
                "client_cloud": "aws",
                "source": "observed",
            },
        ),
    ):
        adapter._collect_client_link_metadata(conn, {"link_probe": True})

    assert adapter._link_probe_timed_out is True
    degraded = adapter._client_link_only_metadata()
    assert degraded["execution_environment"]["client_link"]["client_region"] == "us-east-1"

    adapter._reset_run_scoped_state()
    assert adapter._link_probe_timed_out is False
    assert adapter._client_link_only_metadata() == {}


def test_collect_platform_metadata_skips_tainted_connection() -> None:
    adapter = _DummyAdapter()
    adapter._link_probe_timed_out = True
    adapter._client_link_metadata = {
        "collection_status": "partial",
        "source": "observed",
        "client_region": "us-east-1",
        "client_cloud": "aws",
    }

    with patch.object(
        _DummyAdapter,
        "get_platform_info",
        side_effect=AssertionError("tainted connection must not be reused"),
    ):
        platform_info, normalized = adapter._collect_platform_metadata(MagicMock())

    assert platform_info == {}
    assert normalized["execution_environment"]["client_link"]["client_region"] == "us-east-1"


def test_exclude_probe_wall_time() -> None:
    from benchbox.platforms.base.adapter import exclude_probe_wall_time

    assert exclude_probe_wall_time(10.0, 2.5) == 7.5
    assert exclude_probe_wall_time(1.0, 5.0) == 0.0
    assert exclude_probe_wall_time(0.0, 0.0) == 0.0


def test_collect_client_link_metadata_string_flag_forms() -> None:
    adapter = _DummyAdapter()
    conn = MagicMock()

    with (
        patch("benchbox.platforms.base.adapter.probe_statement_overhead") as mock_probe,
        patch(
            "benchbox.platforms.base.adapter.discover_client_region",
            return_value={
                "client_region": None,
                "client_cloud": None,
                "source": "unavailable",
            },
        ),
    ):
        adapter._collect_client_link_metadata(conn, {"link_probe": "false"})
        mock_probe.assert_not_called()
        assert adapter._client_link_metadata is not None
        assert adapter._client_link_metadata["collection_status"] == "not_requested"

        adapter._collect_client_link_metadata(conn, {"link_probe": "0"})
        mock_probe.assert_not_called()
