from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration._cli_e2e_utils import run_cli_command
from tests.utilities.deprecation_capture import captured_deprecations

pytestmark = [pytest.mark.integration, pytest.mark.medium]


def _deprecation_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if "is deprecated and will be removed" in line]


def _dry_run(tmp_path: Path, *args: str) -> list[str]:
    result = run_cli_command(
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
        env={"BENCHBOX_OUTPUT_DIR": str(tmp_path), "PYTHONWARNINGS": "default"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return _deprecation_lines(result.stdout + result.stderr)


def test_polars_streaming_option_shows_one_warning(tmp_path: Path) -> None:
    lines = _dry_run(tmp_path, "--platform", "polars-df", "--platform-option", "streaming=true")

    assert len(lines) == 1, lines
    assert "Platform option 'streaming' for 'polars'" in lines[0]
    assert "BenchBox 0.6.0" in lines[0]
    assert "'--execution-engine streaming'" in lines[0]


def test_compute_alias_shows_one_warning(tmp_path: Path) -> None:
    lines = _dry_run(tmp_path, "--platform", "snowflake", "--platform-option", "warehouse=ANALYTICS_WH")

    assert len(lines) == 1, lines
    assert "Platform option 'warehouse' for 'snowflake'" in lines[0]
    assert "BenchBox 0.6.0" in lines[0]
    assert "'compute_resource'" in lines[0]


def test_athena_spark_engine_version_alias_shows_one_warning(tmp_path: Path) -> None:
    lines = _dry_run(
        tmp_path, "--platform", "athena-spark", "--platform-option", "engine_version=PySpark engine version 3"
    )

    assert len(lines) == 1, lines
    assert "Platform option 'engine_version' for 'athena-spark'" in lines[0]
    assert "BenchBox 0.6.0" in lines[0]
    assert "'runtime_version'" in lines[0]


def test_clickhouse_mode_alias_names_only_the_supplied_key(tmp_path: Path) -> None:
    lines = _dry_run(tmp_path, "--platform", "clickhouse", "--platform-option", "mode=local")

    assert len(lines) == 1, lines
    assert "Platform option 'mode' for 'clickhouse'" in lines[0]
    assert "deployment_mode" not in lines[0]
    assert "'--platform clickhouse-local'" in lines[0]


def test_polars_streaming_flag_warns_once_and_records_legacy_option(tmp_path: Path) -> None:
    result = run_cli_command(
        [
            "run",
            "--platform",
            "polars-df",
            "--polars-streaming",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--queries",
            "6",
            "--phases",
            "generate,load,power",
            "--non-interactive",
            "--output",
            str(tmp_path / "out"),
        ],
        env={"BENCHBOX_OUTPUT_DIR": str(tmp_path), "PYTHONWARNINGS": "default"},
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    lines = _deprecation_lines(result.stdout + result.stderr)
    assert len(lines) == 1, lines
    assert "Option '--polars-streaming' for 'polars'" in lines[0]
    assert "BenchBox 0.6.0" in lines[0]

    bundles = [path for path in (tmp_path / "results").glob("*.json") if not path.name.startswith("_")]
    assert len(bundles) == 1, bundles
    engine = json.loads(bundles[0].read_text(encoding="utf-8"))["platform"]["execution_engine"]
    assert engine["requested"] == "streaming"
    assert engine["resolution"] == "legacy_option"


def test_mcp_server_warns_once_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from benchbox.mcp import create_server
    from tests.unit.mcp.public_api import call_tool

    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path))
    server = create_server(results_dir=tmp_path / "results", charts_dir=tmp_path / "charts")
    request = {
        "platform": "polars-df",
        "benchmark": "tpch",
        "scale_factor": 0.01,
        "queries": "6",
        "phases": "load,power",
        "platform_options": {"streaming": True},
    }

    per_run: list[list[str]] = []
    for _ in range(2):
        with captured_deprecations(caplog) as seen:
            response = call_tool(server, "run_benchmark", **request)
        assert response["mcp_metadata"]["status"] == "completed", response
        assert response["platform"]["execution_engine"]["resolution"] == "legacy_option"
        per_run.append(seen)

    assert [len(seen) for seen in per_run] == [1, 1], per_run
    assert "Platform option 'streaming' for 'polars'" in per_run[1][0]
