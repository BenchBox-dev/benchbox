from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.publication import check_corpus_bijection as bijection_mod
from scripts.publication.check_artifact_privacy import (
    main as privacy_main,
    scan_directory_for_privacy,
    scan_file_for_privacy,
)
from scripts.publication.check_corpus_bijection import check as check_corpus, check_bijection, main as bijection_main

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _minimal_bundle(*, benchmark: str = "tpch", platform: str = "duckdb", stamp: str = "a") -> dict:
    return {
        "version": "2.2",
        "benchmark": {"id": benchmark, "scale_factor": 0.01},
        "platform": {"name": platform},
        "run": {"timestamp": "2026-01-15T12:00:00+00:00", "total_duration_ms": 1.0},
        "queries": [],
        "summary": {},
        "_stamp": stamp,
    }


def _write_bundle(path: Path, *, stamp: str = "a", **kwargs: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_minimal_bundle(stamp=stamp, **kwargs), sort_keys=True), encoding="utf-8")
    return path


def _write_sqlite_artifact(path: Path, result_ids: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE results (result_id TEXT)")
        con.executemany("INSERT INTO results (result_id) VALUES (?)", [(rid,) for rid in result_ids])
        con.commit()
    finally:
        con.close()
    return path


def _seed(path: Path, dispositions: dict[str, str]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "dispositions": dispositions,
                "union": sorted(dispositions),
                "digests": dict.fromkeys(dispositions, "0" * 64),
            }
        ),
        encoding="utf-8",
    )
    return path


def test_corpus_bijection_exact_match():
    accepted = [
        "results-data/bundles/b1.json",
        "results-data/bundles/b2.json",
        "results-data/bundles/b3.json",
    ]
    published = [
        "results-data/bundles/b1.json",
        "results-data/bundles/b2.json",
        "results-data/bundles/b3.json",
    ]
    valid, errors = check_bijection(accepted, published)
    assert valid is True
    assert errors == []


def test_corpus_bijection_unexplained_skip_rejection():
    accepted = [
        "results-data/bundles/b1.json",
        "results-data/bundles/b2.json",
    ]
    published = [
        "results-data/bundles/b1.json",
    ]
    valid, errors = check_bijection(accepted, published)
    assert valid is False
    assert any("Zero-skip bijection violation" in e for e in errors)


def test_corpus_bijection_with_approved_disposition():
    accepted = [
        "results-data/bundles/b1.json",
        "results-data/bundles/b2_omitted.json",
    ]
    published = [
        "results-data/bundles/b1.json",
    ]
    dispositions = {
        "results-data/bundles/b2_omitted.json": "withdrawn_under_adr_2026_08_23",
    }
    valid, errors = check_bijection(accepted, published, dispositions=dispositions)
    assert valid is True
    assert errors == []


def test_privacy_scanner_clean_and_dirty(tmp_path: Path):
    clean_file = tmp_path / "clean.json"
    clean_file.write_text('{"public_key": "valid_public_data"}', encoding="utf-8")
    assert scan_file_for_privacy(clean_file) == []

    dirty_file = tmp_path / "dirty.json"
    dirty_file.write_text('{"token": "ghp_123456789012345678901234567890123456"}', encoding="utf-8")
    findings = scan_file_for_privacy(dirty_file)
    assert len(findings) > 0
    assert "GitHub Personal Access Token" in findings[0]


def test_privacy_scanner_exempts_aws_example_tokens(tmp_path: Path) -> None:

    doc = tmp_path / "redshift.html"
    doc.write_text(
        "export AWS_ACCESS_KEY_ID=[REDACTED]\nexport AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n",
        encoding="utf-8",
    )
    assert scan_file_for_privacy(doc) == []

    real = tmp_path / "leak.html"
    real.write_text("export AWS_ACCESS_KEY_ID=AKIAZZZZZZZZZZZZZZ12\n", encoding="utf-8")
    findings = scan_file_for_privacy(real)
    assert len(findings) == 1
    assert "AWS Access Key ID" in findings[0]


def test_privacy_scanner_exempts_placeholder_passwords(tmp_path: Path) -> None:

    for url in (
        "postgres://username:password@hostname:port/database?sslmode=require",
        "postgresql://user:pass@host/db",
        "postgres://tsdbadmin:password@abc123.tsdb.cloud.timescale.com:5432/tsdb",
    ):
        doc = tmp_path / "guide.html"
        doc.write_text(f"export SERVICE_URL='{url}'\n", encoding="utf-8")
        assert scan_file_for_privacy(doc) == [], url

    leaked = tmp_path / "dump.html"
    leaked.write_text("postgres://admin:s3cr3t-real@prod.internal:5432/db\n", encoding="utf-8")
    findings = scan_file_for_privacy(leaked)
    assert len(findings) == 1
    assert "connection string" in findings[0]


def test_privacy_scanner_connection_pattern_does_not_slurp_prose(tmp_path: Path) -> None:

    doc = tmp_path / "guide.html"
    doc.write_text(
        "if db_url.startswith('postgresql://'):\n"
        "    import psycopg2  # contact ops@example.com for access\n"
        "no credentials here, just code and an email address\n",
        encoding="utf-8",
    )
    assert scan_file_for_privacy(doc) == []


def test_privacy_scanner_counts_only_real_connection_leaks(tmp_path: Path) -> None:
    mixed = tmp_path / "mixed.html"
    mixed.write_text(
        "a=postgres://user:pass@host/db\nb=postgres://admin:real-secret-1@h1/db\nc=mysql://root:real-secret-2@h2/db\n",
        encoding="utf-8",
    )
    findings = scan_file_for_privacy(mixed)
    assert len(findings) == 1
    assert "2 occurrence(s)" in findings[0]


def test_privacy_scan_missing_directory_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-site"
    findings = scan_directory_for_privacy(missing)
    assert findings
    assert any("does not exist" in f for f in findings)
    assert privacy_main([str(missing)]) != 0


def test_check_published_only_missing_from_dir_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    bundles = tmp_path / "bundles"
    present = _write_bundle(bundles / "present.json", stamp="present")
    accepted = [
        "results-data/bundles/present.json",
        "results-data/bundles/published_only.json",
    ]
    seed = _seed(
        tmp_path / "ledger-seed.json",
        {
            "results-data/bundles/present.json": "accepted",
            "results-data/bundles/published_only.json": "published_only",
        },
    )
    monkeypatch.setattr(bijection_mod, "accepted_paths_from_ref", lambda ref: accepted)
    monkeypatch.setattr(
        bijection_mod,
        "recompute_result_id",
        lambda path: f"rid-{path.stem}",
    )
    errors = check_corpus(
        accepted_ref="HEAD",
        bundles_dir=bundles,
        artifact=None,
        ledger_seed=seed,
    )
    assert errors == []
    assert present.is_file()


def test_check_extra_artifact_id_without_overlay_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    bundles = tmp_path / "bundles"
    _write_bundle(bundles / "b1.json", stamp="b1")
    accepted = ["results-data/bundles/b1.json"]
    seed = _seed(tmp_path / "ledger-seed.json", {"results-data/bundles/b1.json": "accepted"})
    artifact = _write_sqlite_artifact(tmp_path / "results.duckdb", ["rid-b1", "rid-ghost"])

    monkeypatch.setattr(bijection_mod, "accepted_paths_from_ref", lambda ref: accepted)
    monkeypatch.setattr(bijection_mod, "recompute_result_id", lambda path: f"rid-{path.stem}")
    errors = check_corpus(
        accepted_ref="HEAD",
        bundles_dir=bundles,
        artifact=artifact,
        ledger_seed=seed,
    )
    assert errors
    assert any("untraceable publication" in e for e in errors)


def test_check_legacy_overlay_extra_matched_by_recomputed_rid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    bundles = tmp_path / "bundles"
    _write_bundle(bundles / "accepted.json", stamp="acc")
    overlay = _write_bundle(bundles / "overlay.json", stamp="ovl", platform="clickhouse")
    accepted = ["results-data/bundles/accepted.json"]
    seed = _seed(
        tmp_path / "ledger-seed.json",
        {
            "results-data/bundles/accepted.json": "accepted",
            "results-data/bundles/overlay.json": "legacy_overlay",
        },
    )
    overlay_rid = bijection_mod.recompute_result_id_from_bytes(overlay.read_bytes(), hint_path=str(overlay))
    artifact = _write_sqlite_artifact(tmp_path / "results.sqlite", ["rid-accepted", overlay_rid])

    monkeypatch.setattr(bijection_mod, "accepted_paths_from_ref", lambda ref: accepted)
    monkeypatch.setattr(bijection_mod, "recompute_result_id", lambda path: f"rid-{path.stem}")
    errors = check_corpus(
        accepted_ref="HEAD",
        bundles_dir=bundles,
        artifact=artifact,
        ledger_seed=seed,
    )
    assert errors == []


def test_main_require_artifact_missing_exits_nonzero(tmp_path: Path) -> None:
    missing = tmp_path / "no-such.duckdb"
    rc = bijection_main(
        [
            "--accepted-ref",
            "HEAD",
            "--bundles-dir",
            str(tmp_path / "bundles"),
            "--artifact",
            str(missing),
            "--require-artifact",
        ]
    )
    assert rc != 0
