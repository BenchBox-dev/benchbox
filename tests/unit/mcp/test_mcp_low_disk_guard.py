from __future__ import annotations

import shutil
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from benchbox.utils import disk_guard

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GIB = 1024**3


@pytest.fixture(scope="module")
def tool_functions() -> dict[str, Any]:
    import benchbox.utils.printing as printing
    from benchbox.mcp import create_server
    from tests.unit.mcp.public_api import get_tool_functions

    quiet = printing._QUIET
    try:
        return get_tool_functions(create_server())
    finally:
        printing._QUIET = quiet


def test_mcp_run_below_threshold_fails_before_any_write(tool_functions, tmp_path, monkeypatch):
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "runs"))
    low = shutil._ntuple_diskusage(100 * GIB, 99 * GIB, 1 * GIB)
    adapter = MagicMock()
    with (
        patch("benchbox.mcp.tools.benchmark._get_platform_adapter", return_value=adapter),
        patch("benchbox.mcp.tools.benchmark.get_public_benchmark_class", return_value=MagicMock),
        patch.object(disk_guard.shutil, "disk_usage", return_value=low),
        patch("benchbox.core.run_service.run_benchmark_lifecycle") as lifecycle,
    ):
        result = tool_functions["run_benchmark"](platform="duckdb", benchmark="tpch", scale_factor=0.01, phases="power")

    lifecycle.assert_not_called()
    assert "Low disk space" in str(result)
