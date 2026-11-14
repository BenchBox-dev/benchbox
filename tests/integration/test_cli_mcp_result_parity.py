from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = [pytest.mark.integration, pytest.mark.medium]

SEED = 7
QUERY = "6"


def _only_bundle(results_dir: Path) -> dict[str, Any]:
    bundles = sorted(path for path in results_dir.glob("*.json") if not path.name.startswith("_"))
    assert len(bundles) == 1, bundles
    return json.loads(bundles[0].read_text(encoding="utf-8"))


def _cli_bundle(tmp_path: Path, platform: str) -> dict[str, Any]:
    result = run_cli_command(
        [
            "run",
            "--platform",
            platform,
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--phases",
            "load,power",
            "--queries",
            QUERY,
            "--seed",
            str(SEED),
            "--non-interactive",
            "--output",
            str(tmp_path / "cli_out"),
        ],
        env={"BENCHBOX_OUTPUT_DIR": str(tmp_path / "cli_runs")},
        home=tmp_path / "home",
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return _only_bundle(tmp_path / "cli_runs" / "results")


def _mcp_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str) -> dict[str, Any]:
    from benchbox.mcp.tools import benchmark as benchmark_tools

    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path / "mcp_runs"))
    results_dir = tmp_path / "mcp_results"
    response = benchmark_tools._run_benchmark_impl(
        platform,
        "tpch",
        0.01,
        QUERY,
        "load,power",
        "sql" if platform == "duckdb" else None,
        seed=SEED,
        results_dir=results_dir,
        anonymize=False,
    )
    assert response["mcp_metadata"]["status"] == "completed", response
    return _only_bundle(results_dir)


def test_cli_and_mcp_record_the_same_config_block_for_the_same_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _cli_bundle(tmp_path, "duckdb")
    mcp = _mcp_bundle(tmp_path, monkeypatch, "duckdb")

    assert cli["config"]["compression"] == {"type": "zstd", "level": None}
    assert mcp["config"] == cli["config"]
    assert mcp["platform"]["config"]["memory_limit"] == cli["platform"]["config"]["memory_limit"]


@pytest.mark.parametrize("platform", ["polars-df"])
def test_dataframe_runs_record_host_load_samples_from_both_entry_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    pytest.importorskip("psutil")
    pytest.importorskip("polars")

    for bundle in (_cli_bundle(tmp_path, platform), _mcp_bundle(tmp_path, monkeypatch, platform)):
        host_load = bundle["environment"]["host_load"]
        for point in ("start", "end"):
            assert host_load[point]["load_average"] is not None
            assert host_load[point]["memory_available_mb"] is not None
            assert host_load[point]["sampled_at"] is not None
        assert host_load["start"]["sampled_at"] <= host_load["end"]["sampled_at"]
