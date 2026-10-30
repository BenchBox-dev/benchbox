from __future__ import annotations

import pytest

from benchbox.core.platform_registry import LibraryInfo, PlatformRegistry

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _metadata() -> dict:
    return PlatformRegistry._build_platform_metadata()


def test_influxdb_required_library_probes_the_module_the_driver_installs():
    libraries = _metadata()["influxdb"]["libraries"]
    required = next(lib for lib in libraries if lib.get("required"))
    assert required["import_name"] == "influxdb_client_3"


def test_influxdb_is_available_when_the_driver_module_is_importable(monkeypatch):
    monkeypatch.setattr(PlatformRegistry, "_platform_metadata", _metadata())

    def fake_detect(spec):
        return LibraryInfo(name=spec["name"], version=None, installed=spec["import_name"] == "influxdb_client_3")

    monkeypatch.setattr(PlatformRegistry, "_detect_library", staticmethod(fake_detect))
    assert PlatformRegistry._probe_platform_availability("influxdb") is True
