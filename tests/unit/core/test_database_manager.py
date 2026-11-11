from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.databases import manager as db_manager
from benchbox.core.schemas import DatabaseConfig, SystemProfile

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_core_check_connection_delegates_to_adapter():
    cfg = DatabaseConfig(type="duckdb", name="test", options={})
    sysprof = SystemProfile(
        os_name="Linux",
        os_version="6.6",
        architecture="x86_64",
        cpu_model="Test CPU",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        memory_total_gb=16.0,
        memory_available_gb=12.0,
        python_version="3.11",
        disk_space_gb=256.0,
        timestamp=datetime.now(),
        hostname="host",
    )

    with (
        patch(
            "benchbox.core.databases.manager.get_platform_config", return_value={"database_path": "test.duckdb"}
        ) as mock_cfg,
        patch("benchbox.core.databases.manager.get_platform_adapter") as mock_factory,
    ):
        adapter = MagicMock()
        adapter.test_connection.return_value = True
        mock_factory.return_value = adapter

        ok = db_manager.check_connection(cfg, sysprof)

        assert ok is True
        mock_cfg.assert_called_once()
        mock_factory.assert_called_once()
        adapter.test_connection.assert_called_once()


