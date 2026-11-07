from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.utilities.optional_engines import chdb_skip_reason

pytestmark = [pytest.mark.integration, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_chdb_engine.py"
SCRIPT_TIMEOUT_SECONDS = 40
LOADER_REJECTION_MARKER = "mis-aligned LINKEDIT"


def _skip_unless_engine_loads() -> None:
    reason = chdb_skip_reason()
    if reason is None:
        return
    if sys.platform == "darwin" and LOADER_REJECTION_MARKER in reason:
        pytest.skip(reason)
    if os.environ.get("CI") and sys.platform.startswith("linux"):
        pytest.fail(f"chDB must load on Linux CI: {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def engine_report() -> dict[str, object]:
    _skip_unless_engine_loads()
    try:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"chDB tuning-metadata exercise hung for more than {SCRIPT_TIMEOUT_SECONDS}s: "
            f"{(exc.stdout or b'')[-500:]!r}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    assert lines, completed.stdout[-1500:] + completed.stderr[-1500:]
    report = json.loads(lines[-1])
    assert completed.returncode == 0, json.dumps(report, sort_keys=True)
    return report


def test_chdb_4x_engine_loads(engine_report: dict[str, object]):
    version = str(engine_report["chdb_dist_version"])
    major = int(version.split(".")[0])
    if major < 4 and not os.environ.get("CI"):
        pytest.skip(f"installed chDB {version} predates the 4.x engine this check covers")
    assert major >= 4, version


def test_chdb_timestamp_column_round_trips(engine_report: dict[str, object]):
    assert engine_report["timestamp_read_back"] == "2026-11-07 12:34:56"
    assert engine_report["created_at_values_parse"] is True
    assert engine_report["metadata_rows"] > 0


def test_chdb_tuning_metadata_save_completes_without_hanging(engine_report: dict[str, object]):
    assert engine_report["tunings_saved"] is True
    assert engine_report["tunings_loaded"] is True
    assert engine_report["tuned_run_marker_present"] is True
    assert engine_report["metadata_round_trip_seconds"] < SCRIPT_TIMEOUT_SECONDS
