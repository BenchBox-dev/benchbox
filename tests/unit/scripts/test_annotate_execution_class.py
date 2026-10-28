from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchbox.core.results.execution_variant import published_variant_id
from scripts.annotate_execution_class import annotate, main

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SIDECAR = {"bundle_file": "x.json", "bundle_hash": "h", "manifest_version": "1.0", "result_source": "internal"}


def _write(
    directory: Path,
    name: str,
    *,
    os_name: str,
    vendor: str,
    recorded_class: str | None = None,
    source: str = "observed",
) -> Path:
    environment = {"os": os_name, "client_host": {"os": os_name, "cpu_vendor": vendor}}
    if recorded_class:
        environment["client_host"]["execution_class"] = recorded_class
        environment["client_host"]["execution_class_source"] = source
    path = directory / f"{name}.json"
    path.write_text(json.dumps({"platform": {"name": "ClickHouse Local"}, "environment": environment}), "utf-8")
    path.with_name(f"{name}.manifest.json").write_text(json.dumps(SIDECAR), "utf-8")
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_only_linux_guests_on_apple_silicon_are_selected(tmp_path: Path) -> None:
    guest = _write(tmp_path, "guest", os_name="Linux 6.18.15", vendor="Apple")
    native_mac = _write(tmp_path, "mac", os_name="Darwin 27.0.0", vendor="Apple")
    runner = _write(tmp_path, "runner", os_name="Linux 6.8.0-azure", vendor="AMD")
    recorded = _write(tmp_path, "recorded", os_name="Linux 6.18.15", vendor="Apple", recorded_class="native")
    inferred = _write(
        tmp_path, "inferred", os_name="Linux 6.18.15", vendor="Apple", recorded_class="native", source="inferred"
    )
    untouched = {path: _digest(path) for path in (guest, native_mac, runner, recorded, inferred)}

    changed = annotate(tmp_path, write=False)

    assert [path.name for path in changed] == ["guest.manifest.json", "inferred.manifest.json"]
    assert all(_digest(path) == digest for path, digest in untouched.items())
    assert json.loads(guest.with_name("guest.manifest.json").read_text("utf-8")) == SIDECAR


def test_write_attests_the_class_in_the_sidecar_only_and_is_idempotent(tmp_path: Path) -> None:
    guest = _write(tmp_path, "guest", os_name="Linux 6.18.15", vendor="Apple")
    bundle_digest = _digest(guest)

    assert main(["--bundles-dir", str(tmp_path), "--write"]) == 0

    manifest = json.loads(guest.with_name("guest.manifest.json").read_text("utf-8"))
    assert manifest["execution_class"] == "container"
    assert manifest["container_host_os"] == "macOS"
    assert manifest["execution_class_evidence"]
    assert {key: manifest[key] for key in SIDECAR} == SIDECAR
    assert _digest(guest) == bundle_digest
    assert published_variant_id(json.loads(guest.read_text("utf-8")), "clickhouse-local", guest) == (
        "clickhouse-local^container"
    )
    assert annotate(tmp_path, write=True) == []


def test_a_selected_bundle_without_a_sidecar_stops_the_run(tmp_path: Path) -> None:
    guest = _write(tmp_path, "guest", os_name="Linux 6.18.15", vendor="Apple")
    guest.with_name("guest.manifest.json").unlink()

    with pytest.raises(SystemExit, match="no manifest sidecar"):
        annotate(tmp_path, write=True)


def _server(directory: Path, name: str, *, deployment: dict, runtime: str = "unknown", container: bool = True) -> Path:
    environment: dict = {"os": "Darwin 27.0.0", "platform_runtime": {"runtime_type": runtime}}
    if container:
        environment["container"] = {"collection_status": "unavailable"}
    path = directory / f"{name}.json"
    path.write_text(
        json.dumps({"platform": {"name": "Apache Doris", "deployment": deployment}, "environment": environment}),
        "utf-8",
    )
    path.with_name(f"{name}.manifest.json").write_text(json.dumps(SIDECAR), "utf-8")
    return path


def test_docker_server_engines_are_attested_only_on_an_explicit_maintainer_flag(tmp_path: Path) -> None:
    local = {"deployment_type": "self_hosted", "endpoint_class": "localhost_port"}
    doris = _server(tmp_path, "doris", deployment=local)
    _server(tmp_path, "lakesail", deployment=local, runtime="local_process")
    _server(tmp_path, "remote", deployment={"deployment_type": "self_hosted", "endpoint_class": "remote_host"})
    _server(tmp_path, "undiscovered", deployment=local, container=False)
    _server(tmp_path, "duckdb", deployment={"deployment_type": "embedded", "endpoint_class": "embedded_process"})

    assert annotate(tmp_path, write=False) == []

    changed = annotate(tmp_path, write=True, attest_docker_servers=True)

    assert [path.name for path in changed] == ["doris.manifest.json"]
    assert (
        "Maintainer attestation"
        in json.loads(doris.with_name("doris.manifest.json").read_text("utf-8"))["execution_class_evidence"]
    )
    manifest = json.loads(doris.with_name("doris.manifest.json").read_text("utf-8"))
    assert manifest["execution_class"] == "container"
    assert manifest["container_runtime"] == "docker"
    assert manifest["container_host_os"] == "macOS"
    assert published_variant_id(json.loads(doris.read_text("utf-8")), "apache-doris", doris) == "apache-doris^container"
