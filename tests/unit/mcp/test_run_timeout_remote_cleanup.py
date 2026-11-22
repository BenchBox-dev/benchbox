from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from benchbox.utils.run_deadline import remote_cleanup_warning

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _run(tmp_path: Path, platform: str, **arguments: Any) -> dict[str, Any]:
    from benchbox.mcp import create_server
    from benchbox.mcp.tools import benchmark as benchmark_tools
    from tests.unit.mcp.public_api import call_tool

    server = create_server(results_dir=tmp_path, charts_dir=tmp_path)
    with (
        patch.object(benchmark_tools, "_apply_platform_policy", return_value=(None, None)),
        patch.object(benchmark_tools, "_run_benchmark_impl", return_value={"status": "completed"}),
    ):
        return call_tool(server, "run_benchmark", platform=platform, benchmark="tpch", scale_factor=0.01, **arguments)


def test_run_benchmark_warns_that_remote_work_may_continue_on_a_cloud_platform_with_a_limit(tmp_path) -> None:
    implemented = _run(tmp_path, "quanton", timeout_seconds=30)
    not_implemented = _run(tmp_path, "snowflake", timeout_seconds=30)

    assert implemented["warnings"] == [remote_cleanup_warning("Onehouse Quanton", True)]
    assert not_implemented["warnings"] == [remote_cleanup_warning("Snowflake", False)]


@pytest.mark.parametrize(
    "platform, arguments",
    [("quanton", {}), ("quanton", {"timeout_seconds": 0}), ("duckdb", {"timeout_seconds": 30})],
)
def test_run_benchmark_gives_no_remote_work_warning_without_a_limit_or_on_a_local_platform(
    tmp_path, platform, arguments
) -> None:
    response = _run(tmp_path, platform, **arguments)

    assert "run time limit" not in " ".join(response.get("warnings", []))
