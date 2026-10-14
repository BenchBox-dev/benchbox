from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = [pytest.mark.integration, pytest.mark.medium]

pytest.importorskip("polars")

STREAMING_TUNING = """execution:
  engine_affinity: streaming
_metadata:
  version: "1.0"
  format: dataframe_tuning
  platform: polars
"""


def _bundles(results_dir: Path) -> list[Path]:
    return sorted(path for path in results_dir.glob("*.json") if not path.name.startswith("_"))


def _only_receipt(results_dir: Path) -> dict[str, Any]:
    bundles = _bundles(results_dir)
    assert len(bundles) == 1, bundles
    return json.loads(bundles[0].read_text(encoding="utf-8"))["platform"]["execution_engine"]


def _cli_run(tmp_path: Path, *args: str) -> Any:
    return run_cli_command(
        [
            "run",
            "--platform",
            "polars-df",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--queries",
            "6",
            "--phases",
            "generate,load,power",
            *args,
            "--non-interactive",
            "--output",
            str(tmp_path / "out"),
        ],
        env={"BENCHBOX_OUTPUT_DIR": str(tmp_path)},
        timeout=300,
    )


@pytest.fixture
def streaming_tuning(tmp_path: Path) -> str:
    path = tmp_path / "streaming.yaml"
    path.write_text(STREAMING_TUNING, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    ("sources", "resolution"),
    [
        (("--execution-engine", "streaming", "--tuning", "{tuning}", "--polars-streaming"), "explicit"),
        (("--tuning", "{tuning}", "--polars-streaming"), "tuning_profile"),
        (("--polars-streaming",), "legacy_option"),
    ],
    ids=["explicit-tuning-legacy", "tuning-legacy", "legacy"],
)
def test_cli_agreeing_sources_resolve_to_the_highest_precedence(
    tmp_path: Path, streaming_tuning: str, sources: tuple[str, ...], resolution: str
) -> None:
    result = _cli_run(tmp_path, *(part.format(tuning=streaming_tuning) for part in sources))
    assert result.returncode == 0, result.stdout + result.stderr

    receipt = _only_receipt(tmp_path / "results")
    assert receipt["requested"] == "streaming"
    assert receipt["resolution"] == resolution


def test_cli_explicit_engine_conflicting_with_the_tuning_file_fails_before_any_work(
    tmp_path: Path, streaming_tuning: str
) -> None:
    result = _cli_run(tmp_path, "--execution-engine", "in-memory", "--tuning", streaming_tuning)

    output = " ".join((result.stdout + result.stderr).split())
    assert result.returncode != 0, output
    assert "explicit setting requests 'in-memory'" in output
    assert "tuning file requests 'streaming'" in output
    assert _bundles(tmp_path / "results") == []


def test_mcp_explicit_engine_and_legacy_streaming_key_agree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    response = _mcp_run(tmp_path, monkeypatch, execution_engine="streaming", platform_options={"streaming": True})

    assert response["mcp_metadata"]["status"] == "completed", response
    receipt = _only_receipt(tmp_path / "results")
    assert receipt["requested"] == "streaming"
    assert receipt["resolution"] == "explicit"


def test_mcp_explicit_engine_conflicting_with_the_legacy_streaming_key_fails_before_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _mcp_run(tmp_path, monkeypatch, execution_engine="in-memory", platform_options={"streaming": True})

    assert response["error"] is True, response
    assert response["status"] == "failed"
    assert "explicit setting requests 'in-memory'" in response["message"]
    assert "legacy option requests 'streaming'" in response["message"]
    assert _bundles(tmp_path / "results") == []


def _mcp_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **arguments: Any) -> Any:
    from benchbox.mcp import create_server
    from tests.unit.mcp.public_api import call_tool

    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path))
    server = create_server(results_dir=tmp_path / "results", charts_dir=tmp_path / "charts")
    return call_tool(
        server,
        "run_benchmark",
        platform="polars-df",
        benchmark="tpch",
        scale_factor=0.01,
        queries="6",
        phases="load,power",
        **arguments,
    )
