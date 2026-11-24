from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = [pytest.mark.integration, pytest.mark.medium]


def _dry_run(tmp_path: Path, *args: str):
    return run_cli_command(
        [
            "run",
            *args,
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--dry-run",
            str(tmp_path / "dry"),
            "--non-interactive",
        ],
        env={"BENCHBOX_OUTPUT_DIR": str(tmp_path)},
    )


@pytest.mark.parametrize(
    ("platform", "option", "replacement"),
    [
        ("polars-df", "streaming=true", "--execution-engine streaming"),
        ("snowflake", "warehouse=ANALYTICS_WH", "compute_resource"),
        ("athena-spark", "engine_version=PySpark engine version 3", "runtime_version"),
        ("clickhouse", "mode=local", "platform selector"),
    ],
)
def test_removed_platform_option_is_rejected_naming_the_replacement(
    tmp_path: Path, platform: str, option: str, replacement: str
) -> None:
    result = _dry_run(tmp_path, "--platform", platform, "--platform-option", option)

    output = " ".join((result.stdout + result.stderr).split())
    assert result.returncode != 0, output
    assert "was removed in BenchBox 0.6.0" in output
    assert replacement in output


def test_polars_streaming_flag_is_rejected(tmp_path: Path) -> None:
    result = _dry_run(tmp_path, "--platform", "polars-df", "--polars-streaming")

    output = " ".join((result.stdout + result.stderr).split())
    assert result.returncode != 0, output
    assert "--polars-streaming" in output
    assert "--execution-engine streaming" in output


def test_mcp_rejects_a_removed_platform_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchbox.mcp import create_server
    from tests.unit.mcp.public_api import call_tool

    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path))
    server = create_server(results_dir=tmp_path / "results", charts_dir=tmp_path / "charts")
    response = call_tool(
        server,
        "run_benchmark",
        platform="polars-df",
        benchmark="tpch",
        scale_factor=0.01,
        queries="6",
        phases="load,power",
        platform_options={"streaming": True},
    )

    assert response["error"] is True, response
    assert "was removed in BenchBox 0.6.0" in response["message"]
    assert "execution_engine='streaming'" in response["message"]
    assert not list((tmp_path / "results").glob("*.json"))
