from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = [pytest.mark.integration, pytest.mark.medium]


def _run_load_only(tmp_path: Path, output: str, platform: str, *args: str):
    return run_cli_command(
        [
            "run",
            "--platform",
            platform,
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--phases",
            "generate,load",
            *args,
            "--non-interactive",
            "--output",
            output,
        ],
        env={"BENCHBOX_OUTPUT_DIR": str(tmp_path)},
        timeout=300,
    )


@pytest.mark.parametrize(
    ("platform", "args", "module"),
    [
        ("duckdb", (), "duckdb"),
        ("duckdb", ("--tuning", "tuned"), "duckdb"),
        ("polars-df", (), "polars"),
    ],
    ids=["duckdb-sql", "duckdb-sql-tuned", "polars-dataframe"],
)
def test_successful_load_only_run_exits_zero_with_load_summary(
    tmp_path: Path, tpch_sf001_data: Callable[[Path], str], platform: str, args: tuple[str, ...], module: str
) -> None:
    pytest.importorskip(module)

    result = _run_load_only(tmp_path, tpch_sf001_data(tmp_path / "out"), platform, *args)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Data loading completed: SUCCESS" in result.stdout
    assert "overall_status" not in output
    bundles = sorted(path for path in (tmp_path / "results").glob("*.json") if not path.name.startswith("_"))
    assert len(bundles) == 1, bundles
    summary = json.loads(bundles[0].read_text(encoding="utf-8"))["summary"]
    assert summary["data"]["rows_loaded"] > 0
    assert summary["queries"]["total"] == 0
