from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.data_fetch import load_manifest
from benchbox.core.publishing.bundle_publisher import BundlePublisher
from benchbox.core.publishing.store import PublicationStore

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _write_bundle(path: Path, benchmark_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "version": "2.0",
                "run": {
                    "id": f"{benchmark_id}-run",
                    "timestamp": "2026-05-21T00:00:00Z",
                    "total_duration_ms": 100,
                },
                "benchmark": {"id": benchmark_id, "name": benchmark_id, "scale_factor": 1.0},
                "platform": {"name": "DuckDB"},
                "summary": {"validation": "passed", "queries": {"total": 1, "passed": 1, "failed": 0}},
                "queries": [{"id": "Q1", "ms": 1.0, "status": "SUCCESS", "run_type": "measurement"}],
                "execution": {"mode": "sql"},
            }
        ),
        encoding="utf-8",
    )


def test_joinorder_publication_records_dataset_identity(tmp_path: Path) -> None:
    source = tmp_path / "joinorder_sf1_duckdb_sql_test.json"
    _write_bundle(source, "joinorder")
    store = PublicationStore(store_path=tmp_path / "published.json")

    result = BundlePublisher(destination=tmp_path / "published", store=store).publish(source)

    manifest = load_manifest(Path("benchbox/core/joinorder/data_manifest.toml"))
    assert result.record is not None
    assert result.record.dataset_version == manifest.dataset_version
    assert result.record.manifest_hash == manifest.manifest_hash
    assert result.record.data_archive_hash == manifest.data_archive_hash


def test_generated_benchmark_publication_records_null_dataset_identity(tmp_path: Path) -> None:
    source = tmp_path / "tpch_sf1_duckdb_sql_test.json"
    _write_bundle(source, "tpch")
    store = PublicationStore(store_path=tmp_path / "published.json")

    result = BundlePublisher(destination=tmp_path / "published", store=store).publish(source)

    assert result.record is not None
    assert result.record.dataset_version is None
    assert result.record.manifest_hash is None
    assert result.record.data_archive_hash is None


def test_republish_without_identity_preserves_existing_dataset_identity(tmp_path: Path) -> None:
    store = PublicationStore(store_path=tmp_path / "published.json")
    rec1 = store.add(
        source_path="/tmp/result.json",
        destination="/tmp/published",
        reference="file:///tmp/published/result.json",
        dataset_version="joinorder-imdb-2013-v1",
        manifest_hash="manifest-hash",
        data_archive_hash="archive-hash",
    )

    rec2 = store.add(
        source_path="/tmp/result.json",
        destination="/tmp/published",
        reference="file:///tmp/published/result.json",
    )

    assert rec2.pub_id == rec1.pub_id
    assert rec2.dataset_version == "joinorder-imdb-2013-v1"
    assert rec2.manifest_hash == "manifest-hash"
    assert rec2.data_archive_hash == "archive-hash"


@pytest.mark.parametrize("validation_status", ["not_run", "uncertain"])
def test_publication_rejects_non_clean_validation_status(tmp_path: Path, validation_status: str) -> None:
    source = tmp_path / "tpch_sf1_duckdb_sql_test.json"
    _write_bundle(source, "tpch")
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["summary"]["validation"] = validation_status
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = BundlePublisher(destination=tmp_path / "published").publish(source)

    assert result.success is False
    assert result.errors == [f"Source bundle is not a clean pass: validation_status={validation_status}"]
    assert not (tmp_path / "published" / source.name).exists()


def test_publication_rejects_missing_validation_status(tmp_path: Path) -> None:
    source = tmp_path / "tpch_sf1_duckdb_sql_test.json"
    _write_bundle(source, "tpch")
    payload = json.loads(source.read_text(encoding="utf-8"))
    del payload["summary"]["validation"]
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = BundlePublisher(destination=tmp_path / "published").publish(source)

    assert result.success is False
    assert result.errors == ["Source bundle is not a clean pass: validation_status=not_run"]
    assert not (tmp_path / "published" / source.name).exists()


def test_publication_rejects_translation_fallback_even_when_validation_passed(tmp_path: Path) -> None:
    source = tmp_path / "tpch_sf1_duckdb_sql_test.json"
    _write_bundle(source, "tpch")
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["execution"]["translation"] = {"status": "fallback", "strict_mode": False}
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = BundlePublisher(destination=tmp_path / "published").publish(source)

    assert result.success is False
    assert result.errors == ["Source bundle is not a clean pass: translation_status=fallback"]
    assert not (tmp_path / "published" / source.name).exists()


def test_publication_rejects_raw_scalar_translation_fallback(tmp_path: Path) -> None:
    source = tmp_path / "tpch_sf1_duckdb_sql_test.json"
    _write_bundle(source, "tpch")
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["execution"]["translation"] = "fallback"
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = BundlePublisher(destination=tmp_path / "published").publish(source)

    assert result.success is False
    assert result.errors == ["Source bundle is not a clean pass: translation_status=fallback"]
    assert not (tmp_path / "published" / source.name).exists()


