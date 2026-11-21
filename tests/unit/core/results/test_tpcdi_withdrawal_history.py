from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from benchbox.validation.bundle import validate_bundles

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[4]

OWNER_REFUSAL_TEXT = "TPC-DI bundles are not accepted while BenchBox reworks TPC-DI."
WITHDRAWAL_HEADING = "## TPC-DI results withdrawn (2026-11-21)"

LAST_COMMIT_WITH_TPCDI_BUNDLES = "17781bacab05049c432152aeba495ba829455a48"
HISTORICAL_BUNDLE_PATH = "results-data/bundles/tpcdi_sf001_duckdb_sql_20260826_162441_115bd3b6.json"
HISTORICAL_BUNDLE_SHA256 = "d2855bbe5b6aa5ed3e41d0de2ce1426677f0b115a82c98ed2e2fd7b592827814"

_ALLOW_MISSING_HISTORY = os.environ.get("BENCHBOX_ALLOW_MISSING_PUBLISHED_REF") == "1"


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, check=False)


def _commit_present() -> bool:
    return _git("cat-file", "-e", f"{LAST_COMMIT_WITH_TPCDI_BUNDLES}^{{commit}}").returncode == 0


HISTORY_AVAILABLE = _commit_present()
skip_no_history = pytest.mark.skipif(
    not HISTORY_AVAILABLE,
    reason="the commit that holds the withdrawn TPC-DI bundles is unreachable",
)


def test_history_suite_is_not_silently_skipped() -> None:
    assert HISTORY_AVAILABLE or _ALLOW_MISSING_HISTORY, (
        f"commit {LAST_COMMIT_WITH_TPCDI_BUNDLES} is not in this clone; use a full clone (or fetch develop) "
        "so the withdrawn bundles can be checked, or set BENCHBOX_ALLOW_MISSING_PUBLISHED_REF=1 "
        "to allow an offline run"
    )


def _history_bytes(path: str) -> bytes:
    completed = _git("show", f"{LAST_COMMIT_WITH_TPCDI_BUNDLES}:{path}")
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout


def _withdrawal_rows() -> list[list[str]]:
    notes = (REPO_ROOT / "results-data" / "CORPUS_NOTES.md").read_text(encoding="utf-8")
    section = notes.split(WITHDRAWAL_HEADING, 1)[1]
    rows = [line for line in section.splitlines() if line.startswith("| `tpcdi-")]
    return [[cell.strip(" `") for cell in row.strip("|").split("|")] for row in rows]


@skip_no_history
def test_old_format_tpcdi_bundle_from_git_history_is_refused(tmp_path: Path) -> None:
    raw = _history_bytes(HISTORICAL_BUNDLE_PATH)
    assert hashlib.sha256(raw).hexdigest() == HISTORICAL_BUNDLE_SHA256
    assert json.loads(raw)["benchmark"]["id"] == "tpcdi"
    bundle = tmp_path / "tpcdi_sf001_duckdb_sql_20260826_162441_115bd3b6.json"
    bundle.write_bytes(raw)

    (result,) = validate_bundles([bundle])

    assert OWNER_REFUSAL_TEXT in result.errors
    assert not result.ok


@skip_no_history
def test_corpus_notes_hashes_match_the_bundles_in_git_history() -> None:
    rows = _withdrawal_rows()
    assert len(rows) == 33
    for result_id, digest, file in rows:
        assert hashlib.sha256(_history_bytes(f"results-data/bundles/{file}")).hexdigest() == digest, result_id
