from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from benchbox.validation.bundle import (
    SQL_TRANSLATOR_VERSION_KEY,
    TranslatorVersionError,
    ValidationResult,
    _validate_manifest_provenance,
    resolve_translator_version,
)
from tests.unit.scripts.test_corpus_cohort_depth import MINIMAL_BUNDLE, _load_validator

pytestmark = [pytest.mark.unit, pytest.mark.medium]

IDENTITY = {
    "version": "30.24.0.17",
    "fork_sha": "b181afce8e9e9cd78b12ba78151d1cf7dbc9adf7",
    "upstream_sha": "5d588c0f32d17a08191898638f67ff27bb0a13ff",
    "wheel_sha256": "f91d099155ca4be92397b8ab2e86ef5e07b4bee0aefcec31859afced9f4fc526",
}
ATTESTATION = {**IDENTITY, "evidence": "Reconciled wheel table."}
RUNTIME = {"distribution": "benchbox-sqlglot", **IDENTITY}


def _bundle(runtime: dict | None = None) -> dict:
    payload = copy.deepcopy(MINIMAL_BUNDLE)
    if runtime is not None:
        payload.setdefault("execution", {})["translation"] = {"status": "passed", "runtime": runtime}
    return payload


def _write(directory: Path, payload: dict, attestation: object = None, name: str = "bundle.json") -> Path:
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    if attestation is not None:
        sidecar = {"bundle_file": name, "bundle_hash": "h", SQL_TRANSLATOR_VERSION_KEY: attestation}
        path.with_name(f"{path.stem}.manifest.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return path


def test_a_bundle_without_runtime_or_attestation_is_not_recorded(tmp_path: Path) -> None:
    path = _write(tmp_path, _bundle())

    assert resolve_translator_version(json.loads(path.read_text("utf-8")), path) == {"source": "not_recorded"}
    assert resolve_translator_version(_bundle()) == {"source": "not_recorded"}


def test_the_sidecar_attests_a_bundle_that_records_no_runtime(tmp_path: Path) -> None:
    path = _write(tmp_path, _bundle(), ATTESTATION)

    assert resolve_translator_version(_bundle(), path) == {"source": "attested", **ATTESTATION}


def test_a_recorded_runtime_wins_and_is_labelled_recorded(tmp_path: Path) -> None:
    unattested = _write(tmp_path, _bundle(RUNTIME), name="plain.json")
    agreeing = _write(tmp_path, _bundle(RUNTIME), ATTESTATION, name="agreeing.json")

    assert resolve_translator_version(_bundle(RUNTIME), unattested) == {"source": "recorded", **IDENTITY}
    assert resolve_translator_version(_bundle(RUNTIME), agreeing) == {"source": "recorded", **IDENTITY}


@pytest.mark.parametrize(
    "attestation",
    [
        "30.24.0.17",
        {**ATTESTATION, "evidence": " "},
        {key: value for key, value in ATTESTATION.items() if key != "wheel_sha256"},
        {**ATTESTATION, "version": "thirty"},
        {**ATTESTATION, "fork_sha": 7},
        {**ATTESTATION, "sql_hash": "abc"},
    ],
)
def test_a_malformed_attestation_is_rejected_everywhere(tmp_path: Path, attestation: object) -> None:
    path = _write(tmp_path, _bundle(), attestation)
    with pytest.raises(TranslatorVersionError):
        resolve_translator_version(_bundle(), path)

    result = ValidationResult(str(path))
    _validate_manifest_provenance(json.loads(path.with_name("bundle.manifest.json").read_text("utf-8")), path, result)
    assert any(SQL_TRANSLATOR_VERSION_KEY in message for message in result.errors)

    validator = _load_validator()
    with pytest.raises(validator.CorpusReadError, match="translator version"):
        validator.cohort_platforms([path])
    with pytest.raises(TranslatorVersionError):
        BundleTransformer().to_manifest_entry(path)


def test_an_attestation_conflicting_with_a_recorded_runtime_fails_closed(tmp_path: Path) -> None:
    other = {**ATTESTATION, "version": "30.25.0.2"}
    path = _write(tmp_path, _bundle(RUNTIME), other)

    with pytest.raises(TranslatorVersionError, match="conflicts"):
        resolve_translator_version(_bundle(RUNTIME), path)

    result = ValidationResult(str(path))
    _validate_manifest_provenance(json.loads(path.with_name("bundle.manifest.json").read_text("utf-8")), path, result)
    assert any("conflicts" in message for message in result.errors)

    validator = _load_validator()
    with pytest.raises(validator.CorpusReadError, match="conflicts"):
        validator.cohort_platforms([path])
    with pytest.raises(TranslatorVersionError, match="conflicts"):
        BundleTransformer().to_manifest_entry(path)
    with pytest.raises(TranslatorVersionError, match="conflicts"):
        BundleTransformer().to_detail_result(path, "r1")


def test_a_valid_attestation_leaves_the_comparison_identity_unchanged(tmp_path: Path) -> None:
    plain = _write(tmp_path, _bundle(), name="plain.json")
    attested = _write(tmp_path, _bundle(), ATTESTATION, name="attested.json")
    validator = _load_validator()

    assert validator.cohort_platforms([plain]) == validator.cohort_platforms([attested])
    assert BundleTransformer().to_manifest_entry(plain).variant_id == (
        BundleTransformer().to_manifest_entry(attested).variant_id
    )


def test_a_conflicting_attestation_fails_the_pipeline_instead_of_skipping_the_bundle(tmp_path: Path) -> None:
    from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline

    source = Path(__file__).resolve().parents[3] / "results-data" / "bundles"
    name = "tpch_sf1_duckdb_sql_20260826_162117_67e59fb6.json"
    bundles = tmp_path / "data" / "bundles"
    bundles.mkdir(parents=True)
    payload = json.loads((source / name).read_text(encoding="utf-8"))
    payload.setdefault("execution", {}).setdefault("translation", {"status": "passed"})["runtime"] = RUNTIME
    _write(bundles, payload, {**ATTESTATION, "version": "30.25.0.2"}, name=name)

    with pytest.raises(TranslatorVersionError, match="conflicts"):
        ExplorerPipeline().run(data_dir=tmp_path / "data", output_dir=tmp_path / "out")
