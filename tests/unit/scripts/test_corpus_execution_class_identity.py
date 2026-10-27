from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from tests.unit.scripts.test_corpus_cohort_depth import (
    BUNDLES,
    MINIMAL_BUNDLE,
    _load_validator,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]

CONTAINER_ENVIRONMENT = {
    "os": "Linux 6.18.15",
    "arch": "aarch64",
    "cpu_count": 4,
    "memory_gb": 9.8,
    "client_host": {"os": "Linux 6.18.15", "arch": "aarch64", "cpu_count": 4, "memory_gb": 9.8},
}
NATIVE_ENVIRONMENT = {
    "os": "Darwin 25.0.0",
    "arch": "arm64",
    "cpu_count": 10,
    "memory_gb": 16.0,
    "client_host": {"os": "Darwin 25.0.0", "arch": "arm64", "cpu_count": 10, "memory_gb": 16.0},
}
SIDECAR = {
    "execution_class": "container",
    "container_host_os": "macOS",
    "execution_class_evidence": "Linux 6.18.15 aarch64 with Apple CPU vendor",
}


def _bundle(environment: dict | None, *, execution_class: str | None = None) -> dict:
    payload = copy.deepcopy(MINIMAL_BUNDLE)
    payload["platform"] = {"name": "ClickHouse Local", "version": "25.1"}
    if environment is not None:
        payload["environment"] = copy.deepcopy(environment)
        if execution_class is not None:
            payload["environment"]["execution_class"] = execution_class
            payload["environment"]["client_host"]["execution_class"] = execution_class
    return payload


def _write(directory: Path, name: str, payload: dict, sidecar: dict | None = None) -> Path:
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    if sidecar is not None:
        path.with_name(f"{path.stem}.manifest.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return path


def _identities(directory: Path) -> tuple[set[str], set[str]]:
    validator = _load_validator()
    bundles = validator.discover_bundles(directory)
    cohorts = validator.cohort_platforms(bundles)
    transformer = BundleTransformer()
    explorer = {transformer.to_manifest_entry(path).variant_id for path in bundles}
    return {identity for identities in cohorts.values() for identity in identities}, explorer


def test_native_and_unrecorded_bundles_keep_the_plain_identity(tmp_path: Path) -> None:
    _write(tmp_path, "historical.json", _bundle(None))
    _write(tmp_path, "unclassified.json", _bundle(CONTAINER_ENVIRONMENT))
    _write(tmp_path, "native.json", _bundle(NATIVE_ENVIRONMENT, execution_class="native"))

    validator_ids, explorer_ids = _identities(tmp_path)

    assert validator_ids == explorer_ids == {"clickhouse-local"}


def test_recorded_container_class_gets_its_own_identity_in_validator_and_explorer(tmp_path: Path) -> None:
    _write(tmp_path, "native.json", _bundle(NATIVE_ENVIRONMENT, execution_class="native"))
    _write(tmp_path, "container.json", _bundle(CONTAINER_ENVIRONMENT, execution_class="container"))

    validator_ids, explorer_ids = _identities(tmp_path)

    assert validator_ids == explorer_ids == {"clickhouse-local", "clickhouse-local^container"}


def test_manifest_sidecar_attestation_separates_an_unrecorded_container_bundle(tmp_path: Path) -> None:
    _write(tmp_path, "native.json", _bundle(NATIVE_ENVIRONMENT))
    container = _write(tmp_path, "container.json", _bundle(CONTAINER_ENVIRONMENT), SIDECAR)

    validator_ids, explorer_ids = _identities(tmp_path)
    entry = BundleTransformer().to_manifest_entry(container)

    assert validator_ids == explorer_ids == {"clickhouse-local", "clickhouse-local^container"}
    assert entry.variant_label == "ClickHouse Local (Linux container)"
    assert BundleTransformer().to_detail_result(container, "r1").environment.container_host_os == "macOS"


def test_sidecar_attestation_does_not_move_other_bundles(tmp_path: Path) -> None:
    _write(tmp_path, "attested.json", _bundle(CONTAINER_ENVIRONMENT), SIDECAR)
    _write(tmp_path, "plain.json", _bundle(CONTAINER_ENVIRONMENT))
    plain = tmp_path / "plain.json"

    assert BundleTransformer().to_manifest_entry(plain).variant_id == "clickhouse-local"


def test_recorded_class_conflicting_with_the_sidecar_is_refused(tmp_path: Path) -> None:
    from benchbox.core.results.execution_variant import ExecutionClassError

    path = _write(tmp_path, "conflict.json", _bundle(NATIVE_ENVIRONMENT, execution_class="native"), SIDECAR)

    with pytest.raises(ExecutionClassError, match="attests"):
        BundleTransformer().to_manifest_entry(path)
    validator = _load_validator()
    with pytest.raises(validator.CorpusReadError):
        validator.cohort_platforms([path])


def test_a_conflicting_sidecar_fails_the_corpus_gate_even_for_an_unranked_bundle(tmp_path: Path) -> None:
    payload = _bundle(NATIVE_ENVIRONMENT, execution_class="native")
    payload["environment"]["execution_class_source"] = "observed"
    payload["summary"]["validation"] = "failed"
    path = _write(tmp_path, "unranked.json", payload, SIDECAR)
    validator = _load_validator()

    assert validator.exclusion_reason(payload, path) is not None
    with pytest.raises(validator.CorpusReadError, match="attests"):
        validator.cohort_platforms([path])


def test_container_identity_composes_with_engine_and_gateway_suffixes(tmp_path: Path) -> None:
    payload = _bundle(CONTAINER_ENVIRONMENT, execution_class="container")
    payload["platform"] = {"name": "Polars", "execution_engine": {"requested": "streaming"}}
    path = _write(tmp_path, "polars.json", payload)

    entry = BundleTransformer().to_manifest_entry(path)

    assert entry.variant_id == "polars+streaming^container"
    assert entry.variant_label == "Polars (streaming, Linux container)"
    validator_ids, _ = _identities(tmp_path)
    assert validator_ids == {"polars+streaming^container"}


def _attests_container(path: Path) -> bool:
    sidecar = path.with_name(f"{path.stem}.manifest.json")
    return sidecar.exists() and json.loads(sidecar.read_text(encoding="utf-8")).get("execution_class") == "container"


def _records_container(payload: dict) -> bool:
    environment = payload.get("environment")
    if not isinstance(environment, dict):
        return False
    client_host = environment.get("client_host")
    return "container" in (
        environment.get("execution_class"),
        client_host.get("execution_class") if isinstance(client_host, dict) else None,
    )


def test_committed_identities_carry_the_container_suffix_only_for_attested_containers() -> None:
    validator = _load_validator()
    transformer = BundleTransformer()
    bundles = validator.discover_bundles(BUNDLES)

    for path in bundles:
        payload = json.loads(path.read_text(encoding="utf-8"))
        variant_id = transformer.to_manifest_entry(path).variant_id
        assert ("^container" in variant_id) == (_attests_container(path) or _records_container(payload)), path.name

    cohorts = validator.cohort_platforms(bundles)
    assert {identity for identities in cohorts.values() for identity in identities}


def test_native_linux_runner_bundles_keep_their_plain_identity() -> None:
    validator = _load_validator()
    transformer = BundleTransformer()
    runners = []
    for path in validator.discover_bundles(BUNDLES):
        payload = json.loads(path.read_text(encoding="utf-8"))
        environment = payload.get("environment")
        if not isinstance(environment, dict) or not str(environment.get("os", "")).startswith("Linux"):
            continue
        client_host = environment.get("client_host") if isinstance(environment.get("client_host"), dict) else {}
        if str(environment.get("cpu_vendor") or client_host.get("cpu_vendor", "")).lower() == "apple":
            continue
        runners.append(path)
        assert "^" not in transformer.to_manifest_entry(path).variant_id

    assert runners
