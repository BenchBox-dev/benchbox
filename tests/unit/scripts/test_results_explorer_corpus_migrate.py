from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from _project.scripts.results_explorer_corpus_migrate import _semantic_signature, migrate
from benchbox.core.results.anonymization import MissingPublicPseudonymSaltError
from benchbox.core.results.canonical_json import canonical_json_bytes

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(autouse=True)
def _pseudonym_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHBOX_MACHINE_ID_SALT", "test-salt")


def test_migrate_refuses_without_salt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHBOX_MACHINE_ID_SALT", raising=False)
    with pytest.raises(MissingPublicPseudonymSaltError):
        migrate(bundles_dir=tmp_path, write=False, manifest_path=tmp_path / "migration.json")


def _bundle() -> dict[str, object]:
    return {
        "version": "2.0",
        "benchmark": {"id": "tpch", "scale_factor": 0.01, "test_type": "power", "name": "TPC-H"},
        "platform": {"name": "DuckDB", "version": "1.2.3"},
        "run": {"id": "run-1", "timestamp": "2026-05-01T12:00:00Z", "total_duration_ms": 42},
        "summary": {"validation": "passed", "queries": 1, "tpc_metrics": {"power_at_size": 2.5}},
        "queries": [{"id": "q1", "ms": 42, "status": "SUCCESS", "run_type": "measurement"}],
        "metadata": {"working_dir": "/Users/alice/private/benchbox"},
    }


def _write_fixture(root: Path) -> tuple[Path, Path, Path]:
    bundle = root / "run.json"
    companion = root / "run.tuning.json"
    manifest = root / "run.manifest.json"
    bundle.write_text(json.dumps(_bundle()), encoding="utf-8")
    companion.write_text(
        json.dumps({"source_file": "/Users/alice/private/tuning.json", "requested": {}}), encoding="utf-8"
    )
    manifest.write_text(
        json.dumps(
            {
                "bundle_file": bundle.name,
                "bundle_hash": "old-bundle-hash",
                "companion_hashes": {companion.name: "old-companion-hash"},
                "submitted_by": "/Users/alice/private/operator",
            }
        ),
        encoding="utf-8",
    )
    return bundle, companion, manifest


def test_dry_run_reports_mapping_without_writing(tmp_path: Path) -> None:
    bundle, companion, manifest = _write_fixture(tmp_path)
    before = {path: path.read_bytes() for path in (bundle, companion, manifest)}

    result = migrate(bundles_dir=tmp_path, write=False, manifest_path=tmp_path / "migration.json")

    assert result["summary"] == {
        "total_bundles": 1,
        "changed_bundles": 1,
        "unchanged_bundles": 0,
        "result_id_changes": 1,
        "companion_changes": 1,
        "manifest_changes": 1,
    }
    assert {path: path.read_bytes() for path in before} == before
    assert not (tmp_path / "migration.json").exists()


def test_write_preserves_semantics_and_updates_manifest_hashes(tmp_path: Path) -> None:
    bundle, companion, manifest = _write_fixture(tmp_path)
    original = json.loads(bundle.read_text(encoding="utf-8"))

    migrate(bundles_dir=tmp_path, write=True, manifest_path=tmp_path / "migration.json")

    public_bundle = json.loads(bundle.read_text(encoding="utf-8"))
    public_companion = json.loads(companion.read_text(encoding="utf-8"))
    public_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    assert _semantic_signature(original) == _semantic_signature(public_bundle)
    assert "/Users/" not in json.dumps(public_bundle)
    assert "/Users/" not in json.dumps(public_companion)
    assert "/Users/" not in json.dumps(public_manifest)
    assert public_manifest["bundle_hash"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert public_manifest["companion_hashes"][companion.name] == hashlib.sha256(companion.read_bytes()).hexdigest()


def test_second_dry_run_is_a_noop_after_write(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    migration_manifest = tmp_path / "migration.manifest.json"
    migrate(bundles_dir=tmp_path, write=True, manifest_path=migration_manifest)

    result = migrate(bundles_dir=tmp_path, write=False, manifest_path=migration_manifest)

    assert result["summary"]["changed_bundles"] == 0
    assert result["summary"]["manifest_changes"] == 0


def test_existing_migration_manifest_refuses_before_mutating_inputs(tmp_path: Path) -> None:
    bundle, companion, manifest = _write_fixture(tmp_path)
    migration_manifest = tmp_path / "migration.manifest.json"
    migration_manifest.write_text('{"migration": "results-explorer-public-path-privacy-v1"}', encoding="utf-8")
    before = {path: path.read_bytes() for path in (bundle, companion, manifest, migration_manifest)}

    with pytest.raises(FileExistsError, match="requires a new --manifest path"):
        migrate(bundles_dir=tmp_path, write=True, manifest_path=migration_manifest)

    assert {path: path.read_bytes() for path in before} == before


def test_existing_manifest_refuses_companion_only_changes(tmp_path: Path) -> None:
    bundle = tmp_path / "run.json"
    companion = tmp_path / "run.tuning.json"
    clean = _bundle()
    clean["metadata"] = {"note": "clean"}
    bundle.write_bytes(canonical_json_bytes(clean))
    companion.write_text(json.dumps({"source_file": "/Users/alice/private/tuning.json"}), encoding="utf-8")

    migration_manifest = tmp_path / "migration.manifest.json"
    migration_manifest.write_text('{"migration": "results-explorer-public-path-privacy-v1"}', encoding="utf-8")
    before = {path: path.read_bytes() for path in (bundle, companion, migration_manifest)}

    preview = migrate(bundles_dir=tmp_path, write=False, manifest_path=tmp_path / "unused.json")
    assert preview["summary"]["changed_bundles"] == 0
    assert preview["summary"]["companion_changes"] == 1

    with pytest.raises(FileExistsError, match="companion_changes=1"):
        migrate(bundles_dir=tmp_path, write=True, manifest_path=migration_manifest)

    assert {path: path.read_bytes() for path in before} == before


def test_companion_hash_uses_sanitized_bytes_in_dry_run(tmp_path: Path) -> None:
    bundle, companion, manifest = _write_fixture(tmp_path)
    result = migrate(bundles_dir=tmp_path, write=False, manifest_path=tmp_path / "migration.json")
    entry = result["bundles"][0]
    assert entry["companion_changes"][0]["old_sha256"] == hashlib.sha256(companion.read_bytes()).hexdigest()
    assert entry["companion_changes"][0]["new_sha256"] != entry["companion_changes"][0]["old_sha256"]
    assert json.loads(manifest.read_text(encoding="utf-8"))["bundle_hash"] == "old-bundle-hash"
