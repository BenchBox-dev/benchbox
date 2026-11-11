from __future__ import annotations

import json
from pathlib import Path

import pytest

from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline
from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from benchbox.core.results.anonymization import AnonymizationManager
from benchbox.core.results.canonical_json import canonical_json_bytes

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _bundle() -> dict:
    return {
        "version": "2.0",
        "benchmark": {"id": "tpch", "scale_factor": 0.01, "test_type": "power", "name": "TPC-H"},
        "platform": {"name": "DuckDB", "version": "1.2.3"},
        "run": {"id": "run-1", "timestamp": "2026-05-01T12:00:00Z", "total_duration_ms": 42},
        "summary": {"validation": "passed", "queries": 1},
        "queries": [{"id": "q1", "ms": 42, "status": "SUCCESS", "run_type": "measurement"}],
        "metadata": {"working_dir": "/Users/someone/benchbox"},
    }


def test_result_id_hashes_published_bytes_not_raw_bytes(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    raw_data = _bundle()
    path = tmp_path / "bundle.json"
    raw_bytes = json.dumps(raw_data).encode()
    path.write_bytes(raw_bytes)

    public_data = AnonymizationManager().anonymize_result_payload(raw_data)
    public_bytes = canonical_json_bytes(public_data)
    assert public_bytes != raw_bytes, "fixture must actually be changed by anonymization"

    published_id = transformer.result_id_from_bundle(path, data=public_data, raw=public_bytes)
    raw_id = transformer.result_id_from_bundle(path, data=raw_data, raw=raw_bytes)

    assert published_id != raw_id
    recomputed = transformer.result_id_from_bundle(path, data=public_data, raw=public_bytes)
    assert recomputed == published_id


def test_pipeline_derives_the_id_after_anonymizing() -> None:
    source = (
        Path(__file__).resolve().parents[4] / "_project" / "scripts" / "explorer_pipeline" / "pipeline.py"
    ).read_text(encoding="utf-8")

    public_raw_assign = source.index("public_raw = canonical_json_bytes(public_bundle)")
    id_assign = source.index("result_id = self._transformer.result_id_from_bundle(")
    assert public_raw_assign < id_assign, "result_id is derived before the public bytes exist"

    derivation = source[id_assign : id_assign + 220]
    assert "raw=public_raw" in derivation, "result_id is no longer derived from the published bytes"
    assert "raw=bundle_raw" not in derivation, "result_id reverted to hashing raw source bytes"


def test_published_artifact_bytes_recompute_the_result_id(data_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    ExplorerPipeline().run(data_dir, output)

    published = sorted((output / "bundles").glob("*.json"))
    assert published, "pipeline published no bundle"

    transformer = BundleTransformer()
    for artifact in published:
        emitted = artifact.read_bytes()
        recomputed = transformer.result_id_from_bundle(artifact, data=json.loads(emitted), raw=emitted)
        assert recomputed == artifact.stem, (
            f"{artifact.name}: filename does not match the ID recomputed from its own bytes; "
            "the publisher is writing bytes other than the ones it hashed"
        )


