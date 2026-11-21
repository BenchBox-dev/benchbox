from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from benchbox.core.results.schema import build_result_payload
from benchbox.validation.bundle import validate_bundles
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[4]

OWNER_REFUSAL_TEXT = "TPC-DI bundles are not accepted while BenchBox reworks TPC-DI."
OWNER_WITHDRAWAL_REASON = (
    "Withdrawn: these results timed BenchBox-written queries on BenchBox-generated tables "
    "and did not measure the TPC-DI workload."
)
WITHDRAWAL_HEADING = "## TPC-DI results withdrawn (2026-11-21)"

LAST_COMMIT_WITH_TPCDI_BUNDLES = "17781bacab05049c432152aeba495ba829455a48"
HISTORICAL_BUNDLE_PATH = "results-data/bundles/tpcdi_sf001_duckdb_sql_20260826_162441_115bd3b6.json"
HISTORICAL_BUNDLE_SHA256 = "d2855bbe5b6aa5ed3e41d0de2ce1426677f0b115a82c98ed2e2fd7b592827814"


def _historical_bundle_bytes() -> bytes:
    completed = subprocess.run(
        ["git", "show", f"{LAST_COMMIT_WITH_TPCDI_BUNDLES}:{HISTORICAL_BUNDLE_PATH}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("Git history that holds the withdrawn TPC-DI bundle is not available in this checkout")
    return completed.stdout


def test_old_format_tpcdi_bundle_from_git_history_is_refused(tmp_path: Path) -> None:
    raw = _historical_bundle_bytes()
    assert hashlib.sha256(raw).hexdigest() == HISTORICAL_BUNDLE_SHA256
    assert json.loads(raw)["benchmark"]["id"] == "tpcdi"
    bundle = tmp_path / "tpcdi_sf001_duckdb_sql_20260826_162441_115bd3b6.json"
    bundle.write_bytes(raw)

    (result,) = validate_bundles([bundle])

    assert OWNER_REFUSAL_TEXT in result.errors
    assert not result.ok


@pytest.mark.parametrize("benchmark_id", ["tpcdi", "TPCDI", " tpcdi", "TPC-DI", "tpc_di"])
def test_current_format_tpcdi_bundle_is_refused(tmp_path: Path, benchmark_id: str) -> None:
    payload = build_result_payload(make_benchmark_results(platform="duckdb"))
    payload["benchmark"]["id"] = benchmark_id
    bundle = tmp_path / "tpcdi_sf001_duckdb_sql_20261121_000000_00000000.json"
    bundle.write_text(json.dumps(payload), encoding="utf-8")

    (result,) = validate_bundles([bundle])

    assert OWNER_REFUSAL_TEXT in result.errors
    assert not result.ok


def test_other_benchmarks_are_not_refused(tmp_path: Path) -> None:
    payload = build_result_payload(make_benchmark_results(platform="duckdb"))
    assert payload["benchmark"]["id"] != "tpcdi"
    bundle = tmp_path / "other.json"
    bundle.write_text(json.dumps(payload), encoding="utf-8")

    (result,) = validate_bundles([bundle])

    assert OWNER_REFUSAL_TEXT not in result.errors


def test_no_bundle_file_in_the_corpus_names_tpcdi() -> None:
    assert [p for p in (REPO_ROOT / "results-data" / "bundles").rglob("*") if "tpcdi" in p.name.lower()] == []


def _withdrawal_rows() -> list[list[str]]:
    notes = (REPO_ROOT / "results-data" / "CORPUS_NOTES.md").read_text(encoding="utf-8")
    assert WITHDRAWAL_HEADING in notes
    section = notes.split(WITHDRAWAL_HEADING, 1)[1]
    assert f'Reason: "{OWNER_WITHDRAWAL_REASON}"' in section
    rows = [line for line in section.splitlines() if line.startswith("| `tpcdi-")]
    return [[cell.strip(" `") for cell in row.strip("|").split("|")] for row in rows]


def test_corpus_notes_list_the_33_withdrawn_bundles_with_the_owner_reason() -> None:
    rows = _withdrawal_rows()
    assert len(rows) == 33
    assert len({row[0] for row in rows}) == 33
    assert all(len(row[1]) == 64 for row in rows)


def test_corpus_notes_hashes_match_the_bundles_in_git_history() -> None:
    for result_id, digest, file in _withdrawal_rows():
        completed = subprocess.run(
            ["git", "show", f"{LAST_COMMIT_WITH_TPCDI_BUNDLES}:results-data/bundles/{file}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("Git history that holds the withdrawn TPC-DI bundles is not available in this checkout")
        assert hashlib.sha256(completed.stdout).hexdigest() == digest, result_id
