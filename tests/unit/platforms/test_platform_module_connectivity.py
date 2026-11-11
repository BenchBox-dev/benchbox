from unittest.mock import MagicMock, patch

import pytest

from benchbox.platforms import check_platform_connectivity

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_check_platform_connectivity_returns_adapter_result():
    adapter = MagicMock()
    adapter.test_connection.return_value = True

    with patch("benchbox.platforms.get_platform_adapter", return_value=adapter) as factory:
        assert check_platform_connectivity("duckdb", database_path="test.duckdb") is True
        factory.assert_called_once_with("duckdb", database_path="test.duckdb")
        adapter.test_connection.assert_called_once_with()


def test_check_platform_connectivity_handles_exceptions():
    with patch("benchbox.platforms.get_platform_adapter", side_effect=RuntimeError("boom")):
        assert check_platform_connectivity("duckdb") is False


