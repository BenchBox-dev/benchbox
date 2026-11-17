from __future__ import annotations

import pytest

from benchbox.core.results.environment import build_environment_payload
from benchbox.utils import environment as host_env, system_info

pytestmark = pytest.mark.fast

PROFILE = {
    "os_type": "Darwin",
    "os_release": "25.0",
    "architecture": "arm64",
    "cpu_model": "Apple M4",
    "cpu_vendor": "Apple",
    "cpu_identity_provenance": "measured",
    "cpu_count": 10,
}


@pytest.mark.parametrize("runtime_type", ["managed_cloud", "serverless"])
def test_managed_service_environment_keeps_the_client_cpu_out_of_the_top_level(runtime_type: str) -> None:
    payload = build_environment_payload(
        system_profile=PROFILE,
        execution_environment={"platform_runtime": {"runtime_type": runtime_type, "source": "observed"}},
    )

    for key in ("cpu_model", "cpu_vendor", "cpu_identity_provenance", "cpu_count"):
        assert key not in payload
    assert payload["client_host"]["cpu_model"] == "Apple M4"
    assert payload["client_host"]["cpu_count"] == 10


def test_self_run_environment_still_mirrors_the_host_cpu() -> None:
    payload = build_environment_payload(
        system_profile=PROFILE,
        execution_environment={"platform_runtime": {"runtime_type": "local_process", "source": "observed"}},
    )

    assert payload["cpu_model"] == "Apple M4"
    assert payload["cpu_count"] == 10


def test_container_host_cpu_reads_the_host_model_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_env.platform, "machine", lambda: "aarch64")
    monkeypatch.setenv(host_env.CONTAINER_HOST_CPU_MODEL_ENV, " Apple M4 ")

    assert host_env.container_host_cpu() == ("Apple M4", "Apple")


@pytest.mark.parametrize("value", ["", "  ", "aarch64"])
def test_container_host_cpu_ignores_blank_and_architecture_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(host_env.platform, "machine", lambda: "aarch64")
    monkeypatch.setenv(host_env.CONTAINER_HOST_CPU_MODEL_ENV, value)

    assert host_env.container_host_cpu() == (None, None)


def test_system_info_records_the_host_cpu_inside_a_container_as_attested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_env, "detect_cpu_info", lambda: (None, "Apple"))
    monkeypatch.setattr(host_env.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(system_info.platform, "machine", lambda: "aarch64")
    monkeypatch.setenv(host_env.CONTAINER_HOST_CPU_MODEL_ENV, "Apple M4")

    info = system_info.get_system_info()

    assert (info.cpu_model, info.cpu_vendor, info.cpu_identity_provenance) == ("Apple M4", "Apple", "user_attested")
