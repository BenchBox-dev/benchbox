from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.results.driver_metadata import apply_driver_metadata

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_apply_driver_metadata_prefers_adapter_values() -> None:
    result = SimpleNamespace(
        driver_package=None,
        driver_version_requested=None,
        driver_version_resolved=None,
        driver_version_actual=None,
        driver_runtime_strategy=None,
        driver_runtime_path=None,
        driver_runtime_python_executable=None,
        driver_auto_install=False,
        execution_metadata={},
        platform_info={"driver_runtime_path": "preserved"},
    )

    db_config = SimpleNamespace(
        driver_package="duckdb",
        driver_version="1.0.0",
        driver_version_resolved=None,
        driver_version_actual=None,
        driver_runtime_strategy=None,
        driver_runtime_path=None,
        driver_runtime_python_executable=None,
        driver_auto_install=True,
    )
    adapter = SimpleNamespace(
        driver_package="duckdb",
        driver_version_requested="1.1.0",
        driver_version_resolved="1.1.0",
        driver_version_actual="1.1.0",
        driver_runtime_strategy="isolated-site-packages",
        driver_runtime_path="/tmp/runtime",
        driver_runtime_python_executable="/tmp/runtime/bin/python",
        driver_auto_install_used=True,
    )

    apply_driver_metadata(result, database_config=db_config, platform_adapter=adapter)

    assert result.driver_version_resolved == "1.1.0"
    assert result.driver_runtime_strategy == "isolated-site-packages"
    assert result.execution_metadata["driver_version_resolved"] == "1.1.0"
    assert result.execution_metadata["driver_auto_install_used"] is True
    assert result.platform_info["driver_package"] == "duckdb"
    assert result.platform_info["driver_runtime_path"] == "preserved"
    assert result.platform_info["driver_runtime_python_executable"] == "/tmp/runtime/bin/python"


def test_apply_driver_metadata_config_only_when_no_adapter() -> None:
    result = SimpleNamespace(
        driver_package=None,
        driver_version_requested=None,
        driver_version_resolved=None,
        driver_version_actual=None,
        driver_runtime_strategy=None,
        driver_runtime_path=None,
        driver_runtime_python_executable=None,
        driver_auto_install=False,
        execution_metadata={},
        platform_info={},
    )

    db_config = SimpleNamespace(
        driver_package="snowflake-connector-python",
        driver_version="3.5.0",
        driver_version_resolved="3.5.0",
        driver_version_actual=None,
        driver_runtime_strategy=None,
        driver_runtime_path=None,
        driver_runtime_python_executable=None,
        driver_auto_install=False,
    )

    apply_driver_metadata(result, database_config=db_config, platform_adapter=None)

    assert result.driver_package == "snowflake-connector-python"
    assert result.driver_version_requested == "3.5.0"
    assert result.driver_version_resolved == "3.5.0"
    assert result.driver_version_actual is None
    assert result.driver_runtime_strategy is None
    assert result.platform_info["driver_package"] == "snowflake-connector-python"
    assert result.platform_info["driver_version_requested"] == "3.5.0"


def test_apply_driver_metadata_all_none_leaves_defaults() -> None:
    result = SimpleNamespace(
        driver_package=None,
        driver_version_requested=None,
        driver_version_resolved=None,
        driver_version_actual=None,
        driver_runtime_strategy=None,
        driver_runtime_path=None,
        driver_runtime_python_executable=None,
        driver_auto_install=False,
        execution_metadata={},
    )

    adapter = SimpleNamespace(
        driver_package=None,
        driver_version_requested=None,
        driver_version_resolved=None,
        driver_version_actual=None,
        driver_runtime_strategy=None,
        driver_runtime_path=None,
        driver_runtime_python_executable=None,
        driver_auto_install_used=False,
    )

    db_config = SimpleNamespace(
        driver_package=None,
        driver_version=None,
        driver_version_resolved=None,
        driver_version_actual=None,
        driver_runtime_strategy=None,
        driver_runtime_path=None,
        driver_runtime_python_executable=None,
        driver_auto_install=False,
    )

    apply_driver_metadata(result, database_config=db_config, platform_adapter=adapter)

    assert result.driver_package is None
    assert result.driver_version_requested is None
    assert result.driver_version_resolved is None
    assert result.driver_version_actual is None
    assert result.driver_auto_install is False


