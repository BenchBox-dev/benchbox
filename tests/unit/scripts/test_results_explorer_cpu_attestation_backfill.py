from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from _project.scripts.results_explorer_cpu_attestation_backfill import backfill
from benchbox.core.results.canonical_json import canonical_json_bytes

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _bundle() -> dict[str, object]:
    return {
        "version": "2.0",
        "benchmark": {"id": "tpch", "scale_factor": 0.01, "test_type": "power", "name": "TPC-H"},
        "platform": {"name": "DuckDB", "version": "1.2.3"},
        "run": {"id": "run-1", "timestamp": "2026-05-01T12:00:00Z", "total_duration_ms": 42},
        "summary": {"validation": "passed", "queries": 1, "tpc_metrics": {}},
        "queries": [{"id": "q1", "ms": 42, "status": "SUCCESS", "run_type": "measurement"}],
    }


def test_write_records_user_attested_provenance_and_keeps_sidecar_hash_fresh(tmp_path: Path) -> None:
    bundle = tmp_path / "run.json"
    sidecar = tmp_path / "run.manifest.json"
    bundle.write_bytes(canonical_json_bytes(_bundle()))
    sidecar.write_bytes(canonical_json_bytes({"bundle_file": bundle.name, "bundle_hash": "stale"}))

    result = backfill(bundles_dir=tmp_path, write=True, manifest_path=tmp_path / "attestation.json")

    payload = json.loads(bundle.read_text(encoding="utf-8"))
    assert payload["environment"]["cpu_identity_provenance"] == "user_attested"
    assert payload["environment"]["client_host"]["cpu_identity_provenance"] == "user_attested"
    assert (
        json.loads(sidecar.read_text(encoding="utf-8"))["bundle_hash"]
        == hashlib.sha256(bundle.read_bytes()).hexdigest()
    )
    assert result["totals"]["changed"] == 1
    assert result["totals"]["manifest_changes"] == 1


def test_existing_attestation_manifest_refuses_sidecar_only_write(tmp_path: Path) -> None:
    bundle = tmp_path / "run.json"
    first_manifest = tmp_path / "cpu-attestation.manifest.json"
    bundle.write_bytes(canonical_json_bytes(_bundle()))
    backfill(bundles_dir=tmp_path, write=True, manifest_path=first_manifest)

    sidecar = tmp_path / "run.manifest.json"
    sidecar.write_bytes(canonical_json_bytes({"bundle_file": bundle.name, "bundle_hash": "stale"}))
    before = sidecar.read_bytes()

    preview = backfill(bundles_dir=tmp_path, write=False, manifest_path=first_manifest)
    assert preview["totals"]["changed"] == 0
    assert preview["totals"]["manifest_changes"] == 1

    with pytest.raises(FileExistsError, match="manifests=1"):
        backfill(bundles_dir=tmp_path, write=True, manifest_path=first_manifest)

    assert sidecar.read_bytes() == before


