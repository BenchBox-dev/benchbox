from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.vendor_condition_corpus_report import classify_corpus, main, render_report

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _write(directory: Path, stem: str, platform: str, run_timestamp: str, manifest_extra: dict | None = None) -> None:
    (directory / f"{stem}.json").write_text(
        json.dumps({"platform": {"name": platform}, "run": {"timestamp": run_timestamp}}), encoding="utf-8"
    )
    manifest = {"bundle_file": f"{stem}.json", "funding": "unspecified", **(manifest_extra or {})}
    (directory / f"{stem}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _statuses(rows: list[dict[str, str]]) -> dict[str, str]:
    return {row["bundle"]: row["status"] for row in rows}


def test_classifies_pre_policy_evidence_backed_and_none(tmp_path):
    _write(tmp_path, "old_bq", "BigQuery", "2026-09-19T21:11:34")
    _write(
        tmp_path,
        "old_sf",
        "Snowflake",
        "2026-09-19T21:11:34",
        {"plan_tier": "paid", "self_run": True, "vendor_notice": {"sent_on": "2026-09-01", "reference": "t"}},
    )
    _write(tmp_path, "new_db", "Databricks", "2026-11-18T00:00:00")
    _write(tmp_path, "duck", "DuckDB", "2026-09-19T21:11:34")

    statuses = _statuses(classify_corpus(tmp_path, frozenset()))

    assert statuses == {
        "old_bq.json": "pre_policy",
        "old_sf.json": "evidence_backed",
        "new_db.json": "none",
    }


def test_withdrawn_bundle_is_reported_withdrawn(tmp_path):
    _write(tmp_path, "new_db", "Databricks", "2026-11-18T00:00:00")

    statuses = _statuses(classify_corpus(tmp_path, frozenset({"new_db.json"})))

    assert statuses == {"new_db.json": "withdrawn"}


def test_manifest_without_bundle_is_none(tmp_path):
    (tmp_path / "orphan.manifest.json").write_text(json.dumps({"bundle_file": "orphan.json"}), encoding="utf-8")
    (tmp_path / "ledger.manifest.json").write_text(json.dumps({"bundles": []}), encoding="utf-8")

    statuses = _statuses(classify_corpus(tmp_path, frozenset()))

    assert statuses == {"orphan.json": "none"}


def test_fields_present_but_failing_validation_is_none(tmp_path):
    _write(
        tmp_path,
        "free_sf",
        "Snowflake",
        "2026-09-19T21:11:34",
        {"plan_tier": "free", "self_run": True, "vendor_notice": {"sent_on": "2026-09-01", "reference": "t"}},
    )

    statuses = _statuses(classify_corpus(tmp_path, frozenset()))

    assert statuses == {"free_sf.json": "none"}


def test_report_counts_and_exit_code(tmp_path, capsys):
    _write(tmp_path, "old_bq", "BigQuery", "2026-09-19T21:11:34")

    assert main(["--bundles-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "manifests: 1" in output
    assert "pre_policy: 1" in output
    assert "none: 0" in output

    _write(tmp_path, "new_db", "Databricks", "2026-11-18T00:00:00")
    assert main(["--bundles-dir", str(tmp_path)]) == 1


def test_render_report_lists_every_row(tmp_path):
    _write(tmp_path, "old_bq", "BigQuery", "2026-09-19T21:11:34")

    report = render_report(classify_corpus(tmp_path, frozenset()))

    assert "| BigQuery | 2026-09-19 | - | none | pre_policy | old_bq.json |" in report
