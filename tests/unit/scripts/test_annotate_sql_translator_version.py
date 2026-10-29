from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from benchbox.validation.bundle import resolve_translator_version
from scripts.annotate_sql_translator_version import DEFAULT_BUNDLES, DEFAULT_NOTES, annotate, main, reconciled_wheels

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SIDECAR = {"bundle_file": "x.json", "bundle_hash": "h", "manifest_version": "1.0", "result_source": "internal"}
HEADER = "| id | cell | run timestamp (clock) | checkout commit | wheel | evidence |\n|---|---|---|---|---|---|\n"


def _notes(directory: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    body = "".join(
        f"| `{bundle_id}` | SQLite tpch SF1 | 2026-10-26 11:28:37 EDT | `{commit}` | {wheel} | {evidence} |\n"
        for bundle_id, commit, wheel, evidence in rows
    )
    path = directory / "notes.md"
    path.write_text(f"# Notes\n\n{HEADER}{body}\nTrailing prose.\n", encoding="utf-8")
    return path


def _bundle(directory: Path, bundle_id: str, *, runtime: dict | None = None) -> Path:
    path = directory / f"tpch_sf1_sqlite_sql_20261026_112837_{bundle_id}.json"
    payload: dict = {"platform": {"name": "SQLite"}}
    if runtime is not None:
        payload["execution"] = {"translation": {"status": "passed", "runtime": runtime}}
    path.write_text(json.dumps(payload), "utf-8")
    path.with_name(f"{path.stem}.manifest.json").write_text(json.dumps(SIDECAR), "utf-8")
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_only_bundles_in_the_reconciled_table_are_attested_and_bundle_json_is_untouched(tmp_path: Path) -> None:
    listed = _bundle(tmp_path, "aaaaaaaa")
    newer = _bundle(tmp_path, "bbbbbbbb")
    unlisted = _bundle(tmp_path, "cccccccc")
    notes = _notes(
        tmp_path, [("aaaaaaaa", "7b8484a4c", "30.24.0.17", "A"), ("bbbbbbbb", "be144e04f", "30.25.0.2", "C")]
    )
    untouched = {path: _digest(path) for path in (listed, newer, unlisted)}

    assert [path.name for path in annotate(tmp_path, notes, write=False)] == [
        f"{listed.stem}.manifest.json",
        f"{newer.stem}.manifest.json",
    ]
    assert json.loads(listed.with_name(f"{listed.stem}.manifest.json").read_text("utf-8")) == SIDECAR

    assert main(["--bundles-dir", str(tmp_path), "--notes", str(notes), "--write"]) == 0

    assert all(_digest(path) == digest for path, digest in untouched.items())
    first = resolve_translator_version(json.loads(listed.read_text("utf-8")), listed)
    second = resolve_translator_version(json.loads(newer.read_text("utf-8")), newer)
    assert (first["source"], first["version"], first["fork_sha"][:7]) == ("attested", "30.24.0.17", "b181afc")
    assert (second["source"], second["version"], second["fork_sha"][:7]) == ("attested", "30.25.0.2", "9f51489")
    assert "evidence A" in first["evidence"] and "7b8484a4c" in first["evidence"]
    assert resolve_translator_version(json.loads(unlisted.read_text("utf-8")), unlisted) == {"source": "not_recorded"}
    assert annotate(tmp_path, notes, write=True) == []


def test_a_wheel_without_a_recorded_pin_stops_the_run(tmp_path: Path) -> None:
    _bundle(tmp_path, "aaaaaaaa")
    notes = _notes(tmp_path, [("aaaaaaaa", "7b8484a4c", "30.25.0.1", "A")])

    with pytest.raises(SystemExit, match="no recorded pin"):
        annotate(tmp_path, notes, write=True)


def test_a_listed_bundle_without_a_file_or_sidecar_stops_the_run(tmp_path: Path) -> None:
    notes = _notes(tmp_path, [("aaaaaaaa", "7b8484a4c", "30.24.0.17", "A")])
    with pytest.raises(SystemExit, match="exactly one bundle"):
        annotate(tmp_path, notes, write=True)

    bundle = _bundle(tmp_path, "aaaaaaaa")
    bundle.with_name(f"{bundle.stem}.manifest.json").unlink()
    with pytest.raises(SystemExit, match="no manifest sidecar"):
        annotate(tmp_path, notes, write=True)


def test_a_bundle_that_records_a_different_runtime_is_refused(tmp_path: Path) -> None:
    runtime = {
        "distribution": "benchbox-sqlglot",
        "version": "30.25.0.2",
        "fork_sha": "f",
        "upstream_sha": "u",
        "wheel_sha256": "w",
    }
    _bundle(tmp_path, "aaaaaaaa", runtime=runtime)
    notes = _notes(tmp_path, [("aaaaaaaa", "7b8484a4c", "30.24.0.17", "A")])

    with pytest.raises(SystemExit, match="conflicts"):
        annotate(tmp_path, notes, write=True)


def test_the_published_corpus_attests_exactly_the_reconciled_wheel_table() -> None:
    rows = reconciled_wheels(DEFAULT_NOTES)
    assert Counter(row["wheel"] for row in rows.values()) == {"30.24.0.17": 44, "30.25.0.2": 14}

    assert annotate(DEFAULT_BUNDLES, DEFAULT_NOTES, write=False) == []
    for bundle_id, row in rows.items():
        (path,) = list(DEFAULT_BUNDLES.glob(f"*_{bundle_id}.json"))
        reading = resolve_translator_version(json.loads(path.read_text("utf-8")), path)
        assert (reading["source"], reading["version"]) == ("attested", row["wheel"])
