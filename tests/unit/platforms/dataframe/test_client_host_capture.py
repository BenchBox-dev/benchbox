from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.results.environment import build_environment_payload
from benchbox.core.schemas import SystemProfile
from benchbox.platforms.dataframe.benchmark_mixin import _client_host_profile
from benchbox.utils.system_info import get_system_info

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _client_host(system_profile: object) -> dict:
    payload = build_environment_payload(system_profile=system_profile, execution_environment=None)
    return payload.get("client_host", {})


def test_dataframe_profile_yields_a_populated_client_host() -> None:
    host = _client_host(_client_host_profile(None))
    assert host, "a DataFrame run must record a client host, not an empty block"
    for field in ("os", "arch", "python", "cpu_count", "memory_gb"):
        assert field in host, f"DataFrame client_host is missing {field!r}"


def test_capture_time_cpu_detection_records_measured_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("benchbox.utils.environment.detect_cpu_info", lambda: ("Detected CPU", "Detected Vendor"))

    host = _client_host(None)

    assert host["cpu_model"] == "Detected CPU"
    assert host["cpu_identity_provenance"] == "measured"


def test_dataframe_and_sql_record_the_same_client_host_fields() -> None:

    sql_host = _client_host(get_system_info().to_dict())
    dataframe_host = _client_host(_client_host_profile(None))
    assert set(dataframe_host) == set(sql_host)


def test_a_caller_supplied_mapping_is_honoured_verbatim() -> None:
    supplied = {"os_type": "Linux", "architecture": "x86_64", "cpu_model": "Some CPU"}
    assert _client_host_profile(supplied) == supplied


def test_an_explicitly_empty_profile_is_preserved_without_recollection(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_recollection() -> None:
        raise AssertionError("an explicitly supplied unknown profile must not recollect the current host")

    monkeypatch.setattr("benchbox.utils.system_info.get_system_info", fail_recollection)
    assert _client_host_profile({}) == {}


def test_a_typed_profile_snapshot_is_preserved_and_mapped() -> None:
    supplied = SystemProfile(
        os_name="SnapshotOS",
        os_version="1.2",
        architecture="snapshot-arch",
        cpu_model="Snapshot CPU",
        cpu_identity_provenance="measured",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        memory_total_gb=32.0,
        memory_available_gb=20.0,
        python_version="3.13.1",
        disk_space_gb=100.0,
        timestamp=datetime(2026, 8, 30),
    )

    host = _client_host(_client_host_profile(supplied))

    assert host == {
        "os": "SnapshotOS 1.2",
        "arch": "snapshot-arch",
        "cpu_count": 8,
        "memory_gb": 32.0,
        "python": "3.13.1",
        "cpu_model": "Snapshot CPU",
        "cpu_identity_provenance": "measured",
    }


