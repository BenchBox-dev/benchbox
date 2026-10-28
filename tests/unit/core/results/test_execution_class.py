from __future__ import annotations

import json
from pathlib import Path

import pytest

from _project.scripts.explorer_pipeline.models import variant_label
from benchbox.core.execution_variant import VariantIdError, derive_variant_id
from benchbox.core.results.environment import ClientHostEnvironment, build_environment_payload
from benchbox.core.results.execution_variant import (
    ExecutionClassError,
    published_variant_id,
    read_execution_class,
    read_execution_environment,
    read_execution_variant,
    require_comparable_variants,
    variants_comparable,
)
from benchbox.core.results.loader import _extract_system_profile
from benchbox.core.schemas import SystemProfile
from benchbox.utils import environment as host_environment

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _bundle(**environment: object) -> dict:
    return {"platform": {"name": "ClickHouse Local"}, "environment": dict(environment)}


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, "clickhouse-local"),
        ({"os": "Linux 6.18.15"}, "clickhouse-local"),
        ({"execution_class": "native"}, "clickhouse-local"),
        ({"client_host": {"execution_class": "native"}}, "clickhouse-local"),
        ({"execution_class": "container"}, "clickhouse-local^container"),
        ({"client_host": {"execution_class": "container"}}, "clickhouse-local^container"),
        (
            {"execution_class": "container", "client_host": {"execution_class": "container"}},
            "clickhouse-local^container",
        ),
    ],
)
def test_identity_changes_only_for_a_recorded_container(environment: dict, expected: str) -> None:
    assert published_variant_id(_bundle(**environment), "clickhouse-local") == expected


def test_a_bundle_without_an_environment_block_is_native() -> None:
    assert published_variant_id({"platform": {"name": "DuckDB"}}, "duckdb") == "duckdb"
    assert read_execution_class({"platform": {"name": "DuckDB"}}) == "native"


@pytest.mark.parametrize(
    "environment",
    [
        {"execution_class": "vm"},
        {"execution_class": "container", "client_host": {"execution_class": "native"}},
    ],
)
def test_unknown_or_conflicting_recorded_classes_are_refused(environment: dict) -> None:
    with pytest.raises(ExecutionClassError):
        published_variant_id(_bundle(**environment), "clickhouse-local")


def test_manifest_sidecar_supplies_the_class_when_the_bundle_is_silent(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    path.write_text("{}", encoding="utf-8")
    path.with_name("run.manifest.json").write_text(
        json.dumps(
            {
                "execution_class": "container",
                "container_runtime": "docker",
                "container_host_os": "macOS",
                "execution_class_evidence": "owner attestation",
            }
        ),
        encoding="utf-8",
    )

    assert published_variant_id(_bundle(), "clickhouse-local", path) == "clickhouse-local^container"
    assert published_variant_id(_bundle(), "clickhouse-local") == "clickhouse-local"
    assert read_execution_environment(_bundle(), path) == {
        "container_runtime": "docker",
        "container_host_os": "macOS",
        "execution_class": "container",
    }


@pytest.mark.parametrize("content", ["not json", "[]", json.dumps({"known_defects": ["x"]})])
def test_unreadable_or_unrelated_sidecars_leave_identity_unchanged(tmp_path: Path, content: str) -> None:
    path = tmp_path / "run.json"
    path.write_text("{}", encoding="utf-8")
    path.with_name("run.manifest.json").write_text(content, encoding="utf-8")

    assert published_variant_id(_bundle(), "clickhouse-local", path) == "clickhouse-local"


def test_sidecar_with_an_unknown_class_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    path.write_text("{}", encoding="utf-8")
    path.with_name("run.manifest.json").write_text(json.dumps({"execution_class": "vm"}), encoding="utf-8")

    with pytest.raises(ExecutionClassError):
        published_variant_id(_bundle(), "clickhouse-local", path)


def test_class_conflicts_are_not_value_errors_the_pipeline_would_swallow() -> None:
    assert not issubclass(ExecutionClassError, ValueError)


def test_sidecar_cannot_override_a_recorded_class(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    path.write_text("{}", encoding="utf-8")
    path.with_name("run.manifest.json").write_text(json.dumps({"execution_class": "container"}), encoding="utf-8")

    with pytest.raises(ExecutionClassError, match="attests"):
        published_variant_id(_bundle(execution_class="native"), "clickhouse-local", path)


def test_sidecar_corrects_an_inferred_class_but_not_an_observed_one(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    path.write_text("{}", encoding="utf-8")
    path.with_name("run.manifest.json").write_text(
        json.dumps({"execution_class": "container", "container_host_os": "macOS", "execution_class_evidence": "owner"}),
        encoding="utf-8",
    )
    inferred = _bundle(execution_class="native", execution_class_source="inferred", container_host_os="Windows")
    observed = _bundle(execution_class="native", execution_class_source="observed")

    assert published_variant_id(inferred, "clickhouse-local", path) == "clickhouse-local^container"
    assert read_execution_environment(inferred, path)["container_host_os"] == "macOS"
    with pytest.raises(ExecutionClassError, match="attests"):
        published_variant_id(observed, "clickhouse-local", path)


def test_derive_variant_id_orders_the_class_last_and_rejects_the_separator() -> None:
    assert derive_variant_id("snowflake", "other", "photon", "greybeam", "container") == (
        "snowflake~other+photon@greybeam^container"
    )
    assert derive_variant_id("snowflake", execution_class="native") == "snowflake"
    with pytest.raises(VariantIdError):
        derive_variant_id("snowflake", execution_class="a^b")


def test_runs_in_different_execution_classes_are_not_comparable() -> None:
    native = _bundle(execution_class="native")
    container = _bundle(execution_class="container")

    assert variants_comparable(native, _bundle())
    assert not variants_comparable(native, container)
    with pytest.raises(Exception, match=r"execution class differs \(native vs container\)"):
        require_comparable_variants(native, container)
    assert "execution class=container" in read_execution_variant(container).describe()
    assert "execution class" not in read_execution_variant(native).describe()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"variant_id": "clickhouse-local^container", "container_os": "Linux"},
            "ClickHouse Local (Linux container)",
        ),
        (
            {"variant_id": "clickhouse-local^container", "container_os": "Linux", "container_host_os": "macOS"},
            "ClickHouse Local (Linux container on macOS)",
        ),
        ({"variant_id": "clickhouse-local^container"}, "ClickHouse Local (container)"),
        ({"variant_id": "clickhouse-local"}, "ClickHouse Local"),
        (
            {"variant_id": "clickhouse-local+x^container", "requested_execution_engine": "x", "container_os": "Linux"},
            "ClickHouse Local (x, Linux container)",
        ),
        (
            {
                "variant_id": "clickhouse-local+x^container",
                "requested_execution_engine": "x",
                "container_os": "Linux",
                "container_host_os": "macOS",
            },
            "ClickHouse Local (x, Linux container on macOS)",
        ),
        (
            {
                "variant_id": "clickhouse-local@g^container",
                "gateway_name": "g",
                "gateway_routed": True,
                "container_os": "Linux",
            },
            "ClickHouse Local via G (Linux container)",
        ),
    ],
)
def test_variant_label_names_the_container(kwargs: dict, expected: str) -> None:
    assert variant_label("ClickHouse Local", **kwargs) == expected


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_environment.platform, "system", lambda: "Linux")
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv(host_environment.CONTAINER_HOST_OS_ENV, raising=False)
    monkeypatch.setattr(host_environment, "_read_text", lambda path: "")
    monkeypatch.setattr(host_environment.os.path, "exists", lambda path: False)
    monkeypatch.setattr(host_environment, "detect_cpu_info", lambda: (None, None))


def test_detection_infers_native_on_an_unmarked_linux_host(linux: None) -> None:
    assert host_environment.detect_execution_class() == {
        "execution_class": "native",
        "execution_class_source": "inferred",
    }


def test_detection_observes_native_off_linux(monkeypatch: pytest.MonkeyPatch, linux: None) -> None:
    monkeypatch.setattr(host_environment.platform, "system", lambda: "Darwin")

    assert host_environment.detect_execution_class() == {
        "execution_class": "native",
        "execution_class_source": "observed",
    }


def test_detection_infers_a_container_for_an_unmarked_linux_guest_on_apple_silicon(
    monkeypatch: pytest.MonkeyPatch, linux: None
) -> None:
    monkeypatch.setattr(host_environment, "detect_cpu_info", lambda: ("Apple M4", "Apple"))

    assert host_environment.detect_execution_class() == {
        "execution_class": "container",
        "execution_class_source": "inferred",
        "container_host_os": "macOS",
    }


def test_a_marked_container_on_apple_silicon_is_observed_with_a_mac_host(
    monkeypatch: pytest.MonkeyPatch, linux: None
) -> None:
    monkeypatch.setattr(host_environment, "detect_cpu_info", lambda: ("Apple M4", "Apple"))
    monkeypatch.setattr(host_environment.os.path, "exists", lambda path: path == "/.dockerenv")

    assert host_environment.detect_execution_class() == {
        "execution_class": "container",
        "execution_class_source": "observed",
        "container_runtime": "docker",
        "container_host_os": "macOS",
    }


def test_detection_never_reports_a_container_off_linux(monkeypatch: pytest.MonkeyPatch, linux: None) -> None:
    monkeypatch.setattr(host_environment.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")

    assert host_environment.detect_execution_class()["execution_class"] == "native"


@pytest.mark.parametrize(
    ("marker", "runtime"),
    [("/.dockerenv", "docker"), ("/run/.containerenv", "podman")],
)
def test_detection_reads_marker_files(monkeypatch: pytest.MonkeyPatch, linux: None, marker: str, runtime: str) -> None:
    monkeypatch.setattr(host_environment.os.path, "exists", lambda path: path == marker)
    monkeypatch.setenv(host_environment.CONTAINER_HOST_OS_ENV, "macOS")

    assert host_environment.detect_execution_class() == {
        "execution_class": "container",
        "container_runtime": runtime,
        "container_host_os": "macOS",
        "execution_class_source": "observed",
    }


def test_detection_reads_kubernetes_and_cgroup_evidence(monkeypatch: pytest.MonkeyPatch, linux: None) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    assert host_environment.detect_execution_class()["container_runtime"] == "kubernetes"

    monkeypatch.delenv("KUBERNETES_SERVICE_HOST")
    monkeypatch.setattr(host_environment, "_read_text", lambda path: "0::/system.slice/docker-abc.scope")
    assert host_environment.detect_execution_class()["container_runtime"] == "docker"


def test_environment_payload_carries_the_class_next_to_os_and_cpu() -> None:
    profile = {
        "os_type": "Linux",
        "os_release": "6.18.15",
        "cpu_count": 4,
        "memory_gb": 9.8,
        "execution_class": "container",
        "container_runtime": "docker",
        "execution_class_source": "observed",
    }

    payload = build_environment_payload(system_profile=profile, execution_environment=None)

    assert payload["execution_class"] == "container"
    assert payload["client_host"]["container_runtime"] == "docker"
    assert payload["client_host"]["os"] == "Linux 6.18.15"
    assert read_execution_class({"environment": payload}) == "container"


def test_a_profile_without_a_class_records_none_and_is_not_stamped_at_export() -> None:
    payload = build_environment_payload(
        system_profile={"os_type": "Darwin", "cpu_count": 10}, execution_environment=None
    )

    assert "execution_class" not in payload
    assert "execution_class" not in payload["client_host"]


def test_capture_without_a_profile_detects_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchbox.core.results.environment.detect_execution_class",
        lambda: {"execution_class": "container", "container_runtime": "docker", "execution_class_source": "observed"},
    )

    assert ClientHostEnvironment.from_system_profile(None).execution_class == "container"


def test_system_profile_model_round_trips_the_class() -> None:
    profile = SystemProfile.model_validate(
        {
            "os_name": "Linux",
            "os_version": "6.18.15",
            "architecture": "aarch64",
            "cpu_cores_physical": 4,
            "cpu_cores_logical": 4,
            "memory_total_gb": 9.8,
            "memory_available_gb": 5.0,
            "python_version": "3.12",
            "disk_space_gb": 10.0,
            "timestamp": "2026-10-26T00:00:00",
            "execution_class": "container",
            "container_runtime": "docker",
        }
    )

    assert ClientHostEnvironment.from_system_profile(profile).to_dict()["execution_class"] == "container"
    with pytest.raises(ValueError):
        SystemProfile.model_validate({**profile.model_dump(), "execution_class": "vm"})


def test_reloading_a_result_preserves_the_recorded_class_instead_of_restamping() -> None:
    environment = {"os": "Linux 6.18.15", "execution_class": "container", "container_runtime": "docker"}

    profile = _extract_system_profile(environment)

    assert profile["execution_class"] == "container"
    assert profile["container_runtime"] == "docker"
    assert "execution_class" not in _extract_system_profile({"os": "Darwin 25.0.0"})


_MATCHED = {"collection_status": "available", "host_port_matched": True}
_DOCKER_RUNTIME = {"runtime_type": "docker_container", "collection_status": "available", "source": "observed"}


@pytest.mark.parametrize(
    ("client_os", "runtime", "expected"),
    [
        ("Darwin 25.0.0", _DOCKER_RUNTIME, ("container", "docker", "macOS", "observed")),
        ("Windows 11", _DOCKER_RUNTIME, ("container", "docker", "Windows", "observed")),
        ("Linux 6.8.0", _DOCKER_RUNTIME, (None, None, None, None)),
        ("Darwin 25.0.0", {**_DOCKER_RUNTIME, "collection_status": "unavailable"}, (None, None, None, None)),
        ("Darwin 25.0.0", {"runtime_type": "local_process"}, (None, None, None, None)),
    ],
)
def test_a_discovered_docker_engine_on_a_vm_backed_host_is_a_container_run(client_os, runtime, expected) -> None:
    payload = build_environment_payload(
        system_profile={"os_type": client_os.split()[0], "os_release": client_os.split()[1], "cpu_count": 10},
        execution_environment={"platform_runtime": runtime, "container": _MATCHED},
    )

    assert (
        payload.get("execution_class"),
        payload.get("container_runtime"),
        payload.get("container_host_os"),
        payload.get("execution_class_source"),
    ) == expected
    assert payload.get("client_host", {}).get("execution_class") == expected[0]


def test_a_discovered_docker_engine_overrides_an_observed_native_client_class() -> None:
    payload = build_environment_payload(
        system_profile={
            "os_type": "Darwin",
            "os_release": "25.0.0",
            "execution_class": "native",
            "execution_class_source": "observed",
        },
        execution_environment={"platform_runtime": _DOCKER_RUNTIME, "container": _MATCHED},
    )

    assert read_execution_class({"environment": payload}) == "container"


@pytest.mark.parametrize(
    ("runtime", "container"),
    [
        (_DOCKER_RUNTIME, {"collection_status": "available", "host_port_matched": False}),
        (_DOCKER_RUNTIME, {"collection_status": "available"}),
        ({**_DOCKER_RUNTIME, "source": "requested"}, _MATCHED),
    ],
)
def test_a_container_that_is_not_shown_to_serve_the_engine_port_is_not_a_container_run(runtime, container) -> None:
    payload = build_environment_payload(
        system_profile={"os_type": "Darwin", "os_release": "25.0.0"},
        execution_environment={"platform_runtime": runtime, "container": container},
    )

    assert payload.get("execution_class") != "container"
