from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from benchbox.platforms.dataframe.benchmark_mixin import _client_link_block_for_dataframe

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_explicit_region_yields_partial_block() -> None:
    config = SimpleNamespace(client_region="eu-west-1", client_cloud="aws")
    with patch(
        "benchbox.platforms.dataframe.benchmark_mixin.discover_client_region",
        return_value={"client_region": "eu-west-1", "client_cloud": "aws", "source": "cli_option"},
    ) as mock_discover:
        block = _client_link_block_for_dataframe(config, {})

    mock_discover.assert_called_once()
    assert block == {
        "collection_status": "partial",
        "source": "cli_option",
        "client_region": "eu-west-1",
        "client_cloud": "aws",
        "statement_overhead_ms": None,
        "collection_error_class": None,
        "collection_error_message": None,
    }


def test_unknown_locality_yields_no_block() -> None:
    config = SimpleNamespace(client_region=None, client_cloud=None)
    with patch(
        "benchbox.platforms.dataframe.benchmark_mixin.discover_client_region",
        return_value={"client_region": None, "client_cloud": None, "source": "unavailable"},
    ):
        assert _client_link_block_for_dataframe(config, {}) is None


def test_options_map_carries_cli_overrides() -> None:
    config = SimpleNamespace(client_region=None, client_cloud=None)
    options = {"client_region": "us-east-1", "client_cloud": "aws"}
    with patch(
        "benchbox.platforms.dataframe.benchmark_mixin.discover_client_region",
        return_value={"client_region": "us-east-1", "client_cloud": "aws", "source": "cli_option"},
    ) as mock_discover:
        block = _client_link_block_for_dataframe(config, options)

    assert mock_discover.call_args[0][0]["client_region"] == "us-east-1"
    assert block is not None
    assert block["collection_status"] == "partial"


