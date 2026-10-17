from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "tuning"
QUERY_SUBSET = {"tpch": "1", "ssb": "Q1.1", "tpcds": "1"}

APPLIED_VERIFIED = "applied_verified"
APPLIED_UNVERIFIED = "applied_unverified"
NOOP = "noop"
NOT_APPLICABLE = "not_applicable"

EXPECTED_STATUS: dict[tuple[str, str, str], str] = {
    ("duckdb", "tpch", "tuned"): APPLIED_VERIFIED,
    ("duckdb", "tpch", "notuning"): NOT_APPLICABLE,
    ("duckdb", "tpch", "auto"): APPLIED_VERIFIED,
    ("duckdb", "ssb", "tuned"): APPLIED_VERIFIED,
    ("duckdb", "ssb", "notuning"): NOT_APPLICABLE,
    ("duckdb", "ssb", "auto"): APPLIED_VERIFIED,
    ("duckdb", "tpcds", "tuned"): APPLIED_VERIFIED,
    ("duckdb", "tpcds", "notuning"): NOT_APPLICABLE,
    ("duckdb", "tpcds", "auto"): APPLIED_UNVERIFIED,
    ("sqlite", "tpch", "tuned"): APPLIED_UNVERIFIED,
    ("sqlite", "tpch", "notuning"): NOT_APPLICABLE,
    ("sqlite", "tpch", "auto"): APPLIED_UNVERIFIED,
    ("sqlite", "ssb", "tuned"): APPLIED_UNVERIFIED,
    ("sqlite", "ssb", "notuning"): NOT_APPLICABLE,
    ("sqlite", "ssb", "auto"): APPLIED_UNVERIFIED,
    ("sqlite", "tpcds", "tuned"): APPLIED_UNVERIFIED,
    ("sqlite", "tpcds", "notuning"): NOT_APPLICABLE,
    ("sqlite", "tpcds", "auto"): APPLIED_UNVERIFIED,
    ("datafusion", "tpch", "tuned"): NOOP,
    ("datafusion", "tpch", "notuning"): NOT_APPLICABLE,
    ("datafusion", "tpch", "auto"): NOOP,
    ("datafusion", "ssb", "tuned"): NOOP,
    ("datafusion", "ssb", "notuning"): NOT_APPLICABLE,
    ("datafusion", "ssb", "auto"): NOOP,
    ("datafusion", "tpcds", "tuned"): NOOP,
    ("datafusion", "tpcds", "notuning"): NOT_APPLICABLE,
    ("datafusion", "tpcds", "auto"): NOOP,
    ("clickhouse-local", "tpch", "tuned"): APPLIED_VERIFIED,
    ("clickhouse-local", "tpch", "notuning"): NOT_APPLICABLE,
    ("clickhouse-local", "tpch", "auto"): APPLIED_UNVERIFIED,
    ("clickhouse-local", "ssb", "tuned"): APPLIED_VERIFIED,
    ("clickhouse-local", "ssb", "notuning"): NOT_APPLICABLE,
    ("clickhouse-local", "ssb", "auto"): APPLIED_UNVERIFIED,
    ("clickhouse-local", "tpcds", "tuned"): APPLIED_VERIFIED,
    ("clickhouse-local", "tpcds", "notuning"): NOT_APPLICABLE,
    ("clickhouse-local", "tpcds", "auto"): APPLIED_UNVERIFIED,
    ("polars", "tpch", "tuned"): NOOP,
    ("polars", "tpch", "notuning"): NOT_APPLICABLE,
    ("polars", "tpch", "auto"): APPLIED_UNVERIFIED,
    ("polars", "ssb", "tuned"): NOOP,
    ("polars", "ssb", "notuning"): NOT_APPLICABLE,
    ("polars", "ssb", "auto"): APPLIED_UNVERIFIED,
    ("polars", "tpcds", "tuned"): NOOP,
    ("polars", "tpcds", "notuning"): NOT_APPLICABLE,
    ("polars", "tpcds", "auto"): APPLIED_UNVERIFIED,
}


def _chdb_importable() -> bool:
    if importlib.util.find_spec("chdb") is None:
        return False
    probe = subprocess.run([sys.executable, "-c", "import chdb"], capture_output=True)
    return probe.returncode == 0


CHDB_AVAILABLE = _chdb_importable()


@pytest.fixture(scope="module")
def run_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("tuning_liveness")


def _cli_run(run_root: Path, platform: str, benchmark: str, tuning: str) -> tuple[subprocess.CompletedProcess, Any]:
    shared_datagen = run_root / "datagen"
    shared_datagen.mkdir(exist_ok=True)
    tuning_label = Path(tuning).stem if tuning.endswith(".yaml") else tuning
    output_dir = Path(tempfile.mkdtemp(prefix=f"{platform}-{benchmark}-{tuning_label}-", dir=run_root))
    (output_dir / "datagen").symlink_to(shared_datagen, target_is_directory=True)
    env = {key: value for key, value in os.environ.items() if key not in ("FORCE_COLOR", "COLORTERM")}
    env["BENCHBOX_OUTPUT_DIR"] = str(output_dir)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from benchbox.cli.app import main; main()",
            "run",
            "--platform",
            platform,
            "--benchmark",
            benchmark,
            "--scale",
            "0.01",
            "--tuning",
            tuning,
            "--queries",
            QUERY_SUBSET[benchmark],
            "--non-interactive",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    produced = sorted(path for path in (output_dir / "results").glob("*.json") if not path.name.endswith(".plans.json"))
    bundle = json.loads(produced[-1].read_text(encoding="utf-8")) if produced else None
    return completed, bundle


def _tuning(bundle: Any) -> dict[str, Any]:
    return (bundle or {}).get("platform", {}).get("tuning") or {}


def _receipt_verdicts(bundle: Any) -> list[str]:
    applied = _tuning(bundle).get("applied") or {}
    return [entry.get("verdict") for entry in (applied.get("receipt") or {}).get("entries", [])]


def _assert_ran(completed: subprocess.CompletedProcess, bundle: Any) -> None:
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]
    assert bundle is not None, "the run wrote no result bundle"


def test_duckdb_sort_only_custom_config_reaches_applied_verified(run_root: Path):
    completed, bundle = _cli_run(run_root, "duckdb", "tpch", str(FIXTURES / "duckdb_sort_only.yaml"))

    _assert_ran(completed, bundle)
    tuning = _tuning(bundle)
    assert tuning["validation_status"] == APPLIED_VERIFIED
    assert tuning["applied"]["dropped"] == []
    assert "mismatch" not in _receipt_verdicts(bundle)


@pytest.mark.parametrize("benchmark_name", ["tpch", "ssb", "tpcds"])
def test_duckdb_shipped_tuned_templates_reach_applied_verified(run_root: Path, benchmark_name: str):
    completed, bundle = _cli_run(run_root, "duckdb", benchmark_name, "tuned")

    _assert_ran(completed, bundle)
    tuning = _tuning(bundle)
    assert tuning["validation_status"] == APPLIED_VERIFIED, tuning.get("applied", {}).get("dropped")
    assert tuning["tuning_source"] == "auto_discovered"
    assert "mismatch" not in _receipt_verdicts(bundle)


@pytest.mark.skipif(not CHDB_AVAILABLE, reason="chDB is not importable on this host")
def test_chdb_custom_sort_and_partition_config_reaches_applied_verified(run_root: Path):
    completed, bundle = _cli_run(run_root, "clickhouse-local", "tpch", str(FIXTURES / "clickhouse_sort_partition.yaml"))

    _assert_ran(completed, bundle)
    assert _tuning(bundle)["validation_status"] == APPLIED_VERIFIED
    assert "mismatch" not in _receipt_verdicts(bundle)


@pytest.mark.parametrize(("platform", "benchmark_name", "mode"), sorted(EXPECTED_STATUS))
def test_status_matrix(run_root: Path, platform: str, benchmark_name: str, mode: str):
    if platform == "clickhouse-local" and not CHDB_AVAILABLE:
        pytest.skip("chDB is not importable on this host")
    completed, bundle = _cli_run(run_root, platform, benchmark_name, mode)

    _assert_ran(completed, bundle)
    tuning = _tuning(bundle)
    assert tuning.get("validation_status") == EXPECTED_STATUS[(platform, benchmark_name, mode)], tuning
    assert "mismatch" not in _receipt_verdicts(bundle)
